#!/usr/bin/env python3
"""bootstrap_verify_self_checker: 新建 verify_*.py 时计算 V1 self-checker func sha 的脚手架.

infra-P273-new-verify-self-checker-fixup-protocol (phase-36 P275): P273 暴露过
"新建 verify 脚本时 EXPECTED_V4_CHECKER_FUNC_SHA 锁的是写完前的旧 sha, 导致
V1 self-check 首跑 FAIL, 作者错误归因到 pre-existing" 的失真模式。本 helper 让
作者在脚本全部写完后, 一行 invoke 直接拿到该回填的 func sha 字面, 避免靠
"先跑脚本看 FAIL 错误信息抠 sha" 的隐式流程。

用法::

    # 默认: 找名为 v4_behavior 的顶层函数, 输出 EXPECTED_V4_CHECKER_FUNC_SHA = "..." 行
    python scripts/bootstrap_verify_self_checker.py \\
        --verify-script scripts/verify_infra_056.py

    # 指定函数名
    python scripts/bootstrap_verify_self_checker.py \\
        --verify-script scripts/verify_infra_056.py \\
        --func-name v4_behavior

    # JSON 输出 (便于编辑器/agent 程序化消费)
    python scripts/bootstrap_verify_self_checker.py \\
        --verify-script scripts/verify_infra_056.py \\
        --json

本脚本是 *只读* 工具, **不会**修改 --verify-script 源文件; 仅打印建议的常量字面,
作者自行 paste 回填。设计上故意保持 paste-not-write, 避免 "脚本写过头改坏作者意图".

退出码 0 = OK / 2 = 用户用法错误 / 1 = 找不到目标函数等运行期错误.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 让本脚本能 import 同目录的 _verify_lib (与 verify_*.py 一致约定)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _verify_lib import func_sha_by_name  # noqa: E402


def compute_self_checker_sha(verify_script: Path, func_name: str) -> str:
    """计算 verify_script 中 func_name 顶层函数的 canonical sha256.

    薄 wrapper, 直接转发到 ``_verify_lib.func_sha_by_name``, 让本 helper 与
    verify_*.py V1 自锁链路完全同源 (verify 脚本本身也走 func_sha_by_name).
    """
    return func_sha_by_name(verify_script, func_name)


def build_constant_line(const_name: str, sha_hex: str) -> str:
    """返回形如 ``CONST_NAME = "abc...64..."`` 的建议字面。"""
    return f'{const_name} = "{sha_hex}"'


def main() -> int:
    ap = argparse.ArgumentParser(
        description="compute V1 self-checker func sha for new verify_*.py scripts"
    )
    ap.add_argument(
        "--verify-script",
        required=True,
        type=str,
        help="path to the new verify_*.py whose self-checker func sha should be computed",
    )
    ap.add_argument(
        "--func-name",
        type=str,
        default="v4_behavior",
        help="top-level function name to hash (default: v4_behavior)",
    )
    ap.add_argument(
        "--const-name",
        type=str,
        default="EXPECTED_V4_CHECKER_FUNC_SHA",
        help="suggested constant name to paste into the verify script (default: EXPECTED_V4_CHECKER_FUNC_SHA)",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="emit JSON {verify_script, v4_func_name, actual_func_sha} instead of text",
    )
    args = ap.parse_args()

    verify_path = Path(args.verify_script)
    if not verify_path.is_file():
        print(
            f"[bootstrap_verify_self_checker] ERROR: --verify-script not found: {verify_path}",
            file=sys.stderr,
        )
        return 1

    try:
        sha_hex = compute_self_checker_sha(verify_path, args.func_name)
    except ValueError as e:
        print(f"[bootstrap_verify_self_checker] ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # pragma: no cover (defensive)
        print(f"[bootstrap_verify_self_checker] ERROR: unexpected: {e!r}", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "verify_script": str(verify_path),
            "v4_func_name": args.func_name,
            "actual_func_sha": sha_hex,
            "suggested_const": args.const_name,
            "paste_line": build_constant_line(args.const_name, sha_hex),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"[bootstrap_verify_self_checker] verify_script={verify_path} "
            f"func={args.func_name}"
        )
        print(f"[bootstrap_verify_self_checker] actual_func_sha={sha_hex}")
        print("[bootstrap_verify_self_checker] paste this line back into the script:")
        print(f"    {build_constant_line(args.const_name, sha_hex)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
