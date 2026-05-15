"""coco.asr — 中文 ASR 接入 (SenseVoice-Small via sherpa-onnx)。

audio-002 verification 3 主验入口：transcribe_wav(path) -> 文本。

模型路径默认从 ``COCO_ASR_CACHE`` 环境变量取，否则 ``~/.cache/coco/asr``。
模型由 ``scripts/fetch_asr_models.sh`` 提前下载。

设计取舍：
- 模块级单例 recognizer，避免每次调用重新加载 ~239MB int8 模型。
- ``language="zh"`` 强制中文，避免 SenseVoice 误判为日/韩。
- 入参 wav 要求 16k mono；非 16k 直接报错（避免隐式重采样掩盖问题）。
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import sherpa_onnx

DEFAULT_CACHE = Path(
    os.environ.get("COCO_ASR_CACHE", str(Path.home() / ".cache" / "coco" / "asr"))
)
SENSEVOICE_DIR = DEFAULT_CACHE / "sense-voice-2024-07-17"
SILERO_VAD_PATH = DEFAULT_CACHE / "silero_vad" / "silero_vad.onnx"

_recognizer: sherpa_onnx.OfflineRecognizer | None = None


def clean_sensevoice_tags(text: str) -> str:
    """去掉 SenseVoice 输出里的 ``<|zh|><|HAPPY|><|Speech|>`` 等控制标签，只留可读文本。"""
    if not text:
        return ""
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "<" and "|>" in text[i:]:
            i = text.index("|>", i) + 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out).strip()


def _build_vad(
    sample_rate: int = 16000,
    threshold: float = 0.5,
    min_silence_duration: float = 0.25,
    min_speech_duration: float = 0.25,
    buffer_size_in_seconds: float = 100.0,
) -> sherpa_onnx.VoiceActivityDetector:
    """构造 Silero VAD。窗口固定 512 (Silero v4/v5 16k 要求)。"""
    if not SILERO_VAD_PATH.exists():
        raise FileNotFoundError(
            f"Silero VAD 模型未找到: {SILERO_VAD_PATH}。先跑 scripts/fetch_asr_models.sh"
        )
    silero = sherpa_onnx.SileroVadModelConfig(
        model=str(SILERO_VAD_PATH),
        threshold=threshold,
        min_silence_duration=min_silence_duration,
        min_speech_duration=min_speech_duration,
        window_size=512,
    )
    cfg = sherpa_onnx.VadModelConfig(
        silero_vad=silero,
        sample_rate=sample_rate,
        num_threads=1,
        provider="cpu",
        debug=False,
    )
    return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=buffer_size_in_seconds)


def _decode_segment(samples: np.ndarray) -> str:
    """对一段 float32 16k 单声道波形跑 OfflineRecognizer，返回文本。"""
    rec = _get_recognizer()
    stream = rec.create_stream()
    stream.accept_waveform(sample_rate=16000, waveform=samples.astype(np.float32, copy=False))
    rec.decode_stream(stream)
    return stream.result.text.strip()


def transcribe_segments_from_array(audio: np.ndarray, sample_rate: int = 16000) -> list[str]:
    """对一段已加载的 float32 16k 波形跑 VAD + ASR，返回每段文本。

    供离线脚本（喂 wav 给 VAD 验证 VAD→ASR 链路）使用，麦克路径走 transcribe_microphone。
    """
    if sample_rate != 16000:
        raise ValueError(f"采样率 {sample_rate} != 16000，请先重采样")
    audio = np.asarray(audio, dtype=np.float32)
    vad = _build_vad(sample_rate=sample_rate)
    # 按 window_size=512 chunk 喂给 VAD
    window = 512
    for i in range(0, len(audio), window):
        chunk = audio[i : i + window]
        if len(chunk) < window:
            # 末尾不足一窗：补零
            pad = np.zeros(window, dtype=np.float32)
            pad[: len(chunk)] = chunk
            chunk = pad
        vad.accept_waveform(chunk)
    vad.flush()
    results: list[str] = []
    while not vad.empty():
        seg = vad.front
        text = _decode_segment(np.asarray(seg.samples, dtype=np.float32))
        if text:
            results.append(text)
        vad.pop()
    return results


def transcribe_microphone(
    seconds: float = 5.0,
    vad_speech_pad_seconds: float = 0.3,  # 兼容签名占位（Silero 内部已含 pad 逻辑）
) -> list[str]:
    """从默认输入设备录 ``seconds`` 秒，沿途 VAD 切段实时识别，返回每段文本列表。

    退化路径：若 sounddevice 不可用或 VAD 初始化失败，抛原异常给调用方记录到 evidence。
    """
    import time

    import sounddevice as sd

    del vad_speech_pad_seconds  # noqa: F841 — 保留 API 兼容

    sample_rate = 16000
    block_seconds = 0.1
    block_size = int(sample_rate * block_seconds)  # 1600
    window = 512  # Silero 16k 必须 512

    vad = _build_vad(sample_rate=sample_rate)
    results: list[str] = []
    leftover = np.zeros(0, dtype=np.float32)

    deadline = time.monotonic() + float(seconds)
    # audio-011: 真实 InputStream 调用站 wrap 在 open_stream_with_recovery 下。
    # COCO_AUDIO_RECOVERY=1 时退避重试 sd.PortAudioError；OFF 时与原直连字节级等价
    # （helper 内部 short-circuit 直接 ``open_fn()``）。
    def _open_input_stream():
        return sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            blocksize=block_size,
        )
    try:
        from coco.audio_resilience import open_stream_with_recovery as _osr
        _stream = _osr(_open_input_stream, stream_kind="input")
        if _stream is None:
            # recovery 全部尝试用尽（仅 ON 时可能发生）—— 透传给调用方，与历史行为兼容
            raise RuntimeError("asr.transcribe_microphone: InputStream open exhausted")
    except RuntimeError:
        raise
    except Exception:  # noqa: BLE001
        # 任何 helper 自身异常（不应发生），最后兜底直连一次
        _stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            blocksize=block_size,
        )
    with _stream as stream:
        while time.monotonic() < deadline:
            data, _overflow = stream.read(block_size)
            samples = np.asarray(data, dtype=np.float32).reshape(-1)
            buf = np.concatenate([leftover, samples])
            # 按 512 切窗喂 VAD
            n_full = (len(buf) // window) * window
            for i in range(0, n_full, window):
                vad.accept_waveform(buf[i : i + window])
            leftover = buf[n_full:]
            # 取已完成的语音段
            while not vad.empty():
                seg = vad.front
                text = _decode_segment(np.asarray(seg.samples, dtype=np.float32))
                if text:
                    results.append(text)
                vad.pop()

    # 收尾：补零冲掉 leftover，再 flush
    if len(leftover) > 0:
        pad = np.zeros(window, dtype=np.float32)
        pad[: len(leftover)] = leftover
        vad.accept_waveform(pad)
    vad.flush()
    while not vad.empty():
        seg = vad.front
        text = _decode_segment(np.asarray(seg.samples, dtype=np.float32))
        if text:
            results.append(text)
        vad.pop()

    return results


def _get_recognizer() -> sherpa_onnx.OfflineRecognizer:
    """惰性加载 SenseVoice-Small recognizer，进程内单例。"""
    global _recognizer
    if _recognizer is None:
        model_path = SENSEVOICE_DIR / "model.int8.onnx"
        tokens_path = SENSEVOICE_DIR / "tokens.txt"
        if not model_path.exists():
            raise FileNotFoundError(
                f"SenseVoice 模型未找到: {model_path}。先跑 scripts/fetch_asr_models.sh"
            )
        if not tokens_path.exists():
            raise FileNotFoundError(
                f"SenseVoice tokens 未找到: {tokens_path}。先跑 scripts/fetch_asr_models.sh"
            )
        _recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_path),
            tokens=str(tokens_path),
            num_threads=2,
            use_itn=False,
            language="zh",
        )
    return _recognizer


def transcribe_wav(path: str | Path) -> str:
    """转写 wav 文件，返回原始识别文本（含 SenseVoice 标签前缀，由调用方按需后处理）。

    要求 16k mono；多通道自动 mean 成 mono；非 16k 抛 ValueError。

    依赖 scipy（reachy-mini 传递依赖里有），不依赖 soundfile。
    """
    import scipy.io.wavfile as wavfile

    path = str(path)
    sr, audio = wavfile.read(path)
    # 转 float32 [-1, 1]
    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    elif audio.dtype == np.int32:
        audio = audio.astype(np.float32) / 2147483648.0
    elif audio.dtype == np.uint8:
        audio = (audio.astype(np.float32) - 128.0) / 128.0
    else:
        audio = audio.astype(np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        raise ValueError(f"采样率 {sr} != 16000，请先重采样")

    rec = _get_recognizer()
    stream = rec.create_stream()
    stream.accept_waveform(sample_rate=16000, waveform=audio)
    rec.decode_stream(stream)
    return stream.result.text.strip()
