"""coco.watchdog.process_registry: PID + cmdline + env + ports 持久化.

存储位置: ``~/.cache/coco/processes.json`` (XDG_CACHE_HOME 优先).

schema::

    {
      "daemon": {
        "pid": 45165,
        "started_at": "2026-06-05T12:34:56+00:00",
        "cmdline": ["python", "-m", "reachy_mini.daemon.app.main", ...],
        "env": {"PATH": "...", ...},
        "ports": [7447, 8000]
      },
      "coco": {...},
      "copilot-api": {...}
    }

discover(name, port): 通过端口找到运行中 PID，从 ``ps``/``psutil`` 拉 cmdline，
自动 register。失败返回 None。

settle 行为
-----------
- 文件不存在 / JSON 解析失败 → 空 dict, 不抛 (registry 是 best-effort 状态)
- save() 写临时文件 + atomic rename, 防 watchdog 崩在写中途
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple


def _default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "coco"
    home = Path(os.path.expanduser("~"))
    return home / ".cache" / "coco"


DEFAULT_REGISTRY_PATH = _default_cache_dir() / "processes.json"


class Registry:
    """进程注册表 (单文件 JSON 持久化)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_REGISTRY_PATH
        self._data: Dict[str, Dict[str, Any]] = {}

    # -- I/O ---------------------------------------------------------------

    def load(self) -> Dict[str, Dict[str, Any]]:
        """从磁盘加载；文件不存在或损坏返回空 dict (不抛)."""
        try:
            text = self.path.read_text(encoding="utf-8")
            obj = json.loads(text)
            if isinstance(obj, dict):
                # shallow-validate: only dict-valued entries kept
                self._data = {
                    k: v for k, v in obj.items() if isinstance(v, dict)
                }
            else:
                self._data = {}
        except FileNotFoundError:
            self._data = {}
        except (OSError, json.JSONDecodeError):
            self._data = {}
        return dict(self._data)

    def save(self) -> Path:
        """原子写到 self.path; 返回最终路径."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # NamedTemporaryFile in same dir then os.replace for atomicity
        fd, tmp = tempfile.mkstemp(
            prefix=".processes.", suffix=".json.tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False, sort_keys=True)
                f.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
        return self.path

    # -- mutation ----------------------------------------------------------

    def register(
        self,
        name: str,
        *,
        pid: Optional[int],
        cmdline: List[str],
        env: Optional[Mapping[str, str]] = None,
        ports: Optional[List[int]] = None,
        started_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """注册 (或覆盖) 一项；返回写入的条目。"""
        entry: Dict[str, Any] = {
            "pid": int(pid) if pid is not None else None,
            "cmdline": list(cmdline),
            "env": dict(env) if env else {},
            "ports": list(ports) if ports else [],
            "started_at": started_at
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._data[name] = entry
        return entry

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        return self._data.get(name)

    def remove(self, name: str) -> bool:
        return self._data.pop(name, None) is not None

    def all(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._data)

    # -- discovery ---------------------------------------------------------

    def discover(self, name: str, port: int) -> Optional[Dict[str, Any]]:
        """从 port 找到 PID, 拉 cmdline/env, 自动 register; 失败返回 None."""
        pid = _pid_from_port(port)
        if pid is None:
            return None
        cmdline = _cmdline_from_pid(pid) or []
        env = _env_from_pid(pid) or {}
        return self.register(
            name, pid=pid, cmdline=cmdline, env=env, ports=[port]
        )

    def discover_by_cmdline(
        self,
        name: str,
        *,
        ports: Optional[List[int]] = None,
        proc_lister: Optional[Callable[[], List[Tuple[int, List[str]]]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """按 cmdline 关键字找进程, 自动 register; 失败返回 None.

        识别规则:
          - 'dashboard'   : cmdline 含 '-m coco.dashboard'
          - 'coco'        : cmdline 含 '-m coco' (且不是 coco.dashboard / coco.watchdog
                            等子模块, strict match)
          - 'watchdog'    : cmdline 含 '-m coco.watchdog'

        识别顺序: 调用者应先 dashboard / watchdog 后 coco (避免 'python -m coco'
        被先匹配吞掉 coco.dashboard / coco.watchdog).

        proc_lister: 测试注入, 返回 (pid, cmdline) 列表; 默认走 psutil + ps.
        """
        if name not in CMDLINE_RULES:
            return None
        rule = CMDLINE_RULES[name]
        lister = proc_lister or _list_python_processes
        for pid, cmdline in lister():
            if rule(cmdline):
                env = _env_from_pid(pid) or {}
                return self.register(
                    name, pid=pid, cmdline=cmdline, env=env, ports=ports or []
                )
        return None

    def discover_all(
        self,
        *,
        proc_lister: Optional[Callable[[], List[Tuple[int, List[str]]]]] = None,
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """识别所有已知 service; 顺序: dashboard > watchdog > coco; daemon /
        copilot-api 仍走端口 discover. 返回 {name: entry-or-None}.
        """
        out: Dict[str, Optional[Dict[str, Any]]] = {}
        out["daemon"] = self.discover("daemon", 7447) if not self.get("daemon") else self.get("daemon")
        out["copilot-api"] = (
            self.discover("copilot-api", 4141)
            if not self.get("copilot-api")
            else self.get("copilot-api")
        )
        # 顺序: 优先 dashboard, 再 watchdog, 最后 coco; 避免 'python -m coco' 误吞
        for nm in ("dashboard", "watchdog", "coco"):
            out[nm] = self.discover_by_cmdline(
                nm,
                ports=([8765] if nm == "dashboard" else None),
                proc_lister=proc_lister,
            )
        return out


# ---------------------------------------------------------------------------
# helpers (no external deps required; degrade gracefully)
# ---------------------------------------------------------------------------


def _pid_from_port(port: int) -> Optional[int]:
    """优先 psutil; 退回 lsof; 都不可用返 None。"""
    pid = _pid_from_port_psutil(port)
    if pid is not None:
        return pid
    return _pid_from_port_lsof(port)


def _pid_from_port_psutil(port: int) -> Optional[int]:
    try:
        import psutil  # type: ignore
    except ImportError:
        return None
    try:
        for conn in psutil.net_connections(kind="inet"):
            la = getattr(conn, "laddr", None)
            if la and getattr(la, "port", None) == port and conn.pid:
                return int(conn.pid)
    except (psutil.AccessDenied, psutil.Error, OSError):
        return None
    return None


def _pid_from_port_lsof(port: int) -> Optional[int]:
    lsof = shutil.which("lsof")
    if not lsof:
        return None
    try:
        out = subprocess.run(
            [lsof, "-iTCP:%d" % port, "-sTCP:LISTEN", "-t", "-n", "-P"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    line = out.stdout.strip().splitlines()
    if not line:
        return None
    try:
        return int(line[0])
    except ValueError:
        return None


def _cmdline_from_pid(pid: int) -> Optional[List[str]]:
    """优先 psutil; 退回 ps; macOS 没有 /proc, Linux 有。"""
    try:
        import psutil  # type: ignore
        try:
            return list(psutil.Process(pid).cmdline())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
    except ImportError:
        pass
    # /proc fallback (Linux)
    proc = Path("/proc") / str(pid) / "cmdline"
    if proc.exists():
        try:
            raw = proc.read_bytes()
            parts = [p.decode("utf-8", "replace") for p in raw.split(b"\x00") if p]
            return parts or None
        except OSError:
            return None
    # ps fallback (mac)
    ps = shutil.which("ps")
    if not ps:
        return None
    try:
        out = subprocess.run(
            [ps, "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    s = out.stdout.strip()
    if not s:
        return None
    # 简单 split; 这里不还原 quoted args, 仅供 best-effort 重启
    return s.split()


def _env_from_pid(pid: int) -> Optional[Dict[str, str]]:
    try:
        import psutil  # type: ignore
        try:
            return dict(psutil.Process(pid).environ())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
    except ImportError:
        pass
    # /proc fallback
    proc = Path("/proc") / str(pid) / "environ"
    if proc.exists():
        try:
            raw = proc.read_bytes()
            result: Dict[str, str] = {}
            for chunk in raw.split(b"\x00"):
                if not chunk:
                    continue
                k, eq, v = chunk.decode("utf-8", "replace").partition("=")
                if eq:
                    result[k] = v
            return result
        except OSError:
            return None
    return None


def pid_alive(pid: int) -> bool:
    """OS-agnostic 进程存活检查 (signal 0)."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # 存在但属于别用户; 视作 alive
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# cmdline-based discovery rules
# ---------------------------------------------------------------------------


def _cmdline_has_module(cmdline: List[str], module: str) -> bool:
    """检测 cmdline 是否含 ``-m <module>`` (相邻 token, strict)."""
    if not cmdline or not module:
        return False
    for i, tok in enumerate(cmdline[:-1]):
        if tok == "-m" and cmdline[i + 1] == module:
            return True
    return False


# 注: 'coco' 必须 strict (= '-m coco', 而不是 '-m coco.dashboard'); 调用 discover_all
# 时顺序保证 dashboard/watchdog 先识别走, 'coco' 最后兜底
CMDLINE_RULES: Dict[str, Callable[[List[str]], bool]] = {
    "dashboard": lambda cl: _cmdline_has_module(cl, "coco.dashboard"),
    "watchdog": lambda cl: _cmdline_has_module(cl, "coco.watchdog"),
    "coco": lambda cl: _cmdline_has_module(cl, "coco"),
}


def _list_python_processes() -> List[Tuple[int, List[str]]]:
    """列出系统上 python 进程的 (pid, cmdline); 失败返空 list."""
    out: List[Tuple[int, List[str]]] = []
    try:
        import psutil  # type: ignore
    except ImportError:
        # ps fallback (mac/Linux)
        ps = shutil.which("ps")
        if not ps:
            return out
        try:
            res = subprocess.run(
                [ps, "-eo", "pid=,command="],
                capture_output=True,
                text=True,
                timeout=3.0,
            )
        except (subprocess.SubprocessError, OSError):
            return out
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            cmd = parts[1]
            if "python" not in cmd:
                continue
            out.append((pid, cmd.split()))
        return out
    # psutil path
    try:
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if not cmdline:
                continue
            first = cmdline[0] if cmdline else ""
            if "python" not in first.lower():
                continue
            out.append((int(proc.info["pid"]), list(cmdline)))
    except (psutil.AccessDenied, psutil.Error, OSError):
        return out
    return out
