"""coco.dialog_summary — 对话历史压缩 (interact-009).

设计原则：
- 默认 OFF（COCO_DIALOG_SUMMARY=0）。未启用时 DialogMemory 行为与 interact-004 一致。
- 当 history >= threshold_turns 时，把最早的 turns 摘要成 1 条 system turn（"对话摘要：..."），
  保留最近 keep_recent 轮原文。压缩后 deque 长度 = 1（summary）+ keep_recent。
- 摘要器协议化：
  - LLMSummarizer 用 llm_reply_fn 跑摘要 prompt（短回复，max_chars 约束）
  - HeuristicSummarizer fallback：拼接 + 截断
- fail-soft：summarizer 抛错 → 保持原 history 不动 + emit "interact.dialog_summary_failed"。

公开 API：
- DialogSummarizer Protocol
- LLMSummarizer(llm_reply_fn, max_chars=200)
- HeuristicSummarizer(max_chars=200)
- DialogSummaryConfig dataclass + config_from_env(env)
- dialog_summary_enabled_from_env()
"""

from __future__ import annotations

import logging
import inspect
import os
from dataclasses import dataclass
from typing import Callable, List, Mapping, Optional, Protocol, Tuple


log = logging.getLogger(__name__)


DEFAULT_THRESHOLD_TURNS = 10
DEFAULT_KEEP_RECENT = 4
DEFAULT_SUMMARY_MAX_CHARS = 200
DEFAULT_SUMMARIZER_KIND = "llm"

_THRESHOLD_LO, _THRESHOLD_HI = 4, 100
_KEEP_LO, _KEEP_HI = 1, 20
_MAX_CHARS_LO, _MAX_CHARS_HI = 50, 1000


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class DialogSummaryConfig:
    enabled: bool = False
    threshold_turns: int = DEFAULT_THRESHOLD_TURNS
    keep_recent: int = DEFAULT_KEEP_RECENT
    summary_max_chars: int = DEFAULT_SUMMARY_MAX_CHARS
    summarizer_kind: str = DEFAULT_SUMMARIZER_KIND  # "llm" | "heuristic"


def dialog_summary_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    e = env if env is not None else os.environ
    return (e.get("COCO_DIALOG_SUMMARY") or "0").strip().lower() in {"1", "true", "yes", "on"}


def _int_env(env: Mapping[str, str], key: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        log.warning("[dialog_summary] %s=%r 非整数，回退默认 %d", key, raw, default)
        return default
    if v < lo:
        log.warning("[dialog_summary] %s=%d <%d，clamp 到 %d", key, v, lo, lo)
        return lo
    if v > hi:
        log.warning("[dialog_summary] %s=%d >%d，clamp 到 %d", key, v, hi, hi)
        return hi
    return v


def config_from_env(env: Optional[Mapping[str, str]] = None) -> DialogSummaryConfig:
    e = env if env is not None else os.environ
    enabled = dialog_summary_enabled_from_env(e)
    threshold = _int_env(e, "COCO_DIALOG_SUMMARY_THRESHOLD", DEFAULT_THRESHOLD_TURNS,
                         _THRESHOLD_LO, _THRESHOLD_HI)
    keep = _int_env(e, "COCO_DIALOG_SUMMARY_KEEP", DEFAULT_KEEP_RECENT,
                    _KEEP_LO, _KEEP_HI)
    max_chars = _int_env(e, "COCO_DIALOG_SUMMARY_MAX_CHARS", DEFAULT_SUMMARY_MAX_CHARS,
                         _MAX_CHARS_LO, _MAX_CHARS_HI)
    kind = (e.get("COCO_DIALOG_SUMMARY_KIND") or DEFAULT_SUMMARIZER_KIND).strip().lower()
    if kind not in {"llm", "heuristic"}:
        log.warning("[dialog_summary] COCO_DIALOG_SUMMARY_KIND=%r 非法，回退 %s",
                    kind, DEFAULT_SUMMARIZER_KIND)
        kind = DEFAULT_SUMMARIZER_KIND
    # keep_recent 不应 >= threshold（否则永远不会触发）。clamp 到 threshold-1。
    if keep >= threshold:
        log.warning("[dialog_summary] keep_recent=%d >= threshold=%d，clamp 到 %d",
                    keep, threshold, threshold - 1)
        keep = max(1, threshold - 1)
    return DialogSummaryConfig(
        enabled=enabled,
        threshold_turns=threshold,
        keep_recent=keep,
        summary_max_chars=max_chars,
        summarizer_kind=kind,
    )


# ---------------------------------------------------------------------------
# Summarizer Protocol + Implementations
# ---------------------------------------------------------------------------


class DialogSummarizer(Protocol):
    def summarize(self, turns: List[Tuple[str, str]]) -> str:  # pragma: no cover
        """把 (user, assistant) 对列表压缩成一条短摘要。允许抛异常 → 调用方 fail-soft。"""
        ...


class HeuristicSummarizer:
    """无 LLM 时的 fallback：直接拼接 user 文本，截断到 max_chars。

    输出形如：
        "前面聊到：你好；天气怎么样；公园好玩吗"
    """

    def __init__(self, max_chars: int = DEFAULT_SUMMARY_MAX_CHARS) -> None:
        self.max_chars = max_chars

    def summarize(self, turns: List[Tuple[str, str]]) -> str:
        if not turns:
            return ""
        # interact-009 L1-4: 拼 user + assistant 两段，保留"机器人答应/拒绝过什么"
        # 的关键状态。格式：[U]xxx [A]yyy；[U]zzz [A]www
        parts: List[str] = []
        for u, a in turns:
            u = (u or "").strip()
            a = (a or "").strip()
            if not u and not a:
                continue
            seg_pieces: List[str] = []
            if u:
                seg_pieces.append(f"[U]{u}")
            if a:
                seg_pieces.append(f"[A]{a}")
            parts.append(" ".join(seg_pieces))
        body = "；".join(parts) if parts else "（无内容）"
        text = f"前面聊到：{body}"
        if len(text) > self.max_chars:
            text = text[: self.max_chars - 1] + "…"
        return text


class LLMSummarizer:
    """用 llm_reply_fn 跑摘要 prompt。

    llm_reply_fn 签名兼容 coco.llm.LLMClient.reply：
        fn(text, *, system_prompt=None, history=None) -> str
    本类只用 text+system_prompt，不传 history（摘要场景一次性短任务）。
    """

    SYSTEM_PROMPT = (
        "你是对话摘要器。请用 1-2 句话（不超过 {n} 字）概括以下用户与助手的对话要点，"
        "保留人物、话题、情绪线索；不要复述全文，不要加引号，直接输出摘要文本。"
    )

    def __init__(
        self,
        llm_reply_fn: Callable[..., str],
        max_chars: int = DEFAULT_SUMMARY_MAX_CHARS,
    ) -> None:
        if llm_reply_fn is None:
            raise ValueError("LLMSummarizer 需要 llm_reply_fn")
        self.llm_reply_fn = llm_reply_fn
        self.max_chars = max_chars
        # interact-009 L1-5: 用 inspect.signature 一次性 probe 是否接受 system_prompt，
        # 避免 try/except TypeError 把业务侧 TypeError 误判为签名不匹配 → 重复调 LLM。
        self._accepts_system_prompt = self._probe_kwarg(llm_reply_fn, "system_prompt")

    @staticmethod
    def _probe_kwarg(fn: Callable[..., str], name: str) -> bool:
        """探测 fn 是否接受 keyword 参数 name；inspect 失败时保守返回 False。"""
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return False
        for p in sig.parameters.values():
            if p.kind is inspect.Parameter.VAR_KEYWORD:
                return True
            if p.name == name and p.kind in (
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                return True
        return False

    @staticmethod
    def _format_turns(turns: List[Tuple[str, str]]) -> str:
        lines: List[str] = []
        for i, (u, a) in enumerate(turns, 1):
            lines.append(f"{i}. 用户：{(u or '').strip()}")
            lines.append(f"   助手：{(a or '').strip()}")
        return "\n".join(lines)

    def summarize(self, turns: List[Tuple[str, str]]) -> str:
        if not turns:
            return ""
        body = self._format_turns(turns)
        sys_prompt = self.SYSTEM_PROMPT.format(n=self.max_chars)
        # interact-009 L1-5: 用 probe 结果决定签名，业务 TypeError 直接抛上层 fail-soft
        if self._accepts_system_prompt:
            out = self.llm_reply_fn(body, system_prompt=sys_prompt)
        else:
            out = self.llm_reply_fn(body)
        text = (out or "").strip()
        if len(text) > self.max_chars:
            text = text[: self.max_chars - 1] + "…"
        return text


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_summarizer(
    cfg: DialogSummaryConfig,
    llm_reply_fn: Optional[Callable[..., str]] = None,
) -> Optional[DialogSummarizer]:
    """根据 cfg 构造 summarizer；llm 模式但无 llm_reply_fn 时回退 heuristic。"""
    if not cfg.enabled:
        return None
    if cfg.summarizer_kind == "llm":
        if llm_reply_fn is None:
            log.warning("[dialog_summary] kind=llm 但未提供 llm_reply_fn，回退 heuristic")
            return HeuristicSummarizer(max_chars=cfg.summary_max_chars)
        return LLMSummarizer(llm_reply_fn, max_chars=cfg.summary_max_chars)
    return HeuristicSummarizer(max_chars=cfg.summary_max_chars)


__all__ = [
    "DialogSummarizer",
    "LLMSummarizer",
    "HeuristicSummarizer",
    "DialogSummaryConfig",
    "config_from_env",
    "dialog_summary_enabled_from_env",
    "build_summarizer",
    "DEFAULT_THRESHOLD_TURNS",
    "DEFAULT_KEEP_RECENT",
    "DEFAULT_SUMMARY_MAX_CHARS",
    "DEFAULT_SUMMARIZER_KIND",
]
