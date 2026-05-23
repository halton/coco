#!/usr/bin/env python3
"""dump_reverse_sha_lock_index: 反向 sha lock 索引视图工具 (read-only).

infra-038-backlog-reverse-sha-lock-index (phase-34 #5.34, P268): V6 反向 sha
lock 已落地多轮 (P259/P264/P265), ``scripts/`` 下散落 21+ 条形如
``VERIFY_<NNN>_*SHA*`` 的反向锁常量, 但缺一个统一的索引/清单视图供运维或
audit 时快速 overview——"哪个 verify 文件锁哪个 target 文件/函数的 sha?"

本工具复用既有 helper, 纯只读:

- ``scripts/_verify_lib.py:scan_reverse_sha_locks``: 扫所有顶层 64-hex
  反向锁常量, 含 ``VERIFY_<NNN>`` 子串 (P264 抽出)
- ``scripts/_verify_lib.py:live_verify_sha_set``: 计算 scripts 下所有
  verify_*.py + _verify_lib.py 的实时 sha (P264)
- ``scripts/_verify_lib.py:verify_reverse_sha_lock_consistency``: 比对
  scanned vs live, 给出 orphans (sha 不再存在于 live set, 漂移信号)
- ``scripts/dump_v4_sha_graph.py:_infer_target``: 复用其常量名 → target
  推断逻辑 (infra-039-backlog source-file-aware 增强后版本)

输出形式:

- ``--text`` (默认): 按 verify_file 分组的人读表格
- ``--json``: 机读 dict; 含 entries[], orphans[], stats
- ``--check``: 调 verify_reverse_sha_lock_consistency, all_match → rc 0, 否则 1
  - 默认 (隐含 --text) 输出 stdout 文本 summary + orphan 行
  - 与 ``--json`` 协同 → stdout 输出 JSON consistency report
    (schema ``reverse_sha_lock_consistency/v1``), rc 语义不变
- ``--text`` / ``--json`` 互斥 (输出格式选择, rc=2)

INFRA_050_SORT_ORDER_CONTRACT
-----------------------------
``--json`` 输出 ``payload.stats.sort_order`` 字段是 **外部稳定排序契约
标签** (external stability contract label), 当前值锁定为字面串
``"verify_file,lineno,lock_name"``. 它**不是**动态计算出来的字段, 而是
一个静态标签, 由本工具向消费者承诺: ``payload.entries`` 列表已显式
``sorted(entries, key=lambda e: (e["file"], e["lineno"], e["const_name"]))``
排序, 字段名映射如下:

- 外部标签 ``verify_file``  ↔  JSON 内部字段 ``entry["file"]``
- 外部标签 ``lineno``       ↔  JSON 内部字段 ``entry["lineno"]``
- 外部标签 ``lock_name``    ↔  JSON 内部字段 ``entry["const_name"]``

消费者契约: 给定同一份源码, ``--json`` stdout 必须 bytewise 稳定 (verify_infra_050
V4_bytewise_stable 锁 3 轮跑一致); 后续若调整排序键, 必须同步改 ``sort_order``
字符串字面量, verify_infra_050 V0_sort_order_symbols + V4_sort_order_field
会同时漂移, 防止悄悄改契约。

退出码: 0 = 正常列出 (或 --check 通过); 1 = --check 失败 / 解析错.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

# 把 scripts/ 置于 sys.path 头, 与既有 verify_*.py 一致 (infra-034)
sys.path.insert(0, str(SCRIPTS))

from _verify_lib import (  # noqa: E402
    live_verify_sha_set,
    scan_reverse_sha_locks,
    verify_reverse_sha_lock_consistency,
)

# 通过 importlib 加载 dump_v4_sha_graph._infer_target (避免运行其 main)
_DUMP_V4_PATH = SCRIPTS / "dump_v4_sha_graph.py"
_spec = importlib.util.spec_from_file_location("_dump_v4_sha_graph_for_index", _DUMP_V4_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - import-time safety
    raise RuntimeError(f"cannot load dump_v4_sha_graph from {_DUMP_V4_PATH}")
_dump_v4_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dump_v4_mod)
infer_target = _dump_v4_mod._infer_target  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Core: build entries
# ---------------------------------------------------------------------------
def build_entries() -> List[Dict[str, Any]]:
    """扫 scripts/ 下反向 sha lock, 富化 target 推断.

    返回 list[dict]: 每条 {file, lineno, const_name, sha_hex, sha_short,
    inferred_target, kind}.

    infra-049-backlog-dump-index-expose-kind (phase-49 #4.49): 透传
    ``scan_reverse_sha_locks`` 已带的 ``kind`` 字段 (``verify_id`` /
    ``expected_pattern``), 便于消费者 (audit / CI) 区分两类反向锁;
    缺省 ``kind`` 项 fallback 为 ``"unknown"`` (防御性, 当前 helper
    保证 100% 命中两类之一).
    """
    scanned = scan_reverse_sha_locks(SCRIPTS)
    out: List[Dict[str, Any]] = []
    for item in scanned:
        const_name = item["const_name"]
        source_file = item["file"]  # 形如 "scripts/verify_infra_048.py"
        try:
            target = infer_target(const_name, source_file)
        except Exception as e:  # 防御性: 推断失败不阻断输出
            target = f"<infer error: {e!r}>"
        out.append({
            "file": source_file,
            "lineno": item["lineno"],
            "const_name": const_name,
            "sha_hex": item["sha_hex"],
            "sha_short": item["sha_hex"][:8],
            "inferred_target": target,
            "kind": item.get("kind", "unknown"),
        })
    return out


def group_entries_by_file(entries: List[Dict[str, Any]]) -> "OrderedDict[str, List[Dict[str, Any]]]":
    """按 verify_file 分组 entries, 保持稳定排序 (按 file 名升序, file 内按 lineno).
    """
    grouped: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for e in sorted(entries, key=lambda x: (x["file"], x["lineno"])):
        grouped.setdefault(e["file"], []).append(e)
    return grouped


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_text(entries: List[Dict[str, Any]], live_count: int) -> str:
    """人读分组表格. 每行: ``  <const_name>=<sha8> [<kind>] -> <inferred_target>``.

    infra-049-backlog-dump-index-expose-kind (phase-49 #4.49): 行内增加
    ``[<kind>]`` 标签 (verify_id / expected_pattern), 与 JSON 输出的 kind
    字段对齐, 便于人读时区分两类反向锁。
    """
    grouped = group_entries_by_file(entries)
    lines: List[str] = []
    lines.append("# Reverse sha lock index (infra-038-backlog, P268)")
    lines.append(
        f"# scanned_reverse_locks={len(entries)} live_verify_files={live_count}"
    )
    lines.append("")
    for file, items in grouped.items():
        lines.append(f"{file}:  ({len(items)} lock{'s' if len(items) != 1 else ''})")
        for it in items:
            lines.append(
                f"  L{it['lineno']:>4}  {it['const_name']}={it['sha_short']}  [{it.get('kind', 'unknown')}]  ->  {it['inferred_target']}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_json(entries: List[Dict[str, Any]], live_count: int, consistency: Dict[str, Any]) -> str:
    """机读 JSON: {entries, orphans, stats}.

    infra-049-backlog-render-json-sort-stability (P269): entries 显式按
    ``(file, lineno, const_name)`` 升序排序, 锁定 byte-wise 稳定性; stats 块
    新增 ``sort_order`` 字段显式标注该排序契约, 防止后续无意改动悄悄改掉.

    infra-049-backlog-dump-index-expose-kind (phase-49 #4.49): schema 升
    ``reverse_sha_lock_index/v2``, entries 每条新增 ``kind`` 字段
    (``verify_id`` / ``expected_pattern``, 缺省 ``unknown``), stats 新增
    ``kind_breakdown`` 子 dict 统计两类锁数量。schema v1 → v2 是 additive
    破坏性变更 (新增 key, 旧字段不动), 消费者按 schema 字段路由解析即可。
    """
    sorted_entries = sorted(
        entries,
        key=lambda e: (e["file"], e["lineno"], e["const_name"]),
    )
    kind_breakdown: Dict[str, int] = {}
    for e in sorted_entries:
        k = e.get("kind", "unknown")
        kind_breakdown[k] = kind_breakdown.get(k, 0) + 1
    payload: Dict[str, Any] = {
        "schema": "reverse_sha_lock_index/v2",
        "stats": {
            "scanned_count": len(sorted_entries),
            "live_verify_files": live_count,
            "orphan_count": len(consistency.get("orphans", [])),
            "all_match": bool(consistency.get("all_match", False)),
            "sort_order": "verify_file,lineno,lock_name",
            "kind_breakdown": dict(sorted(kind_breakdown.items())),
        },
        "entries": sorted_entries,
        "orphans": consistency.get("orphans", []),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def render_check_json(consistency: Dict[str, Any]) -> str:
    """机读 JSON consistency report (infra-049-backlog-check-coalesce-json, P270).

    供 ``--check --json`` 协同模式使用: rc 语义保持 (all_match → 0, 否则 1),
    但 stdout 输出结构化 consistency report 便于 CI / 运维抓取.

    schema: ``reverse_sha_lock_consistency/v1``; 字段:
    - scanned_count: scan_reverse_sha_locks 命中的反向锁条目数
    - live_count: live_verify_sha_set 实时枚举的 verify 文件数
    - orphans: list[dict] (file/lineno/const_name/sha_hex/target_id, 可能 [])
    - all_match: bool, scanned ⊆ live 时 True
    """
    payload: Dict[str, Any] = {
        "schema": "reverse_sha_lock_consistency/v1",
        "scanned_count": int(consistency.get("scanned_count", 0)),
        "live_count": int(consistency.get("live_count", 0)),
        "orphans": list(consistency.get("orphans", [])),
        "all_match": bool(consistency.get("all_match", False)),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dump_reverse_sha_lock_index",
        description=(
            "Reverse sha lock index over scripts/verify_*.py + _verify_lib.py. "
            "Read-only. Reuses _verify_lib + dump_v4_sha_graph._infer_target."
        ),
    )
    # --text / --json 互斥: 输出格式选择 (P270 调整, 原三档互斥)
    mx = p.add_mutually_exclusive_group()
    mx.add_argument("--text", action="store_true", help="human-readable grouped table (default)")
    mx.add_argument("--json", action="store_true", help="machine-readable JSON payload")
    # --check 独立 flag: 行为开关; 可与 --json 协同 (rc 语义不变, stdout 走 JSON report)
    p.add_argument(
        "--check",
        action="store_true",
        help=(
            "run reverse-sha-lock consistency check; rc 0 = all_match, rc 1 = orphans. "
            "with --json: stdout = JSON consistency report (schema reverse_sha_lock_consistency/v1)"
        ),
    )
    return p


def cmd_check(as_json: bool = False) -> int:
    result = verify_reverse_sha_lock_consistency(SCRIPTS)
    if as_json:
        sys.stdout.write(render_check_json(result))
        return 0 if result["all_match"] else 1
    summary = (
        f"[dump_reverse_sha_lock_index][--check] "
        f"scanned={result['scanned_count']} live={result['live_count']} "
        f"orphans={len(result['orphans'])} all_match={result['all_match']}"
    )
    print(summary, flush=True)
    if not result["all_match"]:
        for o in result["orphans"]:
            print(
                f"  ORPHAN {o['file']}:L{o['lineno']} {o['const_name']}="
                f"{o['sha_hex'][:8]} target_id={o['target_id']}",
                flush=True,
            )
        return 1
    return 0


def main(argv: List[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.check:
        return cmd_check(as_json=bool(args.json))
    entries = build_entries()
    live = live_verify_sha_set(SCRIPTS)
    if args.json:
        consistency = verify_reverse_sha_lock_consistency(SCRIPTS)
        sys.stdout.write(render_json(entries, len(live), consistency))
        return 0
    # default: text
    sys.stdout.write(render_text(entries, len(live)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
