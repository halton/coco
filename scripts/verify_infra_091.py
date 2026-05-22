#!/usr/bin/env python3
"""verify_infra_091 V0-V5: 锁定 verify_infra_062._enforce_closeout_baseline_head_echo_format
真 fire (V4 closeout_verify.baseline_head_echo 形态合规 hard check).

infra-P278-followup-closeout-baseline-head-echo-format-hard-check (phase-44 #5.44):
已有 V4_baseline_head_echo_required 仅检查 baseline_head_echo 字段是否存在且与
closeout_verify.baseline_head_sha 前 7 hex 一致, 但对 baseline_head_echo 本身的
形态 (是否为非空 str / 是否 >=7 / 是否全 hex / 是否与 main_head_sha 不同) 仍无
机械化约束, 仍允许 baseline_head_echo='' / 'abc' / 'XYZ1234' / 与 main_head_sha
完全相同等不可 audit 的占位.

本 feature 在 ``scripts/_verify_lib.py`` 加 ``assert_closeout_baseline_head_echo_format``
helper, 并在 ``scripts/verify_infra_062.py`` 内挂 ``_enforce_closeout_baseline_head_echo_format()``
以 ``V4_closeout_baseline_head_echo_format`` 名义 emit. 062 处采 Default-OFF + soft-PASS
形式 (emit=True), 把 scanned/enforced/soft_skipped/violations 写入 detail.

本 verify (091) 锁住:
- _verify_lib 内 ``assert_closeout_baseline_head_echo_format`` helper 真存在
  且 func sha 等于 EXPECTED_BASELINE_HELPER_FUNC_SHA;
- _verify_lib 文件 sha 等于 EXPECTED_VERIFY_LIB_FILE_SHA;
- 真跑 helper 正例: 仿造 enforce-set 内一个 fully-shaped feature → ok=True;
- 真跑 helper 反例: baseline_head_echo 四种坏形态 (空 / <7 / 含非 hex / == main 前 N hex)
  → ok=False, violations 含全部 4 种情况;
- 062 内必须有 ``_enforce_closeout_baseline_head_echo_format`` 调用挂到 main 链上 (ast 扫);
- mutant: ast 替换 062 中 ENFORCER_NAME 调用名 → mutant_call_count==0 且 n_sub>=1.

INFRA_091_SHA_LOCKS
-------------------
- ``scripts/verify_infra_091.py:main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:assert_closeout_baseline_head_echo_format`` func sha:
  EXPECTED_BASELINE_HELPER_FUNC_SHA

校验层级 (V0-V5, 共 14 checks):

- V0 scaffolding (×5)
- V1 self main() func sha 自锁
- V2 _verify_lib file sha
- V3 assert_closeout_baseline_head_echo_format helper func sha
- V4 行为校验 (5 checks):
  - V4_1 062 函数体 ast 扫到至少一处 _enforce_closeout_baseline_head_echo_format 调用
  - V4_2 调用挂到 062.main() 函数体上 (main → enforcer)
  - V4_3 真跑 helper 正例: tmp feature_list 含一个 fully-shaped enforce-set → ok=True
  - V4_4 真跑 helper 反例: tmp feature_list 含四种坏形态 → violations 覆盖全部 4 种
  - V4_5 mutant: ast 替换 062 中 ENFORCER_NAME 调用名 → mutant_call_count==0 且 n_sub>=1
- V5 reviewer_lgtm_gate (真门: ok is True, V5 pending 期间 FAIL 预期)

注意: 本脚本不锁自己 file sha (与 074/079-090 同形), 避免与 V4 mutant 对源码做 ast
替换时与自检冲突.

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
from _verify_lib import (  # noqa: E402
    assert_closeout_baseline_head_echo_format,
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_SELF_MAIN_FUNC_SHA = "72d4e15f0582a8e21d80463ee5ec8c0d1e64b10135567b015fba55ddb5c58882"
EXPECTED_VERIFY_LIB_FILE_SHA = "41caff9b158c9d829e640b845a0a27c822b547773ddb49ecb27f73f32d56d490"
EXPECTED_BASELINE_HELPER_FUNC_SHA = "99b207a6b0dd305e47695f90b7e556cc1c0283ade0ae314fa6e5463b5f88a668"

DOCSTRING_SENTINEL = "INFRA_091_SHA_LOCKS"

HELPER_NAME = "assert_closeout_baseline_head_echo_format"
ENFORCER_NAME = "_enforce_closeout_baseline_head_echo_format"
V5_GATE_FEATURE_ID = "infra-P278-followup-closeout-baseline-head-echo-format-hard-check"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_091][{mark}] {tag} {detail}", flush=True)
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


def _good_feature() -> dict:
    return {
        "id": "good-feature",
        "status": "passing",
        "evidence": {
            "closeout_verify": {
                "main_head_sha": "deadbeefcafe0001",
                "baseline_head_echo": "abc1234",
                "reviewer": {
                    "reviewer_kind": "sub_agent_fresh_context",
                    "verdict": "LGTM",
                    "summary": "fresh-context review",
                    "checks_run": ["V0"],
                    "findings": {"P0": [], "P1": [], "P2": []},
                },
            },
        },
    }


def _bad_features() -> list:
    # 4 个 feature, 每个触发一种坏形态, 全部在 enforce-set 内:
    base_reviewer = {
        "reviewer_kind": "sub_agent_fresh_context",
        "verdict": "LGTM",
        "summary": "fresh-context review summary text",
        "checks_run": ["V0"],
        "findings": {"P0": [], "P1": [], "P2": []},
    }
    return [
        # 反例 1: 空 str
        {
            "id": "bad-empty",
            "status": "passing",
            "evidence": {
                "closeout_verify": {
                    "main_head_sha": "deadbeefcafe0001",
                    "baseline_head_echo": "",
                    "reviewer": base_reviewer,
                },
            },
        },
        # 反例 2: 长度 < 7
        {
            "id": "bad-short",
            "status": "passing",
            "evidence": {
                "closeout_verify": {
                    "main_head_sha": "deadbeefcafe0001",
                    "baseline_head_echo": "abc",
                    "reviewer": base_reviewer,
                },
            },
        },
        # 反例 3: 含非 hex (大写 / X / Y / Z)
        {
            "id": "bad-nonhex",
            "status": "passing",
            "evidence": {
                "closeout_verify": {
                    "main_head_sha": "deadbeefcafe0001",
                    "baseline_head_echo": "XYZ1234",
                    "reviewer": base_reviewer,
                },
            },
        },
        # 反例 4: == main_head_sha 前 N 字符
        {
            "id": "bad-same",
            "status": "passing",
            "evidence": {
                "closeout_verify": {
                    "main_head_sha": "abcd1234abcd5678",
                    "baseline_head_echo": "abcd123",
                    "reviewer": base_reviewer,
                },
            },
        },
    ]


def _write_tmp_feature_list(features: list) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="verify_091_"))
    p = tmp / "feature_list.json"
    p.write_text(json.dumps({"features": features}), encoding="utf-8")
    return p


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
# V3: assert_closeout_baseline_head_echo_format helper func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, HELPER_NAME)
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"error={e!r}")
        return
    if EXPECTED_BASELINE_HELPER_FUNC_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V3_helper_func_sha",
            True,
            f"placeholder OK; bump EXPECTED_BASELINE_HELPER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_BASELINE_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_BASELINE_HELPER_FUNC_SHA[:16]}",
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

    # V4_3: 真跑 helper 正例 — tmp feature_list 含一个 fully-shaped enforce-set
    try:
        good_path = _write_tmp_feature_list([_good_feature()])
        r_ok = assert_closeout_baseline_head_echo_format(good_path)
        v4_3_ok = (
            isinstance(r_ok, dict)
            and r_ok.get("ok") is True
            and not r_ok.get("error")
            and r_ok.get("enforced_count") == 1
            and len(r_ok.get("violations") or []) == 0
        )
        v4_3_detail = (
            f"ok={r_ok.get('ok')} "
            f"enforced={r_ok.get('enforced_count')} "
            f"soft_skipped={len(r_ok.get('soft_skipped') or [])} "
            f"violations={len(r_ok.get('violations') or [])}"
        )
    except Exception as e:  # noqa: BLE001
        v4_3_ok = False
        v4_3_detail = f"err={e!r}"
    _emit("V4_3_helper_real_run_ok", v4_3_ok, v4_3_detail)

    # V4_4: 真跑 helper 反例 — 4 种坏形态 (empty / <7 / 含非 hex / == main 前 N)
    try:
        bad_path = _write_tmp_feature_list(_bad_features())
        r_bad = assert_closeout_baseline_head_echo_format(bad_path)
        violations = r_bad.get("violations") or []
        # 4 个 feature 都应在 enforce-set, 各产生一条 violation
        feat_ids_hit = {v.get("feature_id") for v in violations}
        expected_ids = {"bad-empty", "bad-short", "bad-nonhex", "bad-same"}
        v4_4_ok = (
            r_bad.get("ok") is False
            and r_bad.get("enforced_count") == 4
            and len(violations) >= 4
            and expected_ids.issubset(feat_ids_hit)
        )
        v4_4_detail = (
            f"ok={r_bad.get('ok')} "
            f"enforced={r_bad.get('enforced_count')} "
            f"violations={len(violations)} "
            f"feat_ids_hit={sorted(feat_ids_hit)} "
            f"expected={sorted(expected_ids)} "
            f"first={violations[0] if violations else None}"
        )
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
            f"[verify_infra_091][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_091][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
