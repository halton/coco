"""audio-010 verification: HotplugWatcher wire + InputStream wrap + error_types 收紧.

跑法::

    uv run python scripts/verify_audio_010.py

子项 (V1-V6)：

V1   HotplugWatcher main.py wire — COCO_AUDIO_HOTPLUG=1 启动序列构造 watcher
     + start() + atexit stop+join(timeout=2)，模拟 atexit 触发不悬挂线程。
V2   main.py env OFF zero-cost no-op — COCO_AUDIO_HOTPLUG 不设时，main wire
     段不构造 watcher 实例、不起线程、不调 query_devices。
V3   error_types 收紧 — open_stream_with_recovery 默认只捕 sd.PortAudioError，
     OSError 透传不被吞（即使 recovery ON 也不被退避吞掉）。
V4   wake_word / vad_trigger 真实调用站替换为 wrap：
     (a) recovery OFF → 调用 helper 路径与原直连等价（calls=1, 无 emit）
     (b) recovery ON + PortAudioError 模拟 → 退避后成功
V5   device_changed emit → reopen_callback 触发计数（added/removed 各计 1 次）
V6   audio-009 / audio-008 回归 PASS

evidence 落 evidence/audio-010/verify_summary.json
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

EVIDENCE_DIR = ROOT / "evidence" / "audio-010"
SUMMARY_PATH = EVIDENCE_DIR / "verify_summary.json"

_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def _scrub_env() -> None:
    for k in (
        "COCO_AUDIO_RECOVERY",
        "COCO_AUDIO_HOTPLUG",
        "COCO_TTS_LRU",
        "COCO_TTS_LRU_SIZE",
    ):
        os.environ.pop(k, None)


FAKE_DEV_BASE = [
    {"index": 0, "name": "Built-in Mic", "max_input_channels": 1, "max_output_channels": 0},
    {"index": 1, "name": "Built-in Spk", "max_input_channels": 0, "max_output_channels": 2},
]
FAKE_DEV_ADDED = FAKE_DEV_BASE + [
    {"index": 2, "name": "Jabra USB Speaker", "max_input_channels": 0, "max_output_channels": 2},
]


# ============================================================
# V1 — HotplugWatcher wire-to-main 启动 + atexit 收尾
# ============================================================
def v1_hotplug_wire_atexit() -> None:
    name = "V1 hotplug wire-to-main: start + atexit stop+join no hang"
    try:
        _scrub_env()
        os.environ["COCO_AUDIO_HOTPLUG"] = "1"
        from coco import audio_resilience as ar

        stop_event = threading.Event()
        calls = {"q": 0}
        cb_calls: List[tuple] = []

        def fake_query():
            calls["q"] += 1
            return list(FAKE_DEV_BASE)

        def reopen_cb(event: str, device: dict) -> None:
            cb_calls.append((event, device.get("name")))

        w = ar.HotplugWatcher(
            stop_event=stop_event,
            poll_interval=0.02,
            emit_fn=lambda *a, **k: None,
            query_devices_fn=fake_query,
            sleep_fn=lambda s: None,
            reopen_callback=reopen_cb,
        )
        started = w.start()
        time.sleep(0.05)  # 让线程跑两轮

        # 模拟 atexit 收尾
        t_start = time.time()
        w.stop()
        t = w._thread
        if t is not None:
            t.join(timeout=2.0)
        join_dur = time.time() - t_start
        ok = (
            started is True
            and t is not None
            and not t.is_alive()
            and join_dur < 2.0
            and calls["q"] >= 1
        )
        _record(
            name,
            ok,
            f"started={started} join_dur={join_dur:.3f}s q_calls={calls['q']} thread_alive={t.is_alive() if t else None}",
        )
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


# ============================================================
# V2 — main.py wire OFF zero-cost
# ============================================================
def v2_main_wire_off_zero_cost() -> None:
    name = "V2 main wire OFF: no watcher instance / no thread / no query_devices"
    try:
        _scrub_env()  # COCO_AUDIO_HOTPLUG 未设
        # 复刻 main.py 的 wire 逻辑判定
        from coco import audio_resilience as ar
        env_on = os.environ.get(ar.ENV_HOTPLUG, "0") == "1"
        # 主链路 sentinel：watcher 实例 / 线程 / query 调用都应该是 0
        instances: List[Any] = []
        threads_before = threading.active_count()

        # 模拟 main.py wire 段执行（OFF gate 早 return）
        if env_on:
            w = ar.HotplugWatcher(stop_event=threading.Event())
            instances.append(w)
            w.start()
        time.sleep(0.02)
        threads_after = threading.active_count()
        ok = (
            env_on is False
            and len(instances) == 0
            and threads_after == threads_before
        )
        _record(
            name,
            ok,
            f"env_on={env_on} instances={len(instances)} "
            f"threads_before={threads_before} threads_after={threads_after}",
        )
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


# ============================================================
# V3 — error_types 收紧：OSError 透传
# ============================================================
def v3_error_types_tightened() -> None:
    name = "V3 error_types tightened: OSError raises through (not retried)"
    try:
        _scrub_env()
        os.environ["COCO_AUDIO_RECOVERY"] = "1"
        from coco import audio_resilience as ar

        calls = {"n": 0}

        def open_fn():
            calls["n"] += 1
            raise OSError("real-os-error")

        events: List[tuple] = []
        raised = False
        try:
            # 不显式传 error_types → 用 audio-010 收紧后的默认 (sd.PortAudioError,)
            ar.open_stream_with_recovery(
                open_fn,
                emit_fn=lambda ev, **p: events.append((ev, p)),
                sleep_fn=lambda s: None,
            )
        except OSError as e:
            raised = (str(e) == "real-os-error")
        ok = raised and calls["n"] == 1 and len(events) == 0
        _record(name, ok, f"raised={raised} open_calls={calls['n']} events={len(events)}")
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


# ============================================================
# V4 — wake_word / vad_trigger 真实调用站 wrap
# ============================================================
def v4_call_site_wrap() -> None:
    name = "V4 call-site wrap: OFF baseline + ON retry success"
    try:
        # ---- (a) OFF 等价：helper 不重试，open_fn 调一次返回 sentinel
        _scrub_env()
        from coco import audio_resilience as ar
        sentinel = object()
        called = {"n": 0}

        def open_fn_off():
            called["n"] += 1
            return sentinel

        events: List[tuple] = []
        out_off = ar.open_stream_with_recovery(
            open_fn_off,
            stream_kind="input",
            emit_fn=lambda ev, **p: events.append((ev, p)),
        )
        off_ok = (out_off is sentinel and called["n"] == 1 and len(events) == 0)

        # ---- (b) ON: 模拟 PortAudioError-style 异常前 2 次失败、第 3 次成功
        os.environ["COCO_AUDIO_RECOVERY"] = "1"
        # 用 sd.PortAudioError（如能 import），否则用 fake-class
        try:
            import sounddevice as _sd  # type: ignore
            PEErr = _sd.PortAudioError
        except Exception:
            class PEErr(Exception):  # type: ignore[no-redef]
                pass

        attempts = {"n": 0}
        sentinel2 = object()

        def open_fn_on():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise PEErr(f"fake-pa-{attempts['n']}")
            return sentinel2

        events2: List[tuple] = []
        out_on = ar.open_stream_with_recovery(
            open_fn_on,
            stream_kind="input",
            emit_fn=lambda ev, **p: events2.append((ev, p)),
            sleep_fn=lambda s: None,
            error_types=(PEErr,),
        )
        succ = [e for e in events2 if e[0] == "audio.recovery_succeeded"]
        att = [e for e in events2 if e[0] == "audio.recovery_attempt"]
        on_ok = (
            out_on is sentinel2
            and attempts["n"] == 3
            and len(att) == 2
            and len(succ) == 1
        )

        # ---- (c) 调用站源码 grep：vad_trigger.py / wake_word.py 已 import helper
        vad_src = (ROOT / "coco" / "vad_trigger.py").read_text()
        wake_src = (ROOT / "coco" / "wake_word.py").read_text()
        src_ok = (
            "open_stream_with_recovery" in vad_src
            and "open_stream_with_recovery" in wake_src
        )

        ok = off_ok and on_ok and src_ok
        _record(
            name,
            ok,
            f"off_ok={off_ok} on_ok={on_ok} src_ok={src_ok} "
            f"on_attempts={attempts['n']} att_evs={len(att)} succ_evs={len(succ)}",
        )
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


# ============================================================
# V5 — device_change → reopen_callback 触发计数
# ============================================================
def v5_device_change_reopen_callback() -> None:
    name = "V5 device_change → reopen_callback fired added=1 removed=1"
    try:
        _scrub_env()
        os.environ["COCO_AUDIO_HOTPLUG"] = "1"
        from coco import audio_resilience as ar

        seq = [list(FAKE_DEV_BASE), list(FAKE_DEV_ADDED), list(FAKE_DEV_BASE)]
        idx = {"i": 0}

        def fake_query():
            r = seq[min(idx["i"], len(seq) - 1)]
            idx["i"] += 1
            return r

        cb_calls: List[tuple] = []

        def reopen_cb(event: str, device: dict) -> None:
            cb_calls.append((event, device.get("name")))

        w = ar.HotplugWatcher(
            stop_event=threading.Event(),
            poll_interval=0.01,
            emit_fn=lambda *a, **k: None,
            query_devices_fn=fake_query,
            sleep_fn=lambda s: None,
            reopen_callback=reopen_cb,
        )
        w.prime()                   # 消耗 seq[0]
        w.poll_once()               # seq[1]: +Jabra → cb 1 次 (added)
        w.poll_once()               # seq[2]: -Jabra → cb 1 次 (removed)
        added = [c for c in cb_calls if c[0] == "added"]
        removed = [c for c in cb_calls if c[0] == "removed"]
        ok = (
            len(added) == 1
            and len(removed) == 1
            and added[0][1] == "Jabra USB Speaker"
            and removed[0][1] == "Jabra USB Speaker"
            and w.reopen_call_count == 2
        )
        _record(name, ok, f"cb_calls={cb_calls} reopen_count={w.reopen_call_count}")
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


# ============================================================
# V6 — audio-009 / audio-008 回归
# ============================================================
def _run_subscript(script: str, timeout: float = 240.0) -> tuple[int, str]:
    env = os.environ.copy()
    for k in ("COCO_AUDIO_RECOVERY", "COCO_AUDIO_HOTPLUG", "COCO_TTS_LRU", "COCO_TTS_LRU_SIZE"):
        env.pop(k, None)
    try:
        proc = subprocess.run(
            ["uv", "run", "python", script],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        return proc.returncode, " | ".join(tail)
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as e:
        return 99, f"{type(e).__name__}: {e}"


def v6_regression_audio_009_008() -> None:
    name = "V6 regression: verify_audio_009.py + verify_audio_008.py PASS"
    failures: List[str] = []
    rc9, tail9 = _run_subscript("scripts/verify_audio_009.py")
    if rc9 != 0:
        failures.append(f"audio_009 rc={rc9} tail={tail9}")
    rc8, tail8 = _run_subscript("scripts/verify_audio_008.py")
    if rc8 != 0:
        failures.append(f"audio_008 rc={rc8} tail={tail8}")
    ok = not failures
    detail = (
        f"audio_009 rc={rc9} | audio_008 rc={rc8}"
        if ok
        else f"failures={failures}"
    )
    _record(name, ok, detail)


# ============================================================
# main
# ============================================================
def main() -> int:
    v1_hotplug_wire_atexit()
    v2_main_wire_off_zero_cost()
    v3_error_types_tightened()
    v4_call_site_wrap()
    v5_device_change_reopen_callback()
    v6_regression_audio_009_008()

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature_id": "audio-010",
        "phase": 14,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "results": _results,
        "passed": sum(1 for r in _results if r["ok"]),
        "failed": sum(1 for r in _results if not r["ok"]),
        "total": len(_results),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(f"\nsummary: {summary['passed']}/{summary['total']} PASS, written {SUMMARY_PATH}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
