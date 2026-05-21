#!/usr/bin/env python3
"""verify_infra_058: _MERMAID_PALETTE 6 色 fill 互不相同硬断言 V0-V5.

infra-053-backlog-classdef-fills-distinct-check (phase-36 #4.36, P277):
P273 (infra-054-classdef-palette-extract) 把 scripts/dump_v4_sha_graph.py
的 6 类 classDef 颜色提到 module-top ``_MERMAID_PALETTE``
(dict[str, dict[str, str]], 6 个 key: hub/verify/lib/dump/module/unknown)。
verify_infra_054 锁了 palette 结构存在 + 颜色 hex 字面, 但**没断言** 6 色
fill 互不相同。如果未来误把两个 key 写成同色, verify_infra_054 会 PASS 但
mermaid 图会失去可读性 (两类节点同色无法区分)。

本 feature 加 helper ``_verify_lib.verify_palette_fills_distinct``, 由
verify_infra_058 V0-V5 完整锁该 helper, 并以真实 ``_MERMAID_PALETTE``
作为正例锚 (6 色互不相同), tmp 构造正/反例覆盖 helper 行为。

INFRA_058_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_LIB_FILE_SHA
- ``verify_palette_fills_distinct`` func sha: EXPECTED_PALETTE_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: _verify_lib.py 存在 + verify_palette_fills_distinct
  公开 (def 形 + ``__all__`` 含入口名)
- V1 docstring sentinel ``INFRA_058_SHA_LOCKS`` + 本脚本 v4_behavior func sha 自锁
- V2 _verify_lib.py file sha
- V3 verify_palette_fills_distinct canonical func sha
- V4 行为:
  - 真实 ``_MERMAID_PALETTE`` 喂入 → all_distinct=True, distinct_fill_count=6,
    duplicates=[]
  - tmp 正例: ``{a:{fill:#aaa},b:{fill:#bbb}}`` → all_distinct=True
  - tmp 反例 (mutant): ``{a:{fill:#aaa},b:{fill:#aaa}}`` → all_distinct=False,
    duplicates=[("a","b","#aaa")]
- V5 Reviewer LGTM gate (print-only)

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
LIB = SCRIPTS / "_verify_lib.py"
DUMP_PY = SCRIPTS / "dump_v4_sha_graph.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    func_sha_by_name,
    verify_palette_fills_distinct,
)

# infra-058 sha lock 常量 (V2 / V3)
EXPECTED_LIB_FILE_SHA = "8e0e005150cd3e543642edc94952ae5d0a73540468e9bc2d979e810c05152664"
EXPECTED_PALETTE_FUNC_SHA = "8415eca01361eb6b841bc8670bd608a1174ed51610315622ad56f5145d5ee456"

# 本脚本 v4_behavior 自锁 (V1) — 首跑 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "9a6b74bda0eca36e7ac2096fa04075a262564fec4b41bbb10aed3d8366748a80"

DOCSTRING_SENTINEL = "INFRA_058_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_058][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_dump_palette() -> dict:
    """静态 import dump_v4_sha_graph.py 拿 _MERMAID_PALETTE 真值 (不跑 __main__)."""
    spec = importlib.util.spec_from_file_location("_dump_v4_for_058", DUMP_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "_MERMAID_PALETTE")


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_verify_lib_exists", LIB.is_file(), f"path={LIB}")
    if not LIB.is_file():
        return
    src = LIB.read_text(encoding="utf-8")
    _emit(
        "V0_helper_def_present",
        "def verify_palette_fills_distinct(" in src,
        "expect 'def verify_palette_fills_distinct(' in lib",
    )
    tree = ast.parse(src)
    all_names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for el in node.value.elts:
                            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                all_names.append(el.value)
    _emit(
        "V0_helper_in_all",
        "verify_palette_fills_distinct" in all_names,
        f"__all__ contains {len(all_names)} names",
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
        got = func_sha_by_name(self_path, "v4_behavior")
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
# V2: _verify_lib.py file sha
# ---------------------------------------------------------------------------
def v2_lib_file_sha() -> None:
    got = _file_sha(LIB)
    if EXPECTED_LIB_FILE_SHA == "__BUMP_ME__":
        _emit("V2_lib_file_sha", False, f"placeholder; bump EXPECTED_LIB_FILE_SHA={got}")
        return
    _emit(
        "V2_lib_file_sha",
        got == EXPECTED_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: verify_palette_fills_distinct canonical func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "verify_palette_fills_distinct")
    except Exception as e:
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_PALETTE_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V3_helper_func_sha",
            False,
            f"placeholder; bump EXPECTED_PALETTE_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_PALETTE_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_PALETTE_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为 — 真 palette + tmp 正例 + tmp 反例 (mutant)
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # 1) 真实 _MERMAID_PALETTE → all_distinct=True, distinct=6, duplicates=[]
    try:
        real_palette = _load_dump_palette()
    except Exception as e:
        _emit("V4_real_load_palette", False, f"load err: {e!r}")
        return
    _emit(
        "V4_real_load_palette",
        isinstance(real_palette, dict) and len(real_palette) == 6,
        f"keys={list(real_palette) if isinstance(real_palette, dict) else type(real_palette).__name__}",
    )
    r_real = verify_palette_fills_distinct(real_palette)
    _emit(
        "V4_real_all_distinct",
        r_real.get("all_distinct") is True,
        f"all_distinct={r_real.get('all_distinct')} duplicates={r_real.get('duplicates')}",
    )
    _emit(
        "V4_real_distinct_count_eq_6",
        r_real.get("distinct_fill_count") == 6,
        f"distinct_fill_count={r_real.get('distinct_fill_count')} expect=6",
    )
    _emit(
        "V4_real_total_keys_eq_6",
        r_real.get("total_keys") == 6,
        f"total_keys={r_real.get('total_keys')} expect=6",
    )
    _emit(
        "V4_real_duplicates_empty",
        r_real.get("duplicates") == [],
        f"duplicates={r_real.get('duplicates')}",
    )

    # 2) tmp 正例: 2 个不同 fill → all_distinct=True
    pos = {"a": {"fill": "#aaa"}, "b": {"fill": "#bbb"}}
    r_pos = verify_palette_fills_distinct(pos)
    _emit(
        "V4_tmp_positive_all_distinct",
        r_pos.get("all_distinct") is True
        and r_pos.get("distinct_fill_count") == 2
        and r_pos.get("duplicates") == [],
        f"all_distinct={r_pos.get('all_distinct')} distinct={r_pos.get('distinct_fill_count')} "
        f"duplicates={r_pos.get('duplicates')}",
    )

    # 3) tmp 反例 (mutant): 2 个相同 fill → all_distinct=False, duplicates=[("a","b","#aaa")]
    neg = {"a": {"fill": "#aaa"}, "b": {"fill": "#aaa"}}
    r_neg = verify_palette_fills_distinct(neg)
    expected_dups = [("a", "b", "#aaa")]
    _emit(
        "V4_tmp_mutant_detects_duplicate",
        r_neg.get("all_distinct") is False
        and r_neg.get("duplicates") == expected_dups
        and r_neg.get("distinct_fill_count") == 1
        and r_neg.get("total_keys") == 2,
        f"all_distinct={r_neg.get('all_distinct')} duplicates={r_neg.get('duplicates')} "
        f"distinct={r_neg.get('distinct_fill_count')} total={r_neg.get('total_keys')}",
    )

    # 4) tmp 反例 (3-way): 3 个 key 同 fill → duplicates 应含 3 个两两组合 (字典序)
    neg3 = {"a": {"fill": "#aaa"}, "b": {"fill": "#aaa"}, "c": {"fill": "#aaa"}}
    r_neg3 = verify_palette_fills_distinct(neg3)
    expected_dups3 = [("a", "b", "#aaa"), ("a", "c", "#aaa"), ("b", "c", "#aaa")]
    _emit(
        "V4_tmp_mutant_three_way_pairs",
        r_neg3.get("all_distinct") is False
        and r_neg3.get("duplicates") == expected_dups3,
        f"duplicates={r_neg3.get('duplicates')}",
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
    v2_lib_file_sha()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_058][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_058][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
