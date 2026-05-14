"""vision-010-fu-4 verification: 关闭 fu-3 caveat-1/2.

caveat-1: COCO_GROUP_PRIMARY_PREFER_BOOST=NaN/Inf 应被拦截.
caveat-2: 应有硬上限 MAX_PRIMARY_PREFER_BOOST=100.0; 超出走 default.

跑法::

    uv run python scripts/verify_vision_010_fu_4.py

子项:

V1   env=nan / NaN → warn + None → coordinator 走 default 2.0
V2   env=inf / -inf / Infinity → warn + None → default
V3   env=100.5 (超上限) → warn + None → default
V4   env=100.0 (边界) → accept = 100.0
V5   env=50.0 (合法范围内) → accept = 50.0
V6   回归 vision-010 / fu-1 / fu-2 / fu-3 verify 全 PASS

retval: 0 全 PASS; 1 任一失败.
evidence 落 evidence/vision-010-fu-4/verify_summary.json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": ok, "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


# ---------------------------------------------------------------------------
# V1: NaN
# ---------------------------------------------------------------------------


def v1_env_nan_rejected() -> None:
    """V1 env=nan / NaN → warn + None → coord 走 DEFAULT_PRIMARY_PREFER_BOOST."""
    from coco.companion.group_mode import (
        DEFAULT_PRIMARY_PREFER_BOOST,
        GroupModeCoordinator,
        read_primary_prefer_boost_from_env,
    )
    cases = ["nan", "NaN", "NAN"]
    warns: List[str] = []
    bad: List[str] = []
    for raw in cases:
        v = read_primary_prefer_boost_from_env(
            {"COCO_GROUP_PRIMARY_PREFER_BOOST": raw},
            warn=warns.append,
        )
        if v is not None:
            bad.append(f"{raw!r} → {v} (expected None)")
    coord = GroupModeCoordinator()
    boost_default = coord.primary_prefer_boost == DEFAULT_PRIMARY_PREFER_BOOST
    ok = not bad and len(warns) == len(cases) and boost_default
    _record(
        "v1_env_nan_rejected",
        ok,
        f"bad={bad} warns={len(warns)}/{len(cases)} default_ok={boost_default}",
    )


# ---------------------------------------------------------------------------
# V2: Inf
# ---------------------------------------------------------------------------


def v2_env_inf_rejected() -> None:
    """V2 env=inf / -inf / Infinity → warn + None → default."""
    from coco.companion.group_mode import (
        DEFAULT_PRIMARY_PREFER_BOOST,
        GroupModeCoordinator,
        read_primary_prefer_boost_from_env,
    )
    cases = ["inf", "-inf", "Infinity", "-Infinity"]
    warns: List[str] = []
    bad: List[str] = []
    for raw in cases:
        v = read_primary_prefer_boost_from_env(
            {"COCO_GROUP_PRIMARY_PREFER_BOOST": raw},
            warn=warns.append,
        )
        if v is not None:
            bad.append(f"{raw!r} → {v} (expected None)")
    coord = GroupModeCoordinator()
    boost_default = coord.primary_prefer_boost == DEFAULT_PRIMARY_PREFER_BOOST
    ok = not bad and len(warns) == len(cases) and boost_default
    _record(
        "v2_env_inf_rejected",
        ok,
        f"bad={bad} warns={len(warns)}/{len(cases)} default_ok={boost_default}",
    )


# ---------------------------------------------------------------------------
# V3: above upper limit
# ---------------------------------------------------------------------------


def v3_env_above_upper_limit_rejected() -> None:
    """V3 env=100.5 / 1e6 (超 MAX_PRIMARY_PREFER_BOOST) → warn + None → default."""
    from coco.companion.group_mode import (
        DEFAULT_PRIMARY_PREFER_BOOST,
        GroupModeCoordinator,
        MAX_PRIMARY_PREFER_BOOST,
        read_primary_prefer_boost_from_env,
    )
    cases = ["100.5", "1e6", "999999"]
    warns: List[str] = []
    bad: List[str] = []
    for raw in cases:
        v = read_primary_prefer_boost_from_env(
            {"COCO_GROUP_PRIMARY_PREFER_BOOST": raw},
            warn=warns.append,
        )
        if v is not None:
            bad.append(f"{raw!r} → {v} (expected None)")
    coord = GroupModeCoordinator()
    boost_default = coord.primary_prefer_boost == DEFAULT_PRIMARY_PREFER_BOOST
    ok = (
        not bad
        and len(warns) == len(cases)
        and boost_default
        and MAX_PRIMARY_PREFER_BOOST == 100.0
    )
    _record(
        "v3_env_above_upper_limit_rejected",
        ok,
        f"bad={bad} warns={len(warns)}/{len(cases)} default_ok={boost_default} MAX={MAX_PRIMARY_PREFER_BOOST}",
    )


# ---------------------------------------------------------------------------
# V4: boundary 100.0 accept
# ---------------------------------------------------------------------------


def v4_env_at_boundary_accepted() -> None:
    """V4 env=100.0 (== MAX) → accept; coord.primary_prefer_boost == 100.0."""
    from coco.companion.group_mode import (
        GroupModeCoordinator,
        MAX_PRIMARY_PREFER_BOOST,
        read_primary_prefer_boost_from_env,
    )
    warns: List[str] = []
    val = read_primary_prefer_boost_from_env(
        {"COCO_GROUP_PRIMARY_PREFER_BOOST": "100.0"},
        warn=warns.append,
    )
    coord = GroupModeCoordinator(primary_prefer_boost=val) if val is not None else GroupModeCoordinator()
    ok = (
        val == 100.0
        and val == MAX_PRIMARY_PREFER_BOOST
        and coord.primary_prefer_boost == 100.0
        and not warns
    )
    _record(
        "v4_env_at_boundary_accepted",
        ok,
        f"val={val} coord.boost={coord.primary_prefer_boost} warns={warns}",
    )


# ---------------------------------------------------------------------------
# V5: legal mid-range
# ---------------------------------------------------------------------------


def v5_env_legal_mid_range_accepted() -> None:
    """V5 env=50.0 (合法范围内) → accept = 50.0."""
    from coco.companion.group_mode import (
        GroupModeCoordinator,
        read_primary_prefer_boost_from_env,
    )
    warns: List[str] = []
    val = read_primary_prefer_boost_from_env(
        {"COCO_GROUP_PRIMARY_PREFER_BOOST": "50.0"},
        warn=warns.append,
    )
    coord = GroupModeCoordinator(primary_prefer_boost=val) if val is not None else GroupModeCoordinator()
    ok = val == 50.0 and coord.primary_prefer_boost == 50.0 and not warns
    _record(
        "v5_env_legal_mid_range_accepted",
        ok,
        f"val={val} coord.boost={coord.primary_prefer_boost} warns={warns}",
    )


# ---------------------------------------------------------------------------
# V6: regression
# ---------------------------------------------------------------------------


def _run_subprocess_verify(script: str) -> Tuple[bool, str]:
    env = os.environ.copy()
    env.pop("COCO_FACE_ID_ARBIT", None)
    env.pop("COCO_FACE_ID_PERSIST", None)
    env.pop("COCO_FACE_ID_REAL", None)
    env.pop("COCO_GROUP_PRIMARY_PREFER_BOOST", None)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300,
    )
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    return proc.returncode == 0, " | ".join(tail)


def v6_regress_010_chain() -> None:
    ok10, t10 = _run_subprocess_verify("verify_vision_010.py")
    okfu1, tfu1 = _run_subprocess_verify("verify_vision_010_fu_1.py")
    okfu2, tfu2 = _run_subprocess_verify("verify_vision_010_fu_2.py")
    okfu3, tfu3 = _run_subprocess_verify("verify_vision_010_fu_3.py")
    _record(
        "v6_regress_010_chain",
        ok10 and okfu1 and okfu2 and okfu3,
        f"010:{ok10} fu1:{okfu1} fu2:{okfu2} fu3:{okfu3}",
    )


# ---------------------------------------------------------------------------


def main() -> int:
    for fn in (
        v1_env_nan_rejected,
        v2_env_inf_rejected,
        v3_env_above_upper_limit_rejected,
        v4_env_at_boundary_accepted,
        v5_env_legal_mid_range_accepted,
        v6_regress_010_chain,
    ):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _record(fn.__name__, False, f"unhandled exception {e!r}")

    out = ROOT / "evidence" / "vision-010-fu-4"
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "vision-010-fu-4",
        "ok": all(r["ok"] for r in _results),
        "results": _results,
    }
    (out / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_pass = sum(1 for r in _results if r["ok"])
    n_total = len(_results)
    print(f"\n[vision-010-fu-4] {n_pass}/{n_total} PASS")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
