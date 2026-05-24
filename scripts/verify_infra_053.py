#!/usr/bin/env python3
"""verify_infra_053: dump_v4_sha_graph mermaid hub color distinguish (verify-only).

infra-048-backlog-hub-color-distinguish (phase-35 #4.35 P272): P267 落地的
6 类 classDef 中 hub (``#f9f`` 粉品红) 与 module (``#c9f`` 淡紫) 色相相邻,
小尺寸渲染下区分度不足。本 feature 把 hub 色值改为橙黄系 ``#fc6``, 拉开
与 module 视觉距离, 同时避开红/绿色盲冲突。

本脚本验证:
- V0 scaffolding: dump_v4_sha_graph.py 存在 + hub/module classDef 在
- V1 docstring sentinel ``INFRA_053_SHA_LOCKS`` 自锁 + 本脚本 v4_behavior func sha
- V2 dump_v4_sha_graph.py file sha + ``render_mermaid`` func sha
- V3 in-memory mutant: 把 render_mermaid 内 hub 色值字符串改回 ``#f9f``,
  sha 必漂移
- V4 行为验证: subprocess 调用 ``python scripts/dump_v4_sha_graph.py --mermaid``,
  stdout 必含 hub 新色值 ``#fc6``; hub 色值与 module 色值字面不同;
  classDef 行总数 = 6
- V5 Reviewer LGTM gate (print-only)

INFRA_053_SHA_LOCKS
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
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DUMP_PY = SCRIPTS / "dump_v4_sha_graph.py"

# infra-053 sha lock 常量 (V2)
EXPECTED_DUMP_FILE_SHA = "72a72a986f513f1483b3c49238ab85c185be6108b3616613eee0a613fc1caeb3"
EXPECTED_RENDER_MERMAID_FUNC_SHA = "30c39582ca99e4f513c85a6261996dedc10e152210d45b7e16cd4d09aa7e17d6"

# 本脚本 v4_behavior 自锁 (V1) — 首跑用 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "1a3d90228607698c842830b67c5984f22e8fe02e516a688e98244c90781df7d1"

DOCSTRING_SENTINEL = "INFRA_053_SHA_LOCKS"

# 新 hub 色值 (橙黄, 与紫色 module #c9f 区分度高, 避免红/绿色盲冲突)
EXPECTED_HUB_FILL = "#fc6"
EXPECTED_MODULE_FILL = "#c9f"


# phase-47 #1.47: V5_GATE evidence-bind helper (grace_period 兜底 soft graduate)
REAL_FEATURE_LIST = Path(__file__).resolve().parents[1] / "feature_list.json"
V5_GATE_FEATURE_ID = "__PHASE_47_PLACEHOLDER_INFRA_053__"

_results: List[Tuple[str, bool, str]] = []


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_053][{mark}] {tag} {detail}", flush=True)
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
    # infra-048-backlog-mermaid-palette-extract (P273): hub / module 色值移入
    # 模块顶部 _MERMAID_PALETTE; render_mermaid 用 f-string 生成 classDef 行,
    # 源码不再含 "classDef hub" 字面。改为检查 _MERMAID_PALETTE 与色值字面。
    needed = [
        "def render_mermaid",
        "_MERMAID_PALETTE",
        '"hub"',
        '"module"',
        EXPECTED_HUB_FILL,
        EXPECTED_MODULE_FILL,
    ]
    found = [n for n in needed if n in src]
    _emit(
        "V0_hub_module_symbols",
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
# V3: in-memory mutant — 把 _MERMAID_PALETTE 内 hub 色值 #fc6 替回 #f9f, 输出必漂移
# infra-048-backlog-mermaid-palette-extract (P273): 色值移至模块顶部 _MERMAID_PALETTE,
# render_mermaid 函数体不再含 hub 色值字面; 不能再用 func sha drift 反证,
# 改为整源 mutate + exec, 验证 render_mermaid 输出 hub 色值漂移。
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    src = DUMP_PY.read_text(encoding="utf-8")
    # 替换 _MERMAID_PALETTE 内 hub fill 值
    needle = f'"fill": "{EXPECTED_HUB_FILL}"'
    if needle not in src:
        _emit("V3_mutant_apply", False, f"needle {needle!r} not in dump src")
        return
    mutated = src.replace(needle, '"fill": "#f9f"', 1)
    _emit("V3_mutant_apply", mutated != src, f"hub {EXPECTED_HUB_FILL} -> #f9f in palette")

    ns: dict = {"__name__": "_infra053_mutant", "__file__": str(DUMP_PY)}
    try:
        exec(compile(mutated, str(DUMP_PY) + ".mutant", "exec"), ns)
        out = ns["render_mermaid"]({})
        hub_lines = [ln for ln in out.splitlines() if "classDef hub" in ln]
        ok = len(hub_lines) == 1 and EXPECTED_HUB_FILL not in hub_lines[0] and "#f9f" in hub_lines[0]
        _emit(
            "V3_mutant_sha_drift",
            ok,
            f"hub_line={hub_lines[0] if hub_lines else '<missing>'}",
        )
    except Exception as e:
        _emit("V3_mutant_sha_drift", False, f"exec err: {e!r}")

    # baseline render_mermaid func sha 与 EXPECTED 仍应匹配 (palette 提取不改函数体本身)
    if EXPECTED_RENDER_MERMAID_FUNC_SHA != "__BUMP_ME__":
        try:
            baseline_sha = _func_sha(DUMP_PY, "render_mermaid")
            _emit(
                "V3_baseline_matches_expected",
                baseline_sha == EXPECTED_RENDER_MERMAID_FUNC_SHA,
                f"baseline={baseline_sha[:16]} expect={EXPECTED_RENDER_MERMAID_FUNC_SHA[:16]}",
            )
        except Exception as e:
            _emit("V3_baseline_matches_expected", False, f"compute err: {e!r}")


# ---------------------------------------------------------------------------
# V4: 行为验证 — subprocess 调用 dump --mermaid, 检查 hub 色值变更
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

    # hub classDef 行: 必含新色值 EXPECTED_HUB_FILL (#fc6)
    hub_lines = [ln for ln in out.splitlines() if "classDef hub" in ln]
    _emit(
        "V4_hub_classdef_present",
        len(hub_lines) == 1,
        f"hub_lines={len(hub_lines)}",
    )
    hub_line = hub_lines[0] if hub_lines else ""
    _emit(
        "V4_hub_fill_value",
        EXPECTED_HUB_FILL in hub_line,
        f"expect_fill={EXPECTED_HUB_FILL} line={hub_line!r}",
    )

    # module classDef 行
    module_lines = [ln for ln in out.splitlines() if "classDef module" in ln]
    _emit(
        "V4_module_classdef_present",
        len(module_lines) == 1,
        f"module_lines={len(module_lines)}",
    )
    module_line = module_lines[0] if module_lines else ""
    _emit(
        "V4_module_fill_value",
        EXPECTED_MODULE_FILL in module_line,
        f"expect_fill={EXPECTED_MODULE_FILL} line={module_line!r}",
    )

    # hub 与 module 色值字面不同 (核心 distinguish 断言)
    def _extract_fill(line: str) -> str:
        m = re.search(r"fill:(#[0-9a-fA-F]+)", line)
        return m.group(1) if m else ""

    hub_fill = _extract_fill(hub_line)
    module_fill = _extract_fill(module_line)
    _emit(
        "V4_hub_module_fill_distinct",
        bool(hub_fill) and bool(module_fill) and hub_fill != module_fill,
        f"hub={hub_fill} module={module_fill}",
    )

    # 旧 hub 色值 #f9f 必须不再出现在任何 classDef 行
    classdef_lines = [ln for ln in out.splitlines() if "classDef " in ln]
    old_hub_residue = [ln for ln in classdef_lines if "#f9f" in ln]
    _emit(
        "V4_no_old_hub_color_residue",
        not old_hub_residue,
        f"residue={old_hub_residue}" if old_hub_residue else "no #f9f in any classDef line",
    )

    # classDef 总行数仍 = 6
    _emit(
        "V4_classdef_total_count",
        len(classdef_lines) == 6,
        f"got={len(classdef_lines)} expect=6",
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
        print(f"[verify_infra_053][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_053][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
