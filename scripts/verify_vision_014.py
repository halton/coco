#!/usr/bin/env python3
"""vision-014 verification: TTL wall clock 设计文档化 + _maybe_identify hot path env cache.

V1 模块/类 docstring 含 TTL wall clock 设计说明 + NTP known-limit 关键词
V2 hot path env cache 生效:
   - persist OFF: _maybe_identify 跑 N 帧, get_face_id / record_name_confidence
     被调用次数 = 0 (整块 wire 被 cache flag short-circuit)
   - persist ON:  _maybe_identify 跑 N 帧, get_face_id / record_name_confidence
     被调用次数 = N (wire 仍按 vision-013 语义生效)
V3 行为等价: persist ON 路径 _face_id_meta[name]['name_confidence'] 仍被写入
   (与 vision-013 V1 等价)
V4 regression: verify_vision_013 / verify_vision_012 / verify_vision_011 子进程 rc=0

retval: 0 全 PASS; 1 任一失败
evidence: evidence/vision-014/verify_summary.json
"""

from __future__ import annotations

import json
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
    def __init__(self, name: str, conf: float) -> None:
        self._name = name
        self._conf = conf
        self.store = None

    def identify(self, crop) -> Tuple[str, float]:  # noqa: ARG002
        return self._name, self._conf


def _seed_primary_snapshot(ft, *, track_id: int = 1) -> None:
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
        name=None,
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
# V1: docstring 含 TTL wall clock 设计 + NTP known-limit
# ---------------------------------------------------------------------------

def v1_docstring_ttl_design() -> None:
    """face_tracker 模块 docstring 必须含:
    - "TTL wall clock" 或等价的"wall clock"/"epoch"设计描述
    - "NTP" 关键词 (known-limit 列举)
    - "monotonic" (与 wall clock 区分)
    - "known limit" / "known-limit" / "调时" / "回拨" / "前跳" 至少一个
    """
    from coco.perception import face_tracker as ft_mod
    doc = (ft_mod.__doc__ or "").lower()
    has_wall_clock = "wall clock" in doc or "wall-clock" in doc
    has_ntp = "ntp" in doc
    has_monotonic = "monotonic" in doc
    has_known_limit = any(
        kw in doc for kw in ("known limit", "known-limit", "调时", "回拨", "前跳")
    )
    ok = has_wall_clock and has_ntp and has_monotonic and has_known_limit
    _record(
        "V1 face_tracker 模块 docstring 含 TTL wall clock + NTP known-limit",
        ok,
        f"wall_clock={has_wall_clock} ntp={has_ntp} monotonic={has_monotonic} "
        f"known_limit={has_known_limit}",
    )


# ---------------------------------------------------------------------------
# V2: hot path env cache — _maybe_identify wire 块走 cache flag, 不读 env
# ---------------------------------------------------------------------------

def v2_hot_path_env_cache() -> None:
    """OFF 路径: persist OFF 时, _maybe_identify 跑 N 帧, 不调 get_face_id
    / record_name_confidence (cache flag short-circuit).
    ON  路径: persist ON 时,  _maybe_identify 跑 N 帧, 调 get_face_id
    / record_name_confidence 各 N 次 (wire 仍生效).
    """
    import numpy as np
    N = 50
    # --- OFF 路径 ---
    _clean_env()
    try:
        ft_off = _fresh_tracker()
        ft_off._face_id_classifier = _FakeClassifier("bob", 0.5)

        # 字段断言: wire gate flag 在 __init__ 已 cache
        has_cache_flag = hasattr(ft_off, "_face_id_identify_wire_enabled")
        cache_flag_off = getattr(ft_off, "_face_id_identify_wire_enabled", None)

        call_count = {"get_face_id": 0, "record_name_confidence": 0}
        orig_get_fid = ft_off.get_face_id
        orig_record = ft_off.record_name_confidence

        def wrap_get_fid(name):
            call_count["get_face_id"] += 1
            return orig_get_fid(name)

        def wrap_record(name, conf):
            call_count["record_name_confidence"] += 1
            return orig_record(name, conf)

        ft_off.get_face_id = wrap_get_fid  # type: ignore[assignment]
        ft_off.record_name_confidence = wrap_record  # type: ignore[assignment]

        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        for _ in range(N):
            _seed_primary_snapshot(ft_off)
            ft_off._maybe_identify(frame, [])

        off_no_wire_calls = (
            call_count["get_face_id"] == 0
            and call_count["record_name_confidence"] == 0
        )
        off_detail = (
            f"flag={cache_flag_off} get_fid={call_count['get_face_id']} "
            f"record={call_count['record_name_confidence']}"
        )
    finally:
        _clean_env()

    # --- ON 路径 ---
    try:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "face_id_map.json"
            _set_env(
                COCO_FACE_ID_REAL="1",
                COCO_FACE_ID_PERSIST="1",
                COCO_FACE_ID_MAP_PATH=str(path),
            )
            ft_on = _fresh_tracker()
            ft_on._face_id_classifier = _FakeClassifier("bob", 0.5)
            cache_flag_on = getattr(ft_on, "_face_id_identify_wire_enabled", None)

            call_count2 = {"get_face_id": 0, "record_name_confidence": 0}
            orig_get_fid2 = ft_on.get_face_id
            orig_record2 = ft_on.record_name_confidence

            def wrap_get_fid2(name):
                call_count2["get_face_id"] += 1
                return orig_get_fid2(name)

            def wrap_record2(name, conf):
                call_count2["record_name_confidence"] += 1
                return orig_record2(name, conf)

            ft_on.get_face_id = wrap_get_fid2  # type: ignore[assignment]
            ft_on.record_name_confidence = wrap_record2  # type: ignore[assignment]

            frame = np.zeros((48, 64, 3), dtype=np.uint8)
            for _ in range(N):
                _seed_primary_snapshot(ft_on)
                ft_on._maybe_identify(frame, [])

            on_wire_calls = (
                call_count2["get_face_id"] == N
                and call_count2["record_name_confidence"] == N
            )
            on_detail = (
                f"flag={cache_flag_on} get_fid={call_count2['get_face_id']} "
                f"record={call_count2['record_name_confidence']}"
            )
    finally:
        _clean_env()

    ok = (
        has_cache_flag
        and cache_flag_off is False
        and cache_flag_on is True
        and off_no_wire_calls
        and on_wire_calls
    )
    _record(
        "V2 hot path env cache: OFF 跳过 wire / ON 进入 wire",
        ok,
        f"has_flag={has_cache_flag} OFF[{off_detail}] ON[{on_detail}]",
    )


# ---------------------------------------------------------------------------
# V3: 行为等价 — persist ON 时 vision-013 wire 语义不变
# ---------------------------------------------------------------------------

def v3_behavior_equivalence() -> None:
    """跑 fixture: persist ON + classifier 注入, 跑 _maybe_identify;
    _face_id_meta['alice']['name_confidence'] 必须被写入 = 0.77,
    snapshot.primary_track.name == 'alice' & name_confidence == 0.77.
    与 vision-013 V1 等价 — 行为字节级未变。"""
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
            ft._face_id_classifier = _FakeClassifier("alice", 0.77)
            _seed_primary_snapshot(ft)
            frame = np.zeros((48, 64, 3), dtype=np.uint8)
            ft._maybe_identify(frame, [])
            with ft._lock:
                snap = ft._snapshot
            with ft._face_id_lock:
                meta = dict(ft._face_id_meta)
            pt_name = snap.primary_track.name if snap.primary_track else None
            pt_conf = snap.primary_track.name_confidence if snap.primary_track else None
            recorded_conf = meta.get("alice", {}).get("name_confidence")
            ok = (
                pt_name == "alice"
                and pt_conf is not None
                and abs(pt_conf - 0.77) < 1e-6
                and recorded_conf is not None
                and abs(float(recorded_conf) - 0.77) < 1e-6
            )
            _record(
                "V3 行为等价: persist ON wire 语义不变",
                ok,
                f"pt_name={pt_name} pt_conf={pt_conf} recorded={recorded_conf}",
            )
    finally:
        _clean_env()


# ---------------------------------------------------------------------------
# V4: regression — verify_vision_013/012/011 子进程 rc=0
# ---------------------------------------------------------------------------

def v4_regression() -> None:
    _clean_env()
    env = dict(os.environ)
    for k in _ENV_KEYS:
        env.pop(k, None)
    results = []
    for script in ("verify_vision_013.py", "verify_vision_012.py",
                   "verify_vision_011.py"):
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
        "V4 regression verify_vision_013/012/011",
        ok,
        ", ".join(f"{s}=rc{rc}" for s, rc in results),
    )


def main() -> int:
    v1_docstring_ttl_design()
    v2_hot_path_env_cache()
    v3_behavior_equivalence()
    v4_regression()
    print("\n--- summary ---", flush=True)
    failed = [r for r in _results if not r["ok"]]
    summary = {
        "feature": "vision-014",
        "total": len(_results),
        "failed": len(failed),
        "results": _results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    ev_dir = REPO_ROOT / "evidence" / "vision-014"
    ev_dir.mkdir(parents=True, exist_ok=True)
    (ev_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
