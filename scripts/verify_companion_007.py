"""companion-007 verify: 情绪驱动 TTS prosody + 表情节律.

V1  默认 OFF（COCO_EMOTION_PROSODY 未设 → enabled=False；EmotionRenderer.start() noop；
    say() 不接收 rate / pitch_semitone 时行为与 phase-3 一致）
V2  happy → tts_rate=+0.05, pitch_semitone=+1, expr_overlay='excited', antenna_pulse=True
V3  sad → tts_rate=-0.10, pitch_semitone=-1, expr_overlay=None, antenna_pulse=False
V4  TTS pitch_semitone fallback：backend 不支持 pitch → emit 'tts.prosody_unsupported'
    （每进程一次），仍正常播放（tts.synthesize 被调用）
V5  5s emotion debounce 复用 baseline：同一秒内 happy→happy→sad 只 emit 1 次 style 变更
V6  EmotionRenderer 与 PostureBaseline 同源：同一次 baseline._snapshot_target 触发，
    listener 收到的 emotion 对象与 baseline 用的对象 ``is`` 同一实例
V7  ExpressionPlayer 主动播放期间 EmotionRenderer 不抢 antenna_pulse / overlay
    （player.is_busy() 时 _dispatch_antenna_pulse / _dispatch_overlay short-circuit）
V8  env clamp / 非法值：clamp tts_rate ∈ [-30%, +30%]、pitch ∈ [-3, +3]；
    非法 COCO_EMOTION_PROSODY 值（"abc"）→ enabled=False
V9  回归：interact-006 + robot-004 + companion-005 verify 仍通过
V10 emit 'companion.emotion_render_changed' payload 含 emotion / tts_rate / tts_pitch_semitone
    / expr_overlay / antenna_pulse / fallback
V11 antenna pulse 与 baseline pause/resume 协议一致：pulse 期间 baseline.is_paused()=True，
    pulse 结束后 is_paused()=False
"""
from __future__ import annotations

import os
import subprocess
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
    "COCO_EMOTION_PROSODY",
    "COCO_EMOTION_PROSODY_PULSE_S",
    "COCO_POSTURE_BASELINE",
    "COCO_POSTURE_BASELINE_RAMP_S",
    "COCO_POSTURE_BASELINE_TICK_S",
    "COCO_POSTURE_BASELINE_DEBOUNCE_S",
    "COCO_EMOTION",
):
    os.environ.pop(k, None)


# =======================================================================
# V1: 默认 OFF
# =======================================================================
print("V1: 默认 OFF")
try:
    from coco.companion.emotion_renderer import (
        EmotionRenderer,
        emotion_renderer_config_from_env,
        emotion_prosody_enabled_from_env,
        NEUTRAL_STYLE,
    )

    cfg = emotion_renderer_config_from_env()
    check("默认 enabled=False", cfg.enabled is False)
    check("emotion_prosody_enabled_from_env=False", emotion_prosody_enabled_from_env() is False)

    # start() noop（不报错，不订阅）
    fake_baseline = MagicMock()
    fake_baseline.add_listener = MagicMock()
    renderer = EmotionRenderer(
        posture_baseline=fake_baseline,
        config=cfg,
    )
    renderer.start()
    check("disabled 时未注册 listener", fake_baseline.add_listener.call_count == 0)
    check("disabled 时 current_style == NEUTRAL", renderer.current_style() == NEUTRAL_STYLE)
    # apply_to_tts_kwargs 不注入键
    kw = {"sid": 50}
    renderer.apply_to_tts_kwargs(kw)
    check("disabled 时 apply_to_tts_kwargs 不注入 rate/pitch",
          "rate" not in kw and "pitch_semitone" not in kw)

    # 验证 say() 不传 rate/pitch_semitone 时行为兼容（不抛、不影响 expression 路径）
    import coco.tts as coco_tts
    coco_tts.reset_prosody_fallback_emit_flag()
    # 不真合成（避免依赖 Kokoro 模型），只确认签名兼容
    import inspect
    sig = inspect.signature(coco_tts.say)
    check("say() 签名含 rate / pitch_semitone（向后兼容默认 None）",
          "rate" in sig.parameters and "pitch_semitone" in sig.parameters
          and sig.parameters["rate"].default is None
          and sig.parameters["pitch_semitone"].default is None)
    sig2 = inspect.signature(coco_tts.say_async)
    check("say_async() 签名含 rate / pitch_semitone",
          "rate" in sig2.parameters and "pitch_semitone" in sig2.parameters)
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())


# =======================================================================
# V2: happy 风格
# =======================================================================
print("V2: happy → rate=+0.05 / pitch=+1 / overlay='excited' / pulse=True")
try:
    from coco.companion.emotion_renderer import style_for_emotion
    from coco.emotion import Emotion

    st = style_for_emotion(Emotion.HAPPY)
    check("happy.tts_rate == +0.05", abs(st.tts_rate - 0.05) < 1e-9, f"got {st.tts_rate}")
    check("happy.tts_pitch_semitone == +1", abs(st.tts_pitch_semitone - 1.0) < 1e-9,
          f"got {st.tts_pitch_semitone}")
    check("happy.expr_overlay == 'excited'", st.expr_overlay == "excited",
          f"got {st.expr_overlay!r}")
    check("happy.antenna_pulse == True", st.antenna_pulse is True)
except Exception:  # noqa: BLE001
    errors.append("V2: " + traceback.format_exc())


# =======================================================================
# V3: sad 风格
# =======================================================================
print("V3: sad → rate=-0.10 / pitch=-1 / overlay=None / pulse=False")
try:
    from coco.companion.emotion_renderer import style_for_emotion
    from coco.emotion import Emotion

    st = style_for_emotion(Emotion.SAD)
    check("sad.tts_rate == -0.10", abs(st.tts_rate - (-0.10)) < 1e-9, f"got {st.tts_rate}")
    check("sad.tts_pitch_semitone == -1", abs(st.tts_pitch_semitone - (-1.0)) < 1e-9,
          f"got {st.tts_pitch_semitone}")
    check("sad.expr_overlay is None", st.expr_overlay is None)
    check("sad.antenna_pulse is False", st.antenna_pulse is False)

    # neutral / focused / 未知
    from coco.companion.emotion_renderer import NEUTRAL_STYLE
    check("neutral == NEUTRAL_STYLE", style_for_emotion(Emotion.NEUTRAL) == NEUTRAL_STYLE)
    check("未知字符串 → NEUTRAL_STYLE",
          style_for_emotion("nonexistent_emo") == NEUTRAL_STYLE)
    check("None → NEUTRAL_STYLE", style_for_emotion(None) == NEUTRAL_STYLE)
    check("'focused' → NEUTRAL_STYLE", style_for_emotion("focused") == NEUTRAL_STYLE)
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4: pitch_semitone fallback emit + 仍合成
# =======================================================================
print("V4: pitch_semitone unsupported → emit tts.prosody_unsupported (一次)")
try:
    import coco.tts as coco_tts
    from coco.logging_setup import emit as _emit_orig

    coco_tts.reset_prosody_fallback_emit_flag()

    # 拦截 emit 收集事件
    captured: list = []

    def _capture(component_event, message="", **payload):
        captured.append((component_event, message, payload))

    import coco.tts as _tts_mod
    # monkey-patch logging_setup.emit through coco.tts 的 import 路径
    import coco.logging_setup as _logmod
    original_emit = _logmod.emit
    _logmod.emit = _capture
    try:
        # 拦截 synthesize / play 不真跑 Kokoro
        import numpy as np
        original_synth = _tts_mod.synthesize
        original_play = _tts_mod.play
        synth_calls = []
        play_calls = []
        def fake_synth(text, sid=50, speed=1.0):
            synth_calls.append((text, sid, speed))
            return np.zeros(160, dtype=np.float32), 16000
        def fake_play(samples, sample_rate, blocking=True):
            play_calls.append((len(samples), sample_rate))
        _tts_mod.synthesize = fake_synth
        _tts_mod.play = fake_play
        try:
            # 第一次 say 带 pitch → 应 emit
            _tts_mod.say("你好", rate=+0.05, pitch_semitone=+1.0)
            # 第二次 say 带 pitch → 不应再次 emit（每进程一次）
            _tts_mod.say("你好", rate=+0.05, pitch_semitone=+1.0)
        finally:
            _tts_mod.synthesize = original_synth
            _tts_mod.play = original_play
    finally:
        _logmod.emit = original_emit

    prosody_events = [c for c in captured if c[0] == "tts.prosody_unsupported"]
    check("emit 'tts.prosody_unsupported' 至少一次", len(prosody_events) >= 1,
          f"got {len(prosody_events)}")
    check("emit 仅一次（进程级 dedup）", len(prosody_events) == 1,
          f"got {len(prosody_events)}")
    # 仍合成 + 播放
    check("synthesize 仍被调用 2 次（fallback 不阻塞）", len(synth_calls) == 2,
          f"got {synth_calls}")
    check("play 仍被调用 2 次", len(play_calls) == 2)
    # rate=+0.05 → speed *= 1.05 = 1.05
    if synth_calls:
        speed_used = synth_calls[0][2]
        check("rate=+0.05 → 等效 speed=1.05", abs(speed_used - 1.05) < 1e-6,
              f"got speed={speed_used}")
    # payload 完整
    if prosody_events:
        payload = prosody_events[0][2]
        check("payload 含 supports_pitch=False",
              payload.get("supports_pitch") is False)
        check("payload 含 backend 字段",
              "backend" in payload)
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5: 5s debounce 复用 baseline
# =======================================================================
print("V5: 5s debounce 复用 baseline（连续相同 emotion 在 debounce 内只触发 1 次）")
try:
    os.environ["COCO_EMOTION_PROSODY"] = "1"
    os.environ["COCO_POSTURE_BASELINE"] = "1"
    os.environ["COCO_POSTURE_BASELINE_DEBOUNCE_S"] = "5"
    os.environ["COCO_POSTURE_BASELINE_RAMP_S"] = "0.2"
    os.environ["COCO_POSTURE_BASELINE_TICK_S"] = "0.05"
    from coco.robot.posture_baseline import (
        PostureBaselineModulator,
        posture_baseline_config_from_env,
        PostureBaseline,
    )
    from coco.companion.emotion_renderer import (
        EmotionRenderer,
        emotion_renderer_config_from_env,
    )
    from coco.emotion import Emotion, EmotionTracker, EmotionLabel

    pb_cfg = posture_baseline_config_from_env()
    er_cfg = emotion_renderer_config_from_env()

    # fake clock；emotion tracker 我们手动插值
    fake_now = [1000.0]
    def clk():
        return fake_now[0]

    tracker = EmotionTracker(decay_s=60.0, clock=clk)
    robot = make_mock_robot()
    mod = PostureBaselineModulator(
        robot=robot,
        emotion_tracker=tracker,
        power_state=None,
        config=pb_cfg,
        clock=clk,
    )
    listener_calls: list = []
    renderer = EmotionRenderer(
        posture_baseline=mod,
        expression_player=None,  # 不挂 player，避免 overlay 干扰
        robot=robot,
        config=er_cfg,
        emit_fn=lambda *a, **k: listener_calls.append((a, k)),
        clock=clk,
    )
    renderer.start()
    check("renderer.start 注册到 baseline.add_listener", renderer._listener_registered)

    # 直接驱动 _tick_once 而不起 thread，方便控制时间
    # 第 1 次：happy → ramp 启动
    tracker.record(EmotionLabel(Emotion.HAPPY, 0.5, ["开心"]), now=fake_now[0])
    mod._tick_once()
    fake_now[0] += 0.1
    # 第 2 次：仍 happy → 不应触发新 ramp（target 没变）
    mod._tick_once()
    fake_now[0] += 0.1
    # 第 3 次：sad → target 变，但 debounce 内（<5s）→ 应被 debounce_skipped
    tracker.record(EmotionLabel(Emotion.SAD, 0.6, ["难过"]), now=fake_now[0])
    mod._tick_once()
    style_change_events = [c for c in listener_calls if c[0][0] == "companion.emotion_render_changed"]
    check("debounce 内仅 1 次 emotion_render_changed",
          len(style_change_events) == 1, f"got {len(style_change_events)}")
    check("baseline.stats.debounce_skipped >= 1", mod.stats.debounce_skipped >= 1,
          f"got {mod.stats.debounce_skipped}")

    # 时间走过 5s debounce → 此时再 sad 应触发
    fake_now[0] += 6.0
    mod._tick_once()
    style_change_events_2 = [c for c in listener_calls if c[0][0] == "companion.emotion_render_changed"]
    check("5s 后 sad 触发新 emotion_render_changed",
          len(style_change_events_2) == 2, f"got {len(style_change_events_2)}")
finally:
    for k in ("COCO_EMOTION_PROSODY", "COCO_POSTURE_BASELINE",
              "COCO_POSTURE_BASELINE_DEBOUNCE_S", "COCO_POSTURE_BASELINE_RAMP_S",
              "COCO_POSTURE_BASELINE_TICK_S"):
        os.environ.pop(k, None)


# =======================================================================
# V6: 同源 emotion 实例
# =======================================================================
print("V6: EmotionRenderer 与 PostureBaseline 同源（同一 emotion 对象）")
try:
    os.environ["COCO_EMOTION_PROSODY"] = "1"
    os.environ["COCO_POSTURE_BASELINE"] = "1"
    os.environ["COCO_POSTURE_BASELINE_DEBOUNCE_S"] = "0"
    os.environ["COCO_POSTURE_BASELINE_RAMP_S"] = "0.2"
    from coco.robot.posture_baseline import (
        PostureBaselineModulator,
        posture_baseline_config_from_env,
    )
    from coco.companion.emotion_renderer import (
        EmotionRenderer,
        emotion_renderer_config_from_env,
    )
    from coco.emotion import Emotion

    pb_cfg = posture_baseline_config_from_env()
    er_cfg = emotion_renderer_config_from_env()

    # 自定义 emotion_tracker 总是返回同一个 Emotion.HAPPY 实例
    SHARED_EMOTION = Emotion.HAPPY

    class FakeTracker:
        def effective(self):
            return SHARED_EMOTION

    captured_in_listener: list = []
    captured_in_baseline: list = []

    # 我们 monkey-patch baseline.compute 来抓 baseline 收到的对象
    from coco.robot.posture_baseline import PostureBaseline as _PB, ZERO_OFFSET
    original_compute = _PB.compute
    def spy_compute(self, emotion, power_state):
        captured_in_baseline.append(emotion)
        return original_compute(self, emotion, power_state)
    _PB.compute = spy_compute

    try:
        mod = PostureBaselineModulator(
            robot=make_mock_robot(),
            emotion_tracker=FakeTracker(),
            power_state=None,
            config=pb_cfg,
        )
        renderer = EmotionRenderer(
            posture_baseline=mod,
            expression_player=None,
            robot=make_mock_robot(),
            config=er_cfg,
        )
        # 自定义 listener 抓 emotion 对象
        def my_listener(emo, ps):
            captured_in_listener.append(emo)
        mod.add_listener(my_listener)

        renderer.start()
        # 一次 tick
        mod._tick_once()

        check("baseline.compute 被调用至少一次", len(captured_in_baseline) >= 1)
        check("listener 收到 emotion 至少一次", len(captured_in_listener) >= 1)
        if captured_in_baseline and captured_in_listener:
            check("listener 收到的 emotion 与 baseline.compute 是同一对象 (is)",
                  captured_in_listener[0] is captured_in_baseline[0],
                  f"listener={captured_in_listener[0]!r} baseline={captured_in_baseline[0]!r}")
    finally:
        _PB.compute = original_compute
finally:
    for k in ("COCO_EMOTION_PROSODY", "COCO_POSTURE_BASELINE",
              "COCO_POSTURE_BASELINE_DEBOUNCE_S", "COCO_POSTURE_BASELINE_RAMP_S"):
        os.environ.pop(k, None)


# =======================================================================
# V7: ExpressionPlayer 主动播放期间 EmotionRenderer 不抢
# =======================================================================
print("V7: ExpressionPlayer 主动播放期间 EmotionRenderer 不抢 antenna / overlay")
try:
    from coco.companion.emotion_renderer import (
        EmotionRenderer,
        EmotionRendererConfig,
    )

    cfg = EmotionRendererConfig(enabled=True, pulse_s=0.05)

    # fake player.is_busy() 永远 True
    fake_player = MagicMock()
    fake_player.is_busy = MagicMock(return_value=True)
    fake_player.play = MagicMock(return_value=True)

    fake_baseline = MagicMock()
    fake_baseline.pause = MagicMock()
    fake_baseline.resume = MagicMock()

    robot = make_mock_robot()
    renderer = EmotionRenderer(
        posture_baseline=fake_baseline,
        expression_player=fake_player,
        robot=robot,
        config=cfg,
    )
    # 直接调内部 dispatch（绕过 listener，专测 short-circuit）
    renderer._dispatch_antenna_pulse()
    time.sleep(0.1)
    check("player.is_busy=True 时 antenna_pulse 跳过 → robot.set_target_antenna_joint_positions 未被 EmotionRenderer 调用",
          robot.set_target_antenna_joint_positions.call_count == 0,
          f"got {robot.set_target_antenna_joint_positions.call_count}")

    # overlay 也跳过
    renderer._dispatch_overlay("excited")
    time.sleep(0.05)
    check("player.is_busy=True 时 overlay 跳过 → player.play 未被调用",
          fake_player.play.call_count == 0)
    check("stats.overlays_skipped_busy >= 1", renderer.stats.overlays_skipped_busy >= 1)

    # 再测 is_busy=False → 真触发
    fake_player.is_busy = MagicMock(return_value=False)
    renderer._dispatch_overlay("excited")
    time.sleep(0.1)
    check("player.is_busy=False 时 overlay play 被调用",
          fake_player.play.call_count >= 1,
          f"got {fake_player.play.call_count}")
except Exception:  # noqa: BLE001
    errors.append("V7: " + traceback.format_exc())


# =======================================================================
# V8: env clamp / 非法值
# =======================================================================
print("V8: env clamp + 非法值")
try:
    from coco.companion.emotion_renderer import (
        EmotionStyle,
        emotion_renderer_config_from_env,
        emotion_prosody_enabled_from_env,
        MAX_TTS_RATE_DELTA,
        MIN_TTS_RATE_DELTA,
        MAX_PITCH_SEMITONES,
        MIN_PITCH_SEMITONES,
    )
    # clamp
    s = EmotionStyle(tts_rate=+0.99, tts_pitch_semitone=+10.0).clamped()
    check("tts_rate clamp 上限 +0.30", abs(s.tts_rate - MAX_TTS_RATE_DELTA) < 1e-9,
          f"got {s.tts_rate}")
    check("pitch clamp 上限 +3", abs(s.tts_pitch_semitone - MAX_PITCH_SEMITONES) < 1e-9)
    s2 = EmotionStyle(tts_rate=-0.99, tts_pitch_semitone=-10.0).clamped()
    check("tts_rate clamp 下限 -0.30", abs(s2.tts_rate - MIN_TTS_RATE_DELTA) < 1e-9)
    check("pitch clamp 下限 -3", abs(s2.tts_pitch_semitone - MIN_PITCH_SEMITONES) < 1e-9)

    # 非法 env
    cfg = emotion_renderer_config_from_env({"COCO_EMOTION_PROSODY": "abc"})
    check("非法 env 'abc' → enabled=False", cfg.enabled is False)
    cfg2 = emotion_renderer_config_from_env({"COCO_EMOTION_PROSODY": ""})
    check("空 env → enabled=False", cfg2.enabled is False)
    cfg3 = emotion_renderer_config_from_env({"COCO_EMOTION_PROSODY": "1",
                                             "COCO_EMOTION_PROSODY_PULSE_S": "100"})
    check("pulse_s clamp 上限 1.0", cfg3.pulse_s == 1.0, f"got {cfg3.pulse_s}")
    cfg4 = emotion_renderer_config_from_env({"COCO_EMOTION_PROSODY": "1",
                                             "COCO_EMOTION_PROSODY_PULSE_S": "0.001"})
    check("pulse_s clamp 下限 0.05", cfg4.pulse_s == 0.05, f"got {cfg4.pulse_s}")
    cfg5 = emotion_renderer_config_from_env({"COCO_EMOTION_PROSODY": "1",
                                             "COCO_EMOTION_PROSODY_PULSE_S": "abc"})
    check("非数字 pulse_s → 默认 0.2", cfg5.pulse_s == 0.2)

    # tts say speed clamp via rate
    import coco.tts as coco_tts
    coco_tts.reset_prosody_fallback_emit_flag()
    import numpy as np
    captured_speed: list = []
    original_synth = coco_tts.synthesize
    original_play = coco_tts.play
    def fake_synth(text, sid=50, speed=1.0):
        captured_speed.append(speed)
        return np.zeros(160, dtype=np.float32), 16000
    def fake_play(samples, sample_rate, blocking=True):
        pass
    coco_tts.synthesize = fake_synth
    coco_tts.play = fake_play
    try:
        # rate=+10 → 极端，最终 effective_speed = 1.0 * 11.0 = clamp 到 2.0
        coco_tts.say("test", rate=+10.0, pitch_semitone=0.0)
        check("say rate=+10 → speed clamp 到 2.0", captured_speed and captured_speed[-1] == 2.0,
              f"got {captured_speed}")
        coco_tts.say("test", rate=-10.0, pitch_semitone=0.0)
        check("say rate=-10 → speed clamp 到 0.5", captured_speed[-1] == 0.5,
              f"got {captured_speed}")
    finally:
        coco_tts.synthesize = original_synth
        coco_tts.play = original_play
except Exception:  # noqa: BLE001
    errors.append("V8: " + traceback.format_exc())


# =======================================================================
# V10: emit payload 完整性
# =======================================================================
print("V10: emit 'companion.emotion_render_changed' payload 完整")
try:
    from coco.companion.emotion_renderer import EmotionRenderer, EmotionRendererConfig
    from coco.emotion import Emotion

    cfg = EmotionRendererConfig(enabled=True, pulse_s=0.05)
    captured: list = []
    fake_baseline = MagicMock()
    fake_baseline.pause = MagicMock()
    fake_baseline.resume = MagicMock()
    fake_baseline.is_paused = MagicMock(return_value=False)

    renderer = EmotionRenderer(
        posture_baseline=fake_baseline,
        expression_player=None,
        robot=make_mock_robot(),
        config=cfg,
        emit_fn=lambda ce, msg="", **p: captured.append((ce, msg, p)),
    )
    renderer._on_baseline_target_changed(Emotion.HAPPY, None)
    time.sleep(0.2)  # 等 pulse worker 跑完
    er_events = [c for c in captured if c[0] == "companion.emotion_render_changed"]
    check("emit at least once", len(er_events) >= 1)
    if er_events:
        p = er_events[0][2]
        for k_field in ("emotion", "tts_rate", "tts_pitch_semitone", "expr_overlay",
                        "antenna_pulse", "fallback"):
            check(f"payload 含 {k_field}", k_field in p, f"keys={list(p.keys())}")
        check("payload.emotion == 'happy'", p.get("emotion") == "happy")
        check("payload.antenna_pulse == True", p.get("antenna_pulse") is True)
        check("payload.expr_overlay == 'excited'", p.get("expr_overlay") == "excited")
except Exception:  # noqa: BLE001
    errors.append("V10: " + traceback.format_exc())


# =======================================================================
# V11: antenna pulse 与 baseline pause/resume 协议一致
# =======================================================================
print("V11: antenna pulse 与 baseline pause/resume 协议一致")
try:
    import threading as _th
    from coco.companion.emotion_renderer import EmotionRenderer, EmotionRendererConfig

    paused_during_pulse = [False]
    pause_called = [0]
    resume_called = [0]

    class FakeBaseline:
        def __init__(self):
            self._paused = False
        def pause(self):
            self._paused = True
            pause_called[0] += 1
        def resume(self):
            self._paused = False
            resume_called[0] += 1
        def is_paused(self):
            return self._paused

    fb = FakeBaseline()
    robot = make_mock_robot()
    # set_target_antenna_joint_positions 实现：被调时 snapshot baseline.is_paused
    def antenna_call(pos):
        paused_during_pulse[0] = fb.is_paused()
    robot.set_target_antenna_joint_positions = antenna_call

    cfg = EmotionRendererConfig(enabled=True, pulse_s=0.05)
    renderer = EmotionRenderer(
        posture_baseline=fb,
        expression_player=None,
        robot=robot,
        config=cfg,
    )
    renderer._dispatch_antenna_pulse()
    time.sleep(0.2)
    check("pulse 期间 baseline.is_paused() == True", paused_during_pulse[0] is True)
    check("pulse 结束后 baseline 已 resume",
          fb.is_paused() is False and pause_called[0] >= 1 and resume_called[0] >= 1,
          f"pause={pause_called[0]} resume={resume_called[0]}")
    check("renderer.stats.pulses_dispatched >= 1", renderer.stats.pulses_dispatched >= 1)
except Exception:  # noqa: BLE001
    errors.append("V11: " + traceback.format_exc())


# =======================================================================
# V9: 回归 interact-006 + robot-004 + companion-005
# =======================================================================
print("V9: 回归 interact-006 / robot-004 / companion-005")
try:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    regress_scripts = [
        ("interact_006", "scripts/verify_interact006.py"),
        ("robot_004", "scripts/verify_robot_004.py"),
        ("companion_005", "scripts/verify_companion_005.py"),
        ("companion_003", "scripts/verify_companion_003.py"),
        ("companion_006", "scripts/verify_companion_006.py"),
    ]
    for name, rel in regress_scripts:
        p = os.path.join(repo_root, rel)
        if not os.path.exists(p):
            print(f"  [SKIP] {name}: {rel} 不存在")
            continue
        try:
            r = subprocess.run(
                [sys.executable, p],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ},
            )
            ok = r.returncode == 0
            check(f"{name} verify exit==0", ok,
                  f"rc={r.returncode} stderr_tail={r.stderr[-300:] if r.stderr else ''}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"V9 {name}: {exc!r}")
            check(f"{name} verify 跑通", False, str(exc))
except Exception:  # noqa: BLE001
    errors.append("V9: " + traceback.format_exc())


# =======================================================================
# 收尾
# =======================================================================
elapsed = time.time() - t0
print()
if errors:
    print(f"FAIL ({len(errors)} errors) elapsed={elapsed:.2f}s")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print(f"PASS elapsed={elapsed:.2f}s")
sys.exit(0)
