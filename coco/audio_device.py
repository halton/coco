"""Shared audio device resolution (audio-013 speaker + audio-016 mic).

audio-016: 对称 audio-013，自动把 mic 输入设备选成 Reachy Mini Audio。

API:
- resolve_input_device() -> int | str | None
- log_input_device_once(logger, device, sr) -> None  (去重 log)
- ENV_INPUT_DEVICE = "COCO_AUDIO_INPUT_DEVICE"

优先级：
1) env COCO_AUDIO_INPUT_DEVICE：纯数字 → int；非空字符串 → 原样返回（sd 支持 substring）
2) 遍历 sd.query_devices() 找 name 含 _REACHY_AUDIO_NAME_SUBSTRINGS 之一
   且 max_input_channels >= 1 → 返回 index
3) None（fallback 系统默认）

任何 sounddevice 查询失败一律 fail-soft 返回 None，不抛。
"""
from __future__ import annotations

import os
from typing import Optional, Union

ENV_INPUT_DEVICE = "COCO_AUDIO_INPUT_DEVICE"

_REACHY_AUDIO_NAME_SUBSTRINGS = ("reachy mini audio", "reachy_mini_audio")

# 进程内 log 去重 sentinel：(device, sr) 变化时才再 emit
_last_logged_input_device: object = object()


def _query_devices_safe():
    """安全返回 sd.query_devices() 列表；任何异常返回 []。"""
    try:
        import sounddevice as sd
        return sd.query_devices()
    except Exception:
        return []


def resolve_input_device() -> Optional[Union[int, str]]:
    """返回 sounddevice 可识别的 input device (int / str / None)。"""
    raw = os.environ.get(ENV_INPUT_DEVICE, "").strip()
    if raw:
        if raw.isdigit():
            try:
                return int(raw)
            except (ValueError, TypeError):
                return raw
        return raw

    try:
        devices = _query_devices_safe()
    except Exception:
        return None
    for idx, dev in enumerate(devices):
        try:
            name_lc = str(dev.get("name", "")).lower()
            max_in = int(dev.get("max_input_channels", 0) or 0)
        except Exception:
            continue
        if max_in < 1:
            continue
        if any(s in name_lc for s in _REACHY_AUDIO_NAME_SUBSTRINGS):
            return idx
    return None


def log_input_device_once(logger, device, sr) -> None:
    """首次或 (device, sr) 变化时 emit 一行 [mic] input device=... sr=... 。"""
    global _last_logged_input_device
    key = (device, sr)
    if key == _last_logged_input_device:
        return
    msg = f"[mic] input device={device} sr={sr}"
    try:
        if logger is not None:
            logger.info(msg)
    except Exception:
        pass
    try:
        print(msg, flush=True)
    except Exception:
        pass
    _last_logged_input_device = key
