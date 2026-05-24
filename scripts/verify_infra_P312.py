#!/usr/bin/env python3
"""verify_infra_P312 V1-V5: 锁 ``bump_strict_unknown_sha`` helper 行为与函数体.

infra-P312-strict-unknown-sha-auto-bump (phase-66 #3):

V6 strict-area-match V3 锁 (``EXPECTED_STRICT_UNKNOWN_SHA256`` + COUNT) 每次新增
``verify_<area>_<NNN>.py`` 都会漂移, 之前需手动 bump。``bump_strict_unknown_sha.py``
把这步机械化 (dry-run / apply / --verify 三档)。本 verify 锁该 helper:

INFRA_P312_LOCKS
----------------
- ``scripts/bump_strict_unknown_sha.py`` 文件 sha (V1): EXPECTED_BUMP_HELPER_FILE_SHA
- ``compute_current`` func sha (V2): EXPECTED_COMPUTE_CURRENT_FUNC_SHA
- ``read_expected_from_v6`` func sha (V3): EXPECTED_READ_EXPECTED_FUNC_SHA
- ``run_bump`` func sha (V4): EXPECTED_RUN_BUMP_FUNC_SHA
- V5 behavior: 当前 repo 无漂移 → dry-run 返回 "OK: 已是最新, 无需 bump"

校验层级 (V1-V5, 共 5 checks):
- V1: helper 文件 sha 锁 (任何无声修改即 FAIL)
- V2-V4: 三个核心 func sha 锁
- V5: dry-run 在当前 repo 上的行为 (no-op + rc=0)

退出码: 0=ALL PASS, 2=任一 FAIL.

## Lock: EXPECTED_BUMP_HELPER_FILE_SHA
- target_function: N/A
- target_file: scripts/bump_strict_unknown_sha.py
- lock_kind: content_sha256
- bump_when: bump_strict_unknown_sha.py 文件内容变化
- bump_protocol: ``python scripts/bump_reverse_sha_lock.py --target scripts/bump_strict_unknown_sha.py --apply --verify`` (跨 verify 反向锁通用 helper)
- rationale: 锁 helper 文件整体 sha, 防 helper 被悄改导致 cascade bump 行为漂移

## Lock: EXPECTED_COMPUTE_CURRENT_FUNC_SHA
- target_function: compute_current
- target_file: scripts/bump_strict_unknown_sha.py
- lock_kind: ast_func_sha
- bump_when: compute_current 实现变化
- bump_protocol: recompute func_sha_by_name("compute_current", scripts/bump_strict_unknown_sha.py) then update constant
- rationale: 锁算新值核心函数

## Lock: EXPECTED_READ_EXPECTED_FUNC_SHA
- target_function: read_expected_from_v6
- target_file: scripts/bump_strict_unknown_sha.py
- lock_kind: ast_func_sha
- bump_when: read_expected_from_v6 实现变化
- bump_protocol: recompute func_sha_by_name("read_expected_from_v6", scripts/bump_strict_unknown_sha.py) then update constant
- rationale: 锁读现状函数

## Lock: EXPECTED_RUN_BUMP_FUNC_SHA
- target_function: run_bump
- target_file: scripts/bump_strict_unknown_sha.py
- lock_kind: ast_func_sha
- bump_when: run_bump 实现变化
- bump_protocol: recompute func_sha_by_name("run_bump", scripts/bump_strict_unknown_sha.py) then update constant
- rationale: 锁主流程函数
"""
from __future__ import annotations

import subprocess
import sys
import hashlib
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
HELPER = SCRIPTS / "bump_strict_unknown_sha.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import func_sha_by_name, verify_summary_exit  # noqa: E402

# V1: helper file sha
EXPECTED_BUMP_HELPER_FILE_SHA = (
    "dd6654be77b23cc9ae27238df8d06b4ea85680114cf6510f2a46a1bcc410d3af"
)
# V2-V4: helper 核心 func sha
EXPECTED_COMPUTE_CURRENT_FUNC_SHA = (
    "5dc0d39e30f5688abc6405b2610e31ead6ad65390ca57d3aca107ad14a54beac"
)
EXPECTED_READ_EXPECTED_FUNC_SHA = (
    "971acf212eb879df45769af39079e9178056a24cb937a38fd0e907e08dd1ccdb"
)
EXPECTED_RUN_BUMP_FUNC_SHA = (
    "255233639003759225c37c044b36b1d1637c1e0a2f49abf8b01f99907f6ffd49"
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P312][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, bool(ok), detail))


def v1_helper_file_sha() -> None:
    if not HELPER.is_file():
        _emit("V1_helper_file_sha", False, f"{HELPER} not found")
        return
    actual = hashlib.sha256(HELPER.read_bytes()).hexdigest()
    ok = actual == EXPECTED_BUMP_HELPER_FILE_SHA
    _emit(
        "V1_helper_file_sha",
        ok,
        f"expected={EXPECTED_BUMP_HELPER_FILE_SHA[:16]} actual={actual[:16]}",
    )


def v2_compute_current_func_sha() -> None:
    actual = func_sha_by_name(HELPER, "compute_current")
    ok = actual == EXPECTED_COMPUTE_CURRENT_FUNC_SHA
    _emit("V2_compute_current_func_sha", ok, f"expected={EXPECTED_COMPUTE_CURRENT_FUNC_SHA[:16]} actual={actual[:16]}")


def v3_read_expected_func_sha() -> None:
    actual = func_sha_by_name(HELPER, "read_expected_from_v6")
    ok = actual == EXPECTED_READ_EXPECTED_FUNC_SHA
    _emit("V3_read_expected_func_sha", ok, f"expected={EXPECTED_READ_EXPECTED_FUNC_SHA[:16]} actual={actual[:16]}")


def v4_run_bump_func_sha() -> None:
    actual = func_sha_by_name(HELPER, "run_bump")
    ok = actual == EXPECTED_RUN_BUMP_FUNC_SHA
    _emit("V4_run_bump_func_sha", ok, f"expected={EXPECTED_RUN_BUMP_FUNC_SHA[:16]} actual={actual[:16]}")


def v5_dry_run_noop_behavior() -> None:
    """当前 repo 无漂移 → dry-run 应返回 rc=0 + "已是最新" 字符串."""
    if not HELPER.is_file():
        _emit("V5_dry_run_noop", False, "helper missing")
        return
    proc = subprocess.run(
        [sys.executable, str(HELPER)],  # 默认 dry-run
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = proc.stdout
    ok = proc.returncode == 0 and "已是最新" in out
    _emit(
        "V5_dry_run_noop_behavior",
        ok,
        f"rc={proc.returncode} has_noop_msg={'已是最新' in out}",
    )


def main() -> int:
    v1_helper_file_sha()
    v2_compute_current_func_sha()
    v3_read_expected_func_sha()
    v4_run_bump_func_sha()
    v5_dry_run_noop_behavior()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(
        f"[verify_infra_P312][SUMMARY] "
        f"{'FAIL ' + str(failed) + '/' + str(total) if failed else 'ALL PASS (' + str(total) + ' checks)'}",
        flush=True,
    )
    verify_summary_exit(failed)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
