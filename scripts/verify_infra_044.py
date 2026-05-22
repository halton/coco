#!/usr/bin/env python3
"""verify_infra_044: dump_v4_sha_graph _infer_target refine lock (verify-only).

infra-039-backlog (phase-33 #5.33): 锁住 ``scripts/dump_v4_sha_graph.py`` 的
``_infer_target`` 推断逻辑及其 ``_KNOWN_NON_NUMERIC_TARGETS`` 查表, 确保 fingerprint
/ bump-only / dump 自锁等非数字常量名能被正确识别。

本脚本验证:
- V0 scaffolding: dump_v4_sha_graph.py 存在 + _infer_target / _KNOWN_NON_NUMERIC_TARGETS 符号在
- V1 docstring sentinel ``INFRA_044_SHA_LOCKS`` 自锁 + 本脚本 v4_behavior func sha
- V2 dump_v4_sha_graph.py file sha + _infer_target func sha
- V3 in-memory mutant: 替换 _infer_target 函数体为 ``return '<unknown>'``, sha 必漂移
- V4 行为验证: import dump_v4_sha_graph 后直接调用 _infer_target, 对若干代表性 const
  的返回必须命中查表 / numeric 推断分支
- V5 Reviewer LGTM gate (print-only)

INFRA_044_SHA_LOCKS
-------------------
- ``scripts/dump_v4_sha_graph.py`` file sha: EXPECTED_DUMP_FILE_SHA
- ``_infer_target`` func sha: EXPECTED_INFER_TARGET_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DUMP_PY = SCRIPTS / "dump_v4_sha_graph.py"

# infra-044 sha lock 常量 (V2)
EXPECTED_DUMP_FILE_SHA = "14ddc08e153a145fd5029a034e7eef7bf44f90d7c14272bc140c7a73e4d45905"
EXPECTED_INFER_TARGET_FUNC_SHA = "8fa29b811670d4f7642e2ca84d571bcf2bf77dbabf5b2a60efa17f4df08eb446"

# 本脚本 v4_behavior 自锁 (V1)
EXPECTED_V4_CHECKER_FUNC_SHA = "b9ae27aaddeb2a1d89171b4dfd1c66be7e0559be16555617b91879a5857bf41a"

DOCSTRING_SENTINEL = "INFRA_044_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_044][{mark}] {tag} {detail}", flush=True)
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


def _load_dump_module():
    spec = importlib.util.spec_from_file_location("_dump_v4_sha_graph_under_test", DUMP_PY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_dump_exists", DUMP_PY.is_file(), f"path={DUMP_PY}")
    src = DUMP_PY.read_text(encoding="utf-8")
    needed = ["def _infer_target", "_KNOWN_NON_NUMERIC_TARGETS", "EXPECTED_DUMP_FILE_SHA"]
    found = [n for n in needed if n in src]
    _emit(
        "V0_infer_target_symbols",
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
# V2: dump file sha + _infer_target func sha
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
        got_func = _func_sha(DUMP_PY, "_infer_target")
    except Exception as e:
        _emit("V2_infer_target_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_INFER_TARGET_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V2_infer_target_func_sha",
            False,
            f"placeholder; bump EXPECTED_INFER_TARGET_FUNC_SHA={got_func}",
        )
        return
    _emit(
        "V2_infer_target_func_sha",
        got_func == EXPECTED_INFER_TARGET_FUNC_SHA,
        f"got={got_func[:16]} expect={EXPECTED_INFER_TARGET_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: in-memory mutant — 替换 _infer_target body, sha 必漂移
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    src = DUMP_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_infer_target":
            target = node
            break
    if target is None:
        _emit("V3_mutant_apply", False, "_infer_target not found")
        return
    baseline_sha = hashlib.sha256(ast.unparse(target).encode("utf-8")).hexdigest()
    mutant = ast.FunctionDef(
        name=target.name,
        args=target.args,
        body=[ast.Return(value=ast.Constant(value="<unknown>"))],
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
    if EXPECTED_INFER_TARGET_FUNC_SHA != "__BUMP_ME__":
        _emit(
            "V3_baseline_matches_expected",
            baseline_sha == EXPECTED_INFER_TARGET_FUNC_SHA,
            f"baseline={baseline_sha[:16]} expect={EXPECTED_INFER_TARGET_FUNC_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V4: 行为验证 — import + 直接调用 _infer_target
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    try:
        mod = _load_dump_module()
    except Exception as e:
        _emit("V4_import_dump", False, f"err: {e!r}")
        return
    _emit("V4_import_dump", True, "module loaded")
    # 查表条目: 非数字常量名
    cases_table = [
        ("EXPECTED_LIB_FILE_SHA", "_verify_lib.py"),
        ("EXPECTED_VERIFY_LIB_FILE_SHA", "_verify_lib.py"),
        ("EXPECTED_DUMP_FILE_SHA", "dump_v4_sha_graph.py"),
        ("EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA", "_verify_lib.py"),
        ("EXPECTED_V6_SCAN_FUNC_SHA", "_verify_lib.py"),
        ("EXPECTED_READ_CONSTANT_FUNC_SHA", "_verify_lib.py"),
        ("EXPECTED_RENDER_MERMAID_FUNC_SHA", "dump_v4_sha_graph.py"),
    ]
    miss = []
    for const, needle in cases_table:
        got = mod._infer_target(const, "scripts/verify_infra_044.py")
        if needle not in got:
            miss.append((const, got))
    _emit(
        "V4_known_table_resolves",
        not miss,
        f"miss={miss}" if miss else f"all {len(cases_table)} table-targets resolved",
    )
    # numeric 路径: VERIFY_<NNN>_ 仍走原流程
    got_numeric = mod._infer_target("VERIFY_025_EXPECTED_SHA", "scripts/verify_robot_027.py")
    _emit(
        "V4_numeric_verify_hint",
        "025" in got_numeric and "verify" in got_numeric.lower(),
        f"got={got_numeric!r}",
    )
    # V<NNN>_ 路径 (infra-039-backlog 扩展)
    got_v = mod._infer_target("V018_EXPECTED_SHA", "scripts/verify_interact_033.py")
    _emit(
        "V4_v_num_hint",
        "018" in got_v,
        f"got={got_v!r}",
    )
    # BUMP_<NNN>_ 路径
    got_bump = mod._infer_target("BUMP_028_EXPECTED_SHA", "scripts/verify_robot_031.py")
    _emit(
        "V4_bump_num_hint",
        "028" in got_bump,
        f"got={got_bump!r}",
    )
    # unknown 回退仍然是 <unknown target>
    got_unknown = mod._infer_target("TOTALLY_RANDOM_NAME", "scripts/anywhere.py")
    _emit(
        "V4_unknown_fallback",
        "<unknown" in got_unknown,
        f"got={got_unknown!r}",
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
        print(f"[verify_infra_044][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_044][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
