"""coco.conversation — 对话状态机 (interact-008).

设计目标
========

InteractSession 之上加一层状态机，让对话不再是"每来一句 ASR 就走一遍 LLM"，
而是有明确状态：

- IDLE       : 空闲，未收到用户语音
- LISTENING  : ASR 正在转写
- THINKING   : LLM 正在生成回复
- SPEAKING   : TTS 正在播放
- TEACHING   : 教学模式（多轮持续，注入特定 system_prompt）
- QUIET      : 用户要求"安静"，N 秒内 InteractSession.handle_audio 直接返回

线程模型
--------
- 所有状态读写用 RLock 保护，调用方可在任意线程驱动
- ConversationStateMachine 提供 ``transition_to(new_state, source=...)`` +
  事件回调（``on_transition``）
- ``current_state`` 始终读取最新值
- ``is_quiet_now()`` 集合体感：含 QUIET 自动过期检查（到时间自动回 IDLE）

事件 emit
---------
- 不直接 emit logging_setup —— 由 InteractSession 调用方在每次 transition 后
  emit "interact.state_transition"，state machine 只负责状态本身。
"""

from __future__ import annotations

import enum
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

log = logging.getLogger(__name__)


class ConvState(str, enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    TEACHING = "teaching"
    QUIET = "quiet"


@dataclass(frozen=True)
class ConversationConfig:
    """对话状态机配置。

    - ``quiet_seconds``：QUIET 状态持续时长，超过自动回 IDLE
    - ``teaching_system_prompt``：进入 TEACHING 状态时附加到 system_prompt
      （由 InteractSession 在 LLM 调用前合并）
    """

    quiet_seconds: float = 30.0
    teaching_max_seconds: float = 600.0
    teaching_system_prompt: str = (
        "你现在处于教学模式。请用更耐心、更结构化、更易懂的方式回答，"
        "可以用类比和分步骤说明，避免一次塞太多概念。"
    )


def config_from_env(env: Optional[Mapping[str, str]] = None) -> ConversationConfig:
    env = env if env is not None else os.environ
    raw = (env.get("COCO_QUIET_S") or "").strip()
    qs = 30.0
    if raw:
        try:
            qs = float(raw)
            if qs < 1.0:
                log.warning("[conversation] COCO_QUIET_S=%.2f <1，clamp 1.0", qs)
                qs = 1.0
            elif qs > 3600.0:
                log.warning("[conversation] COCO_QUIET_S=%.2f >3600，clamp 3600", qs)
                qs = 3600.0
        except ValueError:
            log.warning("[conversation] COCO_QUIET_S=%r 非数字，回退默认 30", raw)
    raw_t = (env.get("COCO_TEACHING_MAX_S") or "").strip()
    tms = 600.0
    if raw_t:
        try:
            tms = float(raw_t)
            if tms < 10.0:
                log.warning("[conversation] COCO_TEACHING_MAX_S=%.2f <10，clamp 10", tms)
                tms = 10.0
            elif tms > 7200.0:
                log.warning("[conversation] COCO_TEACHING_MAX_S=%.2f >7200，clamp 7200", tms)
                tms = 7200.0
        except ValueError:
            log.warning("[conversation] COCO_TEACHING_MAX_S=%r 非数字，回退默认 600", raw_t)
    return ConversationConfig(quiet_seconds=qs, teaching_max_seconds=tms)


@dataclass
class StateTransition:
    from_state: ConvState
    to_state: ConvState
    source: str  # "user_utterance" / "llm_start" / "llm_done" / "tts_start" / ...
    ts: float


class ConversationStateMachine:
    """线程安全的对话状态机。

    用法
    ----
    sm = ConversationStateMachine()
    sm.on_user_utterance(intent)   # → 转移到 LISTENING / TEACHING / QUIET / IDLE
    sm.on_llm_start()              # → THINKING
    sm.on_llm_done()               # → SPEAKING (or back to TEACHING if was teaching)
    sm.on_tts_done()               # → IDLE / TEACHING

    QUIET 状态自动过期：``is_quiet_now()`` 与 ``current_state`` getter 检查超时。
    """

    def __init__(
        self,
        config: Optional[ConversationConfig] = None,
        clock: Optional[Callable[[], float]] = None,
        on_transition: Optional[Callable[[StateTransition], None]] = None,
    ) -> None:
        self.config = config or ConversationConfig()
        self._clock = clock or time.monotonic
        self._on_transition = on_transition
        # interact-008 L2: 公开的 listener 列表（覆盖 _on_transition 是反模式）
        self._transition_listeners: list[Callable[[StateTransition], None]] = []
        self._lock = threading.RLock()
        self._state: ConvState = ConvState.IDLE
        # 进入 TEACHING 模式后，对话主循环结束（SPEAKING→…）时回到 TEACHING 而非 IDLE
        self._teaching_active: bool = False
        # interact-008 L2: TEACHING 进入时间戳，超过 teaching_max_seconds 自动失效
        self._teaching_entered_at: float = 0.0
        # QUIET 进入时间戳
        self._quiet_entered_at: float = 0.0
        self.transitions: list[StateTransition] = []

    def add_transition_listener(self, callback: Callable[[StateTransition], None]) -> None:
        """注册 transition 监听器，每次 state 变化都被调用。

        - 多个 listener 按注册顺序触发；任意 listener 抛异常不影响其他 listener 与状态机本身。
        - 旧 ``on_transition`` 构造参数仍保留作为单回调兼容入口。
        """
        with self._lock:
            self._transition_listeners.append(callback)

    # ------------------------------------------------------------------
    # state read
    # ------------------------------------------------------------------

    @property
    def current_state(self) -> ConvState:
        with self._lock:
            self._maybe_expire_quiet_locked()
            self._maybe_expire_teaching_locked()
            return self._state

    def is_quiet_now(self) -> bool:
        with self._lock:
            self._maybe_expire_quiet_locked()
            return self._state is ConvState.QUIET

    def is_teaching(self) -> bool:
        with self._lock:
            self._maybe_expire_teaching_locked()
            return self._teaching_active

    # ------------------------------------------------------------------
    # transitions
    # ------------------------------------------------------------------

    def _transition_locked(self, new: ConvState, source: str) -> None:
        old = self._state
        if old is new:
            return
        self._state = new
        ts = self._clock()
        if new is ConvState.QUIET:
            self._quiet_entered_at = ts
        tr = StateTransition(from_state=old, to_state=new, source=source, ts=ts)
        self.transitions.append(tr)
        if self._on_transition is not None:
            try:
                self._on_transition(tr)
            except Exception as e:  # noqa: BLE001
                log.warning("on_transition callback failed: %s: %s", type(e).__name__, e)
        for cb in self._transition_listeners:
            try:
                cb(tr)
            except Exception as e:  # noqa: BLE001
                log.warning("transition listener failed: %s: %s", type(e).__name__, e)

    def _maybe_expire_quiet_locked(self) -> None:
        if self._state is ConvState.QUIET:
            now = self._clock()
            if (now - self._quiet_entered_at) >= self.config.quiet_seconds:
                self._transition_locked(ConvState.IDLE, source="quiet_expired")

    def _maybe_expire_teaching_locked(self) -> None:
        """TEACHING 持续超过 teaching_max_seconds 自动失效，回 IDLE。

        - 仅在 _teaching_active=True 时检查
        - 与 QUIET 过期类似，由所有 read getter 触发
        """
        if not self._teaching_active:
            return
        now = self._clock()
        if (now - self._teaching_entered_at) >= self.config.teaching_max_seconds:
            self._teaching_active = False
            if self._state is ConvState.TEACHING:
                self._transition_locked(ConvState.IDLE, source="teaching_expired")

    def force_to(self, new: ConvState, source: str = "force") -> None:
        with self._lock:
            self._transition_locked(new, source)

    # ------------------------------------------------------------------
    # event hooks (driven by InteractSession)
    # ------------------------------------------------------------------

    def on_user_utterance(self, intent_value: str) -> None:
        """用户语音进入处理链路时调用。

        intent_value 是 ``Intent.value`` 字符串（避免 conversation 模块依赖 intent
        模块，方便单测）。已知值：question/command/chitchat/teach/farewell/unknown。

        QUIET 期内任何 utterance 不改变状态（由 InteractSession.handle_audio 检查
        ``is_quiet_now()`` 直接返回）。
        """
        with self._lock:
            self._maybe_expire_quiet_locked()
            if self._state is ConvState.QUIET:
                # 仍在 QUIET 内，不改状态
                return
            if intent_value == "teach":
                if not self._teaching_active:
                    self._teaching_entered_at = self._clock()
                self._teaching_active = True
                self._transition_locked(ConvState.TEACHING, source="user_utterance:teach")
            elif intent_value == "farewell":
                # 告别后回 IDLE，并退出 TEACHING
                self._teaching_active = False
                self._transition_locked(ConvState.LISTENING, source="user_utterance:farewell")
            else:
                self._transition_locked(ConvState.LISTENING, source=f"user_utterance:{intent_value}")

    def enter_quiet(self, source: str = "command:quiet") -> None:
        with self._lock:
            self._teaching_active = False  # QUIET 中断 TEACHING
            self._transition_locked(ConvState.QUIET, source=source)

    def on_llm_start(self) -> None:
        with self._lock:
            self._maybe_expire_quiet_locked()
            if self._state is ConvState.QUIET:
                return
            self._transition_locked(ConvState.THINKING, source="llm_start")

    def on_llm_done(self) -> None:
        with self._lock:
            self._maybe_expire_quiet_locked()
            if self._state is ConvState.QUIET:
                return
            self._transition_locked(ConvState.SPEAKING, source="llm_done")

    def on_tts_start(self) -> None:
        with self._lock:
            self._maybe_expire_quiet_locked()
            if self._state is ConvState.QUIET:
                return
            if self._state is not ConvState.SPEAKING:
                self._transition_locked(ConvState.SPEAKING, source="tts_start")

    def on_tts_done(self) -> None:
        with self._lock:
            self._maybe_expire_quiet_locked()
            if self._state is ConvState.QUIET:
                return
            if self._teaching_active:
                self._transition_locked(ConvState.TEACHING, source="tts_done")
            else:
                self._transition_locked(ConvState.IDLE, source="tts_done")

    def teaching_system_prompt(self) -> str:
        return self.config.teaching_system_prompt


__all__ = [
    "ConvState",
    "ConversationConfig",
    "ConversationStateMachine",
    "StateTransition",
    "config_from_env",
]
