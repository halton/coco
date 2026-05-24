#!/usr/bin/env python3
"""verify_infra_085 V0-V5: 锁定 verify_infra_062._enforce_verify_lib_helper_naming 真 fire (Default-OFF hard check).

infra-P278-followup-verify-lib-helper-naming-convention-lock (phase-42 #5.42):
``scripts/_verify_lib.py`` 内 helper 数量已超 20, 命名风格曾经分化
(assert_* / scan_* / verify_* / func_* / read_*). 本 feature 锁定命名规约:
新增公开 helper 必须以 ``assert_`` 或 ``enforce_`` 开头;
legacy 已 export 的名字通过模块内置 _VERIFY_LIB_LEGACY_PUBLIC_HELPER_ALLOWLIST
显式豁免 (rename 入 backlog 单独 feature).

本 feature 在 ``scripts/_verify_lib.py`` 加
``assert_verify_lib_public_helper_naming(allowed_prefixes, legacy_allowlist)`` helper,
并在 ``scripts/verify_infra_062.py`` 内挂 ``_enforce_verify_lib_helper_naming()``,
以 V4_verify_lib_helper_naming_convention 名义 emit:
- _verify_lib 缺 __all__ → soft_skip (Default-OFF);
- 含 __all__ → hard enforce: __all__ 中 callable 名字必须以 allowed_prefixes 任一开头,
  或在 legacy_allowlist 中;
- violation > 0 → V4 FAIL.

本 verify (085) 锁住:
- _verify_lib 内 ``assert_verify_lib_public_helper_naming`` helper 真存在
  且 func sha 等于 EXPECTED_NAMING_HELPER_FUNC_SHA;
- _verify_lib 文件 sha 等于 EXPECTED_VERIFY_LIB_FILE_SHA;
- 真跑 helper: 当前 _verify_lib 应当 ok=True (scanned>=1, violations==0);
- mutant: 临时注入一个不带前缀的 fake public callable 到 __all__ →
  helper 必须捕获 violation>=1;
- 062 内必须有 ``_enforce_verify_lib_helper_naming`` 调用挂到 main 链上 (ast 扫).

INFRA_085_SHA_LOCKS
-------------------
- ``scripts/verify_infra_085.py:main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:assert_verify_lib_public_helper_naming`` func sha:
  EXPECTED_NAMING_HELPER_FUNC_SHA

校验层级 (V0-V5, 共 14 checks):

- V0 scaffolding (×5)
- V1 self main() func sha 自锁
- V2 _verify_lib file sha
- V3 assert_verify_lib_public_helper_naming helper func sha
- V4 行为校验 (5 checks):
  - V4_1 062 函数体 ast 扫到至少一处 _enforce_verify_lib_helper_naming 调用
  - V4_2 调用挂到 062.main() 函数体上 (main → enforcer)
  - V4_3 真跑 helper 正例: 当前 _verify_lib → ok=True, scanned>=1, violations==0
  - V4_4 真跑 helper 反例: 注入临时不带前缀的 fake public callable →
    violation>=1, ok=False; 还原后 ok=True
  - V4_5 mutant: ast 替换 062 中 ENFORCER_NAME 调用名 → mutant_call_count==0 且 n_sub>=1
- V5 reviewer_lgtm_gate (真门: ok is True)

注意 (与 074/079/080/081/082/083/084 同形): 本脚本不锁自己 file sha,
避免与 V4 mutant 对源码做 ast 替换时与自检冲突, 且与 078 placeholder 禁字面约束兼容.

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
    assert_verify_lib_public_helper_naming,
    func_sha_by_name,
    verify_summary_exit,
    assert_v5_reviewer_gate_evidence_bind,
)

EXPECTED_SELF_MAIN_FUNC_SHA = "87e910ff05a210135a341c163a0c71c1e4ac60371d55fbb983e16f0dc591aadb"
EXPECTED_VERIFY_LIB_FILE_SHA = "507d441cd9181e885e5a23739bf259680262d5c42c83d7b006aa72ada7c56cf7"
EXPECTED_NAMING_HELPER_FUNC_SHA = "2ab8a07e421920e25f22dff751adb514c4b0fddec05ec2d645fe8d839d45f876"

DOCSTRING_SENTINEL = "INFRA_085_SHA_LOCKS"

HELPER_NAME = "assert_verify_lib_public_helper_naming"
ENFORCER_NAME = "_enforce_verify_lib_helper_naming"
V5_GATE_FEATURE_ID = "infra-P278-followup-verify-lib-helper-naming-convention-lock"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_085][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, bool(ok), detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _scan_calls_in_source(src: str, callee: str) -> List[dict]:
    """ast 扫源码中所有 Call 节点, 找 func.id==callee 或 func.attr==callee."""
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
# V3: assert_verify_lib_public_helper_naming helper func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, HELPER_NAME)
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"error={e!r}")
        return
    if EXPECTED_NAMING_HELPER_FUNC_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V3_helper_func_sha",
            True,
            f"placeholder OK; bump EXPECTED_NAMING_HELPER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_NAMING_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_NAMING_HELPER_FUNC_SHA[:16]}",
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

    # V4_2: enforcer 调用必须挂在 062.main() 上 (main → enforcer)
    main_calls_enforcer = any(c["in_func"] == "main" for c in enforcer_calls)
    _emit(
        "V4_2_call_in_main_chain",
        main_calls_enforcer,
        f"main_calls_enforcer={main_calls_enforcer}",
    )

    # V4_3: 真跑 helper 正例 — 当前 _verify_lib 应当 ok=True
    try:
        result = assert_verify_lib_public_helper_naming()
        v4_3_ok = (
            isinstance(result, dict)
            and result.get("ok") is True
            and result.get("scanned_count", 0) >= 1
            and not result.get("error")
            and result.get("soft_skipped") is False
            and len(result.get("violations") or []) == 0
        )
        v4_3_detail = (
            f"ok={result.get('ok')} scanned={result.get('scanned_count')} "
            f"enforced={result.get('enforced_count')} "
            f"legacy_allowlisted={result.get('legacy_allowlisted_count')} "
            f"violations={len(result.get('violations') or [])} "
            f"soft_skipped={result.get('soft_skipped')}"
        )
    except Exception as e:  # noqa: BLE001
        v4_3_ok = False
        v4_3_detail = f"err={e!r}"
    _emit(
        "V4_3_helper_real_run_ok",
        v4_3_ok,
        v4_3_detail,
    )

    # V4_4: 反例 — 临时注入一个不带前缀的 fake public callable 到 _verify_lib
    # __all__, helper 必须捕获 violation>=1; 还原后必须 ok=True
    fake_name = "totally_invalid_public_helper_for_mutant_test"
    try:
        import _verify_lib as _vl
        orig_all = list(_vl.__all__)
        # 注入 callable + 加入 __all__
        setattr(_vl, fake_name, lambda: None)
        _vl.__all__ = orig_all + [fake_name]
        try:
            fr = assert_verify_lib_public_helper_naming()
            v4_4_ok = (
                fr.get("ok") is False
                and len(fr.get("violations") or []) >= 1
                and any(
                    v.get("name") == fake_name for v in (fr.get("violations") or [])
                )
            )
            v4_4_detail = (
                f"ok={fr.get('ok')} violations={len(fr.get('violations') or [])} "
                f"first_violation={(fr.get('violations') or [None])[0]}"
            )
        finally:
            # 还原
            _vl.__all__ = orig_all
            try:
                delattr(_vl, fake_name)
            except AttributeError:
                pass
        # 还原后再跑一次, 确认恢复
        rr = assert_verify_lib_public_helper_naming()
        v4_4_restored_ok = rr.get("ok") is True
        v4_4_detail += f" restored_ok={v4_4_restored_ok}"
        v4_4_ok = v4_4_ok and v4_4_restored_ok
    except Exception as e:  # noqa: BLE001
        v4_4_ok = False
        v4_4_detail = f"err={e!r}"
    _emit(
        "V4_4_helper_real_run_violation",
        v4_4_ok,
        v4_4_detail,
    )

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
    v2_verify_lib_file_sha()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    failed_tags = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_085][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_085][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
