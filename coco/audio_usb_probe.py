"""audio-008: 真扬声器 USB 通路 sim 前置自检.

设计要点
========

- **default-OFF**：未设 ``COCO_AUDIO_USB_PROBE=1`` 时 :func:`probe_usb_speakers`
  立刻返回 disabled :class:`ProbeResult`，**不**调用 ``sounddevice.query_devices``，
  也**不**写 evidence/audio-008/probe.json，主路径零副作用。
- **gate ON**：枚举 sounddevice 输出设备 → 名称 regex/keyword 匹配（"usb" /
  "speaker" / 常见 USB 音频厂牌关键词，case-insensitive）→ 输出设备优先排序
  → 写 evidence/audio-008/probe.json。
- **跨平台**：mac/Linux/Windows 上 ``sounddevice.query_devices`` 接口一致；
  实际设备列表平台相关 → verify 通过 ``query_devices_fn`` 注入 fake provider。
- **失败优雅退化**：``query_devices`` 抛出 / sounddevice 未安装 → 返回
  ``ok=False`` 的 ProbeResult，emit 一条 warn 后继续，**不**抛进主流程。
- **emit schema**：gate ON 时启动期 emit ``audio.usb_probe``，包含
  ``device_count`` / ``matched_devices`` / ``latency_ms`` / ``ok``。

主路径 wire 见 ``coco/main.py``（启动期可选调一次，env gate）。

真机听感（USB 扬声器实际播放）不在本 feature 范围，作 ``real_machine_uat: pending``
异步项跟踪（feature_list.json audio-008 evidence 字段）。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, Optional, Sequence

# audio-008 marker: COCO_AUDIO_USB_PROBE env gate (default-OFF)
ENV_GATE = "COCO_AUDIO_USB_PROBE"

# 默认匹配 keyword（case-insensitive 子串 + 也可作为 regex 元素）
# 包含通用 "usb" / "speaker" + 常见 USB 音频厂牌特征片段
DEFAULT_NAME_PATTERNS: tuple[str, ...] = (
    r"usb",
    r"speaker",
    r"headphone",
    r"audio device",
    r"reachy",
    r"jabra",
    r"logitech",
    r"anker",
)

EVIDENCE_DIR = Path("evidence/audio-008")
EVIDENCE_FILE = EVIDENCE_DIR / "probe.json"


@dataclass
class DeviceInfo:
    """单个 sounddevice 设备的精简视图（不含厂商私有字段）。"""

    index: int
    name: str
    max_output_channels: int
    max_input_channels: int
    default_samplerate: float
    hostapi: int = 0
    matched: bool = False


@dataclass
class ProbeResult:
    """USB audio probe 一次结果。

    - ``enabled``: env gate 是否开启
    - ``ok``: probe 是否未抛异常完成（gate OFF 时为 True 表示"成功 short-circuit"）
    - ``device_count``: 枚举出的设备总数
    - ``matched_devices``: 匹配到的设备列表（最多 16 个，按 output_channels desc / index asc 排序）
    - ``matched_count``: 便利字段 = len(matched_devices)
    - ``latency_ms``: probe 耗时（仅 gate ON 计）
    - ``patterns``: 实际使用的匹配 pattern 列表
    - ``error``: 如 ``ok=False``，错误摘要
    """

    enabled: bool
    ok: bool = True
    device_count: int = 0
    matched_devices: List[DeviceInfo] = field(default_factory=list)
    matched_count: int = 0
    latency_ms: float = 0.0
    patterns: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # dataclass.asdict 已递归处理 DeviceInfo
        return d


def env_gate_enabled(env: Mapping[str, str] | None = None) -> bool:
    """读取 env gate；env 入参为 None 时回落 os.environ。"""
    e = env if env is not None else os.environ
    return e.get(ENV_GATE, "0") == "1"


def _compile_patterns(patterns: Sequence[str]) -> List[re.Pattern[str]]:
    out: List[re.Pattern[str]] = []
    for p in patterns:
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error:
            # 非法 regex 退化为字面量子串匹配
            out.append(re.compile(re.escape(p), re.IGNORECASE))
    return out


def _match_device(name: str, regexes: Sequence[re.Pattern[str]]) -> bool:
    if not name:
        return False
    return any(rx.search(name) for rx in regexes)


def _coerce_device_list(raw: Any) -> List[dict[str, Any]]:
    """sounddevice.query_devices() 没参数时返回 list-like；统一成 list[dict]。"""
    out: List[dict[str, Any]] = []
    if raw is None:
        return out
    if isinstance(raw, Mapping):
        # 单设备情况；保险起见也支持
        out.append(dict(raw))
        return out
    try:
        for item in raw:
            if isinstance(item, Mapping):
                out.append(dict(item))
            else:
                # sounddevice DeviceList 元素本质是 dict-like；用 dict() 强转
                try:
                    out.append(dict(item))  # type: ignore[arg-type]
                except Exception:  # noqa: BLE001
                    continue
    except TypeError:
        return out
    return out


def _to_device_info(idx: int, d: Mapping[str, Any], matched: bool) -> DeviceInfo:
    return DeviceInfo(
        index=int(d.get("index", idx)),
        name=str(d.get("name", "")),
        max_output_channels=int(d.get("max_output_channels", 0) or 0),
        max_input_channels=int(d.get("max_input_channels", 0) or 0),
        default_samplerate=float(d.get("default_samplerate", 0.0) or 0.0),
        hostapi=int(d.get("hostapi", 0) or 0),
        matched=matched,
    )


def probe_usb_speakers(
    *,
    query_devices_fn: Optional[Callable[[], Any]] = None,
    patterns: Optional[Sequence[str]] = None,
    env: Mapping[str, str] | None = None,
    write_evidence: bool = True,
    evidence_path: Optional[Path] = None,
    emit_fn: Optional[Callable[..., None]] = None,
) -> ProbeResult:
    """枚举 sounddevice 输出设备，按 name pattern 过滤并写 evidence.

    Parameters
    ----------
    query_devices_fn:
        可注入的 fake provider；签名 ``() -> list[dict]``。
        默认 None → 使用 ``sounddevice.query_devices``（未装时返回 ok=False）。
    patterns:
        匹配 pattern 序列（regex），默认 :data:`DEFAULT_NAME_PATTERNS`。
    env:
        env mapping；默认 ``os.environ``。
    write_evidence:
        gate ON 时是否写 ``evidence/audio-008/probe.json``。
    evidence_path:
        覆盖默认 evidence 路径（verify / test 注入用）。
    emit_fn:
        可选 emit 回调；默认 None（不 emit）。签名兼容 :func:`coco.logging_setup.emit`。

    Returns
    -------
    ProbeResult
        gate OFF → ``enabled=False, ok=True, device_count=0, matched_devices=[]``；
        且**不**调用 ``query_devices_fn``、**不**写 evidence。
    """
    if not env_gate_enabled(env):
        # default-OFF fast-path: 主路径零副作用
        return ProbeResult(enabled=False, ok=True)

    pats = list(patterns) if patterns is not None else list(DEFAULT_NAME_PATTERNS)
    regexes = _compile_patterns(pats)

    t0 = time.time()
    error: Optional[str] = None
    raw_devices: List[dict[str, Any]] = []

    try:
        if query_devices_fn is None:
            try:
                import sounddevice as _sd  # type: ignore
            except Exception as imp_exc:  # noqa: BLE001
                raise RuntimeError(
                    f"sounddevice import failed: {type(imp_exc).__name__}: {imp_exc}"
                )
            raw = _sd.query_devices()
        else:
            raw = query_devices_fn()
        raw_devices = _coerce_device_list(raw)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"

    matched: List[DeviceInfo] = []
    all_count = len(raw_devices)
    if error is None:
        for i, d in enumerate(raw_devices):
            name = str(d.get("name", ""))
            out_ch = int(d.get("max_output_channels", 0) or 0)
            # 仅匹配 output-capable 设备（扬声器/headphone 走 output）
            if out_ch <= 0:
                continue
            if _match_device(name, regexes):
                matched.append(_to_device_info(i, d, matched=True))

    # 输出设备优先排序：max_output_channels desc, index asc（稳定）
    matched.sort(key=lambda x: (-x.max_output_channels, x.index))
    if len(matched) > 16:
        matched = matched[:16]

    latency_ms = (time.time() - t0) * 1000.0

    result = ProbeResult(
        enabled=True,
        ok=(error is None),
        device_count=all_count,
        matched_devices=matched,
        matched_count=len(matched),
        latency_ms=round(latency_ms, 3),
        patterns=pats,
        error=error,
    )

    if write_evidence:
        try:
            target = evidence_path if evidence_path is not None else EVIDENCE_FILE
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as we:  # noqa: BLE001
            # 写 evidence 失败也不污染主流程
            if emit_fn is not None:
                try:
                    emit_fn(
                        "audio.usb_probe_evidence_write_failed",
                        component="audio",
                        message=f"{type(we).__name__}: {we}",
                    )
                except Exception:  # noqa: BLE001
                    pass

    if emit_fn is not None:
        try:
            emit_fn(
                "audio.usb_probe",
                component="audio",
                ok=result.ok,
                device_count=result.device_count,
                matched_devices=result.matched_count,
                latency_ms=result.latency_ms,
                error=result.error,
            )
        except Exception:  # noqa: BLE001
            pass

    return result


def probe_and_log_once(emit_fn: Optional[Callable[..., None]] = None) -> ProbeResult:
    """主程序启动期 wire 用：跑一次 probe + 一行 stdout log。

    无论 ok 与否都返回 ProbeResult；任何异常都被 :func:`probe_usb_speakers` 捕获。
    本函数自身不抛。
    """
    try:
        result = probe_usb_speakers(emit_fn=emit_fn)
    except Exception as exc:  # noqa: BLE001 — 兜底，绝不污染主路径
        print(f"[coco][audio] usb probe internal failure: {exc!r}", flush=True)
        return ProbeResult(enabled=False, ok=False, error=f"{type(exc).__name__}: {exc}")

    if not result.enabled:
        return result

    # audio-008 V8 marker: main.py 启动 log 一行 'audio usb probe matched=<N>'
    if result.ok:
        print(
            f"[coco][audio] usb probe matched={result.matched_count} "
            f"total={result.device_count} latency_ms={result.latency_ms:.1f}",
            flush=True,
        )
    else:
        print(
            f"[coco][audio] usb probe WARN ok=False error={result.error!r}",
            flush=True,
        )
    return result


__all__ = [
    "ENV_GATE",
    "DEFAULT_NAME_PATTERNS",
    "DeviceInfo",
    "ProbeResult",
    "env_gate_enabled",
    "probe_usb_speakers",
    "probe_and_log_once",
]
