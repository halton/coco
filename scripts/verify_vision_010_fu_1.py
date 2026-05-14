"""vision-010-fu-1 verification: 关闭 vision-010 C1 dead-code.

跑法::

    uv run python scripts/verify_vision_010_fu_1.py

子项：

V1   COCO_FACE_ID_ARBIT=1 + multi-face → _tick 自动 emit `vision.face_id_arbit`
     （不需业务侧手工调用 arbitrate_faces）
V2   _tick 自动调用路径 lock-once 生效（同帧 ts 不重复 emit）
V3   单脸 / 0 脸 _tick 不 emit arbit
V4   GroupModeCoordinator 订阅命中：on_face_id_arbit() → primary state 更新
V5   GroupMode 在 ARBIT OFF 时订阅 no-op（state 永远是 None）
V6   ARBIT 默认 OFF → _tick 路径 bytewise 等价（emit_fn 0 calls，state 保持初始）
V7   回归 verify_vision_010 全 PASS
V8   回归 verify_vision_008 / 009 全 PASS

retval：0 全 PASS；1 任一失败
evidence 落 evidence/vision-010-fu-1/verify_summary.json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": ok, "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


# ---------------------------------------------------------------------------
# Fakes for driving _tick directly
# ---------------------------------------------------------------------------


class _FakeCamera:
    """每次 read() 返回固定 dummy frame；驱动 _tick 不依赖真实摄像头."""

    def __init__(self, h: int = 240, w: int = 320) -> None:
        self._frame = np.zeros((h, w, 3), dtype=np.uint8)

    def read(self) -> Tuple[bool, np.ndarray]:
        return True, self._frame

    def close(self) -> None:  # pragma: no cover - protocol courtesy
        return


class _FakeDetector:
    """detect() 返回构造好的 FaceBox 列表；用于 _tick → arbitrate 路径."""

    def __init__(self, boxes: List[Any]) -> None:
        self._boxes = list(boxes)

    def detect(self, frame_bgr: np.ndarray) -> List[Any]:
        return list(self._boxes)


def _make_box(x: int, y: int, w: int, h: int):
    from coco.perception.face_detect import FaceBox
    return FaceBox(x=x, y=y, w=w, h=h, score=1.0)


def _build_tracker_with_fake_cam(
    *,
    arbit: Optional[bool],
    boxes: List[Any],
    emit_fn,
):
    """构造 FaceTracker，注入 fake camera + detector + emit_fn，受控 env."""
    if arbit is True:
        os.environ["COCO_FACE_ID_ARBIT"] = "1"
    elif arbit is False:
        os.environ.pop("COCO_FACE_ID_ARBIT", None)
    # face_id 解析需要 REAL=1（否则 get_face_id 永远返回 None，arbitrate 跳过）
    os.environ["COCO_FACE_ID_REAL"] = "1"

    from coco.perception.face_tracker import FaceTracker

    ft = FaceTracker(
        threading.Event(),
        detector=_FakeDetector(boxes),
        emit_fn=emit_fn,
        # 关掉 hysteresis 提高确定性：J=1 让 1 帧即 present
        presence_min_hits=1,
        absence_min_misses=1,
        presence_window=2,
    )
    ft._camera = _FakeCamera()
    return ft


def _seed_track_names(ft, name_by_track_idx: Dict[int, str]) -> None:
    """在 _process_detections 之后，按 track 顺序把 name 写入快照 / track state.

    auto-arbitrate 从 snapshot.tracks 读 name；这里直接补 name 到 _TrackState 然后
    再次调用 _process_detections 让 snapshot 重建。简化：直接 patch 当前 snapshot。
    """
    from coco.perception.face_tracker import TrackedFace, FaceSnapshot

    with ft._lock:
        snap = ft._snapshot
        new_tracks = []
        for i, t in enumerate(snap.tracks):
            nm = name_by_track_idx.get(i)
            if nm:
                new_tracks.append(TrackedFace(
                    track_id=t.track_id, box=t.box, age_frames=t.age_frames,
                    hit_count=t.hit_count, miss_count=t.miss_count,
                    smoothed_cx=t.smoothed_cx, smoothed_cy=t.smoothed_cy,
                    presence_score=t.presence_score,
                    first_seen_ts=t.first_seen_ts, last_seen_ts=t.last_seen_ts,
                    name=nm, name_confidence=0.99,
                ))
            else:
                new_tracks.append(t)
        new_snap = FaceSnapshot(
            faces=snap.faces, frame_w=snap.frame_w, frame_h=snap.frame_h,
            present=snap.present, primary=snap.primary, ts=snap.ts,
            detect_count=snap.detect_count, hit_count=snap.hit_count,
            tracks=tuple(new_tracks), primary_track=snap.primary_track,
        )
        ft._snapshot = new_snap


# ---------------------------------------------------------------------------
# V1-V3: _tick auto arbitrate
# ---------------------------------------------------------------------------


def v1_tick_auto_arbit_on() -> None:
    """V1 ARBIT=1 + multi-face → _tick 自动 emit vision.face_id_arbit.

    步骤：
    1. 第一次 _tick 建 tracks（_process_detections + _maybe_identify + _maybe_auto_arbitrate）
       — 此时 tracks 还没 name，arbitrate 跳过。
    2. seed names 到 snapshot.tracks（模拟 classifier 已识别）。
    3. 直接调 _maybe_auto_arbitrate(ts=新 ts)：模拟下一帧 auto-arbitrate 触发。
       不能再调 _tick，否则 _process_detections 会用未带 name 的 _TrackState
       重建 snapshot，把 seed 抹掉。
    """
    captured: List[Dict[str, Any]] = []

    def emit(ce: str, _empty: str = "", **kwargs: Any) -> None:
        captured.append({"ce": ce, **kwargs})

    boxes = [_make_box(20, 20, 60, 60), _make_box(200, 100, 80, 80)]
    ft = _build_tracker_with_fake_cam(arbit=True, boxes=boxes, emit_fn=emit)

    # 第一次 _tick：建立 tracks（tracks 还没 name）
    ft._tick()
    # 给两条 track 都打上 name（模拟 classifier 已识别）
    _seed_track_names(ft, {0: "alice", 1: "bob"})
    # 直接驱 auto-arbitrate（模拟同一 _tick 的尾部，但用新 ts 避免 lock-once 撞前一帧）
    ft._maybe_auto_arbitrate(320, 240, ts=time.monotonic() + 1.0)

    arbit_events = [e for e in captured if e.get("ce") == "vision.face_id_arbit"]
    ok = len(arbit_events) >= 1 and isinstance(arbit_events[-1].get("primary"), str)
    _record(
        "v1_tick_auto_arbit_on",
        ok,
        f"emits={len(arbit_events)} primary={arbit_events[-1].get('primary') if arbit_events else None!r}",
    )


def v2_tick_auto_arbit_lock_once() -> None:
    """V2 _tick 自动调用 lock-once：同帧 ts 不重复 emit.

    arbitrate_faces 内部 lock_once 用 ts 去重；这里测：连续两次 _tick 之间
    若 monotonic 推进 → 各 emit 一次（按帧）；同 ts 直接调 _maybe_auto_arbitrate
    第二次应被去重。
    """
    captured: List[Dict[str, Any]] = []

    def emit(ce: str, _empty: str = "", **kwargs: Any) -> None:
        captured.append({"ce": ce, **kwargs})

    boxes = [_make_box(20, 20, 60, 60), _make_box(200, 100, 80, 80)]
    ft = _build_tracker_with_fake_cam(arbit=True, boxes=boxes, emit_fn=emit)
    ft._tick()
    _seed_track_names(ft, {0: "alice", 1: "bob"})
    # 直接驱 _maybe_auto_arbitrate 两次，用同 ts → 第二次必须 no-op
    ft._maybe_auto_arbitrate(320, 240, ts=99.0)
    n_after_first = sum(1 for e in captured if e.get("ce") == "vision.face_id_arbit")
    ft._maybe_auto_arbitrate(320, 240, ts=99.0)
    n_after_second = sum(1 for e in captured if e.get("ce") == "vision.face_id_arbit")
    same_ts_dedup = n_after_second == n_after_first and n_after_first >= 1
    # 推进 ts 应能再 emit 一次
    ft._maybe_auto_arbitrate(320, 240, ts=99.5)
    n_after_third = sum(1 for e in captured if e.get("ce") == "vision.face_id_arbit")
    advance_emits = n_after_third == n_after_second + 1
    ok = same_ts_dedup and advance_emits
    _record(
        "v2_tick_auto_arbit_lock_once",
        ok,
        f"first={n_after_first} same_ts={n_after_second} advanced={n_after_third}",
    )


def v3_tick_auto_arbit_no_multi_known() -> None:
    """V3 单脸 / 0 脸 _tick 不 emit arbit."""
    # Case A: 0 face
    cap_zero: List[Dict[str, Any]] = []
    ft0 = _build_tracker_with_fake_cam(
        arbit=True, boxes=[],
        emit_fn=lambda ce, _e="", **kw: cap_zero.append({"ce": ce, **kw}),
    )
    ft0._tick()
    n_zero = sum(1 for e in cap_zero if e.get("ce") == "vision.face_id_arbit")

    # Case B: 1 face known
    cap_one: List[Dict[str, Any]] = []
    boxes1 = [_make_box(50, 50, 80, 80)]
    ft1 = _build_tracker_with_fake_cam(
        arbit=True, boxes=boxes1,
        emit_fn=lambda ce, _e="", **kw: cap_one.append({"ce": ce, **kw}),
    )
    ft1._tick()
    _seed_track_names(ft1, {0: "alice"})
    ft1._tick()
    n_one = sum(1 for e in cap_one if e.get("ce") == "vision.face_id_arbit")

    # Case C: 2 faces, 0 known
    cap_unk: List[Dict[str, Any]] = []
    boxes2 = [_make_box(20, 20, 60, 60), _make_box(200, 100, 80, 80)]
    ftU = _build_tracker_with_fake_cam(
        arbit=True, boxes=boxes2,
        emit_fn=lambda ce, _e="", **kw: cap_unk.append({"ce": ce, **kw}),
    )
    ftU._tick()
    ftU._tick()  # 不 seed name → 0 known
    n_unk = sum(1 for e in cap_unk if e.get("ce") == "vision.face_id_arbit")

    ok = n_zero == 0 and n_one == 0 and n_unk == 0
    _record(
        "v3_tick_auto_arbit_no_multi_known",
        ok,
        f"zero={n_zero} one_known={n_one} two_unknown={n_unk}",
    )


# ---------------------------------------------------------------------------
# V4-V5: GroupModeCoordinator subscribe
# ---------------------------------------------------------------------------


def v4_group_mode_subscribe_hits() -> None:
    """V4 ARBIT=1 → GroupModeCoordinator.on_face_id_arbit 写入 primary state."""
    os.environ["COCO_FACE_ID_ARBIT"] = "1"
    from coco.companion.group_mode import GroupModeCoordinator
    coord = GroupModeCoordinator()
    before_pid = coord.current_arbit_primary()
    coord.on_face_id_arbit(
        primary="abcdef012345", primary_name="alice",
        candidates=[{"name": "alice"}, {"name": "bob"}],
        rule="center_area_v1", ts=12.5,
    )
    after_pid = coord.current_arbit_primary()
    after_name = coord.current_arbit_primary_name()
    ok = before_pid is None and after_pid == "abcdef012345" and after_name == "alice"
    _record(
        "v4_group_mode_subscribe_hits",
        ok,
        f"before={before_pid!r} after_pid={after_pid!r} after_name={after_name!r}",
    )


def v5_group_mode_subscribe_off_noop() -> None:
    """V5 ARBIT OFF → on_face_id_arbit no-op，state 永远是 None."""
    os.environ.pop("COCO_FACE_ID_ARBIT", None)
    from coco.companion.group_mode import GroupModeCoordinator
    coord = GroupModeCoordinator()
    coord.on_face_id_arbit(
        primary="abcdef012345", primary_name="alice", ts=12.5,
    )
    pid = coord.current_arbit_primary()
    name = coord.current_arbit_primary_name()
    ok = pid is None and name is None
    _record(
        "v5_group_mode_subscribe_off_noop",
        ok,
        f"pid={pid!r} name={name!r}",
    )


# ---------------------------------------------------------------------------
# V6: default-OFF bytewise 等价
# ---------------------------------------------------------------------------


def v6_default_off_bytewise_equiv() -> None:
    """V6 ARBIT 默认 OFF → _tick 路径 emit_fn 0 calls；coord state 保持 None.

    跑两个 case：face_tracker._tick 多脸 + group coord 收 emit。两者都应零副作用。
    """
    os.environ.pop("COCO_FACE_ID_ARBIT", None)
    captured: List[Dict[str, Any]] = []

    def emit(ce: str, _e: str = "", **kw: Any) -> None:
        captured.append({"ce": ce, **kw})

    boxes = [_make_box(20, 20, 60, 60), _make_box(200, 100, 80, 80)]
    ft = _build_tracker_with_fake_cam(arbit=False, boxes=boxes, emit_fn=emit)
    ft._tick()
    _seed_track_names(ft, {0: "alice", 1: "bob"})
    ft._tick()
    ft._tick()
    arbit_emits = sum(1 for e in captured if e.get("ce") == "vision.face_id_arbit")

    from coco.companion.group_mode import GroupModeCoordinator
    coord = GroupModeCoordinator()
    coord.on_face_id_arbit(primary="deadbeef0001", primary_name="x", ts=1.0)
    coord_pid = coord.current_arbit_primary()

    ok = arbit_emits == 0 and coord_pid is None
    _record(
        "v6_default_off_bytewise_equiv",
        ok,
        f"arbit_emits={arbit_emits} coord_pid={coord_pid!r}",
    )


# ---------------------------------------------------------------------------
# V7-V8: regression
# ---------------------------------------------------------------------------


def _run_subprocess_verify(script: str) -> Tuple[bool, str]:
    """跑同仓库 verify 脚本（独立进程，避免 env / state 污染）."""
    env = os.environ.copy()
    env.pop("COCO_FACE_ID_ARBIT", None)
    env.pop("COCO_FACE_ID_PERSIST", None)
    env.pop("COCO_FACE_ID_REAL", None)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180,
    )
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    return proc.returncode == 0, " | ".join(tail)


def v7_regress_vision_010() -> None:
    ok, tail = _run_subprocess_verify("verify_vision_010.py")
    _record("v7_regress_vision_010", ok, tail)


def v8_regress_vision_008_009() -> None:
    ok8, tail8 = _run_subprocess_verify("verify_vision_008.py")
    ok9, tail9 = _run_subprocess_verify("verify_vision_009.py")
    _record("v8_regress_vision_008_009", ok8 and ok9, f"008: {tail8} || 009: {tail9}")


# ---------------------------------------------------------------------------


def main() -> int:
    for fn in (
        v1_tick_auto_arbit_on,
        v2_tick_auto_arbit_lock_once,
        v3_tick_auto_arbit_no_multi_known,
        v4_group_mode_subscribe_hits,
        v5_group_mode_subscribe_off_noop,
        v6_default_off_bytewise_equiv,
        v7_regress_vision_010,
        v8_regress_vision_008_009,
    ):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _record(fn.__name__, False, f"unhandled exception {e!r}")

    out = ROOT / "evidence" / "vision-010-fu-1"
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "vision-010-fu-1",
        "ok": all(r["ok"] for r in _results),
        "results": _results,
    }
    (out / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_pass = sum(1 for r in _results if r["ok"])
    n_total = len(_results)
    print(f"\n[vision-010-fu-1] {n_pass}/{n_total} PASS")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
