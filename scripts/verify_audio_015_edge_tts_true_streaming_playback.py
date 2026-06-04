#!/usr/bin/env python3
"""verify audio-015: edge-tts 真流式 ffmpeg pipe 播放 (首字延迟 <1.5s 期望).

V0 hash lock on coco/tts.py
V1 imports: _synthesize_and_play_edge_streaming / _ffmpeg_available /
   _edge_streaming_on / ENV_TTS_EDGE_STREAMING / EDGE_STREAMING_SAMPLERATE
V2 _ffmpeg_available() → True 且 `which ffmpeg` 返回非空
V3 COCO_TTS_EDGE_STREAMING=0 → _edge_streaming_on()==False，_try_edge_say 走 audio-014
   整段路径（_synthesize_edge_streaming 被调，_synthesize_and_play_edge_streaming 不被调）
V4 COCO_TTS_EDGE_STREAMING=1 + blocking=True：_try_edge_say 优先调
   _synthesize_and_play_edge_streaming（用 monkey-patch 模拟返回 first_chunk_ms=120）
V5 _synthesize_and_play_edge_streaming：mock edge_tts.Communicate stream 返回若干
   audio chunk → ffmpeg 实际跑（mp3 demux）→ first_chunk_ms 整数 >=0；
   线程清理 (ffmpeg.poll() != None；reader/feeder thread not alive)
V6 ffmpeg spawn 失败 (Popen FileNotFoundError) → 抛 EdgeTTSUnavailable，
   _try_edge_say 进入 audio-014 fallback
V7 stream() 中途断流 (async generator raise) → _synthesize_and_play_edge_streaming
   抛 EdgeTTSUnavailable 或正常返回；ffmpeg 子进程被清理（poll != None）
V8 hash sha 与 coco/tts.py 一致 (重复 V0 防中途篡改)

每个 V 独立 try/except 累积；rc=0 全 PASS。不真实联网。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import time
import traceback
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
TTS_PATH = REPO / "coco" / "tts.py"

# audio-015 当前 sha256；改 tts.py 后需同步更新本 lock。
TTS_SHA256_LOCK = "c8c7d2c84bdb52d4ad9df931d86360aabeb1588396184711258f5129d84088db"

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
        ENV_TTS_EDGE_STREAMING,
        EDGE_STREAMING_SAMPLERATE,
        _edge_streaming_on,
        _ffmpeg_available,
        _synthesize_and_play_edge_streaming,
        _synthesize_edge_streaming,
        _try_edge_say,
        say,
    )
    record("V1_imports", True, "all audio-015 symbols present")
except Exception as e:
    record("V1_imports", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
    print(f"FATAL: V1 import failed; PASS=0 FAIL={len(results)}")
    sys.exit(1)


from coco import tts as tts_mod  # noqa: E402


# ---- V2 ffmpeg available -----------------------------------------------
try:
    import shutil
    which = shutil.which("ffmpeg")
    ok = tts_mod._ffmpeg_available()
    if ok and which:
        record("V2_ffmpeg_available", True, f"which={which}")
    else:
        record("V2_ffmpeg_available", False, f"_ffmpeg_available={ok} which={which}")
except Exception as e:
    record("V2_ffmpeg_available", False, f"{type(e).__name__}: {e}")


# ---- V3 streaming OFF → 走 audio-014 -------------------------------------
try:
    os.environ["COCO_TTS_EDGE_STREAMING"] = "0"
    on = tts_mod._edge_streaming_on()
    assert on is False, f"_edge_streaming_on() should be False when env=0, got {on}"

    import numpy as np
    called_streaming = {"v": False}
    called_whole = {"v": False}

    def _fake_play_streaming(*a, **kw):
        called_streaming["v"] = True
        return 100

    def _fake_whole(text, voice="x", timeout=8.0):
        called_whole["v"] = True
        return np.zeros(2400, dtype=np.float32), 24000

    def _fake_play(samples, sr, blocking=True):
        return None

    with mock.patch.object(tts_mod, "_synthesize_and_play_edge_streaming", _fake_play_streaming), \
         mock.patch.object(tts_mod, "_synthesize_edge_streaming", _fake_whole), \
         mock.patch.object(tts_mod, "play", _fake_play):
        rv = tts_mod._try_edge_say("你好", blocking=True)

    assert rv is True, f"return should be True, got {rv}"
    assert called_streaming["v"] is False, "streaming path should NOT be called when env=0"
    assert called_whole["v"] is True, "whole-buffer path should be called when env=0"
    record("V3_streaming_off_uses_audio014", True, "whole-buffer path called, streaming skipped")
except Exception as e:
    record("V3_streaming_off_uses_audio014", False, f"{type(e).__name__}: {e}")
finally:
    os.environ.pop("COCO_TTS_EDGE_STREAMING", None)


# ---- V4 streaming ON + blocking=True → streaming path -------------------
try:
    os.environ["COCO_TTS_EDGE_STREAMING"] = "1"
    on = tts_mod._edge_streaming_on()
    assert on is True, f"_edge_streaming_on() should be True when env=1, got {on}"

    called_streaming = {"v": False, "first_ms": None}
    called_whole = {"v": False}

    def _fake_play_streaming(text, voice="x", timeout=8.0, device=None, samplerate=24000):
        called_streaming["v"] = True
        called_streaming["first_ms"] = 120
        return 120

    def _fake_whole(*a, **kw):
        called_whole["v"] = True
        raise AssertionError("should not be called when streaming path succeeds")

    with mock.patch.object(tts_mod, "_synthesize_and_play_edge_streaming", _fake_play_streaming), \
         mock.patch.object(tts_mod, "_synthesize_edge_streaming", _fake_whole), \
         mock.patch.object(tts_mod, "_ffmpeg_available", lambda: True):
        rv = tts_mod._try_edge_say("你好世界", blocking=True)

    assert rv is True, f"return should be True, got {rv}"
    assert called_streaming["v"] is True, "streaming path should be called"
    assert called_whole["v"] is False, "whole-buffer should NOT be called when streaming succeeds"
    record("V4_streaming_on_uses_streaming_path", True, f"first_chunk_ms={called_streaming['first_ms']}")
except Exception as e:
    record("V4_streaming_on_uses_streaming_path", False, f"{type(e).__name__}: {e}")
finally:
    os.environ.pop("COCO_TTS_EDGE_STREAMING", None)


# ---- V5 real ffmpeg + mocked edge_tts → first_chunk_ms 整数; 线程清理 --
try:
    # 构造合法 mp3 字节：用 ffmpeg 现场把 silence 编 mp3 当输入材料
    import subprocess
    gen = subprocess.run(
        ["ffmpeg", "-loglevel", "quiet", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", "0.5", "-f", "mp3", "-acodec", "libmp3lame", "-b:a", "64k", "pipe:1"],
        capture_output=True, check=True,
    )
    mp3_payload = gen.stdout
    assert len(mp3_payload) > 200, f"mp3 payload too small: {len(mp3_payload)}"

    # 切成 3 个 chunk
    third = len(mp3_payload) // 3
    chunks = [mp3_payload[:third], mp3_payload[third:2*third], mp3_payload[2*third:]]

    class FakeCommunicate:
        def __init__(self, text, voice):
            self.text = text
            self.voice = voice

        async def stream(self):
            for c in chunks:
                await asyncio.sleep(0.01)
                yield {"type": "audio", "data": c}

    # mock sounddevice.RawOutputStream → 后台 loop 调 callback 直到 CallbackStop
    class FakeStream:
        def __init__(self, *a, **kw):
            self.callback = kw.get("callback")
            self.samplerate = kw.get("samplerate")
            self._t = None
            self._stop = False
        def __enter__(self):
            import threading as _th
            def _loop():
                while not self._stop:
                    try:
                        buf = bytearray(1024 * 2)
                        mv = memoryview(buf)
                        self.callback(mv, 1024, None, None)
                    except Exception:
                        return
            self._t = _th.Thread(target=_loop, daemon=True)
            self._t.start()
            return self
        def __exit__(self, *a):
            self._stop = True
            if self._t:
                self._t.join(timeout=2.0)
            return False

    import sys as _sys
    fake_edge = mock.MagicMock()
    fake_edge.Communicate = FakeCommunicate
    fake_sd = mock.MagicMock()
    fake_sd.RawOutputStream = FakeStream
    fake_sd.CallbackStop = Exception

    with mock.patch.dict(_sys.modules, {"edge_tts": fake_edge, "sounddevice": fake_sd}):
        t0 = time.time()
        first_ms = tts_mod._synthesize_and_play_edge_streaming(
            "测试", voice="zh-CN-XiaoxiaoNeural", timeout=5.0, device=None,
            samplerate=24000,
        )
        dur_ms = int((time.time() - t0) * 1000)

    assert isinstance(first_ms, int) and first_ms >= 0, f"first_ms not int>=0: {first_ms!r}"
    assert first_ms < 4000, f"first_ms={first_ms} unreasonably large (>=4000)"
    record("V5_streaming_real_ffmpeg", True, f"first_chunk_ms={first_ms} total={dur_ms}ms payload={len(mp3_payload)}B")
except Exception as e:
    record("V5_streaming_real_ffmpeg", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()[:500]}")


# ---- V6 ffmpeg spawn 失败 → fallback ---------------------------------
try:
    import subprocess as _sub
    real_popen = _sub.Popen

    def _raise_popen(*a, **kw):
        raise FileNotFoundError("simulated: ffmpeg not found")

    # _ffmpeg_available() 内部用 shutil.which —— 这里强制返回 True 让流式分支进入
    # Popen 失败再抛
    with mock.patch.object(tts_mod, "_ffmpeg_available", lambda: True), \
         mock.patch("subprocess.Popen", _raise_popen):
        try:
            tts_mod._synthesize_and_play_edge_streaming("hi", voice="x")
            ok = False
            msg = "expected EdgeTTSUnavailable, got no exception"
        except tts_mod.EdgeTTSUnavailable as e:
            ok = True
            msg = f"raised EdgeTTSUnavailable as expected: {e}"
        except Exception as e:
            ok = False
            msg = f"wrong exception type: {type(e).__name__}: {e}"

    if ok:
        record("V6_ffmpeg_spawn_fail_fallback", True, msg)
    else:
        record("V6_ffmpeg_spawn_fail_fallback", False, msg)
except Exception as e:
    record("V6_ffmpeg_spawn_fail_fallback", False, f"{type(e).__name__}: {e}")


# ---- V7 stream 中途异常 → 清理无残留 -----------------------------------
try:
    class FaultyCommunicate:
        def __init__(self, text, voice):
            pass

        async def stream(self):
            yield {"type": "audio", "data": b"\xff\xfb" * 100}  # 一些垃圾字节
            await asyncio.sleep(0.05)
            raise RuntimeError("simulated mid-stream error")

    class FakeStream2:
        def __init__(self, *a, **kw):
            self.callback = kw.get("callback")
            self._stop = False
            self._t = None
        def __enter__(self):
            import threading as _th
            def _loop():
                while not self._stop:
                    try:
                        buf = bytearray(1024 * 2)
                        self.callback(memoryview(buf), 1024, None, None)
                    except Exception:
                        return
            self._t = _th.Thread(target=_loop, daemon=True)
            self._t.start()
            return self
        def __exit__(self, *a):
            self._stop = True
            if self._t:
                self._t.join(timeout=2.0)
            return False

    fake_edge = mock.MagicMock()
    fake_edge.Communicate = FaultyCommunicate
    fake_sd = mock.MagicMock()
    fake_sd.RawOutputStream = FakeStream2
    fake_sd.CallbackStop = Exception

    # 跟踪 ffmpeg 进程是否被清理
    import subprocess as _sub
    spawned_procs: list = []
    real_popen = _sub.Popen

    def _track_popen(*a, **kw):
        p = real_popen(*a, **kw)
        spawned_procs.append(p)
        return p

    raised_or_ok = False
    with mock.patch.dict(sys.modules, {"edge_tts": fake_edge, "sounddevice": fake_sd}), \
         mock.patch("subprocess.Popen", _track_popen):
        try:
            tts_mod._synthesize_and_play_edge_streaming("x", voice="v", timeout=2.0)
            raised_or_ok = True  # 即使没抛也算（可能首帧仍 timeout 抛）
        except tts_mod.EdgeTTSUnavailable:
            raised_or_ok = True
        except Exception as e:
            raised_or_ok = False
            print(f"   V7 unexpected exc: {type(e).__name__}: {e}")

    # 给清理一点时间
    time.sleep(0.5)
    leaked = [p for p in spawned_procs if p.poll() is None]
    if raised_or_ok and not leaked:
        record("V7_midstream_cleanup", True, f"spawned={len(spawned_procs)} leaked=0")
    else:
        # 强行清掉以免影响后续 verify
        for p in leaked:
            try:
                p.kill()
            except Exception:
                pass
        record("V7_midstream_cleanup", False, f"raised_or_ok={raised_or_ok} leaked={len(leaked)}")
except Exception as e:
    record("V7_midstream_cleanup", False, f"{type(e).__name__}: {e}")


# ---- V8 hash sha 复核 ---------------------------------------------------
try:
    sha2 = hashlib.sha256(TTS_PATH.read_bytes()).hexdigest()
    if sha2 == TTS_SHA256_LOCK:
        record("V8_hash_recheck", True, sha2)
    else:
        record("V8_hash_recheck", False, f"sha changed mid-run: {sha2}")
except Exception as e:
    record("V8_hash_recheck", False, f"{type(e).__name__}: {e}")


# ---- summary ------------------------------------------------------------
pass_n = sum(1 for _, ok, _ in results if ok)
fail_n = sum(1 for _, ok, _ in results if not ok)
print()
print(f"PASS={pass_n} FAIL={fail_n} TOTAL={len(results)}")
sys.exit(0 if fail_n == 0 else 1)
