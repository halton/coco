"""interact-005 verification: 中文唤醒词 + 6s awake 窗口 + VAD gate.

跑法:
  uv run python scripts/verify_interact005.py

V1: 含 "可可" 的 fixture wav → WakeWordDetector 命中 ≥1 次
V2: 不含 wake word 的 fixture wav → WakeWordDetector 命中 0 次
V3: 触发后 6s 窗口内 VAD utterance 进入真 callback；窗口外被 awake gate 丢弃
V4: 连续两次 wake，window 在第二次后重置 timer（remaining 接近 full）
V5: COCO_WAKE_WORD 默认未设 → wake_word_enabled_from_env() == False（向后兼容）
V6: env 越界 → clamp + warning
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile

from coco.wake_word import (
    DEFAULT_KEYWORDS,
    KWS_DIR,
    WakeConfig,
    WakeGate,
    WakeVADBridge,
    WakeWordDetector,
    config_from_env,
    wake_word_enabled_from_env,
)
from coco.vad_trigger import VADConfig, VADTrigger


REPO = Path(__file__).resolve().parents[1]
FIX_WAKE = REPO / "tests" / "fixtures" / "audio" / "wake_keke.wav"
FIX_NO_WAKE = REPO / "tests" / "fixtures" / "audio" / "zh-001-walk-park.wav"


def _load_wav_f32(path: Path) -> np.ndarray:
    sr, a = wavfile.read(str(path))
    assert sr == 16000, f"{path} sr={sr} != 16000"
    if a.dtype == np.int16:
        a = a.astype(np.float32) / 32768.0
    else:
        a = a.astype(np.float32)
    if a.ndim > 1:
        a = a.mean(axis=1)
    return a


def feed_in_chunks(detector: WakeWordDetector, audio: np.ndarray, chunk: int = 1600) -> None:
    for i in range(0, len(audio), chunk):
        detector.feed(audio[i : i + chunk])
    # tail silence to flush any pending decode
    detector.feed(np.zeros(int(0.5 * 16000), dtype=np.float32))


def main() -> int:
    if not KWS_DIR.exists():
        print(f"FAIL: KWS 模型目录不存在 {KWS_DIR}; 先跑 bash scripts/fetch_kws_models.sh")
        return 2
    if not FIX_WAKE.exists():
        print(f"FAIL: fixture 缺失 {FIX_WAKE}")
        return 2
    if not FIX_NO_WAKE.exists():
        print(f"FAIL: fixture 缺失 {FIX_NO_WAKE}")
        return 2

    summary: dict = {"verifications": {}}

    # -----------------------------------------------------------------
    # V1: '可可，今天天气真好' → 至少 1 次命中
    # -----------------------------------------------------------------
    print("\n--- V1: wake fixture → wake hit ---")
    hits_v1: list[str] = []
    det1 = WakeWordDetector(on_wake=lambda t: hits_v1.append(t), config=WakeConfig())
    audio1 = _load_wav_f32(FIX_WAKE)
    t0 = time.time()
    feed_in_chunks(det1, audio1)
    dt1 = time.time() - t0
    v1_pass = len(hits_v1) >= 1
    summary["verifications"]["V1"] = {
        "fixture": str(FIX_WAKE.relative_to(REPO)),
        "audio_seconds": len(audio1) / 16000,
        "feed_dt_s": round(dt1, 3),
        "hits": hits_v1,
        "stats": dict(
            wakes_total=det1.stats.wakes_total,
            wakes_while_muted=det1.stats.wakes_while_muted,
            callback_ok=det1.stats.callback_ok,
        ),
        "pass": v1_pass,
    }
    print(f"  hits={hits_v1} stats=wakes_total={det1.stats.wakes_total}")
    assert v1_pass, f"V1 FAIL: 期望至少 1 次命中，实际 {len(hits_v1)}"

    # -----------------------------------------------------------------
    # V2: '今天天气真好...' （无 wake word）→ 0 次命中
    # -----------------------------------------------------------------
    print("\n--- V2: no-wake fixture → 0 hits ---")
    hits_v2: list[str] = []
    det2 = WakeWordDetector(on_wake=lambda t: hits_v2.append(t), config=WakeConfig())
    audio2 = _load_wav_f32(FIX_NO_WAKE)
    feed_in_chunks(det2, audio2)
    v2_pass = len(hits_v2) == 0
    summary["verifications"]["V2"] = {
        "fixture": str(FIX_NO_WAKE.relative_to(REPO)),
        "audio_seconds": len(audio2) / 16000,
        "hits": hits_v2,
        "pass": v2_pass,
    }
    print(f"  hits={hits_v2}")
    assert v2_pass, f"V2 FAIL: 期望 0 次命中，实际 {len(hits_v2)}: {hits_v2}"

    # -----------------------------------------------------------------
    # V3: WakeGate + VAD bridge 验 awake window 行为
    # -----------------------------------------------------------------
    print("\n--- V3: WakeGate + VAD bridge ---")
    real_calls: list[tuple[int, int]] = []  # (audio_size, sr)
    def real_cb(audio: np.ndarray, sr: int) -> None:
        real_calls.append((int(audio.size), sr))
    det3 = WakeWordDetector(on_wake=lambda t: None, config=WakeConfig(window_seconds=6.0))
    gate3 = WakeGate(window_seconds=6.0)
    bridge = WakeVADBridge(det3, gate3, real_cb)
    vad = VADTrigger(bridge.vad_gate_callback, config=VADConfig(cooldown_seconds=0.0))
    bridge.bind_vad(vad)

    # V3.a: 未唤醒，喂 VAD-only fixture → bridge gate 应丢弃
    for i in range(0, len(audio2), 1600):
        vad.feed(audio2[i:i+1600])
    vad.flush()
    dropped_before = bridge.utterances_dropped_sleeping
    forwarded_before = bridge.utterances_forwarded
    print(f"  V3.a sleeping → forwarded={forwarded_before} dropped={dropped_before}")
    assert forwarded_before == 0 and dropped_before >= 1, (
        f"V3.a FAIL: 期望 forwarded=0 dropped>=1, 实际 forwarded={forwarded_before} dropped={dropped_before}"
    )

    # V3.b: 主动 trigger gate（绕开 KWS）模拟 wake → 6s 内 VAD 应 forward
    gate3.trigger()
    assert gate3.is_awake(), "V3.b FAIL: gate.trigger 后 is_awake 应 True"
    # 重置 vad cooldown stat 影响：构造新 vad
    real_calls.clear()
    real_calls_b: list[tuple[int, int]] = []
    def real_cb_b(audio: np.ndarray, sr: int) -> None:
        real_calls_b.append((int(audio.size), sr))
    bridge_b = WakeVADBridge(det3, gate3, real_cb_b)
    vad_b = VADTrigger(bridge_b.vad_gate_callback, config=VADConfig(cooldown_seconds=0.0))
    bridge_b.bind_vad(vad_b)
    for i in range(0, len(audio2), 1600):
        vad_b.feed(audio2[i:i+1600])
    vad_b.flush()
    forwarded_awake = bridge_b.utterances_forwarded
    dropped_awake = bridge_b.utterances_dropped_sleeping
    print(f"  V3.b awake → forwarded={forwarded_awake} dropped={dropped_awake}")
    assert forwarded_awake >= 1 and dropped_awake == 0, (
        f"V3.b FAIL: 期望 forwarded>=1 dropped=0, 实际 forwarded={forwarded_awake} dropped={dropped_awake}"
    )

    # V3.c: gate 超时 → is_awake() 应 False
    gate3_short = WakeGate(window_seconds=0.2)
    gate3_short.trigger()
    assert gate3_short.is_awake(), "V3.c FAIL: 刚 trigger 应 awake"
    time.sleep(0.3)
    assert not gate3_short.is_awake(), "V3.c FAIL: 0.3s 后超时应 sleeping"
    assert gate3_short.expired_count == 1, (
        f"V3.c FAIL: expired_count 应 == 1, 实际 {gate3_short.expired_count}"
    )

    summary["verifications"]["V3"] = {
        "sleeping": {"forwarded": forwarded_before, "dropped": dropped_before},
        "awake": {"forwarded": forwarded_awake, "dropped": dropped_awake},
        "expire": {"expired_count": gate3_short.expired_count},
        "pass": True,
    }
    print("  V3 PASS")

    # -----------------------------------------------------------------
    # V4: 连续两次 wake → 第二次重置 timer
    # -----------------------------------------------------------------
    print("\n--- V4: consecutive wake resets timer ---")
    gate4 = WakeGate(window_seconds=6.0)
    gate4.trigger()
    time.sleep(0.5)
    rem_before = gate4.remaining_seconds()
    gate4.trigger()
    rem_after = gate4.remaining_seconds()
    print(f"  rem_before_2nd_trigger={rem_before:.2f}s rem_after={rem_after:.2f}s")
    assert rem_after > rem_before + 0.3, (
        f"V4 FAIL: 第二次 trigger 后 remaining 应增大 (重置 timer), "
        f"before={rem_before:.2f} after={rem_after:.2f}"
    )
    summary["verifications"]["V4"] = {
        "rem_before": round(rem_before, 3),
        "rem_after": round(rem_after, 3),
        "pass": True,
    }
    print("  V4 PASS")

    # -----------------------------------------------------------------
    # V5: env 默认 → wake_word_enabled_from_env False
    # -----------------------------------------------------------------
    print("\n--- V5: COCO_WAKE_WORD default off ---")
    os.environ.pop("COCO_WAKE_WORD", None)
    v5_pass = wake_word_enabled_from_env() == False
    os.environ["COCO_WAKE_WORD"] = "1"
    v5_on = wake_word_enabled_from_env() == True
    os.environ.pop("COCO_WAKE_WORD", None)
    summary["verifications"]["V5"] = {"default_off": v5_pass, "explicit_on": v5_on, "pass": v5_pass and v5_on}
    print(f"  default_off={v5_pass} explicit_on={v5_on}")
    assert v5_pass and v5_on, "V5 FAIL"

    # -----------------------------------------------------------------
    # V6: env 越界 clamp
    # -----------------------------------------------------------------
    print("\n--- V6: env clamp ---")
    os.environ["COCO_WAKE_THRESHOLD"] = "1.5"
    os.environ["COCO_WAKE_WINDOW_SECONDS"] = "120"
    cfg6 = config_from_env()
    os.environ.pop("COCO_WAKE_THRESHOLD")
    os.environ.pop("COCO_WAKE_WINDOW_SECONDS")
    v6_pass = cfg6.threshold == 0.95 and cfg6.window_seconds == 60.0
    summary["verifications"]["V6"] = {
        "threshold": cfg6.threshold, "window_seconds": cfg6.window_seconds, "pass": v6_pass,
    }
    print(f"  threshold={cfg6.threshold} window={cfg6.window_seconds}")
    assert v6_pass, "V6 FAIL clamp"

    # -----------------------------------------------------------------
    # V7: COCO_WAKE_WORD=0 → 等价 interact-003 路径（VAD 直接 forward, gate 不介入）
    # -----------------------------------------------------------------
    print("\n--- V7: backward-compat (wake disabled → VAD direct forward) ---")
    direct_calls: list[int] = []
    def direct_cb(audio: np.ndarray, sr: int) -> None:
        direct_calls.append(int(audio.size))
    vad_direct = VADTrigger(direct_cb, config=VADConfig(cooldown_seconds=0.0))
    for i in range(0, len(audio2), 1600):
        vad_direct.feed(audio2[i:i+1600])
    vad_direct.flush()
    v7_pass = len(direct_calls) >= 1
    summary["verifications"]["V7"] = {
        "direct_callbacks": len(direct_calls), "pass": v7_pass,
    }
    print(f"  direct callbacks={len(direct_calls)}")
    assert v7_pass, "V7 FAIL: VAD-only 路径应至少触发 1 次"

    # -----------------------------------------------------------------
    summary["all_pass"] = all(v.get("pass") for v in summary["verifications"].values())
    out = REPO / "evidence" / "interact-005" / "verify_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n--- Summary written → {out.relative_to(REPO)} ---")
    print(f"all_pass={summary['all_pass']}")
    return 0 if summary["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
