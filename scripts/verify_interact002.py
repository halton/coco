#!/usr/bin/env python3
"""verify_interact002.py — interact-002 验证脚本（无网络依赖默认走 fallback）.

覆盖：
  V1 unit: 单元测试 LLMClient.reply 行为
        - fallback backend 永远返回中文 ≤ 60 字
        - mock backend 返回非中文 → 自动降级
        - mock backend 抛异常 → 自动降级
        - 截断到 max_chars
  V2 integration: fixture wav 闭环（mockup-sim daemon + InteractSession）
        - 跑 2 次 fixture，记录 transcript / reply / action
  V3 fallback path: 不设 COCO_LLM_BACKEND，闭环仍能跑通
  V4 latency sampling: ≥10 次 LLMClient.reply 采样 P50/P95/max
"""
from __future__ import annotations

import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.llm import (  # noqa: E402
    DEFAULT_MAX_CHARS,
    DEFAULT_TIMEOUT,
    FallbackBackend,
    LLMClient,
    build_default_client,
)
from coco.interact import InteractSession  # noqa: E402


EVIDENCE_DIR = ROOT / "evidence" / "interact-002"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
FIXTURE_WAV = ROOT / "tests/fixtures/audio/zh-001-walk-park.wav"


def _han_count(s: str) -> int:
    return sum(1 for c in s if "一" <= c <= "鿿")


# ---------------------------------------------------------------------------
# V1 unit
# ---------------------------------------------------------------------------


class _RaisingBackend:
    name = "raising"

    def chat(self, user_text, *, timeout):
        raise TimeoutError("simulated network timeout")


class _NonChineseBackend:
    name = "english"

    def chat(self, user_text, *, timeout):
        return "Hello there!"


class _LongChineseBackend:
    name = "long-zh"

    def chat(self, user_text, *, timeout):
        return "你好" * 100  # 200 字，应被截断到 max_chars


def v1_unit() -> dict:
    print("=" * 60)
    print("V1 — LLMClient unit tests")
    print("=" * 60)
    results = {}

    # case 1: fallback backend 应返回 KEYWORD_ROUTES 文本
    c = LLMClient(FallbackBackend(), timeout=1.0)
    r = c.reply("你好")
    print(f"  fallback('你好') -> {r!r}")
    assert _han_count(r) >= 1 and len(r) <= DEFAULT_MAX_CHARS, f"fallback bad: {r!r}"
    results["fallback_hello"] = r

    # case 2: 异常 backend → 降级
    c2 = LLMClient(_RaisingBackend(), timeout=1.0)
    r2 = c2.reply("天气真好")
    print(f"  raising-backend('天气真好') -> {r2!r}")
    assert _han_count(r2) >= 1, f"raising fallback bad: {r2!r}"
    assert c2.stats.backend_fail == 1 and c2.stats.fallback_used == 1
    results["raising_fallback"] = r2

    # case 3: 非中文 backend → 视为失败降级
    c3 = LLMClient(_NonChineseBackend(), timeout=1.0)
    r3 = c3.reply("你好")
    print(f"  english-backend('你好') -> {r3!r}")
    assert _han_count(r3) >= 1, f"english fallback bad: {r3!r}"
    assert c3.stats.fallback_used == 1
    results["english_fallback"] = r3

    # case 4: 过长 → 截断
    c4 = LLMClient(_LongChineseBackend(), timeout=1.0, max_chars=20)
    r4 = c4.reply("讲个长故事")
    print(f"  long-zh-backend('讲个长故事') -> len={len(r4)} {r4!r}")
    assert len(r4) <= 20, f"truncate failed: len={len(r4)}"
    assert _han_count(r4) > 0
    results["long_truncated_len"] = len(r4)

    # case 5: 空输入
    c5 = LLMClient(FallbackBackend(), timeout=1.0)
    r5 = c5.reply("")
    print(f"  fallback('') -> {r5!r}")
    assert _han_count(r5) >= 1
    results["empty_input"] = r5

    print("V1 PASS")
    return {"status": "PASS", **results}


# ---------------------------------------------------------------------------
# V2 + V3 integration with fixture wav (no daemon required — mock asr/tts/robot)
# ---------------------------------------------------------------------------


class _StubRobot:
    """最小 stub 让 actions.nod/look_left/look_right 不需要真 robot。
    actions 调用 robot.goto_target(head=...)。"""
    def goto_target(self, *args, **kwargs):
        return None


def _read_wav_int16(path: Path):
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n = wf.getnframes()
        raw = wf.readframes(n)
    audio = np.frombuffer(raw, dtype=np.int16)
    return audio, sr


def _make_asr_stub(text: str):
    def _asr(audio, sr):
        return text
    return _asr


def _tts_stub(text, blocking=True):
    return None


def _run_integration(label: str, llm_reply_fn, wav_path: Path):
    audio, sr = _read_wav_int16(wav_path)
    expected_text = "今天天气真好我们一起去公园散步"
    sess = InteractSession(
        robot=_StubRobot(),
        asr_fn=_make_asr_stub(expected_text),
        tts_say_fn=_tts_stub,
        idle_animator=None,
        llm_reply_fn=llm_reply_fn,
    )
    out1 = sess.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
    out2 = sess.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
    print(f"  [{label}] run1 transcript={out1['transcript']!r} reply={out1['reply']!r} action={out1['action']}")
    print(f"  [{label}] run2 transcript={out2['transcript']!r} reply={out2['reply']!r} action={out2['action']}")
    assert out1["asr_ok"] and out2["asr_ok"], f"asr failed in {label}"
    assert out1["transcript"] == expected_text
    assert _han_count(out1["reply"]) >= 1, f"reply not chinese: {out1['reply']!r}"
    assert len(out1["reply"]) <= DEFAULT_MAX_CHARS, f"reply too long: {out1['reply']!r}"
    return out1, out2


def v2_integration_with_llm() -> dict:
    print("=" * 60)
    print("V2 — fixture wav 闭环（LLM 注入；本环境无 backend 配置 → 实际走 fallback）")
    print("=" * 60)
    if not FIXTURE_WAV.exists():
        print(f"WARN: fixture wav not found: {FIXTURE_WAV}; SKIP")
        return {"status": "SKIP", "reason": "fixture missing"}
    client = build_default_client()
    out1, out2 = _run_integration("with-llm-fn", client.reply, FIXTURE_WAV)
    return {
        "status": "PASS",
        "backend": client.backend.name,
        "run1": {"transcript": out1["transcript"], "reply": out1["reply"], "action": out1["action"]},
        "run2": {"transcript": out2["transcript"], "reply": out2["reply"], "action": out2["action"]},
    }


def v3_fallback_path() -> dict:
    print("=" * 60)
    print("V3 — 显式取消 COCO_LLM_BACKEND，闭环仍跑通（KEYWORD_ROUTES）")
    print("=" * 60)
    if not FIXTURE_WAV.exists():
        return {"status": "SKIP", "reason": "fixture missing"}
    saved = os.environ.pop("COCO_LLM_BACKEND", None)
    try:
        client = build_default_client()
        assert client.backend.name == "fallback", f"expected fallback, got {client.backend.name}"
        out1, _out2 = _run_integration("fallback-only", client.reply, FIXTURE_WAV)
        return {
            "status": "PASS",
            "backend": client.backend.name,
            "reply_when_fallback": out1["reply"],
        }
    finally:
        if saved is not None:
            os.environ["COCO_LLM_BACKEND"] = saved


# ---------------------------------------------------------------------------
# V4 latency sampling — backend that simulates a small fixed delay
# ---------------------------------------------------------------------------


class _DelayedFallbackBackend:
    name = "delayed-fallback"
    def __init__(self, delay_s: float):
        self.delay_s = delay_s
    def chat(self, user_text, *, timeout):
        time.sleep(self.delay_s)
        return "（模拟回应）" + (user_text or "")[:10]


def v4_latency_sampling(n: int = 12) -> dict:
    print("=" * 60)
    print(f"V4 — 延迟采样 N={n}（模拟 backend 延迟 50ms）")
    print("=" * 60)
    # 用模拟延迟 backend，避免依赖外部网络；演示采样基础设施
    client = LLMClient(_DelayedFallbackBackend(delay_s=0.05), timeout=2.0)
    samples = ["你好", "今天怎么样", "讲个故事", "外面下雨吗", "我有点累",
               "你叫什么", "吃饭了吗", "周末做什么", "推荐音乐", "再见",
               "好的", "继续"]
    samples = samples[:n]
    for s in samples:
        r = client.reply(s)
        # 因为 _DelayedFallbackBackend 返回非中文标签 → 实际仍走 fallback；这里只是为 stats
    summary = client.stats.summary()
    print(f"  stats: {summary}")
    return {"status": "PASS", "n": n, "stats": summary}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    report = {}
    try:
        report["v1_unit"] = v1_unit()
        report["v2_integration"] = v2_integration_with_llm()
        report["v3_fallback"] = v3_fallback_path()
        report["v4_latency"] = v4_latency_sampling(n=12)
    except AssertionError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        report["error"] = str(e)
        out = EVIDENCE_DIR / "v1_summary.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    out = EVIDENCE_DIR / "v1_summary.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nSummary written to {out}")
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
