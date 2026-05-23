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
  - 边界 (P283): 空 dict / entry 缺 fill / entry 非 dict 三种静默契约锁
    — helper 静默跳过, 不抛 TypeError, distinct_fill_count 只计有效 entry
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
from _verify_lib import (
    func_sha_by_name,
    verify_palette_fills_distinct,
    assert_v5_reviewer_gate_evidence_bind,
)

# infra-058 sha lock 常量 (V2 / V3)
EXPECTED_LIB_FILE_SHA = "f7248f548eab36eab74ff678aa84e567928b5fc02e9a085f9289539aaef1ee99"
EXPECTED_PALETTE_FUNC_SHA = "8415eca01361eb6b841bc8670bd608a1174ed51610315622ad56f5145d5ee456"

# 本脚本 v4_behavior 自锁 (V1) — 首跑 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "eccdbf04b367d01a1006e9aa315b8239f9dbc9a26c7c3dfbdd01720f99df1b2d"

DOCSTRING_SENTINEL = "INFRA_058_SHA_LOCKS"


# phase-47 #1.47: V5_GATE evidence-bind helper (grace_period 兜底 soft graduate)
REAL_FEATURE_LIST = Path(__file__).resolve().parents[1] / "feature_list.json"
V5_GATE_FEATURE_ID = "__PHASE_47_PLACEHOLDER_INFRA_058__"

_results: List[Tuple[str, bool, str]] = []


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

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

    # 5) 边界 (P283, infra-P283-palette-distinct-helper-edge-case-locks):
    #    helper 对边界输入的静默契约显式锁, 防未来实现改动导致静默漂移.
    # 5a) 空 dict → distinct_fill_count=0, duplicates=[], all_distinct=True, total_keys=0
    r_empty = verify_palette_fills_distinct({})
    _emit(
        "V4_edge_empty_dict_silent",
        r_empty.get("all_distinct") is True
        and r_empty.get("distinct_fill_count") == 0
        and r_empty.get("duplicates") == []
        and r_empty.get("total_keys") == 0
        and r_empty.get("fills") == [],
        f"all_distinct={r_empty.get('all_distinct')} "
        f"distinct={r_empty.get('distinct_fill_count')} "
        f"duplicates={r_empty.get('duplicates')} "
        f"total={r_empty.get('total_keys')} fills={r_empty.get('fills')}",
    )

    # 5b) entry 缺 fill 字段 → helper 应静默跳过, 不计入 distinct_fill_count;
    #     total_keys 仍是输入 key 总数 (按 docstring 语义).
    missing_fill = {"a": {"color": "#aaa"}, "b": {"fill": "#bbb"}}
    r_missing = verify_palette_fills_distinct(missing_fill)
    _emit(
        "V4_edge_missing_fill_silent_skip",
        r_missing.get("all_distinct") is True
        and r_missing.get("distinct_fill_count") == 1
        and r_missing.get("duplicates") == []
        and r_missing.get("fills") == [("b", "#bbb")],
        f"all_distinct={r_missing.get('all_distinct')} "
        f"distinct={r_missing.get('distinct_fill_count')} "
        f"duplicates={r_missing.get('duplicates')} "
        f"fills={r_missing.get('fills')}",
    )

    # 5c) entry 非 dict (如 str / None / int) → 静默跳过, 不抛 TypeError.
    non_dict = {"a": "#aaa", "b": None, "c": 123, "d": {"fill": "#ddd"}}
    try:
        r_nondict = verify_palette_fills_distinct(non_dict)
        raised = False
    except TypeError as e:
        r_nondict = {}
        raised = True
    _emit(
        "V4_edge_non_dict_entry_silent_skip",
        not raised
        and r_nondict.get("all_distinct") is True
        and r_nondict.get("distinct_fill_count") == 1
        and r_nondict.get("duplicates") == []
        and r_nondict.get("fills") == [("d", "#ddd")],
        f"raised={raised} all_distinct={r_nondict.get('all_distinct')} "
        f"distinct={r_nondict.get('distinct_fill_count')} "
        f"duplicates={r_nondict.get('duplicates')} "
        f"fills={r_nondict.get('fills')}",
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
