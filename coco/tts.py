"""coco.tts — 中文 TTS 输出 (Kokoro-multi-lang-v1.1 int8 via sherpa-onnx)。

audio-003 主入口：
  - say(text, prefer="local") -> None  合成并通过 sounddevice 播放
  - synthesize(text, ...) -> (samples, sample_rate)  仅合成不播放（便于落 wav）

设计要点：
- 模块级单例 OfflineTts，避免每次调用重新加载 ~110MB int8 + 50MB voices.bin。
- 离线优先：默认 prefer="local" 走 Kokoro；prefer="edge" 联网走 edge-tts，失败自动回退到 local。
- edge-tts 是可选依赖（pyproject extras 'tts-online'），未装时 prefer="edge" 直接降级。
- 中文使用 Kokoro v1.1-zh 体系，speaker id 默认 50（v1.1 中文女声音色范围 50..102；具体音色看 voices.bin 顺序，可由 sid 调整）。
- 模型路径 ${COCO_TTS_CACHE:-~/.cache/coco/tts}/kokoro-int8-multi-lang-v1_1/，由 scripts/fetch_tts_models.sh 提前下载。
"""

from __future__ import annotations

import os
import shutil
import time
import wave
import logging
from pathlib import Path
from typing import Literal, Optional

import numpy as np
import sherpa_onnx

DEFAULT_CACHE = Path(
    os.environ.get("COCO_TTS_CACHE", str(Path.home() / ".cache" / "coco" / "tts"))
)
KOKORO_DIR = DEFAULT_CACHE / "kokoro-int8-multi-lang-v1_1"

# Kokoro v1.1 中文女声 sid 默认；上游 voices.bin 含 100+ speaker，常见中文女声音色在 50 之后
DEFAULT_SID = 50
DEFAULT_SPEED = 1.0

# 安全上限，防止误传超长文本卡住 CPU
MAX_TEXT_LEN = 500

_tts: sherpa_onnx.OfflineTts | None = None

# robot-003: 可选 ExpressionPlayer 注入点。
# main.py 在构造完 ExpressionPlayer 后调 ``set_expression_player(player)``。
# 若未注入，``say(expression=...)`` 仅 log 不触发 robot 动作。
_expression_player: object | None = None


def set_expression_player(player: object | None) -> None:
    """注入 ExpressionPlayer（None 表示解绑）。

    main.py 启动时调用一次。expression_player 是 robot-003 的能力；
    未注入时 ``say(expression=...)`` 路径完全退化（行为等价 phase-3）。
    """
    global _expression_player
    _expression_player = player


def get_expression_player() -> object | None:
    return _expression_player


def _build_tts() -> sherpa_onnx.OfflineTts:
    """构造 Kokoro OfflineTts。缺文件直接 raise FileNotFoundError 并提示。"""
    model = KOKORO_DIR / "model.int8.onnx"
    voices = KOKORO_DIR / "voices.bin"
    tokens = KOKORO_DIR / "tokens.txt"
    data_dir = KOKORO_DIR / "espeak-ng-data"
    dict_dir = KOKORO_DIR / "dict"
    lexicon = KOKORO_DIR / "lexicon-zh.txt"  # 主中文 lexicon；多语 lexicon 用 ',' 串接也可

    for p in (model, voices, tokens, data_dir, dict_dir):
        if not p.exists():
            raise FileNotFoundError(
                f"Kokoro TTS 资源未找到: {p}。先跑 `bash scripts/fetch_tts_models.sh`"
            )

    kokoro_cfg = sherpa_onnx.OfflineTtsKokoroModelConfig(
        model=str(model),
        voices=str(voices),
        tokens=str(tokens),
        data_dir=str(data_dir),
        dict_dir=str(dict_dir),
        lexicon=str(lexicon) if lexicon.exists() else "",
        length_scale=1.0,
        lang="",  # 自动按文本检测；明确填 "zh" 也可
    )
    model_cfg = sherpa_onnx.OfflineTtsModelConfig(
        kokoro=kokoro_cfg,
        num_threads=2,
        debug=False,
        provider="cpu",
    )
    cfg = sherpa_onnx.OfflineTtsConfig(
        model=model_cfg,
        max_num_sentences=1,
    )
    if not cfg.validate():
        raise RuntimeError("OfflineTtsConfig.validate() 返回 False，配置不合法")
    return sherpa_onnx.OfflineTts(cfg)


def _get_tts() -> sherpa_onnx.OfflineTts:
    global _tts
    if _tts is None:
        _tts = _build_tts()
    return _tts


def _check_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    text = text.strip()
    if not text:
        raise ValueError("text is empty after strip")
    if len(text) > MAX_TEXT_LEN:
        raise ValueError(f"text length {len(text)} > MAX_TEXT_LEN={MAX_TEXT_LEN}")
    return text


def synthesize(
    text: str,
    sid: int = DEFAULT_SID,
    speed: float = DEFAULT_SPEED,
) -> tuple[np.ndarray, int]:
    """本地 Kokoro 合成。返回 (samples float32 [-1,1], sample_rate)."""
    text = _check_text(text)
    if not (0.5 <= speed <= 2.0):
        raise ValueError(f"speed={speed} out of range [0.5, 2.0]")

    tts = _get_tts()
    audio = tts.generate(text, sid=sid, speed=speed)
    samples = np.asarray(audio.samples, dtype=np.float32)
    return samples, int(audio.sample_rate)


def write_wav(path: Path | str, samples: np.ndarray, sample_rate: int) -> None:
    """落 16-bit PCM mono wav。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


def play(samples: np.ndarray, sample_rate: int, blocking: bool = True) -> None:
    """走本机默认输出设备播放。延迟 import sounddevice 避免主路径阻塞。"""
    import sounddevice as sd

    sd.play(samples, samplerate=sample_rate, blocking=blocking)


def synthesize_edge(
    text: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
    out_path: Path | str | None = None,
) -> tuple[np.ndarray, int]:
    """edge-tts 联网兜底。需安装 edge-tts (extras=tts-online)。

    返回 (samples float32, sample_rate)；可选写到 out_path。
    """
    text = _check_text(text)
    try:
        import asyncio
        import edge_tts  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "edge-tts 未安装。装 extras: `uv pip install -e .[tts-online]` 或 `pip install edge-tts`"
        ) from e

    # edge-tts 输出 mp3，需要 ffmpeg/soundfile 解码；为简化只落 mp3 + 再读
    import tempfile

    if out_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        mp3_path = Path(tmp.name)
        tmp.close()
    else:
        mp3_path = Path(out_path).with_suffix(".mp3")
        mp3_path.parent.mkdir(parents=True, exist_ok=True)

    async def _run() -> None:
        comm = edge_tts.Communicate(text, voice)
        await comm.save(str(mp3_path))

    asyncio.run(_run())

    # 解码 mp3：优先 soundfile（可选依赖），失败则只返回路径相关空 array 让调用方播放 mp3
    try:
        import soundfile as sf  # type: ignore
        samples, sr = sf.read(str(mp3_path), dtype="float32", always_2d=False)
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        return samples.astype(np.float32), int(sr)
    except Exception:
        # 返回原始 mp3 字节给调用方处理
        return np.zeros(0, dtype=np.float32), 0


def say(
    text: str,
    prefer: Literal["local", "edge"] = "local",
    sid: int = DEFAULT_SID,
    speed: float = DEFAULT_SPEED,
    blocking: bool = True,
    emotion: Optional[str] = None,
    expression: Optional[str] = None,
) -> None:
    """合成并通过本机扬声器播放（**默认阻塞，整段播完才返回**）。

    prefer="local"  → Kokoro；
    prefer="edge"   → edge-tts，失败/无网/未装时自动回退到 local。

    interact-006: ``emotion`` 参数仅 log 标注（''tts say emotion=happy text=...''），
    phase-4 simulate-only 不真实改 voice 参数；真机调参留 milestone gate。

    robot-003: ``expression`` 参数语义化触发 ExpressionPlayer.play(expression)。
    与 emotion 等价共用同一触发路径（expression 显式胜过 emotion）。两者都未传则
    完全不走 expression 链路。

    注意：player.play(expression) 在 say() 内**同步阻塞**（典型 ~1s，依 sequence
    帧数与 duration），随后才进入 synthesize/play 音频环节。在 ReachyMiniApp.run()
    等需要保持心跳/stop_event 循环的主线程内，请改用 say_async()，否则播放期间
    (~2-5s) 心跳会被卡住。
    """
    # expression 优先级 > emotion；emotion 作为兼容路径
    trigger_label = expression or emotion
    if trigger_label:
        # spec V4: 用 print 与 logging 双发；evidence 抓 'tts say emotion=...'
        msg = f"tts say emotion={trigger_label} text={text!r}"
        print(f"[coco.tts] {msg}")
        logging.getLogger("tts").info(msg, extra={"component": "tts", "event": "say", "emotion": trigger_label, "text": text})
        # robot-003: 若 ExpressionPlayer 已注入且 expression 命中库，触发 play
        # 设计：与 TTS 同步前置 fire（先动头再发声），fail-soft 失败不阻塞 say()
        player = _expression_player
        if player is not None:
            try:
                play_fn = getattr(player, "play", None)
                if callable(play_fn):
                    play_fn(trigger_label)
            except Exception as e:  # noqa: BLE001
                logging.getLogger("tts").warning(
                    "expression_player.play(%r) failed: %s: %s",
                    trigger_label, type(e).__name__, e,
                )
    if prefer == "edge":
        try:
            samples, sr = synthesize_edge(text)
            if samples.size > 0 and sr > 0:
                play(samples, sr, blocking=blocking)
                return
        except Exception as e:
            print(f"[coco.tts] edge-tts 失败回退本地: {type(e).__name__}: {e}")

    samples, sr = synthesize(text, sid=sid, speed=speed)
    play(samples, sr, blocking=blocking)


def has_edge_tts() -> bool:
    """是否安装了 edge-tts 可选依赖。"""
    try:
        import edge_tts  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def say_async(
    text: str,
    prefer: Literal["local", "edge"] = "local",
    sid: int = DEFAULT_SID,
    speed: float = DEFAULT_SPEED,
    *,
    expression: Optional[str] = None,
    emotion: Optional[str] = None,
):
    """非阻塞版 say()。返回一个 daemon Thread，调用方可决定是否 join。

    用于 ReachyMiniApp.run() 等需要保持心跳/stop_event 循环不被阻塞的场景。
    异常被吞掉只打日志，避免线程崩溃影响主循环。

    robot-003: ``expression`` / ``emotion`` 透传到 say()，让异步路径同样能触发
    ExpressionPlayer.play(expression)；与同步 say() 行为等价（player.play 同步
    在 worker 线程内调用，~1s 阻塞不影响主线程心跳）。
    """
    import threading

    def _worker() -> None:
        try:
            say(
                text,
                prefer=prefer,
                sid=sid,
                speed=speed,
                blocking=True,
                expression=expression,
                emotion=emotion,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[coco.tts] say_async 失败: {type(e).__name__}: {e}", flush=True)

    t = threading.Thread(target=_worker, name="coco-tts-say", daemon=True)
    t.start()
    return t


__all__ = [
    "DEFAULT_SID",
    "DEFAULT_SPEED",
    "MAX_TEXT_LEN",
    "KOKORO_DIR",
    "synthesize",
    "synthesize_edge",
    "say",
    "say_async",
    "play",
    "write_wav",
    "has_edge_tts",
    "set_expression_player",
    "get_expression_player",
]
