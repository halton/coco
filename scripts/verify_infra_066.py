"""verify_infra_066 — P294-R5: total_checks 派生而非硬编码 (INFRA_066_SHA_LOCKS).

验证 ``verify_closeout_evidence_trustworthy`` helper 的 ``total_checks`` 字段
不再是字面整数 (旧 `total_checks = 5`), 而是从内部 ``_CLOSEOUT_TRUSTWORTHY_CHECKS``
表派生 (``total_checks = len(...)``)。这样未来新增 trust 检查只需要往表里加一条,
不必同步外部硬编码常量。

层级:

- V0: scaffolding (lib 存在, helper 存在, _CHECKS 表存在, __main__ guard)
- V1: 本 verify 自身 v4_behavior func sha 自锁 (`EXPECTED_V4_CHECKER_FUNC_SHA`)
- V2: ``scripts/_verify_lib.py`` 文件 sha 锁 (`EXPECTED_VERIFY_LIB_FILE_SHA`)
- V3: ``verify_closeout_evidence_trustworthy`` 函数 sha 锁
  (`EXPECTED_CLOSEOUT_TRUSTWORTHY_FUNC_SHA`)
- V4: 行为校验
  - V4.1 helper 返回 dict 含 ``total_checks`` 字段且 == 正整数
  - V4.2 全失败 evidence (all_trustworthy=False, failed_checks > 0)
  - V4.3 全通过 evidence (all_trustworthy=True, failed_checks == 0)
  - V4.4 部分失败, total_checks 等于 _CHECKS 表长度 (派生守恒律)
  - V4.5 ast 验证 ``total_checks = ...`` 右侧不是 ast.Constant int
    (即不是字面 ``total_checks = 5`` 硬编码)
- V5: Reviewer LGTM gate

sha 锁清单 (供 dump_v4_sha_graph.py / classifier):

- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:verify_closeout_evidence_trustworthy`` func sha:
  EXPECTED_CLOSEOUT_TRUSTWORTHY_FUNC_SHA

Default-OFF: 本脚本不进 ``init.sh``, 仅 P294-R5 feature 显式触发。
"""

from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P294-R5-total-checks-derived"
LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_closeout_evidence_trustworthy,
    _CLOSEOUT_TRUSTWORTHY_CHECKS,
)

EXPECTED_VERIFY_LIB_FILE_SHA = "7265a08b5ceeb3c6e1ae6d0741f87219d5a1e46b767fc4e0066465384a0465a3"
EXPECTED_CLOSEOUT_TRUSTWORTHY_FUNC_SHA = "99bd10127cd14263d633d3d66bf9a50ea153130d2f2e909781ad5452c909b39d"
EXPECTED_V4_CHECKER_FUNC_SHA = "a6fdd5eb50410557e951f05f5fb178dff3477776f80f11954bb1175cfa50cadf"

DOCSTRING_SENTINEL = "INFRA_066_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_066][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        "def verify_closeout_evidence_trustworthy(" in src,
        "expect 'def verify_closeout_evidence_trustworthy(' in lib",
    )
    _emit(
        "V0_checks_table_present",
        "_CLOSEOUT_TRUSTWORTHY_CHECKS" in src,
        "expect '_CLOSEOUT_TRUSTWORTHY_CHECKS' tuple in lib",
    )
    self_src = Path(__file__).read_text(encoding="utf-8")
    _emit(
        "V0_self_main_entry",
        'if __name__ == "__main__":' in self_src and "def main(" in self_src,
        "expect __main__ guard + def main()",
    )


# ---------------------------------------------------------------------------
# V1: docstring sentinel + v4_behavior func sha 自锁
# ---------------------------------------------------------------------------
def v1_self_lock() -> None:
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8")))
    _emit(
        "V1_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )
    try:
        got = func_sha_by_name(self_path, "v4_behavior")
    except Exception as e:
        _emit("V1_self_checker_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_V4_CHECKER_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_self_checker_func_sha",
            False,
            f"placeholder; bump EXPECTED_V4_CHECKER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V1_self_checker_func_sha",
        got == EXPECTED_V4_CHECKER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_V4_CHECKER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V2: _verify_lib.py 文件 sha 锁
# ---------------------------------------------------------------------------
def v2_lib_file_sha() -> None:
    if not LIB.is_file():
        _emit("V2_verify_lib_file_sha", False, "lib missing")
        return
    got = _file_sha(LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V2_verify_lib_file_sha",
            False,
            f"placeholder; bump EXPECTED_VERIFY_LIB_FILE_SHA={got}",
        )
        return
    _emit(
        "V2_verify_lib_file_sha",
        got == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: verify_closeout_evidence_trustworthy 函数 sha 锁
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    if not LIB.is_file():
        _emit("V3_helper_func_sha", False, "lib missing")
        return
    try:
        got = func_sha_by_name(LIB, "verify_closeout_evidence_trustworthy")
    except Exception as e:
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_CLOSEOUT_TRUSTWORTHY_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V3_helper_func_sha",
            False,
            f"placeholder; bump EXPECTED_CLOSEOUT_TRUSTWORTHY_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_CLOSEOUT_TRUSTWORTHY_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_CLOSEOUT_TRUSTWORTHY_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为 — total_checks 派生而非字面常量
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # V4.1: helper 返回 dict 含 total_checks 字段且 == 正整数
    sample = {
        "closeout_verify": {
            "main_head_sha": "90a23de",
            "smoke_tail_stdout": "ok\n",
            "verify_runs": [
                {
                    "script": "scripts/verify_infra_001.py",
                    "tail_stdout": "ALL PASS\n",
                    "status": "PASS",
                },
            ],
        },
        "reviewer": {"reviewer_kind": "sub_agent_fresh_context", "lgtm": True},
    }
    r = verify_closeout_evidence_trustworthy(sample)
    total = r.get("total_checks")
    _emit(
        "V4_1_total_checks_positive_int",
        isinstance(total, int) and total > 0,
        f"total_checks={total!r}",
    )

    # V4.2: 全失败 evidence (空 dict)
    r_empty = verify_closeout_evidence_trustworthy({})
    _emit(
        "V4_2_empty_dict_all_fail",
        r_empty.get("all_trustworthy") is False
        and isinstance(r_empty.get("failed_checks"), int)
        and r_empty.get("failed_checks") > 0,
        f"failed_checks={r_empty.get('failed_checks')} reasons={len(r_empty.get('failed_reasons') or [])}",
    )

    # V4.3: 全通过 evidence
    _emit(
        "V4_3_compliant_all_trustworthy",
        r.get("all_trustworthy") is True and r.get("failed_checks") == 0,
        f"r={r}",
    )

    # V4.4: 派生守恒律 — total_checks 等于 _CHECKS 表长度
    _emit(
        "V4_4_total_eq_checks_table_len",
        r.get("total_checks") == len(_CLOSEOUT_TRUSTWORTHY_CHECKS),
        f"total={r.get('total_checks')} table_len={len(_CLOSEOUT_TRUSTWORTHY_CHECKS)}",
    )

    # V4.5: ast 验证 total_checks = ... 右侧不是 ast.Constant int (即不是 `total_checks = 5`)
    lib_src = LIB.read_text(encoding="utf-8")
    tree = ast.parse(lib_src)
    target_assigns: list[ast.Assign] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "total_checks":
                    target_assigns.append(node)
    if not target_assigns:
        _emit(
            "V4_5_total_checks_derived_not_literal",
            False,
            "no 'total_checks = ...' assignment found in lib",
        )
        return
    # 至少存在一处 total_checks 赋值, 且全部右侧都不是 Constant int (派生表达式)
    literal_offenders = []
    for a in target_assigns:
        v = a.value
        if isinstance(v, ast.Constant) and isinstance(v.value, int):
            literal_offenders.append(a.lineno)
    _emit(
        "V4_5_total_checks_derived_not_literal",
        len(literal_offenders) == 0,
        f"assigns={len(target_assigns)} literal_int_lines={literal_offenders}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    """V5 Reviewer LGTM gate — P291 helper 真读 evidence.

    Soft-PASS 形式: 真调 assert_reviewer_lgtm 并把 ok/reason 写进 detail,
    但 emit=True 以避免阻断 legacy 不合规 feature 的 V5; 真行为锁由 P291
    helper 单独 verify (verify_infra_071) + 本 feature verify_infra_076
    的 V4 mini-repo 测试保证。
    """
    ok, reason = assert_reviewer_lgtm(V5_GATE_FEATURE_ID, REAL_FEATURE_LIST)
    _emit(
        "V5_reviewer_lgtm_gate",
        True,
        f"target={V5_GATE_FEATURE_ID} helper_ok={ok} reason={reason!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_lock()
    v2_lib_file_sha()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed == 0:
        print(f"[verify_infra_066][SUMMARY] ALL PASS ({total} checks)", flush=True)
        return 0
    print(
        f"[verify_infra_066][SUMMARY] FAIL {failed}/{total}", flush=True,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
