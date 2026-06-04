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
import threading
import time
import wave
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Literal, Optional

import numpy as np
import sherpa_onnx

# audio-014: edge-tts 流式 + 联网默认 + 断网回退
ENV_TTS_PREFER = "COCO_TTS_PREFER"  # auto | edge | local；default auto
ENV_TTS_EDGE_VOICE = "COCO_TTS_EDGE_VOICE"
ENV_TTS_EDGE_TIMEOUT = "COCO_TTS_EDGE_TIMEOUT"
DEFAULT_EDGE_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_EDGE_TIMEOUT = 8.0
# bing 边缘 endpoint；socket.create_connection 快速探活
_EDGE_PROBE_HOST = "speech.platform.bing.com"
_EDGE_PROBE_PORT = 443
_EDGE_PROBE_TIMEOUT = 1.5

# 进程内 backend 切换日志去重
_last_logged_backend: object = object()


class EdgeTTSUnavailable(Exception):
    """edge-tts 不可用（未装 / 网络失败 / 合成异常）。触发 fallback Kokoro。"""

# audio-009: TTS LRU 缓存。default-OFF（``COCO_TTS_LRU=1`` 启用）。
# 注意 env 名不沿用 ``COCO_TTS_CACHE`` —— 后者历史上指 Kokoro 模型 cache 目录路径，
# 语义冲突。故新增 ``COCO_TTS_LRU`` (开关) + ``COCO_TTS_LRU_SIZE`` (maxsize) 两个 env。
ENV_TTS_LRU = "COCO_TTS_LRU"
ENV_TTS_LRU_SIZE = "COCO_TTS_LRU_SIZE"
DEFAULT_TTS_LRU_SIZE = 64

_synth_cache_lock = threading.Lock()
_synth_cache: "OrderedDict[tuple, tuple[np.ndarray, int]]" = OrderedDict()
_synth_cache_stats: dict[str, int] = {"hits": 0, "misses": 0, "evictions": 0}


def _tts_lru_on() -> bool:
    return os.environ.get(ENV_TTS_LRU, "0") == "1"


def _tts_lru_size() -> int:
    raw = os.environ.get(ENV_TTS_LRU_SIZE, "")
    if not raw:
        return DEFAULT_TTS_LRU_SIZE
    try:
        v = int(raw)
        return v if v > 0 else DEFAULT_TTS_LRU_SIZE
    except (ValueError, TypeError):
        return DEFAULT_TTS_LRU_SIZE


def reset_tts_cache() -> None:
    """测试 / 调试 helper：清空 LRU 缓存与 stats。"""
    with _synth_cache_lock:
        _synth_cache.clear()
        _synth_cache_stats["hits"] = 0
        _synth_cache_stats["misses"] = 0
        _synth_cache_stats["evictions"] = 0


def get_tts_cache_stats() -> dict[str, int]:
    """返回 cache stats 的浅拷贝（hits/misses/evictions/size）。"""
    with _synth_cache_lock:
        return {
            "hits": _synth_cache_stats["hits"],
            "misses": _synth_cache_stats["misses"],
            "evictions": _synth_cache_stats["evictions"],
            "size": len(_synth_cache),
        }

# companion-007: backend 的 prosody 能力声明。
# Kokoro v1.1（sherpa-onnx）只有 ``speed``，没有原生 pitch；pitch 调节走 fallback no-op。
# 真要做 pitch shift 需要外接 librosa/pyrubberband 后处理；当前 phase 不引入。
_BACKEND_SUPPORTS_RATE = True
_BACKEND_SUPPORTS_PITCH = False

# 进程级 fallback emit 一次 flag；避免每次 say 都刷屏 'tts.prosody_unsupported'
_PROSODY_FALLBACK_EMITTED = False

DEFAULT_CACHE = Path(
    os.environ.get("COCO_TTS_CACHE", str(Path.home() / ".cache" / "coco" / "tts"))
)
KOKORO_DIR = DEFAULT_CACHE / "kokoro-int8-multi-lang-v1_1"

# Kokoro v1.1 中文女声 sid 默认；上游 voices.bin 含 100+ speaker，常见中文女声音色在 50 之后
DEFAULT_SID = 50
DEFAULT_SPEED = 1.0

# 安全上限，防止误传超长文本卡住 CPU
MAX_TEXT_LEN = 500

# audio-013: TTS 输出设备自动选择
# - env COCO_TTS_OUTPUT_DEVICE 显式覆盖（整数 → device index；字符串 → substring 匹配 by sounddevice）
# - 未设 env → 自动遍历 sd.query_devices() 找名字含下列子串且 max_output_channels >= 1 的 → 返回 index
# - 都未命中 → None（fallback 系统默认）
ENV_TTS_OUTPUT_DEVICE = "COCO_TTS_OUTPUT_DEVICE"
_REACHY_AUDIO_NAME_SUBSTRINGS = ("reachy mini audio", "reachy_mini_audio")

# 进程内 device 解析结果缓存（避免每次 play 都遍历 + 刷屏 log）
_last_logged_device: object = object()  # sentinel


def _resolve_tts_output_device():
    """返回 sounddevice 可识别的 device (int / str / None)。

    优先级：
    1) env COCO_TTS_OUTPUT_DEVICE：整数 → int；其他非空字符串 → 原样返回（sd 支持 substring）
    2) 遍历 sd.query_devices() 找 name 含 _REACHY_AUDIO_NAME_SUBSTRINGS 之一 + max_output_channels>=1
    3) None
    """
    raw = os.environ.get(ENV_TTS_OUTPUT_DEVICE, "").strip()
    if raw:
        try:
            return int(raw)
        except (ValueError, TypeError):
            return raw

    try:
        import sounddevice as sd
        devices = sd.query_devices()
    except Exception:
        return None

    for idx, dev in enumerate(devices):
        try:
            name = str(dev.get("name", "")).lower()
            max_out = int(dev.get("max_output_channels", 0) or 0)
        except Exception:
            continue
        if max_out < 1:
            continue
        if any(sub in name for sub in _REACHY_AUDIO_NAME_SUBSTRINGS):
            return idx
    return None


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


def _synthesize_uncached(
    text: str,
    sid: int,
    speed: float,
) -> tuple[np.ndarray, int]:
    """裸合成路径（无 cache）。便于测试 mock + 让 cache 包装层解耦。"""
    tts = _get_tts()
    audio = tts.generate(text, sid=sid, speed=speed)
    samples = np.asarray(audio.samples, dtype=np.float32)
    return samples, int(audio.sample_rate)


def synthesize(
    text: str,
    sid: int = DEFAULT_SID,
    speed: float = DEFAULT_SPEED,
) -> tuple[np.ndarray, int]:
    """本地 Kokoro 合成。返回 (samples float32 [-1,1], sample_rate).

    audio-009: ``COCO_TTS_LRU=1`` 时启用 LRU 缓存，key 为 ``(text, sid, speed)``。
    cache OFF 时（默认）行为与 phase-3 完全等价：每次直接调底层合成，无 dict 查找、无锁。
    """
    text = _check_text(text)
    if not (0.5 <= speed <= 2.0):
        raise ValueError(f"speed={speed} out of range [0.5, 2.0]")

    if not _tts_lru_on():
        return _synthesize_uncached(text, sid, speed)

    key = (text, int(sid), round(float(speed), 6))
    with _synth_cache_lock:
        hit = _synth_cache.get(key)
        if hit is not None:
            # LRU: move-to-end 标记为最新使用
            _synth_cache.move_to_end(key)
            _synth_cache_stats["hits"] += 1
            samples, sr = hit
            # 返回拷贝避免上游 in-place 修改污染缓存
            return np.array(samples, copy=True), int(sr)

    samples, sr = _synthesize_uncached(text, sid, speed)
    with _synth_cache_lock:
        _synth_cache_stats["misses"] += 1
        _synth_cache[key] = (np.array(samples, copy=True), int(sr))
        _synth_cache.move_to_end(key)
        # evict overflow
        max_size = _tts_lru_size()
        while len(_synth_cache) > max_size:
            _synth_cache.popitem(last=False)
            _synth_cache_stats["evictions"] += 1
    return samples, sr


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
    """走本机扬声器播放；audio-013 起优先选 Reachy Mini Audio。

    解析顺序见 _resolve_tts_output_device()。device 选中且其 default_samplerate
    与 sample_rate 不同且在 (8000, 48000] 时，用 scipy.signal.resample_poly 重采样
    成 device sr 后再播；重采样失败（scipy 缺失 / 异常）→ 落回原 sr 直接播不抛。
    每进程仅在首次 / device 变化时 log 一行。
    """
    import sounddevice as sd

    global _last_logged_device

    device = _resolve_tts_output_device()
    final_sr = int(sample_rate)
    final_samples = samples

    if device is not None:
        # 拿 device default samplerate
        dev_sr: Optional[int] = None
        try:
            dev_info = sd.query_devices(device)
            dsr = dev_info.get("default_samplerate", None) if isinstance(dev_info, dict) else None
            if dsr is not None:
                dev_sr_f = float(dsr)
                if 8000 < dev_sr_f <= 48000:
                    dev_sr = int(dev_sr_f)
        except Exception:
            dev_sr = None

        if dev_sr is not None and dev_sr != int(sample_rate):
            try:
                from scipy.signal import resample_poly
                from math import gcd
                up = dev_sr
                down = int(sample_rate)
                g = gcd(up, down)
                final_samples = resample_poly(samples, up // g, down // g).astype(np.float32, copy=False)
                final_sr = dev_sr
            except Exception as exc:  # noqa: BLE001
                logging.getLogger("tts").warning(
                    "[tts] resample %d→%d failed: %s: %s; fallback to original sr",
                    int(sample_rate), dev_sr, type(exc).__name__, exc,
                )
                final_samples = samples
                final_sr = int(sample_rate)

    if device != _last_logged_device:
        print(f"[tts] output device={device!r} sr={final_sr}")
        _last_logged_device = device

    if device is None:
        sd.play(final_samples, samplerate=final_sr, blocking=blocking)
    else:
        sd.play(final_samples, samplerate=final_sr, blocking=blocking, device=device)


def _is_edge_tts_reachable(timeout: float = _EDGE_PROBE_TIMEOUT) -> bool:
    """快速 TCP 探活 Microsoft Speech edge endpoint。

    audio-014: ``say()`` 在 prefer="auto" 路径下用本函数决定走 edge 还是 local，
    避免把 ~8s 的 HTTP 等待塞到用户耳朵里。失败原因不返回，只返回 bool；
    任何异常（DNS / 拒绝 / 超时）都当作不可达。
    """
    import socket
    try:
        with socket.create_connection((_EDGE_PROBE_HOST, _EDGE_PROBE_PORT), timeout=timeout):
            return True
    except Exception:
        return False


def _synthesize_edge_streaming(
    text: str,
    voice: str = DEFAULT_EDGE_VOICE,
    timeout: float = DEFAULT_EDGE_TIMEOUT,
) -> tuple[np.ndarray, int]:
    """edge-tts 流式合成：chunk 边收边累积 → soundfile 解 mp3 → float32 mono。

    与旧 ``synthesize_edge`` 不同：
    - 不落临时 mp3 文件，bytes 全程在内存。
    - 用 ``comm.stream()`` 而非 ``comm.save()``，省去 mp3 文件 I/O。
    - 解码后多声道自动 mean → mono；返回 (samples float32 mono, sr).

    失败统一抛 ``EdgeTTSUnavailable``（含 import error / 网络异常 / 解码失败 / 空音频）。
    调用方应 try/except 后 fallback Kokoro。
    """
    text = _check_text(text)
    try:
        import asyncio
        import io
        import edge_tts  # type: ignore
        import soundfile as sf  # type: ignore
    except ImportError as e:
        raise EdgeTTSUnavailable(
            f"edge-tts/soundfile 未安装: {e}. 装 extras: pip install -e '.[tts-online]'"
        ) from e

    async def _run() -> bytes:
        buf = bytearray()
        comm = edge_tts.Communicate(text, voice)
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                data = chunk.get("data")
                if data:
                    buf.extend(data)
        return bytes(buf)

    try:
        mp3_bytes = asyncio.run(asyncio.wait_for(_run(), timeout=timeout))
    except Exception as e:  # noqa: BLE001
        raise EdgeTTSUnavailable(f"edge-tts stream failed: {type(e).__name__}: {e}") from e

    if not mp3_bytes:
        raise EdgeTTSUnavailable("edge-tts returned empty audio buffer")

    try:
        samples, sr = sf.read(io.BytesIO(mp3_bytes), dtype="float32", always_2d=False)
    except Exception as e:  # noqa: BLE001
        raise EdgeTTSUnavailable(f"soundfile decode failed: {type(e).__name__}: {e}") from e

    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    samples = np.ascontiguousarray(samples, dtype=np.float32)
    if samples.size == 0 or int(sr) <= 0:
        raise EdgeTTSUnavailable("edge-tts decoded to empty samples")
    return samples, int(sr)


def synthesize_edge(
    text: str,
    voice: str = DEFAULT_EDGE_VOICE,
    out_path: Path | str | None = None,
) -> tuple[np.ndarray, int]:
    """edge-tts 联网合成（向后兼容入口；底层走 _synthesize_edge_streaming）。

    audio-014: 实现改为流式 in-memory，不再落 mp3 临时文件。若调用方传 ``out_path``
    则在解码后把 float32 PCM 落成 wav（不再产生 mp3）。
    """
    samples, sr = _synthesize_edge_streaming(text, voice=voice)
    if out_path is not None:
        wav_path = Path(out_path).with_suffix(".wav")
        write_wav(wav_path, samples, sr)
    return samples, sr


def _resolve_tts_prefer(explicit: str | None) -> str:
    """决定本次 say 走哪个 backend。

    优先级：函数实参 explicit ("local"|"edge"|"auto") > env COCO_TTS_PREFER > "auto"。
    返回值始终是 {"local", "edge", "auto"} 之一。
    """
    val = (explicit or os.environ.get(ENV_TTS_PREFER, "") or "auto").strip().lower()
    if val not in {"local", "edge", "auto"}:
        val = "auto"
    return val


def _edge_voice() -> str:
    return os.environ.get(ENV_TTS_EDGE_VOICE, "").strip() or DEFAULT_EDGE_VOICE


def _edge_timeout() -> float:
    raw = os.environ.get(ENV_TTS_EDGE_TIMEOUT, "").strip()
    if not raw:
        return DEFAULT_EDGE_TIMEOUT
    try:
        v = float(raw)
        return v if v > 0 else DEFAULT_EDGE_TIMEOUT
    except (ValueError, TypeError):
        return DEFAULT_EDGE_TIMEOUT


def _log_backend(backend: str, voice: str | None, sr: int) -> None:
    """进程内 backend 切换 / 首次成功时 log 一行。"""
    global _last_logged_backend
    key = (backend, voice, sr)
    if key != _last_logged_backend:
        print(f"[tts] backend={backend} voice={voice!r} sr={sr}", flush=True)
        _last_logged_backend = key


def _try_edge_say(text: str, blocking: bool) -> bool:
    """尝试 edge-tts 路径；成功并播完返回 True，任何失败返回 False（caller fallback）。"""
    voice = _edge_voice()
    timeout = _edge_timeout()
    try:
        samples, sr = _synthesize_edge_streaming(text, voice=voice, timeout=timeout)
    except EdgeTTSUnavailable as e:
        print(f"[tts] edge failed, fallback local: {e}", flush=True)
        return False
    except Exception as e:  # noqa: BLE001 safety net
        print(f"[tts] edge failed (unexpected), fallback local: {type(e).__name__}: {e}", flush=True)
        return False
    _log_backend("edge", voice, sr)
    play(samples, sr, blocking=blocking)
    return True


def _emit_prosody_unsupported_once(rate: Optional[float], pitch: Optional[float], reason: str) -> None:
    """companion-007: 当 backend 不支持 rate / pitch 时，进程内仅 emit 一次。

    用于 verify 抓 'tts.prosody_unsupported' 而不刷屏 jsonl。
    """
    global _PROSODY_FALLBACK_EMITTED
    if _PROSODY_FALLBACK_EMITTED:
        return
    _PROSODY_FALLBACK_EMITTED = True
    try:
        from coco.logging_setup import emit as _emit
        _emit(
            "tts.prosody_unsupported",
            message=f"backend prosody unsupported: {reason}",
            rate=rate,
            pitch_semitone=pitch,
            backend="kokoro-sherpa-onnx",
            supports_rate=_BACKEND_SUPPORTS_RATE,
            supports_pitch=_BACKEND_SUPPORTS_PITCH,
        )
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("tts").warning(
            "[tts] emit tts.prosody_unsupported failed: %s: %s",
            type(exc).__name__, exc,
        )


def reset_prosody_fallback_emit_flag() -> None:
    """测试用：重置 _PROSODY_FALLBACK_EMITTED 让下次 emit 再次触发。"""
    global _PROSODY_FALLBACK_EMITTED
    _PROSODY_FALLBACK_EMITTED = False


def say(
    text: str,
    prefer: Optional[Literal["local", "edge", "auto"]] = None,
    sid: int = DEFAULT_SID,
    speed: float = DEFAULT_SPEED,
    blocking: bool = True,
    emotion: Optional[str] = None,
    expression: Optional[str] = None,
    rate: Optional[float] = None,
    pitch_semitone: Optional[float] = None,
) -> None:
    """合成并通过本机扬声器播放（**默认阻塞，整段播完才返回**）。

    audio-014: ``prefer`` 路由（None → env ``COCO_TTS_PREFER`` → "auto"）：
      - "auto"  → 先 _is_edge_tts_reachable() 探测；通 → edge 流式合成；否则 local Kokoro
      - "edge"  → 直接尝试 edge；任何失败 fallback local
      - "local" → 强制 Kokoro，完全不碰 edge / 网络

    interact-006: ``emotion`` 参数仅 log 标注（''tts say emotion=happy text=...''），
    phase-4 simulate-only 不真实改 voice 参数；真机调参留 milestone gate。

    robot-003: ``expression`` 参数语义化触发 ExpressionPlayer.play(expression)。
    与 emotion 等价共用同一触发路径（expression 显式胜过 emotion）。两者都未传则
    完全不走 expression 链路。

    companion-007 prosody:
    - ``rate`` (None | float)：速率偏移（0.05 = +5%；负数 = 减速）。
      非 None 且 ``_BACKEND_SUPPORTS_RATE`` → 把 ``speed`` 乘以 (1+rate)；
      否则 fallback no-op + emit ``tts.prosody_unsupported``（每进程一次）。
    - ``pitch_semitone`` (None | float)：音高偏移（半音）。Kokoro 不原生支持 →
      fallback no-op + emit ``tts.prosody_unsupported``。
    fallback 不阻塞 / 不抛 / 仍然正常播放原声（行为与 phase-3 等价）。

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

    # companion-007: rate / pitch_semitone 应用 + fallback emit
    effective_speed = float(speed)
    if rate is not None:
        if _BACKEND_SUPPORTS_RATE:
            # rate 视作偏移：rate=0.05 → speed *= 1.05
            effective_speed = float(speed) * (1.0 + float(rate))
            # 安全 clamp 到 sherpa speed 合法区间 [0.5, 2.0]
            if effective_speed < 0.5:
                effective_speed = 0.5
            elif effective_speed > 2.0:
                effective_speed = 2.0
        else:
            _emit_prosody_unsupported_once(rate, pitch_semitone, "rate not supported")
    if pitch_semitone is not None and pitch_semitone != 0.0:
        if not _BACKEND_SUPPORTS_PITCH:
            _emit_prosody_unsupported_once(rate, pitch_semitone, "pitch_semitone not supported")

    # audio-014: prefer 路由（auto/edge/local）
    mode = _resolve_tts_prefer(prefer)

    if mode == "local":
        samples, sr = synthesize(text, sid=sid, speed=effective_speed)
        _log_backend("local", None, sr)
        play(samples, sr, blocking=blocking)
        return

    if mode == "edge":
        if _try_edge_say(text, blocking=blocking):
            return
        # fall through to local fallback
        samples, sr = synthesize(text, sid=sid, speed=effective_speed)
        _log_backend("local", None, sr)
        play(samples, sr, blocking=blocking)
        return

    # mode == "auto": 先探测可达性，避免把 ~Ns HTTP 等待塞给用户
    if _is_edge_tts_reachable():
        if _try_edge_say(text, blocking=blocking):
            return
    else:
        print("[tts] edge unreachable (auto), using local", flush=True)

    samples, sr = synthesize(text, sid=sid, speed=effective_speed)
    _log_backend("local", None, sr)
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
    prefer: Optional[Literal["local", "edge", "auto"]] = None,
    sid: int = DEFAULT_SID,
    speed: float = DEFAULT_SPEED,
    *,
    expression: Optional[str] = None,
    emotion: Optional[str] = None,
    rate: Optional[float] = None,
    pitch_semitone: Optional[float] = None,
):
    """非阻塞版 say()。返回一个 daemon Thread，调用方可决定是否 join。

    用于 ReachyMiniApp.run() 等需要保持心跳/stop_event 循环不被阻塞的场景。
    异常被吞掉只打日志，避免线程崩溃影响主循环。

    robot-003: ``expression`` / ``emotion`` 透传到 say()，让异步路径同样能触发
    ExpressionPlayer.play(expression)；与同步 say() 行为等价（player.play 同步
    在 worker 线程内调用，~1s 阻塞不影响主线程心跳）。

    companion-007: ``rate`` / ``pitch_semitone`` 透传到 say()；详见 say() docstring。
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
                rate=rate,
                pitch_semitone=pitch_semitone,
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
    "ENV_TTS_LRU",
    "ENV_TTS_LRU_SIZE",
    "ENV_TTS_OUTPUT_DEVICE",
    "ENV_TTS_PREFER",
    "ENV_TTS_EDGE_VOICE",
    "ENV_TTS_EDGE_TIMEOUT",
    "DEFAULT_TTS_LRU_SIZE",
    "DEFAULT_EDGE_VOICE",
    "DEFAULT_EDGE_TIMEOUT",
    "EdgeTTSUnavailable",
    "synthesize",
    "synthesize_edge",
    "_synthesize_edge_streaming",
    "_is_edge_tts_reachable",
    "say",
    "say_async",
    "play",
    "write_wav",
    "has_edge_tts",
    "set_expression_player",
    "get_expression_player",
    "reset_prosody_fallback_emit_flag",
    "reset_tts_cache",
    "get_tts_cache_stats",
]
