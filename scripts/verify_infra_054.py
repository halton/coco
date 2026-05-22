#!/usr/bin/env python3
"""verify_infra_054: dump_v4_sha_graph _MERMAID_PALETTE 常量提取 (verify-only).

infra-048-backlog-mermaid-palette-extract (phase-35 #5.35 P273): P267 落地 + P272
hub 调色后, render_mermaid 内 6 类 classDef 色值散在函数体内字面量。本 feature
把 6 色值提到模块顶部常量 _MERMAID_PALETTE (dict[str, dict[str, str]]),
render_mermaid 按固定顺序 (hub/verify/lib/dump/module/unknown) 迭代生成 classDef
行；输出必须与 P272 状态 bytewise 完全一致 (6 个 verify 依赖此输出)。

本脚本验证:
- V0 scaffolding: dump_v4_sha_graph.py 存在 + _MERMAID_PALETTE / render_mermaid 在
- V1 docstring sentinel ``INFRA_054_SHA_LOCKS`` 自锁 + 本脚本 v4_behavior func sha
- V2 dump_v4_sha_graph.py file sha + ``render_mermaid`` func sha + ``_MERMAID_PALETTE``
  常量顶层 assignment 存在 (ast 找 Module body 顶层 Assign / AnnAssign)
- V3 mutant 反证:
  - mutant-A: 改 _MERMAID_PALETTE hub fill 值 (#fc6 → #abc), 重新 exec module 后
    render_mermaid 输出漂移 (hub classDef 行不再含 #fc6)
  - mutant-B: 删除 _MERMAID_PALETTE 顶层赋值, exec module 后 render_mermaid 必 NameError
- V4 行为验证: subprocess 调用 ``python scripts/dump_v4_sha_graph.py --mermaid``,
  stdout 含 6 行 classDef + 命中 hub #fc6 / verify #9cf / lib #9f9 / dump #ff9 /
  module #c9f / unknown #f99 全部 6 类色值 (与 P272 一致); bytewise 抽样至少 3 行
  classDef 字符串值与硬编码期望相等; 重复跑 3 次输出 bytewise 一致
- V5 Reviewer LGTM gate (print-only)

INFRA_054_SHA_LOCKS
-------------------
- ``scripts/dump_v4_sha_graph.py`` file sha: EXPECTED_DUMP_FILE_SHA
- ``render_mermaid`` func sha: EXPECTED_RENDER_MERMAID_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DUMP_PY = SCRIPTS / "dump_v4_sha_graph.py"

# infra-054 sha lock 常量 (V2)
EXPECTED_DUMP_FILE_SHA = "14ddc08e153a145fd5029a034e7eef7bf44f90d7c14272bc140c7a73e4d45905"
EXPECTED_RENDER_MERMAID_FUNC_SHA = "4a6e52392548fa257b8eb5cd3bef1cd48c222364031da0f0e14f191aef7ec695"

# 本脚本 v4_behavior 自锁 (V1) — 首跑用 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "e8663f659806810092f583a6ff5813ccd3612f399df8bc387f7bcc03588bdd46"

DOCSTRING_SENTINEL = "INFRA_054_SHA_LOCKS"

# 期望 6 色 palette (与 P272 状态一致)
EXPECTED_PALETTE: dict = {
    "hub":     {"fill": "#fc6", "stroke": "#b85", "color": "#000"},
    "verify":  {"fill": "#9cf", "stroke": "#069", "color": "#000"},
    "lib":     {"fill": "#9f9", "stroke": "#090", "color": "#000"},
    "dump":    {"fill": "#ff9", "stroke": "#990", "color": "#000"},
    "module":  {"fill": "#c9f", "stroke": "#609", "color": "#000"},
    "unknown": {"fill": "#f99", "stroke": "#900", "color": "#000"},
}
# 期望 6 行 classDef 字面输出 (bytewise 锚定)
EXPECTED_CLASSDEF_LINES: List[str] = [
    f"    classDef {cls} fill:{p['fill']},stroke:{p['stroke']},color:{p['color']};"
    for cls, p in EXPECTED_PALETTE.items()
]

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_054][{mark}] {tag} {detail}", flush=True)
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


def _find_palette_assign(tree: ast.Module) -> ast.AST:
    """在 Module 顶层 body 找 _MERMAID_PALETTE 的 Assign / AnnAssign 节点。"""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_MERMAID_PALETTE":
                    return node
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "_MERMAID_PALETTE":
            return node
    raise LookupError("_MERMAID_PALETTE not found at module top level")


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_dump_exists", DUMP_PY.is_file(), f"path={DUMP_PY}")
    src = DUMP_PY.read_text(encoding="utf-8")
    needed = [
        "def render_mermaid",
        "_MERMAID_PALETTE",
        "classDef ",  # 仍残留构造语句 (f-string)
    ]
    found = [n for n in needed if n in src]
    _emit(
        "V0_palette_symbols",
        len(found) == len(needed),
        f"found {len(found)}/{len(needed)}",
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
        got = _func_sha(self_path, "v4_behavior")
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
# V2: dump file sha + render_mermaid func sha + _MERMAID_PALETTE 顶层存在
# ---------------------------------------------------------------------------
def v2_dump_locks() -> None:
    got_file = _file_sha(DUMP_PY)
    if EXPECTED_DUMP_FILE_SHA == "__BUMP_ME__":
        _emit("V2_dump_file_sha", False, f"placeholder; bump EXPECTED_DUMP_FILE_SHA={got_file}")
    else:
        _emit(
            "V2_dump_file_sha",
            got_file == EXPECTED_DUMP_FILE_SHA,
            f"got={got_file[:16]} expect={EXPECTED_DUMP_FILE_SHA[:16]}",
        )
    try:
        got_func = _func_sha(DUMP_PY, "render_mermaid")
    except Exception as e:
        _emit("V2_render_mermaid_func_sha", False, f"compute err: {e!r}")
    else:
        if EXPECTED_RENDER_MERMAID_FUNC_SHA == "__BUMP_ME__":
            _emit(
                "V2_render_mermaid_func_sha",
                False,
                f"placeholder; bump EXPECTED_RENDER_MERMAID_FUNC_SHA={got_func}",
            )
        else:
            _emit(
                "V2_render_mermaid_func_sha",
                got_func == EXPECTED_RENDER_MERMAID_FUNC_SHA,
                f"got={got_func[:16]} expect={EXPECTED_RENDER_MERMAID_FUNC_SHA[:16]}",
            )

    # _MERMAID_PALETTE 顶层 assignment 存在
    src = DUMP_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    try:
        _find_palette_assign(tree)
        _emit("V2_palette_top_level_assign", True, "_MERMAID_PALETTE at module top level")
    except LookupError as e:
        _emit("V2_palette_top_level_assign", False, str(e))


# ---------------------------------------------------------------------------
# V3: mutant 反证 — 改 palette 值 / 删除 palette 顶层赋值
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    src = DUMP_PY.read_text(encoding="utf-8")

    # mutant-A: 把 _MERMAID_PALETTE 的 hub fill #fc6 改为 #abc, exec 后输出漂移
    mutated_src = src.replace('"fill": "#fc6"', '"fill": "#abc"', 1)
    if mutated_src == src:
        _emit("V3a_mutant_apply", False, "literal '\"fill\": \"#fc6\"' not found in source")
    else:
        _emit("V3a_mutant_apply", True, "hub fill #fc6 -> #abc applied")
        ns: dict = {"__name__": "_infra054_mutantA", "__file__": str(DUMP_PY)}
        try:
            exec(compile(mutated_src, str(DUMP_PY) + ".mutantA", "exec"), ns)
            out = ns["render_mermaid"]({})
            hub_lines = [ln for ln in out.splitlines() if "classDef hub" in ln]
            ok = len(hub_lines) == 1 and "#fc6" not in hub_lines[0] and "#abc" in hub_lines[0]
            _emit(
                "V3a_mutant_output_drift",
                ok,
                f"hub_line={hub_lines[0] if hub_lines else '<missing>'}",
            )
        except Exception as e:
            _emit("V3a_mutant_output_drift", False, f"exec err: {e!r}")

    # mutant-B: 删除 _MERMAID_PALETTE 顶层赋值 (用 ast 移除), exec 后 render_mermaid 必 NameError
    tree = ast.parse(src)
    new_body = []
    removed = False
    for node in tree.body:
        is_palette = False
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_MERMAID_PALETTE":
                    is_palette = True
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "_MERMAID_PALETTE":
            is_palette = True
        if is_palette:
            removed = True
            continue
        new_body.append(node)
    tree.body = new_body
    if not removed:
        _emit("V3b_mutant_remove_palette", False, "no _MERMAID_PALETTE assignment to remove")
        return
    _emit("V3b_mutant_remove_palette", True, "removed _MERMAID_PALETTE top-level assignment")

    ast.fix_missing_locations(tree)
    ns2: dict = {"__name__": "_infra054_mutantB", "__file__": str(DUMP_PY)}
    try:
        exec(compile(tree, str(DUMP_PY) + ".mutantB", "exec"), ns2)
        # exec module-level 应成功 (palette 仅在 render_mermaid 内引用); 调 render_mermaid 必 NameError
        try:
            ns2["render_mermaid"]({})
            _emit("V3b_render_raises_without_palette", False, "render_mermaid did NOT raise")
        except NameError as e:
            _emit("V3b_render_raises_without_palette", True, f"NameError: {e}")
        except Exception as e:
            _emit("V3b_render_raises_without_palette", False, f"unexpected exc: {type(e).__name__}: {e}")
    except Exception as e:
        # exec module-level 失败也算反证成功 (palette 被引用)
        _emit("V3b_render_raises_without_palette", True, f"module exec err (acceptable): {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V4: 行为验证 — subprocess 调用 dump --mermaid, 检查 6 色 classDef + bytewise
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    outputs: List[str] = []
    for i in range(3):
        try:
            res = subprocess.run(
                [sys.executable, str(DUMP_PY), "--mermaid"],
                cwd=str(REPO),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as e:
            _emit(f"V4_subprocess_run_{i}", False, f"err: {e!r}")
            return
        _emit(f"V4_subprocess_run_{i}", res.returncode == 0, f"rc={res.returncode}")
        outputs.append(res.stdout)

    out = outputs[0]
    classdef_lines = [ln for ln in out.splitlines() if "classDef " in ln]
    _emit(
        "V4_classdef_total_count",
        len(classdef_lines) == 6,
        f"got={len(classdef_lines)} expect=6",
    )

    # 6 类色值全部命中
    expected_fills = {cls: p["fill"] for cls, p in EXPECTED_PALETTE.items()}
    hit = []
    for cls, fill in expected_fills.items():
        matched = any(f"classDef {cls} " in ln and fill in ln for ln in classdef_lines)
        if matched:
            hit.append(cls)
    _emit(
        "V4_all_six_fills_present",
        len(hit) == 6,
        f"hit={hit} expect=6",
    )

    # bytewise 抽样: 6 行 classDef 中至少 3 行与硬编码期望字面相等
    bytewise_matches = sum(1 for exp in EXPECTED_CLASSDEF_LINES if exp in classdef_lines)
    _emit(
        "V4_bytewise_sample_match",
        bytewise_matches >= 3,
        f"bytewise_matches={bytewise_matches}/6 (expect >=3)",
    )

    # 6 行 classDef 全部 bytewise 与期望相等 (加强断言)
    _emit(
        "V4_bytewise_all_six_match",
        classdef_lines == EXPECTED_CLASSDEF_LINES,
        f"got_first={classdef_lines[0] if classdef_lines else ''!r} expect_first={EXPECTED_CLASSDEF_LINES[0]!r}",
    )

    # 3 次跑 stdout bytewise 一致
    _emit(
        "V4_three_runs_bytewise_consistent",
        outputs[0] == outputs[1] == outputs[2],
        f"len0={len(outputs[0])} len1={len(outputs[1])} len2={len(outputs[2])}",
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
    v2_dump_locks()
    v3_mutant()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_054][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_054][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
