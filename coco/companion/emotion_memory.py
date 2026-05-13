"""coco.companion.emotion_memory — companion-010 情绪记忆窗口 + 告警协调.

设计目标
========

把 ``EmotionTracker.record()`` 流出的每次强情绪样本入一个最近 N(=20) 轮的
滑窗 deque。当窗口内连续 sad 比例 ≥ 0.6 且至少有 K(=10) 条样本时，
emit ``companion.emotion_alert(kind='persistent_sad', ratio, window_size, ts)``
并通过 ``ProactiveScheduler.record_emotion_alert_trigger`` 触发一次主动安慰
话题（注入"安慰"类 prefer 一段时间，到期由 Coordinator 还原）。

alert 自带 30 分钟 cooldown（避免重复触发循环），不依赖 ProactiveScheduler
的 cooldown。告警事件由 ``EmotionAlertCoordinator`` 同时写入
``PersistentProfileStore.emotion_alerts``（append + cap），让跨会话也能跟进。

default-OFF
-----------

``COCO_EMO_MEMORY=1`` 才在 main.py 装配；本模块自身不读 env，便于复用 + verify
直构造。默认 OFF 行为：``main.py`` 不构造 ``EmotionAlertCoordinator``，
EmotionTracker 不绑定本模块 listener，行为与 companion-007 之前一致。

线程模型
--------

- ``EmotionMemoryWindow`` 内部 RLock；``on_emotion`` 任意线程调用。
- ``EmotionAlertCoordinator.on_emotion`` 跑在 EmotionTracker.record 的调用
  线程（通常是 InteractSession.process 一次性的对话线程）；alert 触发后的
  ProactiveScheduler / ProfileStore 调用都 fail-soft，绝不阻塞主对话流。

stats（用于 verify + 排障）
----------------------------

- samples_total：总入窗样本数
- alerts_triggered：累计 alert 次数
- alerts_per_kind：按 kind 拆分
- cooldown_skipped：cooldown 期内被压制的次数
"""

from __future__ import annotations

import collections
import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Tuple


log = logging.getLogger(__name__)


DEFAULT_WINDOW_SIZE = 20
DEFAULT_MIN_SAMPLES_K = 10
DEFAULT_RATIO_THRESHOLD = 0.6
DEFAULT_ALERT_COOLDOWN_S = 1800.0  # 30 min
DEFAULT_PREFER_DURATION_S = 600.0  # 安慰类 prefer 注入 10 min 后还原

# 安慰类 prefer 关键词权重（命中 ProactiveScheduler.select_topic_seed 的 candidate）
DEFAULT_COMFORT_PREFER: Dict[str, float] = {
    "安慰": 1.0,
    "陪伴": 0.9,
    "聊聊": 0.7,
    "低落": 0.6,
    "心情": 0.6,
}


# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------


def _bool_env(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def emotion_memory_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    """``COCO_EMO_MEMORY=1`` → 启用情绪记忆 + 告警；默认 OFF。"""
    e = env if env is not None else os.environ
    return _bool_env(e, "COCO_EMO_MEMORY", False)


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


@dataclass
class EmotionWindowStats:
    samples_total: int = 0
    alerts_triggered: int = 0
    alerts_per_kind: Dict[str, int] = field(default_factory=dict)
    cooldown_skipped: int = 0


@dataclass
class EmotionSample:
    emotion: str
    score: float
    ts: float


class EmotionMemoryWindow:
    """N 轮情绪滑窗 + 触发条件 + cooldown。

    线程安全：所有公开方法均通过内部 RLock 串行化。
    """

    def __init__(
        self,
        window_size: int = DEFAULT_WINDOW_SIZE,
        *,
        min_samples_k: int = DEFAULT_MIN_SAMPLES_K,
        ratio_threshold: float = DEFAULT_RATIO_THRESHOLD,
        alert_cooldown_s: float = DEFAULT_ALERT_COOLDOWN_S,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if window_size < 1:
            window_size = 1
        self.window_size = int(window_size)
        # K 不应大于窗口
        self.min_samples_k = max(1, min(int(min_samples_k), self.window_size))
        # ratio_threshold clamp 到 (0, 1]
        rt = float(ratio_threshold)
        if rt <= 0:
            rt = 0.01
        if rt > 1.0:
            rt = 1.0
        self.ratio_threshold = rt
        self.alert_cooldown_s = max(0.0, float(alert_cooldown_s))
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._window: Deque[EmotionSample] = collections.deque(maxlen=self.window_size)
        self._last_alert_ts: float = 0.0
        self.stats = EmotionWindowStats()

    # ------------------------------------------------------------------
    # 输入
    # ------------------------------------------------------------------

    def on_emotion(self, emotion: Any, score: float = 0.0,
                   ts: Optional[float] = None) -> None:
        """记一次情绪样本。emotion 接受 str 或 Emotion enum（取 .value）。"""
        if emotion is None:
            return
        name = getattr(emotion, "value", None) or str(emotion)
        name = str(name).strip()
        if not name:
            return
        t = float(ts) if ts is not None else float(self._clock())
        with self._lock:
            self._window.append(EmotionSample(emotion=name, score=float(score), ts=t))
            self.stats.samples_total += 1

    def reset(self) -> None:
        """清空窗口（保留 stats / last_alert_ts，便于"没事"快速 reset 后继续观察）。"""
        with self._lock:
            self._window.clear()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def size(self) -> int:
        with self._lock:
            return len(self._window)

    def ratio(self, emotion_name: str) -> float:
        """返回窗口内 emotion_name 的比例（0..1）；空窗口返回 0。"""
        if not emotion_name:
            return 0.0
        with self._lock:
            n = len(self._window)
            if n == 0:
                return 0.0
            hit = sum(1 for s in self._window if s.emotion == emotion_name)
            return hit / n

    def snapshot(self) -> List[EmotionSample]:
        with self._lock:
            return list(self._window)

    # ------------------------------------------------------------------
    # 触发判定
    # ------------------------------------------------------------------

    def should_alert(self, now: Optional[float] = None
                     ) -> Tuple[bool, Optional[str], float]:
        """检查是否应触发 alert。返回 (fire, kind, ratio)。

        当前规则：sad 比例 ≥ ratio_threshold 且窗口样本数 ≥ min_samples_k 且
        距上次 alert ≥ cooldown → ('persistent_sad')。
        """
        t = float(now) if now is not None else float(self._clock())
        with self._lock:
            n = len(self._window)
            if n < self.min_samples_k:
                return False, None, 0.0
            sad_hits = sum(1 for s in self._window if s.emotion == "sad")
            r = sad_hits / n
            if r < self.ratio_threshold:
                return False, None, r
            if self._last_alert_ts > 0 and (t - self._last_alert_ts) < self.alert_cooldown_s:
                self.stats.cooldown_skipped += 1
                return False, None, r
            return True, "persistent_sad", r

    def record_alert(self, kind: str, now: Optional[float] = None) -> None:
        """登记一次 alert：更新 last_alert_ts + stats。"""
        t = float(now) if now is not None else float(self._clock())
        with self._lock:
            self._last_alert_ts = t
            self.stats.alerts_triggered += 1
            self.stats.alerts_per_kind[kind] = self.stats.alerts_per_kind.get(kind, 0) + 1

    def last_alert_ts(self) -> float:
        with self._lock:
            return self._last_alert_ts


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


EmitFn = Callable[..., None]


@dataclass
class CoordinatorStats:
    listener_bound: bool = False
    prefer_bumps: int = 0
    prefer_restores: int = 0
    profile_alerts_written: int = 0


class EmotionAlertCoordinator:
    """把 EmotionTracker → EmotionMemoryWindow → ProactiveScheduler 串起来.

    - 注册自己为 ``EmotionTracker.add_listener``（companion-010 在 EmotionTracker
      上新增的 hook）。每次 record() 命中 → on_emotion 入窗 → 检查 should_alert
      → 触发则 emit + record_emotion_alert_trigger + bump prefer + 写 profile。
    - prefer bump：调用 ``ProactiveScheduler.set_topic_preferences(comfort_prefer)``；
      cooldown_s 后通过 ``_pending_restore`` 异步恢复原 prefer。restore 由调用方
      主动触发（``tick(now)``）；不起后台线程，verify 友好。
    - profile 写盘：仅当注入了 ``profile_store_fn``（返回 ``PersistentProfileStore`` +
      profile_id）时；写盘异常吞掉不阻塞。

    使用：

        coord = EmotionAlertCoordinator(window, proactive_scheduler=ps,
                                        emit_fn=_emit)
        coord.start(emotion_tracker)
        # ... runtime ...
        coord.stop()
    """

    def __init__(
        self,
        memory: EmotionMemoryWindow,
        *,
        proactive_scheduler: Any = None,
        emit_fn: Optional[EmitFn] = None,
        comfort_prefer: Optional[Mapping[str, float]] = None,
        prefer_duration_s: float = DEFAULT_PREFER_DURATION_S,
        profile_store_provider: Optional[Callable[[], Tuple[Any, str]]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.memory = memory
        self.proactive = proactive_scheduler
        self._emit_fn = emit_fn
        self.comfort_prefer: Dict[str, float] = dict(comfort_prefer or DEFAULT_COMFORT_PREFER)
        self.prefer_duration_s = max(0.0, float(prefer_duration_s))
        self._profile_store_provider = profile_store_provider
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._tracker: Any = None
        self._original_prefer: Optional[Dict[str, float]] = None
        self._restore_at: float = 0.0
        self.stats = CoordinatorStats()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self, emotion_tracker: Any) -> None:
        """绑定 EmotionTracker.add_listener；幂等。"""
        if emotion_tracker is None:
            log.warning("[emotion_memory] start() emotion_tracker=None — skip")
            return
        with self._lock:
            if self._tracker is not None:
                return
            add = getattr(emotion_tracker, "add_listener", None)
            if not callable(add):
                log.warning(
                    "[emotion_memory] EmotionTracker 无 add_listener；"
                    "Coordinator 无法启用（需 companion-010 patch）"
                )
                return
            add(self.on_emotion)
            self._tracker = emotion_tracker
            self.stats.listener_bound = True
        log.info(
            "[emotion_memory] coordinator started window=%d K=%d ratio=%.2f "
            "cooldown=%.0fs prefer_dur=%.0fs",
            self.memory.window_size, self.memory.min_samples_k,
            self.memory.ratio_threshold, self.memory.alert_cooldown_s,
            self.prefer_duration_s,
        )

    def stop(self) -> None:
        """解绑 listener + 还原 prefer（若仍 bump 中）；幂等。"""
        with self._lock:
            tr = self._tracker
            self._tracker = None
            if tr is not None:
                rm = getattr(tr, "remove_listener", None)
                if callable(rm):
                    try:
                        rm(self.on_emotion)
                    except Exception:  # noqa: BLE001
                        pass
            # 主动还原 prefer，避免 stop 后 ProactiveScheduler 继续保留安慰偏好
            if self._original_prefer is not None and self.proactive is not None:
                try:
                    self.proactive.set_topic_preferences(self._original_prefer)
                    self.stats.prefer_restores += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("[emotion_memory] restore prefer on stop failed: %s",
                                e)
            self._original_prefer = None
            self._restore_at = 0.0
            self.stats.listener_bound = False
        log.info("[emotion_memory] coordinator stopped")

    # ------------------------------------------------------------------
    # 输入回调（绑给 EmotionTracker.add_listener）
    # ------------------------------------------------------------------

    def on_emotion(self, emotion: Any, score: float = 0.0,
                   ts: Optional[float] = None) -> None:
        try:
            self.memory.on_emotion(emotion, score=score, ts=ts)
        except Exception as e:  # noqa: BLE001
            log.warning("[emotion_memory] on_emotion record failed: %s: %s",
                        type(e).__name__, e)
            return
        try:
            fire, kind, ratio = self.memory.should_alert(now=ts)
        except Exception as e:  # noqa: BLE001
            log.warning("[emotion_memory] should_alert failed: %s: %s",
                        type(e).__name__, e)
            return
        if fire and kind:
            self._trigger_alert(kind=kind, ratio=ratio, ts=ts)
        # 顺便 tick 一次 prefer 恢复
        self.tick(now=ts)

    # ------------------------------------------------------------------
    # tick — prefer 到期恢复
    # ------------------------------------------------------------------

    def tick(self, now: Optional[float] = None) -> None:
        """到期还原 prefer（main.py 也可在主循环里定期 tick；on_emotion 内部已 tick）。"""
        with self._lock:
            if self._original_prefer is None:
                return
            t = float(now) if now is not None else float(self._clock())
            if t < self._restore_at:
                return
            saved = self._original_prefer
            self._original_prefer = None
            self._restore_at = 0.0
        if self.proactive is not None:
            try:
                self.proactive.set_topic_preferences(saved)
                with self._lock:
                    self.stats.prefer_restores += 1
            except Exception as e:  # noqa: BLE001
                log.warning("[emotion_memory] restore prefer failed: %s: %s",
                            type(e).__name__, e)

    # ------------------------------------------------------------------
    # 触发分支
    # ------------------------------------------------------------------

    def _trigger_alert(self, kind: str, ratio: float, ts: Optional[float]) -> None:
        t = float(ts) if ts is not None else float(self._clock())
        # 1) 登记 cooldown
        try:
            self.memory.record_alert(kind=kind, now=t)
        except Exception:  # noqa: BLE001
            pass
        # 2) emit 业务事件
        try:
            emit_fn = self._emit_fn
            if emit_fn is None:
                from coco.logging_setup import emit as _emit
                emit_fn = _emit
            emit_fn(
                "companion.emotion_alert",
                kind=kind,
                ratio=float(ratio),
                window_size=int(self.memory.size()),
                ts=float(t),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[emotion_memory] emit emotion_alert failed: %s: %s",
                        type(e).__name__, e)
        # 3) 通知 ProactiveScheduler
        if self.proactive is not None:
            try:
                self.proactive.record_emotion_alert_trigger(
                    kind=kind, ratio=ratio, window_size=self.memory.size(),
                )
            except Exception as e:  # noqa: BLE001
                log.warning("[emotion_memory] proactive record failed: %s: %s",
                            type(e).__name__, e)
            # 4) bump 安慰类 prefer（保留原 prefer 用于到期还原）
            self._bump_comfort_prefer(now=t)
        # 5) 写 profile
        self._append_profile_alert(kind=kind, ratio=ratio, ts=t)

    def _bump_comfort_prefer(self, now: float) -> None:
        if self.proactive is None:
            return
        get = getattr(self.proactive, "get_topic_preferences", None)
        sett = getattr(self.proactive, "set_topic_preferences", None)
        if not callable(sett):
            return
        with self._lock:
            # 首次 bump：保存原 prefer
            if self._original_prefer is None and callable(get):
                try:
                    self._original_prefer = dict(get() or {})
                except Exception:  # noqa: BLE001
                    self._original_prefer = {}
            # 合并：comfort 优先，但不抹去用户原偏好（取 max）
            merged: Dict[str, float] = dict(self._original_prefer or {})
            for k, w in self.comfort_prefer.items():
                merged[k] = max(merged.get(k, 0.0), float(w))
            self._restore_at = now + self.prefer_duration_s
            self.stats.prefer_bumps += 1
        try:
            sett(merged)
        except Exception as e:  # noqa: BLE001
            log.warning("[emotion_memory] bump prefer failed: %s: %s",
                        type(e).__name__, e)

    def _append_profile_alert(self, kind: str, ratio: float, ts: float) -> None:
        provider = self._profile_store_provider
        if provider is None:
            return
        try:
            store, pid = provider()
        except Exception as e:  # noqa: BLE001
            log.warning("[emotion_memory] profile_store_provider failed: %s: %s",
                        type(e).__name__, e)
            return
        if store is None or not pid:
            return
        try:
            rec = store.load(pid)
            if rec is None:
                return
            alerts = list(getattr(rec, "emotion_alerts", []) or [])
            alerts.append({"kind": str(kind), "ts": float(ts), "ratio": float(ratio)})
            # cap 100 条
            if len(alerts) > 100:
                alerts = alerts[-100:]
            rec.emotion_alerts = alerts
            rec.updated_ts = float(ts)
            store.save(rec)
            with self._lock:
                self.stats.profile_alerts_written += 1
        except Exception as e:  # noqa: BLE001
            log.warning("[emotion_memory] append profile alert failed: %s: %s",
                        type(e).__name__, e)


__all__ = [
    "DEFAULT_WINDOW_SIZE",
    "DEFAULT_MIN_SAMPLES_K",
    "DEFAULT_RATIO_THRESHOLD",
    "DEFAULT_ALERT_COOLDOWN_S",
    "DEFAULT_PREFER_DURATION_S",
    "DEFAULT_COMFORT_PREFER",
    "EmotionSample",
    "EmotionWindowStats",
    "EmotionMemoryWindow",
    "CoordinatorStats",
    "EmotionAlertCoordinator",
    "emotion_memory_enabled_from_env",
]
