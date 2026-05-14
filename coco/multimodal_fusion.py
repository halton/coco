"""coco.multimodal_fusion — vision-007 多模态主动话题融合.

设计目标
========

把 vision-006 的 ``SceneCaption``、ASR partial/final 与
``ProactiveScheduler`` 串成一条主动闭环：

- **R1 dark_silence**：scene caption 含『暗 / 黑 / 夜』等关键词 + 最近
  ``silence_window_s`` 秒无 ASR final + interact 处于 IDLE → 主动开口候选
  （hint：『要不要开灯？』）。
- **R2 motion_greet**：scene caption 含『移动 / 在左侧 / 在右侧 / 在中央』
  等关键词 + 最近 ``idle_window_s`` 秒无任何用户交互 → 主动打招呼候选。

被触发后：

1. 写每条规则独立 cooldown（默认 300s）+ 全局 max ``rate_limit_per_min``
   触发（默认 1/min），防刷屏；
2. 写 ``ProactiveScheduler._next_priority_boost``（如有该字段；vision-007
   不强求 scheduler 支持，仅写一份记账即可）；
3. 调 ``ProactiveScheduler.record_multimodal_trigger(rule_id, hint)``
   做共享 cooldown 记账（与 vision-006 record_caption_trigger 同模式）；
4. emit ``proactive.multimodal_triggered``（component='proactive'）。

设计原则
========

- **default-OFF**：仅在 ``COCO_MM_PROACTIVE=1`` 且 SceneCaption + Proactive
  都启用时 main.py 才构造本类；未启用即零开销。
- **不直接调 LLM/TTS**：只发记账信号给 ProactiveScheduler；保持单一调度入口。
- **fail-soft**：任何子调用异常不抛，仅写 stats.errors。
- **线程安全**：所有公共方法 RLock 保护。
- **ASR 解耦**：不强依赖某个 ASR 后端的 emit 事件；通过显式 API
  ``on_asr_event(kind, text)`` 注入，main.py 在有 ASR 回调时挂一份，
  没有则 fall back 到 idle_time（仅 R2 motion_greet 可用）。

vision-007 / phase-9。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Optional


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


DEFAULT_SILENCE_WINDOW_S = 60.0
DEFAULT_IDLE_WINDOW_S = 120.0
DEFAULT_RULE_COOLDOWN_S = 300.0
DEFAULT_RATE_LIMIT_PER_MIN = 1


# R1 dark_silence —— caption 含『暗 / 黑 / 夜』
_DARK_KEYWORDS = ("暗", "黑", "夜")

# R2 motion_greet —— caption 含『移动』或方位词
_MOTION_KEYWORDS = ("移动", "在左侧", "在右侧", "在中央", "靠近", "经过")


@dataclass(frozen=True)
class MultimodalFusionConfig:
    enabled: bool = False
    silence_window_s: float = DEFAULT_SILENCE_WINDOW_S
    idle_window_s: float = DEFAULT_IDLE_WINDOW_S
    rule_cooldown_s: float = DEFAULT_RULE_COOLDOWN_S
    rate_limit_per_min: int = DEFAULT_RATE_LIMIT_PER_MIN


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def _bool_env(key: str, default: bool = False) -> bool:
    raw = (os.environ.get(key) or "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


def _float_env(key: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        log.warning("[mm_fusion] %s=%r 非数字，回退默认 %.2f", key, raw, default)
        return default
    return max(lo, min(hi, v))


def _int_env(key: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        log.warning("[mm_fusion] %s=%r 非整数，回退默认 %d", key, raw, default)
        return default
    return max(lo, min(hi, v))


def mm_proactive_enabled_from_env() -> bool:
    return _bool_env("COCO_MM_PROACTIVE", default=False)


def config_from_env() -> MultimodalFusionConfig:
    return MultimodalFusionConfig(
        enabled=mm_proactive_enabled_from_env(),
        silence_window_s=_float_env(
            "COCO_MM_SILENCE_WINDOW_S", DEFAULT_SILENCE_WINDOW_S, 1.0, 3600.0,
        ),
        idle_window_s=_float_env(
            "COCO_MM_IDLE_WINDOW_S", DEFAULT_IDLE_WINDOW_S, 1.0, 3600.0,
        ),
        rule_cooldown_s=_float_env(
            "COCO_MM_RULE_COOLDOWN_S", DEFAULT_RULE_COOLDOWN_S, 0.0, 7200.0,
        ),
        rate_limit_per_min=_int_env(
            "COCO_MM_RATE_LIMIT_PER_MIN", DEFAULT_RATE_LIMIT_PER_MIN, 1, 60,
        ),
    )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class MultimodalFusionStats:
    captions_seen: int = 0
    asr_partial_seen: int = 0
    asr_final_seen: int = 0
    triggered_total: int = 0
    cooldown_skipped: int = 0
    rate_limit_skipped: int = 0
    state_skipped: int = 0  # interact state != IDLE 之类
    errors: int = 0
    per_rule: Dict[str, int] = field(default_factory=dict)
    priority_boost_count: int = 0
    last_rule_id: str = ""
    last_trigger_ts: float = 0.0
    history: Deque[str] = field(default_factory=lambda: deque(maxlen=100))


# ---------------------------------------------------------------------------
# MultimodalFusion
# ---------------------------------------------------------------------------


class MultimodalFusion:
    """多模态融合规则引擎（vision-007）.

    生命周期：构造 → ``on_scene_caption`` / ``on_asr_event``
    / ``on_interact_state`` 被外部回调钩进 → 不需要独立线程（事件驱动）。
    """

    # 默认状态值——main.py 未注入 interact_state 时按 IDLE 处理
    STATE_IDLE = "IDLE"
    STATE_SPEAKING = "SPEAKING"
    STATE_AWAITING = "AWAITING_RESPONSE"

    def __init__(
        self,
        *,
        config: Optional[MultimodalFusionConfig] = None,
        proactive: Any = None,
        clock: Optional[Callable[[], float]] = None,
        emit_fn: Optional[Callable[..., None]] = None,
    ) -> None:
        self.config = config or MultimodalFusionConfig()
        self.proactive = proactive
        self.clock = clock or time.monotonic
        self._emit = emit_fn

        self._lock = threading.RLock()
        self.stats = MultimodalFusionStats()

        # 每条规则独立 cooldown 记账：rule_id → last_trigger_ts
        self._rule_last_ts: Dict[str, float] = {}
        # 全局 rate limit：最近 60s 内触发时间戳
        self._recent_triggers: Deque[float] = deque()

        # 输入信号最新状态
        self._last_caption_text: str = ""
        self._last_caption_ts: float = 0.0
        self._last_asr_partial_ts: float = 0.0
        self._last_asr_final_ts: float = 0.0
        # 最近一次"用户交互"时间（partial / final / interact_state 切回 IDLE 之外）
        self._last_user_activity_ts: float = self.clock()
        self._interact_state: str = self.STATE_IDLE

    # ------------------------------------------------------------------
    # 输入回调
    # ------------------------------------------------------------------

    def on_scene_caption(self, text: str, meta: Optional[Dict[str, Any]] = None) -> None:
        """SceneCaptionEmitter 命中后调入。

        在 caption 命中时刻评估两条规则；规则命中即记账 + 通知 proactive。
        """
        if not self.config.enabled:
            return
        try:
            with self._lock:
                self.stats.captions_seen += 1
                t = self.clock()
                self._last_caption_text = text or ""
                self._last_caption_ts = t

                # 评估两条规则
                fired = self._eval_rules_unlocked(t, text or "", meta or {})
                if fired:
                    return
        except Exception as e:  # noqa: BLE001
            self.stats.errors += 1
            log.warning("[mm_fusion] on_scene_caption failed: %s: %s", type(e).__name__, e)

    def on_asr_event(self, kind: str, text: str = "") -> None:
        """ASR partial / final 事件回调.

        kind: 'partial' / 'final' / 其他视为 'partial'。
        """
        if not self.config.enabled:
            return
        try:
            with self._lock:
                t = self.clock()
                k = (kind or "").strip().lower()
                if k == "final":
                    self.stats.asr_final_seen += 1
                    self._last_asr_final_ts = t
                else:
                    self.stats.asr_partial_seen += 1
                    self._last_asr_partial_ts = t
                self._last_user_activity_ts = t
        except Exception as e:  # noqa: BLE001
            self.stats.errors += 1
            log.warning("[mm_fusion] on_asr_event failed: %s: %s", type(e).__name__, e)

    def on_interact_state(self, state: str) -> None:
        """interact 状态机回调；用于规则中『仅 IDLE 时触发』。"""
        if not self.config.enabled:
            return
        try:
            with self._lock:
                self._interact_state = (state or self.STATE_IDLE).upper()
                # 用户开口 / 等待响应都视为交互发生
                if self._interact_state in (self.STATE_SPEAKING, self.STATE_AWAITING):
                    self._last_user_activity_ts = self.clock()
        except Exception as e:  # noqa: BLE001
            self.stats.errors += 1
            log.warning("[mm_fusion] on_interact_state failed: %s: %s", type(e).__name__, e)

    # ------------------------------------------------------------------
    # 测试用注入 API
    # ------------------------------------------------------------------

    def inject_asr_event(self, kind: str, text: str = "") -> None:
        """test-only 别名，等价 on_asr_event。

        .. deprecated:: infra-009
           直接调 ``on_asr_event``；本别名仅为兼容旧 verify，下一个 phase 会移除。
        """
        import warnings as _w
        _w.warn(
            "MultimodalFusion.inject_asr_event is deprecated; use on_asr_event instead",
            DeprecationWarning,
            stacklevel=2,
        )
        self.on_asr_event(kind, text)

    def inject_user_activity(self, ts: Optional[float] = None) -> None:
        """test-only：显式设置 last_user_activity_ts。"""
        with self._lock:
            self._last_user_activity_ts = ts if ts is not None else self.clock()

    # ------------------------------------------------------------------
    # 规则
    # ------------------------------------------------------------------

    def _eval_rules_unlocked(self, t: float, text: str, meta: Dict[str, Any]) -> bool:
        """评估所有规则；命中第一条即返回 True。锁内调用。"""
        # R1 dark_silence
        if self._matches_keywords(text, _DARK_KEYWORDS):
            silence_for = t - max(self._last_asr_final_ts, self._last_asr_partial_ts)
            if (
                self._last_asr_final_ts <= 0
                and self._last_asr_partial_ts <= 0
            ):
                silence_for = t  # 启动到现在的窗口
            if silence_for >= self.config.silence_window_s and self._interact_state == self.STATE_IDLE:
                if self._try_fire_unlocked(t, "dark_silence", "要不要开灯？"):
                    return True
            elif self._interact_state != self.STATE_IDLE:
                self.stats.state_skipped += 1

        # R2 motion_greet
        if self._matches_keywords(text, _MOTION_KEYWORDS):
            idle_for = t - self._last_user_activity_ts
            if idle_for >= self.config.idle_window_s and self._interact_state == self.STATE_IDLE:
                if self._try_fire_unlocked(t, "motion_greet", "看到你在那边，要不要聊聊？"):
                    return True
            elif self._interact_state != self.STATE_IDLE:
                self.stats.state_skipped += 1

        return False

    @staticmethod
    def _matches_keywords(text: str, keywords) -> bool:
        if not text:
            return False
        for kw in keywords:
            if kw in text:
                return True
        return False

    def _try_fire_unlocked(self, t: float, rule_id: str, hint: str) -> bool:
        """尝试触发；命中 cooldown / 限速则 skip。锁内调用。"""
        # 规则级 cooldown
        last = self._rule_last_ts.get(rule_id, 0.0)
        if last > 0 and (t - last) < self.config.rule_cooldown_s:
            self.stats.cooldown_skipped += 1
            return False

        # 全局 rate limit（最近 60s）
        cutoff = t - 60.0
        while self._recent_triggers and self._recent_triggers[0] < cutoff:
            self._recent_triggers.popleft()
        if len(self._recent_triggers) >= self.config.rate_limit_per_min:
            self.stats.rate_limit_skipped += 1
            return False

        # 通过 → 触发
        self._rule_last_ts[rule_id] = t
        self._recent_triggers.append(t)
        self.stats.triggered_total += 1
        self.stats.per_rule[rule_id] = self.stats.per_rule.get(rule_id, 0) + 1
        self.stats.last_rule_id = rule_id
        self.stats.last_trigger_ts = t
        self.stats.history.append(f"@{t:.2f}: {rule_id}:{hint[:40]}")

        # 通知 proactive（记账）
        if self.proactive is not None:
            # record_multimodal_trigger（vision-007 新增）
            try:
                rec = getattr(self.proactive, "record_multimodal_trigger", None)
                if rec is not None:
                    rec(rule_id, hint)
            except Exception as e:  # noqa: BLE001
                self.stats.errors += 1
                log.warning("[mm_fusion] proactive.record_multimodal_trigger failed: %s: %s", type(e).__name__, e)

            # interact-012: 若 COCO_MM_PROACTIVE_LLM=1 且 scheduler 支持 set_mm_llm_context，
            # 把场景上下文塞过去；下一次 maybe_trigger 命中时由 _build_mm_system_prompt_unlocked
            # 注入专用 prompt。default-OFF：env 未设 → 直接跳过；env=ON 但 scheduler 无该方法 →
            # 同样跳过（保持向后兼容）。
            try:
                if _bool_env("COCO_MM_PROACTIVE_LLM", default=False):
                    setter = getattr(self.proactive, "set_mm_llm_context", None)
                    if setter is not None:
                        ctx = {
                            "rule_id": rule_id,
                            "hint": hint,
                            "caption": self._last_caption_text,
                            "ts": t,
                        }
                        setter(ctx)
            except Exception as e:  # noqa: BLE001
                self.stats.errors += 1
                log.warning("[mm_fusion] set_mm_llm_context failed: %s: %s", type(e).__name__, e)

            # priority boost（如果 scheduler 提供该字段；否则只本地计数）
            try:
                if hasattr(self.proactive, "_next_priority_boost"):
                    self.proactive._next_priority_boost = True  # noqa: SLF001
                self.stats.priority_boost_count += 1
            except Exception as e:  # noqa: BLE001
                self.stats.errors += 1
                log.warning("[mm_fusion] priority_boost failed: %s: %s", type(e).__name__, e)

        # emit
        try:
            emit_fn = self._emit
            if emit_fn is None:
                from coco.logging_setup import emit as _emit
                emit_fn = _emit
            emit_fn(
                "proactive.multimodal_triggered",
                rule_id=rule_id,
                hint=hint[:200],
                subtype=f"mm_{rule_id}",
            )
        except Exception as e:  # noqa: BLE001
            self.stats.errors += 1
            log.warning("[mm_fusion] emit failed: %s: %s", type(e).__name__, e)

        log.info("[mm_fusion] triggered rule=%s hint=%r", rule_id, hint[:60])
        return True


__all__ = [
    "MultimodalFusion",
    "MultimodalFusionConfig",
    "MultimodalFusionStats",
    "config_from_env",
    "mm_proactive_enabled_from_env",
    "DEFAULT_SILENCE_WINDOW_S",
    "DEFAULT_IDLE_WINDOW_S",
    "DEFAULT_RULE_COOLDOWN_S",
    "DEFAULT_RATE_LIMIT_PER_MIN",
]
