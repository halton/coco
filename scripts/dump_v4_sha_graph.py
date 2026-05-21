#!/usr/bin/env python3
"""dump_v4_sha_graph: V4 sha-lock 链路 DAG 可视化工具 (infra-039, verify-only).

扫描 ``scripts/verify_*.py`` 与 ``scripts/_verify_lib.py``, 抽取 ``*_SHA = "<64hex>"``
或 ``*_SHA = ( "<64hex>" )`` 形式的 sha 锁常量, 并尝试推断锁定 target (常见模式:
verify_<id> 交叉锁 / _verify_lib file 锁 / v4_sha.json 表), 以文本或 JSON 形式输出
当前仓库的整套 V4 sha-lock graph, 便于 closeout / Reviewer 一眼把握链路。

用法:
    python scripts/dump_v4_sha_graph.py            # 文本 graph -> stdout
    python scripts/dump_v4_sha_graph.py --json     # JSON dump
    python scripts/dump_v4_sha_graph.py --mermaid  # mermaid graph LR 语法
    python scripts/dump_v4_sha_graph.py --out F    # 写入文件 F

本脚本是 *只读* 工具, 不修改任何文件, 不依赖 reachy-mini SDK。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
V4_SHA_JSON = REPO / "evidence" / "infra-034" / "v4_sha.json"

# infra-048-backlog-mermaid-palette-extract: 6 类 classDef 色值常量化
# render_mermaid 按固定顺序 (hub/verify/lib/dump/module/unknown) 迭代生成 classDef 行；
# 输出必须与提取前 P272 状态 bytewise 完全一致 (6 个 verify 依赖此输出)。
_MERMAID_PALETTE: Dict[str, Dict[str, str]] = {
    "hub":     {"fill": "#fc6", "stroke": "#b85", "color": "#000"},
    "verify":  {"fill": "#9cf", "stroke": "#069", "color": "#000"},
    "lib":     {"fill": "#9f9", "stroke": "#090", "color": "#000"},
    "dump":    {"fill": "#ff9", "stroke": "#990", "color": "#000"},
    "module":  {"fill": "#c9f", "stroke": "#609", "color": "#000"},
    "unknown": {"fill": "#f99", "stroke": "#900", "color": "#000"},
}

# 形如  CONST = "abc...64..."  或  CONST = (\n    "abc...64..."\n)
_RE_SINGLELINE = re.compile(
    r'^([A-Z_][A-Z0-9_]*)\s*=\s*["\']([0-9a-f]{64})["\']\s*(?:#.*)?$'
)
_RE_TUPLE_OPEN = re.compile(r'^([A-Z_][A-Z0-9_]*)\s*=\s*\(\s*$')
_RE_TUPLE_HEX = re.compile(r'^\s*["\']([0-9a-f]{64})["\']\s*,?\s*$')

# 用于推断 target: 例如 EXPECTED_VERIFY_024_SHA256 -> verify_interact_024 / verify_robot_024 / verify_infra_024 等
_RE_VERIFY_HINT = re.compile(r'VERIFY_(\d+)_')
_RE_LIB_HINT = re.compile(r'(LIB|VERIFY_LIB|HELPER)')
# infra-039-backlog-target-inference: 形如 V018_EXPECTED_SHA / EXPECTED_V024_SHA256 / V010_EXPECTED_SHA / BUMP_028_EXPECTED_SHA
_RE_V_NUM_HINT = re.compile(r'V(\d{3})_')
_RE_BUMP_HINT = re.compile(r'BUMP_(\d+)_')

# infra-039-backlog-target-inference: 非数字常量名 → 文件路径 查表 (fingerprint / bump-only / dump 自锁)
# 用于覆盖 _RE_VERIFY_HINT 无法识别的复合 sha 锁; 含通用文件 sha 与 func sha
_KNOWN_NON_NUMERIC_TARGETS: Dict[str, str] = {
    # _verify_lib 反向锁
    "EXPECTED_LIB_FILE_SHA": "scripts/_verify_lib.py (file-sha)",
    "EXPECTED_VERIFY_LIB_FILE_SHA": "scripts/_verify_lib.py (file-sha)",
    "EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA": "scripts/_verify_lib.py:func_sha_by_name (func-sha)",
    "EXPECTED_V6_SCAN_FUNC_SHA": "scripts/_verify_lib.py:_v6_scan_constants (func-sha)",
    "EXPECTED_V6_TARGET_ID_FUNC_SHA": "scripts/_verify_lib.py:_v6_target_id (func-sha)",
    "EXPECTED_READ_CONSTANT_FUNC_SHA": "scripts/_verify_lib.py:read_constant (func-sha)",
    # dump_v4_sha_graph 自锁 (infra-039 / 043 / 044)
    "EXPECTED_DUMP_FILE_SHA": "scripts/dump_v4_sha_graph.py (file-sha)",
    "EXPECTED_RENDER_MERMAID_FUNC_SHA": "scripts/dump_v4_sha_graph.py:render_mermaid (func-sha)",
    "EXPECTED_INFER_TARGET_FUNC_SHA": "scripts/dump_v4_sha_graph.py:_infer_target (func-sha)",
    # verify_template 锁 (infra-040)
    "EXPECTED_VERIFY_TMPL_SHA": "scripts/_verify_template.py (file-sha; if exists)",
}

# infra-039-backlog-source-file-aware: (source_file_basename, const_name) → target 二级查表
# 用于跨 verify 文件同名常量歧义场景 (如 EXPECTED_FILE_SHA / EXPECTED_FUNC_SHA / SETTER_BLOCK_EXPECTED_SHA),
# 单纯按 const 名无法判定; 必须结合 source_file 才能锁出唯一 target。
_PER_FILE_LOCKS: Dict[Tuple[str, str], str] = {
    # verify_infra_045 — 锁 _verify_lib.py 的具体 func sha
    ("verify_infra_045.py", "EXPECTED_SCAN_FUNC_SHA"):
        "scripts/_verify_lib.py:scan_reverse_sha_locks (func-sha)",
    ("verify_infra_045.py", "EXPECTED_LIVE_FUNC_SHA"):
        "scripts/_verify_lib.py:live_verify_sha_set (func-sha)",
    ("verify_infra_045.py", "EXPECTED_CHECK_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_reverse_sha_lock_consistency (func-sha)",
    # verify_infra_046 — 锁 bump_reverse_sha_lock.py 的 file / func sha
    ("verify_infra_046.py", "EXPECTED_BUMP_FILE_SHA"):
        "scripts/bump_reverse_sha_lock.py (file-sha)",
    ("verify_infra_046.py", "EXPECTED_RUN_BUMP_FUNC_SHA"):
        "scripts/bump_reverse_sha_lock.py:run_bump (func-sha)",
    ("verify_infra_046.py", "EXPECTED_FIND_LOCKS_FUNC_SHA"):
        "scripts/bump_reverse_sha_lock.py:find_locks_for_target (func-sha)",
    ("verify_infra_046.py", "EXPECTED_BUMP_IN_FILE_FUNC_SHA"):
        "scripts/bump_reverse_sha_lock.py:_bump_in_file (func-sha)",
    ("verify_infra_046.py", "EXPECTED_MAIN_FUNC_SHA"):
        "scripts/bump_reverse_sha_lock.py:main (func-sha)",
    # verify_interact_037 — 锁 verify_interact_024.py 的 func / file sha
    ("verify_interact_037.py", "EXPECTED_FUNC_SHA"):
        "scripts/verify_interact_024.py:_append_drift_history (func-sha)",
    ("verify_interact_037.py", "EXPECTED_FILE_SHA"):
        "scripts/verify_interact_024.py (file-sha)",
    # verify_robot_025 / 027 — coco/proactive.py setter block
    ("verify_robot_025.py", "SETTER_BLOCK_EXPECTED_SHA"):
        "coco/proactive.py (setter block sha)",
    ("verify_robot_027.py", "SETTER_BLOCK_EXPECTED_SHA"):
        "coco/proactive.py (setter block sha)",
    ("verify_robot_028.py", "BUMP_EXPECTED_SHA"):
        "coco/proactive.py (bump-only block sha)",
    ("verify_robot_029.py", "SETTER_BASELINE_EXPECTED_SHA"):
        "coco/proactive.py (setter baseline sha)",
    ("verify_robot_030.py", "BLOCK_BASELINE_EXPECTED_SHA"):
        "coco/proactive.py (block baseline sha)",
    # verify_robot_033 — coco/proactive.py 多锚点
    ("verify_robot_033.py", "INIT_LINE_SHA"):
        "coco/proactive.py (init line sha)",
    ("verify_robot_033.py", "EXCEPT_BLOCK_SHA"):
        "coco/proactive.py (except block sha)",
    ("verify_robot_033.py", "FILE_SHA"):
        "coco/proactive.py (file-sha)",
    # verify_robot_034 — docs file
    ("verify_robot_034.py", "EXPECTED_DOC_SHA"):
        "docs (robot-032 headings doc) (file-sha)",
    # verify_robot_036 — verify_robot_032.py sentinel line
    ("verify_robot_036.py", "EXPECTED_SENTINEL_LINE_SHA"):
        "scripts/verify_robot_032.py:_HEADINGS_SECTION_SENTINEL (line-sha)",
    # ── P285 phase-37 #1.37 ──
    # infra-P285-classifier-recognize-lib-func-locks: 补全 13 个 unknown 中可消除项的 (source, const) → target
    # 真实 target 取自各 verify 脚本 docstring 头部 ``sha 锁清单`` 字段; 落地后这些 const
    # 在 render_mermaid 中可解析到正确文件 stem (而非 unknown_<CONST> 占位), 并按 _classify_node
    # 归 lib (_verify_lib helper) / dump (dump_reverse_sha_lock_index helper) 两类。
    # verify_infra_049 / 050 / 051 — 锁 dump_reverse_sha_lock_index.py file / func sha
    ("verify_infra_049.py", "EXPECTED_DUMP_INDEX_FILE_SHA"):
        "scripts/dump_reverse_sha_lock_index.py (file-sha)",
    ("verify_infra_049.py", "EXPECTED_RENDER_TEXT_FUNC_SHA"):
        "scripts/dump_reverse_sha_lock_index.py:render_text (func-sha)",
    ("verify_infra_049.py", "EXPECTED_RENDER_JSON_FUNC_SHA"):
        "scripts/dump_reverse_sha_lock_index.py:render_json (func-sha)",
    ("verify_infra_050.py", "EXPECTED_DUMP_INDEX_FILE_SHA"):
        "scripts/dump_reverse_sha_lock_index.py (file-sha)",
    ("verify_infra_050.py", "EXPECTED_RENDER_JSON_FUNC_SHA"):
        "scripts/dump_reverse_sha_lock_index.py:render_json (func-sha)",
    ("verify_infra_051.py", "EXPECTED_DUMP_INDEX_FILE_SHA"):
        "scripts/dump_reverse_sha_lock_index.py (file-sha)",
    ("verify_infra_051.py", "EXPECTED_RENDER_CHECK_JSON_FUNC_SHA"):
        "scripts/dump_reverse_sha_lock_index.py:render_check_json (func-sha)",
    ("verify_infra_051.py", "EXPECTED_CMD_CHECK_FUNC_SHA"):
        "scripts/dump_reverse_sha_lock_index.py:cmd_check (func-sha)",
    ("verify_infra_051.py", "EXPECTED_BUILD_ARG_PARSER_FUNC_SHA"):
        "scripts/dump_reverse_sha_lock_index.py:build_arg_parser (func-sha)",
    # verify_infra_052 — _verify_lib helper sha (与 045 同名 const, 同 lib helper)
    ("verify_infra_052.py", "EXPECTED_SCAN_FUNC_SHA"):
        "scripts/_verify_lib.py:scan_reverse_sha_locks (func-sha)",
    ("verify_infra_052.py", "EXPECTED_CHECK_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_reverse_sha_lock_consistency (func-sha)",
    # verify_infra_055 / 057 / 058 / 059 — _verify_lib helper sha (各 1 个)
    ("verify_infra_055.py", "EXPECTED_ASSERT_VERIFY_PASSED_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_verify_passed (func-sha)",
    ("verify_infra_057.py", "EXPECTED_VERIFY_EP_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_expected_pattern_consistency (func-sha)",
    ("verify_infra_058.py", "EXPECTED_PALETTE_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_palette_fills_distinct (func-sha)",
    ("verify_infra_059.py", "EXPECTED_UNKNOWN_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_unknown_node_count_bound (func-sha)",
    # verify_infra_060 — 锁 dump_v4_sha_graph.py 自身 _classify_node func sha
    ("verify_infra_060.py", "EXPECTED_CLASSIFY_FUNC_SHA"):
        "scripts/dump_v4_sha_graph.py:_classify_node (func-sha)",
    # verify_infra_061 (P281) — 锁 verify_expected_prefix_typo_guard func sha
    ("verify_infra_061.py", "EXPECTED_TYPO_GUARD_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_expected_prefix_typo_guard (func-sha)",
    # verify_infra_062 (P278) — 锁 verify_closeout_evidence_trustworthy func sha
    ("verify_infra_062.py", "EXPECTED_CLOSEOUT_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_closeout_evidence_trustworthy (func-sha)",
    # verify_infra_063 (P276) — 锁 bootstrap_verify_self_checker.py file / func / const sha
    ("verify_infra_063.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_063.py", "EXPECTED_BOOTSTRAP_FILE_SHA"):
        "scripts/bootstrap_verify_self_checker.py (file-sha)",
    ("verify_infra_063.py", "EXPECTED_CANARY_FUNC_SHA"):
        "scripts/bootstrap_verify_self_checker.py:run_canary_self_check (func-sha)",
    ("verify_infra_063.py", "EXPECTED_CANARY_CONST_SHA"):
        "scripts/bootstrap_verify_self_checker.py:_CANARY_EXPECTED_SHA (const-lock)",
    # verify_infra_064 (P284) — 锁 smoke_history.jsonl 不再 tracked 政策
    ("verify_infra_064.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    # verify_infra_065 (P294-R4) — 锁 verify_baseline_fail_claims helper
    ("verify_infra_065.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_065.py", "EXPECTED_BASELINE_FAIL_CROSS_CHECK_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_baseline_fail_claims (func-sha)",
}

# infra-039-backlog-source-file-aware: 真自锁 const 名 (target = source_file 自身)
# 当 (source_file, const_name) 未命中 _PER_FILE_LOCKS 时, 这里命中则返回 source_file 自锁标记。
_PER_FILE_SELF_LOCKS: set = {
    "EXPECTED_FINGERPRINT",  # verify_infra_022 / verify_infra_028 自我 fingerprint
}


def _scan_file(path: Path) -> List[Dict[str, str]]:
    """返回 [{const, sha, line}] 列表."""
    out: List[Dict[str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return out
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _RE_SINGLELINE.match(line)
        if m:
            out.append({"const": m.group(1), "sha": m.group(2), "line": str(i + 1)})
            i += 1
            continue
        m2 = _RE_TUPLE_OPEN.match(line)
        if m2 and i + 1 < len(lines):
            mh = _RE_TUPLE_HEX.match(lines[i + 1])
            if mh:
                out.append({"const": m2.group(1), "sha": mh.group(1), "line": str(i + 1)})
                i += 2
                continue
        i += 1
    return out


def _infer_target(const: str, source_file: str) -> str:
    """从常量名推断锁定 target. 无把握时返回 '<unknown>'.

    infra-039-backlog: 先查 _KNOWN_NON_NUMERIC_TARGETS, 再走 _RE_VERIFY_HINT,
    再尝试 V<NNN>_ / BUMP_<NNN>_ 数字提取, 最后是 lib / self / unknown 兜底。
    """
    # 0) source-file-aware 查表 (infra-039-backlog)
    # 同一 const 名在不同 verify 脚本中锁不同 target 的歧义场景, 必须结合 source_file 锁定
    src_base = source_file.rsplit("/", 1)[-1] if source_file else ""
    if src_base and (src_base, const) in _PER_FILE_LOCKS:
        return _PER_FILE_LOCKS[(src_base, const)]
    # 0.5) source-file self-lock (target = source_file 自身)
    if src_base and const in _PER_FILE_SELF_LOCKS:
        return f"{source_file} (self file-sha)"
    # 1) 非数字常量名查表 (fingerprint / bump-only / dump 自锁)
    if const in _KNOWN_NON_NUMERIC_TARGETS:
        return _KNOWN_NON_NUMERIC_TARGETS[const]
    # 2) 优先 _verify_lib 锁
    if _RE_LIB_HINT.search(const):
        if "FILE" in const:
            return "scripts/_verify_lib.py (file-sha)"
        if "FUNC" in const:
            # 形如 EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA → func_sha_by_name
            tail = const.replace("EXPECTED_", "").replace("_FUNC_SHA", "")
            return f"scripts/_verify_lib.py:{tail.lower()} (func-sha)"
        if "HELPER" in const:
            return "scripts/_verify_lib.py (helper sha)"
        return "scripts/_verify_lib.py"
    # 3) VERIFY_<num>_ 交叉锁
    m = _RE_VERIFY_HINT.search(const)
    if m:
        num = m.group(1)
        candidates = sorted(SCRIPTS.glob(f"verify_*_{num}.py"))
        if candidates:
            rels = [str(c.relative_to(REPO)) for c in candidates]
            return f"{', '.join(rels)} (file-sha)"
        return f"scripts/verify_*_{num}.py (file-sha; not found)"
    # 4) V<NNN>_ / BUMP_<NNN>_ 数字提取 (infra-039-backlog)
    mv = _RE_V_NUM_HINT.search(const) or _RE_BUMP_HINT.search(const)
    if mv:
        num = mv.group(1).lstrip("0") or "0"
        # 3 位 V018 -> 18; 直接补齐 3 位查找
        num3 = num.zfill(3)
        candidates = sorted(SCRIPTS.glob(f"verify_*_{num3}.py"))
        if candidates:
            rels = [str(c.relative_to(REPO)) for c in candidates]
            return f"{', '.join(rels)} (file-sha)"
        return f"scripts/verify_*_{num3}.py (file-sha; not found)"
    # 5) 自锁 — checker 自身函数 sha
    if "CHECKER" in const or "SELF" in const:
        return f"{source_file} self-checker (func-sha)"
    return "<unknown target>"


def _scan_v4_sha_json() -> Optional[Dict[str, str]]:
    if not V4_SHA_JSON.is_file():
        return None
    try:
        data = json.loads(V4_SHA_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data.get("targets") or {}


def build_graph() -> Dict:
    """返回结构:
    {
      "v4_sha_json": {"path": str, "targets": {path: sha, ...}, "count": int} | None,
      "locks": [
        {"source": "scripts/verify_xxx.py", "const": "...", "sha": "...", "target": "..."},
        ...
      ],
    }
    """
    graph: Dict = {"v4_sha_json": None, "locks": []}
    v4 = _scan_v4_sha_json()
    if v4 is not None:
        graph["v4_sha_json"] = {
            "path": str(V4_SHA_JSON.relative_to(REPO)),
            "count": len(v4),
            "targets": v4,
        }

    files: List[Path] = []
    files.extend(sorted(SCRIPTS.glob("verify_*.py")))
    lib = SCRIPTS / "_verify_lib.py"
    if lib.is_file():
        files.append(lib)
    for path in files:
        rel = str(path.relative_to(REPO))
        for entry in _scan_file(path):
            target = _infer_target(entry["const"], rel)
            graph["locks"].append({
                "source": rel,
                "const": entry["const"],
                "sha": entry["sha"],
                "line": entry["line"],
                "target": target,
            })
    return graph


def render_text(graph: Dict) -> str:
    out: List[str] = []
    out.append("=== V4 SHA-LOCK GRAPH ===")
    out.append("")
    v4 = graph.get("v4_sha_json")
    if v4:
        out.append(f"{v4['path']} ({v4['count']} targets):")
        for tgt, sha in sorted(v4["targets"].items()):
            out.append(f"  ├─ {tgt:<40s} @ {sha[:16]}...")
        out.append("")
        out.append("scripts/verify_infra_034.py V4")
        out.append(f"  └─→ reads {v4['path']} (sort_keys canonical)")
        out.append(f"      └─→ locks {v4['count']} verify scripts file-sha")
        out.append("")
    # 按 source 文件分组
    locks = graph.get("locks", [])
    by_src: Dict[str, List[Dict]] = {}
    for lock in locks:
        by_src.setdefault(lock["source"], []).append(lock)
    for src in sorted(by_src.keys()):
        items = by_src[src]
        out.append(f"{src}")
        for it in items:
            out.append(f"  └─→ {it['const']} = {it['sha'][:16]}...  (L{it['line']})")
            out.append(f"      └─→ locks {it['target']}")
        out.append("")
    out.append(f"=== SUMMARY: {len(locks)} sha-lock constants across {len(by_src)} files ===")
    return "\n".join(out)


def _classify_node(node_id: str) -> str:
    """根据 node_id 判定 classDef 类别 (infra-039-backlog-mermaid-classDef-styling).

    返回 className: hub / verify / lib / dump / module / unknown
    """
    if node_id == "v4_sha_json":
        return "hub"
    if node_id.startswith("unknown_"):
        return "unknown"
    if node_id == "_verify_lib":
        return "lib"
    # infra-P285-classifier-recognize-lib-func-locks: dump_v4_sha_graph 与
    # dump_reverse_sha_lock_index 同属 dump 工具家族, 同归 dump classDef。
    if node_id == "dump_v4_sha_graph" or node_id == "dump_reverse_sha_lock_index":
        return "dump"
    if node_id.startswith("verify_"):
        return "verify"
    return "module"


def render_mermaid(graph: Dict) -> str:
    """渲染 mermaid graph LR 语法, 可直接 paste 到 mermaid.live.

    infra-039-backlog-mermaid-classDef-styling: 在末尾 emit 6 类 classDef 与
    每个节点的 class 关联, 视觉上分层 hub / verify / lib / dump / module / unknown。
    """
    out: List[str] = []
    out.append("graph LR")
    nodes: set = set()

    def _node_id(label: str) -> str:
        # sanitize: 只留字母数字下划线
        nid = re.sub(r"[^A-Za-z0-9_]", "_", label)
        if nid and nid[0].isdigit():
            nid = "n_" + nid
        return nid or "anon"

    # v4_sha.json hub 节点
    v4 = graph.get("v4_sha_json")
    if v4:
        hub = "v4_sha_json"
        if hub not in nodes:
            out.append(f'    {hub}["v4_sha.json ({v4["count"]} targets)"]')
            nodes.add(hub)
        for tgt in sorted(v4["targets"].keys()):
            # tgt 形如 scripts/verify_xxx.py
            stem = Path(tgt).stem
            nid = _node_id(stem)
            if nid not in nodes:
                out.append(f'    {nid}["{stem}"]')
                nodes.add(nid)
            out.append(f"    {hub} -->|file-sha| {nid}")

    # 反向 sha lock 边: source --|const|--> target
    for lock in graph.get("locks", []):
        src_stem = Path(lock["source"]).stem
        src_id = _node_id(src_stem)
        if src_id not in nodes:
            out.append(f'    {src_id}["{src_stem}"]')
            nodes.add(src_id)
        target = lock["target"]
        # target 可能含多文件 (逗号分隔) 或描述; 取第一个 .py stem
        m = re.search(r"([A-Za-z0-9_]+)\.py", target)
        if m:
            tgt_stem = m.group(1)
            tgt_id = _node_id(tgt_stem)
            if tgt_id not in nodes:
                out.append(f'    {tgt_id}["{tgt_stem}"]')
                nodes.add(tgt_id)
            out.append(f"    {src_id} -->|{lock['const']}| {tgt_id}")
        else:
            # unknown target — 用占位节点
            tgt_id = _node_id("unknown_" + lock["const"])
            if tgt_id not in nodes:
                out.append(f'    {tgt_id}["?{lock["const"]}"]')
                nodes.add(tgt_id)
            out.append(f"    {src_id} -->|{lock['const']}| {tgt_id}")

    # infra-039-backlog-mermaid-classDef-styling: classDef 声明 + 每节点 class 关联
    # infra-048-backlog-mermaid-palette-extract: 色值来自 _MERMAID_PALETTE, 固定顺序
    for _cls in ("hub", "verify", "lib", "dump", "module", "unknown"):
        _p = _MERMAID_PALETTE[_cls]
        out.append(
            f"    classDef {_cls} fill:{_p['fill']},stroke:{_p['stroke']},color:{_p['color']};"
        )
    for nid in sorted(nodes):
        out.append(f"    class {nid} {_classify_node(nid)};")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="V4 sha-lock graph dump")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--mermaid", action="store_true", help="emit mermaid graph LR syntax")
    ap.add_argument("--out", type=str, default=None, help="write output to file")
    args = ap.parse_args()
    if args.json and args.mermaid:
        print("[dump_v4_sha_graph] --json and --mermaid are mutually exclusive", file=sys.stderr)
        return 2
    graph = build_graph()
    if args.json:
        output = json.dumps(graph, indent=2, sort_keys=True)
    elif args.mermaid:
        output = render_mermaid(graph)
    else:
        output = render_text(graph)
    if args.out:
        Path(args.out).write_text(output + ("\n" if not output.endswith("\n") else ""), encoding="utf-8")
        print(f"[dump_v4_sha_graph] wrote {args.out} ({len(output)} bytes)", flush=True)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
