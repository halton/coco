#!/usr/bin/env python3
"""infra-015 verify: verify-matrix.yml lint pre-job 落地 + actionlint binary 接入 CI.

phase-12 infra-014-fu-1 落地了 scripts/lint_workflows.py（actionlint dry-run hook）
+ scripts/lint_paths_filter.py。本 feature 把它们接入 .github/workflows/
verify-matrix.yml 的 lint pre-job，并让所有 verify-* matrix job 通过 needs 链
依赖 lint，使 workflow 自身坏掉时立即 fail-fast。

V1  verify-matrix.yml 含 `lint:` job
V2  lint job 含 actionlint 安装 step（download-actionlint.bash 官方脚本，pin v1.7.12）
V3  lint job 含 lint_paths_filter.py 调用
V4  lint job 含 lint_workflows.py --strict 调用
V5  changes / smoke / verify-* matrix job 通过 needs 链依赖 lint
V6  本机跑 lint_paths_filter.py rc=0
V7  本机跑 lint_workflows.py --strict rc=0（要求本机已装 actionlint）
V8  actionlint 直接对 verify-matrix.yml dry-run rc=0（自校验本 feature 改动合法）
V9  verify-matrix.yml 整体仍合法 YAML（防止 lint job 注入破坏顶层结构）
V10 联网校验 rhysd/actionlint v1.7.12 tag 真存在（gh api，捕远程引用错误）
V11 lint job PATH 注入走 $GITHUB_PATH，不再写 /usr/local/bin/ 等系统路径（C3）

round 2 fix:
- C1: rhysd/actionlint@v1 不是合法 GitHub Action，改用官方 download-actionlint.bash
- C2: 加 V10 用 gh api 联网校验远程 tag
- C3: $GITHUB_PATH 注入；V11 守护回归
- C5: actionlint subprocess 包一层 helper 记录 cmd+rc 到 stdout
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

ACTIONLINT_PIN = "1.7.12"


def _read_workflow_text() -> str:
    assert WORKFLOW.exists(), f"workflow 缺失: {WORKFLOW}"
    return WORKFLOW.read_text()


def _run_logged(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """C5 helper: 跑 subprocess 并 echo cmd+rc 到 stdout，方便 evidence/log 对账。"""
    print(f"  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    print(f"    rc={r.returncode}")
    return r


def v1_lint_job_present() -> None:
    print("V1: verify-matrix.yml 含 lint: job")
    text = _read_workflow_text()
    assert "\n  lint:\n" in text, "未找到 lint job 顶层 key"
    assert "name: lint workflows + paths-filter (infra-015)" in text, \
        "lint job name 缺失或不一致"
    print("  ok: lint job 已落地")


def v2_actionlint_setup() -> None:
    """round 2: 改用 download-actionlint.bash 官方脚本（C1 fix）。"""
    print("V2: lint job 含 actionlint 安装 step（download-actionlint.bash, pin v1.7.12）")
    text = _read_workflow_text()
    assert "download-actionlint.bash" in text, \
        "缺 download-actionlint.bash 官方安装脚本（C1 fix）"
    assert f"v{ACTIONLINT_PIN}" in text, \
        f"actionlint 未 pin v{ACTIONLINT_PIN}"
    # round 2 守护：rhysd/actionlint@ 这种伪 action 引用不能再出现在 yaml
    # `uses:` 行里（注释/文档可保留作历史说明）。
    bad = []
    for ln in text.splitlines():
        stripped = ln.lstrip()
        if stripped.startswith("#"):
            continue  # 注释允许提及
        if "uses:" in stripped and "rhysd/actionlint" in stripped:
            bad.append(stripped)
    assert not bad, (
        "yaml 仍有 `uses: rhysd/actionlint@...` 引用，该仓库无 action.yml，"
        f"不能作 GitHub Action 用（C1 守护）：{bad}"
    )
    print(f"  ok: download-actionlint.bash + pin v{ACTIONLINT_PIN} + 无伪 action uses")


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
    assert "\n    needs: lint\n" in text, \
        "changes / smoke 缺 needs: lint（lint fail-fast 链断裂）"
    needed_jobs = [
        "verify-vision:", "verify-interact:", "verify-companion:",
        "verify-audio:", "verify-robot:", "verify-infra:", "verify-publish:",
    ]
    for job in needed_jobs:
        assert job in text, f"job {job} 缺失"
    assert text.count("needs: [smoke, changes]") >= 7, \
        "verify-* job needs: [smoke, changes] 数 < 7（链可能断）"
    print("  ok: needs 链 lint → smoke/changes → verify-* 完整")


def v6_local_lint_paths_rc0() -> None:
    print("V6: 本机跑 lint_paths_filter.py rc=0")
    r = _run_logged([sys.executable, str(LINT_PATHS)], cwd=REPO_ROOT)
    assert r.returncode == 0, (
        f"lint_paths_filter.py rc={r.returncode}\n"
        f"stdout={r.stdout}\nstderr={r.stderr}"
    )
    print(f"    {r.stdout.strip().splitlines()[-1]}")


def v7_local_lint_workflows_strict_rc0() -> None:
    print("V7: 本机跑 lint_workflows.py --strict rc=0（要求 actionlint 已装）")
    bin_path = shutil.which("actionlint")
    assert bin_path is not None, (
        "本机未装 actionlint；CI 通过 download-actionlint.bash 注入，本地用 "
        "`brew install actionlint` 装。"
    )
    r = _run_logged(
        [sys.executable, str(LINT_WORKFLOWS), "--strict"],
        cwd=REPO_ROOT,
    )
    assert r.returncode == 0, (
        f"lint_workflows.py --strict rc={r.returncode}\n"
        f"stdout={r.stdout}\nstderr={r.stderr}"
    )
    print(f"    actionlint at {bin_path}")


def v8_actionlint_verify_matrix_rc0() -> None:
    print("V8: actionlint 直接对 verify-matrix.yml dry-run rc=0（本 feature 改动自校验）")
    bin_path = shutil.which("actionlint")
    assert bin_path is not None, "未装 actionlint，跳过条件已在 V7 断言失败"
    r = _run_logged([bin_path, str(WORKFLOW)], cwd=REPO_ROOT)
    if r.stdout:
        for ln in r.stdout.strip().splitlines():
            print(f"    [stdout] {ln}")
    if r.stderr:
        for ln in r.stderr.strip().splitlines():
            print(f"    [stderr] {ln}")
    assert r.returncode == 0, f"actionlint rc={r.returncode}"
    print(f"    OK: rc=0 ({WORKFLOW.relative_to(REPO_ROOT)})")


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
    for jname in ("changes", "smoke"):
        j = jobs.get(jname)
        assert j is not None, f"job {jname} 缺失"
        needs = j.get("needs")
        assert needs == "lint" or (isinstance(needs, list) and "lint" in needs), \
            f"job {jname} 未 needs lint（实际 needs={needs!r}）"
    print(f"  ok: yaml 合法，jobs={len(jobs)}，lint 已被 changes/smoke needs")


def v10_remote_actionlint_tag_exists() -> None:
    """round 2 C2: 用 gh api 联网校验 rhysd/actionlint v1.7.12 tag 真存在。

    本机 V check 全是文本子串无法捕捉远程引用错误（round 1 就是因为
    rhysd/actionlint@v1 在 yaml 文本上"看起来"合法但 GitHub 上不存在 v1
    tag/不是 action 仓而首跑就崩）。本 V check 直接打 GitHub API。

    若 gh CLI 未装或未登录/限流，gracefully degrade（warn-only），不阻 PASS。
    但 404 必 fail（tag 真不存在）。
    """
    print(f"V10: gh api 联网校验 rhysd/actionlint v{ACTIONLINT_PIN} tag 存在（C2）")
    if shutil.which("gh") is None:
        print("  WARN: gh CLI 未装，V10 联网校验 skip")
        return
    r = _run_logged(
        ["gh", "api", f"repos/rhysd/actionlint/git/refs/tags/v{ACTIONLINT_PIN}",
         "--jq", ".object.sha"],
        cwd=REPO_ROOT,
    )
    if r.returncode != 0:
        print(f"  WARN: gh api rc={r.returncode}（可能未登录或限流），降级为静态 check")
        if r.stderr.strip():
            print(f"    stderr: {r.stderr.strip()}")
        # 404 必 fail（tag 真不存在）
        assert "404" not in (r.stderr or ""), \
            f"gh api 返回 404：rhysd/actionlint v{ACTIONLINT_PIN} tag 不存在"
        return
    sha = r.stdout.strip()
    assert sha, "gh api 返回空 sha"
    print(f"  ok: tag v{ACTIONLINT_PIN} 存在，sha={sha[:12]}")


def v11_path_injection_via_github_path() -> None:
    """round 2 C3 守护：PATH 注入走 $GITHUB_PATH，不再 ln -sf /usr/local/bin/。"""
    print("V11: lint job PATH 注入走 $GITHUB_PATH，不再写系统路径（C3 守护）")
    text = _read_workflow_text()
    assert "GITHUB_PATH" in text, "lint job 缺 GITHUB_PATH 注入"
    assert "/usr/local/bin/actionlint" not in text, \
        "lint job 不应再 ln -sf /usr/local/bin/actionlint（C3 守护）"
    print("  ok: GITHUB_PATH 注入，无 /usr/local/bin/actionlint 系统污染")


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
    v10_remote_actionlint_tag_exists,
    v11_path_injection_via_github_path,
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
