#!/usr/bin/env python3
"""verify_infra_097 V0-V5: 锁定 verify_infra_062._enforce_closeout_verify_runs_freshness
emit 已是真硬 (bool(result['ok'])), 配套 grace_period 34 historic features.

infra-P299-followup-engineer-stale-verify-evidence (phase-46 #2.46):
新增 V4_closeout_verify_runs_freshness check, 首次即 hard PASS — emit=bool(result['ok']),
但用 grace_period_feature_ids 一次性 grandfather 34 个已存在 (passing+sub_agent_fresh_context)
但缺 freshness_anchor 字段的历史 feature; 新 feature 必须 hard PASS freshness_anchor 形态.

本 verify (097) 锁住:
- _verify_lib.py 的 assert_closeout_verify_runs_freshness helper 接受
  ``grace_period_feature_ids`` 参数 (signature 检查);
- _verify_lib file sha 与 helper func sha;
- 062 内必须定义 ``V4_VERIFY_RUNS_FRESHNESS_GRACE_PERIOD_FEATURE_IDS`` 常量 (非空 tuple);
- 062 _enforce_closeout_verify_runs_freshness 调用 helper 时传入 grace_period_feature_ids;
- 062 _enforce_closeout_verify_runs_freshness 内的 _emit 第二参不是裸 True (首次即真硬);
- 真跑 helper 正例: feature 在 grace_set 内 + 含 violation → ok=True + grace_skipped 含该 fid;
- 真跑 helper 反例: feature 不在 grace_set 内 + 含 violation → ok=False + violations 非空;
- mutant: 062 中 grace 常量名替换 → 062 仍可解析但 grace_period_count==0.

INFRA_097_SHA_LOCKS
-------------------
- ``scripts/verify_infra_097.py:main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:assert_closeout_verify_runs_freshness`` func sha:
  EXPECTED_FRESHNESS_HELPER_FUNC_SHA

校验层级 (V0-V5, 共 14 checks):

- V0 scaffolding (×5)
- V1 self main() func sha 自锁
- V2 _verify_lib file sha
- V3 assert_closeout_verify_runs_freshness helper func sha
- V4 行为校验 (5 checks):
  - V4_1 062 中 V4_VERIFY_RUNS_FRESHNESS_GRACE_PERIOD_FEATURE_IDS 常量定义 + 非空 tuple
  - V4_2 062 _enforce_closeout_verify_runs_freshness 调 helper 时传 grace_period_feature_ids
        参数 且 _emit 第二参不是裸 True (真硬 promote 检查)
  - V4_3 真跑 helper 正例: bad feature ∈ grace_set → ok=True, grace_skipped 含 fid
  - V4_4 真跑 helper 反例: bad feature ∉ grace_set → ok=False, violations 非空
  - V4_5 mutant: 062 中 GRACE 常量赋值改名 → re.subn 应替换 >=2 处 (常量定义 + call-site 引用)
- V5 reviewer_lgtm_gate (真门: V5 pending 期间预期 FAIL)

退出码: 0=ALL PASS, 2=任一 FAIL (verify_summary_exit).

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
from _verify_lib import (  # noqa: E402
    assert_closeout_verify_runs_freshness,
    assert_reviewer_lgtm,
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_SELF_MAIN_FUNC_SHA = "22907b65d6189e710ad51bf2ca1befe2a2870fdb0d65716f22f6767e1b12f441"
EXPECTED_VERIFY_LIB_FILE_SHA = "0461b1817b5d8a2c37f8ec39ac7c561eb20d9021110a237af2abec6e0ce90366"
EXPECTED_FRESHNESS_HELPER_FUNC_SHA = "624db90dcd2e321a1231ed65ac821941193ca6ab30ed547e5682b362aadc4fdd"

DOCSTRING_SENTINEL = "INFRA_097_SHA_LOCKS"

HELPER_NAME = "assert_closeout_verify_runs_freshness"
ENFORCER_NAME = "_enforce_closeout_verify_runs_freshness"
GRACE_CONST_NAME = "V4_VERIFY_RUNS_FRESHNESS_GRACE_PERIOD_FEATURE_IDS"
EMIT_TAG = "V4_closeout_verify_runs_freshness"
V5_GATE_FEATURE_ID = (
    "infra-P299-followup-engineer-stale-verify-evidence"
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_097][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, bool(ok), detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _bad_freshness_feature() -> dict:
    """构造一个 enforce-set 命中 (status=passing, reviewer_kind=sub_agent_fresh_context)
    但 verify_runs entry 缺 freshness_anchor 字段的 violation feature."""
    return {
        "id": "tmp-fid-freshness",
        "status": "passing",
        "evidence": {
            "closeout_verify": {
                "main_head_sha": "abc1234def5678",
                "verify_runs": [
                    {
                        "script": "scripts/verify_infra_001.py",
                        "tail_stdout": "[verify_infra_001][SUMMARY] ALL PASS\n",
                        "status": "PASS",
                        # freshness_anchor 故意缺失 → violation
                    },
                ],
                "reviewer": {
                    "reviewer_kind": "sub_agent_fresh_context",
                    "verdict": "LGTM",
                    "summary": "ok summary long enough text",
                    "checks_run": ["a"],
                    "findings": {"P0": [], "P1": [], "P2": []},
                },
            },
        },
    }


def _write_tmp_feature_list(features: list) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="verify_097_"))
    p = tmp / "feature_list.json"
    p.write_text(json.dumps({"features": features}), encoding="utf-8")
    return p


def _read_grace_tuple_from_062() -> tuple:
    """从 062 源码用 ast 抽 GRACE_CONST_NAME 的赋值 tuple (字符串元素)."""
    src = VERIFY_062.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == GRACE_CONST_NAME:
                    if isinstance(node.value, (ast.Tuple, ast.List)):
                        out = []
                        for el in node.value.elts:
                            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                out.append(el.value)
                        return tuple(out)
    return ()


def _enforcer_passes_grace_kwarg() -> tuple:
    """ast 扫 062 中 ENFORCER_NAME 函数, 检查内部是否对 HELPER_NAME 调用
    传入 keyword arg 'grace_period_feature_ids'. 返回 (ok, detail_str)."""
    src = VERIFY_062.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target_fn = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == ENFORCER_NAME:
            target_fn = node
            break
    if target_fn is None:
        return False, f"enforcer fn {ENFORCER_NAME} not found"
    for sub in ast.walk(target_fn):
        if isinstance(sub, ast.Call):
            fname = None
            if isinstance(sub.func, ast.Name):
                fname = sub.func.id
            elif isinstance(sub.func, ast.Attribute):
                fname = sub.func.attr
            if fname == HELPER_NAME:
                kws = [kw.arg for kw in sub.keywords]
                if "grace_period_feature_ids" in kws:
                    return True, f"call site OK; kws={kws}"
                return False, f"helper call missing grace_period_feature_ids kwarg; kws={kws}"
    return False, f"no call to {HELPER_NAME} inside {ENFORCER_NAME}"


def _enforcer_emit_not_naked_true() -> tuple:
    """ast 扫 062 中 ENFORCER_NAME 函数体, 找 _emit(EMIT_TAG, X, ...) 调用;
    若至少一个 X 是表达式(非裸 True 常量), 则视为 promote 到真硬 (PASS)."""
    src = VERIFY_062.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target_fn = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == ENFORCER_NAME:
            target_fn = node
            break
    if target_fn is None:
        return False, f"enforcer fn {ENFORCER_NAME} not found"
    last_naked_true = None
    last_promoted = None
    for sub in ast.walk(target_fn):
        if isinstance(sub, ast.Call):
            fname = None
            if isinstance(sub.func, ast.Name):
                fname = sub.func.id
            elif isinstance(sub.func, ast.Attribute):
                fname = sub.func.attr
            if fname == "_emit" and sub.args:
                first = sub.args[0]
                if isinstance(first, ast.Constant) and first.value == EMIT_TAG:
                    if len(sub.args) >= 2:
                        second = sub.args[1]
                        if isinstance(second, ast.Constant) and second.value is True:
                            last_naked_true = (getattr(sub, "lineno", -1))
                        else:
                            last_promoted = (getattr(sub, "lineno", -1), type(second).__name__)
    if last_promoted is not None:
        return True, f"promoted _emit at line {last_promoted}; (naked_true_lines may still exist for soft-skip err path)"
    return False, f"no promoted _emit found; last_naked_true={last_naked_true}"


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_verify_lib_exists", LIB.is_file(), f"path={LIB}")
    _emit("V0_verify_062_exists", VERIFY_062.is_file(), f"path={VERIFY_062}")
    _emit(
        "V0_names_nonempty",
        bool(HELPER_NAME) and bool(ENFORCER_NAME) and bool(GRACE_CONST_NAME),
        f"helper={HELPER_NAME} enforcer={ENFORCER_NAME} grace={GRACE_CONST_NAME}",
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
# V3: assert_closeout_verify_runs_freshness helper func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, HELPER_NAME)
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"error={e!r}")
        return
    if EXPECTED_FRESHNESS_HELPER_FUNC_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V3_helper_func_sha",
            True,
            f"placeholder OK; bump EXPECTED_FRESHNESS_HELPER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_FRESHNESS_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_FRESHNESS_HELPER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为校验
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    grace = _read_grace_tuple_from_062()
    _emit(
        "V4_1_grace_const_defined_nonempty",
        len(grace) >= 1,
        f"grace_const={GRACE_CONST_NAME} len={len(grace)} sample={grace[:2] if grace else None}",
    )

    ok_kwarg, detail_kwarg = _enforcer_passes_grace_kwarg()
    ok_promote, detail_promote = _enforcer_emit_not_naked_true()
    _emit(
        "V4_2_enforcer_passes_grace_and_promoted",
        ok_kwarg and ok_promote,
        f"kwarg_ok={ok_kwarg} ({detail_kwarg}); promote_ok={ok_promote} ({detail_promote})",
    )

    try:
        bad = _bad_freshness_feature()
        fid = bad["id"]
        path = _write_tmp_feature_list([bad])
        r_in_grace = assert_closeout_verify_runs_freshness(
            path, grace_period_feature_ids=(fid,)
        )
        gs = r_in_grace.get("grace_skipped") or []
        v4_3_ok = (
            r_in_grace.get("ok") is True
            and not r_in_grace.get("error")
            and fid in gs
            and len(r_in_grace.get("violations") or []) == 0
            and r_in_grace.get("grace_period_count") == 1
        )
        v4_3_detail = (
            f"ok={r_in_grace.get('ok')} "
            f"grace_skipped={gs} "
            f"violations={len(r_in_grace.get('violations') or [])} "
            f"grace_period_count={r_in_grace.get('grace_period_count')}"
        )
    except Exception as e:  # noqa: BLE001
        v4_3_ok = False
        v4_3_detail = f"err={e!r}"
    _emit("V4_3_grace_in_set_softpasses", v4_3_ok, v4_3_detail)

    try:
        bad = _bad_freshness_feature()
        path = _write_tmp_feature_list([bad])
        r_out_grace = assert_closeout_verify_runs_freshness(
            path, grace_period_feature_ids=()
        )
        violations = r_out_grace.get("violations") or []
        v4_4_ok = (
            r_out_grace.get("ok") is False
            and len(violations) >= 1
            and r_out_grace.get("grace_period_count") == 0
            and len(r_out_grace.get("grace_skipped") or []) == 0
        )
        v4_4_detail = (
            f"ok={r_out_grace.get('ok')} "
            f"violations={len(violations)} "
            f"grace_skipped={r_out_grace.get('grace_skipped')} "
            f"grace_period_count={r_out_grace.get('grace_period_count')} "
            f"first={violations[0] if violations else None}"
        )
    except Exception as e:  # noqa: BLE001
        v4_4_ok = False
        v4_4_detail = f"err={e!r}"
    _emit("V4_4_grace_out_of_set_hardfails", v4_4_ok, v4_4_detail)

    src062 = VERIFY_062.read_text(encoding="utf-8")
    occurrences = len(re.findall(re.escape(GRACE_CONST_NAME), src062))
    mutant_src, n_sub = re.subn(
        re.escape(GRACE_CONST_NAME), f"_MUTANT_{GRACE_CONST_NAME}", src062
    )
    _emit(
        "V4_5_mutant_grace_const_rename",
        occurrences >= 2 and n_sub == occurrences,
        f"orig_occurrences={occurrences} mutant_subs={n_sub}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (真门: ok is True)
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    # phase-46 #4.46: V5 switched to assert_v5_reviewer_gate_evidence_bind
    # (verdict in {LGTM,conditional} + sub_agent_fresh_context + summary>=20).
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
            f"[verify_infra_097][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_097][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
