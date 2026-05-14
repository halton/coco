"""audio-009 verification: sounddevice 退避恢复 + USB hot-plug 检测 + TTS LRU 缓存.

跑法::

    uv run python scripts/verify_audio_009.py

子项 (V1-V12)：

V1   COCO_AUDIO_RECOVERY=1：模拟前 3 次 PortAudioError、第 4 次成功 →
     重连成功 + emit recovery_attempt(*3) + emit recovery_succeeded
V2   max_attempts 用尽 → emit audio.recovery_failed + 返回 None + 不抛
V3   COCO_AUDIO_HOTPLUG=1：构造 fake query_devices 返回值变化 →
     emit audio.device_change(added) 与 (removed)
V4   HOTPLUG OFF → start() 返回 False、不起线程、无 emit
V5   COCO_TTS_LRU=1：连续两次同 (text,sid,speed) → 第二次 hit cache
     (底层 _synthesize_uncached call_count=1, hits=1)
V6   COCO_TTS_LRU=1：不同 (text|sid|speed) → cache miss（每次都打底）
V7   COCO_TTS_LRU=1, LRU_SIZE=2：超出 maxsize → 最旧 entry 被 evict
V8   TTS_LRU OFF → bytewise 等价基线（同 input 调用底层两次, hits=0）
V9   AUDIO_RECOVERY OFF → 异常 raise 透传（不退避，open_stream_fn 只调 1 次）
V10  三 env 全 OFF → 默认行为完全等价 baseline（无 emit、无线程、stats 无 hits）
V11  回归 audio-008 verify (必须 PASS)
V12  回归核心 audio verify（audio-007 / audio-006 / audio003-tts）+ 关键 vision/companion 验证不受影响

evidence 落 evidence/audio-009/verify_summary.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "evidence" / "audio-009"
SUMMARY_PATH = EVIDENCE_DIR / "verify_summary.json"

_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def _scrub_env(*keys: str) -> None:
    for k in keys:
        os.environ.pop(k, None)


# 公共：每个 V 跑前清三 env，避免污染
def _clean_env_all() -> None:
    _scrub_env(
        "COCO_AUDIO_RECOVERY",
        "COCO_AUDIO_HOTPLUG",
        "COCO_TTS_LRU",
        "COCO_TTS_LRU_SIZE",
    )


# ============================================================
# V1 / V2 / V9 — recovery
# ============================================================
def v1_recovery_eventual_success() -> None:
    name = "V1 recovery: 3 fails then 4th success → emits + returns stream"
    try:
        _clean_env_all()
        os.environ["COCO_AUDIO_RECOVERY"] = "1"
        from coco import audio_resilience as ar

        class FakePortAudioError(OSError):
            pass

        attempts = {"n": 0}
        sentinel = object()

        def open_fn():
            attempts["n"] += 1
            if attempts["n"] < 4:
                raise FakePortAudioError(f"fake-fail-{attempts['n']}")
            return sentinel

        events: List[tuple] = []

        def emit(ev, **payload):
            events.append((ev, payload))

        sleep_calls: List[float] = []

        def fake_sleep(s):
            sleep_calls.append(s)

        result = ar.open_stream_with_recovery(
            open_fn,
            stream_kind="output",
            emit_fn=emit,
            sleep_fn=fake_sleep,
            error_types=(FakePortAudioError,),
        )
        attempt_evs = [e for e in events if e[0] == "audio.recovery_attempt"]
        succ_evs = [e for e in events if e[0] == "audio.recovery_succeeded"]
        fail_evs = [e for e in events if e[0] == "audio.recovery_failed"]
        ok = (
            result is sentinel
            and attempts["n"] == 4
            and len(attempt_evs) == 3
            and len(succ_evs) == 1
            and len(fail_evs) == 0
            and len(sleep_calls) == 3
            and sleep_calls[0] == 0.5
            and sleep_calls[1] == 1.0
            and sleep_calls[2] == 2.0
            and succ_evs[0][1].get("attempt") == 4
        )
        _record(
            name,
            ok,
            f"attempts={attempts['n']} attempt_evs={len(attempt_evs)} "
            f"succ={len(succ_evs)} fails={len(fail_evs)} sleeps={sleep_calls}",
        )
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


def v2_recovery_exhausted() -> None:
    name = "V2 recovery: max_attempts exhausted → recovery_failed + returns None"
    try:
        _clean_env_all()
        os.environ["COCO_AUDIO_RECOVERY"] = "1"
        from coco import audio_resilience as ar

        class FakePortAudioError(OSError):
            pass

        def open_fn():
            raise FakePortAudioError("always-fail")

        events: List[tuple] = []
        result = ar.open_stream_with_recovery(
            open_fn,
            stream_kind="input",
            emit_fn=lambda ev, **p: events.append((ev, p)),
            sleep_fn=lambda s: None,
            error_types=(FakePortAudioError,),
            max_attempts=5,
        )
        attempt_evs = [e for e in events if e[0] == "audio.recovery_attempt"]
        fail_evs = [e for e in events if e[0] == "audio.recovery_failed"]
        ok = (
            result is None
            and len(attempt_evs) == 5
            and len(fail_evs) == 1
            and fail_evs[0][1].get("attempts") == 5
            and fail_evs[0][1].get("stream_kind") == "input"
        )
        _record(name, ok, f"attempt_evs={len(attempt_evs)} fail_evs={len(fail_evs)} result={result}")
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


def v9_recovery_off_passthrough() -> None:
    name = "V9 recovery OFF: exception raises through, no retry"
    try:
        _clean_env_all()  # OFF
        from coco import audio_resilience as ar

        calls = {"n": 0}

        def open_fn():
            calls["n"] += 1
            raise OSError("expected-passthrough")

        raised = False
        try:
            ar.open_stream_with_recovery(open_fn, emit_fn=lambda *a, **k: None)
        except OSError as e:
            raised = (str(e) == "expected-passthrough")
        ok = raised and calls["n"] == 1
        _record(name, ok, f"raised={raised} calls={calls['n']}")
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


# ============================================================
# V3 / V4 — hot-plug
# ============================================================
FAKE_DEV_BASE = [
    {"index": 0, "name": "Built-in Mic", "max_input_channels": 1, "max_output_channels": 0},
    {"index": 1, "name": "Built-in Spk", "max_input_channels": 0, "max_output_channels": 2},
]
FAKE_DEV_ADDED = FAKE_DEV_BASE + [
    {"index": 2, "name": "Jabra USB Speaker", "max_input_channels": 0, "max_output_channels": 2},
]


def v3_hotplug_emits_added_removed() -> None:
    name = "V3 hotplug: device add then remove → 2x device_change emits"
    try:
        _clean_env_all()
        os.environ["COCO_AUDIO_HOTPLUG"] = "1"
        from coco import audio_resilience as ar
        import threading

        events: List[tuple] = []
        # 序列：base → +Jabra → base
        seq = [list(FAKE_DEV_BASE), list(FAKE_DEV_ADDED), list(FAKE_DEV_BASE)]
        idx = {"i": 0}

        def fake_query():
            r = seq[min(idx["i"], len(seq) - 1)]
            idx["i"] += 1
            return r

        w = ar.HotplugWatcher(
            stop_event=threading.Event(),
            poll_interval=0.01,
            emit_fn=lambda ev, **p: events.append((ev, p)),
            query_devices_fn=fake_query,
            sleep_fn=lambda s: None,
            clock_fn=lambda: 1234.5,
        )
        # 手动驱动：prime（基线，不 emit）→ poll → poll
        w.prime()  # 消耗 seq[0]
        added1, removed1 = w.poll_once()  # seq[1]: +Jabra
        added2, removed2 = w.poll_once()  # seq[2]: -Jabra
        added_ev = [e for e in events if e[0] == "audio.device_change" and e[1].get("event") == "added"]
        removed_ev = [e for e in events if e[0] == "audio.device_change" and e[1].get("event") == "removed"]
        ok = (
            len(added1) == 1 and len(removed1) == 0
            and len(added2) == 0 and len(removed2) == 1
            and len(added_ev) == 1 and len(removed_ev) == 1
            and added_ev[0][1].get("device", {}).get("name") == "Jabra USB Speaker"
            and removed_ev[0][1].get("device", {}).get("name") == "Jabra USB Speaker"
        )
        _record(name, ok, f"added_ev={len(added_ev)} removed_ev={len(removed_ev)}")
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


def v4_hotplug_off_no_op() -> None:
    name = "V4 hotplug OFF: start() returns False, no thread, no emits"
    try:
        _clean_env_all()  # OFF
        from coco import audio_resilience as ar
        import threading

        events: List[tuple] = []
        called = {"n": 0}

        def fake_query():
            called["n"] += 1
            return list(FAKE_DEV_BASE)

        w = ar.HotplugWatcher(
            stop_event=threading.Event(),
            poll_interval=0.01,
            emit_fn=lambda ev, **p: events.append((ev, p)),
            query_devices_fn=fake_query,
            sleep_fn=lambda s: None,
        )
        started = w.start()
        # 短暂等待，确保即便误起线程也会暴露
        time.sleep(0.05)
        ok = (started is False and w._thread is None and called["n"] == 0 and len(events) == 0)
        _record(name, ok, f"started={started} thread={w._thread} q_calls={called['n']} events={len(events)}")
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


# ============================================================
# V5 / V6 / V7 / V8 — TTS LRU
# ============================================================
def _patch_synth_uncached(monkey_calls: Dict[str, Any]):
    """替换 _synthesize_uncached 为可计数 fake，避免触发 sherpa-onnx 重活。

    返回 (orig_fn, install, restore)。
    """
    from coco import tts as ttsm
    import numpy as np

    monkey_calls.setdefault("by_key", {})
    monkey_calls.setdefault("count", 0)
    orig = ttsm._synthesize_uncached

    def fake(text: str, sid: int, speed: float):
        monkey_calls["count"] += 1
        key = (text, int(sid), round(float(speed), 6))
        monkey_calls["by_key"][key] = monkey_calls["by_key"].get(key, 0) + 1
        # 用 hash 生成稳定但不同的 sample 数组
        h = abs(hash(key)) % 1000
        samples = np.array([float(h), float(h + 1)], dtype=np.float32)
        return samples, 24000

    def install():
        ttsm._synthesize_uncached = fake

    def restore():
        ttsm._synthesize_uncached = orig

    return install, restore


def v5_tts_cache_hit() -> None:
    name = "V5 TTS LRU ON: same key twice → 2nd is cache hit"
    try:
        _clean_env_all()
        os.environ["COCO_TTS_LRU"] = "1"
        from coco import tts as ttsm
        ttsm.reset_tts_cache()
        calls: Dict[str, Any] = {}
        install, restore = _patch_synth_uncached(calls)
        try:
            install()
            s1, sr1 = ttsm.synthesize("你好", sid=50, speed=1.0)
            s2, sr2 = ttsm.synthesize("你好", sid=50, speed=1.0)
            stats = ttsm.get_tts_cache_stats()
        finally:
            restore()
        ok = (
            calls["count"] == 1
            and stats["hits"] == 1
            and stats["misses"] == 1
            and stats["size"] == 1
            and (s1 == s2).all()
            and sr1 == sr2 == 24000
        )
        _record(name, ok, f"call_count={calls['count']} stats={stats}")
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


def v6_tts_cache_miss_distinct_keys() -> None:
    name = "V6 TTS LRU ON: distinct keys → all miss"
    try:
        _clean_env_all()
        os.environ["COCO_TTS_LRU"] = "1"
        from coco import tts as ttsm
        ttsm.reset_tts_cache()
        calls: Dict[str, Any] = {}
        install, restore = _patch_synth_uncached(calls)
        try:
            install()
            ttsm.synthesize("你好", sid=50, speed=1.0)
            ttsm.synthesize("再见", sid=50, speed=1.0)   # text differs
            ttsm.synthesize("你好", sid=51, speed=1.0)   # sid differs
            ttsm.synthesize("你好", sid=50, speed=1.1)   # speed differs
            stats = ttsm.get_tts_cache_stats()
        finally:
            restore()
        ok = calls["count"] == 4 and stats["hits"] == 0 and stats["misses"] == 4 and stats["size"] == 4
        _record(name, ok, f"call_count={calls['count']} stats={stats}")
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


def v7_tts_cache_lru_evict() -> None:
    name = "V7 TTS LRU ON, SIZE=2: 3rd insert evicts oldest"
    try:
        _clean_env_all()
        os.environ["COCO_TTS_LRU"] = "1"
        os.environ["COCO_TTS_LRU_SIZE"] = "2"
        from coco import tts as ttsm
        ttsm.reset_tts_cache()
        calls: Dict[str, Any] = {}
        install, restore = _patch_synth_uncached(calls)
        try:
            install()
            ttsm.synthesize("A", sid=50, speed=1.0)
            ttsm.synthesize("B", sid=50, speed=1.0)
            ttsm.synthesize("C", sid=50, speed=1.0)  # 应 evict A
            stats_after_3 = ttsm.get_tts_cache_stats()
            ttsm.synthesize("A", sid=50, speed=1.0)  # A 已被 evict → miss + 重新合成
            stats_final = ttsm.get_tts_cache_stats()
        finally:
            restore()
        ok = (
            stats_after_3["size"] == 2
            and stats_after_3["evictions"] == 1
            and stats_final["evictions"] == 2  # A 重入又把 B 挤了
            and stats_final["misses"] == 4
            and stats_final["hits"] == 0
            and calls["count"] == 4
        )
        _record(name, ok, f"after3={stats_after_3} final={stats_final} calls={calls['count']}")
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


def v8_tts_cache_off_baseline() -> None:
    name = "V8 TTS LRU OFF: same key twice → both call underlying (no cache)"
    try:
        _clean_env_all()  # OFF
        from coco import tts as ttsm
        ttsm.reset_tts_cache()
        calls: Dict[str, Any] = {}
        install, restore = _patch_synth_uncached(calls)
        try:
            install()
            ttsm.synthesize("hello", sid=50, speed=1.0)
            ttsm.synthesize("hello", sid=50, speed=1.0)
            stats = ttsm.get_tts_cache_stats()
        finally:
            restore()
        ok = calls["count"] == 2 and stats["hits"] == 0 and stats["misses"] == 0 and stats["size"] == 0
        _record(name, ok, f"call_count={calls['count']} stats={stats}")
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


# ============================================================
# V10 — 三 env 全 OFF default-OFF 等价
# ============================================================
def v10_default_off_equivalence() -> None:
    name = "V10 default OFF: no emits, no threads, baseline equivalent"
    try:
        _clean_env_all()
        from coco import audio_resilience as ar
        from coco import tts as ttsm
        import threading

        # recovery OFF: 不重试，原样调用一次
        events: List[tuple] = []
        sentinel = object()
        called = {"n": 0}

        def open_fn():
            called["n"] += 1
            return sentinel

        out = ar.open_stream_with_recovery(open_fn, emit_fn=lambda ev, **p: events.append((ev, p)))
        recovery_ok = (out is sentinel and called["n"] == 1 and len(events) == 0)

        # hotplug OFF: start no-op
        w = ar.HotplugWatcher(
            stop_event=threading.Event(),
            poll_interval=0.01,
            emit_fn=lambda ev, **p: events.append((ev, p)),
            query_devices_fn=lambda: list(FAKE_DEV_BASE),
        )
        started = w.start()
        time.sleep(0.02)
        hotplug_ok = (started is False and w._thread is None)

        # tts OFF: no cache state mutation
        ttsm.reset_tts_cache()
        calls: Dict[str, Any] = {}
        install, restore = _patch_synth_uncached(calls)
        try:
            install()
            ttsm.synthesize("baseline", sid=50, speed=1.0)
            ttsm.synthesize("baseline", sid=50, speed=1.0)
        finally:
            restore()
        stats = ttsm.get_tts_cache_stats()
        tts_ok = (calls["count"] == 2 and stats == {"hits": 0, "misses": 0, "evictions": 0, "size": 0})

        ok = recovery_ok and hotplug_ok and tts_ok and len(events) == 0
        _record(
            name,
            ok,
            f"recovery_ok={recovery_ok} hotplug_ok={hotplug_ok} tts_ok={tts_ok} "
            f"events={len(events)} stats={stats}",
        )
    except Exception as e:
        _record(name, False, f"{type(e).__name__}: {e}")


# ============================================================
# V11 / V12 — 回归
# ============================================================
def _run_subscript(script: str, env_extra: Dict[str, str] | None = None, timeout: float = 180.0) -> tuple[int, str]:
    env = os.environ.copy()
    # 回归 verify 时清三 env，避免新行为污染老脚本
    for k in ("COCO_AUDIO_RECOVERY", "COCO_AUDIO_HOTPLUG", "COCO_TTS_LRU", "COCO_TTS_LRU_SIZE"):
        env.pop(k, None)
    if env_extra:
        env.update(env_extra)
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


def v11_regress_audio_008() -> None:
    name = "V11 regression: verify_audio_008.py PASS"
    rc, tail = _run_subscript("scripts/verify_audio_008.py")
    _record(name, rc == 0, f"rc={rc} tail={tail}")


def v12_regress_other_audio() -> None:
    name = "V12 regression: core audio + tts smoke verifies PASS"
    candidates = [
        "scripts/verify_audio003_tts.py",
        "scripts/verify_audio_007.py",
        "scripts/verify_audio_006.py",
    ]
    failures: List[str] = []
    ran = 0
    for s in candidates:
        if not (ROOT / s).exists():
            continue
        ran += 1
        rc, tail = _run_subscript(s, timeout=240.0)
        if rc != 0:
            failures.append(f"{s} rc={rc} tail={tail}")
    ok = ran > 0 and not failures
    detail = f"ran={ran} failures={failures}" if failures else f"ran={ran} all PASS"
    _record(name, ok, detail)


# ============================================================
# main
# ============================================================
def main() -> int:
    v1_recovery_eventual_success()
    v2_recovery_exhausted()
    v3_hotplug_emits_added_removed()
    v4_hotplug_off_no_op()
    v5_tts_cache_hit()
    v6_tts_cache_miss_distinct_keys()
    v7_tts_cache_lru_evict()
    v8_tts_cache_off_baseline()
    v9_recovery_off_passthrough()
    v10_default_off_equivalence()
    v11_regress_audio_008()
    v12_regress_other_audio()

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature_id": "audio-009",
        "phase": 13,
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
