"""vision-009 verification: face_tracker emit_fn wire + classifier 分歧 lock-once.

跑法::

    uv run python scripts/verify_vision_009.py

子项：

V1   main.py 在 FaceTracker 构造时把 ``emit_fn=emit`` 真注入（AST/grep marker）
V2   gate ON 首次解析 emit "vision.face_id_resolved"（schema 含
     name / face_id / source）被订阅者 (fake_emit) 收到
V3   emit_fn=None fallback 静默 — gate ON 但未注入 emit_fn 不抛异常
V4   classifier 后注入分歧锁定：一旦 sha1 绑定，后续注入 classifier
     不重绑（lock-once policy）+ stats.face_id_classifier_late_inject_skipped 计数
V5   classifier 先绑 fid_<user_id>，运行期 classifier 置 None / store 清空
     时该 name 不退到 sha1 fallback（lock-once 反向）
V6   emit once-per-name：同 name 多次解析仅 emit 一次（重复抑制）
V7   docstring：TrackedFace.name_confidence 与 face_id 正交关系在
     face_tracker.py 中明确写下（grep marker）
V8   gate OFF 主路径零开销不 emit；get_face_id 返回 None；emit_fn 不被调用
V9   回归 verify_vision_008.py 10/10 仍通过（spawn subprocess）

retval：0 全 PASS；1 任一失败
evidence 落 evidence/vision-009/verify_summary.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": ok, "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def _fresh_face_tracker(*, real: Optional[bool], **kwargs):
    if real is True:
        os.environ["COCO_FACE_ID_REAL"] = "1"
    elif real is False:
        os.environ.pop("COCO_FACE_ID_REAL", None)
    from coco.perception.face_tracker import FaceTracker
    return FaceTracker(threading.Event(), **kwargs)


# ---------------------------------------------------------------------------

def v1_main_wires_emit_fn() -> None:
    """V1 main.py 在 FaceTracker 构造时把 emit_fn=emit 真注入。"""
    src = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    # 找 _FaceTracker( ... emit_fn=emit ... ) 块
    # 容忍跨行；用 regex 匹配 _FaceTracker 调用块内出现 emit_fn=emit
    m = re.search(
        r"_FaceTracker\s*\(\s*[^)]*?emit_fn\s*=\s*emit[^)]*\)",
        src,
        re.DOTALL,
    )
    has_marker = "vision-009" in src
    _record(
        "V1 main.py FaceTracker(emit_fn=emit) wire + vision-009 marker",
        m is not None and has_marker,
        f"wire_match={bool(m)} marker={has_marker}",
    )


def v2_gate_on_emit_payload() -> None:
    """V2 gate ON 首次解析 emit 'vision.face_id_resolved' 被订阅者收到。"""
    captured: List[Dict[str, Any]] = []

    def fake_emit(component_event: str, message: str = "", **payload: Any) -> None:
        # 模拟 logging_setup.emit 签名：拆 component / event
        if "." in component_event:
            comp, ev = component_event.split(".", 1)
        else:
            comp, ev = component_event, "event"
        captured.append({"component": comp, "event": ev, "message": message, **payload})

    ft = _fresh_face_tracker(real=True, emit_fn=fake_emit)
    fid = ft.get_face_id("alice")
    ok = (
        len(captured) == 1
        and captured[0]["component"] == "vision"
        and captured[0]["event"] == "face_id_resolved"
        and captured[0].get("name") == "alice"
        and isinstance(captured[0].get("face_id"), str)
        and captured[0]["face_id"].startswith("fid_")
        and captured[0].get("source") in ("classifier", "sha1")
        and fid == captured[0]["face_id"]
    )
    _record(
        "V2 gate ON emit vision.face_id_resolved schema + payload",
        ok,
        f"captured={captured} fid={fid!r}",
    )


def v3_emit_fn_none_silent() -> None:
    """V3 gate ON 但 emit_fn=None 时 fallback 静默（不抛）。"""
    try:
        ft = _fresh_face_tracker(real=True)  # 不传 emit_fn → 默认 None
        fid = ft.get_face_id("alice")
        ok = isinstance(fid, str) and fid.startswith("fid_")
        _record(
            "V3 emit_fn=None gate ON 静默 fallback",
            ok,
            f"fid={fid!r}",
        )
    except Exception as e:  # noqa: BLE001
        _record("V3 emit_fn=None gate ON 静默 fallback", False, f"exception {e!r}")


def v4_late_classifier_inject_locked() -> None:
    """V4 sha1 已绑后再注入 classifier 不重绑（lock-once）+ stats 计数。"""
    ft = _fresh_face_tracker(real=True)
    # 第一次解析 alice：无 classifier，走 sha1
    fid_sha1 = ft.get_face_id("alice")
    expect_sha1 = "fid_" + hashlib.sha1(b"alice").hexdigest()[:8]
    # 现在后注入 classifier（store: alice→user_id=7）
    store = SimpleNamespace(
        all_records=lambda: {7: SimpleNamespace(name="alice")}
    )
    ft._face_id_classifier = SimpleNamespace(store=store)  # 模拟运行期注入
    # 再解析 alice：lock-once → 仍 fid_sha1，不应变成 fid_7
    fid_after = ft.get_face_id("alice")
    fid_after2 = ft.get_face_id("alice")  # 多次访问触发多次 skip 计数
    skipped = ft.stats.face_id_classifier_late_inject_skipped
    ok = (
        fid_sha1 == expect_sha1
        and fid_after == fid_sha1
        and fid_after != "fid_7"
        and skipped >= 2  # 两次后续访问都计数
    )
    _record(
        "V4 sha1 已绑后 classifier 注入 lock-once + 计数",
        ok,
        f"sha1={fid_sha1!r} after={fid_after!r} after2={fid_after2!r} skipped={skipped}",
    )


def v5_classifier_failed_no_degrade() -> None:
    """V5 fid_<user_id> 已绑，classifier 失效 / store 清空时该 name 不退到 sha1。"""
    store = SimpleNamespace(
        all_records=lambda: {7: SimpleNamespace(name="alice")}
    )
    classifier = SimpleNamespace(store=store)
    ft = _fresh_face_tracker(real=True, face_id_classifier=classifier)
    fid_first = ft.get_face_id("alice")
    expect = "fid_7"
    # 模拟 classifier 失效（置 None）
    ft._face_id_classifier = None
    fid_after_none = ft.get_face_id("alice")
    # 模拟 store 清空再注入
    empty_store = SimpleNamespace(all_records=lambda: {})
    ft._face_id_classifier = SimpleNamespace(store=empty_store)
    fid_after_empty = ft.get_face_id("alice")
    ok = (
        fid_first == expect
        and fid_after_none == expect
        and fid_after_empty == expect
    )
    _record(
        "V5 fid_<user_id> 已绑 classifier 失效不退到 sha1 (lock-once 反向)",
        ok,
        f"first={fid_first!r} after_none={fid_after_none!r} after_empty={fid_after_empty!r}",
    )


def v6_emit_once_per_name() -> None:
    """V6 同 name 多次解析 emit 仅一次（重复抑制）。"""
    captured: List[Dict[str, Any]] = []

    def fake_emit(component_event: str, message: str = "", **payload: Any) -> None:
        captured.append({"ce": component_event, **payload})

    ft = _fresh_face_tracker(real=True, emit_fn=fake_emit)
    a1 = ft.get_face_id("alice")
    a2 = ft.get_face_id("alice")
    a3 = ft.get_face_id("alice")
    b1 = ft.get_face_id("bob")
    ok = (
        sum(1 for e in captured if e.get("name") == "alice") == 1
        and sum(1 for e in captured if e.get("name") == "bob") == 1
        and len(captured) == 2
        and a1 == a2 == a3
    )
    _record(
        "V6 emit once-per-name 重复抑制",
        ok,
        f"captured_len={len(captured)} alice_eq={a1==a2==a3} b1={b1!r}",
    )


def v7_docstring_orthogonal_marker() -> None:
    """V7 face_tracker.py docstring 写明 name_confidence 与 face_id 正交。"""
    src = (ROOT / "coco" / "perception" / "face_tracker.py").read_text(encoding="utf-8")
    # 必含关键词：name_confidence + face_id + 正交（或独立读路径同义）
    has_name_conf = "name_confidence" in src
    has_face_id_term = "face_id" in src
    has_orth = "正交" in src or "独立读路径" in src
    has_v009_marker = "vision-009" in src
    ok = has_name_conf and has_face_id_term and has_orth and has_v009_marker
    _record(
        "V7 docstring name_confidence ⊥ face_id + vision-009 marker",
        ok,
        f"name_conf={has_name_conf} face_id={has_face_id_term} orth={has_orth} marker={has_v009_marker}",
    )


def v8_gate_off_no_emit() -> None:
    """V8 gate OFF 主路径零开销：get_face_id 返回 None，emit_fn 永不被调用。"""
    captured: List[Dict[str, Any]] = []

    def fake_emit(component_event: str, message: str = "", **payload: Any) -> None:
        captured.append({"ce": component_event, **payload})

    ft = _fresh_face_tracker(real=False, emit_fn=fake_emit)
    r1 = ft.get_face_id("alice")
    r2 = ft.get_face_id("bob")
    r3 = ft.get_face_id("alice")
    ok = r1 is None and r2 is None and r3 is None and len(captured) == 0
    _record(
        "V8 gate OFF get_face_id=None + emit_fn 0 calls",
        ok,
        f"r1={r1!r} r2={r2!r} r3={r3!r} captured_len={len(captured)}",
    )


def v9_regress_vision_008() -> None:
    """V9 回归 verify_vision_008.py 10/10。"""
    script = ROOT / "scripts" / "verify_vision_008.py"
    if not script.exists():
        _record("V9 regress verify_vision_008", False, "script missing")
        return
    try:
        # 在干净 env 子进程跑，避免本进程 env 污染
        env = os.environ.copy()
        env.pop("COCO_FACE_ID_REAL", None)
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        ok = proc.returncode == 0
        tail = (proc.stdout or "").splitlines()[-3:]
        _record(
            "V9 regress verify_vision_008 (10/10)",
            ok,
            f"rc={proc.returncode} tail={tail}",
        )
    except Exception as e:  # noqa: BLE001
        _record("V9 regress verify_vision_008", False, f"exception {e!r}")


# ---------------------------------------------------------------------------

def main() -> int:
    for fn in (
        v1_main_wires_emit_fn,
        v2_gate_on_emit_payload,
        v3_emit_fn_none_silent,
        v4_late_classifier_inject_locked,
        v5_classifier_failed_no_degrade,
        v6_emit_once_per_name,
        v7_docstring_orthogonal_marker,
        v8_gate_off_no_emit,
        v9_regress_vision_008,
    ):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _record(fn.__name__, False, f"unhandled exception {e!r}")

    out = ROOT / "evidence" / "vision-009"
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "vision-009",
        "ok": all(r["ok"] for r in _results),
        "results": _results,
    }
    (out / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_pass = sum(1 for r in _results if r["ok"])
    n_total = len(_results)
    print(f"\n[vision-009] {n_pass}/{n_total} PASS")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
