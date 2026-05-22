#!/usr/bin/env python3
"""verify_infra_088 V0-V5: 锁定 verify_infra_062._enforce_v3_helper_drift_detector 真 fire (V3 helper drift detector).

infra-V6-backlog-062-v3-helper-func-sha-rebump-round2 (phase-44 #2.44):
062 V3 锁此前仅锁单个 helper (verify_closeout_evidence_trustworthy) 的 func sha.
round2 任务: 把"helper 集合本身"也纳入漂移检测 — 任何新加 helper 必须同步加入
062.V3_HELPER_FUNC_NAMES 列表 (或 V3_HELPER_DRIFT_ALLOWLIST), 否则 hard FAIL.

本 feature 在 ``scripts/_verify_lib.py`` 加 ``assert_verify_lib_helpers_in_v3_sha_table``
helper, 并在 ``scripts/verify_infra_062.py`` 内挂 ``_enforce_v3_helper_drift_detector()``
以 V3_helper_func_sha_drift_detector 名义 emit:
- 扫 _verify_lib.py 所有公共 helper (def + 非下划线开头);
- 与 062.V3_HELPER_FUNC_NAMES 比对; 任何 missing/extra → hard FAIL;
- 含 scanned_helpers/v3_table_keys/allowlist/missing/extra 写入 detail.

本 verify (088) 锁住:
- _verify_lib 内 ``assert_verify_lib_helpers_in_v3_sha_table`` helper 真存在
  且 func sha 等于 EXPECTED_DRIFT_HELPER_FUNC_SHA;
- _verify_lib 文件 sha 等于 EXPECTED_VERIFY_LIB_FILE_SHA;
- 真跑 helper 正例: 当前 062.V3_HELPER_FUNC_NAMES → ok=True;
- mutant: 删一个 helper 名 → ok=False, missing_in_v3_table 非空;
- 062 内必须有 ``_enforce_v3_helper_drift_detector`` 调用挂到 main 链上 (ast 扫).

INFRA_088_SHA_LOCKS
-------------------
- ``scripts/verify_infra_088.py:main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:assert_verify_lib_helpers_in_v3_sha_table`` func sha:
  EXPECTED_DRIFT_HELPER_FUNC_SHA

校验层级 (V0-V5, 共 14 checks):

- V0 scaffolding (×5)
- V1 self main() func sha 自锁
- V2 _verify_lib file sha
- V3 assert_verify_lib_helpers_in_v3_sha_table helper func sha
- V4 行为校验 (5 checks):
  - V4_1 062 函数体 ast 扫到至少一处 _enforce_v3_helper_drift_detector 调用
  - V4_2 调用挂到 062.main() 函数体上 (main → enforcer)
  - V4_3 真跑 helper 正例: 062.V3_HELPER_FUNC_NAMES → ok=True
  - V4_4 真跑 helper 反例: 删一个 name → ok=False, missing 非空
  - V4_5 mutant: ast 替换 062 中 ENFORCER_NAME 调用名 → mutant_call_count==0 且 n_sub>=1
- V5 reviewer_lgtm_gate (真门: ok is True, V5 pending 期间 FAIL 预期)

注意 (与 074/079/080/081/082/083/084/085/086/087 同形): 本脚本不锁自己 file sha,
避免与 V4 mutant 对源码做 ast 替换时与自检冲突.

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
    assert_verify_lib_helpers_in_v3_sha_table,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_SELF_MAIN_FUNC_SHA = "985b9e4552bcdc2ba0594669ac6d9caf8a8bd34535568bd567a0cea6adac8e87"
EXPECTED_VERIFY_LIB_FILE_SHA = "87db6ab1d43be8c94eeefb2b6e93752ebf9428b99cb387b0123ecc06ba73277e"
EXPECTED_DRIFT_HELPER_FUNC_SHA = "5b68351e4bc7916fc9b817ca3d2b2bbed8733227252a3bb13008950256e4d8ff"

DOCSTRING_SENTINEL = "INFRA_088_SHA_LOCKS"

HELPER_NAME = "assert_verify_lib_helpers_in_v3_sha_table"
ENFORCER_NAME = "_enforce_v3_helper_drift_detector"
V5_GATE_FEATURE_ID = "infra-V6-backlog-062-v3-helper-func-sha-rebump-round2"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_088][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, bool(ok), detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _scan_calls_in_source(src: str, callee: str) -> List[dict]:
    out: List[dict] = []
    try:
        tree = ast.parse(src)
    except Exception:  # noqa: BLE001
        return out

    def _walk(node, func_stack):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_stack = func_stack + [node.name]
        if isinstance(node, ast.Call):
            f = node.func
            name = None
            if isinstance(f, ast.Name):
                name = f.id
            elif isinstance(f, ast.Attribute):
                name = f.attr
            if name == callee:
                out.append({
                    "lineno": getattr(node, "lineno", -1),
                    "in_func": func_stack[-1] if func_stack else "<module>",
                })
        for child in ast.iter_child_nodes(node):
            _walk(child, func_stack)

    _walk(tree, [])
    return out


def _read_062_v3_names() -> tuple:
    """从 062 源码读出 V3_HELPER_FUNC_NAMES 元组 (ast 不 import 062, 防循环)."""
    src = VERIFY_062.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "V3_HELPER_FUNC_NAMES":
                    val = node.value
                    if isinstance(val, (ast.Tuple, ast.List)):
                        names = []
                        for el in val.elts:
                            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                names.append(el.value)
                        return tuple(names)
    return ()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_verify_lib_exists", LIB.is_file(), f"path={LIB}")
    _emit("V0_verify_062_exists", VERIFY_062.is_file(), f"path={VERIFY_062}")
    _emit(
        "V0_helper_name_nonempty",
        bool(HELPER_NAME) and bool(ENFORCER_NAME),
        f"helper={HELPER_NAME} enforcer={ENFORCER_NAME}",
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
# V2: _verify_lib file sha
# ---------------------------------------------------------------------------
def v2_verify_lib_file_sha() -> None:
    got = _file_sha(LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V2_verify_lib_file_sha",
            True,
            f"placeholder OK; bump EXPECTED_VERIFY_LIB_FILE_SHA={got}",
        )
        return
    _emit(
        "V2_verify_lib_file_sha",
        got == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: assert_verify_lib_helpers_in_v3_sha_table helper func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, HELPER_NAME)
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"error={e!r}")
        return
    if EXPECTED_DRIFT_HELPER_FUNC_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V3_helper_func_sha",
            True,
            f"placeholder OK; bump EXPECTED_DRIFT_HELPER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_DRIFT_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_DRIFT_HELPER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为校验
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    src062 = VERIFY_062.read_text(encoding="utf-8")
    enforcer_calls = _scan_calls_in_source(src062, ENFORCER_NAME)

    # V4_1: 062 内至少一处 enforcer 调用
    _emit(
        "V4_1_verify_062_calls_enforcer",
        len(enforcer_calls) >= 1,
        f"call_count={len(enforcer_calls)} "
        f"sites={[(c['lineno'], c['in_func']) for c in enforcer_calls]}",
    )

    # V4_2: enforcer 调用必须挂在 062.main() 上
    main_calls_enforcer = any(c["in_func"] == "main" for c in enforcer_calls)
    _emit(
        "V4_2_call_in_main_chain",
        main_calls_enforcer,
        f"main_calls_enforcer={main_calls_enforcer}",
    )

    # V4_3: 真跑 helper 正例 — 当前 062.V3_HELPER_FUNC_NAMES → ok=True
    try:
        names = _read_062_v3_names()
        result = assert_verify_lib_helpers_in_v3_sha_table(LIB, names)
        v4_3_ok = (
            isinstance(result, dict)
            and result.get("ok") is True
            and not result.get("error")
            and len(result.get("missing_in_v3_table") or []) == 0
            and len(result.get("extra_in_v3_table") or []) == 0
            and len(result.get("scanned_helpers") or []) >= 10
        )
        v4_3_detail = (
            f"ok={result.get('ok')} "
            f"scanned={len(result.get('scanned_helpers') or [])} "
            f"v3_table={len(result.get('v3_table_keys') or [])} "
            f"missing={len(result.get('missing_in_v3_table') or [])} "
            f"extra={len(result.get('extra_in_v3_table') or [])}"
        )
    except Exception as e:  # noqa: BLE001
        v4_3_ok = False
        v4_3_detail = f"err={e!r}"
    _emit("V4_3_helper_real_run_ok", v4_3_ok, v4_3_detail)

    # V4_4: 反例 — 删一个 name → missing 非空, ok=False
    try:
        names = _read_062_v3_names()
        # 删一个 (取第一个)
        truncated = tuple(names[1:]) if len(names) >= 2 else ()
        fr = assert_verify_lib_helpers_in_v3_sha_table(LIB, truncated)
        missing = fr.get("missing_in_v3_table") or []
        v4_4_ok = (
            fr.get("ok") is False
            and len(missing) >= 1
            and (names[0] in missing if names else False)
        )
        v4_4_detail = (
            f"ok={fr.get('ok')} "
            f"removed_name={names[0] if names else None} "
            f"missing_count={len(missing)} "
            f"first_missing={missing[0] if missing else None}"
        )
        # 还原跑一次确认无副作用
        rr = assert_verify_lib_helpers_in_v3_sha_table(LIB, names)
        v4_4_restored_ok = rr.get("ok") is True
        v4_4_detail += f" restored_ok={v4_4_restored_ok}"
        v4_4_ok = v4_4_ok and v4_4_restored_ok
    except Exception as e:  # noqa: BLE001
        v4_4_ok = False
        v4_4_detail = f"err={e!r}"
    _emit("V4_4_helper_real_run_violation", v4_4_ok, v4_4_detail)

    # V4_5: mutant — ast 替换 062 中 ENFORCER_NAME 调用名 → 扫降到 0
    mutant_src, n_sub = re.subn(
        re.escape(ENFORCER_NAME) + r"\b",
        f"_DELETED_{ENFORCER_NAME}_",
        src062,
    )
    mutant_calls = _scan_calls_in_source(mutant_src, ENFORCER_NAME)
    _emit(
        "V4_5_mutant_strip_call_detected_zero",
        n_sub >= 1 and len(mutant_calls) == 0,
        f"n_sub={n_sub} mutant_call_count={len(mutant_calls)} "
        f"orig_count={len(enforcer_calls)}",
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
        f"target={V5_GATE_FEATURE_ID} helper_ok={ok} reason={reason!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_func_sha()
    v2_verify_lib_file_sha()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    failed_tags = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_088][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_088][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
