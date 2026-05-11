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
import sys
import time
from typing import Any, Dict

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


def setup_logging(jsonl: bool = False, level: str = "INFO") -> None:
    """配置 root logger。jsonl=False 时与现有 basicConfig 行为兼容。

    幂等：重复调用清旧 handler。
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


__all__ = ["setup_logging", "emit", "JsonlFormatter", "MAX_LINE_BYTES", "AUTHORITATIVE_COMPONENTS"]
