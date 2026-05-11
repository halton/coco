"""robot-003 verify: ExpressionPlayer / ExpressionSequence 行为校验.

V1  默认 OFF（COCO_EXPRESSIONS 未设 → ExpressionsConfig.enabled=False）
V2  COCO_EXPRESSIONS=1 构造成功（ExpressionPlayer 可实例化）
V3  EXPRESSION_LIBRARY 至少含 5 个预设：welcome / thinking / praise / confused / shy
V4  play("welcome") 按帧顺序调用 mock robot.goto_target
V5  play("unknown") fail-soft 不抛 + emit robot.expression_not_found
V6  cooldown 内重复 play 同名直接跳过 + emit robot.expression_cooldown_skip
V7  并发 play 排队/拒绝（单线程串行，第二个并发 play 立即返回 False）
V8  IdleAnimator pause/resume 钩子被调用
V9  tts.say(expression="welcome") 触发 player.play
V10 emit "robot.expression_played"；"robot" 在 AUTHORITATIVE_COMPONENTS
V11 env clamp（COCO_EXPRESSIONS_SPEED 越界被 clamp）
V12 stop() 干净退出 + ProactiveScheduler 可注入 expression hook（轻量验证）

全部用 mock robot（不需要 daemon），跑得快；
sim-only feature，真机扭力/姿态属 uat 异步 milestone。
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from typing import Any, List, Tuple
from unittest.mock import MagicMock

import numpy as np

errors: List[str] = []
t0 = time.time()


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


# -----------------------------------------------------------------------
# 重要：每个测试 V 重置 _expression_player 注入与 env，避免顺序耦合
# -----------------------------------------------------------------------


def make_mock_robot() -> Any:
    """构造一个 MagicMock robot，记录 goto_target 调用次数 + 参数。"""
    r = MagicMock()
    r.goto_target = MagicMock(return_value=None)
    r.set_target_antenna_joint_positions = MagicMock(return_value=None)
    return r


# =======================================================================
# V1
# =======================================================================
print("V1: 默认 OFF（COCO_EXPRESSIONS 未设）")
try:
    # 清干净 env
    for k in ("COCO_EXPRESSIONS", "COCO_EXPRESSIONS_COOLDOWN_S", "COCO_EXPRESSIONS_SPEED"):
        os.environ.pop(k, None)
    from coco.robot.expressions import expressions_config_from_env
    cfg = expressions_config_from_env()
    check("默认 enabled=False", cfg.enabled is False, f"got enabled={cfg.enabled}")
    check("默认 cooldown=1.0", cfg.cooldown_default_s == 1.0)
    check("默认 speed=1.0", cfg.global_speed_scale == 1.0)
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())

# =======================================================================
# V2
# =======================================================================
print("V2: COCO_EXPRESSIONS=1 构造成功")
try:
    os.environ["COCO_EXPRESSIONS"] = "1"
    from coco.robot.expressions import (
        expressions_config_from_env,
        ExpressionPlayer,
        ExpressionsConfig,
    )
    cfg = expressions_config_from_env()
    check("enabled=True", cfg.enabled is True)
    r = make_mock_robot()
    player = ExpressionPlayer(r, config=cfg)
    check("ExpressionPlayer 实例化", player is not None)
    check("初始 stats.plays_started=0", player.stats.plays_started == 0)
finally:
    os.environ.pop("COCO_EXPRESSIONS", None)

# =======================================================================
# V3
# =======================================================================
print("V3: EXPRESSION_LIBRARY 含 5 个预设")
try:
    from coco.robot.expressions import EXPRESSION_LIBRARY
    expected = {"welcome", "thinking", "praise", "confused", "shy"}
    have = set(EXPRESSION_LIBRARY.keys())
    missing = expected - have
    check("welcome/thinking/praise/confused/shy 都在库内",
          len(missing) == 0, f"missing={missing}")
    check("库总数 >= 5", len(EXPRESSION_LIBRARY) >= 5, f"got {len(EXPRESSION_LIBRARY)}")
    # 每个 seq.validate() OK
    for name, seq in EXPRESSION_LIBRARY.items():
        try:
            seq.validate()
        except Exception as e:  # noqa: BLE001
            errors.append(f"V3 {name} validate failed: {e}")
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())

# =======================================================================
# V4
# =======================================================================
print("V4: play('welcome') 按帧顺序下发 goto_target")
try:
    from coco.robot.expressions import (
        ExpressionPlayer, ExpressionsConfig, EXPRESSION_LIBRARY,
    )
    r = make_mock_robot()
    player = ExpressionPlayer(r, config=ExpressionsConfig(enabled=True))
    ok = player.play("welcome")
    check("play 返回 True", ok is True)
    welcome = EXPRESSION_LIBRARY["welcome"]
    expected_calls = len(welcome.frames) + (1 if welcome.return_to_center else 0)
    got_calls = r.goto_target.call_count
    check(f"goto_target 调用次数 = frames + return_to_center({expected_calls})",
          got_calls == expected_calls, f"got {got_calls}")
    # 校验前两帧 yaw 顺序（第一帧 +8, 第二帧 -8）
    first_kwargs = r.goto_target.call_args_list[0].kwargs
    second_kwargs = r.goto_target.call_args_list[1].kwargs
    head1 = first_kwargs.get("head")
    head2 = second_kwargs.get("head")
    from scipy.spatial.transform import Rotation as R
    yaw1 = R.from_matrix(head1[:3, :3]).as_euler("xyz", degrees=True)[2]
    yaw2 = R.from_matrix(head2[:3, :3]).as_euler("xyz", degrees=True)[2]
    check("frame[0] yaw>0 (welcome 首帧右摆，+yaw)", yaw1 > 4.0, f"yaw1={yaw1:.2f}")
    check("frame[1] yaw<0 (welcome 次帧左摆，-yaw)", yaw2 < -4.0, f"yaw2={yaw2:.2f}")
    check("stats.frames_dispatched == frames",
          player.stats.frames_dispatched == len(welcome.frames),
          f"got {player.stats.frames_dispatched}")
    check("stats.plays_completed=1", player.stats.plays_completed == 1)
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())

# =======================================================================
# V5
# =======================================================================
print("V5: play('unknown') fail-soft + emit not_found")
try:
    from coco.robot.expressions import ExpressionPlayer, ExpressionsConfig
    r = make_mock_robot()
    emits: List[Tuple[str, str, dict]] = []
    def fake_emit(ev, msg="", **payload):
        emits.append((ev, msg, payload))
    player = ExpressionPlayer(r, config=ExpressionsConfig(enabled=True), emit_fn=fake_emit)
    ok = player.play("__no_such_expression__")
    check("unknown play 返回 False", ok is False)
    check("不抛异常", True)
    check("goto_target 未被调用", r.goto_target.call_count == 0)
    check("stats.plays_not_found=1", player.stats.plays_not_found == 1)
    events = [e[0] for e in emits]
    check("emit robot.expression_not_found",
          "robot.expression_not_found" in events, f"emits={events}")
except Exception:  # noqa: BLE001
    errors.append("V5: " + traceback.format_exc())

# =======================================================================
# V6
# =======================================================================
print("V6: cooldown 内重复 play 同名被跳过")
try:
    from coco.robot.expressions import ExpressionPlayer, ExpressionsConfig
    r = make_mock_robot()
    emits = []
    def fake_emit(ev, msg="", **payload):
        emits.append((ev, msg, payload))
    cfg = ExpressionsConfig(enabled=True)
    player = ExpressionPlayer(r, config=cfg, emit_fn=fake_emit)
    ok1 = player.play("welcome")
    check("第 1 次 play OK", ok1 is True)
    ok2 = player.play("welcome")
    check("第 2 次 play 立即返回 False (cooldown)", ok2 is False)
    check("stats.plays_skipped_cooldown=1", player.stats.plays_skipped_cooldown == 1)
    events = [e[0] for e in emits]
    check("emit robot.expression_cooldown_skip",
          "robot.expression_cooldown_skip" in events, f"emits={events}")
    # 不同 name 不受 cooldown 限制
    ok3 = player.play("thinking")
    check("不同 name 不受 cooldown 影响", ok3 is True)
except Exception:  # noqa: BLE001
    errors.append("V6: " + traceback.format_exc())

# =======================================================================
# V7
# =======================================================================
print("V7: 并发 play 立即拒绝 + busy 事件")
try:
    from coco.robot.expressions import ExpressionPlayer, ExpressionsConfig

    # 让 goto_target 阻塞 0.3s，模拟"动作中"
    delays: List[float] = []
    def slow_goto(head=None, duration=0.0):
        time.sleep(0.25)
        delays.append(duration)

    r = MagicMock()
    r.goto_target = MagicMock(side_effect=slow_goto)
    emits = []
    def fake_emit(ev, msg="", **payload):
        emits.append((ev, msg, payload))
    player = ExpressionPlayer(r, config=ExpressionsConfig(enabled=True), emit_fn=fake_emit)

    results: List[Any] = [None, None]
    def t1_play():
        results[0] = player.play("praise")
    def t2_play():
        # 等 t1 抢到锁后再 play
        time.sleep(0.1)
        results[1] = player.play("thinking")
    t1 = threading.Thread(target=t1_play)
    t2 = threading.Thread(target=t2_play)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    check("线程 1 play 成功", results[0] is True)
    check("线程 2 并发 play 被拒", results[1] is False, f"got {results[1]}")
    check("stats.plays_rejected_busy=1", player.stats.plays_rejected_busy == 1)
    events = [e[0] for e in emits]
    check("emit robot.expression_busy", "robot.expression_busy" in events,
          f"emits={events}")
except Exception:  # noqa: BLE001
    errors.append("V7: " + traceback.format_exc())

# =======================================================================
# V8
# =======================================================================
print("V8: IdleAnimator pause/resume 钩子被调用")
try:
    from coco.robot.expressions import ExpressionPlayer, ExpressionsConfig
    r = make_mock_robot()
    idle_mock = MagicMock()
    idle_mock.pause = MagicMock()
    idle_mock.resume = MagicMock()
    player = ExpressionPlayer(
        r, idle_animator=idle_mock, config=ExpressionsConfig(enabled=True)
    )
    ok = player.play("welcome")
    check("play 成功", ok is True)
    check("idle.pause() 被调 1 次", idle_mock.pause.call_count == 1,
          f"got {idle_mock.pause.call_count}")
    check("idle.resume() 被调 1 次", idle_mock.resume.call_count == 1,
          f"got {idle_mock.resume.call_count}")
    # 异常路径也要 resume：mock robot 抛错 → play 仍 finally resume
    r2 = MagicMock()
    r2.goto_target = MagicMock(side_effect=RuntimeError("simulated SDK fail"))
    idle_mock2 = MagicMock()
    player2 = ExpressionPlayer(
        r2, idle_animator=idle_mock2, config=ExpressionsConfig(enabled=True)
    )
    player2.play("thinking")
    check("SDK 异常路径 idle.resume() 仍被调",
          idle_mock2.resume.call_count == 1,
          f"got {idle_mock2.resume.call_count}")
    check("SDK 异常计入 stats.sdk_errors",
          player2.stats.sdk_errors > 0,
          f"got {player2.stats.sdk_errors}")
except Exception:  # noqa: BLE001
    errors.append("V8: " + traceback.format_exc())

# =======================================================================
# V9
# =======================================================================
print("V9: tts.say(expression='welcome') 触发 player.play")
try:
    import coco.tts as coco_tts
    fake_player = MagicMock()
    fake_player.play = MagicMock(return_value=True)
    coco_tts.set_expression_player(fake_player)
    # 拦截真正合成 / 播放，避免依赖 Kokoro 模型
    orig_synth = coco_tts.synthesize
    orig_play = coco_tts.play
    coco_tts.synthesize = lambda text, sid=50, speed=1.0: (np.zeros(160, dtype=np.float32), 16000)
    coco_tts.play = lambda samples, sr, blocking=True: None
    try:
        coco_tts.say("你好", expression="welcome")
        check("expression='welcome' 触发 player.play", fake_player.play.call_count == 1,
              f"got {fake_player.play.call_count}")
        args, _ = fake_player.play.call_args
        check("player.play 参数 = 'welcome'",
              args[0] == "welcome", f"got {args}")
        # emotion 兼容路径
        fake_player.play.reset_mock()
        coco_tts.say("你好", emotion="praise")
        check("emotion='praise' 也触发 player.play",
              fake_player.play.call_count == 1)
        # expression 优先级 > emotion
        fake_player.play.reset_mock()
        coco_tts.say("你好", emotion="praise", expression="thinking")
        check("expression 优先级 > emotion",
              fake_player.play.call_args[0][0] == "thinking",
              f"got {fake_player.play.call_args[0]}")
    finally:
        coco_tts.synthesize = orig_synth
        coco_tts.play = orig_play
        coco_tts.set_expression_player(None)
except Exception:  # noqa: BLE001
    errors.append("V9: " + traceback.format_exc())

# =======================================================================
# V10
# =======================================================================
print("V10: emit robot.expression_played + 'robot' 在 AUTHORITATIVE_COMPONENTS")
try:
    from coco.logging_setup import AUTHORITATIVE_COMPONENTS
    check("'robot' 在 AUTHORITATIVE_COMPONENTS",
          "robot" in AUTHORITATIVE_COMPONENTS,
          f"set={sorted(AUTHORITATIVE_COMPONENTS)}")
    from coco.robot.expressions import ExpressionPlayer, ExpressionsConfig
    r = make_mock_robot()
    emits = []
    def fake_emit(ev, msg="", **payload):
        emits.append((ev, msg, payload))
    player = ExpressionPlayer(r, config=ExpressionsConfig(enabled=True), emit_fn=fake_emit)
    player.play("agreeing")
    events = [e[0] for e in emits]
    check("emit robot.expression_played",
          "robot.expression_played" in events, f"events={events}")
    # payload 包含 expression name & frames
    played = [e for e in emits if e[0] == "robot.expression_played"][0]
    check("emit payload 含 expression name",
          played[2].get("expression") == "agreeing")
    check("emit payload 含 frames count",
          played[2].get("frames", 0) > 0, f"frames={played[2].get('frames')}")
except Exception:  # noqa: BLE001
    errors.append("V10: " + traceback.format_exc())

# =======================================================================
# V11
# =======================================================================
print("V11: env clamp（speed/cooldown 越界被 clamp）")
try:
    from coco.robot.expressions import expressions_config_from_env

    os.environ["COCO_EXPRESSIONS_SPEED"] = "999"
    cfg = expressions_config_from_env()
    check("speed > 4.0 被 clamp 到 4.0",
          cfg.global_speed_scale == 4.0, f"got {cfg.global_speed_scale}")

    os.environ["COCO_EXPRESSIONS_SPEED"] = "0.01"
    cfg = expressions_config_from_env()
    check("speed < 0.25 被 clamp 到 0.25",
          cfg.global_speed_scale == 0.25, f"got {cfg.global_speed_scale}")

    os.environ["COCO_EXPRESSIONS_SPEED"] = "abc"
    cfg = expressions_config_from_env()
    check("speed=非数字 回退 1.0",
          cfg.global_speed_scale == 1.0, f"got {cfg.global_speed_scale}")

    os.environ["COCO_EXPRESSIONS_COOLDOWN_S"] = "99999"
    cfg = expressions_config_from_env()
    check("cooldown 越界 clamp 到 30.0",
          cfg.cooldown_default_s == 30.0, f"got {cfg.cooldown_default_s}")
    # global_speed_scale 应用到 frame.duration 后仍 clamp 到 [MIN_DURATION_S, MAX_DURATION_S]
    from coco.robot.expressions import ExpressionPlayer, ExpressionsConfig
    os.environ.pop("COCO_EXPRESSIONS_SPEED", None)
    os.environ.pop("COCO_EXPRESSIONS_COOLDOWN_S", None)
    r = make_mock_robot()
    # speed=10 → duration / 10 可能 < MIN_DURATION_S; 检查实际 dispatch duration >= MIN_DURATION_S
    player = ExpressionPlayer(
        r,
        config=ExpressionsConfig(enabled=True, global_speed_scale=10.0),
    )
    player.play("welcome")
    durations = [call.kwargs.get("duration") for call in r.goto_target.call_args_list]
    from coco.actions import MIN_DURATION_S, MAX_DURATION_S
    bad = [d for d in durations if d is not None and (d < MIN_DURATION_S or d > MAX_DURATION_S)]
    check(f"所有 duration 在 [{MIN_DURATION_S}, {MAX_DURATION_S}]",
          not bad, f"out_of_range={bad}")
finally:
    for k in ("COCO_EXPRESSIONS_SPEED", "COCO_EXPRESSIONS_COOLDOWN_S", "COCO_EXPRESSIONS"):
        os.environ.pop(k, None)

# =======================================================================
# V12
# =======================================================================
print("V12: stop() 干净退出 + ProactiveScheduler 可注入")
try:
    from coco.robot.expressions import ExpressionPlayer, ExpressionsConfig
    r = make_mock_robot()
    player = ExpressionPlayer(r, config=ExpressionsConfig(enabled=True))
    player.stop()
    ok = player.play("welcome")
    check("stop() 后 play 返回 False", ok is False)
    check("stop() 后 goto_target 不再被调",
          r.goto_target.call_count == 0,
          f"got {r.goto_target.call_count}")

    # ProactiveScheduler 集成：proactive 触发后 tts_say_fn 路径若带 expression，
    # 通过 _expression_player 注入即可触发 play。
    # 实现：让 proactive 的 tts_say_fn 包一层调 say(text, expression="thinking")。
    import coco.tts as coco_tts
    fake_player = MagicMock()
    fake_player.play = MagicMock(return_value=True)
    coco_tts.set_expression_player(fake_player)
    orig_synth = coco_tts.synthesize
    orig_play = coco_tts.play
    coco_tts.synthesize = lambda text, sid=50, speed=1.0: (np.zeros(160, dtype=np.float32), 16000)
    coco_tts.play = lambda samples, sr, blocking=True: None
    try:
        # 模拟 proactive 内 tts_say_fn(text, blocking=True) 包一层
        def proactive_say(text, blocking=True):
            coco_tts.say(text, blocking=blocking, expression="thinking")
        proactive_say("我们聊点什么吧", blocking=True)
        check("ProactiveScheduler tts_say_fn 包一层后 expression 链路通",
              fake_player.play.call_count == 1,
              f"got {fake_player.play.call_count}")
        check("触发的 expression name=thinking",
              fake_player.play.call_args[0][0] == "thinking")
    finally:
        coco_tts.synthesize = orig_synth
        coco_tts.play = orig_play
        coco_tts.set_expression_player(None)
except Exception:  # noqa: BLE001
    errors.append("V12: " + traceback.format_exc())


# =======================================================================
# 收尾
# =======================================================================
dt = time.time() - t0
print(f"\nverify_robot_003 done in {dt:.2f}s")
if errors:
    print(f"FAIL ({len(errors)})")
    for e in errors:
        print(f"  - {e[:300]}")
    sys.exit(1)
print("ALL PASS V1-V12")
sys.exit(0)
