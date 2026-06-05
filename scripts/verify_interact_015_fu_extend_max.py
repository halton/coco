"""scripts/verify_interact_015_fu_extend_max.py — interact-015-fu-extend-max P2 锁定.

锁定 WakeGate.extend() 新语义：``_awake_until = max(_awake_until, now + N)``。
只续不缩——若当前剩余窗口比 ``now + N`` 还长，保留更长的 deadline。

V0  hash lock on coco/wake_word.py + anchors（含新语义关键字）
V1  extend(15) 后 trigger(6)：trigger 手动覆盖 → remaining ≈6s
V2  trigger(30) 后 extend(15)：max 取大 → remaining 仍 ≈30s（核心 P2 用例）
V3  trigger(6) 后 extend(30)：extend 取大 → remaining ≈30s
V4  extend(0) no-op（保留现有窗）
V5  extend(-5) no-op（保留现有窗）
V6  未 trigger 过直接 extend(20)：初始 _awake_until=0 兼容 → remaining ≈20s
V7  monkey-patch time.monotonic 模拟 25s 后：extend(15) 仍 max → 不缩短

跑法：``./.venv/bin/python scripts/verify_interact_015_fu_extend_max.py``
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}: {detail}")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# V0 hash lock + anchors（新 max 语义关键字）
# ---------------------------------------------------------------------------


EXPECTED_WAKE_SHA = "0464c21af3e168a742d9c58263328c4b6680bae26baf38a49a7f1fdbe230b8cf"


def v0_hash_lock() -> None:
    try:
        wake_path = ROOT / "coco" / "wake_word.py"
        if not wake_path.exists():
            _record("V0_hash_lock", False, f"missing {wake_path}")
            return
        actual = _sha256(wake_path)
        src = wake_path.read_text(encoding="utf-8")
        anchors = {
            "def_extend": "def extend(" in src,
            "max_call": "max(current, new_deadline)" in src,
            "current_default": "self._awake_until or 0.0" in src,
            "P2_doc": "只续不缩" in src,
            "feature_tag": "interact-015-fu-extend-max" in src,
        }
        missing = [k for k, v in anchors.items() if not v]
        sha_ok = actual == EXPECTED_WAKE_SHA
        ok = sha_ok and not missing
        detail = f"sha256={actual[:16]} expected={EXPECTED_WAKE_SHA[:16]} anchors_missing={missing}"
        _record("V0_hash_lock", ok, detail)
    except Exception as e:  # noqa: BLE001
        _record("V0_hash_lock", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V1 extend(15) then trigger(6) → trigger 覆盖
# ---------------------------------------------------------------------------


def v1_trigger_after_extend_overrides() -> None:
    try:
        from coco.wake_word import WakeGate
        g = WakeGate(window_seconds=6.0)
        g.extend(15.0)
        rem1 = g.remaining_seconds()
        # trigger 是手动开窗（不取 max），它本来就直接覆盖 _awake_until
        g.trigger()
        rem2 = g.remaining_seconds()
        # extend(15) 后约 15s；trigger() 用默认 window_seconds=6.0 → 约 6s
        ok = (14.0 < rem1 <= 15.0) and (5.0 < rem2 <= 6.0)
        _record("V1_trigger_after_extend_overrides", ok,
                f"after_extend15={rem1:.2f}s after_trigger6={rem2:.2f}s")
    except Exception as e:  # noqa: BLE001
        _record("V1_trigger_after_extend_overrides", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V2 trigger(30) then extend(15) → max 取大（核心 P2 修复用例）
# ---------------------------------------------------------------------------


def v2_extend_shorter_keeps_longer() -> None:
    try:
        from coco.wake_word import WakeGate
        g = WakeGate(window_seconds=30.0)
        g.trigger()
        rem_before = g.remaining_seconds()
        g.extend(15.0)
        rem_after = g.remaining_seconds()
        # 旧语义会缩到 ~15s；新 max() 语义保留 ~30s
        ok = (29.0 < rem_before <= 30.0) and (29.0 < rem_after <= 30.0)
        _record("V2_extend_shorter_keeps_longer", ok,
                f"trigger30={rem_before:.2f}s extend15→{rem_after:.2f}s (expect ~30, not shrunk)")
    except Exception as e:  # noqa: BLE001
        _record("V2_extend_shorter_keeps_longer", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V3 trigger(6) then extend(30) → max 取大（extend 续到更长）
# ---------------------------------------------------------------------------


def v3_extend_longer_takes_max() -> None:
    try:
        from coco.wake_word import WakeGate
        g = WakeGate(window_seconds=6.0)
        g.trigger()
        rem_before = g.remaining_seconds()
        g.extend(30.0)
        rem_after = g.remaining_seconds()
        ok = (5.0 < rem_before <= 6.0) and (29.0 < rem_after <= 30.0)
        _record("V3_extend_longer_takes_max", ok,
                f"trigger6={rem_before:.2f}s extend30→{rem_after:.2f}s (expect ~30)")
    except Exception as e:  # noqa: BLE001
        _record("V3_extend_longer_takes_max", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V4 extend(0) no-op
# ---------------------------------------------------------------------------


def v4_extend_zero_noop() -> None:
    try:
        from coco.wake_word import WakeGate
        g = WakeGate(window_seconds=10.0)
        g.trigger()
        rem_before = g.remaining_seconds()
        g.extend(0)
        rem_after = g.remaining_seconds()
        # 0 no-op：窗口完全不变（容差 0.1s 用于代码执行耗时）
        ok = (9.0 < rem_before <= 10.0) and (rem_after >= rem_before - 0.1) and (rem_after <= 10.0)
        _record("V4_extend_zero_noop", ok,
                f"before={rem_before:.2f}s after_extend0={rem_after:.2f}s")
    except Exception as e:  # noqa: BLE001
        _record("V4_extend_zero_noop", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V5 extend(-5) no-op
# ---------------------------------------------------------------------------


def v5_extend_negative_noop() -> None:
    try:
        from coco.wake_word import WakeGate
        g = WakeGate(window_seconds=10.0)
        g.trigger()
        rem_before = g.remaining_seconds()
        g.extend(-5)
        rem_after = g.remaining_seconds()
        ok = (9.0 < rem_before <= 10.0) and (rem_after >= rem_before - 0.1) and (rem_after <= 10.0)
        _record("V5_extend_negative_noop", ok,
                f"before={rem_before:.2f}s after_extend-5={rem_after:.2f}s")
    except Exception as e:  # noqa: BLE001
        _record("V5_extend_negative_noop", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V6 未 trigger 过直接 extend → 初始 _awake_until=0 兼容
# ---------------------------------------------------------------------------


def v6_initial_zero_compat() -> None:
    try:
        from coco.wake_word import WakeGate
        g = WakeGate(window_seconds=6.0)
        # 不 trigger，直接 extend → 初始 _awake_until 应为 0
        assert g._awake_until == 0.0, f"初始 _awake_until 应为 0，实际 {g._awake_until}"
        g.extend(20.0)
        rem = g.remaining_seconds()
        ok = g.is_awake() and (19.0 < rem <= 20.0)
        _record("V6_initial_zero_compat", ok,
                f"initial_zero_then_extend20={rem:.2f}s is_awake={g.is_awake()}")
    except Exception as e:  # noqa: BLE001
        _record("V6_initial_zero_compat", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V7 monkey-patch time.monotonic 模拟 25s 后 extend 仍 max
# ---------------------------------------------------------------------------


def v7_monkey_patch_long_window() -> None:
    """模拟用户场景：COCO_WAKE_WINDOW=30，trigger 后过 5s reply 完，extend(15) 不缩。

    用 monkey-patch time.monotonic 模拟，避免真 sleep。
    """
    try:
        import coco.wake_word as wake_mod
        from coco.wake_word import WakeGate

        # 用容器保存 fake_clock 状态，避免闭包修改问题
        clock = [1000.0]

        def fake_monotonic() -> float:
            return clock[0]

        orig_mono = wake_mod.time.monotonic
        wake_mod.time.monotonic = fake_monotonic
        try:
            g = WakeGate(window_seconds=30.0)
            g.trigger()  # _awake_until = 1000+30 = 1030
            # 跳到 5s 后
            clock[0] = 1005.0
            rem_at_5s = g.remaining_seconds()  # 1030 - 1005 = 25.0
            # 此时 extend(15) → max(1030, 1005+15=1020) = 1030 (不缩)
            g.extend(15.0)
            rem_after_extend = g.remaining_seconds()  # 仍 25.0
            # 用 extend(40) 续到更长 → max(1030, 1005+40=1045) = 1045 → remaining 40
            g.extend(40.0)
            rem_after_extend_longer = g.remaining_seconds()  # 40.0
        finally:
            wake_mod.time.monotonic = orig_mono

        ok = (
            abs(rem_at_5s - 25.0) < 0.01
            and abs(rem_after_extend - 25.0) < 0.01  # 关键：不缩短
            and abs(rem_after_extend_longer - 40.0) < 0.01  # 续到更长
        )
        _record("V7_monkey_patch_long_window", ok,
                f"at_5s={rem_at_5s:.2f}s after_extend15={rem_after_extend:.2f}s "
                f"after_extend40={rem_after_extend_longer:.2f}s")
    except Exception as e:  # noqa: BLE001
        _record("V7_monkey_patch_long_window", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    v0_hash_lock()
    v1_trigger_after_extend_overrides()
    v2_extend_shorter_keeps_longer()
    v3_extend_longer_takes_max()
    v4_extend_zero_noop()
    v5_extend_negative_noop()
    v6_initial_zero_compat()
    v7_monkey_patch_long_window()
    print("\n" + "=" * 60)
    fail = [r for r in _results if not r["ok"]]
    print(f"Summary: {len(_results) - len(fail)}/{len(_results)} PASS")
    if fail:
        for r in fail:
            print(f"  FAIL: {r['name']}: {r['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
