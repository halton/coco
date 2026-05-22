#!/usr/bin/env python3
"""verify_infra_079 V0-V5: 锁定 verify_infra_074 V4_2 精确等比 + V4_2b 实跑 cross-check.

infra-P286-followup-v4-2-stricter-equal-check (phase-40 #5.40):
verify_infra_074 V4_2 之前断言 ``EXPECTED_CURRENT_TOTAL_NODES > 0`` (弱断言).
本 feature 把 074 V4_2 收紧为:

- V4_2: 059 中 EXPECTED_CURRENT_TOTAL_NODES 值精确 == 074 自持
  ``EXPECTED_TOTAL_NODES_TRUTH`` 真值常量.
- V4_2b: subprocess 跑 059 + parse stdout 中
  ``V4_real_total_nodes_within_tolerance expect=<N>`` 行, 断言 N == 真值.

verify_infra_079 锁定 074 上述行为存在 (常量名 + 真值 + tag 字面 + 实跑 074
看 V4_2/V4_2b PASS).

INFRA_079_SHA_LOCKS
-------------------
- ``scripts/verify_infra_074.py`` file sha: EXPECTED_VERIFY_074_FILE_SHA
- ``scripts/verify_infra_074.py:main`` func sha: EXPECTED_074_MAIN_FUNC_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:func_sha_by_name`` func sha:
  EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA (helper func-sha)
- 真值锁: EXPECTED_TOTAL_NODES_TRUTH_VALUE (int 81)
- 本脚本 main() 自锁 func sha: EXPECTED_SELF_MAIN_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding (×5)
- V1 self main() func sha 自锁
- V2 verify_infra_074.py file sha
- V3 _verify_lib.py:func_sha_by_name helper func sha
- V4 行为校验 (5 checks):
  - V4_1 ast 扫: 074 顶层含 EXPECTED_TOTAL_NODES_TRUTH 常量 (int)
  - V4_2 该常量值 == EXPECTED_TOTAL_NODES_TRUTH_VALUE (79 自持真值 81)
  - V4_3 ast 扫: 074 v4_behavior 函数源码含
    ``V4_2_const_value_eq_truth`` 与 ``V4_2b_real_dump_expect_eq_truth``
    两个 emit tag 字面 (回归保护, 防止有人改回旧 tag 名)
  - V4_4 subprocess 真跑 074 → rc=0 且 stdout 含两个 tag 的 PASS 行
  - V4_5 mutant (round-2 整改, 真触发 FAIL): 把 074 源码写到 tmp 路径,
    regex 替换 ``EXPECTED_TOTAL_NODES_TRUTH: int = 81`` → ``= 9999``,
    subprocess 跑 tmp 074 (env PYTHONPATH=SCRIPTS, cwd=REPO), 断言
    rc≠0 且 stdout 含 ``[verify_infra_074][FAIL] V4_2_const_value_eq_truth``.
    074 不锁自己 file_sha, 所以 mutant 074 不会被 V2 自检拦下, 会真走到 V4_2
    然后断 81 != 9999 → FAIL. 这才是 074 V4_2 那条 check **自己**会变红的真证.
- V5 reviewer_lgtm_gate (真门: ok is True)

退出码: 0=ALL PASS, 2=任一 FAIL (走 verify_summary_exit)。

运行环境约定 (infra-034): 必须在 .venv 下运行。
"""
from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_074 = SCRIPTS / "verify_infra_074.py"
LIB = SCRIPTS / "_verify_lib.py"
REAL_FEATURE_LIST = REPO / "feature_list.json"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_VERIFY_074_FILE_SHA = "d843e85b0b0a19acb598701bb9b1c60ea440beac62458bff5001f7aa88af709e"
EXPECTED_VERIFY_LIB_FILE_SHA = "3ccf0f771d0d4af129708ff76dde0a5e836dc172e350758882e4c2b13265e750"
EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA = "c668f3d46c0b188adee8c087650f854e570a51f0ff1202cd5f1fe09c9e5a78db"
EXPECTED_SELF_MAIN_FUNC_SHA = "18760bb2e4c06f9a0f64a09b1bdb6e58eafacefd730b5f2b85b9c693b9799526"

# 074 真值常量名 + 期望真值. 任一处 mutate 即 FAIL.
# infra-P286-followup-tolerance-headroom-bump phase-41 #1.41 同步 bump 81→86.
EXPECTED_TOTAL_NODES_TRUTH_CONST_NAME = "EXPECTED_TOTAL_NODES_TRUTH"
EXPECTED_TOTAL_NODES_TRUTH_VALUE: int = 86

DOCSTRING_SENTINEL = "INFRA_079_SHA_LOCKS"

V5_GATE_FEATURE_ID = "infra-P286-followup-v4-2-stricter-equal-check"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_079][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _get_int_const(src: str, name: str):
    """Top-level Assign/AnnAssign int const lookup. None if not found."""
    tree = ast.parse(src)
    for node in tree.body:
        targets: List[str] = []
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets.append(node.target.id)
            value = node.value
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    targets.append(t.id)
            value = node.value
        else:
            continue
        if name in targets and isinstance(value, ast.Constant) and isinstance(value.value, int):
            return value.value
    return None


def _func_source(src: str, name: str) -> str:
    """Return source text of top-level function `name` (ast.unparse). '' if missing."""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    return ""


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_verify_074_exists", VERIFY_074.is_file(), f"path={VERIFY_074}")
    _emit("V0_lib_exists", LIB.is_file(), f"path={LIB}")
    _emit(
        "V0_const_truth_value_positive_int",
        isinstance(EXPECTED_TOTAL_NODES_TRUTH_VALUE, int)
        and EXPECTED_TOTAL_NODES_TRUTH_VALUE > 0,
        f"val={EXPECTED_TOTAL_NODES_TRUTH_VALUE}",
    )
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8")))
    _emit(
        "V0_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )


# ---------------------------------------------------------------------------
# V1: self main() func sha
# ---------------------------------------------------------------------------
def v1_self_func_sha() -> None:
    try:
        got = func_sha_by_name(Path(__file__), "main")
    except Exception as e:  # noqa: BLE001
        _emit("V1_self_main_func_sha", False, f"error={e!r}")
        return
    if EXPECTED_SELF_MAIN_FUNC_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V1_self_main_func_sha",
            True,
            f"placeholder OK; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
        return
    if not _is_hex64(EXPECTED_SELF_MAIN_FUNC_SHA):
        _emit(
            "V1_self_main_func_sha",
            False,
            f"EXPECTED_SELF_MAIN_FUNC_SHA not 64-hex; actual={got}",
        )
        return
    _emit(
        "V1_self_main_func_sha",
        got == EXPECTED_SELF_MAIN_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V2: verify_infra_074.py file sha
# ---------------------------------------------------------------------------
def v2_074_file_sha() -> None:
    got = _file_sha(VERIFY_074)
    if EXPECTED_VERIFY_074_FILE_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V2_verify_074_file_sha",
            True,
            f"placeholder OK; bump EXPECTED_VERIFY_074_FILE_SHA={got}",
        )
        return
    _emit(
        "V2_verify_074_file_sha",
        got == EXPECTED_VERIFY_074_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_074_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: _verify_lib.py:func_sha_by_name helper func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "func_sha_by_name")
    except Exception as e:  # noqa: BLE001
        _emit("V3_func_sha_by_name_func_sha", False, f"error={e!r}")
        return
    _emit(
        "V3_func_sha_by_name_func_sha",
        got == EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为校验
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    src = VERIFY_074.read_text(encoding="utf-8")

    # V4_1: 074 顶层含 EXPECTED_TOTAL_NODES_TRUTH 常量 (int)
    val_truth = _get_int_const(src, EXPECTED_TOTAL_NODES_TRUTH_CONST_NAME)
    _emit(
        "V4_1_truth_const_present_int",
        isinstance(val_truth, int),
        f"value={val_truth} name={EXPECTED_TOTAL_NODES_TRUTH_CONST_NAME}",
    )

    # V4_2: 该常量值 == 079 自持真值
    _emit(
        "V4_2_truth_const_value_eq",
        val_truth == EXPECTED_TOTAL_NODES_TRUTH_VALUE,
        f"value={val_truth} expect={EXPECTED_TOTAL_NODES_TRUTH_VALUE}",
    )

    # V4_3: 074:v4_behavior 函数体源码含两个新 tag 字面
    v4_src = _func_source(src, "v4_behavior")
    has_eq = "V4_2_const_value_eq_truth" in v4_src
    has_2b = "V4_2b_real_dump_expect_eq_truth" in v4_src
    _emit(
        "V4_3_v4_behavior_contains_new_tags",
        has_eq and has_2b,
        f"has_V4_2_const_value_eq_truth={has_eq} has_V4_2b_real_dump_expect_eq_truth={has_2b}",
    )

    # V4_4: 真跑 074 → rc=0 且 stdout 含两个 tag 的 PASS 行
    try:
        proc = subprocess.run(
            [sys.executable, str(VERIFY_074)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        rc = proc.returncode
        stdout = proc.stdout
    except Exception as e:  # noqa: BLE001
        rc = -1
        stdout = ""
        _emit("V4_4_real_run_074_rc0_and_pass", False, f"exception: {e!r}")
        return
    pass_eq = bool(re.search(
        r"\[verify_infra_074\]\[PASS\] V4_2_const_value_eq_truth", stdout
    ))
    pass_2b = bool(re.search(
        r"\[verify_infra_074\]\[PASS\] V4_2b_real_dump_expect_eq_truth", stdout
    ))
    _emit(
        "V4_4_real_run_074_rc0_and_pass",
        rc == 0 and pass_eq and pass_2b,
        f"rc={rc} pass_V4_2_eq_truth={pass_eq} pass_V4_2b={pass_2b}",
    )

    # V4_5 mutant (P0 round-2 整改): 真跑 mutant 074 subprocess, 断言 rc≠0 且
    # stdout 含 [FAIL] V4_2_const_value_eq_truth. 这才能证明 074 V4_2 那条精确
    # 等比 check 在真值漂移时**自己会变红** — 而不是在 079 内部重写一遍 logic
    # 自测 ast helper (round-1 假阳性 root cause).
    #
    # 关键事实: 074 不锁自己 file_sha (只锁 059 file_sha), 所以 mutant 074 跑
    # 起来不会被 V2 自检拦下, 会真的走到 V4_2 然后断 81 != 9999 → FAIL.
    #
    # 074 内部用 ``Path(__file__).resolve().parents[1] / "scripts" /
    # "verify_infra_059.py"`` 找 059, 所以 mutant 074 必须落在真 SCRIPTS/ 下
    # (而不是 tmp dir, 否则 parents[1]/scripts 不存在 → FileNotFoundError).
    # 文件名故意不以 `verify_` 起首, 避免被任何 verify_infra_*.py glob 扫到.
    # try/finally 保证跑完立删, 不污染 repo.
    mutant_src = re.sub(
        r"(EXPECTED_TOTAL_NODES_TRUTH\s*:\s*int\s*=\s*)\d+",
        r"\g<1>9999",
        src,
        count=1,
    )
    substituted = "= 9999" in mutant_src and mutant_src != src
    mutant_path = SCRIPTS / "_for_079_v4_5_mutant_074.py"
    try:
        mutant_path.write_text(mutant_src, encoding="utf-8")
        try:
            mproc = subprocess.run(
                [sys.executable, str(mutant_path)],
                capture_output=True,
                text=True,
                timeout=180,
                cwd=str(REPO),
            )
            mrc = mproc.returncode
            mstdout = mproc.stdout
            mstderr = mproc.stderr
        except Exception as e:  # noqa: BLE001
            _emit(
                "V4_5_mutant_real_subprocess_fail",
                False,
                f"exception: {e!r}",
            )
            return
    finally:
        try:
            mutant_path.unlink()
        except FileNotFoundError:
            pass
    # 断言: mutant 074 跑出非 0 rc + stdout 含 V4_2 那条 tag 的 FAIL 行
    v4_2_fail_re = re.compile(
        r"\[verify_infra_074\]\[FAIL\]\s+V4_2_const_value_eq_truth"
    )
    has_v4_2_fail = bool(v4_2_fail_re.search(mstdout))
    ok_all = substituted and mrc != 0 and has_v4_2_fail
    fail_lines = [
        ln for ln in mstdout.splitlines() if "[FAIL]" in ln
    ]
    _emit(
        "V4_5_mutant_real_subprocess_fail",
        ok_all,
        f"substituted={substituted} rc={mrc} has_V4_2_FAIL={has_v4_2_fail} "
        f"fail_lines_count={len(fail_lines)} first_fail_line={fail_lines[0] if fail_lines else ''!r} "
        f"stderr_tail={mstderr.strip().splitlines()[-1] if mstderr.strip() else ''!r}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    if not REAL_FEATURE_LIST.is_file():
        _emit(
            "V5_reviewer_lgtm_gate",
            False,
            f"feature_list.json not found at {REAL_FEATURE_LIST}",
        )
        return
    ok, reason = assert_reviewer_lgtm(V5_GATE_FEATURE_ID, REAL_FEATURE_LIST)
    _emit(
        "V5_reviewer_lgtm_gate",
        ok is True,
        f"target={V5_GATE_FEATURE_ID} helper_ok={ok} reason={reason!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_func_sha()
    v2_074_file_sha()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    failed_tags = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_079][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_079][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
