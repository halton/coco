#!/usr/bin/env python3
"""verify_infra_050: dump_reverse_sha_lock_index.render_json sort 稳定性 meta-lock.

infra-049-backlog-render-json-sort-stability (phase-35 #1.35, P269): P268 落地的
``scripts/dump_reverse_sha_lock_index.py:render_json`` 输出 entries 列表, 但当前
``sort_keys=False``, entries 顺序依赖 build_entries / group_entries_by_file 的
隐式行为, 没有显式锁定. Reviewer 提议加 ``sort_order`` 字段显式标注约定,
并锁定 entries 的排序契约.

P269 改动 (上游):

- ``render_json`` 内显式 ``sorted(entries, key=lambda e: (e["file"], e["lineno"],
  e["const_name"]))``
- payload.stats 新增字段 ``"sort_order": "verify_file,lineno,lock_name"``

本 verify 锁住:

INFRA_050_SHA_LOCKS
-------------------
- ``scripts/dump_reverse_sha_lock_index.py`` file sha:
  EXPECTED_DUMP_INDEX_FILE_SHA (cascade 与 verify_infra_049 同步)
- ``render_json`` func sha: EXPECTED_RENDER_JSON_FUNC_SHA (cascade 同步)
- 本脚本 v4_behavior func sha: EXPECTED_V4_CHECKER_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: dump_reverse_sha_lock_index.py 含 ``sort_order`` 字符串
  与 ``"verify_file,lineno,lock_name"`` 常量
- V1 docstring sentinel ``INFRA_050_SHA_LOCKS`` + 本脚本 v4_behavior func sha 自锁
- V2 dump_reverse_sha_lock_index.py file sha + render_json func sha
- V3 in-memory mutant: 把 sort key tuple ``(file, lineno, const_name)`` 改成
  ``(file, const_name, lineno)``, AST sha 必漂移 (反证: 改 sort key 会被检出)
- V4 行为验证 (subprocess --json):
  - stats 块含 ``"sort_order": "verify_file,lineno,lock_name"``
  - entries 实际按 (file, lineno, const_name) 升序排列 (相邻两条非降)
  - 重复跑 3 次输出 bytewise 一致 (稳定性)
- V5 Reviewer LGTM gate (print-only)

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DUMP_INDEX_PY = SCRIPTS / "dump_reverse_sha_lock_index.py"

# cascade-locked with verify_infra_049 (P268/P269)
EXPECTED_DUMP_INDEX_FILE_SHA = "dc0944f842e98a96c71ed521ae47b23448b0aa539c0a411a4e9ac1c975d1344b"
EXPECTED_RENDER_JSON_FUNC_SHA = "5e1bf67b5c3d66d15b2e9f42b66c06e9bf16549361080b518221b8a2bc2d2798"

# 本脚本 v4_behavior 自锁 (V1) — 首跑 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "c24db73e5b331b261c91bc378ff87c527c5165f6425c75d114dd026656bbb69f"

DOCSTRING_SENTINEL = "INFRA_050_SHA_LOCKS"
SORT_ORDER_VALUE = "verify_file,lineno,lock_name"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_050][{mark}] {tag} {detail}", flush=True)
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
    _emit("V0_dump_index_exists", DUMP_INDEX_PY.is_file(), f"path={DUMP_INDEX_PY}")
    src = DUMP_INDEX_PY.read_text(encoding="utf-8")
    needed = [
        '"sort_order"',
        f'"{SORT_ORDER_VALUE}"',
        'sorted(',
        '"file"',
        '"lineno"',
        '"const_name"',
    ]
    found = [n for n in needed if n in src]
    _emit(
        "V0_sort_order_symbols",
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
# V2: dump_index file sha + render_json func sha (cascade)
# ---------------------------------------------------------------------------
def v2_dump_locks() -> None:
    got_file = _file_sha(DUMP_INDEX_PY)
    _emit(
        "V2_dump_index_file_sha",
        got_file == EXPECTED_DUMP_INDEX_FILE_SHA,
        f"got={got_file[:16]} expect={EXPECTED_DUMP_INDEX_FILE_SHA[:16]}",
    )
    try:
        got = _func_sha(DUMP_INDEX_PY, "render_json")
    except Exception as e:
        _emit("V2_render_json_func_sha", False, f"compute err: {e!r}")
        return
    _emit(
        "V2_render_json_func_sha",
        got == EXPECTED_RENDER_JSON_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_RENDER_JSON_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: in-memory mutant — 改 sort key tuple 顺序, sha 必漂移
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    src = DUMP_INDEX_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "render_json":
            target = node
            break
    if target is None:
        _emit("V3_mutant_apply", False, "render_json not found")
        return
    baseline_sha = hashlib.sha256(ast.unparse(target).encode("utf-8")).hexdigest()
    # mutant: 改 sort key 字段顺序 ("file","lineno","const_name") -> ("file","const_name","lineno")
    baseline_src = ast.unparse(target)
    mutant_src = baseline_src.replace(
        "(e['file'], e['lineno'], e['const_name'])",
        "(e['file'], e['const_name'], e['lineno'])",
    )
    # ast.unparse 用双引号或单引号取决于版本; 兼容两种
    if mutant_src == baseline_src:
        mutant_src = baseline_src.replace(
            '(e["file"], e["lineno"], e["const_name"])',
            '(e["file"], e["const_name"], e["lineno"])',
        )
    mutant_sha = hashlib.sha256(mutant_src.encode("utf-8")).hexdigest()
    _emit(
        "V3_mutant_sha_drift",
        baseline_sha != mutant_sha and mutant_src != baseline_src,
        f"baseline={baseline_sha[:16]} mutant={mutant_sha[:16]} replaced={mutant_src != baseline_src}",
    )
    _emit(
        "V3_baseline_matches_expected",
        baseline_sha == EXPECTED_RENDER_JSON_FUNC_SHA,
        f"baseline={baseline_sha[:16]} expect={EXPECTED_RENDER_JSON_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为验证 — subprocess --json sort_order + 排序 + bytewise 稳定
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    runs: List[str] = []
    for i in range(3):
        try:
            r = subprocess.run(
                [sys.executable, str(DUMP_INDEX_PY), "--json"],
                cwd=str(REPO),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as e:
            _emit(f"V4_json_run{i}", False, f"err: {e!r}")
            return
        if r.returncode != 0:
            _emit(f"V4_json_run{i}", False, f"rc={r.returncode} stderr={r.stderr[:200]!r}")
            return
        _emit(f"V4_json_run{i}", True, f"len={len(r.stdout)}")
        runs.append(r.stdout)

    # bytewise stable across 3 runs
    _emit(
        "V4_bytewise_stable",
        runs[0] == runs[1] == runs[2],
        f"len0={len(runs[0])} len1={len(runs[1])} len2={len(runs[2])} eq={runs[0]==runs[1]==runs[2]}",
    )

    try:
        parsed = json.loads(runs[0])
    except Exception as e:
        _emit("V4_json_parse", False, f"err: {e!r}")
        return
    _emit("V4_json_parse", isinstance(parsed, dict), f"type={type(parsed).__name__}")

    stats = parsed.get("stats") or {}
    _emit(
        "V4_sort_order_field",
        stats.get("sort_order") == SORT_ORDER_VALUE,
        f"sort_order={stats.get('sort_order')!r} expect={SORT_ORDER_VALUE!r}",
    )

    entries = parsed.get("entries") or []
    _emit("V4_entries_nonempty", len(entries) >= 2, f"n={len(entries)}")

    # 逐对断言非降序: (file, lineno, const_name)
    bad = []
    for i in range(1, len(entries)):
        prev = (entries[i - 1]["file"], entries[i - 1]["lineno"], entries[i - 1]["const_name"])
        curr = (entries[i]["file"], entries[i]["lineno"], entries[i]["const_name"])
        if prev > curr:
            bad.append((i, prev, curr))
    _emit(
        "V4_entries_sorted",
        not bad,
        f"violations={len(bad)}" + (f" first={bad[0]!r}" if bad else ""),
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
        print(f"[verify_infra_050][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_050][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
