"""coco.robot.posture_baseline — 心情驱动姿态（robot-004）.

设计目标
========

在 IdleAnimator / ExpressionPlayer 的"动作 sample"之上叠加一层
**baseline offset**，让 Coco 的默认 idle 姿态因 emotion + power_state
整体偏移，不再机械居中：

- ACTIVE + happy      → 头略抬 + 天线展开
- ACTIVE + sad        → 头略低 + 天线收
- ACTIVE + angry      → 头略前倾 + 天线半展
- ACTIVE + surprised  → 头略抬 + 天线展开
- ACTIVE + neutral    → 中位
- DROWSY + *          → 头进一步压低（叠加 drowsy_extra）
- SLEEP               → noop（PowerStateMachine.on_enter_sleep 已 goto_sleep，
                         baseline 此时不下发任何 SetTarget，避免与 sleep pose 打架）

对外契约
========

- :class:`PostureOffset` — frozen dataclass(pitch_deg, yaw_deg, antenna)，
  clamp 在 ±5° pitch / ±3° yaw / antenna [0, 1]
- :class:`PostureBaseline` — 纯计算：``compute(emotion, power_state) -> PostureOffset``
- :class:`PostureBaselineModulator` — 后台 daemon thread：
  - 1Hz tick：snapshot emotion_tracker / power_state，compute target offset
  - 2s linear ramp 平滑过渡到 target（防瞬切，缓解 emotion 误判抖动）
  - ``current_offset()`` 返回 in-flight offset（IdleAnimator 在每次 sample 取并叠加）
  - 天线：每次 ramp tick 直接 ``set_target_antenna_joint_positions([right, left])``
  - 与 ExpressionPlayer 协调：``pause()/resume()``；pause 期间 baseline 不下发任何 SDK 调用，
    cooldown 后恢复（IdleAnimator 与 expression 共用同一 baseline 读取路径）

env
===

- ``COCO_POSTURE_BASELINE`` — ``1`` 启用，默认 OFF（向后兼容）
- ``COCO_POSTURE_BASELINE_RAMP_S`` — ramp 时长（秒），clamp [0.2, 10.0]，默认 2.0
- ``COCO_POSTURE_BASELINE_TICK_S`` — 后台 tick 周期，clamp [0.05, 5.0]，默认 0.2
- ``COCO_POSTURE_BASELINE_DEBOUNCE_S`` — emotion 切换 debounce（与 ramp 一起防抖），
  clamp [0.0, 30.0]，默认 5.0

线程模型
========

- ``PostureBaselineModulator.start()`` 起一个 daemon thread "coco-posture-baseline"，
  以 ``tick_interval`` 周期推进 ramp + 下发天线
- ``current_offset()`` 读取 ``self._current``（dataclass instance；CPython 引用赋值原子），
  IdleAnimator 在每次 ``_micro_head`` / ``_breathe`` 中取一次叠加。多线程读单线程写
  天然安全。
- ``pause()/resume()``：sync flag，pause 时 ramp 仍在内部推进（保证 resume 后立即生效），
  但不下发天线 SetTarget。

fail-soft
=========

- robot 不可用 / SDK 失败 → log + stats，不抛
- emotion_tracker / power_state 抛 → 视为 NEUTRAL / ACTIVE，不抛
- enabled=False → start() 直接 return；IdleAnimator 取 ``current_offset()`` 始终 ZERO
"""

from __future__ import annotations

import enum
import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 安全上限（与 verification 字段对齐）
# ---------------------------------------------------------------------------

MAX_BASELINE_PITCH_DEG: float = 5.0   # ±5° pitch
MAX_BASELINE_YAW_DEG: float = 3.0     # ±3° yaw
MIN_BASELINE_ANTENNA: float = 0.0
MAX_BASELINE_ANTENNA: float = 1.0


# ---------------------------------------------------------------------------
# PostureOffset
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PostureOffset:
    """baseline 姿态偏移。

    - pitch_deg: 头俯仰偏移（正 = 低头，与 ExpressionFrame.pitch_deg 同向），clamp ±5°
    - yaw_deg:   头偏航偏移（正 = 右转），clamp ±3°
    - antenna:   天线"展开度"，0=收，1=完全展开。映射到天线弧度（0→0, 1→±0.5 rad）
    """

    pitch_deg: float = 0.0
    yaw_deg: float = 0.0
    antenna: float = 0.5  # 中位

    def clamped(self) -> "PostureOffset":
        return PostureOffset(
            pitch_deg=_clamp(self.pitch_deg, -MAX_BASELINE_PITCH_DEG, MAX_BASELINE_PITCH_DEG),
            yaw_deg=_clamp(self.yaw_deg, -MAX_BASELINE_YAW_DEG, MAX_BASELINE_YAW_DEG),
            antenna=_clamp(self.antenna, MIN_BASELINE_ANTENNA, MAX_BASELINE_ANTENNA),
        )

    def antenna_joint_rad(self) -> Tuple[float, float]:
        """把 antenna [0,1] 映射成 ``set_target_antenna_joint_positions([right, left])``。

        antenna=0     → 收（[0, 0]）
        antenna=0.5   → 中位（[0, 0]，与收一致；中位即静默）
        antenna=1.0   → 完全展开（[+0.5, -0.5]，对称外展）

        注：天线 SDK 单位是 rad，正负方向真机经验值；ramp 期间 0..1 线性映射。
        """
        # 0..0.5 视为"未展开"段（保持中性，避免微抖动）；> 0.5 才开始外展
        if self.antenna <= 0.5:
            return (0.0, 0.0)
        scale = (self.antenna - 0.5) * 2.0  # 0.5..1.0 → 0..1
        amp = 0.5 * scale  # 最大 0.5 rad（与 IdleAnimator micro_antenna_amp_rad=0.15 同量级但更大）
        return (+amp, -amp)


ZERO_OFFSET = PostureOffset(pitch_deg=0.0, yaw_deg=0.0, antenna=0.5)


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


# ---------------------------------------------------------------------------
# PostureBaseline — 纯查表
# ---------------------------------------------------------------------------


# 表查找：(emotion_value, power_state_value) → PostureOffset
# emotion_value 与 coco.emotion.Emotion.value 对齐；power_state_value 与
# coco.power_state.PowerState.value 对齐。
#
# 设计原则：
#   - ACTIVE 下，emotion 决定姿态"个性"
#   - DROWSY 下，所有 emotion 都被压低 +2° pitch（在 ACTIVE 基础上叠加），天线半收
#   - SLEEP 下统一 ZERO（外层会 short-circuit 不下发）
_LOOKUP: Dict[Tuple[str, str], PostureOffset] = {
    # ---- ACTIVE ----
    ("happy", "active"):     PostureOffset(pitch_deg=-3.0, yaw_deg=0.0, antenna=1.0),
    ("sad", "active"):       PostureOffset(pitch_deg=+3.0, yaw_deg=0.0, antenna=0.0),
    ("angry", "active"):     PostureOffset(pitch_deg=-1.0, yaw_deg=0.0, antenna=0.6),
    ("surprised", "active"): PostureOffset(pitch_deg=-3.0, yaw_deg=0.0, antenna=1.0),
    ("neutral", "active"):   PostureOffset(pitch_deg=0.0, yaw_deg=0.0, antenna=0.5),
    # ---- DROWSY（ACTIVE 基础上 pitch+2、antenna 半收）----
    ("happy", "drowsy"):     PostureOffset(pitch_deg=-1.0, yaw_deg=0.0, antenna=0.6),
    ("sad", "drowsy"):       PostureOffset(pitch_deg=+5.0, yaw_deg=0.0, antenna=0.0),
    ("angry", "drowsy"):     PostureOffset(pitch_deg=+1.0, yaw_deg=0.0, antenna=0.4),
    ("surprised", "drowsy"): PostureOffset(pitch_deg=-1.0, yaw_deg=0.0, antenna=0.6),
    ("neutral", "drowsy"):   PostureOffset(pitch_deg=+2.0, yaw_deg=0.0, antenna=0.4),
    # ---- SLEEP ----
    # 全部 ZERO；外层 modulator 在 SLEEP 下 short-circuit，不会真正叠加 / 下发
    ("happy", "sleep"):      ZERO_OFFSET,
    ("sad", "sleep"):        ZERO_OFFSET,
    ("angry", "sleep"):      ZERO_OFFSET,
    ("surprised", "sleep"):  ZERO_OFFSET,
    ("neutral", "sleep"):    ZERO_OFFSET,
}


class PostureBaseline:
    """纯计算：(emotion, power_state) → PostureOffset。

    ``compute()`` 返回的 offset **未经** ramp 平滑；调用方（Modulator）负责插值。
    """

    def compute(self, emotion: Any, power_state: Any) -> PostureOffset:
        """查表 + clamp + fallback。

        参数：
          - emotion: ``coco.emotion.Emotion`` 枚举 / 字符串 / None
          - power_state: ``coco.power_state.PowerState`` 枚举 / 字符串 / None

        异常输入一律 fallback (neutral, active) → ZERO_OFFSET 中位。
        """
        emo = _normalize(emotion, default="neutral")
        psv = _normalize(power_state, default="active")
        off = _LOOKUP.get((emo, psv))
        if off is None:
            log.debug("[posture_baseline] no entry for (%s, %s); fallback ZERO", emo, psv)
            off = ZERO_OFFSET
        return off.clamped()


def _normalize(v: Any, default: str) -> str:
    if v is None:
        return default
    s = getattr(v, "value", v)
    if not isinstance(s, str):
        return default
    return s.lower()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PostureBaselineConfig:
    enabled: bool = False
    ramp_s: float = 2.0
    tick_interval_s: float = 0.2
    debounce_s: float = 5.0


def _bool_env(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


def _float_env(env: Mapping[str, str], key: str, default: float, lo: float, hi: float) -> float:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        log.warning("[posture_baseline] %s=%r 非数字，回退默认 %.2f", key, raw, default)
        return default
    if v < lo:
        log.warning("[posture_baseline] %s=%.2f <%.2f，clamp", key, v, lo)
        return lo
    if v > hi:
        log.warning("[posture_baseline] %s=%.2f >%.2f，clamp", key, v, hi)
        return hi
    return v


def posture_baseline_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    e = env if env is not None else os.environ
    return _bool_env(e, "COCO_POSTURE_BASELINE", False)


def posture_baseline_config_from_env(env: Optional[Mapping[str, str]] = None) -> PostureBaselineConfig:
    e = env if env is not None else os.environ
    return PostureBaselineConfig(
        enabled=_bool_env(e, "COCO_POSTURE_BASELINE", False),
        ramp_s=_float_env(e, "COCO_POSTURE_BASELINE_RAMP_S", 2.0, 0.2, 10.0),
        tick_interval_s=_float_env(e, "COCO_POSTURE_BASELINE_TICK_S", 0.2, 0.05, 5.0),
        debounce_s=_float_env(e, "COCO_POSTURE_BASELINE_DEBOUNCE_S", 5.0, 0.0, 30.0),
    )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class PostureBaselineStats:
    ticks: int = 0
    target_changes: int = 0          # 目标 offset 改变次数（含 debounce 通过后）
    debounce_skipped: int = 0        # 因 debounce 未生效的目标变更
    ramps_started: int = 0           # 实际开启的 ramp 次数
    antenna_dispatched: int = 0      # 天线下发次数（ramp 中每 tick 一次）
    sdk_errors: int = 0
    sleep_skipped: int = 0           # SLEEP 状态下被 short-circuit 的 tick
    paused_skipped: int = 0          # pause() 期间 short-circuit 的 tick
    last_target_emotion: Optional[str] = None
    last_target_power: Optional[str] = None
    last_change_at: float = 0.0
    history: list = field(default_factory=list)  # ["happy/active@12.34", ...]


# ---------------------------------------------------------------------------
# PostureBaselineModulator
# ---------------------------------------------------------------------------


class PostureBaselineModulator:
    """后台 daemon，把 emotion + power_state 翻译成 in-flight ``current_offset()``。

    用法（main.py）：
        mod = PostureBaselineModulator(
            robot=reachy_mini,
            emotion_tracker=_emotion_tracker,
            power_state=power_state,
            config=posture_baseline_config_from_env(),
            emit_fn=emit,
        )
        mod.start(stop_event)
        # IdleAnimator / ExpressionPlayer 在每次 sample 时取 mod.current_offset()
        ...
        stop_event.set()
        mod.join(timeout=2.0)
    """

    def __init__(
        self,
        *,
        robot: Any,
        emotion_tracker: Any = None,
        power_state: Any = None,
        config: Optional[PostureBaselineConfig] = None,
        baseline: Optional[PostureBaseline] = None,
        emit_fn: Optional[Callable[..., None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.robot = robot
        self.emotion_tracker = emotion_tracker
        self.power_state = power_state
        self.config = config or PostureBaselineConfig()
        self.baseline = baseline or PostureBaseline()
        self._emit = emit_fn
        self.clock = clock or time.monotonic
        self.stats = PostureBaselineStats()

        self._lock = threading.RLock()
        self._stop: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None
        self._paused = threading.Event()  # set = paused

        # 三段 offset：起点（ramp 起始）/ 目标（ramp 终点）/ 当前（in-flight 插值）
        self._ramp_from: PostureOffset = ZERO_OFFSET
        self._target: PostureOffset = ZERO_OFFSET
        self._current: PostureOffset = ZERO_OFFSET
        self._ramp_started_at: float = 0.0
        # debounce：上次"决定切换 target"的时刻；连续相同新 target 在 debounce_s 内只允许一次
        # 用 -inf 起点，确保首次 target 切换不被 debounce 拒绝
        self._last_target_change_at: float = float("-inf")
        # 上次成功 snapshot 的 (emotion_str, power_str)；None 表示尚未 snapshot
        self._last_snapshot_key: Optional[Tuple[str, str]] = None
        # companion-007: target 变更监听器；每次 _begin_ramp 调用所有 listener
        # 签名 fn(emotion, power_state) — 与 PostureBaseline.compute 同源 snapshot
        self._listeners: list = []
        self._last_target_emotion_obj: Any = None
        self._last_target_power_obj: Any = None

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def current_offset(self) -> PostureOffset:
        """返回 in-flight offset；未启用 / SLEEP / 异常时返回 ZERO_OFFSET。

        多线程读：CPython 引用赋值原子，无需锁。
        """
        if not self.config.enabled:
            return ZERO_OFFSET
        # SLEEP 下 short-circuit
        if self._is_sleep():
            return ZERO_OFFSET
        return self._current

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def add_listener(self, fn: Callable[[Any, Any], None]) -> None:
        """注册 target 变更监听器（companion-007）。

        每次 ``_begin_ramp`` 决定切换 target 时（已通过 5s debounce）调用所有
        listener，签名 ``fn(emotion, power_state)`` —— 与本 modulator 计算 baseline
        同源（同一次 ``_snapshot_target``）。listener 抛错被吞，不影响 baseline。
        """
        if not callable(fn):
            return
        with self._lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    def pause(self) -> None:
        """暂停天线下发（与 expression / talk gesture 协调）。ramp 内部插值仍在推进。"""
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def start(self, stop_event: threading.Event) -> None:
        if not self.config.enabled:
            log.info("[posture_baseline] disabled (COCO_POSTURE_BASELINE not set); start() noop")
            return
        if self._thread is not None and self._thread.is_alive():
            log.warning("[posture_baseline] already running")
            return
        self._stop = stop_event
        self._thread = threading.Thread(
            target=self._run,
            name="coco-posture-baseline",
            daemon=True,
        )
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _run(self) -> None:
        assert self._stop is not None
        log.info(
            "[posture_baseline] driver started ramp_s=%.1f tick_s=%.2f debounce_s=%.1f",
            self.config.ramp_s, self.config.tick_interval_s, self.config.debounce_s,
        )
        # 起始基线：snapshot 一次定起点
        try:
            init_target = self._snapshot_target()
            self._target = init_target
            self._current = init_target
            self._ramp_from = init_target
        except Exception as exc:  # noqa: BLE001
            log.warning("[posture_baseline] init snapshot failed: %s: %s", type(exc).__name__, exc)
        try:
            ev = self._stop
            tick = self.config.tick_interval_s
            while not ev.wait(timeout=tick):
                try:
                    self._tick_once()
                except Exception as exc:  # noqa: BLE001
                    log.warning("[posture_baseline] tick error: %s: %s", type(exc).__name__, exc)
        finally:
            log.info("[posture_baseline] driver stopped stats=%s", self.stats)

    def _tick_once(self) -> None:
        self.stats.ticks += 1
        # SLEEP 短路：不更新 target、不下发天线
        if self._is_sleep():
            self.stats.sleep_skipped += 1
            return
        # 计算新 target（含 debounce）
        new_target = self._snapshot_target()
        now = self.clock()
        if new_target != self._target:
            # debounce：在 debounce_s 内不允许重复 target 切换（防 emotion 误判抖动）
            if (now - self._last_target_change_at) < self.config.debounce_s:
                self.stats.debounce_skipped += 1
            else:
                self._begin_ramp(new_target, now)
        # 推进 ramp（插值）
        self._advance_ramp(now)
        # 下发天线（pause 期间 skip）
        if self._paused.is_set():
            self.stats.paused_skipped += 1
            return
        self._dispatch_antenna()

    def _snapshot_target(self) -> PostureOffset:
        """从 emotion_tracker / power_state 拼出新 target offset。失败 fail-soft → ZERO/中位。

        副作用：把本次 snapshot 的原始 emotion / power_state 对象缓存到
        ``self._last_target_emotion_obj`` / ``self._last_target_power_obj``，
        供 listener / log 使用（保证 listener 拿到的对象与计算 baseline 同源）。
        """
        emo: Any = None
        psv: Any = None
        if self.emotion_tracker is not None:
            try:
                eff = self.emotion_tracker.effective
                emo = eff() if callable(eff) else eff
            except Exception as exc:  # noqa: BLE001
                log.debug("[posture_baseline] emotion snapshot failed: %s", exc)
                emo = None
        if self.power_state is not None:
            try:
                psv = self.power_state.current_state
            except Exception as exc:  # noqa: BLE001
                log.debug("[posture_baseline] power snapshot failed: %s", exc)
                psv = None
        self._last_target_emotion_obj = emo
        self._last_target_power_obj = psv
        return self.baseline.compute(emo, psv)

    def _begin_ramp(self, new_target: PostureOffset, now: float) -> None:
        with self._lock:
            self._ramp_from = self._current
            self._target = new_target
            self._ramp_started_at = now
            self._last_target_change_at = now
            self.stats.ramps_started += 1
            self.stats.target_changes += 1
            self.stats.last_change_at = now
            # 把 emotion / power 字符串记入 stats（便于 evidence 打印）
            ek = _normalize(self._infer_emotion_for_log(), default="neutral")
            pk = _normalize(self._infer_power_for_log(), default="active")
            self.stats.last_target_emotion = ek
            self.stats.last_target_power = pk
            self.stats.history.append(f"{ek}/{pk}@{now:.2f}")
            log.info(
                "[posture_baseline] target -> pitch=%.2f yaw=%.2f antenna=%.2f (emo=%s power=%s)",
                new_target.pitch_deg, new_target.yaw_deg, new_target.antenna, ek, pk,
            )
            self._emit_event(
                "robot.posture_baseline_changed",
                message=f"baseline -> {ek}/{pk}",
                from_pitch=round(self._ramp_from.pitch_deg, 3),
                from_yaw=round(self._ramp_from.yaw_deg, 3),
                from_antenna=round(self._ramp_from.antenna, 3),
                to_pitch=round(new_target.pitch_deg, 3),
                to_yaw=round(new_target.yaw_deg, 3),
                to_antenna=round(new_target.antenna, 3),
                emotion=ek,
                power_state=pk,
                ramp_s=float(self.config.ramp_s),
            )
        # companion-007: fire listeners（在锁外，避免 listener 重新进 modulator API 死锁）
        listeners = list(self._listeners)
        emo_obj = self._last_target_emotion_obj
        pwr_obj = self._last_target_power_obj
        for fn in listeners:
            try:
                fn(emo_obj, pwr_obj)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "[posture_baseline] listener %r failed: %s: %s",
                    fn, type(exc).__name__, exc,
                )

    def _advance_ramp(self, now: float) -> None:
        ramp = max(0.001, float(self.config.ramp_s))
        elapsed = now - self._ramp_started_at
        if elapsed >= ramp:
            self._current = self._target
            return
        t = elapsed / ramp  # 0..1 linear
        self._current = PostureOffset(
            pitch_deg=_lerp(self._ramp_from.pitch_deg, self._target.pitch_deg, t),
            yaw_deg=_lerp(self._ramp_from.yaw_deg, self._target.yaw_deg, t),
            antenna=_lerp(self._ramp_from.antenna, self._target.antenna, t),
        )

    def _dispatch_antenna(self) -> None:
        if self.robot is None:
            return
        if not hasattr(self.robot, "set_target_antenna_joint_positions"):
            return
        try:
            right, left = self._current.antenna_joint_rad()
            self.robot.set_target_antenna_joint_positions([right, left])
            self.stats.antenna_dispatched += 1
        except Exception as exc:  # noqa: BLE001
            self.stats.sdk_errors += 1
            log.warning("[posture_baseline] antenna SDK failed: %s: %s", type(exc).__name__, exc)

    def _is_sleep(self) -> bool:
        if self.power_state is None:
            return False
        try:
            from coco.power_state import PowerState as _PS
            return self.power_state.current_state == _PS.SLEEP
        except Exception:  # noqa: BLE001
            return False

    def _infer_emotion_for_log(self) -> Any:
        if self.emotion_tracker is None:
            return None
        try:
            eff = self.emotion_tracker.effective
            return eff() if callable(eff) else eff
        except Exception:  # noqa: BLE001
            return None

    def _infer_power_for_log(self) -> Any:
        if self.power_state is None:
            return None
        try:
            return self.power_state.current_state
        except Exception:  # noqa: BLE001
            return None

    def _emit_event(self, component_event: str, message: str = "", **payload: Any) -> None:
        try:
            fn = self._emit
            if fn is None:
                from coco.logging_setup import emit as _emit
                fn = _emit
            fn(component_event, message, **payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("[posture_baseline] emit failed: %s: %s", type(exc).__name__, exc)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


__all__ = [
    "PostureOffset",
    "PostureBaseline",
    "PostureBaselineConfig",
    "PostureBaselineModulator",
    "PostureBaselineStats",
    "ZERO_OFFSET",
    "MAX_BASELINE_PITCH_DEG",
    "MAX_BASELINE_YAW_DEG",
    "MIN_BASELINE_ANTENNA",
    "MAX_BASELINE_ANTENNA",
    "posture_baseline_enabled_from_env",
    "posture_baseline_config_from_env",
]
