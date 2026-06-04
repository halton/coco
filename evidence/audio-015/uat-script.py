"""audio-015 真机 UAT: 测首字延迟 (用户跑).

期望：edge-tts 真流式从原 ~6.2s 降到 <1.5s。

Run:
    .venv/bin/python evidence/audio-015/uat-script.py

输出形如：
    [tts] backend=edge-stream voice='zh-CN-XiaoxiaoNeural' sr=24000
    [tts] edge streaming first_chunk_ms=<N>
    total_ms=<M>

判定：
- first_chunk_ms < 1500：达标
- 听感：句首立即出声，无明显前置延迟
"""
import time
from coco.tts import say

text = "你好，我是可可，现在测试真流式播放的首字延迟。"
t0 = time.time()
say(text, prefer="edge")
print(f"total_ms={int((time.time()-t0)*1000)}")
