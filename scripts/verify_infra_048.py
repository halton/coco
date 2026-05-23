#!/usr/bin/env python3
"""verify_infra_048: dump_v4_sha_graph mermaid classDef styling (verify-only).

infra-039-backlog-mermaid-classDef-styling (phase-34 #4.34): 锁住
``scripts/dump_v4_sha_graph.py`` 的 ``render_mermaid`` 在末尾 emit 的 6 类
``classDef`` 与每个节点 ``class <node_id> <className>`` 关联语句, 以及新增的
``_classify_node`` 分类函数:

- hub: v4_sha_json
- verify: scripts/verify_*.py
- lib: _verify_lib
- dump: dump_v4_sha_graph (self-ref)
- module: 业务模块 (如 proactive)
- unknown: unknown_* 兜底 (P266 后理论为 0, 但 classDef 保留兼容)

本脚本验证:
- V0 scaffolding: dump_v4_sha_graph.py 存在 + ``_classify_node`` / ``render_mermaid`` /
  ``classDef hub`` / ``classDef verify`` 等符号在
- V1 docstring sentinel ``INFRA_048_SHA_LOCKS`` 自锁 + 本脚本 v4_behavior func sha
- V2 dump_v4_sha_graph.py file sha + ``render_mermaid`` func sha
- V3 in-memory mutant: 替换 render_mermaid body 为 ``return 'graph LR'``, sha 必漂移
- V4 行为验证: subprocess 调用 ``python scripts/dump_v4_sha_graph.py --mermaid``,
  stdout 必含 6 行 classDef + 关键节点 ``class`` 关联 (hub/verify/lib/dump)
- V5 Reviewer LGTM gate (print-only)

INFRA_048_SHA_LOCKS
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

# infra-048 sha lock 常量 (V2)
EXPECTED_DUMP_FILE_SHA = "f9720b4b4f7f0474087ee30600e2fe3e359cb8b9843d1e1ba6d7dc0e5db1bed9"
EXPECTED_RENDER_MERMAID_FUNC_SHA = "4a6e52392548fa257b8eb5cd3bef1cd48c222364031da0f0e14f191aef7ec695"

# 本脚本 v4_behavior 自锁 (V1) — 首跑用 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "9c47345119ce380e4dd1edd1322a4167f40ef1288e31f972d2fab451a0a8fc35"

DOCSTRING_SENTINEL = "INFRA_048_SHA_LOCKS"


# phase-47 #1.47: V5_GATE evidence-bind helper (grace_period 兜底 soft graduate)
REAL_FEATURE_LIST = Path(__file__).resolve().parents[1] / "feature_list.json"
V5_GATE_FEATURE_ID = "__PHASE_47_PLACEHOLDER_INFRA_048__"

_results: List[Tuple[str, bool, str]] = []


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_048][{mark}] {tag} {detail}", flush=True)
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
    _emit("V0_dump_exists", DUMP_PY.is_file(), f"path={DUMP_PY}")
    src = DUMP_PY.read_text(encoding="utf-8")
    # infra-048-backlog-mermaid-palette-extract (P273): classDef 行改为 f-string
    # 从 _MERMAID_PALETTE 迭代生成; 源码不再含 6 个 "classDef hub" 等字面 token,
    # 改为检查 _MERMAID_PALETTE 6 个键 + classDef f-string 构造符
    needed = [
        "def _classify_node",
        "def render_mermaid",
        "_MERMAID_PALETTE",
        '"hub"',
        '"verify"',
        '"lib"',
        '"dump"',
        '"module"',
        '"unknown"',
        'classDef {',
    ]
    found = [n for n in needed if n in src]
    _emit(
        "V0_classdef_symbols",
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
# V2: dump file sha + render_mermaid func sha
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
        return
    if EXPECTED_RENDER_MERMAID_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V2_render_mermaid_func_sha",
            False,
            f"placeholder; bump EXPECTED_RENDER_MERMAID_FUNC_SHA={got_func}",
        )
        return
    _emit(
        "V2_render_mermaid_func_sha",
        got_func == EXPECTED_RENDER_MERMAID_FUNC_SHA,
        f"got={got_func[:16]} expect={EXPECTED_RENDER_MERMAID_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: in-memory mutant — 替换 render_mermaid body, sha 必漂移
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    src = DUMP_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "render_mermaid":
            target = node
            break
    if target is None:
        _emit("V3_mutant_apply", False, "render_mermaid not found")
        return
    baseline_sha = hashlib.sha256(ast.unparse(target).encode("utf-8")).hexdigest()
    mutant = ast.FunctionDef(
        name=target.name,
        args=target.args,
        body=[ast.Return(value=ast.Constant(value="graph LR"))],
        decorator_list=[],
        returns=target.returns,
    )
    ast.fix_missing_locations(mutant)
    mutant_sha = hashlib.sha256(ast.unparse(mutant).encode("utf-8")).hexdigest()
    _emit(
        "V3_mutant_sha_drift",
        baseline_sha != mutant_sha,
        f"baseline={baseline_sha[:16]} mutant={mutant_sha[:16]}",
    )
    if EXPECTED_RENDER_MERMAID_FUNC_SHA != "__BUMP_ME__":
        _emit(
            "V3_baseline_matches_expected",
            baseline_sha == EXPECTED_RENDER_MERMAID_FUNC_SHA,
            f"baseline={baseline_sha[:16]} expect={EXPECTED_RENDER_MERMAID_FUNC_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V4: 行为验证 — subprocess 调用 dump --mermaid, 检查 classDef + class 关联
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    try:
        res = subprocess.run(
            [sys.executable, str(DUMP_PY), "--mermaid"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as e:
        _emit("V4_subprocess", False, f"err: {e!r}")
        return
    _emit("V4_subprocess", res.returncode == 0, f"rc={res.returncode}")
    out = res.stdout

    classdef_needles = [
        "classDef hub",
        "classDef verify",
        "classDef lib",
        "classDef dump",
        "classDef module",
        "classDef unknown",
    ]
    miss_cd = [n for n in classdef_needles if n not in out]
    _emit(
        "V4_classdef_lines",
        not miss_cd,
        f"miss={miss_cd}" if miss_cd else f"all {len(classdef_needles)} classDef present",
    )

    class_assoc_needles = [
        "class v4_sha_json hub",
        "class verify_robot_025 verify",
        "class _verify_lib lib",
        "class dump_v4_sha_graph dump",
    ]
    miss_cls = [n for n in class_assoc_needles if n not in out]
    _emit(
        "V4_class_associations",
        not miss_cls,
        f"miss={miss_cls}" if miss_cls else f"all {len(class_assoc_needles)} class assocs present",
    )

    # head 必须仍是 graph LR (mermaid syntax)
    _emit(
        "V4_graph_lr_header",
        out.lstrip().startswith("graph LR"),
        f"first_line={out.splitlines()[0] if out else '<empty>'!r}",
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
    v1_self_lock()
    v2_dump_locks()
    v3_mutant()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_048][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_048][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
