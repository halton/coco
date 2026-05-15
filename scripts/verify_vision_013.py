#!/usr/bin/env python3
"""vision-013 verification: _maybe_identify 生产路径 wire + monotonic 时基 + penalty<0 fallback log.

V1 _maybe_identify 端到端: 注入 fake classifier 返回 (name, conf), 跑 _maybe_identify
   断言 _face_id_meta[name]['name_confidence'] 已写入 (record_name_confidence 真接入)
V2 _gc_last_time 用 time.monotonic: monkey-patch monotonic 触发 time_due
V3 NTP 回拨场景: monkey-patch wall clock 回拨, GC time_due 不被影响 (monotonic 单调)
V4 penalty<0 fallback: env=-1 → 实例字段 fallback 到默认 1e6 + log.warning 被触发
V5 regression 子进程跑 verify_vision_012 + verify_vision_011 全 PASS

retval: 0 全 PASS; 1 任一失败
evidence: evidence/vision-013/verify_summary.json
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": ok, "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


_ENV_KEYS = (
    "COCO_FACE_ID_REAL",
    "COCO_FACE_ID_PERSIST",
    "COCO_FACE_ID_ARBIT",
    "COCO_FACE_ID_MAP_GC",
    "COCO_FACE_ID_MAP_PATH",
    "COCO_FACE_ID_MAP_MAX",
    "COCO_FACE_ID_MAP_TTL_DAYS",
    "COCO_FACE_ID_MAP_GC_PERIOD_FRAMES",
    "COCO_FACE_ID_MAP_GC_INTERVAL_FRAMES",
    "COCO_FACE_ID_MAP_GC_INTERVAL_S",
    "COCO_FACE_ID_UNTRUSTED_CONF_THRESHOLD",
    "COCO_FACE_ID_UNTRUSTED_PENALTY",
)


def _clean_env() -> None:
    for k in _ENV_KEYS:
        os.environ.pop(k, None)


def _set_env(**kwargs: Optional[str]) -> None:
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _fresh_tracker(**kwargs: Any):
    from coco.perception.face_tracker import FaceTracker
    return FaceTracker(threading.Event(), **kwargs)


def _make_box(x: int, y: int, w: int, h: int):
    from coco.perception.face_detect import FaceBox
    return FaceBox(x=x, y=y, w=w, h=h, score=1.0)


class _FakeClassifier:
    """Mock face_id_classifier for _maybe_identify tests."""

    def __init__(self, name: str, conf: float) -> None:
        self._name = name
        self._conf = conf
        self.store = None

    def identify(self, crop) -> Tuple[str, float]:  # noqa: ARG002
        return self._name, self._conf


def _seed_primary_snapshot(ft, *, track_id: int = 1, name: Optional[str] = None) -> None:
    """构造一个 primary_track 已确定的 snapshot, 让 _maybe_identify 能跑下去."""
    from coco.perception.face_tracker import FaceSnapshot, TrackedFace
    box = _make_box(10, 10, 30, 30)
    tf = TrackedFace(
        track_id=track_id,
        box=box,
        age_frames=10,
        hit_count=10,
        miss_count=0,
        smoothed_cx=25.0,
        smoothed_cy=25.0,
        presence_score=1.0,
        first_seen_ts=0.0,
        last_seen_ts=0.0,
        name=name,
        name_confidence=0.0,
    )
    snap = FaceSnapshot(
        faces=(box,),
        frame_w=64,
        frame_h=48,
        present=True,
        primary=box,
        ts=time.monotonic(),
        detect_count=1,
        hit_count=1,
        tracks=(tf,),
        primary_track=tf,
    )
    with ft._lock:
        ft._snapshot = snap


# ---------------------------------------------------------------------------
# V1: _maybe_identify 生产路径端到端 wire
# ---------------------------------------------------------------------------

def v1_maybe_identify_wire() -> None:
    """注入 fake classifier 返回 (name='alice', conf=0.42),
    跑 _maybe_identify; 断言:
    - snapshot.primary_track.name == 'alice', name_confidence ≈ 0.42
    - persist 开启时 _face_id_meta['alice']['name_confidence'] ≈ 0.42  ← 关键 wire 点
    """
    import numpy as np
    _clean_env()
    try:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "face_id_map.json"
            _set_env(
                COCO_FACE_ID_REAL="1",
                COCO_FACE_ID_PERSIST="1",
                COCO_FACE_ID_MAP_PATH=str(path),
            )
            ft = _fresh_tracker()
            ft._face_id_classifier = _FakeClassifier("alice", 0.42)
            _seed_primary_snapshot(ft)
            frame = np.zeros((48, 64, 3), dtype=np.uint8)
            ft._maybe_identify(frame, [])
            with ft._lock:
                snap = ft._snapshot
            with ft._face_id_lock:
                meta = dict(ft._face_id_meta)
            pt_name = snap.primary_track.name if snap.primary_track else None
            pt_conf = snap.primary_track.name_confidence if snap.primary_track else None
            alice_rec = meta.get("alice", {})
            recorded_conf = alice_rec.get("name_confidence")
            ok = (
                pt_name == "alice"
                and pt_conf is not None
                and abs(pt_conf - 0.42) < 1e-6
                and "alice" in meta
                and recorded_conf is not None
                and abs(float(recorded_conf) - 0.42) < 1e-6
            )
            _record(
                "V1 _maybe_identify → record_name_confidence 生产路径接入",
                ok,
                f"pt_name={pt_name} pt_conf={pt_conf} "
                f"meta_keys={sorted(meta.keys())} recorded_conf={recorded_conf}",
            )
    finally:
        _clean_env()


# ---------------------------------------------------------------------------
# V2: _gc_last_time 用 time.monotonic
# ---------------------------------------------------------------------------

def v2_monotonic_time_base() -> None:
    """monkey-patch time.monotonic 推进时间, 触发 time_due GC.
    同时 monkey-patch time.time 保持不变, 证明 due 不再依赖 wall clock.
    """
    from coco.perception import face_tracker as ft_mod

    captured: List[Dict[str, Any]] = []

    def emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    _clean_env()
    fake_mono = [1000.0]
    real_mono = time.monotonic
    real_wall = time.time
    wall_t = 5_000_000.0  # 固定 wall clock

    def patched_mono() -> float:
        return fake_mono[0]

    def patched_wall() -> float:
        return wall_t

    try:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "face_id_map.json"
            _set_env(
                COCO_FACE_ID_REAL="1",
                COCO_FACE_ID_PERSIST="1",
                COCO_FACE_ID_MAP_GC="1",
                COCO_FACE_ID_MAP_PATH=str(path),
                COCO_FACE_ID_MAP_GC_PERIOD_FRAMES="1500",  # frame 周期不会触发
                COCO_FACE_ID_MAP_GC_INTERVAL_S="10.0",  # 10s 触发
                COCO_FACE_ID_MAP_TTL_DAYS="30",
            )
            ft = _fresh_tracker(emit_fn=emit)
            # 注入 stale entry, 以便 GC 触发后 emit ttl
            stale_ts = wall_t - 40 * 86400
            with ft._face_id_lock:
                ft._face_id_meta = {
                    "u_stale": {"face_id": "fid_s", "first_seen": stale_ts,
                                "last_seen": stale_ts},
                }
                ft._face_id_map = {"u_stale": "fid_s"}

            ft_mod.time.monotonic = patched_mono  # type: ignore[assignment]
            ft_mod.time.time = patched_wall  # type: ignore[assignment]

            # tick 1: 初始化 _gc_last_time=1000, 不触发
            ft._maybe_periodic_gc()
            ttl_n1 = len([e for e in captured if e.get("reason") == "ttl"])
            last_after_1 = ft._gc_last_time
            # 推进 monotonic 12s → time_due
            fake_mono[0] = 1012.0
            ft._maybe_periodic_gc()
            ttl_n2 = len([e for e in captured if e.get("reason") == "ttl"])
            last_after_2 = ft._gc_last_time

            ok = (
                ttl_n1 == 0
                and ttl_n2 == 1
                and abs((last_after_1 or 0) - 1000.0) < 1e-6
                and abs((last_after_2 or 0) - 1012.0) < 1e-6
                # _gc_last_time 完全是 monotonic 区间, 不接近 wall_t (5e6)
                and (last_after_2 or 0) < 1e5
            )
            _record(
                "V2 _gc_last_time 用 time.monotonic (time_due 由 monotonic 推动)",
                ok,
                f"ttl1={ttl_n1} ttl2={ttl_n2} last1={last_after_1} last2={last_after_2}",
            )
    finally:
        ft_mod.time.monotonic = real_mono  # type: ignore[assignment]
        ft_mod.time.time = real_wall  # type: ignore[assignment]
        _clean_env()


# ---------------------------------------------------------------------------
# V3: NTP 回拨场景 — wall clock 跳回, GC time_due 仍按 monotonic 推进
# ---------------------------------------------------------------------------

def v3_ntp_clock_rollback() -> None:
    """wall clock 跳回 1 小时, monotonic 正常推进; GC time_due 不被回拨影响."""
    from coco.perception import face_tracker as ft_mod

    captured: List[Dict[str, Any]] = []

    def emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    _clean_env()
    real_mono = time.monotonic
    real_wall = time.time
    fake_mono = [2000.0]
    fake_wall = [10_000.0]

    def patched_mono() -> float:
        return fake_mono[0]

    def patched_wall() -> float:
        return fake_wall[0]

    try:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "face_id_map.json"
            _set_env(
                COCO_FACE_ID_REAL="1",
                COCO_FACE_ID_PERSIST="1",
                COCO_FACE_ID_MAP_GC="1",
                COCO_FACE_ID_MAP_PATH=str(path),
                COCO_FACE_ID_MAP_GC_PERIOD_FRAMES="1500",
                COCO_FACE_ID_MAP_GC_INTERVAL_S="5.0",
                COCO_FACE_ID_MAP_TTL_DAYS="0.0001",  # 极短 TTL 让 ttl 也触发
            )
            ft = _fresh_tracker(emit_fn=emit)
            ft_mod.time.monotonic = patched_mono  # type: ignore[assignment]
            ft_mod.time.time = patched_wall  # type: ignore[assignment]

            with ft._face_id_lock:
                ft._face_id_meta = {
                    "u": {"face_id": "fid_u", "first_seen": 0.0,
                          "last_seen": 0.0},
                }
                ft._face_id_map = {"u": "fid_u"}

            # tick 1: 初始化 monotonic last=2000
            ft._maybe_periodic_gc()
            # **NTP 回拨**: wall clock 跳回 1h, monotonic 推进 6s
            fake_wall[0] = 10_000.0 - 3600.0
            fake_mono[0] = 2006.0
            ft._maybe_periodic_gc()
            # 旧实现 (wall): 现 wall - last_wall = -3600-...，永不 >= 5.0
            # 新实现 (monotonic): 2006 - 2000 = 6 >= 5 → 触发
            ttl_n = len([e for e in captured if e.get("reason") == "ttl"])
            last_mono = ft._gc_last_time
            ok = (
                ttl_n >= 1  # GC 在 wall 回拨下仍按 monotonic 触发
                and abs((last_mono or 0) - 2006.0) < 1e-6
            )
            _record(
                "V3 NTP 回拨场景 (wall jump back), monotonic 仍单调推进触发 GC",
                ok,
                f"ttl_events={ttl_n} last_mono={last_mono} wall_now={fake_wall[0]}",
            )
    finally:
        ft_mod.time.monotonic = real_mono  # type: ignore[assignment]
        ft_mod.time.time = real_wall  # type: ignore[assignment]
        _clean_env()


# ---------------------------------------------------------------------------
# V4: penalty<0 fallback 触发 log.warning
# ---------------------------------------------------------------------------

class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def v4_penalty_negative_fallback_log() -> None:
    """COCO_FACE_ID_UNTRUSTED_PENALTY=-1.5 → 实例字段 fallback 到默认 1e6
    + log.warning 含 'invalid' / 'fallback' 关键词被触发."""
    _clean_env()
    logger = logging.getLogger("coco.perception.face_tracker")
    handler = _ListHandler()
    logger.addHandler(handler)
    prev_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "face_id_map.json"
            _set_env(
                COCO_FACE_ID_REAL="1",
                COCO_FACE_ID_PERSIST="1",
                COCO_FACE_ID_MAP_PATH=str(path),
                COCO_FACE_ID_UNTRUSTED_PENALTY="-1.5",
            )
            ft = _fresh_tracker()
            penalty_field = ft._face_id_untrusted_penalty
            warn_records = [
                r for r in handler.records
                if r.levelno == logging.WARNING
                and "PENALTY" in r.getMessage().upper()
                and "fallback" in r.getMessage().lower()
            ]
            ok = (
                abs(penalty_field - 1e6) < 1e-3
                and len(warn_records) >= 1
            )
            _record(
                "V4 penalty<0 → fallback 默认 + log.warning 提示",
                ok,
                f"field={penalty_field} warn_count={len(warn_records)} "
                f"sample={warn_records[0].getMessage() if warn_records else '<none>'}",
            )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)
        _clean_env()


# ---------------------------------------------------------------------------
# V5: regression 子进程跑 verify_vision_012 + verify_vision_011
# ---------------------------------------------------------------------------

def v5_regression() -> None:
    _clean_env()
    env = dict(os.environ)
    for k in _ENV_KEYS:
        env.pop(k, None)
    results = []
    for script in ("verify_vision_012.py", "verify_vision_011.py"):
        rc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / script)],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        results.append((script, rc.returncode))
        if rc.returncode != 0:
            print(f"  [{script} STDOUT tail]\n{rc.stdout[-2000:]}", flush=True)
            print(f"  [{script} STDERR tail]\n{rc.stderr[-2000:]}", flush=True)
    ok = all(rc == 0 for _, rc in results)
    _record(
        "V5 regression verify_vision_012 + verify_vision_011 子进程",
        ok,
        ", ".join(f"{s}=rc{rc}" for s, rc in results),
    )


def main() -> int:
    v1_maybe_identify_wire()
    v2_monotonic_time_base()
    v3_ntp_clock_rollback()
    v4_penalty_negative_fallback_log()
    v5_regression()
    print("\n--- summary ---", flush=True)
    failed = [r for r in _results if not r["ok"]]
    summary = {
        "feature": "vision-013",
        "total": len(_results),
        "failed": len(failed),
        "results": _results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    # 落 evidence
    ev_dir = REPO_ROOT / "evidence" / "vision-013"
    ev_dir.mkdir(parents=True, exist_ok=True)
    (ev_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
