#!/usr/bin/env python3
"""verify_infra_109: render_mermaid tuple/multi-target fanout 渲染 (verify-only).

infra-039-backlog-mermaid-tuple-fanout (phase-57 #1):
``scripts/dump_v4_sha_graph.py`` 的 ``render_mermaid`` 此前对 multi-target lock
(target 字段含逗号分隔的多个 ``.py`` 文件, 由 _RE_VERIFY_HINT / _RE_V_NUM_HINT /
_RE_BUMP_HINT 分支生成) 只取第一个 stem 作为单 edge target, 把真实的 1→N 反向 sha
耦合压缩成 1→1 渲染, 视觉上丢失 fanout 拓扑。本 verify 锁住 fanout 改写后的行为:

- render_mermaid func sha 锁 (改写痕迹)
- behavior: build_graph 中存在的 multi-target lock 在 mermaid 输出里必须 emit
  N 条独立 edge (每个 target 一条), 而不是 1 条 edge

INFRA_109_SHA_LOCKS
-------------------
- ``scripts/dump_v4_sha_graph.py`` file sha: EXPECTED_DUMP_FILE_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``dump_v4_sha_graph.render_mermaid`` func sha: EXPECTED_RENDER_MERMAID_FUNC_SHA
- self ``main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA

校验层级 (V0-V6):

- V0 scaffolding: dump_v4_sha_graph.py 存在 + render_mermaid 顶层符号在
- V1 docstring sentinel ``INFRA_109_SHA_LOCKS`` 自锁
- V2 双 file sha 锁 (dump + lib)
- V3 render_mermaid func sha 锁 (与 verify_infra_043 主锁同步;
  本 verify 独立持有第二份锁, render_mermaid 任何漂移都会击中)
- V4 fanout behavior (live):
  - V4_multi_target_locks_exist: build_graph 至少含 1 条 target 含逗号 multi-py 的 lock
  - V4_fanout_edges_match_targets: 对每条 multi-target lock, mermaid 输出中
    ``src -->|const| tgt`` 字面 edge 数必须 == 该 lock target 中独立 .py stem 数
  - V4_no_single_edge_for_multi: 不能再出现"multi-target lock 仅 1 条 edge"的退化
- V6 reverse_sha meta-lock: render_mermaid func sha + 字面 fanout 边计数双锁
  (即 mermaid 输出中 ``-->|`` 字面出现次数必须 >= 已知 fanout edges 下限)
- V5 Reviewer LGTM gate (grace_period 兜底)

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DUMP_PY = SCRIPTS / "dump_v4_sha_graph.py"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

# infra-039-backlog-mermaid-tuple-fanout sha lock 常量 (V2 + V3)
EXPECTED_DUMP_FILE_SHA = (
    "b1fbe28b70bf3048a5b919877c07d15f966fe8da5769a03b2dfb6ffbc4bb6682"
)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "f789e0d870c9e6c3764b893a6bcbcca356bfc464520d9c49f3d4b17045ecd243"
)
EXPECTED_RENDER_MERMAID_FUNC_SHA = (
    "7c7a3ff794c99af00d24b42346f4e73ce879ab0a9cde462b3966d778be236685"
)
# 自身 main func sha (首跑用 __BUMP_ME__ 占位, 再回填)
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "63fc082201fccd77441ab570ed3a2ef3dde7c8f444e104fd5437a6ca0fcf352c"
)

DOCSTRING_SENTINEL = "INFRA_109_SHA_LOCKS"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-039-backlog-mermaid-tuple-fanout"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    _results.append((tag, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[verify_infra_109] {status} {tag}: {detail}", flush=True)


def _file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    # V0 scaffolding
    if not DUMP_PY.is_file():
        _emit("V0_dump_exists", False, f"missing {DUMP_PY}")
        verify_summary_exit(sum(1 for _, ok, _ in _results if not ok))
        return
    _emit("V0_dump_exists", True, str(DUMP_PY.relative_to(REPO)))
    src = DUMP_PY.read_text(encoding="utf-8")
    if "def render_mermaid" not in src:
        _emit("V0_render_mermaid_symbol", False, "render_mermaid not found")
        verify_summary_exit(sum(1 for _, ok, _ in _results if not ok))
        return
    _emit("V0_render_mermaid_symbol", True, "render_mermaid present")

    # V1 docstring sentinel
    self_src = Path(__file__).read_text(encoding="utf-8")
    _emit(
        "V1_docstring_sentinel",
        DOCSTRING_SENTINEL in self_src,
        f"sentinel={DOCSTRING_SENTINEL}",
    )

    # V2 file sha (dump + lib)
    got_dump = _file_sha(DUMP_PY)
    if EXPECTED_DUMP_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V2_dump_file_sha",
            False,
            f"placeholder; bump EXPECTED_DUMP_FILE_SHA={got_dump}",
        )
    else:
        _emit(
            "V2_dump_file_sha",
            got_dump == EXPECTED_DUMP_FILE_SHA,
            f"got={got_dump[:16]} expect={EXPECTED_DUMP_FILE_SHA[:16]}",
        )
    got_lib = _file_sha(VERIFY_LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V2_verify_lib_file_sha",
            False,
            f"placeholder; bump EXPECTED_VERIFY_LIB_FILE_SHA={got_lib}",
        )
    else:
        _emit(
            "V2_verify_lib_file_sha",
            got_lib == EXPECTED_VERIFY_LIB_FILE_SHA,
            f"got={got_lib[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
        )

    # V3 render_mermaid func sha
    try:
        got_render = func_sha_by_name(DUMP_PY, "render_mermaid")
    except Exception as e:
        _emit("V3_render_mermaid_func_sha", False, f"compute err: {e!r}")
        got_render = ""
    if got_render:
        if EXPECTED_RENDER_MERMAID_FUNC_SHA == "__BUMP_ME__":
            _emit(
                "V3_render_mermaid_func_sha",
                False,
                f"placeholder; bump EXPECTED_RENDER_MERMAID_FUNC_SHA={got_render}",
            )
        else:
            _emit(
                "V3_render_mermaid_func_sha",
                got_render == EXPECTED_RENDER_MERMAID_FUNC_SHA,
                f"got={got_render[:16]} expect={EXPECTED_RENDER_MERMAID_FUNC_SHA[:16]}",
            )

    # V4 fanout behavior (live build_graph + render_mermaid)
    from dump_v4_sha_graph import build_graph, render_mermaid  # noqa: E402

    g = build_graph()
    locks = g.get("locks", [])
    # 找出 target 字段含 ≥2 个 .py 的 multi-target lock
    multi_locks: List[dict] = []
    for lk in locks:
        stems = re.findall(r"([A-Za-z0-9_]+)\.py", lk.get("target", ""))
        uniq = sorted(set(stems))
        if len(uniq) >= 2:
            multi_locks.append({**lk, "_stems": uniq})
    _emit(
        "V4_multi_target_locks_exist",
        len(multi_locks) >= 1,
        f"multi-target lock count={len(multi_locks)}",
    )

    if multi_locks:
        mermaid = render_mermaid(g)
        # 检查每条 multi-target lock 在 mermaid 输出中 emit 了 N 条独立 edge
        mismatch: List[str] = []
        edge_count_total = 0
        for lk in multi_locks:
            src_stem = Path(lk["source"]).stem
            const = lk["const"]
            # 形如: "    verify_xxx -->|CONST| verify_yyy"
            pat = re.compile(
                rf"^\s*{re.escape(src_stem)}\s+-->\|{re.escape(const)}\|\s+(\S+)",
                re.MULTILINE,
            )
            found = pat.findall(mermaid)
            edge_count_total += len(found)
            if len(found) != len(lk["_stems"]):
                mismatch.append(
                    f"{src_stem}|{const}: edges={len(found)} expected={len(lk['_stems'])}"
                )
        _emit(
            "V4_fanout_edges_match_targets",
            len(mismatch) == 0,
            f"mismatch={mismatch[:3]} (total multi={len(multi_locks)}, edges={edge_count_total})"
            if mismatch
            else f"all {len(multi_locks)} multi-target locks fanout correctly (total fanout edges={edge_count_total})",
        )
        # V4_no_single_edge_for_multi: 至少有一条 multi-lock 输出 >=2 edges
        any_fanout = any(
            len(
                re.compile(
                    rf"^\s*{re.escape(Path(lk['source']).stem)}\s+-->\|{re.escape(lk['const'])}\|",
                    re.MULTILINE,
                ).findall(mermaid)
            )
            >= 2
            for lk in multi_locks
        )
        _emit(
            "V4_no_single_edge_for_multi",
            any_fanout,
            f"at_least_one_fanout={any_fanout}",
        )

        # V6 字面 fanout 边计数下限锁 (deterministic 行为锁)
        # 多 target lock 的总 fanout edges = sum(len(stems))
        expected_min_fanout_edges = sum(len(lk["_stems"]) for lk in multi_locks)
        all_arrows = mermaid.count("-->|")
        _emit(
            "V6_fanout_edge_literal_count",
            all_arrows >= expected_min_fanout_edges,
            f"mermaid_arrows={all_arrows} expected_min_fanout={expected_min_fanout_edges}",
        )

    # V4b self main func sha
    try:
        got_main = func_sha_by_name(Path(__file__), "main")
    except Exception as e:
        _emit("V4b_self_main_func_sha", False, f"compute err: {e!r}")
        got_main = ""
    if got_main:
        if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
            _emit(
                "V4b_self_main_func_sha",
                False,
                f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got_main}",
            )
        else:
            _emit(
                "V4b_self_main_func_sha",
                got_main == EXPECTED_SELF_MAIN_FUNC_SHA,
                f"got={got_main[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
            )

    # V5 Reviewer LGTM gate (grace_period 兜底)
    if REAL_FEATURE_LIST.is_file():
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
    else:
        _emit("V5_reviewer_lgtm_gate", False, f"feature_list not found: {REAL_FEATURE_LIST}")

    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(f"[verify_infra_109][SUMMARY] FAIL {failed}/{total}: {names}", flush=True)
    else:
        print(f"[verify_infra_109][SUMMARY] ALL PASS ({total} checks)", flush=True)
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
