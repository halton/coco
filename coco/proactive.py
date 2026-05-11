"""coco.proactive — 主动话题发起 (interact-007).

设计目标
========

ACTIVE 状态下，机器人看到人脸 + 一段时间没人说话时，自动发起一个轻量话题，
用 UserProfile 注入 system_prompt 偏向用户兴趣/学习目标，让陪伴感不只是被动应答。

触发条件（必须 ALL 满足才发）
----------------------------

1. ``COCO_PROACTIVE=1`` 启用（默认 OFF，向后兼容 phase-3/4 全部测试不变）
2. ``power_state.current_state == ACTIVE``（DROWSY/SLEEP 不主动）
3. ``face_tracker.latest().present`` 为 True（视野里有人）
4. 距上次 ``InteractSession`` 交互（``on_interaction``）已超过 ``idle_threshold_s``
5. 距上次主动话题已超过 ``cooldown_s``
6. 最近 1h 内主动话题次数 < ``max_topics_per_hour``

环境变量
--------

- ``COCO_PROACTIVE``: 主开关，默认 OFF
- ``COCO_PROACTIVE_IDLE_S``: 触发阈值，默认 60.0，clamp [10, 3600]
- ``COCO_PROACTIVE_COOLDOWN_S``: 主动话题间冷却，默认 180.0，clamp [10, 7200]
- ``COCO_PROACTIVE_MAX_PER_HOUR``: 限流，默认 10，clamp [1, 60]
- ``COCO_PROACTIVE_TICK_S``: scheduler 心跳，默认 1.0

线程模型
--------

- ``ProactiveScheduler.start(stop_event)``: 起一个 daemon 线程，``tick_s`` 周期检查
- 触发后调用 ``llm_client.reply(prompt, system_prompt=...)`` + ``tts_say_fn(text)``
- LLM/TTS 异常一律吞掉（fail-soft），不让线程崩；写 stats.errors
- 触发后调 ``on_proactive(reply)`` 钩子（默认指向 ``power_state.record_interaction``）
  避免主动话题刚发完又被自己当 idle 立即重发

不破坏 default-OFF
------------------

- ``COCO_PROACTIVE=0``（默认）→ ``main.py`` 不构造 scheduler，零开销
- 构造后即使没注入 face_tracker / power_state，``_should_trigger`` 也会因约束不满足
  返回 False，不会乱发
"""

from __future__ import annotations

import collections
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, List, Optional


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults / config
# ---------------------------------------------------------------------------


DEFAULT_IDLE_S = 60.0
DEFAULT_COOLDOWN_S = 180.0
DEFAULT_MAX_PER_HOUR = 10
DEFAULT_TICK_S = 1.0

# 用于 LLM 提示的"prompt"种子；真正人格/兴趣由 system_prompt 注入
DEFAULT_TOPIC_SEED = "用一句温柔好奇的话主动开个话题，可以问对方在做什么或聊一个轻松的小事。"


@dataclass(frozen=True)
class ProactiveConfig:
    enabled: bool = False
    idle_threshold_s: float = DEFAULT_IDLE_S
    cooldown_s: float = DEFAULT_COOLDOWN_S
    max_topics_per_hour: int = DEFAULT_MAX_PER_HOUR
    tick_s: float = DEFAULT_TICK_S
    topic_seed: str = DEFAULT_TOPIC_SEED


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class ProactiveStats:
    ticks: int = 0
    triggered: int = 0
    skipped_disabled: int = 0
    skipped_power: int = 0
    skipped_no_face: int = 0
    skipped_idle: int = 0
    skipped_cooldown: int = 0
    skipped_rate_limit: int = 0
    llm_errors: int = 0
    tts_errors: int = 0
    last_topic: str = ""
    last_topic_ts: float = 0.0
    # interact-007 L2: history 用 deque(maxlen=200)，避免长跑会话内存无界增长
    history: Deque[str] = field(default_factory=lambda: collections.deque(maxlen=200))


# ---------------------------------------------------------------------------
# Env
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
        log.warning("[proactive] %s=%r 非数字，回退默认 %.2f", key, raw, default)
        return default
    if v < lo:
        log.warning("[proactive] %s=%.2f <%.2f，clamp 到 %.2f", key, v, lo, lo)
        return lo
    if v > hi:
        log.warning("[proactive] %s=%.2f >%.2f，clamp 到 %.2f", key, v, hi, hi)
        return hi
    return v


def _int_env(key: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        log.warning("[proactive] %s=%r 非整数，回退默认 %d", key, raw, default)
        return default
    if v < lo:
        log.warning("[proactive] %s=%d <%d，clamp 到 %d", key, v, lo, lo)
        return lo
    if v > hi:
        log.warning("[proactive] %s=%d >%d，clamp 到 %d", key, v, hi, hi)
        return hi
    return v


def proactive_enabled_from_env() -> bool:
    return _bool_env("COCO_PROACTIVE", default=False)


def config_from_env() -> ProactiveConfig:
    return ProactiveConfig(
        enabled=proactive_enabled_from_env(),
        idle_threshold_s=_float_env("COCO_PROACTIVE_IDLE_S", DEFAULT_IDLE_S, 10.0, 3600.0),
        cooldown_s=_float_env("COCO_PROACTIVE_COOLDOWN_S", DEFAULT_COOLDOWN_S, 10.0, 7200.0),
        max_topics_per_hour=_int_env("COCO_PROACTIVE_MAX_PER_HOUR", DEFAULT_MAX_PER_HOUR, 1, 60),
        tick_s=_float_env("COCO_PROACTIVE_TICK_S", DEFAULT_TICK_S, 0.1, 30.0),
    )


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class ProactiveScheduler:
    """主动话题调度器。

    依赖（全部 optional，缺一即 _should_trigger=False）：

    - power_state: PowerStateMachine（current_state==ACTIVE 才触发）
    - face_tracker: 提供 ``.latest()`` 返回有 ``.present`` 字段的对象
    - llm_reply_fn: ``(text, *, system_prompt=None) -> str`` —— 通常是
      ``llm_client.reply``；不接受 system_prompt 时本类自动退化
    - tts_say_fn: ``(text, blocking=True) -> None``
    - profile_store: 可选；有则 ``build_system_prompt(profile)`` 注入
    - on_interaction: 触发后调用，统一记账（默认指向 power_state.record_interaction）
    - clock: 时间源，便于 fake clock 测试
    """

    def __init__(
        self,
        *,
        config: Optional[ProactiveConfig] = None,
        power_state: Any = None,
        face_tracker: Any = None,
        llm_reply_fn: Optional[Callable[..., str]] = None,
        tts_say_fn: Optional[Callable[..., None]] = None,
        profile_store: Any = None,
        on_interaction: Optional[Callable[[str], None]] = None,
        clock: Optional[Callable[[], float]] = None,
        emit_fn: Optional[Callable[..., None]] = None,
    ) -> None:
        self.config = config or ProactiveConfig()
        self.power_state = power_state
        self.face_tracker = face_tracker
        self.llm_reply_fn = llm_reply_fn
        self.tts_say_fn = tts_say_fn
        self.profile_store = profile_store
        self.on_interaction = on_interaction
        self.clock = clock or time.monotonic
        self._emit = emit_fn  # 由测试注入；None 时延迟 import logging_setup.emit
        self._lock = threading.RLock()
        self.stats = ProactiveStats()

        # last_interaction_ts：从 InteractSession 钩进来；初始化为"刚启动"，
        # 让 idle_threshold 从 start 时刻起算（避免一上来就秒发）。
        self._last_interaction_ts: float = self.clock()
        self._last_proactive_ts: float = 0.0
        # 最近 1h 触发时间戳队列（用于 max_per_hour 限流）
        self._recent_triggers: Deque[float] = collections.deque()

        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None

        # 探测 llm_reply_fn 是否接受 system_prompt
        self._llm_accepts_system_prompt = self._probe_kwarg(llm_reply_fn, "system_prompt")

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    @staticmethod
    def _probe_kwarg(fn: Optional[Callable[..., Any]], name: str) -> bool:
        if fn is None:
            return False
        import inspect
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

    def record_interaction(self, source: str = "interact") -> None:
        """挂到 InteractSession.on_interaction：每次交互重置 idle 计时。"""
        with self._lock:
            self._last_interaction_ts = self.clock()

    def start(self, stop_event: threading.Event) -> None:
        if self._thread is not None and self._thread.is_alive():
            log.warning("[proactive] scheduler already running")
            return
        self._stop_event = stop_event
        self._thread = threading.Thread(
            target=self._loop,
            name="coco-proactive",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "[proactive] scheduler started cfg=idle=%.1fs cooldown=%.1fs max/h=%d tick=%.1fs",
            self.config.idle_threshold_s, self.config.cooldown_s,
            self.config.max_topics_per_hour, self.config.tick_s,
        )

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # 触发逻辑（同步，便于 verify 直调）
    # ------------------------------------------------------------------

    def _should_trigger(self, now: Optional[float] = None) -> Optional[str]:
        """检查是否该触发；返回 None 表示触发，否则返回 skip 原因字符串。"""
        if not self.config.enabled:
            return "disabled"
        t = now if now is not None else self.clock()
        # 1) power state must be ACTIVE
        if self.power_state is not None:
            try:
                from coco.power_state import PowerState as _PS
                if self.power_state.current_state != _PS.ACTIVE:
                    return "power"
            except Exception as e:  # noqa: BLE001
                log.warning("[proactive] power_state read failed: %s: %s", type(e).__name__, e)
                return "power"
        # 2) face presence
        if self.face_tracker is not None:
            try:
                snap = self.face_tracker.latest()
                if not bool(getattr(snap, "present", False)):
                    return "no_face"
            except Exception as e:  # noqa: BLE001
                log.warning("[proactive] face_tracker read failed: %s: %s", type(e).__name__, e)
                return "no_face"
        else:
            # 没注入 face_tracker → 必然不发（保护性默认）
            return "no_face"
        # 3) idle threshold
        idle_for = max(0.0, t - self._last_interaction_ts)
        if idle_for < self.config.idle_threshold_s:
            return "idle"
        # 4) cooldown since last proactive
        if self._last_proactive_ts > 0:
            since = max(0.0, t - self._last_proactive_ts)
            if since < self.config.cooldown_s:
                return "cooldown"
        # 5) rate limit
        # 清理 1h 之外的旧条目
        cutoff = t - 3600.0
        while self._recent_triggers and self._recent_triggers[0] < cutoff:
            self._recent_triggers.popleft()
        if len(self._recent_triggers) >= self.config.max_topics_per_hour:
            return "rate_limit"
        return None

    def maybe_trigger(self, now: Optional[float] = None) -> bool:
        """同步检查并触发；返回是否触发了一次主动话题。

        verify 路径直接调；scheduler 线程也调它（共享路径，避免行为漂移）。

        interact-007 L1-2: 锁的作用域从"全程"收缩到"判定 + 抢占式预占"，
        实际 LLM/TTS（耗时数秒）在锁外执行，避免阻塞 InteractSession.record_interaction
        刷新 _last_interaction_ts。
        """
        # ---- 锁内：判定 + 抢占式预占（fail-soft：不回滚，宁少发也不连发）----
        with self._lock:
            self.stats.ticks += 1
            t = now if now is not None else self.clock()
            reason = self._should_trigger(now=t)
            if reason is not None:
                key = f"skipped_{reason}"
                if hasattr(self.stats, key):
                    setattr(self.stats, key, getattr(self.stats, key) + 1)
                return False
            # 抢占式预占：先把 last_proactive_ts / last_interaction_ts / recent_triggers
            # 写好，再放锁；这样 LLM/TTS 期间外部线程读到的"已发"，不会被同 tick 重复触发。
            self._last_proactive_ts = t
            self._last_interaction_ts = t
            self._recent_triggers.append(t)
            self.stats.triggered += 1
            system_prompt = self._build_system_prompt()
            seed = self.config.topic_seed
        # ---- 锁外：实际 LLM + TTS + emit + on_interaction（耗时操作）----
        self._do_trigger_unlocked(t, system_prompt=system_prompt, seed=seed)
        return True

    def _do_trigger_unlocked(self, t: float, *, system_prompt: Optional[str], seed: str) -> None:
        """实际触发的耗时段：LLM → TTS → emit → on_interaction。锁外执行。

        失败时**不回滚**预占（fail-soft）：即使 LLM/TTS 都失败，也宁可少一次主动话题，
        也不冒"重新放锁后立即重发"的风险。仅 emit 一个 proactive_topic_failed 事件
        让上层可观测。
        """
        topic_text = ""
        # 1) LLM
        if self.llm_reply_fn is not None:
            try:
                if self._llm_accepts_system_prompt and system_prompt is not None:
                    topic_text = self.llm_reply_fn(seed, system_prompt=system_prompt)
                else:
                    topic_text = self.llm_reply_fn(seed)
                topic_text = (topic_text or "").strip()
            except Exception as e:  # noqa: BLE001
                self.stats.llm_errors += 1
                log.warning("[proactive] llm_reply_fn failed: %s: %s", type(e).__name__, e)
                topic_text = ""
        if not topic_text:
            # fail-soft：用一句兜底，仍走 TTS（保证"主动开口"这件事 happen）
            topic_text = "我们聊点什么吧？"

        # 2) TTS
        tts_ok = True
        if self.tts_say_fn is not None:
            try:
                self.tts_say_fn(topic_text, blocking=True)
            except Exception as e:  # noqa: BLE001
                tts_ok = False
                self.stats.tts_errors += 1
                log.warning("[proactive] tts_say_fn failed: %s: %s", type(e).__name__, e)

        # 3) 记 last_topic / history（锁内已经预占了 ts/triggered/recent_triggers）
        self.stats.last_topic = topic_text
        self.stats.last_topic_ts = t
        self.stats.history.append(f"@{t:.2f}: {topic_text[:60]}")

        # 4) on_interaction 钩子（默认走 power_state.record_interaction）
        if self.on_interaction is not None:
            try:
                self.on_interaction("proactive")
            except Exception as e:  # noqa: BLE001
                log.warning("[proactive] on_interaction failed: %s: %s", type(e).__name__, e)

        # 5) emit event
        try:
            emit_fn = self._emit
            if emit_fn is None:
                from coco.logging_setup import emit as _emit
                emit_fn = _emit
            # interact-007 L2: 去掉 informational 但语义无效的 idle_for 字段
            emit_fn(
                "interact.proactive_topic",
                topic=topic_text[:200],
                source="scheduler",
            )
            if not tts_ok or self.stats.llm_errors > 0:
                # 仅记一次失败事件（不影响计数已经预占的 triggered）
                emit_fn(
                    "interact.proactive_topic_failed",
                    topic=topic_text[:200],
                    llm_errors=int(self.stats.llm_errors),
                    tts_errors=int(self.stats.tts_errors),
                )
        except Exception as e:  # noqa: BLE001
            log.warning("[proactive] emit failed: %s: %s", type(e).__name__, e)

        log.info("[proactive] triggered topic=%r", topic_text[:80])

    def _build_system_prompt(self) -> Optional[str]:
        if self.profile_store is None:
            return None
        try:
            from coco.profile import build_system_prompt
            from coco.llm import SYSTEM_PROMPT as _BASE
            prof = self.profile_store.load()
            return build_system_prompt(prof, base=_BASE)
        except Exception as e:  # noqa: BLE001
            log.warning("[proactive] build_system_prompt failed: %s: %s", type(e).__name__, e)
            return None

    # ------------------------------------------------------------------
    # 后台线程
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        assert self._stop_event is not None
        ev = self._stop_event
        try:
            while not ev.wait(timeout=self.config.tick_s):
                try:
                    self.maybe_trigger()
                except Exception as e:  # noqa: BLE001
                    log.warning("[proactive] tick error: %s", e)
        finally:
            log.info("[proactive] scheduler stopped stats=%s", self.stats)


__all__ = [
    "ProactiveConfig",
    "ProactiveScheduler",
    "ProactiveStats",
    "config_from_env",
    "proactive_enabled_from_env",
    "DEFAULT_IDLE_S",
    "DEFAULT_COOLDOWN_S",
    "DEFAULT_MAX_PER_HOUR",
    "DEFAULT_TICK_S",
    "DEFAULT_TOPIC_SEED",
]
