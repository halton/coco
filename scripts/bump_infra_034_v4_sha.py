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
import os
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
    # 保留 version + comment + targets, 强制 canonical 序 (infra-035-backlog #5.50):
    # sort_keys=True 锁顶层 key 字典序 + targets 子 dict key 字典序, 保证不同
    # Python 版本 / dict 实现下输出 bytewise 一致, 避免 v4_sha.json 键序漂移
    # 干扰 diff 与 sha 锁链.
    out = {
        "version": data.get("version", 1),
        "comment": data.get("comment", ""),
        "targets": new_table,
    }
    # 原子写 (infra-035-backlog-bump-atomic-write #1.53):
    # 先写 sibling tmp 文件再 os.replace, 保证 v4_sha.json 任意时刻都是完整
    # 可解析状态. 防 SIGKILL / 磁盘满 / 解释器崩溃留半截 JSON 破坏 sha 锁链
    # 根节点的"读得到但不一致"边界. 失败时清理 tmp 不污染目录.
    payload = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp_path = V4_SHA_JSON.with_suffix(V4_SHA_JSON.suffix + ".tmp")
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, V4_SHA_JSON)
    except Exception:
        # 失败回滚: 清理 tmp (若存在), 原 V4_SHA_JSON 未被触碰
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        raise
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
