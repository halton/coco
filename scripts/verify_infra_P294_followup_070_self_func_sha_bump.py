#!/usr/bin/env python3
"""verify_infra_P294_followup_070_self_func_sha_bump: lock V1/V6 placeholder removal.

phase-67 #18 (infra-P294-followup-070-self-func-sha-bump):
verify_infra_070.py 中 V1 (EXPECTED_SELF_MAIN_FUNC_SHA) 与 V6
(EXPECTED_MAKE_MINI_REPO_FUNC_SHA) 两处历史 ``__BUMP_ME__`` 占位早返回分支被移除,
现在常量必须是 hex64 实算值, 否则 V1/V6 直接 FAIL。本 verify 机械化锁定该不可逆迁移。

INFRA_P294_FOLLOWUP_070_SHA_LOCKS
---------------------------------
- scripts/verify_infra_070.py 文件 sha: EXPECTED_TARGET_FILE_SHA
- 该脚本中 EXPECTED_SELF_MAIN_FUNC_SHA / EXPECTED_MAKE_MINI_REPO_FUNC_SHA 必须 hex64
- 本脚本 main() canonical func sha 自锁: EXPECTED_SELF_MAIN_FUNC_SHA (本 verify)

校验层级 (V0-V5):

- V0 scaffolding: 目标脚本存在 + 本脚本 main sha hex64
- V1 target_consts_hex64: 静态 AST 读 verify_infra_070.py 的两个 EXPECTED_* 常量,
  断言均为 hex64 且 != "__BUMP_ME__"
- V2 placeholder_branch_removed: AST 扫 verify_infra_070.py, 不存在
  ``if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__"`` 与
  ``if EXPECTED_MAKE_MINI_REPO_FUNC_SHA == "__BUMP_ME__"`` 两个比较 Compare 节点
- V3 target_runs_passing: 真跑 verify_infra_070.py, rc==0 且 stdout 含
  "V1_self_main_func_sha" 与 "V6_make_mini_repo_func_sha" 两行 PASS
- V4_mutant: 临时副本中把 EXPECTED_SELF_MAIN_FUNC_SHA 改回 "__BUMP_ME__", 跑同样脚本,
  必须 rc!=0 且 V1 行报 FAIL (placeholder/invalid)
- V5 reviewer_lgtm_gate (backloaded): assert_reviewer_lgtm 实读 feature_list.json,
  在 Engineer 阶段允许 FAIL (尚无 Reviewer 写入), close-out 前必须 PASS

退出码: 0=ALL PASS, 2=任一 FAIL (走 verify_summary_exit).
运行环境: 必须 .venv (Python 3.13).
"""
from __future__ import annotations

import ast
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
TARGET = SCRIPTS / "verify_infra_070.py"
FEATURE_LIST = REPO / "feature_list.json"
FEATURE_ID = "infra-P294-followup-070-self-func-sha-bump"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
)

# Target file sha (locks verify_infra_070.py contents after this feature's edits).
EXPECTED_TARGET_FILE_SHA = (
    "da364d1437ba3431c2dccb2e27921024e800f05ef567e35e43c08890bd094ba7"
)

# Self main() func sha self-lock (P299 — backloaded after first run, must be hex64
# at close-out; FAIL OK during Engineer bring-up).
EXPECTED_SELF_MAIN_FUNC_SHA = "3807a54a1f6e59da0700d28b97faf32b059eb6ca14069246de14c1e4ffea9206"

DOCSTRING_SENTINEL = "INFRA_P294_FOLLOWUP_070_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    _results.append((tag, ok, detail))
    print(f"[verify_P294_followup_070][{mark}] {tag} {detail}", flush=True)


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_target_const(name: str) -> str | None:
    """Static AST read of a module-level str constant from TARGET."""
    src = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name) or tgt.id != name:
            continue
        v = node.value
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            return v.value
    return None


def v0_scaffolding() -> None:
    _emit("V0_target_exists", TARGET.is_file(), f"path={TARGET}")
    _emit("V0_self_const_hex64_or_placeholder",
          EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__"
          or _is_hex64(EXPECTED_SELF_MAIN_FUNC_SHA),
          f"val={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}")
    _emit("V0_target_file_sha_hex64", _is_hex64(EXPECTED_TARGET_FILE_SHA),
          f"val={EXPECTED_TARGET_FILE_SHA[:16]}")
    self_doc = ast.get_docstring(ast.parse(Path(__file__).read_text(encoding="utf-8")))
    _emit("V0_docstring_sentinel",
          bool(self_doc) and DOCSTRING_SENTINEL in (self_doc or ""),
          f"sentinel={DOCSTRING_SENTINEL}")


def v1_target_consts_hex64() -> None:
    self_v = _read_target_const("EXPECTED_SELF_MAIN_FUNC_SHA")
    mmr_v = _read_target_const("EXPECTED_MAKE_MINI_REPO_FUNC_SHA")
    self_ok = _is_hex64(self_v) and self_v != "__BUMP_ME__"
    mmr_ok = _is_hex64(mmr_v) and mmr_v != "__BUMP_ME__"
    _emit("V1_self_const_hex64_not_placeholder", self_ok,
          f"val={(self_v or '')[:16]}")
    _emit("V1_mmr_const_hex64_not_placeholder", mmr_ok,
          f"val={(mmr_v or '')[:16]}")


def _has_bumpme_compare(tree: ast.AST, lhs_name: str) -> bool:
    """True if AST contains `EXPECTED_xxx == '__BUMP_ME__'` or its reverse."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        # left and comparators of length 1 (single ==)
        if len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
            continue
        operands = [node.left, *node.comparators]
        names = {o.id for o in operands if isinstance(o, ast.Name)}
        consts = {
            o.value for o in operands
            if isinstance(o, ast.Constant) and isinstance(o.value, str)
        }
        if lhs_name in names and "__BUMP_ME__" in consts:
            return True
    return False


def v2_placeholder_branch_removed() -> None:
    src = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(src)
    self_present = _has_bumpme_compare(tree, "EXPECTED_SELF_MAIN_FUNC_SHA")
    mmr_present = _has_bumpme_compare(tree, "EXPECTED_MAKE_MINI_REPO_FUNC_SHA")
    _emit("V2_self_placeholder_branch_removed", not self_present,
          f"compare_node_present={self_present}")
    _emit("V2_mmr_placeholder_branch_removed", not mmr_present,
          f"compare_node_present={mmr_present}")


def v3_target_runs_passing() -> None:
    got = _file_sha(TARGET)
    _emit("V3_target_file_sha_match", got == EXPECTED_TARGET_FILE_SHA,
          f"got={got[:16]} expect={EXPECTED_TARGET_FILE_SHA[:16]}")
    proc = subprocess.run(
        [sys.executable, str(TARGET)],
        capture_output=True, text=True, cwd=str(REPO), timeout=180,
    )
    out = proc.stdout + proc.stderr
    rc_ok = proc.returncode == 0
    v1_line_pass = any(
        "[verify_infra_070][PASS] V1_self_main_func_sha" in ln
        for ln in out.splitlines()
    )
    v6_line_pass = any(
        "[verify_infra_070][PASS] V6_make_mini_repo_func_sha" in ln
        for ln in out.splitlines()
    )
    _emit("V3_target_rc0", rc_ok, f"rc={proc.returncode}")
    _emit("V3_target_v1_pass_line_present", v1_line_pass, "")
    _emit("V3_target_v6_pass_line_present", v6_line_pass, "")


def v4_mutant_placeholder_reintroduced() -> None:
    """Mutate target copy: set EXPECTED_SELF_MAIN_FUNC_SHA back to __BUMP_ME__.
    Re-run; expect rc != 0 and V1 FAIL line (placeholder/invalid)."""
    with tempfile.TemporaryDirectory(prefix="p294_fu070_mutant_") as td:
        td_path = Path(td)
        # Copy repo skeleton needed: scripts/ + feature_list.json + venv? Use
        # in-place mutation via tempfile-edited copy executed via python -c is
        # awkward — easiest: copy entire repo subset, mutate, run.
        mutant_scripts = td_path / "scripts"
        shutil.copytree(SCRIPTS, mutant_scripts, dirs_exist_ok=False)
        shutil.copy2(FEATURE_LIST, td_path / "feature_list.json")
        mutant_target = mutant_scripts / TARGET.name
        src = mutant_target.read_text(encoding="utf-8")
        # Replace the hex64 constant line with __BUMP_ME__.
        new_src, n = re.subn(
            r'EXPECTED_SELF_MAIN_FUNC_SHA\s*=\s*"[0-9a-f]{64}"',
            'EXPECTED_SELF_MAIN_FUNC_SHA = "__BUMP_ME__"',
            src, count=1,
        )
        if n != 1:
            _emit("V4_mutant_placeholder_apply", False,
                  f"failed to mutate constant; subn count={n}")
            return
        mutant_target.write_text(new_src, encoding="utf-8")
        _emit("V4_mutant_placeholder_apply", True, "constant rewritten to __BUMP_ME__")
        proc = subprocess.run(
            [sys.executable, str(mutant_target)],
            capture_output=True, text=True, cwd=str(td_path), timeout=180,
        )
        out = proc.stdout + proc.stderr
        rc_nonzero = proc.returncode != 0
        v1_fail_line = any(
            ("[verify_infra_070][FAIL] V1_self_main_func_sha" in ln)
            and ("placeholder/invalid" in ln)
            for ln in out.splitlines()
        )
        _emit("V4_mutant_rc_nonzero", rc_nonzero, f"rc={proc.returncode}")
        _emit("V4_mutant_v1_fail_placeholder_message", v1_fail_line, "")


def v5_reviewer_lgtm_gate() -> None:
    """Backloaded: FAIL OK during Engineer; close-out demands PASS."""
    ok, reason = assert_reviewer_lgtm(FEATURE_ID, FEATURE_LIST)
    _emit("V5_reviewer_lgtm_gate", ok,
          f"feature={FEATURE_ID} reason={reason!r}")


def v_self_func_sha() -> None:
    """Self main() func sha lock — backloaded after first run."""
    try:
        got = func_sha_by_name(Path(__file__), "main")
    except Exception as e:
        _emit("V_self_main_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit("V_self_main_func_sha", True,
              f"placeholder OK during bring-up; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}")
        return
    _emit("V_self_main_func_sha",
          got == EXPECTED_SELF_MAIN_FUNC_SHA,
          f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}")


def main() -> int:
    v0_scaffolding()
    v_self_func_sha()
    v1_target_consts_hex64()
    v2_placeholder_branch_removed()
    v3_target_runs_passing()
    v4_mutant_placeholder_reintroduced()
    v5_reviewer_lgtm_gate()
    failed = sum(1 for _, ok, _ in _results if not ok)
    total = len(_results)
    print(
        f"[verify_P294_followup_070][SUMMARY] {total - failed}/{total} PASS, "
        f"{failed} FAIL",
        flush=True,
    )
    verify_summary_exit(failed)
    return 0  # pragma: no cover (verify_summary_exit calls sys.exit)


if __name__ == "__main__":
    sys.exit(main())
