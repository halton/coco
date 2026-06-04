#!/usr/bin/env python3
"""verify_audio_016_mic_auto_select_reachy — V0..V8

Each V independent try/except; rc=0 全 PASS。
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

EXPECT_HASH = "d636efa2ae135517b6ea0e4234e94b2e5afd79f4d9e6d06360fc68fa81224ed5"

results: list[tuple[str, str, str]] = []  # (name, status, msg)


def _record(name: str, ok: bool, msg: str = "") -> None:
    results.append((name, "PASS" if ok else "FAIL", msg))


# ---- V0: hash lock on coco/audio_device.py ----
try:
    p = REPO / "coco" / "audio_device.py"
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    ok = (h == EXPECT_HASH)
    _record("V0_hash_lock", ok, f"hash={h} expect={EXPECT_HASH}")
except Exception as e:
    _record("V0_hash_lock", False, repr(e))

# ---- V1: import surface ----
try:
    from coco.audio_device import resolve_input_device, log_input_device_once, ENV_INPUT_DEVICE  # noqa
    ok = (ENV_INPUT_DEVICE == "COCO_AUDIO_INPUT_DEVICE"
          and callable(resolve_input_device)
          and callable(log_input_device_once))
    _record("V1_import_surface", ok, f"ENV={ENV_INPUT_DEVICE}")
except Exception as e:
    _record("V1_import_surface", False, repr(e))

# ---- V2: env=int string -> int ----
try:
    import coco.audio_device as ad
    os.environ["COCO_AUDIO_INPUT_DEVICE"] = "5"
    v = ad.resolve_input_device()
    ok = (v == 5 and isinstance(v, int))
    _record("V2_env_int", ok, f"v={v!r}")
finally:
    os.environ.pop("COCO_AUDIO_INPUT_DEVICE", None)

# ---- V3: env=substring -> substring str ----
try:
    import coco.audio_device as ad
    os.environ["COCO_AUDIO_INPUT_DEVICE"] = "Reachy Mini Audio"
    v = ad.resolve_input_device()
    ok = (v == "Reachy Mini Audio")
    _record("V3_env_substring", ok, f"v={v!r}")
finally:
    os.environ.pop("COCO_AUDIO_INPUT_DEVICE", None)

# ---- V4: env empty + mock query contains Reachy in_ch>=1 -> index ----
try:
    import coco.audio_device as ad
    os.environ.pop("COCO_AUDIO_INPUT_DEVICE", None)
    fake = [
        {"name": "MacBook Air Mic", "max_input_channels": 1, "max_output_channels": 0},
        {"name": "Reachy Mini Audio", "max_input_channels": 2, "max_output_channels": 2},
        {"name": "MacBook Air Speakers", "max_input_channels": 0, "max_output_channels": 2},
    ]
    orig = ad._query_devices_safe
    ad._query_devices_safe = lambda: fake
    try:
        v = ad.resolve_input_device()
    finally:
        ad._query_devices_safe = orig
    ok = (v == 1)
    _record("V4_auto_reachy_hit", ok, f"v={v!r}")
except Exception as e:
    _record("V4_auto_reachy_hit", False, repr(e))

# ---- V5: env empty + Reachy but max_input_channels=0 -> None ----
try:
    import coco.audio_device as ad
    os.environ.pop("COCO_AUDIO_INPUT_DEVICE", None)
    fake = [
        {"name": "MacBook Air Mic", "max_input_channels": 1, "max_output_channels": 0},
        {"name": "Reachy Mini Audio", "max_input_channels": 0, "max_output_channels": 2},
    ]
    orig = ad._query_devices_safe
    ad._query_devices_safe = lambda: fake
    try:
        v = ad.resolve_input_device()
    finally:
        ad._query_devices_safe = orig
    ok = (v is None)
    _record("V5_reachy_out_only_skip", ok, f"v={v!r}")
except Exception as e:
    _record("V5_reachy_out_only_skip", False, repr(e))

# ---- V6: env empty + no Reachy -> None ----
try:
    import coco.audio_device as ad
    os.environ.pop("COCO_AUDIO_INPUT_DEVICE", None)
    fake = [
        {"name": "MacBook Air Mic", "max_input_channels": 1, "max_output_channels": 0},
        {"name": "External USB Mic", "max_input_channels": 1, "max_output_channels": 0},
    ]
    orig = ad._query_devices_safe
    ad._query_devices_safe = lambda: fake
    try:
        v = ad.resolve_input_device()
    finally:
        ad._query_devices_safe = orig
    ok = (v is None)
    _record("V6_no_reachy_none", ok, f"v={v!r}")
except Exception as e:
    _record("V6_no_reachy_none", False, repr(e))

# ---- V7: query_devices raises -> None (fail-soft) ----
try:
    import coco.audio_device as ad
    os.environ.pop("COCO_AUDIO_INPUT_DEVICE", None)
    def _raise():
        raise RuntimeError("portaudio broken")
    orig = ad._query_devices_safe
    ad._query_devices_safe = _raise
    try:
        try:
            v = ad.resolve_input_device()
            ok = (v is None)
            msg = f"v={v!r}"
        except Exception as e:
            ok = False
            msg = f"raised={e!r}"
    finally:
        ad._query_devices_safe = orig
    _record("V7_query_raise_fail_soft", ok, msg)
except Exception as e:
    _record("V7_query_raise_fail_soft", False, repr(e))

# ---- V8: 5 call sites all reference resolve_input_device ----
try:
    targets = {
        "coco/vad_trigger.py": 1,
        "coco/wake_word.py": 1,
        "coco/asr.py": 1,
        "coco/main.py": 1,
    }
    total_refs = 0
    site_count = 0
    details = []
    for rel, min_refs in targets.items():
        text = (REPO / rel).read_text(encoding="utf-8")
        c = text.count("resolve_input_device")
        details.append(f"{rel}:{c}")
        total_refs += c
        if c >= min_refs:
            site_count += 1
    # 4 files × imported+called = 至少 8 个 token；这里 site_count==4 即 4 个调用站；
    # 第 5 个 "调用站" 是 main.py 内的第二个 InputStream（已 device=_main_input_device 注入），
    # 只算一次 import + 一次 resolve()。device= injection 已在 grep 校验中确认。
    ok = (site_count == 4 and total_refs >= 8)
    _record("V8_call_sites", ok, f"sites={site_count} total={total_refs} {details}")
except Exception as e:
    _record("V8_call_sites", False, repr(e))

# ---- summary ----
print("=" * 64)
fail = 0
for name, status, msg in results:
    print(f"[{status}] {name}: {msg}")
    if status != "PASS":
        fail += 1
print("=" * 64)
print(f"summary: total={len(results)} pass={len(results)-fail} fail={fail}")
sys.exit(0 if fail == 0 else 1)
