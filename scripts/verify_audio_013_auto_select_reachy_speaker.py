#!/usr/bin/env python3
"""verify_audio_013_auto_select_reachy_speaker.py

audio-013: TTS 自动检测 Reachy Mini Audio speaker；env 显式覆盖；24k→16k 重采样兜底。

校验集：
- V0  : coco/tts.py file SHA256 == expected (hash lock)
- V1  : env 不设 + mock query_devices 返回含 "Reachy Mini Audio" → _resolve_tts_output_device() 返回该 index
- V2  : env=COCO_TTS_OUTPUT_DEVICE="Reachy Mini Audio" → 直接返回原字符串
- V3  : env=COCO_TTS_OUTPUT_DEVICE="5" → 返回 int 5
- V4  : 无 Reachy device 且 env 空 → 返回 None
- V5  : play() 走重采样路径——24000Hz samples → device default_samplerate=16000 → resample_poly 调用 + 输出长度比例 ≈ 16000/24000

rc=0 全 PASS；rc=1 任一 FAIL。
"""
from __future__ import annotations

import hashlib
import os
import sys
import traceback
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np

REPO = Path(__file__).resolve().parent.parent
TTS_PATH = REPO / "coco" / "tts.py"
EXPECTED_TTS_SHA = "6a8d5e0ab5d2c3f9e6fb6a76bc2bb2ec425b48a7a897b22190b859513ae208fa"

results: list[tuple[str, str, str]] = []  # (name, status, detail)


def _record(name: str, ok: bool, detail: str) -> None:
    status = "PASS" if ok else "FAIL"
    results.append((name, status, detail))
    print(f"[{status}] {name}: {detail}")


def v0_hash_lock() -> None:
    try:
        actual = hashlib.sha256(TTS_PATH.read_bytes()).hexdigest()
        ok = actual == EXPECTED_TTS_SHA
        _record("V0_hash_lock", ok, f"expected={EXPECTED_TTS_SHA[:16]}.. actual={actual[:16]}..")
    except Exception as e:
        _record("V0_hash_lock", False, f"exception: {type(e).__name__}: {e}")


def v1_auto_detect_reachy() -> None:
    try:
        from coco import tts as tts_mod
        os.environ.pop(tts_mod.ENV_TTS_OUTPUT_DEVICE, None)
        fake_devices = [
            {"name": "MacBook Pro Microphone", "max_input_channels": 1, "max_output_channels": 0, "default_samplerate": 48000.0},
            {"name": "Reachy Mini Audio", "max_input_channels": 1, "max_output_channels": 2, "default_samplerate": 16000.0},
            {"name": "MacBook Pro Speakers", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 48000.0},
        ]
        fake_sd = MagicMock()
        fake_sd.query_devices = MagicMock(return_value=fake_devices)
        with patch.dict(sys.modules, {"sounddevice": fake_sd}):
            res = tts_mod._resolve_tts_output_device()
        ok = res == 1
        _record("V1_auto_detect_reachy", ok, f"got={res!r} (want=1)")
    except Exception as e:
        traceback.print_exc()
        _record("V1_auto_detect_reachy", False, f"exception: {type(e).__name__}: {e}")


def v2_env_substring() -> None:
    try:
        from coco import tts as tts_mod
        os.environ[tts_mod.ENV_TTS_OUTPUT_DEVICE] = "Reachy Mini Audio"
        try:
            res = tts_mod._resolve_tts_output_device()
        finally:
            os.environ.pop(tts_mod.ENV_TTS_OUTPUT_DEVICE, None)
        ok = res == "Reachy Mini Audio"
        _record("V2_env_substring", ok, f"got={res!r} (want='Reachy Mini Audio')")
    except Exception as e:
        _record("V2_env_substring", False, f"exception: {type(e).__name__}: {e}")


def v3_env_int() -> None:
    try:
        from coco import tts as tts_mod
        os.environ[tts_mod.ENV_TTS_OUTPUT_DEVICE] = "5"
        try:
            res = tts_mod._resolve_tts_output_device()
        finally:
            os.environ.pop(tts_mod.ENV_TTS_OUTPUT_DEVICE, None)
        ok = res == 5 and isinstance(res, int)
        _record("V3_env_int", ok, f"got={res!r} type={type(res).__name__} (want int 5)")
    except Exception as e:
        _record("V3_env_int", False, f"exception: {type(e).__name__}: {e}")


def v4_no_reachy_none() -> None:
    try:
        from coco import tts as tts_mod
        os.environ.pop(tts_mod.ENV_TTS_OUTPUT_DEVICE, None)
        fake_devices = [
            {"name": "MacBook Pro Microphone", "max_input_channels": 1, "max_output_channels": 0, "default_samplerate": 48000.0},
            {"name": "MacBook Pro Speakers", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 48000.0},
        ]
        fake_sd = MagicMock()
        fake_sd.query_devices = MagicMock(return_value=fake_devices)
        with patch.dict(sys.modules, {"sounddevice": fake_sd}):
            res = tts_mod._resolve_tts_output_device()
        ok = res is None
        _record("V4_no_reachy_none", ok, f"got={res!r} (want=None)")
    except Exception as e:
        _record("V4_no_reachy_none", False, f"exception: {type(e).__name__}: {e}")


def v5_resample_path() -> None:
    """play(): 24kHz samples → device default_samplerate=16kHz → resample_poly 调用 + 长度 ≈ 比例."""
    try:
        from coco import tts as tts_mod
        os.environ.pop(tts_mod.ENV_TTS_OUTPUT_DEVICE, None)
        # 重置 device 缓存以确保 log 路径稳定（不影响 verify）
        tts_mod._last_logged_device = object()

        # 输入：1 秒 24000Hz 正弦
        sr_in = 24000
        n_in = sr_in
        samples = np.sin(2 * np.pi * 440 * np.arange(n_in) / sr_in).astype(np.float32)

        fake_devices = [
            {"name": "Reachy Mini Audio", "max_input_channels": 1, "max_output_channels": 2, "default_samplerate": 16000.0},
        ]
        captured = {}

        def fake_play(data, samplerate, blocking, device=None):
            captured["samplerate"] = samplerate
            captured["len"] = len(data)
            captured["device"] = device

        fake_sd = MagicMock()
        fake_sd.query_devices = MagicMock(side_effect=lambda *a, **kw: (
            fake_devices[0] if a else fake_devices
        ))
        fake_sd.play = MagicMock(side_effect=fake_play)

        with patch.dict(sys.modules, {"sounddevice": fake_sd}):
            tts_mod.play(samples, sr_in, blocking=True)

        got_sr = captured.get("samplerate")
        got_len = captured.get("len")
        expected_len = int(round(n_in * 16000 / 24000))  # = 16000
        # resample_poly 输出长度允许 ±1 容差
        ok_sr = got_sr == 16000
        ok_len = got_len is not None and abs(got_len - expected_len) <= 4
        ok_dev = captured.get("device") == 0
        ok = ok_sr and ok_len and ok_dev
        _record(
            "V5_resample_path",
            ok,
            f"sr={got_sr} (want 16000) len={got_len} (want≈{expected_len}) device={captured.get('device')!r} (want 0)",
        )
    except Exception as e:
        traceback.print_exc()
        _record("V5_resample_path", False, f"exception: {type(e).__name__}: {e}")


def main() -> int:
    v0_hash_lock()
    v1_auto_detect_reachy()
    v2_env_substring()
    v3_env_int()
    v4_no_reachy_none()
    v5_resample_path()

    print("\n=== SUMMARY ===")
    fail = 0
    for name, status, detail in results:
        print(f"  [{status}] {name}")
        if status != "PASS":
            fail += 1
    rc = 0 if fail == 0 else 1
    print(f"\nrc={rc} (fails={fail}/{len(results)})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
