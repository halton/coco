#!/usr/bin/env python3
"""
infra-035: bump helper for evidence/infra-034/v4_sha.json.

verify_infra_034.py V4 不再 hardcode 5 个目标 verify 的 sha256, 而是从
evidence/infra-034/v4_sha.json 读取. 当任一目标 verify 被有意修改并落盘,
本脚本一键重算并写回 JSON, 保留 version + comment + key 排序.

退出码:
  0  一致 (无需更新) / dry-run 成功 / 写回成功
  2  v4_sha.json 缺失或无法解析
  3  schema 不合规 (缺 targets 或类型错)
  4  写回后再读校验不一致
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Dict

REPO = Path(__file__).resolve().parents[1]
V4_SHA_JSON = REPO / "evidence" / "infra-034" / "v4_sha.json"


def compute_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json() -> dict:
    if not V4_SHA_JSON.is_file():
        print(f"FAIL: {V4_SHA_JSON} 不存在", file=sys.stderr)
        sys.exit(2)
    try:
        data = json.loads(V4_SHA_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"FAIL: {V4_SHA_JSON} 解析失败: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict) or not isinstance(data.get("targets"), dict):
        print("FAIL: schema 不合规 (缺 targets dict)", file=sys.stderr)
        sys.exit(3)
    return data


def bump(dry_run: bool) -> int:
    data = load_json()
    targets: Dict[str, str] = data["targets"]

    new_table: Dict[str, str] = {}
    diffs = []
    for rel in sorted(targets.keys()):
        p = REPO / rel
        if not p.is_file():
            print(f"FAIL: target {rel} 不存在", file=sys.stderr)
            return 3
        new_sha = compute_sha(p)
        old_sha = targets[rel]
        new_table[rel] = new_sha
        if old_sha != new_sha:
            diffs.append((rel, old_sha, new_sha))
        print(f"{rel}: old={old_sha[:16]} new={new_sha[:16]} {'CHANGED' if old_sha != new_sha else 'same'}")

    if not diffs:
        print("OK: 已一致, 无需更新")
        return 0

    if dry_run:
        for rel, old, new in diffs:
            print(f"DRY-RUN: {rel} {old} -> {new} (未落盘)")
        return 0

    data["targets"] = new_table
    # 保留 version + comment + 排序 targets
    out = {
        "version": data.get("version", 1),
        "comment": data.get("comment", ""),
        "targets": new_table,
    }
    V4_SHA_JSON.write_text(
        json.dumps(out, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    # 写回再校验
    after = json.loads(V4_SHA_JSON.read_text(encoding="utf-8"))
    if after.get("targets") != new_table:
        print("FAIL: 写回后再读不一致", file=sys.stderr)
        return 4
    print(f"OK: 已更新 {V4_SHA_JSON.name} ({len(diffs)} 项变更)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只打印差异, 不落盘")
    args = parser.parse_args()
    return bump(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
