"""Spike v2: 直接用 sounddevice 验 mac 麦克。

策略转向：audio 与 robot 解耦。robot 只管动作，audio 走独立路径。
跑法：python spike_audio.py
预期：对 mac 麦克说话，看到非零 RMS。
"""

import time
import numpy as np
import sounddevice as sd

SAMPLERATE = 16000
CHANNELS = 1
DURATION_S = 3.0

print("input devices:")
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0:
        print(f"  [{i}] {d['name']}  (in={d['max_input_channels']}, sr={d['default_samplerate']:.0f})")

print(f"\ndefault input: {sd.default.device}")

input(f"\n按 Enter 开始录 {DURATION_S}s，然后对麦克说话...")
print("录音中...")
rec = sd.rec(int(SAMPLERATE * DURATION_S), samplerate=SAMPLERATE, channels=CHANNELS, dtype="float32")
sd.wait()

rms = float(np.sqrt(np.mean(rec ** 2)))
peak = float(np.max(np.abs(rec)))
print(f"整段: shape={rec.shape} rms={rms:.6f} peak={peak:.4f}")

# 分 6 段看 RMS 时序，便于看说话期与静默期对比
chunk = len(rec) // 6
for i in range(6):
    seg = rec[i * chunk:(i + 1) * chunk]
    seg_rms = float(np.sqrt(np.mean(seg ** 2)))
    seg_peak = float(np.max(np.abs(seg)))
    bar = "#" * int(seg_rms * 500)
    print(f"  [{i}] rms={seg_rms:.6f} peak={seg_peak:.4f}  {bar}")

print("done")
