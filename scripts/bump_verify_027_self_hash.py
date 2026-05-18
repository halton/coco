#!/usr/bin/env python3
"""
robot-028: verify_robot_027.py V4 self-hash bump helper.

verify-only / default-OFF 友好。无业务源码改动。

verify_robot_027.py V4 锁定 verify_robot_025.py 的 sha256（字面常量 `VERIFY_025_EXPECTED_SHA`）。
当 verify_robot_025.py 升级（新增 sentinel / 调整行号锚定 hash 等）落盘后，该常量需要同步刷新；
人工 sed 容易漂移。本助手脚本自动:

  1. 计算 scripts/verify_robot_025.py 的当前 sha256
  2. 在 scripts/verify_robot_027.py 中 in-place 替换 `VERIFY_025_EXPECTED_SHA = "<64 hex>"`
     的字面值为当前 sha
  3. 支持 --dry-run（仅打印差异，不改文件）

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
VERIFY_025 = REPO / "scripts" / "verify_robot_025.py"
VERIFY_027 = REPO / "scripts" / "verify_robot_027.py"

# 字面常量行: `    "<64-hex>"` 紧跟在 `VERIFY_025_EXPECTED_SHA = (` 之后
# verify_robot_027.py 中实际写法:
#   VERIFY_025_EXPECTED_SHA = (
#       "1c1cba08214e6a510ace376f0f9c06fd0eb21063c13c71442850a0d997f0ef6d"
#   )
CONST_NAME = "VERIFY_025_EXPECTED_SHA"
# 匹配整段 (含括号与字面 hex), group(1)=旧 hex
CONST_RE = re.compile(
    r"(" + re.escape(CONST_NAME) + r"\s*=\s*\(\s*\")([0-9a-fA-F]{64})(\"\s*\))",
)


def compute_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bump(dry_run: bool) -> int:
    if not VERIFY_025.is_file():
        print(f"FAIL: {VERIFY_025} 不存在", file=sys.stderr)
        return 2
    if not VERIFY_027.is_file():
        print(f"FAIL: {VERIFY_027} 不存在", file=sys.stderr)
        return 2

    new_sha = compute_sha(VERIFY_025)
    src = VERIFY_027.read_text(encoding="utf-8")
    m = CONST_RE.search(src)
    if not m:
        print(
            f"FAIL: 在 {VERIFY_027.name} 中找不到字面常量 {CONST_NAME} = (\"...64hex...\")",
            file=sys.stderr,
        )
        return 3
    old_sha = m.group(2)

    print(f"verify_robot_025.py sha256 = {new_sha}")
    print(f"verify_robot_027.py {CONST_NAME} (old) = {old_sha}")

    if old_sha == new_sha:
        print("OK: 已一致, 无需更新")
        return 0

    new_src = src[: m.start(2)] + new_sha + src[m.end(2):]
    if dry_run:
        print(f"DRY-RUN: 将替换 {old_sha} -> {new_sha} (未落盘)")
        return 0

    VERIFY_027.write_text(new_src, encoding="utf-8")
    # 再读回核对
    after_sha_in_file = CONST_RE.search(
        VERIFY_027.read_text(encoding="utf-8")
    ).group(2)
    if after_sha_in_file != new_sha:
        print("FAIL: 写回后再读字面不一致", file=sys.stderr)
        return 4
    print(f"OK: 已更新 {VERIFY_027.name} {CONST_NAME} -> {new_sha}")
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
