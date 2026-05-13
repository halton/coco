#!/usr/bin/env python3
"""infra-006 verify: GitHub Actions verify matrix CI 配置自检.

V1 .github/workflows/verify-matrix.yml 存在且合法 YAML
V2 workflow 包含 smoke / verify-vision / verify-interact / verify-companion / verify-robot / verify-infra 全 6 个 job 名
V3 matrix python 含 3.13
V4 scripts/run_verify_all.py 存在可执行且 import 不抛
V5 run_verify_all.py --list 输出含全部 verify_*.py 文件名
V6 init.sh 支持 COCO_CI=1 环境变量（grep 检测）
V7 run_verify_all.py --dry-run 输出预期任务列表
V8 workflow 包含 actions/upload-artifact 步骤上传 evidence
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "verify-matrix.yml"
RUNNER = REPO_ROOT / "scripts" / "run_verify_all.py"
INIT_SH = REPO_ROOT / "init.sh"


def _load_yaml(p: Path):
    try:
        import yaml  # type: ignore
    except ImportError:
        # 退路：用极简 parser 抽 job 名 + python-version 字符串
        return None
    with open(p) as f:
        return yaml.safe_load(f)


def v1_workflow_yaml() -> None:
    print("V1: workflow yaml 存在 + 合法")
    assert WORKFLOW.exists(), f"workflow 缺失: {WORKFLOW}"
    data = _load_yaml(WORKFLOW)
    if data is None:
        # PyYAML 不在；至少校验文件非空可读
        text = WORKFLOW.read_text()
        assert "jobs:" in text and "name:" in text, "workflow yaml 缺关键节"
        print("  ok: PyYAML 未装，做了文本子串校验")
        return
    assert isinstance(data, dict), f"yaml root 非 dict: {type(data)}"
    assert "jobs" in data, "yaml 缺 jobs"
    print(f"  ok: {WORKFLOW.relative_to(REPO_ROOT)} yaml 合法")


def v2_jobs_present() -> None:
    print("V2: 6 个 job 名齐")
    text = WORKFLOW.read_text()
    needed = ["smoke:", "verify-vision:", "verify-interact:",
              "verify-companion:", "verify-robot:", "verify-infra:"]
    missing = [j for j in needed if j not in text]
    assert not missing, f"workflow 缺 job: {missing}"
    print(f"  ok: jobs = {[j.rstrip(':') for j in needed]}")


def v3_python_313() -> None:
    print("V3: matrix python 含 3.13")
    text = WORKFLOW.read_text()
    assert "3.13" in text, "workflow 未声明 python 3.13"
    print("  ok: '3.13' 出现于 workflow")


def v4_runner_importable() -> None:
    print("V4: scripts/run_verify_all.py 存在 + import 不抛")
    assert RUNNER.exists(), f"runner 缺失: {RUNNER}"
    spec = importlib.util.spec_from_file_location("run_verify_all", RUNNER)
    assert spec and spec.loader, "spec build 失败"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert hasattr(mod, "discover"), "缺 discover()"
    assert hasattr(mod, "classify"), "缺 classify()"
    print("  ok: runner import + discover/classify 可用")


def v5_list_covers_all() -> None:
    print("V5: --list 含全部 verify_*.py")
    on_disk = sorted(
        p.name for p in (REPO_ROOT / "scripts").glob("verify_*.py")
        if p.name != "verify_infra_006.py"
    )
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--list"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"--list 退出码 {proc.returncode}: {proc.stderr}"
    out = proc.stdout
    missing = [n for n in on_disk if n not in out]
    assert not missing, f"--list 漏掉: {missing}"
    print(f"  ok: --list 覆盖 {len(on_disk)} 个 verify_*.py")


def v6_init_sh_ci() -> None:
    print("V6: init.sh 支持 COCO_CI=1")
    text = INIT_SH.read_text()
    assert "COCO_CI" in text, "init.sh 未引用 COCO_CI"
    print("  ok: init.sh 含 COCO_CI 分支")


def v7_dry_run() -> None:
    print("V7: --dry-run 输出预期任务列表")
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--dry-run"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"--dry-run rc={proc.returncode}: {proc.stderr}"
    out = proc.stdout
    assert "DRY-RUN" in out and "would run" in out, "dry-run 输出缺关键标记"
    # 至少含一个已知 verify
    assert "verify_infra_005.py" in out, "dry-run 输出未列 verify_infra_005.py"
    print("  ok: --dry-run 列出任务且未实际执行")


def v8_upload_artifact() -> None:
    print("V8: workflow 含 actions/upload-artifact 上传 evidence")
    text = WORKFLOW.read_text()
    assert "actions/upload-artifact" in text, "workflow 未用 actions/upload-artifact"
    assert "evidence/**" in text, "workflow 未上传 evidence/** 路径"
    print("  ok: upload-artifact 步骤 + evidence/** 路径出现")


def main() -> int:
    checks = [v1_workflow_yaml, v2_jobs_present, v3_python_313,
              v4_runner_importable, v5_list_covers_all, v6_init_sh_ci,
              v7_dry_run, v8_upload_artifact]
    failed = []
    for fn in checks:
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed.append(fn.__name__)
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL: {type(e).__name__}: {e}")
            failed.append(fn.__name__)
    print()
    if failed:
        print(f"FAIL: {len(failed)}/{len(checks)} — {failed}")
        return 1
    print(f"PASS: {len(checks)}/{len(checks)} (infra-006 verify matrix CI)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
