"""vision-011 verification: face_id_map LRU + GC + 漂移自愈.

跑法::

    uv run python scripts/verify_vision_011.py

子项（对齐 feature_list.json vision-011 acceptance V1-V6）：

V1   LRU 超 max_entries 按 last_seen 升序淘汰最久未见
V2   GC 周期 TTL 清理 + emit map_repair{reason='ttl', dropped_n}
V3   单 entry malformed → 只丢该 entry + emit map_repair{reason='schema'} + 其余 hydrate 成功
V4   untrusted (name_confidence 长期 < 0.3) 仲裁优先级降低
V5   COCO_FACE_ID_MAP_GC OFF → zero-cost no-op (default-OFF bytewise 等价)
V6   vision-010 V1-V10 回归 PASS

retval：0 全 PASS；1 任一失败
evidence 落 evidence/vision-011/verify_summary.json
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
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": ok, "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def _clean_env(*keys: str) -> None:
    for k in keys:
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


# ---------------------------------------------------------------------------
# V1: LRU 超 max_entries 按 last_seen 升序淘汰
# ---------------------------------------------------------------------------

def v1_lru_evict_oldest() -> None:
    """注入 5 个 entry，max=3 → GC 后保留最近 3 个（按 last_seen）."""
    captured: List[Dict[str, Any]] = []

    def emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "face_id_map.json"
        _set_env(
            COCO_FACE_ID_REAL="1",
            COCO_FACE_ID_PERSIST="1",
            COCO_FACE_ID_MAP_GC="1",
            COCO_FACE_ID_MAP_PATH=str(path),
            COCO_FACE_ID_MAP_MAX="3",
            COCO_FACE_ID_MAP_TTL_DAYS="0",  # 关 TTL，纯测 LRU
        )
        try:
            ft = _fresh_tracker(emit_fn=emit)
            # 手工注入 5 个 entry（不同 last_seen）
            now = time.time()
            names = ["u_old1", "u_old2", "u_mid", "u_new1", "u_new2"]
            last_seens = [now - 1000, now - 800, now - 500, now - 100, now - 10]
            with ft._face_id_lock:
                for n, ls in zip(names, last_seens):
                    fid = f"fid_test_{n}"
                    ft._face_id_map[n] = fid
                    ft._face_id_meta[n] = {
                        "face_id": fid, "first_seen": ls, "last_seen": ls,
                    }
            # 跑 GC
            result = ft.run_gc_cycle(now=now)
            with ft._face_id_lock:
                remaining = set(ft._face_id_meta.keys())
            lru_events = [e for e in captured if e.get("reason") == "lru"]
            ok = (
                result["dropped_lru"] == 2
                and remaining == {"u_mid", "u_new1", "u_new2"}
                and len(lru_events) == 1
                and lru_events[0].get("dropped_n") == 2
            )
            _record(
                "V1 LRU evict oldest (max=3 keeps 3 newest by last_seen)",
                ok,
                f"dropped_lru={result['dropped_lru']} remaining={sorted(remaining)} "
                f"lru_events={len(lru_events)}",
            )
        finally:
            _clean_env(
                "COCO_FACE_ID_REAL", "COCO_FACE_ID_PERSIST",
                "COCO_FACE_ID_MAP_GC", "COCO_FACE_ID_MAP_PATH",
                "COCO_FACE_ID_MAP_MAX", "COCO_FACE_ID_MAP_TTL_DAYS",
            )


# ---------------------------------------------------------------------------
# V2: TTL 清理 + emit reason='ttl'
# ---------------------------------------------------------------------------

def v2_ttl_gc_emits_repair() -> None:
    """注入 stale entry (last_seen 距今 > TTL_DAYS)；GC 清理并 emit."""
    captured: List[Dict[str, Any]] = []

    def emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "face_id_map.json"
        _set_env(
            COCO_FACE_ID_REAL="1",
            COCO_FACE_ID_PERSIST="1",
            COCO_FACE_ID_MAP_GC="1",
            COCO_FACE_ID_MAP_PATH=str(path),
            COCO_FACE_ID_MAP_MAX="500",
            COCO_FACE_ID_MAP_TTL_DAYS="30",
        )
        try:
            ft = _fresh_tracker(emit_fn=emit)
            now = time.time()
            # 2 个 stale (40d, 35d), 1 个 fresh (1d)
            stale_ts = now - 40 * 86400
            stale2_ts = now - 35 * 86400
            fresh_ts = now - 1 * 86400
            with ft._face_id_lock:
                ft._face_id_meta = {
                    "u_stale1": {"face_id": "fid_s1", "first_seen": stale_ts,
                                 "last_seen": stale_ts},
                    "u_stale2": {"face_id": "fid_s2", "first_seen": stale2_ts,
                                 "last_seen": stale2_ts},
                    "u_fresh": {"face_id": "fid_f", "first_seen": fresh_ts,
                                "last_seen": fresh_ts},
                }
                ft._face_id_map = {
                    "u_stale1": "fid_s1", "u_stale2": "fid_s2", "u_fresh": "fid_f",
                }
            result = ft.run_gc_cycle(now=now)
            with ft._face_id_lock:
                remaining = set(ft._face_id_meta.keys())
            ttl_events = [
                e for e in captured
                if e.get("ce") == "vision.face_id_map_repair" and e.get("reason") == "ttl"
            ]
            ok = (
                result["dropped_ttl"] == 2
                and remaining == {"u_fresh"}
                and len(ttl_events) == 1
                and ttl_events[0].get("dropped_n") == 2
                and ttl_events[0].get("reason") == "ttl"
            )
            _record(
                "V2 TTL GC drops stale + emits map_repair{reason='ttl'}",
                ok,
                f"dropped_ttl={result['dropped_ttl']} remaining={sorted(remaining)} "
                f"events={len(ttl_events)} dropped_n={ttl_events[0].get('dropped_n') if ttl_events else None}",
            )
        finally:
            _clean_env(
                "COCO_FACE_ID_REAL", "COCO_FACE_ID_PERSIST",
                "COCO_FACE_ID_MAP_GC", "COCO_FACE_ID_MAP_PATH",
                "COCO_FACE_ID_MAP_MAX", "COCO_FACE_ID_MAP_TTL_DAYS",
            )


# ---------------------------------------------------------------------------
# V3: 单 entry malformed → 只丢该 entry + emit schema repair
# ---------------------------------------------------------------------------

def v3_malformed_entry_partial_hydrate() -> None:
    """face_id_map.json 含 1 个 malformed entry + 2 个合法 entry → 丢 1 + hydrate 2."""
    captured: List[Dict[str, Any]] = []

    def emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "face_id_map.json"
        # 手工写一个 mixed schema 文件
        data = {
            "version": 1,
            "saved_at": time.time(),
            "entries": [
                {"name": "alice", "face_id": "fid_alice",
                 "first_seen": 100.0, "last_seen": 200.0},
                # malformed: face_id 不是 string
                {"name": "bob", "face_id": 12345,
                 "first_seen": 100.0, "last_seen": 200.0},
                {"name": "carol", "face_id": "fid_carol",
                 "first_seen": 100.0, "last_seen": 300.0},
                # malformed: 不是 dict
                "not-a-dict",
            ],
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        _set_env(
            COCO_FACE_ID_REAL="1",
            COCO_FACE_ID_PERSIST="1",
            COCO_FACE_ID_MAP_GC="0",
            COCO_FACE_ID_MAP_PATH=str(path),
        )
        try:
            ft = _fresh_tracker(emit_fn=emit)
            with ft._face_id_lock:
                names = set(ft._face_id_meta.keys())
                alice_fid = ft._face_id_map.get("alice")
                carol_fid = ft._face_id_map.get("carol")
            schema_events = [
                e for e in captured
                if e.get("ce") == "vision.face_id_map_repair" and e.get("reason") == "schema"
            ]
            ok = (
                names == {"alice", "carol"}
                and alice_fid == "fid_alice"
                and carol_fid == "fid_carol"
                and len(schema_events) == 1
                and schema_events[0].get("dropped_n") == 2
                and schema_events[0].get("reason") == "schema"
            )
            _record(
                "V3 malformed entries dropped + others hydrated + emit schema",
                ok,
                f"hydrated={sorted(names)} alice_fid={alice_fid} carol_fid={carol_fid} "
                f"schema_events={len(schema_events)} dropped_n={schema_events[0].get('dropped_n') if schema_events else None}",
            )
        finally:
            _clean_env(
                "COCO_FACE_ID_REAL", "COCO_FACE_ID_PERSIST",
                "COCO_FACE_ID_MAP_GC", "COCO_FACE_ID_MAP_PATH",
            )


# ---------------------------------------------------------------------------
# V4: untrusted 仲裁降权
# ---------------------------------------------------------------------------

def v4_untrusted_arbitration_demoted() -> None:
    """alice 中心大脸 + name_confidence=0.1（untrusted）；
    bob 边缘小脸 + 高置信。仲裁应选 bob（alice 被降权）。"""
    captured: List[Dict[str, Any]] = []

    def emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "face_id_map.json"
        _set_env(
            COCO_FACE_ID_REAL="1",
            COCO_FACE_ID_PERSIST="1",
            COCO_FACE_ID_ARBIT="1",
            COCO_FACE_ID_MAP_GC="0",
            COCO_FACE_ID_MAP_PATH=str(path),
        )
        try:
            ft = _fresh_tracker(emit_fn=emit)
            # 先解析两人（让 face_id_map 有 entry）
            ft.get_face_id("alice")
            ft.get_face_id("bob")
            # alice 长期低 confidence → untrusted
            ft.record_name_confidence("alice", 0.1)
            ft.record_name_confidence("bob", 0.9)

            # frame=320x240 center=(160,120)
            # alice 中心大脸 (cx=160, cy=120, area=80*80=6400) baseline score≈0
            # bob 边缘小脸 (cx=30, cy=30, area=20*20=400) baseline score 大
            # 没降权时 alice 会赢；降权后 alice score 乘 100 → bob 赢
            box_alice = _make_box(120, 80, 80, 80)
            box_bob = _make_box(20, 20, 20, 20)
            payload = ft.arbitrate_faces(
                [box_alice, box_bob], ["alice", "bob"],
                frame_w=320, frame_h=240, ts=10.0,
            )
            primary_name = payload.get("primary_name") if payload else None
            alice_cand = next((c for c in (payload.get("candidates") or [])
                               if c.get("name") == "alice"), None)
            ok = (
                primary_name == "bob"
                and alice_cand is not None
                and alice_cand.get("untrusted") is True
            )
            _record(
                "V4 untrusted (low confidence) arbit demoted (bob wins over center alice)",
                ok,
                f"primary_name={primary_name!r} alice_untrusted="
                f"{alice_cand.get('untrusted') if alice_cand else None}",
            )
        finally:
            _clean_env(
                "COCO_FACE_ID_REAL", "COCO_FACE_ID_PERSIST",
                "COCO_FACE_ID_ARBIT", "COCO_FACE_ID_MAP_GC",
                "COCO_FACE_ID_MAP_PATH",
            )


# ---------------------------------------------------------------------------
# V5: GC OFF zero-cost no-op (default-OFF bytewise 等价)
# ---------------------------------------------------------------------------

def v5_gc_off_zero_cost() -> None:
    """COCO_FACE_ID_MAP_GC 未设 / =0 → run_gc_cycle 返回空 + 不写文件 + 不 emit."""
    captured: List[Dict[str, Any]] = []

    def emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "face_id_map.json"
        # 持久化 ON 但 GC OFF：依然 LRU/GC no-op
        _set_env(
            COCO_FACE_ID_REAL="1",
            COCO_FACE_ID_PERSIST="1",
            COCO_FACE_ID_MAP_PATH=str(path),
            COCO_FACE_ID_MAP_MAX="2",
        )
        _clean_env("COCO_FACE_ID_MAP_GC", "COCO_FACE_ID_MAP_TTL_DAYS")
        try:
            ft = _fresh_tracker(emit_fn=emit)
            # 注入 5 个 entry（超 max=2）；GC OFF 时不应有淘汰发生
            now = time.time()
            with ft._face_id_lock:
                for i in range(5):
                    ft._face_id_meta[f"u{i}"] = {
                        "face_id": f"fid_u{i}", "first_seen": now,
                        "last_seen": now,
                    }
                    ft._face_id_map[f"u{i}"] = f"fid_u{i}"
            result = ft.run_gc_cycle(now=now)
            with ft._face_id_lock:
                count = len(ft._face_id_meta)
            repair_events = [e for e in captured if e.get("ce") == "vision.face_id_map_repair"]
            ok = (
                result["dropped_ttl"] == 0
                and result["dropped_lru"] == 0
                and count == 5
                and len(repair_events) == 0
            )
            _record(
                "V5 GC OFF: zero-cost no-op (no emit, no eviction)",
                ok,
                f"dropped_ttl={result['dropped_ttl']} dropped_lru={result['dropped_lru']} "
                f"count={count} repair_events={len(repair_events)}",
            )
        finally:
            _clean_env(
                "COCO_FACE_ID_REAL", "COCO_FACE_ID_PERSIST",
                "COCO_FACE_ID_MAP_PATH", "COCO_FACE_ID_MAP_MAX",
            )


# ---------------------------------------------------------------------------
# V5b: 完全 default-OFF (PERSIST + GC 都 OFF) bytewise 等价
# ---------------------------------------------------------------------------

def v5b_default_off_bytewise() -> None:
    """完全 default-OFF：PERSIST + GC 都未设 → 无文件 IO + 无 meta 维护 + 无 emit."""
    captured: List[Dict[str, Any]] = []

    def emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "face_id_map.json"
        _clean_env(
            "COCO_FACE_ID_PERSIST", "COCO_FACE_ID_MAP_GC",
            "COCO_FACE_ID_ARBIT", "COCO_FACE_ID_MAP_TTL_DAYS",
            "COCO_FACE_ID_MAP_MAX",
        )
        _set_env(
            COCO_FACE_ID_REAL="1",
            COCO_FACE_ID_MAP_PATH=str(path),
        )
        try:
            ft = _fresh_tracker(emit_fn=emit)
            fid = ft.get_face_id("alice")
            result = ft.run_gc_cycle()
            no_file = not path.exists()
            no_meta = len(getattr(ft, "_face_id_meta", {})) == 0
            # 仅检查 vision-011 引入的 face_id_map_repair emit
            # （face_id_resolved 是 vision-009 的，不在 vision-011 scope）
            repair_events = [e for e in captured if e.get("ce") == "vision.face_id_map_repair"]
            no_repair = len(repair_events) == 0
            ok = (
                isinstance(fid, str)
                and result["dropped_ttl"] == 0
                and result["dropped_lru"] == 0
                and no_file and no_meta and no_repair
            )
            _record(
                "V5b PERSIST+GC both OFF: bytewise eq (no IO, no meta, no map_repair emit)",
                ok,
                f"fid={fid!r} no_file={no_file} no_meta={no_meta} no_repair={no_repair}",
            )
        finally:
            _clean_env(
                "COCO_FACE_ID_REAL", "COCO_FACE_ID_MAP_PATH",
            )


# ---------------------------------------------------------------------------
# V6: vision-010 regression
# ---------------------------------------------------------------------------

def v6_regress_vision_010() -> None:
    """V6 跑 verify_vision_010.py，应全 PASS."""
    script = ROOT / "scripts" / "verify_vision_010.py"
    env = os.environ.copy()
    for k in (
        "COCO_FACE_ID_REAL", "COCO_FACE_ID_PERSIST",
        "COCO_FACE_ID_MAP_PATH", "COCO_FACE_ID_ARBIT",
        "COCO_FACE_ID_MAP_GC", "COCO_FACE_ID_MAP_MAX",
        "COCO_FACE_ID_MAP_TTL_DAYS",
    ):
        env.pop(k, None)
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT), capture_output=True, text=True,
        timeout=300, env=env,
    )
    ok = proc.returncode == 0
    tail = (proc.stdout or "").splitlines()[-3:]
    _record(
        "V6 regress vision-010 V1-V10 still PASS",
        ok,
        f"rc={proc.returncode} tail={tail}",
    )


# ---------------------------------------------------------------------------

def main() -> int:
    for fn in (
        v1_lru_evict_oldest,
        v2_ttl_gc_emits_repair,
        v3_malformed_entry_partial_hydrate,
        v4_untrusted_arbitration_demoted,
        v5_gc_off_zero_cost,
        v5b_default_off_bytewise,
        v6_regress_vision_010,
    ):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _record(fn.__name__, False, f"unhandled exception {e!r}")

    out = ROOT / "evidence" / "vision-011"
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "vision-011",
        "ok": all(r["ok"] for r in _results),
        "results": _results,
    }
    (out / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_pass = sum(1 for r in _results if r["ok"])
    n_total = len(_results)
    print(f"\n[vision-011] {n_pass}/{n_total} PASS")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
