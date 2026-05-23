#!/usr/bin/env python3
"""verify_infra_081 V0-V5: 锁定 verify_infra_062 必须 wire assert_report_matches_closeout_runs 进 closeout gate.

infra-P299-followup-wire-into-closeout-gate (phase-41 #3.41):
phase-40 #1.41 (P299-engineer-report-vs-impl-trustworthy) 建好 helper
``assert_report_matches_closeout_runs`` (detached worktree + subprocess
实跑 + sha256(actual_tail) vs sha256(claimed_tail) + rc 比对), 但 helper
仅作"工具"暴露, 没有 verify_infra_*.py 业务真的调用它. closeout
sub-agent 即使 report 假数据, V0-V6 链也 catch 不到这条违规.

本 feature 把 helper wire 进 062 (closeout-verify-trustworthy 总入口):

- 062 内新增 ``_enforce_closeout_byte_match`` 函数体, ast 静态调用
  ``assert_report_matches_closeout_runs``;
- 062.main() 真调 ``_enforce_closeout_byte_match()``;
- 081 V4 ast-lock 锁住 "062 函数体必须出现 ``assert_report_matches_closeout_runs``
  调用" 这一静态事实, 删调用即 081 FAIL (机械化阻 merge).

实际 byte-match 真跑保持 Default-OFF (schema 不兼容或 main_head 不匹配
→ soft-PASS 跳过): 现存 closeout_verify.verify_runs 是 flat list
[{script,status,tail_stdout}], P299 helper 期望 nested round
{"verify_runs":[{round,scripts:{name:{rc,tail_stdout,status}}}]}, 且
legacy schema 缺 rc 字段, 适配后跳过. 完整真跑 enforcement 留 backlog
``infra-P299-followup2-enable-byte-match-real-run``.

INFRA_081_SHA_LOCKS
-------------------
- ``scripts/verify_infra_081.py:main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:assert_report_matches_closeout_runs`` func sha:
  EXPECTED_HELPER_FUNC_SHA
- ``scripts/verify_infra_062.py`` file sha: EXPECTED_VERIFY_062_FILE_SHA

注意 (与 074/079/080 同形): 本脚本不锁自己 file sha, 避免与 V4_4 mutant
跑过 V2 自检冲突, 且与 078 placeholder 禁字面约束兼容.

校验层级 (V0-V5, 共 14 checks):

- V0 scaffolding (×5)
- V1 self main() func sha 自锁
- V2 _verify_lib.py file sha
- V3 helper assert_report_matches_closeout_runs func sha
- V4 行为校验 (5 checks):
  - V4_1 062 函数体 ast 扫到至少一处 ``assert_report_matches_closeout_runs`` 调用
  - V4_2 调用位于真 def 函数体 (非 if False / 注释 / docstring), 且至少一个
    在 062.main() 可达调用链 (main → _enforce_closeout_byte_match)
  - V4_3 调用入参第一个位置参数语义合理 (Name='report_obj' 或 dict literal,
    防止有人调成空 dict 应付检查)
  - V4_4 mutant: 拷贝 062 到 tmp 并删 helper 调用, 再 ast 扫 → 检测必须 0 调用
  - V4_5 062 file sha 锁 (EXPECTED_VERIFY_062_FILE_SHA), 防止 062 被悄悄改
    回不调 helper 的版本但还能过 V4_1
- V5 reviewer_lgtm_gate (真门: ok is True)

退出码: 0=ALL PASS, 2=任一 FAIL (走 verify_summary_exit).

运行环境约定 (infra-034): 必须在 .venv 下运行.
"""
from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "_verify_lib.py"
VERIFY_062 = SCRIPTS / "verify_infra_062.py"
REAL_FEATURE_LIST = REPO / "feature_list.json"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
    assert_v5_reviewer_gate_evidence_bind,
)

EXPECTED_SELF_MAIN_FUNC_SHA = "110572e4de22344c1bd70be5006b106c8a1eb93794b7d10d786599928c7a3245"
EXPECTED_VERIFY_LIB_FILE_SHA = "b3005a7d9272fb06c30bc254d8530848d481f2854b77580ef6bb2a7f6166bf55"
EXPECTED_HELPER_FUNC_SHA = "6578542d1b17fe12f2d2e98a598701192c1f9b2cc9fa3be185d4d4f00fad2894"
EXPECTED_VERIFY_062_FILE_SHA = "21c0f98d5be3b69131faaafe1fc5e101ce46c5b072d320ec7e092439082008ac"

DOCSTRING_SENTINEL = "INFRA_081_SHA_LOCKS"

HELPER_NAME = "assert_report_matches_closeout_runs"
V5_GATE_FEATURE_ID = "infra-P299-followup-wire-into-closeout-gate"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_081][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, bool(ok), detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _scan_calls_in_source(src: str, callee: str) -> List[dict]:
    """ast 扫源码中所有 Call 节点, 找 func.id==callee 或 func.attr==callee.

    返回 [{lineno, in_func, arg1_source}], in_func 是定位到的最内层 FunctionDef
    名 (没找到 → '<module>'); arg1_source 是第一个位置参数的 ast.unparse 表示.
    """
    out: List[dict] = []
    try:
        tree = ast.parse(src)
    except Exception:  # noqa: BLE001
        return out

    def _walk(node, func_stack):
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            func_stack = func_stack + [node.name]
        if isinstance(node, ast.Call):
            f = node.func
            name = None
            if isinstance(f, ast.Name):
                name = f.id
            elif isinstance(f, ast.Attribute):
                name = f.attr
            if name == callee:
                arg1 = None
                if node.args:
                    try:
                        arg1 = ast.unparse(node.args[0])
                    except Exception:  # noqa: BLE001
                        arg1 = "<unparse-err>"
                out.append({
                    "lineno": getattr(node, "lineno", -1),
                    "in_func": func_stack[-1] if func_stack else "<module>",
                    "arg1_source": arg1,
                })
        for child in ast.iter_child_nodes(node):
            _walk(child, func_stack)

    _walk(tree, [])
    return out


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_lib_exists", LIB.is_file(), f"path={LIB}")
    _emit("V0_verify_062_exists", VERIFY_062.is_file(), f"path={VERIFY_062}")
    _emit(
        "V0_helper_name_nonempty",
        bool(HELPER_NAME),
        f"helper={HELPER_NAME}",
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
# V2: _verify_lib.py file sha
# ---------------------------------------------------------------------------
def v2_lib_file_sha() -> None:
    got = _file_sha(LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V2_lib_file_sha",
            True,
            f"placeholder OK; bump EXPECTED_VERIFY_LIB_FILE_SHA={got}",
        )
        return
    _emit(
        "V2_lib_file_sha",
        got == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: helper func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, HELPER_NAME)
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"error={e!r}")
        return
    if EXPECTED_HELPER_FUNC_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V3_helper_func_sha",
            True,
            f"placeholder OK; bump EXPECTED_HELPER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_HELPER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为校验
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    src062 = VERIFY_062.read_text(encoding="utf-8")
    calls = _scan_calls_in_source(src062, HELPER_NAME)

    # V4_1: 062 内至少一处 helper 调用
    _emit(
        "V4_1_verify_062_calls_helper",
        len(calls) >= 1,
        f"call_count={len(calls)} sites={[(c['lineno'], c['in_func']) for c in calls]}",
    )

    # V4_2: 至少一个调用位于真函数体 (in_func != '<module>')
    in_func_calls = [c for c in calls if c["in_func"] != "<module>"]
    # 进一步要求 main() 调用链可达: 062 main 直接调用 _enforce_closeout_byte_match,
    # 后者内调 helper. 静态校验: main() 函数体 ast 含 Name=_enforce_closeout_byte_match
    # 调用, 且 _enforce_closeout_byte_match 函数体含 HELPER_NAME 调用.
    enforce_calls = _scan_calls_in_source(src062, "_enforce_closeout_byte_match")
    main_calls_enforcer = any(c["in_func"] == "main" for c in enforce_calls)
    enforcer_calls_helper = any(
        c["in_func"] == "_enforce_closeout_byte_match" for c in calls
    )
    _emit(
        "V4_2_call_in_main_chain",
        len(in_func_calls) >= 1 and main_calls_enforcer and enforcer_calls_helper,
        f"in_func_count={len(in_func_calls)} "
        f"main_calls_enforcer={main_calls_enforcer} "
        f"enforcer_calls_helper={enforcer_calls_helper}",
    )

    # V4_3: 第一个 helper 调用的 arg1 不能是空 dict / None 字面 (防应付调用)
    bad_arg1 = []
    for c in calls:
        a = (c.get("arg1_source") or "").strip()
        if a in ("{}", "None", "", "<unparse-err>"):
            bad_arg1.append((c["lineno"], a))
    _emit(
        "V4_3_helper_arg1_nontrivial",
        not bad_arg1 and len(calls) >= 1,
        f"bad_arg1={bad_arg1} sample_arg1={[c.get('arg1_source') for c in calls][:3]}",
    )

    # V4_4: mutant — 拷贝 062 到 tmp 并删除所有 helper 调用 (regex 替换),
    # 再扫 → 必须 0 调用. 用 ast 检测不依赖文件磁盘, 直接传源码字符串.
    # 故意把 callee 名替换成 _DELETED_callee_, 确保 ast 不再匹配 HELPER_NAME.
    mutant_src, n_sub = re.subn(
        re.escape(HELPER_NAME) + r"\b",
        f"_DELETED_{HELPER_NAME}_",
        src062,
    )
    mutant_calls = _scan_calls_in_source(mutant_src, HELPER_NAME)
    # n_sub 应当 >= 调用次数 + import 行数; 实际只要 mutant_calls 为 0 且 n_sub>=1
    _emit(
        "V4_4_mutant_strip_call_detected_zero",
        n_sub >= 1 and len(mutant_calls) == 0,
        f"n_sub={n_sub} mutant_call_count={len(mutant_calls)} orig_count={len(calls)}",
    )

    # V4_5: 062 file sha 锁
    got = _file_sha(VERIFY_062)
    if EXPECTED_VERIFY_062_FILE_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V4_5_verify_062_file_sha",
            True,
            f"placeholder OK; bump EXPECTED_VERIFY_062_FILE_SHA={got}",
        )
    else:
        _emit(
            "V4_5_verify_062_file_sha",
            got == EXPECTED_VERIFY_062_FILE_SHA,
            f"got={got[:16]} expect={EXPECTED_VERIFY_062_FILE_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (真门: ok is True)
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    """V5 Reviewer LGTM gate — phase-47 #1.47 graduate to evidence-bind helper."""
    if not REAL_FEATURE_LIST.is_file():
        _emit(
            "V5_reviewer_lgtm_gate",
            False,
            f"feature_list.json not found at {REAL_FEATURE_LIST}",
        )
        return
    result = assert_v5_reviewer_gate_evidence_bind(
        V5_GATE_FEATURE_ID, REAL_FEATURE_LIST,
    )
    _emit(
        "V5_reviewer_lgtm_gate",
        bool(result["ok"]),
        f"target={V5_GATE_FEATURE_ID} helper_ok={result['ok']} "
        f"verdict={result['verdict']!r} kind={result['reviewer_kind']!r} "
        f"summary_len={result['summary_len']} reason={result['reason']!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_func_sha()
    v2_lib_file_sha()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    failed_tags = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_081][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_081][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
