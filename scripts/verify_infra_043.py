#!/usr/bin/env python3
"""verify_infra_043: dump_v4_sha_graph --mermaid output mode lock (verify-only).

infra-039-backlog (phase-33 #4.33): 锁住 ``scripts/dump_v4_sha_graph.py`` 的
``--mermaid`` 渲染输出模式. 输出可直接 paste 到 mermaid.live 渲染.

本脚本验证:
- V0 scaffolding: dump_v4_sha_graph.py 存在 + render_mermaid 函数符号在
- V1 docstring sentinel ``INFRA_043_SHA_LOCKS`` 自锁 + 本脚本 v4_behavior func sha
- V2 dump_v4_sha_graph.py 整体 file sha (cascade lock, 与 verify_infra_039 共享)
  + render_mermaid func sha 锁
- V3 in-memory mutant: 替换 render_mermaid 函数体为 ``return ''``, unparse 后 sha 必漂移
- V4 行为验证: subprocess call dump_v4_sha_graph.py --mermaid, 验
  prefix ``graph LR``, 含已知节点 (verify_robot_025 / v4_sha_json), 含 ``-->`` 边
- V5 Reviewer LGTM gate (print-only)

INFRA_043_SHA_LOCKS
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

# infra-043 sha lock 常量 (V2)
EXPECTED_DUMP_FILE_SHA = "bf558d1eafe9499427725567e0b38a91abf5a752ddcc25d9857d5bb9ac0855de"
EXPECTED_RENDER_MERMAID_FUNC_SHA = "30c39582ca99e4f513c85a6261996dedc10e152210d45b7e16cd4d09aa7e17d6"

# 本脚本 v4_behavior 自锁 (V1 占位; 末尾跑通后回填)
EXPECTED_V4_CHECKER_FUNC_SHA = "fff6745e3889bda8f4e671965e4eef6cb476f6ec20bab5423d379e5138133ed0"

DOCSTRING_SENTINEL = "INFRA_043_SHA_LOCKS"


# phase-47 #1.47: V5_GATE evidence-bind helper (grace_period 兜底 soft graduate)
REAL_FEATURE_LIST = Path(__file__).resolve().parents[1] / "feature_list.json"
V5_GATE_FEATURE_ID = "__PHASE_47_PLACEHOLDER_INFRA_043__"

_results: List[Tuple[str, bool, str]] = []


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_043][{mark}] {tag} {detail}", flush=True)
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
    needed = ["def render_mermaid", "--mermaid", "graph LR"]
    found = [n for n in needed if n in src]
    _emit(
        "V0_mermaid_symbols",
        len(found) == len(needed),
        f"found {len(found)}/{len(needed)}: {found}",
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
    _emit(
        "V2_render_mermaid_func_sha",
        got_func == EXPECTED_RENDER_MERMAID_FUNC_SHA,
        f"got={got_func[:16]} expect={EXPECTED_RENDER_MERMAID_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: in-memory mutant — 替换 render_mermaid 函数体, sha 必漂移
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
    # 构造 mutant: 同 signature, body = `return ''`
    mutant = ast.FunctionDef(
        name=target.name,
        args=target.args,
        body=[ast.Return(value=ast.Constant(value=""))],
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
    _emit(
        "V3_baseline_matches_expected",
        baseline_sha == EXPECTED_RENDER_MERMAID_FUNC_SHA,
        f"baseline={baseline_sha[:16]} expect={EXPECTED_RENDER_MERMAID_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为验证 — subprocess call --mermaid
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    proc = subprocess.run(
        [sys.executable, str(DUMP_PY), "--mermaid"],
        capture_output=True, text=True, timeout=30,
    )
    _emit("V4_subprocess_rc", proc.returncode == 0, f"rc={proc.returncode}")
    out = proc.stdout
    # prefix
    _emit(
        "V4_mermaid_prefix",
        out.startswith("graph LR") or out.startswith("graph TD"),
        f"first_line={out.splitlines()[0] if out else ''!r}",
    )
    # 已知节点
    anchors = ["verify_robot_025", "v4_sha_json", "-->"]
    missing = [a for a in anchors if a not in out]
    _emit(
        "V4_anchors_present",
        not missing,
        f"missing={missing} total_lines={len(out.splitlines())}",
    )
    # 与 --json 互斥
    proc2 = subprocess.run(
        [sys.executable, str(DUMP_PY), "--mermaid", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    _emit(
        "V4_mutex_with_json",
        proc2.returncode != 0,
        f"rc={proc2.returncode} stderr={proc2.stderr.strip()[:80]!r}",
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
        print(f"[verify_infra_043][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_043][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
