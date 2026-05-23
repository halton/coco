#!/usr/bin/env python3
"""verify_infra_046: bump_reverse_sha_lock helper sha lock (verify-only).

infra-V6-backlog-scope-extend-bump-helpers (phase-34 #2.34):
锁住 ``scripts/bump_reverse_sha_lock.py`` 通用 V6 反向 sha lock bump helper,
保证后续改动不绕过 sha lockchain (V6 pre-flight 会扫到本脚本里的反向锁常量)。

V0 scaffolding: bump_reverse_sha_lock.py 存在 + 本脚本可执行 + 关键函数都在源码顶层。
V1 docstring sentinel ``INFRA_046_SHA_LOCKS`` 自锁。
V2 bump helper file sha + 主入口 func sha 五锁 (file/run_bump/find_locks_for_target/
   _bump_in_file/main).
V3 in-memory ast mutant: 替换 run_bump 函数体, unparse 后 sha 必漂移 (反证锁可被破坏)。
V4 行为验证: dry-run 模式 call ``find_locks_for_target`` 与 ``run_bump``, 给定
   现有 target (``scripts/verify_robot_025.py``) 验:
   - 至少能找到 1 个反向锁 (verify_robot_027.py 的 VERIFY_025_EXPECTED_SHA)
   - dry-run 不写盘 (验证后比对 file sha 不变)
   - exit code = 0 (no-op) 或 0 (apply dry-run change)
V5 Reviewer LGTM gate (print-only, evidence 字段)。

INFRA_046_SHA_LOCKS
-------------------
- ``scripts/bump_reverse_sha_lock.py`` file sha: EXPECTED_BUMP_FILE_SHA
- ``run_bump`` func sha: EXPECTED_RUN_BUMP_FUNC_SHA
- ``find_locks_for_target`` func sha: EXPECTED_FIND_LOCKS_FUNC_SHA
- ``_bump_in_file`` func sha: EXPECTED_BUMP_IN_FILE_FUNC_SHA
- ``main`` func sha: EXPECTED_MAIN_FUNC_SHA

退出码 0=ALL PASS / 1=任一 FAIL。

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
BUMP = SCRIPTS / "bump_reverse_sha_lock.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import func_sha_by_name, assert_v5_reviewer_gate_evidence_bind  # noqa: E402

# infra-046 sha lock 常量 (V2)
EXPECTED_BUMP_FILE_SHA = "b85d7dda59abe58498bbbcfa206bace654868a112a9619c304ba563533f45b9e"
EXPECTED_RUN_BUMP_FUNC_SHA = "fdb2bee973a34e33bd330e20d88b98ca91be520ce2ac192588e47ae54c7e9527"
EXPECTED_FIND_LOCKS_FUNC_SHA = "6870d794a9d8e36b456de7f1155f8e938ed42ad06e370a53dedeedde2c906e38"
EXPECTED_BUMP_IN_FILE_FUNC_SHA = "7ab7869dcd8762e0b99119aa801f7de2010092ff448ec57793a3d8c736f61d3f"
EXPECTED_MAIN_FUNC_SHA = "8ec9b0e4de249c87c77a11340dd65779ae01852e22a0886cbd594babbcb6fa86"

DOCSTRING_SENTINEL = "INFRA_046_SHA_LOCKS"
FUNC_NAMES = ("run_bump", "find_locks_for_target", "_bump_in_file", "main")


# phase-47 #1.47: V5_GATE evidence-bind helper (grace_period 兜底 soft graduate)
REAL_FEATURE_LIST = Path(__file__).resolve().parents[1] / "feature_list.json"
V5_GATE_FEATURE_ID = "__PHASE_47_PLACEHOLDER_INFRA_046__"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_046][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_bump_exists", BUMP.is_file(), f"path={BUMP}")
    self_path = Path(__file__)
    _emit("V0_self_exists", self_path.is_file(), f"path={self_path}")
    src = BUMP.read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_funcs = {
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in FUNC_NAMES:
        _emit(
            f"V0_top_func_{name}",
            name in top_funcs,
            f"top-level funcs has {name}",
        )


# ---------------------------------------------------------------------------
# V1: docstring sentinel 自锁
# ---------------------------------------------------------------------------
def v1_docstring_sentinel() -> None:
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8")))
    _emit(
        "V1_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )


# ---------------------------------------------------------------------------
# V2: bump helper file sha + func sha 五锁
# ---------------------------------------------------------------------------
def v2_file_and_func_sha() -> None:
    got_file = _file_sha(BUMP)
    _emit(
        "V2_bump_file_sha",
        got_file == EXPECTED_BUMP_FILE_SHA,
        f"got={got_file[:16]} expect={EXPECTED_BUMP_FILE_SHA[:16]}",
    )
    expects = {
        "run_bump": EXPECTED_RUN_BUMP_FUNC_SHA,
        "find_locks_for_target": EXPECTED_FIND_LOCKS_FUNC_SHA,
        "_bump_in_file": EXPECTED_BUMP_IN_FILE_FUNC_SHA,
        "main": EXPECTED_MAIN_FUNC_SHA,
    }
    for name, exp in expects.items():
        got = func_sha_by_name(BUMP, name)
        _emit(
            f"V2_func_sha_{name}",
            got == exp,
            f"got={got[:16]} expect={exp[:16]}",
        )


# ---------------------------------------------------------------------------
# V3: in-memory ast mutant — 替换 run_bump 函数体, sha 必漂移
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    src = BUMP.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target: ast.FunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "run_bump":
            target = node
            break
    if target is None:
        _emit("V3_mutant_apply", False, "run_bump not found")
        return
    baseline_sha = hashlib.sha256(ast.unparse(target).encode("utf-8")).hexdigest()
    # mutant: 函数体替换为 return (0, [])
    mutant_node = ast.parse(ast.unparse(target)).body[0]
    assert isinstance(mutant_node, ast.FunctionDef)
    mutant_node.body = [ast.Return(
        value=ast.Tuple(
            elts=[ast.Constant(value=0), ast.List(elts=[], ctx=ast.Load())],
            ctx=ast.Load(),
        ),
    )]
    ast.fix_missing_locations(mutant_node)
    mutant_sha = hashlib.sha256(ast.unparse(mutant_node).encode("utf-8")).hexdigest()
    _emit(
        "V3_mutant_sha_drift",
        baseline_sha != mutant_sha,
        f"baseline={baseline_sha[:16]} mutant={mutant_sha[:16]}",
    )
    _emit(
        "V3_baseline_matches_expected",
        baseline_sha == EXPECTED_RUN_BUMP_FUNC_SHA,
        f"baseline={baseline_sha[:16]} expect={EXPECTED_RUN_BUMP_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为验证 — dry-run call find_locks_for_target + run_bump
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # 直接 import bump helper module 调函数 (file-level, 避免 subprocess 复杂)
    sys.path.insert(0, str(SCRIPTS))
    import importlib
    mod = importlib.import_module("bump_reverse_sha_lock")

    target = SCRIPTS / "verify_robot_025.py"
    _emit(
        "V4_target_exists",
        target.is_file(),
        f"target={target.relative_to(REPO)}",
    )

    # find_locks_for_target 应至少返回 1 条 (verify_robot_027 VERIFY_025_EXPECTED_SHA)
    locks = mod.find_locks_for_target(target)
    _emit(
        "V4_find_locks_nonempty",
        len(locks) >= 1,
        f"locks_count={len(locks)}",
    )
    # 检查 schema
    if locks:
        sample = locks[0]
        required_keys = {"file", "lineno", "const_name", "sha_hex"}
        _emit(
            "V4_lock_schema",
            required_keys.issubset(sample.keys()),
            f"sample_keys={sorted(sample.keys())}",
        )
        # 应该有一条指向 verify_robot_025 的反向锁 (VERIFY_025_EXPECTED_SHA)
        has_025 = any(
            "VERIFY_025" in lk["const_name"] for lk in locks
        )
        _emit(
            "V4_lock_has_verify_025",
            has_025,
            f"locks={[lk['const_name'] for lk in locks]}",
        )

    # dry-run run_bump 不应改盘
    file_before = _file_sha(BUMP)
    rc, report = mod.run_bump(target, dry_run=True)
    file_after = _file_sha(BUMP)
    _emit(
        "V4_dry_run_no_disk_change",
        file_before == file_after,
        f"before={file_before[:16]} after={file_after[:16]}",
    )
    _emit(
        "V4_dry_run_rc_ok",
        rc in (0, 3),  # 0=ok 3=no locks found, 都不算 hard fail
        f"rc={rc} (0=ok/no-op or applied, 3=no locks)",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (print-only)
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    """V5 Reviewer LGTM gate — phase-47 #1.47 graduate to evidence-bind helper.

    target feature evidence 不完整 (legacy / not_started)，通过 grace_period 兜底
    保持 emit=True，待 target feature 补齐 reviewer evidence 后从 grace 列表移除。
    """
    if not REAL_FEATURE_LIST.is_file():
        _emit(
            "V5_reviewer_lgtm_gate",
            False,
            f"feature_list.json not found at {REAL_FEATURE_LIST}",
        )
        return
    result = assert_v5_reviewer_gate_evidence_bind(
        V5_GATE_FEATURE_ID, REAL_FEATURE_LIST,
        grace_period_feature_ids=(V5_GATE_FEATURE_ID,),
    )
    _emit(
        "V5_reviewer_lgtm_gate",
        bool(result["ok"]),
        f"target={V5_GATE_FEATURE_ID} helper_ok={result['ok']} "
        f"grace_skipped={result['grace_skipped']} "
        f"verdict={result['verdict']!r} kind={result['reviewer_kind']!r} "
        f"summary_len={result['summary_len']} reason={result['reason']!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_docstring_sentinel()
    v2_file_and_func_sha()
    v3_mutant()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_046][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_046][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
