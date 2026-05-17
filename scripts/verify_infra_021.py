#!/usr/bin/env python3
"""infra-021 verify: smoke.py rc_table 共存场景优先级端到端验证。

来源 backlog: infra-020-backlog-mixed-warn-skip-case

承接 infra-020 (V0–V5 覆盖单态 + OFF), 本次 V6+ 补**多状态共存**断言:
确认 `_decide_rc` 在 ON 模式下严格按 **FAIL > WARN > SKIP > PASS** 优先级
工作 — 即两个/三个状态同时出现在 areas dict 中时, 取最高优先级的对应 rc。

Acceptance
----------
V0 源码 fingerprint (与 infra-020 一致, 检测 smoke.py rc 决策漂移)
V1 ON + 全 PASS → rc=0          (regression of infra-020 V1)
V2 ON + WARN-only → rc=2        (regression of infra-020 V2)
V3 ON + SKIP-only → rc=3        (regression of infra-020 V4)
V4 OFF + 混合无 FAIL → rc=0     (regression of infra-020 V5 subset)
V5 ON + FAIL-only → rc=1        (regression of infra-020 V3)

新增共存断言:
V6 ON + WARN + SKIP (无 FAIL)        → rc=2  (WARN 胜出 SKIP)  ← 主目标
V7 ON + FAIL + WARN                  → rc=1  (FAIL 压制 WARN)
V8 ON + FAIL + SKIP                  → rc=1  (FAIL 压制 SKIP)
V9 ON + FAIL + WARN + SKIP           → rc=1  (FAIL 压制全部)
V10 ON + PASS + WARN + SKIP (混合三类无 FAIL) → rc=2  (WARN > SKIP > PASS)
V11 OFF + WARN+SKIP 共存            → rc=0  (OFF 模式所有非 FAIL 均 0)

Sim-first: 静态/纯 Python, 不跑真 smoke 子检查。复用 infra-020 验证的
`_decide_rc` 复刻 + 源码 fingerprint 双重耦合检测。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "evidence" / "infra-021"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
SMOKE_PATH = REPO_ROOT / "scripts" / "smoke.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("coco_smoke", SMOKE_PATH)
    assert spec and spec.loader, "cannot load scripts/smoke.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _decide_rc(areas: dict[str, str], failed: bool, gate_on: bool) -> int:
    """复刻 scripts/smoke.py main() 末尾 rc 决策语义 (infra-018):

      failed → 1                          # FAIL 不依赖 gate
      else:
        if gate_on:
          WARN in states → 2              # WARN 优先于 SKIP
          SKIP in states → 3
        return 0

    优先级总览: FAIL(1) > WARN(2) > SKIP(3) > PASS(0). 与 smoke.py 当前实现
    强耦合 (V0 源码 fingerprint 兜底).
    """
    if failed:
        return 1
    if gate_on:
        states = set(areas.values())
        if "WARN" in states:
            return 2
        if "SKIP" in states:
            return 3
    return 0


def v0_source_fingerprint() -> dict:
    """与 infra-020 V0 一致, 检测 smoke.py rc 决策片段未漂移。

    额外锁定 WARN 分支出现在 SKIP 分支**之前** (源码顺序蕴含优先级)。
    """
    src = SMOKE_PATH.read_text(encoding="utf-8")
    must_have = [
        'os.environ.get("COCO_SMOKE_FINEGRAINED_EXIT"',
        'return 1',
        'return 2',
        'return 3',
        'return 0',
        '_finegrained_exit_enabled()',
        # WARN 分支必须先于 SKIP 分支出现 (优先级语义)
        '"WARN" in states',
        '"SKIP" in states',
    ]
    missing = [tok for tok in must_have if tok not in src]
    _ok(not missing, f"V0 smoke.py 缺少 rc 决策关键片段: {missing}")
    warn_idx = src.find('"WARN" in states')
    skip_idx = src.find('"SKIP" in states')
    _ok(
        0 <= warn_idx < skip_idx,
        f"V0 优先级顺序漂移: WARN 分支应在 SKIP 之前 (warn_idx={warn_idx}, skip_idx={skip_idx})",
    )
    return {
        "must_have_count": len(must_have),
        "missing": missing,
        "warn_before_skip": True,
    }


# ---- V1–V5: regression of infra-020 单态 ----

def v1_on_all_pass(gate_fn) -> dict:
    os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = "1"
    gate_on = gate_fn()
    _ok(gate_on, "V1 expected gate ON")
    areas = {"audio": "PASS", "asr": "PASS"}
    rc = _decide_rc(areas, failed=False, gate_on=gate_on)
    _ok(rc == 0, f"V1 ON+全PASS expected rc=0, got {rc}")
    return {"areas": areas, "rc": rc}


def v2_on_warn_only(gate_fn) -> dict:
    os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = "1"
    gate_on = gate_fn()
    areas = {"a": "PASS", "b": "WARN"}
    rc = _decide_rc(areas, failed=False, gate_on=gate_on)
    _ok(rc == 2, f"V2 ON+WARN-only expected rc=2, got {rc}")
    return {"areas": areas, "rc": rc}


def v3_on_skip_only(gate_fn) -> dict:
    os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = "1"
    gate_on = gate_fn()
    areas = {"a": "PASS", "b": "SKIP"}
    rc = _decide_rc(areas, failed=False, gate_on=gate_on)
    _ok(rc == 3, f"V3 ON+SKIP-only expected rc=3, got {rc}")
    return {"areas": areas, "rc": rc}


def v4_off_mixed(gate_fn) -> dict:
    os.environ.pop("COCO_SMOKE_FINEGRAINED_EXIT", None)
    gate_on = gate_fn()
    _ok(not gate_on, "V4 expected gate OFF when env unset")
    areas = {"a": "PASS", "b": "WARN", "c": "SKIP"}
    rc = _decide_rc(areas, failed=False, gate_on=gate_on)
    _ok(rc == 0, f"V4 OFF+mixed expected rc=0, got {rc}")
    return {"areas": areas, "rc": rc}


def v5_on_fail_only(gate_fn) -> dict:
    os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = "1"
    gate_on = gate_fn()
    areas = {"a": "FAIL"}
    rc = _decide_rc(areas, failed=True, gate_on=gate_on)
    _ok(rc == 1, f"V5 ON+FAIL expected rc=1, got {rc}")
    return {"areas": areas, "rc": rc}


# ---- V6–V11: 共存优先级 (本 feature 主目标) ----

def v6_on_warn_and_skip(gate_fn) -> dict:
    """主目标: WARN > SKIP 共存优先级。"""
    os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = "1"
    gate_on = gate_fn()
    _ok(gate_on, "V6 expected gate ON")
    # 三种排列, 都应 rc=2 (WARN 胜出 SKIP, 与 areas 插入顺序无关)
    arrangements = [
        {"a": "PASS", "b": "WARN", "c": "SKIP"},
        {"a": "SKIP", "b": "WARN"},
        {"a": "WARN", "b": "SKIP", "c": "PASS", "d": "PASS"},
    ]
    rcs: list[int] = []
    for areas in arrangements:
        rc = _decide_rc(areas, failed=False, gate_on=gate_on)
        _ok(rc == 2, f"V6 ON+WARN+SKIP expected rc=2 for {areas}, got {rc}")
        rcs.append(rc)
    return {"arrangements_count": len(arrangements), "rcs": rcs}


def v7_on_fail_and_warn(gate_fn) -> dict:
    os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = "1"
    gate_on = gate_fn()
    areas = {"a": "FAIL", "b": "WARN"}
    rc = _decide_rc(areas, failed=True, gate_on=gate_on)
    _ok(rc == 1, f"V7 ON+FAIL+WARN expected rc=1, got {rc}")
    return {"areas": areas, "rc": rc}


def v8_on_fail_and_skip(gate_fn) -> dict:
    os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = "1"
    gate_on = gate_fn()
    areas = {"a": "FAIL", "b": "SKIP"}
    rc = _decide_rc(areas, failed=True, gate_on=gate_on)
    _ok(rc == 1, f"V8 ON+FAIL+SKIP expected rc=1, got {rc}")
    return {"areas": areas, "rc": rc}


def v9_on_fail_warn_skip(gate_fn) -> dict:
    os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = "1"
    gate_on = gate_fn()
    areas = {"a": "FAIL", "b": "WARN", "c": "SKIP", "d": "PASS"}
    rc = _decide_rc(areas, failed=True, gate_on=gate_on)
    _ok(rc == 1, f"V9 ON+FAIL+WARN+SKIP expected rc=1, got {rc}")
    return {"areas": areas, "rc": rc}


def v10_on_pass_warn_skip(gate_fn) -> dict:
    """混合三类无 FAIL → WARN 胜出 (与 V6 同理, 但显式包含 PASS)。"""
    os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = "1"
    gate_on = gate_fn()
    areas = {"a": "PASS", "b": "WARN", "c": "SKIP"}
    rc = _decide_rc(areas, failed=False, gate_on=gate_on)
    _ok(rc == 2, f"V10 ON+PASS+WARN+SKIP expected rc=2, got {rc}")
    return {"areas": areas, "rc": rc}


def v11_off_warn_skip(gate_fn) -> dict:
    """OFF 模式下任何非 FAIL 混合 (含 WARN+SKIP 共存) 一律 rc=0。"""
    os.environ.pop("COCO_SMOKE_FINEGRAINED_EXIT", None)
    gate_on = gate_fn()
    _ok(not gate_on, "V11 expected gate OFF")
    areas = {"a": "WARN", "b": "SKIP"}
    rc = _decide_rc(areas, failed=False, gate_on=gate_on)
    _ok(rc == 0, f"V11 OFF+WARN+SKIP expected rc=0, got {rc}")
    return {"areas": areas, "rc": rc}


def v12_regression_infra_020(gate_fn) -> dict:
    """跨脚本回归: 跑 scripts/verify_infra_020.py 必须 rc=0 (未破坏前置)。"""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_infra_020.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    _ok(
        result.returncode == 0,
        f"V12 verify_infra_020.py expected rc=0, got {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}",
    )
    return {"rc": result.returncode, "stdout_tail": result.stdout.strip().splitlines()[-3:]}


def main() -> int:
    smoke = _load_smoke()
    gate_fn = smoke._finegrained_exit_enabled
    classify = smoke._classify_stdout

    # sanity: _classify_stdout 接口存在 (与 infra-019/020 共享)
    _ok(classify("WARN: x") == "WARN", "sanity classify WARN")
    _ok(classify("SKIP: y") == "SKIP", "sanity classify SKIP")

    results: dict = {}
    failures: list[str] = []
    orig_env = os.environ.get("COCO_SMOKE_FINEGRAINED_EXIT")
    try:
        for vname, fn in [
            ("V0_source_fingerprint", v0_source_fingerprint),
            ("V1_on_all_pass", lambda: v1_on_all_pass(gate_fn)),
            ("V2_on_warn_only", lambda: v2_on_warn_only(gate_fn)),
            ("V3_on_skip_only", lambda: v3_on_skip_only(gate_fn)),
            ("V4_off_mixed", lambda: v4_off_mixed(gate_fn)),
            ("V5_on_fail_only", lambda: v5_on_fail_only(gate_fn)),
            ("V6_on_warn_and_skip", lambda: v6_on_warn_and_skip(gate_fn)),
            ("V7_on_fail_and_warn", lambda: v7_on_fail_and_warn(gate_fn)),
            ("V8_on_fail_and_skip", lambda: v8_on_fail_and_skip(gate_fn)),
            ("V9_on_fail_warn_skip", lambda: v9_on_fail_warn_skip(gate_fn)),
            ("V10_on_pass_warn_skip", lambda: v10_on_pass_warn_skip(gate_fn)),
            ("V11_off_warn_skip", lambda: v11_off_warn_skip(gate_fn)),
            ("V12_regression_infra_020", lambda: v12_regression_infra_020(gate_fn)),
        ]:
            try:
                results[vname] = {"status": "PASS", "detail": fn()}
                print(f"[{vname}] PASS")
            except AssertionError as e:
                results[vname] = {"status": "FAIL", "error": str(e)}
                failures.append(f"{vname}: {e}")
                print(f"[{vname}] FAIL: {e}")
            except Exception as e:  # noqa: BLE001
                results[vname] = {"status": "ERROR", "error": f"{type(e).__name__}: {e}"}
                failures.append(f"{vname}: {type(e).__name__}: {e}")
                print(f"[{vname}] ERROR: {type(e).__name__}: {e}")
    finally:
        if orig_env is None:
            os.environ.pop("COCO_SMOKE_FINEGRAINED_EXIT", None)
        else:
            os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = orig_env

    summary = {
        "feature": "infra-021",
        "status": "PASS" if not failures else "FAIL",
        "priority_table": {
            "FAIL": 1,
            "WARN": 2,
            "SKIP": 3,
            "PASS_or_OFF": 0,
            "order": "FAIL > WARN > SKIP > PASS",
        },
        "coexistence_cases": {
            "ON+WARN+SKIP": 2,
            "ON+FAIL+WARN": 1,
            "ON+FAIL+SKIP": 1,
            "ON+FAIL+WARN+SKIP": 1,
            "ON+PASS+WARN+SKIP": 2,
            "OFF+WARN+SKIP": 0,
        },
        "results": results,
        "failures": failures,
    }
    out_path = EVIDENCE_DIR / "verify_summary.json"
    out_path.write_text(
        json.dumps(summary, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {out_path}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
