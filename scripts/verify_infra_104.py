#!/usr/bin/env python3
"""verify_infra_104: _PER_FILE_SELF_LOCKS 集合边界 comment 自锁 (verify-only).

infra-047-backlog-per-file-self-locks-comment (phase-53 #5.53):
``scripts/dump_v4_sha_graph.py`` 的 ``_PER_FILE_SELF_LOCKS`` 仅装"target =
source_file 整文件 file-sha 自锁"; 形似自锁但实际指向 source_file 内某段
func-sha / block-sha 的常量 (如 ``SETTER_BLOCK_EXPECTED_SHA`` /
``EXCEPT_BLOCK_SHA``) 留在 ``_PER_FILE_LOCKS`` 二级查表。本 feature 在
``_PER_FILE_SELF_LOCKS`` 上方加 comment 解释这一边界, 防止后续维护误归类。

INFRA_104_SHA_LOCKS
-------------------
- ``scripts/dump_v4_sha_graph.py`` file sha: EXPECTED_DUMP_FILE_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- self ``main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: dump_v4_sha_graph.py 存在 + _PER_FILE_SELF_LOCKS 与
  _PER_FILE_LOCKS 两个顶层符号存在
- V1 comment 关键词: _PER_FILE_SELF_LOCKS 上方 10 行 comment 必须同时含
  "SETTER_BLOCK_EXPECTED_SHA" + "func-sha" + "_PER_FILE_LOCKS" 三个关键词
- V2 双 file sha 锁 (dump + lib)
- V4b 自身 main func sha 自锁
- V5 Reviewer LGTM gate (grace_period 兜底)

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DUMP_PY = SCRIPTS / "dump_v4_sha_graph.py"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

# infra-047-backlog sha lock 常量 (V2)
EXPECTED_DUMP_FILE_SHA = (
    "f9720b4b4f7f0474087ee30600e2fe3e359cb8b9843d1e1ba6d7dc0e5db1bed9"
)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "e583aed3fd27d6dcc1f55b7f326b2cd6296876d3bdb0c5b1a111994f3f4f99c1"
)
# 自身 main func sha (首跑用 __BUMP_ME__ 占位, 再回填)
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "96bf631ccd781706a7b26304544d97926d1332e8cd03d624f07d5b9e94c9a915"
)

DOCSTRING_SENTINEL = "INFRA_104_SHA_LOCKS"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-047-backlog-per-file-self-locks-comment"

# V1: _PER_FILE_SELF_LOCKS 上方 comment 必须同时含这三个关键词
V1_COMMENT_KEYWORDS = (
    "SETTER_BLOCK_EXPECTED_SHA",
    "func-sha",
    "_PER_FILE_LOCKS",
)
V1_COMMENT_WINDOW = 12  # 向上扫描的行数

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_104][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_dump_exists", DUMP_PY.is_file(), f"path={DUMP_PY.relative_to(REPO)}")
    if not DUMP_PY.is_file():
        return
    src = DUMP_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_assigns = set()
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                if isinstance(tgt, ast.Name):
                    top_assigns.add(tgt.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            top_assigns.add(n.target.id)
    for sym in ("_PER_FILE_LOCKS", "_PER_FILE_SELF_LOCKS"):
        _emit(
            f"V0_{sym}_present",
            sym in top_assigns,
            f"expect top-level constant {sym}",
        )


# ---------------------------------------------------------------------------
# V1: _PER_FILE_SELF_LOCKS 上方 comment 必须含三个关键词
# ---------------------------------------------------------------------------
def v1_comment_keywords() -> None:
    src_lines = DUMP_PY.read_text(encoding="utf-8").splitlines()
    target_idx = -1
    for i, line in enumerate(src_lines):
        # 匹配 "_PER_FILE_SELF_LOCKS: set = {" 或赋值起始行
        if line.startswith("_PER_FILE_SELF_LOCKS"):
            target_idx = i
            break
    _emit(
        "V1_self_locks_line_found",
        target_idx >= 0,
        f"_PER_FILE_SELF_LOCKS line idx={target_idx}",
    )
    if target_idx < 0:
        for kw in V1_COMMENT_KEYWORDS:
            _emit(f"V1_comment_has_{kw.replace('_', '').replace('-', '')[:24]}",
                  False, "anchor missing")
        return
    start = max(0, target_idx - V1_COMMENT_WINDOW)
    window_text = "\n".join(src_lines[start:target_idx])
    for kw in V1_COMMENT_KEYWORDS:
        tag_suffix = kw.replace("_", "").replace("-", "")[:24]
        _emit(
            f"V1_comment_has_{tag_suffix}",
            kw in window_text,
            f"window=[{start},{target_idx}) keyword={kw!r}",
        )


# ---------------------------------------------------------------------------
# V2: 双 file sha 锁
# ---------------------------------------------------------------------------
def v2_file_sha() -> None:
    got_dump = _file_sha(DUMP_PY)
    if EXPECTED_DUMP_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V2_dump_file_sha",
            False,
            f"placeholder; bump EXPECTED_DUMP_FILE_SHA={got_dump}",
        )
    else:
        _emit(
            "V2_dump_file_sha",
            got_dump == EXPECTED_DUMP_FILE_SHA,
            f"got={got_dump[:16]} expect={EXPECTED_DUMP_FILE_SHA[:16]}",
        )
    got_lib = _file_sha(VERIFY_LIB)
    _emit(
        "V2_verify_lib_file_sha",
        got_lib == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got_lib[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4b: 自身 main func sha 自锁
# ---------------------------------------------------------------------------
def v4b_self_main_func_sha() -> None:
    self_path = Path(__file__)
    got = func_sha_by_name(self_path, "main")
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V4b_self_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
    else:
        _emit(
            "V4b_self_main_func_sha",
            got == EXPECTED_SELF_MAIN_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
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


def main() -> None:
    v0_scaffolding()
    v1_comment_keywords()
    v2_file_sha()
    v4b_self_main_func_sha()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(f"[verify_infra_104][SUMMARY] FAIL {failed}/{total}: {names}", flush=True)
    else:
        print(f"[verify_infra_104][SUMMARY] ALL PASS ({total} checks)", flush=True)
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
