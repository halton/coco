"""infra-013 verification: paths-filter meta safety-net + workflow_dispatch behavior.

V1-V8：
  V1 .github/paths-filter.yml 含 `meta:` 段
  V2 meta 段含 pyproject.toml / tests/ / conftest.py / .github/
  V3 evidence/infra-008/paths-filter.yml 与 .github/paths-filter.yml byte-identical
  V4 verify-matrix.yml changes job outputs 含 meta
  V5 7 个 verify-XXX job 的 if 条件均含 needs.changes.outputs.meta
  V6 verify-matrix.yml 有 workflow_dispatch trigger（或 changes job dispatch 下跳过；任一）
  V7 evidence/infra-013/README.md 存在且含 "cross-area" 或 "trade-off"
  V8 回归：verify_infra_011 仍 PASS
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/verify-matrix.yml"
PATHS_FILTER_GITHUB = ROOT / ".github/paths-filter.yml"
PATHS_FILTER_EVIDENCE = ROOT / "evidence/infra-008/paths-filter.yml"
README = ROOT / "evidence/infra-013/README.md"

AREAS = ["vision", "interact", "companion", "audio", "robot", "infra", "publish"]


def _load_yaml(path: Path):
    import yaml  # type: ignore
    with open(path) as f:
        return yaml.safe_load(f)


def v1_meta_section_present() -> None:
    print("V1: .github/paths-filter.yml 含 meta 段")
    data = _load_yaml(PATHS_FILTER_GITHUB)
    assert "meta" in data, f"paths-filter.yml 缺 meta 段，现有 keys={list(data.keys())}"
    assert isinstance(data["meta"], list) and len(data["meta"]) >= 4, (
        f"meta 段应为 list 且 >=4 条，得到 {data['meta']!r}"
    )
    print(f"  PASS — meta 段 {len(data['meta'])} 条")


def v2_meta_includes_key_paths() -> None:
    print("V2: meta 段含 pyproject.toml / tests / conftest.py / .github")
    data = _load_yaml(PATHS_FILTER_GITHUB)
    meta = data["meta"]
    required_substrings = ["pyproject.toml", "tests/", "conftest.py", ".github/"]
    flat = "\n".join(meta)
    missing = [s for s in required_substrings if s not in flat]
    assert not missing, f"meta 段缺以下 pattern: {missing}\nmeta={meta!r}"
    print(f"  PASS — 4 个核心 pattern 均存在")


def v3_paths_filter_byte_identical() -> None:
    print("V3: .github/paths-filter.yml 与 evidence/infra-008/paths-filter.yml byte-identical")
    assert PATHS_FILTER_EVIDENCE.exists(), f"{PATHS_FILTER_EVIDENCE} 不存在"
    gh = PATHS_FILTER_GITHUB.read_bytes()
    ev = PATHS_FILTER_EVIDENCE.read_bytes()
    assert gh == ev, (
        f"两 paths-filter 不一致\n"
        f"  .github size={len(gh)}\n"
        f"  evidence size={len(ev)}"
    )
    print(f"  PASS — {len(gh)} bytes")


def v4_changes_outputs_has_meta() -> None:
    print("V4: verify-matrix.yml changes job outputs 含 meta")
    data = _load_yaml(WORKFLOW)
    outputs = data["jobs"]["changes"]["outputs"]
    assert "meta" in outputs, (
        f"changes job outputs 缺 meta，现有 keys={list(outputs.keys())}"
    )
    assert "steps.filter.outputs.meta" in outputs["meta"], (
        f"meta output 表达式异常: {outputs['meta']!r}"
    )
    print(f"  PASS — meta output = {outputs['meta']!r}")


def v5_verify_jobs_if_has_meta() -> None:
    print("V5: 7 个 verify-XXX job 的 if 条件均含 needs.changes.outputs.meta")
    data = _load_yaml(WORKFLOW)
    missing = []
    for area in AREAS:
        job_name = f"verify-{area}"
        job = data["jobs"].get(job_name)
        assert job is not None, f"workflow 缺 job {job_name}"
        if_cond = job.get("if", "")
        if "needs.changes.outputs.meta" not in if_cond:
            missing.append((job_name, if_cond))
    assert not missing, f"以下 job 的 if 缺 meta 兜底: {missing}"
    print(f"  PASS — 7 个 verify-XXX 均含 meta 兜底")


def v6_workflow_dispatch_behavior() -> None:
    print("V6: verify-matrix.yml 有 workflow_dispatch trigger（或 dispatch 下走全量）")
    data = _load_yaml(WORKFLOW)
    # PyYAML 把 yaml key `on:` 解析为 Python True（bool）
    on_section = data.get(True, data.get("on"))
    assert on_section is not None, f"workflow 缺 on/trigger 段，keys={list(data.keys())}"
    if isinstance(on_section, dict):
        has_dispatch = "workflow_dispatch" in on_section
    else:
        has_dispatch = "workflow_dispatch" in str(on_section)
    assert has_dispatch, (
        f"verify-matrix.yml 缺 workflow_dispatch trigger；on={on_section!r}"
    )
    # 另外验证 verify-XXX 的 if 第一支为 `github.event_name != 'pull_request'`
    # 这样 dispatch 时短路 OR 跑全量
    for area in AREAS:
        if_cond = data["jobs"][f"verify-{area}"]["if"]
        assert "github.event_name != 'pull_request'" in if_cond, (
            f"verify-{area} if 缺 dispatch 兜底: {if_cond!r}"
        )
    print(f"  PASS — workflow_dispatch trigger 存在 + 7 job if 兜底完整")


def v7_readme_exists() -> None:
    print("V7: evidence/infra-013/README.md 存在 + 含 cross-area / trade-off")
    assert README.exists(), f"{README} 不存在"
    text = README.read_text()
    assert "cross-area" in text.lower() or "cross area" in text.lower(), (
        f"README 不含 'cross-area' 关键字"
    )
    assert "trade-off" in text.lower() or "tradeoff" in text.lower(), (
        f"README 不含 'trade-off' 关键字"
    )
    print(f"  PASS — README {len(text)} chars，关键字命中")


def v8_infra_011_regression() -> None:
    print("V8: 回归 verify_infra_011 仍 PASS")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_infra_011.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, (
        f"verify_infra_011 回归失败 rc={result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    print(f"  PASS — verify_infra_011 OK")


def main() -> int:
    checks = [
        v1_meta_section_present,
        v2_meta_includes_key_paths,
        v3_paths_filter_byte_identical,
        v4_changes_outputs_has_meta,
        v5_verify_jobs_if_has_meta,
        v6_workflow_dispatch_behavior,
        v7_readme_exists,
        v8_infra_011_regression,
    ]
    passed = 0
    failed = []
    for check in checks:
        try:
            check()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL — {e}")
            failed.append(check.__name__)
        except Exception as e:
            print(f"  ERROR — {type(e).__name__}: {e}")
            failed.append(check.__name__)
    total = len(checks)
    print(f"\ninfra-013: {passed}/{total} PASS")
    if failed:
        print(f"failed: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
