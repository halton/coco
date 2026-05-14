"""vision-010-fu-2 verification: GroupMode group_decision 真消费 _arbit_primary_*.

接入点：``GroupModeCoordinator._merge_member_prefer`` —— 当 ARBIT 有
``_arbit_primary_name`` 且 primary_name 在当前 group members 中时，给 primary
的 prefer_topics 整体 weight 乘 ``primary_prefer_boost``（默认 2.0），让
``ProactiveScheduler.set_topic_preferences`` 收到的 merged prefer 更倾向
primary 的兴趣。ARBIT OFF 时 primary 永远 None → boost 永不生效，bytewise
等价。

跑法::

    uv run python scripts/verify_vision_010_fu_2.py

子项：

V1   ARBIT=1 + arbit emit → coord 收到 primary（沿用 fu-1 wire 路径，回归保护）
V2   决策路径接入：alice-as-primary 与 bob-as-primary 时 _on_enter 推到
     ProactiveScheduler 的 merged prefer 不同（top-1 keyword 跟随 primary）
V3   ARBIT OFF → 决策路径与 baseline bytewise 等价（同输入同输出）
V4   ARBIT ON 但 primary 还未 wire（_arbit_primary_name=None）→ 决策走 fallback
     （= V3 baseline），不 crash
V5   primary 切换（先 alice 后 bob）→ 下一次 _on_enter merged prefer 跟进
V6   回归 verify_vision_010 / verify_vision_010_fu_1 全 PASS
V7   回归 verify_vision_008 / verify_vision_009 全 PASS

retval：0 全 PASS；1 任一失败
evidence 落 evidence/vision-010-fu-2/verify_summary.json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": ok, "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeRec:
    profile_id: str
    prefer_topics: Dict[str, float]
    group_sessions: list = field(default_factory=list)
    updated_ts: float = 0.0


class _FakePersistStore:
    def __init__(self, recs: Dict[str, _FakeRec]) -> None:
        self._recs = dict(recs)
        self.saved: List[_FakeRec] = []

    def load(self, pid: str) -> Optional[_FakeRec]:
        return self._recs.get(pid)

    def save(self, rec: _FakeRec) -> None:
        self.saved.append(rec)


class _FakeProactive:
    def __init__(self) -> None:
        self.prefer: Dict[str, float] = {}
        self.template_override: Optional[Tuple[str, ...]] = None
        self.prefer_history: List[Dict[str, float]] = []
        self.stats = SimpleNamespace(
            group_mode_trigger_count=0,
            group_mode_active_total=0,
        )

    def get_topic_preferences(self) -> Dict[str, float]:
        return dict(self.prefer)

    def set_topic_preferences(self, prefer: Mapping[str, float]) -> None:
        self.prefer = dict(prefer)
        self.prefer_history.append(dict(prefer))

    def set_group_template_override(self, phrases: Optional[Tuple[str, ...]]) -> None:
        self.template_override = tuple(phrases) if phrases else None


def _build_coord(
    *,
    arbit_env: bool,
    recs: Dict[str, _FakeRec],
    name_to_pid: Dict[str, str],
    primary_prefer_boost: Optional[float] = None,
):
    if arbit_env:
        os.environ["COCO_FACE_ID_ARBIT"] = "1"
    else:
        os.environ.pop("COCO_FACE_ID_ARBIT", None)
    from coco.companion.group_mode import GroupModeCoordinator

    proactive = _FakeProactive()
    persist = _FakePersistStore(recs)
    kwargs: Dict[str, Any] = dict(
        proactive_scheduler=proactive,
        persist_store=persist,
        profile_id_resolver=lambda nm: name_to_pid.get(nm),
        # hold=0 让一次 observe (≥2 known) 立刻进入 group_mode（同一 ts 第二次 observe）
        enter_hold_s=0.0,
        exit_hold_s=0.0,
    )
    if primary_prefer_boost is not None:
        kwargs["primary_prefer_boost"] = primary_prefer_boost
    coord = GroupModeCoordinator(**kwargs)
    return coord, proactive, persist


def _make_snapshot(names: List[str]):
    """构造 duck-typed snapshot：tracks=[obj.name=...]."""
    tracks = [SimpleNamespace(name=n) for n in names]
    return SimpleNamespace(tracks=tuple(tracks))


def _drive_enter(coord, names: List[str]) -> None:
    """触发 enter：enter_hold_s=0 → 第二次 observe 即 enter（candidate→hold check）."""
    snap = _make_snapshot(names)
    coord.observe(snap, now=1.0)
    coord.observe(snap, now=2.0)


_ALICE_PID = "aaaaaaaaaaaa"
_BOB_PID = "bbbbbbbbbbbb"

# alice 偏好：cats(1.0), travel(0.5), food(0.3)
# bob   偏好：travel(1.0), gaming(0.6), food(0.4)
_BASE_RECS = {
    _ALICE_PID: _FakeRec(_ALICE_PID, {"cats": 1.0, "travel": 0.5, "food": 0.3}),
    _BOB_PID: _FakeRec(_BOB_PID, {"travel": 1.0, "gaming": 0.6, "food": 0.4}),
}
_NAME_TO_PID = {"alice": _ALICE_PID, "bob": _BOB_PID}


def _top_key(d: Mapping[str, float]) -> Optional[str]:
    if not d:
        return None
    return max(d.items(), key=lambda kv: kv[1])[0]


# ---------------------------------------------------------------------------
# V1
# ---------------------------------------------------------------------------


def v1_arbit_wire_state_update() -> None:
    """V1 ARBIT=1 + on_face_id_arbit → coord primary state 写入（fu-1 回归保护）."""
    coord, _, _ = _build_coord(
        arbit_env=True, recs=_BASE_RECS, name_to_pid=_NAME_TO_PID
    )
    before = coord.current_arbit_primary()
    coord.on_face_id_arbit(primary=_ALICE_PID, primary_name="alice", ts=1.0)
    after_pid = coord.current_arbit_primary()
    after_name = coord.current_arbit_primary_name()
    ok = before is None and after_pid == _ALICE_PID and after_name == "alice"
    _record("v1_arbit_wire_state_update",
            ok, f"before={before!r} pid={after_pid!r} name={after_name!r}")


# ---------------------------------------------------------------------------
# V2
# ---------------------------------------------------------------------------


def v2_decision_consumes_primary() -> None:
    """V2 alice-primary vs bob-primary → merged prefer top-1 keyword 跟随 primary.

    alice 偏好 top: cats（独有）。bob 偏好 top: travel（与 alice 共有）。
    无 boost baseline：travel 因 union+intersect 在两人都有得到 bonus，应该是 top。
    alice-as-primary boost=3.0：cats(alice 独有) weight = 1.0 * 3.0 = 3.0；
        travel weight = (0.5*3.0 + 1.0)*1.5 = (1.5+1.0)*1.5 = 3.75 → travel 仍 top。
    需要 boost 足够大；为稳，boost=10.0 让 alice 独有 cats 一定胜出。
    bob-as-primary boost=10.0：bob 独有 gaming = 0.6 * 10.0 = 6.0 vs travel
        = (0.5 + 1.0*10.0)*1.5 = 15.75 → travel 仍 top；
        但 alice 独有 cats=1.0 vs bob 独有 gaming=6.0 → bob 的 gaming 排名上升。

    简化判据：top-1 在两 case 下排序变化 / weight 差异显著（不同输出即可证明
    决策真消费了 primary）。
    """
    members = ["alice", "bob"]

    # Case A: alice as primary, boost=10
    coord_a, p_a, _ = _build_coord(
        arbit_env=True, recs=_BASE_RECS, name_to_pid=_NAME_TO_PID,
        primary_prefer_boost=10.0,
    )
    coord_a.on_face_id_arbit(primary=_ALICE_PID, primary_name="alice", ts=1.0)
    _drive_enter(coord_a, members)
    prefer_alice = dict(p_a.prefer)

    # Case B: bob as primary, boost=10
    coord_b, p_b, _ = _build_coord(
        arbit_env=True, recs=_BASE_RECS, name_to_pid=_NAME_TO_PID,
        primary_prefer_boost=10.0,
    )
    coord_b.on_face_id_arbit(primary=_BOB_PID, primary_name="bob", ts=1.0)
    _drive_enter(coord_b, members)
    prefer_bob = dict(p_b.prefer)

    # 关键判据：两 prefer 不同 + alice-primary 下 cats 的归一 weight 显著高于
    # bob-primary 下 cats 的归一 weight。
    diff = prefer_alice != prefer_bob
    cats_alice = prefer_alice.get("cats", 0.0)
    cats_bob = prefer_bob.get("cats", 0.0)
    gaming_alice = prefer_alice.get("gaming", 0.0)
    gaming_bob = prefer_bob.get("gaming", 0.0)
    cats_promoted = cats_alice > cats_bob
    gaming_promoted = gaming_bob > gaming_alice
    ok = diff and cats_promoted and gaming_promoted
    _record(
        "v2_decision_consumes_primary",
        ok,
        f"diff={diff} cats(a)={cats_alice:.3f}>cats(b)={cats_bob:.3f} && "
        f"gaming(b)={gaming_bob:.3f}>gaming(a)={gaming_alice:.3f}",
    )


# ---------------------------------------------------------------------------
# V3 default-OFF bytewise equiv
# ---------------------------------------------------------------------------


def v3_default_off_bytewise_equiv() -> None:
    """V3 ARBIT OFF → on_face_id_arbit no-op → primary 永远 None → 决策走 baseline.

    构造两个 case，相同输入：
    A. ARBIT OFF（primary state 永远 None） → merged prefer = X
    B. ARBIT OFF + 调 on_face_id_arbit（应被 gate 掉无效）→ merged prefer = X
    判定：两 prefer bytewise 完全相等，且不依赖 primary boost 路径。
    """
    members = ["alice", "bob"]

    # A: 纯 OFF
    coord_a, p_a, _ = _build_coord(
        arbit_env=False, recs=_BASE_RECS, name_to_pid=_NAME_TO_PID,
        primary_prefer_boost=10.0,  # 即使 boost 配高，OFF 也不该生效
    )
    _drive_enter(coord_a, members)
    prefer_a = dict(p_a.prefer)

    # B: OFF 但被错误地调用 on_face_id_arbit（gate 应阻断）
    coord_b, p_b, _ = _build_coord(
        arbit_env=False, recs=_BASE_RECS, name_to_pid=_NAME_TO_PID,
        primary_prefer_boost=10.0,
    )
    coord_b.on_face_id_arbit(primary=_ALICE_PID, primary_name="alice", ts=1.0)
    _drive_enter(coord_b, members)
    prefer_b = dict(p_b.prefer)

    bytewise_eq = prefer_a == prefer_b
    primary_a = coord_a.current_arbit_primary()
    primary_b = coord_b.current_arbit_primary()
    primary_none = primary_a is None and primary_b is None
    ok = bytewise_eq and primary_none and len(prefer_a) > 0
    _record(
        "v3_default_off_bytewise_equiv",
        ok,
        f"eq={bytewise_eq} primary_a={primary_a!r} primary_b={primary_b!r} "
        f"size={len(prefer_a)}",
    )


# ---------------------------------------------------------------------------
# V4 ARBIT ON but primary not wired → fallback
# ---------------------------------------------------------------------------


def v4_arbit_on_primary_unwired_fallback() -> None:
    """V4 ARBIT=1 但还没收到 arbit emit → primary_name=None → 走 baseline，不 crash."""
    members = ["alice", "bob"]
    coord, p, _ = _build_coord(
        arbit_env=True, recs=_BASE_RECS, name_to_pid=_NAME_TO_PID,
        primary_prefer_boost=10.0,
    )
    # 不调 on_face_id_arbit
    _drive_enter(coord, members)
    prefer_unwired = dict(p.prefer)

    # baseline = ARBIT OFF 同输入
    coord_base, p_base, _ = _build_coord(
        arbit_env=False, recs=_BASE_RECS, name_to_pid=_NAME_TO_PID,
        primary_prefer_boost=10.0,
    )
    _drive_enter(coord_base, members)
    prefer_baseline = dict(p_base.prefer)

    eq = prefer_unwired == prefer_baseline
    ok = eq and len(prefer_unwired) > 0
    _record(
        "v4_arbit_on_primary_unwired_fallback",
        ok,
        f"eq_baseline={eq} size={len(prefer_unwired)}",
    )


# ---------------------------------------------------------------------------
# V5 primary 切换 → 下一次 enter 跟进
# ---------------------------------------------------------------------------


def v5_primary_switch_followed() -> None:
    """V5 先 alice 进 group → exit → 切 primary 到 bob → 再次 enter → prefer 不同."""
    members = ["alice", "bob"]
    coord, p, _ = _build_coord(
        arbit_env=True, recs=_BASE_RECS, name_to_pid=_NAME_TO_PID,
        primary_prefer_boost=10.0,
    )
    # 第一轮：alice as primary
    coord.on_face_id_arbit(primary=_ALICE_PID, primary_name="alice", ts=1.0)
    _drive_enter(coord, members)
    prefer_round1 = dict(p.prefer)
    # 退出 group_mode（≤1 known，exit_hold=0 → 第二次 observe 退）
    snap_solo = _make_snapshot(["alice"])
    coord.observe(snap_solo, now=3.0)
    coord.observe(snap_solo, now=4.0)
    assert not coord.is_active(), "expected group exit"

    # 第二轮：切 primary 到 bob，重新 enter
    coord.on_face_id_arbit(primary=_BOB_PID, primary_name="bob", ts=5.0)
    snap_pair = _make_snapshot(members)
    coord.observe(snap_pair, now=6.0)
    coord.observe(snap_pair, now=7.0)
    prefer_round2 = dict(p.prefer)

    diff = prefer_round1 != prefer_round2
    cats_r1 = prefer_round1.get("cats", 0.0)
    cats_r2 = prefer_round2.get("cats", 0.0)
    gaming_r1 = prefer_round1.get("gaming", 0.0)
    gaming_r2 = prefer_round2.get("gaming", 0.0)
    switched = cats_r1 > cats_r2 and gaming_r2 > gaming_r1
    ok = diff and switched
    _record(
        "v5_primary_switch_followed",
        ok,
        f"diff={diff} cats r1={cats_r1:.3f}>r2={cats_r2:.3f} && "
        f"gaming r2={gaming_r2:.3f}>r1={gaming_r1:.3f}",
    )


# ---------------------------------------------------------------------------
# V6/V7 regression
# ---------------------------------------------------------------------------


def _run_subprocess_verify(script: str) -> Tuple[bool, str]:
    env = os.environ.copy()
    env.pop("COCO_FACE_ID_ARBIT", None)
    env.pop("COCO_FACE_ID_PERSIST", None)
    env.pop("COCO_FACE_ID_REAL", None)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=240,
    )
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    return proc.returncode == 0, " | ".join(tail)


def v6_regress_vision_010_and_fu1() -> None:
    ok10, t10 = _run_subprocess_verify("verify_vision_010.py")
    okfu1, tfu1 = _run_subprocess_verify("verify_vision_010_fu_1.py")
    _record(
        "v6_regress_vision_010_and_fu1",
        ok10 and okfu1,
        f"010: {t10} || 010-fu-1: {tfu1}",
    )


def v7_regress_vision_008_009() -> None:
    ok8, t8 = _run_subprocess_verify("verify_vision_008.py")
    ok9, t9 = _run_subprocess_verify("verify_vision_009.py")
    _record(
        "v7_regress_vision_008_009",
        ok8 and ok9,
        f"008: {t8} || 009: {t9}",
    )


# ---------------------------------------------------------------------------


def main() -> int:
    for fn in (
        v1_arbit_wire_state_update,
        v2_decision_consumes_primary,
        v3_default_off_bytewise_equiv,
        v4_arbit_on_primary_unwired_fallback,
        v5_primary_switch_followed,
        v6_regress_vision_010_and_fu1,
        v7_regress_vision_008_009,
    ):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _record(fn.__name__, False, f"unhandled exception {e!r}")

    out = ROOT / "evidence" / "vision-010-fu-2"
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "vision-010-fu-2",
        "ok": all(r["ok"] for r in _results),
        "results": _results,
    }
    (out / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_pass = sum(1 for r in _results if r["ok"])
    n_total = len(_results)
    print(f"\n[vision-010-fu-2] {n_pass}/{n_total} PASS")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
