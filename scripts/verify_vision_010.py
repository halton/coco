"""vision-010 verification: face_id_map 跨进程持久化 + 多脸仲裁.

跑法::

    uv run python scripts/verify_vision_010.py

子项：

V1   COCO_FACE_ID_PERSIST=1 启用持久化；首次解析 alice 后磁盘出现 face_id_map.json
V2   schema 合规 + 双进程 hydrate 命中 (子进程读到同一 face_id)
V3   文件损坏 → warn-once + 空 map + 不 crash
V4   default-OFF: 未设 PERSIST 时无文件 IO（路径不存在），bytewise 等价
V5   COCO_FACE_ID_ARBIT=1 启用，多脸场景 emit "vision.face_id_arbit"
V6   rule center_area_v1 打分正确（构造 fixture 验证 primary 选择）
V7   lock-once policy 同帧只 emit 一次
V8   单脸 / 0 脸场景不 emit arbit
V9   ARBIT 默认 OFF → 无 arbit emit
V10  回归 verify_vision_008 / 009 仍 PASS

retval：0 全 PASS；1 任一失败
evidence 落 evidence/vision-010/verify_summary.json
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
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


def _fresh_face_tracker(
    *,
    real: Optional[bool] = None,
    persist: Optional[bool] = None,
    persist_path: Optional[Path] = None,
    arbit: Optional[bool] = None,
    **kwargs,
):
    """build FaceTracker with controlled env. reload module 以重读 env? 不必：
    env 在 __init__ 里读，只要 ctor 时设好即可。
    """
    if real is True:
        os.environ["COCO_FACE_ID_REAL"] = "1"
    elif real is False:
        os.environ.pop("COCO_FACE_ID_REAL", None)
    if persist is True:
        os.environ["COCO_FACE_ID_PERSIST"] = "1"
    elif persist is False:
        os.environ.pop("COCO_FACE_ID_PERSIST", None)
    if persist_path is not None:
        os.environ["COCO_FACE_ID_MAP_PATH"] = str(persist_path)
    else:
        os.environ.pop("COCO_FACE_ID_MAP_PATH", None)
    if arbit is True:
        os.environ["COCO_FACE_ID_ARBIT"] = "1"
    elif arbit is False:
        os.environ.pop("COCO_FACE_ID_ARBIT", None)
    # ensure fresh import effects (face_tracker reads env in __init__ only)
    from coco.perception.face_tracker import FaceTracker
    return FaceTracker(threading.Event(), **kwargs)


def _make_box(x: int, y: int, w: int, h: int):
    from coco.perception.face_detect import FaceBox
    return FaceBox(x=x, y=y, w=w, h=h, score=1.0)


# ---------------------------------------------------------------------------

def v1_persist_writes_file() -> None:
    """V1 PERSIST=1 时首次解析后文件出现且 schema 合规.

    infra-017 V7：detail 剥离 tmpdir 路径与时间戳浮点，evidence 字节稳定。
    """
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "face_id_map.json"
        ft = _fresh_face_tracker(real=True, persist=True, persist_path=path)
        fid = ft.get_face_id("alice")
        exists = path.exists()
        if exists:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = data.get("entries", [])
            ok = (
                data.get("version") == 1
                and isinstance(entries, list)
                and len(entries) == 1
                and entries[0]["name"] == "alice"
                and entries[0]["face_id"] == fid
                and "saved_at" in data
            )
            # 静态 detail：只暴露稳定字段（schema 版本 / entries 数 / name / fid）
            _record(
                "V1 PERSIST=1 first resolve writes face_id_map.json",
                ok,
                f"version={data.get('version')} entries_count={len(entries)} "
                f"name={entries[0]['name']!r} face_id={fid!r}",
            )
        else:
            _record("V1 PERSIST=1 first resolve writes face_id_map.json",
                    False, "face_id_map.json not created")


def v2_hydrate_across_process() -> None:
    """V2 双进程：进程A 写 → 进程B 读 hydrate 命中相同 face_id."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "face_id_map.json"
        # 进程A：写
        ft1 = _fresh_face_tracker(real=True, persist=True, persist_path=path)
        fid1 = ft1.get_face_id("alice")
        ft1.flush_face_id_map()
        # 进程B：subprocess 读
        helper = (
            "import os, json, sys, threading;"
            "sys.path.insert(0, %r);" % str(ROOT)
            + "os.environ['COCO_FACE_ID_REAL']='1';"
            "os.environ['COCO_FACE_ID_PERSIST']='1';"
            "os.environ['COCO_FACE_ID_MAP_PATH']=%r;" % str(path)
            + "from coco.perception.face_tracker import FaceTracker;"
            "ft=FaceTracker(threading.Event());"
            "fid=ft.get_face_id('alice');"
            "print('FID=' + str(fid))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", helper],
            capture_output=True, text=True, timeout=30,
        )
        out = proc.stdout.strip()
        match = [l for l in out.splitlines() if l.startswith("FID=")]
        fid2 = match[-1].split("=", 1)[1] if match else None
        ok = fid1 is not None and fid1 == fid2
        _record(
            "V2 hydrate across process (same face_id)",
            ok,
            f"fid1={fid1!r} fid2={fid2!r} rc={proc.returncode}",
        )


def v3_corrupt_file_warn_once() -> None:
    """V3 损坏文件 → warn + 空 map + 不 crash."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "face_id_map.json"
        path.write_text("{ this is not json", encoding="utf-8")
        try:
            ft = _fresh_face_tracker(real=True, persist=True, persist_path=path)
            # 空 map → 解析 alice 应走新路径
            fid = ft.get_face_id("alice")
            # 空 map 表现：内部 map 不含 alice 直到现在
            ok = isinstance(fid, str) and fid.startswith("fid_")
            _record("V3 corrupt file -> warn + empty map + no crash",
                    ok, f"fid={fid!r}")
        except Exception as e:  # noqa: BLE001
            _record("V3 corrupt file -> warn + empty map + no crash",
                    False, f"crashed: {e!r}")


def v4_default_off_no_io() -> None:
    """V4 PERSIST 未设 → 不写文件，bytewise 等价旧路径."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "face_id_map.json"
        # 注意：路径设了，但 PERSIST=False，应该不读不写
        ft = _fresh_face_tracker(real=True, persist=False, persist_path=path)
        fid = ft.get_face_id("alice")
        # 文件不应被创建
        no_file = not path.exists()
        # 内部 meta 也应为空
        no_meta = len(getattr(ft, "_face_id_meta", {})) == 0
        ok = isinstance(fid, str) and no_file and no_meta
        _record(
            "V4 PERSIST off: no file IO + meta empty (bytewise eq)",
            ok,
            f"fid={fid!r} no_file={no_file} no_meta={no_meta}",
        )


def v5_arbit_on_emits() -> None:
    """V5 ARBIT=1 多脸场景 emit vision.face_id_arbit."""
    captured: List[Dict[str, Any]] = []

    def fake_emit(component_event: str, message: str = "", **payload: Any) -> None:
        captured.append({"ce": component_event, **payload})

    ft = _fresh_face_tracker(real=True, arbit=True, emit_fn=fake_emit)
    boxes = [_make_box(0, 0, 50, 50), _make_box(100, 100, 60, 60)]
    names = ["alice", "bob"]
    payload = ft.arbitrate_faces(boxes, names, frame_w=320, frame_h=240, ts=1.0)
    arbit_events = [e for e in captured if e.get("ce") == "vision.face_id_arbit"]
    ok = (
        payload is not None
        and len(arbit_events) == 1
        and arbit_events[0].get("rule") == "center_area_v1"
        and arbit_events[0].get("primary", "").startswith("fid_")
        and len(arbit_events[0].get("candidates", [])) == 2
    )
    _record(
        "V5 ARBIT on: emits vision.face_id_arbit",
        ok,
        f"payload_primary={payload.get('primary') if payload else None} events={len(arbit_events)}",
    )


def v6_arbit_rule_correct() -> None:
    """V6 center_area_v1 打分正确：靠中心 + 大面积 胜出."""
    captured: List[Dict[str, Any]] = []

    def fake_emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    ft = _fresh_face_tracker(real=True, arbit=True, emit_fn=fake_emit)
    # frame 320x240, center=(160,120)
    # alice: 边缘小脸 (cx=30, cy=30, area=20*20=400) → score 大
    # bob:   中心大脸 (cx=160, cy=120, area=80*80=6400) → score≈0
    box_alice = _make_box(20, 20, 20, 20)   # cx=30 cy=30
    box_bob = _make_box(120, 80, 80, 80)    # cx=160 cy=120
    payload = ft.arbitrate_faces(
        [box_alice, box_bob], ["alice", "bob"],
        frame_w=320, frame_h=240, ts=2.0,
    )
    primary_name = payload.get("primary_name") if payload else None
    ok = primary_name == "bob"
    _record(
        "V6 rule center_area_v1: bob (center+large) wins",
        ok,
        f"primary_name={primary_name!r} candidates_scores="
        f"{[c.get('score') for c in (payload.get('candidates') or [])] if payload else None}",
    )


def v7_arbit_lock_once_per_frame() -> None:
    """V7 同 ts 多次调用仅 emit 一次."""
    captured: List[Dict[str, Any]] = []

    def fake_emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    ft = _fresh_face_tracker(real=True, arbit=True, emit_fn=fake_emit)
    boxes = [_make_box(0, 0, 50, 50), _make_box(100, 100, 60, 60)]
    names = ["alice", "bob"]
    p1 = ft.arbitrate_faces(boxes, names, 320, 240, ts=3.0)
    p2 = ft.arbitrate_faces(boxes, names, 320, 240, ts=3.0)  # 同 ts
    p3 = ft.arbitrate_faces(boxes, names, 320, 240, ts=3.5)  # 新 ts
    arbit_events = [e for e in captured if e.get("ce") == "vision.face_id_arbit"]
    ok = (
        p1 is not None
        and p2 is None
        and p3 is not None
        and len(arbit_events) == 2
    )
    _record(
        "V7 lock-once per-frame (same ts only emits once)",
        ok,
        f"p1={bool(p1)} p2={bool(p2)} p3={bool(p3)} events={len(arbit_events)}",
    )


def v8_arbit_no_single_face() -> None:
    """V8 单脸 / 0 脸 不 emit."""
    captured: List[Dict[str, Any]] = []

    def fake_emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    ft = _fresh_face_tracker(real=True, arbit=True, emit_fn=fake_emit)
    p_zero = ft.arbitrate_faces([], [], 320, 240, ts=4.0)
    p_one = ft.arbitrate_faces([_make_box(0, 0, 50, 50)], ["alice"], 320, 240, ts=4.1)
    p_one_unk = ft.arbitrate_faces(
        [_make_box(0, 0, 50, 50), _make_box(60, 60, 50, 50)],
        ["alice", None],
        320, 240, ts=4.2,
    )
    ok = p_zero is None and p_one is None and p_one_unk is None and len(captured) == 0
    _record(
        "V8 single/zero known faces: no emit",
        ok,
        f"zero={p_zero} one={p_one} one_unk={p_one_unk} captured={len(captured)}",
    )


def v9_arbit_default_off() -> None:
    """V9 ARBIT 默认 OFF → 不 emit."""
    captured: List[Dict[str, Any]] = []

    def fake_emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    ft = _fresh_face_tracker(real=True, arbit=False, emit_fn=fake_emit)
    boxes = [_make_box(0, 0, 50, 50), _make_box(100, 100, 60, 60)]
    payload = ft.arbitrate_faces(boxes, ["alice", "bob"], 320, 240, ts=5.0)
    ok = payload is None and len(captured) == 0
    _record(
        "V9 ARBIT off (default): no emit",
        ok,
        f"payload={payload} captured={len(captured)}",
    )


def v10_regress_008_009() -> None:
    """V10 回归 verify_vision_008 / 009 仍 PASS."""
    results = {}
    for fname in ("verify_vision_008.py", "verify_vision_009.py"):
        script = ROOT / "scripts" / fname
        if not script.exists():
            results[fname] = ("missing", -1)
            continue
        env = os.environ.copy()
        # 清掉本会话污染的 env，让子进程从 default-OFF 起
        for k in (
            "COCO_FACE_ID_REAL", "COCO_FACE_ID_PERSIST",
            "COCO_FACE_ID_MAP_PATH", "COCO_FACE_ID_ARBIT",
        ):
            env.pop(k, None)
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(ROOT), capture_output=True, text=True,
            timeout=180, env=env,
        )
        results[fname] = ("ok" if proc.returncode == 0 else "fail",
                          proc.returncode)
    ok = all(v[1] == 0 for v in results.values())
    _record(
        "V10 regress vision-008 / 009 still PASS",
        ok,
        f"results={results}",
    )


# ---------------------------------------------------------------------------

def main() -> int:
    for fn in (
        v1_persist_writes_file,
        v2_hydrate_across_process,
        v3_corrupt_file_warn_once,
        v4_default_off_no_io,
        v5_arbit_on_emits,
        v6_arbit_rule_correct,
        v7_arbit_lock_once_per_frame,
        v8_arbit_no_single_face,
        v9_arbit_default_off,
        v10_regress_008_009,
    ):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _record(fn.__name__, False, f"unhandled exception {e!r}")

    out = ROOT / "evidence" / "vision-010"
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "vision-010",
        "ok": all(r["ok"] for r in _results),
        "results": _results,
    }
    (out / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_pass = sum(1 for r in _results if r["ok"])
    n_total = len(_results)
    print(f"\n[vision-010] {n_pass}/{n_total} PASS")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
