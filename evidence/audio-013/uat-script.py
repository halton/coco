#!/usr/bin/env python3
"""audio-013 真机 UAT 脚本（user_pending）。

用法（真机接 Reachy Mini Audio，daemon/coco 不需要运行）：
    cd /Users/halton/work/coco
    .venv/bin/python evidence/audio-013/uat-script.py

预期：
- 终端输出 `[tts] output device=1 sr=16000`（auto-detect 命中 Reachy Mini Audio + 重采样 24000→16000）
- 通过 Reachy Mini 的扬声器听到一句中文："你好，我是可可，现在我在用机器人扬声器说话"

显式覆盖测试（可选）：
    COCO_TTS_OUTPUT_DEVICE="MacBook Air Speakers" .venv/bin/python evidence/audio-013/uat-script.py
"""
from coco import tts

if __name__ == "__main__":
    tts.say("你好，我是可可，现在我在用机器人扬声器说话")
    print("[uat] done")
