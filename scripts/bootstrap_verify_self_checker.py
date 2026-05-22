#!/usr/bin/env python3
"""bootstrap_verify_self_checker: 新建 verify_*.py 时计算 V1 self-checker func sha 的脚手架.

infra-P273-new-verify-self-checker-fixup-protocol (phase-36 P275): P273 暴露过
"新建 verify 脚本时 EXPECTED_V4_CHECKER_FUNC_SHA 锁的是写完前的旧 sha, 导致
V1 self-check 首跑 FAIL, 作者错误归因到 pre-existing" 的失真模式。本 helper 让
作者在脚本全部写完后, 一行 invoke 直接拿到该回填的 func sha 字面, 避免靠
"先跑脚本看 FAIL 错误信息抠 sha" 的隐式流程。

infra-P276-bootstrap-helper-self-mutant-detection (phase-37 #4.37): P275 引入
本 helper 时缺自我退化检测 — 若 ``compute_self_checker_sha`` 被改坏 (例如
直接 ``return ""``) 上游用户不会立刻发现, 与 P291 同质 (helper 退化成 noop).
本轮加 self-mutant canary: 内嵌一段最小合法 verify 脚本 + 其期望 v4_behavior
canonical sha; ``--canary`` 子模式跑 ``run_canary_self_check()``, 实测与期望
不符即报 mutant_detected=True 并 exit 2.

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

    # 自检 mutant canary (无 --verify-script): ok=True/False + mutant_detected
    python scripts/bootstrap_verify_self_checker.py --canary

    # 计算当前 _CANARY_VERIFY_SRC 对应的期望 sha (P297, 编辑 canary 时使用)
    python scripts/bootstrap_verify_self_checker.py --compute-canary-sha

本脚本是 *只读* 工具, **不会**修改 --verify-script 源文件; 仅打印建议的常量字面,
作者自行 paste 回填。设计上故意保持 paste-not-write, 避免 "脚本写过头改坏作者意图".

退出码 0 = OK / 2 = 用户用法错误或 canary mutant_detected / 1 = 找不到目标函数等运行期错误.

## 编辑 _CANARY_VERIFY_SRC / _CANARY_EXPECTED_SHA 的流程 (P297)

如需修改 canary 嵌入源 (``_CANARY_VERIFY_SRC``) 或期望 sha
(``_CANARY_EXPECTED_SHA``), 必须按以下顺序避免 canary mutant 模式漏报/误报:

1. 修改 ``_CANARY_VERIFY_SRC`` 字符串内容 (e.g. 调整 sample mutant 代码).
2. 立即跑::

       python scripts/bootstrap_verify_self_checker.py --compute-canary-sha

   该子命令复用 ``run_canary_self_check`` 内部相同路径: 把 ``_CANARY_VERIFY_SRC``
   写入临时文件, 调 ``compute_self_checker_sha(tmp, "v4_behavior")``, 把结果以
   纯 64 hex 单行打印到 stdout. 这正是应当填入 ``_CANARY_EXPECTED_SHA`` 的值.
3. 把第 2 步打印的 sha 填入 ``_CANARY_EXPECTED_SHA`` 常数.
4. 跑::

       python scripts/bootstrap_verify_self_checker.py --canary

   期望 rc=0 (canary self check PASS, ``ok=True mutant_detected=False``).
5. 跑::

       python scripts/verify_infra_063.py

   期望 ALL PASS — V4.2 用 monkeypatch 注入 mutant compute_self_checker_sha,
   ``--canary`` 子模式应 rc=2 (mutant 实跑 case 仍然被检出).
6. 由于本文件 file sha 改变, ``verify_infra_069`` 锁的 ``EXPECTED_BOOTSTRAP_FILE_SHA``
   也需 bump (见 ``scripts/dump_v4_sha_graph.py`` cascade graph).

**反例 (导致 canary 失效)**:

- 改 ``_CANARY_VERIFY_SRC`` 后忘记同步 ``_CANARY_EXPECTED_SHA``:
  ``--canary`` 子模式立即 rc=2 + stderr ``MUTANT DETECTED`` 的 *false positive* —
  helper 没坏, 是 expected const 没跟上.
- 既改 src 又同步 sha, 但 src 改成 "等价于真 compute_self_checker_sha 的逻辑"
  (例如把 v4_behavior 改成无函数体 / 改函数名), 导致 V4.2 mutant 检测条件不再触发 —
  ``verify_infra_063`` V4.2 实跑模式应抓到此类退化.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Dict

# 让本脚本能 import 同目录的 _verify_lib (与 verify_*.py 一致约定)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _verify_lib import func_sha_by_name  # noqa: E402


# ---------------------------------------------------------------------------
# Self-mutant canary (infra-P276):
# ``_CANARY_VERIFY_SRC`` 是一段最小合法 verify 脚本片段, 含名为 ``v4_behavior``
# 的顶层函数 + ``if __name__`` 守卫. ``_CANARY_EXPECTED_SHA`` 锁该函数当前 ast
# .unparse canonical sha. 任何对 ``compute_self_checker_sha`` (或其转发链)
# 的退化改动 (return ""/return None/算错算法), 跑 ``--canary`` 即可被 catch.
# ---------------------------------------------------------------------------
_CANARY_VERIFY_SRC: str = '''def v4_behavior():
    x = 1 + 2
    return x

if __name__ == "__main__":
    v4_behavior()
'''

_CANARY_EXPECTED_SHA: str = (
    "5d611a08c7d9a7df9e79b6da1e1a5aabfb88c9e5fc373917f612f69f3580d249"
)


def compute_self_checker_sha(verify_script: Path, func_name: str) -> str:
    """计算 verify_script 中 func_name 顶层函数的 canonical sha256.

    薄 wrapper, 直接转发到 ``_verify_lib.func_sha_by_name``, 让本 helper 与
    verify_*.py V1 自锁链路完全同源 (verify 脚本本身也走 func_sha_by_name).
    """
    return func_sha_by_name(verify_script, func_name)


def build_constant_line(const_name: str, sha_hex: str) -> str:
    """返回形如 ``CONST_NAME = "abc...64..."`` 的建议字面。"""
    return f'{const_name} = "{sha_hex}"'


def run_canary_self_check() -> Dict[str, object]:
    """跑内嵌 canary 验脚本, 校验 ``compute_self_checker_sha`` 输出未退化.

    把 ``_CANARY_VERIFY_SRC`` 写入临时文件, 调 ``compute_self_checker_sha``
    取实测 sha, 与 ``_CANARY_EXPECTED_SHA`` 比对.

    Returns:
        ok=True 时: ``{'ok': True, 'mutant_detected': False, 'expected': ..., 'actual': ...}``
        ok=False 时: ``{'ok': False, 'mutant_detected': True, 'expected': ..., 'actual': ...}``
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(_CANARY_VERIFY_SRC)
        tmp_path = Path(f.name)
    try:
        try:
            actual = compute_self_checker_sha(tmp_path, "v4_behavior")
        except Exception as e:
            return {
                "ok": False,
                "mutant_detected": True,
                "expected": _CANARY_EXPECTED_SHA,
                "actual": f"<exception: {e!r}>",
            }
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    if actual != _CANARY_EXPECTED_SHA:
        return {
            "ok": False,
            "mutant_detected": True,
            "expected": _CANARY_EXPECTED_SHA,
            "actual": actual,
        }
    return {
        "ok": True,
        "mutant_detected": False,
        "expected": _CANARY_EXPECTED_SHA,
        "actual": actual,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="compute V1 self-checker func sha for new verify_*.py scripts"
    )
    ap.add_argument(
        "--verify-script",
        required=False,
        type=str,
        default=None,
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
    ap.add_argument(
        "--canary",
        action="store_true",
        help="run self-mutant canary check (no --verify-script needed); exit 2 if mutant_detected",
    )
    ap.add_argument(
        "--compute-canary-sha",
        action="store_true",
        help="print sha that _CANARY_EXPECTED_SHA should hold for current _CANARY_VERIFY_SRC (P297)",
    )
    args = ap.parse_args()

    # infra-P297: compute-canary-sha 模式 — 复用 canary 计算路径打印期望 sha
    if args.compute_canary_sha:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(_CANARY_VERIFY_SRC)
            tmp_path = Path(f.name)
        try:
            sha_hex = compute_self_checker_sha(tmp_path, "v4_behavior")
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
        print(sha_hex)
        return 0

    # infra-P276: canary 自检模式 (与 --verify-script 互斥)
    if args.canary:
        if args.verify_script is not None:
            print(
                "[bootstrap_verify_self_checker] ERROR: --canary and --verify-script are mutually exclusive",
                file=sys.stderr,
            )
            return 2
        result = run_canary_self_check()
        print(
            "[bootstrap_verify_self_checker][canary] "
            f"ok={result['ok']} mutant_detected={result['mutant_detected']}"
        )
        print(
            f"[bootstrap_verify_self_checker][canary] expected={result['expected']}"
        )
        print(
            f"[bootstrap_verify_self_checker][canary] actual  ={result['actual']}"
        )
        if not result["ok"]:
            print(
                "[bootstrap_verify_self_checker][canary] MUTANT DETECTED — "
                "compute_self_checker_sha 输出与内嵌期望不符, helper 可能被改坏",
                file=sys.stderr,
            )
            return 2
        return 0

    # 原有 --verify-script 模式
    if args.verify_script is None:
        print(
            "[bootstrap_verify_self_checker] ERROR: --verify-script is required (or use --canary)",
            file=sys.stderr,
        )
        return 2

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
