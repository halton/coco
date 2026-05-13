#!/usr/bin/env python3
"""infra-006 verify: GitHub Actions verify matrix CI 配置自检.

V1 .github/workflows/verify-matrix.yml 存在且合法 YAML
V2 workflow 包含 smoke / verify-vision / verify-interact / verify-companion /
   verify-robot / verify-infra / verify-audio / verify-publish 全 8 个 job 名
V3 matrix python 含 3.13
V4 scripts/run_verify_all.py 存在可执行且 import 不抛
V5 run_verify_all.py --list 输出含全部 verify_*.py 文件名（除 EXCLUDED）
V6 init.sh 支持 COCO_CI=1 环境变量（grep 检测）
V7 run_verify_all.py --dry-run 输出预期任务列表
V8 workflow 包含 actions/upload-artifact 步骤上传 evidence
V9 每个 verify-XXX job 在 run 中调用 run_verify_all.py 且 --area 与 job 名匹配
   （抓 phase-1 矩阵覆盖盲点）
"""

from __future__ import annotations

import importlib.util
import re
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
    print("V2: 8 个 job 名齐")
    text = WORKFLOW.read_text()
    needed = ["smoke:", "verify-vision:", "verify-interact:",
              "verify-companion:", "verify-robot:", "verify-infra:",
              "verify-audio:", "verify-publish:"]
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
    print("V5: --list 含全部 verify_*.py（除 EXCLUDED）")
    # 从 runner 模块 import EXCLUDED，保证两边共用同一份
    spec = importlib.util.spec_from_file_location("run_verify_all", RUNNER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    excluded: frozenset[str] = getattr(mod, "EXCLUDED")
    assert "verify_infra_006.py" in excluded, \
        "EXCLUDED 必须含 verify_infra_006.py（避免矩阵自检递归）"
    on_disk = sorted(
        p.name for p in (REPO_ROOT / "scripts").glob("verify_*.py")
        if p.name not in excluded
    )
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--list"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"--list 退出码 {proc.returncode}: {proc.stderr}"
    out = proc.stdout
    missing = [n for n in on_disk if n not in out]
    assert not missing, f"--list 漏掉: {missing}"
    print(f"  ok: --list 覆盖 {len(on_disk)} 个 verify_*.py（EXCLUDED={sorted(excluded)}）")


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


def v9_jobs_call_runner_with_matching_area() -> None:
    """每个 verify-XXX job 必须在 run 段调用 run_verify_all.py 且 --area 与
    job 名匹配。phase-1 矩阵覆盖盲点正是因为 run 段用了 ``--filter NNN`` 把
    area 内大量 verify 静默筛掉，本检查直接卡这个。
    """
    print("V9: 每个 verify-XXX job 调 run_verify_all.py 且 --area 匹配 job 名")
    try:
        import yaml  # type: ignore
    except ImportError:
        print("  SKIP: PyYAML 未装；V9 需结构化解析")
        return
    with open(WORKFLOW) as f:
        data = yaml.safe_load(f)
    jobs = data.get("jobs", {})
    # 期待映射：job 名 → expected --area 值
    expected = {
        "verify-vision": "vision",
        "verify-interact": "interact",
        "verify-companion": "companion",
        "verify-robot": "robot",
        "verify-infra": "infra",
        "verify-audio": "audio",
        "verify-publish": "publish",
    }
    failed: list[str] = []
    for job_name, area in expected.items():
        if job_name not in jobs:
            failed.append(f"{job_name}: 缺 job")
            continue
        steps = jobs[job_name].get("steps") or []
        runs = " \n".join(
            str(s.get("run", "")) for s in steps if isinstance(s, dict)
        )
        if "run_verify_all.py" not in runs:
            failed.append(f"{job_name}: run 段未调 run_verify_all.py")
            continue
        # 必须显式 --area <area>，否则可能误用 --filter
        if not re.search(rf"--area\s+{re.escape(area)}\b", runs):
            failed.append(f"{job_name}: run 段缺 '--area {area}'")
            continue
        # 禁止 --filter NNN 这种硬编码列表（rework L0-1 要求）
        if re.search(r"--filter\s+\S", runs):
            failed.append(f"{job_name}: 不允许 --filter（参考 rework L0-1）")
            continue
        # robot job 不允许 continue-on-error: true（L0-2 要求）
        if job_name == "verify-robot":
            if jobs[job_name].get("continue-on-error") is True:
                failed.append("verify-robot: 仍带 continue-on-error: true (L0-2)")
    assert not failed, "V9 失败:\n  - " + "\n  - ".join(failed)
    print(f"  ok: {len(expected)} 个 verify-XXX job 全部调 runner --area 且匹配 job 名")


def main() -> int:
    checks = [v1_workflow_yaml, v2_jobs_present, v3_python_313,
              v4_runner_importable, v5_list_covers_all, v6_init_sh_ci,
              v7_dry_run, v8_upload_artifact,
              v9_jobs_call_runner_with_matching_area]
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
