"""coco.vad_trigger — VAD 驱动的 push-to-talk 替代（interact-003）.

替换 stdin Enter PTT：用 Silero VAD（已由 audio-002 下载到 ~/.cache/coco/asr/silero_vad）
持续监听麦克风，speech 段累计 ≥ 250ms 自动触发 InteractSession.handle_audio。

设计取舍：
- VAD 实例与 ``coco.asr._build_vad`` 同源（同一个模型路径），保持单一事实源。
- ``feed(samples_f32)`` 是测试/集成的统一入口：sub-agent verification 直接喂 fixture wav，
  不依赖真 sounddevice 流（避免 CI / 无声卡环境炸）。
- ``start_microphone()`` 才会起后台 sounddevice 流；在无麦权限的环境里不应被调用。
- TTS 期间应主动 mute（避免话筒收到自家 TTS 输出再次触发，与 notes 风险 (3) 对齐）：
  ``mute_during(callable)`` 装饰器 / 上下文，包住 tts_say_fn / handle_audio。
- 与 IdleAnimator soft mutex 由 InteractSession.handle_audio 已经处理；本模块只负责
  「检出 speech 段 → 调 callback」。
- COCO_VAD_DISABLE=1 时调用方应跳过本模块，回到 stdin PTT；本模块不做这层旁路（边界清晰）。

线程模型：
- ``feed`` 是同步函数，调用方决定在哪个线程喂帧（测试用主线程；麦克模式下在内部 worker 线程）。
- 当 VAD 检出一段 speech 时，``on_utterance`` 在 *同一个* feed 线程内被同步调用 —
  下游（InteractSession.handle_audio）有自己的 ``_busy`` lock，重入安全。
- ``start_microphone()`` 起一个 daemon 线程跑 sounddevice 输入循环；``stop()`` 让它退出。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from coco import asr as coco_asr

log = logging.getLogger(__name__)


# audio-012: env override for reopen buffer-loss window estimate.
# 取值含义: 强制使用该毫秒数作为 lost_n_actual / window_ms 的估算窗口
# （便于 sim 验证 / 真机现场校准）。OFF (未设/非法/<=0) 时使用实测窗口。
# audio-013: 常量与解析函数实际定义已移到 coco.audio_resilience（公共 util）；
# 这里保留同名导出作为 thin delegate，维持外部向后兼容（含 verify_audio_012）。
from coco.audio_resilience import (
    ENV_LOSS_WINDOW_MS as ENV_LOSS_WINDOW_MS,  # re-export
    read_loss_window_override_ms as _read_loss_window_override_ms,  # alias
)


# ---------------------------------------------------------------------------
# Config & stats
# ---------------------------------------------------------------------------


@dataclass
class VADConfig:
    sample_rate: int = 16000
    window: int = 512  # Silero v4/v5 16k 必须 512
    threshold: float = 0.5
    min_speech_seconds: float = 0.25       # 累计 ≥ 250ms 才视为有效 utterance
    min_silence_seconds: float = 0.25      # VAD 内部 offset 判定（与 asr.py 对齐）
    cooldown_seconds: float = 1.5           # 触发后冷却，防连击
    max_utterance_seconds: float = 10.0     # 安全上限：超过强制截断丢弃


@dataclass
class VADStats:
    frames_fed: int = 0
    utterances_total: int = 0
    utterances_too_short: int = 0
    utterances_too_long: int = 0
    utterances_in_cooldown: int = 0
    utterances_while_muted: int = 0
    callback_ok: int = 0
    callback_fail: int = 0
    last_utterance_seconds: float = 0.0
    last_trigger_monotonic: float = 0.0


# ---------------------------------------------------------------------------
# VADTrigger
# ---------------------------------------------------------------------------


class VADTrigger:
    """VAD-driven trigger: feed audio frames, fire ``on_utterance`` per speech segment.

    Parameters
    ----------
    on_utterance : Callable[[np.ndarray, int], None]
        Receives (audio_int16 mono, sample_rate). Called synchronously in feed thread.
        Typical use: ``session.handle_audio``.
    config : VADConfig
        Tunable thresholds; defaults match feature_list verification.
    """

    def __init__(
        self,
        on_utterance: Callable[[np.ndarray, int], None],
        config: Optional[VADConfig] = None,
    ) -> None:
        self.on_utterance = on_utterance
        self.config = config or VADConfig()
        self.stats = VADStats()
        # 复用 asr._build_vad，保持单一事实源
        self._vad = coco_asr._build_vad(
            sample_rate=self.config.sample_rate,
            threshold=self.config.threshold,
            min_silence_duration=self.config.min_silence_seconds,
            min_speech_duration=self.config.min_speech_seconds,
        )
        self._leftover = np.zeros(0, dtype=np.float32)
        self._muted = False
        self._stop_event = threading.Event()
        self._mic_thread: Optional[threading.Thread] = None
        # feed 内部状态保护（feed 自身串行）；mute 标志独立，主线程可改
        self._lock = threading.Lock()
        # start_microphone 幂等保护（infra-debt-sweep M3）：
        # 多线程并发调用 start_microphone() 时只允许起一份 InputStream，
        # 第二次返回已起的引用并 log.warning。
        self._mic_lock = threading.Lock()
        # audio-011: reopen 机制 — hotplug device_change 时外部调 request_reopen()，
        # mic_loop 主动 stop+close 当前 stream 并 reopen 新 stream，emit
        # audio.stream_reopened + audio.reopen_buffer_lost_n。
        self._reopen_event = threading.Event()
        self._reopen_meta: dict = {}
        self._current_stream = None  # 仅供 inspection / 测试，不在外部线程操作
        self._stream_lock = threading.Lock()
        self._reopen_count = 0

    def request_reopen(
        self,
        event: str = "changed",
        device: Optional[dict] = None,
        error_type: str = "requested",
    ) -> None:
        """外部 hotplug cb 调用：请求 mic_loop 在下一轮 read 后 stop+reopen 当前 stream。

        线程安全：通过设置 ``_reopen_event`` + 直接 stop 当前 stream 强制 read 返回。
        实际 reopen 在 mic_loop 内部线程完成，避免跨线程操作 sounddevice 句柄。

        audio-012: 新增 ``error_type`` 字段（``"requested"`` 主动 hotplug / ``"portaudio_error"``
        read 异常自愈 / ``"unknown"``）。该值会回传给 ``audio.stream_reopened`` emit payload。
        """
        self._reopen_meta = {
            "event": str(event),
            "device": dict(device or {}),
            "error_type": str(error_type or "requested"),
        }
        self._reopen_event.set()
        # 主动 stop 当前 stream，让 mic_loop 内 read 立刻返回 / raise，进入 reopen 分支
        with self._stream_lock:
            s = self._current_stream
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
    # Mute / unmute（TTS 期间用，防自激）
    # ------------------------------------------------------------------
    def mute(self) -> None:
        self._muted = True

    def unmute(self) -> None:
        self._muted = False

    def is_muted(self) -> bool:
        return self._muted

    def wrap_tts(self, tts_say_fn: Callable[..., None]) -> Callable[..., None]:
        """包一层：调用 tts_say_fn 之前 mute、结束后 unmute（reset VAD 内部缓冲）。

        wrap 的 fn 与原签名一致 (text, blocking=True, ...) -> None。
        """
        def _wrapped(*args, **kwargs):
            self.mute()
            try:
                return tts_say_fn(*args, **kwargs)
            finally:
                # reset：丢弃 mute 期间累积的 vad 状态，避免残留触发
                self.reset_buffer()
                self.unmute()
        return _wrapped

    # ------------------------------------------------------------------
    # 核心：feed 一段 float32 16k 单声道波形
    # ------------------------------------------------------------------
    def feed(self, samples_f32: np.ndarray) -> None:
        """喂入一段 float32 mono 帧。VAD 内部累积 → 检出 utterance → 触发 callback。

        样本量任意；内部按 512 chunk 切窗喂 sherpa-onnx VAD。mute 期间样本依然消费但不触发回调。

        线程模型（infra-debt-sweep M2）：VAD 内部状态变更（accept_waveform / leftover）持
        ``self._lock``；但 ``on_utterance`` callback 在锁外调用，避免 callback 反向调用
        ``self.stop()`` / ``self.reset_buffer()`` 死锁。
        """
        if samples_f32.size == 0:
            return
        samples_f32 = np.asarray(samples_f32, dtype=np.float32).reshape(-1)
        cfg = self.config
        with self._lock:
            self.stats.frames_fed += int(samples_f32.size)
            buf = np.concatenate([self._leftover, samples_f32])
            n_full = (len(buf) // cfg.window) * cfg.window
            for i in range(0, n_full, cfg.window):
                self._vad.accept_waveform(buf[i : i + cfg.window])
            self._leftover = buf[n_full:]
            ready = self._drain_ready_segments_locked()
        # callback 在锁外，避免 callback 反向调用 stop()/reset_buffer() 死锁
        self._fire_segments(ready)

    def flush(self) -> None:
        """末尾收尾：补零冲掉 leftover 并让 VAD flush。供 verification 收尾用。"""
        cfg = self.config
        with self._lock:
            if len(self._leftover) > 0:
                pad = np.zeros(cfg.window, dtype=np.float32)
                pad[: len(self._leftover)] = self._leftover
                self._vad.accept_waveform(pad)
                self._leftover = np.zeros(0, dtype=np.float32)
            self._vad.flush()
            ready = self._drain_ready_segments_locked()
        self._fire_segments(ready)

    def reset_buffer(self) -> None:
        """丢掉 VAD 内部已累积的状态（mute 结束时用，避免 TTS 残留）。"""
        with self._lock:
            self._vad.reset()
            self._leftover = np.zeros(0, dtype=np.float32)

    # ------------------------------------------------------------------
    # 内部：从 VAD pop 出已完成 utterance，跑判决与回调
    # ------------------------------------------------------------------
    def _drain_ready_segments_locked(self) -> list[np.ndarray]:
        """从 VAD pop 出全部已完成 segments（须在 self._lock 内调用）。

        只做 VAD 层面的 pop（涉及 self._vad 内部状态），不跑长度/cooldown/mute 判决，
        也不触发 callback。判决与 callback 由 ``_fire_segments`` 在锁外完成。
        """
        out: list[np.ndarray] = []
        while not self._vad.empty():
            seg = self._vad.front
            samples_f32 = np.asarray(seg.samples, dtype=np.float32)
            self._vad.pop()
            out.append(samples_f32)
        return out

    def _fire_segments(self, segments: list[np.ndarray]) -> None:
        """对锁外的 segments 跑长度/cooldown/mute 判决，必要时触发 callback。

        infra-debt-sweep M2：callback 在 self._lock 之外调用，允许 callback 反向调
        ``self.stop()`` / ``self.reset_buffer()`` 而不死锁。stats 更新依赖 GIL 原子性
        （只是 ``+=`` 一个 int），不再额外加锁，与 mute 标志同样的并发模型。
        """
        cfg = self.config
        for samples_f32 in segments:
            seconds = len(samples_f32) / float(cfg.sample_rate)
            self.stats.last_utterance_seconds = seconds
            # 长度过滤
            if seconds < cfg.min_speech_seconds:
                self.stats.utterances_too_short += 1
                log.debug("[vad] drop too-short utterance %.3fs", seconds)
                continue
            if seconds > cfg.max_utterance_seconds:
                self.stats.utterances_too_long += 1
                log.warning("[vad] drop too-long utterance %.3fs", seconds)
                continue
            # mute 期间丢弃
            if self._muted:
                self.stats.utterances_while_muted += 1
                log.debug("[vad] drop muted utterance %.3fs", seconds)
                continue
            # cooldown
            now = time.monotonic()
            if now - self.stats.last_trigger_monotonic < cfg.cooldown_seconds:
                self.stats.utterances_in_cooldown += 1
                log.debug("[vad] drop cooldown utterance %.3fs", seconds)
                continue
            # 触发：转 int16，调 callback（锁外）
            self.stats.utterances_total += 1
            self.stats.last_trigger_monotonic = now
            audio_int16 = np.clip(samples_f32, -1.0, 1.0)
            audio_int16 = (audio_int16 * 32767).astype(np.int16)
            try:
                self.on_utterance(audio_int16, cfg.sample_rate)
                self.stats.callback_ok += 1
            except Exception as e:  # noqa: BLE001
                log.warning("[vad] on_utterance failed: %s: %s", type(e).__name__, e)
                self.stats.callback_fail += 1

    # ------------------------------------------------------------------
    # Microphone runtime
    # ------------------------------------------------------------------
    def start_microphone(self, *, block_seconds: float = 0.1) -> None:
        """起 daemon 线程持续读 sounddevice 输入，喂给 self.feed。

        失败（设备不可用 / 权限问题）会 log 并退出线程，不抛回主线程。

        infra-debt-sweep M3：用 ``self._mic_lock`` 保护幂等性，重复并发调用只起一份
        InputStream；第二次进入会 log.warning 并返回，不再起第二个线程。
        """
        with self._mic_lock:
            if self._mic_thread is not None and self._mic_thread.is_alive():
                log.warning(
                    "[vad] start_microphone already running (thread=%s); ignoring",
                    self._mic_thread.name,
                )
                return
            self._stop_event.clear()
            self._mic_thread = threading.Thread(
                target=self._mic_loop,
                args=(block_seconds,),
                name="coco-vad-mic",
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
            log.warning("[vad] sounddevice unavailable, mic loop exits: %s", e)
            return
        block = max(int(cfg.sample_rate * block_seconds), cfg.window)
        # audio-010: 真实 InputStream 调用站可选 wrap 在 open_stream_with_recovery 下。
        # COCO_AUDIO_RECOVERY=1 时构造 wrap（捕 sd.PortAudioError 退避重试）；
        # OFF 时调用 helper 路径与原直连 ``sd.InputStream(...)`` 字节级等价
        # （helper 内部 short-circuit 直接 ``open_fn()``，sentinel 透传）。
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
                    log.warning("[vad] InputStream open exhausted, mic loop exits")
                    return None
                return _s
            except Exception:  # noqa: BLE001
                # 任何 helper 自身异常（不应发生），最后兜底直连一次
                return sd.InputStream(
                    samplerate=cfg.sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=block,
                )

        # audio-011: 外层 reopen-loop，内层 read-loop；reopen_event 触发时 stop+close +
        # 重 open + emit 事件。首次进入直接 open。
        _stream = _do_open()
        if _stream is None:
            return
        with self._stream_lock:
            self._current_stream = _stream
        try:
            while not self._stop_event.is_set():
                # 进入新的 stream 的 read-loop
                # _stream 可能是 context manager（真 sd.InputStream）或 fake；
                # 真 InputStream 需要 __enter__/__exit__ 来启 PortAudio；fake 也支持。
                try:
                    _stream.__enter__()
                except Exception as e:  # noqa: BLE001
                    log.warning("[vad] InputStream __enter__ failed: %s; mic loop exits", e)
                    return
                log.info("[vad] mic loop started (sr=%d block=%d)", cfg.sample_rate, block)
                try:
                    while not self._stop_event.is_set() and not self._reopen_event.is_set():
                        try:
                            data, _ovf = _stream.read(block)
                        except Exception as e:  # noqa: BLE001
                            # reopen_event 可能在 read 期间被外部 request_reopen 调 stop()
                            # 触发；这里不区分错误源，回到外层让 reopen 分支处理
                            if self._reopen_event.is_set():
                                break
                            log.warning("[vad] InputStream.read error: %s; sleep+retry", e)
                            time.sleep(0.2)
                            continue
                        samples = np.asarray(data, dtype=np.float32).reshape(-1)
                        try:
                            self.feed(samples)
                        except Exception as e:  # noqa: BLE001
                            log.warning("[vad] feed error: %s", e)
                finally:
                    try:
                        _stream.__exit__(None, None, None)
                    except Exception:  # noqa: BLE001
                        pass

                if self._stop_event.is_set():
                    break
                if not self._reopen_event.is_set():
                    # read-loop 自然结束但未 reopen 也未 stop — 退出 mic_loop
                    break

                # ===== reopen 分支 =====
                t_stop = time.monotonic()
                meta = dict(self._reopen_meta)
                self._reopen_event.clear()
                old_dev = meta.get("device") or {}
                old_idx = old_dev.get("index")
                error_type = str(meta.get("error_type") or "requested")
                # 显式 stop+close（真 sd.InputStream / fake 都应支持）
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
                # audio-012: 记录 close 完成时刻，用于"实际丢失窗口"度量
                # (vs 上界 dt = t_reopen_done - t_stop，包含 stop+close 自身耗时)
                t_close_done = time.monotonic()
                with self._stream_lock:
                    self._current_stream = None

                # 打开新 stream
                _stream = _do_open()
                t_reopen_done = time.monotonic()
                if _stream is None:
                    log.warning("[vad] reopen failed: open exhausted; mic loop exits")
                    return
                with self._stream_lock:
                    self._current_stream = _stream
                self._reopen_count += 1
                # emit 事件
                try:
                    from coco.logging_setup import emit as _emit
                    new_dev = meta.get("device") or {}
                    new_idx = new_dev.get("index")
                    _emit(
                        "audio.stream_reopened",
                        subsystem="vad",
                        reason=str(meta.get("event") or "changed"),
                        old_device_idx=old_idx,
                        new_device_idx=new_idx,
                        error_type=error_type,
                        ts=time.time(),
                    )
                    # buffer-loss 估算：
                    #   - lost_n (上界)：stop→reopen_done 全时段 * sample_rate（保留旧字段供回归）
                    #   - lost_n_actual (校准)：close_done→reopen_done 实际不可读窗口
                    #   - window_ms：actual 窗口的毫秒
                    # env COCO_AUDIO_REOPEN_LOSS_WINDOW_MS 可强制覆盖 window（测试/校准用）
                    dt_total = max(0.0, t_reopen_done - t_stop)
                    dt_actual = max(0.0, t_reopen_done - t_close_done)
                    env_ms_override = _read_loss_window_override_ms()
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
                        subsystem="vad",
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
            with self._stream_lock:
                self._current_stream = None
            log.info("[vad] mic loop stopped")


# ---------------------------------------------------------------------------
# Env-backed factory（main.py 用）
# ---------------------------------------------------------------------------


def vad_disabled_from_env() -> bool:
    return os.environ.get("COCO_VAD_DISABLE", "0").strip().lower() in {"1", "true", "yes", "on"}


def _parse_clamped_float(env_key: str, default: float, lo: float, hi: float) -> float:
    """Parse env float, clamp to [lo, hi]; warn + fallback on parse error or out-of-range."""
    raw = os.environ.get(env_key)
    if raw is None:
        return default
    try:
        val = float(raw)
    except ValueError:
        log.warning("[vad] %s=%r invalid float; fallback default=%s", env_key, raw, default)
        return default
    if val < lo or val > hi:
        clamped = max(lo, min(hi, val))
        log.warning(
            "[vad] %s=%s out of range [%s, %s]; clamped to %s",
            env_key, val, lo, hi, clamped,
        )
        return clamped
    return val


def config_from_env() -> VADConfig:
    cfg = VADConfig()
    cfg.threshold = _parse_clamped_float("COCO_VAD_THRESHOLD", cfg.threshold, 0.0, 1.0)
    cfg.cooldown_seconds = _parse_clamped_float(
        "COCO_VAD_COOLDOWN", cfg.cooldown_seconds, 0.0, 10.0
    )
    cfg.min_speech_seconds = _parse_clamped_float(
        "COCO_VAD_MIN_SPEECH", cfg.min_speech_seconds, 0.05, 5.0
    )
    cfg.max_utterance_seconds = _parse_clamped_float(
        "COCO_VAD_MAX_SPEECH", cfg.max_utterance_seconds, 0.5, 30.0
    )
    return cfg


__all__ = [
    "VADConfig",
    "VADStats",
    "VADTrigger",
    "vad_disabled_from_env",
    "config_from_env",
]
