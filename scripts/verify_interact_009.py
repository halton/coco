#!/usr/bin/env python3
"""verify_interact_009.py — 对话历史压缩 (interact-009) 验证.

V1  默认 OFF：COCO_DIALOG_SUMMARY 未设 → enabled_from_env=False；
    DialogMemory 不引用 summarizer 时不会自动压缩。
V2  COCO_DIALOG_SUMMARY=1 → DialogSummaryConfig.enabled=True；build_summarizer 构造成功
V3  LLMSummarizer 调用 llm_reply_fn 拿摘要并截断
V4  HeuristicSummarizer fallback（无 LLM 时拼接 + 截断）
V5  DialogMemory.compress_if_needed 在 turns < threshold 时 no-op
V6  turns >= threshold 时压缩：len(memory) == 1 + keep_recent，带 summary 标记
V7  压缩后 build_messages 含 system 摘要 + 最近 keep_recent 轮
V8  LLMSummarizer 失败 fail-soft：保持原 history + emit "interact.dialog_summary_failed"
V9  InteractSession 集成：handle_audio 自动触发 compress_if_needed
V10 emit "interact.dialog_summarized"
V11 env clamp（threshold ∈ [4,100] / keep ∈ [1,20] / max_chars ∈ [50,1000]）
V12 摘要 system turn 在下一次 build_messages 中被注入到 messages
V13 累积压缩：第二次压缩把旧 summary 当 pseudo-turn 注入（L1-1）
V14 Hot path guard：刚压缩完不会立即再压缩（L1-3）
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import MagicMock

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.dialog import DialogMemory  # noqa: E402
from coco.dialog_summary import (  # noqa: E402
    DialogSummaryConfig,
    HeuristicSummarizer,
    LLMSummarizer,
    build_summarizer,
    config_from_env as ds_config_from_env,
    dialog_summary_enabled_from_env,
)
from coco.interact import InteractSession  # noqa: E402
from coco.logging_setup import setup_logging  # noqa: E402


EVIDENCE_DIR = ROOT / "evidence" / "interact-009"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

PASSES: List[str] = []
FAILURES: List[str] = []


def ok(msg: str) -> None:
    print(f"  PASS {msg}", flush=True)
    PASSES.append(msg)


def fail(msg: str) -> None:
    print(f"  FAIL {msg}", flush=True)
    FAILURES.append(msg)


def assert_true(cond: bool, label: str) -> bool:
    if cond:
        ok(label)
        return True
    fail(label)
    return False


def assert_eq(actual: Any, expected: Any, label: str) -> bool:
    if actual == expected:
        ok(f"{label}: {actual!r} == {expected!r}")
        return True
    fail(f"{label}: actual={actual!r} expected={expected!r}")
    return False


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class CapturedEmit:
    def __init__(self) -> None:
        self.events: List[dict] = []

    def __call__(self, event: str, message: str = "", **payload: Any) -> None:
        self.events.append({"event": event, "message": message, **payload})


class FakeLLM:
    def __init__(self, reply_text: str = "前面用户问候并询问了天气") -> None:
        self.reply_text = reply_text
        self.calls: list[dict] = []

    def reply(self, text: str, *, system_prompt: Optional[str] = None,
              history: Optional[list] = None) -> str:
        self.calls.append({"text": text, "system_prompt": system_prompt, "history": history})
        return self.reply_text


class FailingLLM:
    def reply(self, text: str, *, system_prompt: Optional[str] = None,
              history: Optional[list] = None) -> str:
        raise RuntimeError("LLM 网络故障")


# ---------------------------------------------------------------------------
# V1
# ---------------------------------------------------------------------------


def v1_default_off() -> None:
    print("\n[V1] 默认 OFF", flush=True)
    saved = os.environ.pop("COCO_DIALOG_SUMMARY", None)
    try:
        assert_eq(dialog_summary_enabled_from_env(), False,
                  "dialog_summary_enabled_from_env() 未设")
        cfg = ds_config_from_env()
        assert_eq(cfg.enabled, False, "默认 enabled=False")
    finally:
        if saved is not None:
            os.environ["COCO_DIALOG_SUMMARY"] = saved
    # DialogMemory 无 summarizer 时不会自动压缩
    mem = DialogMemory(max_turns=20, idle_timeout_s=300.0)
    for i in range(15):
        mem.append(f"u{i}", f"a{i}")
    assert_eq(mem.summary, None, "无 summarizer 注入 → mem.summary=None")
    assert_eq(len(mem), 15, "未压缩 len=15")


# ---------------------------------------------------------------------------
# V2
# ---------------------------------------------------------------------------


def v2_enabled_constructs() -> None:
    print("\n[V2] COCO_DIALOG_SUMMARY=1 → 构造成功", flush=True)
    os.environ["COCO_DIALOG_SUMMARY"] = "1"
    try:
        assert_eq(dialog_summary_enabled_from_env(), True, "enabled=True")
        cfg = ds_config_from_env()
        assert_true(cfg.enabled, "DialogSummaryConfig.enabled=True")
        s = build_summarizer(cfg, llm_reply_fn=FakeLLM().reply)
        assert_true(s is not None, "build_summarizer 返回非 None")
        assert_true(isinstance(s, LLMSummarizer), "kind=llm 默认 → LLMSummarizer")
    finally:
        del os.environ["COCO_DIALOG_SUMMARY"]


# ---------------------------------------------------------------------------
# V3
# ---------------------------------------------------------------------------


def v3_llm_summarizer() -> None:
    print("\n[V3] LLMSummarizer 调用 llm_reply_fn", flush=True)
    fake = FakeLLM(reply_text="用户和助手聊了天气和公园")
    s = LLMSummarizer(fake.reply, max_chars=200)
    out = s.summarize([("你好", "嗨"), ("天气怎么样", "外面挺好"), ("公园好玩吗", "好玩")])
    assert_eq(out, "用户和助手聊了天气和公园", "返回 LLM 摘要文本")
    assert_eq(len(fake.calls), 1, "调用了 llm_reply_fn 1 次")
    assert_true("用户：你好" in fake.calls[0]["text"], "传入文本含 user/assistant 格式")
    assert_true("摘要" in (fake.calls[0]["system_prompt"] or ""), "system_prompt 含'摘要'指令")
    # 截断
    long_fake = FakeLLM(reply_text="x" * 500)
    s2 = LLMSummarizer(long_fake.reply, max_chars=50)
    out2 = s2.summarize([("a", "b")])
    assert_true(len(out2) <= 50, f"截断到 max_chars=50（实际 {len(out2)}）")
    assert_true(out2.endswith("…"), "截断后以 … 结尾")


# ---------------------------------------------------------------------------
# V4
# ---------------------------------------------------------------------------


def v4_heuristic_summarizer() -> None:
    print("\n[V4] HeuristicSummarizer fallback (含 assistant)", flush=True)
    s = HeuristicSummarizer(max_chars=200)
    out = s.summarize([("你好", "嗨你好"), ("天气怎么样", "外面挺好")])
    assert_true("前面聊到" in out, "含'前面聊到'前缀")
    assert_true("你好" in out and "天气怎么样" in out, "拼接 user 文本")
    # interact-009 L1-4: 含 assistant 文本片段
    assert_true("嗨你好" in out, f"摘要含 assistant 文本'嗨你好': {out!r}")
    assert_true("外面挺好" in out, f"摘要含 assistant 文本'外面挺好': {out!r}")
    # build_summarizer 在 kind=llm 但无 llm_reply_fn 时降级
    cfg = DialogSummaryConfig(enabled=True, summarizer_kind="llm")
    s2 = build_summarizer(cfg, llm_reply_fn=None)
    assert_true(isinstance(s2, HeuristicSummarizer),
                "kind=llm 无 llm_reply_fn → 降级 HeuristicSummarizer")


# ---------------------------------------------------------------------------
# V5
# ---------------------------------------------------------------------------


def v5_no_op_below_threshold() -> None:
    print("\n[V5] turns < threshold → no-op", flush=True)
    mem = DialogMemory(max_turns=20, idle_timeout_s=300.0)
    s = HeuristicSummarizer()
    for i in range(5):
        mem.append(f"u{i}", f"a{i}")
    triggered = mem.compress_if_needed(threshold_turns=10, keep_recent=4, summarizer=s)
    assert_eq(triggered, False, "未触发返回 False")
    assert_eq(mem.summary, None, "summary 未设")
    assert_eq(len(mem), 5, "未变 len=5")


# ---------------------------------------------------------------------------
# V6
# ---------------------------------------------------------------------------


def v6_compress_at_threshold() -> None:
    print("\n[V6] turns >= threshold → 压缩", flush=True)
    mem = DialogMemory(max_turns=20, idle_timeout_s=300.0)
    s = HeuristicSummarizer(max_chars=200)
    for i in range(10):
        mem.append(f"用户消息{i}", f"回复{i}")
    triggered = mem.compress_if_needed(threshold_turns=10, keep_recent=4, summarizer=s)
    assert_eq(triggered, True, "触发返回 True")
    assert_eq(len(mem), 1 + 4, "len = 1(summary) + 4(keep_recent)")
    assert_true(mem.summary is not None and "前面聊到" in mem.summary,
                f"summary 含'前面聊到': {mem.summary!r}")
    # 保留的最后 4 轮应是 6,7,8,9
    tail = mem.recent_turns()
    assert_eq(len(tail), 4, "recent_turns 长度 4")
    assert_eq(tail[0][0], "用户消息6", "首个保留轮 = 用户消息6")
    assert_eq(tail[-1][0], "用户消息9", "末尾保留轮 = 用户消息9")


# ---------------------------------------------------------------------------
# V7
# ---------------------------------------------------------------------------


def v7_build_messages_with_summary() -> None:
    print("\n[V7] 压缩后 build_messages 含 system 摘要 + 最近 turns", flush=True)
    mem = DialogMemory(max_turns=20, idle_timeout_s=300.0)
    s = HeuristicSummarizer()
    for i in range(10):
        mem.append(f"u{i}", f"a{i}")
    mem.compress_if_needed(threshold_turns=10, keep_recent=4, summarizer=s)
    msgs = mem.build_messages(system_prompt="你是 Coco", user_text="现在呢")
    # 期望：system(coco) + system(摘要) + 4*(user+assistant) + user(现在呢)
    assert_eq(msgs[0]["role"], "system", "[0] role=system")
    assert_eq(msgs[0]["content"], "你是 Coco", "[0] content=系统 prompt")
    assert_eq(msgs[1]["role"], "system", "[1] role=system (摘要)")
    assert_true("对话摘要" in msgs[1]["content"], f"[1] 含'对话摘要': {msgs[1]['content']!r}")
    # 最近 4 轮 = 8 条 user/assistant
    body = msgs[2:-1]
    assert_eq(len(body), 8, "body 长度 = 4 turns * 2 = 8")
    assert_eq(body[0]["role"], "user", "body[0]=user")
    assert_eq(body[0]["content"], "u6", "body[0]=u6（最早保留）")
    assert_eq(msgs[-1]["role"], "user", "末尾=当前 user_text")
    assert_eq(msgs[-1]["content"], "现在呢", "末尾 content=现在呢")


# ---------------------------------------------------------------------------
# V8
# ---------------------------------------------------------------------------


def v8_fail_soft() -> None:
    print("\n[V8] LLMSummarizer 失败 → fail-soft + emit failed", flush=True)
    mem = DialogMemory(max_turns=20, idle_timeout_s=300.0)
    for i in range(10):
        mem.append(f"u{i}", f"a{i}")
    s = LLMSummarizer(FailingLLM().reply)
    emit = CapturedEmit()
    triggered = mem.compress_if_needed(
        threshold_turns=10, keep_recent=4, summarizer=s, emit_fn=emit
    )
    assert_eq(triggered, False, "失败返回 False")
    assert_eq(mem.summary, None, "summary 仍为 None")
    assert_eq(len(mem), 10, "history 长度未变 = 10")
    failed = [e for e in emit.events if e["event"] == "interact.dialog_summary_failed"]
    assert_eq(len(failed), 1, "emit 1 条 dialog_summary_failed")
    assert_eq(failed[0]["error_type"], "RuntimeError", "error_type=RuntimeError")


# ---------------------------------------------------------------------------
# V9
# ---------------------------------------------------------------------------


def v9_session_integration() -> None:
    print("\n[V9] InteractSession 集成 handle_audio 自动触发 + summary 注入 LLM", flush=True)
    mem = DialogMemory(max_turns=20, idle_timeout_s=300.0)
    s = HeuristicSummarizer()

    # 预填 9 轮，使第 10 轮 append 后触发
    for i in range(9):
        mem.append(f"用户消息{i}", f"机器人回复{i}")

    # mocks for InteractSession — llm_fn 必须接受 history kwarg 才会被探测命中
    robot = MagicMock()
    asr_fn = MagicMock(return_value="测试句子")
    tts_fn = MagicMock()

    llm_calls: list[dict] = []

    def llm_fn(text: str, *, history=None, system_prompt=None) -> str:
        llm_calls.append({"text": text, "history": history, "system_prompt": system_prompt})
        return "LLM 回应"

    sess = InteractSession(
        robot=robot,
        asr_fn=asr_fn,
        tts_say_fn=tts_fn,
        llm_reply_fn=llm_fn,
        dialog_memory=mem,
        dialog_summarizer=s,
        dialog_summary_threshold=10,
        dialog_summary_keep_recent=4,
    )
    audio = np.zeros(16000, dtype=np.int16)
    sess.handle_audio(audio, sample_rate=16000, skip_action=True, skip_tts_play=True)
    # 第 10 轮 append 后压缩 → len = 1 + 4
    assert_eq(len(mem), 5, "session 自动压缩后 len=5")
    assert_true(mem.summary is not None, "summary 已设")

    # interact-009 L0 修复关键断言：summary 真的进了下一轮 LLM history
    # handle_audio 流程是：先调 LLM（这时 mem 还是 9 轮，summary 还没产生）
    # → 再 append 第 10 轮 → 再 compress。所以第一次 LLM 调用 summary=None。
    # 触发第二轮 audio 才能验证 summary 注入。
    sess.handle_audio(audio, sample_rate=16000, skip_action=True, skip_tts_play=True)
    assert_true(len(llm_calls) >= 2, f"LLM 至少被调 2 次（实际 {len(llm_calls)}）")
    second_call = llm_calls[1]
    history = second_call.get("history") or []
    # 找到 system role 且 content 含'对话摘要'的条目
    summary_msgs = [
        m for m in history
        if isinstance(m, dict)
        and m.get("role") == "system"
        and "对话摘要" in (m.get("content") or "")
    ]
    assert_true(
        len(summary_msgs) >= 1,
        f"第二轮 LLM history 含'对话摘要' system message（实际 history 长度 {len(history)}）",
    )
    if summary_msgs:
        assert_true(
            mem.summary in summary_msgs[0]["content"],
            "summary 文本完整出现在 history system message 中",
        )


# ---------------------------------------------------------------------------
# V10
# ---------------------------------------------------------------------------


def v10_emit_summarized() -> None:
    print("\n[V10] emit interact.dialog_summarized", flush=True)
    mem = DialogMemory(max_turns=20, idle_timeout_s=300.0)
    s = HeuristicSummarizer()
    for i in range(10):
        mem.append(f"u{i}", f"a{i}")
    emit = CapturedEmit()
    mem.compress_if_needed(
        threshold_turns=10, keep_recent=4, summarizer=s, emit_fn=emit,
    )
    summarized = [e for e in emit.events if e["event"] == "interact.dialog_summarized"]
    assert_eq(len(summarized), 1, "emit 1 条 dialog_summarized")
    ev = summarized[0]
    assert_eq(ev["summarized_turns"], 6, "summarized_turns=6")
    assert_eq(ev["kept_turns"], 4, "kept_turns=4")
    assert_true(ev["summary_chars"] > 0, "summary_chars > 0")


# ---------------------------------------------------------------------------
# V11
# ---------------------------------------------------------------------------


def v11_env_clamp() -> None:
    print("\n[V11] env clamp", flush=True)
    saved = {k: os.environ.get(k) for k in [
        "COCO_DIALOG_SUMMARY", "COCO_DIALOG_SUMMARY_THRESHOLD",
        "COCO_DIALOG_SUMMARY_KEEP", "COCO_DIALOG_SUMMARY_MAX_CHARS",
    ]}
    try:
        # 越下界
        os.environ["COCO_DIALOG_SUMMARY"] = "1"
        os.environ["COCO_DIALOG_SUMMARY_THRESHOLD"] = "2"
        os.environ["COCO_DIALOG_SUMMARY_KEEP"] = "0"
        os.environ["COCO_DIALOG_SUMMARY_MAX_CHARS"] = "10"
        cfg = ds_config_from_env()
        assert_eq(cfg.threshold_turns, 4, "threshold clamp 下界 4")
        assert_eq(cfg.keep_recent, 1, "keep clamp 下界 1")
        assert_eq(cfg.summary_max_chars, 50, "max_chars clamp 下界 50")
        # 越上界
        os.environ["COCO_DIALOG_SUMMARY_THRESHOLD"] = "9999"
        os.environ["COCO_DIALOG_SUMMARY_KEEP"] = "9999"
        os.environ["COCO_DIALOG_SUMMARY_MAX_CHARS"] = "99999"
        cfg2 = ds_config_from_env()
        assert_eq(cfg2.threshold_turns, 100, "threshold clamp 上界 100")
        assert_eq(cfg2.keep_recent, 20, "keep clamp 上界 20")
        assert_eq(cfg2.summary_max_chars, 1000, "max_chars clamp 上界 1000")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# V12
# ---------------------------------------------------------------------------


def v12_summary_in_messages() -> None:
    print("\n[V12] 摘要 system turn 在下一次 build_messages 中被注入", flush=True)
    mem = DialogMemory(max_turns=20, idle_timeout_s=300.0)
    s = HeuristicSummarizer()
    for i in range(12):
        mem.append(f"u{i}", f"a{i}")
    mem.compress_if_needed(threshold_turns=10, keep_recent=4, summarizer=s)
    sys_p = "你是 Coco，一只可爱的学习伴侣"
    msgs = mem.build_messages(system_prompt=sys_p, user_text="今天学什么")
    # 第一条 = 业务 system prompt（与 build_system_prompt 不冲突）
    assert_eq(msgs[0]["content"], sys_p, "[0] = 业务 system_prompt 原样")
    # 第二条 = 摘要 system
    assert_eq(msgs[1]["role"], "system", "[1] role=system")
    assert_true(msgs[1]["content"].startswith("对话摘要："), "[1] 以'对话摘要：'起始")
    # 末尾 = 当前用户输入
    assert_eq(msgs[-1]["content"], "今天学什么", "末尾用户文本")


# ---------------------------------------------------------------------------
# V13 — 累积压缩（L1-1）
# ---------------------------------------------------------------------------


def v13_cumulative_compress() -> None:
    print("\n[V13] 第二次压缩把旧 summary 累积进来，最早信息不丢", flush=True)
    mem = DialogMemory(max_turns=30, idle_timeout_s=300.0)

    # 用一个 mock summarizer，捕获 summarize() 收到的 turns，验证旧 summary 被注入
    captured_turns_lists: list[list] = []

    class CapturingSummarizer:
        def __init__(self) -> None:
            self.counter = 0

        def summarize(self, turns):
            captured_turns_lists.append(list(turns))
            self.counter += 1
            return f"摘要-第{self.counter}次-含{len(turns)}轮"

    s = CapturingSummarizer()

    # 第一次压缩：填 10 轮（含独特关键词"早期话题XYZ"），threshold=10 keep=4
    mem.append("早期话题XYZ", "早期回答")
    for i in range(1, 10):
        mem.append(f"u{i}", f"a{i}")
    triggered1 = mem.compress_if_needed(threshold_turns=10, keep_recent=4, summarizer=s)
    assert_eq(triggered1, True, "第一次压缩触发")
    first_summary = mem.summary
    assert_true(first_summary is not None, "第一次摘要已设")
    assert_true("早期话题XYZ" not in (first_summary or ""),
                "[健全] 第一次 summary 是 mock 文本不含原文")

    # 现在 buf 长度 = 4。再加 6 轮使总长 = 10 触发第二次压缩。
    for i in range(10, 16):
        mem.append(f"u{i}", f"a{i}")
    triggered2 = mem.compress_if_needed(threshold_turns=10, keep_recent=4, summarizer=s)
    assert_eq(triggered2, True, "第二次压缩触发")

    # 关键断言：第二次 summarize 收到的 turns 头部应含旧 summary 的 pseudo-turn
    assert_true(len(captured_turns_lists) == 2, f"summarize 被调 2 次（实际 {len(captured_turns_lists)}）")
    second_call_turns = captured_turns_lists[1]
    head_user_text = second_call_turns[0][0] if second_call_turns else ""
    assert_true(
        "[此前摘要]" in head_user_text and first_summary in head_user_text,
        f"第二次 summarize 头部含旧 summary（实际头部：{head_user_text!r}）",
    )


# ---------------------------------------------------------------------------
# V14 — Hot path guard（L1-3）
# ---------------------------------------------------------------------------


def v14_hot_path_guard() -> None:
    print("\n[V14] 压缩冷却：刚压缩完不会立即再压缩", flush=True)
    mem = DialogMemory(max_turns=20, idle_timeout_s=300.0)

    call_count = [0]

    class CountingSummarizer:
        def summarize(self, turns):
            call_count[0] += 1
            return f"summary-{call_count[0]}"

    s = CountingSummarizer()

    # 触发第一次压缩：10 轮 → 压缩 → buf 剩 4
    for i in range(10):
        mem.append(f"u{i}", f"a{i}")
    triggered1 = mem.compress_if_needed(threshold_turns=10, keep_recent=4, summarizer=s)
    assert_eq(triggered1, True, "第一次压缩触发")
    assert_eq(call_count[0], 1, "summarizer 调用 1 次")

    # 立即追加 1 轮 → buf=5。threshold=5 keep=4 时旧实现会再次触发
    # 但 hot-path guard：自上次压缩后新增 turns 必须 >= keep_recent(=4) 才再压
    # 当前 buf=5，上次压缩后剩 4，新增 1 < 4 → 不触发
    mem.append("u_extra1", "a_extra1")
    triggered_again = mem.compress_if_needed(threshold_turns=5, keep_recent=4, summarizer=s)
    assert_eq(triggered_again, False, "新增 1 轮（< keep_recent）不再触发")
    assert_eq(call_count[0], 1, "summarizer 仍只调 1 次")

    # 再追加 3 轮使新增达到 keep_recent=4 → 应再次触发
    for i in range(3):
        mem.append(f"ux{i}", f"ax{i}")
    triggered3 = mem.compress_if_needed(threshold_turns=5, keep_recent=4, summarizer=s)
    assert_eq(triggered3, True, "新增达 keep_recent → 再次触发")
    assert_eq(call_count[0], 2, "summarizer 调用 2 次")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    setup_logging(jsonl=False, level="INFO")
    t0 = time.time()
    v1_default_off()
    v2_enabled_constructs()
    v3_llm_summarizer()
    v4_heuristic_summarizer()
    v5_no_op_below_threshold()
    v6_compress_at_threshold()
    v7_build_messages_with_summary()
    v8_fail_soft()
    v9_session_integration()
    v10_emit_summarized()
    v11_env_clamp()
    v12_summary_in_messages()
    v13_cumulative_compress()
    v14_hot_path_guard()
    dt = time.time() - t0

    summary = {
        "feature": "interact-009",
        "passes": len(PASSES),
        "failures": len(FAILURES),
        "failure_messages": FAILURES,
        "duration_s": round(dt, 2),
    }
    (EVIDENCE_DIR / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== interact-009 verify: {len(PASSES)} PASS / {len(FAILURES)} FAIL "
          f"in {dt:.2f}s ===", flush=True)
    if FAILURES:
        for m in FAILURES:
            print(f"  FAIL {m}", flush=True)
        return 1
    print("ALL PASS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
