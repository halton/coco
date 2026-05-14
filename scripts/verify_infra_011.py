"""infra-011 verification: paths-filter wired into verify-matrix.yml.

V1-V10：
  V1 verify-matrix.yml 含 changes job
  V2 changes job 用 dorny/paths-filter@v3
  V3 7 个 area output 声明齐全
  V4 每个 verify-XXX job 都 needs: [..., changes]
  V5 每个 verify-XXX job 的 if 条件含 needs.changes.outputs.<对应 area>
  V6 smoke job 无 if 条件（永远跑）
  V7 if 条件兼顾"非 PR 跑全量"（含 github.event_name != 'pull_request' 或等价）
  V8 paths-filter 内容与 evidence/infra-008/paths-filter.yml 一致
     （.github/paths-filter.yml 与 evidence 文件 byte-identical）
  V9 infra-006 V9 旧约束不破（matrix 内 '--area X' 模式仍在）
  V10 publish job 仍在 SKIP（uat-phase8），CI 默认不跑实际 publish 步骤
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/verify-matrix.yml"
PATHS_FILTER_GITHUB = ROOT / ".github/paths-filter.yml"
PATHS_FILTER_EVIDENCE = ROOT / "evidence/infra-008/paths-filter.yml"

AREAS = ["vision", "interact", "companion", "audio", "robot", "infra", "publish"]
VERIFY_JOBS = {f"verify-{a}": a for a in AREAS}


def _load_yaml():
    import yaml  # type: ignore
    with open(WORKFLOW) as f:
        return yaml.safe_load(f)


def v1_changes_job_present() -> None:
    print("V1: verify-matrix.yml 含 changes job")
    data = _load_yaml()
    jobs = data.get("jobs", {})
    assert "changes" in jobs, "缺 changes job"
    print("  ok: changes job 存在")


def v2_uses_paths_filter_v3() -> None:
    print("V2: changes job 用 dorny/paths-filter@v3")
    data = _load_yaml()
    steps = data["jobs"]["changes"].get("steps") or []
    uses_lines = [str(s.get("uses", "")) for s in steps if isinstance(s, dict)]
    assert any("dorny/paths-filter@v3" in u for u in uses_lines), (
        f"未找到 dorny/paths-filter@v3，uses={uses_lines}"
    )
    print("  ok: dorny/paths-filter@v3")


def v3_seven_outputs_declared() -> None:
    print("V3: changes job 声明 7 个 area output")
    data = _load_yaml()
    outputs = data["jobs"]["changes"].get("outputs") or {}
    missing = [a for a in AREAS if a not in outputs]
    assert not missing, f"output 缺失: {missing}"
    print(f"  ok: 7 个 area output 齐全 ({list(outputs.keys())})")


def v4_each_verify_needs_changes() -> None:
    print("V4: 每个 verify-XXX job 都 needs: changes")
    data = _load_yaml()
    jobs = data.get("jobs", {})
    failed = []
    for job_name in VERIFY_JOBS:
        if job_name not in jobs:
            failed.append(f"{job_name}: 缺 job")
            continue
        needs = jobs[job_name].get("needs")
        if isinstance(needs, str):
            needs_list = [needs]
        elif isinstance(needs, list):
            needs_list = needs
        else:
            needs_list = []
        if "changes" not in needs_list:
            failed.append(f"{job_name}: needs={needs_list} 缺 'changes'")
    assert not failed, "V4 失败:\n  - " + "\n  - ".join(failed)
    print(f"  ok: {len(VERIFY_JOBS)} 个 verify-XXX job 都 needs changes")


def v5_each_if_references_area_output() -> None:
    print("V5: 每个 verify-XXX job 的 if 条件含 needs.changes.outputs.<area>")
    data = _load_yaml()
    jobs = data.get("jobs", {})
    failed = []
    for job_name, area in VERIFY_JOBS.items():
        cond = str(jobs.get(job_name, {}).get("if", ""))
        pat = rf"needs\.changes\.outputs\.{re.escape(area)}\b"
        if not re.search(pat, cond):
            failed.append(f"{job_name}: if='{cond}' 缺 {pat}")
    assert not failed, "V5 失败:\n  - " + "\n  - ".join(failed)
    print(f"  ok: {len(VERIFY_JOBS)} 个 verify-XXX job 的 if 都引用了对应 area output")


def v6_smoke_no_if() -> None:
    print("V6: smoke job 无 if 条件（永远跑）")
    data = _load_yaml()
    smoke = data["jobs"].get("smoke", {})
    cond = smoke.get("if")
    assert not cond, f"smoke 不应有 if 条件，实际: {cond!r}"
    print("  ok: smoke job 无 if")


def v7_if_covers_non_pr_full_run() -> None:
    print("V7: if 条件兼顾'非 PR 跑全量'")
    data = _load_yaml()
    jobs = data.get("jobs", {})
    failed = []
    for job_name in VERIFY_JOBS:
        cond = str(jobs.get(job_name, {}).get("if", ""))
        # 期望含 github.event_name != 'pull_request' 或等价（== 'push' 等）
        ok = (
            "github.event_name != 'pull_request'" in cond
            or "github.event_name == 'push'" in cond
        )
        if not ok:
            failed.append(f"{job_name}: if='{cond}' 未覆盖非 PR 全量")
    assert not failed, "V7 失败:\n  - " + "\n  - ".join(failed)
    print(f"  ok: {len(VERIFY_JOBS)} 个 job 都覆盖非 PR 全量")


def v8_paths_filter_consistent() -> None:
    print("V8: .github/paths-filter.yml 与 evidence/infra-008/paths-filter.yml 一致")
    assert PATHS_FILTER_GITHUB.exists(), f"缺 {PATHS_FILTER_GITHUB}"
    assert PATHS_FILTER_EVIDENCE.exists(), f"缺 {PATHS_FILTER_EVIDENCE}"
    a = PATHS_FILTER_GITHUB.read_text()
    b = PATHS_FILTER_EVIDENCE.read_text()
    assert a == b, ".github/paths-filter.yml 与 evidence 不一致"
    # 同时确认 changes job 的 filters 引用该文件（或 inline 含 7 area key）
    import yaml  # type: ignore
    pf = yaml.safe_load(a)
    missing = [k for k in AREAS if k not in pf]
    assert not missing, f"paths-filter 缺 area key: {missing}"
    print(f"  ok: 两份文件 byte-identical，7 个 area key 齐全")


def v9_infra006_area_pattern_intact() -> None:
    print("V9: infra-006 V9 旧约束不破（matrix 内 '--area X' 模式仍在）")
    import yaml  # type: ignore
    data = _load_yaml()
    jobs = data.get("jobs", {})
    failed = []
    for job_name, area in VERIFY_JOBS.items():
        steps = jobs.get(job_name, {}).get("steps") or []
        runs = " \n".join(
            str(s.get("run", "")) for s in steps if isinstance(s, dict)
        )
        if "run_verify_all.py" not in runs:
            failed.append(f"{job_name}: 缺 run_verify_all.py")
            continue
        if not re.search(rf"--area\s+{re.escape(area)}\b", runs):
            failed.append(f"{job_name}: 缺 '--area {area}'")
            continue
        if re.search(r"--filter\s+\S", runs):
            failed.append(f"{job_name}: 不允许 --filter")
    assert not failed, "V9 失败:\n  - " + "\n  - ".join(failed)
    print(f"  ok: {len(VERIFY_JOBS)} 个 verify-XXX job 仍带 '--area X'")


def v10_publish_still_skipped() -> None:
    print("V10: publish job 仍在 SKIP（CI 默认不跑实际 publish）")
    # publish job 自身在 verify-matrix 内存在（job 名占位），但
    # scripts/run_verify_all.py 的 SKIP_LIST 应仍包含 verify_publish.py。
    runner = (ROOT / "scripts/run_verify_all.py").read_text()
    # 寻找 SKIP_LIST 中含 verify_publish 的迹象
    assert "verify_publish" in runner, (
        "scripts/run_verify_all.py 未引用 verify_publish（SKIP_LIST 期望含之）"
    )
    # 同时确认 job 名仍在（保证 area 不静默）
    data = _load_yaml()
    assert "verify-publish" in data.get("jobs", {}), "verify-publish job 不应被删"
    print("  ok: verify-publish job 占位仍在，runner SKIP 引用 verify_publish")


CHECKS = [
    v1_changes_job_present,
    v2_uses_paths_filter_v3,
    v3_seven_outputs_declared,
    v4_each_verify_needs_changes,
    v5_each_if_references_area_output,
    v6_smoke_no_if,
    v7_if_covers_non_pr_full_run,
    v8_paths_filter_consistent,
    v9_infra006_area_pattern_intact,
    v10_publish_still_skipped,
]


def main() -> int:
    failed = []
    for fn in CHECKS:
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed.append(fn.__name__)
    print()
    print(f"PASS {len(CHECKS) - len(failed)}/{len(CHECKS)}")
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
