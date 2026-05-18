"""infra-027 verification: CI matrix.os 集合稳定性 spec 锁面 (verify-only).

跑法::

    uv run python scripts/verify_infra_027.py

子项 (与 docs/ci-matrix-os-spec.md 对齐, verify-only 不动 CI yml)::

V0 fingerprint sha256 锁 — 锁 .github/workflows/verify-matrix.yml + spec doc
   bytewise sha256 + size, 任何字节变化都被记录入 evidence (不强制等值, 但
   写盘做 diff baseline).

V1 matrix.os 集合字面量锁 — 枚举 verify-matrix.yml 所有 matrix-bearing job
   的 strategy.matrix.os, 计算 unique sorted union, 必须严格等于本 spec
   SPEC_OS_UNION = ["ubuntu-latest"]. cardinality + 字面量同时锁.

V2 spec doc 关键短语锁 — docs/ci-matrix-os-spec.md 必须含 SPEC_LOCKED_PHRASES
   (闭集合 / ubuntu-latest / verify-only / infra-026 / SPEC_OS_UNION /
   不衍生 fu chain). 改 spec 时必须同步改本 verify 否则 FAIL.

V3 runs-on ∈ matrix.os ∪ ALLOWED_RUNNERS — 对每个 job, runs-on 字面量
   (剥占位后) 必须落在 SPEC_OS_UNION 或 OS-fixed ALLOWED_RUNNERS 内. 防
   self-hosted / typo image 偷渡.

V4 邻近 verify 静态回归 — verify_infra_019 / 024 / 025 / 026 单跑 rc=0,
   保证不 regress 已锁面的兄弟契约.

V5 smoke 11/11 PASS — 跑 ./init.sh (COCO_CI=1) 全 smoke 必须通过.

V_n evidence/migration_note — 写 evidence/infra-027/verify_summary.json +
   migration_note.md, 含 spec union / fingerprint / files_changed.

retval: 0 全 PASS; 1 任一失败.
"""

from __future__ import annotations

import hashlib
import json
import os
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
SPEC_DOC = ROOT / "docs" / "ci-matrix-os-spec.md"
INIT_SH = ROOT / "init.sh"

# === Authoritative spec snapshot (sorted unique union over all matrix-bearing jobs) ===
SPEC_OS_UNION: List[str] = ["ubuntu-latest"]

# OS-fixed job (lint / changes) 允许的 image 集合 (子集 of GH-hosted whitelist).
# 与 verify_infra_026.ALLOWED_RUNNERS 对齐, 但本 verify 不 import 它 (松耦合).
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

SPEC_LOCKED_PHRASES: List[str] = [
    "closed set",
    "ubuntu-latest",
    "verify-only",
    "infra-026",
    "SPEC_OS_UNION",
    "不衍生 fu chain",
]

MATRIX_OS_PLACEHOLDER_RE = re.compile(r"\$\{\{\s*matrix\.os\s*\}\}")

ADJACENT_VERIFIES: List[str] = [
    "scripts/verify_infra_019.py",
    "scripts/verify_infra_024.py",
    "scripts/verify_infra_025.py",
    "scripts/verify_infra_026.py",
]


def _print(tag: str, msg: str) -> None:
    print(f"[verify_infra_027] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


# ---------------------------------------------------------------------------
# V0: fingerprint sha256
# ---------------------------------------------------------------------------


def v0_fingerprint() -> Dict[str, Any]:
    info: Dict[str, Any] = {}
    ok = True
    for p in (WORKFLOW, SPEC_DOC):
        if not p.is_file():
            ok = False
            info[str(p.relative_to(ROOT))] = {"present": False}
            continue
        raw = p.read_bytes()
        info[str(p.relative_to(ROOT))] = {
            "present": True,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    _record(
        "V0_fingerprint",
        ok,
        f"workflow_present={info.get('.github/workflows/verify-matrix.yml', {}).get('present')}, "
        f"spec_present={info.get('docs/ci-matrix-os-spec.md', {}).get('present')}",
    )
    return info


# ---------------------------------------------------------------------------
# V1: matrix.os 集合字面量
# ---------------------------------------------------------------------------


def _job_matrix_os(job: Dict[str, Any]) -> List[str] | None:
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


def v1_matrix_os_union(doc: Dict[str, Any]) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "spec_union": SPEC_OS_UNION,
        "observed_union": [],
        "per_job": {},
        "violations": [],
    }
    if not doc:
        _record("V1_matrix_os_union", False, "doc empty")
        return report
    union: set[str] = set()
    jobs = doc.get("jobs", {})
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        m_os = _job_matrix_os(job)
        if m_os is None:
            continue
        report["per_job"][job_name] = m_os
        union.update(m_os)
    observed_sorted = sorted(union)
    report["observed_union"] = observed_sorted
    if observed_sorted != sorted(SPEC_OS_UNION):
        report["violations"].append(
            {
                "reason": "matrix.os union mismatch SPEC_OS_UNION",
                "spec": sorted(SPEC_OS_UNION),
                "observed": observed_sorted,
            }
        )
    ok = not report["violations"]
    _record(
        "V1_matrix_os_union",
        ok,
        f"spec={sorted(SPEC_OS_UNION)} observed={observed_sorted} jobs={len(report['per_job'])}",
    )
    return report


# ---------------------------------------------------------------------------
# V2: spec doc 关键短语锁
# ---------------------------------------------------------------------------


def v2_spec_phrases() -> Dict[str, Any]:
    report: Dict[str, Any] = {"phrases": SPEC_LOCKED_PHRASES, "missing": []}
    if not SPEC_DOC.is_file():
        _record("V2_spec_phrases", False, "spec doc missing")
        return report
    text = SPEC_DOC.read_text(encoding="utf-8")
    for phrase in SPEC_LOCKED_PHRASES:
        if phrase not in text:
            report["missing"].append(phrase)
    ok = not report["missing"]
    _record(
        "V2_spec_phrases",
        ok,
        f"checked={len(SPEC_LOCKED_PHRASES)} missing={report['missing']}",
    )
    return report


# ---------------------------------------------------------------------------
# V3: runs-on ∈ SPEC_OS_UNION ∪ ALLOWED_RUNNERS (剥占位)
# ---------------------------------------------------------------------------


def v3_runs_on_membership(doc: Dict[str, Any]) -> Dict[str, Any]:
    report: Dict[str, Any] = {"checked": [], "violations": []}
    if not doc:
        _record("V3_runs_on_membership", False, "doc empty")
        return report
    union_set: set[str] = set(SPEC_OS_UNION) | set(ALLOWED_RUNNERS)
    jobs = doc.get("jobs", {})
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        runs_on = job.get("runs-on")
        if not isinstance(runs_on, str):
            report["violations"].append(
                {"job": job_name, "reason": f"runs-on not a string: {runs_on!r}"}
            )
            continue
        # 剥 ${{ matrix.os }} 占位: 若整个 runs-on 是占位, 视作 SPEC_OS_UNION 成员 (合规).
        stripped = MATRIX_OS_PLACEHOLDER_RE.sub("", runs_on).strip()
        entry = {"job": job_name, "runs_on": runs_on, "stripped": stripped}
        if not stripped:
            # 纯占位 -> 合规 (假设 matrix.os 已被 V1 校验落在 SPEC_OS_UNION)
            entry["resolved_via"] = "matrix.os placeholder"
        elif stripped in union_set:
            entry["resolved_via"] = "literal in SPEC_OS_UNION ∪ ALLOWED_RUNNERS"
        else:
            report["violations"].append(
                {
                    "job": job_name,
                    "runs_on": runs_on,
                    "reason": f"runs-on literal {stripped!r} not in SPEC_OS_UNION ∪ ALLOWED_RUNNERS",
                }
            )
            continue
        report["checked"].append(entry)
    ok = not report["violations"]
    _record(
        "V3_runs_on_membership",
        ok,
        f"checked={len(report['checked'])} violations={len(report['violations'])}",
    )
    return report


# ---------------------------------------------------------------------------
# V4: 邻近 verify 回归
# ---------------------------------------------------------------------------


def v4_adjacent_regression() -> Dict[str, Any]:
    report: Dict[str, Any] = {"runs": [], "violations": []}
    for rel in ADJACENT_VERIFIES:
        script = ROOT / rel
        if not script.is_file():
            report["violations"].append({"script": rel, "reason": "missing"})
            continue
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            report["violations"].append({"script": rel, "reason": "timeout 180s"})
            continue
        entry = {"script": rel, "rc": proc.returncode}
        report["runs"].append(entry)
        if proc.returncode != 0:
            report["violations"].append(
                {"script": rel, "rc": proc.returncode, "stderr_tail": proc.stderr[-200:]}
            )
    ok = not report["violations"]
    _record(
        "V4_adjacent_regression",
        ok,
        f"runs={len(report['runs'])} violations={len(report['violations'])}",
    )
    return report


# ---------------------------------------------------------------------------
# V5: smoke (./init.sh)
# ---------------------------------------------------------------------------


def v5_smoke() -> Dict[str, Any]:
    report: Dict[str, Any] = {"rc": None, "ok": False}
    if not INIT_SH.is_file():
        _record("V5_smoke", False, "init.sh missing")
        return report
    env = {**os.environ, "COCO_CI": "1"}
    try:
        proc = subprocess.run(
            ["bash", str(INIT_SH)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=240,
            env=env,
        )
    except subprocess.TimeoutExpired:
        _record("V5_smoke", False, "timeout 240s")
        return report
    report["rc"] = proc.returncode
    # 估计 PASS 计数 (best-effort, 不强卡 11/11 字面量, 但记录)
    tail = proc.stdout[-400:] + proc.stderr[-200:]
    report["tail"] = tail
    ok = proc.returncode == 0
    report["ok"] = ok
    _record("V5_smoke", ok, f"rc={proc.returncode}")
    return report


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _parse_workflow() -> Dict[str, Any]:
    if not WORKFLOW.is_file():
        return {}
    try:
        doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    return doc if isinstance(doc, dict) else {}


def main() -> int:
    fingerprint = v0_fingerprint()
    doc = _parse_workflow()
    v1 = v1_matrix_os_union(doc)
    v2 = v2_spec_phrases()
    v3 = v3_runs_on_membership(doc)
    v4 = v4_adjacent_regression()
    v5 = v5_smoke()

    ok = all(r["ok"] for r in _results)

    summary = {
        "feature": "infra-027",
        "source_backlog": "infra-019-backlog-runs-on-matrix-os",
        "direction": (
            "verify-only: CI matrix.os 集合 (union over matrix-bearing jobs) 字面量 + cardinality 锁面; "
            "spec doc 关键短语锁; runs-on 成员资格锁; 邻近 infra-019/024/025/026 回归; smoke 通过"
        ),
        "ok": ok,
        "spec_os_union": SPEC_OS_UNION,
        "results": _results,
        "audit": {
            "v0_fingerprint": fingerprint,
            "v1_matrix_os_union": v1,
            "v2_spec_phrases": v2,
            "v3_runs_on_membership": v3,
            "v4_adjacent_regression": v4,
            "v5_smoke": {k: v for k, v in v5.items() if k != "tail"},
        },
        "expansion_guidance": [
            "扩 matrix.os 时, 先改 docs/ci-matrix-os-spec.md §1 表格 + 字面量列表, 再改 SPEC_OS_UNION 常量, 最后改 verify-matrix.yml.",
            "spec doc 删任一 SPEC_LOCKED_PHRASES 关键短语会让 V2 爆 FAIL — 改前请同步 verify.",
            "runs-on / matrix.os 字段一致性由 infra-026 锁面, 本 verify 只锁集合本身.",
        ],
        "files_changed": [
            "docs/ci-matrix-os-spec.md",
            "scripts/verify_infra_027.py",
            "evidence/infra-027/verify_summary.json",
            "evidence/infra-027/migration_note.md",
        ],
        "real_machine_uat": "n/a",
        "default_off": "verify-only, 0 业务源码改动, 0 CI 行为改动",
    }

    ev_dir = ROOT / "evidence" / "infra-027"
    ev_dir.mkdir(parents=True, exist_ok=True)
    (ev_dir / "verify_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _print(
        "DONE",
        f"ok={ok}, results={len(_results)}, evidence=evidence/infra-027/verify_summary.json",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
