"""真机活体验证：跑 edge-tts 流式 + 本机 Reachy Mini Audio 扬声器播一段中文。

需要联网。预期：
  - synth_ms < 2000ms（流式应明显快于以前 mp3 落盘）
  - play_ms ≈ 音频实际时长
  - 听到温暖女声（zh-CN-XiaoxiaoNeural）
  - log 含 `[tts] backend=edge voice='zh-CN-XiaoxiaoNeural' sr=24000`

跑法（在 repo 根目录）：
  source .venv/bin/activate
  python evidence/audio-014/uat-script.py

断网测试（拔网线 / 关 Wi-Fi 后跑）：
  应看到 `[tts] edge unreachable (auto), using local`
  紧接 Kokoro 离线音正常播出，无沉默 / 无崩溃。
"""
from __future__ import annotations

import time

from coco import tts as t


def main() -> None:
    text = "你好，我是可可，这是 edge-tts 的小小音色，响应应该比之前快很多。"
    t0 = time.time()
    samples, sr = t.synthesize_edge(text)
    synth_ms = int((time.time() - t0) * 1000)
    print(f"synth_ms={synth_ms} sr={sr} samples={len(samples)}")
    t1 = time.time()
    t.play(samples, sr, blocking=True)
    play_ms = int((time.time() - t1) * 1000)
    print(f"play_ms={play_ms}")


if __name__ == "__main__":
    main()
