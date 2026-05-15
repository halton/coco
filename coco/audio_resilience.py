"""coco.audio_resilience — audio-009 (sounddevice 恢复 + USB hot-plug 检测).

phase-13 / audio-009 引入两块 sim-可证的稳定性增强，**默认 OFF，env 显式开启**。
任何路径在 env OFF 时 short-circuit，对主链路 0 副作用、bytewise 等价。

模块一：``open_stream_with_recovery``
    封装 ``sd.OutputStream`` / ``sd.InputStream`` 构造，遇 ``PortAudioError``
    按指数退避（base=0.5s, max=8s, max_attempts=5）重试；超过尝试次数 emit
    ``audio.recovery_failed`` 并返回 None（调用方自行降级到 fake/no-op）。
    每次重试 emit ``audio.recovery_attempt``，成功 emit ``audio.recovery_succeeded``。

    env: ``COCO_AUDIO_RECOVERY=1``  开启。OFF 时直接 raise（透传原异常）。

模块二：``HotplugWatcher``
    后台线程每 ``poll_interval``（默认 5s）调 ``sd.query_devices()``，与上次缓存
    diff，新增/移除设备 emit ``audio.device_change`` payload
    ``{event: "added"|"removed", device, ts}``。

    env: ``COCO_AUDIO_HOTPLUG=1``  开启。OFF 时不起线程，不轮询。

设计原则：
- 测试可注入 fake ``query_devices_fn`` / ``open_stream_fn`` / ``sleep_fn`` /
  ``emit_fn``，避免真起 sounddevice 与真 sleep。
- 失败优雅退化：任何 fallback 路径都不抛到调用方主线程。
- 子模块化：**只暴露 helper 函数 / class**，wire 由 main.py 在启动期一次性挂入；
  audio-008/probe 与本 hot-plug watcher 互不依赖，可独立 OFF。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Iterable, List, Optional

log = logging.getLogger("audio_resilience")

# ---- env gates ------------------------------------------------------------
ENV_RECOVERY = "COCO_AUDIO_RECOVERY"
ENV_HOTPLUG = "COCO_AUDIO_HOTPLUG"

# ---- recovery 参数 (硬编码；改动需新立 feature) -------------------------
RECOVERY_BASE_DELAY = 0.5
RECOVERY_MAX_DELAY = 8.0
RECOVERY_MAX_ATTEMPTS = 5


def _is_recovery_on() -> bool:
    return os.environ.get(ENV_RECOVERY, "0") == "1"


def _is_hotplug_on() -> bool:
    return os.environ.get(ENV_HOTPLUG, "0") == "1"


def _safe_emit(emit_fn: Optional[Callable[..., None]], event_name: str, **payload: Any) -> None:
    """emit 包一层 try，避免 emit 自身异常打断 audio 主路径。

    第二个参数命名为 ``event_name`` 而非 ``event``，避免和 hotplug payload 里的
    ``event="added"|"removed"`` kwarg 撞名（Python 会报 multiple values for 'event'）。
    """
    if emit_fn is None:
        return
    try:
        emit_fn(event_name, **payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("[audio_resilience] emit %s failed: %s: %s", event_name, type(exc).__name__, exc)


# =====================================================================
# 模块一：退避恢复
# =====================================================================
def open_stream_with_recovery(
    open_stream_fn: Callable[[], Any],
    *,
    stream_kind: str = "output",
    emit_fn: Optional[Callable[..., None]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    base_delay: float = RECOVERY_BASE_DELAY,
    max_delay: float = RECOVERY_MAX_DELAY,
    max_attempts: int = RECOVERY_MAX_ATTEMPTS,
    error_types: Optional[Iterable[type]] = None,
) -> Any:
    """尝试构造 stream；遇 PortAudioError 类异常退避重试。

    返回成功构造的 stream 对象；若超过 ``max_attempts`` 仍失败，emit
    ``audio.recovery_failed`` 并返回 ``None`` 让调用方决定 fake/no-op。

    env OFF (默认) 时直接调用 ``open_stream_fn()``，异常透传，不重试。

    参数:
        open_stream_fn: 0 参 callable，每次调用产出一个新 stream（或抛异常）。
        stream_kind: ``"output"`` / ``"input"`` 标签，仅打 emit 用。
        emit_fn: ``coco.logging_setup.emit`` 风格的 callable；None 则不 emit。
        sleep_fn: 注入点，测试用 ``MagicMock`` 替换。
        error_types: 视作可重试的异常类元组；默认捕 ``sd.PortAudioError``
            （sounddevice 未装时退化为 ``OSError``）。
    """
    if not _is_recovery_on():
        return open_stream_fn()

    # audio-010 收紧：默认只捕 sd.PortAudioError，让 OSError / RuntimeError 透传。
    # 调用方需要兜更广的异常时显式传 error_types=(...) 覆盖。
    # sounddevice 不可用时退化为空元组（即默认行为=透传，等价 recovery OFF；不再误吞 OSError）。
    if error_types is None:
        try:
            import sounddevice as _sd  # type: ignore
            error_types = (_sd.PortAudioError,)
        except Exception:
            error_types = ()
    error_types = tuple(error_types)
    if not error_types:
        # 没有可重试的异常类型 → 直接 passthrough（与 recovery OFF 等价）
        return open_stream_fn()

    delay = float(base_delay)
    last_exc: Optional[BaseException] = None
    for attempt in range(1, int(max_attempts) + 1):
        try:
            stream = open_stream_fn()
        except error_types as exc:
            last_exc = exc
            _safe_emit(
                emit_fn,
                "audio.recovery_attempt",
                stream_kind=stream_kind,
                attempt=attempt,
                max_attempts=int(max_attempts),
                delay_s=float(delay),
                error_type=type(exc).__name__,
                error_msg=str(exc),
            )
            if attempt >= int(max_attempts):
                break
            try:
                sleep_fn(min(float(delay), float(max_delay)))
            except Exception as se:  # noqa: BLE001
                log.warning("[audio_resilience] sleep_fn raised: %s", se)
            delay = min(delay * 2.0, float(max_delay))
            continue
        # success
        _safe_emit(
            emit_fn,
            "audio.recovery_succeeded",
            stream_kind=stream_kind,
            attempt=attempt,
        )
        return stream

    # 所有 attempts 用尽
    _safe_emit(
        emit_fn,
        "audio.recovery_failed",
        stream_kind=stream_kind,
        attempts=int(max_attempts),
        last_error_type=type(last_exc).__name__ if last_exc else "Unknown",
        last_error_msg=str(last_exc) if last_exc else "",
    )
    return None


# =====================================================================
# 模块二：USB hot-plug 检测
# =====================================================================
def _device_key(d: dict) -> tuple:
    """生成设备唯一 key —— 用 (index, name, max_in, max_out) 元组。"""
    return (
        int(d.get("index", -1)),
        str(d.get("name", "")),
        int(d.get("max_input_channels", 0) or 0),
        int(d.get("max_output_channels", 0) or 0),
    )


def diff_devices(
    prev: List[dict],
    curr: List[dict],
) -> tuple[List[dict], List[dict]]:
    """返回 (added, removed) 两份 dict 列表（按 key 比对）。"""
    prev_map = {_device_key(d): d for d in prev}
    curr_map = {_device_key(d): d for d in curr}
    added = [d for k, d in curr_map.items() if k not in prev_map]
    removed = [d for k, d in prev_map.items() if k not in curr_map]
    return added, removed


class HotplugWatcher:
    """后台 device hot-plug 轮询。env OFF 时 ``start()`` no-op。

    使用方式::

        w = HotplugWatcher(stop_event, emit_fn=emit)
        w.start()
        # ... 主循环 ...
        w.stop()  # 与 stop_event.set() 等价

    测试可注入 ``query_devices_fn`` / ``sleep_fn`` 以避开真 sounddevice。
    """

    def __init__(
        self,
        stop_event: Optional[threading.Event] = None,
        *,
        poll_interval: float = 5.0,
        emit_fn: Optional[Callable[..., None]] = None,
        query_devices_fn: Optional[Callable[[], Any]] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock_fn: Callable[[], float] = time.time,
        reopen_callback: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self._stop_event = stop_event or threading.Event()
        self._poll_interval = float(poll_interval)
        self._emit_fn = emit_fn
        self._query_fn = query_devices_fn
        self._sleep_fn = sleep_fn
        self._clock_fn = clock_fn
        # audio-010: device_change → 业务订阅 reopen 钩子。
        # 触发条件：每个 added/removed device emit 完后调用一次 callback(event, device)。
        # 任何异常被吞掉只 log，不中断 poll 线程。
        self._reopen_callback = reopen_callback
        self._reopen_call_count = 0
        self._thread: Optional[threading.Thread] = None
        self._prev: List[dict] = []
        self._lock = threading.Lock()

    def _query(self) -> List[dict]:
        if self._query_fn is not None:
            raw = self._query_fn()
        else:
            try:
                import sounddevice as _sd  # type: ignore
            except Exception as exc:  # noqa: BLE001
                log.warning("[hotplug] sounddevice unavailable: %s", exc)
                return []
            try:
                raw = _sd.query_devices()
            except Exception as exc:  # noqa: BLE001
                log.warning("[hotplug] query_devices failed: %s", exc)
                return []
        out: List[dict] = []
        for i, d in enumerate(list(raw)):
            try:
                dd = dict(d)
                dd.setdefault("index", i)
                out.append(dd)
            except Exception:  # noqa: BLE001
                continue
        return out

    def poll_once(self) -> tuple[List[dict], List[dict]]:
        """单次轮询；返回 (added, removed)。供测试单步驱动。"""
        curr = self._query()
        with self._lock:
            added, removed = diff_devices(self._prev, curr)
            self._prev = curr
        ts = self._clock_fn()
        for d in added:
            _safe_emit(
                self._emit_fn,
                "audio.device_change",
                event="added",
                device=d,
                ts=ts,
            )
            self._fire_reopen("added", d)
        for d in removed:
            _safe_emit(
                self._emit_fn,
                "audio.device_change",
                event="removed",
                device=d,
                ts=ts,
            )
            self._fire_reopen("removed", d)
        return added, removed

    def _fire_reopen(self, event: str, device: dict) -> None:
        """触发 reopen callback；callback 抛错只 log，不影响 poll 线程。"""
        cb = self._reopen_callback
        if cb is None:
            return
        try:
            cb(event, device)
            self._reopen_call_count += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("[hotplug] reopen_callback raised: %s: %s", type(exc).__name__, exc)

    @property
    def reopen_call_count(self) -> int:
        return self._reopen_call_count

    def prime(self) -> None:
        """初始化基线（不 emit）。供 start() 与测试共用。"""
        with self._lock:
            self._prev = self._query()

    def start(self) -> bool:
        """env OFF 时返回 False（no-op）。否则起 daemon 线程。"""
        if not _is_hotplug_on():
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        # 启动期记录初始基线，避免 first-poll 把全部已有设备误报成 added
        self.prime()
        self._thread = threading.Thread(
            target=self._loop,
            name="coco-audio-hotplug",
            daemon=True,
        )
        self._thread.start()
        return True

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001
                log.warning("[hotplug] poll_once raised: %s: %s", type(exc).__name__, exc)
            # 用 wait 让 stop 立即生效
            if self._stop_event.wait(timeout=self._poll_interval):
                break

    def stop(self) -> None:
        self._stop_event.set()


__all__ = [
    "ENV_RECOVERY",
    "ENV_HOTPLUG",
    "RECOVERY_BASE_DELAY",
    "RECOVERY_MAX_DELAY",
    "RECOVERY_MAX_ATTEMPTS",
    "open_stream_with_recovery",
    "diff_devices",
    "HotplugWatcher",
]
