"""scripts/verify_interact_014_fix_busy_lock_leak.py — _busy lock leak 修复验证.

V0  fingerprint sha256 锁面 coco/interact.py
V1  import InteractSession + 新 helper _check_lock_watchdog / _release_busy_safe
V2  正常 acquire→finally release 路径：lock 被释放 → 下次 acquire 成功
V3  acquire 后中间抛异常 → finally 仍释放 → 下次 acquire 成功
V4  历史泄漏 (lock 被锁住 + acquired_ts=time-31s) → _check_lock_watchdog 强释放
    + 下次 acquire 成功 + emit interact.lock_recovered
V5  持锁但未超时 (time-5s) → watchdog 不干预
V6  env COCO_INTERACT_LOCK_TIMEOUT_S=10 覆盖默认 30 → 12s 触发 / 8s 不触发
V7  acquire 失败 (lock 已被合法持有 < timeout) 不影响 watchdog 状态
V8  double release (释放已释放 lock) 不抛 RuntimeError

跑法：``./.venv/bin/python scripts/verify_interact_014_fix_busy_lock_leak.py``
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 清掉 env 避免泄漏到不该有覆盖的子项
os.environ.pop("COCO_INTERACT_LOCK_TIMEOUT_S", None)


import numpy as np


# ---------------------------------------------------------------------------
# Stub deps
# ---------------------------------------------------------------------------


class FakeRobot:
    def head_to(self, *a, **k): pass
    def goto_sleep(self, *a, **k): pass
    def goto_zero(self, *a, **k): pass


class FakeTTS:
    def __init__(self) -> None:
        self.spoken: List[str] = []

    def say(self, text: str, *, blocking: bool = True, **kw) -> None:
        self.spoken.append(text)


def _fake_asr_ok(_audio: np.ndarray, _sr: int) -> str:
    return "你好"


def _fake_asr_raises(_audio: np.ndarray, _sr: int) -> str:
    raise RuntimeError("simulated asr crash inside handle_audio mid-path")


def _silence_audio() -> tuple[np.ndarray, int]:
    return np.zeros(16000, dtype=np.int16), 16000


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "", **extra: Any) -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail, **extra})
    line = "PASS" if ok else "FAIL"
    print(f"[{line}] {name}: {detail}")


# ---------------------------------------------------------------------------
# V0 fingerprint sha256
# ---------------------------------------------------------------------------


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def v0_fingerprint() -> None:
    p = ROOT / "coco" / "interact.py"
    if not p.exists():
        _record("V0_fingerprint_sha256", False, f"missing {p}")
        return
    digest = _sha256(p)
    src = p.read_text(encoding="utf-8")
    # 关键锚点：interact-014 修法关键字面量
    anchors = [
        "_check_lock_watchdog",
        "_release_busy_safe",
        "_busy_acquired_ts",
        "_DEFAULT_LOCK_TIMEOUT_S",
        "COCO_INTERACT_LOCK_TIMEOUT_S",
        "interact.lock_recovered",
    ]
    missing = [a for a in anchors if a not in src]
    ok = not missing
    _record(
        "V0_fingerprint_sha256",
        ok,
        f"sha256={digest[:12]} anchors_missing={missing}",
    )


# ---------------------------------------------------------------------------
# V1 import + helper presence
# ---------------------------------------------------------------------------


def v1_imports() -> None:
    try:
        from coco.interact import (
            InteractSession,
            _DEFAULT_LOCK_TIMEOUT_S,
            _resolve_lock_timeout_s,
        )
    except Exception as e:  # noqa: BLE001
        _record("V1_imports", False, f"import failed: {type(e).__name__}: {e}")
        return
    has_watchdog = hasattr(InteractSession, "_check_lock_watchdog")
    has_release_safe = hasattr(InteractSession, "_release_busy_safe")
    default_ok = _DEFAULT_LOCK_TIMEOUT_S == 30.0
    resolved_ok = _resolve_lock_timeout_s() == 30.0
    ok = has_watchdog and has_release_safe and default_ok and resolved_ok
    _record(
        "V1_imports",
        ok,
        f"watchdog={has_watchdog} release_safe={has_release_safe} "
        f"default={_DEFAULT_LOCK_TIMEOUT_S} resolved_default={_resolve_lock_timeout_s()}",
    )


# ---------------------------------------------------------------------------
# Helpers for V2-V8
# ---------------------------------------------------------------------------


def _make_session(asr_fn=_fake_asr_ok):
    from coco.interact import InteractSession
    return InteractSession(
        robot=FakeRobot(),
        asr_fn=asr_fn,
        tts_say_fn=FakeTTS().say,
    )


# ---------------------------------------------------------------------------
# V2 normal acquire→release path
# ---------------------------------------------------------------------------


def v2_normal_release() -> None:
    sess = _make_session(_fake_asr_ok)
    audio, sr = _silence_audio()
    r1 = sess.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
    assert not r1.get("dropped"), f"first should not drop: {r1}"
    locked_after_first = sess._busy.locked()
    ts_after_first = sess._busy_acquired_ts
    # 再 acquire 一次，证明已释放
    r2 = sess.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
    ok = (
        not locked_after_first
        and ts_after_first is None
        and not r2.get("dropped")
    )
    _record(
        "V2_normal_release",
        ok,
        f"locked_after={locked_after_first} ts_after={ts_after_first} "
        f"r2.dropped={r2.get('dropped')}",
    )


# ---------------------------------------------------------------------------
# V3 mid-path exception → finally still releases
# ---------------------------------------------------------------------------


def v3_exception_release() -> None:
    # asr_fn 抛异常 — 当前 handle_audio 把 ASR 异常吞掉只记 stats，不会跳过
    # finally。我们用 monkey-patch idle_animator.pause 抛异常更激进地模拟
    # mid-path 异常路径（pause 在 try 块开头被调用）。
    sess = _make_session(_fake_asr_ok)
    class CrashIdle:
        def pause(self):
            raise RuntimeError("simulated mid-path crash inside try block")
        def resume(self):
            pass
        def set_current_emotion(self, *a, **k):
            pass
    sess.idle_animator = CrashIdle()  # type: ignore[assignment]
    audio, sr = _silence_audio()
    raised = False
    try:
        sess.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
    except RuntimeError:
        raised = True  # handle_audio 把异常 propagate 是可接受的
    locked_after = sess._busy.locked()
    ts_after = sess._busy_acquired_ts
    # 关键断言：无论 handle_audio 是否 raise，lock 必须释放
    ok = (not locked_after) and (ts_after is None)
    _record(
        "V3_exception_release",
        ok,
        f"raised={raised} locked_after={locked_after} ts_after={ts_after}",
    )


# ---------------------------------------------------------------------------
# V4 historical leak → watchdog forces release
# ---------------------------------------------------------------------------


def v4_watchdog_recovers_leak() -> None:
    from coco.interact import InteractSession
    # 监听 emit
    events: List[Dict[str, Any]] = []
    import coco.logging_setup as logging_setup
    orig_emit = logging_setup.emit
    def capture(name: str, **fields):
        events.append({"name": name, **fields})
        # 仍走原逻辑（保持 logging 副作用一致）
        try:
            return orig_emit(name, **fields)
        except Exception:
            pass
    logging_setup.emit = capture  # type: ignore[assignment]
    try:
        sess = _make_session(_fake_asr_ok)
        # 模拟历史泄漏：手工 acquire + 设 ts=now-31s
        sess._busy.acquire(blocking=False)
        sess._busy_acquired_ts = time.time() - 31.0
        # 调 watchdog —— 应强释放
        triggered = sess._check_lock_watchdog()
        locked_after = sess._busy.locked()
        ts_after = sess._busy_acquired_ts
        # 下次 acquire 应成功（通过 handle_audio 触发）
        audio, sr = _silence_audio()
        # 注意 handle_audio 内部又会跑一次 watchdog（此时已 clean），然后正常 acquire
        r = sess.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
        # 检查 lock_recovered 事件
        recovered_events = [e for e in events if e["name"] == "interact.lock_recovered"]
        ok = (
            triggered
            and not locked_after
            and ts_after is None
            and not r.get("dropped")
            and len(recovered_events) >= 1
        )
        detail_evt = recovered_events[0] if recovered_events else None
        _record(
            "V4_watchdog_recovers_leak",
            ok,
            f"triggered={triggered} locked_after={locked_after} "
            f"r.dropped={r.get('dropped')} "
            f"recovered_events={len(recovered_events)} first={detail_evt}",
        )
    finally:
        logging_setup.emit = orig_emit  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# V5 holding lock but not yet timeout — watchdog ignores
# ---------------------------------------------------------------------------


def v5_watchdog_skips_within_timeout() -> None:
    sess = _make_session(_fake_asr_ok)
    sess._busy.acquire(blocking=False)
    sess._busy_acquired_ts = time.time() - 5.0
    triggered = sess._check_lock_watchdog()
    still_locked = sess._busy.locked()
    ts_unchanged = sess._busy_acquired_ts is not None
    # cleanup
    try:
        sess._busy.release()
    except RuntimeError:
        pass
    sess._busy_acquired_ts = None
    ok = (not triggered) and still_locked and ts_unchanged
    _record(
        "V5_watchdog_skips_within_timeout",
        ok,
        f"triggered={triggered} still_locked={still_locked} ts_unchanged={ts_unchanged}",
    )


# ---------------------------------------------------------------------------
# V6 env override
# ---------------------------------------------------------------------------


def v6_env_override() -> None:
    from coco.interact import _resolve_lock_timeout_s
    os.environ["COCO_INTERACT_LOCK_TIMEOUT_S"] = "10"
    try:
        resolved = _resolve_lock_timeout_s()
        sess = _make_session(_fake_asr_ok)
        # 12s 持锁应触发（>10s）
        sess._busy.acquire(blocking=False)
        sess._busy_acquired_ts = time.time() - 12.0
        t1 = sess._check_lock_watchdog()
        # cleanup state
        try:
            sess._busy.release()
        except RuntimeError:
            pass
        sess._busy_acquired_ts = None
        # 8s 持锁不应触发（<10s）
        sess._busy.acquire(blocking=False)
        sess._busy_acquired_ts = time.time() - 8.0
        t2 = sess._check_lock_watchdog()
        # cleanup
        try:
            sess._busy.release()
        except RuntimeError:
            pass
        sess._busy_acquired_ts = None
        ok = resolved == 10.0 and t1 and not t2
        _record(
            "V6_env_override",
            ok,
            f"resolved={resolved} 12s_triggered={t1} 8s_triggered={t2}",
        )
    finally:
        os.environ.pop("COCO_INTERACT_LOCK_TIMEOUT_S", None)


# ---------------------------------------------------------------------------
# V7 acquire fail (concurrent legitimate hold) does not corrupt watchdog state
# ---------------------------------------------------------------------------


def v7_concurrent_acquire_fail() -> None:
    sess = _make_session(_fake_asr_ok)
    audio, sr = _silence_audio()
    # 主线程先 acquire（模拟一个正在跑的 turn），ts 设当前
    assert sess._busy.acquire(blocking=False)
    sess._busy_acquired_ts = time.time()
    saved_ts = sess._busy_acquired_ts
    # 另一个 thread 调 handle_audio → 应 dropped 但不能改 ts，不能强释放
    container: Dict[str, Any] = {}
    def worker():
        container["r"] = sess.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=5.0)
    r = container.get("r", {})
    # 状态检查
    still_locked = sess._busy.locked()
    ts_unchanged = sess._busy_acquired_ts == saved_ts
    # cleanup
    try:
        sess._busy.release()
    except RuntimeError:
        pass
    sess._busy_acquired_ts = None
    ok = r.get("dropped") is True and still_locked and ts_unchanged
    _record(
        "V7_concurrent_acquire_fail",
        ok,
        f"r.dropped={r.get('dropped')} still_locked={still_locked} ts_unchanged={ts_unchanged}",
    )


# ---------------------------------------------------------------------------
# V8 double release does not raise
# ---------------------------------------------------------------------------


def v8_double_release_safe() -> None:
    sess = _make_session(_fake_asr_ok)
    sess._busy.acquire(blocking=False)
    sess._busy_acquired_ts = time.time()
    # 第一次 release（合法）
    sess._release_busy_safe()
    raised = None
    # 第二次 release（已释放）应静默
    try:
        sess._release_busy_safe()
    except Exception as e:  # noqa: BLE001
        raised = e
    # 第三次再调一次也不抛
    try:
        sess._release_busy_safe()
    except Exception as e:  # noqa: BLE001
        raised = raised or e
    final_locked = sess._busy.locked()
    final_ts = sess._busy_acquired_ts
    ok = raised is None and not final_locked and final_ts is None
    _record(
        "V8_double_release_safe",
        ok,
        f"raised={raised} final_locked={final_locked} final_ts={final_ts}",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 72)
    print("verify_interact_014_fix_busy_lock_leak — _busy lock leak 修复 V0-V8")
    print("=" * 72)

    v0_fingerprint()
    v1_imports()
    v2_normal_release()
    v3_exception_release()
    v4_watchdog_recovers_leak()
    v5_watchdog_skips_within_timeout()
    v6_env_override()
    v7_concurrent_acquire_fail()
    v8_double_release_safe()

    print("-" * 72)
    fails = [r for r in _results if not r["ok"]]
    print(f"summary: total={len(_results)} pass={len(_results) - len(fails)} fail={len(fails)}")
    for r in fails:
        print(f"  FAIL {r['name']}: {r['detail']}")
    print("-" * 72)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
