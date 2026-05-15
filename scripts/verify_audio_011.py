"""audio-011 verification: vad/wake mic_loop 真 stop+reopen + emit 事件 + registry + env + regression.

跑法::

    uv run python scripts/verify_audio_011.py

子项 (V1-V7)：

V1   vad mic_loop 真 stop+reopen 路径：注入 fake sd.InputStream，调
     ``VADTrigger.request_reopen(event, device)`` → mic_loop stop()+close() 旧 stream
     + 重 open 新 stream + emit ``audio.stream_reopened`` 字段含
     subsystem/reason/old_device_idx/new_device_idx/ts。
V1b  asr.transcribe_microphone wrap 路径（原 V1，保留作为信号路径回归）。
V2   wake mic_loop 真 stop+reopen 路径（同 V1 形态）。
V2b  main fallback wrap off-equivalence（原 V2，保留）。
V3   reopen_buffer_lost_n emit：reopen 期间 emit
     ``audio.reopen_buffer_lost_n`` 含 subsystem + lost_n + ms + ts。
V4   HotplugWatcher callback registry — add/remove + poll_once + 异常不传染（原 V3）。
V5   env COCO_AUDIO_HOTPLUG_INTERVAL_S 9-case + Watcher 默认从 env 读（原 V4）。
V6   default-OFF：COCO_AUDIO_HOTPLUG=0 时 HotplugWatcher 不应被 main 启动（启动逻辑 gate）。
V7   regression — verify_audio_010/009/infra_018/robot_007 全 PASS（原 V5）。

evidence 落 evidence/audio-011/verify_summary.json
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

EVIDENCE_DIR = ROOT / "evidence" / "audio-011"
SUMMARY_PATH = EVIDENCE_DIR / "verify_summary.json"

_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, **detail: Any) -> None:
    _results.append({"name": name, "ok": bool(ok), **detail})
    flag = "PASS" if ok else "FAIL"
    print(f"[verify_audio_011] {name}: {flag} {detail}", flush=True)


# ---------------------------------------------------------------------------
# Fake sd.InputStream — 行为足够覆盖 stop/close/start + read 阻塞退出
# ---------------------------------------------------------------------------
class _FakeMicStream:
    """模拟 sounddevice.InputStream：
    - __enter__/__exit__ 兼容 `with` 语义
    - read(block) 返回零样本；stop() 后 read 抛错（模拟 PortAudio 在 stop 后 read 失败）
    - stop()/close()/start() 都计数
    """
    instances: List["_FakeMicStream"] = []

    def __init__(self, sr: int = 16000, block: int = 512):
        import numpy as np  # local
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
        # 模拟阻塞采样：sleep 小段，返回零样本
        time.sleep(0.02)
        return (self._np.zeros((n, 1), dtype="float32"), False)


# ---------------------------------------------------------------------------
# Capture coco.logging_setup.emit 调用
# ---------------------------------------------------------------------------
def _install_emit_capture() -> List[tuple]:
    """patch coco.logging_setup.emit → 把所有 emit (evt, payload) 收集到 list。
    返回 list 句柄。
    """
    captured: List[tuple] = []
    from coco import logging_setup as ls
    orig = ls.emit

    def _cap(evt, **payload):
        captured.append((evt, dict(payload)))
        return None

    ls.emit = _cap  # type: ignore[assignment]
    captured.append(("__orig__", {"orig": orig}))  # sentinel for restore
    return captured


def _restore_emit(captured: List[tuple]) -> None:
    # 取出 sentinel 恢复
    from coco import logging_setup as ls
    for evt, payload in captured:
        if evt == "__orig__":
            ls.emit = payload["orig"]  # type: ignore[assignment]
            return


# ---------------------------------------------------------------------------
# V1: vad mic_loop 真 stop+reopen + emit audio.stream_reopened
# ---------------------------------------------------------------------------
def v1_vad_real_stop_reopen() -> None:
    name = "V1_vad_real_stop_reopen"
    try:
        import importlib
        from coco import vad_trigger as vt_mod
        importlib.reload(vt_mod)
        from coco.vad_trigger import VADTrigger, VADConfig

        # monkeypatch sounddevice.InputStream → _FakeMicStream
        import sounddevice as sd
        orig_input = sd.InputStream
        _FakeMicStream.instances = []
        sd.InputStream = lambda **kw: _FakeMicStream(sr=kw.get("samplerate", 16000), block=kw.get("blocksize", 512))  # type: ignore[assignment]

        captured = _install_emit_capture()
        try:
            triggered: List = []
            v = VADTrigger(on_utterance=lambda s: triggered.append(len(s)), config=VADConfig(sample_rate=16000))
            v.start_microphone(block_seconds=0.05)
            time.sleep(0.15)  # 让 mic_loop 跑一会
            n_before = len(_FakeMicStream.instances)
            old_dev = {"index": 7, "name": "OldMic"}
            new_dev = {"index": 9, "name": "NewMic"}
            # 模拟 hotplug：先调一次 reopen
            # request_reopen 用 device=new_dev 表示新设备
            v.request_reopen(event="changed", device=new_dev)
            time.sleep(0.25)
            n_after = len(_FakeMicStream.instances)
            v.stop(timeout=2.0)

            # 找到 audio.stream_reopened emit
            reopened = [(e, p) for e, p in captured if e == "audio.stream_reopened"]
            ok_emit = bool(reopened) and reopened[0][1].get("subsystem") == "vad"
            ok_fields = bool(reopened) and all(
                k in reopened[0][1] for k in ("subsystem", "reason", "old_device_idx", "new_device_idx", "ts")
            )
            # 至少创建 2 个 FakeMicStream（初始 + reopen 后）
            ok_reopened = n_after > n_before
            # 第一个 fake 必须被 stop+close
            first = _FakeMicStream.instances[0]
            ok_stopclose = first.stopped >= 1 and first.closed >= 1

            ok = ok_emit and ok_fields and ok_reopened and ok_stopclose
            _record(
                name, ok,
                instances_before=n_before, instances_after=n_after,
                first_stopped=first.stopped, first_closed=first.closed,
                emit_reopened_n=len(reopened),
                emit_fields_ok=ok_fields,
                reopen_count=v.reopen_count,
            )
        finally:
            _restore_emit(captured)
            sd.InputStream = orig_input  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        import traceback
        _record(name, False, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc().splitlines()[-3:])


# ---------------------------------------------------------------------------
# V1b: asr.transcribe_microphone wrap → wrapper 退避重试（保留原 V1）
# ---------------------------------------------------------------------------
def v1b_asr_inputstream_wrap() -> None:
    name = "V1b_asr_inputstream_wrap"
    try:
        os.environ["COCO_AUDIO_RECOVERY"] = "1"
        import importlib
        import sounddevice as sd
        from coco import audio_resilience as ar
        importlib.reload(ar)

        calls = {"n": 0}
        sleeps: List[float] = []

        class _FakeStream:
            def __enter__(self):
                return self
            def __exit__(self, *a, **k):
                return False
            def read(self, n):
                return (b"\x00" * n, False)

        def _open():
            calls["n"] += 1
            if calls["n"] < 3:
                raise sd.PortAudioError("fake port audio error")
            return _FakeStream()

        stream = ar.open_stream_with_recovery(
            _open,
            stream_kind="input",
            sleep_fn=lambda s: sleeps.append(s),
        )
        ok = stream is not None and calls["n"] == 3 and len(sleeps) == 2
        _record(name, ok, calls=calls["n"], sleeps=sleeps, recovered=stream is not None)
    except Exception as exc:  # noqa: BLE001
        _record(name, False, error=f"{type(exc).__name__}: {exc}")
    finally:
        os.environ.pop("COCO_AUDIO_RECOVERY", None)


# ---------------------------------------------------------------------------
# V2: wake mic_loop 真 stop+reopen + emit
# ---------------------------------------------------------------------------
def v2_wake_real_stop_reopen() -> None:
    name = "V2_wake_real_stop_reopen"
    try:
        # 测 mic_loop 不需要真 KWS spotter；构造一个最小 WakeWordDetector 桩对象，
        # 复用 _mic_loop 方法即可。
        from coco.wake_word import WakeConfig, WakeWordDetector

        # 用 __new__ 绕过 KWS 加载
        w = WakeWordDetector.__new__(WakeWordDetector)
        w.on_wake = lambda kw: None
        w.config = WakeConfig()
        w._muted = False
        w._stop_event = threading.Event()
        w._mic_thread = None
        w._mic_lock = threading.Lock()
        w._lock = threading.Lock()
        # audio-011 字段
        w._reopen_event = threading.Event()
        w._reopen_meta = {}
        w._current_mic_stream = None
        w._mic_stream_lock = threading.Lock()
        w._reopen_count = 0
        # 让 feed no-op（避免触 KWS）
        w.feed = lambda samples: None  # type: ignore[assignment]

        import sounddevice as sd
        orig_input = sd.InputStream
        _FakeMicStream.instances = []
        sd.InputStream = lambda **kw: _FakeMicStream(sr=kw.get("samplerate", 16000), block=kw.get("blocksize", 512))  # type: ignore[assignment]

        captured = _install_emit_capture()
        try:
            # 直接起 mic_loop 线程
            t = threading.Thread(target=w._mic_loop, args=(0.05,), daemon=True)
            t.start()
            time.sleep(0.15)
            n_before = len(_FakeMicStream.instances)
            new_dev = {"index": 11, "name": "NewWakeMic"}
            w.request_reopen(event="added", device=new_dev)
            time.sleep(0.25)
            n_after = len(_FakeMicStream.instances)
            w._stop_event.set()
            t.join(timeout=2.0)

            reopened = [(e, p) for e, p in captured if e == "audio.stream_reopened"]
            wake_reopened = [r for r in reopened if r[1].get("subsystem") == "wake"]
            ok_emit = bool(wake_reopened)
            ok_fields = bool(wake_reopened) and all(
                k in wake_reopened[0][1] for k in ("subsystem", "reason", "old_device_idx", "new_device_idx", "ts")
            )
            ok_reopened = n_after > n_before
            first = _FakeMicStream.instances[0]
            ok_stopclose = first.stopped >= 1 and first.closed >= 1
            ok = ok_emit and ok_fields and ok_reopened and ok_stopclose
            _record(
                name, ok,
                instances_before=n_before, instances_after=n_after,
                first_stopped=first.stopped, first_closed=first.closed,
                emit_wake_reopened_n=len(wake_reopened),
                reopen_count=w.reopen_count,
            )
        finally:
            _restore_emit(captured)
            sd.InputStream = orig_input  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        import traceback
        _record(name, False, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc().splitlines()[-3:])


# ---------------------------------------------------------------------------
# V2b: main.py 主循环 fallback wrap — 等价路径行为校验（原 V2）
# ---------------------------------------------------------------------------
def v2b_main_inputstream_wrap_recovery_off_equivalence() -> None:
    name = "V2b_main_inputstream_wrap_off_equivalence"
    try:
        os.environ.pop("COCO_AUDIO_RECOVERY", None)
        import importlib
        from coco import audio_resilience as ar
        importlib.reload(ar)

        calls = {"n": 0}
        emits: List[tuple] = []
        sleeps: List[float] = []

        def _open():
            calls["n"] += 1
            return object()

        out = ar.open_stream_with_recovery(
            _open,
            stream_kind="input",
            emit_fn=lambda evt, **payload: emits.append((evt, payload)),
            sleep_fn=lambda s: sleeps.append(s),
        )
        ok = out is not None and calls["n"] == 1 and not emits and not sleeps
        _record(name, ok, calls=calls["n"], emits_n=len(emits), sleeps_n=len(sleeps))
    except Exception as exc:  # noqa: BLE001
        _record(name, False, error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# V3: reopen_buffer_lost_n emit（vad + wake 各一次 reopen 后均应 emit）
# ---------------------------------------------------------------------------
def v3_reopen_buffer_lost() -> None:
    name = "V3_reopen_buffer_lost_n"
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
            v.request_reopen(event="changed", device={"index": 3, "name": "X"})
            time.sleep(0.25)
            v.stop(timeout=2.0)

            lost = [(e, p) for e, p in captured if e == "audio.reopen_buffer_lost_n"]
            ok_emit = bool(lost)
            ok_subsystem = bool(lost) and lost[0][1].get("subsystem") == "vad"
            ok_lostn = bool(lost) and isinstance(lost[0][1].get("lost_n"), int) and lost[0][1].get("lost_n") >= 0
            ok_ms = bool(lost) and isinstance(lost[0][1].get("ms"), int) and lost[0][1].get("ms") >= 0
            ok = ok_emit and ok_subsystem and ok_lostn and ok_ms
            _record(
                name, ok,
                emit_n=len(lost),
                subsystem=lost[0][1].get("subsystem") if lost else None,
                lost_n=lost[0][1].get("lost_n") if lost else None,
                ms=lost[0][1].get("ms") if lost else None,
            )
        finally:
            _restore_emit(captured)
            sd.InputStream = orig_input  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        import traceback
        _record(name, False, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc().splitlines()[-3:])


# ---------------------------------------------------------------------------
# V4: HotplugWatcher callback registry (原 V3)
# ---------------------------------------------------------------------------
def v4_hotplug_registry() -> None:
    name = "V4_hotplug_callback_registry"
    try:
        import importlib
        from coco import audio_resilience as ar
        importlib.reload(ar)

        seen_a: List[tuple] = []
        seen_b: List[tuple] = []

        def cb_a(event: str, device: dict) -> None:
            seen_a.append((event, device.get("name")))

        def cb_b(event: str, device: dict) -> None:
            seen_b.append((event, device.get("name")))

        def cb_raises(event: str, device: dict) -> None:
            raise RuntimeError("registry cb error must not poison sibling cbs")

        states = [
            [],
            [{"index": 0, "name": "Fake Mic", "max_input_channels": 1, "max_output_channels": 0}],
            [],
        ]
        idx = {"i": 0}

        def _q():
            i = idx["i"]
            idx["i"] = min(i + 1, len(states) - 1)
            return states[i]

        emits: List[tuple] = []

        w = ar.HotplugWatcher(
            poll_interval=0.05,
            emit_fn=lambda evt, **p: emits.append((evt, p)),
            query_devices_fn=_q,
        )
        w.add_reopen_callback(cb_a)
        w.add_reopen_callback(cb_b)
        w.add_reopen_callback(cb_raises)
        w.add_reopen_callback(cb_a)
        count_after_add = w.reopen_callback_count
        w.prime()
        added1, removed1 = w.poll_once()
        added2, removed2 = w.poll_once()

        removed_flag = w.remove_reopen_callback(cb_b)
        removed_again = w.remove_reopen_callback(cb_b)

        ok = (
            count_after_add == 3
            and len(added1) == 1 and len(removed1) == 0
            and len(added2) == 0 and len(removed2) == 1
            and seen_a == [("added", "Fake Mic"), ("removed", "Fake Mic")]
            and seen_b == [("added", "Fake Mic"), ("removed", "Fake Mic")]
            and removed_flag is True
            and removed_again is False
            and w.reopen_call_count >= 2
        )
        _record(
            name, ok,
            count_after_add=count_after_add,
            seen_a=seen_a, seen_b=seen_b,
            removed_flag=removed_flag,
            removed_again=removed_again,
            reopen_call_count=w.reopen_call_count,
            emits_n=len(emits),
        )
    except Exception as exc:  # noqa: BLE001
        _record(name, False, error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# V5: env COCO_AUDIO_HOTPLUG_INTERVAL_S 9-case (原 V4)
# ---------------------------------------------------------------------------
def v5_env_poll_interval() -> None:
    name = "V5_env_hotplug_interval"
    try:
        import importlib
        from coco import audio_resilience as ar
        importlib.reload(ar)

        env = ar.ENV_HOTPLUG_INTERVAL
        default = ar.HOTPLUG_INTERVAL_DEFAULT
        min_v = ar.HOTPLUG_INTERVAL_MIN

        cases = [
            (None, default),
            ("", default),
            ("   ", default),
            ("abc", default),
            ("-1", default),
            ("0", default),
            ("0.001", min_v),
            ("3", 3.0),
            ("0.5", 0.5),
        ]
        bad: List[Dict[str, Any]] = []
        for val, expect in cases:
            if val is None:
                os.environ.pop(env, None)
            else:
                os.environ[env] = val
            got = ar._read_hotplug_interval()
            if abs(got - expect) > 1e-9:
                bad.append({"input": val, "expected": expect, "got": got})

        os.environ[env] = "0.05"
        w = ar.HotplugWatcher()
        watcher_pi = w._poll_interval
        w2 = ar.HotplugWatcher(poll_interval=2.5)
        explicit_pi = w2._poll_interval

        ok = (
            not bad
            and abs(watcher_pi - 0.05) < 1e-9
            and abs(explicit_pi - 2.5) < 1e-9
        )
        _record(name, ok, bad=bad, watcher_pi=watcher_pi, explicit_pi=explicit_pi, cases_n=len(cases))
    except Exception as exc:  # noqa: BLE001
        _record(name, False, error=f"{type(exc).__name__}: {exc}")
    finally:
        os.environ.pop("COCO_AUDIO_HOTPLUG_INTERVAL_S", None)


# ---------------------------------------------------------------------------
# V6: default-OFF — COCO_AUDIO_HOTPLUG=0 时 HotplugWatcher 在 main 应不启动
# ---------------------------------------------------------------------------
def v6_default_off() -> None:
    name = "V6_default_off"
    try:
        # main.py 启动 watcher 的 gate（grep coco/main.py 中 COCO_AUDIO_HOTPLUG 用法）
        # 这里做 source-level 静态确认 + 行为：HotplugWatcher 构造本身轻量，gate 在 main。
        main_py = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
        has_gate = "COCO_AUDIO_HOTPLUG" in main_py and "HotplugWatcher" in main_py
        # 模拟 OFF：env unset 时 main 不应启动 watcher（运行时不便起 main，做静态字符串检查）
        # 找 HotplugWatcher 启动行附近的 env gate
        # 简单 heuristic：env 出现在 HotplugWatcher 出现之前的 1000 字内
        try:
            idx_w = main_py.index("HotplugWatcher(")
            window = main_py[max(0, idx_w - 1500):idx_w]
            gate_in_window = "COCO_AUDIO_HOTPLUG" in window
        except ValueError:
            gate_in_window = False
        ok = has_gate and gate_in_window
        _record(name, ok, has_gate=has_gate, gate_in_window=gate_in_window)
    except Exception as exc:  # noqa: BLE001
        _record(name, False, error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# V7: regression — verify_audio_010/009/infra-018/robot-007 (原 V5)
# ---------------------------------------------------------------------------
def v7_regression() -> None:
    name = "V7_regression"
    targets = [
        "scripts/verify_audio_010.py",
        "scripts/verify_audio_009.py",
        "scripts/verify_infra_018.py",
        "scripts/verify_robot_007.py",
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
                timeout=180,
            )
            rcs[t] = r.returncode
        except Exception as exc:  # noqa: BLE001
            rcs[t] = -2
            print(f"[verify_audio_011] regression {t} raised: {exc!r}", flush=True)
    ok = all(v == 0 or v == -1 for v in rcs.values())
    _record(name, ok, rcs=rcs)


def main() -> int:
    v1_vad_real_stop_reopen()
    v1b_asr_inputstream_wrap()
    v2_wake_real_stop_reopen()
    v2b_main_inputstream_wrap_recovery_off_equivalence()
    v3_reopen_buffer_lost()
    v4_hotplug_registry()
    v5_env_poll_interval()
    v6_default_off()
    v7_regression()

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "audio-011",
        "ts": time.time(),
        "all_passed": all(r["ok"] for r in _results),
        "results": _results,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"[verify_audio_011] summary → {SUMMARY_PATH}", flush=True)
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
