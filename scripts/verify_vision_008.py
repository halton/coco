"""vision-008 verification: face_id 真接 GroupModeCoordinator.

跑法::

    uv run python scripts/verify_vision_008.py

子项：

V1   default-OFF：未设 COCO_FACE_ID_REAL 时 FaceTracker.get_face_id 返回 None
     （companion-012 fu-2 stub 路径 bytewise 等价）
V2   gate ON：FaceTracker.get_face_id 对同一 name 返回稳定 face_id
V3   多 face_id 区分：不同 name → 不同 face_id；多次调用同 name → 同 face_id
V4   接口契约：GroupModeCoordinator 通过 profile_id_resolver 调用 get_face_id
     时 face_id 路径生效（fid_xxx 进 compute_profile_id 当 stable_id）
V5   gate OFF 兼容路径：未设 env 时 resolver 走 sha1(name) fallback（与
     companion-012 V5 相同行为）
V6   FaceTracker emit hook：env ON 时首次解析 emit "vision.face_id_resolved"
V7   FaceTracker schema 向后兼容：__init__ 不带 emit_fn 仍可构造；TrackedFace
     dataclass 字段未删
V8   AST/grep marker：face_tracker.py 含 vision-008 marker + COCO_FACE_ID_REAL
V9   fixture two_faces.mp4 存在且非空，cv2 可解码
V10  classifier-aware path：注入带 store 的 fake classifier → face_id 用
     fid_<user_id> 形式

retval：0 全 PASS；1 任一失败
evidence 落 evidence/vision-008/verify_summary.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": ok, "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def _fresh_face_tracker(*, real: Optional[bool], **kwargs):
    """构造 FaceTracker，按 real 显式 set/unset env，避免测试间污染。"""
    if real is True:
        os.environ["COCO_FACE_ID_REAL"] = "1"
    elif real is False:
        os.environ.pop("COCO_FACE_ID_REAL", None)
    # delayed import 保证 env 在 init 时已就位
    from coco.perception.face_tracker import FaceTracker
    return FaceTracker(threading.Event(), **kwargs)


def v1_default_off() -> None:
    """V1 default-OFF：未设 env → get_face_id 返回 None。"""
    ft = _fresh_face_tracker(real=False)
    r = ft.get_face_id("alice")
    _record("V1 default-OFF get_face_id('alice') is None", r is None, f"got={r!r}")


def v2_stable_id_on() -> None:
    """V2 ON 时同一 name 返回稳定 face_id。"""
    ft = _fresh_face_tracker(real=True)
    a1 = ft.get_face_id("alice")
    a2 = ft.get_face_id("alice")
    ok = bool(a1) and a1 == a2 and a1.startswith("fid_")
    _record("V2 stable face_id for same name", ok, f"a1={a1!r} a2={a2!r}")


def v3_distinct_ids() -> None:
    """V3 不同 name → 不同 face_id。"""
    ft = _fresh_face_tracker(real=True)
    a = ft.get_face_id("alice")
    b = ft.get_face_id("bob")
    c = ft.get_face_id("charlie")
    distinct = len({a, b, c}) == 3
    same_repeat = ft.get_face_id("alice") == a and ft.get_face_id("bob") == b
    _record("V3 distinct face_id for distinct names + idempotent",
            distinct and same_repeat,
            f"a={a!r} b={b!r} c={c!r} same_repeat={same_repeat}")


def v4_group_mode_resolver_face_id_path() -> None:
    """V4 GroupModeCoordinator.observe via resolver 真接 face_id."""
    try:
        from types import SimpleNamespace
        from coco.companion.group_mode import GroupModeCoordinator
        from coco.companion.profile_persist import compute_profile_id

        ft = _fresh_face_tracker(real=True)

        captured: Dict[str, Optional[str]] = {}

        def resolver(name: str):
            if not name:
                return None
            fid = ft.get_face_id(name)
            captured[name] = fid
            stable = fid or name
            return compute_profile_id(stable, name)

        class _FakePersist:
            def __init__(self) -> None:
                self.saved: List[Any] = []
                self._recs: Dict[str, Any] = {}

            def load(self, pid):
                return self._recs.get(pid)

            def save(self, rec):
                self.saved.append(rec)
                self._recs[rec.profile_id] = rec

        coord = GroupModeCoordinator(
            proactive_scheduler=None,
            persist_store=_FakePersist(),
            profile_id_resolver=resolver,
            enter_hold_s=0.0,
            exit_hold_s=0.0,
        )

        def _snap(*names):
            return SimpleNamespace(
                tracks=[SimpleNamespace(name=n) for n in names]
            )

        t = 0.0
        for _ in range(3):
            coord.observe(_snap("alice", "bob"), now=t)
            t += 1.0

        # alice/bob 都被 resolver 调过 → captured 应有非空 face_id
        ok = (
            captured.get("alice", None) is not None
            and captured["alice"].startswith("fid_")
            and captured.get("bob", None) is not None
            and captured["bob"].startswith("fid_")
            and captured["alice"] != captured["bob"]
        )
        _record(
            "V4 GroupMode resolver 走 face_id 路径",
            ok,
            f"captured={captured!r} active={coord.is_active()}",
        )
    except Exception as e:  # noqa: BLE001
        _record("V4 GroupMode resolver face_id path", False, f"exception {e!r}")


def v5_gate_off_compat() -> None:
    """V5 gate OFF：resolver 走 sha1(name) fallback (= companion-012 V5 路径)."""
    try:
        from coco.companion.profile_persist import compute_profile_id

        ft = _fresh_face_tracker(real=False)

        def resolver(name: str):
            if not name:
                return None
            fid = None
            try:
                fid = ft.get_face_id(name)
            except Exception:  # noqa: BLE001
                fid = None
            stable = fid or name
            return compute_profile_id(stable, name)

        pid = resolver("alice")
        # 期望：face_id 为 None → stable = name → compute_profile_id(name, name)
        expected = compute_profile_id("alice", "alice")
        ok = pid is not None and pid == expected
        _record("V5 gate-OFF resolver 走 sha1(name) fallback",
                ok, f"pid={pid!r} expected={expected!r}")
    except Exception as e:  # noqa: BLE001
        _record("V5 gate-OFF resolver fallback", False, f"exception {e!r}")


def v6_emit_face_id_resolved() -> None:
    """V6 首次解析 emit vision.face_id_resolved。

    vision-009 后 emit_fn 签名对齐 ``coco.logging_setup.emit``，即
    ``(component_event: str, message: str = "", **payload)``。
    """
    captured: List[Dict[str, Any]] = []

    def fake_emit(component_event: str, message: str = "", **payload: Any) -> None:
        # 兼容 vision-009 后签名（component_event="vision.face_id_resolved"）
        if "." in component_event:
            comp, ev = component_event.split(".", 1)
        else:
            comp, ev = component_event, "event"
        captured.append({"component": comp, "event": ev, **payload})

    ft = _fresh_face_tracker(real=True, emit_fn=fake_emit)
    fid1 = ft.get_face_id("alice")
    fid2 = ft.get_face_id("alice")  # 缓存命中，不应再 emit
    fid3 = ft.get_face_id("bob")
    only_first_for_each = (
        sum(1 for e in captured if e.get("name") == "alice") == 1
        and sum(1 for e in captured if e.get("name") == "bob") == 1
    )
    schema_ok = all(
        e.get("component") == "vision"
        and e.get("event") == "face_id_resolved"
        and isinstance(e.get("face_id"), str)
        and e["face_id"].startswith("fid_")
        for e in captured
    )
    _record(
        "V6 emit vision.face_id_resolved schema + once-per-name",
        only_first_for_each and schema_ok and len(captured) == 2,
        f"captured={captured} fid1={fid1!r} fid2={fid2!r} fid3={fid3!r}",
    )


def v7_schema_backcompat() -> None:
    """V7 FaceTracker 不带 emit_fn 可构造；TrackedFace 字段完整。"""
    try:
        from coco.perception.face_tracker import FaceTracker, TrackedFace
        # 不传 emit_fn / face_id_classifier 默认参数
        ft = FaceTracker(threading.Event())
        # TrackedFace 仍有 vision-003 字段
        fields = {f for f in TrackedFace.__dataclass_fields__.keys()}
        need = {"track_id", "box", "age_frames", "hit_count", "miss_count",
                "smoothed_cx", "smoothed_cy", "presence_score",
                "first_seen_ts", "last_seen_ts", "name", "name_confidence"}
        ok = need.issubset(fields)
        _record("V7 FaceTracker / TrackedFace schema 向后兼容",
                ok, f"missing={need - fields}")
    except Exception as e:  # noqa: BLE001
        _record("V7 schema back-compat", False, f"exception {e!r}")


def v8_marker_in_source() -> None:
    """V8 face_tracker.py 含 vision-008 marker + COCO_FACE_ID_REAL 字面量。"""
    src = (ROOT / "coco" / "perception" / "face_tracker.py").read_text(encoding="utf-8")
    has_marker = "vision-008" in src
    has_env = "COCO_FACE_ID_REAL" in src
    has_emit = "face_id_resolved" in src
    _record(
        "V8 face_tracker.py vision-008 marker + env + emit",
        has_marker and has_env and has_emit,
        f"marker={has_marker} env={has_env} emit={has_emit}",
    )


def v9_fixture_two_faces() -> None:
    """V9 fixture two_faces.mp4 存在且 cv2 可解码。"""
    p = ROOT / "tests" / "fixtures" / "vision" / "two_faces.mp4"
    exists = p.exists() and p.stat().st_size > 0
    decodable = False
    nframes = 0
    if exists:
        try:
            import cv2  # type: ignore
            cap = cv2.VideoCapture(str(p))
            if cap.isOpened():
                while True:
                    ok, _ = cap.read()
                    if not ok:
                        break
                    nframes += 1
                cap.release()
                decodable = nframes > 0
        except Exception as e:  # noqa: BLE001
            decodable = False
            print(f"  cv2 decode error: {e!r}")
    _record("V9 fixture two_faces.mp4 存在且可解码",
            exists and decodable,
            f"exists={exists} frames={nframes} size={p.stat().st_size if exists else 0}")


def v10_classifier_aware_path() -> None:
    """V10 注入带 store 的 fake classifier → face_id 用 fid_<user_id> 形式。"""
    try:
        from types import SimpleNamespace
        # fake store: alice→user_id=7, bob→user_id=42
        store = SimpleNamespace(
            all_records=lambda: {
                7: SimpleNamespace(name="alice"),
                42: SimpleNamespace(name="bob"),
            }
        )
        classifier = SimpleNamespace(store=store)
        ft = _fresh_face_tracker(real=True, face_id_classifier=classifier)
        a = ft.get_face_id("alice")
        b = ft.get_face_id("bob")
        c = ft.get_face_id("charlie")  # 不在 store → fallback sha1
        sha1_charlie = "fid_" + hashlib.sha1("charlie".encode("utf-8")).hexdigest()[:8]
        ok = (
            a == "fid_7"
            and b == "fid_42"
            and c == sha1_charlie
        )
        _record(
            "V10 classifier-aware face_id 来自 store user_id",
            ok,
            f"a={a!r} b={b!r} c={c!r}",
        )
    except Exception as e:  # noqa: BLE001
        _record("V10 classifier-aware path", False, f"exception {e!r}")


def main() -> int:
    for fn in (
        v1_default_off,
        v2_stable_id_on,
        v3_distinct_ids,
        v4_group_mode_resolver_face_id_path,
        v5_gate_off_compat,
        v6_emit_face_id_resolved,
        v7_schema_backcompat,
        v8_marker_in_source,
        v9_fixture_two_faces,
        v10_classifier_aware_path,
    ):
        # 每个测试自治清理 env
        os.environ.pop("COCO_FACE_ID_REAL", None)
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _record(fn.__name__, False, f"raised {type(e).__name__}: {e}")
    os.environ.pop("COCO_FACE_ID_REAL", None)

    ok_all = all(r["ok"] for r in _results)
    summary = {
        "feature": "vision-008",
        "ok": ok_all,
        "results": _results,
    }
    out_dir = ROOT / "evidence" / "vision-008"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print(
        f"[verify_vision_008] {'ALL PASS' if ok_all else 'FAILED'}"
        f" {sum(1 for r in _results if r['ok'])}/{len(_results)}"
    )
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
