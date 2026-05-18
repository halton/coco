"""infra-026 verification: verify-matrix.yml runs-on 与 matrix.os 一致性契约审计.

跑法::

    uv run python scripts/verify_infra_026.py

子项（与 feature_list.json infra-026.description 对齐, verify-only 不扩 matrix）：

V0 yaml parse — .github/workflows/verify-matrix.yml 可解析为合法 mapping,
   含 ``jobs`` 顶层键。

V1 runs-on 与 matrix.os 一致性审计 — 枚举所有 jobs.<job>:
   (a) 若 job 有 ``strategy.matrix.os`` (matrix-bearing): 当前 ``runs-on``
       要么是 ``${{ matrix.os }}`` 占位, 要么是硬编码 OS 但 matrix.os 单元素
       且与之相等 (退化等价: 单 os 矩阵跑同 runner). 两种之外即破坏契约;
   (b) 若 job 无 matrix.os (OS-fixed, 如 lint / changes): runs-on 必须是
       合法 GH-runner image 字符串 (不带占位).
   当前 baseline: 所有 matrix-bearing job runs-on 硬编码 ``ubuntu-latest``,
   matrix.os=[``ubuntu-latest``], 退化等价 PASS. 一旦扩 matrix.os 多元素而
   runs-on 未跟着切到 ``${{ matrix.os }}``, V1 会爆 FAIL — 这就是契约锁.

V2 文档化扩 matrix 路径建议 + 命名锁面 — 对所有 matrix-bearing job 的
   upload-artifact name 必须含 ``${{ matrix.os }}`` 占位 (infra-019 落地);
   非 matrix-bearing job 不强制 (但记录). 该项保证未来扩 OS 时 artifact 名
   天然按 OS 分桶, 不冲突.

V3 regression — verify_infra_019 单跑 rc=0 (artifact name 含 matrix.os 的
   既有断言不破).

V4 matrix.os 列表均为合法 GH-runner 集合 — 当前白名单:
   ``ubuntu-latest`` / ``ubuntu-22.04`` / ``ubuntu-24.04`` /
   ``macos-latest`` / ``macos-13`` / ``macos-14`` / ``macos-15`` /
   ``windows-latest`` / ``windows-2022`` / ``windows-2025``.
   任一 matrix.os 元素不在白名单 → FAIL (防 typo / 防 self-hosted 偷渡).

V5 yaml bytewise 锁面 — sha256(verify-matrix.yml) 记入 summary, 不强制等于
   某 baseline (允许后续合规改动), 但快照写盘便于 diff. 同时断言文件存在
   且非空.

retval: 0 全 PASS; 1 任一失败.
evidence: evidence/infra-026/verify_summary.json
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WORKFLOW = ROOT / ".github" / "workflows" / "verify-matrix.yml"
VERIFY_INFRA_019 = ROOT / "scripts" / "verify_infra_019.py"

# GH-hosted runner 合法 image 名 (2026-05 已稳定): 不含 self-hosted / 自定义 label
ALLOWED_RUNNERS: frozenset[str] = frozenset(
    {
        "ubuntu-latest",
        "ubuntu-22.04",
        "ubuntu-24.04",
        "macos-latest",
        "macos-13",
        "macos-14",
        "macos-15",
        "windows-latest",
        "windows-2022",
        "windows-2025",
    }
)

MATRIX_OS_PLACEHOLDER_RE = re.compile(r"\$\{\{\s*matrix\.os\s*\}\}")


def _print(tag: str, msg: str) -> None:
    print(f"[verify_infra_026] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


# ---------------------------------------------------------------------------
# V0: yaml parse
# ---------------------------------------------------------------------------


def v0_yaml_parse() -> Dict[str, Any]:
    if not WORKFLOW.is_file():
        _record("V0_yaml_parse", False, f"{WORKFLOW} missing")
        return {}
    try:
        doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        _record("V0_yaml_parse", False, f"yaml.YAMLError: {e}")
        return {}
    if not isinstance(doc, dict) or "jobs" not in doc or not isinstance(doc["jobs"], dict):
        _record("V0_yaml_parse", False, f"not a mapping with `jobs`: type={type(doc).__name__}")
        return {}
    _record(
        "V0_yaml_parse",
        True,
        f"jobs={sorted(doc['jobs'].keys())}",
    )
    return doc


# ---------------------------------------------------------------------------
# V1: runs-on vs matrix.os consistency
# ---------------------------------------------------------------------------


def _job_matrix_os(job: Dict[str, Any]) -> List[str] | None:
    """Return matrix.os list (or None if absent / malformed)."""
    strategy = job.get("strategy")
    if not isinstance(strategy, dict):
        return None
    matrix = strategy.get("matrix")
    if not isinstance(matrix, dict):
        return None
    os_axis = matrix.get("os")
    if isinstance(os_axis, list) and all(isinstance(x, str) for x in os_axis):
        return list(os_axis)
    return None


def v1_runs_on_matrix_consistency(doc: Dict[str, Any]) -> Dict[str, Any]:
    """枚举 jobs, 分类审计 runs-on vs matrix.os 一致性."""
    audit: Dict[str, Any] = {"matrix_bearing": [], "os_fixed": [], "violations": []}
    if not doc:
        _record("V1_runs_on_matrix_consistency", False, "doc empty")
        return audit

    jobs = doc.get("jobs", {})
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            audit["violations"].append({"job": job_name, "reason": "job not a mapping"})
            continue
        runs_on = job.get("runs-on")
        if not isinstance(runs_on, str):
            audit["violations"].append(
                {"job": job_name, "reason": f"runs-on not a string: {runs_on!r}"}
            )
            continue
        matrix_os = _job_matrix_os(job)
        entry = {
            "job": job_name,
            "runs_on": runs_on,
            "matrix_os": matrix_os,
        }
        if matrix_os is None:
            # OS-fixed job: runs-on 必须是合法 GH-runner image (无占位)
            if MATRIX_OS_PLACEHOLDER_RE.search(runs_on):
                audit["violations"].append(
                    {
                        "job": job_name,
                        "reason": "runs-on references matrix.os but no matrix.os defined",
                    }
                )
            elif runs_on not in ALLOWED_RUNNERS:
                audit["violations"].append(
                    {
                        "job": job_name,
                        "reason": f"OS-fixed runs-on={runs_on!r} not in ALLOWED_RUNNERS",
                    }
                )
            audit["os_fixed"].append(entry)
        else:
            # matrix-bearing job
            uses_placeholder = bool(MATRIX_OS_PLACEHOLDER_RE.search(runs_on))
            if uses_placeholder:
                # runs-on 走占位 ⇒ 自动随 matrix.os 扩, 合规
                pass
            else:
                # runs-on 硬编码字符串: 仅当 matrix.os 单元素且与之相等才视为退化等价
                if len(matrix_os) == 1 and matrix_os[0] == runs_on:
                    # 退化等价 (当前 baseline): PASS, 但作 caveat 记 (将来扩 OS 必须切占位)
                    entry["degenerate_equivalent"] = True
                else:
                    audit["violations"].append(
                        {
                            "job": job_name,
                            "reason": (
                                f"matrix.os={matrix_os} but runs-on={runs_on!r} "
                                f"hard-coded (not ${{ matrix.os }} placeholder); 扩 matrix 会全部跑同 runner"
                            ),
                        }
                    )
            audit["matrix_bearing"].append(entry)

    ok = not audit["violations"]
    _record(
        "V1_runs_on_matrix_consistency",
        ok,
        f"matrix_bearing={len(audit['matrix_bearing'])}, os_fixed={len(audit['os_fixed'])}, "
        f"violations={len(audit['violations'])}",
    )
    return audit


# ---------------------------------------------------------------------------
# V2: artifact name 含 matrix.os 占位 (扩 matrix 路径锁面)
# ---------------------------------------------------------------------------


def v2_artifact_name_locked(doc: Dict[str, Any]) -> Dict[str, Any]:
    """对所有 matrix-bearing job 的 upload-artifact name 强制含 ${{ matrix.os }} 占位.

    防 future regression: infra-019 已落地, 这里把它固化成契约.
    """
    report: Dict[str, Any] = {"checked": [], "violations": []}
    if not doc:
        _record("V2_artifact_name_locked", False, "doc empty")
        return report

    jobs = doc.get("jobs", {})
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        matrix_os = _job_matrix_os(job)
        if matrix_os is None:
            continue  # OS-fixed job 不强制
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            continue
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            uses = step.get("uses", "")
            if not isinstance(uses, str) or "actions/upload-artifact" not in uses:
                continue
            with_block = step.get("with", {}) or {}
            artifact_name = with_block.get("name") if isinstance(with_block, dict) else None
            if not isinstance(artifact_name, str):
                report["violations"].append(
                    {"job": job_name, "step_idx": idx, "reason": "upload-artifact without `with.name`"}
                )
                continue
            entry = {"job": job_name, "step_idx": idx, "name": artifact_name}
            if not MATRIX_OS_PLACEHOLDER_RE.search(artifact_name):
                report["violations"].append(
                    {
                        **entry,
                        "reason": (
                            "matrix-bearing job 的 artifact name 缺少 ${{ matrix.os }} 占位; "
                            "扩 OS 时多 runner 上传同名 artifact 会冲突 (infra-019 已修, 不能 regress)"
                        ),
                    }
                )
            report["checked"].append(entry)

    ok = not report["violations"]
    _record(
        "V2_artifact_name_locked",
        ok,
        f"checked={len(report['checked'])} artifact uploads, violations={len(report['violations'])}",
    )
    return report


# ---------------------------------------------------------------------------
# V3: regression — verify_infra_019 rc=0
# ---------------------------------------------------------------------------


def v3_regression_infra_019() -> None:
    if not VERIFY_INFRA_019.is_file():
        _record("V3_regression_infra_019", False, f"{VERIFY_INFRA_019} missing")
        return
    try:
        proc = subprocess.run(
            [sys.executable, str(VERIFY_INFRA_019)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        _record("V3_regression_infra_019", False, "timeout 120s")
        return
    ok = proc.returncode == 0
    detail = f"rc={proc.returncode}"
    if not ok:
        detail += f"; stderr_tail={proc.stderr[-200:]!r}"
    _record("V3_regression_infra_019", ok, detail)


# ---------------------------------------------------------------------------
# V4: matrix.os 元素均合法
# ---------------------------------------------------------------------------


def v4_matrix_os_runners_allowed(doc: Dict[str, Any]) -> Dict[str, Any]:
    report: Dict[str, Any] = {"observed": {}, "violations": []}
    if not doc:
        _record("V4_matrix_os_runners_allowed", False, "doc empty")
        return report
    jobs = doc.get("jobs", {})
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        matrix_os = _job_matrix_os(job)
        if matrix_os is None:
            continue
        report["observed"][job_name] = matrix_os
        for image in matrix_os:
            if image not in ALLOWED_RUNNERS:
                report["violations"].append(
                    {"job": job_name, "image": image, "reason": "not in ALLOWED_RUNNERS"}
                )
    ok = not report["violations"]
    _record(
        "V4_matrix_os_runners_allowed",
        ok,
        f"observed_jobs={len(report['observed'])}, violations={len(report['violations'])}",
    )
    return report


# ---------------------------------------------------------------------------
# V5: yaml bytewise 锁面 (sha256 + size)
# ---------------------------------------------------------------------------


def v5_yaml_lock() -> Dict[str, Any]:
    if not WORKFLOW.is_file():
        _record("V5_yaml_lock", False, "workflow missing")
        return {}
    raw = WORKFLOW.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    info = {
        "path": str(WORKFLOW.relative_to(ROOT)),
        "size": len(raw),
        "sha256": sha,
    }
    ok = len(raw) > 0
    _record("V5_yaml_lock", ok, f"size={len(raw)} sha256={sha[:16]}...")
    return info


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    doc = v0_yaml_parse()
    v1_audit = v1_runs_on_matrix_consistency(doc)
    v2_audit = v2_artifact_name_locked(doc)
    v3_regression_infra_019()
    v4_audit = v4_matrix_os_runners_allowed(doc)
    v5_info = v5_yaml_lock()

    ok = all(r["ok"] for r in _results)

    summary = {
        "feature": "infra-026",
        "source_backlog": "infra-019-backlog-runs-on-matrix-os",
        "direction": (
            "verify-only 契约审计 (不扩 matrix, 不改源码): runs-on vs matrix.os 一致性 + "
            "artifact name 占位锁面 + matrix.os 白名单 + regression infra-019 + yaml 锁面"
        ),
        "ok": ok,
        "results": _results,
        "audit": {
            "v1_runs_on_consistency": v1_audit,
            "v2_artifact_name_locked": v2_audit,
            "v4_matrix_os_allowed": v4_audit,
            "v5_yaml_lock": v5_info,
        },
        "expansion_guidance": [
            "扩 matrix.os 到 macos-latest / windows-latest 时, 必须同时把每个 matrix-bearing job 的 "
            "`runs-on: ubuntu-latest` 切到 `runs-on: ${{ matrix.os }}`; 否则 V1 会爆 FAIL.",
            "新增 matrix-bearing job 的 upload-artifact 时, name 必须含 `${{ matrix.os }}` 占位 (V2 锁面).",
            "新增 OS image 时, 加进 verify_infra_026.ALLOWED_RUNNERS 白名单 (V4 防 typo).",
            "lint / changes 等 OS-fixed job (无 strategy.matrix.os) 允许硬编码 runs-on, 不在本契约约束.",
        ],
        "files_changed": [
            "scripts/verify_infra_026.py",
            "evidence/infra-026/verify_summary.json",
        ],
        "real_machine_uat": "n/a",
    }

    ev_dir = ROOT / "evidence" / "infra-026"
    ev_dir.mkdir(parents=True, exist_ok=True)
    (ev_dir / "verify_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _print("DONE", f"ok={ok}, results={len(_results)}, evidence=evidence/infra-026/verify_summary.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
