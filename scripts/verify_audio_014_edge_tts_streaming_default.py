#!/usr/bin/env python3
"""verify audio-014: edge-tts 流式 + 联网默认 + 断网回退 Kokoro.

V0 hash lock on coco/tts.py
V1 imports: _synthesize_edge_streaming / _is_edge_tts_reachable / EdgeTTSUnavailable
V2 prefer="local" → 不调 _is_edge_tts_reachable，不调 _synthesize_edge_streaming
V3 prefer="edge" + edge OK → 走 edge 路径 (调 _synthesize_edge_streaming, 不 fallback synthesize)
V4 prefer="auto" + edge unreachable → 走 local fallback (调 synthesize)
V5 prefer="auto" + reachable=True 但 streaming raise EdgeTTSUnavailable → fallback local
V6 _synthesize_edge_streaming 内部 stereo decode → 自动 mean 成 mono

每个 V 独立 try/except 累积失败；rc=0 全 PASS。
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import traceback
from pathlib import Path
from unittest import mock

import numpy as np

REPO = Path(__file__).resolve().parents[1]
TTS_PATH = REPO / "coco" / "tts.py"

# audio-014 当前 sha256；改 tts.py 后需同步更新本 lock。
TTS_SHA256_LOCK = "71a075d785fdab0043116d8e2fb1e28eea52da5a9b66b4d04d6b2edf3d65e26c"


results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, msg: str = "") -> None:
    results.append((name, ok, msg))
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}: {msg}")


# ---- V0 hash lock -------------------------------------------------------
try:
    sha = hashlib.sha256(TTS_PATH.read_bytes()).hexdigest()
    if sha != TTS_SHA256_LOCK:
        record("V0_hash_lock", False, f"coco/tts.py sha256={sha} != lock={TTS_SHA256_LOCK}")
    else:
        record("V0_hash_lock", True, sha)
except Exception as e:
    record("V0_hash_lock", False, f"{type(e).__name__}: {e}")


# ---- V1 imports ---------------------------------------------------------
try:
    from coco.tts import (  # noqa: F401
        EdgeTTSUnavailable,
        _is_edge_tts_reachable,
        _synthesize_edge_streaming,
        say,
        synthesize,
    )
    record("V1_imports", True, "all symbols present")
except Exception as e:
    record("V1_imports", False, f"{type(e).__name__}: {e}")
    print("FATAL: V1 import failed, aborting subsequent tests")
    print(f"PASS={sum(1 for _,ok,_ in results if ok)} FAIL={sum(1 for _,ok,_ in results if not ok)}")
    sys.exit(1)


from coco import tts as tts_mod  # noqa: E402


def _silent_play(*_a, **_kw) -> None:
    return None


# ---- V2 prefer=local --------------------------------------------------
try:
    os.environ.pop("COCO_TTS_PREFER", None)
    fake_samples = np.zeros(1600, dtype=np.float32)
    with mock.patch.object(tts_mod, "_is_edge_tts_reachable") as m_reach, \
         mock.patch.object(tts_mod, "_synthesize_edge_streaming") as m_edge, \
         mock.patch.object(tts_mod, "synthesize", return_value=(fake_samples, 24000)) as m_local, \
         mock.patch.object(tts_mod, "play", new=_silent_play):
        tts_mod.say("你好", prefer="local")
    if m_reach.called:
        record("V2_local_no_reach", False, "_is_edge_tts_reachable should not be called for prefer=local")
    elif m_edge.called:
        record("V2_local_no_edge", False, "_synthesize_edge_streaming should not be called for prefer=local")
    elif not m_local.called:
        record("V2_local_calls_synthesize", False, "synthesize() not called")
    else:
        record("V2_prefer_local", True, "local-only path confirmed")
except Exception as e:
    record("V2_prefer_local", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---- V3 prefer=edge OK -------------------------------------------------
try:
    fake_samples = np.linspace(-0.1, 0.1, 4800, dtype=np.float32)
    with mock.patch.object(tts_mod, "_is_edge_tts_reachable", return_value=True), \
         mock.patch.object(tts_mod, "_synthesize_edge_streaming", return_value=(fake_samples, 24000)) as m_edge, \
         mock.patch.object(tts_mod, "synthesize") as m_local, \
         mock.patch.object(tts_mod, "play", new=_silent_play):
        tts_mod.say("你好", prefer="edge")
    if not m_edge.called:
        record("V3_prefer_edge", False, "edge streaming not called")
    elif m_local.called:
        record("V3_prefer_edge", False, "local synthesize unexpectedly called on edge success")
    else:
        record("V3_prefer_edge", True, "edge path taken")
except Exception as e:
    record("V3_prefer_edge", False, f"{type(e).__name__}: {e}")


# ---- V4 auto + unreachable → local --------------------------------------
try:
    fake_samples = np.zeros(2400, dtype=np.float32)
    with mock.patch.object(tts_mod, "_is_edge_tts_reachable", return_value=False) as m_reach, \
         mock.patch.object(tts_mod, "_synthesize_edge_streaming") as m_edge, \
         mock.patch.object(tts_mod, "synthesize", return_value=(fake_samples, 24000)) as m_local, \
         mock.patch.object(tts_mod, "play", new=_silent_play):
        tts_mod.say("你好", prefer="auto")
    if not m_reach.called:
        record("V4_auto_unreachable", False, "_is_edge_tts_reachable not consulted")
    elif m_edge.called:
        record("V4_auto_unreachable", False, "edge streaming called despite unreachable")
    elif not m_local.called:
        record("V4_auto_unreachable", False, "local synthesize not called")
    else:
        record("V4_auto_unreachable_fallback", True, "auto→local on unreachable")
except Exception as e:
    record("V4_auto_unreachable", False, f"{type(e).__name__}: {e}")


# ---- V5 auto + reachable but edge raises --------------------------------
try:
    fake_samples = np.zeros(2400, dtype=np.float32)
    def _raise(*_a, **_kw):
        raise tts_mod.EdgeTTSUnavailable("simulated network drop")

    with mock.patch.object(tts_mod, "_is_edge_tts_reachable", return_value=True), \
         mock.patch.object(tts_mod, "_synthesize_edge_streaming", side_effect=_raise) as m_edge, \
         mock.patch.object(tts_mod, "synthesize", return_value=(fake_samples, 24000)) as m_local, \
         mock.patch.object(tts_mod, "play", new=_silent_play):
        tts_mod.say("你好", prefer="auto")
    if not m_edge.called:
        record("V5_auto_edge_raise", False, "edge streaming not attempted")
    elif not m_local.called:
        record("V5_auto_edge_raise", False, "local fallback not invoked after edge exception")
    else:
        record("V5_auto_edge_raise_fallback", True, "auto→local on EdgeTTSUnavailable")
except Exception as e:
    record("V5_auto_edge_raise", False, f"{type(e).__name__}: {e}")


# ---- V6 stereo decode → mono --------------------------------------------
try:
    import soundfile as sf  # type: ignore

    # 合成一个 stereo mp3 buf 写到 BytesIO 是 mp3 编码，跨平台不一定有 mp3 encoder。
    # 直接 stub: patch soundfile.read 返回 (N,2) array，模拟 stereo decode。
    stereo = np.stack(
        [np.linspace(-0.5, 0.5, 1600, dtype=np.float32),
         np.linspace(0.5, -0.5, 1600, dtype=np.float32)],
        axis=1,
    )  # shape (1600, 2)

    async def _fake_stream(self):
        yield {"type": "audio", "data": b"\x00\x01\x02\x03"}

    class _FakeComm:
        def __init__(self, *_a, **_kw): pass
        def stream(self):
            return _fake_stream(self)

    import edge_tts  # type: ignore
    with mock.patch.object(edge_tts, "Communicate", _FakeComm), \
         mock.patch.object(sf, "read", return_value=(stereo, 24000)):
        samples, sr = tts_mod._synthesize_edge_streaming("你好", voice="zh-CN-XiaoxiaoNeural", timeout=2.0)
    if samples.ndim != 1:
        record("V6_stereo_to_mono", False, f"ndim={samples.ndim}, expected 1")
    elif samples.shape[0] != 1600:
        record("V6_stereo_to_mono", False, f"len={samples.shape[0]}, expected 1600")
    elif samples.dtype != np.float32:
        record("V6_stereo_to_mono", False, f"dtype={samples.dtype}, expected float32")
    elif sr != 24000:
        record("V6_stereo_to_mono", False, f"sr={sr}, expected 24000")
    else:
        # mean of (-0.5, 0.5) per row = 0; check first & last ~0
        if abs(float(samples[0])) > 1e-6 or abs(float(samples[-1])) > 1e-6:
            record("V6_stereo_to_mono", False, f"mean mismatch first={samples[0]} last={samples[-1]}")
        else:
            record("V6_stereo_to_mono", True, "stereo averaged to mono float32")
except Exception as e:
    record("V6_stereo_to_mono", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---- summary -----------------------------------------------------------
n_pass = sum(1 for _, ok, _ in results if ok)
n_fail = sum(1 for _, ok, _ in results if not ok)
print(f"\nSUMMARY: PASS={n_pass} FAIL={n_fail} / TOTAL={len(results)}")
for name, ok, msg in results:
    print(f"  {'PASS' if ok else 'FAIL'} {name}")

sys.exit(0 if n_fail == 0 else 1)
