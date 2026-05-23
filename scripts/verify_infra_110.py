#!/usr/bin/env python3
"""verify_infra_110: render_mermaid unknown target 复合 key 防 collision (verify-only).

infra-039-backlog-mermaid-unknown-target-id-collision (phase-59 #4):
``scripts/dump_v4_sha_graph.py`` 的 ``render_mermaid`` 此前对 unknown target
(``_infer_target`` 返回 ``<unknown target>``, 即 target 字段中无 ``.py`` token 的
反向锁) 用 ``unknown_<CONST>`` 作为占位 node id; 跨 source 同名 const (例如
``EXPECTED_TARGET_FILE_SHA`` 同时出现在 verify_infra_P290 与 verify_infra_P301)
会被合并到同一个 ``unknown_<CONST>`` 节点, 视觉上把多个互不相干的 unknown target
错误地呈现为一个节点接收多条入边。本 verify 锁住复合 key 改写后的行为:

- render_mermaid func sha 锁 (改写痕迹)
- behavior: build_graph 中若存在跨 source 同 const → unknown 的场景, mermaid 输出
  中每条 unknown 边的 target node id 必须互不相同 (即 outbound 到 unknown 的
  ``(src, const)`` 二元组与 ``tgt_id`` 一一对应, 不存在两个不同 source 指向同一个
  ``unknown_*`` 节点的退化)

INFRA_110_SHA_LOCKS
-------------------
- ``scripts/dump_v4_sha_graph.py`` file sha: EXPECTED_DUMP_FILE_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``dump_v4_sha_graph.render_mermaid`` func sha: EXPECTED_RENDER_MERMAID_FUNC_SHA
- self ``main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA

校验层级 (V0-V6):

- V0 scaffolding: dump_v4_sha_graph.py 存在 + render_mermaid 顶层符号在
- V1 docstring sentinel ``INFRA_110_SHA_LOCKS`` 自锁
- V2 双 file sha 锁 (dump + lib)
- V3 render_mermaid func sha 锁
- V4 behavior (live):
  - V4_unknown_edges_exist: build_graph 至少含 1 条 unknown target lock
  - V4_no_unknown_id_collision: 渲染出的 mermaid 输出中, 不存在「两条 unknown
    入边落到同一个 unknown_* node id」的情况; 即每条 unknown 边的 ``tgt_id``
    必须唯一, 且 ``tgt_id`` 形如 ``unknown_<src_stem>_<CONST>``
  - V4_composite_key_well_formed: 每个 unknown_* node id 必须能解析出 src_stem
    与 const 两段, 验证复合 key 设计而非单 key 退化
- V4b self main func sha
- V5 Reviewer LGTM gate (grace_period 兜底)
- V6 reverse_sha meta-lock: render_mermaid func sha (同 V3, 第二份独立持有)

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

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

# infra-039-backlog-mermaid-unknown-target-id-collision sha lock 常量
EXPECTED_DUMP_FILE_SHA = (
    "b1fbe28b70bf3048a5b919877c07d15f966fe8da5769a03b2dfb6ffbc4bb6682"
)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "c923b8de60e1930b02d43b84d6638ebbe7064976c881e0bb6da2d652bfc825fb"
)
EXPECTED_RENDER_MERMAID_FUNC_SHA = (
    "7c7a3ff794c99af00d24b42346f4e73ce879ab0a9cde462b3966d778be236685"
)
# 自身 main func sha (首跑用 __BUMP_ME__ 占位, 再回填)
EXPECTED_SELF_MAIN_FUNC_SHA = "a26fcd972d4941d479e9db1a0df3c2ddfb7cf077fd7c8d4508e06ac1229beaff"

DOCSTRING_SENTINEL = "INFRA_110_SHA_LOCKS"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-039-backlog-mermaid-unknown-target-id-collision"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    _results.append((tag, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[verify_infra_110] {status} {tag}: {detail}", flush=True)


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
        _emit("V2_dump_file_sha", False, f"placeholder; bump EXPECTED_DUMP_FILE_SHA={got_dump}")
    else:
        _emit(
            "V2_dump_file_sha",
            got_dump == EXPECTED_DUMP_FILE_SHA,
            f"got={got_dump[:16]} expect={EXPECTED_DUMP_FILE_SHA[:16]}",
        )
    got_lib = _file_sha(VERIFY_LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit("V2_verify_lib_file_sha", False, f"placeholder; bump EXPECTED_VERIFY_LIB_FILE_SHA={got_lib}")
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

    # V4 behavior — live build_graph + render_mermaid 观测 unknown 边
    from dump_v4_sha_graph import build_graph, render_mermaid  # noqa: E402

    g = build_graph()
    mermaid = render_mermaid(g)
    # 抓所有 edge ``src -->|const| tgt`` 中 tgt 以 ``unknown_`` 开头者
    edge_pat = re.compile(
        r"^\s*([A-Za-z0-9_]+)\s+-->\|([^|]+)\|\s+(unknown_\S+)\s*$",
        re.MULTILINE,
    )
    unknown_edges: List[Tuple[str, str, str]] = edge_pat.findall(mermaid)
    _emit(
        "V4_unknown_edges_exist",
        len(unknown_edges) >= 1,
        f"unknown_edge_count={len(unknown_edges)}",
    )

    # V4_no_unknown_id_collision: 不存在两条来自不同 (src, const) 的边落到同一 tgt_id
    # 反过来也要保证: 同一个 tgt_id 只对应一个 (src, const) 组合
    tgt_to_keys: Dict[str, set] = {}
    for src, const, tgt in unknown_edges:
        tgt_to_keys.setdefault(tgt, set()).add((src, const.strip()))
    collisions = {tgt: keys for tgt, keys in tgt_to_keys.items() if len(keys) > 1}
    _emit(
        "V4_no_unknown_id_collision",
        len(collisions) == 0,
        f"collisions={list(collisions.items())[:3]}" if collisions else f"all {len(tgt_to_keys)} unknown_* tgt_ids unique per (src,const)",
    )

    # V4_composite_key_well_formed: 每个 unknown_<src>_<CONST> 必须能拆出 src 与 const
    # 复合 key 的字符特征: 形如 unknown_<src_stem>_<CONST> 且 CONST 出现在 edge 标签中
    malformed: List[str] = []
    for src, const, tgt in unknown_edges:
        # tgt 应以 "unknown_" + src + "_" 开头, 后跟 const (sanitize 后)
        const_sanitized = re.sub(r"[^A-Za-z0-9_]", "_", const.strip())
        src_sanitized = re.sub(r"[^A-Za-z0-9_]", "_", src)
        expected_prefix = f"unknown_{src_sanitized}_"
        if not tgt.startswith(expected_prefix):
            malformed.append(f"{tgt} (expected prefix {expected_prefix})")
        elif const_sanitized not in tgt:
            malformed.append(f"{tgt} (missing const {const_sanitized})")
    _emit(
        "V4_composite_key_well_formed",
        len(malformed) == 0,
        f"malformed={malformed[:3]}" if malformed else f"all {len(unknown_edges)} edges have composite key",
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

    # V6 reverse_sha meta-lock (与 V3 同 render_mermaid func sha, 独立持有)
    if got_render and EXPECTED_RENDER_MERMAID_FUNC_SHA != "__BUMP_ME__":
        _emit(
            "V6_render_mermaid_func_sha_meta",
            got_render == EXPECTED_RENDER_MERMAID_FUNC_SHA,
            f"meta got={got_render[:16]} expect={EXPECTED_RENDER_MERMAID_FUNC_SHA[:16]}",
        )

    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(f"[verify_infra_110][SUMMARY] FAIL {failed}/{total}: {names}", flush=True)
    else:
        print(f"[verify_infra_110][SUMMARY] ALL PASS ({total} checks)", flush=True)
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
