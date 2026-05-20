#!/usr/bin/env python3
"""dump_v4_sha_graph: V4 sha-lock 链路 DAG 可视化工具 (infra-039, verify-only).

扫描 ``scripts/verify_*.py`` 与 ``scripts/_verify_lib.py``, 抽取 ``*_SHA = "<64hex>"``
或 ``*_SHA = ( "<64hex>" )`` 形式的 sha 锁常量, 并尝试推断锁定 target (常见模式:
verify_<id> 交叉锁 / _verify_lib file 锁 / v4_sha.json 表), 以文本或 JSON 形式输出
当前仓库的整套 V4 sha-lock graph, 便于 closeout / Reviewer 一眼把握链路。

用法:
    python scripts/dump_v4_sha_graph.py            # 文本 graph -> stdout
    python scripts/dump_v4_sha_graph.py --json     # JSON dump
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

# 形如  CONST = "abc...64..."  或  CONST = (\n    "abc...64..."\n)
_RE_SINGLELINE = re.compile(
    r'^([A-Z_][A-Z0-9_]*)\s*=\s*["\']([0-9a-f]{64})["\']\s*(?:#.*)?$'
)
_RE_TUPLE_OPEN = re.compile(r'^([A-Z_][A-Z0-9_]*)\s*=\s*\(\s*$')
_RE_TUPLE_HEX = re.compile(r'^\s*["\']([0-9a-f]{64})["\']\s*,?\s*$')

# 用于推断 target: 例如 EXPECTED_VERIFY_024_SHA256 -> verify_interact_024 / verify_robot_024 / verify_infra_024 等
_RE_VERIFY_HINT = re.compile(r'VERIFY_(\d+)_')
_RE_LIB_HINT = re.compile(r'(LIB|VERIFY_LIB|HELPER)')


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
    """从常量名推断锁定 target. 无把握时返回 '<unknown>'."""
    # 优先 _verify_lib 锁
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
    # verify_XXX 交叉锁
    m = _RE_VERIFY_HINT.search(const)
    if m:
        num = m.group(1)
        # 在仓库内找 verify_*_<num>.py
        candidates = sorted(SCRIPTS.glob(f"verify_*_{num}.py"))
        if candidates:
            rels = [str(c.relative_to(REPO)) for c in candidates]
            return f"{', '.join(rels)} (file-sha)"
        return f"scripts/verify_*_{num}.py (file-sha; not found)"
    # 自锁 — checker 自身函数 sha
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


def main() -> int:
    ap = argparse.ArgumentParser(description="V4 sha-lock graph dump")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--out", type=str, default=None, help="write output to file")
    args = ap.parse_args()
    graph = build_graph()
    if args.json:
        output = json.dumps(graph, indent=2, sort_keys=True)
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
