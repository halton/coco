"""coco.power_state — 节能 idle 状态机（companion-003）.

设计目标
--------
长时无交互时让 Coco 自动降级，停掉持续 idle micro 减少电机磨损/发热；
有交互（wake-word / face-seen / interact）立刻 wake_up 回到 active。

三态 FSM
--------
- ACTIVE：默认状态；IdleAnimator 用默认 interval 跑 micro/glance
- DROWSY：最近 ``drowsy_after`` 秒无活动；IdleAnimator interval ×
          ``drowsy_micro_scale``（默认 2.0），频率减半但还在动
- SLEEP：最近 ``sleep_after`` 秒无活动；调 ``robot.goto_sleep()``、
        IdleAnimator pause()，仅保留 wake-word 监听（在 main.py 路径）

转移规则
--------
- ACTIVE  → DROWSY: idle_for >= drowsy_after
- DROWSY  → SLEEP : idle_for >= sleep_after
- 任意状态 → ACTIVE: ``record_interaction()`` 被调用（wake-word / face / interact）
    - 离开 SLEEP 时调 ``robot.wake_up()``

时间源
------
默认 ``time.monotonic``，可注入 ``clock`` 参数（fake clock for tests）。
所有内部时间都是 monotonic，避免系统时间跳变误触发。

env 配置
--------
- ``COCO_POWER_IDLE``: ``1`` 启用，``0``（默认）旁路。``=0`` 时上层应
  完全不构造 PowerStateMachine，行为退化到 companion-002 路径。
- ``COCO_POWER_DROWSY_AFTER``: float seconds, clamp [5, 3600]
- ``COCO_POWER_SLEEP_AFTER`` : float seconds, clamp [10, 7200]

线程模型
--------
``tick(now=None)`` 是纯计算 + 副作用（callback）；线程安全（内部 Lock）。
驱动方式两种（任选其一）：

1. ``start_driver()`` 起后台 daemon thread，1Hz 调 ``tick()``，``stop_event``
   set 时退出（< 1s）。
2. 手动在外部循环里调 ``tick()``（如已有 1Hz 心跳）。

对外副作用通过 callback 解耦，便于 test 注入 FakeRobot：
- ``on_enter_drowsy(state_machine)``
- ``on_enter_sleep(state_machine)`` —— 这里调 ``robot.goto_sleep()``
- ``on_enter_active(state_machine, from_state)`` —— from_state==SLEEP 时调 wake_up

任何 SDK 调用异常被 except 吞 + log + ``stats.callback_errors += 1``，
绝不让 driver 线程崩溃。
"""

from __future__ import annotations

import enum
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

log = logging.getLogger(__name__)


class PowerState(enum.Enum):
    ACTIVE = "active"
    DROWSY = "drowsy"
    SLEEP = "sleep"


@dataclass
class PowerConfig:
    drowsy_after: float = 60.0       # active → drowsy 阈值（秒）
    sleep_after: float = 120.0       # active → sleep 总阈值（秒）
    drowsy_micro_scale: float = 2.0  # IdleAnimator interval 乘数（drowsy 时）
    tick_interval: float = 1.0       # driver thread tick 周期

    def validate(self) -> None:
        if not (1.0 <= self.drowsy_after <= 86400.0):
            raise ValueError(f"drowsy_after={self.drowsy_after} 越界 [1, 86400]")
        if not (self.drowsy_after < self.sleep_after <= 86400.0):
            raise ValueError(
                f"sleep_after={self.sleep_after} 必须 > drowsy_after={self.drowsy_after} 且 <= 86400"
            )
        if not (1.0 <= self.drowsy_micro_scale <= 10.0):
            raise ValueError(f"drowsy_micro_scale={self.drowsy_micro_scale} 越界 [1, 10]")
        if not (0.1 <= self.tick_interval <= 30.0):
            raise ValueError(f"tick_interval={self.tick_interval} 越界 [0.1, 30]")


@dataclass
class PowerStats:
    transitions_to_drowsy: int = 0
    transitions_to_sleep: int = 0
    transitions_to_active: int = 0
    interactions_recorded: int = 0
    sleep_callbacks_invoked: int = 0  # robot.goto_sleep 调成功次数
    wake_callbacks_invoked: int = 0   # robot.wake_up 调成功次数
    callback_errors: int = 0
    last_transition_at: float = 0.0
    history: List[str] = field(default_factory=list)  # 形如 "active->drowsy@12.34"


# 回调签名：(state_machine,) 或 (state_machine, from_state)
EnterCallback = Callable[["PowerStateMachine"], None]
EnterActiveCallback = Callable[["PowerStateMachine", PowerState], None]


class PowerStateMachine:
    """三态 idle FSM。

    用法（最小）：
        psm = PowerStateMachine(config=PowerConfig(drowsy_after=60, sleep_after=120))
        psm.start_driver(stop_event)
        ...
        psm.record_interaction()   # 任何交互入口
        ...
        stop_event.set()
        psm.join_driver(timeout=2)

    集成 IdleAnimator / robot：
        psm.on_enter_sleep = lambda m: robot.goto_sleep()
        psm.on_enter_active = lambda m, prev: robot.wake_up() if prev == PowerState.SLEEP else None
        psm.on_enter_drowsy = lambda m: idle.set_micro_scale(2.0)
    """

    def __init__(
        self,
        config: Optional[PowerConfig] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.config = config or PowerConfig()
        self.config.validate()
        self.clock = clock or time.monotonic
        self.stats = PowerStats()
        self._state = PowerState.ACTIVE
        self._last_interaction = self.clock()
        # RLock：用户 callback 内若再调 record_interaction（例如 wake-up 完成
        # 后想再 bump 一次 idle 计时）不会自死锁；_transit_locked 因此可以
        # 直接调 callback 而无须 release/reacquire dance（companion-003 L1-2）。
        self._lock = threading.RLock()
        self._driver_thread: Optional[threading.Thread] = None
        self._driver_stop: Optional[threading.Event] = None

        # 用户挂的回调；默认 no-op
        self.on_enter_drowsy: Optional[EnterCallback] = None
        self.on_enter_sleep: Optional[EnterCallback] = None
        self.on_enter_active: Optional[EnterActiveCallback] = None

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    @property
    def current_state(self) -> PowerState:
        with self._lock:
            return self._state

    def idle_for(self) -> float:
        with self._lock:
            return max(0.0, self.clock() - self._last_interaction)

    def record_interaction(self, source: str = "unknown") -> None:
        """重置 idle 计时；若当前在 DROWSY/SLEEP 则切回 ACTIVE 并触发 callback。"""
        with self._lock:
            self.stats.interactions_recorded += 1
            self._last_interaction = self.clock()
            prev = self._state
            if prev != PowerState.ACTIVE:
                self._transit_locked(PowerState.ACTIVE, source=f"interaction:{source}")

    def tick(self, now: Optional[float] = None) -> Optional[PowerState]:
        """推进 FSM；返回本次 tick 触发的新 state（无变化时 None）。"""
        with self._lock:
            t = now if now is not None else self.clock()
            idle = max(0.0, t - self._last_interaction)
            target: Optional[PowerState] = None
            cfg = self.config
            if self._state == PowerState.ACTIVE and idle >= cfg.drowsy_after:
                target = PowerState.DROWSY
                if idle >= cfg.sleep_after:
                    # 跨过两个阈值（如 fake clock 一次跳很大），直接进 SLEEP
                    target = PowerState.SLEEP
            elif self._state == PowerState.DROWSY and idle >= cfg.sleep_after:
                target = PowerState.SLEEP
            if target is None or target == self._state:
                return None
            self._transit_locked(target, source=f"tick@idle={idle:.1f}s")
            return target

    # ------------------------------------------------------------------
    # 内部转移：必须在 self._lock 下调用
    # ------------------------------------------------------------------
    def _transit_locked(self, target: PowerState, source: str) -> None:
        prev = self._state
        if prev == target:
            return
        self._state = target
        now = self.clock()
        self.stats.last_transition_at = now
        self.stats.history.append(f"{prev.value}->{target.value}@{now:.2f}({source})")
        if target == PowerState.DROWSY:
            self.stats.transitions_to_drowsy += 1
        elif target == PowerState.SLEEP:
            self.stats.transitions_to_sleep += 1
        elif target == PowerState.ACTIVE:
            self.stats.transitions_to_active += 1
        log.info("[power] %s -> %s (%s)", prev.value, target.value, source)

        # callback 在锁内调用：RLock 允许同线程重入（L1-2 简化）。
        # 用户回调若再调 record_interaction / current_state 等公开方法都安全。
        if target == PowerState.DROWSY and self.on_enter_drowsy is not None:
            self._invoke(lambda: self.on_enter_drowsy(self), label="on_enter_drowsy")  # type: ignore[misc]
        elif target == PowerState.SLEEP and self.on_enter_sleep is not None:
            ok = self._invoke(lambda: self.on_enter_sleep(self), label="on_enter_sleep")  # type: ignore[misc]
            if ok:
                self.stats.sleep_callbacks_invoked += 1
        elif target == PowerState.ACTIVE and self.on_enter_active is not None:
            ok = self._invoke(
                lambda: self.on_enter_active(self, prev), label="on_enter_active"  # type: ignore[misc]
            )
            if ok and prev == PowerState.SLEEP:
                self.stats.wake_callbacks_invoked += 1

    def _invoke(self, fn: Callable[[], None], label: str) -> bool:
        try:
            fn()
            return True
        except Exception as e:  # noqa: BLE001
            self.stats.callback_errors += 1
            log.warning("[power] callback %s failed: %s: %s", label, type(e).__name__, e)
            return False

    # ------------------------------------------------------------------
    # Driver thread
    # ------------------------------------------------------------------
    def start_driver(self, stop_event: threading.Event) -> None:
        if self._driver_thread is not None and self._driver_thread.is_alive():
            log.warning("[power] driver already running")
            return
        self._driver_stop = stop_event
        self._driver_thread = threading.Thread(
            target=self._driver_loop,
            name="coco-power-state",
            daemon=True,
        )
        self._driver_thread.start()

    def _driver_loop(self) -> None:
        assert self._driver_stop is not None
        ev = self._driver_stop
        log.info("[power] driver started cfg=%s", self.config)
        try:
            while not ev.wait(timeout=self.config.tick_interval):
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001
                    log.warning("[power] tick error: %s", e)
        finally:
            log.info("[power] driver stopped state=%s stats=%s",
                     self._state.value, self.stats)

    def join_driver(self, timeout: Optional[float] = None) -> None:
        if self._driver_thread is not None:
            self._driver_thread.join(timeout=timeout)


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def power_idle_enabled_from_env() -> bool:
    # L1-1: COCO_POWER_IDLE_DISABLE=1/true/yes/on 强制关闭，覆盖 COCO_POWER_IDLE。
    disable_raw = os.environ.get("COCO_POWER_IDLE_DISABLE", "0").strip().lower()
    if disable_raw in {"1", "true", "yes", "on"}:
        return False
    return os.environ.get("COCO_POWER_IDLE", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _parse_clamped_float(env_key: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(env_key)
    if raw is None:
        return default
    try:
        val = float(raw)
    except ValueError:
        log.warning("[power] %s=%r invalid float; fallback default=%s", env_key, raw, default)
        return default
    if val < lo or val > hi:
        clamped = max(lo, min(hi, val))
        log.warning(
            "[power] %s=%s out of range [%s, %s]; clamped to %s",
            env_key, val, lo, hi, clamped,
        )
        return clamped
    return val


def _resolve_seconds(seconds_key: str, minutes_key: str, default_s: float,
                     lo: float, hi: float) -> float:
    """L1-1: 优先用 ``COCO_POWER_*_MINUTES``（× 60 转秒），否则 fallback ``*_AFTER``（秒）。

    两个 env 同时存在时 *_MINUTES 胜出（spec 字面命名优先）。
    """
    raw_min = os.environ.get(minutes_key)
    if raw_min is not None:
        try:
            val = float(raw_min) * 60.0
        except ValueError:
            log.warning("[power] %s=%r invalid float; falling back to %s", minutes_key, raw_min, seconds_key)
        else:
            if val < lo or val > hi:
                clamped = max(lo, min(hi, val))
                log.warning(
                    "[power] %s=%s minutes (=%ss) out of range [%s, %s]; clamped to %s",
                    minutes_key, raw_min, val, lo, hi, clamped,
                )
                return clamped
            return val
    return _parse_clamped_float(seconds_key, default_s, lo, hi)


def config_from_env() -> PowerConfig:
    cfg = PowerConfig()
    cfg.drowsy_after = _resolve_seconds(
        "COCO_POWER_DROWSY_AFTER", "COCO_POWER_DROWSY_MINUTES",
        cfg.drowsy_after, 5.0, 3600.0,
    )
    cfg.sleep_after = _resolve_seconds(
        "COCO_POWER_SLEEP_AFTER", "COCO_POWER_SLEEP_MINUTES",
        cfg.sleep_after, 10.0, 7200.0,
    )
    # 防御：env clamp 后可能 sleep_after <= drowsy_after，强制修正
    if cfg.sleep_after <= cfg.drowsy_after:
        new_sleep = min(7200.0, cfg.drowsy_after + 1.0)
        log.warning(
            "[power] sleep_after(%s) <= drowsy_after(%s); bumping sleep_after to %s",
            cfg.sleep_after, cfg.drowsy_after, new_sleep,
        )
        cfg.sleep_after = new_sleep
    return cfg


__all__ = [
    "PowerConfig",
    "PowerState",
    "PowerStateMachine",
    "PowerStats",
    "config_from_env",
    "power_idle_enabled_from_env",
]
