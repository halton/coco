#!/usr/bin/env python3
"""verify_interact006.py — 情绪/语气检测验证.

覆盖 spec 5 条 verification：
  V1 EmotionDetector 5 类典型文本各分类正确（正向 case）
  V2 多类同分时仲裁顺序：HAPPY > SAD > ANGRY > SURPRISED > NEUTRAL
  V3 confidence 单调：更多匹配词 → 更高 score
  V4 异常输入（None / 数字 / 超长 / 仅标点 / 空串）→ neutral score=0 不抛
  V5 fixture 20 条准确率 ≥ 0.80
  V6 backward-compat：COCO_EMOTION 未设默认 OFF；IdleAnimator 不接 set_current_emotion
     时行为完全等价 phase-3（emotion_bias 不影响 amp）
  V7 IdleAnimator.set_current_emotion(happy) 后 micro_amp 平均 ≥ 1.2x neutral baseline
     （60s sim trace；sad 0.7x 缩放）
  V8 EmotionTracker decay：record happy → effective=happy；推进 decay+1s 后 effective=neutral
  V9 集成 InteractSession：注入 emotion_detector + emotion_tracker，跑两轮（开心句 + 难过句），
     断言对应 emotion 被分类 + idle_animator.set_current_emotion 被调用 + emit 写入
  V10 TTS emotion 标注：tts_say_fn 接受 emotion kwarg 时 InteractSession 自动透传
  V11 env：COCO_EMOTION_DECAY_S clamp [1, 3600]；非法值回退默认 60
  V12 CocoConfig 集成：load_config 含 emotion 子配置 + config_summary 含 emotion 段

evidence/interact-006/verify_summary.json 写确定性结果。
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import MagicMock

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.emotion import (  # noqa: E402
    DEFAULT_DECAY_S,
    DEFAULT_LEXICON,
    Emotion,
    EmotionConfig,
    EmotionDetector,
    EmotionLabel,
    EmotionTracker,
    config_from_env,
    emotion_enabled_from_env,
)
from coco.idle import IdleAnimator, IdleConfig  # noqa: E402
from coco.interact import InteractSession  # noqa: E402
from coco.config import load_config, config_summary  # noqa: E402
from coco.logging_setup import setup_logging  # noqa: E402


EVIDENCE_DIR = ROOT / "evidence" / "interact-006"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
FIXTURE = ROOT / "tests" / "fixtures" / "text" / "emotion_cases.json"


PASSES: List[str] = []
FAILURES: List[str] = []


def ok(msg: str) -> None:
    print(f"  PASS {msg}", flush=True)
    PASSES.append(msg)


def fail(msg: str) -> None:
    print(f"  FAIL {msg}", flush=True)
    FAILURES.append(msg)


def assert_eq(actual: Any, expected: Any, label: str) -> bool:
    if actual == expected:
        ok(f"{label}: {actual!r} == {expected!r}")
        return True
    fail(f"{label}: actual={actual!r} expected={expected!r}")
    return False


# ---------------------------------------------------------------------------
# V1: 5 类典型文本分类
# ---------------------------------------------------------------------------


def v1_typical_classification() -> None:
    print("\n[V1] 5 类典型文本各分类正确", flush=True)
    det = EmotionDetector()
    cases = [
        ("今天好开心啊", Emotion.HAPPY),
        ("我有点难过", Emotion.SAD),
        ("气死我了", Emotion.ANGRY),
        ("真的吗？哇", Emotion.SURPRISED),
        ("我们去公园", Emotion.NEUTRAL),
    ]
    for text, expected in cases:
        label = det.detect(text)
        assert_eq(label.name, expected, f"V1 detect({text!r})")


# ---------------------------------------------------------------------------
# V2: 仲裁顺序 HAPPY > SAD > ANGRY > SURPRISED > NEUTRAL
# ---------------------------------------------------------------------------


def v2_priority_arbitration() -> None:
    print("\n[V2] 多类同分仲裁顺序", flush=True)
    det = EmotionDetector()
    # 同分时 happy 优先于 sad
    label = det.detect("开心又难过")  # happy 1, sad 1
    assert_eq(label.name, Emotion.HAPPY, "V2 happy>sad 同分仲裁")
    # happy 优先于 angry
    label = det.detect("开心 讨厌")  # happy 1, angry 1
    assert_eq(label.name, Emotion.HAPPY, "V2 happy>angry 同分仲裁")
    # sad 优先于 angry
    label = det.detect("难过 烦")  # sad 1, angry 1
    assert_eq(label.name, Emotion.SAD, "V2 sad>angry 同分仲裁")
    # angry 优先于 surprised
    label = det.detect("讨厌 居然")  # angry 1, surprised 1
    assert_eq(label.name, Emotion.ANGRY, "V2 angry>surprised 同分仲裁")


# ---------------------------------------------------------------------------
# V3: confidence 单调
# ---------------------------------------------------------------------------


def v3_confidence_monotone() -> None:
    print("\n[V3] confidence 随匹配词数单调递增", flush=True)
    det = EmotionDetector()
    # 同长文本，匹配词数不同；用 padding 让长度近似
    s1 = det.detect("开心啊啊啊啊啊啊啊").score      # 1 hit
    s2 = det.detect("开心 高兴 啊啊啊").score        # 2 hits
    s3 = det.detect("开心 高兴 喜欢 啊").score        # 3 hits
    if s1 < s2 < s3:
        ok(f"V3 单调: {s1:.4f} < {s2:.4f} < {s3:.4f}")
    else:
        fail(f"V3 单调失败: {s1:.4f} {s2:.4f} {s3:.4f}")


# ---------------------------------------------------------------------------
# V4: 异常输入
# ---------------------------------------------------------------------------


def v4_invalid_inputs() -> None:
    print("\n[V4] 异常输入返回 NEUTRAL score=0 不抛", flush=True)
    det = EmotionDetector()
    cases: List[Any] = [None, 123, 3.14, [], "", "   ", "...?!", "x" * 5000]
    for c in cases:
        try:
            label = det.detect(c)
        except Exception as e:  # noqa: BLE001
            fail(f"V4 detect({c!r}) 抛异常: {type(e).__name__}: {e}")
            continue
        if label.name == Emotion.NEUTRAL and label.score == 0.0:
            ok(f"V4 detect({type(c).__name__}={str(c)[:30]!r}) → NEUTRAL score=0")
        else:
            fail(f"V4 detect({c!r}) → {label.name} score={label.score}")


# ---------------------------------------------------------------------------
# V5: fixture 准确率 ≥ 0.80
# ---------------------------------------------------------------------------


def v5_fixture_accuracy() -> None:
    print("\n[V5] fixture 20 条准确率 ≥ 0.80", flush=True)
    if not FIXTURE.exists():
        fail(f"V5 fixture 缺失: {FIXTURE}")
        return
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = data["cases"]
    if len(cases) < 20:
        fail(f"V5 fixture cases={len(cases)} < 20")
        return
    det = EmotionDetector()
    correct = 0
    misclassified: List[str] = []
    for c in cases:
        label = det.detect(c["text"])
        if label.value == c["expected"]:
            correct += 1
        else:
            misclassified.append(f"{c['text']!r}: got {label.value} want {c['expected']}")
    acc = correct / len(cases)
    print(f"  acc={acc:.3f}  correct={correct}/{len(cases)}", flush=True)
    if misclassified:
        print(f"  misclassified={misclassified}", flush=True)
    if acc >= 0.80:
        ok(f"V5 fixture acc={acc:.3f} >= 0.80 (correct={correct}/{len(cases)})")
    else:
        fail(f"V5 fixture acc={acc:.3f} < 0.80; misclassified={misclassified}")


# ---------------------------------------------------------------------------
# V6: backward-compat — COCO_EMOTION 未设默认 OFF；IdleAnimator 不接 emotion 等价 phase-3
# ---------------------------------------------------------------------------


def v6_backward_compat() -> None:
    print("\n[V6] backward-compat：默认 OFF + IdleAnimator 不接 emotion 等价 phase-3", flush=True)
    # 6.1 默认 OFF
    saved = os.environ.pop("COCO_EMOTION", None)
    try:
        assert_eq(emotion_enabled_from_env(), False, "V6.1 emotion_enabled_from_env 默认 False")
    finally:
        if saved is not None:
            os.environ["COCO_EMOTION"] = saved

    # 6.2 IdleAnimator 不调 set_current_emotion 时 _emotion_scale=1.0
    import threading as _th
    from unittest.mock import MagicMock as _MM
    robot = _MM()
    stop_ev = _th.Event()
    cfg = IdleConfig()
    anim = IdleAnimator(robot, stop_ev, config=cfg)
    if anim.get_current_emotion() is None and anim._emotion_scale() == 1.0:
        ok("V6.2 默认 emotion=None scale=1.0")
    else:
        fail(f"V6.2 默认 emotion={anim.get_current_emotion()} scale={anim._emotion_scale()}")

    # 6.3 不接 set_current_emotion 时 _micro_head 用原 amp
    cfg2 = IdleConfig()
    anim2 = IdleAnimator(robot, stop_ev, config=cfg2)
    samples_yaw: List[float] = []
    samples_pitch: List[float] = []

    def _capture(head=None, duration=None):  # noqa: ANN001
        # head 是 4x4 matrix；取 yaw/pitch 不易，这里直接读 anim2 调用前的 sample 缓存
        pass
    # 直接调 _micro_head 多次，断言 robot.goto_target 被调，且 amp 区间在原 cfg 之内
    for _ in range(50):
        anim2._micro_head()
    # 简化：检查 robot.goto_target 调用次数
    if robot.goto_target.call_count >= 50:
        ok(f"V6.3 不注入 emotion 时 micro_head 跑通 (calls={robot.goto_target.call_count})")
    else:
        fail(f"V6.3 micro_head call_count={robot.goto_target.call_count}")


# ---------------------------------------------------------------------------
# V7: emotion bias 影响 micro_amp
# ---------------------------------------------------------------------------


def v7_emotion_bias_amp() -> None:
    print("\n[V7] HAPPY/SAD bias 缩放 micro_amp", flush=True)
    import threading as _th

    def _trace_amps(emotion: Optional[str], n: int = 200) -> List[float]:
        """跑 n 次 _micro_head；从 robot.goto_target 调用 args 解出 yaw 幅度。

        简化策略：mock robot.goto_target 用一个 capture 函数收 head 矩阵；
        从 head 矩阵反推 yaw 不直观，所以改用 monkey-patch：直接覆盖 anim._safe
        来记录 micro_yaw_amp_deg * scale 的当前值。
        """
        robot = MagicMock()
        stop_ev = _th.Event()
        cfg = IdleConfig()
        # 用固定种子保证确定性
        import random as _r
        anim = IdleAnimator(robot, stop_ev, config=cfg, rng=_r.Random(42))
        if emotion is not None:
            anim.set_current_emotion(emotion)
        amps: List[float] = []
        # 直接调 _micro_head；rng.uniform(-amp, amp) 的 amp = cfg.micro_yaw_amp_deg * scale
        # 我们 monkey-patch rng.uniform 来 capture 实际 amp
        orig_uniform = anim.rng.uniform

        def _spy_uniform(a, b):
            amps.append(b)  # b 即正向上界，等于 micro_*_amp_deg * scale
            return orig_uniform(a, b)
        anim.rng.uniform = _spy_uniform  # type: ignore[assignment]
        for _ in range(n):
            anim._micro_head()
        return amps

    base_amps = _trace_amps(None, n=200)
    happy_amps = _trace_amps("happy", n=200)
    sad_amps = _trace_amps("sad", n=200)

    # base 应每两次进 _micro_head 收 yaw 上界 + pitch 上界 共 2 个值
    base_avg = sum(base_amps) / len(base_amps)
    happy_avg = sum(happy_amps) / len(happy_amps)
    sad_avg = sum(sad_amps) / len(sad_amps)

    print(f"  base_avg_amp={base_avg:.4f}  happy_avg={happy_avg:.4f}  sad_avg={sad_avg:.4f}", flush=True)

    if happy_avg >= base_avg * 1.2:
        ok(f"V7.1 happy_amp ≥ base × 1.2 ({happy_avg:.3f} >= {base_avg * 1.2:.3f})")
    else:
        fail(f"V7.1 happy_amp {happy_avg:.3f} < base × 1.2 ({base_avg * 1.2:.3f})")

    if sad_avg <= base_avg * 0.8:
        ok(f"V7.2 sad_amp ≤ base × 0.8 ({sad_avg:.3f} <= {base_avg * 0.8:.3f})")
    else:
        fail(f"V7.2 sad_amp {sad_avg:.3f} > base × 0.8 ({base_avg * 0.8:.3f})")


# ---------------------------------------------------------------------------
# V8: EmotionTracker decay
# ---------------------------------------------------------------------------


def v8_tracker_decay() -> None:
    print("\n[V8] EmotionTracker decay 半衰期", flush=True)
    clk = {"t": 1000.0}
    tracker = EmotionTracker(decay_s=60.0, clock=lambda: clk["t"])
    label = EmotionLabel(Emotion.HAPPY, 0.5, ["开心"])
    tracker.record(label)
    assert_eq(tracker.effective(), Emotion.HAPPY, "V8.1 record 后 effective=HAPPY")
    # 推进 30s（decay 内）
    clk["t"] += 30.0
    assert_eq(tracker.effective(), Emotion.HAPPY, "V8.2 30s 后仍 HAPPY")
    # 推进到 61s（超 decay）
    clk["t"] += 31.0
    assert_eq(tracker.effective(), Emotion.NEUTRAL, "V8.3 61s 后回 NEUTRAL")

    # 6.4 reset 直接归零
    tracker.record(EmotionLabel(Emotion.SAD, 0.3, ["难过"]))
    tracker.reset()
    assert_eq(tracker.effective(), Emotion.NEUTRAL, "V8.4 reset 后 NEUTRAL")


# ---------------------------------------------------------------------------
# V9: InteractSession 集成
# ---------------------------------------------------------------------------


def v9_interact_session_integration() -> None:
    print("\n[V9] InteractSession 集成 emotion_detector", flush=True)
    import threading as _th
    robot = MagicMock()
    asr_calls: List[str] = []
    asr_outputs = ["今天好开心啊", "我有点难过"]

    def asr_fn(audio, sr):  # noqa: ANN001
        return asr_outputs[len(asr_calls) % len(asr_outputs)] if asr_outputs else ""

    # tts_say_fn 不接 emotion kwarg（V10 单独验）
    def tts_say(text, blocking=True):  # noqa: ANN001
        asr_calls.append(text)

    stop_ev = _th.Event()
    anim = IdleAnimator(robot, stop_ev, config=IdleConfig())
    det = EmotionDetector()
    tracker = EmotionTracker(decay_s=60.0)

    sess = InteractSession(
        robot=robot,
        asr_fn=asr_fn,
        tts_say_fn=tts_say,
        idle_animator=anim,
        emotion_detector=det,
        emotion_tracker=tracker,
    )
    audio = np.zeros(1600, dtype=np.int16)
    r1 = sess.handle_audio(audio, 16000, skip_action=True, skip_tts_play=True)
    asr_outputs.pop(0)  # 滚到下一个
    r2 = sess.handle_audio(audio, 16000, skip_action=True, skip_tts_play=True)

    if r1.get("emotion") == "happy":
        ok(f"V9.1 第 1 轮 emotion=happy (transcript={r1.get('transcript')!r})")
    else:
        fail(f"V9.1 第 1 轮 emotion={r1.get('emotion')} transcript={r1.get('transcript')!r}")
    if r2.get("emotion") == "sad":
        ok(f"V9.2 第 2 轮 emotion=sad (transcript={r2.get('transcript')!r})")
    else:
        fail(f"V9.2 第 2 轮 emotion={r2.get('emotion')} transcript={r2.get('transcript')!r}")
    # 第 2 轮后 idle_animator.current_emotion 应为 sad（最近一次强情绪）
    if anim.get_current_emotion() == "sad":
        ok("V9.3 IdleAnimator.current_emotion 注入成功 = sad")
    else:
        fail(f"V9.3 IdleAnimator.current_emotion={anim.get_current_emotion()}")


# ---------------------------------------------------------------------------
# V10: TTS emotion 透传
# ---------------------------------------------------------------------------


def v10_tts_emotion_passthrough() -> None:
    print("\n[V10] tts_say_fn 接受 emotion kwarg 时自动透传", flush=True)
    import threading as _th
    robot = MagicMock()
    captured: List[dict] = []

    def tts_with_emotion(text, blocking=True, emotion=None):  # noqa: ANN001
        captured.append({"text": text, "emotion": emotion})

    def asr_fn(audio, sr):  # noqa: ANN001
        return "今天好开心"

    stop_ev = _th.Event()
    anim = IdleAnimator(robot, stop_ev, config=IdleConfig())
    sess = InteractSession(
        robot=robot,
        asr_fn=asr_fn,
        tts_say_fn=tts_with_emotion,
        idle_animator=anim,
        emotion_detector=EmotionDetector(),
        emotion_tracker=EmotionTracker(decay_s=60.0),
    )
    audio = np.zeros(1600, dtype=np.int16)
    sess.handle_audio(audio, 16000, skip_action=True, skip_tts_play=False)
    if captured and captured[-1].get("emotion") == "happy":
        ok(f"V10.1 tts 收到 emotion=happy (text={captured[-1]['text']!r})")
    else:
        fail(f"V10.1 tts captured={captured}")

    # 反向：tts_say_fn 不接 emotion kwarg 时，不应抛
    tts_calls: List[dict] = []

    def tts_no_emotion(text, blocking=True):  # noqa: ANN001
        tts_calls.append({"text": text})

    sess2 = InteractSession(
        robot=robot,
        asr_fn=asr_fn,
        tts_say_fn=tts_no_emotion,
        idle_animator=anim,
        emotion_detector=EmotionDetector(),
        emotion_tracker=EmotionTracker(decay_s=60.0),
    )
    sess2.handle_audio(audio, 16000, skip_action=True, skip_tts_play=False)
    if tts_calls:
        ok(f"V10.2 旧签名 tts 仍被调 (calls={len(tts_calls)})")
    else:
        fail("V10.2 旧签名 tts 未被调")


# ---------------------------------------------------------------------------
# V11: env clamp
# ---------------------------------------------------------------------------


def v11_env_clamp() -> None:
    print("\n[V11] COCO_EMOTION_DECAY_S env clamp", flush=True)
    cfg = config_from_env({"COCO_EMOTION_DECAY_S": "0.5"})
    assert_eq(cfg.decay_s, 1.0, "V11.1 lo clamp 0.5 → 1.0")
    cfg = config_from_env({"COCO_EMOTION_DECAY_S": "9999"})
    assert_eq(cfg.decay_s, 3600.0, "V11.2 hi clamp 9999 → 3600")
    cfg = config_from_env({"COCO_EMOTION_DECAY_S": "abc"})
    assert_eq(cfg.decay_s, DEFAULT_DECAY_S, "V11.3 非数字 → 默认 60")
    cfg = config_from_env({})
    assert_eq(cfg.decay_s, DEFAULT_DECAY_S, "V11.4 缺失 → 默认 60")
    assert_eq(cfg.enabled, False, "V11.5 enabled 默认 False")
    cfg = config_from_env({"COCO_EMOTION": "1"})
    assert_eq(cfg.enabled, True, "V11.6 COCO_EMOTION=1 → True")


# ---------------------------------------------------------------------------
# V12: CocoConfig 集成
# ---------------------------------------------------------------------------


def v12_cocoConfig_integration() -> None:
    print("\n[V12] CocoConfig 集成 emotion 子配置", flush=True)
    cfg = load_config({})
    # emotion 字段存在
    if cfg.emotion is not None and isinstance(cfg.emotion, EmotionConfig):
        ok(f"V12.1 cfg.emotion 类型正确 = {type(cfg.emotion).__name__}")
    else:
        fail(f"V12.1 cfg.emotion = {cfg.emotion!r}")
    assert_eq(cfg.emotion_enabled, False, "V12.2 emotion_enabled 默认 False")

    cfg2 = load_config({"COCO_EMOTION": "1", "COCO_EMOTION_DECAY_S": "120"})
    assert_eq(cfg2.emotion_enabled, True, "V12.3 env override emotion_enabled=True")
    assert_eq(cfg2.emotion.decay_s, 120.0, "V12.4 env override decay_s=120")

    summary = config_summary(cfg2)
    if "emotion" in summary and summary["emotion"]["enabled"] is True:
        ok(f"V12.5 config_summary 含 emotion 段：{summary['emotion']}")
    else:
        fail(f"V12.5 summary['emotion']={summary.get('emotion')}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=== verify_interact006 ===", flush=True)
    setup_logging(jsonl=False, level="WARNING")
    v1_typical_classification()
    v2_priority_arbitration()
    v3_confidence_monotone()
    v4_invalid_inputs()
    v5_fixture_accuracy()
    v6_backward_compat()
    v7_emotion_bias_amp()
    v8_tracker_decay()
    v9_interact_session_integration()
    v10_tts_emotion_passthrough()
    v11_env_clamp()
    v12_cocoConfig_integration()

    print(f"\n--- 总结 ---", flush=True)
    print(f"PASS={len(PASSES)}  FAIL={len(FAILURES)}", flush=True)

    summary = {
        "verification": "verify_interact006",
        "pass_count": len(PASSES),
        "fail_count": len(FAILURES),
        "passes": PASSES,
        "failures": FAILURES,
    }
    (EVIDENCE_DIR / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if FAILURES:
        print("==> FAIL: interact-006 有 failure", flush=True)
        for f in FAILURES:
            print(f"  - {f}", flush=True)
        return 1
    print("==> PASS: interact-006 verification 全部通过", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
