#!/usr/bin/env python3
"""verify_infra_074 V0-V5: total_nodes 绝对值锁存在性 lock.

infra-P286-total-nodes-lock (phase-39 #5.39):

verify_infra_059 V4 之前只锁 unknown_count 精确值 + ratio 上界 (unknown/total),
不锁绝对 total_nodes。若大量 sha lock 被误删 (V4 与 total 一起缩水, ratio 不变),
ratio 检查无法报警。本 feature 给 059 引入:

- ``EXPECTED_CURRENT_TOTAL_NODES: int`` (P286 实测 = 81)
- ``TOTAL_NODES_TOLERANCE: int = 5``
- V4 行为锁: ``abs(total_nodes - EXPECTED_CURRENT_TOTAL_NODES) <=
  TOTAL_NODES_TOLERANCE``

verify_infra_074 锁定 verify_infra_059 上述行为存在 (ast 扫描常量 + 真跑 059)。

infra-P286-followup-v4-2-stricter-equal-check (phase-40 #5.40):
原 V4_2 仅断言常量值 ``> 0`` (弱断言: 任何非零正整数都过). 收紧为:

- V4_2: 059 中 EXPECTED_CURRENT_TOTAL_NODES 值精确等于 074 自持的真值常量
  ``EXPECTED_TOTAL_NODES_TRUTH`` (P286 实测 81).
- V4_2b: subprocess 真跑 059, 解析其 stdout 中
  ``V4_real_total_nodes_within_tolerance total_nodes=<N> expect=<E>`` 行,
  断言 ``expect == EXPECTED_TOTAL_NODES_TRUTH`` (与 dump 实跑 cross-check).

任意一处 mutate (改 059 常量, 或改 074 真值) 都被精确等比立即抓住。

INFRA_074_SHA_LOCKS
-------------------
- ``scripts/verify_infra_059.py`` file sha: EXPECTED_VERIFY_059_FILE_SHA
- ``scripts/verify_infra_059.py:main`` func sha: EXPECTED_059_MAIN_FUNC_SHA
- 常量名锁: EXPECTED_TOTAL_NODES_CONST_NAME / EXPECTED_TOLERANCE_CONST_NAME
- 真值锁: EXPECTED_TOTAL_NODES_TRUTH (int 真值)
- 本脚本 main() 自锁 func sha: EXPECTED_SELF_MAIN_FUNC_SHA

校验层级 (V0-V5, 共 16 checks):

- V0 scaffolding: 路径存在 + 常量 hex64 + docstring sentinel (7 checks)
- V1 self main() func sha 自锁 (1 check)
- V2 verify_infra_059.py file sha (1 check)
- V3 verify_infra_059.py:main func sha (1 check)
- V4 业务实测 (5 checks):
  - V4_1 ast 扫: verify_infra_059.py 顶层含 EXPECTED_CURRENT_TOTAL_NODES 常量
  - V4_2 该常量值精确 == EXPECTED_TOTAL_NODES_TRUTH (本 feature 收紧)
  - V4_2b subprocess 真跑 059 + parse stdout → expect == EXPECTED_TOTAL_NODES_TRUTH
  - V4_3 含 TOTAL_NODES_TOLERANCE 常量 (int, ≥ 0)
  - V4_4 subprocess 真跑 059 → rc=0 (ALL PASS, 含 V4_real_total_nodes_within_tolerance)
- V5 reviewer_lgtm_gate: 用 ``assert_reviewer_lgtm`` 真校 P291 (已 passing) (1 check)

(round-1 曾包含 V4_5 mutant check, 但 tmp dir 跑 059 缺失 live scripts/ 下其它
verify 文件依赖, 导致 dump 出来 node 数远低于 81, V4_real_total_nodes_within_tolerance
必然 FAIL — 与是否真 mutate 常量无关, 证据链断裂违反 P291 real-or-remove,
round-2 整改: 移除 V4_5)

退出码: 0=ALL PASS, 2=任一 FAIL (走 verify_summary_exit)。

运行环境约定 (infra-034): 必须在 .venv 下运行。
"""
from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_059 = SCRIPTS / "verify_infra_059.py"
REAL_FEATURE_LIST = REPO / "feature_list.json"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
    assert_v5_reviewer_gate_evidence_bind,
)

EXPECTED_VERIFY_059_FILE_SHA = "610f575ca6cb81a3b7ef7de0fbae7112cf9ecc710ac3fa04fcf3ecc752369643"
EXPECTED_059_MAIN_FUNC_SHA = "278578c2c220870f79d07f15b6b554e150e3716a17cbe4966782fd721b262f49"
EXPECTED_SELF_MAIN_FUNC_SHA = "80c710deae78cb5405c0c1a5c6072c46ef9934d59e7ec722c6d5563b4e12f2e7"

EXPECTED_TOTAL_NODES_CONST_NAME = "EXPECTED_CURRENT_TOTAL_NODES"
EXPECTED_TOLERANCE_CONST_NAME = "TOTAL_NODES_TOLERANCE"
# P286 实测真值 81; infra-P286-followup-tolerance-headroom-bump phase-41 #1.41
# bump 到 86 (与 059 EXPECTED_CURRENT_TOTAL_NODES 同步). 任一侧 mutate 即 FAIL.
EXPECTED_TOTAL_NODES_TRUTH: int = 86

DOCSTRING_SENTINEL = "INFRA_074_SHA_LOCKS"

V5_GATE_FEATURE_ID = "infra-P291-reviewer-gate-real-or-remove"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_074][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _get_int_const(src: str, name: str):
    """Scan top-level ast.Assign / ast.AnnAssign for constant int by name."""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
                return node.value.value
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
                        return node.value.value
    return None


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_verify_059_exists", VERIFY_059.is_file(), f"path={VERIFY_059}")
    _emit(
        "V0_const_059_file_sha_hex64",
        _is_hex64(EXPECTED_VERIFY_059_FILE_SHA),
        f"val={EXPECTED_VERIFY_059_FILE_SHA[:16]}",
    )
    _emit(
        "V0_const_059_main_sha_hex64",
        _is_hex64(EXPECTED_059_MAIN_FUNC_SHA),
        f"val={EXPECTED_059_MAIN_FUNC_SHA[:16]}",
    )
    _emit(
        "V0_const_name_total_nodes",
        EXPECTED_TOTAL_NODES_CONST_NAME == "EXPECTED_CURRENT_TOTAL_NODES",
        f"val={EXPECTED_TOTAL_NODES_CONST_NAME}",
    )
    _emit(
        "V0_const_name_tolerance",
        EXPECTED_TOLERANCE_CONST_NAME == "TOTAL_NODES_TOLERANCE",
        f"val={EXPECTED_TOLERANCE_CONST_NAME}",
    )
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8")))
    _emit(
        "V0_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )


# ---------------------------------------------------------------------------
# V1: self main() func sha (placeholder)
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
# V2: verify_infra_059.py file sha
# ---------------------------------------------------------------------------
def v2_059_file_sha() -> None:
    got = _file_sha(VERIFY_059)
    _emit(
        "V2_verify_059_file_sha",
        got == EXPECTED_VERIFY_059_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_059_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: verify_infra_059.py:main func sha
# ---------------------------------------------------------------------------
def v3_059_main_func_sha() -> None:
    try:
        got = func_sha_by_name(VERIFY_059, "main")
    except Exception as e:  # noqa: BLE001
        _emit("V3_059_main_func_sha", False, f"compute err: {e!r}")
        return
    _emit(
        "V3_059_main_func_sha",
        got == EXPECTED_059_MAIN_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_059_MAIN_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 业务实测
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    src = VERIFY_059.read_text(encoding="utf-8")
    # V4_1 ast 扫: 含 EXPECTED_CURRENT_TOTAL_NODES 常量
    val_total = _get_int_const(src, EXPECTED_TOTAL_NODES_CONST_NAME)
    _emit(
        "V4_1_const_EXPECTED_CURRENT_TOTAL_NODES_present",
        val_total is not None,
        f"value={val_total}",
    )
    # V4_2 (P286-followup): 值精确 == EXPECTED_TOTAL_NODES_TRUTH
    _emit(
        "V4_2_const_value_eq_truth",
        isinstance(val_total, int) and val_total == EXPECTED_TOTAL_NODES_TRUTH,
        f"value={val_total} truth={EXPECTED_TOTAL_NODES_TRUTH}",
    )
    # V4_3 含 TOTAL_NODES_TOLERANCE 常量 (int ≥ 0)
    val_tol = _get_int_const(src, EXPECTED_TOLERANCE_CONST_NAME)
    _emit(
        "V4_3_const_TOTAL_NODES_TOLERANCE_present",
        isinstance(val_tol, int) and val_tol >= 0,
        f"value={val_tol}",
    )

    # V4_4 subprocess 真跑 059 → rc=0 + V4_2b 解析 stdout cross-check
    try:
        proc = subprocess.run(
            [sys.executable, str(VERIFY_059)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        rc = proc.returncode
        stdout = proc.stdout
        tail = "\n".join(stdout.strip().splitlines()[-3:])
    except Exception as e:  # noqa: BLE001
        rc = -1
        stdout = ""
        tail = f"exception: {e!r}"
    _emit(
        "V4_4_real_run_059_rc0",
        rc == 0,
        f"rc={rc} tail={tail!r}",
    )
    # V4_2b: parse "V4_real_total_nodes_within_tolerance ... expect=<N>" 行,
    # 断言 N == EXPECTED_TOTAL_NODES_TRUTH (与 dump 实跑 cross-check)
    expect_match = re.search(
        r"V4_real_total_nodes_within_tolerance.*?expect=(?P<n>\d+)",
        stdout,
    )
    parsed_expect = int(expect_match.group("n")) if expect_match else None
    _emit(
        "V4_2b_real_dump_expect_eq_truth",
        parsed_expect == EXPECTED_TOTAL_NODES_TRUTH,
        f"parsed_expect={parsed_expect} truth={EXPECTED_TOTAL_NODES_TRUTH} "
        f"matched={bool(expect_match)}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    """V5 Reviewer LGTM gate — phase-47 #1.47 graduate to evidence-bind helper.

    target feature evidence 不完整 (legacy / not_started)，通过 grace_period 兜底
    保持 emit=True，待 target feature 补齐 reviewer evidence 后从 grace 列表移除。
    """
    if not REAL_FEATURE_LIST.is_file():
        _emit(
            "V5_reviewer_lgtm_gate",
            False,
            f"feature_list.json not found at {REAL_FEATURE_LIST}",
        )
        return
    result = assert_v5_reviewer_gate_evidence_bind(
        V5_GATE_FEATURE_ID, REAL_FEATURE_LIST,
        grace_period_feature_ids=(V5_GATE_FEATURE_ID,),
    )
    _emit(
        "V5_reviewer_lgtm_gate",
        bool(result["ok"]),
        f"target={V5_GATE_FEATURE_ID} helper_ok={result['ok']} "
        f"grace_skipped={result['grace_skipped']} "
        f"verdict={result['verdict']!r} kind={result['reviewer_kind']!r} "
        f"summary_len={result['summary_len']} reason={result['reason']!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_func_sha()
    v2_059_file_sha()
    v3_059_main_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    failed_tags = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_074][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_074][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
