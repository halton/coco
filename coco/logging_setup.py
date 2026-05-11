"""infra-002: 结构化 jsonl 日志 + setup_logging。

用法
====

::

    from coco.logging_setup import setup_logging, emit
    from coco.config import load_config

    cfg = load_config()
    setup_logging(jsonl=cfg.log.jsonl, level=cfg.log.level)

    emit("asr.transcribe", text="你好", cer=0.0, latency_ms=120)
    emit("llm.reply", backend="fallback", latency_ms=8, chars=12)
    emit("vad.utterance", duration_s=1.4, peak_db=-22.3)
    emit("wake.hit", word="可可", score=0.83)
    emit("power.transition", from_state="active", to_state="drowsy", source="tick@idle=120s")

设计
====

- ``setup_logging(jsonl=False)``：等价于现有 ``logging.basicConfig(level=...,
  stream=sys.stderr)`` —— **不改任何 phase-3 行为**。
- ``setup_logging(jsonl=True)``：root logger 挂 ``JsonlFormatter``；每行一个
  JSON {ts, level, component, event, message, **payload}。
- ``emit(component_event, **payload)``：把 'asr.transcribe' 这种 'comp.event'
  形式拆开并发到 logger.info；非 jsonl 模式渲染成人类可读 'comp/event k=v ...'。
- ``MAX_LINE_BYTES=4000``：单行 jsonl 超长 truncate（防泄漏 / 防写满磁盘）。
- ``setup_logging`` 幂等：重复调用先清旧 handler 再装新 handler。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional, Union

MAX_LINE_BYTES = 4000

_INSTALLED = False


# infra-002 closeout L1-2：authoritative component 短名集合。
# emit() 入参 component 短名不在此集合时仅 warn 不阻断，避免出现
# 同子系统 jsonl 行 component 字段两套（如 'vad' vs 'coco.vad_trigger'）。
AUTHORITATIVE_COMPONENTS = frozenset({
    "asr",
    "llm",
    "vad",
    "wake",
    "power",
    "dialog",
    "face",
    "idle",
    "interact",
    "metrics",
    "vision",
    "companion",
    "robot",
    "startup",
})

_UNKNOWN_COMPONENTS_WARNED: set = set()


class JsonlFormatter(logging.Formatter):
    """每条 record 序列化成单行 JSON。

    - 必填 ``ts`` (epoch 秒) / ``level`` / ``component`` / ``event`` / ``message``
    - extra payload：从 record.__dict__ 中除内置字段外的全部条目
    - 单行字节超 ``MAX_LINE_BYTES`` 时 truncate + 加 ``"_truncated": true`` 字段
    """

    _RESERVED = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload: Dict[str, Any] = {}
        for k, v in record.__dict__.items():
            if k in self._RESERVED or k.startswith("_"):
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        component = payload.pop("component", record.name)
        event = payload.pop("event", "log")
        line = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "component": component,
            "event": event,
            "message": record.getMessage(),
        }
        line.update(payload)
        # infra-002 closeout L1-1：保留 traceback。logger.exception() 出来的
        # record 携带 exc_info，phase-1 实现把它列入 _RESERVED 直接丢掉，导致
        # jsonl 行只有 message 没有 stack trace；现在用 formatException 补回。
        if record.exc_info:
            try:
                line["exc"] = self.formatException(record.exc_info)
            except Exception:  # noqa: BLE001
                line["exc"] = repr(record.exc_info)
        elif record.exc_text:
            line["exc"] = record.exc_text
        s = json.dumps(line, ensure_ascii=False)
        if len(s.encode("utf-8")) > MAX_LINE_BYTES:
            # 暴力 truncate：保留 ts/level/component/event/message + truncated 标志
            short = {
                "ts": line["ts"],
                "level": line["level"],
                "component": line["component"],
                "event": line["event"],
                "message": line["message"][:200],
                "_truncated": True,
            }
            # 即便 truncate 也带 exc 摘要（最多 1KB），避免崩栈被吞
            if "exc" in line:
                short["exc"] = line["exc"][:1000]
            s = json.dumps(short, ensure_ascii=False)
        return s


class RotatingJsonlHandler(RotatingFileHandler):
    """size-based rotating handler，写 jsonl 行（接 logging.LogRecord 接口）。

    基于 stdlib ``RotatingFileHandler``：达到 ``max_bytes`` 触发 rollover，
    保留 .1 .2 ... .N（``backup_count``）。每行 jsonl 在 emit 时已经是单行
    ``JsonlFormatter`` 输出，rollover 在行边界发生，不会切坏 JSON。

    并发：stdlib 已用 ``self.lock`` 保护 emit；多线程写不丢日志。
    """

    def __init__(self, filename: Union[str, Path], max_bytes: int = 10 * 1024 * 1024,
                 backup_count: int = 3, encoding: str = "utf-8") -> None:
        p = Path(filename)
        p.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(
            filename=str(p),
            maxBytes=int(max_bytes),
            backupCount=int(backup_count),
            encoding=encoding,
        )


class RotatingJsonlWriter:
    """独立于 logging 的 jsonl 写入器，支持 size-based rotate。

    用于 ``coco.metrics``：直接写 ``metric`` 行而不经 root logger。和
    ``RotatingFileHandler`` 同样语义（rotate 行边界 + 保留 N 份），但是
    暴露 ``write_line(s: str)`` 而非 logging.LogRecord 接口。

    线程安全：内部 ``threading.Lock`` 保护 write + rotate。
    """

    def __init__(self, path: Union[str, Path], max_bytes: int = 50 * 1024 * 1024,
                 backup_count: int = 3) -> None:
        self.path = Path(path)
        self.max_bytes = int(max_bytes)
        self.backup_count = max(1, int(backup_count))
        self._lock = threading.Lock()
        self._fh = None
        self._bytes_written = 0  # 当前 fh 已写字节（包含未 flush）
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._open()

    def _open(self) -> None:
        if self._fh is not None:
            return
        try:
            # 续写模式；初始 bytes_written = 现有文件大小
            self._fh = open(self.path, "a", encoding="utf-8")
            try:
                self._bytes_written = self.path.stat().st_size if self.path.exists() else 0
            except OSError:
                self._bytes_written = 0
        except Exception as e:  # noqa: BLE001
            logging.getLogger("coco.logging_setup").warning(
                "[rotating_jsonl] open %s failed: %r", self.path, e)
            self._fh = None
            self._bytes_written = 0

    def _should_rotate(self, line_bytes: int) -> bool:
        if self.max_bytes <= 0:
            return False
        return (self._bytes_written + line_bytes) > self.max_bytes

    def _do_rotate(self) -> None:
        """关闭当前文件、shift .N → .N+1，重新打开。"""
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:  # noqa: BLE001
                pass
            self._fh = None
        suf = self.path.suffix  # 通常 ".jsonl"
        base = self.path
        # 删最老
        oldest = base.with_suffix(suf + f".{self.backup_count}")
        try:
            if oldest.exists():
                oldest.unlink()
        except OSError:
            pass
        # shift .{N-1} -> .{N}
        for i in range(self.backup_count - 1, 0, -1):
            src = base.with_suffix(suf + f".{i}")
            dst = base.with_suffix(suf + f".{i+1}")
            try:
                if src.exists():
                    src.rename(dst)
            except OSError:
                pass
        # main -> .1
        try:
            if base.exists():
                base.rename(base.with_suffix(suf + ".1"))
        except OSError:
            pass
        self._open()

    def write_line(self, s: str) -> None:
        """单行（不带 \\n 也行）写入，自动追加换行。rotate 在行边界发生。"""
        if not s.endswith("\n"):
            s = s + "\n"
        b = s.encode("utf-8")
        with self._lock:
            if self._should_rotate(len(b)):
                self._do_rotate()
            if self._fh is None:
                self._open()
            if self._fh is None:
                return
            try:
                self._fh.write(s)
                self._bytes_written += len(b)
            except Exception as e:  # noqa: BLE001
                logging.getLogger("coco.logging_setup").debug(
                    "[rotating_jsonl] write failed: %r", e)

    def flush(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.flush()
                except Exception:  # noqa: BLE001
                    pass

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.flush()
                    self._fh.close()
                except Exception:  # noqa: BLE001
                    pass
                self._fh = None


def setup_logging(jsonl: bool = False, level: str = "INFO") -> None:
    """配置 root logger。jsonl=False 时与现有 basicConfig 行为兼容。

    幂等：重复调用清旧 handler。

    infra-004: 当 jsonl=True 且 ``COCO_LOG_FILE`` 设置时，附加 ``RotatingJsonlHandler``
    写入文件（rotate by size，env ``COCO_LOG_MAX_MB`` 默认 10，retention=3）。
    stderr handler 保留，不影响现有 verify。
    """
    global _INSTALLED
    root = logging.getLogger()
    # 清掉旧 handler（包含 basicConfig 默认装的）以保证幂等
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(stream=sys.stderr)
    if jsonl:
        handler.setFormatter(JsonlFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
    root.addHandler(handler)
    # infra-004: 可选 file rotate handler（jsonl 模式下生效；非 jsonl 行为不变）
    if jsonl:
        log_file = os.environ.get("COCO_LOG_FILE", "").strip()
        if log_file:
            try:
                max_mb = float(os.environ.get("COCO_LOG_MAX_MB", "10"))
                backup_n = int(os.environ.get("COCO_LOG_BACKUP_N", "3"))
                fh = RotatingJsonlHandler(
                    Path(os.path.expanduser(log_file)),
                    max_bytes=int(max_mb * 1024 * 1024),
                    backup_count=max(1, backup_n),
                )
                fh.setFormatter(JsonlFormatter())
                root.addHandler(fh)
            except Exception as e:  # noqa: BLE001
                root.warning("[logging_setup] file handler init failed: %r", e)
    try:
        root.setLevel(getattr(logging, level.upper()))
    except AttributeError:
        root.setLevel(logging.INFO)
    _INSTALLED = True


def emit(component_event: str, message: str = "", **payload: Any) -> None:
    """快捷打 structured event。

    component_event: 形如 'asr.transcribe' / 'power.transition'。第一个 '.'
    左侧为 component，右侧为 event。

    L1-2：component 短名不在 ``AUTHORITATIVE_COMPONENTS`` 时 warn 一次（每个未知
    component 仅 warn 一次），但不阻断 emit。
    """
    if "." in component_event:
        component, event = component_event.split(".", 1)
    else:
        component = component_event
        event = "event"
    if component not in AUTHORITATIVE_COMPONENTS and component not in _UNKNOWN_COMPONENTS_WARNED:
        _UNKNOWN_COMPONENTS_WARNED.add(component)
        logging.getLogger("coco.logging_setup").warning(
            "[logging_setup] emit() component=%r 不在 AUTHORITATIVE_COMPONENTS=%s；"
            "请改用短名以保持 jsonl 行 component 字段一致",
            component,
            sorted(AUTHORITATIVE_COMPONENTS),
        )
    logger = logging.getLogger(component)
    extra = {"component": component, "event": event, **payload}
    logger.info(message or f"{component}.{event}", extra=extra)


__all__ = ["setup_logging", "emit", "JsonlFormatter", "MAX_LINE_BYTES",
           "AUTHORITATIVE_COMPONENTS", "RotatingJsonlHandler", "RotatingJsonlWriter"]
