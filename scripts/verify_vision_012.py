#!/usr/bin/env python3
"""vision-012 verification: face_id GC 时间+帧双触发 + untrusted/penalty env 化.

V1 默认 env (frame 周期触发, vision-011 行为不变).
V2 COCO_FACE_ID_MAP_GC_INTERVAL_S 时间触发: 帧周期未达, 时间到 → GC 触发.
V3 同帧双触发节流: 仅一次 GC.
V4 COCO_FACE_ID_UNTRUSTED_CONF_THRESHOLD env 化: name_conf 介于默认与新阈值之间被判 untrusted.
V5 COCO_FACE_ID_UNTRUSTED_PENALTY env 化 (0 关闭降权; 自定义值参与 score);
   regression 子进程跑 verify_vision_011 全 PASS.
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

# 把 repo root 加进 sys.path（与 verify_vision_011 一致的姿势）
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


# ---------------------------------------------------------------------------
# V1: 默认 env 下 — frame 周期触发 (覆盖 vision-011 V3 行为)
# ---------------------------------------------------------------------------

def v1_default_frame_trigger() -> None:
    """默认 env (无 GC_INTERVAL_S 设置) → frame 周期累计到阈值后触发 GC.

    用 PERIOD_FRAMES=3 + 注入 stale entry, 跑 3 个 _tick 后应触发一次 GC.
    """
    captured: List[Dict[str, Any]] = []

    def emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    _clean_env()
    try:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "face_id_map.json"
            _set_env(
                COCO_FACE_ID_REAL="1",
                COCO_FACE_ID_PERSIST="1",
                COCO_FACE_ID_MAP_GC="1",
                COCO_FACE_ID_MAP_PATH=str(path),
                COCO_FACE_ID_MAP_MAX="500",
                COCO_FACE_ID_MAP_TTL_DAYS="30",
                COCO_FACE_ID_MAP_GC_PERIOD_FRAMES="3",
                # NO COCO_FACE_ID_MAP_GC_INTERVAL_S → 默认 300s 不会在测试内触发
            )
            ft = _fresh_tracker(emit_fn=emit)
            # 默认值断言
            ok_defaults = (
                abs(ft._face_id_untrusted_threshold - 0.3) < 1e-9
                and abs(ft._face_id_untrusted_penalty - 1e6) < 1e-3
                and abs(ft._gc_period_s - 300.0) < 1e-9
                and ft._gc_period_frames == 3
            )
            # 注入 stale entry
            now = time.time()
            stale_ts = now - 40 * 86400
            with ft._face_id_lock:
                ft._face_id_meta = {
                    "u_stale": {"face_id": "fid_s", "first_seen": stale_ts,
                                "last_seen": stale_ts},
                }
                ft._face_id_map = {"u_stale": "fid_s"}
            # 跑 3 次 _maybe_periodic_gc (frame_counter: 1, 2, 3 → 触发)
            for _ in range(3):
                ft._maybe_periodic_gc()
            ttl_events = [e for e in captured if e.get("reason") == "ttl"]
            with ft._face_id_lock:
                remaining = set(ft._face_id_meta.keys())
            ok = (
                ok_defaults
                and len(ttl_events) == 1
                and ttl_events[0].get("dropped_n") == 1
                and remaining == set()
            )
            _record(
                "V1 默认 env → frame 周期 GC 触发 + 默认值正确",
                ok,
                f"defaults_ok={ok_defaults} ttl_events={len(ttl_events)} "
                f"remaining={sorted(remaining)}",
            )
    finally:
        _clean_env()


# ---------------------------------------------------------------------------
# V2: 时间触发 — frame 周期未达, COCO_FACE_ID_MAP_GC_INTERVAL_S 到点触发
# ---------------------------------------------------------------------------

def v2_time_trigger_low_fps() -> None:
    """frame 周期 1500 帧未达, 但 time period=2s 到点 → GC 触发."""
    captured: List[Dict[str, Any]] = []

    def emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    _clean_env()
    try:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "face_id_map.json"
            _set_env(
                COCO_FACE_ID_REAL="1",
                COCO_FACE_ID_PERSIST="1",
                COCO_FACE_ID_MAP_GC="1",
                COCO_FACE_ID_MAP_PATH=str(path),
                COCO_FACE_ID_MAP_MAX="500",
                COCO_FACE_ID_MAP_TTL_DAYS="30",
                # frame period 大到测试内绝不会到 (1500 帧)
                COCO_FACE_ID_MAP_GC_PERIOD_FRAMES="1500",
                COCO_FACE_ID_MAP_GC_INTERVAL_S="0.05",  # 50ms 便于测试
            )
            ft = _fresh_tracker(emit_fn=emit)
            ok_field = abs(ft._gc_period_s - 0.05) < 1e-9
            now = time.time()
            stale_ts = now - 40 * 86400
            with ft._face_id_lock:
                ft._face_id_meta = {
                    "u_stale": {"face_id": "fid_s", "first_seen": stale_ts,
                                "last_seen": stale_ts},
                }
                ft._face_id_map = {"u_stale": "fid_s"}
            import numpy as np
            frame = np.zeros((48, 64, 3), dtype=np.uint8)
            # 第一次 tick 仅初始化 _gc_last_time, 不触发
            ft._maybe_periodic_gc()
            ttl_after_1 = len([e for e in captured if e.get("reason") == "ttl"])
            # 等 60ms 后再 tick → 时间触发
            time.sleep(0.08)
            ft._maybe_periodic_gc()
            ttl_after_2 = len([e for e in captured if e.get("reason") == "ttl"])
            ok = (
                ok_field
                and ttl_after_1 == 0
                and ttl_after_2 == 1
                and ft._gc_frame_counter == 0  # GC 触发后两计数都重置
            )
            _record(
                "V2 时间触发 (frame 周期未达, INTERVAL_S 到点) → GC 触发",
                ok,
                f"field_ok={ok_field} ttl_after_1={ttl_after_1} "
                f"ttl_after_2={ttl_after_2} frame_counter={ft._gc_frame_counter}",
            )
    finally:
        _clean_env()


# ---------------------------------------------------------------------------
# V3: 同帧双触发节流 — frame 与 time 同帧都满足, 仅一次 GC
# ---------------------------------------------------------------------------

def v3_double_trigger_throttled() -> None:
    """frame_counter 达 + 时间到, 同一帧 → 仅一次 run_gc_cycle 调用."""
    captured: List[Dict[str, Any]] = []
    gc_calls: List[float] = []

    def emit(ce: str, msg: str = "", **payload: Any) -> None:
        captured.append({"ce": ce, **payload})

    _clean_env()
    try:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "face_id_map.json"
            _set_env(
                COCO_FACE_ID_REAL="1",
                COCO_FACE_ID_PERSIST="1",
                COCO_FACE_ID_MAP_GC="1",
                COCO_FACE_ID_MAP_PATH=str(path),
                COCO_FACE_ID_MAP_MAX="500",
                COCO_FACE_ID_MAP_TTL_DAYS="30",
                COCO_FACE_ID_MAP_GC_PERIOD_FRAMES="2",
                COCO_FACE_ID_MAP_GC_INTERVAL_S="0.05",
            )
            ft = _fresh_tracker(emit_fn=emit)
            # 注入 stale; 触发后会 emit 一次 ttl
            now = time.time()
            stale_ts = now - 40 * 86400
            with ft._face_id_lock:
                ft._face_id_meta = {
                    "u_stale1": {"face_id": "fid1", "first_seen": stale_ts,
                                 "last_seen": stale_ts},
                }
                ft._face_id_map = {"u_stale1": "fid1"}

            # patch run_gc_cycle 记录调用次数
            orig = ft.run_gc_cycle

            def patched(now: Optional[float] = None, reason_tag: str = "tick"):
                gc_calls.append(time.time())
                return orig(now=now, reason_tag=reason_tag)

            ft.run_gc_cycle = patched  # type: ignore[assignment]

            import numpy as np
            frame = np.zeros((48, 64, 3), dtype=np.uint8)
            # 第一次 tick：init _gc_last_time, frame_counter=1
            ft._maybe_periodic_gc()
            calls_after_1 = len(gc_calls)
            # 等 60ms (超过 0.05s) 后再 tick: frame_counter=2 (满足) AND time_due=True
            time.sleep(0.08)
            ft._maybe_periodic_gc()
            calls_after_2 = len(gc_calls)
            ok = (
                calls_after_1 == 0
                and calls_after_2 == 1  # 节流：双触发同帧 → 仅一次
            )
            _record(
                "V3 同帧双触发 (frame_due AND time_due) → 仅一次 GC",
                ok,
                f"calls_after_1={calls_after_1} calls_after_2={calls_after_2}",
            )
    finally:
        _clean_env()


# ---------------------------------------------------------------------------
# V4: COCO_FACE_ID_UNTRUSTED_CONF_THRESHOLD env 化
# ---------------------------------------------------------------------------

def v4_untrusted_threshold_env() -> None:
    """threshold=0.5 → name_conf=0.4 被判 untrusted (默认 0.3 下不会)."""
    _clean_env()
    try:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "face_id_map.json"
            # case A: 默认 0.3
            _set_env(
                COCO_FACE_ID_REAL="1",
                COCO_FACE_ID_PERSIST="1",
                COCO_FACE_ID_MAP_GC="1",
                COCO_FACE_ID_MAP_PATH=str(path),
            )
            ft_default = _fresh_tracker()
            with ft_default._face_id_lock:
                ft_default._face_id_meta["alice"] = {
                    "face_id": "fid_a", "first_seen": 0.0, "last_seen": 0.0,
                    "name_confidence": 0.4,
                }
            untrusted_default = ft_default._is_untrusted("alice")
            # case B: threshold=0.5
            _clean_env()
            with tempfile.TemporaryDirectory() as td2:
                path2 = Path(td2) / "face_id_map.json"
                _set_env(
                    COCO_FACE_ID_REAL="1",
                    COCO_FACE_ID_PERSIST="1",
                    COCO_FACE_ID_MAP_GC="1",
                    COCO_FACE_ID_MAP_PATH=str(path2),
                    COCO_FACE_ID_UNTRUSTED_CONF_THRESHOLD="0.5",
                )
                ft_new = _fresh_tracker()
                with ft_new._face_id_lock:
                    ft_new._face_id_meta["alice"] = {
                        "face_id": "fid_a", "first_seen": 0.0, "last_seen": 0.0,
                        "name_confidence": 0.4,
                    }
                untrusted_new = ft_new._is_untrusted("alice")
                threshold_field = ft_new._face_id_untrusted_threshold
        ok = (
            untrusted_default is False
            and untrusted_new is True
            and abs(threshold_field - 0.5) < 1e-9
        )
        _record(
            "V4 untrusted threshold env 化 (0.3 default → 0.5 → name_conf=0.4 被判 untrusted)",
            ok,
            f"default_untrusted={untrusted_default} new_untrusted={untrusted_new} "
            f"field={threshold_field}",
        )
    finally:
        _clean_env()


# ---------------------------------------------------------------------------
# V5: COCO_FACE_ID_UNTRUSTED_PENALTY env 化 + regression vision-011
# ---------------------------------------------------------------------------

def v5_penalty_env_and_regression() -> None:
    """penalty=99 → arbitrate score 增加 99 而非 1e6;
    penalty=0 → 关闭降权; 子进程跑 verify_vision_011 全 PASS."""
    _clean_env()
    try:
        # case A: penalty=99 → arbitrate 中 untrusted 候选 score 增加 99
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "face_id_map.json"
            _set_env(
                COCO_FACE_ID_REAL="1",
                COCO_FACE_ID_PERSIST="1",
                COCO_FACE_ID_ARBIT="1",
                COCO_FACE_ID_MAP_GC="1",
                COCO_FACE_ID_MAP_PATH=str(path),
                COCO_FACE_ID_UNTRUSTED_PENALTY="99.0",
            )
            captured99: List[Dict[str, Any]] = []

            def emit99(ce: str, msg: str = "", **payload: Any) -> None:
                captured99.append({"ce": ce, **payload})

            ft = _fresh_tracker(emit_fn=emit99)
            # 注入 2 个 known + 1 个 untrusted
            with ft._face_id_lock:
                ft._face_id_map["alice"] = "fid_a"
                ft._face_id_map["bob"] = "fid_b"
                ft._face_id_meta["alice"] = {
                    "face_id": "fid_a", "first_seen": 0.0, "last_seen": 0.0,
                    "name_confidence": 0.9,  # trusted
                }
                ft._face_id_meta["bob"] = {
                    "face_id": "fid_b", "first_seen": 0.0, "last_seen": 0.0,
                    "name_confidence": 0.1,  # untrusted（默认阈值 0.3）
                }
            # 让 alice 与 bob 距画面中心一样, 都不会因 dx/dy 显著拉开 score
            # alice 在画面中心 (0,0 偏移最小); bob 也几乎一样 → penalty 决定排序
            boxes = [_make_box(100, 100, 50, 50), _make_box(105, 105, 50, 50)]
            names = ["alice", "bob"]
            payload = ft.arbitrate_faces(boxes, names, 240, 240, ts=time.monotonic())
            assert payload is not None
            cands = payload["candidates"]
            bob_score = next(c["score"] for c in cands if c["name"] == "bob")
            alice_score = next(c["score"] for c in cands if c["name"] == "alice")
            penalty_99_ok = (
                # bob 是 untrusted 应被加 99, 不是 1e6
                bob_score < 200  # 99 + 几乎 0 的 baseline
                and bob_score > 90
                and payload["primary_name"] == "alice"  # alice 仍胜（penalty 把 bob 推后）
                and abs(ft._face_id_untrusted_penalty - 99.0) < 1e-9
            )
            ok_a = penalty_99_ok
        # case B: penalty=0 → 关闭降权 (untrusted 不再被加 penalty)
        _clean_env()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "face_id_map.json"
            _set_env(
                COCO_FACE_ID_REAL="1",
                COCO_FACE_ID_PERSIST="1",
                COCO_FACE_ID_ARBIT="1",
                COCO_FACE_ID_MAP_GC="1",
                COCO_FACE_ID_MAP_PATH=str(path),
                COCO_FACE_ID_UNTRUSTED_PENALTY="0",
            )
            ft0 = _fresh_tracker()
            with ft0._face_id_lock:
                ft0._face_id_map["alice"] = "fid_a"
                ft0._face_id_map["bob"] = "fid_b"
                ft0._face_id_meta["alice"] = {
                    "face_id": "fid_a", "first_seen": 0.0, "last_seen": 0.0,
                    "name_confidence": 0.9,
                }
                ft0._face_id_meta["bob"] = {
                    "face_id": "fid_b", "first_seen": 0.0, "last_seen": 0.0,
                    "name_confidence": 0.1,
                }
            # bob 更接近中心 → 关闭 penalty 时 bob 胜
            boxes2 = [_make_box(200, 200, 40, 40), _make_box(110, 110, 40, 40)]
            names2 = ["alice", "bob"]
            payload2 = ft0.arbitrate_faces(boxes2, names2, 240, 240, ts=time.monotonic())
            assert payload2 is not None
            bob_score2 = next(c["score"] for c in payload2["candidates"] if c["name"] == "bob")
            ok_b = (
                bob_score2 < 10  # 几乎 0 (无 penalty 添加)
                and payload2["primary_name"] == "bob"
                and ft0._face_id_untrusted_penalty == 0.0
            )

        # case C: regression - 子进程跑 verify_vision_011
        _clean_env()
        env = dict(os.environ)
        for k in _ENV_KEYS:
            env.pop(k, None)
        rc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "verify_vision_011.py")],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        ok_c = rc.returncode == 0
        ok = ok_a and ok_b and ok_c
        _record(
            "V5 penalty env 化 (99=参与, 0=关闭) + regression verify_vision_011",
            ok,
            f"penalty_99_ok={ok_a} penalty_0_ok={ok_b} "
            f"v011_rc={rc.returncode}",
        )
    finally:
        _clean_env()


def main() -> int:
    v1_default_frame_trigger()
    v2_time_trigger_low_fps()
    v3_double_trigger_throttled()
    v4_untrusted_threshold_env()
    v5_penalty_env_and_regression()
    print("\n--- summary ---", flush=True)
    failed = [r for r in _results if not r["ok"]]
    print(json.dumps({"total": len(_results), "failed": len(failed),
                      "results": _results}, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
