#!/usr/bin/env python3
"""verify_infra_082 V0-V5: 锁定 verify_infra_062 必须 wire assert_baseline_head_echo_present_and_matches 进 closeout gate.

infra-P286-followup3-promote-baseline-head-echo-to-P278-hard-required (phase-42 #1.42):
P286 round-1 dogfood (verify_infra_077) 把 reviewer.baseline_head_echo 字段在
单个 evidence 内做了 legacy 容差校验; 但这只是 "可选信号", 任一新 closeout 漏
带 echo 都不会阻 062 (P278 主入口). 本 feature 把 baseline_head_echo 从
optional + legacy-skip 升级为 P278 第 6 hard-required 信号:

- _verify_lib.py 引入跨 feature_list 扫描型 helper
  ``assert_baseline_head_echo_present_and_matches`` (Default-OFF / 渐进 promote:
  缺字段 → soft_skipped, 含字段 → 严格 enforce 前 7+ hex 等值匹配
  closeout_verify.baseline_head_sha);
- verify_infra_062.main() 内新增 ``_enforce_baseline_head_echo_required`` 函数
  ast 静态调用 helper, 写出 V4_baseline_head_echo_required emit;
- 082 V4 ast-lock 锁住 "062 函数体必须出现 helper 调用" 这一静态事实, 删调用
  即 082 FAIL (机械化阻 merge), 并通过 mutant 替换验证 detection 真实有效.

策略 (Default-OFF 渐进 promote, 与 062 V4_byte_match 风格一致):
  - 老 feature (缺 reviewer.baseline_head_echo 字段) → soft_skipped
    (不阻 pre-P286 历史 evidence PASS, 不强 retro fix)
  - 含 baseline_head_echo 字段 → hard enforce 前 7+ hex 等值匹配
    closeout_verify.baseline_head_sha (大小写不敏感); 不匹配 → violation
  - closeout_verify.baseline_head_sha 缺失 → soft_skipped (老 schema)

INFRA_082_SHA_LOCKS
-------------------
- ``scripts/verify_infra_082.py:main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:assert_baseline_head_echo_present_and_matches``
  canonical func sha: EXPECTED_HELPER_FUNC_SHA
- ``scripts/verify_infra_062.py`` file sha: EXPECTED_VERIFY_062_FILE_SHA

校验层级 (V0-V5, 共 14 checks):

- V0 scaffolding (×5)
- V1 self main() func sha 自锁
- V2 _verify_lib.py file sha
- V3 helper canonical func sha
- V4 行为校验 (5 checks):
  - V4_1 062 函数体 ast 扫到至少一处 helper 调用
  - V4_2 调用位于真 def 函数体且 main() 可达 (main → _enforce_baseline_head_echo_required
    → helper)
  - V4_3 调用入参第一个位置参数非平凡 (非空 dict/None/<unparse-err>; 防应付调用)
  - V4_4 mutant: 替换 062 中 helper callee 名 → ast 扫 helper 调用必须降到 0
  - V4_5 062 file sha 锁 (EXPECTED_VERIFY_062_FILE_SHA), 防止 062 被悄悄改回
    不调 helper 的版本但还能过 V4_1
- V5 reviewer_lgtm_gate (真门: ok is True)

注意 (与 074/079/080/081 同形): 本脚本不锁自己 file sha, 避免与 V4_4 mutant
对源码做 ast 替换时与 V2 自检冲突, 且与 078 placeholder 禁字面约束兼容.

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
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_SELF_MAIN_FUNC_SHA = "c45cf4954d41da02d2c90c419de7913db5915bdb51146441bfd0c626813b7378"
EXPECTED_VERIFY_LIB_FILE_SHA = "ebcec7ec936822801c7d656f9b6de429d2145f74133676ead1dfd860c5addd0d"
EXPECTED_HELPER_FUNC_SHA = "c6dc6341726578428019a97be704d6a4432f9b0d3fbb17b384f19ab18dc1d873"
EXPECTED_VERIFY_062_FILE_SHA = "51fb9bdf49233ccbcf8eb9c1c47b492d18a1a152f5b7cd060cfca04b19ea878f"

DOCSTRING_SENTINEL = "INFRA_082_SHA_LOCKS"

HELPER_NAME = "assert_baseline_head_echo_present_and_matches"
ENFORCER_NAME = "_enforce_baseline_head_echo_required"
V5_GATE_FEATURE_ID = "infra-P286-followup3-promote-baseline-head-echo-to-P278-hard-required"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_082][{mark}] {tag} {detail}", flush=True)
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

    # V4_2: 调用位于真 def 函数体且 main 可达
    # main → _enforce_baseline_head_echo_required → helper
    in_func_calls = [c for c in calls if c["in_func"] != "<module>"]
    enforcer_calls = _scan_calls_in_source(src062, ENFORCER_NAME)
    main_calls_enforcer = any(c["in_func"] == "main" for c in enforcer_calls)
    enforcer_calls_helper = any(
        c["in_func"] == ENFORCER_NAME for c in calls
    )
    _emit(
        "V4_2_call_in_main_chain",
        len(in_func_calls) >= 1 and main_calls_enforcer and enforcer_calls_helper,
        f"in_func_count={len(in_func_calls)} "
        f"main_calls_enforcer={main_calls_enforcer} "
        f"enforcer_calls_helper={enforcer_calls_helper}",
    )

    # V4_3: helper 调用 arg1 非平凡 (典型为 feature_list 路径 Name/Attribute,
    # 防止有人调成空 dict / None / 字面应付检查)
    bad_arg1 = []
    for c in calls:
        a = (c.get("arg1_source") or "").strip()
        if a in ("{}", "None", "", "<unparse-err>", "[]"):
            bad_arg1.append((c["lineno"], a))
    _emit(
        "V4_3_helper_arg1_nontrivial",
        not bad_arg1 and len(calls) >= 1,
        f"bad_arg1={bad_arg1} sample_arg1={[c.get('arg1_source') for c in calls][:3]}",
    )

    # V4_4: mutant — 把 062 中 HELPER_NAME 替换成 _DELETED_<HELPER>_,
    # 再 ast 扫 → 必须 0 调用 (detection 真有效)
    mutant_src, n_sub = re.subn(
        re.escape(HELPER_NAME) + r"\b",
        f"_DELETED_{HELPER_NAME}_",
        src062,
    )
    mutant_calls = _scan_calls_in_source(mutant_src, HELPER_NAME)
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
        f"target={V5_GATE_FEATURE_ID} ok={ok} reason={reason!r}",
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
            f"[verify_infra_082][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_082][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
