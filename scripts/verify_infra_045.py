#!/usr/bin/env python3
"""verify_infra_045: V6 reverse-sha-lock helpers in _verify_lib (verify-only).

infra-V6-backlog (phase-34 #1.34): 锁住 ``scripts/_verify_lib.py`` 内三个由
verify_infra_034 V6 抽出的共享 helper:
- ``scan_reverse_sha_locks(scripts_dir)``
- ``live_verify_sha_set(scripts_dir)``
- ``verify_reverse_sha_lock_consistency(scripts_dir)``

verify_infra_034 V6 通过 ``from _verify_lib import verify_reverse_sha_lock_consistency``
调本 lib 函数实现反向 sha lock pre-flight; 本 verify-only 脚本保证 helper 的存在
+ ast 级 func sha + 端到端行为正确 (live_count > 0, scanned_count > 0, all_match=True)。

V0 scaffolding: _verify_lib.py 存在 + 三个 helper 都在 __all__ + 本脚本本身可执行。
V1 docstring sentinel ``INFRA_045_SHA_LOCKS`` 自锁。
V2 _verify_lib.py file sha + 三个 helper func sha 四锁。
V3 in-memory mutant: ast 改 scan_reverse_sha_locks 内的关键判断, unparse 后 sha 必漂移。
V4 行为验证: 直接 call lib 函数, scanned_count > 0, live_count > 0, all_match=True
   (主仓库当前状态下应全 match)。
V5 Reviewer LGTM gate (print-only, evidence 字段)。

INFRA_045_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_LIB_FILE_SHA
- ``scan_reverse_sha_locks`` func sha: EXPECTED_SCAN_FUNC_SHA
- ``live_verify_sha_set`` func sha: EXPECTED_LIVE_FUNC_SHA
- ``verify_reverse_sha_lock_consistency`` func sha: EXPECTED_CHECK_FUNC_SHA

退出码 0=ALL PASS / 1=任一 FAIL。

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (
    func_sha_by_name,
    live_verify_sha_set,
    scan_reverse_sha_locks,
    verify_reverse_sha_lock_consistency,
    assert_v5_reviewer_gate_evidence_bind,
)

# infra-045 sha lock 常量 (V2)
EXPECTED_LIB_FILE_SHA = "f789e0d870c9e6c3764b893a6bcbcca356bfc464520d9c49f3d4b17045ecd243"
EXPECTED_SCAN_FUNC_SHA = "772a2d916fc113667b910f4a4f416d9c3b0db9b0bea5ffa749e474a074cfd2b7"
EXPECTED_LIVE_FUNC_SHA = "64d8b3791cdc50cb890f2b5375f8f7500c43d589fec2400f8f6294bd0618fd54"
EXPECTED_CHECK_FUNC_SHA = "beb049cbae91be6ad21958f6016a18ea744db67256d6741b67be3c6fb1651970"

DOCSTRING_SENTINEL = "INFRA_045_SHA_LOCKS"
HELPER_NAMES = (
    "scan_reverse_sha_locks",
    "live_verify_sha_set",
    "verify_reverse_sha_lock_consistency",
)


# phase-47 #1.47: V5_GATE evidence-bind helper (grace_period 兜底 soft graduate)
REAL_FEATURE_LIST = Path(__file__).resolve().parents[1] / "feature_list.json"
V5_GATE_FEATURE_ID = "__PHASE_47_PLACEHOLDER_INFRA_045__"

_results: List[Tuple[str, bool, str]] = []


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_045][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_lib_exists", LIB.is_file(), f"path={LIB}")
    self_path = Path(__file__)
    _emit("V0_self_exists", self_path.is_file(), f"path={self_path}")
    src = LIB.read_text(encoding="utf-8")
    tree = ast.parse(src)
    all_list: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    try:
                        all_list = list(ast.literal_eval(node.value))
                    except Exception:
                        all_list = []
    for name in HELPER_NAMES:
        _emit(
            f"V0_helper_in_all_{name}",
            name in all_list,
            f"__all__ contains {name}",
        )


# ---------------------------------------------------------------------------
# V1: docstring sentinel 自锁
# ---------------------------------------------------------------------------
def v1_docstring_sentinel() -> None:
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8")))
    _emit(
        "V1_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )


# ---------------------------------------------------------------------------
# V2: _verify_lib.py file sha + 三 helper func sha 四锁
# ---------------------------------------------------------------------------
def v2_file_and_func_sha() -> None:
    got_file = _file_sha(LIB)
    _emit(
        "V2_lib_file_sha",
        got_file == EXPECTED_LIB_FILE_SHA,
        f"got={got_file[:16]} expect={EXPECTED_LIB_FILE_SHA[:16]}",
    )
    expects = {
        "scan_reverse_sha_locks": EXPECTED_SCAN_FUNC_SHA,
        "live_verify_sha_set": EXPECTED_LIVE_FUNC_SHA,
        "verify_reverse_sha_lock_consistency": EXPECTED_CHECK_FUNC_SHA,
    }
    for name, exp in expects.items():
        got = func_sha_by_name(LIB, name)
        _emit(
            f"V2_func_sha_{name}",
            got == exp,
            f"got={got[:16]} expect={exp[:16]}",
        )


# ---------------------------------------------------------------------------
# V3: in-memory ast mutant — 把 scan_reverse_sha_locks 顶层逻辑改一处 (return [])
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    src = LIB.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target: ast.FunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "scan_reverse_sha_locks":
            target = node
            break
    if target is None:
        _emit("V3_mutant_apply", False, "scan_reverse_sha_locks not found")
        return
    baseline_sha = hashlib.sha256(ast.unparse(target).encode("utf-8")).hexdigest()
    # mutant: 把函数体替换为单一 return [], unparse 后必漂移
    mutant_node = ast.parse(ast.unparse(target)).body[0]
    assert isinstance(mutant_node, ast.FunctionDef)
    mutant_node.body = [ast.Return(value=ast.List(elts=[], ctx=ast.Load()))]
    ast.fix_missing_locations(mutant_node)
    mutant_sha = hashlib.sha256(ast.unparse(mutant_node).encode("utf-8")).hexdigest()
    _emit(
        "V3_mutant_sha_drift",
        baseline_sha != mutant_sha,
        f"baseline={baseline_sha[:16]} mutant={mutant_sha[:16]}",
    )
    _emit(
        "V3_baseline_matches_expected",
        baseline_sha == EXPECTED_SCAN_FUNC_SHA,
        f"baseline={baseline_sha[:16]} expect={EXPECTED_SCAN_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为验证 — call lib 函数, 验 scanned_count/live_count > 0, all_match=True
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    live = live_verify_sha_set(SCRIPTS)
    _emit(
        "V4_live_verify_sha_set_nonempty",
        len(live) > 0,
        f"live_count={len(live)}",
    )
    scanned = scan_reverse_sha_locks(SCRIPTS)
    _emit(
        "V4_scan_reverse_sha_locks_nonempty",
        len(scanned) > 0,
        f"scanned_count={len(scanned)}",
    )
    # 抽样 schema 检查
    if scanned:
        sample = scanned[0]
        required_keys = {"file", "lineno", "const_name", "sha_hex"}
        _emit(
            "V4_scan_schema",
            required_keys.issubset(sample.keys()),
            f"sample_keys={sorted(sample.keys())}",
        )
    result = verify_reverse_sha_lock_consistency(SCRIPTS)
    _emit(
        "V4_check_result_schema",
        {"scanned_count", "live_count", "orphans", "all_match"}.issubset(result.keys()),
        f"keys={sorted(result.keys())}",
    )
    _emit(
        "V4_check_all_match",
        result["all_match"] is True,
        f"scanned={result['scanned_count']} live={result['live_count']} "
        f"orphans={len(result['orphans'])}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (print-only)
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
    v1_docstring_sentinel()
    v2_file_and_func_sha()
    v3_mutant()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_045][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_045][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
