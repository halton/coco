"""infra-007: SelfHealStrategy 注册表 + 指数退避.

目的
====

在 infra-005 HealthMonitor 之上抽出统一的「检测到异常 → 重启某子系统」
的策略框架。每个 SelfHealStrategy 实现：

- name: 唯一名字（注册/查找/统计用）
- should_apply(failure_kind, ctx) -> bool: 是否处理这种 failure
- apply(ctx) -> bool: 真正执行 heal（True=成功，False=失败）
- cooldown_s: 默认 30s（同一策略上次 attempt 后多久才能再 attempt）
- max_attempts: 默认 5（infra-005 default 是 3；feature_list.json 指定 5）

退避计算
========

base = 5s, ratio = 2, sequence = [5, 10, 20, 40, 80]，cap = 120s。
jitter = ±10%（基于注入的 rand_fn，默认 random.uniform，test 可注入确定性源）。

backoff_for(idx) = clamp(base * 2^idx, 0, cap) * jitter(0.9..1.1)

3 个内置策略（注入式 reopen_fn）
================================

- AudioReopenStrategy: failure_kind in {"audio_stream_lost", "audio_stream_dead"}
- ASRRestartStrategy:  failure_kind in {"asr_latency_high", "asr_dead"}
- CameraReopenStrategy: failure_kind in {"camera_dead", "camera_read_none"}

3 个策略的真 reopen_fn 由 main.py 在 real-machine 模式下绑；sim 下用 lambda **kw: True。

real-machine gate
=================

SelfHealRegistry.dispatch 在 sim 模式下默认 dry-run（emit `self_heal.dry_run` 不真调 apply）；
COCO_REAL_MACHINE=1 或 COCO_BACKEND=robot 才真调 strategy.apply()。
这与 infra-005 daemon restart 真机不动的策略对称。

**计数语义（infra-007 rework L1-b）**：

- `StrategyStats.attempts`：观测计数，含 dry-run 与真机；用于统计 / 告警 / 可视化。
- `StrategyStats.real_attempts`：真机实际尝试次数；giveup latch 与 backoff index 的真理来源。
- `StrategyStats.last_attempt_ts`：dry-run 也推进，保留 cooldown 抑流；避免 sim 暴风。

意味着 sim 下任意次 dry-run 都 **不会** 让 strategy 进 giveup latch；切到真机时
（`COCO_REAL_MACHINE=1` 或 backend 切换）strategy 仍可正常尝试 `max_attempts` 次，
不需要外部 `reset_strategy()`。

线程安全
========

RLock 包围 _state 字典；apply 内部不持锁（apply 可能耗时）。

emit topics
===========

- self_heal.attempt   —— 策略 apply 被调用前
- self_heal.success   —— apply 返回 True
- self_heal.failed    —— apply 返回 False 或抛
- self_heal.cooldown_skip —— cooldown 内被跳过
- self_heal.giveup    —— max_attempts 后进入 latch
- self_heal.dry_run   —— sim 模式（real-machine gate）跳过真 apply

env
===

- COCO_SELFHEAL=1 启用（main.py 装配）；未启用时 SelfHealRegistry 不构造。
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Set, runtime_checkable

from coco.logging_setup import emit as _root_emit


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 默认参数
# ---------------------------------------------------------------------------

DEFAULT_BACKOFF_BASE_S = 5.0
DEFAULT_BACKOFF_CAP_S = 120.0
DEFAULT_BACKOFF_RATIO = 2.0
DEFAULT_BACKOFF_JITTER = 0.10  # ±10%
DEFAULT_COOLDOWN_S = 30.0
DEFAULT_MAX_ATTEMPTS = 5


# ---------------------------------------------------------------------------
# Strategy 接口
# ---------------------------------------------------------------------------


@runtime_checkable
class SelfHealStrategy(Protocol):
    """SelfHealStrategy 协议（duck-typed; 运行时 isinstance 可用）。

    各属性 / 方法的语义见模块 docstring。具体策略一般继承 BaseSelfHealStrategy。
    """

    name: str
    cooldown_s: float
    max_attempts: int

    def should_apply(self, failure_kind: str, ctx: Mapping[str, Any]) -> bool: ...

    def apply(self, ctx: Mapping[str, Any]) -> bool: ...


class BaseSelfHealStrategy:
    """便利基类；3 个内置策略都继承自这里。"""

    name: str = "base"
    failure_kinds: Set[str] = frozenset()
    cooldown_s: float = DEFAULT_COOLDOWN_S
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

    def __init__(
        self,
        *,
        name: Optional[str] = None,
        failure_kinds: Optional[Set[str]] = None,
        cooldown_s: Optional[float] = None,
        max_attempts: Optional[int] = None,
        reopen_fn: Optional[Callable[..., bool]] = None,
    ) -> None:
        if name is not None:
            self.name = str(name)
        if failure_kinds is not None:
            self.failure_kinds = frozenset(failure_kinds)
        if cooldown_s is not None:
            self.cooldown_s = max(0.0, float(cooldown_s))
        if max_attempts is not None:
            self.max_attempts = max(0, int(max_attempts))
        self._reopen_fn = reopen_fn or (lambda **kw: True)

    def should_apply(self, failure_kind: str, ctx: Mapping[str, Any]) -> bool:
        return failure_kind in self.failure_kinds

    def apply(self, ctx: Mapping[str, Any]) -> bool:
        try:
            r = self._reopen_fn(**dict(ctx))
            return bool(r) if r is not None else False
        except Exception as e:  # noqa: BLE001
            log.debug("[self_heal] %s apply raised: %r", self.name, e)
            return False


class AudioReopenStrategy(BaseSelfHealStrategy):
    name = "audio_reopen"
    failure_kinds = frozenset({"audio_stream_lost", "audio_stream_dead"})


class ASRRestartStrategy(BaseSelfHealStrategy):
    name = "asr_restart"
    failure_kinds = frozenset({"asr_latency_high", "asr_dead"})


class CameraReopenStrategy(BaseSelfHealStrategy):
    name = "camera_reopen"
    failure_kinds = frozenset({"camera_dead", "camera_read_none"})


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class StrategyStats:
    attempts: int = 0  # 观测计数（含 dry-run + 真机 attempt），仅用于统计与告警
    real_attempts: int = 0  # 真机实际尝试次数（giveup latch 与 backoff index 的真理来源）
    succeeded: int = 0
    failed: int = 0
    cooldown_skipped: int = 0
    giveup: bool = False
    last_attempt_ts: float = 0.0


@dataclass
class RegistryStats:
    attempts_total: int = 0
    succeeded_total: int = 0
    failed_total: int = 0
    cooldown_skipped_total: int = 0
    giveup_after_max: int = 0
    dry_run_total: int = 0
    no_strategy_total: int = 0
    per_strategy: Dict[str, StrategyStats] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


def backoff_for(
    attempt_idx: int,
    *,
    base: float = DEFAULT_BACKOFF_BASE_S,
    ratio: float = DEFAULT_BACKOFF_RATIO,
    cap: float = DEFAULT_BACKOFF_CAP_S,
    jitter: float = DEFAULT_BACKOFF_JITTER,
    rand_fn: Optional[Callable[[float, float], float]] = None,
) -> float:
    """指数退避：base * ratio^idx，clip 到 cap，乘以 (1 ± jitter)。

    attempt_idx 从 0 起：[5, 10, 20, 40, 80, 120, 120, ...] for default。
    rand_fn 默认 random.uniform，test 可注入确定性源（如 lambda a,b: (a+b)/2）。
    """
    if attempt_idx < 0:
        attempt_idx = 0
    raw = base * (ratio ** attempt_idx)
    if raw > cap:
        raw = cap
    if raw < 0:
        raw = 0
    if jitter <= 0:
        return raw
    lo = 1.0 - jitter
    hi = 1.0 + jitter
    rf = rand_fn or random.uniform
    return raw * rf(lo, hi)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SelfHealRegistry:
    """策略注册表 + 调度 + 退避 + 统计.

    线程安全：_lock (RLock) 保护 _state / _strategies / stats；apply 调用不持锁。
    real-machine gate：构造时拿 is_real_machine_fn 注入；dispatch 内部判断；
    sim 模式下走 dry-run，emit `self_heal.dry_run`，不真调 apply。
    """

    def __init__(
        self,
        *,
        is_real_machine_fn: Optional[Callable[[], bool]] = None,
        emit_fn: Optional[Callable[..., None]] = None,
        now_fn: Optional[Callable[[], float]] = None,
        rand_fn: Optional[Callable[[float, float], float]] = None,
    ) -> None:
        self._strategies: List[SelfHealStrategy] = []
        self._state: Dict[str, StrategyStats] = {}
        self._lock = threading.RLock()
        self._is_real_machine_fn = is_real_machine_fn or _default_is_real_machine
        self._emit = emit_fn or _safe_emit
        self._now = now_fn or time.time
        self._rand_fn = rand_fn  # backoff jitter；None 表示用 random.uniform
        self.stats = RegistryStats()

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register(self, strategy: SelfHealStrategy) -> None:
        with self._lock:
            # 名字唯一
            for s in self._strategies:
                if s.name == strategy.name:
                    raise ValueError(f"strategy already registered: {strategy.name!r}")
            self._strategies.append(strategy)
            self._state[strategy.name] = StrategyStats()
            self.stats.per_strategy[strategy.name] = self._state[strategy.name]

    def unregister(self, name: str) -> bool:
        with self._lock:
            for i, s in enumerate(self._strategies):
                if s.name == name:
                    self._strategies.pop(i)
                    self._state.pop(name, None)
                    self.stats.per_strategy.pop(name, None)
                    return True
            return False

    def list_strategies(self) -> List[str]:
        with self._lock:
            return [s.name for s in self._strategies]

    def get_strategy(self, name: str) -> Optional[SelfHealStrategy]:
        with self._lock:
            for s in self._strategies:
                if s.name == name:
                    return s
            return None

    def reset_strategy(self, name: str) -> bool:
        """清零某策略的 attempts/giveup latch（运维 / test 用）。"""
        with self._lock:
            st = self._state.get(name)
            if st is None:
                return False
            st.attempts = 0
            st.real_attempts = 0
            st.succeeded = 0
            st.failed = 0
            st.cooldown_skipped = 0
            st.giveup = False
            st.last_attempt_ts = 0.0
            return True

    def reset_all(self) -> None:
        with self._lock:
            for name in list(self._state.keys()):
                self.reset_strategy(name)

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def dispatch(self, failure_kind: str, ctx: Optional[Mapping[str, Any]] = None) -> bool:
        """根据 failure_kind 找命中的策略并尝试 apply.

        返回 True 表示有策略成功 heal；False 表示没策略命中 / 全部失败 / cooldown 跳过。
        sim 模式 (real-machine gate=False) 走 dry-run（计 attempt 但不调真 apply）。
        """
        ctx = dict(ctx or {})
        ctx.setdefault("failure_kind", failure_kind)

        # 拿候选策略（持锁拷贝；apply 不持锁）
        with self._lock:
            candidates = [s for s in self._strategies if _safe_should_apply(s, failure_kind, ctx)]

        if not candidates:
            self.stats.no_strategy_total += 1
            self._emit("self_heal.no_strategy", failure_kind=failure_kind)
            return False

        any_success = False
        for strat in candidates:
            with self._lock:
                st = self._state.setdefault(strat.name, StrategyStats())
                self.stats.per_strategy.setdefault(strat.name, st)

                # giveup latch（基于 real_attempts；dry-run 不会推进 real_attempts，因此 sim 永远不进 latch）
                if st.giveup:
                    self._emit(
                        "self_heal.giveup_skip",
                        strategy=strat.name,
                        failure_kind=failure_kind,
                    )
                    continue

                now = self._now()

                # cooldown（dry-run 也推进 last_attempt_ts，避免 sim 暴风）
                if st.last_attempt_ts > 0 and (now - st.last_attempt_ts) < float(strat.cooldown_s):
                    st.cooldown_skipped += 1
                    self.stats.cooldown_skipped_total += 1
                    self._emit(
                        "self_heal.cooldown_skip",
                        strategy=strat.name,
                        failure_kind=failure_kind,
                        elapsed=round(now - st.last_attempt_ts, 3),
                        cooldown_s=float(strat.cooldown_s),
                    )
                    continue

                # real-machine gate（提前判定，决定是否推进 real_attempts / giveup）
                try:
                    real = bool(self._is_real_machine_fn())
                except Exception:  # noqa: BLE001
                    real = False

                # max_attempts → giveup latch（仅基于 real_attempts；sim dry-run 不会触发）
                if real and st.real_attempts >= int(strat.max_attempts):
                    st.giveup = True
                    self.stats.giveup_after_max += 1
                    self._emit(
                        "self_heal.giveup",
                        strategy=strat.name,
                        failure_kind=failure_kind,
                        attempts=st.real_attempts,
                        max_attempts=int(strat.max_attempts),
                    )
                    continue

                # 进入 attempt：observed attempts 总是 +1；real_attempts 仅真机 +1
                # backoff index 用 real_attempts（sim 下保持 0 → 始终是首次 backoff，避免被 sim 把 idx 推到 cap）
                attempt_idx = st.real_attempts if real else 0
                st.attempts += 1
                if real:
                    st.real_attempts += 1
                st.last_attempt_ts = now
                self.stats.attempts_total += 1

            # ---- 锁外 ----
            backoff_s = backoff_for(
                attempt_idx,
                rand_fn=self._rand_fn,
            )
            self._emit(
                "self_heal.attempt",
                strategy=strat.name,
                failure_kind=failure_kind,
                attempt=attempt_idx + 1,
                max_attempts=int(strat.max_attempts),
                backoff_s=round(backoff_s, 3),
                mode="real" if real else "sim",
            )

            if not real:
                # sim 模式：dry-run（不动 real_attempts / giveup，attempts 已 +1 作为观测）
                self.stats.dry_run_total += 1
                self._emit(
                    "self_heal.dry_run",
                    strategy=strat.name,
                    failure_kind=failure_kind,
                    attempt=st.attempts,
                )
                continue

            # 真机：调 apply
            try:
                ok = bool(strat.apply(ctx))
            except Exception as e:  # noqa: BLE001
                log.debug("[self_heal] %s apply raised: %r", strat.name, e)
                ok = False
                with self._lock:
                    st.failed += 1
                    self.stats.failed_total += 1
                self._emit(
                    "self_heal.failed",
                    strategy=strat.name,
                    failure_kind=failure_kind,
                    attempt=attempt_idx + 1,
                    error=type(e).__name__,
                )
                continue

            with self._lock:
                if ok:
                    st.succeeded += 1
                    self.stats.succeeded_total += 1
                else:
                    st.failed += 1
                    self.stats.failed_total += 1

            self._emit(
                "self_heal.success" if ok else "self_heal.failed",
                strategy=strat.name,
                failure_kind=failure_kind,
                attempt=attempt_idx + 1,
            )

            if ok:
                any_success = True
                # 第一个成功的策略后即返回；其余候选可下次再来
                break

        return any_success


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _safe_should_apply(strat: SelfHealStrategy, failure_kind: str, ctx: Mapping[str, Any]) -> bool:
    try:
        return bool(strat.should_apply(failure_kind, ctx))
    except Exception as e:  # noqa: BLE001
        log.debug("[self_heal] %s should_apply raised: %r", getattr(strat, "name", "?"), e)
        return False


def _safe_emit(topic: str, **payload: Any) -> None:
    try:
        _root_emit(topic, **payload)
    except Exception as e:  # noqa: BLE001
        log.debug("[self_heal] emit %s failed: %r", topic, e)


def _default_is_real_machine(env: Optional[Mapping[str, str]] = None) -> bool:
    e = env if env is not None else os.environ
    rm = (e.get("COCO_REAL_MACHINE") or "0").strip().lower() in {"1", "true", "yes", "on"}
    backend = (e.get("COCO_BACKEND") or "").strip().lower()
    return rm or backend == "robot"


# ---------------------------------------------------------------------------
# env
# ---------------------------------------------------------------------------


def selfheal_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    e = env if env is not None else os.environ
    return (e.get("COCO_SELFHEAL") or "0").strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def build_default_registry(
    *,
    audio_reopen_fn: Optional[Callable[..., bool]] = None,
    asr_restart_fn: Optional[Callable[..., bool]] = None,
    camera_reopen_fn: Optional[Callable[..., bool]] = None,
    is_real_machine_fn: Optional[Callable[[], bool]] = None,
    emit_fn: Optional[Callable[..., None]] = None,
    now_fn: Optional[Callable[[], float]] = None,
    rand_fn: Optional[Callable[[float, float], float]] = None,
) -> SelfHealRegistry:
    """构造默认 registry + 注册 3 个内置策略；real-machine 模式下绑真 reopen_fn。

    main.py 在 sim 模式应传入 lambda **kw: True（或不传）。
    """
    reg = SelfHealRegistry(
        is_real_machine_fn=is_real_machine_fn,
        emit_fn=emit_fn,
        now_fn=now_fn,
        rand_fn=rand_fn,
    )
    reg.register(AudioReopenStrategy(reopen_fn=audio_reopen_fn))
    reg.register(ASRRestartStrategy(reopen_fn=asr_restart_fn))
    reg.register(CameraReopenStrategy(reopen_fn=camera_reopen_fn))
    return reg


__all__ = [
    "SelfHealStrategy",
    "BaseSelfHealStrategy",
    "AudioReopenStrategy",
    "ASRRestartStrategy",
    "CameraReopenStrategy",
    "SelfHealRegistry",
    "StrategyStats",
    "RegistryStats",
    "backoff_for",
    "build_default_registry",
    "selfheal_enabled_from_env",
    "DEFAULT_BACKOFF_BASE_S",
    "DEFAULT_BACKOFF_CAP_S",
    "DEFAULT_BACKOFF_RATIO",
    "DEFAULT_BACKOFF_JITTER",
    "DEFAULT_COOLDOWN_S",
    "DEFAULT_MAX_ATTEMPTS",
]
