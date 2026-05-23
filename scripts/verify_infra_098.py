#!/usr/bin/env python3
"""verify_infra_098 V0-V5: 锁定 assert_v5_reviewer_gate_evidence_bind helper +
092-097 共 6 个 verify_infra 切换 V5 至 helper 调用.

infra-P294-followup-v5-reviewer-gate-evidence-bind (phase-46 #4.46):
V5_reviewer_lgtm_gate 在多数 verify_infra_*.py 中此前用 helper_ok 类
placeholder/structural pass; 本 feature 把 V5 升级为实读 evidence.reviewer
(verdict ∈ {LGTM,conditional} + reviewer_kind == sub_agent_fresh_context +
summary 长度 >= 20 字符).

本 verify (098) 锁住:
- _verify_lib.py 的 assert_v5_reviewer_gate_evidence_bind helper 公开 (def +
  __all__) + 接受 grace_period_feature_ids/min_summary_chars/allowed_verdicts
  /required_reviewer_kind 参数;
- _verify_lib.py file sha 与 helper func sha;
- 092-097 共 6 个 verify_infra 在 V5 中真调 assert_v5_reviewer_gate_evidence_bind
  (源码 grep);
- 真跑 helper 正例: feature 在 grace_period_feature_ids 内 → ok=True;
- 真跑 helper 反例: feature evidence 缺 reviewer → ok=False;
- 真跑 helper 正例: feature evidence 完整 + verdict=conditional → ok=True;
- 真跑 helper 反例: summary 过短 → ok=False;
- V5 自指本 feature (infra-P294-followup-v5-reviewer-gate-evidence-bind);
  feature 当前 not_started, V5 预期 FAIL — closeout 切 passing 写入
  evidence.closeout_verify.reviewer 后 V5 自然 PASS.

INFRA_098_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:assert_v5_reviewer_gate_evidence_bind`` func sha:
  EXPECTED_V5_HELPER_FUNC_SHA

校验层级 (V0-V5, 共 14 checks):

- V0 scaffolding (×5):
  - V0_verify_lib_exists
  - V0_helper_def_present (def assert_v5_reviewer_gate_evidence_bind( in lib)
  - V0_helper_in_all (__all__ contains name)
  - V0_self_main_entry
  - V0_targets_use_new_helper (092-097 各文件源码均 grep 到 helper 名调用)
- V1 self main() func sha 自锁 (1 check)
- V2 _verify_lib file sha (1 check)
- V3 assert_v5_reviewer_gate_evidence_bind helper func sha (1 check)
- V4 行为校验 (5 checks):
  - V4_1 真跑 helper 正例: grace_period 命中 → ok=True grace_skipped=True
  - V4_2 真跑 helper 反例: evidence 缺 reviewer → ok=False
  - V4_3 真跑 helper 正例: verdict=conditional + 完整 evidence → ok=True
  - V4_4 真跑 helper 反例: summary 过短 → ok=False (reason mention summary)
  - V4_5 真跑 helper 反例: reviewer_kind 错 (e.g. main_context_self) → ok=False
- V5 reviewer_lgtm_gate (真门: V5 pending 期间预期 FAIL)

退出码: 0=ALL PASS, 2=任一 FAIL (verify_summary_exit).
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
REAL_FEATURE_LIST = REPO / "feature_list.json"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_SELF_MAIN_FUNC_SHA = (
    "aad0e302134fb6c72acfbf7ab5add861f886ce2dd61ea7bc932e5c40183c6ea1"
)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "48a283fba25bb498b0dbf2b00423d80d2f8df647453822179e2ffb29f973abfa"
)
EXPECTED_V5_HELPER_FUNC_SHA = (
    "f27436457cf1bd3b07e92226a517ec383b008c58d0f47c89acc096fca32dff62"
)

DOCSTRING_SENTINEL = "INFRA_098_SHA_LOCKS"
HELPER_NAME = "assert_v5_reviewer_gate_evidence_bind"
TARGET_VERIFY_IDS = ("092", "093", "094", "095", "096", "097")
V5_GATE_FEATURE_ID = "infra-P294-followup-v5-reviewer-gate-evidence-bind"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_098][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, bool(ok), detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_tmp_feature_list(features: list) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="verify_098_"))
    p = tmp / "feature_list.json"
    p.write_text(json.dumps({"features": features}), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_verify_lib_exists", LIB.is_file(), f"path={LIB}")
    if not LIB.is_file():
        return
    src = LIB.read_text(encoding="utf-8")
    _emit(
        "V0_helper_def_present",
        f"def {HELPER_NAME}(" in src,
        f"expect 'def {HELPER_NAME}(' in lib",
    )
    tree = ast.parse(src)
    all_names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for el in node.value.elts:
                            if isinstance(el, ast.Constant) and isinstance(
                                el.value, str
                            ):
                                all_names.append(el.value)
    _emit(
        "V0_helper_in_all",
        HELPER_NAME in all_names,
        f"__all__ contains {len(all_names)} names",
    )
    self_src = Path(__file__).read_text(encoding="utf-8")
    _emit(
        "V0_self_main_entry",
        'if __name__ == "__main__":' in self_src and "def main(" in self_src,
        "expect __main__ guard + def main()",
    )
    # V0_targets_use_new_helper: 092-097 全部 grep 到 helper 调用
    missing = []
    for vid in TARGET_VERIFY_IDS:
        f = SCRIPTS / f"verify_infra_{vid}.py"
        if not f.is_file():
            missing.append(f"{vid}:missing_file")
            continue
        s = f.read_text(encoding="utf-8")
        if HELPER_NAME not in s:
            missing.append(f"{vid}:no_call")
    _emit(
        "V0_targets_use_new_helper",
        len(missing) == 0,
        f"targets={list(TARGET_VERIFY_IDS)} missing={missing}",
    )


# ---------------------------------------------------------------------------
# V1: self main() func sha
# ---------------------------------------------------------------------------
def v1_self_func_sha() -> None:
    self_path = Path(__file__)
    try:
        got = func_sha_by_name(self_path, "main")
    except Exception as e:
        _emit("V1_self_main_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_self_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
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
def v2_verify_lib_file_sha() -> None:
    got = _file_sha(LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V2_lib_file_sha",
            False,
            f"placeholder; bump EXPECTED_VERIFY_LIB_FILE_SHA={got}",
        )
        return
    _emit(
        "V2_lib_file_sha",
        got == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: assert_v5_reviewer_gate_evidence_bind helper func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, HELPER_NAME)
    except Exception as e:
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_V5_HELPER_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V3_helper_func_sha",
            False,
            f"placeholder; bump EXPECTED_V5_HELPER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_V5_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_V5_HELPER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为正反例
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # V4_1: grace_period 命中 → ok=True
    p = _write_tmp_feature_list([
        {"id": "FX-grace", "status": "passing", "evidence": {}},
    ])
    r = assert_v5_reviewer_gate_evidence_bind(
        "FX-grace", p, grace_period_feature_ids=("FX-grace",),
    )
    _emit(
        "V4_1_grace_period_skip",
        r["ok"] is True and r["grace_skipped"] is True,
        f"ok={r['ok']} grace_skipped={r['grace_skipped']}",
    )
    # V4_2: evidence 缺 reviewer → ok=False
    p2 = _write_tmp_feature_list([
        {"id": "FX-no-reviewer", "status": "passing", "evidence": {}},
    ])
    r2 = assert_v5_reviewer_gate_evidence_bind("FX-no-reviewer", p2)
    _emit(
        "V4_2_missing_reviewer_fails",
        r2["ok"] is False and "reviewer" in r2["reason"].lower(),
        f"ok={r2['ok']} reason={r2['reason'][:80]!r}",
    )
    # V4_3: verdict=conditional + 完整 evidence → ok=True
    p3 = _write_tmp_feature_list([
        {
            "id": "FX-conditional",
            "status": "passing",
            "evidence": {
                "closeout_verify": {
                    "reviewer": {
                        "reviewer_kind": "sub_agent_fresh_context",
                        "verdict": "conditional",
                        "summary": "A" * 25,
                    },
                },
            },
        },
    ])
    r3 = assert_v5_reviewer_gate_evidence_bind("FX-conditional", p3)
    _emit(
        "V4_3_conditional_verdict_pass",
        r3["ok"] is True,
        f"ok={r3['ok']} verdict={r3['verdict']!r} reason={r3['reason'][:80]!r}",
    )
    # V4_4: summary 过短 → ok=False mentions summary
    p4 = _write_tmp_feature_list([
        {
            "id": "FX-short-summary",
            "status": "passing",
            "evidence": {
                "closeout_verify": {
                    "reviewer": {
                        "reviewer_kind": "sub_agent_fresh_context",
                        "verdict": "LGTM",
                        "summary": "short",
                    },
                },
            },
        },
    ])
    r4 = assert_v5_reviewer_gate_evidence_bind("FX-short-summary", p4)
    _emit(
        "V4_4_short_summary_fails",
        r4["ok"] is False and "summary" in r4["reason"].lower(),
        f"ok={r4['ok']} summary_len={r4['summary_len']} "
        f"reason={r4['reason'][:80]!r}",
    )
    # V4_5: reviewer_kind 错 → ok=False mentions reviewer_kind
    p5 = _write_tmp_feature_list([
        {
            "id": "FX-wrong-kind",
            "status": "passing",
            "evidence": {
                "closeout_verify": {
                    "reviewer": {
                        "reviewer_kind": "main_context_self",
                        "verdict": "LGTM",
                        "summary": "A" * 25,
                    },
                },
            },
        },
    ])
    r5 = assert_v5_reviewer_gate_evidence_bind("FX-wrong-kind", p5)
    _emit(
        "V4_5_wrong_reviewer_kind_fails",
        r5["ok"] is False and "reviewer_kind" in r5["reason"].lower(),
        f"ok={r5['ok']} kind={r5['reviewer_kind']!r} "
        f"reason={r5['reason'][:80]!r}",
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
            f"[verify_infra_098][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_098][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
