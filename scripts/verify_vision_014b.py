#!/usr/bin/env python3
"""vision-014b verification: TTL 边界 + GC 触发 + overhead per-frame contract 锁定.

承接 vision-014 (docstring + hot path env cache 已落地, status=passing),
本 verify 进一步把 vision-013/014 的 TTL/GC/overhead 三方契约用可执行断言锁死,
作为长期 regression guard。**verify-only, 不动源码** (除新增 docs/)。

V1 TTL 边界 strict-greater-than (>):
   last_seen == cutoff - 1s → 不淘汰 (保留)
   last_seen == cutoff - ttl_secs (= now - ttl_secs) → 保留 (边界点严格不大于)
   last_seen == cutoff - ttl_secs - 0.001 (= now - ttl_secs - 0.001) → 淘汰
   ttl_days <= 0 → 整块 TTL short-circuit, 全保留
V2 GC 触发 contract (frame_due OR time_due, 任一即触发, 触发后双计数器 reset):
   - frame_due-only: period_frames=3, 跑 3 帧 → 触发一次 run_gc
   - time_due-only: period_frames=999999, period_s=0.1, monotonic mock → 触发一次
   - 双 due: 任一先到都触发, counter 复位
   - 首帧不立即 time-due 触发 (_gc_last_time = None → init = now_mono, 差值 0)
   - period_s <= 0 → 禁用 time-due 路径
V3 Overhead per-frame: default-OFF _maybe_identify wire 块 short-circuit
   - get_face_id call count == 0
   - record_name_confidence call count == 0
   - flag (_face_id_identify_wire_enabled) == False
   反例 (persist ON): flag == True, get_face_id/record 各 == N
V4 docs/vision-ttl-design.md 存在且 ≥3 段 (h2 计数), 含关键短语
   "wall clock", "monotonic", "NTP", "跨进程", "strict", "Default-OFF"
V5 regression: verify_vision_014.py / verify_vision_013.py 子进程 rc=0

retval: 0 全 PASS; 1 任一 FAIL
evidence: evidence/vision-014b/verify_summary.json
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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


# =====================================================================
# V1 TTL 边界 strict-greater-than (>)
# =====================================================================
def v1_ttl_boundary() -> None:
    """TTL 边界 contract: (now - last_seen) > ttl_secs 严格大于, 边界保留."""
    name = "V1 TTL 边界 strict-greater-than (>)"
    detail_parts: List[str] = []
    try:
        _clean_env()
        tmp_dir = tempfile.mkdtemp(prefix="v014b_v1_")
        map_path = os.path.join(tmp_dir, "face_id_map.json")
        # TTL 1 day, persist+gc 启用
        _set_env(
            COCO_FACE_ID_PERSIST="1",
            COCO_FACE_ID_MAP_GC="1",
            COCO_FACE_ID_MAP_PATH=map_path,
            COCO_FACE_ID_MAP_TTL_DAYS="1",
            COCO_FACE_ID_MAP_MAX="1000",
        )
        tr = _fresh_tracker()
        # 注入 4 条 entry: last_seen 距 now 分别为
        #  e_keep_safe:    1.0s ago     → 远未过期, 保留
        #  e_boundary:     86400.0s ago → 恰边界 (now - last_seen == ttl_secs), 严格大于 → 保留
        #  e_just_over:    86400.001s ago → 严格大于 ttl_secs → 淘汰
        #  e_long_over:    100000.0s ago → 淘汰
        now = time.time()
        ttl_secs = 86400.0  # 1 day
        tr._face_id_meta = {
            "e_keep_safe": {"face_id": "fid_safe", "first_seen": now - 1.0,
                            "last_seen": now - 1.0, "name_confidence": 0.9},
            "e_boundary": {"face_id": "fid_b", "first_seen": now - ttl_secs,
                           "last_seen": now - ttl_secs, "name_confidence": 0.9},
            "e_just_over": {"face_id": "fid_jo", "first_seen": now - ttl_secs - 0.001,
                            "last_seen": now - ttl_secs - 0.001, "name_confidence": 0.9},
            "e_long_over": {"face_id": "fid_lo", "first_seen": now - 100000.0,
                            "last_seen": now - 100000.0, "name_confidence": 0.9},
        }
        tr._face_id_map = {k: v["face_id"] for k, v in tr._face_id_meta.items()}
        res = tr.run_gc_cycle(now=now, reason_tag="v1")
        survived = set(tr._face_id_meta.keys())
        detail_parts.append(f"survived={sorted(survived)}")
        detail_parts.append(f"dropped_ttl={res.get('dropped_ttl')}")
        ok_keep = "e_keep_safe" in survived and "e_boundary" in survived
        ok_drop = "e_just_over" not in survived and "e_long_over" not in survived
        ok_count = res.get("dropped_ttl") == 2

        # ttl_days <= 0 → 禁用 TTL 路径
        _set_env(COCO_FACE_ID_MAP_TTL_DAYS="0")
        tr2 = _fresh_tracker()
        tr2._face_id_meta = {
            "any_old": {"face_id": "fid_old", "first_seen": now - 1e9,
                        "last_seen": now - 1e9, "name_confidence": 0.9},
        }
        tr2._face_id_map = {"any_old": "fid_old"}
        res2 = tr2.run_gc_cycle(now=now, reason_tag="v1_ttl0")
        ttl0_keep = "any_old" in tr2._face_id_meta and res2.get("dropped_ttl") == 0
        detail_parts.append(f"ttl_days=0_keep={ttl0_keep}")

        ok = ok_keep and ok_drop and ok_count and ttl0_keep
        _record(name, ok, "; ".join(detail_parts))
    except Exception as e:  # noqa: BLE001
        _record(name, False, f"exception: {type(e).__name__}: {e}")
    finally:
        _clean_env()


# =====================================================================
# V2 GC 触发 contract (frame_due OR time_due)
# =====================================================================
def v2_gc_trigger_contract() -> None:
    name = "V2 GC 触发 frame_due OR time_due, 双计数器复位"
    detail_parts: List[str] = []
    try:
        _clean_env()
        tmp_dir = tempfile.mkdtemp(prefix="v014b_v2_")
        # ---- (a) frame-due only ----
        _set_env(
            COCO_FACE_ID_PERSIST="1",
            COCO_FACE_ID_MAP_GC="1",
            COCO_FACE_ID_MAP_PATH=os.path.join(tmp_dir, "a.json"),
            COCO_FACE_ID_MAP_GC_INTERVAL_FRAMES="3",
            COCO_FACE_ID_MAP_GC_INTERVAL_S="999999",
        )
        tr = _fresh_tracker()
        call_log: List[str] = []
        orig_run = tr.run_gc_cycle

        def spy_a(*args: Any, **kwargs: Any) -> Dict[str, int]:
            call_log.append("a")
            return orig_run(*args, **kwargs)

        tr.run_gc_cycle = spy_a  # type: ignore[assignment]
        # 跑 5 帧, 期望: 第 3 帧触发一次, 之后 counter reset, 第 6 帧才会再触发
        for _ in range(5):
            tr._maybe_periodic_gc()
        a_fires = len(call_log)
        a_counter = tr._gc_frame_counter
        detail_parts.append(f"a(frame3/5frames)={a_fires} counter_after={a_counter}")
        ok_a = a_fires == 1 and a_counter == 2  # 触发后剩 2 帧

        # ---- (b) time-due only ----
        _clean_env()
        _set_env(
            COCO_FACE_ID_PERSIST="1",
            COCO_FACE_ID_MAP_GC="1",
            COCO_FACE_ID_MAP_PATH=os.path.join(tmp_dir, "b.json"),
            COCO_FACE_ID_MAP_GC_INTERVAL_FRAMES="999999",
            COCO_FACE_ID_MAP_GC_INTERVAL_S="0.1",
        )
        import coco.perception.face_tracker as ft_mod
        tr2 = _fresh_tracker()
        call_log_b: List[str] = []
        orig2 = tr2.run_gc_cycle

        def spy_b(*args: Any, **kwargs: Any) -> Dict[str, int]:
            call_log_b.append("b")
            return orig2(*args, **kwargs)

        tr2.run_gc_cycle = spy_b  # type: ignore[assignment]
        # monotonic mock: 第 1 帧 t=100.0 (init _gc_last_time=100.0, 不触发),
        # 第 2 帧 t=100.05 (差 0.05<0.1, 不触发),
        # 第 3 帧 t=100.2 (差 0.2>=0.1, 触发)
        mono_seq = iter([100.0, 100.05, 100.2, 100.21, 100.22])
        orig_mono = ft_mod.time.monotonic

        def fake_mono() -> float:
            try:
                return next(mono_seq)
            except StopIteration:
                return 200.0

        ft_mod.time.monotonic = fake_mono  # type: ignore[assignment]
        try:
            for _ in range(3):
                tr2._maybe_periodic_gc()
        finally:
            ft_mod.time.monotonic = orig_mono  # type: ignore[assignment]
        b_fires = len(call_log_b)
        detail_parts.append(f"b(time0.1s/3frames)={b_fires}")
        ok_b = b_fires == 1

        # ---- (c) 首帧不立即 time-due (_gc_last_time init = now_mono, 差 0) ----
        _clean_env()
        _set_env(
            COCO_FACE_ID_PERSIST="1",
            COCO_FACE_ID_MAP_GC="1",
            COCO_FACE_ID_MAP_PATH=os.path.join(tmp_dir, "c.json"),
            COCO_FACE_ID_MAP_GC_INTERVAL_FRAMES="999999",
            COCO_FACE_ID_MAP_GC_INTERVAL_S="0.001",  # 极短, 但首帧仍不该触发
        )
        tr3 = _fresh_tracker()
        call_log_c: List[str] = []
        orig3 = tr3.run_gc_cycle

        def spy_c(*args: Any, **kwargs: Any) -> Dict[str, int]:
            call_log_c.append("c")
            return orig3(*args, **kwargs)

        tr3.run_gc_cycle = spy_c  # type: ignore[assignment]
        tr3._maybe_periodic_gc()  # 仅 1 帧
        c_fires = len(call_log_c)
        c_last_time = tr3._gc_last_time
        detail_parts.append(f"c(first_frame_init)={c_fires} _gc_last_time_set={c_last_time is not None}")
        ok_c = c_fires == 0 and c_last_time is not None

        # ---- (d) period_s <= 0 禁用 time-due (frame-due 仍能触发) ----
        _clean_env()
        _set_env(
            COCO_FACE_ID_PERSIST="1",
            COCO_FACE_ID_MAP_GC="1",
            COCO_FACE_ID_MAP_PATH=os.path.join(tmp_dir, "d.json"),
            COCO_FACE_ID_MAP_GC_INTERVAL_FRAMES="2",
            COCO_FACE_ID_MAP_GC_INTERVAL_S="0",  # 禁用 time-due
        )
        tr4 = _fresh_tracker()
        call_log_d: List[str] = []
        orig4 = tr4.run_gc_cycle

        def spy_d(*args: Any, **kwargs: Any) -> Dict[str, int]:
            call_log_d.append("d")
            return orig4(*args, **kwargs)

        tr4.run_gc_cycle = spy_d  # type: ignore[assignment]
        # 即使等很久, time-due 也不该触发; frame-due 仍触发
        for _ in range(2):
            tr4._maybe_periodic_gc()
        d_fires = len(call_log_d)
        detail_parts.append(f"d(period_s=0,frame=2/2frames)={d_fires}")
        ok_d = d_fires == 1

        # ---- (e) Default-OFF (gc/persist 任一未启) → run_gc_cycle short-circuit ----
        _clean_env()
        tr5 = _fresh_tracker()
        res5 = tr5.run_gc_cycle(now=time.time(), reason_tag="v2_off")
        ok_e = res5 == {"dropped_ttl": 0, "dropped_lru": 0}
        detail_parts.append(f"e(default_off)={res5}")

        ok = ok_a and ok_b and ok_c and ok_d and ok_e
        _record(name, ok, "; ".join(detail_parts))
    except Exception as e:  # noqa: BLE001
        _record(name, False, f"exception: {type(e).__name__}: {e}")
    finally:
        _clean_env()


# =====================================================================
# V3 Overhead per-frame: default-OFF wire short-circuit
# =====================================================================
def v3_overhead_default_off() -> None:
    name = "V3 _maybe_identify default-OFF wire short-circuit (0 method call)"
    detail_parts: List[str] = []
    try:
        _clean_env()
        # ---- default-OFF: persist 未设 → wire flag = False ----
        tr = _fresh_tracker()
        flag_off = tr._face_id_identify_wire_enabled
        detail_parts.append(f"flag_off={flag_off}")
        # spy get_face_id / record_name_confidence
        get_calls: List[Any] = []
        rec_calls: List[Any] = []
        orig_get = tr.get_face_id
        orig_rec = tr.record_name_confidence

        def spy_get(n: Optional[str]) -> Optional[str]:
            get_calls.append(n)
            return orig_get(n)

        def spy_rec(n: str, c: float) -> None:
            rec_calls.append((n, c))
            return orig_rec(n, c)

        tr.get_face_id = spy_get  # type: ignore[assignment]
        tr.record_name_confidence = spy_rec  # type: ignore[assignment]

        # 注入一个 fake classifier (任何 identify 返回 ("alice", 0.8))
        class FakeClassifier:
            def identify(self, crop):
                return ("alice", 0.8)

        tr._face_id_classifier = FakeClassifier()

        # 注入 snapshot.primary + primary_track, 才会进 wire 段
        from coco.perception.face_tracker import FaceSnapshot, TrackedFace
        from coco.perception.face_detect import FaceBox
        import numpy as np  # type: ignore[import-not-found]
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        for i in range(20):
            box = FaceBox(x=10, y=10, w=40, h=40, score=1.0)
            pt = TrackedFace(
                track_id=1, box=box, age_frames=i, hit_count=i, miss_count=0,
                smoothed_cx=30.0, smoothed_cy=30.0, presence_score=1.0,
                first_seen_ts=0.0, last_seen_ts=float(i),
                name=None, name_confidence=0.0,
            )
            tr._snapshot = FaceSnapshot(
                faces=(box,), frame_w=100, frame_h=100, present=True,
                primary=box, ts=float(i), detect_count=1, hit_count=1,
                tracks=(pt,), primary_track=pt,
            )
            tr._maybe_identify(frame, [box])
        off_get = len(get_calls)
        off_rec = len(rec_calls)
        detail_parts.append(f"OFF[20frames] get={off_get} rec={off_rec}")
        ok_off = (flag_off is False) and (off_get == 0) and (off_rec == 0)

        # ---- ON: persist=1 → wire flag = True, 调用次数 == N ----
        _clean_env()
        tmp_dir = tempfile.mkdtemp(prefix="v014b_v3_")
        _set_env(
            COCO_FACE_ID_PERSIST="1",
            COCO_FACE_ID_MAP_PATH=os.path.join(tmp_dir, "on.json"),
        )
        tr_on = _fresh_tracker()
        flag_on = tr_on._face_id_identify_wire_enabled
        detail_parts.append(f"flag_on={flag_on}")
        get_calls_on: List[Any] = []
        rec_calls_on: List[Any] = []
        orig_get_on = tr_on.get_face_id
        orig_rec_on = tr_on.record_name_confidence

        def spy_get_on(n: Optional[str]) -> Optional[str]:
            get_calls_on.append(n)
            return orig_get_on(n)

        def spy_rec_on(n: str, c: float) -> None:
            rec_calls_on.append((n, c))
            return orig_rec_on(n, c)

        tr_on.get_face_id = spy_get_on  # type: ignore[assignment]
        tr_on.record_name_confidence = spy_rec_on  # type: ignore[assignment]
        tr_on._face_id_classifier = FakeClassifier()

        for i in range(20):
            box = FaceBox(x=10, y=10, w=40, h=40, score=1.0)
            pt = TrackedFace(
                track_id=1, box=box, age_frames=i, hit_count=i, miss_count=0,
                smoothed_cx=30.0, smoothed_cy=30.0, presence_score=1.0,
                first_seen_ts=0.0, last_seen_ts=float(i),
                name=None, name_confidence=0.0,
            )
            tr_on._snapshot = FaceSnapshot(
                faces=(box,), frame_w=100, frame_h=100, present=True,
                primary=box, ts=float(i), detect_count=1, hit_count=1,
                tracks=(pt,), primary_track=pt,
            )
            tr_on._maybe_identify(frame, [box])
        on_get = len(get_calls_on)
        on_rec = len(rec_calls_on)
        detail_parts.append(f"ON[20frames] get={on_get} rec={on_rec}")
        ok_on = (flag_on is True) and (on_get == 20) and (on_rec == 20)

        ok = ok_off and ok_on
        _record(name, ok, "; ".join(detail_parts))
    except Exception as e:  # noqa: BLE001
        _record(name, False, f"exception: {type(e).__name__}: {e}")
    finally:
        _clean_env()


# =====================================================================
# V4 docs/vision-ttl-design.md 存在且 ≥3 段 + 关键短语
# =====================================================================
def v4_docs_present() -> None:
    name = "V4 docs/vision-ttl-design.md ≥3 段 + 关键短语全覆盖"
    try:
        doc_path = REPO_ROOT / "docs" / "vision-ttl-design.md"
        if not doc_path.is_file():
            _record(name, False, f"missing {doc_path}")
            return
        text = doc_path.read_text(encoding="utf-8")
        # h2 数 (## 开头, 非 ### / ####)
        h2_count = len(re.findall(r"^## [^#]", text, flags=re.MULTILINE))
        required_phrases = [
            "wall clock", "monotonic", "NTP", "跨进程", "strict", "Default-OFF",
        ]
        missing = [p for p in required_phrases if p not in text]
        ok = (h2_count >= 3) and (not missing)
        detail = f"h2_count={h2_count} missing_phrases={missing}"
        _record(name, ok, detail)
    except Exception as e:  # noqa: BLE001
        _record(name, False, f"exception: {type(e).__name__}: {e}")


# =====================================================================
# V5 regression: verify_vision_014.py / verify_vision_013.py 子进程 rc=0
# =====================================================================
def v5_regression() -> None:
    name = "V5 regression verify_vision_014/013"
    try:
        scripts = ["scripts/verify_vision_014.py", "scripts/verify_vision_013.py"]
        details = []
        all_ok = True
        for s in scripts:
            sp = REPO_ROOT / s
            if not sp.is_file():
                details.append(f"{s}=MISSING")
                all_ok = False
                continue
            r = subprocess.run(
                [sys.executable, str(sp)],
                cwd=str(REPO_ROOT),
                capture_output=True,
                timeout=120,
            )
            details.append(f"{s}=rc{r.returncode}")
            if r.returncode != 0:
                all_ok = False
        _record(name, all_ok, ", ".join(details))
    except Exception as e:  # noqa: BLE001
        _record(name, False, f"exception: {type(e).__name__}: {e}")


def main() -> int:
    v1_ttl_boundary()
    v2_gc_trigger_contract()
    v3_overhead_default_off()
    v4_docs_present()
    v5_regression()

    total = len(_results)
    failed = sum(1 for r in _results if not r["ok"])
    summary = {
        "feature": "vision-014b",
        "total": total,
        "failed": failed,
        "results": _results,
    }
    out_dir = REPO_ROOT / "evidence" / "vision-014b"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"\n[summary] {total - failed}/{total} PASS")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
