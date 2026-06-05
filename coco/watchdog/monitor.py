"""coco.watchdog.monitor: 健康检查 + 重启策略 + run loop.

monitored services (dashboard 不在内)
-----------------------------------
- daemon (reachy-mini daemon): TCP localhost:7447 + HTTP http://localhost:8000/
- coco (主进程): /tmp/coco-frame.jpg mtime < FRAME_MAX_AGE_S (默认 60s) +
  registry pid alive (best-effort)
- copilot-api: HTTP http://localhost:4141/v1/models

env / 默认值
-----------
- COCO_WATCHDOG_INTERVAL_S       默认 30  (>=5, <=600)
- COCO_WATCHDOG_MAX_RESTARTS     默认 3   (>=1, <=20)
- COCO_WATCHDOG_FRAME_MAX_AGE_S  默认 60  (>=5)
- COCO_WATCHDOG_EVENTS_PATH      默认 /tmp/coco-watchdog-events.log
- COCO_WATCHDOG_HTTP_TIMEOUT_S   默认 5

事件日志
--------
每次 health 变化 / 重启尝试 / give-up, 追加 JSON 行到 events.log。
dashboard 红条留 backlog (infra-watchdog-fu-dashboard-redbar)。

重启
----
- 每个 service 维护 failures_count; 健康一次清零
- failures_count >= max_restarts -> give_up=True, 之后不再 attempt restart, 仅
  继续观测 + emit。
- 重启用 registry 里的 cmdline + env 启动子进程; stdout 重定向 /tmp/coco-{name}.log
- 重启后 registry 的 pid 更新

verify / test 友好
-----------------
- HealthCheck 各方法接受 injection (port_checker / http_getter / mtime_getter)
- run_loop(once=True) 跑一轮即返回
- RestartPolicy 与 HealthCheck 解耦, 都纯函数化便于单测
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from coco.watchdog.process_registry import Registry, pid_alive


log = logging.getLogger("coco.watchdog")


# ---------------------------------------------------------------------------
# 默认参数 + env 解析
# ---------------------------------------------------------------------------

DEFAULT_INTERVAL_S = 30.0
INTERVAL_LO = 5.0
INTERVAL_HI = 600.0

DEFAULT_MAX_RESTARTS = 3
MAX_RESTARTS_LO = 1
MAX_RESTARTS_HI = 20

DEFAULT_FRAME_MAX_AGE_S = 60.0
FRAME_MAX_AGE_LO = 5.0

DEFAULT_HTTP_TIMEOUT_S = 5.0
DEFAULT_EVENTS_PATH = "/tmp/coco-watchdog-events.log"
DEFAULT_FRAME_PATH = "/tmp/coco-frame.jpg"

DAEMON_ZENOH_PORT = 7447
DAEMON_HTTP_URL = "http://localhost:8000/"
COPILOT_API_URL = "http://localhost:4141/v1/models"
DASHBOARD_URL = "http://localhost:8765/"
DASHBOARD_PORT = 8765

SERVICE_NAMES = ("daemon", "coco", "dashboard", "copilot-api")


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def env_float(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


@dataclass
class WatchdogConfig:
    interval_s: float = DEFAULT_INTERVAL_S
    max_restarts: int = DEFAULT_MAX_RESTARTS
    frame_max_age_s: float = DEFAULT_FRAME_MAX_AGE_S
    http_timeout_s: float = DEFAULT_HTTP_TIMEOUT_S
    events_path: str = DEFAULT_EVENTS_PATH
    frame_path: str = DEFAULT_FRAME_PATH

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "WatchdogConfig":
        # snapshot then restore os.environ for clean injection
        if env is not None:
            saved = {k: os.environ.get(k) for k in (
                "COCO_WATCHDOG_INTERVAL_S",
                "COCO_WATCHDOG_MAX_RESTARTS",
                "COCO_WATCHDOG_FRAME_MAX_AGE_S",
                "COCO_WATCHDOG_HTTP_TIMEOUT_S",
                "COCO_WATCHDOG_EVENTS_PATH",
                "COCO_WATCHDOG_FRAME_PATH",
            )}
            for k in saved:
                if k in env:
                    os.environ[k] = env[k]
                elif saved[k] is not None:
                    os.environ.pop(k, None)
            try:
                return cls._read_env()
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        return cls._read_env()

    @classmethod
    def _read_env(cls) -> "WatchdogConfig":
        return cls(
            interval_s=env_float(
                "COCO_WATCHDOG_INTERVAL_S",
                DEFAULT_INTERVAL_S,
                INTERVAL_LO,
                INTERVAL_HI,
            ),
            max_restarts=env_int(
                "COCO_WATCHDOG_MAX_RESTARTS",
                DEFAULT_MAX_RESTARTS,
                MAX_RESTARTS_LO,
                MAX_RESTARTS_HI,
            ),
            frame_max_age_s=env_float(
                "COCO_WATCHDOG_FRAME_MAX_AGE_S",
                DEFAULT_FRAME_MAX_AGE_S,
                FRAME_MAX_AGE_LO,
                3600.0,
            ),
            http_timeout_s=env_float(
                "COCO_WATCHDOG_HTTP_TIMEOUT_S",
                DEFAULT_HTTP_TIMEOUT_S,
                0.5,
                60.0,
            ),
            events_path=os.environ.get(
                "COCO_WATCHDOG_EVENTS_PATH", DEFAULT_EVENTS_PATH
            ),
            frame_path=os.environ.get(
                "COCO_WATCHDOG_FRAME_PATH", DEFAULT_FRAME_PATH
            ),
        )


# ---------------------------------------------------------------------------
# 健康检查 (纯函数, 接受 injection)
# ---------------------------------------------------------------------------


def check_port(host: str, port: int, timeout_s: float = 2.0) -> bool:
    """TCP connect 探活; 同时尝试 IPv4 + IPv6 (daemon 可能只 bind ::1)."""
    try:
        infos = socket.getaddrinfo(
            host, int(port), type=socket.SOCK_STREAM
        )
    except (socket.gaierror, OSError):
        return False
    for fam, socktype, proto, _, sa in infos:
        s = None
        try:
            s = socket.socket(fam, socktype, proto)
            s.settimeout(timeout_s)
            s.connect(sa)
            return True
        except (OSError, socket.timeout):
            continue
        finally:
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
    return False


def check_http_200(url: str, timeout_s: float = 5.0) -> bool:
    # 用 stdlib 避免外部依赖
    try:
        from urllib import request, error
        req = request.Request(url, method="GET")
        with request.urlopen(req, timeout=timeout_s) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            return 200 <= int(code) < 300
    except Exception:  # noqa: BLE001  (third-party urllib raises many types)
        return False


def check_file_mtime_fresh(
    path: str, max_age_s: float, now_fn: Callable[[], float] = time.time
) -> bool:
    try:
        st = os.stat(path)
    except (FileNotFoundError, OSError):
        return False
    age = now_fn() - float(st.st_mtime)
    return age <= max_age_s


@dataclass
class HealthCheck:
    """每个 service 一个独立 check 实例; 可注入 fakes."""

    config: WatchdogConfig
    registry: Registry
    # injection points (tests 用)
    port_checker: Callable[[str, int, float], bool] = check_port
    http_getter: Callable[[str, float], bool] = check_http_200
    mtime_checker: Callable[[str, float, Callable[[], float]], bool] = (
        check_file_mtime_fresh
    )
    now_fn: Callable[[], float] = time.time

    def check_daemon(self) -> Tuple[bool, str]:
        port_ok = self.port_checker(
            "localhost", DAEMON_ZENOH_PORT, min(self.config.http_timeout_s, 3.0)
        )
        if not port_ok:
            return False, "tcp_7447_closed"
        http_ok = self.http_getter(DAEMON_HTTP_URL, self.config.http_timeout_s)
        if not http_ok:
            return False, "http_8000_not_200"
        return True, "ok"

    def check_coco(self) -> Tuple[bool, str]:
        fresh = self.mtime_checker(
            self.config.frame_path, self.config.frame_max_age_s, self.now_fn
        )
        if not fresh:
            return False, "frame_stale_or_missing"
        # PID alive 检查 (best-effort, 没注册就只信 frame)
        entry = self.registry.get("coco")
        if entry and entry.get("pid"):
            if not pid_alive(int(entry["pid"])):
                return False, "registered_pid_dead"
        return True, "ok"

    def check_copilot_api(self) -> Tuple[bool, str]:
        ok = self.http_getter(COPILOT_API_URL, self.config.http_timeout_s)
        if not ok:
            return False, "http_4141_not_200"
        return True, "ok"

    def check_dashboard(self) -> Tuple[bool, str]:
        ok = self.http_getter(DASHBOARD_URL, self.config.http_timeout_s)
        if not ok:
            return False, "http_8765_not_200"
        # PID alive 检查 (best-effort; 没注册就只信 HTTP)
        entry = self.registry.get("dashboard")
        if entry and entry.get("pid"):
            if not pid_alive(int(entry["pid"])):
                return False, "registered_pid_dead"
        return True, "ok"

    def check(self, name: str) -> Tuple[bool, str]:
        if name == "daemon":
            return self.check_daemon()
        if name == "coco":
            return self.check_coco()
        if name == "dashboard":
            return self.check_dashboard()
        if name == "copilot-api":
            return self.check_copilot_api()
        return False, f"unknown_service:{name}"


# ---------------------------------------------------------------------------
# 重启策略
# ---------------------------------------------------------------------------


@dataclass
class RestartPolicy:
    """每个 service 独立 failures 计数; 达到 max_restarts 进入 give-up."""

    max_restarts: int = DEFAULT_MAX_RESTARTS
    failures: Dict[str, int] = field(default_factory=dict)
    given_up: Dict[str, bool] = field(default_factory=dict)
    last_attempt_ts: Dict[str, float] = field(default_factory=dict)
    # 上一轮 healthy 状态 (None=未观测过); 仅作 emit 去重用
    last_healthy: Dict[str, Optional[bool]] = field(default_factory=dict)

    def record_failure(self, name: str) -> int:
        n = self.failures.get(name, 0) + 1
        self.failures[name] = n
        if n >= self.max_restarts:
            self.given_up[name] = True
        return n

    def record_success(self, name: str) -> None:
        self.failures[name] = 0
        self.given_up.pop(name, None)

    def give_up(self, name: str) -> bool:
        return bool(self.given_up.get(name, False))

    def attempts(self, name: str) -> int:
        return self.failures.get(name, 0)

    def mark_attempt(self, name: str, ts: float) -> None:
        self.last_attempt_ts[name] = ts


# ---------------------------------------------------------------------------
# Restart launcher (子进程启动)
# ---------------------------------------------------------------------------


def _open_log(name: str) -> Any:
    path = f"/tmp/coco-{name}.log"
    try:
        return open(path, "ab", buffering=0)
    except OSError:
        return subprocess.DEVNULL


def launch_from_registry(
    name: str,
    registry: Registry,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
) -> Optional[int]:
    """用 registry 里的 cmdline+env 起子进程; 返回新 PID 或 None.

    stdout/stderr 重定向 /tmp/coco-{name}.log (append)。
    """
    entry = registry.get(name)
    if not entry:
        log.warning("watchdog.restart: no registry entry for %s", name)
        return None
    cmdline = entry.get("cmdline") or []
    if not cmdline:
        log.warning("watchdog.restart: empty cmdline for %s", name)
        return None
    env = entry.get("env") or {}
    if not env:
        env = dict(os.environ)
    log_fh = _open_log(name)
    try:
        proc = popen(
            cmdline,
            env=env,
            stdout=log_fh,
            stderr=log_fh,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        log.error("watchdog.restart: popen failed for %s: %r", name, exc)
        return None
    pid = getattr(proc, "pid", None)
    if pid:
        registry.register(
            name,
            pid=int(pid),
            cmdline=list(cmdline),
            env=dict(env),
            ports=entry.get("ports") or [],
        )
        try:
            registry.save()
        except OSError as exc:
            log.warning("watchdog.restart: registry save failed: %r", exc)
    return pid


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------


def emit_event(path: str, kind: str, **payload: Any) -> None:
    """追加一行 JSON 到 events.log; 失败静默."""
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
    }
    rec.update(payload)
    try:
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        line = json.dumps({"ts": rec["ts"], "kind": kind, "_drop": True})
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Run loop
# ---------------------------------------------------------------------------


def tick_once(
    config: WatchdogConfig,
    registry: Registry,
    policy: RestartPolicy,
    health: HealthCheck,
    *,
    services: Tuple[str, ...] = SERVICE_NAMES,
    launcher: Callable[..., Optional[int]] = launch_from_registry,
    event_sink: Callable[..., None] = emit_event,
    always_emit: Optional[bool] = None,
) -> Dict[str, Dict[str, Any]]:
    """跑一轮所有 service 的 check + 必要时 restart; 返回结果摘要.

    always_emit:
      - True  → 每轮 tick 都 emit ``health.healthy`` / ``health.degraded``
      - False → 仅在状态变化时 emit (旧行为)
      - None  → 读 env ``COCO_WATCHDOG_ALWAYS_EMIT`` (默认 "1" = True)
    """
    if always_emit is None:
        always_emit = env_bool("COCO_WATCHDOG_ALWAYS_EMIT", True)
    summary: Dict[str, Dict[str, Any]] = {}
    for name in services:
        try:
            ok, reason = health.check(name)
        except Exception as exc:  # noqa: BLE001
            ok, reason = False, f"check_raised:{exc!r}"
        entry: Dict[str, Any] = {
            "healthy": ok,
            "reason": reason,
            "restart_attempted": False,
            "give_up": policy.give_up(name),
        }
        prev = policy.last_healthy.get(name)
        state_changed = prev != ok
        if ok:
            policy.record_success(name)
            if always_emit or state_changed:
                event_sink(
                    config.events_path,
                    "health.healthy",
                    service=name,
                    reason=reason,
                )
        else:
            n = policy.record_failure(name)
            if always_emit or state_changed:
                event_sink(
                    config.events_path,
                    "health.degraded",
                    service=name,
                    reason=reason,
                    failures=n,
                )
            if not policy.give_up(name):
                # 重启 (有 registry 时)
                if registry.get(name):
                    new_pid = launcher(name, registry)
                    entry["restart_attempted"] = True
                    entry["new_pid"] = new_pid
                    policy.mark_attempt(name, time.time())
                    event_sink(
                        config.events_path,
                        "restart.attempted",
                        service=name,
                        attempt=n,
                        new_pid=new_pid,
                    )
                else:
                    event_sink(
                        config.events_path,
                        "restart.skipped_no_registry",
                        service=name,
                    )
            else:
                event_sink(
                    config.events_path,
                    "restart.give_up",
                    service=name,
                    failures=n,
                )
                entry["give_up"] = True
        policy.last_healthy[name] = ok
        summary[name] = entry
    return summary


def run_loop(
    config: Optional[WatchdogConfig] = None,
    registry: Optional[Registry] = None,
    policy: Optional[RestartPolicy] = None,
    health: Optional[HealthCheck] = None,
    *,
    once: bool = False,
    max_iters: Optional[int] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    event_sink: Callable[..., None] = emit_event,
    launcher: Callable[..., Optional[int]] = launch_from_registry,
) -> int:
    """主 loop. once=True 跑一次返回; max_iters=N 跑 N 次返回."""
    cfg = config or WatchdogConfig.from_env()
    reg = registry or Registry()
    reg.load()
    pol = policy or RestartPolicy(max_restarts=cfg.max_restarts)
    hc = health or HealthCheck(config=cfg, registry=reg)

    event_sink(cfg.events_path, "watchdog.started", config={
        "interval_s": cfg.interval_s,
        "max_restarts": cfg.max_restarts,
        "frame_max_age_s": cfg.frame_max_age_s,
    })

    iters = 0
    try:
        while True:
            tick_once(cfg, reg, pol, hc, launcher=launcher, event_sink=event_sink)
            iters += 1
            if once or (max_iters is not None and iters >= max_iters):
                break
            sleep_fn(cfg.interval_s)
    except KeyboardInterrupt:
        event_sink(cfg.events_path, "watchdog.stopped", reason="keyboard_interrupt")
        return 130
    event_sink(cfg.events_path, "watchdog.stopped", reason="loop_exit", iters=iters)
    return 0
