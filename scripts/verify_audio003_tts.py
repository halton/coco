"""audio-003 V1: 本地 Kokoro TTS 合成「你好，我是可可」+ 写 wav + 元数据断言.

不依赖人工耳测（sub-agent 无声卡）。检查项：
- 模型可加载（_get_tts 返回 OfflineTts）
- 合成耗时合理（< 30s on macOS arm64）
- samples 非空、非全零、rms > 0.01（即有声）
- sample_rate 落在 [16000, 48000]
- wav 写入成功且文件大小 > 30KB（≥1s 16-bit 音频）
- wav 可被 wave 模块重新读取，与原 samples 一致

可选（联网时）：edge-tts 同句合成，写 edge_tts.wav；离线/未装 edge-tts 跳过并记 skip。

退出码：0=PASS，1=FAIL。
"""
from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

import numpy as np

from coco.tts import (
    DEFAULT_SID,
    KOKORO_DIR,
    has_edge_tts,
    synthesize,
    synthesize_edge,
    write_wav,
)

OUT_DIR = Path("tests/fixtures/audio/tts_out")
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_WAV = OUT_DIR / "local_kokoro.wav"
EDGE_WAV = OUT_DIR / "edge_tts.wav"

TEXT = "你好，我是可可"

errors: list[str] = []
notes: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        errors.append(msg)
        print(f"  FAIL {msg}")
    else:
        print(f"  ok   {msg}")


print("[audio-003 V1] 本地 Kokoro 合成")
print(f"  KOKORO_DIR={KOKORO_DIR}")
check(KOKORO_DIR.exists(), f"KOKORO_DIR 存在: {KOKORO_DIR}")

t0 = time.time()
try:
    samples, sr = synthesize(TEXT, sid=DEFAULT_SID)
    dt = time.time() - t0
    print(f"  synth ok dt={dt:.2f}s samples.shape={samples.shape} sr={sr}")
except Exception as e:
    print(f"  FAIL synth: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

check(dt < 30.0, f"synth 耗时 {dt:.2f}s < 30s")
check(samples.size > 0, f"samples.size={samples.size} > 0")
check(sr in (16000, 22050, 24000, 44100, 48000), f"sample_rate={sr} 在常用集合内")

duration = samples.size / sr
check(0.5 <= duration <= 10.0, f"duration={duration:.2f}s 在 [0.5, 10] 区间")

rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
peak = float(np.max(np.abs(samples)))
print(f"  rms={rms:.4f} peak={peak:.4f}")
check(rms > 0.01, f"rms={rms:.4f} > 0.01 (非静音)")
check(peak <= 1.0001, f"peak={peak:.4f} <= 1.0 (无 clip)")

# 写 wav
write_wav(LOCAL_WAV, samples, sr)
size = LOCAL_WAV.stat().st_size
print(f"  wrote {LOCAL_WAV} size={size}B")
check(size > 30 * 1024, f"wav size {size}B > 30KB")

# 回读校验
with wave.open(str(LOCAL_WAV), "rb") as w:
    assert w.getnchannels() == 1
    assert w.getsampwidth() == 2
    read_sr = w.getframerate()
    nframes = w.getnframes()
    raw = w.readframes(nframes)
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
print(f"  reread sr={read_sr} nframes={nframes} pcm.shape={pcm.shape}")
check(read_sr == sr, f"reread sr {read_sr} == 原 sr {sr}")
check(abs(nframes - samples.size) <= 1, f"reread nframes {nframes} ≈ 原 samples.size {samples.size}")
read_rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
check(abs(read_rms - rms) < 0.01, f"reread rms {read_rms:.4f} ≈ 原 rms {rms:.4f} (Δ<0.01)")

# 可选：edge-tts 联网兜底
print("\n[audio-003 V1] edge-tts 联网兜底（可选）")
if not has_edge_tts():
    notes.append("edge-tts 未安装（pyproject extras=tts-online 未启用）→ skip 联网兜底验证")
    print(f"  skip: {notes[-1]}")
else:
    try:
        t1 = time.time()
        e_samples, e_sr = synthesize_edge(TEXT, out_path=EDGE_WAV)
        dt2 = time.time() - t1
        if e_samples.size > 0 and e_sr > 0:
            write_wav(EDGE_WAV, e_samples, e_sr)
            e_rms = float(np.sqrt(np.mean(e_samples.astype(np.float64) ** 2)))
            print(f"  edge ok dt={dt2:.2f}s sr={e_sr} samples={e_samples.size} rms={e_rms:.4f}")
            notes.append(f"edge-tts ok: dt={dt2:.2f}s sr={e_sr} rms={e_rms:.4f}")
        else:
            notes.append("edge-tts 已合成 mp3 但 soundfile 解码失败 → 无 wav；不阻塞 PASS")
            print(f"  partial: {notes[-1]}")
    except Exception as e:
        notes.append(f"edge-tts 失败（无网/被墙/认证）: {type(e).__name__}: {e} → skip，不阻塞 PASS")
        print(f"  skip: {notes[-1]}")

# 总结
print("\n[audio-003 V1] 总结")
for n in notes:
    print(f"  note: {n}")

if errors:
    print(f"\n[audio-003 V1] FAIL {len(errors)} error(s)")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)

print(f"\n[audio-003 V1] PASS — Kokoro 本地合成 OK，wav 落 {LOCAL_WAV}")
sys.exit(0)
