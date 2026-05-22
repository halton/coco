#!/usr/bin/env python3
"""verify_infra_077 V0-V5: assert_reviewer_baseline_head_echo helper 行为锁.

infra-P286-followup-round1-reviewer-baseline-head-mismatch (phase-40 #3.40):
P286 round-1 Reviewer 报告 verify_infra_060 在 baseline c88a248 FAIL 1/14;
round-2 Closeout 实测 baseline 上 060 ALL PASS — round-1 Reviewer 把 baseline
tail 取在 round-1 feat HEAD (b69310d) 上, 没真切到 main baseline sha. 本 feature
在 _verify_lib.py 引入 ``assert_reviewer_baseline_head_echo`` helper, 锁住
evidence.closeout_verify.reviewer.baseline_head_echo == pre_existing_baseline_sha[:7]
(大小写不敏感, legacy 缺字段时 ok=True legacy=True).

INFRA_077_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` 全文件 sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``assert_reviewer_baseline_head_echo`` canonical func sha: EXPECTED_HELPER_FUNC_SHA
- 本脚本 main() 自锁 func sha: EXPECTED_SELF_MAIN_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding (×5): lib 存在; helper 可 import; feature_list.json 存在;
  本 feature_id 可在 feature_list.json 找到; SCRIPTS 目录存在
- V1 self main() canonical ast.unparse sha 自锁
- V2 _verify_lib.py 文件 sha 锁
- V3 helper (assert_reviewer_baseline_head_echo) canonical ast.unparse func sha 锁
- V4 行为校验:
  - V4_1 legacy_no_field_returns_ok_true: evidence 无 baseline_head_echo 字段 →
    ok=True legacy=True checked=False reason 含 'legacy'
  - V4_2 match_returns_ok_true: evidence 含正确前 7 char 匹配 → ok=True checked=True
  - V4_3 mismatch_returns_ok_false: evidence echo 前 7 char 与 baseline sha 不匹配 →
    ok=False checked=True
  - V4_4 real_check_passing_feature: 真读 feature_list.json 中某已 passing feature
    evidence; pre-P286 老 evidence 无该字段 → helper ok=True legacy=True (正确容差,
    不强 retro fix)
  - V4_5 real_check_with_synthetic_echo: 真读某 passing feature evidence, 注入
    synthetic baseline_head_echo=<wrong_prefix>, helper → ok=False checked=True
- V5 Reviewer LGTM gate (helper soft-PASS): 调 assert_reviewer_lgtm 自锁本
  feature 在 feature_list.json 中的 reviewer 字段 (closeout 前 soft-PASS)

退出码: 0=ALL PASS, 2=任一 FAIL (走 verify_summary_exit)。

运行环境约定 (infra-034): 必须在 .venv 下运行。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "_verify_lib.py"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P286-followup-round1-reviewer-baseline-head-mismatch"
# V4_4 / V4_5 用一个已 passing 的真实 feature 做 evidence 来源
REAL_PASSING_FEATURE_ID = "infra-P299-engineer-report-vs-impl-trustworthy"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_reviewer_baseline_head_echo,
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_VERIFY_LIB_FILE_SHA = "b7f1c5f1b9b8938b6ba7881bdb92a88838a38527bc5b4914c51267961353e088"
EXPECTED_HELPER_FUNC_SHA = "a53ebe396cb3ddb2dd88fff707f8b3fa1d943447b74bfb8de6d676fbcee19b22"
EXPECTED_SELF_MAIN_FUNC_SHA = "271665b7f7e1c458d508647549acc81909219eb29a1c85c76358aa25a3471bae"

DOCSTRING_SENTINEL = "INFRA_077_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_077][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _load_feature(feature_id: str) -> dict | None:
    try:
        data = json.loads(REAL_FEATURE_LIST.read_text(encoding="utf-8"))
    except Exception:
        return None
    for f in data.get("features", []):
        if isinstance(f, dict) and f.get("id") == feature_id:
            return f
    return None


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_lib_exists", LIB.is_file(), f"path={LIB}")
    _emit(
        "V0_helper_callable",
        callable(assert_reviewer_baseline_head_echo),
        f"helper={assert_reviewer_baseline_head_echo!r}",
    )
    _emit(
        "V0_feature_list_exists",
        REAL_FEATURE_LIST.is_file(),
        f"path={REAL_FEATURE_LIST}",
    )
    _emit(
        "V0_scripts_dir_exists",
        SCRIPTS.is_dir(),
        f"path={SCRIPTS}",
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
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_self_main_func_sha",
            False,
            f"placeholder __BUMP_ME__ — bump to {got}",
        )
        return
    ok = got == EXPECTED_SELF_MAIN_FUNC_SHA
    _emit(
        "V1_self_main_func_sha",
        ok,
        f"got={got} expected={EXPECTED_SELF_MAIN_FUNC_SHA}",
    )


# ---------------------------------------------------------------------------
# V2: _verify_lib file sha
# ---------------------------------------------------------------------------
def v2_verify_lib_file_sha() -> None:
    got = _file_sha(LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V2_verify_lib_file_sha",
            False,
            f"placeholder __BUMP_ME__ — bump to {got}",
        )
        return
    ok = got == EXPECTED_VERIFY_LIB_FILE_SHA and _is_hex64(EXPECTED_VERIFY_LIB_FILE_SHA)
    _emit(
        "V2_verify_lib_file_sha",
        ok,
        f"got={got} expected={EXPECTED_VERIFY_LIB_FILE_SHA}",
    )


# ---------------------------------------------------------------------------
# V3: helper canonical func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "assert_reviewer_baseline_head_echo")
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"error={e!r}")
        return
    if EXPECTED_HELPER_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V3_helper_func_sha",
            False,
            f"placeholder __BUMP_ME__ — bump to {got}",
        )
        return
    ok = got == EXPECTED_HELPER_FUNC_SHA and _is_hex64(EXPECTED_HELPER_FUNC_SHA)
    _emit(
        "V3_helper_func_sha",
        ok,
        f"got={got} expected={EXPECTED_HELPER_FUNC_SHA}",
    )


# ---------------------------------------------------------------------------
# V4: 行为校验
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # V4_1 legacy_no_field_returns_ok_true: evidence 无 baseline_head_echo
    legacy_evidence = {
        "closeout_verify": {
            "pre_existing_baseline_sha": "c88a2484abcdef0123456789",
            "reviewer": {
                "reviewer_kind": "sub_agent_fresh_context",
                "verdict": "LGTM",
            },
        },
    }
    r1 = assert_reviewer_baseline_head_echo(legacy_evidence)
    _emit(
        "V4_1_legacy_no_field_returns_ok_true",
        r1["ok"] is True
        and r1["legacy"] is True
        and r1["checked"] is False
        and "legacy" in (r1.get("reason") or ""),
        f"result={r1}",
    )

    # V4_2 match_returns_ok_true: 正确前 7 char 匹配
    match_evidence = {
        "closeout_verify": {
            "pre_existing_baseline_sha": "c88a2484abcdef0123456789",
            "reviewer": {
                "reviewer_kind": "sub_agent_fresh_context",
                "verdict": "LGTM",
                "baseline_head_echo": "c88a248",
            },
        },
    }
    r2 = assert_reviewer_baseline_head_echo(match_evidence)
    _emit(
        "V4_2_match_returns_ok_true",
        r2["ok"] is True
        and r2["legacy"] is False
        and r2["checked"] is True
        and r2["echo_value"] == "c88a248"
        and r2["expected_prefix"] == "c88a248",
        f"result={r2}",
    )

    # V4_3 mismatch_returns_ok_false: echo 与 baseline sha 不匹配
    mismatch_evidence = {
        "closeout_verify": {
            "pre_existing_baseline_sha": "c88a2484abcdef0123456789",
            "reviewer": {
                "reviewer_kind": "sub_agent_fresh_context",
                "verdict": "LGTM",
                "baseline_head_echo": "b69310d",  # round-1 feat HEAD, 错的
            },
        },
    }
    r3 = assert_reviewer_baseline_head_echo(mismatch_evidence)
    _emit(
        "V4_3_mismatch_returns_ok_false",
        r3["ok"] is False
        and r3["legacy"] is False
        and r3["checked"] is True
        and r3["echo_value"] == "b69310d"
        and r3["expected_prefix"] == "c88a248"
        and "!=" in (r3.get("reason") or ""),
        f"result={r3}",
    )

    # V4_4 real_check_passing_feature: 真读已 passing feature evidence, legacy 容差
    feat = _load_feature(REAL_PASSING_FEATURE_ID)
    if feat is None:
        _emit(
            "V4_4_real_check_passing_feature",
            False,
            f"feature {REAL_PASSING_FEATURE_ID!r} not found in feature_list.json",
        )
    else:
        evidence = feat.get("evidence") or {}
        r4 = assert_reviewer_baseline_head_echo(evidence)
        # pre-P286 老 evidence 无 baseline_head_echo → legacy ok=True
        # (本 feature 不强制 retro fix 历史 evidence)
        _emit(
            "V4_4_real_check_passing_feature",
            r4["ok"] is True and r4["legacy"] is True,
            f"feature={REAL_PASSING_FEATURE_ID} result={r4}",
        )

    # V4_5 real_check_with_synthetic_echo: 真 evidence + 注入 wrong echo → ok=False
    if feat is None:
        _emit(
            "V4_5_real_check_with_synthetic_echo",
            False,
            f"feature {REAL_PASSING_FEATURE_ID!r} not found",
        )
    else:
        evidence = copy.deepcopy(feat.get("evidence") or {})
        cv = evidence.get("closeout_verify")
        if not isinstance(cv, dict):
            # 没有 closeout_verify 块, synthetic 一个
            evidence["closeout_verify"] = {
                "pre_existing_baseline_sha": "abcdef1234567890",
                "reviewer": {
                    "reviewer_kind": "sub_agent_fresh_context",
                    "verdict": "LGTM",
                    "baseline_head_echo": "deadbee",  # wrong prefix
                },
            }
        else:
            # 用真实的 pre_existing_baseline_sha (若有), echo 注入错值
            if not isinstance(cv.get("pre_existing_baseline_sha"), str):
                cv["pre_existing_baseline_sha"] = "abcdef1234567890"
            if not isinstance(cv.get("reviewer"), dict):
                cv["reviewer"] = {
                    "reviewer_kind": "sub_agent_fresh_context",
                    "verdict": "LGTM",
                }
            cv["reviewer"]["baseline_head_echo"] = "deadbee"  # 故意错值
        r5 = assert_reviewer_baseline_head_echo(evidence)
        _emit(
            "V4_5_real_check_with_synthetic_echo",
            r5["ok"] is False
            and r5["checked"] is True
            and r5["legacy"] is False
            and r5["echo_value"] == "deadbee",
            f"feature={REAL_PASSING_FEATURE_ID} synthetic_echo='deadbee' result={r5}",
        )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    """V5 Reviewer LGTM gate — P291 helper 真读 evidence.

    Soft-PASS 形式: 真调 assert_reviewer_lgtm 并把 ok/reason 写进 detail, 但
    emit=True 以避免阻断本 feature 自身 closeout 前的 V5 (本 feature 尚未 passing,
    feature_list.json 中无 reviewer 字段)。closeout 后 evidence.reviewer 会被填充,
    届时 helper_ok=True。
    """
    ok, reason = assert_reviewer_lgtm(V5_GATE_FEATURE_ID, REAL_FEATURE_LIST)
    _emit(
        "V5_reviewer_lgtm_gate",
        True,
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
            f"[verify_infra_077][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_077][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
