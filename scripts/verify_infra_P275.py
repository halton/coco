#!/usr/bin/env python3
"""verify_infra_P275: assert_verify_passed `min_checks` 参数 + 示范 caller 迁移。

INFRA_P275_SHA_LOCKS sentinel.

P274 dogfood (infra-P273-evidence-report-accuracy) 暴露
``[verify_x] summary total=0 failed=0`` 仍会被 assert_verify_passed 判
``passed=True`` 的盲点 —— 若 verify 脚本因 bug 一个 check 都没跑就 emit
summary, sub-agent 仍可被误导。本 feature 给 helper 加 ``min_checks: int | None``
参数:

- ``min_checks=None`` (默认) → 保留旧语义, backward compatible
- ``min_checks=N`` → 要求实测 ``checks >= N``, 否则 ``passed=False``
  并在 reason 里追加 ``checks=X < min_checks=N``

V0-V6 meta-lock 防止 helper / 迁移点被悄悄回退:

- V0: scaffolding (_verify_lib.py & verify_infra_055.py 存在)
- V1: docstring sentinel + 本脚本 main 自锁 (V4b)
- V3: _verify_lib.py file sha lock (cascade source)
- V4: assert_verify_passed func sha lock
- V5: ``min_checks`` 字面 grep + 3 示范 caller (r1/r6/r7) 各传 min_checks
- V6: behavior — total=0 反例 with min_checks=1 → passed=False (反证 mutation)

Run::

    python scripts/verify_infra_P275.py
"""

from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"
VERIFY_055 = SCRIPTS / "verify_infra_055.py"
SELF_PATH = Path(__file__).resolve()

sys.path.insert(0, str(SCRIPTS))

# V3: _verify_lib.py file sha (cascade source) — bump 在 cascade 步骤
EXPECTED_LIB_FILE_SHA = "7df5af6b9d48687e0a5efab7b3dc2e3dfc2fd54e6aa07d1583fb9d5a56604ef4"

# V4: assert_verify_passed func sha (ast.unparse)
EXPECTED_ASSERT_VERIFY_PASSED_FUNC_SHA = (
    "7754106551f77aa43caaaa27593a3b40d0532ec791a90b22f77d7c5098beb084"
)

# V4b: 本脚本 main() 自锁 — 首跑 __BUMP_ME__, 跑出实测再回填
EXPECTED_MAIN_FUNC_SHA = "d58f1801983c7f84e8bda71943227b793f8acdd847fc22b5a53938fed9e3c62b"

DOCSTRING_SENTINEL = "INFRA_P275_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P275][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _func_sha(path: Path, func_name: str) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return hashlib.sha256(ast.unparse(node).encode("utf-8")).hexdigest()
    raise RuntimeError(f"func {func_name!r} not found in {path}")


def v0_scaffolding() -> None:
    _emit("V0_verify_lib_exists", VERIFY_LIB.is_file(), f"path={VERIFY_LIB}")
    _emit("V0_verify_055_exists", VERIFY_055.is_file(), f"path={VERIFY_055}")
    _emit("V0_self_exists", SELF_PATH.is_file(), f"path={SELF_PATH}")


def v1_sentinel_and_main_sha() -> None:
    src = SELF_PATH.read_text(encoding="utf-8")
    _emit(
        "V1_docstring_sentinel",
        DOCSTRING_SENTINEL in src,
        f"expect '{DOCSTRING_SENTINEL}' in self source",
    )
    try:
        got = _func_sha(SELF_PATH, "main")
    except Exception as e:
        _emit("V4b_main_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V4b_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_MAIN_FUNC_SHA={got}",
        )
    else:
        _emit(
            "V4b_main_func_sha",
            got == EXPECTED_MAIN_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_MAIN_FUNC_SHA[:16]}",
        )


def v3_lib_file_sha() -> None:
    got = _file_sha(VERIFY_LIB)
    _emit(
        "V3_lib_file_sha",
        got == EXPECTED_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_LIB_FILE_SHA[:16]}",
    )


def v4_func_sha() -> None:
    try:
        got = _func_sha(VERIFY_LIB, "assert_verify_passed")
    except Exception as e:
        _emit("V4_assert_verify_passed_func_sha", False, f"compute err: {e!r}")
        return
    _emit(
        "V4_assert_verify_passed_func_sha",
        got == EXPECTED_ASSERT_VERIFY_PASSED_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_ASSERT_VERIFY_PASSED_FUNC_SHA[:16]}",
    )


def v5_min_checks_literal_and_callers() -> None:
    lib_src = VERIFY_LIB.read_text(encoding="utf-8")
    # helper 签名带 min_checks 参数
    _emit(
        "V5_helper_signature_has_min_checks",
        "min_checks: 'int | None' = None" in lib_src or 'min_checks: "int | None" = None' in lib_src,
        "expect signature `min_checks: \"int | None\" = None` in _verify_lib",
    )
    # helper 内部用到 min_checks
    _emit(
        "V5_helper_body_uses_min_checks",
        "min_checks" in lib_src and "checks < min_checks" in lib_src,
        "expect 'checks < min_checks' literal in helper body",
    )
    # 3 个示范 caller 各传 min_checks
    v55 = VERIFY_055.read_text(encoding="utf-8")
    _emit(
        "V5_caller_r1_min_checks",
        'assert_verify_passed(good, "verify_infra_055", min_checks=2)' in v55,
        "expect r1 migration: min_checks=2",
    )
    _emit(
        "V5_caller_r6_min_checks",
        'assert_verify_passed(good_b, "verify_infra_034", min_checks=53)' in v55,
        "expect r6 migration: min_checks=53",
    )
    _emit(
        "V5_caller_r7_min_checks",
        'assert_verify_passed(good_b2, "verify_robot_035", min_checks=8)' in v55,
        "expect r7 migration: min_checks=8",
    )


def v6_behavior_mutation() -> None:
    """反证: total=0 失真模式 — min_checks=1 必须把 passed 翻成 False."""
    from _verify_lib import assert_verify_passed

    empty = "[verify_infra_055] summary total=0 failed=0\n"
    # 旧语义 (None) 仍然误判 passed=True (这正是本 feature 想堵的盲点)
    r_old = assert_verify_passed(empty, "verify_infra_055")
    _emit(
        "V6_old_semantics_still_passes_total0",
        r_old["passed"] is True and r_old["checks"] == 0,
        f"r_old={r_old} (旧语义 backward compat: total=0 仍判 passed=True)",
    )
    # 新参数 min_checks=1 立即把它翻成 False
    r_new = assert_verify_passed(empty, "verify_infra_055", min_checks=1)
    _emit(
        "V6_min_checks_rejects_total0",
        r_new["passed"] is False
        and "checks=0 < min_checks=1" in r_new["reason"],
        f"r_new={r_new}",
    )
    # min_checks=N 满足时正例仍 PASS (无副作用)
    ok = (
        "[verify_infra_055][PASS] V0_x ok\n"
        "[verify_infra_055][SUMMARY] ALL PASS (5 checks)\n"
    )
    r_ok = assert_verify_passed(ok, "verify_infra_055", min_checks=5)
    _emit(
        "V6_min_checks_eq_pass",
        r_ok["passed"] is True and r_ok["checks"] == 5,
        f"r_ok={r_ok}",
    )
    # min_checks > 实际 → FAIL
    r_short = assert_verify_passed(ok, "verify_infra_055", min_checks=99)
    _emit(
        "V6_min_checks_gt_rejects",
        r_short["passed"] is False
        and "checks=5 < min_checks=99" in r_short["reason"],
        f"r_short={r_short}",
    )


def main() -> int:
    v0_scaffolding()
    v1_sentinel_and_main_sha()
    v3_lib_file_sha()
    v4_func_sha()
    v5_min_checks_literal_and_callers()
    v6_behavior_mutation()

    fails = [t for (t, ok, _d) in _results if not ok]
    total = len(_results)
    if fails:
        print(
            f"[verify_infra_P275][SUMMARY] FAIL {len(fails)}/{total}: {fails}",
            flush=True,
        )
        return 1
    print(f"[verify_infra_P275][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
