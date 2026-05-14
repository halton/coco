"""audio-008 verification: 真扬声器 USB 通路 sim 前置自检.

跑法::

    uv run python scripts/verify_audio_008.py

子项（与 feature_list.json description 对齐）：

V1   gate OFF 主路径无 probe 调用 + 不写 evidence + ProbeResult.enabled=False
V2   gate ON probe 执行 + ProbeResult.enabled=True + ok=True
V3   probe.json 写入 evidence/audio-008/ 且 schema 含必需键
V4   probe 失败（query_devices 抛出）退化 ok=False，主流程不抛
V5   name 匹配支持 regex（自定义 patterns 与默认 patterns 行为一致）
V6   sounddevice 未装时优雅退化（query_devices_fn 用模拟 ImportError raiser）
V7   ProbeResult 含 device_count / matched_count / latency_ms / matched_devices
V8   AST/grep marker：coco/audio_usb_probe.py 含 audio-008 marker + ENV_GATE 名称；
     coco/main.py wire 含 probe_and_log_once 调用

retval：0 全 PASS；1 任一失败
evidence 落 evidence/audio-008/verify_summary.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": ok, "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def _fresh_env(*, gate_on: bool) -> Dict[str, str]:
    """构造 env mapping，避免污染 os.environ。"""
    e = {}
    if gate_on:
        e["COCO_AUDIO_USB_PROBE"] = "1"
    return e


# ----- 通用 fake fixtures -----
FAKE_DEVICES = [
    {
        "index": 0,
        "name": "MacBook Pro Microphone",
        "max_input_channels": 1,
        "max_output_channels": 0,
        "default_samplerate": 48000.0,
        "hostapi": 0,
    },
    {
        "index": 1,
        "name": "MacBook Pro Speakers",
        "max_input_channels": 0,
        "max_output_channels": 2,
        "default_samplerate": 48000.0,
        "hostapi": 0,
    },
    {
        "index": 2,
        "name": "Jabra Speak 510 USB",
        "max_input_channels": 1,
        "max_output_channels": 2,
        "default_samplerate": 48000.0,
        "hostapi": 0,
    },
    {
        "index": 3,
        "name": "USB Audio Device",
        "max_input_channels": 0,
        "max_output_channels": 2,
        "default_samplerate": 44100.0,
        "hostapi": 0,
    },
    {
        "index": 4,
        "name": "Anker PowerConf Speaker (USB)",
        "max_input_channels": 1,
        "max_output_channels": 6,
        "default_samplerate": 48000.0,
        "hostapi": 0,
    },
    {
        "index": 5,
        "name": "Unrelated Bluetooth Headset",  # 含 "headphone/headset" 相关但不命中关键词
        "max_input_channels": 0,
        "max_output_channels": 2,
        "default_samplerate": 48000.0,
        "hostapi": 0,
    },
]


def v1_default_off() -> None:
    """V1 gate OFF：probe short-circuit，不调用 query_devices，不写 evidence。"""
    from coco.audio_usb_probe import probe_usb_speakers

    call_counter = {"n": 0}

    def fake_qd():
        call_counter["n"] += 1
        return FAKE_DEVICES

    # 显式 env 不含 gate；避免读 os.environ
    tmp_evidence = ROOT / "evidence" / "audio-008" / "_v1_should_not_exist.json"
    if tmp_evidence.exists():
        tmp_evidence.unlink()
    r = probe_usb_speakers(
        query_devices_fn=fake_qd,
        env={},
        evidence_path=tmp_evidence,
    )
    ok = (
        r.enabled is False
        and r.ok is True
        and r.device_count == 0
        and r.matched_count == 0
        and call_counter["n"] == 0
        and not tmp_evidence.exists()
    )
    _record(
        "V1 gate OFF short-circuit (no query_devices call, no evidence)",
        ok,
        f"enabled={r.enabled} qd_calls={call_counter['n']} evidence_exists={tmp_evidence.exists()}",
    )


def v2_gate_on() -> None:
    """V2 gate ON：probe 执行，ok=True，matched 命中 USB 输出设备。"""
    from coco.audio_usb_probe import probe_usb_speakers

    r = probe_usb_speakers(
        query_devices_fn=lambda: FAKE_DEVICES,
        env=_fresh_env(gate_on=True),
        write_evidence=False,
    )
    names = [d.name for d in r.matched_devices]
    ok = (
        r.enabled is True
        and r.ok is True
        and r.matched_count >= 3  # 至少命中 3 个含 usb/speaker 的输出设备
        and any("Jabra" in n for n in names)
        and any("USB Audio Device" in n for n in names)
        and any("Anker" in n for n in names)
    )
    _record(
        "V2 gate ON probe matches USB output devices",
        ok,
        f"enabled={r.enabled} ok={r.ok} matched={r.matched_count} names={names}",
    )


def v3_evidence_written() -> None:
    """V3 gate ON 时 evidence/audio-008/probe.json 写入 + schema 含必需键。"""
    from coco.audio_usb_probe import probe_usb_speakers

    tmp = ROOT / "evidence" / "audio-008" / "_v3_probe.json"
    if tmp.exists():
        tmp.unlink()

    r = probe_usb_speakers(
        query_devices_fn=lambda: FAKE_DEVICES,
        env=_fresh_env(gate_on=True),
        write_evidence=True,
        evidence_path=tmp,
    )
    exists = tmp.exists()
    schema_ok = False
    payload: Optional[Dict[str, Any]] = None
    if exists:
        try:
            payload = json.loads(tmp.read_text(encoding="utf-8"))
            required = {
                "enabled",
                "ok",
                "device_count",
                "matched_devices",
                "matched_count",
                "latency_ms",
                "patterns",
            }
            schema_ok = required.issubset(payload.keys())
        except Exception as e:  # noqa: BLE001
            schema_ok = False
            payload = {"_err": repr(e)}

    ok = exists and schema_ok and r.enabled and r.ok
    _record(
        "V3 evidence probe.json written with required schema",
        ok,
        f"exists={exists} keys={(list(payload.keys()) if isinstance(payload, dict) else None)}",
    )


def v4_query_devices_raises() -> None:
    """V4 query_devices 抛出 → ok=False，主流程不抛，evidence 仍写。"""
    from coco.audio_usb_probe import probe_usb_speakers

    def raiser():
        raise RuntimeError("synthetic backend failure")

    tmp = ROOT / "evidence" / "audio-008" / "_v4_probe.json"
    if tmp.exists():
        tmp.unlink()

    try:
        r = probe_usb_speakers(
            query_devices_fn=raiser,
            env=_fresh_env(gate_on=True),
            evidence_path=tmp,
        )
        raised = False
    except Exception as e:  # noqa: BLE001
        raised = True
        r = None  # type: ignore[assignment]
        print(f"   unexpected raise: {e!r}")

    ok = (
        not raised
        and r is not None
        and r.enabled is True
        and r.ok is False
        and r.error
        and "synthetic backend failure" in r.error
        and tmp.exists()
    )
    _record(
        "V4 query_devices raises → graceful ok=False, evidence still written",
        bool(ok),
        f"raised={raised} ok_attr={(r.ok if r else None)} error={(r.error if r else None)!r}",
    )


def v5_regex_patterns() -> None:
    """V5 name 匹配支持 regex：自定义 pattern 严格区分。"""
    from coco.audio_usb_probe import probe_usb_speakers

    # 只匹配 "Jabra" 或 "Anker"
    r = probe_usb_speakers(
        query_devices_fn=lambda: FAKE_DEVICES,
        env=_fresh_env(gate_on=True),
        patterns=[r"^Jabra\b", r"^Anker\b"],
        write_evidence=False,
    )
    names = [d.name for d in r.matched_devices]
    ok = (
        r.enabled
        and r.ok
        and r.matched_count == 2
        and any("Jabra" in n for n in names)
        and any("Anker" in n for n in names)
        and not any("USB Audio Device" == n for n in names)
    )
    _record(
        "V5 custom regex patterns filter precisely",
        ok,
        f"matched_count={r.matched_count} names={names}",
    )


def v6_sounddevice_missing() -> None:
    """V6 sounddevice 未装：通过 query_devices_fn raise ImportError 形式模拟。

    本 verify 不真的 uninstall sounddevice；我们用 raiser 模拟 import 失败语义
    （audio_usb_probe 内对任何 import 异常都包成 RuntimeError 走 ok=False
    路径，对调用方而言效果一致）。
    """
    from coco.audio_usb_probe import probe_usb_speakers

    def raiser():
        raise ImportError("No module named 'sounddevice'")

    r = probe_usb_speakers(
        query_devices_fn=raiser,
        env=_fresh_env(gate_on=True),
        write_evidence=False,
    )
    ok = (
        r.enabled
        and r.ok is False
        and r.error
        and "sounddevice" in r.error
    )
    _record(
        "V6 sounddevice missing → graceful degrade ok=False",
        bool(ok),
        f"ok_attr={r.ok} error={r.error!r}",
    )


def v7_result_schema_fields() -> None:
    """V7 ProbeResult 含 device_count / matched_count / latency_ms / matched_devices。"""
    from coco.audio_usb_probe import probe_usb_speakers, ProbeResult, DeviceInfo

    r = probe_usb_speakers(
        query_devices_fn=lambda: FAKE_DEVICES,
        env=_fresh_env(gate_on=True),
        write_evidence=False,
    )
    field_ok = (
        isinstance(r, ProbeResult)
        and isinstance(r.device_count, int)
        and isinstance(r.matched_count, int)
        and isinstance(r.latency_ms, float)
        and isinstance(r.matched_devices, list)
        and all(isinstance(d, DeviceInfo) for d in r.matched_devices)
        and r.device_count == len(FAKE_DEVICES)
        and r.matched_count == len(r.matched_devices)
        and r.latency_ms >= 0.0
    )
    # 排序：输出 channels desc，相同时 index asc
    if len(r.matched_devices) >= 2:
        sorted_ok = all(
            (r.matched_devices[i].max_output_channels, -r.matched_devices[i].index)
            >= (r.matched_devices[i + 1].max_output_channels, -r.matched_devices[i + 1].index)
            for i in range(len(r.matched_devices) - 1)
        )
    else:
        sorted_ok = True
    ok = field_ok and sorted_ok
    _record(
        "V7 ProbeResult schema + stable sort",
        ok,
        f"device_count={r.device_count} matched_count={r.matched_count} "
        f"latency_ms={r.latency_ms} sorted={sorted_ok}",
    )


def v8_source_markers() -> None:
    """V8 AST/grep marker：模块 + main.py wire 标记齐全。"""
    probe_src = (ROOT / "coco" / "audio_usb_probe.py").read_text(encoding="utf-8")
    main_src = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")

    probe_ok = (
        "audio-008" in probe_src
        and "COCO_AUDIO_USB_PROBE" in probe_src
        and "ENV_GATE" in probe_src
        and "default-OFF" in probe_src
    )
    main_ok = (
        "audio-008" in main_src
        and "probe_and_log_once" in main_src
        and "COCO_AUDIO_USB_PROBE" in main_src
    )
    ok = probe_ok and main_ok
    _record(
        "V8 source markers (audio-008 / ENV_GATE / wire)",
        ok,
        f"probe_ok={probe_ok} main_ok={main_ok}",
    )


def _summarize() -> int:
    total = len(_results)
    failed = [r for r in _results if not r["ok"]]
    print()
    print(f"audio-008 verify: {total - len(failed)}/{total} PASS")
    for f in failed:
        print(f"  FAIL: {f['name']} — {f['detail']}")

    summary = {
        "feature": "audio-008",
        "results": _results,
        "passed": total - len(failed),
        "total": total,
        "ok": not failed,
    }
    out_dir = ROOT / "evidence" / "audio-008"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0 if not failed else 1


def main() -> int:
    # 确保 verify 自身不污染 os.environ（即使先前测试设过 gate）
    os.environ.pop("COCO_AUDIO_USB_PROBE", None)
    v1_default_off()
    v2_gate_on()
    v3_evidence_written()
    v4_query_devices_raises()
    v5_regex_patterns()
    v6_sounddevice_missing()
    v7_result_schema_fields()
    v8_source_markers()
    return _summarize()


if __name__ == "__main__":
    sys.exit(main())
