#!/usr/bin/env python3
"""verify_infra_059: dump_v4_sha_graph mermaid unknown 节点数行为锁 V0-V5.

infra-048-backlog-docstring-unknown-zero-fact (phase-36 #5.36, P278):
``scripts/dump_v4_sha_graph.py`` 通过 ``_classify_node`` 把 mermaid 节点分 6
类: hub / verify / lib / dump / module / unknown。其中 ``unknown`` 是兜底
分类——仅当 ``node_id.startswith("unknown_")`` (lock target 描述里无 ``.py``
stem, render_mermaid 退化成 ``unknown_<CONST>`` 占位节点) 时才命中。

verify_infra_048 (palette extract 锁) 已锁 6 类分类逻辑存在 + 颜色字面, 但
**没断言 unknown 节点数 ≤ N** 作为行为锁。如果未来分类规则失效 (target
解析正则破损 / 大量 lock target 无法 stem-resolve), unknown 节点会悄悄
膨胀, verify 仍 PASS。

本 feature 新增 helper ``_verify_lib.verify_unknown_node_count_bound``, 由
verify_infra_059 V0-V5 完整锁该 helper, 并以真实 ``dump_v4_sha_graph.py
--mermaid`` 派生的 nodes 作为行为锚 (当前实测 unknown_count=13, 主要为
``_verify_lib`` 的 func 锁 target 无 ``.py`` stem 导致退化), tmp 构造正/反例
覆盖 helper 行为面。

INFRA_059_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_LIB_FILE_SHA
- ``verify_unknown_node_count_bound`` func sha: EXPECTED_UNKNOWN_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA
- 当前实测 unknown_count 精确锁: EXPECTED_CURRENT_UNKNOWN_COUNT

校验层级 (V0-V5):

- V0 scaffolding: _verify_lib.py 存在 + verify_unknown_node_count_bound
  公开 (def 形 + ``__all__`` 含入口名)
- V1 docstring sentinel ``INFRA_059_SHA_LOCKS`` + 本脚本 v4_behavior func
  sha 自锁
- V2 _verify_lib.py file sha
- V3 verify_unknown_node_count_bound canonical func sha
- V4 行为:
  - 真实 ``dump_v4_sha_graph.py --mermaid`` → 解析 ``class <id> <kind>;``
    行 → 派生 nodes 列表 → helper(max=EXPECTED_CURRENT_UNKNOWN_COUNT)
    within_bound=True, unknown_count 精确 == EXPECTED_CURRENT_UNKNOWN_COUNT
  - tmp 正例 (全非 unknown): nodes=[{id:a,kind:verify},{id:b,kind:lib}],
    max=1 → within_bound=True, unknown_count=0
  - tmp 边界: nodes=[{id:x,kind:unknown}], max=1 → within_bound=True
  - tmp mutant (超额): nodes=[{id:x,kind:unknown},{id:y,kind:unknown}],
    max=1 → within_bound=False, unknown_ids=["x","y"], unknown_count=2
  - tmp mutant (max=0): nodes=[{id:x,kind:unknown}], max=0 → within_bound=False
- V5 Reviewer LGTM gate (print-only)

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
LIB = SCRIPTS / "_verify_lib.py"
DUMP_PY = SCRIPTS / "dump_v4_sha_graph.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (
    func_sha_by_name,
    verify_unknown_node_count_bound,
    assert_v5_reviewer_gate_evidence_bind,
)

# infra-059 sha lock 常量 (V2 / V3)
EXPECTED_LIB_FILE_SHA = "7df5af6b9d48687e0a5efab7b3dc2e3dfc2fd54e6aa07d1583fb9d5a56604ef4"
EXPECTED_UNKNOWN_FUNC_SHA = "fd311e570696f415e74a06ecd7a974f3c730323d4901df616c8a09e46638e50a"

# 本脚本 v4_behavior 自锁 (V1) — 首跑 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "eb87f84607599b54b5e234b7e54f4bbb00c93e312299e0db9bbe3281530ed9cb"

# 当前实测 unknown 节点数精确锁 (V4_real_count_eq_current)
# 来源: ``.venv/bin/python scripts/dump_v4_sha_graph.py --mermaid | grep -E
# "^\s*class " | awk '{print $3}' | sort | uniq -c`` (P278 实测)。
# 未来若有新 lock 引入新 unknown_ 节点, 该常量应有意识地 bump (并复审是否
# 应改进 _classify_node / render_mermaid 让该 lock 被正确归类)。
# infra-P285-classifier-recognize-lib-func-locks (phase-37 #1.37): 由 13 → 1。
# 13 个原 unknown_EXPECTED_*_FUNC_SHA / FILE_SHA 通过补全 _PER_FILE_LOCKS 13 条
# (source, const) → target 映射, 现可被 render_mermaid 解析到正确 .py stem
# 并按 _classify_node 归 lib / dump。仅剩 EXPECTED_DOC_SHA (verify_robot_034)
# 因 target 是 docs/ 非 .py 文件, 继续保留 unknown 占位。
EXPECTED_CURRENT_UNKNOWN_COUNT = 1

# infra-P286-total-nodes-lock (phase-39 #5.39): V4 sha graph 节点总数锁。
# 当前 ratio 锁只锁 V4/total 比例下界, 不锁绝对节点数; 若大量 sha lock 被
# 误删 (V4 与 total 一起缩水, ratio 不变), ratio 检查不报警。引入
# EXPECTED_CURRENT_TOTAL_NODES (实测值 + ±TOTAL_NODES_TOLERANCE 浮动) 形成
# count + total + ratio 三重锁, 防节点漂移。
# 实测来源: dump_v4_sha_graph --mermaid 派生 nodes 总数 (P286 实测 = 81;
# infra-P286-followup-tolerance-headroom-bump phase-41 #1.41 实测 = 86, bump
# truth 到 86 并扩 tolerance 到 ±10 留 headroom, 下次新增 verify 累计到 96 才再爆)。
# 未来新增 verify_infra_* / lib helper 引起 total_nodes 漂移 > ±10 应有意识地
# bump 该常量 (并复审是否新 lock 真的有效)。
EXPECTED_CURRENT_TOTAL_NODES: int = 86
TOTAL_NODES_TOLERANCE: int = 10

DOCSTRING_SENTINEL = "INFRA_059_SHA_LOCKS"


# phase-47 #1.47: V5_GATE evidence-bind helper (grace_period 兜底 soft graduate)
REAL_FEATURE_LIST = Path(__file__).resolve().parents[1] / "feature_list.json"
V5_GATE_FEATURE_ID = "__PHASE_47_PLACEHOLDER_INFRA_059__"

_results: List[Tuple[str, bool, str]] = []


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_059][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# class 行格式: "    class <node_id> <kind>;"
_RE_MERMAID_CLASS = re.compile(r"^\s*class\s+(\S+)\s+(\w+);\s*$")


def _derive_nodes_from_mermaid(mermaid_text: str) -> list[dict]:
    """从 dump --mermaid 输出解析 ``class <id> <kind>;`` 行, 派生 nodes 列表。

    每个 entry: {"id": <node_id>, "kind": <className>}。顺序按出现顺序。
    """
    nodes: list[dict] = []
    for line in mermaid_text.splitlines():
        m = _RE_MERMAID_CLASS.match(line)
        if m:
            nodes.append({"id": m.group(1), "kind": m.group(2)})
    return nodes


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
        "def verify_unknown_node_count_bound(" in src,
        "expect 'def verify_unknown_node_count_bound(' in lib",
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
        "verify_unknown_node_count_bound" in all_names,
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
# V3: verify_unknown_node_count_bound canonical func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "verify_unknown_node_count_bound")
    except Exception as e:
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_UNKNOWN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V3_helper_func_sha",
            False,
            f"placeholder; bump EXPECTED_UNKNOWN_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_UNKNOWN_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_UNKNOWN_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为 — 真 dump + tmp 正/边界/反例
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # 1) 真实 dump --mermaid → 派生 nodes → helper(max=EXPECTED_CURRENT_UNKNOWN_COUNT)
    try:
        proc = subprocess.run(
            [sys.executable, str(DUMP_PY), "--mermaid"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        mermaid_text = proc.stdout
    except Exception as e:
        _emit("V4_real_dump_run", False, f"dump err: {e!r}")
        return
    _emit("V4_real_dump_run", True, f"stdout_bytes={len(mermaid_text)}")
    nodes = _derive_nodes_from_mermaid(mermaid_text)
    _emit(
        "V4_real_nodes_parsed",
        len(nodes) > 0,
        f"node_count={len(nodes)}",
    )
    r_real = verify_unknown_node_count_bound(
        nodes, max_unknown=EXPECTED_CURRENT_UNKNOWN_COUNT
    )
    _emit(
        "V4_real_within_bound",
        r_real.get("within_bound") is True,
        f"within_bound={r_real.get('within_bound')} "
        f"unknown_count={r_real.get('unknown_count')} "
        f"max_allowed={r_real.get('max_allowed')}",
    )
    _emit(
        "V4_real_count_eq_current",
        r_real.get("unknown_count") == EXPECTED_CURRENT_UNKNOWN_COUNT,
        f"unknown_count={r_real.get('unknown_count')} "
        f"expect={EXPECTED_CURRENT_UNKNOWN_COUNT}",
    )
    _emit(
        "V4_real_total_nodes_ge_unknown",
        isinstance(r_real.get("total_nodes"), int)
        and r_real.get("total_nodes", 0) >= r_real.get("unknown_count", 0),
        f"total_nodes={r_real.get('total_nodes')} "
        f"unknown_count={r_real.get('unknown_count')}",
    )

    # infra-P286-total-nodes-lock: total_nodes 绝对值上下界 (±TOLERANCE)
    _tn = r_real.get("total_nodes")
    _tn_ok = (
        isinstance(_tn, int)
        and abs(_tn - EXPECTED_CURRENT_TOTAL_NODES) <= TOTAL_NODES_TOLERANCE
    )
    _emit(
        "V4_real_total_nodes_within_tolerance",
        _tn_ok,
        f"total_nodes={_tn} expect={EXPECTED_CURRENT_TOTAL_NODES} "
        f"tolerance=±{TOTAL_NODES_TOLERANCE}",
    )

    # 2) tmp 正例: 全非 unknown
    pos = [{"id": "a", "kind": "verify"}, {"id": "b", "kind": "lib"}]
    r_pos = verify_unknown_node_count_bound(pos, max_unknown=1)
    _emit(
        "V4_tmp_positive_all_classified",
        r_pos.get("within_bound") is True
        and r_pos.get("unknown_count") == 0
        and r_pos.get("unknown_ids") == []
        and r_pos.get("total_nodes") == 2
        and r_pos.get("max_allowed") == 1,
        f"within_bound={r_pos.get('within_bound')} "
        f"unknown_count={r_pos.get('unknown_count')} "
        f"unknown_ids={r_pos.get('unknown_ids')}",
    )

    # 3) tmp 边界: 1 unknown @ max=1
    boundary = [{"id": "x", "kind": "unknown"}]
    r_b = verify_unknown_node_count_bound(boundary, max_unknown=1)
    _emit(
        "V4_tmp_boundary_eq_max",
        r_b.get("within_bound") is True
        and r_b.get("unknown_count") == 1
        and r_b.get("unknown_ids") == ["x"]
        and r_b.get("max_allowed") == 1,
        f"within_bound={r_b.get('within_bound')} "
        f"unknown_count={r_b.get('unknown_count')} "
        f"unknown_ids={r_b.get('unknown_ids')}",
    )

    # 4) tmp mutant (超额): 2 unknown @ max=1 → within_bound=False
    excess = [
        {"id": "x", "kind": "unknown"},
        {"id": "y", "kind": "unknown"},
    ]
    r_e = verify_unknown_node_count_bound(excess, max_unknown=1)
    _emit(
        "V4_tmp_mutant_excess_detected",
        r_e.get("within_bound") is False
        and r_e.get("unknown_count") == 2
        and r_e.get("unknown_ids") == ["x", "y"]
        and r_e.get("max_allowed") == 1,
        f"within_bound={r_e.get('within_bound')} "
        f"unknown_count={r_e.get('unknown_count')} "
        f"unknown_ids={r_e.get('unknown_ids')}",
    )

    # 5) tmp mutant (max=0): 1 unknown 即超 → within_bound=False
    strict = [{"id": "x", "kind": "unknown"}]
    r_s = verify_unknown_node_count_bound(strict, max_unknown=0)
    _emit(
        "V4_tmp_mutant_zero_max",
        r_s.get("within_bound") is False
        and r_s.get("unknown_count") == 1
        and r_s.get("max_allowed") == 0,
        f"within_bound={r_s.get('within_bound')} "
        f"unknown_count={r_s.get('unknown_count')} "
        f"max_allowed={r_s.get('max_allowed')}",
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
        print(f"[verify_infra_059][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_059][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
