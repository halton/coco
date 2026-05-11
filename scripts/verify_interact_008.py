#!/usr/bin/env python3
"""verify_interact_008.py — 对话状态机 + intent 分类 (interact-008) 验证.

V1  默认 OFF：COCO_INTENT 未设 → intent_enabled_from_env=False；main 不构造 classifier
V2  COCO_INTENT=1 → IntentClassifier + ConversationStateMachine 构造成功
V3  classify QUESTION：含 "?" / 疑问词
V4  classify COMMAND：含 "安静"/"重复"
V5  classify CHITCHAT：含 "你好"
V6  classify TEACH：含 "教我"/"怎么写"
V7  classify FAREWELL：含 "再见"
V8  classify UNKNOWN（长文本不触发任何启发式）
V9  ConvState 转换：IDLE→LISTENING→THINKING→SPEAKING→IDLE
V10 COMMAND="安静" → QUIET 状态，N 秒内 handle_audio 直接返回
V11 QUIET 自动过期回 IDLE
V12 COMMAND="重复" → 不调 LLM，重发上次 TTS
V13 TEACHING 状态注入教学 system_prompt
V14 emit "interact.intent_classified" + "interact.state_transition"
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

from coco.intent import (  # noqa: E402
    Intent,
    IntentClassifier,
    IntentConfig,
    IntentLabel,
    config_from_env as intent_config_from_env,
    intent_enabled_from_env,
)
from coco.conversation import (  # noqa: E402
    ConvState,
    ConversationConfig,
    ConversationStateMachine,
    StateTransition,
    config_from_env as conv_config_from_env,
)
from coco.interact import InteractSession  # noqa: E402
from coco.logging_setup import setup_logging  # noqa: E402


EVIDENCE_DIR = ROOT / "evidence" / "interact-008"
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


class FakeClock:
    def __init__(self, t0: float = 1000.0) -> None:
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class CapturedLLM:
    def __init__(self, reply_text: str = "好的我懂啦") -> None:
        self.reply_text = reply_text
        self.calls: list[dict] = []

    def reply(self, text: str, *, system_prompt: Optional[str] = None,
              history: Optional[list] = None) -> str:
        self.calls.append({"text": text, "system_prompt": system_prompt, "history": history})
        return self.reply_text


# ---------------------------------------------------------------------------
# V1
# ---------------------------------------------------------------------------


def v1_default_off() -> None:
    print("\n[V1] 默认 OFF：COCO_INTENT 未设 → enabled=False", flush=True)
    saved = os.environ.pop("COCO_INTENT", None)
    try:
        assert_eq(intent_enabled_from_env(), False, "intent_enabled_from_env() 未设")
        cfg = intent_config_from_env()
        assert_eq(cfg.llm_fallback, False, "默认 llm_fallback=False")
    finally:
        if saved is not None:
            os.environ["COCO_INTENT"] = saved
    main_src = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    assert_true(
        "if _intent_enabled():" in main_src,
        "main.py 用 _intent_enabled() 守门 IntentClassifier 构造",
    )


# ---------------------------------------------------------------------------
# V2
# ---------------------------------------------------------------------------


def v2_enabled_constructs() -> None:
    print("\n[V2] COCO_INTENT=1 → 构造成功", flush=True)
    os.environ["COCO_INTENT"] = "1"
    try:
        assert_eq(intent_enabled_from_env(), True, "enabled=True")
        clf = IntentClassifier()
        assert_true(clf is not None, "IntentClassifier 构造成功")
        sm = ConversationStateMachine()
        assert_eq(sm.current_state, ConvState.IDLE, "初始状态 IDLE")
    finally:
        os.environ.pop("COCO_INTENT", None)


# ---------------------------------------------------------------------------
# V3-V8 — classify
# ---------------------------------------------------------------------------


def v3_classify_question() -> None:
    print("\n[V3] classify QUESTION", flush=True)
    clf = IntentClassifier()
    assert_eq(clf.classify("你叫什么名字？").intent, Intent.QUESTION, "句末问号")
    assert_eq(clf.classify("为什么天是蓝的").intent, Intent.QUESTION, "为什么")
    assert_eq(clf.classify("可以告诉我吗").intent, Intent.QUESTION, "句末'吗'")


def v4_classify_command() -> None:
    print("\n[V4] classify COMMAND（quiet/repeat 子类型）", flush=True)
    clf = IntentClassifier()
    q = clf.classify("安静一会")
    assert_eq(q.intent, Intent.COMMAND, "安静 → COMMAND")
    assert_true(IntentClassifier.is_quiet_command(q), "安静 → is_quiet_command")
    r = clf.classify("再说一遍")
    assert_eq(r.intent, Intent.COMMAND, "再说一遍 → COMMAND")
    assert_true(IntentClassifier.is_repeat_command(r), "再说一遍 → is_repeat_command")
    p = clf.classify("停一下")
    assert_true(IntentClassifier.is_quiet_command(p), "停一下 → quiet")


def v5_classify_chitchat() -> None:
    print("\n[V5] classify CHITCHAT", flush=True)
    clf = IntentClassifier()
    assert_eq(clf.classify("你好").intent, Intent.CHITCHAT, "你好")
    assert_eq(clf.classify("hi").intent, Intent.CHITCHAT, "hi")


def v6_classify_teach() -> None:
    print("\n[V6] classify TEACH", flush=True)
    clf = IntentClassifier()
    assert_eq(clf.classify("教我做菜").intent, Intent.TEACH, "教我做菜")
    assert_eq(clf.classify("怎么写代码").intent, Intent.TEACH, "怎么写代码")


def v7_classify_farewell() -> None:
    print("\n[V7] classify FAREWELL", flush=True)
    clf = IntentClassifier()
    assert_eq(clf.classify("再见").intent, Intent.FAREWELL, "再见")
    assert_eq(clf.classify("拜拜啦").intent, Intent.FAREWELL, "拜拜啦")
    assert_eq(clf.classify("晚安").intent, Intent.FAREWELL, "晚安")


def v8_classify_unknown_or_low() -> None:
    print("\n[V8] classify UNKNOWN（长文本不触发任何启发式）", flush=True)
    clf = IntentClassifier()
    long_text = "这是一段很长的话没有任何关键词也没有问号陈述事实而已就这样我去散步"
    label = clf.classify(long_text)
    assert_eq(label.intent, Intent.UNKNOWN, "长无关键词文本 → UNKNOWN")
    # 空文本
    assert_eq(clf.classify("").intent, Intent.UNKNOWN, "空文本 → UNKNOWN")


# ---------------------------------------------------------------------------
# V9 — state machine 正常路径
# ---------------------------------------------------------------------------


def v9_state_transitions() -> None:
    print("\n[V9] ConvState IDLE→LISTENING→THINKING→SPEAKING→IDLE", flush=True)
    clock = FakeClock()
    sm = ConversationStateMachine(clock=clock)
    assert_eq(sm.current_state, ConvState.IDLE, "init IDLE")
    sm.on_user_utterance("question")
    assert_eq(sm.current_state, ConvState.LISTENING, "→ LISTENING")
    sm.on_llm_start()
    assert_eq(sm.current_state, ConvState.THINKING, "→ THINKING")
    sm.on_llm_done()
    assert_eq(sm.current_state, ConvState.SPEAKING, "→ SPEAKING")
    sm.on_tts_done()
    assert_eq(sm.current_state, ConvState.IDLE, "→ IDLE")


# ---------------------------------------------------------------------------
# V10 — COMMAND="安静" → QUIET，handle_audio 直接返回
# ---------------------------------------------------------------------------


def _build_session(clock: FakeClock, llm: CapturedLLM, tts_calls: list,
                   sm: ConversationStateMachine,
                   clf: IntentClassifier) -> InteractSession:
    def _asr(audio, sr):
        # asr_fn 由 verify 用 monkey-patch 形式覆盖
        return ""

    def _tts(text, blocking=True):
        tts_calls.append(text)

    robot = MagicMock()
    return InteractSession(
        robot=robot,
        asr_fn=_asr,
        tts_say_fn=_tts,
        llm_reply_fn=llm.reply,
        intent_classifier=clf,
        conv_state_machine=sm,
    )


def v10_quiet_command_blocks() -> None:
    print("\n[V10] COMMAND='安静' → QUIET，后续 handle_audio drop", flush=True)
    clock = FakeClock()
    sm = ConversationStateMachine(clock=clock, config=ConversationConfig(quiet_seconds=30.0))
    clf = IntentClassifier()
    llm = CapturedLLM()
    tts_calls: list = []
    session = _build_session(clock, llm, tts_calls, sm, clf)
    # 第 1 次：用户说 "安静一下"
    session.asr_fn = lambda a, sr: "安静一下"  # type: ignore[assignment]
    audio = np.zeros(16000, dtype=np.int16)
    r1 = session.handle_audio(audio, 16000, skip_action=True, skip_tts_play=True)
    assert_eq(r1.get("intent_action"), "quiet", "intent_action=quiet")
    assert_eq(sm.current_state, ConvState.QUIET, "进入 QUIET")
    assert_eq(len(llm.calls), 0, "LLM 未被调")
    # 第 2 次：QUIET 内任何输入都 drop
    session.asr_fn = lambda a, sr: "你好啊"  # type: ignore[assignment]
    r2 = session.handle_audio(audio, 16000, skip_action=True, skip_tts_play=True)
    assert_eq(r2.get("dropped"), True, "QUIET 内 dropped=True")
    assert_eq(r2.get("quiet"), True, "quiet=True")
    assert_eq(len(llm.calls), 0, "LLM 仍未被调")


# ---------------------------------------------------------------------------
# V11 — QUIET 自动过期
# ---------------------------------------------------------------------------


def v11_quiet_auto_expire() -> None:
    print("\n[V11] QUIET 超时自动回 IDLE", flush=True)
    clock = FakeClock()
    sm = ConversationStateMachine(clock=clock, config=ConversationConfig(quiet_seconds=10.0))
    sm.enter_quiet()
    assert_eq(sm.current_state, ConvState.QUIET, "进入 QUIET")
    clock.advance(5.0)
    assert_eq(sm.current_state, ConvState.QUIET, "5s 内仍 QUIET")
    clock.advance(10.0)
    # 触发 getter 过期
    assert_eq(sm.current_state, ConvState.IDLE, ">10s 自动回 IDLE")


# ---------------------------------------------------------------------------
# V12 — COMMAND="重复" → 不调 LLM，重发上一句
# ---------------------------------------------------------------------------


def v12_repeat_command() -> None:
    print("\n[V12] COMMAND='重复' → 不调 LLM，重发上一句", flush=True)
    clock = FakeClock()
    sm = ConversationStateMachine(clock=clock)
    clf = IntentClassifier()
    llm = CapturedLLM(reply_text="今天阳光不错")
    tts_calls: list = []
    session = _build_session(clock, llm, tts_calls, sm, clf)

    audio = np.zeros(16000, dtype=np.int16)
    # 第 1 次：正常对话产生 last_reply
    session.asr_fn = lambda a, sr: "今天天气怎么样"  # type: ignore[assignment]
    r1 = session.handle_audio(audio, 16000, skip_action=True, skip_tts_play=False)
    assert_eq(r1["reply"], "今天阳光不错", "首句 reply")
    assert_eq(len(llm.calls), 1, "LLM 调 1 次")
    assert_eq(len(tts_calls), 1, "TTS 调 1 次")

    # 第 2 次："再说一遍" → 不调 LLM，重发上一句
    session.asr_fn = lambda a, sr: "再说一遍"  # type: ignore[assignment]
    r2 = session.handle_audio(audio, 16000, skip_action=True, skip_tts_play=False)
    assert_eq(r2.get("intent_action"), "repeat", "intent_action=repeat")
    assert_eq(r2["reply"], "今天阳光不错", "重发上一句")
    assert_eq(len(llm.calls), 1, "LLM 仍 1 次（未调）")
    assert_eq(len(tts_calls), 2, "TTS 调 2 次")


# ---------------------------------------------------------------------------
# V13 — TEACHING 状态注入教学 system_prompt
# ---------------------------------------------------------------------------


def v13_teaching_system_prompt() -> None:
    print("\n[V13] TEACHING → 注入教学 system_prompt", flush=True)
    clock = FakeClock()
    sm = ConversationStateMachine(clock=clock)
    clf = IntentClassifier()
    llm = CapturedLLM(reply_text="我们一步步来")
    tts_calls: list = []
    session = _build_session(clock, llm, tts_calls, sm, clf)

    audio = np.zeros(16000, dtype=np.int16)
    session.asr_fn = lambda a, sr: "教我做加法"  # type: ignore[assignment]
    r = session.handle_audio(audio, 16000, skip_action=True, skip_tts_play=True)
    assert_eq(r.get("intent"), "teach", "intent=teach")
    assert_eq(len(llm.calls), 1, "LLM 调 1 次")
    sp = llm.calls[0].get("system_prompt") or ""
    assert_true("教学" in sp or "耐心" in sp, "system_prompt 含教学提示")
    assert_true(sm.is_teaching(), "is_teaching=True")
    # SPEAKING done 后回 TEACHING（而不是 IDLE）
    assert_eq(sm.current_state, ConvState.TEACHING, "TTS done 后回 TEACHING")


# ---------------------------------------------------------------------------
# V14 — emit events
# ---------------------------------------------------------------------------


def v14_emit_events() -> None:
    print("\n[V14] emit interact.intent_classified + interact.state_transition", flush=True)
    # 把 logging_setup.emit 挂个 spy
    from coco import logging_setup as _ls

    captured: list[dict] = []
    orig_emit = _ls.emit

    def _spy(component_event: str, message: str = "", **payload):
        captured.append({"event": component_event, **payload})
        return orig_emit(component_event, message, **payload)

    _ls.emit = _spy  # type: ignore[assignment]
    # 同时 patch interact 模块内已经 from ... import 的引用（如有）
    try:
        clock = FakeClock()
        sm = ConversationStateMachine(clock=clock)
        clf = IntentClassifier()
        llm = CapturedLLM(reply_text="ok")
        tts_calls: list = []
        session = _build_session(clock, llm, tts_calls, sm, clf)
        audio = np.zeros(16000, dtype=np.int16)
        session.asr_fn = lambda a, sr: "你好"  # type: ignore[assignment]
        session.handle_audio(audio, 16000, skip_action=True, skip_tts_play=True)
        events = [c["event"] for c in captured]
        assert_true(
            "interact.intent_classified" in events,
            "emit interact.intent_classified",
        )
        assert_true(
            "interact.state_transition" in events,
            "emit interact.state_transition",
        )
        # 至少一次 transition payload 含 from_state/to_state/source
        st_events = [c for c in captured if c["event"] == "interact.state_transition"]
        assert_true(len(st_events) > 0, "至少一次 state_transition emit")
        first = st_events[0]
        assert_true(
            "from_state" in first and "to_state" in first and "source" in first,
            "state_transition payload 完整",
        )
    finally:
        _ls.emit = orig_emit  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=== verify_interact_008 ===", flush=True)
    setup_logging(jsonl=False, level="WARNING")
    v1_default_off()
    v2_enabled_constructs()
    v3_classify_question()
    v4_classify_command()
    v5_classify_chitchat()
    v6_classify_teach()
    v7_classify_farewell()
    v8_classify_unknown_or_low()
    v9_state_transitions()
    v10_quiet_command_blocks()
    v11_quiet_auto_expire()
    v12_repeat_command()
    v13_teaching_system_prompt()
    v14_emit_events()

    print(f"\n--- 总结 ---", flush=True)
    print(f"PASS={len(PASSES)}  FAIL={len(FAILURES)}", flush=True)

    summary = {
        "verification": "verify_interact_008",
        "pass_count": len(PASSES),
        "fail_count": len(FAILURES),
        "passes": PASSES,
        "failures": FAILURES,
    }
    (EVIDENCE_DIR / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if FAILURES:
        print("==> FAIL: interact-008 有 failure", flush=True)
        for f in FAILURES:
            print(f"  - {f}", flush=True)
        return 1
    print("==> PASS: interact-008 verification 全部通过", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
