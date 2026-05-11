"""infra-003: 运行时健康指标采集 + SLO 告警。

设计
====

一层独立于 logging 的"指标层"：

- ``Metric(name, value, ts, tags)`` — 一条指标采样
- ``MetricSource = Callable[[], Iterable[Metric]]`` — 注册源，每次 tick 调用一次
- ``MetricsCollector`` — 后台 daemon 线程，按 ``interval_s`` tick；把所有 source
  返回的 Metric 写到 jsonl 文件（默认 ``~/.cache/coco/metrics.jsonl``）
- ``SLORule(metric, op, threshold, window_n, severity)`` — 滑动窗口防抖：连续
  ``window_n`` 次采样违例才 emit ``metrics.slo_breach`` 事件（通过
  ``coco.logging_setup.emit``）
- 内置 sources：``cpu_percent`` / ``mem_rss_mb`` / ``power_state`` /
  ``dialog_turns_total`` / ``proactive_topics_total`` / ``face_tracks_active``
  ——都是"软依赖"：拿不到对应对象就 skip 该 source，不抛异常
- 默认 OFF：业务路径需显式 ``COCO_METRICS=1`` 才构造，零开销

jsonl 行格式
============

::

    {"ts": 1715000000.123, "metric": "cpu_percent", "value": 12.3, "tags": {"unit": "percent"}}

单行 ≤ ``MAX_LINE_BYTES`` (4000) — 与 logging 一致；超长会丢 tags 后再截。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from coco.logging_setup import emit, RotatingJsonlWriter

log = logging.getLogger(__name__)

MAX_LINE_BYTES = 4000
DEFAULT_INTERVAL_S = 5.0
INTERVAL_LO = 1.0
INTERVAL_HI = 300.0


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class Metric:
    """一条指标采样。"""

    name: str
    value: Any  # int / float / str (state) — 必须 json.dumps 安全
    ts: float = field(default_factory=time.time)
    tags: Dict[str, Any] = field(default_factory=dict)


MetricSource = Callable[[], Iterable[Metric]]


@dataclass
class SLORule:
    """SLO 阈值规则，连续 window_n 次违例 emit metrics.slo_breach。

    op: ">" / ">=" / "<" / "<=" / "==" / "!="
    cooldown_s: 同一规则两次 emit 之间最小间隔（秒），默认 60s
    """

    metric: str
    op: str
    threshold: float
    window_n: int = 3
    severity: str = "warn"
    cooldown_s: float = 60.0


def _cmp(op: str, value: float, threshold: float) -> bool:
    if op == ">":
        return value > threshold
    if op == ">=":
        return value >= threshold
    if op == "<":
        return value < threshold
    if op == "<=":
        return value <= threshold
    if op == "==":
        return value == threshold
    if op == "!=":
        return value != threshold
    raise ValueError(f"未知 SLO op={op!r}")


# ---------------------------------------------------------------------------
# Built-in sources
# ---------------------------------------------------------------------------


def _try_import_psutil():
    """psutil 是软依赖：拿不到就返回 None；调用方 skip 该 source。"""
    try:
        import psutil  # type: ignore
        return psutil
    except Exception:  # noqa: BLE001
        return None


def system_source_factory() -> Optional[MetricSource]:
    """返回 cpu_percent / mem_rss_mb 的 source；psutil 不可用时返回 None。"""
    psutil = _try_import_psutil()
    if psutil is None:
        log.warning("[metrics] psutil 不可用；system_source 跳过")
        return None
    proc = psutil.Process()

    def _src() -> List[Metric]:
        out: List[Metric] = []
        try:
            cpu = float(proc.cpu_percent(interval=None))
            out.append(Metric("cpu_percent", cpu, tags={"unit": "percent"}))
        except Exception as e:  # noqa: BLE001
            log.debug("[metrics] cpu_percent failed: %r", e)
        try:
            rss = float(proc.memory_info().rss) / (1024.0 * 1024.0)
            out.append(Metric("mem_rss_mb", round(rss, 2), tags={"unit": "MB"}))
        except Exception as e:  # noqa: BLE001
            log.debug("[metrics] mem_rss_mb failed: %r", e)
        return out

    return _src


def power_source_factory(power_state) -> Optional[MetricSource]:
    if power_state is None:
        return None

    def _src() -> List[Metric]:
        try:
            st = power_state.current_state()
            name = getattr(st, "value", None) or str(st)
        except Exception as e:  # noqa: BLE001
            log.debug("[metrics] power_state read failed: %r", e)
            return []
        # value 用数值化：active=2, drowsy=1, sleep=0 — 便于 SLO；string 进 tags
        mapping = {"active": 2, "drowsy": 1, "sleep": 0}
        v = mapping.get(name, -1)
        return [Metric("power_state", v, tags={"state": name})]

    return _src


def dialog_source_factory(dialog_memory) -> Optional[MetricSource]:
    """累计 dialog turns；无 memory 实例就 skip。"""
    if dialog_memory is None:
        return None

    def _src() -> List[Metric]:
        try:
            turns = dialog_memory.recent_turns()
            n = len(turns) if turns is not None else 0
        except Exception as e:  # noqa: BLE001
            log.debug("[metrics] dialog read failed: %r", e)
            return []
        return [Metric("dialog_turns_total", int(n), tags={})]

    return _src


def proactive_source_factory(proactive) -> Optional[MetricSource]:
    if proactive is None:
        return None

    def _src() -> List[Metric]:
        try:
            stats = getattr(proactive, "stats", None)
            triggered = int(getattr(stats, "triggered", 0)) if stats else 0
        except Exception as e:  # noqa: BLE001
            log.debug("[metrics] proactive read failed: %r", e)
            return []
        return [Metric("proactive_topics_total", triggered, tags={})]

    return _src


def face_tracks_source_factory(face_tracker) -> Optional[MetricSource]:
    if face_tracker is None:
        return None

    def _src() -> List[Metric]:
        try:
            snap = face_tracker.latest()
            tracks = getattr(snap, "tracks", ()) or ()
            n = len(tracks)
        except Exception as e:  # noqa: BLE001
            log.debug("[metrics] face_tracker read failed: %r", e)
            return []
        return [Metric("face_tracks_active", int(n), tags={})]

    return _src


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


def default_metrics_path() -> Path:
    # L2: 用 Path.home() 风格统一（与项目其他 default path 风格一致）
    return Path.home() / ".cache" / "coco" / "metrics.jsonl"


def _serialize_metric(m: Metric) -> str:
    """单行 JSON；若超 MAX_LINE_BYTES 则丢 tags 重序列化。

    ts 精度：``round(ts, 3)`` 与 ``coco.logging_setup`` 中 ``record.created`` 同样
    截到毫秒（见 logging_setup.py 第 110 行），保持两层时间戳跨日志可对齐。
    """
    line: Dict[str, Any] = {
        "ts": round(float(m.ts), 3),
        "metric": str(m.name),
        "value": m.value,
    }
    tags = dict(m.tags) if m.tags else {}
    if tags:
        line["tags"] = tags
    s = json.dumps(line, ensure_ascii=False)
    if len(s.encode("utf-8")) > MAX_LINE_BYTES:
        # L2: 不仅 str value 截 200，dict / list / 其他 repr 长的也截 200
        v = line["value"]
        if isinstance(v, str):
            v_short: Any = v[:200]
        elif isinstance(v, (int, float, bool)) or v is None:
            v_short = v
        else:
            v_short = repr(v)[:200]
        short = {
            "ts": line["ts"],
            "metric": line["metric"],
            "value": v_short,
            "_truncated": True,
        }
        s = json.dumps(short, ensure_ascii=False, default=str)
        # 如果 value 本身就超长（极端），强行用 placeholder
        if len(s.encode("utf-8")) > MAX_LINE_BYTES:
            short["value"] = "<truncated>"
            s = json.dumps(short, ensure_ascii=False)
    return s


class MetricsCollector:
    """注册 sources + SLO rules，启动后台线程按 interval_s tick。

    ``start()`` 是幂等的；``stop()`` 让线程在下一个 interval 退出并 flush 文件。
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        interval_s: float = DEFAULT_INTERVAL_S,
        slo_rules: Optional[Sequence[SLORule]] = None,
        max_bytes: Optional[int] = None,
        backup_count: int = 3,
    ):
        self.path = Path(path) if path else default_metrics_path()
        self.interval_s = float(interval_s)
        # infra-004: rotate by size。max_bytes=None 时读 COCO_METRICS_MAX_MB
        # env（默认 50MB）；显式 0 表示不 rotate（向后兼容）
        if max_bytes is None:
            try:
                mb = float(os.environ.get("COCO_METRICS_MAX_MB", "50"))
            except ValueError:
                mb = 50.0
            max_bytes = int(mb * 1024 * 1024)
        self.max_bytes = int(max_bytes)
        self.backup_count = max(1, int(backup_count))
        self.sources: List[MetricSource] = []
        self.slo_rules: List[SLORule] = list(slo_rules or [])
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._writer: Optional[RotatingJsonlWriter] = None
        # 每条 SLO 的连续违例计数
        self._slo_violations: Dict[int, int] = {}
        # L1-1: latched 状态——一旦 emit 一次就锁定，直到出现 healthy 才解锁
        self._slo_latched: Dict[int, bool] = {}
        # L1-1: 上次 emit 时间戳（cooldown 用）
        self._slo_last_emit: Dict[int, float] = {}
        self._lock = threading.Lock()
        # tick 计数（test 用）
        self.ticks = 0

    def add_source(self, src: MetricSource) -> None:
        if src is None:
            return
        self.sources.append(src)

    def add_slo(self, rule: SLORule) -> None:
        self.slo_rules.append(rule)

    def _ensure_dir(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            log.warning("[metrics] mkdir %s failed: %r", self.path.parent, e)

    def _open(self) -> None:
        if self._writer is not None:
            return
        self._ensure_dir()
        try:
            self._writer = RotatingJsonlWriter(
                self.path,
                max_bytes=self.max_bytes,
                backup_count=self.backup_count,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[metrics] open %s failed: %r", self.path, e)
            self._writer = None

    def _close(self) -> None:
        with self._lock:
            if self._writer is not None:
                try:
                    self._writer.close()
                except Exception:  # noqa: BLE001
                    pass
                self._writer = None

    def _write_metric(self, m: Metric) -> None:
        try:
            line = _serialize_metric(m)
            with self._lock:
                if self._writer is None:
                    return
                self._writer.write_line(line)
        except Exception as e:  # noqa: BLE001
            log.debug("[metrics] write failed: %r", e)

    def _check_slo(self, metric: Metric) -> None:
        for idx, rule in enumerate(self.slo_rules):
            if rule.metric != metric.name:
                continue
            try:
                v = float(metric.value)
            except (TypeError, ValueError):
                continue
            try:
                breached = _cmp(rule.op, v, float(rule.threshold))
            except Exception as e:  # noqa: BLE001
                log.debug("[metrics] slo cmp failed: %r", e)
                continue
            if breached:
                self._slo_violations[idx] = self._slo_violations.get(idx, 0) + 1
                if self._slo_violations[idx] >= int(rule.window_n):
                    # L1-1: latched 模式——已 latched 时不再 emit；
                    # 同时遵守 cooldown_s 最小间隔保险
                    now = time.time()
                    last = self._slo_last_emit.get(idx, 0.0)
                    if self._slo_latched.get(idx, False):
                        # 已 latched，跳过 emit；保持计数不清零等 healthy 解锁
                        continue
                    if (now - last) < float(rule.cooldown_s):
                        # cooldown 期内，跳过 emit
                        continue
                    try:
                        emit(
                            "metrics.slo_breach",
                            metric=rule.metric,
                            op=rule.op,
                            threshold=rule.threshold,
                            value=v,
                            window_n=rule.window_n,
                            severity=rule.severity,
                        )
                    except Exception as e:  # noqa: BLE001
                        log.debug("[metrics] emit slo_breach failed: %r", e)
                    self._slo_latched[idx] = True
                    self._slo_last_emit[idx] = now
                    # 不 reset 计数；保持 latched，等 healthy 采样才 unlatch
            else:
                # L1-1: healthy 采样 → unlatch + 计数清零
                self._slo_violations[idx] = 0
                self._slo_latched[idx] = False

    def tick_once(self) -> List[Metric]:
        """同步跑一轮采集 + 写 + SLO 检查。test 路径用。"""
        self._open()
        out: List[Metric] = []
        for src in list(self.sources):
            try:
                metrics = src() or []
            except Exception as e:  # noqa: BLE001
                log.debug("[metrics] source raised: %r", e)
                continue
            for m in metrics:
                if not isinstance(m, Metric):
                    continue
                out.append(m)
                self._write_metric(m)
                self._check_slo(m)
        # flush so jsonl 立即可读（test 路径关键）
        if self._writer is not None:
            try:
                with self._lock:
                    self._writer.flush()
            except Exception:  # noqa: BLE001
                pass
        self.ticks += 1
        return out

    def _run(self) -> None:
        self._open()
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception as e:  # noqa: BLE001
                log.warning("[metrics] tick failed: %r", e)
            # wait 而不是 sleep — stop_event 早唤醒
            if self._stop.wait(timeout=self.interval_s):
                break
        self._close()

    def start(self, stop_event: Optional[threading.Event] = None) -> None:
        """起后台线程；幂等。stop_event 可选注入（与主 app 共享）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        if stop_event is not None:
            # L1-2: 桥接用 0.5s 轮询 + 双唤醒——外部 stop_event 或内部 _stop
            # 任一 set 都让 bridge 退出，避免 stop() 后 bridge 永远死等外部 event
            self._external_stop = stop_event

            def _bridge(_e=stop_event, _s=self._stop) -> None:
                while not _s.is_set():
                    if _e.wait(timeout=0.5):
                        _s.set()
                        return

            threading.Thread(target=_bridge, name="coco-metrics-stop-bridge", daemon=True).start()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="coco-metrics", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._close()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# ---------------------------------------------------------------------------
# env
# ---------------------------------------------------------------------------


def metrics_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    e = env if env is not None else os.environ
    return (e.get("COCO_METRICS") or "0").strip().lower() in {"1", "true", "yes", "on"}


def interval_from_env(env: Optional[Mapping[str, str]] = None) -> float:
    e = env if env is not None else os.environ
    raw = (e.get("COCO_METRICS_INTERVAL") or "").strip()
    if not raw:
        return DEFAULT_INTERVAL_S
    try:
        v = float(raw)
    except ValueError:
        log.warning("[metrics] COCO_METRICS_INTERVAL=%r 非数字，回退 %.1f", raw, DEFAULT_INTERVAL_S)
        return DEFAULT_INTERVAL_S
    if v < INTERVAL_LO:
        log.warning("[metrics] interval=%.2f <%.1f，clamp", v, INTERVAL_LO)
        v = INTERVAL_LO
    if v > INTERVAL_HI:
        log.warning("[metrics] interval=%.2f >%.1f，clamp", v, INTERVAL_HI)
        v = INTERVAL_HI
    return v


def path_from_env(env: Optional[Mapping[str, str]] = None) -> Path:
    e = env if env is not None else os.environ
    raw = (e.get("COCO_METRICS_PATH") or "").strip()
    if raw:
        return Path(os.path.expanduser(raw))
    return default_metrics_path()


def default_slo_rules() -> List[SLORule]:
    """内置默认 SLO：CPU 持续 >80% / 内存 >1500MB 时 warn。"""
    return [
        SLORule(metric="cpu_percent", op=">", threshold=80.0, window_n=3, severity="warn"),
        SLORule(metric="mem_rss_mb", op=">", threshold=1500.0, window_n=3, severity="warn"),
    ]


def build_default_collector(
    *,
    power_state=None,
    dialog_memory=None,
    proactive=None,
    face_tracker=None,
    path: Optional[Path] = None,
    interval_s: Optional[float] = None,
    slo_rules: Optional[Sequence[SLORule]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> MetricsCollector:
    """组装一个带内置 source 的 collector（不会自动 start）。"""
    p = path if path is not None else path_from_env(env)
    i = interval_s if interval_s is not None else interval_from_env(env)
    rules = list(slo_rules) if slo_rules is not None else default_slo_rules()
    c = MetricsCollector(path=p, interval_s=i, slo_rules=rules)

    sys_src = system_source_factory()
    if sys_src is not None:
        c.add_source(sys_src)
    for src in (
        power_source_factory(power_state),
        dialog_source_factory(dialog_memory),
        proactive_source_factory(proactive),
        face_tracks_source_factory(face_tracker),
    ):
        if src is not None:
            c.add_source(src)
    return c


__all__ = [
    "Metric",
    "MetricSource",
    "MetricsCollector",
    "SLORule",
    "MAX_LINE_BYTES",
    "DEFAULT_INTERVAL_S",
    "metrics_enabled_from_env",
    "interval_from_env",
    "path_from_env",
    "default_metrics_path",
    "default_slo_rules",
    "build_default_collector",
    "system_source_factory",
    "power_source_factory",
    "dialog_source_factory",
    "proactive_source_factory",
    "face_tracks_source_factory",
]
