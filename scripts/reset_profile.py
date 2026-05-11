#!/usr/bin/env python3
"""reset_profile.py — 一键删除本地 UserProfile（companion-004 隐私 reset）.

用法：
    python scripts/reset_profile.py            # 删默认路径下 profile.json
    python scripts/reset_profile.py --dry-run  # 仅打印路径，不删
    COCO_PROFILE_PATH=/tmp/p.json python scripts/reset_profile.py

退出码：
    0 — 成功删除（或文件本就不存在）
    1 — 删除失败（IO 错误等）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.profile import default_profile_path  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Delete local Coco user profile.")
    ap.add_argument("--dry-run", action="store_true", help="只打印路径，不删")
    args = ap.parse_args()

    path = default_profile_path()
    print(f"[reset_profile] target: {path}")
    if not path.exists():
        print("[reset_profile] file does not exist; nothing to do")
        return 0
    if args.dry_run:
        print("[reset_profile] --dry-run set, skip unlink")
        return 0
    try:
        path.unlink()
    except OSError as e:
        print(f"[reset_profile] unlink failed: {e}")
        return 1
    print("[reset_profile] deleted OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
