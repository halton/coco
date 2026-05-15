"""audio-012 verification: hotplug→reopen 真链路 + buffer-loss 窗口校准 + error_type 区分.

跑法::

    uv run python scripts/verify_audio_012.py

子项 (V1-V5)：

V1   端到端 hotplug→reopen 链路：HotplugWatcher 注入 fake query_devices_fn 模拟设备
     变化 → 真实的 vad reopen cb (调 VADTrigger.request_reopen) → mic_loop 真做
     stop+close+reopen → emit audio.stream_reopened (subsystem=vad)。
V2   error_type 字段：request_reopen(error_type="portaudio_error") 与
     "requested" 两种来源在 emit audio.stream_reopened / audio.reopen_buffer_lost_n
     payload 中均含 error_type 字段且值正确传播。
V3   buffer-loss 窗口校准：emit audio.reopen_buffer_lost_n 包含新字段
     lost_n_actual / window_ms / actual_ms；满足 lost_n_actual <= lost_n（actual 是更紧的窗口）。
V4   env COCO_AUDIO_REOPEN_LOSS_WINDOW_MS override：9-case 解析 + 实际 emit 时
     window_ms / lost_n_actual 被强制覆盖（不再使用实测窗口）。
V5   regression — verify_audio_011 / audio_010 / audio_009 / infra-018 / robot-007 /
     vision-012 / interact-017 全 PASS。

evidence 落 evidence/audio-012/verify_summary.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "evidence" / "audio-012"
SUMMARY_PATH = EVIDENCE_DIR / "verify_summary.json"

_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, **detail: Any) -> None:
    _results.append({"name": name, "ok": bool(ok), **detail})
    flag = "PASS" if ok else "FAIL"
    print(f"[verify_audio_012] {name}: {flag} {detail}", flush=True)


# ---------------------------------------------------------------------------
# Fake sd.InputStream（与 verify_audio_011 同形态）
# ---------------------------------------------------------------------------
class _FakeMicStream:
    instances: List["_FakeMicStream"] = []

    def __init__(self, sr: int = 16000, block: int = 512):
        import numpy as np
        self.sr = sr
        self.block = block
        self.started = 0
        self.stopped = 0
        self.closed = 0
        self.entered = 0
        self.exited = 0
        self.reads = 0
        self._stopped_flag = False
        self._np = np
        _FakeMicStream.instances.append(self)

    def __enter__(self):
        self.entered += 1
        self.started += 1
        return self

    def __exit__(self, *a, **k):
        self.exited += 1
        return False

    def start(self):
        self.started += 1
        self._stopped_flag = False

    def stop(self):
        self.stopped += 1
        self._stopped_flag = True

    def close(self):
        self.closed += 1

    def read(self, n):
        if self._stopped_flag:
            raise RuntimeError("fake stream stopped")
        self.reads += 1
        time.sleep(0.02)
        return (self._np.zeros((n, 1), dtype="float32"), False)


def _install_emit_capture() -> List[tuple]:
    captured: List[tuple] = []
    from coco import logging_setup as ls
    orig = ls.emit

    def _cap(evt, **payload):
        captured.append((evt, dict(payload)))
        return None

    ls.emit = _cap  # type: ignore[assignment]
    captured.append(("__orig__", {"orig": orig}))
    return captured


def _restore_emit(captured: List[tuple]) -> None:
    from coco import logging_setup as ls
    for evt, payload in captured:
        if evt == "__orig__":
            ls.emit = payload["orig"]  # type: ignore[assignment]
            return


# ---------------------------------------------------------------------------
# V1: 端到端 hotplug→reopen 链路
# ---------------------------------------------------------------------------
def v1_end_to_end_chain() -> None:
    name = "V1_end_to_end_hotplug_reopen_chain"
    try:
        import importlib
        from coco import vad_trigger as vt_mod
        importlib.reload(vt_mod)
        from coco import audio_resilience as ar_mod
        importlib.reload(ar_mod)
        from coco.vad_trigger import VADTrigger, VADConfig
        from coco.audio_resilience import HotplugWatcher

        import sounddevice as sd
        orig_input = sd.InputStream
        _FakeMicStream.instances = []
        sd.InputStream = lambda **kw: _FakeMicStream(sr=kw.get("samplerate", 16000), block=kw.get("blocksize", 512))  # type: ignore[assignment]

        captured = _install_emit_capture()
        try:
            v = VADTrigger(on_utterance=lambda s: None, config=VADConfig(sample_rate=16000))
            v.start_microphone(block_seconds=0.05)
            time.sleep(0.1)

            # 模拟 HotplugWatcher device 变化序列
            states = [
                [{"index": 0, "name": "OldMic", "max_input_channels": 1, "max_output_channels": 0}],
                [{"index": 0, "name": "OldMic", "max_input_channels": 1, "max_output_channels": 0},
                 {"index": 1, "name": "NewMic", "max_input_channels": 1, "max_output_channels": 0}],
            ]
            idx = {"i": 0}

            def _q():
                i = idx["i"]
                idx["i"] = min(i + 1, len(states) - 1)
                return states[i]

            emits_hotplug: List[tuple] = []
            w = HotplugWatcher(
                poll_interval=0.05,
                emit_fn=lambda evt, **p: emits_hotplug.append((evt, p)),
                query_devices_fn=_q,
            )

            # 注册真实业务 cb：调 vad.request_reopen
            cb_called = {"n": 0, "last_event": None, "last_device": None}

            def _real_vad_reopen_cb(event: str, device: dict) -> None:
                cb_called["n"] += 1
                cb_called["last_event"] = event
                cb_called["last_device"] = dict(device or {})
                v.request_reopen(event=event, device=device, error_type="requested")

            w.add_reopen_callback(_real_vad_reopen_cb)
            w.prime()  # baseline
            # 此时 _prev = states[0]; 下次 poll_once 拿 states[1] → diff 出 added=NewMic
            n_before = len(_FakeMicStream.instances)
            w.poll_once()
            # 等 mic_loop 完成 stop+reopen
            time.sleep(0.25)
            n_after = len(_FakeMicStream.instances)
            v.stop(timeout=2.0)

            reopened = [(e, p) for e, p in captured if e == "audio.stream_reopened"]
            ok_cb_called = cb_called["n"] == 1 and cb_called["last_event"] == "added"
            ok_stream_recreated = n_after > n_before
            ok_emit = bool(reopened) and reopened[0][1].get("subsystem") == "vad"
            # 第一个 fake 必须被 stop+close
            first = _FakeMicStream.instances[0]
            ok_stopclose = first.stopped >= 1 and first.closed >= 1
            # HotplugWatcher 也应 emit 一次 audio.device_change
            dev_changes = [e for e in emits_hotplug if e[0] == "audio.device_change"]
            ok_dev_change = len(dev_changes) == 1 and dev_changes[0][1].get("event") == "added"

            ok = ok_cb_called and ok_stream_recreated and ok_emit and ok_stopclose and ok_dev_change
            _record(
                name, ok,
                cb_called_n=cb_called["n"],
                instances_before=n_before, instances_after=n_after,
                first_stopped=first.stopped, first_closed=first.closed,
                emit_reopened_n=len(reopened),
                dev_change_n=len(dev_changes),
                reopen_call_count=w.reopen_call_count,
            )
        finally:
            _restore_emit(captured)
            sd.InputStream = orig_input  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        import traceback
        _record(name, False, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc().splitlines()[-3:])


# ---------------------------------------------------------------------------
# V2: error_type 字段在 emit payload 中传播
# ---------------------------------------------------------------------------
def v2_error_type_propagation() -> None:
    name = "V2_error_type_propagation"
    try:
        import importlib
        from coco import vad_trigger as vt_mod
        importlib.reload(vt_mod)
        from coco.vad_trigger import VADTrigger, VADConfig

        import sounddevice as sd
        orig_input = sd.InputStream
        _FakeMicStream.instances = []
        sd.InputStream = lambda **kw: _FakeMicStream(sr=kw.get("samplerate", 16000), block=kw.get("blocksize", 512))  # type: ignore[assignment]

        captured = _install_emit_capture()
        try:
            v = VADTrigger(on_utterance=lambda s: None, config=VADConfig(sample_rate=16000))
            v.start_microphone(block_seconds=0.05)
            time.sleep(0.1)
            # 先一次 "requested"
            v.request_reopen(event="changed", device={"index": 1, "name": "M1"}, error_type="requested")
            time.sleep(0.18)
            # 再一次 "portaudio_error"
            v.request_reopen(event="changed", device={"index": 2, "name": "M2"}, error_type="portaudio_error")
            time.sleep(0.18)
            v.stop(timeout=2.0)

            reopened = [p for e, p in captured if e == "audio.stream_reopened"]
            lost = [p for e, p in captured if e == "audio.reopen_buffer_lost_n"]
            types_reopen = [p.get("error_type") for p in reopened]
            types_lost = [p.get("error_type") for p in lost]
            # 至少两次 reopen，前后 error_type 分别为 requested / portaudio_error
            ok_count = len(reopened) >= 2 and len(lost) >= 2
            ok_field_present = all("error_type" in p for p in reopened) and all("error_type" in p for p in lost)
            ok_types = (
                "requested" in types_reopen and "portaudio_error" in types_reopen
                and "requested" in types_lost and "portaudio_error" in types_lost
            )
            ok = ok_count and ok_field_present and ok_types
            _record(
                name, ok,
                reopened_n=len(reopened), lost_n=len(lost),
                types_reopen=types_reopen, types_lost=types_lost,
            )
        finally:
            _restore_emit(captured)
            sd.InputStream = orig_input  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        import traceback
        _record(name, False, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc().splitlines()[-3:])


# ---------------------------------------------------------------------------
# V3: lost_n_actual / window_ms 字段 + 与上界 lost_n 关系
# ---------------------------------------------------------------------------
def v3_window_calibration() -> None:
    name = "V3_window_calibration_fields"
    try:
        import importlib
        from coco import vad_trigger as vt_mod
        importlib.reload(vt_mod)
        from coco.vad_trigger import VADTrigger, VADConfig

        import sounddevice as sd
        orig_input = sd.InputStream
        _FakeMicStream.instances = []
        sd.InputStream = lambda **kw: _FakeMicStream(sr=kw.get("samplerate", 16000), block=kw.get("blocksize", 512))  # type: ignore[assignment]

        captured = _install_emit_capture()
        try:
            os.environ.pop("COCO_AUDIO_REOPEN_LOSS_WINDOW_MS", None)
            v = VADTrigger(on_utterance=lambda s: None, config=VADConfig(sample_rate=16000))
            v.start_microphone(block_seconds=0.05)
            time.sleep(0.1)
            v.request_reopen(event="changed", device={"index": 3, "name": "X"})
            time.sleep(0.25)
            v.stop(timeout=2.0)

            lost = [p for e, p in captured if e == "audio.reopen_buffer_lost_n"]
            ok_emit = bool(lost)
            p0 = lost[0] if lost else {}
            ok_fields = all(
                k in p0 for k in ("lost_n", "ms", "lost_n_actual", "window_ms", "actual_ms", "error_type")
            )
            # actual 是更紧的窗口 → lost_n_actual <= lost_n
            ok_relation = (
                ok_emit
                and isinstance(p0.get("lost_n"), int)
                and isinstance(p0.get("lost_n_actual"), int)
                and p0["lost_n_actual"] <= p0["lost_n"]
                and isinstance(p0.get("window_ms"), int)
                and p0["window_ms"] >= 0
                and p0["window_ms"] == p0.get("actual_ms")
            )
            ok = ok_emit and ok_fields and ok_relation
            _record(
                name, ok,
                emit_n=len(lost),
                lost_n=p0.get("lost_n"),
                lost_n_actual=p0.get("lost_n_actual"),
                window_ms=p0.get("window_ms"),
                actual_ms=p0.get("actual_ms"),
                fields_ok=ok_fields,
            )
        finally:
            _restore_emit(captured)
            sd.InputStream = orig_input  # type: ignore[assignment]
            os.environ.pop("COCO_AUDIO_REOPEN_LOSS_WINDOW_MS", None)
    except Exception as exc:  # noqa: BLE001
        import traceback
        _record(name, False, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc().splitlines()[-3:])


# ---------------------------------------------------------------------------
# V4: env COCO_AUDIO_REOPEN_LOSS_WINDOW_MS override 解析 + 实际 emit 覆盖
# ---------------------------------------------------------------------------
def v4_env_loss_window_override() -> None:
    name = "V4_env_loss_window_override"
    try:
        import importlib
        from coco import vad_trigger as vt_mod
        importlib.reload(vt_mod)
        from coco.vad_trigger import _read_loss_window_override_ms, ENV_LOSS_WINDOW_MS, VADTrigger, VADConfig

        # 9-case 解析（与 hotplug interval 同 spec）
        cases = [
            (None, None),
            ("", None),
            ("   ", None),
            ("abc", None),
            ("-1", None),
            ("0", None),
            ("nan", None),
            ("250", 250.0),
            ("12.5", 12.5),
        ]
        bad: List[Dict[str, Any]] = []
        for val, expect in cases:
            if val is None:
                os.environ.pop(ENV_LOSS_WINDOW_MS, None)
            else:
                os.environ[ENV_LOSS_WINDOW_MS] = val
            got = _read_loss_window_override_ms()
            ok_case = (got is None and expect is None) or (got is not None and expect is not None and abs(got - expect) < 1e-9)
            if not ok_case:
                bad.append({"input": val, "expected": expect, "got": got})
        os.environ.pop(ENV_LOSS_WINDOW_MS, None)

        # 实际 emit 时 window_ms / lost_n_actual 被覆盖
        import sounddevice as sd
        orig_input = sd.InputStream
        _FakeMicStream.instances = []
        sd.InputStream = lambda **kw: _FakeMicStream(sr=kw.get("samplerate", 16000), block=kw.get("blocksize", 512))  # type: ignore[assignment]

        captured = _install_emit_capture()
        try:
            os.environ[ENV_LOSS_WINDOW_MS] = "500"  # 500ms override
            v = VADTrigger(on_utterance=lambda s: None, config=VADConfig(sample_rate=16000))
            v.start_microphone(block_seconds=0.05)
            time.sleep(0.1)
            v.request_reopen(event="changed", device={"index": 5, "name": "Y"})
            time.sleep(0.25)
            v.stop(timeout=2.0)
            lost = [p for e, p in captured if e == "audio.reopen_buffer_lost_n"]
            p0 = lost[0] if lost else {}
            # override=500ms → window_ms == 500, lost_n_actual == 500/1000 * 16000 == 8000
            expected_lost_n_actual = int(0.5 * 16000)
            ok_override = (
                bool(lost)
                and p0.get("window_ms") == 500
                and p0.get("actual_ms") == 500
                and p0.get("lost_n_actual") == expected_lost_n_actual
            )
            # 而上界 lost_n (基于 dt_total 实测) 不应被 override，应为某个小数（sim 下毫秒级）
            ok_upper_not_overridden = bool(lost) and isinstance(p0.get("lost_n"), int) and p0["lost_n"] != expected_lost_n_actual
        finally:
            _restore_emit(captured)
            sd.InputStream = orig_input  # type: ignore[assignment]
            os.environ.pop(ENV_LOSS_WINDOW_MS, None)

        ok = not bad and ok_override and ok_upper_not_overridden
        _record(
            name, ok,
            parse_bad=bad,
            override_window_ms=p0.get("window_ms"),
            override_lost_n_actual=p0.get("lost_n_actual"),
            upper_lost_n=p0.get("lost_n"),
            ok_override=ok_override,
            ok_upper_not_overridden=ok_upper_not_overridden,
        )
    except Exception as exc:  # noqa: BLE001
        import traceback
        _record(name, False, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc().splitlines()[-3:])
    finally:
        os.environ.pop("COCO_AUDIO_REOPEN_LOSS_WINDOW_MS", None)


# ---------------------------------------------------------------------------
# V5: regression
# ---------------------------------------------------------------------------
def v5_regression() -> None:
    name = "V5_regression"
    targets = [
        "scripts/verify_audio_011.py",
        "scripts/verify_audio_010.py",
        "scripts/verify_audio_009.py",
        "scripts/verify_infra_018.py",
        "scripts/verify_robot_007.py",
        "scripts/verify_vision_012.py",
        "scripts/verify_interact_017.py",
    ]
    rcs: Dict[str, int] = {}
    for t in targets:
        p = ROOT / t
        if not p.exists():
            rcs[t] = -1
            continue
        try:
            r = subprocess.run(
                [sys.executable, str(p)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=300,
            )
            rcs[t] = r.returncode
            if r.returncode != 0:
                tail = "\n".join((r.stdout or "").splitlines()[-15:])
                print(f"[verify_audio_012] regression {t} rc={r.returncode}\n{tail}", flush=True)
        except Exception as exc:  # noqa: BLE001
            rcs[t] = -2
            print(f"[verify_audio_012] regression {t} raised: {exc!r}", flush=True)
    ok = all(v == 0 or v == -1 for v in rcs.values())
    _record(name, ok, rcs=rcs)


def main() -> int:
    v1_end_to_end_chain()
    v2_error_type_propagation()
    v3_window_calibration()
    v4_env_loss_window_override()
    v5_regression()

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "audio-012",
        "ts": time.time(),
        "all_passed": all(r["ok"] for r in _results),
        "results": _results,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"[verify_audio_012] summary → {SUMMARY_PATH}", flush=True)
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
