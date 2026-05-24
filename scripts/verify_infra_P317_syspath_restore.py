#!/usr/bin/env python3
"""verify_infra_P317_syspath_restore: V12 _infer_target_via_ast sys.path 还原锁.

infra-P317-V12-syspath-restore (phase-65 #3, from phase-64 #5 Reviewer P2-1):
``scripts/dump_v4_sha_graph.py:_infer_target_via_ast`` 在 lazy import ``_verify_lib``
时此前直接 ``sys.path.insert(0, str(SCRIPTS))``, 短命 CLI 进程无碍, 但若作为库
被上游 import (例如未来 verify 系列脚本直接 ``from dump_v4_sha_graph import ...``),
会在调用方留下永久 sys.path 副作用, 污染上游模块解析。本 verify 锁住:

V0  scaffolding: dump_v4_sha_graph.py 存在, ``_infer_target_via_ast`` 顶层可访问
V1  AST 静态结构: 在 ``_infer_target_via_ast`` 函数体内必须出现 ``try`` 与
    ``finally`` 块, 且 ``finally`` 块中含 ``sys.path.pop`` 或 ``sys.path.remove`` 调用
V2  AST 静态结构 (negative): ``_infer_target_via_ast`` 函数体内不允许出现
    **裸** ``sys.path.insert`` 调用 — 任何 ``sys.path.insert`` 必须在
    ``Try`` (含 finally handler) 节点之内
V3  行为 (live, idempotent): 调用 ``_infer_target_via_ast`` 一次, 函数返回后
    ``sys.path`` 与调用前完全一致 (无副作用)
V4  行为 (live, repeated): 连续调用 100 次后 ``sys.path`` 长度增量 == 0
V5  行为 (live, pre-insert robustness): 若调用方已把 SCRIPTS 加入 sys.path,
    函数调用后 SCRIPTS 仍在 sys.path 且未被重复 append
V6  整体 sha256 锁 verify 自身 (drift 抓手): EXPECTED_DUMP_FILE_SHA

verify-only / sim-only / 无业务源码改动 (除 dump_v4_sha_graph.py 本身).
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DUMP_FILE = SCRIPTS / "dump_v4_sha_graph.py"

# V6: dump_v4_sha_graph.py 整体 sha256 锁 (drift 抓手); 任何 dump 改动需同 bump.
EXPECTED_DUMP_FILE_SHA = (
    "a4cc770d49c77b1c4e6bec1d4cb62245e57bda9efdc4cc9ae2945fdad873eed5"
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    _results.append((tag, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P317] {status} {tag}: {detail}", flush=True)


def _find_func(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _is_sys_path_attr(node: ast.AST, attr: str) -> bool:
    """匹配 sys.path.<attr> 形态调用 (Attribute on Attribute)."""
    if not isinstance(node, ast.Attribute):
        return False
    if node.attr != attr:
        return False
    inner = node.value
    if not isinstance(inner, ast.Attribute):
        return False
    if inner.attr != "path":
        return False
    base = inner.value
    return isinstance(base, ast.Name) and base.id == "sys"


def v0_scaffolding() -> None:
    if not DUMP_FILE.is_file():
        _emit("V0_scaffolding", False, "dump_v4_sha_graph.py missing")
        return
    src = DUMP_FILE.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        _emit("V0_scaffolding", False, f"parse error: {e}")
        return
    fn = _find_func(tree, "_infer_target_via_ast")
    _emit("V0_scaffolding", fn is not None, f"func_found={fn is not None}")


def v1_try_finally_pop_present() -> None:
    src = DUMP_FILE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = _find_func(tree, "_infer_target_via_ast")
    if fn is None:
        _emit("V1_try_finally_pop_present", False, "func missing")
        return
    # 在 fn 子树中找含 finalbody 的 Try, finally 中含 sys.path.pop / remove
    found = False
    detail = ""
    for node in ast.walk(fn):
        if isinstance(node, ast.Try) and node.finalbody:
            for fnode in ast.walk(ast.Module(body=node.finalbody, type_ignores=[])):
                if isinstance(fnode, ast.Call):
                    if _is_sys_path_attr(fnode.func, "pop") or _is_sys_path_attr(
                        fnode.func, "remove"
                    ):
                        found = True
                        detail = f"call={ast.unparse(fnode.func)}"
                        break
            if found:
                break
    _emit("V1_try_finally_pop_present", found, detail or "no finally pop/remove found")


def v2_no_bare_sys_path_insert() -> None:
    """函数体内任何 sys.path.insert 必须被 Try (含 finalbody) 包裹."""
    src = DUMP_FILE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = _find_func(tree, "_infer_target_via_ast")
    if fn is None:
        _emit("V2_no_bare_sys_path_insert", False, "func missing")
        return
    # 收集所有 Try 节点 (含 finalbody) 在 fn 内的 lineno 范围
    try_ranges: List[Tuple[int, int]] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Try) and node.finalbody:
            try_ranges.append((node.lineno, node.end_lineno or node.lineno))
    bare_inserts: List[int] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _is_sys_path_attr(node.func, "insert"):
            ln = node.lineno
            covered = any(lo <= ln <= hi for lo, hi in try_ranges)
            if not covered:
                bare_inserts.append(ln)
    ok = not bare_inserts
    _emit(
        "V2_no_bare_sys_path_insert",
        ok,
        f"bare_inserts={bare_inserts} try_ranges={try_ranges}",
    )


def _import_dump():
    """动态 import dump_v4_sha_graph 模块 (不污染本进程的 sys.path)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "dump_v4_sha_graph_for_P317", str(DUMP_FILE)
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"[verify_infra_P317] FAIL: dump import 失败: {e}", file=sys.stderr)
        return None
    return mod


def v3_no_sys_path_side_effect() -> None:
    mod = _import_dump()
    if mod is None:
        _emit("V3_no_sys_path_side_effect", False, "import failed")
        return
    before = list(sys.path)
    # 调用一次, 用一个能触发 lazy import 路径的 const + source_file
    # ``verify_infra_033_lock_doc_rollout.py`` 文件名以 verify_ 开头, 满足白名单
    try:
        mod._infer_target_via_ast(
            "EXPECTED_PARSE_AREA_FUNC_SHA",
            "scripts/verify_infra_033_lock_doc_rollout.py",
        )
    except Exception as e:
        _emit("V3_no_sys_path_side_effect", False, f"call raised: {e}")
        return
    after = list(sys.path)
    ok = before == after
    _emit(
        "V3_no_sys_path_side_effect",
        ok,
        f"before_len={len(before)} after_len={len(after)} equal={ok}",
    )


def v4_repeated_calls_no_growth() -> None:
    mod = _import_dump()
    if mod is None:
        _emit("V4_repeated_calls_no_growth", False, "import failed")
        return
    before_len = len(sys.path)
    for _ in range(100):
        try:
            mod._infer_target_via_ast(
                "EXPECTED_PARSE_AREA_FUNC_SHA",
                "scripts/verify_infra_033_lock_doc_rollout.py",
            )
        except Exception:
            pass
    delta = len(sys.path) - before_len
    _emit("V4_repeated_calls_no_growth", delta == 0, f"delta={delta}")


def v5_pre_inserted_scripts_preserved() -> None:
    mod = _import_dump()
    if mod is None:
        _emit("V5_pre_inserted_scripts_preserved", False, "import failed")
        return
    scripts_str = str(SCRIPTS)
    # 清场后预先插入 SCRIPTS, 模拟调用方已加入
    if scripts_str in sys.path:
        sys.path.remove(scripts_str)
    sys.path.insert(0, scripts_str)
    before_count = sys.path.count(scripts_str)
    try:
        mod._infer_target_via_ast(
            "EXPECTED_PARSE_AREA_FUNC_SHA",
            "scripts/verify_infra_033_lock_doc_rollout.py",
        )
    except Exception as e:
        sys.path.remove(scripts_str)
        _emit("V5_pre_inserted_scripts_preserved", False, f"call raised: {e}")
        return
    after_count = sys.path.count(scripts_str)
    # 清理
    if scripts_str in sys.path:
        sys.path.remove(scripts_str)
    ok = before_count == 1 and after_count == 1
    _emit(
        "V5_pre_inserted_scripts_preserved",
        ok,
        f"before_count={before_count} after_count={after_count}",
    )


def v6_dump_file_sha_lock() -> None:
    if not DUMP_FILE.is_file():
        _emit("V6_dump_file_sha_lock", False, "dump missing")
        return
    actual = hashlib.sha256(DUMP_FILE.read_bytes()).hexdigest()
    if EXPECTED_DUMP_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V6_dump_file_sha_lock",
            False,
            f"placeholder; bump EXPECTED_DUMP_FILE_SHA={actual}",
        )
        return
    ok = actual == EXPECTED_DUMP_FILE_SHA
    _emit(
        "V6_dump_file_sha_lock",
        ok,
        f"actual={actual[:16]} expect={EXPECTED_DUMP_FILE_SHA[:16]}",
    )


def main() -> int:
    v0_scaffolding()
    v1_try_finally_pop_present()
    v2_no_bare_sys_path_insert()
    v3_no_sys_path_side_effect()
    v4_repeated_calls_no_growth()
    v5_pre_inserted_scripts_preserved()
    v6_dump_file_sha_lock()
    fail = [t for t, ok, _ in _results if not ok]
    if fail:
        print(
            f"[verify_infra_P317] OVERALL FAIL: {len(fail)}/{len(_results)} failed: {fail}",
            flush=True,
        )
        return 1
    print(
        f"[verify_infra_P317] OVERALL PASS: {len(_results)}/{len(_results)} checks",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
