#!/usr/bin/env python3
"""verify_infra_084 V0-V5: 锁定 verify_infra_062._enforce_reviewer_summary_nonempty 真 fire (Default-OFF hard check).

infra-P278-followup-reviewer-summary-nonempty-hard-check (phase-42 #4.42):
P278 closeout-verify-trustworthy 系列已经在 062 内挂了 `_enforce_baseline_head_echo_required`
(verify_infra_082 锁), 但 `reviewer.summary` 字段虽长期约定要有可读的总结句, 实际
schema 上并未校验:
- 老 feature reviewer 既可能是 dict 也可能是 list, 缺 summary 字段直接被 062 跳过;
- 含 summary 但是空串 / 仅空白 / 单字符占位 (`"ok"` 等) 也算过, 等于挂牌不开门。

本 feature 在 `scripts/_verify_lib.py` 加 `assert_reviewer_summary_nonempty(min_chars=20)`
helper, 并在 `scripts/verify_infra_062.py` 内挂 `_enforce_reviewer_summary_nonempty()`,
以 V4_reviewer_summary_nonempty 名义 emit:
- 老 feature 缺 reviewer.summary → soft_skipped (Default-OFF 渐进 promote);
- 含 reviewer.summary → hard enforce: `.strip()` 后长度必须 >= 20 字符;
- violation > 0 → V4 FAIL.

本 verify (084) 锁住:
- _verify_lib 内 `assert_reviewer_summary_nonempty` helper 真存在且 func sha 等于
  EXPECTED_REVIEWER_SUMMARY_HELPER_FUNC_SHA;
- _verify_lib 文件 sha 等于 EXPECTED_VERIFY_LIB_FILE_SHA;
- 真跑 helper: 当前 feature_list 应当 ok=True (scan>=1 且 enforced/soft_skip 之和
  等于 scan; violations==0);
- mutant: 删 _verify_lib 中 helper def → import 失败 / func 缺 → 行为不再可用;
- 062 内必须有 `_enforce_reviewer_summary_nonempty` 调用挂到 main 链上 (ast 扫).

INFRA_084_SHA_LOCKS
-------------------
- ``scripts/verify_infra_084.py:main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:assert_reviewer_summary_nonempty`` func sha:
  EXPECTED_REVIEWER_SUMMARY_HELPER_FUNC_SHA

校验层级 (V0-V5, 共 14 checks):

- V0 scaffolding (×5)
- V1 self main() func sha 自锁
- V2 _verify_lib file sha
- V3 assert_reviewer_summary_nonempty helper func sha
- V4 行为校验 (5 checks):
  - V4_1 062 函数体 ast 扫到至少一处 _enforce_reviewer_summary_nonempty 调用
  - V4_2 调用挂到 062.main() 函数体上 (main → _enforce_reviewer_summary_nonempty)
  - V4_3 真跑 helper 正例: 当前 REAL_FEATURE_LIST → ok=True, scanned >=1
  - V4_4 真跑 helper 反例: 注入虚拟 feature_list, 含 reviewer.summary="x" → violation>=1
  - V4_5 mutant: ast 替换 062 中 ENFORCER_NAME 调用名 → mutant_call_count==0 且 n_sub>=1
- V5 reviewer_lgtm_gate (真门: ok is True)

注意 (与 074/079/080/081/082/083 同形): 本脚本不锁自己 file sha, 避免与 V4 mutant
对源码做 ast 替换时与自检冲突, 且与 078 placeholder 禁字面约束兼容.

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
    assert_reviewer_lgtm,
    assert_reviewer_summary_nonempty,
    func_sha_by_name,
    verify_summary_exit,
    assert_v5_reviewer_gate_evidence_bind,
)

EXPECTED_SELF_MAIN_FUNC_SHA = "9e8c5b34d9b4cacac6301ced964831d52247d82bd2ae6a28908b4a469e558b71"
EXPECTED_VERIFY_LIB_FILE_SHA = "1990a9b61d3b14c9b827a23188a46001be2a208234352e1bc7af955285578f50"
EXPECTED_REVIEWER_SUMMARY_HELPER_FUNC_SHA = "4877aa66f51e5528ac0b266824205fe6c9380bfae076884061739ef59f45bdcb"

DOCSTRING_SENTINEL = "INFRA_084_SHA_LOCKS"

HELPER_NAME = "assert_reviewer_summary_nonempty"
ENFORCER_NAME = "_enforce_reviewer_summary_nonempty"
V5_GATE_FEATURE_ID = "infra-P278-followup-reviewer-summary-nonempty-hard-check"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_084][{mark}] {tag} {detail}", flush=True)
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
# V3: assert_reviewer_summary_nonempty helper func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, HELPER_NAME)
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"error={e!r}")
        return
    if EXPECTED_REVIEWER_SUMMARY_HELPER_FUNC_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V3_helper_func_sha",
            True,
            f"placeholder OK; bump EXPECTED_REVIEWER_SUMMARY_HELPER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_REVIEWER_SUMMARY_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_REVIEWER_SUMMARY_HELPER_FUNC_SHA[:16]}",
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

    # V4_3: 真跑 helper 正例 — 当前 REAL_FEATURE_LIST 应当 ok=True
    try:
        result = assert_reviewer_summary_nonempty(REAL_FEATURE_LIST, min_chars=20)
        v4_3_ok = (
            isinstance(result, dict)
            and result.get("ok") is True
            and result.get("scanned_count", 0) >= 1
            and not result.get("error")
        )
        v4_3_detail = (
            f"ok={result.get('ok')} scanned={result.get('scanned_count')} "
            f"enforced={result.get('enforced_count')} "
            f"soft_skipped={len(result.get('soft_skipped') or [])} "
            f"violations={len(result.get('violations') or [])}"
        )
    except Exception as e:  # noqa: BLE001
        v4_3_ok = False
        v4_3_detail = f"err={e!r}"
    _emit(
        "V4_3_helper_real_run_ok",
        v4_3_ok,
        v4_3_detail,
    )

    # V4_4: 反例 — 注入临时 feature_list, 含 reviewer.summary="x" (长度 1 < 20)
    # → enforced>=1, violations>=1, ok=False
    try:
        fake = {
            "features": [
                {
                    "id": "fake-too-short",
                    "status": "passing",
                    "evidence": {
                        "closeout_verify": {
                            "reviewer": {"summary": "x"},
                        },
                    },
                },
                {
                    "id": "fake-missing-soft-skip",
                    "status": "passing",
                    "evidence": {
                        "closeout_verify": {
                            "reviewer": {},
                        },
                    },
                },
            ]
        }
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as tf:
            json.dump(fake, tf)
            fake_path = Path(tf.name)
        try:
            fr = assert_reviewer_summary_nonempty(fake_path, min_chars=20)
            v4_4_ok = (
                fr.get("ok") is False
                and (fr.get("enforced_count") or 0) >= 1
                and len(fr.get("violations") or []) >= 1
                and len(fr.get("soft_skipped") or []) >= 1
            )
            v4_4_detail = (
                f"ok={fr.get('ok')} enforced={fr.get('enforced_count')} "
                f"violations={len(fr.get('violations') or [])} "
                f"soft_skipped={len(fr.get('soft_skipped') or [])}"
            )
        finally:
            try:
                fake_path.unlink()
            except OSError:
                pass
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
            f"[verify_infra_084][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_084][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
