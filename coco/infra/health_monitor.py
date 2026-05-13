"""infra-005: HealthMonitor —— daemon 自愈 + 多源观测。

设计
====

一层独立于 logging / metrics 的「健康观测层」：

- 周期 ``tick_s`` 采样四类探针：
    1. daemon Zenoh 心跳（注入 ``daemon_heartbeat_probe`` callable，返回 epoch ts 或 None）
    2. sounddevice 流活跃度（注入 ``stream_active_probe`` callable，返回 bool 或 None）
    3. ASR / LLM 最近 N=200 条延迟 p50/p95（内部 ring buffer，通过 ``record_latency`` 喂入）
    4. 主线程 watchdog：tick 之间 wall-clock 间隔 > 阈值 → 告警
- 任一探针 fail → emit ``health.degraded``(component=..., reason=..., value=..., threshold=...);
  上次 degraded、本次 healthy → emit ``health.recovered``(component=...). 状态机用 latched
  边沿触发，防止 tick-by-tick 事件风暴。
- daemon 心跳静默 >= ``daemon_silence_threshold_s`` 时：
    * sim 模式（``is_real_machine_fn() == False``）：触发 ``daemon_restart_fn`` 一次；30s
      cooldown + max 3 retry；超过 max → emit ``health.daemon_giveup`` 后不再重试。
    * 真机模式：仅 emit ``health.degraded``，不重启子进程。
- ring buffer 上限 200 条 / component，FIFO 丢旧，不无限增长。
- env：``COCO_HEALTH=1``（默认 OFF）；``COCO_HEALTH_TICK_S`` / ``COCO_HEALTH_RESTART_COOLDOWN_S``
  / ``COCO_HEALTH_DAEMON_SILENCE_S`` / ``COCO_REAL_MACHINE=1`` 可调。
- Default-OFF：未启用时 HealthMonitor 不构造、不起线程，行为完全等同今天。

emit topics
===========

- ``health.degraded`` —— 单项观测变坏（component / reason / value / threshold）
- ``health.recovered`` —— 单项观测恢复
- ``health.tick_lag`` —— 主线程 watchdog 触发
- ``health.restart_attempted`` —— daemon restart fn 被调用（attempt 计数）
- ``health.restart_succeeded`` —— restart fn 返回非 None（暂未用，留接口）
- ``health.restart_failed`` —— restart fn 抛异常
- ``health.daemon_giveup`` —— 超过 max retry，停止重试

线程模型
========

- ``start(stop_event)``：起后台 daemon 线程跑 tick；幂等。
- ``stop(timeout)``：set 内部 stop event + join；干净退出。
- ``tick_once()``：同步跑一轮，test 路径用。

Sim-first：所有外部依赖（heartbeat / stream / restart）都用注入的 callable，verify
路径可注入 fake fn 完全在内存里跑。
"""

from __future__ import annotations

import logging
import os
import statistics
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Mapping, Optional

from coco.logging_setup import emit


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 默认参数
# ---------------------------------------------------------------------------

DEFAULT_TICK_S = 5.0
TICK_LO = 0.1
TICK_HI = 60.0

DEFAULT_DAEMON_SILENCE_THRESHOLD_S = 60.0
DEFAULT_RESTART_COOLDOWN_S = 30.0
DEFAULT_MAX_RESTART_RETRIES = 3
DEFAULT_WATCHDOG_LAG_THRESHOLD_S = 5.0  # tick=5s 时，> 5s 即 lag（间隔 2x 即视为卡）
DEFAULT_LATENCY_WINDOW = 200
DEFAULT_ASR_P95_THRESHOLD_MS = 2000.0
DEFAULT_LLM_P95_THRESHOLD_MS = 3000.0


# ---------------------------------------------------------------------------
# 默认探针实现（可被注入覆盖）
# ---------------------------------------------------------------------------


def default_daemon_heartbeat_probe() -> Optional[float]:
    """默认 daemon 心跳探针：用 pgrep 检查 ``desktop-app-daemon`` / ``reachy_mini.daemon``
    PID 是否存在；存在则返回当前时间戳；否则返回 None。

    sim 路径下可被注入的 fake probe 替换；真实 verify 路径不依赖此默认。
    """
    try:
        for pattern in ("desktop-app-daemon", "reachy_mini.daemon"):
            r = subprocess.run(
                ["pgrep", "-f", pattern],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            )
            if r.returncode == 0:
                return time.time()
        return None
    except Exception as e:  # noqa: BLE001
        log.debug("[health] default heartbeat probe failed: %r", e)
        return None


def default_daemon_restart_fn() -> Optional[Any]:
    """默认 daemon 自愈：spawn ``reachy_mini.daemon`` mockup-sim 子进程。

    仅在 sim 路径下被调用（真机模式 HealthMonitor 不调它）。verify 路径用注入
    的 fake fn，不会真起子进程。
    """
    import sys

    cmd = [
        sys.executable, "-m", "reachy_mini.daemon.app.main",
        "--mockup-sim", "--deactivate-audio", "--localhost-only",
    ]
    p = subprocess.Popen(  # noqa: S603
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return p


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------


@dataclass
class HealthStats:
    ticks: int = 0
    errors: int = 0
    degraded_emits: int = 0
    recovered_emits: int = 0
    restart_attempts: int = 0
    restart_failures: int = 0
    last_tick_ts: float = 0.0


@dataclass
class _ComponentState:
    """单个观测项的 latched 状态机；degraded/recovered 边沿触发。"""

    degraded: bool = False
    last_reason: str = ""


# ---------------------------------------------------------------------------
# HealthMonitor
# ---------------------------------------------------------------------------


class HealthMonitor:
    """周期采样 + emit + daemon 自愈。

    所有外部依赖通过 callable 注入；verify 路径不依赖真 daemon / 真 sounddevice /
    真 metrics 写盘。
    """

    def __init__(
        self,
        *,
        tick_s: float = DEFAULT_TICK_S,
        daemon_silence_threshold_s: float = DEFAULT_DAEMON_SILENCE_THRESHOLD_S,
        restart_cooldown_s: float = DEFAULT_RESTART_COOLDOWN_S,
        max_restart_retries: int = DEFAULT_MAX_RESTART_RETRIES,
        watchdog_lag_threshold_s: float = DEFAULT_WATCHDOG_LAG_THRESHOLD_S,
        latency_window_n: int = DEFAULT_LATENCY_WINDOW,
        asr_p95_threshold_ms: float = DEFAULT_ASR_P95_THRESHOLD_MS,
        llm_p95_threshold_ms: float = DEFAULT_LLM_P95_THRESHOLD_MS,
        daemon_heartbeat_probe: Optional[Callable[[], Optional[float]]] = None,
        stream_active_probe: Optional[Callable[[], Optional[bool]]] = None,
        daemon_restart_fn: Optional[Callable[[], Any]] = None,
        is_real_machine_fn: Optional[Callable[[], bool]] = None,
        emit_fn: Optional[Callable[..., None]] = None,
        now_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        # 参数 clamp，防呆
        self.tick_s = max(TICK_LO, min(TICK_HI, float(tick_s)))
        self.daemon_silence_threshold_s = max(1.0, float(daemon_silence_threshold_s))
        self.restart_cooldown_s = max(0.0, float(restart_cooldown_s))
        self.max_restart_retries = max(0, int(max_restart_retries))
        self.watchdog_lag_threshold_s = max(0.1, float(watchdog_lag_threshold_s))
        self.latency_window_n = max(1, int(latency_window_n))
        self.asr_p95_threshold_ms = float(asr_p95_threshold_ms)
        self.llm_p95_threshold_ms = float(llm_p95_threshold_ms)

        self._daemon_heartbeat_probe = daemon_heartbeat_probe or default_daemon_heartbeat_probe
        self._stream_active_probe = stream_active_probe  # None → skip
        self._daemon_restart_fn = daemon_restart_fn or default_daemon_restart_fn
        self._is_real_machine_fn = is_real_machine_fn or _default_is_real_machine
        self._emit = emit_fn or _safe_emit
        self._now = now_fn or time.time

        # 状态
        self._components: Dict[str, _ComponentState] = {
            "daemon": _ComponentState(),
            "sounddevice": _ComponentState(),
            "asr_latency": _ComponentState(),
            "llm_latency": _ComponentState(),
            "watchdog": _ComponentState(),
        }
        self._latencies: Dict[str, Deque[float]] = {
            "asr": deque(maxlen=self.latency_window_n),
            "llm": deque(maxlen=self.latency_window_n),
        }
        self._lock = threading.Lock()

        # daemon restart 状态
        self._restart_attempts = 0
        self._last_restart_ts = 0.0
        self._daemon_gaveup = False
        # 子进程 handle（restart_fn 返回 Popen-like 对象时保存，stop() 时 terminate）
        self._daemon_child: Any = None

        # 线程
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # tick 时序 + 统计
        self.stats = HealthStats()

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def record_latency(self, component: str, value_ms: float) -> None:
        """喂入一条延迟（毫秒）。ASR / LLM 在主路径主动调；超容量 FIFO 丢旧。"""
        if component not in self._latencies:
            return
        try:
            v = float(value_ms)
        except (TypeError, ValueError):
            return
        if v < 0:
            return
        with self._lock:
            self._latencies[component].append(v)

    def latency_p50_p95(self, component: str) -> Optional[tuple]:
        """读 (p50, p95) 当前窗口；样本不足返回 None。"""
        with self._lock:
            samples = list(self._latencies.get(component, ()))
        if len(samples) < 5:  # 样本太少不做断言
            return None
        samples.sort()
        n = len(samples)
        p50 = samples[n // 2]
        # 简单 p95：index = ceil(0.95 * (n-1))
        idx95 = max(0, min(n - 1, int(round(0.95 * (n - 1)))))
        p95 = samples[idx95]
        return (p50, p95)

    # ------------------------------------------------------------------
    # tick
    # ------------------------------------------------------------------

    def tick_once(self) -> Dict[str, Any]:
        """同步跑一轮：四类探针 + degraded/recovered 状态机 + daemon restart。

        返回本轮 snapshot（test 用）。fail-soft：任一探针抛异常 → stats.errors++ + 继续。
        """
        now = self._now()

        # 1. watchdog：用 wall-clock 间隔判断 tick 是否被卡
        last_tick = self.stats.last_tick_ts
        if last_tick > 0:
            lag = now - last_tick - self.tick_s
            # lag > threshold 视为主线程卡
            if lag > self.watchdog_lag_threshold_s:
                self._mark_degraded(
                    "watchdog",
                    reason="tick_lag",
                    value=round(lag, 3),
                    threshold=self.watchdog_lag_threshold_s,
                )
                try:
                    self._emit(
                        "health.tick_lag",
                        lag_s=round(lag, 3),
                        threshold_s=self.watchdog_lag_threshold_s,
                    )
                except Exception:  # noqa: BLE001
                    self.stats.errors += 1
            else:
                self._mark_recovered("watchdog")

        # 2. daemon heartbeat
        try:
            last_hb = self._daemon_heartbeat_probe()
        except Exception as e:  # noqa: BLE001
            log.debug("[health] daemon heartbeat probe raised: %r", e)
            self.stats.errors += 1
            last_hb = None
        daemon_silent = False
        if last_hb is None:
            daemon_silent = True
        else:
            silence_age = now - float(last_hb)
            if silence_age > self.daemon_silence_threshold_s:
                daemon_silent = True
        if daemon_silent:
            self._mark_degraded(
                "daemon",
                reason="heartbeat_silence",
                value=None if last_hb is None else round(now - float(last_hb), 3),
                threshold=self.daemon_silence_threshold_s,
            )
            self._maybe_restart_daemon(now=now)
        else:
            self._mark_recovered("daemon")

        # 3. sounddevice stream active
        if self._stream_active_probe is not None:
            try:
                active = self._stream_active_probe()
            except Exception as e:  # noqa: BLE001
                log.debug("[health] stream probe raised: %r", e)
                self.stats.errors += 1
                active = None
            if active is False:
                self._mark_degraded(
                    "sounddevice",
                    reason="stream_inactive",
                    value=False,
                    threshold=True,
                )
            elif active is True:
                self._mark_recovered("sounddevice")
            # None → skip（探针不可用，不动状态）

        # 4. ASR / LLM latency
        self._check_latency_slo("asr", "asr_latency", self.asr_p95_threshold_ms)
        self._check_latency_slo("llm", "llm_latency", self.llm_p95_threshold_ms)

        # 收尾：tick 计数 + last_tick_ts
        self.stats.ticks += 1
        self.stats.last_tick_ts = now
        return {
            "now": now,
            "daemon_silent": daemon_silent,
            "restart_attempts": self._restart_attempts,
            "gaveup": self._daemon_gaveup,
            "components": {k: v.degraded for k, v in self._components.items()},
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _check_latency_slo(self, lat_key: str, comp_key: str, threshold_ms: float) -> None:
        stats = self.latency_p50_p95(lat_key)
        if stats is None:
            # 样本不够 → 不动状态
            return
        p50, p95 = stats
        if p95 > threshold_ms:
            self._mark_degraded(
                comp_key,
                reason="p95_over_threshold",
                value=round(p95, 1),
                threshold=threshold_ms,
                p50=round(p50, 1),
            )
        else:
            self._mark_recovered(comp_key)

    def _mark_degraded(
        self,
        component: str,
        *,
        reason: str,
        value: Any,
        threshold: Any,
        **extra: Any,
    ) -> None:
        st = self._components.setdefault(component, _ComponentState())
        if st.degraded and st.last_reason == reason:
            # 已 latched 同样 reason，跳过 emit（防风暴）
            return
        st.degraded = True
        st.last_reason = reason
        try:
            self._emit(
                "health.degraded",
                component=component,
                reason=reason,
                value=value,
                threshold=threshold,
                **extra,
            )
            self.stats.degraded_emits += 1
        except Exception:  # noqa: BLE001
            self.stats.errors += 1

    def _mark_recovered(self, component: str) -> None:
        st = self._components.setdefault(component, _ComponentState())
        if not st.degraded:
            return
        st.degraded = False
        prev_reason = st.last_reason
        st.last_reason = ""
        try:
            self._emit(
                "health.recovered",
                component=component,
                prev_reason=prev_reason,
            )
            self.stats.recovered_emits += 1
        except Exception:  # noqa: BLE001
            self.stats.errors += 1

    def _maybe_restart_daemon(self, *, now: float) -> None:
        """sim 模式 daemon 60s 无心跳 → restart；真机模式仅告警不重启。"""
        # 真机模式：仅告警（_mark_degraded 已 emit 过 health.degraded）；不重启
        try:
            real = bool(self._is_real_machine_fn())
        except Exception:  # noqa: BLE001
            real = False
        if real:
            return

        # 已 giveup：永不再试，直到外部 reset_restart_state
        if self._daemon_gaveup:
            return

        # cooldown 检查
        if (now - self._last_restart_ts) < self.restart_cooldown_s and self._last_restart_ts > 0:
            return

        # 已超过 max retry → emit giveup 一次
        if self._restart_attempts >= self.max_restart_retries:
            self._daemon_gaveup = True
            try:
                self._emit(
                    "health.daemon_giveup",
                    attempts=self._restart_attempts,
                    max_retries=self.max_restart_retries,
                )
            except Exception:  # noqa: BLE001
                self.stats.errors += 1
            return

        # 触发一次 restart
        self._restart_attempts += 1
        self._last_restart_ts = now
        self.stats.restart_attempts += 1
        try:
            self._emit(
                "health.restart_attempted",
                attempt=self._restart_attempts,
                max_retries=self.max_restart_retries,
            )
        except Exception:  # noqa: BLE001
            self.stats.errors += 1
        try:
            child = self._daemon_restart_fn()
            if child is not None:
                self._daemon_child = child
        except Exception as e:  # noqa: BLE001
            self.stats.restart_failures += 1
            try:
                self._emit(
                    "health.restart_failed",
                    attempt=self._restart_attempts,
                    error=type(e).__name__,
                    message=str(e)[:200],
                )
            except Exception:  # noqa: BLE001
                self.stats.errors += 1

    def reset_restart_state(self) -> None:
        """test / 运维路径：daemon 心跳恢复后清零 attempts + giveup。"""
        self._restart_attempts = 0
        self._last_restart_ts = 0.0
        self._daemon_gaveup = False

    # ------------------------------------------------------------------
    # 线程
    # ------------------------------------------------------------------

    def _run(self) -> None:
        # 第一轮立刻跑，但不计 watchdog lag（用 last_tick_ts 控制）
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception as e:  # noqa: BLE001
                log.warning("[health] tick failed: %r", e)
                self.stats.errors += 1
            if self._stop.wait(timeout=self.tick_s):
                break

    def start(self, stop_event: Optional[threading.Event] = None) -> None:
        """起后台 daemon 线程；幂等。可传入外部 stop_event 桥接。"""
        if self._thread is not None and self._thread.is_alive():
            return
        if stop_event is not None:
            def _bridge(_e=stop_event, _s=self._stop) -> None:
                while not _s.is_set():
                    if _e.wait(timeout=0.5):
                        _s.set()
                        return

            threading.Thread(target=_bridge, name="coco-health-stop-bridge", daemon=True).start()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="coco-health", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """干净退出：set stop event + join + terminate 任何 spawn 的子进程。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        # 清理 spawn 的 daemon 子进程（若有），防泄漏
        child = self._daemon_child
        if child is not None:
            try:
                terminator = getattr(child, "terminate", None)
                if callable(terminator):
                    terminator()
            except Exception:  # noqa: BLE001
                pass
            self._daemon_child = None
        # 清理 ring buffer（释放内存 + verify 断言）
        with self._lock:
            for k in list(self._latencies.keys()):
                self._latencies[k].clear()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# ---------------------------------------------------------------------------
# env
# ---------------------------------------------------------------------------


def health_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    e = env if env is not None else os.environ
    return (e.get("COCO_HEALTH") or "0").strip().lower() in {"1", "true", "yes", "on"}


def tick_from_env(env: Optional[Mapping[str, str]] = None) -> float:
    e = env if env is not None else os.environ
    raw = (e.get("COCO_HEALTH_TICK_S") or "").strip()
    if not raw:
        return DEFAULT_TICK_S
    try:
        v = float(raw)
    except ValueError:
        log.warning("[health] COCO_HEALTH_TICK_S=%r 非数字，回退 %.1f", raw, DEFAULT_TICK_S)
        return DEFAULT_TICK_S
    return max(TICK_LO, min(TICK_HI, v))


def restart_cooldown_from_env(env: Optional[Mapping[str, str]] = None) -> float:
    e = env if env is not None else os.environ
    raw = (e.get("COCO_HEALTH_RESTART_COOLDOWN_S") or "").strip()
    if not raw:
        return DEFAULT_RESTART_COOLDOWN_S
    try:
        v = float(raw)
    except ValueError:
        return DEFAULT_RESTART_COOLDOWN_S
    return max(0.0, v)


def daemon_silence_from_env(env: Optional[Mapping[str, str]] = None) -> float:
    e = env if env is not None else os.environ
    raw = (e.get("COCO_HEALTH_DAEMON_SILENCE_S") or "").strip()
    if not raw:
        return DEFAULT_DAEMON_SILENCE_THRESHOLD_S
    try:
        v = float(raw)
    except ValueError:
        return DEFAULT_DAEMON_SILENCE_THRESHOLD_S
    return max(1.0, v)


def _default_is_real_machine(env: Optional[Mapping[str, str]] = None) -> bool:
    """真机 / sim 判定：``COCO_REAL_MACHINE=1`` 或 ``COCO_BACKEND=robot``。"""
    e = env if env is not None else os.environ
    rm = (e.get("COCO_REAL_MACHINE") or "0").strip().lower() in {"1", "true", "yes", "on"}
    backend = (e.get("COCO_BACKEND") or "").strip().lower()
    return rm or backend == "robot"


def _safe_emit(component_event: str, **payload: Any) -> None:
    """包一层 try：emit 失败不应炸 HealthMonitor。"""
    try:
        emit(component_event, **payload)
    except Exception as e:  # noqa: BLE001
        log.debug("[health] emit %s failed: %r", component_event, e)


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def build_health_monitor(
    *,
    env: Optional[Mapping[str, str]] = None,
    daemon_heartbeat_probe: Optional[Callable[[], Optional[float]]] = None,
    stream_active_probe: Optional[Callable[[], Optional[bool]]] = None,
    daemon_restart_fn: Optional[Callable[[], Any]] = None,
    is_real_machine_fn: Optional[Callable[[], bool]] = None,
    emit_fn: Optional[Callable[..., None]] = None,
    now_fn: Optional[Callable[[], float]] = None,
) -> HealthMonitor:
    """读 env 构造默认 HealthMonitor（不会自动 start）。"""
    return HealthMonitor(
        tick_s=tick_from_env(env),
        daemon_silence_threshold_s=daemon_silence_from_env(env),
        restart_cooldown_s=restart_cooldown_from_env(env),
        daemon_heartbeat_probe=daemon_heartbeat_probe,
        stream_active_probe=stream_active_probe,
        daemon_restart_fn=daemon_restart_fn,
        is_real_machine_fn=is_real_machine_fn or (lambda: _default_is_real_machine(env)),
        emit_fn=emit_fn,
        now_fn=now_fn,
    )


__all__ = [
    "HealthMonitor",
    "HealthStats",
    "build_health_monitor",
    "health_enabled_from_env",
    "tick_from_env",
    "restart_cooldown_from_env",
    "daemon_silence_from_env",
    "default_daemon_heartbeat_probe",
    "default_daemon_restart_fn",
    "DEFAULT_TICK_S",
    "DEFAULT_DAEMON_SILENCE_THRESHOLD_S",
    "DEFAULT_RESTART_COOLDOWN_S",
    "DEFAULT_MAX_RESTART_RETRIES",
    "DEFAULT_LATENCY_WINDOW",
    "DEFAULT_ASR_P95_THRESHOLD_MS",
    "DEFAULT_LLM_P95_THRESHOLD_MS",
]
