#!/usr/bin/env python3
"""verify_interact004.py — interact-004 多轮对话上下文验证.

覆盖：
  V1 DialogMemory ring buffer：append + recent_turns + max_turns 截断（先进先出）
  V2 idle 自动清空：fake clock 推进 idle_timeout+1 后下一次 op 触发 reset
  V3 build_messages 结构正确（system + 历史 user/assistant 对 + 当前 user）
  V4 env：默认 OFF；COCO_DIALOG_MEMORY=1 ON；非法值 clamp 警告
  V5 LLMClient.reply(history=) 透传到 backend.chat（OpenAI / Ollama 兼容路径）
     + FallbackBackend 忽略 history 仍正常返回中文
  V6 InteractSession 集成：dialog_memory=None 不影响（向后兼容）；
     注入 DialogMemory 时第 1 轮 backend 收到 history=空，第 2 轮收到上一轮
  V7 InteractSession + idle reset：跨 idle 第 3 轮 backend 收到 history=空
  V8 history 长度上限 ≤ N：N=2，连灌 5 轮，第 6 轮 backend 收到 history 长度 ≤4
     （N 轮 = N 对 = 2*N messages）

evidence/interact-004/verify_summary.json 记录通过情况与统计。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import List, Optional

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.dialog import (  # noqa: E402
    DEFAULT_IDLE_TIMEOUT_S,
    DEFAULT_MAX_TURNS,
    DialogConfig,
    DialogMemory,
    config_from_env,
    dialog_memory_enabled_from_env,
)
from coco.llm import (  # noqa: E402
    FallbackBackend,
    LLMClient,
)
from coco.interact import InteractSession  # noqa: E402


EVIDENCE_DIR = ROOT / "evidence" / "interact-004"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class FakeClock:
    """注入 monotonic 时钟，无线程依赖。"""

    def __init__(self, t0: float = 1000.0) -> None:
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class RecordingBackend:
    """记录 chat() 收到的 history 与 user_text，每次返回固定中文。"""

    name = "recording"

    def __init__(self, reply: str = "好的呀") -> None:
        self.reply = reply
        self.calls: List[dict] = []  # [{user_text, history(list or None)}]

    def chat(self, user_text, *, timeout, history=None):
        # 深拷贝避免后续突变
        h = None if history is None else [dict(m) for m in history]
        self.calls.append({"user_text": user_text, "history": h, "timeout": timeout})
        return self.reply


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# ---------------------------------------------------------------------------
# V1
# ---------------------------------------------------------------------------


def v1_ring_buffer() -> dict:
    print("=" * 60)
    print("V1 — DialogMemory ring buffer + max_turns 截断")
    print("=" * 60)
    clk = FakeClock()
    mem = DialogMemory(max_turns=4, idle_timeout_s=120.0, clock=clk)
    _ok(len(mem) == 0, "初始应为空")
    _ok(mem.recent_turns() == [], "初始 recent_turns 应空")

    pairs = [("u1", "a1"), ("u2", "a2"), ("u3", "a3"), ("u4", "a4")]
    for u, a in pairs:
        clk.advance(1.0)
        mem.append(u, a)
    _ok(len(mem) == 4, f"4 轮后 len 应为 4，实际 {len(mem)}")
    _ok(mem.recent_turns() == pairs, f"4 轮后内容不对：{mem.recent_turns()}")

    # 第 5 轮把 u1 挤掉
    clk.advance(1.0)
    mem.append("u5", "a5")
    expected = [("u2", "a2"), ("u3", "a3"), ("u4", "a4"), ("u5", "a5")]
    _ok(mem.recent_turns() == expected, f"超长 FIFO 错：{mem.recent_turns()}")

    # clear
    mem.clear()
    _ok(len(mem) == 0, "clear 后应为空")

    print("V1 PASS")
    return {"name": "V1 ring buffer", "passed": True, "turns_after_overflow": len(expected)}


# ---------------------------------------------------------------------------
# V2
# ---------------------------------------------------------------------------


def v2_idle_reset() -> dict:
    print("=" * 60)
    print("V2 — idle 自动清空（fake clock）")
    print("=" * 60)
    clk = FakeClock()
    mem = DialogMemory(max_turns=4, idle_timeout_s=10.0, clock=clk)
    mem.append("hello", "hi")
    _ok(len(mem) == 1, "append 1 后应有 1 轮")

    # 推进 < idle_timeout：不应清
    clk.advance(5.0)
    _ok(len(mem.recent_turns()) == 1, "未超 idle 不应 reset")

    # 推进 > idle_timeout：下次 op 应触发 reset
    clk.advance(20.0)  # 总 25s > 10s（注意 _last_append_ts 在第一次 advance 后未更新）
    turns = mem.recent_turns()
    _ok(turns == [], f"超 idle 后 recent_turns 应空，实际 {turns}")

    # append 后再次进入新会话
    mem.append("u_new", "a_new")
    _ok(mem.recent_turns() == [("u_new", "a_new")], "idle 后新会话起点错")

    # 紧接 append 不应触发 idle
    clk.advance(1.0)
    mem.append("u2", "a2")
    _ok(len(mem) == 2, "未超 idle 不应清")

    print("V2 PASS")
    return {"name": "V2 idle reset", "passed": True}


# ---------------------------------------------------------------------------
# V3
# ---------------------------------------------------------------------------


def v3_build_messages() -> dict:
    print("=" * 60)
    print("V3 — build_messages 结构")
    print("=" * 60)
    mem = DialogMemory(max_turns=4)
    msgs0 = mem.build_messages("SYS", "你好")
    _ok(msgs0[0] == {"role": "system", "content": "SYS"}, f"system 头错：{msgs0[0]}")
    _ok(msgs0[-1] == {"role": "user", "content": "你好"}, f"末尾应是当前 user：{msgs0[-1]}")
    _ok(len(msgs0) == 2, f"空历史应只有 system+user，实际 {len(msgs0)}")

    mem.append("看这只猫", "这是一只可爱的小猫～")
    msgs1 = mem.build_messages("SYS", "它叫什么？")
    # 期望 system, user(看这只猫), assistant(...), user(它叫什么？)
    _ok(len(msgs1) == 4, f"1 轮历史应 4 条，实际 {len(msgs1)}")
    _ok(msgs1[1]["role"] == "user" and "猫" in msgs1[1]["content"], f"历史 user 错：{msgs1[1]}")
    _ok(msgs1[2]["role"] == "assistant", f"历史 assistant 错：{msgs1[2]}")
    _ok(msgs1[3] == {"role": "user", "content": "它叫什么？"}, f"末尾错：{msgs1[3]}")

    # 空 system 时不输出 system
    mem2 = DialogMemory()
    msgs2 = mem2.build_messages("", "hi")
    _ok(msgs2 == [{"role": "user", "content": "hi"}], f"空 system 错：{msgs2}")

    print("V3 PASS")
    return {"name": "V3 build_messages", "passed": True}


# ---------------------------------------------------------------------------
# V4
# ---------------------------------------------------------------------------


def v4_env() -> dict:
    print("=" * 60)
    print("V4 — env helpers")
    print("=" * 60)
    saved = {k: os.environ.get(k) for k in ("COCO_DIALOG_MEMORY", "COCO_DIALOG_MAX_TURNS", "COCO_DIALOG_IDLE_S")}
    try:
        # 默认 OFF
        for k in saved:
            os.environ.pop(k, None)
        _ok(dialog_memory_enabled_from_env() is False, "默认应 OFF")
        cfg_def = config_from_env()
        _ok(cfg_def.max_turns == DEFAULT_MAX_TURNS, f"默认 N 错：{cfg_def.max_turns}")
        _ok(cfg_def.idle_timeout_s == DEFAULT_IDLE_TIMEOUT_S, f"默认 idle 错：{cfg_def.idle_timeout_s}")

        # ON 各种值
        for v in ("1", "true", "yes", "on"):
            os.environ["COCO_DIALOG_MEMORY"] = v
            _ok(dialog_memory_enabled_from_env() is True, f"{v!r} 应 ON")
        os.environ["COCO_DIALOG_MEMORY"] = "0"
        _ok(dialog_memory_enabled_from_env() is False, "0 应 OFF")

        # 非法 / 越界 clamp
        os.environ["COCO_DIALOG_MAX_TURNS"] = "0"
        os.environ["COCO_DIALOG_IDLE_S"] = "0"
        cfg = config_from_env()
        _ok(cfg.max_turns == 1, f"N=0 应 clamp 到 1，实际 {cfg.max_turns}")
        _ok(cfg.idle_timeout_s == 1.0, f"idle=0 应 clamp 到 1.0，实际 {cfg.idle_timeout_s}")

        os.environ["COCO_DIALOG_MAX_TURNS"] = "100"
        os.environ["COCO_DIALOG_IDLE_S"] = "99999"
        cfg = config_from_env()
        _ok(cfg.max_turns == 16, f"N=100 应 clamp 到 16，实际 {cfg.max_turns}")
        _ok(cfg.idle_timeout_s == 3600.0, f"idle=99999 应 clamp 到 3600，实际 {cfg.idle_timeout_s}")

        os.environ["COCO_DIALOG_MAX_TURNS"] = "abc"
        os.environ["COCO_DIALOG_IDLE_S"] = "xyz"
        cfg = config_from_env()
        _ok(cfg.max_turns == DEFAULT_MAX_TURNS, "非整数应回退默认")
        _ok(cfg.idle_timeout_s == DEFAULT_IDLE_TIMEOUT_S, "非数字应回退默认")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print("V4 PASS")
    return {"name": "V4 env", "passed": True}


# ---------------------------------------------------------------------------
# V5
# ---------------------------------------------------------------------------


def v5_llm_history_passthrough() -> dict:
    print("=" * 60)
    print("V5 — LLMClient.reply(history=) 透传 + Fallback 忽略")
    print("=" * 60)
    rec = RecordingBackend(reply="可以的呀")
    client = LLMClient(backend=rec, timeout=2.0)

    # 无 history
    out1 = client.reply("第一句")
    _ok(out1 == "可以的呀", f"reply 错：{out1!r}")
    _ok(rec.calls[-1]["history"] is None, f"无 history 时 backend 应收 None，实际 {rec.calls[-1]['history']}")

    # 有 history
    history = [
        {"role": "user", "content": "看这只猫"},
        {"role": "assistant", "content": "这是一只可爱的小猫～"},
    ]
    out2 = client.reply("它叫什么？", history=history)
    _ok(out2 == "可以的呀", f"reply 错：{out2!r}")
    seen = rec.calls[-1]["history"]
    _ok(seen == history, f"history 透传错：{seen}")
    _ok(rec.calls[-1]["user_text"] == "它叫什么？", "user_text 透传错")

    # FallbackBackend 接受 history kwarg 但忽略
    fb = FallbackBackend()
    fb_client = LLMClient(backend=fb, timeout=2.0)
    out3 = fb_client.reply("看这只猫", history=history)
    _ok(out3 and isinstance(out3, str), f"fallback 应返回字符串：{out3!r}")
    # 中文检查
    _ok(any("一" <= c <= "鿿" for c in out3), f"fallback 应含中文：{out3!r}")

    print("V5 PASS")
    return {"name": "V5 llm history passthrough", "passed": True, "rec_calls": len(rec.calls)}


# ---------------------------------------------------------------------------
# V6 / V7 / V8 — InteractSession 集成
# ---------------------------------------------------------------------------


def _make_session(asr_text_seq: List[str], backend: RecordingBackend,
                  dialog_memory: Optional[DialogMemory]) -> InteractSession:
    """构造一个最小 session：ASR 按序列返回；TTS 静默；LLM 走 RecordingBackend。"""
    seq_iter = iter(asr_text_seq)

    def _asr(_audio: np.ndarray, _sr: int) -> str:
        return next(seq_iter)

    def _tts(_text: str, **_kw) -> None:
        return None

    client = LLMClient(backend=backend, timeout=2.0)

    def _llm_reply(text: str, *, history=None) -> str:
        return client.reply(text, history=history)

    return InteractSession(
        robot=None,
        asr_fn=_asr,
        tts_say_fn=_tts,
        llm_reply_fn=_llm_reply,
        dialog_memory=dialog_memory,
    )


def _dummy_audio() -> np.ndarray:
    return np.zeros(1600, dtype=np.int16)


def v6_session_basic_history() -> dict:
    print("=" * 60)
    print("V6 — InteractSession 集成：第 1 轮无 history，第 2 轮含上一轮")
    print("=" * 60)

    # 6a: dialog_memory=None 时 backend 应始终收到 history=None / 空
    rec_a = RecordingBackend(reply="嗯嗯")
    sess_a = _make_session(["你好", "再说一遍"], rec_a, dialog_memory=None)
    sess_a.handle_audio(_dummy_audio(), 16000, skip_action=True, skip_tts_play=True)
    sess_a.handle_audio(_dummy_audio(), 16000, skip_action=True, skip_tts_play=True)
    _ok(len(rec_a.calls) == 2, f"应 2 次 LLM 调用：{len(rec_a.calls)}")
    _ok(rec_a.calls[0]["history"] is None and rec_a.calls[1]["history"] is None,
        f"dialog_memory=None 时 history 应都为 None：{[c['history'] for c in rec_a.calls]}")

    # 6b: 注入 DialogMemory，第 1 轮 history=空，第 2 轮 history 含 (u1,a1)
    rec_b = RecordingBackend(reply="好呀")
    mem = DialogMemory(max_turns=4, idle_timeout_s=300.0)
    sess_b = _make_session(["看这只猫", "它叫什么？"], rec_b, dialog_memory=mem)
    r1 = sess_b.handle_audio(_dummy_audio(), 16000, skip_action=True, skip_tts_play=True)
    r2 = sess_b.handle_audio(_dummy_audio(), 16000, skip_action=True, skip_tts_play=True)
    _ok(r1["transcript"] == "看这只猫", f"r1 transcript 错：{r1}")
    _ok(r2["transcript"] == "它叫什么？", f"r2 transcript 错：{r2}")

    h1 = rec_b.calls[0]["history"]
    h2 = rec_b.calls[1]["history"]
    _ok(h1 == [] or h1 is None, f"第 1 轮 history 应空：{h1}")
    _ok(isinstance(h2, list) and len(h2) == 2,
        f"第 2 轮 history 应含 user+assistant 两条：{h2}")
    _ok(h2[0]["role"] == "user" and h2[0]["content"] == "看这只猫", f"h2[0] 错：{h2[0]}")
    _ok(h2[1]["role"] == "assistant" and h2[1]["content"] == "好呀", f"h2[1] 错：{h2[1]}")

    print("V6 PASS")
    return {"name": "V6 session basic history", "passed": True,
            "round2_history_len": len(h2)}


def v7_session_idle_reset() -> dict:
    print("=" * 60)
    print("V7 — InteractSession + idle reset：跨 idle 第 3 轮 history=空")
    print("=" * 60)
    clk = FakeClock()
    rec = RecordingBackend(reply="好呀")
    mem = DialogMemory(max_turns=4, idle_timeout_s=10.0, clock=clk)
    sess = _make_session(["你好", "再说一遍", "新话题"], rec, dialog_memory=mem)

    sess.handle_audio(_dummy_audio(), 16000, skip_action=True, skip_tts_play=True)
    clk.advance(1.0)
    sess.handle_audio(_dummy_audio(), 16000, skip_action=True, skip_tts_play=True)
    # 跨 idle
    clk.advance(60.0)
    sess.handle_audio(_dummy_audio(), 16000, skip_action=True, skip_tts_play=True)

    _ok(len(rec.calls) == 3, f"应 3 次：{len(rec.calls)}")
    h3 = rec.calls[2]["history"]
    _ok(h3 == [] or h3 is None, f"跨 idle 后第 3 轮 history 应空：{h3}")

    print("V7 PASS")
    return {"name": "V7 session idle reset", "passed": True}


def v8_history_capped_at_N() -> dict:
    print("=" * 60)
    print("V8 — history 长度上限 ≤ N（N=2，灌 5 轮，第 6 轮 history ≤ 4 条）")
    print("=" * 60)
    rec = RecordingBackend(reply="好")
    mem = DialogMemory(max_turns=2, idle_timeout_s=300.0)
    seq = [f"u{i}" for i in range(1, 7)]  # 6 轮
    sess = _make_session(seq, rec, dialog_memory=mem)
    for _ in range(6):
        sess.handle_audio(_dummy_audio(), 16000, skip_action=True, skip_tts_play=True)

    _ok(len(rec.calls) == 6, f"应 6 次：{len(rec.calls)}")
    # 检查每轮 history 长度 ≤ 2*N = 4
    for i, c in enumerate(rec.calls):
        h = c["history"] or []
        _ok(len(h) <= 4, f"第 {i+1} 轮 history 长度 {len(h)} > 4")
    last_h = rec.calls[-1]["history"]
    _ok(len(last_h) == 4, f"第 6 轮应有 4 条（2 轮）：{len(last_h)}")
    # 内容应是最近 2 轮（u4,a4 / u5,a5）
    _ok(last_h[0]["content"] == "u4", f"history[0] 应 u4：{last_h[0]}")
    _ok(last_h[2]["content"] == "u5", f"history[2] 应 u5：{last_h[2]}")

    print("V8 PASS")
    return {"name": "V8 history capped", "passed": True, "last_history_len": len(last_h)}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    results = []
    failures = []
    for fn in (v1_ring_buffer, v2_idle_reset, v3_build_messages, v4_env,
               v5_llm_history_passthrough, v6_session_basic_history,
               v7_session_idle_reset, v8_history_capped_at_N):
        try:
            results.append(fn())
        except Exception as e:
            print(f"!!! {fn.__name__} FAILED: {type(e).__name__}: {e}")
            results.append({"name": fn.__name__, "passed": False, "error": f"{type(e).__name__}: {e}"})
            failures.append(fn.__name__)

    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r.get("passed")),
        "failed": len(failures),
        "failures": failures,
        "results": results,
    }
    out = EVIDENCE_DIR / "verify_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 60)
    print(f"Summary: {summary['passed']}/{summary['total']} PASS  -> {out}")
    if failures:
        print("FAIL:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
