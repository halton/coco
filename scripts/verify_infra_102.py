#!/usr/bin/env python3
"""verify_infra_102: _classify_node 用 frozenset 替代 OR 表达式 (verify-only).

infra-P290-classify-node-family-sets (phase-53 #3.53):
``scripts/dump_v4_sha_graph.py`` 的 ``_classify_node`` 函数当前用 OR 链识别
dump 工具家族 (``node_id == "dump_v4_sha_graph" or node_id == "dump_reverse_sha_lock_index"``),
每新增一个 dump 工具就要追加一条 ``or`` 分支。本 feature 把 hub / lib / dump
三类改成模块级 frozenset 集合判断 (``_HUB_FAMILY`` / ``_LIB_FAMILY`` / ``_DUMP_FAMILY``),
让家族扩展边界显式化。

INFRA_102_SHA_LOCKS
-------------------
- ``scripts/dump_v4_sha_graph.py`` file sha: EXPECTED_DUMP_FILE_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- self ``main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding:
  - V0_dump_exists / V0_classify_node_present
  - V0__HUB_FAMILY_present / V0__LIB_FAMILY_present / V0__DUMP_FAMILY_present
- V1_docstring_sentinel ``INFRA_102_SHA_LOCKS`` 自锁
- V2 双 file sha 锁:
  - V2_dump_file_sha / V2_verify_lib_file_sha
- V3 family sets shape + contents:
  - V3_hub_is_frozenset / V3_lib_is_frozenset / V3_dump_is_frozenset
  - V3_dump_family_contents == frozenset({dump_v4_sha_graph, dump_reverse_sha_lock_index})
  - V3_hub_family_contents == frozenset({v4_sha_json})
  - V3_lib_family_contents == frozenset({_verify_lib})
- V4 _classify_node behavior + AST 改写痕迹:
  - V4_classify_dump_v4 / V4_classify_dump_reverse → "dump"
  - V4_classify_verify_lib → "lib"
  - V4_classify_v4_sha_json → "hub"
  - V4_classify_verify_prefix → "verify"
  - V4_classify_unknown_prefix → "unknown"
  - V4_classify_default_module → "module"
  - V4_no_or_chain: 函数源码不含 ``node_id == "dump_v4_sha_graph" or``
  - V4_uses_in_dump_family / V4_uses_in_hub_family / V4_uses_in_lib_family:
    函数源码分别含 ``node_id in _DUMP_FAMILY`` / ``in _HUB_FAMILY`` / ``in _LIB_FAMILY``
  - V4b_self_main_func_sha: 自身 ``main`` func sha 锁
- V5_reviewer_lgtm_gate (grace_period 兜底)

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

# infra-P290 sha lock 常量 (V2)
EXPECTED_DUMP_FILE_SHA = (
    "72a72a986f513f1483b3c49238ab85c185be6108b3616613eee0a613fc1caeb3"
)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "1990a9b61d3b14c9b827a23188a46001be2a208234352e1bc7af955285578f50"
)
# 自身 main func sha (首跑用 __BUMP_ME__ 占位, 再回填)
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "072652ad44846671abec9b9b38aa5d229bbe71d21e9b90d7bee3f356cdacbc2f"
)

DOCSTRING_SENTINEL = "INFRA_102_SHA_LOCKS"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P290-classify-node-family-sets"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_102][{mark}] {tag} {detail}", flush=True)
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
    top_funcs = {
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    _emit(
        "V0_classify_node_present",
        "_classify_node" in top_funcs,
        "expect '_classify_node' top-level function",
    )
    top_assigns = set()
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                if isinstance(tgt, ast.Name):
                    top_assigns.add(tgt.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            top_assigns.add(n.target.id)
    for sym, tag in (
        ("_HUB_FAMILY", "V0__HUB_FAMILY_present"),
        ("_LIB_FAMILY", "V0__LIB_FAMILY_present"),
        ("_DUMP_FAMILY", "V0__DUMP_FAMILY_present"),
    ):
        _emit(
            tag,
            sym in top_assigns,
            f"expect top-level constant {sym}",
        )


# ---------------------------------------------------------------------------
# V1: docstring sentinel
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
# V2: 双 file sha 锁
# ---------------------------------------------------------------------------
def v2_file_sha() -> None:
    got_dump = _file_sha(DUMP_PY)
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
# V3: family sets shape + contents
# ---------------------------------------------------------------------------
def v3_family_sets() -> None:
    import importlib
    sys.path.insert(0, str(SCRIPTS))
    mod = importlib.import_module("dump_v4_sha_graph")
    mod = importlib.reload(mod)

    hub = getattr(mod, "_HUB_FAMILY", None)
    lib = getattr(mod, "_LIB_FAMILY", None)
    dump = getattr(mod, "_DUMP_FAMILY", None)

    _emit("V3_hub_is_frozenset", isinstance(hub, frozenset), f"type={type(hub).__name__}")
    _emit("V3_lib_is_frozenset", isinstance(lib, frozenset), f"type={type(lib).__name__}")
    _emit("V3_dump_is_frozenset", isinstance(dump, frozenset), f"type={type(dump).__name__}")

    _emit(
        "V3_hub_family_contents",
        hub == frozenset({"v4_sha_json"}),
        f"got={sorted(hub) if hub else None} expect=['v4_sha_json']",
    )
    _emit(
        "V3_lib_family_contents",
        lib == frozenset({"_verify_lib"}),
        f"got={sorted(lib) if lib else None} expect=['_verify_lib']",
    )
    _emit(
        "V3_dump_family_contents",
        dump == frozenset({"dump_v4_sha_graph", "dump_reverse_sha_lock_index"}),
        f"got={sorted(dump) if dump else None} "
        f"expect=['dump_reverse_sha_lock_index', 'dump_v4_sha_graph']",
    )


# ---------------------------------------------------------------------------
# V4: _classify_node behavior + AST 改写痕迹
# ---------------------------------------------------------------------------
def v4_behavior_and_ast() -> None:
    import importlib
    sys.path.insert(0, str(SCRIPTS))
    mod = importlib.import_module("dump_v4_sha_graph")
    mod = importlib.reload(mod)

    cls = mod._classify_node
    cases = [
        ("dump_v4_sha_graph", "dump", "V4_classify_dump_v4"),
        ("dump_reverse_sha_lock_index", "dump", "V4_classify_dump_reverse"),
        ("_verify_lib", "lib", "V4_classify_verify_lib"),
        ("v4_sha_json", "hub", "V4_classify_v4_sha_json"),
        ("verify_infra_060", "verify", "V4_classify_verify_prefix"),
        ("unknown_EXPECTED_FOO", "unknown", "V4_classify_unknown_prefix"),
        ("some_random_module", "module", "V4_classify_default_module"),
    ]
    for node_id, expect, tag in cases:
        got = cls(node_id)
        _emit(tag, got == expect, f"node_id={node_id!r} got={got!r} expect={expect!r}")

    # AST 改写痕迹: 函数源码必须包含 "in _DUMP_FAMILY", 不能再含旧 OR 链
    src = DUMP_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    classify_src = ""
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == "_classify_node":
            classify_src = ast.unparse(n)
            break
    _emit(
        "V4_no_or_chain",
        'node_id == "dump_v4_sha_graph" or' not in classify_src
        and "node_id == 'dump_v4_sha_graph' or" not in classify_src,
        "expect 'node_id == \"dump_v4_sha_graph\" or' absent from _classify_node",
    )
    _emit(
        "V4_uses_in_dump_family",
        "node_id in _DUMP_FAMILY" in classify_src,
        "expect 'node_id in _DUMP_FAMILY' in _classify_node body",
    )
    _emit(
        "V4_uses_in_hub_family",
        "node_id in _HUB_FAMILY" in classify_src,
        "expect 'node_id in _HUB_FAMILY' in _classify_node body",
    )
    _emit(
        "V4_uses_in_lib_family",
        "node_id in _LIB_FAMILY" in classify_src,
        "expect 'node_id in _LIB_FAMILY' in _classify_node body",
    )


# ---------------------------------------------------------------------------
# V4b: 自身 main func sha 自锁 (本脚本 V4 行为锁)
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
    v1_docstring_sentinel()
    v2_file_sha()
    v3_family_sets()
    v4_behavior_and_ast()
    v4b_self_main_func_sha()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(f"[verify_infra_102][SUMMARY] FAIL {failed}/{total}: {names}", flush=True)
    else:
        print(f"[verify_infra_102][SUMMARY] ALL PASS ({total} checks)", flush=True)
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
