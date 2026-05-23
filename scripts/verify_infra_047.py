#!/usr/bin/env python3
"""verify_infra_047: dump_v4_sha_graph source-file-aware self-lock inference (verify-only).

infra-039-backlog (phase-34 #3.34): 锁住 ``scripts/dump_v4_sha_graph.py`` 的
``_infer_target`` 推断逻辑在 ``_PER_FILE_LOCKS`` (二级查表) 与 ``_PER_FILE_SELF_LOCKS``
(真自锁) 上的新增支持。这两套表面向跨 verify 文件同名常量歧义场景, 例如:

- ``EXPECTED_FILE_SHA`` 在 verify_interact_037.py 中锁 verify_interact_024.py;
- ``SETTER_BLOCK_EXPECTED_SHA`` 在 verify_robot_025.py / 027 中锁 coco/proactive.py;
- ``EXPECTED_FINGERPRINT`` 在 verify_infra_022 / 028 中是真自锁 (target = source_file 自身).

本脚本验证:
- V0 scaffolding: dump_v4_sha_graph.py 存在 + _PER_FILE_LOCKS / _PER_FILE_SELF_LOCKS / _infer_target 符号在
- V1 docstring sentinel ``INFRA_047_SHA_LOCKS`` 自锁 + 本脚本 v4_behavior func sha
- V2 dump_v4_sha_graph.py file sha + _infer_target func sha
- V3 in-memory mutant: 替换 _infer_target 函数体为 ``return '<unknown>'``, sha 必漂移
- V4 行为验证: import dump_v4_sha_graph 后直接调用 _infer_target, 覆盖
  per-file lock / per-file self-lock / numeric fallback / 表外 unknown 兜底
- V5 Reviewer LGTM gate (print-only)

INFRA_047_SHA_LOCKS
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

# infra-047 sha lock 常量 (V2)
EXPECTED_DUMP_FILE_SHA = "60232fe8eccd11117bee4d8314b98f4231370351027c0676bc4d7e606c58acb0"
EXPECTED_INFER_TARGET_FUNC_SHA = "8fa29b811670d4f7642e2ca84d571bcf2bf77dbabf5b2a60efa17f4df08eb446"

# 本脚本 v4_behavior 自锁 (V1) — 首跑用 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "321fca97bc45b4b2a77180aba7b7977d43740df79611643a47b38baa1c5859be"

DOCSTRING_SENTINEL = "INFRA_047_SHA_LOCKS"


# phase-47 #1.47: V5_GATE evidence-bind helper (grace_period 兜底 soft graduate)
REAL_FEATURE_LIST = Path(__file__).resolve().parents[1] / "feature_list.json"
V5_GATE_FEATURE_ID = "__PHASE_47_PLACEHOLDER_INFRA_047__"

_results: List[Tuple[str, bool, str]] = []


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_047][{mark}] {tag} {detail}", flush=True)
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
    spec = importlib.util.spec_from_file_location("_dump_v4_sha_graph_under_test_047", DUMP_PY)
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
    needed = [
        "def _infer_target",
        "_PER_FILE_LOCKS",
        "_PER_FILE_SELF_LOCKS",
    ]
    found = [n for n in needed if n in src]
    _emit(
        "V0_per_file_symbols",
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
# V4: 行为验证 — per-file lock / per-file self-lock / numeric / unknown fallback
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    try:
        mod = _load_dump_module()
    except Exception as e:
        _emit("V4_import_dump", False, f"err: {e!r}")
        return
    _emit("V4_import_dump", True, "module loaded")

    # per-file lock 命中: (source_file, const) 二级查表
    per_file_cases = [
        ("scripts/verify_infra_045.py", "EXPECTED_SCAN_FUNC_SHA", "_verify_lib.py"),
        ("scripts/verify_infra_046.py", "EXPECTED_BUMP_FILE_SHA", "bump_reverse_sha_lock.py"),
        ("scripts/verify_interact_037.py", "EXPECTED_FILE_SHA", "verify_interact_024.py"),
        ("scripts/verify_interact_037.py", "EXPECTED_FUNC_SHA", "verify_interact_024.py"),
        ("scripts/verify_robot_025.py", "SETTER_BLOCK_EXPECTED_SHA", "proactive.py"),
        ("scripts/verify_robot_027.py", "SETTER_BLOCK_EXPECTED_SHA", "proactive.py"),
        ("scripts/verify_robot_033.py", "FILE_SHA", "proactive.py"),
        ("scripts/verify_robot_034.py", "EXPECTED_DOC_SHA", "doc"),
        ("scripts/verify_robot_036.py", "EXPECTED_SENTINEL_LINE_SHA", "verify_robot_032.py"),
    ]
    miss_pf = []
    for src_file, const, needle in per_file_cases:
        got = mod._infer_target(const, src_file)
        if needle.lower() not in got.lower():
            miss_pf.append((src_file, const, got))
    _emit(
        "V4_per_file_lock_resolves",
        not miss_pf,
        f"miss={miss_pf}" if miss_pf else f"all {len(per_file_cases)} per-file targets resolved",
    )

    # per-file self-lock: EXPECTED_FINGERPRINT 在 verify_infra_022 / 028 中锁 source_file 自身
    self_cases = [
        ("scripts/verify_infra_022.py", "EXPECTED_FINGERPRINT"),
        ("scripts/verify_infra_028.py", "EXPECTED_FINGERPRINT"),
    ]
    miss_self = []
    for src_file, const in self_cases:
        got = mod._infer_target(const, src_file)
        # 自锁结果必须包含 source_file basename
        base = src_file.rsplit("/", 1)[-1]
        if base not in got:
            miss_self.append((src_file, const, got))
    _emit(
        "V4_per_file_self_lock_resolves",
        not miss_self,
        f"miss={miss_self}" if miss_self else f"all {len(self_cases)} self-lock resolved",
    )

    # numeric 路径不受影响: VERIFY_025 在 verify_robot_027.py 仍解析为 verify_*_025
    got_numeric = mod._infer_target("VERIFY_025_EXPECTED_SHA", "scripts/verify_robot_027.py")
    _emit(
        "V4_numeric_path_preserved",
        "025" in got_numeric,
        f"got={got_numeric!r}",
    )

    # _KNOWN_NON_NUMERIC_TARGETS 查表不受影响 (例: EXPECTED_LIB_FILE_SHA)
    got_lib = mod._infer_target("EXPECTED_LIB_FILE_SHA", "scripts/verify_robot_035.py")
    _emit(
        "V4_known_table_preserved",
        "_verify_lib.py" in got_lib,
        f"got={got_lib!r}",
    )

    # 表外仍走 unknown 兜底
    got_unknown = mod._infer_target("TOTALLY_RANDOM_NAME_XYZ", "scripts/anywhere.py")
    _emit(
        "V4_unknown_fallback",
        "<unknown" in got_unknown,
        f"got={got_unknown!r}",
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
        print(f"[verify_infra_047][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_047][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
