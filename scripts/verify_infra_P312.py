#!/usr/bin/env python3
"""verify_infra_P312 V1-V7: 锁 ``bump_strict_unknown_sha`` helper 行为与函数体.

infra-P312-strict-unknown-sha-auto-bump (phase-66 #3) +
infra-V20-strict-unknown-bump-cascade (phase-66 #4):

V6 strict-area-match V3 锁 (``EXPECTED_STRICT_UNKNOWN_SHA256`` + COUNT) 每次新增
``verify_<area>_<NNN>.py`` 都会漂移, 之前需手动 bump。``bump_strict_unknown_sha.py``
把这步机械化 (dry-run / apply / --verify / --cascade 四档)。本 verify 锁该 helper:

INFRA_P312_LOCKS
----------------
- ``scripts/bump_strict_unknown_sha.py`` 文件 sha (V1): EXPECTED_BUMP_HELPER_FILE_SHA
- ``compute_current`` func sha (V2): EXPECTED_COMPUTE_CURRENT_FUNC_SHA
- ``read_expected_from_v6`` func sha (V3): EXPECTED_READ_EXPECTED_FUNC_SHA
- ``run_bump`` func sha (V4): EXPECTED_RUN_BUMP_FUNC_SHA
- V5 behavior: 当前 repo 无漂移 → dry-run 返回 "OK: 已是最新, 无需 bump"
- V6 cascade structure: ``main`` 含 ``--cascade`` argparse + ``run_cascade``
  函数包含 ``bump_reverse_sha_lock.py`` 字面量调用 (AST 锁)
- V7 cascade behavior: ``--cascade`` 选项 no-op 路径 (当前 repo 无漂移) rc=0,
  对 bump_reverse_sha_lock 的 subprocess.run 调用应被跳过 (因为 V6 sha 没变,
  run_bump 早返 rc=0 之前不会触发 cascade) — 但更直接的锁是断言 ``main``
  AST 中存在 ``args.cascade`` 节点 (静态检查)

## Lock: EXPECTED_BUMP_HELPER_FILE_SHA
- target_function: N/A
- target_file: scripts/bump_strict_unknown_sha.py
- lock_kind: content_sha256
- bump_when: bump_strict_unknown_sha.py 文件内容变化
- bump_protocol: ``python scripts/bump_reverse_sha_lock.py --target scripts/bump_strict_unknown_sha.py --apply --verify``
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

## Lock: EXPECTED_RUN_CASCADE_FUNC_SHA
- target_function: run_cascade
- target_file: scripts/bump_strict_unknown_sha.py
- lock_kind: ast_func_sha
- bump_when: run_cascade 实现变化
- bump_protocol: recompute func_sha_by_name("run_cascade", scripts/bump_strict_unknown_sha.py) then update constant
- rationale: 锁 cascade 子流程函数 (确保 subprocess 调 bump_reverse_sha_lock.py 不被静默删除)
"""
from __future__ import annotations

import ast
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
    "e5e6f14d5060f6c9db34e296211b5bc8af53bd2d76ff081438979c38a8e003f9"
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
# V6 cascade: run_cascade func sha
EXPECTED_RUN_CASCADE_FUNC_SHA = (
    "8185b1725fc33954553378816d815489a451378e2de90d8ff86d9e94104881c4"
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


def v6_cascade_structure() -> None:
    """AST 静态锁: 验证 helper 含 --cascade argparse + run_cascade 函数体内含
    'bump_reverse_sha_lock.py' 字面量 (subprocess.run 引用)。

    防 cascade 选项被悄悄删除或 cascade 子命令调用被改成 print 等 no-op。
    """
    if not HELPER.is_file():
        _emit("V6_cascade_structure", False, "helper missing")
        return
    src = HELPER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    has_cascade_arg = False
    has_subprocess_call_to_reverse_helper = False
    for node in ast.walk(tree):
        # argparse --cascade 检测: add_argument("--cascade", ...) 调用
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "add_argument":
                for a in node.args:
                    if isinstance(a, ast.Constant) and a.value == "--cascade":
                        has_cascade_arg = True
        # bump_reverse_sha_lock.py 字面量出现在任何位置
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "bump_reverse_sha_lock.py" in node.value:
                has_subprocess_call_to_reverse_helper = True
    # 额外: run_cascade 函数 sha 锁
    actual_func_sha = func_sha_by_name(HELPER, "run_cascade")
    sha_ok = actual_func_sha == EXPECTED_RUN_CASCADE_FUNC_SHA
    ok = has_cascade_arg and has_subprocess_call_to_reverse_helper and sha_ok
    _emit(
        "V6_cascade_structure",
        ok,
        f"cascade_arg={has_cascade_arg} reverse_helper_ref={has_subprocess_call_to_reverse_helper} "
        f"run_cascade_sha_match={sha_ok} actual={actual_func_sha[:16]}",
    )


def v7_cascade_dry_run_via_args() -> None:
    """行为锁: --cascade 在 no-drift 场景下 rc=0, 且 stdout 含 cascade 触发标记。

    no-drift 时 V6 sha 不变 → cascade 后 reverse helper 也是 no-op, 整体 rc=0。
    我们检 stdout 必须含 reverse helper 的输出印记 (例如 'target_new_sha=' 或
    'OK:' / 'no-op'), 防 cascade 选项被改成无 subprocess 调用的 no-op。
    """
    if not HELPER.is_file():
        _emit("V7_cascade_behavior", False, "helper missing")
        return
    proc = subprocess.run(
        [sys.executable, str(HELPER), "--cascade"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = proc.stdout
    # cascade 触发标记: reverse helper 输出含 "target_new_sha=" (来自 run_bump report)
    has_cascade_marker = "target_new_sha=" in out
    has_ok_msg = "OK: cascade bump_reverse_sha_lock.py PASS" in out
    ok = proc.returncode == 0 and has_cascade_marker and has_ok_msg
    _emit(
        "V7_cascade_behavior",
        ok,
        f"rc={proc.returncode} has_cascade_marker={has_cascade_marker} has_ok_msg={has_ok_msg}",
    )


def main() -> int:
    v1_helper_file_sha()
    v2_compute_current_func_sha()
    v3_read_expected_func_sha()
    v4_run_bump_func_sha()
    v5_dry_run_noop_behavior()
    v6_cascade_structure()
    v7_cascade_dry_run_via_args()
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
