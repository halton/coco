"""coco.dialog — 多轮对话上下文（interact-004）.

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
import time
from dataclasses import dataclass
from collections import deque
from typing import Callable, Deque, List, Optional, Tuple


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

    # ------------------------------------------------------------------ core
    def _check_idle(self) -> bool:
        """如果距离最后一次 append 超时，清空 buffer。返回 True 表示发生了 reset。"""
        if self._last_append_ts is None:
            return False
        if (self._clock() - self._last_append_ts) > self.idle_timeout_s:
            self._buf.clear()
            self._last_append_ts = None
            log.info("[dialog] idle timeout (>%ss), memory cleared", self.idle_timeout_s)
            return True
        return False

    def append(self, user_text: str, assistant_text: str) -> None:
        """追加一轮 (user, assistant) 对话。空字符串也允许（极端情况）。"""
        # 在追加前先 check idle —— 跨过 idle 后这轮算新会话起点
        self._check_idle()
        u = (user_text or "").strip()
        a = (assistant_text or "").strip()
        self._buf.append((u, a))
        self._last_append_ts = self._clock()

    def recent_turns(self) -> List[Tuple[str, str]]:
        """返回最近 ≤max_turns 轮（按时间顺序，旧→新）。先 check idle。"""
        self._check_idle()
        return list(self._buf)

    def clear(self) -> None:
        self._buf.clear()
        self._last_append_ts = None

    def __len__(self) -> int:
        return len(self._buf)

    # --------------------------------------------------------------- adapter
    def build_messages(
        self,
        system_prompt: str,
        user_text: str,
    ) -> List[dict]:
        """组装 OpenAI/Ollama 兼容的 messages：
        [system] + flatten(recent_turns 每轮 user+assistant) + [当前 user_text]。

        当前 user_text 不会被预先 append（append 由调用方在拿到 assistant 回复后做）。
        """
        msgs: List[dict] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        for u, a in self.recent_turns():
            if u:
                msgs.append({"role": "user", "content": u})
            if a:
                msgs.append({"role": "assistant", "content": a})
        msgs.append({"role": "user", "content": (user_text or "").strip()})
        return msgs


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
