#!/usr/bin/env python3
"""verify_infra_087 V0-V5: 锁定 verify_infra_062._enforce_closeout_smoke_tail_nonempty 真 fire (Default-OFF hard check).

infra-P278-followup-closeout-smoke-tail-nonempty-hard-check (phase-43 #5.43):
Closeout-verify-trustworthy 硬规则已要求 smoke_tail_stdout 为 string, 但未约束
内容. 常见占位 'smoke OK' 或空串无法 audit 真跑 ./init.sh. 加 Default-OFF→hard
检 (min_chars=20, must_contain=('Smoke','smoke') case-insensitive 任一匹配),
提升 smoke 证据可信度.

本 feature 在 ``scripts/_verify_lib.py`` 加
``assert_closeout_smoke_tail_nonempty(feature_list_path, min_chars=20,
must_contain=('Smoke','smoke'))`` helper, 并在 ``scripts/verify_infra_062.py``
内挂 ``_enforce_closeout_smoke_tail_nonempty()`` 以 V4_closeout_smoke_tail_nonempty
名义 emit:
- 缺 closeout_verify.smoke_tail_stdout 字段 → soft_skip (Default-OFF);
- 含字段但 strip 后 len < min_chars 或不含 keyword → hard FAIL;
- violations 列表 + 首条 violation 写进 detail.

本 verify (087) 锁住:
- _verify_lib 内 ``assert_closeout_smoke_tail_nonempty`` helper 真存在
  且 func sha 等于 EXPECTED_SMOKE_TAIL_HELPER_FUNC_SHA;
- _verify_lib 文件 sha 等于 EXPECTED_VERIFY_LIB_FILE_SHA;
- 真跑 helper 正例: 当前 feature_list.json → ok=True (scanned>=1, violations==0);
- mutant: 临时构造 in-memory mini feature_list 把某 passing feature
  smoke_tail_stdout 截到 'x' (短于 min_chars 且不含 Smoke) →
  helper 必须捕获 violation>=1;
- 062 内必须有 ``_enforce_closeout_smoke_tail_nonempty`` 调用挂到 main 链上 (ast 扫).

INFRA_087_SHA_LOCKS
-------------------
- ``scripts/verify_infra_087.py:main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:assert_closeout_smoke_tail_nonempty`` func sha:
  EXPECTED_SMOKE_TAIL_HELPER_FUNC_SHA

校验层级 (V0-V5, 共 14 checks):

- V0 scaffolding (×5)
- V1 self main() func sha 自锁
- V2 _verify_lib file sha
- V3 assert_closeout_smoke_tail_nonempty helper func sha
- V4 行为校验 (5 checks):
  - V4_1 062 函数体 ast 扫到至少一处 _enforce_closeout_smoke_tail_nonempty 调用
  - V4_2 调用挂到 062.main() 函数体上 (main → enforcer)
  - V4_3 真跑 helper 正例: 当前 feature_list.json → ok=True, scanned>=1, violations==0
  - V4_4 真跑 helper 反例: 构造临时 mini feature_list (tmpfile), 把某 feature
    smoke_tail_stdout 截到 'x' → violation>=1, ok=False; 原 feature_list 不被改动
  - V4_5 mutant: ast 替换 062 中 ENFORCER_NAME 调用名 → mutant_call_count==0 且 n_sub>=1
- V5 reviewer_lgtm_gate (真门: ok is True)

注意 (与 074/079/080/081/082/083/084/085/086 同形): 本脚本不锁自己 file sha,
避免与 V4 mutant 对源码做 ast 替换时与自检冲突, 且与 078 placeholder 禁字面约束兼容.

退出码: 0=ALL PASS, 2=任一 FAIL (走 verify_summary_exit).

运行环境约定 (infra-034): 必须在 .venv 下运行.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "_verify_lib.py"
VERIFY_062 = SCRIPTS / "verify_infra_062.py"
REAL_FEATURE_LIST = REPO / "feature_list.json"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (
    assert_closeout_smoke_tail_nonempty,
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
    assert_v5_reviewer_gate_evidence_bind,
)

EXPECTED_SELF_MAIN_FUNC_SHA = "a86044883527d054244f43c4bb74bb420f80458ac6d0126ca63aa9ed02be524f"
EXPECTED_VERIFY_LIB_FILE_SHA = "6098f8c1b0a70331a12407d0e184b7d30c6a014e5a7b37090ff4981cc357a93b"
EXPECTED_SMOKE_TAIL_HELPER_FUNC_SHA = "916354a6b581f4b118a5675937ea4eb96df5068c196aabd9ae3ba4188069a977"

DOCSTRING_SENTINEL = "INFRA_087_SHA_LOCKS"

HELPER_NAME = "assert_closeout_smoke_tail_nonempty"
ENFORCER_NAME = "_enforce_closeout_smoke_tail_nonempty"
V5_GATE_FEATURE_ID = "infra-P278-followup-closeout-smoke-tail-nonempty-hard-check"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_087][{mark}] {tag} {detail}", flush=True)
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
# V3: assert_closeout_smoke_tail_nonempty helper func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, HELPER_NAME)
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"error={e!r}")
        return
    if EXPECTED_SMOKE_TAIL_HELPER_FUNC_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V3_helper_func_sha",
            True,
            f"placeholder OK; bump EXPECTED_SMOKE_TAIL_HELPER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_SMOKE_TAIL_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_SMOKE_TAIL_HELPER_FUNC_SHA[:16]}",
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

    # V4_3: 真跑 helper 正例 — 当前 feature_list.json 应当 ok=True
    try:
        result = assert_closeout_smoke_tail_nonempty(
            REAL_FEATURE_LIST,
            min_chars=20,
            must_contain=("Smoke", "smoke"),
        )
        v4_3_ok = (
            isinstance(result, dict)
            and result.get("ok") is True
            and result.get("scanned_count", 0) >= 1
            and not result.get("error")
            and len(result.get("violations") or []) == 0
        )
        v4_3_detail = (
            f"ok={result.get('ok')} scanned={result.get('scanned_count')} "
            f"enforced={result.get('enforced_count')} "
            f"soft_skipped={len(result.get('soft_skipped') or [])} "
            f"violations={len(result.get('violations') or [])} "
            f"min_chars={result.get('min_chars')} "
            f"must_contain={result.get('must_contain')}"
        )
    except Exception as e:  # noqa: BLE001
        v4_3_ok = False
        v4_3_detail = f"err={e!r}"
    _emit(
        "V4_3_helper_real_run_ok",
        v4_3_ok,
        v4_3_detail,
    )

    # V4_4: 反例 — 构造临时 mini feature_list, 含一个 passing feature 其
    # closeout_verify.smoke_tail_stdout 仅 "x" (短于 20 且不含 Smoke);
    # helper 必须捕获 violation>=1. 原 feature_list.json 不被改动.
    try:
        mini = {
            "features": [
                {
                    "id": "mutant-fake-feature-for-087-test",
                    "status": "passing",
                    "evidence": {
                        "closeout_verify": {
                            "smoke_tail_stdout": "x"
                        }
                    },
                }
            ]
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tf:
            json.dump(mini, tf)
            tmppath = tf.name
        try:
            fr = assert_closeout_smoke_tail_nonempty(
                tmppath, min_chars=20, must_contain=("Smoke", "smoke")
            )
            v4_4_ok = (
                fr.get("ok") is False
                and len(fr.get("violations") or []) >= 1
                and any(
                    v.get("feature_id") == "mutant-fake-feature-for-087-test"
                    for v in (fr.get("violations") or [])
                )
            )
            v4_4_detail = (
                f"ok={fr.get('ok')} "
                f"violations={len(fr.get('violations') or [])} "
                f"first_violation={(fr.get('violations') or [None])[0]}"
            )
        finally:
            try:
                Path(tmppath).unlink()
            except OSError:
                pass
        # 还原后跑真 feature_list, 确认未受副作用影响
        rr = assert_closeout_smoke_tail_nonempty(
            REAL_FEATURE_LIST, min_chars=20, must_contain=("Smoke", "smoke")
        )
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
            f"[verify_infra_087][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_087][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
