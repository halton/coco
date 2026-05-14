#!/usr/bin/env python3
"""infra-015 verify: verify-matrix.yml lint pre-job 落地 + actionlint binary 接入 CI.

phase-12 infra-014-fu-1 落地了 scripts/lint_workflows.py（actionlint dry-run hook）
+ scripts/lint_paths_filter.py。本 feature 把它们接入 .github/workflows/
verify-matrix.yml 的 lint pre-job，并让所有 verify-* matrix job 通过 needs 链
依赖 lint，使 workflow 自身坏掉时立即 fail-fast。

V1 verify-matrix.yml 含 `lint:` job
V2 lint job 含 actionlint setup step（rhysd/actionlint@v1）
V3 lint job 含 lint_paths_filter.py 调用
V4 lint job 含 lint_workflows.py --strict 调用
V5 changes / smoke / verify-* matrix job 通过 needs 链依赖 lint
V6 本机跑 lint_paths_filter.py rc=0
V7 本机跑 lint_workflows.py --strict rc=0（要求本机已装 actionlint）
V8 actionlint 直接对 verify-matrix.yml dry-run rc=0（自校验本 feature 改动合法）
V9 verify-matrix.yml 整体仍合法 YAML（防止 lint job 注入破坏顶层结构）
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "verify-matrix.yml"
LINT_PATHS = REPO_ROOT / "scripts" / "lint_paths_filter.py"
LINT_WORKFLOWS = REPO_ROOT / "scripts" / "lint_workflows.py"


def _read_workflow_text() -> str:
    assert WORKFLOW.exists(), f"workflow 缺失: {WORKFLOW}"
    return WORKFLOW.read_text()


def v1_lint_job_present() -> None:
    print("V1: verify-matrix.yml 含 lint: job")
    text = _read_workflow_text()
    assert "\n  lint:\n" in text, "未找到 lint job 顶层 key"
    assert "name: lint workflows + paths-filter (infra-015)" in text, \
        "lint job name 缺失或不一致"
    print("  ok: lint job 已落地")


def v2_actionlint_setup() -> None:
    print("V2: lint job 含 actionlint setup step")
    text = _read_workflow_text()
    assert "rhysd/actionlint@v1" in text, "缺 rhysd/actionlint@v1 action"
    assert "id: actionlint" in text, "actionlint step 缺 id（后续 step 引用 output 用）"
    print("  ok: rhysd/actionlint@v1 binary 接入")


def v3_lint_paths_call() -> None:
    print("V3: lint job 含 lint_paths_filter.py 调用")
    text = _read_workflow_text()
    assert "python scripts/lint_paths_filter.py" in text, \
        "lint job 未调用 lint_paths_filter.py"
    print("  ok: lint_paths_filter.py 已接入")


def v4_lint_workflows_call() -> None:
    print("V4: lint job 含 lint_workflows.py --strict 调用")
    text = _read_workflow_text()
    assert "python scripts/lint_workflows.py --strict" in text, \
        "lint job 未调用 lint_workflows.py --strict"
    print("  ok: lint_workflows.py --strict 已接入")


def v5_needs_chain() -> None:
    print("V5: changes / smoke / verify-* job 通过 needs 链依赖 lint")
    text = _read_workflow_text()
    # changes 与 smoke 直接 needs: lint
    assert "\n    needs: lint\n" in text, \
        "changes / smoke 缺 needs: lint（lint fail-fast 链断裂）"
    # verify-* 走 needs: [smoke, changes]，smoke 又依赖 lint，传递依赖即可
    # 但显式校验 smoke / changes 自身 needs lint 已在上面断言。
    # 再校验 verify- 的 needs 至少含 smoke（保留原链）
    needed_jobs = [
        "verify-vision:", "verify-interact:", "verify-companion:",
        "verify-audio:", "verify-robot:", "verify-infra:", "verify-publish:",
    ]
    for job in needed_jobs:
        assert job in text, f"job {job} 缺失"
    # 抓所有 needs: [smoke, changes] 行
    assert text.count("needs: [smoke, changes]") >= 7, \
        "verify-* job needs: [smoke, changes] 数 < 7（链可能断）"
    print("  ok: needs 链 lint → smoke/changes → verify-* 完整")


def v6_local_lint_paths_rc0() -> None:
    print("V6: 本机跑 lint_paths_filter.py rc=0")
    r = subprocess.run(
        [sys.executable, str(LINT_PATHS)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert r.returncode == 0, (
        f"lint_paths_filter.py rc={r.returncode}\n"
        f"stdout={r.stdout}\nstderr={r.stderr}"
    )
    print(f"  ok: rc=0\n    {r.stdout.strip().splitlines()[-1]}")


def v7_local_lint_workflows_strict_rc0() -> None:
    print("V7: 本机跑 lint_workflows.py --strict rc=0（要求 actionlint 已装）")
    bin_path = shutil.which("actionlint")
    assert bin_path is not None, (
        "本机未装 actionlint；CI 通过 rhysd/actionlint@v1 注入，本地用 "
        "`brew install actionlint` 装。"
    )
    r = subprocess.run(
        [sys.executable, str(LINT_WORKFLOWS), "--strict"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert r.returncode == 0, (
        f"lint_workflows.py --strict rc={r.returncode}\n"
        f"stdout={r.stdout}\nstderr={r.stderr}"
    )
    print(f"  ok: rc=0 ({bin_path})")


def v8_actionlint_verify_matrix_rc0() -> None:
    print("V8: actionlint 直接对 verify-matrix.yml dry-run rc=0（本 feature 改动自校验）")
    bin_path = shutil.which("actionlint")
    assert bin_path is not None, "未装 actionlint，跳过条件已在 V7 断言失败"
    r = subprocess.run(
        [bin_path, str(WORKFLOW)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert r.returncode == 0, (
        f"actionlint rc={r.returncode}\n"
        f"stdout={r.stdout}\nstderr={r.stderr}"
    )
    print(f"  ok: rc=0 ({bin_path} {WORKFLOW.relative_to(REPO_ROOT)})")


def v9_yaml_still_valid() -> None:
    print("V9: verify-matrix.yml 整体仍合法 YAML")
    try:
        import yaml  # type: ignore
    except ImportError:
        text = _read_workflow_text()
        assert "jobs:" in text and "lint:" in text, "yaml 关键节缺失"
        print("  ok: PyYAML 未装，做了文本子串校验")
        return
    with open(WORKFLOW) as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), f"yaml root 非 dict: {type(data)}"
    assert "jobs" in data, "yaml 缺 jobs"
    jobs = data["jobs"]
    assert "lint" in jobs, "lint job 解析后缺失"
    # 校验 lint 在 needs 中被引用
    for jname in ("changes", "smoke"):
        j = jobs.get(jname)
        assert j is not None, f"job {jname} 缺失"
        needs = j.get("needs")
        assert needs == "lint" or (isinstance(needs, list) and "lint" in needs), \
            f"job {jname} 未 needs lint（实际 needs={needs!r}）"
    print(f"  ok: yaml 合法，jobs={len(jobs)}，lint 已被 changes/smoke needs")


CHECKS = [
    v1_lint_job_present,
    v2_actionlint_setup,
    v3_lint_paths_call,
    v4_lint_workflows_call,
    v5_needs_chain,
    v6_local_lint_paths_rc0,
    v7_local_lint_workflows_strict_rc0,
    v8_actionlint_verify_matrix_rc0,
    v9_yaml_still_valid,
]


def main() -> int:
    failed = []
    for fn in CHECKS:
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL: {e}", file=sys.stderr)
            failed.append(fn.__name__)
        except Exception as e:  # pragma: no cover
            print(f"  ERROR: {fn.__name__}: {e!r}", file=sys.stderr)
            failed.append(fn.__name__)
    if failed:
        print(f"\ninfra-015 verify FAIL: {failed}", file=sys.stderr)
        return 1
    print(f"\ninfra-015 verify PASS ({len(CHECKS)}/{len(CHECKS)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
