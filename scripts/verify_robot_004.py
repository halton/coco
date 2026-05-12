"""robot-004 verify: PostureBaseline / PostureBaselineModulator 行为校验.

V1  默认 OFF（COCO_POSTURE_BASELINE 未设 → enabled=False；start() noop）
V2  COCO_POSTURE_BASELINE=1 → cfg.enabled=True，配置 clamp 生效
V3  PostureBaseline.compute(emotion=happy, power=ACTIVE) → pitch_offset < 0（抬头）
V4  PostureBaseline.compute(emotion=sad, power=ACTIVE) → pitch_offset > 0（低头）
V5  PostureBaseline.compute(power=SLEEP) → ZERO_OFFSET（外层 short-circuit）
V6  PostureOffset.clamped() 严格在 ±5° pitch / ±3° yaw / antenna [0,1]
V7  PostureBaselineModulator: emotion 切换 → 2s linear ramp（不瞬切；中段插值在 from..to 之间）
V8  Modulator: SLEEP 状态下 current_offset() 返回 ZERO；stats.sleep_skipped 累加，无天线下发
V9  Modulator: pause()/resume() 期间停止 / 恢复天线下发；ramp 仍在内部推进
V10 Modulator: emit "robot.posture_baseline_changed" payload 含 from/to/emotion/power
V11 Modulator: emotion 误判抖动被 debounce 抑制（debounce 内不触发新 ramp）
V12 IdleAnimator._micro_head 叠加 baseline offset：head sample center 在 (bp_pitch, bp_yaw) 而非 0
V13 IdleAnimator._breathe 在 baseline 启用时回的"中位"是 baseline pose 而非 INIT_HEAD_POSE
V14 ExpressionPlayer.play 调 baseline.pause / resume（与 expression 帧绝对值不冲突）
V15 与 SituationalIdleModulator 叠加后总幅度仍 clamp 在 actions.MAX_PITCH/YAW 内
V16 SDK 异常 fail-soft：robot.set_target_antenna_joint_positions 抛 → stats.sdk_errors 累加，不抛
V17 不可用 emotion_tracker / power_state（None）→ 等价 (neutral, active) → 中位 baseline
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from typing import Any, List
from unittest.mock import MagicMock

errors: List[str] = []
t0 = time.time()


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


def make_mock_robot() -> Any:
    r = MagicMock()
    r.goto_target = MagicMock(return_value=None)
    r.set_target_antenna_joint_positions = MagicMock(return_value=None)
    return r


# 清干净 env
for k in (
    "COCO_POSTURE_BASELINE",
    "COCO_POSTURE_BASELINE_RAMP_S",
    "COCO_POSTURE_BASELINE_TICK_S",
    "COCO_POSTURE_BASELINE_DEBOUNCE_S",
):
    os.environ.pop(k, None)


# =======================================================================
# V1
# =======================================================================
print("V1: 默认 OFF")
try:
    from coco.robot.posture_baseline import (
        posture_baseline_config_from_env,
        posture_baseline_enabled_from_env,
        PostureBaselineModulator,
    )
    cfg = posture_baseline_config_from_env()
    check("默认 enabled=False", cfg.enabled is False)
    check("posture_baseline_enabled_from_env=False", posture_baseline_enabled_from_env() is False)
    # start() noop（不起 thread）
    mod = PostureBaselineModulator(robot=make_mock_robot(), config=cfg)
    stop = threading.Event()
    mod.start(stop)
    check("disabled 时 thread 未启动", not mod.is_alive())
    check("disabled 时 current_offset == ZERO", mod.current_offset().pitch_deg == 0.0
          and mod.current_offset().yaw_deg == 0.0 and mod.current_offset().antenna == 0.5)
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())


# =======================================================================
# V2
# =======================================================================
print("V2: COCO_POSTURE_BASELINE=1 → enabled=True，配置 clamp 生效")
try:
    os.environ["COCO_POSTURE_BASELINE"] = "1"
    os.environ["COCO_POSTURE_BASELINE_RAMP_S"] = "100"  # 越界 → clamp 到 10.0
    os.environ["COCO_POSTURE_BASELINE_TICK_S"] = "0.001"  # 越界 → clamp 到 0.05
    os.environ["COCO_POSTURE_BASELINE_DEBOUNCE_S"] = "0"
    from coco.robot.posture_baseline import posture_baseline_config_from_env
    cfg = posture_baseline_config_from_env()
    check("enabled=True", cfg.enabled is True)
    check("ramp_s clamp 上限 10.0", cfg.ramp_s == 10.0, f"got {cfg.ramp_s}")
    check("tick_interval_s clamp 下限 0.05", cfg.tick_interval_s == 0.05, f"got {cfg.tick_interval_s}")
    check("debounce_s=0 OK", cfg.debounce_s == 0.0)
finally:
    for k in (
        "COCO_POSTURE_BASELINE",
        "COCO_POSTURE_BASELINE_RAMP_S",
        "COCO_POSTURE_BASELINE_TICK_S",
        "COCO_POSTURE_BASELINE_DEBOUNCE_S",
    ):
        os.environ.pop(k, None)


# =======================================================================
# V3
# =======================================================================
print("V3: happy + ACTIVE → pitch_offset < 0（抬头）")
try:
    from coco.robot.posture_baseline import PostureBaseline
    from coco.emotion import Emotion
    from coco.power_state import PowerState
    bl = PostureBaseline()
    off = bl.compute(Emotion.HAPPY, PowerState.ACTIVE)
    check("happy/ACTIVE pitch < 0", off.pitch_deg < 0.0, f"got {off.pitch_deg}")
    check("happy/ACTIVE antenna 大（>=0.6）", off.antenna >= 0.6, f"got {off.antenna}")
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4
# =======================================================================
print("V4: sad + ACTIVE → pitch_offset > 0（低头）")
try:
    from coco.robot.posture_baseline import PostureBaseline
    from coco.emotion import Emotion
    from coco.power_state import PowerState
    bl = PostureBaseline()
    off = bl.compute(Emotion.SAD, PowerState.ACTIVE)
    check("sad/ACTIVE pitch > 0", off.pitch_deg > 0.0, f"got {off.pitch_deg}")
    check("sad/ACTIVE antenna 小（<=0.2）", off.antenna <= 0.2, f"got {off.antenna}")
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5
# =======================================================================
print("V5: SLEEP → ZERO_OFFSET（外层 short-circuit）")
try:
    from coco.robot.posture_baseline import (
        PostureBaseline, PostureBaselineModulator, PostureBaselineConfig, ZERO_OFFSET,
    )
    from coco.emotion import Emotion
    from coco.power_state import PowerState, PowerStateMachine, PowerConfig

    bl = PostureBaseline()
    off = bl.compute(Emotion.HAPPY, PowerState.SLEEP)
    check("SLEEP 表查 ZERO_OFFSET", off == ZERO_OFFSET)

    # Modulator: 注入 power_state 当前为 SLEEP，current_offset() 返回 ZERO
    pcfg = PowerConfig(drowsy_after=10.0, sleep_after=20.0)
    psm = PowerStateMachine(config=pcfg)
    # 强制把状态设到 SLEEP（直接 _transit）
    psm._transit_locked(PowerState.SLEEP, source="test")  # noqa: SLF001
    cfg = PostureBaselineConfig(enabled=True, ramp_s=0.5, tick_interval_s=0.05, debounce_s=0.0)
    mod = PostureBaselineModulator(robot=make_mock_robot(), power_state=psm, config=cfg)
    cur = mod.current_offset()
    check("SLEEP current_offset == ZERO", cur == ZERO_OFFSET, f"got {cur}")
except Exception:  # noqa: BLE001
    errors.append("V5: " + traceback.format_exc())


# =======================================================================
# V6
# =======================================================================
print("V6: PostureOffset.clamped() 严格在 ±5° / ±3° / [0,1]")
try:
    from coco.robot.posture_baseline import PostureOffset
    o = PostureOffset(pitch_deg=99.0, yaw_deg=-99.0, antenna=99.0).clamped()
    check("pitch clamp 5.0", o.pitch_deg == 5.0)
    check("yaw clamp -3.0", o.yaw_deg == -3.0)
    check("antenna clamp 1.0", o.antenna == 1.0)
    o2 = PostureOffset(pitch_deg=-99.0, yaw_deg=99.0, antenna=-99.0).clamped()
    check("pitch clamp -5.0", o2.pitch_deg == -5.0)
    check("yaw clamp 3.0", o2.yaw_deg == 3.0)
    check("antenna clamp 0.0", o2.antenna == 0.0)
except Exception:  # noqa: BLE001
    errors.append("V6: " + traceback.format_exc())


# =======================================================================
# V7
# =======================================================================
print("V7: emotion 切换 → 2s linear ramp（中段在 from..to 之间）")
try:
    from coco.robot.posture_baseline import (
        PostureBaselineModulator, PostureBaselineConfig, PostureOffset,
    )

    class FakeEmoTracker:
        def __init__(self, emo): self._e = emo
        def effective(self): return self._e

    class FakePower:
        def __init__(self, st): self._st = st
        @property
        def current_state(self): return self._st

    # 用 fake clock 推进
    clk = {"t": 100.0}
    def _clock(): return clk["t"]

    from coco.emotion import Emotion
    from coco.power_state import PowerState
    emo = FakeEmoTracker(Emotion.HAPPY)
    pwr = FakePower(PowerState.ACTIVE)
    cfg = PostureBaselineConfig(enabled=True, ramp_s=2.0, tick_interval_s=0.05, debounce_s=0.0)
    mod = PostureBaselineModulator(
        robot=make_mock_robot(),
        emotion_tracker=emo, power_state=pwr,
        config=cfg, clock=_clock,
    )
    # 手工 init：snapshot + tick 一次
    mod._target = mod._snapshot_target()  # noqa: SLF001
    mod._current = mod._target  # noqa: SLF001
    mod._ramp_from = mod._target  # noqa: SLF001
    happy_pitch = mod._current.pitch_deg
    check("初始 happy pitch=-3", abs(happy_pitch - (-3.0)) < 1e-6)

    # 切到 SAD：begin_ramp，然后推进 1s（=ramp 50%）
    emo._e = Emotion.SAD
    clk["t"] += 0.0
    mod._tick_once()  # noqa: SLF001  → begin_ramp + advance 0
    # 推进 1.0s（ramp 50%）
    clk["t"] += 1.0
    mod._tick_once()  # noqa: SLF001
    mid = mod._current.pitch_deg
    check("ramp 中段 pitch 在 happy(-3) 与 sad(+3) 之间",
          -3.0 < mid < 3.0, f"got {mid:.3f}")
    # 推进到 ramp 结束
    clk["t"] += 1.5
    mod._tick_once()  # noqa: SLF001
    end = mod._current.pitch_deg
    check("ramp 结束 pitch == sad(+3)", abs(end - 3.0) < 1e-6, f"got {end:.3f}")
except Exception:  # noqa: BLE001
    errors.append("V7: " + traceback.format_exc())


# =======================================================================
# V8
# =======================================================================
print("V8: SLEEP 下 current_offset == ZERO，sleep_skipped 累加，无天线下发")
try:
    from coco.robot.posture_baseline import (
        PostureBaselineModulator, PostureBaselineConfig, ZERO_OFFSET,
    )

    class FakePower2:
        def __init__(self, st): self._st = st
        @property
        def current_state(self): return self._st

    from coco.power_state import PowerState
    pwr = FakePower2(PowerState.SLEEP)
    r = make_mock_robot()
    cfg = PostureBaselineConfig(enabled=True, ramp_s=0.5, tick_interval_s=0.05, debounce_s=0.0)
    mod = PostureBaselineModulator(robot=r, power_state=pwr, config=cfg)
    mod._tick_once()  # noqa: SLF001
    mod._tick_once()  # noqa: SLF001
    check("SLEEP current_offset == ZERO", mod.current_offset() == ZERO_OFFSET)
    check("SLEEP sleep_skipped >= 2", mod.stats.sleep_skipped >= 2,
          f"got {mod.stats.sleep_skipped}")
    check("SLEEP 期间无天线下发",
          r.set_target_antenna_joint_positions.call_count == 0,
          f"got {r.set_target_antenna_joint_positions.call_count}")
except Exception:  # noqa: BLE001
    errors.append("V8: " + traceback.format_exc())


# =======================================================================
# V9
# =======================================================================
print("V9: pause/resume — pause 期间停止天线下发，ramp 仍在内部推进")
try:
    from coco.robot.posture_baseline import (
        PostureBaselineModulator, PostureBaselineConfig,
    )

    class FE:
        def __init__(self, emo): self.e = emo
        def effective(self): return self.e

    class FP:
        def __init__(self, s): self.s = s
        @property
        def current_state(self): return self.s

    from coco.emotion import Emotion
    from coco.power_state import PowerState
    clk = {"t": 0.0}
    cfg = PostureBaselineConfig(enabled=True, ramp_s=1.0, tick_interval_s=0.05, debounce_s=0.0)
    r = make_mock_robot()
    mod = PostureBaselineModulator(
        robot=r,
        emotion_tracker=FE(Emotion.HAPPY),
        power_state=FP(PowerState.ACTIVE),
        config=cfg, clock=lambda: clk["t"],
    )
    mod._tick_once()  # noqa: SLF001 init target=happy
    mod.pause()
    n_before = r.set_target_antenna_joint_positions.call_count
    # tick 数次，pause 期间不应下发
    for _ in range(5):
        clk["t"] += 0.05
        mod._tick_once()  # noqa: SLF001
    n_after = r.set_target_antenna_joint_positions.call_count
    check("pause 期间天线 0 下发", n_after == n_before, f"before={n_before} after={n_after}")
    check("pause 期间 paused_skipped 累加", mod.stats.paused_skipped >= 5,
          f"got {mod.stats.paused_skipped}")
    mod.resume()
    clk["t"] += 0.05
    mod._tick_once()  # noqa: SLF001
    check("resume 后天线下发恢复",
          r.set_target_antenna_joint_positions.call_count > n_after)
except Exception:  # noqa: BLE001
    errors.append("V9: " + traceback.format_exc())


# =======================================================================
# V10
# =======================================================================
print("V10: emit robot.posture_baseline_changed payload 含 from/to/emotion/power")
try:
    from coco.robot.posture_baseline import (
        PostureBaselineModulator, PostureBaselineConfig,
    )

    captured: List[tuple] = []
    def fake_emit(event, message="", **payload):
        captured.append((event, payload))

    class FE2:
        def __init__(self): self.e = None
        def effective(self): return self.e

    class FP2:
        def __init__(self, s): self.s = s
        @property
        def current_state(self): return self.s

    from coco.emotion import Emotion
    from coco.power_state import PowerState
    clk = {"t": 0.0}
    cfg = PostureBaselineConfig(enabled=True, ramp_s=1.0, tick_interval_s=0.05, debounce_s=0.0)
    fe = FE2()
    fe.e = Emotion.NEUTRAL
    mod = PostureBaselineModulator(
        robot=make_mock_robot(),
        emotion_tracker=fe, power_state=FP2(PowerState.ACTIVE),
        config=cfg, clock=lambda: clk["t"], emit_fn=fake_emit,
    )
    mod._tick_once()  # noqa: SLF001 → init neutral; first ramp from ZERO->neutral
    captured.clear()
    fe.e = Emotion.HAPPY
    clk["t"] += 0.1
    mod._tick_once()  # noqa: SLF001 → 应触发 ramp，emit posture_baseline_changed
    found = [e for e in captured if e[0] == "robot.posture_baseline_changed"]
    check("至少一次 robot.posture_baseline_changed", len(found) >= 1)
    if found:
        ev, p = found[0]
        for k in ("from_pitch", "to_pitch", "from_yaw", "to_yaw",
                  "from_antenna", "to_antenna", "emotion", "power_state", "ramp_s"):
            check(f"payload 含 {k}", k in p, f"got keys {sorted(p.keys())}")
        check("emotion=happy", p.get("emotion") == "happy", f"got {p.get('emotion')}")
        check("power_state=active", p.get("power_state") == "active", f"got {p.get('power_state')}")
except Exception:  # noqa: BLE001
    errors.append("V10: " + traceback.format_exc())


# =======================================================================
# V11
# =======================================================================
print("V11: emotion 误判抖动被 debounce 抑制")
try:
    from coco.robot.posture_baseline import (
        PostureBaselineModulator, PostureBaselineConfig,
    )
    from coco.emotion import Emotion
    from coco.power_state import PowerState

    class FE3:
        def __init__(self): self.e = Emotion.HAPPY
        def effective(self): return self.e

    class FP3:
        def __init__(self): self.s = PowerState.ACTIVE
        @property
        def current_state(self): return self.s

    clk = {"t": 0.0}
    cfg = PostureBaselineConfig(enabled=True, ramp_s=0.5, tick_interval_s=0.05, debounce_s=5.0)
    fe = FE3()
    mod = PostureBaselineModulator(
        robot=make_mock_robot(),
        emotion_tracker=fe, power_state=FP3(),
        config=cfg, clock=lambda: clk["t"],
    )
    mod._tick_once()  # noqa: SLF001 → init happy（target_changes=1, last_change=0）
    n_changes_init = mod.stats.target_changes
    # 把时间推过 debounce 后切到 SAD —— 一次 ramp（成功）
    fe.e = Emotion.SAD
    clk["t"] += 6.0  # 越过 debounce 5s
    mod._tick_once()  # noqa: SLF001
    n_after_sad = mod.stats.target_changes
    check("SAD 切换（debounce 外）触发一次 target_change",
          n_after_sad == n_changes_init + 1,
          f"init={n_changes_init} after={n_after_sad}")
    # 立刻又切到 HAPPY（在 debounce 5s 内）—— 应被 skip
    fe.e = Emotion.HAPPY
    clk["t"] += 0.5  # 远 < 5s
    mod._tick_once()  # noqa: SLF001
    check("debounce 内重切 target_change 不增", mod.stats.target_changes == n_after_sad,
          f"got changes={mod.stats.target_changes}")
    check("debounce_skipped >= 1", mod.stats.debounce_skipped >= 1,
          f"got {mod.stats.debounce_skipped}")
    # 越过 debounce 后切换到不同 target（ANGRY；当前 target 是 SAD，HAPPY 被 skip 未生效）
    clk["t"] += 6.0
    fe.e = Emotion.ANGRY
    mod._tick_once()  # noqa: SLF001
    check("越过 debounce 后切换被接受",
          mod.stats.target_changes == n_after_sad + 1,
          f"got {mod.stats.target_changes}")
except Exception:  # noqa: BLE001
    errors.append("V11: " + traceback.format_exc())


# =======================================================================
# V12
# =======================================================================
print("V12: IdleAnimator._micro_head 叠加 baseline offset")
try:
    from coco.idle import IdleAnimator, IdleConfig

    class StubBaseline:
        from coco.robot.posture_baseline import PostureOffset as _PO
        def current_offset(self):
            return self._PO(pitch_deg=2.0, yaw_deg=-1.0, antenna=0.5)

    r = make_mock_robot()
    stop = threading.Event()
    cfg = IdleConfig()
    cfg.validate()
    anim = IdleAnimator(r, stop, config=cfg, posture_baseline=StubBaseline())
    # 直接调 _micro_head 多次，统计 head 调用的中位
    sums = {"pitch": 0.0, "yaw": 0.0}
    N = 200
    import numpy as np
    for _ in range(N):
        r.goto_target.reset_mock()
        anim._micro_head()  # noqa: SLF001
        # extract head pose from goto_target call
        last = r.goto_target.call_args
        head = last.kwargs["head"]
        # head is 4x4 numpy; convert back via inverse euler — 太复杂，改为读 stub_pitch/yaw 累加
        # 这里采用近似：调用前把 idle 的 sample 压到 0（用 rng patch）
    # 改用更直接的断言：在 sample=0 + baseline=(2, -1) 情况下，目标矩阵应等价 euler_pose(2,-1)
    import random
    from coco.actions import euler_pose
    anim2 = IdleAnimator(r, stop, config=cfg, posture_baseline=StubBaseline(),
                         rng=random.Random(0))
    # patch rng.uniform to always return 0
    anim2.rng.uniform = lambda a, b: 0.0  # type: ignore[assignment]
    r.goto_target.reset_mock()
    anim2._micro_head()  # noqa: SLF001
    head = r.goto_target.call_args.kwargs["head"]
    expected = euler_pose(pitch_deg=2.0, yaw_deg=-1.0)
    check("baseline 叠加后 micro_head 目标 = euler_pose(baseline_pitch, baseline_yaw)",
          np.allclose(head, expected),
          f"head[:3,:3]={head[:3,:3].tolist()} expected[:3,:3]={expected[:3,:3].tolist()}")
except Exception:  # noqa: BLE001
    errors.append("V12: " + traceback.format_exc())


# =======================================================================
# V13
# =======================================================================
print("V13: IdleAnimator._breathe 在 baseline 启用时回的中位是 baseline pose")
try:
    from coco.idle import IdleAnimator, IdleConfig
    from coco.actions import INIT_HEAD_POSE, euler_pose
    import numpy as np

    class B2:
        from coco.robot.posture_baseline import PostureOffset as _PO
        def current_offset(self):
            return self._PO(pitch_deg=3.0, yaw_deg=2.0, antenna=0.5)

    r = make_mock_robot()
    stop = threading.Event()
    cfg = IdleConfig()
    anim = IdleAnimator(r, stop, config=cfg, posture_baseline=B2())
    r.goto_target.reset_mock()
    anim._breathe()  # noqa: SLF001
    # _breathe 调两次：head + (antenna)，head 是 goto_target，antenna 是 set_target_antenna_joint_positions
    head_calls = [c for c in r.goto_target.call_args_list]
    check("breathe head 调用 1 次", len(head_calls) == 1)
    head = head_calls[0].kwargs["head"]
    expected = euler_pose(pitch_deg=3.0, yaw_deg=2.0)
    check("breathe head pose = baseline pose（非 INIT_HEAD_POSE）",
          np.allclose(head, expected) and not np.allclose(head, INIT_HEAD_POSE))

    # baseline=ZERO 情况下应回 INIT_HEAD_POSE（向后兼容）
    class B3:
        from coco.robot.posture_baseline import PostureOffset as _PO
        def current_offset(self):
            return self._PO(pitch_deg=0.0, yaw_deg=0.0, antenna=0.5)

    r2 = make_mock_robot()
    anim3 = IdleAnimator(r2, stop, config=cfg, posture_baseline=B3())
    anim3._breathe()  # noqa: SLF001
    head2 = r2.goto_target.call_args.kwargs["head"]
    check("baseline=ZERO 时 breathe head = INIT_HEAD_POSE",
          np.allclose(head2, INIT_HEAD_POSE))
except Exception:  # noqa: BLE001
    errors.append("V13: " + traceback.format_exc())


# =======================================================================
# V14
# =======================================================================
print("V14: ExpressionPlayer.play 调 baseline.pause / resume")
try:
    from coco.robot.expressions import ExpressionPlayer, ExpressionsConfig

    pause_calls = []
    class StubBL:
        def pause(self): pause_calls.append("pause")
        def resume(self): pause_calls.append("resume")

    r = make_mock_robot()
    bl = StubBL()
    player = ExpressionPlayer(r, config=ExpressionsConfig(enabled=True), posture_baseline=bl)
    ok = player.play("welcome")
    check("play 成功", ok is True)
    check("baseline.pause 被调用", "pause" in pause_calls)
    check("baseline.resume 被调用", "resume" in pause_calls)
    # 顺序：pause 在 resume 之前
    check("pause 在 resume 之前",
          pause_calls.index("pause") < pause_calls.index("resume"))
except Exception:  # noqa: BLE001
    errors.append("V14: " + traceback.format_exc())


# =======================================================================
# V15
# =======================================================================
print("V15: 与 SituationalIdleModulator 叠加后总幅度 clamp 在 actions.MAX_PITCH/YAW 内")
try:
    from coco.idle import IdleAnimator, IdleConfig
    from coco.actions import MAX_PITCH_DEG, MAX_YAW_DEG, euler_pose
    from coco.companion.situational_idle import IdleBias
    import numpy as np
    import math

    class StubSit:
        def tick(self): return IdleBias(micro_amp_scale=2.0, glance_prob_scale=1.0, glance_amp_scale=1.0)

    class B5:
        from coco.robot.posture_baseline import PostureOffset as _PO
        def current_offset(self):
            return self._PO(pitch_deg=5.0, yaw_deg=3.0, antenna=1.0)  # 顶天

    r = make_mock_robot()
    stop = threading.Event()
    cfg = IdleConfig()
    anim = IdleAnimator(r, stop, config=cfg, posture_baseline=B5(),
                        situational_modulator=StubSit())
    # 强制 rng.uniform 返回 amp 上限
    anim.rng.uniform = lambda a, b: b  # type: ignore[assignment]
    anim._micro_head()  # noqa: SLF001
    head = r.goto_target.call_args.kwargs["head"]
    # 解出 pitch/yaw（4x4 矩阵 → ZYX euler）
    # head = R = Rz(yaw) @ Ry(pitch) @ Rx(roll)，提取 pitch 用 -arcsin(R[2,0])
    R = head[:3, :3]
    pitch_back_deg = math.degrees(-math.asin(max(-1.0, min(1.0, R[2, 0]))))
    yaw_back_deg = math.degrees(math.atan2(R[1, 0], R[0, 0]))
    check(f"叠加后 pitch_abs <= MAX_PITCH_DEG({MAX_PITCH_DEG})",
          abs(pitch_back_deg) <= MAX_PITCH_DEG + 1e-3, f"got {pitch_back_deg:.3f}")
    check(f"叠加后 yaw_abs <= MAX_YAW_DEG({MAX_YAW_DEG})",
          abs(yaw_back_deg) <= MAX_YAW_DEG + 1e-3, f"got {yaw_back_deg:.3f}")
except Exception:  # noqa: BLE001
    errors.append("V15: " + traceback.format_exc())


# =======================================================================
# V16
# =======================================================================
print("V16: SDK 异常 fail-soft")
try:
    from coco.robot.posture_baseline import (
        PostureBaselineModulator, PostureBaselineConfig,
    )

    class FE4:
        def effective(self): return None  # neutral fallback

    class FP4:
        @property
        def current_state(self):
            from coco.power_state import PowerState
            return PowerState.ACTIVE

    r = MagicMock()
    r.set_target_antenna_joint_positions = MagicMock(side_effect=RuntimeError("zenoh dropped"))
    cfg = PostureBaselineConfig(enabled=True, ramp_s=0.5, tick_interval_s=0.05, debounce_s=0.0)
    mod = PostureBaselineModulator(
        robot=r, emotion_tracker=FE4(), power_state=FP4(), config=cfg,
    )
    # tick 不抛
    mod._tick_once()  # noqa: SLF001
    mod._tick_once()  # noqa: SLF001
    check("SDK 抛但 tick 不冒泡", True)
    check("stats.sdk_errors >= 2", mod.stats.sdk_errors >= 2,
          f"got {mod.stats.sdk_errors}")
except Exception:  # noqa: BLE001
    errors.append("V16: " + traceback.format_exc())


# =======================================================================
# V17
# =======================================================================
print("V17: emotion_tracker / power_state = None → (neutral, active) → 中位 baseline")
try:
    from coco.robot.posture_baseline import (
        PostureBaselineModulator, PostureBaselineConfig, ZERO_OFFSET,
    )
    cfg = PostureBaselineConfig(enabled=True, ramp_s=0.5, tick_interval_s=0.05, debounce_s=0.0)
    mod = PostureBaselineModulator(
        robot=make_mock_robot(),
        emotion_tracker=None, power_state=None,
        config=cfg,
    )
    mod._tick_once()  # noqa: SLF001
    cur = mod.current_offset()
    check("None 输入 current_offset == ZERO_OFFSET",
          cur == ZERO_OFFSET, f"got {cur}")
except Exception:  # noqa: BLE001
    errors.append("V17: " + traceback.format_exc())


# =======================================================================
# 汇总
# =======================================================================
elapsed = time.time() - t0
print()
if errors:
    print(f"FAIL ({len(errors)} 项): {elapsed:.2f}s")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print(f"PASS: {elapsed:.2f}s")
    sys.exit(0)
