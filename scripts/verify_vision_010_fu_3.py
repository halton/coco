"""vision-010-fu-3 verification: 关闭 fu-2 caveat C-3 + C-4.

C-3: ``primary_prefer_boost`` 暴露为 env ``COCO_GROUP_PRIMARY_PREFER_BOOST``;
     main.py 构造 GroupModeCoordinator 时读取并 override.
C-4: ``group_phrases`` 接受 ``{primary_name}`` 占位; render 时按 ARBIT primary
     填入或剔除含占位句式; default-OFF bytewise 等价.

跑法::

    uv run python scripts/verify_vision_010_fu_3.py

子项:

V1   env COCO_GROUP_PRIMARY_PREFER_BOOST=5.0 → coord.primary_prefer_boost==5.0
V2   env 未设 → coord 走 DEFAULT_PRIMARY_PREFER_BOOST (=2.0)
V3   env 非数字 / 负数 / 0 → fallback to default + warn, 不 crash
V4   ARBIT primary 已 wire → set_group_template_override 收到的 phrases 含
     渲染后 primary_name (e.g. "alice")
V5   ARBIT primary 未 wire (None) → 含 {primary_name} 的句式被剔除, fallback
     通用句式; 不出现 "{primary_name}" 字面量 / "None" / 空 brace
V6   ARBIT OFF / DEFAULT_GROUP_PHRASES → set_group_template_override 收到的
     phrases 与原 DEFAULT_GROUP_PHRASES bytewise 等价
V7   回归 verify_vision_010 / verify_vision_010_fu_1 / verify_vision_010_fu_2
V8   回归 verify_vision_008 / verify_vision_009

retval: 0 全 PASS; 1 任一失败.
evidence 落 evidence/vision-010-fu-3/verify_summary.json
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
# Fakes (复用 fu-2 风格)
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
        self.template_history: List[Optional[Tuple[str, ...]]] = []
        self.stats = SimpleNamespace(
            group_mode_trigger_count=0,
            group_mode_active_total=0,
        )

    def get_topic_preferences(self) -> Dict[str, float]:
        return dict(self.prefer)

    def set_topic_preferences(self, prefer: Mapping[str, float]) -> None:
        self.prefer = dict(prefer)

    def set_group_template_override(
        self, phrases: Optional[Tuple[str, ...]]
    ) -> None:
        self.template_override = tuple(phrases) if phrases else None
        self.template_history.append(
            tuple(phrases) if phrases else None
        )


_ALICE_PID = "aaaaaaaaaaaa"
_BOB_PID = "bbbbbbbbbbbb"
_BASE_RECS = {
    _ALICE_PID: _FakeRec(_ALICE_PID, {"cats": 1.0, "travel": 0.5}),
    _BOB_PID: _FakeRec(_BOB_PID, {"travel": 1.0, "gaming": 0.6}),
}
_NAME_TO_PID = {"alice": _ALICE_PID, "bob": _BOB_PID}


def _make_snapshot(names: List[str]):
    tracks = [SimpleNamespace(name=n) for n in names]
    return SimpleNamespace(tracks=tuple(tracks))


def _build_coord(
    *,
    arbit_env: bool,
    group_phrases: Optional[Tuple[str, ...]] = None,
    primary_prefer_boost: Optional[float] = None,
):
    if arbit_env:
        os.environ["COCO_FACE_ID_ARBIT"] = "1"
    else:
        os.environ.pop("COCO_FACE_ID_ARBIT", None)
    from coco.companion.group_mode import GroupModeCoordinator

    proactive = _FakeProactive()
    persist = _FakePersistStore(_BASE_RECS)
    kwargs: Dict[str, Any] = dict(
        proactive_scheduler=proactive,
        persist_store=persist,
        profile_id_resolver=lambda nm: _NAME_TO_PID.get(nm),
        enter_hold_s=0.0,
        exit_hold_s=0.0,
    )
    if group_phrases is not None:
        kwargs["group_phrases"] = group_phrases
    if primary_prefer_boost is not None:
        kwargs["primary_prefer_boost"] = primary_prefer_boost
    coord = GroupModeCoordinator(**kwargs)
    return coord, proactive, persist


def _drive_enter(coord, names: List[str]) -> None:
    snap = _make_snapshot(names)
    coord.observe(snap, now=1.0)
    coord.observe(snap, now=2.0)


# ---------------------------------------------------------------------------
# C-3: env COCO_GROUP_PRIMARY_PREFER_BOOST
# ---------------------------------------------------------------------------


def v1_env_boost_override() -> None:
    """V1 env=5.0 → coord.primary_prefer_boost == 5.0."""
    from coco.companion.group_mode import (
        GroupModeCoordinator,
        read_primary_prefer_boost_from_env,
    )
    val = read_primary_prefer_boost_from_env({"COCO_GROUP_PRIMARY_PREFER_BOOST": "5.0"})
    coord = GroupModeCoordinator(primary_prefer_boost=val)
    ok = val == 5.0 and coord.primary_prefer_boost == 5.0
    _record("v1_env_boost_override",
            ok, f"val={val} coord.boost={coord.primary_prefer_boost}")


def v2_env_unset_default() -> None:
    """V2 env 未设 → read 返回 None → coord 走 DEFAULT_PRIMARY_PREFER_BOOST (=2.0)."""
    from coco.companion.group_mode import (
        DEFAULT_PRIMARY_PREFER_BOOST,
        GroupModeCoordinator,
        read_primary_prefer_boost_from_env,
    )
    val = read_primary_prefer_boost_from_env({})
    # 模拟 main.py 行为：val is None → 不传 kwargs → coord 走 default
    coord = GroupModeCoordinator()
    ok = (
        val is None
        and coord.primary_prefer_boost == DEFAULT_PRIMARY_PREFER_BOOST
        and DEFAULT_PRIMARY_PREFER_BOOST == 2.0
    )
    _record("v2_env_unset_default",
            ok, f"val={val} coord.boost={coord.primary_prefer_boost} default={DEFAULT_PRIMARY_PREFER_BOOST}")


def v3_env_invalid_fallback() -> None:
    """V3 非数字 / 负数 / 0 → fallback to None + warn, 不 crash."""
    from coco.companion.group_mode import (
        DEFAULT_PRIMARY_PREFER_BOOST,
        GroupModeCoordinator,
        read_primary_prefer_boost_from_env,
    )
    cases = ["abc", "-1.5", "0", " "]
    warns: List[str] = []
    bad: List[str] = []
    for raw in cases:
        try:
            v = read_primary_prefer_boost_from_env(
                {"COCO_GROUP_PRIMARY_PREFER_BOOST": raw},
                warn=warns.append,
            )
        except Exception as ex:  # noqa: BLE001
            bad.append(f"crash on {raw!r}: {ex!r}")
            continue
        if v is not None:
            bad.append(f"{raw!r} → got {v} (expected None)")
    # 空白 " " 走 "no env" 路径，不 warn；"abc"/"-1.5"/"0" 都应 warn
    expected_warn_count = 3
    coord = GroupModeCoordinator()
    boost_default = coord.primary_prefer_boost == DEFAULT_PRIMARY_PREFER_BOOST
    ok = (
        not bad
        and len(warns) == expected_warn_count
        and boost_default
    )
    _record("v3_env_invalid_fallback",
            ok, f"bad={bad} warns={len(warns)}/{expected_warn_count} default_ok={boost_default}")


# ---------------------------------------------------------------------------
# C-4: {primary_name} 占位渲染
# ---------------------------------------------------------------------------


_PHRASES_WITH_PLACEHOLDER = (
    "{primary_name} 你今天看起来不错",
    "{primary_name} 跟大家分享一下",
    "大家好，一起聊聊",
    "你们好啊",
)


def v4_arbit_primary_wired_render() -> None:
    """V4 ARBIT primary 已 wire → 渲染含 {primary_name} 的句式正确填入."""
    coord, proactive, _ = _build_coord(
        arbit_env=True,
        group_phrases=_PHRASES_WITH_PLACEHOLDER,
        primary_prefer_boost=2.0,
    )
    coord.on_face_id_arbit(primary=_ALICE_PID, primary_name="alice", ts=1.0)
    _drive_enter(coord, ["alice", "bob"])
    rendered = proactive.template_override
    expected = (
        "alice 你今天看起来不错",
        "alice 跟大家分享一下",
        "大家好，一起聊聊",
        "你们好啊",
    )
    ok = rendered == expected
    _record("v4_arbit_primary_wired_render",
            ok, f"rendered={rendered} expected={expected}")


def v5_arbit_primary_unwired_drop() -> None:
    """V5 primary_name=None → 含占位的句式被剔除, fallback 通用句式; 不出现
    '{primary_name}' / 'None' / 空 brace."""
    coord, proactive, _ = _build_coord(
        arbit_env=True,
        group_phrases=_PHRASES_WITH_PLACEHOLDER,
        primary_prefer_boost=2.0,
    )
    # 不调 on_face_id_arbit → primary 仍 None
    _drive_enter(coord, ["alice", "bob"])
    rendered = proactive.template_override or ()
    expected = (
        "大家好，一起聊聊",
        "你们好啊",
    )
    no_brace_leak = all(
        "{primary_name}" not in p and "None" not in p
        for p in rendered
    )
    ok = rendered == expected and no_brace_leak
    _record("v5_arbit_primary_unwired_drop",
            ok, f"rendered={rendered} expected={expected} no_leak={no_brace_leak}")


def v6_default_off_bytewise_equiv() -> None:
    """V6 ARBIT OFF + DEFAULT_GROUP_PHRASES → set_group_template_override
    收到的 phrases 与原 DEFAULT_GROUP_PHRASES bytewise 等价."""
    from coco.companion.group_mode import DEFAULT_GROUP_PHRASES
    coord, proactive, _ = _build_coord(
        arbit_env=False,
        group_phrases=None,  # 用 default
        primary_prefer_boost=10.0,  # boost 配高也不该影响 template render
    )
    _drive_enter(coord, ["alice", "bob"])
    rendered = proactive.template_override
    ok = rendered == DEFAULT_GROUP_PHRASES
    _record("v6_default_off_bytewise_equiv",
            ok, f"rendered_eq_default={ok} default={DEFAULT_GROUP_PHRASES}")


# ---------------------------------------------------------------------------
# Bonus: primary 切换 in-flight re-render
# ---------------------------------------------------------------------------


def v6b_primary_switch_rerender() -> None:
    """额外: group active 期间 primary 切换 → re-render override."""
    coord, proactive, _ = _build_coord(
        arbit_env=True,
        group_phrases=_PHRASES_WITH_PLACEHOLDER,
        primary_prefer_boost=2.0,
    )
    coord.on_face_id_arbit(primary=_ALICE_PID, primary_name="alice", ts=1.0)
    _drive_enter(coord, ["alice", "bob"])
    first = proactive.template_override
    # 切到 bob
    coord.on_face_id_arbit(primary=_BOB_PID, primary_name="bob", ts=2.0)
    second = proactive.template_override
    ok = (
        first is not None
        and second is not None
        and first != second
        and any("bob" in p for p in second)
        and not any("alice" in p for p in second)
    )
    _record("v6b_primary_switch_rerender",
            ok, f"first={first} second={second}")


# ---------------------------------------------------------------------------
# Regression
# ---------------------------------------------------------------------------


def _run_subprocess_verify(script: str) -> Tuple[bool, str]:
    env = os.environ.copy()
    env.pop("COCO_FACE_ID_ARBIT", None)
    env.pop("COCO_FACE_ID_PERSIST", None)
    env.pop("COCO_FACE_ID_REAL", None)
    env.pop("COCO_GROUP_PRIMARY_PREFER_BOOST", None)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=240,
    )
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    return proc.returncode == 0, " | ".join(tail)


def v7_regress_010_chain() -> None:
    ok10, t10 = _run_subprocess_verify("verify_vision_010.py")
    okfu1, tfu1 = _run_subprocess_verify("verify_vision_010_fu_1.py")
    okfu2, tfu2 = _run_subprocess_verify("verify_vision_010_fu_2.py")
    _record(
        "v7_regress_010_chain",
        ok10 and okfu1 and okfu2,
        f"010:{ok10} fu1:{okfu1} fu2:{okfu2} | {t10[:60]} || {tfu1[:60]} || {tfu2[:60]}",
    )


def v8_regress_008_009() -> None:
    ok8, t8 = _run_subprocess_verify("verify_vision_008.py")
    ok9, t9 = _run_subprocess_verify("verify_vision_009.py")
    _record(
        "v8_regress_008_009",
        ok8 and ok9,
        f"008:{ok8} 009:{ok9}",
    )


# ---------------------------------------------------------------------------


def main() -> int:
    for fn in (
        v1_env_boost_override,
        v2_env_unset_default,
        v3_env_invalid_fallback,
        v4_arbit_primary_wired_render,
        v5_arbit_primary_unwired_drop,
        v6_default_off_bytewise_equiv,
        v6b_primary_switch_rerender,
        v7_regress_010_chain,
        v8_regress_008_009,
    ):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _record(fn.__name__, False, f"unhandled exception {e!r}")

    out = ROOT / "evidence" / "vision-010-fu-3"
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "vision-010-fu-3",
        "ok": all(r["ok"] for r in _results),
        "results": _results,
    }
    (out / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_pass = sum(1 for r in _results if r["ok"])
    n_total = len(_results)
    print(f"\n[vision-010-fu-3] {n_pass}/{n_total} PASS")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
