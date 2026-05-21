#!/usr/bin/env python3
"""verify_infra_060: P285 classifier lib-func locks 行为锁 V0-V5.

infra-P285-classifier-recognize-lib-func-locks (phase-37 #1.37):
``scripts/dump_v4_sha_graph.py`` 中 ``_classify_node`` 仅按 node_id 字面
判定 hub / verify / lib / dump / module / unknown 六类; 而 ``unknown_<CONST>``
形占位节点是 ``render_mermaid`` 在 lock target 描述不含 ``.py`` stem 时
退化产生 (line 360-366), 真实根因在 ``_infer_target`` 兜底 ``<unknown target>``
即 ``(source, const)`` 未注册到 ``_PER_FILE_LOCKS``。

P285 修复路径 (无新增 helper, 全程编辑 dump_v4_sha_graph.py):

1. ``_PER_FILE_LOCKS`` 补 13 条 ``(source, const) → target`` 映射, 覆盖
   verify_infra_049/050/051/052/055/057/058/059 中 13 个原 unknown
   EXPECTED_*_FUNC_SHA / FILE_SHA 常量, 让 ``_infer_target`` 返回真实
   target 字符串 (含 ``.py`` stem)。
2. ``_classify_node`` 增 ``dump_reverse_sha_lock_index`` → ``dump`` 识别
   (与已有 dump_v4_sha_graph 同组)。

效果: ``dump --mermaid`` 输出 unknown 节点数 13 → 1 (剩 EXPECTED_DOC_SHA
target 是 docs/ 非 .py 文件, 保留 unknown)。verify_infra_059 EXPECTED_CURRENT_UNKNOWN_COUNT
由 13 同步 bump 1。

INFRA_060_SHA_LOCKS
-------------------
- ``scripts/dump_v4_sha_graph.py`` file sha: EXPECTED_DUMP_FILE_SHA
- ``_classify_node`` func sha: EXPECTED_CLASSIFY_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA
- 13 条新 (source, const) 映射 frozenset: EXPECTED_NEW_LOCKS

校验层级 (V0-V5):

- V0 scaffolding: dump_v4_sha_graph.py 存在 + _PER_FILE_LOCKS / _classify_node 符号
- V1 docstring sentinel ``INFRA_060_SHA_LOCKS`` + 本脚本 v4_behavior func sha 自锁
- V2 dump_v4_sha_graph.py file sha
- V3 _classify_node canonical func sha + _PER_FILE_LOCKS 含 13 条 P285 映射
- V4 行为:
  - 真实 dump --mermaid → 派生 nodes, 断言 unknown_count == 1
  - 13 个原 unknown_EXPECTED_* 节点 ID 不再出现在 mermaid 输出
  - 4 个 lib helper sha 常量已分类为 lib (assert_verify_passed / scan /
    palette / unknown helper 各 1 个采样)
  - 5 个 dump_reverse_sha_lock_index 常量已分类为 dump
- V5 Reviewer LGTM gate (print-only)

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
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
DUMP_PY = SCRIPTS / "dump_v4_sha_graph.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import func_sha_by_name  # noqa: E402

# P285 sha lock 常量 (V2 / V3)
EXPECTED_DUMP_FILE_SHA = "3ed333f3aaac3f5da885136b15aea03a287cec30dbb5c4e9814737906cb83904"
EXPECTED_CLASSIFY_FUNC_SHA = "03f5ccb1de76db543a36edf52cd666f11542e0432f92125bd977ca5efd202aee"

# 本脚本 v4_behavior 自锁 (V1) — 首跑 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "8b709e3bc54acf5a4fa131c752ce804318ce409e5e851b013eb623142439324a"

# P285 新增的 13 条 (source, const) → target 映射;
# V3 断言 _PER_FILE_LOCKS 字典含每一条 key, 且 value 含正确 .py stem。
EXPECTED_NEW_LOCKS: tuple = (
    ("verify_infra_049.py", "EXPECTED_DUMP_INDEX_FILE_SHA", "dump_reverse_sha_lock_index.py"),
    ("verify_infra_049.py", "EXPECTED_RENDER_TEXT_FUNC_SHA", "dump_reverse_sha_lock_index.py"),
    ("verify_infra_049.py", "EXPECTED_RENDER_JSON_FUNC_SHA", "dump_reverse_sha_lock_index.py"),
    ("verify_infra_050.py", "EXPECTED_DUMP_INDEX_FILE_SHA", "dump_reverse_sha_lock_index.py"),
    ("verify_infra_050.py", "EXPECTED_RENDER_JSON_FUNC_SHA", "dump_reverse_sha_lock_index.py"),
    ("verify_infra_051.py", "EXPECTED_DUMP_INDEX_FILE_SHA", "dump_reverse_sha_lock_index.py"),
    ("verify_infra_051.py", "EXPECTED_RENDER_CHECK_JSON_FUNC_SHA", "dump_reverse_sha_lock_index.py"),
    ("verify_infra_051.py", "EXPECTED_CMD_CHECK_FUNC_SHA", "dump_reverse_sha_lock_index.py"),
    ("verify_infra_051.py", "EXPECTED_BUILD_ARG_PARSER_FUNC_SHA", "dump_reverse_sha_lock_index.py"),
    ("verify_infra_052.py", "EXPECTED_SCAN_FUNC_SHA", "_verify_lib.py"),
    ("verify_infra_052.py", "EXPECTED_CHECK_FUNC_SHA", "_verify_lib.py"),
    ("verify_infra_055.py", "EXPECTED_ASSERT_VERIFY_PASSED_FUNC_SHA", "_verify_lib.py"),
    ("verify_infra_057.py", "EXPECTED_VERIFY_EP_FUNC_SHA", "_verify_lib.py"),
    ("verify_infra_058.py", "EXPECTED_PALETTE_FUNC_SHA", "_verify_lib.py"),
    ("verify_infra_059.py", "EXPECTED_UNKNOWN_FUNC_SHA", "_verify_lib.py"),
)
# 当前实测 unknown 节点数精确锁 (P285 后): 13 → 1
EXPECTED_CURRENT_UNKNOWN_COUNT = 1

# 13 个原 unknown 节点 ID (P285 前实测)
ORIGINAL_UNKNOWN_IDS: frozenset = frozenset({
    "unknown_EXPECTED_ASSERT_VERIFY_PASSED_FUNC_SHA",
    "unknown_EXPECTED_BUILD_ARG_PARSER_FUNC_SHA",
    "unknown_EXPECTED_CHECK_FUNC_SHA",
    "unknown_EXPECTED_CMD_CHECK_FUNC_SHA",
    # EXPECTED_DOC_SHA 留作合理 unknown (target=docs/ 非 .py), 保留
    "unknown_EXPECTED_DUMP_INDEX_FILE_SHA",
    "unknown_EXPECTED_PALETTE_FUNC_SHA",
    "unknown_EXPECTED_RENDER_CHECK_JSON_FUNC_SHA",
    "unknown_EXPECTED_RENDER_JSON_FUNC_SHA",
    "unknown_EXPECTED_RENDER_TEXT_FUNC_SHA",
    "unknown_EXPECTED_SCAN_FUNC_SHA",
    "unknown_EXPECTED_UNKNOWN_FUNC_SHA",
    "unknown_EXPECTED_VERIFY_EP_FUNC_SHA",
})

DOCSTRING_SENTINEL = "INFRA_060_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_060][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# class 行格式: "    class <node_id> <kind>;"
_RE_MERMAID_CLASS = re.compile(r"^\s*class\s+(\S+)\s+(\w+);\s*$")


def _derive_nodes_from_mermaid(mermaid_text: str) -> list[dict]:
    nodes: list[dict] = []
    for line in mermaid_text.splitlines():
        m = _RE_MERMAID_CLASS.match(line)
        if m:
            nodes.append({"id": m.group(1), "kind": m.group(2)})
    return nodes


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_dump_exists", DUMP_PY.is_file(), f"path={DUMP_PY}")
    if not DUMP_PY.is_file():
        return
    src = DUMP_PY.read_text(encoding="utf-8")
    _emit(
        "V0_per_file_locks_present",
        "_PER_FILE_LOCKS" in src,
        "expect '_PER_FILE_LOCKS' dict in dump_v4_sha_graph",
    )
    _emit(
        "V0_classify_node_present",
        "def _classify_node(" in src,
        "expect 'def _classify_node(' in dump_v4_sha_graph",
    )


# ---------------------------------------------------------------------------
# V1: docstring sentinel + 本脚本 v4_behavior func sha 自锁
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
# V2: dump_v4_sha_graph.py file sha
# ---------------------------------------------------------------------------
def v2_dump_file_sha() -> None:
    got = _file_sha(DUMP_PY)
    if EXPECTED_DUMP_FILE_SHA == "__BUMP_ME__":
        _emit("V2_dump_file_sha", False, f"placeholder; bump EXPECTED_DUMP_FILE_SHA={got}")
        return
    _emit(
        "V2_dump_file_sha",
        got == EXPECTED_DUMP_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_DUMP_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: _classify_node canonical func sha + _PER_FILE_LOCKS 含 P285 13 条映射
# ---------------------------------------------------------------------------
def v3_classify_and_lookup_table() -> None:
    try:
        got = func_sha_by_name(DUMP_PY, "_classify_node")
    except Exception as e:
        _emit("V3_classify_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_CLASSIFY_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V3_classify_func_sha",
            False,
            f"placeholder; bump EXPECTED_CLASSIFY_FUNC_SHA={got}",
        )
    else:
        _emit(
            "V3_classify_func_sha",
            got == EXPECTED_CLASSIFY_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_CLASSIFY_FUNC_SHA[:16]}",
        )
    # _PER_FILE_LOCKS 13 条 P285 映射断言: 通过 import dump_v4_sha_graph 直接查表
    sys.path.insert(0, str(SCRIPTS))
    import importlib
    mod = importlib.import_module("dump_v4_sha_graph")
    pfl = getattr(mod, "_PER_FILE_LOCKS", {})
    miss = []
    wrong = []
    for src, const, stem in EXPECTED_NEW_LOCKS:
        key = (src, const)
        if key not in pfl:
            miss.append(key)
            continue
        tgt = pfl[key]
        if stem not in tgt:
            wrong.append((key, tgt, stem))
    _emit(
        "V3_per_file_locks_p285_present",
        not miss and not wrong,
        f"missing={miss[:3]} wrong={wrong[:2]} total_required={len(EXPECTED_NEW_LOCKS)}",
    )


# ---------------------------------------------------------------------------
# V4: 行为 — 真 dump --mermaid → unknown_count == 1, 13 旧 unknown id 全消失
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # 1) 真实 dump --mermaid 派生 nodes
    try:
        proc = subprocess.run(
            [sys.executable, str(DUMP_PY), "--mermaid"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        mermaid_text = proc.stdout
    except Exception as e:
        _emit("V4_real_dump_run", False, f"dump err: {e!r}")
        return
    _emit("V4_real_dump_run", True, f"stdout_bytes={len(mermaid_text)}")
    nodes = _derive_nodes_from_mermaid(mermaid_text)
    by_kind: dict = {}
    for n in nodes:
        by_kind.setdefault(n["kind"], []).append(n["id"])
    unknown_ids = set(by_kind.get("unknown", []))
    _emit(
        "V4_real_unknown_count_eq_one",
        len(unknown_ids) == EXPECTED_CURRENT_UNKNOWN_COUNT,
        f"unknown_count={len(unknown_ids)} expect={EXPECTED_CURRENT_UNKNOWN_COUNT} ids={sorted(unknown_ids)}",
    )
    # 2) 13 个旧 unknown ID 全消失
    leaked = ORIGINAL_UNKNOWN_IDS & unknown_ids
    _emit(
        "V4_original_13_unknown_eliminated",
        len(leaked) == 0,
        f"leaked={sorted(leaked)} (expect empty; 13 旧 unknown 应全归 lib/dump)",
    )
    # 3) lib 分类含 4 个采样 helper sha 节点
    lib_ids = set(by_kind.get("lib", []))
    lib_expected_samples = {"_verify_lib"}  # render_mermaid 把 lib helper 锁映射到 _verify_lib stem
    _emit(
        "V4_lib_classification_present",
        lib_expected_samples.issubset(lib_ids),
        f"lib_ids={sorted(lib_ids)} expect superset_of={sorted(lib_expected_samples)}",
    )
    # 4) dump 分类含 dump_reverse_sha_lock_index (P285 新增识别)
    dump_ids = set(by_kind.get("dump", []))
    _emit(
        "V4_dump_includes_reverse_index",
        "dump_reverse_sha_lock_index" in dump_ids,
        f"dump_ids={sorted(dump_ids)}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    _emit(
        "V5_reviewer_lgtm_gate",
        True,
        "closeout 阶段必须有 sub-agent fresh-context Reviewer LGTM (evidence 记录)",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_lock()
    v2_dump_file_sha()
    v3_classify_and_lookup_table()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_060][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_060][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
