"""infra-publish-flow 静态/dry-run 验证。

跑法：
  uv run python scripts/verify_publish.py

做什么：
  1. `reachy_mini.apps.app check .` — 官方 check（含临时 venv 安装/卸载）
  2. 列出 publish candidate artifacts（路径 + 大小）：
       pyproject.toml / README.md / index.html / style.css / coco/ 包整体大小
  3. entry_points 字段静态检查（避免 check 改坑后没及时同步）
  4. `coco.main` 可 import 到 class 定义阶段（不实例化、不起 daemon）

不做什么：
  - 不真的 publish（不上传 HF Space、不 git push）
  - 不起 Control.app（macOS 桌面应用，自动化里没法操作）
  - 不连真机

退出码：
  - 0 = 全通过
  - 1 = 任一项失败

被 init.sh 间接通过 smoke_publish 调用；也可独立跑用于 evidence 收集。
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _bytes_human(n: int) -> str:
    """B / KB / MB 简单换算，便于 evidence 阅读。"""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024  # type: ignore[assignment]
    return f"{n} B"


def _dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def step_check_cli() -> bool:
    """跑官方 reachy_mini.apps.app check．成功标准：returncode == 0。"""
    print("==> Step 1: reachy_mini.apps.app check .")
    result = subprocess.run(
        [sys.executable, "-m", "reachy_mini.apps.app", "check", str(REPO_ROOT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        print("FAIL: reachy_mini.apps.app check 退出码非零")
        return False
    print("OK: reachy_mini.apps.app check 通过")
    return True


def step_list_artifacts() -> bool:
    """列出 publish 时会被 git push 到 HF Space 的关键文件 + 大小。

    publish 实际机制是 git push 到 HuggingFace Space（见
    .venv/lib/python3.13/site-packages/reachy_mini/apps/assistant.py:publish），
    并不产出 wheel。所以"artifacts"指仓库里会被推上去的文件。
    """
    print("\n==> Step 2: publish candidate artifacts")
    required = ["pyproject.toml", "README.md", "index.html", "style.css"]
    missing = []
    for rel in required:
        p = REPO_ROOT / rel
        if not p.exists():
            missing.append(rel)
            print(f"  MISSING: {rel}")
            continue
        print(f"  {rel:20s}  {_bytes_human(p.stat().st_size)}")

    pkg_dir = REPO_ROOT / "coco"
    if not pkg_dir.is_dir():
        missing.append("coco/")
        print("  MISSING: coco/ 包目录")
    else:
        print(f"  {'coco/ (recursive)':20s}  {_bytes_human(_dir_size(pkg_dir))}")

    if missing:
        print(f"FAIL: 缺少 publish artifacts: {missing}")
        return False
    print("OK: publish artifacts 齐全")
    return True


def step_entry_points() -> bool:
    """静态校验 pyproject.toml 中 reachy_mini_apps entry-point。"""
    print("\n==> Step 3: entry_points 静态检查")
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    eps = (
        data.get("project", {})
        .get("entry-points", {})
        .get("reachy_mini_apps", {})
    )
    if "coco" not in eps:
        print(f"FAIL: 未找到 entry-point 'coco'，实际: {eps}")
        return False
    expected = "coco.main:Coco"
    if eps["coco"] != expected:
        print(f"FAIL: entry-point 期望 '{expected}' 实际 '{eps['coco']}'")
        return False
    keywords = data.get("project", {}).get("keywords", [])
    if "reachy-mini-app" not in keywords:
        print(f"FAIL: keywords 应含 'reachy-mini-app'，实际: {keywords}")
        return False
    print(f"OK: entry-point coco={eps['coco']}, keywords 含 reachy-mini-app")
    return True


def step_import_coco_main() -> bool:
    """import coco.main，确认 Coco 类可加载（不实例化、不起 daemon）。"""
    print("\n==> Step 4: import coco.main")
    try:
        mod = importlib.import_module("coco.main")
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: import coco.main 失败 ({e})")
        return False
    cls = getattr(mod, "Coco", None)
    if cls is None:
        print("FAIL: coco.main 没有 Coco 类")
        return False
    # 校验继承关系（不需要实例化）
    from reachy_mini import ReachyMiniApp

    if not issubclass(cls, ReachyMiniApp):
        print("FAIL: Coco 不继承 ReachyMiniApp")
        return False
    print("OK: coco.main:Coco 已加载，且继承 ReachyMiniApp")
    return True


def main() -> int:
    print(f"verify_publish: repo={REPO_ROOT}")
    steps = [step_check_cli, step_list_artifacts, step_entry_points, step_import_coco_main]
    failed = [s.__name__ for s in steps if not s()]
    print()
    if failed:
        print(f"==> FAIL: {failed}")
        return 1
    print("==> PASS: infra-publish-flow dry-run 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
