"""coco.wake_word — 中文唤醒词检测（interact-005）。

用 sherpa-onnx ``KeywordSpotter`` (zipformer-wenetspeech 3.3M, 拼音建模) 做轻量
KWS，前置在 VAD trigger 之前：未唤醒时 VAD 段会被 awake gate 丢弃，唤醒后
开 ``window_seconds`` 秒「awake 窗口」，窗口内 VAD 段照常进入 InteractSession，
窗口超时回到 sleeping。

设计取舍
--------
- 模型缓存默认 ``~/.cache/coco/kws/``（``COCO_KWS_CACHE`` 可覆盖），由
  ``scripts/fetch_kws_models.sh`` 提前下载，与 audio-002 的 ``~/.cache/coco/asr/``
  分目录，互不干扰。
- 关键词运行时写入临时文件（``coco-wake-keywords-*.txt``），格式与上游一致：
  ``<声母 韵母 ...> @<原文>``。默认仅 "可可"（``k ě k ě @可可``）。
- ``WakeWordDetector.feed(samples_f32, sr)`` 同步喂帧；命中时调 ``on_wake()``
  并 ``reset_stream`` 准备下一次检测。线程模型与 ``VADTrigger`` 一致：feed 在
  调用方线程同步执行，回调也在同一线程。
- ``WakeGate`` 是无 IO 的纯状态机：``trigger()`` 开窗口、``is_awake()`` 判
  当前是否在窗口内、``reset()`` 提前回 sleeping。窗口超时由 wall clock
  ``time.monotonic()`` 判，不起线程。
- 默认 keyword 阈值 ``0.25``（与上游模型默认一致）；过低易误唤醒、过高易漏。
  环境变量 ``COCO_WAKE_THRESHOLD`` 可调（clamp 到 [0.05, 0.95]）。
- TTS 期间 KWS 与 VAD 同步 mute（防自激）；wrap_tts 由 main.py 串接。
- ``COCO_WAKE_WORD=0``（默认）时调用方应跳过整个 wake gate；``=1`` 才启用。
  本模块不读取该 env，由调用方决定，边界清晰（与 ``vad_trigger.vad_disabled_from_env``
  风格对齐）。

模型路径
--------
``~/.cache/coco/kws/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01/``：
  - ``encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx``
  - ``decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx``
  - ``joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx``
  - ``tokens.txt``
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

log = logging.getLogger(__name__)


DEFAULT_CACHE = Path(
    os.environ.get("COCO_KWS_CACHE", str(Path.home() / ".cache" / "coco" / "kws"))
)
KWS_DIR = DEFAULT_CACHE / "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"

# 单一事实源：默认唤醒词 "可可"（拼音 ke3 ke3）
DEFAULT_KEYWORDS: List[str] = ["k ě k ě @可可"]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class WakeConfig:
    sample_rate: int = 16000
    threshold: float = 0.25                # KWS keywords_threshold；越大越严
    keywords_score: float = 1.0
    window_seconds: float = 6.0            # awake 窗口；超时回 sleeping
    keywords: List[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    num_threads: int = 1


@dataclass
class WakeStats:
    frames_fed: int = 0
    wakes_total: int = 0
    wakes_while_muted: int = 0
    wakes_while_awake: int = 0             # 已 awake 时再次命中（不重复触发 on_wake）
    callback_ok: int = 0
    callback_fail: int = 0
    last_wake_text: str = ""
    last_wake_monotonic: float = 0.0


# ---------------------------------------------------------------------------
# WakeGate — 无 IO 状态机：管理 awake / sleeping
# ---------------------------------------------------------------------------


class WakeGate:
    """6s 窗口状态机。``trigger()`` 开窗、``is_awake()`` 检查、``reset()`` 提前关。

    线程安全：内部用 ``threading.Lock`` 保护 ``_awake_until``；多线程读写 OK。
    """

    def __init__(self, window_seconds: float = 6.0) -> None:
        self.window_seconds = float(window_seconds)
        self._awake_until: float = 0.0
        self._lock = threading.Lock()
        # 统计：窗口超时回 sleeping 的次数（is_awake() 内自然触发）
        self.expired_count = 0
        self._was_awake = False

    def trigger(self) -> None:
        """开/续 awake 窗口（每次命中都重置 deadline，连续两次 wake 在窗口内 = reset timer）。"""
        with self._lock:
            self._awake_until = time.monotonic() + self.window_seconds
            self._was_awake = True

    def is_awake(self) -> bool:
        with self._lock:
            now = time.monotonic()
            awake = now < self._awake_until
            # 边沿检测：上一轮 awake、本轮已超时 → expired_count++
            if self._was_awake and not awake:
                self.expired_count += 1
                self._was_awake = False
                log.info("[wake] awake window expired, back to sleeping")
            return awake

    def remaining_seconds(self) -> float:
        with self._lock:
            return max(0.0, self._awake_until - time.monotonic())

    def reset(self) -> None:
        with self._lock:
            self._awake_until = 0.0
            self._was_awake = False


# ---------------------------------------------------------------------------
# WakeWordDetector
# ---------------------------------------------------------------------------


class WakeWordDetector:
    """sherpa-onnx KWS wrapper：feed 帧 → 命中关键词 → ``on_wake(text)``。

    与 ``VADTrigger`` 同形：``feed(samples_f32)`` 同步喂入；命中时调 callback；
    ``mute()/unmute()`` 在 TTS 期间使用避免自激；``start_microphone()`` / ``stop()``
    可独立起 daemon 线程，但实践中由 main.py 通过 ``WakeVADBridge``（见下文工厂）
    与 VADTrigger 共享同一路 sounddevice 流，避免双开抢设备。
    """

    def __init__(
        self,
        on_wake: Callable[[str], None],
        config: Optional[WakeConfig] = None,
        *,
        kws_dir: Optional[Path] = None,
    ) -> None:
        self.on_wake = on_wake
        self.config = config or WakeConfig()
        self.stats = WakeStats()
        self._muted = False
        self._stop_event = threading.Event()
        self._mic_thread: Optional[threading.Thread] = None
        self._mic_lock = threading.Lock()
        self._lock = threading.Lock()
        self._kws_dir = kws_dir or KWS_DIR
        self._spotter = self._build_spotter()
        self._stream = self._spotter.create_stream()
        # 临时关键词文件需要保留生命期到 spotter 释放（Path 对象只在 __init__ 创建）
        self._keywords_file: Optional[Path] = None  # set 在 _build_spotter 内
        # audio-011: hotplug reopen 机制（与 VADTrigger 同形态）
        self._reopen_event = threading.Event()
        self._reopen_meta: dict = {}
        self._current_mic_stream = None
        self._mic_stream_lock = threading.Lock()
        self._reopen_count = 0

    def request_reopen(
        self,
        event: str = "changed",
        device: Optional[dict] = None,
        error_type: str = "requested",
    ) -> None:
        """外部 hotplug cb 调用：请求 mic_loop stop+reopen 当前 InputStream。

        见 ``VADTrigger.request_reopen`` 注释。
        audio-012: 新增 ``error_type``（``"requested"`` / ``"portaudio_error"`` / ``"unknown"``）。
        """
        self._reopen_meta = {
            "event": str(event),
            "device": dict(device or {}),
            "error_type": str(error_type or "requested"),
        }
        self._reopen_event.set()
        with self._mic_stream_lock:
            s = self._current_mic_stream
        if s is not None:
            try:
                stop_fn = getattr(s, "stop", None)
                if callable(stop_fn):
                    stop_fn()
            except Exception:  # noqa: BLE001
                pass

    @property
    def reopen_count(self) -> int:
        return self._reopen_count

    # ------------------------------------------------------------------
    # 构造 KeywordSpotter
    # ------------------------------------------------------------------
    def _build_spotter(self):
        import sherpa_onnx

        d = self._kws_dir
        encoder = d / "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
        decoder = d / "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
        joiner = d / "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
        tokens = d / "tokens.txt"
        for p in (encoder, decoder, joiner, tokens):
            if not p.exists():
                raise FileNotFoundError(
                    f"KWS 资源未找到: {p}。先跑 `bash scripts/fetch_kws_models.sh`"
                )
        # 写关键词到临时文件
        kw_path = Path(tempfile.gettempdir()) / "coco-wake-keywords.txt"
        kw_path.write_text("\n".join(self.config.keywords) + "\n", encoding="utf-8")
        self._keywords_file = kw_path
        log.info(
            "[wake] KWS init: %d keyword(s) -> %s, threshold=%.2f window=%.1fs",
            len(self.config.keywords), kw_path, self.config.threshold,
            self.config.window_seconds,
        )
        return sherpa_onnx.KeywordSpotter(
            tokens=str(tokens),
            encoder=str(encoder),
            decoder=str(decoder),
            joiner=str(joiner),
            keywords_file=str(kw_path),
            num_threads=self.config.num_threads,
            sample_rate=int(self.config.sample_rate),
            keywords_score=self.config.keywords_score,
            keywords_threshold=self.config.threshold,
        )

    # ------------------------------------------------------------------
    # mute / unmute（TTS 期间用，与 VADTrigger 同步）
    # ------------------------------------------------------------------
    def mute(self) -> None:
        self._muted = True

    def unmute(self) -> None:
        self._muted = False

    def is_muted(self) -> bool:
        return self._muted

    def reset_buffer(self) -> None:
        """丢掉 KWS 内部已累积的 stream 状态（mute 结束 / 强制清缓存时用）。"""
        with self._lock:
            self._stream = self._spotter.create_stream()

    # ------------------------------------------------------------------
    # 核心：feed
    # ------------------------------------------------------------------
    def feed(self, samples_f32: np.ndarray) -> None:
        """喂入一段 float32 mono 16k 帧；命中关键词时调 ``on_wake(text)``。

        线程模型：与 VADTrigger 对齐 — sherpa-onnx 内部状态变更（accept_waveform /
        decode_stream / get_result）持 ``self._lock``；callback 在锁外触发，避
        免反向调用 ``stop()`` / ``reset_buffer()`` 死锁。
        """
        if samples_f32.size == 0:
            return
        samples_f32 = np.asarray(samples_f32, dtype=np.float32).reshape(-1)
        # mute 期间样本依然消费（持续 decode），但命中视为 muted-drop
        hits: list[str] = []
        with self._lock:
            self.stats.frames_fed += int(samples_f32.size)
            try:
                self._stream.accept_waveform(self.config.sample_rate, samples_f32)
                while self._spotter.is_ready(self._stream):
                    self._spotter.decode_stream(self._stream)
                r = self._spotter.get_result(self._stream)
            except Exception as e:  # noqa: BLE001
                log.warning("[wake] KWS decode error: %s", e)
                return
            if r:
                hits.append(r)
                # 命中后清流，准备下一次（与上游 keyword-spotter.py 示例一致）
                self._spotter.reset_stream(self._stream)
        # callback 在锁外
        for text in hits:
            self._handle_hit(text)

    def _handle_hit(self, text: str) -> None:
        self.stats.last_wake_text = text
        self.stats.last_wake_monotonic = time.monotonic()
        if self._muted:
            self.stats.wakes_while_muted += 1
            log.debug("[wake] drop muted hit %r", text)
            return
        self.stats.wakes_total += 1
        try:
            self.on_wake(text)
            self.stats.callback_ok += 1
        except Exception as e:  # noqa: BLE001
            log.warning("[wake] on_wake failed: %s: %s", type(e).__name__, e)
            self.stats.callback_fail += 1

    # ------------------------------------------------------------------
    # Microphone runtime（独立 daemon 模式；正常路径用 WakeVADBridge 共享流）
    # ------------------------------------------------------------------
    def start_microphone(self, *, block_seconds: float = 0.1) -> None:
        """起 daemon 线程持续读 sounddevice 输入，喂给 self.feed。

        与 ``VADTrigger.start_microphone`` 同样幂等：重复调用只起一份 InputStream，
        第二次进入 log.warning 并返回。
        """
        with self._mic_lock:
            if self._mic_thread is not None and self._mic_thread.is_alive():
                log.warning(
                    "[wake] start_microphone already running (thread=%s); ignoring",
                    self._mic_thread.name,
                )
                return
            self._stop_event.clear()
            self._mic_thread = threading.Thread(
                target=self._mic_loop,
                args=(block_seconds,),
                name="coco-wake-mic",
                daemon=True,
            )
            self._mic_thread.start()

    def stop(self, timeout: float = 1.5) -> None:
        self._stop_event.set()
        if self._mic_thread is not None and self._mic_thread is not threading.current_thread():
            self._mic_thread.join(timeout=timeout)

    def is_listening(self) -> bool:
        return self._mic_thread is not None and self._mic_thread.is_alive()

    def _mic_loop(self, block_seconds: float) -> None:
        cfg = self.config
        try:
            import sounddevice as sd
        except Exception as e:  # noqa: BLE001
            log.warning("[wake] sounddevice unavailable, mic loop exits: %s", e)
            return
        block = max(int(cfg.sample_rate * block_seconds), 1024)
        # audio-010: 真实 InputStream 调用站可选 wrap 在 open_stream_with_recovery 下。
        # COCO_AUDIO_RECOVERY=1 时退避重试 PortAudioError；OFF 时与原直连等价。
        def _open_input_stream():
            return sd.InputStream(
                samplerate=cfg.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=block,
            )

        def _do_open():
            try:
                from coco.audio_resilience import open_stream_with_recovery as _osr
                _s = _osr(_open_input_stream, stream_kind="input")
                if _s is None:
                    log.warning("[wake] InputStream open exhausted, mic loop exits")
                    return None
                return _s
            except Exception:  # noqa: BLE001
                return sd.InputStream(
                    samplerate=cfg.sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=block,
                )

        # audio-011: 外层 reopen-loop，内层 read-loop
        _stream = _do_open()
        if _stream is None:
            return
        with self._mic_stream_lock:
            self._current_mic_stream = _stream
        try:
            while not self._stop_event.is_set():
                try:
                    _stream.__enter__()
                except Exception as e:  # noqa: BLE001
                    log.warning("[wake] InputStream __enter__ failed: %s; mic loop exits", e)
                    return
                log.info("[wake] mic loop started (sr=%d block=%d)", cfg.sample_rate, block)
                try:
                    while not self._stop_event.is_set() and not self._reopen_event.is_set():
                        try:
                            data, _ovf = _stream.read(block)
                        except Exception as e:  # noqa: BLE001
                            if self._reopen_event.is_set():
                                break
                            log.warning("[wake] InputStream.read error: %s; sleep+retry", e)
                            time.sleep(0.2)
                            continue
                        samples = np.asarray(data, dtype=np.float32).reshape(-1)
                        try:
                            self.feed(samples)
                        except Exception as e:  # noqa: BLE001
                            log.warning("[wake] feed error: %s", e)
                finally:
                    try:
                        _stream.__exit__(None, None, None)
                    except Exception:  # noqa: BLE001
                        pass

                if self._stop_event.is_set():
                    break
                if not self._reopen_event.is_set():
                    break

                # ===== reopen 分支 =====
                t_stop = time.monotonic()
                meta = dict(self._reopen_meta)
                self._reopen_event.clear()
                old_dev = meta.get("device") or {}
                old_idx = old_dev.get("index")
                error_type = str(meta.get("error_type") or "requested")
                try:
                    _stop = getattr(_stream, "stop", None)
                    if callable(_stop):
                        _stop()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    _close = getattr(_stream, "close", None)
                    if callable(_close):
                        _close()
                except Exception:  # noqa: BLE001
                    pass
                # audio-012: 记录 close 完成时刻（实际丢失窗口下界）
                t_close_done = time.monotonic()
                with self._mic_stream_lock:
                    self._current_mic_stream = None

                _stream = _do_open()
                t_reopen_done = time.monotonic()
                if _stream is None:
                    log.warning("[wake] reopen failed: open exhausted; mic loop exits")
                    return
                with self._mic_stream_lock:
                    self._current_mic_stream = _stream
                self._reopen_count += 1
                try:
                    from coco.logging_setup import emit as _emit
                    new_dev = meta.get("device") or {}
                    new_idx = new_dev.get("index")
                    _emit(
                        "audio.stream_reopened",
                        subsystem="wake",
                        reason=str(meta.get("event") or "changed"),
                        old_device_idx=old_idx,
                        new_device_idx=new_idx,
                        error_type=error_type,
                        ts=time.time(),
                    )
                    # audio-012: 校准 lost_n window — 同 VADTrigger
                    dt_total = max(0.0, t_reopen_done - t_stop)
                    dt_actual = max(0.0, t_reopen_done - t_close_done)
                    from coco.vad_trigger import _read_loss_window_override_ms as _read_ovr
                    env_ms_override = _read_ovr()
                    if env_ms_override is not None:
                        window_ms = int(env_ms_override)
                        actual_ms = int(env_ms_override)
                        lost_n_actual = int((env_ms_override / 1000.0) * cfg.sample_rate)
                    else:
                        window_ms = int(dt_actual * 1000)
                        actual_ms = window_ms
                        lost_n_actual = int(dt_actual * cfg.sample_rate)
                    lost_n = int(dt_total * cfg.sample_rate)
                    _emit(
                        "audio.reopen_buffer_lost_n",
                        subsystem="wake",
                        lost_n=lost_n,
                        ms=int(dt_total * 1000),
                        lost_n_actual=lost_n_actual,
                        window_ms=window_ms,
                        actual_ms=actual_ms,
                        error_type=error_type,
                        ts=time.time(),
                    )
                except Exception:  # noqa: BLE001
                    pass
        finally:
            with self._mic_stream_lock:
                self._current_mic_stream = None
            log.info("[wake] mic loop stopped")


# ---------------------------------------------------------------------------
# Env-backed factory
# ---------------------------------------------------------------------------


def wake_word_enabled_from_env() -> bool:
    """``COCO_WAKE_WORD=1`` 才启用；默认 0（兼容 interact-003 行为不变）。"""
    return os.environ.get("COCO_WAKE_WORD", "0").strip().lower() in {"1", "true", "yes", "on"}


def _parse_clamped_float(env_key: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(env_key)
    if raw is None:
        return default
    try:
        val = float(raw)
    except ValueError:
        log.warning("[wake] %s=%r invalid float; fallback default=%s", env_key, raw, default)
        return default
    if val < lo or val > hi:
        clamped = max(lo, min(hi, val))
        log.warning(
            "[wake] %s=%s out of range [%s, %s]; clamped to %s",
            env_key, val, lo, hi, clamped,
        )
        return clamped
    return val


def config_from_env() -> WakeConfig:
    cfg = WakeConfig()
    cfg.threshold = _parse_clamped_float(
        "COCO_WAKE_THRESHOLD", cfg.threshold, 0.05, 0.95
    )
    cfg.window_seconds = _parse_clamped_float(
        "COCO_WAKE_WINDOW_SECONDS", cfg.window_seconds, 1.0, 60.0
    )
    return cfg


# ---------------------------------------------------------------------------
# WakeVADBridge —— 把 KWS + VAD 串成「awake-gated VAD」
# ---------------------------------------------------------------------------


class WakeVADBridge:
    """协调 ``WakeWordDetector`` + ``WakeGate`` + ``VADTrigger``。

    - 单一麦克数据流先喂给 ``wake.feed`` 做 KWS，再喂给 ``vad.feed`` 做 VAD。
    - VAD 触发的 utterance 在调 ``vad_callback`` 之前先过 ``gate.is_awake()``：
        - awake 内 → forward 给真 VAD callback（InteractSession.handle_audio）
        - sleeping → 丢弃 + ``stats.utterances_dropped_sleeping += 1``
    - wake 命中 → ``gate.trigger()`` 开窗口 + 可选打 log "[wake] awake for Ns"。

    用法：
        bridge = WakeVADBridge(detector, gate, real_vad_callback)
        vad = VADTrigger(bridge.vad_gate_callback, ...)
        bridge.bind_vad(vad)        # 把 mute/unmute 同步给 wake
        # main loop: 一份 sd InputStream → bridge.feed(samples_f32)

    线程：与 VADTrigger / WakeWordDetector 同步语义（feed 在调用方线程）。
    """

    def __init__(
        self,
        detector: WakeWordDetector,
        gate: WakeGate,
        vad_callback: Callable[[np.ndarray, int], None],
    ) -> None:
        self.detector = detector
        self.gate = gate
        self._real_vad_callback = vad_callback
        self._vad: Optional[object] = None
        self.utterances_dropped_sleeping = 0
        self.utterances_forwarded = 0
        # 接管 detector.on_wake 以驱动 gate
        original_on_wake = detector.on_wake

        def _wrapped_on_wake(text: str) -> None:
            self.gate.trigger()
            log.info("[wake] hit %r → awake for %.1fs", text, gate.window_seconds)
            try:
                original_on_wake(text)
            except Exception as e:  # noqa: BLE001
                log.warning("[wake] outer on_wake failed: %s", e)

        detector.on_wake = _wrapped_on_wake

    def bind_vad(self, vad) -> None:  # type hint loose to avoid circular import
        """把 VADTrigger 引用记下，供 mute/unmute 同步。"""
        self._vad = vad

    def vad_gate_callback(self, audio_int16: np.ndarray, sr: int) -> None:
        """VADTrigger.on_utterance 的实际接收方：先过 awake gate。"""
        if not self.gate.is_awake():
            self.utterances_dropped_sleeping += 1
            log.debug("[wake] drop VAD utterance (sleeping); dropped_total=%d",
                      self.utterances_dropped_sleeping)
            return
        self.utterances_forwarded += 1
        self._real_vad_callback(audio_int16, sr)

    def feed(self, samples_f32: np.ndarray) -> None:
        """单流入口：先 KWS 后 VAD（VADTrigger.feed 由外部 / wrap_tts 控制 mute）。"""
        # KWS
        self.detector.feed(samples_f32)
        # VAD
        if self._vad is not None:
            self._vad.feed(samples_f32)


__all__ = [
    "DEFAULT_CACHE",
    "DEFAULT_KEYWORDS",
    "KWS_DIR",
    "WakeConfig",
    "WakeGate",
    "WakeStats",
    "WakeVADBridge",
    "WakeWordDetector",
    "config_from_env",
    "wake_word_enabled_from_env",
]
