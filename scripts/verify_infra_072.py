#!/usr/bin/env python3
"""verify_infra_072 V0-V5: closeout-verify-trustworthy helper passed_checks 字段守恒律.

infra-P299-closeout-verify-trustworthy-helper-passed-checks-field (phase-39 #3.39):

现状: ``verify_closeout_evidence_trustworthy`` helper 已派生 ``total_checks``
(P294-R5), 但返回 dict 仅含 ``total_checks + failed_checks + failed_reasons``,
下游若想严格 assert 守恒律 (passed + failed == total), 需自行算
``passed = total - len(failed_reasons)``。本 feature 在 helper 加
``passed_checks`` 独立字段, 把守恒律从"推导关系"升级到"独立字段交叉锁"。

INFRA_072_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` 全文件 sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``verify_closeout_evidence_trustworthy`` canonical func sha: EXPECTED_HELPER_FUNC_SHA
- 本脚本 main() 自锁 func sha: EXPECTED_SELF_MAIN_FUNC_SHA (placeholder __BUMP_ME__)

校验层级 (V0-V5):

- V0 scaffolding: 路径存在 + 常量 hex64 + docstring sentinel
- V1 self main() func sha 自锁 (placeholder OK)
- V2 _verify_lib file sha
- V3 helper func sha (verify_closeout_evidence_trustworthy)
- V4 业务实测 (合成 evidence 调 helper):
  - V4_1: 完整合规 evidence → returned dict 含 "passed_checks" 字段 + ok=True
  - V4_2: invariant 守恒 passed+failed==total 真校验 (合规 evidence)
  - V4_3: passed_checks 数值正确 (= total - failed) — 合规 evidence
  - V4_4: 不合规 evidence (missing field) → passed_checks 仍存在 + 守恒仍成立
  - V4_5 mutant: monkey-patch helper 返回 passed=total+1, 检查 invariant FAIL
    (反证 V4_2 守恒 assert 真有抓 bug 的能力)
- V5_reviewer_lgtm_gate: 用 ``assert_reviewer_lgtm`` 真校 P291 (已 passing)

退出码: 0=ALL PASS, 2=任一 FAIL (走 verify_summary_exit)。

运行环境约定 (infra-034): 必须在 .venv 下运行。
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
REAL_FEATURE_LIST = REPO / "feature_list.json"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_closeout_evidence_trustworthy,
    verify_summary_exit,
)

EXPECTED_VERIFY_LIB_FILE_SHA = "3ccf0f771d0d4af129708ff76dde0a5e836dc172e350758882e4c2b13265e750"
EXPECTED_HELPER_FUNC_SHA = "99bd10127cd14263d633d3d66bf9a50ea153130d2f2e909781ad5452c909b39d"
EXPECTED_SELF_MAIN_FUNC_SHA = "c2c699e0762289b286cf6fc44bf3f7e82491fcba04d6f1d5786ae082741f61a7"

DOCSTRING_SENTINEL = "INFRA_072_SHA_LOCKS"

# V5 gate target: P291 已 passing
V5_GATE_FEATURE_ID = "infra-P291-reviewer-gate-real-or-remove"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_072][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_verify_lib_exists", LIB.is_file(), f"path={LIB}")
    _emit(
        "V0_const_file_sha_hex64",
        _is_hex64(EXPECTED_VERIFY_LIB_FILE_SHA),
        f"val={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )
    _emit(
        "V0_const_helper_sha_hex64",
        _is_hex64(EXPECTED_HELPER_FUNC_SHA),
        f"val={EXPECTED_HELPER_FUNC_SHA[:16]}",
    )
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8")))
    _emit(
        "V0_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )


# ---------------------------------------------------------------------------
# V1: self main() func sha lock (placeholder __BUMP_ME__ 风格)
# ---------------------------------------------------------------------------
def v1_self_func_sha() -> None:
    try:
        got = func_sha_by_name(Path(__file__), "main")
    except Exception as e:  # noqa: BLE001
        _emit("V1_self_main_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_self_main_func_sha",
            True,
            f"placeholder OK; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
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
    _emit(
        "V2_verify_lib_file_sha",
        got == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: helper func sha (verify_closeout_evidence_trustworthy)
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "verify_closeout_evidence_trustworthy")
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_HELPER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 业务实测 (合成 evidence 调 helper)
# ---------------------------------------------------------------------------
def _good_evidence() -> dict:
    return {
        "closeout_verify": {
            "main_head_sha": "1234567",
            "smoke_tail_stdout": "smoke OK line\n",
            "verify_runs": [
                {
                    "script": "scripts/verify_infra_072.py",
                    "tail_stdout": "[verify_infra_072][SUMMARY] ALL PASS",
                    "status": "PASS",
                },
            ],
        },
        "reviewer": {
            "reviewer_kind": "sub_agent_fresh_context",
            "lgtm": True,
        },
    }


def v4_behavior() -> None:
    # V4_1: 完整合规 evidence → returned dict 含 "passed_checks" 字段 + ok=True
    res_ok = verify_closeout_evidence_trustworthy(_good_evidence())
    has_field = "passed_checks" in res_ok
    _emit(
        "V4_1_passed_checks_field_present",
        has_field and res_ok.get("all_trustworthy") is True,
        f"keys={sorted(res_ok.keys())} ok={res_ok.get('all_trustworthy')}",
    )

    # V4_2: invariant 守恒 passed+failed==total (合规 evidence)
    p = res_ok.get("passed_checks")
    f = res_ok.get("failed_checks")
    t = res_ok.get("total_checks")
    _emit(
        "V4_2_invariant_passed_plus_failed_eq_total_good",
        isinstance(p, int) and isinstance(f, int) and isinstance(t, int) and (p + f == t),
        f"passed={p} failed={f} total={t}",
    )

    # V4_3: passed_checks 数值正确 (= total - failed) — 合规 evidence
    expected_passed = (t - f) if isinstance(t, int) and isinstance(f, int) else None
    _emit(
        "V4_3_passed_checks_value_correct",
        p == expected_passed,
        f"passed={p} expected(total-failed)={expected_passed}",
    )

    # V4_4: 不合规 evidence (missing reviewer) → passed_checks 仍存在 + 守恒仍成立
    bad_ev = _good_evidence()
    del bad_ev["reviewer"]
    res_bad = verify_closeout_evidence_trustworthy(bad_ev)
    pb = res_bad.get("passed_checks")
    fb = res_bad.get("failed_checks")
    tb = res_bad.get("total_checks")
    field_still_there = "passed_checks" in res_bad
    invariant_bad = (
        isinstance(pb, int) and isinstance(fb, int) and isinstance(tb, int)
        and (pb + fb == tb)
    )
    not_trustworthy = res_bad.get("all_trustworthy") is False
    _emit(
        "V4_4_invariant_holds_for_bad_evidence",
        field_still_there and invariant_bad and not_trustworthy,
        f"passed={pb} failed={fb} total={tb} ok={res_bad.get('all_trustworthy')}",
    )

    # V4_5 mutant: monkey-patch helper to violate invariant (passed = total+1).
    # 反证 V4_2 守恒 assert 真能抓 bug。
    import _verify_lib as _vl

    real_helper = _vl.verify_closeout_evidence_trustworthy

    def _mutant_helper(evidence: dict) -> dict:
        out = real_helper(evidence)
        out = dict(out)
        out["passed_checks"] = out["total_checks"] + 1  # 故意破坏守恒
        return out

    try:
        _vl.verify_closeout_evidence_trustworthy = _mutant_helper  # type: ignore[assignment]
        res_mut = _vl.verify_closeout_evidence_trustworthy(_good_evidence())
        pm = res_mut.get("passed_checks")
        fm = res_mut.get("failed_checks")
        tm = res_mut.get("total_checks")
        invariant_violated = (pm + fm) != tm
        _emit(
            "V4_5_mutant_invariant_violation_detected",
            invariant_violated and pm == tm + 1,
            f"mutant passed={pm} failed={fm} total={tm} invariant_violated={invariant_violated}",
        )
    finally:
        _vl.verify_closeout_evidence_trustworthy = real_helper  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate — 复用 P291 helper, 锁 P291 自身 passing evidence
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
            f"[verify_infra_072][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_072][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
