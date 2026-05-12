"""coco.dialog — 多轮对话上下文（interact-004）+ 历史压缩（interact-009）.

设计原则：
- 默认 OFF（COCO_DIALOG_MEMORY=0）保持向后兼容。InteractSession 不引用 DialogMemory
  时，行为完全等同 interact-002/003。
- ring buffer 长度 N（默认 4）；每轮 (user, assistant) 一对；超长按"先进先出"丢弃。
- idle 超时（默认 120s）：从 *最后一次 append 时刻* 起计算；下一次 append 前检查，
  若超时则先 clear()，再 append。这样重启对话不会带过期话题。
- monotonic 时钟可注入（fake clock 给单测用，无线程依赖）。
- 不持久化；进程重启即清空（符合"陪伴"而非"助理"定位）。

公开 API：
- DialogMemory(max_turns=4, idle_timeout_s=120.0, clock=time.monotonic)
- append(user_text, assistant_text)
- recent_turns() -> list[tuple[str, str]]            （最近一对靠后）
- build_messages(system_prompt, user_text) -> list[{role, content}]
- clear()
- env helpers：dialog_memory_enabled_from_env() / config_from_env()

注意：本模块只管"记 + 取"；是否注入到 LLM 请求由 LLMClient.reply(history=...) 决定。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from collections import deque
from typing import Any, Callable, Deque, List, Optional, Tuple


log = logging.getLogger(__name__)


DEFAULT_MAX_TURNS = 4
DEFAULT_IDLE_TIMEOUT_S = 120.0


@dataclass
class DialogConfig:
    max_turns: int = DEFAULT_MAX_TURNS
    idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S


class DialogMemory:
    """最近 N 轮 (user, assistant) ring buffer，含 idle 自动清空。

    线程安全：单线程使用足够（InteractSession.handle_audio 串行）；如多线程
    访问，调用方自行加锁。
    """

    def __init__(
        self,
        *,
        max_turns: int = DEFAULT_MAX_TURNS,
        idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_turns < 1:
            raise ValueError(f"max_turns 必须 ≥1，got {max_turns}")
        if idle_timeout_s <= 0:
            raise ValueError(f"idle_timeout_s 必须 >0，got {idle_timeout_s}")
        self.max_turns = int(max_turns)
        self.idle_timeout_s = float(idle_timeout_s)
        self._clock = clock
        self._buf: Deque[Tuple[str, str]] = deque(maxlen=self.max_turns)
        self._last_append_ts: Optional[float] = None
        # interact-009: 压缩摘要（None 表示未压缩）。compress_if_needed 触发后填入。
        self._summary: Optional[str] = None
        # interact-009 L1-3: 记录上次压缩完成后 deque 长度，用于 hot-path guard
        self._last_compress_buf_len: Optional[int] = None
        # interact-009: 线程安全锁（compress_if_needed 可能跨线程触发）
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ core
    def _check_idle(self) -> bool:
        """如果距离最后一次 append 超时，清空 buffer。返回 True 表示发生了 reset。"""
        if self._last_append_ts is None:
            return False
        if (self._clock() - self._last_append_ts) > self.idle_timeout_s:
            had_summary = self._summary is not None
            self._buf.clear()
            self._last_append_ts = None
            self._summary = None
            self._last_compress_buf_len = None
            log.info("[dialog] idle timeout (>%ss), memory cleared", self.idle_timeout_s)
            # interact-009 L2: idle clear summary 时 emit 调试事件
            if had_summary:
                try:
                    from coco.logging_setup import emit as _emit
                    _emit(
                        "interact.dialog_summary_cleared_idle",
                        idle_timeout_s=self.idle_timeout_s,
                    )
                except Exception:  # noqa: BLE001
                    pass
            return True
        return False

    def append(self, user_text: str, assistant_text: str) -> None:
        """追加一轮 (user, assistant) 对话。空字符串也允许（极端情况）。"""
        with self._lock:
            # 在追加前先 check idle —— 跨过 idle 后这轮算新会话起点
            self._check_idle()
            u = (user_text or "").strip()
            a = (assistant_text or "").strip()
            self._buf.append((u, a))
            self._last_append_ts = self._clock()

    def recent_turns(self) -> List[Tuple[str, str]]:
        """返回最近 ≤max_turns 轮（按时间顺序，旧→新）。先 check idle。"""
        with self._lock:
            self._check_idle()
            return list(self._buf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
            self._last_append_ts = None
            self._summary = None
            self._last_compress_buf_len = None

    def __len__(self) -> int:
        # interact-009: 含 summary 时计 1 + 真实 turn 数（V6）
        with self._lock:
            n = len(self._buf)
            if self._summary is not None:
                n += 1
            return n

    @property
    def summary(self) -> Optional[str]:
        return self._summary

    # --------------------------------------------------------------- adapter
    def build_messages(
        self,
        system_prompt: str,
        user_text: str,
    ) -> List[dict]:
        """组装 OpenAI/Ollama 兼容的 messages：
        [system_prompt] + [system 摘要（若已压缩）] + flatten(recent_turns) + [当前 user_text]。

        当前 user_text 不会被预先 append（append 由调用方在拿到 assistant 回复后做）。
        """
        msgs: List[dict] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        # interact-009: 注入压缩摘要（在 system_prompt 之后、原文 turns 之前）
        with self._lock:
            summary = self._summary
        if summary:
            msgs.append({"role": "system", "content": f"对话摘要：{summary}"})
        for u, a in self.recent_turns():
            if u:
                msgs.append({"role": "user", "content": u})
            if a:
                msgs.append({"role": "assistant", "content": a})
        msgs.append({"role": "user", "content": (user_text or "").strip()})
        return msgs

    # ---------------------------------------------------------- interact-009
    def compress_if_needed(
        self,
        *,
        threshold_turns: int,
        keep_recent: int,
        summarizer: Any,  # DialogSummarizer protocol
        emit_fn: Optional[Callable[..., None]] = None,
    ) -> bool:
        """当 turns 数 >= threshold_turns 时压缩最早的 (n - keep_recent) 轮为单条摘要。

        参数：
        - threshold_turns: 触发阈值（应 >=4）
        - keep_recent: 保留最近 N 轮原文
        - summarizer: 实现 DialogSummarizer.summarize(turns) -> str
        - emit_fn: 可选 metrics emit 函数（默认从 coco.logging_setup 取）

        返回：True 表示发生了压缩；False 表示未触发或失败（fail-soft，原历史不动）。

        失败处理：summarizer 抛异常 / 返回空 → 保持原 history 不动 + emit
        "interact.dialog_summary_failed"。
        """
        if threshold_turns < 1 or keep_recent < 0:
            return False
        if keep_recent >= threshold_turns:
            # 配置无效：保留数 >= 阈值，永远不会触发；fail-soft
            return False
        if emit_fn is None:
            try:
                from coco.logging_setup import emit as _emit
                emit_fn = _emit
            except Exception:  # noqa: BLE001
                emit_fn = lambda *_a, **_k: None  # noqa: E731

        with self._lock:
            n = len(self._buf)
            if n < threshold_turns:
                return False
            # interact-009 L1-3: hot-path guard — 自上次压缩后新增 turns 必须
            # >= keep_recent 才再次压缩，避免 max_turns ≈ threshold 时每轮都触发 LLM。
            if self._last_compress_buf_len is not None:
                # _last_compress_buf_len 记录"上次压缩完成后 deque 的长度"
                # 例：keep_recent=4 → 压缩完 buf 长度=4 → 必须涨到 4+4=8 才允许再次压缩
                if n - self._last_compress_buf_len < keep_recent:
                    return False
            # 取出待压缩的最早 (n - keep_recent) 轮
            to_summarize: List[Tuple[str, str]] = list(self._buf)[: n - keep_recent]
            tail: List[Tuple[str, str]] = list(self._buf)[n - keep_recent:]
            # interact-009 L1-1: 累积摘要 — 若已存在旧 summary，作为 pseudo-turn 注入
            # 待压缩列表头部，让 summarizer 在"旧摘要 + 新中段"基础上再总结，
            # 避免第二次压缩覆盖第一次摘要导致最早信息丢失。
            prev_summary = self._summary
        if prev_summary:
            to_summarize = [("[此前摘要] " + prev_summary, "")] + to_summarize

        # 调 summarizer（不持锁，避免 LLM 慢路径阻塞 append）
        try:
            summary_text = summarizer.summarize(to_summarize)
            if not summary_text or not str(summary_text).strip():
                raise ValueError("summarizer 返回空")
            summary_text = str(summary_text).strip()
        except Exception as ex:  # noqa: BLE001
            log.warning("[dialog] summarizer 失败 fail-soft 保留原历史: %s: %s",
                        type(ex).__name__, ex)
            try:
                emit_fn(
                    "interact.dialog_summary_failed",
                    error_type=type(ex).__name__,
                    error=str(ex)[:200],
                    turns_count=len(to_summarize),
                )
            except Exception:  # noqa: BLE001
                pass
            return False

        # 应用结果
        with self._lock:
            # 重建 deque：丢前缀，保留 tail
            self._buf.clear()
            for t in tail:
                self._buf.append(t)
            self._summary = summary_text
            # interact-009 L1-3: 记录压缩完成后 deque 长度
            self._last_compress_buf_len = len(self._buf)

        try:
            emit_fn(
                "interact.dialog_summarized",
                summarized_turns=len(to_summarize),
                kept_turns=len(tail),
                summary_chars=len(summary_text),
            )
        except Exception:  # noqa: BLE001
            pass
        log.info("[dialog] compressed %d turns -> 1 summary, kept %d recent",
                 len(to_summarize), len(tail))
        return True


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def dialog_memory_enabled_from_env() -> bool:
    """COCO_DIALOG_MEMORY=1 启用。默认 OFF（向后兼容 interact-002/003）。"""
    return (os.environ.get("COCO_DIALOG_MEMORY") or "0").strip() in ("1", "true", "yes", "on")


def config_from_env() -> DialogConfig:
    """从环境读 max_turns / idle_timeout，越界 clamp + warn。

    - COCO_DIALOG_MAX_TURNS: 1..16，默认 4
    - COCO_DIALOG_IDLE_S:    1..3600，默认 120
    """
    raw_n = os.environ.get("COCO_DIALOG_MAX_TURNS", str(DEFAULT_MAX_TURNS)).strip()
    raw_t = os.environ.get("COCO_DIALOG_IDLE_S", str(DEFAULT_IDLE_TIMEOUT_S)).strip()
    try:
        n = int(raw_n)
    except ValueError:
        log.warning("[dialog] COCO_DIALOG_MAX_TURNS=%r 非整数，回退默认 %d", raw_n, DEFAULT_MAX_TURNS)
        n = DEFAULT_MAX_TURNS
    try:
        t = float(raw_t)
    except ValueError:
        log.warning("[dialog] COCO_DIALOG_IDLE_S=%r 非数字，回退默认 %.1f", raw_t, DEFAULT_IDLE_TIMEOUT_S)
        t = DEFAULT_IDLE_TIMEOUT_S
    if n < 1:
        log.warning("[dialog] COCO_DIALOG_MAX_TURNS=%d <1，clamp 到 1", n)
        n = 1
    if n > 16:
        log.warning("[dialog] COCO_DIALOG_MAX_TURNS=%d >16，clamp 到 16", n)
        n = 16
    if t < 1.0:
        log.warning("[dialog] COCO_DIALOG_IDLE_S=%.2f <1，clamp 到 1.0", t)
        t = 1.0
    if t > 3600.0:
        log.warning("[dialog] COCO_DIALOG_IDLE_S=%.2f >3600，clamp 到 3600.0", t)
        t = 3600.0
    return DialogConfig(max_turns=n, idle_timeout_s=t)


__all__ = [
    "DialogMemory",
    "DialogConfig",
    "DEFAULT_MAX_TURNS",
    "DEFAULT_IDLE_TIMEOUT_S",
    "dialog_memory_enabled_from_env",
    "config_from_env",
]
