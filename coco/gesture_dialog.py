"""coco.gesture_dialog — interact-010 手势驱动对话回合.

设计目标
========

vision-005 的 GestureRecognizer 已能 emit ``vision.gesture_detected``，并在
``main.py`` 内分发到「行为侧」handler（WAVE→glance+say "你好" / THUMBS_UP→
expression(excited)）。本模块在「行为侧」之外加一条**对话侧**通路：

- ``WAVE @ ConvState.IDLE``  → 触发一次 ProactiveScheduler 风格的主动话题
  （"你想聊点什么？"），与 ProactiveScheduler 共享 30s cooldown 窗口
- ``WAVE @ AWAITING_RESPONSE`` → 抑制（避免重复打扰）
- ``THUMBS_UP @ AWAITING + 5s 内`` → 当 yes 注入（"好的"），跳过 ASR 直接驱动
  下一轮 LLM
- ``NOD @ AWAITING + 上一句 assistant 是 yes/no 提问 + 5s 内`` → 当 yes
  （"是"）注入
- ``SHAKE @ AWAITING + 上一句 assistant 是 yes/no 提问 + 5s 内`` → 当 no
  （"不是"）注入
- ``NOD/SHAKE @ IDLE`` → 仅记录事件，不动对话

线程模型
--------

- gesture handler 在 vision tick 线程（GestureRecognizer 内部）
- ConvStateMachine / DialogMemory 在主交互线程
- 本模块用 ``threading.RLock`` 保护内部状态；下游 ``inject_user_text_fn``
  自身需线程安全（典型由 main.py wire 一个 thread-safe lambda）

AWAITING 语义
-------------

ConvState 里没有显式的 "AWAITING_RESPONSE"。在 Coco 当前架构中，"awaiting"
约等于：上一次 assistant 刚说完话（``register_assistant_utterance``）后的
``awaiting_window_s``（默认 5s）窗口内，且 ``current_state in (IDLE, SPEAKING)``
即 ConvState 还未因新一轮用户输入切到 LISTENING / THINKING。这个时间窗 +
"我们刚说完话"两个事实合起来就是 AWAITING。

yes/no 提问识别
---------------

为避免引入 LLM 二次调用，``register_assistant_utterance(text)`` 时本模块用启
发式（句末问号 + 关键词："是不是" / "对吗" / "好吗" / "行吗" / "可以吗" /
"对不对" / "要不要" 等）判定该句是否 yes/no 提问，缓存到 bridge 内部。

cooldown 共享
-------------

bridge 触发对话后必须告知 ProactiveScheduler "已在 cooldown 窗口内"，避免
proactive scheduler 立刻又起一个主动话题；反之 proactive scheduler 起话题
后，gesture bridge 在共享窗口内也应抑制。本模块通过 ``proactive_scheduler``
（可选）调用其新增的 ``record_trigger(source)`` / ``is_in_cooldown()``
接口实现「写穿 _last_proactive_ts」。proactive scheduler 内 ``_should_trigger``
现有的 cooldown 检查会自动 honor；反向 gesture bridge 检查 ``is_in_cooldown``
来抑制自己。

Default-OFF
-----------

- ``COCO_GESTURE_DIALOG=1`` 才启用 bridge（main.py 才构造）；
- 即使 bridge 构造，vision-005 现有 WAVE→glance / THUMBS_UP→Expression
  行为侧 handler **不受影响**——两条通路并存，分别 gate。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env / Config
# ---------------------------------------------------------------------------


DEFAULT_AWAITING_WINDOW_S = 5.0
DEFAULT_COOLDOWN_S = 30.0
DEFAULT_PROACTIVE_PROMPT = "你想聊点什么？"
DEFAULT_THUMBS_UP_TEXT = "好的"
DEFAULT_NOD_TEXT = "是"
DEFAULT_SHAKE_TEXT = "不是"


def _bool_env(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw == "":
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    # 非法值 → clamp 到 default（OFF）
    log.warning("[gesture_dialog] %s=%r 非法布尔值，clamp 到 %s", key, raw, default)
    return default


def gesture_dialog_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    """COCO_GESTURE_DIALOG=1 启用 GestureDialogBridge。默认 OFF。"""
    e = env if env is not None else os.environ
    return _bool_env(e, "COCO_GESTURE_DIALOG", default=False)


@dataclass(frozen=True)
class GestureDialogConfig:
    enabled: bool = False
    awaiting_window_s: float = DEFAULT_AWAITING_WINDOW_S
    cooldown_s: float = DEFAULT_COOLDOWN_S
    proactive_prompt: str = DEFAULT_PROACTIVE_PROMPT
    thumbs_up_text: str = DEFAULT_THUMBS_UP_TEXT
    nod_text: str = DEFAULT_NOD_TEXT
    shake_text: str = DEFAULT_SHAKE_TEXT


def config_from_env(env: Optional[Mapping[str, str]] = None) -> GestureDialogConfig:
    e = env if env is not None else os.environ
    return GestureDialogConfig(
        enabled=gesture_dialog_enabled_from_env(e),
        awaiting_window_s=DEFAULT_AWAITING_WINDOW_S,
        cooldown_s=DEFAULT_COOLDOWN_S,
    )


# ---------------------------------------------------------------------------
# yes/no 提问启发式
# ---------------------------------------------------------------------------


YESNO_HINTS = (
    "是不是",
    "对吗",
    "对不对",
    "好吗",
    "行吗",
    "可以吗",
    "要不要",
    "好不好",
    "行不行",
    "可不可以",
    "是吗",
    "吗？",
    "吗?",
    "嗯",
)

QUESTION_PUNCT_PATTERN = re.compile(r"[?？]\s*$")


def is_yes_no_question(utterance: str) -> bool:
    """启发式判断 utterance 是否 yes/no 提问。

    规则（命中任一即 True）：
    - 包含明确的 yes/no 短语（"是不是" / "对吗" / "好吗" 等）
    - 含 "吗" + 句末 ``?`` / ``？``（覆盖 "你喜欢吗？" 这类口语 yes/no）
    其他句末问号（wh-question 如 "你叫什么名字？"）→ False，避免 NOD/SHAKE 误注。
    """
    if not utterance:
        return False
    t = utterance.strip()
    if not t:
        return False
    # 1) 明确的 yes/no 短语（口语场景常无问号）
    for hint in YESNO_HINTS:
        if hint in t:
            return True
    # 2) 句末问号 + 含 "吗" → yes/no 句式
    if QUESTION_PUNCT_PATTERN.search(t) and "吗" in t:
        return True
    return False


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class GestureDialogStats:
    events_total: int = 0
    skipped_disabled: int = 0
    skipped_state: int = 0
    skipped_window: int = 0
    skipped_yesno: int = 0
    skipped_cooldown: int = 0
    triggered_proactive: int = 0
    triggered_thumbs_up_yes: int = 0
    triggered_nod_yes: int = 0
    triggered_shake_no: int = 0
    errors: int = 0


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class GestureDialogBridge:
    """订阅 gesture event，按 ConvState + 手势 kind 路由到对话动作。

    使用方式（典型 wiring，main.py）::

        bridge = GestureDialogBridge(
            config=cfg,
            conv_state_machine=conv_sm,
            dialog_memory=dialog_memory,
            llm_reply_fn=llm.reply,
            tts_say_fn=tts.say,
            proactive_scheduler=proactive,  # 可选：cooldown 共享
            emit_fn=emit,
        )
        # 在已有 vision-005 _on_gesture 闭包末尾追加：
        if bridge_enabled:
            try:
                bridge.on_gesture_event(label)
            except Exception as e:
                log.warning(...)

        # InteractSession.add_transition_listener 注册一次：
        conv_sm.add_transition_listener(bridge.on_conv_transition)

        # 在 InteractSession 完成 reply 后（或 main.py 的 on_interaction）调用：
        bridge.register_assistant_utterance(reply_text)
    """

    def __init__(
        self,
        *,
        config: Optional[GestureDialogConfig] = None,
        conv_state_machine: Any = None,
        dialog_memory: Any = None,
        llm_reply_fn: Optional[Callable[..., str]] = None,
        tts_say_fn: Optional[Callable[..., None]] = None,
        proactive_scheduler: Any = None,
        emit_fn: Optional[Callable[..., None]] = None,
        clock: Optional[Callable[[], float]] = None,
        # 测试钩子：替代默认「调 llm + tts + 写 dialog_memory」组合
        # 签名 inject(text, *, kind, source) → str (assistant_reply) 或 None
        inject_user_text_fn: Optional[Callable[..., Optional[str]]] = None,
    ) -> None:
        self.config = config or GestureDialogConfig()
        self.conv_state_machine = conv_state_machine
        self.dialog_memory = dialog_memory
        self.llm_reply_fn = llm_reply_fn
        self.tts_say_fn = tts_say_fn
        self.proactive_scheduler = proactive_scheduler
        self._emit = emit_fn
        self._clock = clock or time.monotonic
        self._inject_user_text_fn = inject_user_text_fn

        self._lock = threading.RLock()
        # 上一次 assistant utterance + ts + 是否 yes/no 提问
        self._last_assistant_text: str = ""
        self._last_assistant_ts: float = 0.0
        self._last_assistant_is_yesno: bool = False
        # 上一次本 bridge 触发的时刻（用于自检共享 cooldown）
        self._last_trigger_ts: float = 0.0
        self.stats = GestureDialogStats()

    # ------------------------------------------------------------------
    # public hooks
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    def register_assistant_utterance(self, text: str) -> None:
        """每次 assistant 说完一句话后调用。判定 yes/no 提问 + 记录时间戳。"""
        if not self.enabled:
            return
        if not text:
            return
        t = (text or "").strip()
        if not t:
            return
        is_yn = is_yes_no_question(t)
        with self._lock:
            self._last_assistant_text = t
            self._last_assistant_ts = self._clock()
            self._last_assistant_is_yesno = is_yn

    def on_conv_transition(self, transition: Any) -> None:
        """ConversationStateMachine.add_transition_listener 回调。

        当前实现：仅在转入 LISTENING / TEACHING（用户开始新一轮）时清掉
        last_assistant 标记，避免本轮内 NOD/SHAKE 还命中上一轮的 yes/no flag。
        """
        if not self.enabled:
            return
        try:
            new_state = transition.to_state
        except Exception:  # noqa: BLE001
            return
        # 只处理用户输入开启的转移；name 比较走字符串避免循环 import
        try:
            name = getattr(new_state, "value", None) or str(new_state)
        except Exception:  # noqa: BLE001
            name = ""
        if name in {"listening", "teaching"}:
            with self._lock:
                self._last_assistant_text = ""
                self._last_assistant_ts = 0.0
                self._last_assistant_is_yesno = False

    def on_gesture_event(self, label: Any) -> Optional[str]:
        """主入口。返回触发动作名（"proactive" / "yes" / "no" / None=未触发）。

        fail-soft：捕获所有异常，记 stats.errors，不抛。
        """
        try:
            return self._on_gesture_event_inner(label)
        except Exception as e:  # noqa: BLE001
            self.stats.errors += 1
            log.warning("[gesture_dialog] handler crashed: %s: %s", type(e).__name__, e)
            return None

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _on_gesture_event_inner(self, label: Any) -> Optional[str]:
        with self._lock:
            self.stats.events_total += 1

        if not self.enabled:
            self.stats.skipped_disabled += 1
            return None

        try:
            kind = label.kind.value if hasattr(label.kind, "value") else str(label.kind)
        except Exception:  # noqa: BLE001
            kind = ""
        kind = (kind or "").lower()

        # 当前 ConvState（容错读取）
        state_name = self._read_state_name()
        is_awaiting = self._is_awaiting_now()

        # 共享 cooldown 检查（与 ProactiveScheduler 共用窗口）
        in_cd = self._is_in_shared_cooldown()

        # ---- 路由 ----
        if kind == "wave":
            if is_awaiting:
                # WAVE@AWAITING → 抑制
                self.stats.skipped_state += 1
                return None
            if state_name == "idle":
                if in_cd:
                    self.stats.skipped_cooldown += 1
                    return None
                return self._trigger_proactive(kind=kind)
            # 其他状态（LISTENING / THINKING / SPEAKING / TEACHING / QUIET）
            self.stats.skipped_state += 1
            return None

        if kind == "thumbs_up":
            if not is_awaiting:
                # THUMBS_UP@IDLE 等不动作
                self.stats.skipped_state += 1
                return None
            if in_cd:
                self.stats.skipped_cooldown += 1
                return None
            return self._inject(self.config.thumbs_up_text, kind=kind, action="yes")

        if kind in {"nod", "shake"}:
            if not is_awaiting:
                # NOD/SHAKE@IDLE 不动作
                self.stats.skipped_state += 1
                return None
            with self._lock:
                yn = self._last_assistant_is_yesno
            if not yn:
                # AWAITING 但上一句不是 yes/no 提问
                self.stats.skipped_yesno += 1
                return None
            if in_cd:
                self.stats.skipped_cooldown += 1
                return None
            text = self.config.nod_text if kind == "nod" else self.config.shake_text
            action = "yes" if kind == "nod" else "no"
            return self._inject(text, kind=kind, action=action)

        # 未知 kind（HEART 等）→ no-op
        self.stats.skipped_state += 1
        return None

    # ------------------------------------------------------------------
    # state introspection
    # ------------------------------------------------------------------

    def _read_state_name(self) -> str:
        sm = self.conv_state_machine
        if sm is None:
            return "idle"  # 没注入 SM 视为 idle
        try:
            cs = sm.current_state
            return getattr(cs, "value", None) or str(cs)
        except Exception as e:  # noqa: BLE001
            log.warning("[gesture_dialog] read current_state failed: %s: %s",
                        type(e).__name__, e)
            return "idle"

    def _is_awaiting_now(self) -> bool:
        """是否在「Coco 刚说完话等用户回应」的窗口内。

        判定规则：
        - state in {IDLE, SPEAKING}（未被新一轮用户输入打断到 LISTENING/THINKING）
        - 距离上一次 register_assistant_utterance 在 awaiting_window_s 内
        - 上一次 assistant_text 非空
        """
        state_name = self._read_state_name()
        if state_name in {"listening", "thinking", "quiet"}:
            return False
        with self._lock:
            if not self._last_assistant_text:
                return False
            dt = self._clock() - self._last_assistant_ts
        return 0.0 <= dt <= self.config.awaiting_window_s

    def _is_in_shared_cooldown(self) -> bool:
        # 1) 自身 last_trigger_ts
        with self._lock:
            t = self._clock()
            if self._last_trigger_ts > 0 and (t - self._last_trigger_ts) < self.config.cooldown_s:
                return True
        # 2) ProactiveScheduler 已触发？
        ps = self.proactive_scheduler
        if ps is not None:
            try:
                if hasattr(ps, "is_in_cooldown"):
                    return bool(ps.is_in_cooldown(now=t))
            except Exception as e:  # noqa: BLE001
                log.warning("[gesture_dialog] proactive.is_in_cooldown failed: %s: %s",
                            type(e).__name__, e)
        return False

    # ------------------------------------------------------------------
    # action helpers
    # ------------------------------------------------------------------

    def _trigger_proactive(self, *, kind: str) -> str:
        prompt = self.config.proactive_prompt
        # 1) 喂给 ProactiveScheduler 共享窗口（先记，再做事，避免双源叠加）
        self._record_shared_trigger("gesture")
        # 2) 实际"开口"——若有 inject_fn 走它（测试便利），否则走 tts_say_fn 直接说
        reply: Optional[str] = None
        if self._inject_user_text_fn is not None:
            try:
                reply = self._inject_user_text_fn(prompt, kind=kind, source="proactive")
            except Exception as e:  # noqa: BLE001
                self.stats.errors += 1
                log.warning("[gesture_dialog] inject_user_text_fn failed: %s: %s",
                            type(e).__name__, e)
        else:
            # 直接调 tts 把 prompt 说出来；这是最低限度的"主动开口"
            if self.tts_say_fn is not None:
                try:
                    self.tts_say_fn(prompt, blocking=False)
                except TypeError:
                    try:
                        self.tts_say_fn(prompt)
                    except Exception as e:  # noqa: BLE001
                        self.stats.errors += 1
                        log.warning("[gesture_dialog] tts_say_fn failed: %s: %s",
                                    type(e).__name__, e)
                except Exception as e:  # noqa: BLE001
                    self.stats.errors += 1
                    log.warning("[gesture_dialog] tts_say_fn failed: %s: %s",
                                type(e).__name__, e)
            reply = prompt
        # 3) 写 dialog_memory（assistant 端 = prompt 自己，user 端留空）
        self._append_dialog(user_text="", assistant_text=prompt, kind=kind)
        # 4) 注册"我刚说了一句"——后续 5s 内 thumbs_up/nod/shake 才命中 awaiting
        self.register_assistant_utterance(prompt)
        # 5) 计数 + emit
        self.stats.triggered_proactive += 1
        self._emit_event(
            "interact.gesture_dialog_triggered",
            gesture_kind=kind,
            conv_state=self._read_state_name(),
            action="proactive",
            dialog_id=self._dialog_id(),
        )
        return "proactive"

    def _inject(self, user_text: str, *, kind: str, action: str) -> str:
        """注入用户回合（手势→user 文本→LLM→assistant→TTS→写 memory）。"""
        # 1) 共享 cooldown 写穿
        self._record_shared_trigger("gesture")
        # 2) 调 inject_fn 或者本地 fallback（llm + tts）
        reply: Optional[str] = None
        # tag prefix 进入 user_text，确保 dialog_memory / summarizer 可识别
        tagged_user = f"[手势:{kind}] {user_text}"
        if self._inject_user_text_fn is not None:
            try:
                reply = self._inject_user_text_fn(user_text, kind=kind, source=action)
            except Exception as e:  # noqa: BLE001
                self.stats.errors += 1
                log.warning("[gesture_dialog] inject_user_text_fn failed: %s: %s",
                            type(e).__name__, e)
        else:
            # 本地 fallback：llm → tts
            if self.llm_reply_fn is not None:
                try:
                    reply = self.llm_reply_fn(user_text)
                    reply = (reply or "").strip()
                except Exception as e:  # noqa: BLE001
                    self.stats.errors += 1
                    log.warning("[gesture_dialog] llm_reply_fn failed: %s: %s",
                                type(e).__name__, e)
            if reply and self.tts_say_fn is not None:
                try:
                    self.tts_say_fn(reply, blocking=False)
                except TypeError:
                    try:
                        self.tts_say_fn(reply)
                    except Exception as e:  # noqa: BLE001
                        self.stats.errors += 1
                        log.warning("[gesture_dialog] tts_say_fn failed: %s: %s",
                                    type(e).__name__, e)
                except Exception as e:  # noqa: BLE001
                    self.stats.errors += 1
                    log.warning("[gesture_dialog] tts_say_fn failed: %s: %s",
                                type(e).__name__, e)

        # 3) 写 dialog_memory（user 端含 [手势:xxx] 前缀）
        self._append_dialog(user_text=tagged_user, assistant_text=reply or "", kind=kind)

        # 4) 若 reply 非空，注册下一轮 awaiting（让连续手势链可继续）
        if reply:
            self.register_assistant_utterance(reply)

        # 5) 计数 + emit
        if action == "yes" and kind == "thumbs_up":
            self.stats.triggered_thumbs_up_yes += 1
        elif action == "yes" and kind == "nod":
            self.stats.triggered_nod_yes += 1
        elif action == "no" and kind == "shake":
            self.stats.triggered_shake_no += 1

        self._emit_event(
            "interact.gesture_dialog_triggered",
            gesture_kind=kind,
            conv_state=self._read_state_name(),
            action=action,
            dialog_id=self._dialog_id(),
        )
        return action

    def _append_dialog(self, *, user_text: str, assistant_text: str, kind: str) -> None:
        dm = self.dialog_memory
        if dm is None:
            return
        try:
            # DialogMemory.append(user, assistant) — 二者都是 str；
            # 通过 user_text 前缀承载 "[手势:xxx]" tag
            dm.append(user_text or "", assistant_text or "")
        except Exception as e:  # noqa: BLE001
            log.warning("[gesture_dialog] dialog_memory.append failed: %s: %s",
                        type(e).__name__, e)

    def _record_shared_trigger(self, source: str) -> None:
        with self._lock:
            self._last_trigger_ts = self._clock()
        ps = self.proactive_scheduler
        if ps is not None:
            try:
                if hasattr(ps, "record_trigger"):
                    ps.record_trigger(source=source)
            except Exception as e:  # noqa: BLE001
                log.warning("[gesture_dialog] proactive.record_trigger failed: %s: %s",
                            type(e).__name__, e)

    def _emit_event(self, name: str, **fields: Any) -> None:
        emit_fn = self._emit
        if emit_fn is None:
            try:
                from coco.logging_setup import emit as _emit
                emit_fn = _emit
            except Exception:  # noqa: BLE001
                emit_fn = None
        if emit_fn is None:
            return
        try:
            emit_fn(name, component="interact", **fields)
        except Exception as e:  # noqa: BLE001
            log.warning("[gesture_dialog] emit failed: %s: %s", type(e).__name__, e)

    def _dialog_id(self) -> str:
        with self._lock:
            return f"gd-{int(self._last_trigger_ts * 1000)}"


__all__ = [
    "GestureDialogBridge",
    "GestureDialogConfig",
    "GestureDialogStats",
    "config_from_env",
    "gesture_dialog_enabled_from_env",
    "is_yes_no_question",
    "DEFAULT_AWAITING_WINDOW_S",
    "DEFAULT_COOLDOWN_S",
    "DEFAULT_PROACTIVE_PROMPT",
    "DEFAULT_THUMBS_UP_TEXT",
    "DEFAULT_NOD_TEXT",
    "DEFAULT_SHAKE_TEXT",
]
