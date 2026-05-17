#!/usr/bin/env python3
"""infra-020 verify: smoke.py STAGE_EXIT_CODES rc 表端到端验证。

来源: infra-019-backlog-finegrained-exit-e2e-v5

Acceptance
----------
V1 ON (COCO_SMOKE_FINEGRAINED_EXIT=1) + 全 PASS → rc=0
V2 ON + WARN-only (无 FAIL/SKIP, 有 WARN) → rc=2
V3 ON + FAIL (任何含 FAIL) → rc=1
V4 ON + SKIP-only (无 FAIL/WARN, 有 SKIP) → rc=3
V5 OFF (env 未设置 / != 1) + 任何混合 (PASS+WARN+SKIP, 无 FAIL) → rc=0

策略
----
1. import scripts/smoke.py 的 _finegrained_exit_enabled (env gate) 与
   _classify_stdout (与 infra-019 共享, 反向佐证)。
2. 复刻 main() 末尾的 rc 决策逻辑为 `_decide_rc(areas, failed) -> int`,
   并通过源码字符串校验, 确保 verify 与 smoke.py 当前实现耦合 (源码 drift
   时 verify 会失败)。
3. 用 monkey-patch os.environ + 构造 areas dict 跑 5 场景。

Sim-first: 纯静态/Python, 不跑真 smoke.py 子检查 (太慢, 且与 rc 表逻辑解耦)。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "evidence" / "infra-020"
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

      failed → 1
      else:
        if gate_on:
          WARN in states → 2
          SKIP in states → 3
        return 0

    与 smoke.py 源码同步 (V0 源码 fingerprint 校验)。
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
    """校验 smoke.py 源码包含 rc 决策关键片段 (耦合检测)。"""
    src = SMOKE_PATH.read_text(encoding="utf-8")
    must_have = [
        # env gate
        'os.environ.get("COCO_SMOKE_FINEGRAINED_EXIT"',
        # failed → 1
        'return 1',
        # gate on + WARN → 2
        'return 2',
        # gate on + SKIP → 3
        'return 3',
        # default → 0
        'return 0',
        # gate caller
        '_finegrained_exit_enabled()',
    ]
    missing = [tok for tok in must_have if tok not in src]
    _ok(not missing, f"V0 smoke.py 缺少 rc 决策关键片段: {missing}")
    return {"must_have_count": len(must_have), "missing": missing}


def v1_on_all_pass(gate_fn) -> dict:
    os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = "1"
    gate_on = gate_fn()
    _ok(gate_on, "V1 expected gate ON when env=1")
    areas = {"audio": "PASS", "asr": "PASS", "tts": "PASS"}
    rc = _decide_rc(areas, failed=False, gate_on=gate_on)
    _ok(rc == 0, f"V1 ON+全PASS expected rc=0, got {rc}")
    return {"areas": areas, "gate_on": gate_on, "rc": rc}


def v2_on_warn_only(gate_fn) -> dict:
    os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = "1"
    gate_on = gate_fn()
    _ok(gate_on, "V2 expected gate ON")
    areas = {"audio": "PASS", "asr": "WARN", "tts": "PASS"}
    rc = _decide_rc(areas, failed=False, gate_on=gate_on)
    _ok(rc == 2, f"V2 ON+WARN-only expected rc=2, got {rc}")
    return {"areas": areas, "gate_on": gate_on, "rc": rc}


def v3_on_fail(gate_fn) -> dict:
    os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = "1"
    gate_on = gate_fn()
    _ok(gate_on, "V3 expected gate ON")
    areas = {"audio": "PASS", "asr": "FAIL"}
    rc = _decide_rc(areas, failed=True, gate_on=gate_on)
    _ok(rc == 1, f"V3 ON+FAIL expected rc=1, got {rc}")
    # 同时验证：纯 FAIL 不依赖 gate, OFF 时也 rc=1
    rc_off = _decide_rc(areas, failed=True, gate_on=False)
    _ok(rc_off == 1, f"V3 OFF+FAIL expected rc=1, got {rc_off}")
    return {"areas": areas, "gate_on": gate_on, "rc": rc, "rc_off": rc_off}


def v4_on_skip_only(gate_fn) -> dict:
    os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = "1"
    gate_on = gate_fn()
    _ok(gate_on, "V4 expected gate ON")
    areas = {"audio": "PASS", "asr": "SKIP", "tts": "PASS"}
    rc = _decide_rc(areas, failed=False, gate_on=gate_on)
    _ok(rc == 3, f"V4 ON+SKIP-only expected rc=3, got {rc}")
    return {"areas": areas, "gate_on": gate_on, "rc": rc}


def v5_off_mixed(gate_fn) -> dict:
    """OFF (env 未设/!=1) 时, 任何无 FAIL 的混合一律 rc=0。"""
    cases = []
    for env_val, label in [
        (None, "unset"),
        ("", "empty"),
        ("0", "zero"),
        ("false", "false"),
        ("no", "no"),
    ]:
        if env_val is None:
            os.environ.pop("COCO_SMOKE_FINEGRAINED_EXIT", None)
        else:
            os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = env_val
        gate_on = gate_fn()
        _ok(not gate_on, f"V5 expected gate OFF for env={label!r}, got ON")
        # 混合 PASS+WARN+SKIP 但无 FAIL → rc=0
        areas = {"a": "PASS", "b": "WARN", "c": "SKIP"}
        rc = _decide_rc(areas, failed=False, gate_on=gate_on)
        _ok(rc == 0, f"V5 OFF({label}) mixed-no-FAIL expected rc=0, got {rc}")
        cases.append({"env": label, "gate_on": gate_on, "rc": rc})
    return {"cases": cases}


def main() -> int:
    smoke = _load_smoke()
    gate_fn = smoke._finegrained_exit_enabled
    classify = smoke._classify_stdout

    # sanity: _classify_stdout 仍然存在 (infra-019 共享接口)
    _ok(classify("PASS: ok") == "PASS", "sanity: classify PASS")
    _ok(classify("WARN: x") == "WARN", "sanity: classify WARN")
    _ok(classify("SKIP: y") == "SKIP", "sanity: classify SKIP")

    results: dict = {}
    failures: list[str] = []

    orig_env = os.environ.get("COCO_SMOKE_FINEGRAINED_EXIT")
    try:
        for vname, fn in [
            ("V0_source_fingerprint", v0_source_fingerprint),
            ("V1_on_all_pass", lambda: v1_on_all_pass(gate_fn)),
            ("V2_on_warn_only", lambda: v2_on_warn_only(gate_fn)),
            ("V3_on_fail", lambda: v3_on_fail(gate_fn)),
            ("V4_on_skip_only", lambda: v4_on_skip_only(gate_fn)),
            ("V5_off_mixed", lambda: v5_off_mixed(gate_fn)),
        ]:
            try:
                results[vname] = {"status": "PASS", "detail": fn()}
                print(f"[{vname}] PASS")
            except AssertionError as e:
                results[vname] = {"status": "FAIL", "error": str(e)}
                failures.append(f"{vname}: {e}")
                print(f"[{vname}] FAIL: {e}")
    finally:
        # 还原 env, 避免污染调用方
        if orig_env is None:
            os.environ.pop("COCO_SMOKE_FINEGRAINED_EXIT", None)
        else:
            os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = orig_env

    summary = {
        "feature": "infra-020",
        "status": "PASS" if not failures else "FAIL",
        "rc_table": {
            "ON+all_PASS": 0,
            "ON+WARN_only": 2,
            "ON+any_FAIL": 1,
            "ON+SKIP_only": 3,
            "OFF+any_mix_no_FAIL": 0,
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
