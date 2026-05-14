#!/usr/bin/env python3
"""infra-014-fu-1: actionlint dry-run hook for .github/workflows/*.yml

封装外部二进制 `actionlint`（https://github.com/rhysd/actionlint）做 GitHub
Actions workflow 语法 / 表达式 / shellcheck 静态检查。default-OFF dev/CI 工具，
不属运行期 gate。

行为：
  - actionlint 已装：对 `.github/workflows/*.yml` 跑 actionlint，rc != 0 → fail
  - actionlint 未装：优雅 skip + 警告（不让 verify fail），打印安装提示
  - --strict：未装时 fail（CI 用）

返回码：
  0  PASS（包括优雅 skip 路径）
  1  actionlint 报错 / strict 模式下未装
  2  内部异常（保留）

用法：
  python scripts/lint_workflows.py
  python scripts/lint_workflows.py --strict           # 未装即 fail
  python scripts/lint_workflows.py --files A.yml B.yml

安装 actionlint（macOS / Linux）：
  brew install actionlint                # macOS / Linuxbrew
  bash <(curl https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash)
  # 或 go install github.com/rhysd/actionlint/cmd/actionlint@latest
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

INSTALL_HINT = (
    "actionlint 未安装。安装方式：\n"
    "  brew install actionlint                # macOS\n"
    "  bash <(curl https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash)\n"
    "  go install github.com/rhysd/actionlint/cmd/actionlint@latest\n"
    "未装时本 lint 优雅 skip（不阻断 verify），CI 中可用 --strict 强制 fail。"
)


def _list_workflows() -> list[Path]:
    if not WORKFLOWS_DIR.is_dir():
        return []
    return sorted(p for p in WORKFLOWS_DIR.glob("*.yml") if p.is_file())


def actionlint_available() -> str | None:
    """返回 actionlint 可执行路径，未装返回 None。"""
    return shutil.which("actionlint")


def run_actionlint(files: list[Path]) -> tuple[int, str, str]:
    """对给定 workflow 文件跑 actionlint dry-run。返回 (rc, stdout, stderr)。

    actionlint 本身就是静态分析，不会真触发 workflow，因此天然属 dry-run 范畴。
    """
    bin_path = actionlint_available()
    if bin_path is None:
        return -1, "", "actionlint not in PATH"
    if not files:
        return 0, "no workflow files\n", ""
    cmd = [bin_path, *[str(f) for f in files]]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def main() -> int:
    ap = argparse.ArgumentParser(
        description="infra-014-fu-1 actionlint dry-run hook",
    )
    ap.add_argument(
        "--files",
        nargs="*",
        type=Path,
        default=None,
        help="workflow 文件列表；省略则扫 .github/workflows/*.yml",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="actionlint 未安装即 fail（CI 用）；默认优雅 skip",
    )
    args = ap.parse_args()

    files = args.files if args.files else _list_workflows()
    bin_path = actionlint_available()

    if bin_path is None:
        if args.strict:
            print(f"[lint_workflows] FAIL: {INSTALL_HINT}", file=sys.stderr)
            return 1
        print(f"[lint_workflows] SKIP actionlint 未安装（非 strict 模式）。")
        print(INSTALL_HINT)
        return 0

    if not files:
        print("[lint_workflows] OK no workflow files to check")
        return 0

    rc, out, err = run_actionlint(files)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    if rc == 0:
        print(
            f"[lint_workflows] OK actionlint PASS on {len(files)} workflow(s)"
            f" ({bin_path})"
        )
        return 0
    print(
        f"[lint_workflows] FAIL actionlint rc={rc} ({bin_path}, "
        f"{len(files)} workflow(s))",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
