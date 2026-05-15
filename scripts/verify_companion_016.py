#!/usr/bin/env python3
"""verify_companion_016 — PreferenceLearner emit 真节流（min_interval_s + content-hash + suppressed_since_last）。

V1 min_interval_s 节流：mock clock 1s 内 10 save → 1 emit
V2 content-hash dedup：相同 prefer state save 不 emit
V3 suppressed_since_last 字段累积正确
V4 env COCO_PERSIST_EMIT_MIN_INTERVAL_S 注入生效 + 非法值 fallback default + WARN once
V5 docstring 措辞：grep '非节流' 不命中
V6 companion-015 V1-V8 回归 PASS（schema 兼容）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS: List[Dict[str, Any]] = []


def _check(name: str, ok: bool, info: str = "") -> None:
    RESULTS.append({"name": name, "ok": bool(ok), "info": info})
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name} -- {info}")


# ---------------------------------------------------------------------------
def _make_learner(tmp_root: Path, captured: List[Dict[str, Any]], clock_holder: List[float]):
    from coco.companion.preference_learner import PreferenceLearner

    def emit(topic: str, *args: Any, **kw: Any) -> None:
        captured.append({"topic": topic, "kw": kw})

    def clock() -> float:
        return clock_holder[0]

    cache_path = tmp_root / "pref.json"
    learner = PreferenceLearner(state_cache_path=cache_path, emit_fn=emit, clock=clock)
    return learner


# V1: 1s 内 10 save → 1 emit
def v1_min_interval(tmp_root: Path) -> None:
    captured: List[Dict[str, Any]] = []
    clock = [1000.0]
    learner = _make_learner(tmp_root, captured, clock)
    # 初始化时已 emit 一条 load
    captured.clear()
    # 默认 10s interval；构造 10 次内容真变化（hash 不同），但同一窗口内只允许 1 个 save emit
    for i in range(10):
        with learner._lock:
            learner._state_cache["alice"] = {f"t{i}": float(i)}
        learner.flush_state()
        clock[0] += 0.1  # 1s 累计
    persist_emits = [e for e in captured if e["topic"] == "companion.preference_persisted"]
    save_emits = [e for e in persist_emits if e["kw"].get("action") == "save"]
    _check(
        "V1 min_interval_s 节流 (10 save in 1s -> 1 emit)",
        len(save_emits) == 1,
        f"save_emits_count={len(save_emits)} (expect 1)",
    )


# V2: content-hash dedup（相同 state save 不 emit；即便已过 interval 也跳）
def v2_content_hash_dedup(tmp_root: Path) -> None:
    captured: List[Dict[str, Any]] = []
    clock = [2000.0]
    learner = _make_learner(tmp_root, captured, clock)
    captured.clear()
    with learner._lock:
        learner._state_cache["bob"] = {"鱼": 0.8}
    learner.flush_state()  # 首次 save emit
    n_first = sum(1 for e in captured if e["topic"] == "companion.preference_persisted"
                  and e["kw"].get("action") == "save")
    # 跨过 interval，但内容完全相同
    clock[0] += 100.0
    learner.flush_state()
    n_second = sum(1 for e in captured if e["topic"] == "companion.preference_persisted"
                   and e["kw"].get("action") == "save")
    _check(
        "V2 content-hash dedup (相同 state 跨 interval 仍不 emit)",
        n_first == 1 and n_second == 1,
        f"first={n_first} second={n_second}",
    )


# V3: suppressed_since_last 累积
def v3_suppressed_since_last(tmp_root: Path) -> None:
    captured: List[Dict[str, Any]] = []
    clock = [3000.0]
    learner = _make_learner(tmp_root, captured, clock)
    captured.clear()
    # 第 1 次 emit save（state 设非空，触发 interval / hash 首发）
    with learner._lock:
        learner._state_cache["c"] = {"A": 1.0}
    learner.flush_state()  # save#1
    # 同窗口（不增 clock，min_interval=10s 默认）连续 3 次内容变化 → 全被节流
    for i in range(3):
        with learner._lock:
            learner._state_cache["c"] = {f"X{i}": 1.0}
        learner.flush_state()
        clock[0] += 0.01
    # 跨过 interval，触发下一次 emit
    clock[0] += 20.0
    with learner._lock:
        learner._state_cache["c"] = {"FINAL": 1.0}
    learner.flush_state()
    save_emits = [e["kw"] for e in captured
                  if e["topic"] == "companion.preference_persisted"
                  and e["kw"].get("action") == "save"]
    # 期望：2 条 save emit；第二条 suppressed_since_last == 3
    second_supp = save_emits[-1].get("suppressed_since_last") if len(save_emits) >= 2 else None
    _check(
        "V3 suppressed_since_last 累积正确",
        len(save_emits) == 2 and second_supp == 3,
        f"save_emits={len(save_emits)} second_supp={second_supp}",
    )


# V4: env 注入 + 非法值 fallback + WARN once
def v4_env_injection(tmp_root: Path) -> None:
    from coco.companion import preference_learner as pl_mod
    f = pl_mod.preference_persist_emit_min_interval_s_from_env
    DEFAULT = pl_mod._PERSIST_EMIT_MIN_INTERVAL_S_DEFAULT

    # 合法
    ok_legit = f({"COCO_PERSIST_EMIT_MIN_INTERVAL_S": "5"}) == 5.0
    ok_zero = f({"COCO_PERSIST_EMIT_MIN_INTERVAL_S": "0"}) == 0.0
    # 非法 → fallback
    pl_mod._PERSIST_EMIT_INTERVAL_WARN_ONCE = False  # 重置 warn-once
    v_neg = f({"COCO_PERSIST_EMIT_MIN_INTERVAL_S": "-1"})
    v_abc = f({"COCO_PERSIST_EMIT_MIN_INTERVAL_S": "abc"})
    v_empty = f({"COCO_PERSIST_EMIT_MIN_INTERVAL_S": ""})
    v_unset = f({})
    # WARN once: 第一次非法触发后置 True
    warn_set = pl_mod._PERSIST_EMIT_INTERVAL_WARN_ONCE is True
    ok_fallback = (v_neg == DEFAULT and v_abc == DEFAULT
                   and v_empty == DEFAULT and v_unset == DEFAULT)
    # 实例化时 env=5 → instance min_interval=5
    os.environ["COCO_PERSIST_EMIT_MIN_INTERVAL_S"] = "7"
    try:
        learner = pl_mod.PreferenceLearner(state_cache_path=tmp_root / "x.json")
        inst_ok = abs(learner._persist_emit_min_interval_s - 7.0) < 1e-6
    finally:
        os.environ.pop("COCO_PERSIST_EMIT_MIN_INTERVAL_S", None)
    _check(
        "V4 env 注入 + 非法 fallback + WARN once",
        ok_legit and ok_zero and ok_fallback and warn_set and inst_ok,
        f"legit={ok_legit} zero={ok_zero} fallback={ok_fallback} warn_set={warn_set} inst={inst_ok}",
    )


# V5: grep 非节流 不命中
def v5_docstring() -> None:
    p = ROOT / "coco" / "companion" / "preference_learner.py"
    txt = p.read_text(encoding="utf-8")
    hit = "非节流" in txt
    _check("V5 docstring 不再含 '非节流'", not hit, f"hit={hit}")


# V6: companion-015 V1-V8 回归 PASS（schema 兼容；V9/V10 是 vision-010 chain，与本 feature 无关）
def v6_regression() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_companion_015.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=240,
    )
    text = proc.stdout + proc.stderr
    # 仅检查 V1..V8 的状态行
    target_lines = [ln for ln in text.splitlines()
                    if (ln.startswith("[PASS]") or ln.startswith("[FAIL]"))
                    and any(f" V{i} " in ln for i in range(1, 9))]
    v18_pass = [ln for ln in target_lines if ln.startswith("[PASS]")]
    v18_fail = [ln for ln in target_lines if ln.startswith("[FAIL]")]
    ok = len(v18_fail) == 0 and len(v18_pass) >= 8
    info = f"v1-v8 pass={len(v18_pass)} fail={len(v18_fail)}"
    if v18_fail:
        info += " | " + " || ".join(v18_fail)
    _check("V6 regression companion-015 V1-V8 PASS (schema 兼容)", ok, info)


# ---------------------------------------------------------------------------
def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp_root = Path(td)
        v1_min_interval(tmp_root)
        v2_content_hash_dedup(tmp_root)
        v3_suppressed_since_last(tmp_root)
        v4_env_injection(tmp_root)
        v5_docstring()
        v6_regression()

    summary = {
        "feature_id": "companion-016",
        "checks": RESULTS,
        "passed": sum(1 for r in RESULTS if r["ok"]),
        "total": len(RESULTS),
    }
    out_dir = ROOT / "evidence" / "companion-016"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    all_ok = all(r["ok"] for r in RESULTS)
    print(f"\n{'OK' if all_ok else 'FAIL'}: {summary['passed']}/{summary['total']}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
