#!/usr/bin/env python3
"""
robot-031: verify_robot_028.py V4 BUMP_EXPECTED_SHA 递归 bump helper
(scripts/bump_verify_028_self_hash.py).

verify-only / default-OFF 友好。无业务源码改动。

robot-028 在 verify_robot_028.py V4 中硬锁了 scripts/bump_verify_027_self_hash.py
的 sha256（字面常量 `BUMP_EXPECTED_SHA = ("<64 hex>")`）。当
bump_verify_027_self_hash.py 升级（注释/逻辑微调）落盘后，该常量需要同步刷新；
人工 sed 容易漂移。本助手脚本自动:

  1. 计算 scripts/bump_verify_027_self_hash.py 的当前 sha256
  2. 在 scripts/verify_robot_028.py 中 in-place 替换
     `BUMP_EXPECTED_SHA = ( "<64 hex>" )` 的字面值为当前 sha
  3. 支持 --dry-run（仅打印差异，不改文件）

这是 robot-028 递归 meta-lock 的下一层:
  verify_robot_025  -- verify_robot_027 V4 hardcoded ---┐
                                                        |
  bump_verify_027_self_hash <-- 自动同步上一层字面常量    |
                                                        |
  bump_verify_027_self_hash -- verify_robot_028 V4 ----┘ <-- 本脚本同步
                                                            该字面常量

退出码:
  0  一致（无需更新）或已更新成功
  非 0 异常（找不到常量字面 / 文件缺失 / 等等）
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUMP_027 = REPO / "scripts" / "bump_verify_027_self_hash.py"
VERIFY_028 = REPO / "scripts" / "verify_robot_028.py"

# verify_robot_028.py 中的字面写法:
#   BUMP_EXPECTED_SHA = (
#       "c29927bc3f920a761228440505ecdbe16969812704bfca0e3cabb77560132540"
#   )
CONST_NAME = "BUMP_EXPECTED_SHA"
CONST_RE = re.compile(
    r"(" + re.escape(CONST_NAME) + r"\s*=\s*\(\s*\")([0-9a-fA-F]{64})(\"\s*\))",
)


def compute_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bump(dry_run: bool) -> int:
    if not BUMP_027.is_file():
        print(f"FAIL: {BUMP_027} 不存在", file=sys.stderr)
        return 2
    if not VERIFY_028.is_file():
        print(f"FAIL: {VERIFY_028} 不存在", file=sys.stderr)
        return 2

    new_sha = compute_sha(BUMP_027)
    src = VERIFY_028.read_text(encoding="utf-8")
    m = CONST_RE.search(src)
    if not m:
        print(
            f"FAIL: 在 {VERIFY_028.name} 中找不到字面常量 "
            f"{CONST_NAME} = (\"...64hex...\")",
            file=sys.stderr,
        )
        return 3
    old_sha = m.group(2)

    print(f"bump_verify_027_self_hash.py sha256 = {new_sha}")
    print(f"verify_robot_028.py {CONST_NAME} (old) = {old_sha}")

    if old_sha == new_sha:
        print("OK: 已一致, 无需更新")
        return 0

    new_src = src[: m.start(2)] + new_sha + src[m.end(2):]
    if dry_run:
        print(f"DRY-RUN: 将替换 {old_sha} -> {new_sha} (未落盘)")
        return 0

    VERIFY_028.write_text(new_src, encoding="utf-8")
    after_sha_in_file = CONST_RE.search(
        VERIFY_028.read_text(encoding="utf-8")
    ).group(2)
    if after_sha_in_file != new_sha:
        print("FAIL: 写回后再读字面不一致", file=sys.stderr)
        return 4
    print(f"OK: 已更新 {VERIFY_028.name} {CONST_NAME} -> {new_sha}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印差异, 不落盘",
    )
    args = parser.parse_args()
    return bump(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
