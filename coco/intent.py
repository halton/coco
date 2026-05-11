"""coco.intent — 轻量 intent 分类 (interact-008).

设计目标
========

InteractSession 收到 ASR 文本后，先做一次轻量 intent 分类，得到一个
``IntentLabel(intent, confidence, raw_text)``。下游 ConversationStateMachine
据此选择对话策略（COMMAND="安静"→QUIET / COMMAND="重复"→重发上一句 /
TEACH→TEACHING 模式 / 其他→正常 LLM 回复）。

设计原则：
- 默认走启发式（关键词 + 句末标点 + 疑问词），无外部依赖；
- 启发式覆盖不到时，可选 LLM 兜底（``COCO_INTENT_LLM=1`` 才用，默认 OFF）；
- 任何异常一律 fail-soft → 返回 ``IntentLabel(UNKNOWN, 0.0, raw_text)``，
  绝不阻塞主对话流。

Intent 语义
----------
- QUESTION：提问（"为什么"/"什么是"/句末"?"等）
- COMMAND：指令（"安静"/"停一下"/"重复"/"再说一遍"等）
- CHITCHAT：闲聊（"你好"/"今天好天气"等）
- TEACH：教学请求（"教我"/"怎么写"/"如何"等）
- FAREWELL：告别（"再见"/"拜拜"/"晚安"）
- UNKNOWN：兜底
"""

from __future__ import annotations

import enum
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent enum + label
# ---------------------------------------------------------------------------


class Intent(str, enum.Enum):
    QUESTION = "question"
    COMMAND = "command"
    CHITCHAT = "chitchat"
    TEACH = "teach"
    FAREWELL = "farewell"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IntentLabel:
    intent: Intent
    confidence: float
    raw_text: str
    matched_terms: tuple = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------


# COMMAND 子类型：通过 matched_terms 暴露给 state machine 用
COMMAND_QUIET_TERMS = ("安静", "别说话", "不要说话", "停一下", "停下", "暂停")
COMMAND_REPEAT_TERMS = ("重复", "再说一遍", "再说一次", "刚才说什么")

COMMAND_TERMS = COMMAND_QUIET_TERMS + COMMAND_REPEAT_TERMS

FAREWELL_TERMS = ("再见", "拜拜", "晚安", "bye", "回头见", "下次见")

TEACH_TERMS = ("教我", "教一下", "怎么写", "如何", "怎么做", "怎么", "如何做")

CHITCHAT_TERMS = ("你好", "嗨", "hello", "hi", "在吗", "今天")

QUESTION_HINTS = ("为什么", "什么", "怎么样", "是不是", "对不对", "可以吗", "吗", "呢")

QUESTION_PUNCT = ("?", "？")


def _contains_any(text: str, terms) -> Optional[str]:
    for t in terms:
        if t and t in text:
            return t
    return None


@dataclass(frozen=True)
class IntentConfig:
    """Intent classifier 配置。

    - ``llm_fallback``：启发式 UNKNOWN 时是否调 LLM 兜底（默认 False）
    """

    llm_fallback: bool = False


def config_from_env(env: Optional[Mapping[str, str]] = None) -> IntentConfig:
    env = env if env is not None else os.environ
    raw = (env.get("COCO_INTENT_LLM") or "0").strip().lower()
    return IntentConfig(llm_fallback=raw in {"1", "true", "yes", "on"})


def intent_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    env = env if env is not None else os.environ
    raw = (env.get("COCO_INTENT") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


class IntentClassifier:
    """启发式 + 可选 LLM 兜底 intent 分类器。

    线程安全：纯函数，无内部可变状态（除 stats）。
    """

    def __init__(
        self,
        config: Optional[IntentConfig] = None,
        llm_fn: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.config = config or IntentConfig()
        # llm_fn(text)->str 返回 intent name（"question" / "command" / ...）。
        # None 即不启用 LLM 兜底。
        self.llm_fn = llm_fn

    def classify(self, text: str) -> IntentLabel:
        raw = text or ""
        t = raw.strip()
        if not t:
            return IntentLabel(Intent.UNKNOWN, 0.0, raw)

        # 1) COMMAND（最优先：用户的控制指令必须可靠拦截）
        cmd_term = _contains_any(t, COMMAND_TERMS)
        if cmd_term:
            return IntentLabel(Intent.COMMAND, 0.9, raw, (cmd_term,))

        # 2) FAREWELL
        fw_term = _contains_any(t, FAREWELL_TERMS)
        if fw_term:
            return IntentLabel(Intent.FAREWELL, 0.9, raw, (fw_term,))

        # 3) TEACH（教学请求）
        teach_term = _contains_any(t, TEACH_TERMS)
        if teach_term:
            # "怎么样" 实为 QUESTION 而非 TEACH，单独排除
            if teach_term == "怎么" and "怎么样" in t:
                pass
            else:
                return IntentLabel(Intent.TEACH, 0.85, raw, (teach_term,))

        # 4) QUESTION（句末问号 / 疑问词）
        if any(p in t for p in QUESTION_PUNCT):
            return IntentLabel(Intent.QUESTION, 0.9, raw, ("?",))
        q_term = _contains_any(t, QUESTION_HINTS)
        if q_term:
            return IntentLabel(Intent.QUESTION, 0.8, raw, (q_term,))

        # 5) CHITCHAT
        cc_term = _contains_any(t, CHITCHAT_TERMS)
        if cc_term:
            return IntentLabel(Intent.CHITCHAT, 0.7, raw, (cc_term,))

        # 6) LLM 兜底（可选）
        if self.config.llm_fallback and self.llm_fn is not None:
            try:
                name = (self.llm_fn(t) or "").strip().lower()
                for it in Intent:
                    if it.value == name:
                        return IntentLabel(it, 0.6, raw, ("llm",))
            except Exception as e:  # noqa: BLE001
                log.warning("intent LLM fallback failed: %s: %s", type(e).__name__, e)

        # 7) 兜底：CHITCHAT（短文本默认按闲聊处理，避免 UNKNOWN 把对话打断）
        if len(t) <= 12:
            return IntentLabel(Intent.CHITCHAT, 0.4, raw, ())
        return IntentLabel(Intent.UNKNOWN, 0.0, raw, ())

    # 子类型判别（命令的进一步区分）
    @staticmethod
    def is_quiet_command(label: IntentLabel) -> bool:
        return label.intent is Intent.COMMAND and any(
            t in COMMAND_QUIET_TERMS for t in label.matched_terms
        )

    @staticmethod
    def is_repeat_command(label: IntentLabel) -> bool:
        return label.intent is Intent.COMMAND and any(
            t in COMMAND_REPEAT_TERMS for t in label.matched_terms
        )


__all__ = [
    "Intent",
    "IntentLabel",
    "IntentConfig",
    "IntentClassifier",
    "config_from_env",
    "intent_enabled_from_env",
    "COMMAND_QUIET_TERMS",
    "COMMAND_REPEAT_TERMS",
]
