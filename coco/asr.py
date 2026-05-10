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

_recognizer: sherpa_onnx.OfflineRecognizer | None = None


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
