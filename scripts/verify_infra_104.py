#!/usr/bin/env python3
"""verify_infra_104: _PER_FILE_SELF_LOCKS 集合边界 comment 自锁 (V1 window 加固).

infra-047-backlog-per-file-self-locks-comment (phase-53 #5.53):
``scripts/dump_v4_sha_graph.py`` 的 ``_PER_FILE_SELF_LOCKS`` 仅装"target =
source_file 整文件 file-sha 自锁"; 形似自锁但实际指向 source_file 内某段
func-sha / block-sha 的常量 (如 ``SETTER_BLOCK_EXPECTED_SHA`` /
``EXCEPT_BLOCK_SHA``) 留在 ``_PER_FILE_LOCKS`` 二级查表。本 feature 在
``_PER_FILE_SELF_LOCKS`` 上方加 comment 解释这一边界, 防止后续维护误归类。

infra-104-backlog-v1-window-hardening (phase-57 #2):
原 V1 用 ``[target_idx-12, target_idx)`` 行号偏移窗口扫 comment 关键词,
当 ``_PER_FILE_LOCKS`` dict 体量增长时上方窗口可能误纳入 dict 末尾行造成假阳/假阴。
本次改为 AST 锚 + sentinel comment 双锁:
  1. AST 解析 dump_v4_sha_graph.py, 找 ``_PER_FILE_SELF_LOCKS`` 顶层赋值, 取其 lineno
     作为 anchor (不依赖字符串 startswith / 行号偏移);
  2. 在 anchor 上方 (含整文件) literal-grep ``V1_SELF_LOCKS_COMMENT_BEGIN`` /
     ``V1_SELF_LOCKS_COMMENT_END`` 双 sentinel; 两个 sentinel 之间的精确窗口才是
     V1 comment 区, 关键词必须在其中出现; dict literal 内容不能进入窗口;
  3. V6 behavior 实测: 从源文件 extract sentinel-delimited window, assert
     ``_PER_FILE_SELF_LOCKS`` 顶层赋值 lineno 在 ``V1_SELF_LOCKS_COMMENT_END``
     之下, 且所有 V1 关键词均在 sentinel window 内, dict literal 内绝不含
     sentinel.

INFRA_104_SHA_LOCKS
-------------------
- ``scripts/dump_v4_sha_graph.py`` file sha: EXPECTED_DUMP_FILE_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- self ``main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA

校验层级 (V0-V6):

- V0 scaffolding: dump_v4_sha_graph.py 存在 + _PER_FILE_SELF_LOCKS / _PER_FILE_LOCKS 顶层符号
- V1 sentinel-delimited window 内含 ("SETTER_BLOCK_EXPECTED_SHA", "func-sha", "_PER_FILE_LOCKS")
- V2 双 file sha 锁 (dump + lib)
- V3 dump file sha 自锁 (与 V2 共享常量, V3 单独 emit 强调 V1 anchor 文件)
- V4b 自身 main func sha 自锁
- V5 Reviewer LGTM gate (grace_period 兜底)
- V6 sentinel + AST anchor 行为锁:
  * V6a sentinel begin/end literal present in dump_v4_sha_graph.py
  * V6b AST 找到 _PER_FILE_SELF_LOCKS 顶层 assign, lineno > sentinel_end_lineno
  * V6c sentinel window 内全部 V1 关键词命中
  * V6d dict literal body (assign value source segment) 不含任何 sentinel / V1 关键词

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import List, Optional, Tuple

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

# infra-047-backlog sha lock 常量 (V2 / V3)
EXPECTED_DUMP_FILE_SHA = (
    "ea7e205d6714b452a8875b3b7330055afc0a10a4dcf59e0bd0169b2357d1697b"
)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "7df5af6b9d48687e0a5efab7b3dc2e3dfc2fd54e6aa07d1583fb9d5a56604ef4"
)
# 自身 main func sha (首跑用 __BUMP_ME__ 占位, 再回填)
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "704e17edd1d890f22df06d9a2cf4417da736f4966f9b3c780b12d42482bc108d"
)

DOCSTRING_SENTINEL = "INFRA_104_SHA_LOCKS"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-047-backlog-per-file-self-locks-comment"

# V1: sentinel-delimited window 必须含这三个关键词 (与原 feature 含义一致)
V1_COMMENT_KEYWORDS = (
    "SETTER_BLOCK_EXPECTED_SHA",
    "func-sha",
    "_PER_FILE_LOCKS",
)
SENTINEL_BEGIN = "V1_SELF_LOCKS_COMMENT_BEGIN"
SENTINEL_END = "V1_SELF_LOCKS_COMMENT_END"
SELF_LOCKS_NAME = "_PER_FILE_SELF_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_104][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _find_self_locks_assign_lineno(src: str) -> Optional[int]:
    """AST 锚定 ``_PER_FILE_SELF_LOCKS`` 顶层赋值的 lineno (1-based)."""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == SELF_LOCKS_NAME:
                return node.lineno
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == SELF_LOCKS_NAME:
                    return node.lineno
    return None


def _find_self_locks_assign_node(src: str) -> Optional[ast.stmt]:
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == SELF_LOCKS_NAME:
                return node
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == SELF_LOCKS_NAME:
                    return node
    return None


def _line_index_of(src_lines: List[str], needle: str) -> int:
    for i, line in enumerate(src_lines):
        if needle in line:
            return i
    return -1


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
# V1: sentinel-delimited window 内含 V1_COMMENT_KEYWORDS (替代旧 [-12,0) 行偏移)
# ---------------------------------------------------------------------------
def v1_comment_keywords() -> None:
    src = DUMP_PY.read_text(encoding="utf-8")
    src_lines = src.splitlines()

    anchor_lineno = _find_self_locks_assign_lineno(src)
    _emit(
        "V1_self_locks_ast_anchor",
        anchor_lineno is not None,
        f"AST anchor lineno={anchor_lineno}",
    )
    if anchor_lineno is None:
        for kw in V1_COMMENT_KEYWORDS:
            _emit(
                f"V1_comment_has_{kw.replace('_', '').replace('-', '')[:24]}",
                False,
                "AST anchor missing",
            )
        return

    begin_idx = _line_index_of(src_lines, SENTINEL_BEGIN)
    end_idx = _line_index_of(src_lines, SENTINEL_END)
    _emit(
        "V1_sentinel_begin_found",
        begin_idx >= 0,
        f"{SENTINEL_BEGIN} idx={begin_idx}",
    )
    _emit(
        "V1_sentinel_end_found",
        end_idx >= 0,
        f"{SENTINEL_END} idx={end_idx}",
    )
    if begin_idx < 0 or end_idx < 0:
        for kw in V1_COMMENT_KEYWORDS:
            _emit(
                f"V1_comment_has_{kw.replace('_', '').replace('-', '')[:24]}",
                False,
                "sentinel missing",
            )
        return

    # sentinel 必须在 anchor 上方 (lineno 1-based, idx 0-based; idx < anchor_lineno-1)
    sentinel_above_anchor = end_idx < anchor_lineno - 1 and begin_idx < end_idx
    _emit(
        "V1_sentinel_above_anchor_ordered",
        sentinel_above_anchor,
        f"begin_idx={begin_idx} end_idx={end_idx} anchor_lineno={anchor_lineno}",
    )

    # sentinel 之间的精确 window (含两端)
    window_text = "\n".join(src_lines[begin_idx : end_idx + 1])
    for kw in V1_COMMENT_KEYWORDS:
        tag_suffix = kw.replace("_", "").replace("-", "")[:24]
        _emit(
            f"V1_comment_has_{tag_suffix}",
            kw in window_text,
            f"sentinel_window=[{begin_idx},{end_idx}] keyword={kw!r}",
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
# V3: dump file sha 自锁 (V1 anchor file lock — 单独 emit 突出 V1 加固范围)
# ---------------------------------------------------------------------------
def v3_dump_file_self_lock() -> None:
    got = _file_sha(DUMP_PY)
    if EXPECTED_DUMP_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V3_dump_file_self_lock",
            False,
            f"placeholder; bump EXPECTED_DUMP_FILE_SHA={got}",
        )
        return
    _emit(
        "V3_dump_file_self_lock",
        got == EXPECTED_DUMP_FILE_SHA,
        f"V1_anchor_file_sha got={got[:16]} expect={EXPECTED_DUMP_FILE_SHA[:16]}",
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


# ---------------------------------------------------------------------------
# V6: sentinel + AST 双锚行为锁 (V1 加固 — infra-104)
# ---------------------------------------------------------------------------
def v6_sentinel_ast_behavior() -> None:
    src = DUMP_PY.read_text(encoding="utf-8")
    src_lines = src.splitlines()

    # V6a sentinel literal present
    begin_count = sum(1 for ln in src_lines if SENTINEL_BEGIN in ln)
    end_count = sum(1 for ln in src_lines if SENTINEL_END in ln)
    _emit(
        "V6a_sentinel_begin_literal",
        begin_count == 1,
        f"{SENTINEL_BEGIN} count={begin_count} expect=1",
    )
    _emit(
        "V6a_sentinel_end_literal",
        end_count == 1,
        f"{SENTINEL_END} count={end_count} expect=1",
    )

    # V6b AST anchor 找到 + sentinel_end 在 anchor 上方
    node = _find_self_locks_assign_node(src)
    _emit(
        "V6b_ast_assign_found",
        node is not None,
        f"_PER_FILE_SELF_LOCKS AST assign node={'found' if node else 'missing'}",
    )
    if node is None:
        return
    anchor_lineno = node.lineno
    end_idx = _line_index_of(src_lines, SENTINEL_END)
    _emit(
        "V6b_sentinel_end_above_anchor",
        end_idx >= 0 and end_idx < anchor_lineno - 1,
        f"sentinel_end_idx={end_idx} anchor_lineno={anchor_lineno}",
    )

    # V6c sentinel window 内所有 V1 关键词命中
    begin_idx = _line_index_of(src_lines, SENTINEL_BEGIN)
    if begin_idx < 0 or end_idx < 0:
        _emit("V6c_window_keywords_all_present", False, "sentinel missing")
    else:
        window_text = "\n".join(src_lines[begin_idx : end_idx + 1])
        missing = [kw for kw in V1_COMMENT_KEYWORDS if kw not in window_text]
        _emit(
            "V6c_window_keywords_all_present",
            not missing,
            f"missing={missing} window_lines={end_idx - begin_idx + 1}",
        )

    # V6d dict literal body (AST 节点 value 的 source segment) 不含 sentinel / V1 关键词
    # 防止 dict 末尾行混入 V1 window
    try:
        value_seg = ast.get_source_segment(src, node.value) or ""
    except Exception as e:
        value_seg = ""
        _emit("V6d_dict_literal_seg_extracted", False, f"err={e!r}")
    else:
        _emit(
            "V6d_dict_literal_seg_extracted",
            bool(value_seg.strip()),
            f"seg_len={len(value_seg)}",
        )

    polluters = [SENTINEL_BEGIN, SENTINEL_END] + list(V1_COMMENT_KEYWORDS)
    found_pollute = [p for p in polluters if p in value_seg]
    _emit(
        "V6d_dict_literal_clean",
        not found_pollute,
        f"polluters_in_dict_value={found_pollute}",
    )


def main() -> None:
    v0_scaffolding()
    v1_comment_keywords()
    v2_file_sha()
    v3_dump_file_self_lock()
    v4b_self_main_func_sha()
    v5_reviewer_gate()
    v6_sentinel_ast_behavior()
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
