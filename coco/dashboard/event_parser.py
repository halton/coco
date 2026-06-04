"""coco.dashboard.event_parser — 把 coco stdout 日志解析为结构化事件.

输入: 单行字符串 (从 /tmp/coco-stdout.log tail 出来)
输出: dict | None

支持的事件类型 (type):
- transcript : VAD/ASR 出来的用户语音转文字
- reply      : LLM 回复文本
- wake       : 唤醒词命中
- vision     : 视觉事件 (FaceTracker primary 等)
- tts        : TTS 首字延迟等
"""
from __future__ import annotations

import re
import time
from typing import Dict, Optional

# [coco][vad] transcript='你好' reply='你好呀!' ...
_VAD_RE = re.compile(
    r"\[coco\]\[vad\].*?transcript=(['\"])(?P<tx>.*?)\1.*?reply=(['\"])(?P<rp>.*?)\3"
)
# 简化版：transcript 单独出现
_VAD_TX_ONLY_RE = re.compile(r"\[coco\]\[vad\].*?transcript=(['\"])(?P<tx>.*?)\1")

# wake.hit / wake hit
_WAKE_RE = re.compile(r"wake[._]hit|\[wake\].*hit|wake.*matched")

# FaceTracker primary switch / new primary
_FACE_PRIMARY_RE = re.compile(
    r"FaceTracker.*primary(?:[_ ]switch)?[ =:].*?(?:track_id|id)[ =:](?P<tid>\w+)"
)
_FACE_PRIMARY_SIMPLE_RE = re.compile(r"FaceTracker.*primary")

# [tts] first_chunk_ms=123  或  tts.first_chunk_ms=123
_TTS_RE = re.compile(r"(?:\[tts\]|tts).*?first[_ ]chunk[_ ]ms[ =:](?P<ms>\d+)")


def parse_line(line: str) -> Optional[Dict[str, object]]:
    """把一行日志解析成 {ts, type, ...} 结构化事件，无法识别返回 None。"""
    if not line:
        return None
    s = line.rstrip("\n")
    ts = time.time()

    m = _VAD_RE.search(s)
    if m:
        return {
            "ts": ts,
            "type": "transcript",
            "transcript": m.group("tx"),
            "reply": m.group("rp"),
            "raw": s,
        }

    m = _TTS_RE.search(s)
    if m:
        return {
            "ts": ts,
            "type": "tts",
            "first_chunk_ms": int(m.group("ms")),
            "raw": s,
        }

    if _WAKE_RE.search(s):
        return {"ts": ts, "type": "wake", "raw": s}

    m = _FACE_PRIMARY_RE.search(s)
    if m:
        return {
            "ts": ts,
            "type": "vision",
            "event": "face_primary",
            "track_id": m.group("tid"),
            "raw": s,
        }
    if _FACE_PRIMARY_SIMPLE_RE.search(s):
        return {"ts": ts, "type": "vision", "event": "face_primary", "raw": s}

    m = _VAD_TX_ONLY_RE.search(s)
    if m:
        return {
            "ts": ts,
            "type": "transcript",
            "transcript": m.group("tx"),
            "reply": "",
            "raw": s,
        }

    return None
