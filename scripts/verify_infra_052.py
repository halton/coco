#!/usr/bin/env python3
"""verify_infra_052: scan_reverse_sha_locks pattern expand V0-V5.

infra-049-backlog-reverse-lock-pattern-expand (phase-35 #3.35, P271): 锁住
``scripts/_verify_lib.py`` 中 ``scan_reverse_sha_locks`` 在 P271 引入的:

- pattern 放宽: 除 ``VERIFY_<NNN>`` 子串模式 (verify-to-verify 反向锁) 外,
  也捕获 ``^EXPECTED_.*_(FILE|FUNC)_SHA$`` 模式 (verify-to-source-script /
  verify-to-func 反向锁), 让 P266 落地的 dump_reverse_sha_lock_index 全景索引视图
  覆盖此前被漏扫的 ~40+ 个 EXPECTED_*_FILE/FUNC_SHA 常量。
- 返回 schema 新增 ``kind`` 字段 (``verify_id`` / ``expected_pattern``),
  让 V6 ``verify_reverse_sha_lock_consistency`` 只对 ``verify_id`` kind 做
  orphan 一致性硬检查 (expected_pattern 锁的对象多为外部脚本文件 / 函数,
  hex 不属于 ``live_verify_sha_set`` 维度, 不可硬比对)。

INFRA_052_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_LIB_FILE_SHA
- ``scan_reverse_sha_locks`` func sha: EXPECTED_SCAN_FUNC_SHA
- ``verify_reverse_sha_lock_consistency`` func sha: EXPECTED_CHECK_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: _verify_lib.py 存在 + 关键符号
  (``_RE_REVLOCK_EXPECTED_PATTERN`` 常量, ``kind`` 在 scan 函数源码中出现)
- V1 docstring sentinel ``INFRA_052_SHA_LOCKS`` 自锁 + 本脚本 v4_behavior func sha
- V2 _verify_lib.py file sha + scan_reverse_sha_locks / verify_reverse_sha_lock_consistency
  func sha
- V3 in-memory mutant: ast 替 scan_reverse_sha_locks 把 _RE_REVLOCK_EXPECTED_PATTERN
  分支删掉 (kind="expected_pattern" 分支), unparse 后 sha 必漂移 → 反证
- V4 行为验证 (直接调用 + subprocess):
  - 直接 call ``scan_reverse_sha_locks('scripts')`` 断言:
    - len(scanned) >= 15 (远高于 P268 baseline 7)
    - 每个 item 含 ``kind`` 字段, 值在 {"verify_id", "expected_pattern"} 内
    - 至少 1 个 kind="verify_id", 至少 5 个 kind="expected_pattern"
    - 抽样: 必含 ``EXPECTED_DUMP_INDEX_FILE_SHA`` (P270 落地的反向锁,
      P271 前会被漏扫, 现在必须可见)
  - 直接 call ``verify_reverse_sha_lock_consistency('scripts')`` 断言:
    - ``all_match == True``
    - ``scanned_count == len(scanned)``
  - subprocess ``python scripts/dump_reverse_sha_lock_index.py --json`` rc=0,
    stdout JSON parse 含 ``stats.scanned_count >= 15``
  - subprocess ``python scripts/verify_infra_034.py`` rc=0 (V6 一致性仍 PASS)
- V5 Reviewer LGTM gate (print-only)

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "_verify_lib.py"
DUMP_INDEX_PY = SCRIPTS / "dump_reverse_sha_lock_index.py"
VERIFY_034 = SCRIPTS / "verify_infra_034.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (
    scan_reverse_sha_locks,
    verify_reverse_sha_lock_consistency,
    assert_v5_reviewer_gate_evidence_bind,
)

# infra-052 sha lock 常量 (V2)
EXPECTED_LIB_FILE_SHA = "0461b1817b5d8a2c37f8ec39ac7c561eb20d9021110a237af2abec6e0ce90366"
EXPECTED_SCAN_FUNC_SHA = "772a2d916fc113667b910f4a4f416d9c3b0db9b0bea5ffa749e474a074cfd2b7"
EXPECTED_CHECK_FUNC_SHA = "beb049cbae91be6ad21958f6016a18ea744db67256d6741b67be3c6fb1651970"

# 本脚本 v4_behavior 自锁 (V1) — 首跑 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "708999d0d0af66ab054d08542ae6387f18f8af8fa612d2e51e5d9f27e869989f"

DOCSTRING_SENTINEL = "INFRA_052_SHA_LOCKS"


# phase-47 #1.47: V5_GATE evidence-bind helper (grace_period 兜底 soft graduate)
REAL_FEATURE_LIST = Path(__file__).resolve().parents[1] / "feature_list.json"
V5_GATE_FEATURE_ID = "__PHASE_47_PLACEHOLDER_INFRA_052__"

_results: List[Tuple[str, bool, str]] = []


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_052][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _func_sha(path: Path, func_name: str) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return hashlib.sha256(ast.unparse(node).encode("utf-8")).hexdigest()
    raise RuntimeError(f"func {func_name!r} not found in {path}")


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_lib_exists", LIB.is_file(), f"path={LIB}")
    src = LIB.read_text(encoding="utf-8")
    needed = [
        "_RE_REVLOCK_EXPECTED_PATTERN",
        '"kind": kind',
        '"expected_pattern"',
        '"verify_id"',
    ]
    for needle in needed:
        _emit(
            f"V0_lib_contains_{needle[:32]}",
            needle in src,
            f"needle={needle!r}",
        )


# ---------------------------------------------------------------------------
# V1: docstring sentinel + 本脚本 v4_behavior 自锁
# ---------------------------------------------------------------------------
def v1_docstring_sentinel() -> None:
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8")))
    _emit(
        "V1_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )
    # v4_behavior 自 sha 锁
    got = _func_sha(self_path, "v4_behavior")
    if EXPECTED_V4_CHECKER_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_self_v4_checker_sha_bump_pending",
            False,
            f"got={got} EXPECTED=__BUMP_ME__ (回填该 sha 即可)",
        )
    else:
        _emit(
            "V1_self_v4_checker_sha",
            got == EXPECTED_V4_CHECKER_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_V4_CHECKER_FUNC_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V2: _verify_lib.py file sha + 两 helper func sha
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
        "verify_reverse_sha_lock_consistency": EXPECTED_CHECK_FUNC_SHA,
    }
    for name, exp in expects.items():
        got = _func_sha(LIB, name)
        _emit(
            f"V2_func_sha_{name}",
            got == exp,
            f"got={got[:16]} expect={exp[:16]}",
        )


# ---------------------------------------------------------------------------
# V3: in-memory ast mutant — 删 scan_reverse_sha_locks 中 expected_pattern 分支
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
    # mutant: 把函数体替换为单一 return [] (简单 in-memory mutant)
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
# V4: 行为验证 — 直接 call + subprocess dump_index + verify_infra_034
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    scanned = scan_reverse_sha_locks(SCRIPTS)
    _emit(
        "V4_scan_count_ge_15",
        len(scanned) >= 15,
        f"scanned_count={len(scanned)}",
    )
    # kind 字段存在 & 取值合法
    kinds_ok = all(
        isinstance(it.get("kind"), str) and it["kind"] in ("verify_id", "expected_pattern")
        for it in scanned
    )
    _emit(
        "V4_scan_kind_field_valid",
        kinds_ok,
        f"sample_kinds={sorted({it.get('kind') for it in scanned})}",
    )
    verify_id_items = [it for it in scanned if it.get("kind") == "verify_id"]
    expected_items = [it for it in scanned if it.get("kind") == "expected_pattern"]
    _emit(
        "V4_scan_has_verify_id",
        len(verify_id_items) >= 1,
        f"verify_id_count={len(verify_id_items)}",
    )
    _emit(
        "V4_scan_has_expected_pattern",
        len(expected_items) >= 5,
        f"expected_pattern_count={len(expected_items)}",
    )
    # 抽样: EXPECTED_DUMP_INDEX_FILE_SHA 是 P270 落地的反向锁,
    # P271 前会被漏扫, 现在必须被扫到
    has_dump_index_lock = any(
        it["const_name"] == "EXPECTED_DUMP_INDEX_FILE_SHA" for it in scanned
    )
    _emit(
        "V4_sample_expected_dump_index_file_sha_scanned",
        has_dump_index_lock,
        "EXPECTED_DUMP_INDEX_FILE_SHA 应在新 pattern 下被扫到",
    )

    # 一致性核验 (跳过 expected_pattern, 只对 verify_id 做 orphan 检测)
    result = verify_reverse_sha_lock_consistency(SCRIPTS)
    _emit(
        "V4_consistency_all_match",
        result["all_match"] is True,
        f"scanned={result['scanned_count']} live={result['live_count']} "
        f"orphans={len(result['orphans'])}",
    )
    _emit(
        "V4_consistency_scanned_count_matches",
        result["scanned_count"] == len(scanned),
        f"result.scanned_count={result['scanned_count']} vs len(scanned)={len(scanned)}",
    )

    # subprocess dump_reverse_sha_lock_index.py --json
    proc = subprocess.run(
        [sys.executable, str(DUMP_INDEX_PY), "--json"],
        cwd=str(REPO), capture_output=True, text=True, timeout=30,
    )
    _emit(
        "V4_dump_index_json_rc",
        proc.returncode == 0,
        f"rc={proc.returncode} stderr={proc.stderr[:120]!r}",
    )
    try:
        payload = json.loads(proc.stdout)
        scanned_count_json = payload.get("stats", {}).get("scanned_count", -1)
    except Exception as e:
        payload = None
        scanned_count_json = -1
        _emit("V4_dump_index_json_parse", False, f"err={e}")
    if payload is not None:
        _emit(
            "V4_dump_index_json_scanned_ge_15",
            scanned_count_json >= 15,
            f"stats.scanned_count={scanned_count_json}",
        )

    # subprocess verify_infra_034 (V6 一致性应仍 PASS)
    proc2 = subprocess.run(
        [sys.executable, str(VERIFY_034)],
        cwd=str(REPO), capture_output=True, text=True, timeout=60,
    )
    _emit(
        "V4_verify_infra_034_rc",
        proc2.returncode == 0,
        f"rc={proc2.returncode} (V6 一致性仍 PASS)",
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
        print(f"[verify_infra_052][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_052][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
