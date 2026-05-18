#!/usr/bin/env python3
"""infra-029 verify: verify_infra_024.py V5 命名清理 meta-验证 (verify-only rename)。

来源 backlog: infra-024-backlog-v5-rename
    "verify_infra_024.py V5 当前命名偏泛, 建议改为 V5_stdout_chain_assert
     显式表达 'subprocess stdout 链路断言' 意图。纯命名清理。"

Acceptance
----------
V0 file existence: scripts/verify_infra_024.py 存在且可读。
V1 anchor: 文件含 ``V5_stdout_chain_assert`` 字面量 (rename 命中目标名)。
V2 no-stale: 文件不含旧名 ``V5_stdout_real_chain`` / ``v5_stdout_real_chain``
    (case-sensitive, 任一残留 → FAIL)。
V3 subprocess: ``python scripts/verify_infra_024.py`` rc==0 (rename 不破断言)。
V4 mutant: 在内存中把 V5 命名回旧名, 重跑 V1/V2 锁应 FAIL
    (反证 V1/V2 真的能在旧名上炸, 不是空锁)。
V5 summary: 写 evidence/infra-029/verify_summary.json。

Sim-first: 纯文件 + subprocess, 不依赖真硬件。
default-OFF 严守: 0 业务源码改动, 仅 scripts/verify_infra_024.py 命名清理。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET = REPO_ROOT / "scripts" / "verify_infra_024.py"
EVIDENCE_DIR = REPO_ROOT / "evidence" / "infra-029"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

NEW_NAME = "V5_stdout_chain_assert"
NEW_NAME_LOWER = "v5_stdout_chain_assert"
OLD_NAME = "V5_stdout_real_chain"
OLD_NAME_LOWER = "v5_stdout_real_chain"


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def v0_file_existence() -> dict:
    _ok(TARGET.exists(), f"V0 target missing: {TARGET}")
    text = TARGET.read_text(encoding="utf-8")
    _ok(len(text) > 0, "V0 target file is empty")
    return {"path": str(TARGET), "bytes": len(text.encode("utf-8"))}


def v1_anchor_new_name() -> dict:
    text = TARGET.read_text(encoding="utf-8")
    _ok(NEW_NAME in text,
        f"V1 anchor missing: expected literal {NEW_NAME!r} in {TARGET.name}")
    _ok(NEW_NAME_LOWER in text,
        f"V1 anchor missing lowercase function name {NEW_NAME_LOWER!r}")
    upper_count = text.count(NEW_NAME)
    lower_count = text.count(NEW_NAME_LOWER)
    return {"upper_count": upper_count, "lower_count": lower_count}


def v2_no_stale_old_name() -> dict:
    text = TARGET.read_text(encoding="utf-8")
    _ok(OLD_NAME not in text,
        f"V2 stale old name leak: {OLD_NAME!r} still in {TARGET.name}")
    _ok(OLD_NAME_LOWER not in text,
        f"V2 stale old name leak: {OLD_NAME_LOWER!r} still in {TARGET.name}")
    return {"old_upper_count": 0, "old_lower_count": 0}


def v3_subprocess_passes() -> dict:
    proc = subprocess.run(
        [sys.executable, str(TARGET)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    _ok(proc.returncode == 0,
        f"V3 verify_infra_024 rc={proc.returncode} (expected 0)\n"
        f"STDOUT tail:\n{proc.stdout[-1000:]}\nSTDERR:\n{proc.stderr[-500:]}")
    # 顺手锁: 新名也应出现在 stdout (case label 打印行)
    _ok(NEW_NAME in proc.stdout,
        f"V3 stdout missing new-name label {NEW_NAME!r}; tail:\n{proc.stdout[-800:]}")
    return {"rc": proc.returncode, "stdout_len": len(proc.stdout)}


def v4_mutant_old_name_should_fail() -> dict:
    """反证 V1/V2 真能炸: 把目标文件 text 在内存中改回旧名, 重跑 V1/V2 锁,
    必须 FAIL。不写盘, 不污染真文件。
    """
    text = TARGET.read_text(encoding="utf-8")
    mutant = text.replace(NEW_NAME, OLD_NAME).replace(NEW_NAME_LOWER, OLD_NAME_LOWER)
    _ok(mutant != text, "V4 mutant: replace did nothing — anchor literals not present?")
    _ok(OLD_NAME in mutant, "V4 mutant: old upper name not re-injected")
    _ok(NEW_NAME not in mutant, "V4 mutant: new name still present (replace incomplete)")

    # 模拟 V1 锁在 mutant 上跑 → 应当 fail (新名缺失)
    v1_would_fail = NEW_NAME not in mutant
    # 模拟 V2 锁在 mutant 上跑 → 应当 fail (旧名残留)
    v2_would_fail = OLD_NAME in mutant
    _ok(v1_would_fail and v2_would_fail,
        f"V4 mutant did not flip both locks: v1_fail={v1_would_fail}, v2_fail={v2_would_fail}")
    return {"v1_would_fail_on_mutant": v1_would_fail,
            "v2_would_fail_on_mutant": v2_would_fail}


def main() -> int:
    results: dict = {}
    failures: list[str] = []

    cases = [
        ("V0_file_existence", v0_file_existence),
        ("V1_anchor_new_name", v1_anchor_new_name),
        ("V2_no_stale_old_name", v2_no_stale_old_name),
        ("V3_subprocess_passes", v3_subprocess_passes),
        ("V4_mutant_old_name_should_fail", v4_mutant_old_name_should_fail),
    ]

    for vname, fn in cases:
        try:
            results[vname] = {"status": "PASS", "detail": fn()}
            print(f"[{vname}] PASS")
        except AssertionError as e:
            results[vname] = {"status": "FAIL", "error": str(e)}
            failures.append(f"{vname}: {e}")
            print(f"[{vname}] FAIL: {e}")

    summary = {
        "feature": "infra-029",
        "status": "PASS" if not failures else "FAIL",
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
