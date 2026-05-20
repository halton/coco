#!/usr/bin/env python3
"""verify_infra_039: scripts/dump_v4_sha_graph.py V4 sha-lock graph dump 工具加固 (verify-only).

infra-039: 引入 ``scripts/dump_v4_sha_graph.py`` — V4 sha-lock 链路 DAG 可视化工具,
扫描所有 ``scripts/verify_*.py`` + ``scripts/_verify_lib.py`` 抽取 sha-lock 常量,
并尝试推断锁定 target (v4_sha.json 表 / 交叉 verify 锁 / lib file 或 func 锁),
方便 closeout / Reviewer 一眼看清整套链路。本脚本锁住 dump 工具的存在、本脚本
docstring sentinel + 自 checker func sha、dump 工具 file sha、mutant 反证、
行为验证。

V0 scaffolding: dump_v4_sha_graph.py 存在 + importable, 含 6 个关键符号
   (build_graph / render_text / _scan_file / _infer_target / main /
   _RE_SINGLELINE / _RE_TUPLE_OPEN 任 6 项均算 PASS, 最少 5)。
V1 docstring sentinel + 本脚本 v4_behavior checker 自身 func sha 自锁
   (sentinel section ``INFRA_039_SHA_LOCKS`` 必须存在).
V2 dump_v4_sha_graph.py 整体 file-sha 锁 (避免无脑改 dump).
V3 mutant 反证 — 临时把 dump 的 ``_RE_SINGLELINE`` 改成永不匹配的 regex,
   subprocess 运行 dump, 输出 locks 计数应比 baseline 显著减少, finally 还原.
V4 行为验证 — subprocess 调 dump, grep 输出含若干已知锁
   ("v4_sha.json", "15 targets", "verify_infra_034.py", "verify_infra_037.py").
V5 Reviewer-LGTM gate (print-only).

INFRA_039_SHA_LOCKS
-------------------
- ``scripts/dump_v4_sha_graph.py`` file sha: EXPECTED_DUMP_FILE_SHA
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

# infra-039 sha lock 常量 (V2): dump_v4_sha_graph.py 整体 file sha
EXPECTED_DUMP_FILE_SHA = "c2be81bec69402521013df4c7e20e65d72204f0165893685d6cbbfc8d1e65c85"

# infra-039 自身 v4_behavior 函数 sha (V1 自锁, 占位; 末尾自计算后回填)
EXPECTED_V4_CHECKER_FUNC_SHA = "7ae405196b8a92f2191d92871cdd8278dcbdcc43d7a4ac21e71b76dcd7084fea"

DOCSTRING_SENTINEL = "INFRA_039_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_039][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _func_sha_via_unparse(path: Path, func_name: str) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return hashlib.sha256(ast.unparse(node).encode("utf-8")).hexdigest()
    raise RuntimeError(f"func {func_name!r} not found in {path}")


# ---------------------------------------------------------------------------
# V0: scaffolding — dump 存在 + 关键符号
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    if not DUMP_PY.is_file():
        _emit("V0_dump_exists", False, f"missing {DUMP_PY}")
        return
    _emit("V0_dump_exists", True, str(DUMP_PY))
    src = DUMP_PY.read_text(encoding="utf-8")
    needed = [
        "def build_graph",
        "def render_text",
        "def _scan_file",
        "def _infer_target",
        "def main",
        "_RE_SINGLELINE",
        "_RE_TUPLE_OPEN",
    ]
    found = [n for n in needed if n in src]
    _emit(
        "V0_dump_symbols",
        len(found) >= 6,
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
        got = _func_sha_via_unparse(self_path, "v4_behavior")
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
# V2: dump 自身 file sha 锁
# ---------------------------------------------------------------------------
def v2_dump_file_sha() -> None:
    got = _file_sha(DUMP_PY)
    _emit(
        "V2_dump_file_sha",
        got == EXPECTED_DUMP_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_DUMP_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: mutant 反证 — 改坏 _RE_SINGLELINE 让其匹配为空
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    original = DUMP_PY.read_text(encoding="utf-8")
    # 改 single-line regex 让其永不匹配 (要求行以 # 起首 + 严格不可能模式)
    needle = '_RE_SINGLELINE = re.compile('
    if needle not in original:
        _emit("V3_mutant_apply", False, "no _RE_SINGLELINE compile site found")
        return
    mutant = original.replace(
        '_RE_SINGLELINE = re.compile(\n    r\'^([A-Z_][A-Z0-9_]*)\\s*=\\s*["\\\']([0-9a-f]{64})["\\\']\\s*(?:#.*)?$\'\n)',
        '_RE_SINGLELINE = re.compile(r"^__ZZZ_NEVER_MATCH_INFRA_039__$")',
        1,
    )
    if mutant == original:
        # fallback: 用更宽容 anchor 注入
        mutant = original.replace(
            '_RE_SINGLELINE = re.compile(',
            '_RE_SINGLELINE = re.compile("^__ZZZ_NEVER_MATCH__$"); _RE_SINGLELINE_ORIG = re.compile(',
            1,
        )
    if mutant == original:
        _emit("V3_mutant_apply", False, "could not inject mutant regex")
        return
    baseline_locks = _count_locks_via_subprocess()
    try:
        DUMP_PY.write_text(mutant, encoding="utf-8")
        mut_locks = _count_locks_via_subprocess()
        # mutant 应显著减少 locks (因为 single-line 锁占大多数)
        _emit(
            "V3_mutant_detected",
            mut_locks < baseline_locks,
            f"baseline={baseline_locks} mutant={mut_locks}",
        )
    finally:
        DUMP_PY.write_text(original, encoding="utf-8")


def _count_locks_via_subprocess() -> int:
    """subprocess 跑 dump --json, 数 locks 数."""
    try:
        out = subprocess.run(
            [sys.executable, str(DUMP_PY), "--json"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if out.returncode != 0:
            return -1
        import json as _json
        graph = _json.loads(out.stdout)
        return len(graph.get("locks", []))
    except Exception:
        return -1


# ---------------------------------------------------------------------------
# V4: 行为验证 — subprocess 跑 dump, grep 输出含已知锚点
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    try:
        out = subprocess.run(
            [sys.executable, str(DUMP_PY)],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except Exception as e:
        _emit("V4_subprocess_run", False, f"err: {e!r}")
        return
    _emit("V4_subprocess_run", out.returncode == 0, f"rc={out.returncode}")
    text = out.stdout
    anchors = [
        "v4_sha.json",
        "15 targets",
        "verify_infra_034.py",
        "verify_infra_037.py",
        "=== V4 SHA-LOCK GRAPH ===",
        "SUMMARY:",
    ]
    missing = [a for a in anchors if a not in text]
    _emit(
        "V4_output_anchors",
        not missing,
        f"missing={missing}" if missing else f"all {len(anchors)} anchors found",
    )
    # JSON 模式也应跑通
    try:
        out_j = subprocess.run(
            [sys.executable, str(DUMP_PY), "--json"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        import json as _json
        graph = _json.loads(out_j.stdout)
        ok = (
            out_j.returncode == 0
            and isinstance(graph.get("locks"), list)
            and isinstance(graph.get("v4_sha_json"), dict)
        )
        _emit(
            "V4_json_mode",
            ok,
            f"rc={out_j.returncode} locks={len(graph.get('locks', []))}",
        )
    except Exception as e:
        _emit("V4_json_mode", False, f"err: {e!r}")


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (print-only)
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
    v3_mutant()
    v4_behavior()
    v5_reviewer_gate()
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_039][SUMMARY] FAILED tags: {failed}", flush=True)
        return 1
    print(f"[verify_infra_039][SUMMARY] ALL PASS ({len(_results)} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
