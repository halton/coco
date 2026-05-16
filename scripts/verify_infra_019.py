#!/usr/bin/env python3
"""infra-019 verify: smoke _classify_stdout 严格前缀匹配 + verify-matrix
artifact name OS-axis 占位。

来源: infra-018-backlog-classify-and-matrix C1/C2

Acceptance
----------
V1 _classify_stdout 对合法 SKIP 行（行首 ``SKIP:``，无论缩进与大小写）返回 "SKIP"。
V2 _classify_stdout 对含 "skipped" 描述性子串但实为 WARN 的输入返回 "WARN"（旧
   实现误判为 SKIP — 即 C1 修复点）；对纯描述性 "no tests skipped" / 含 "skipped=0"
   而无 SKIP:/WARN: 前缀的 stdout 返回 "PASS"。
V3 FAIL/UNKNOWN 边界回归：纯 PASS 文本返回 "PASS"；空字符串返回 "PASS"；同时
   含 SKIP: 与 WARN: 行时 SKIP 优先（与旧实现意图一致）。
V4 verify-matrix.yml 解析后所有 upload-artifact step 的 name 字段包含
   ``${{ matrix.os }}`` 占位；matrix 块声明了 ``os`` 维度（即使当前只有
   ubuntu-latest，模板已就位）。

Sim-first：纯静态/纯 Python 验证，无真机依赖。
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "evidence" / "infra-019"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "verify-matrix.yml"
SMOKE_PATH = REPO_ROOT / "scripts" / "smoke.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("coco_smoke", SMOKE_PATH)
    assert spec and spec.loader, "cannot load scripts/smoke.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def v1_skip_prefix(classify) -> dict:
    cases = {
        "SKIP_prefix_indented": "  SKIP: COCO_CI=1 跳过真麦克",
        "SKIP_prefix_top": "SKIP: all 3 stages skipped due to model missing",
        "SKIP_lowercase_prefix": "skip: lowercase still recognized",
        "SKIP_with_other_lines": "starting...\n  SKIP: model missing\ndone",
    }
    res = {k: classify(v) for k, v in cases.items()}
    for k, v in res.items():
        _ok(v == "SKIP", f"V1 case {k}: expected SKIP, got {v} (input={cases[k]!r})")
    return res


def v2_warn_no_false_skip(classify) -> dict:
    cases = {
        # 旧实现 'skip' in lower(text) 会把这些误判为 SKIP；新实现应正确归为 WARN
        "WARN_with_skipped_word": "  WARN: ASR model not downloaded, skipped (run scripts/fetch_asr_models.sh)",
        "WARN_with_fixture_skipped": "  WARN: fixture missing /tmp/x.wav, skipped",
        # 纯描述性子串但无 marker → PASS
        "PASS_no_tests_skipped": "summary: no tests skipped, all good",
        "PASS_skipped_eq_zero": "results: passed=10 skipped=0 failed=0",
        "PASS_skipped_in_log": "the previous stage skipped retry policy and continued",
    }
    res = {k: classify(v) for k, v in cases.items()}
    _ok(res["WARN_with_skipped_word"] == "WARN",
        f"V2 WARN_with_skipped_word: expected WARN, got {res['WARN_with_skipped_word']}")
    _ok(res["WARN_with_fixture_skipped"] == "WARN",
        f"V2 WARN_with_fixture_skipped: expected WARN, got {res['WARN_with_fixture_skipped']}")
    _ok(res["PASS_no_tests_skipped"] == "PASS",
        f"V2 PASS_no_tests_skipped: expected PASS, got {res['PASS_no_tests_skipped']}")
    _ok(res["PASS_skipped_eq_zero"] == "PASS",
        f"V2 PASS_skipped_eq_zero: expected PASS, got {res['PASS_skipped_eq_zero']}")
    _ok(res["PASS_skipped_in_log"] == "PASS",
        f"V2 PASS_skipped_in_log: expected PASS, got {res['PASS_skipped_in_log']}")
    return res


def v3_regression(classify) -> dict:
    cases = {
        "PASS_empty": "",
        "PASS_only_info": "INFO: started\nINFO: done in 1.2s\n",
        "WARN_only": "  WARN: degraded mode\n",
        "SKIP_over_WARN_priority": "  WARN: degraded\n  SKIP: model missing\n",
    }
    res = {k: classify(v) for k, v in cases.items()}
    _ok(res["PASS_empty"] == "PASS", f"V3 empty: got {res['PASS_empty']}")
    _ok(res["PASS_only_info"] == "PASS", f"V3 only-info: got {res['PASS_only_info']}")
    _ok(res["WARN_only"] == "WARN", f"V3 warn-only: got {res['WARN_only']}")
    _ok(res["SKIP_over_WARN_priority"] == "SKIP",
        f"V3 SKIP-priority: got {res['SKIP_over_WARN_priority']}")
    return res


def v4_artifact_os_axis() -> dict:
    src = WORKFLOW_PATH.read_text(encoding="utf-8")
    # 1) 抓 upload-artifact step 紧跟 with: 之后的 name 字段
    # 模式: uses: actions/upload-artifact@vN  ...  with:  name: <value>
    artifact_blocks = re.findall(
        r"uses:\s*actions/upload-artifact@[^\n]+\s*\n"
        r"(?:\s*[^\n]+\n)*?"
        r"\s*with:\s*\n"
        r"\s*name:\s*([^\n]+)",
        src,
    )
    artifact_names = [n.strip() for n in artifact_blocks]
    _ok(len(artifact_names) >= 6,
        f"V4 expected ≥6 upload-artifact name lines, found {len(artifact_names)}: {artifact_names}")
    missing = [n for n in artifact_names if "${{ matrix.os }}" not in n]
    _ok(not missing, f"V4 artifact name(s) missing matrix.os placeholder: {missing}")
    # 2) matrix 块需声明 os: [ubuntu-latest]（至少出现一次，应该多次）
    os_decl_count = len(re.findall(r"^\s*os:\s*\[ubuntu-latest\]\s*$", src, flags=re.M))
    _ok(os_decl_count >= 6,
        f"V4 expected ≥6 matrix os: [ubuntu-latest] declarations, found {os_decl_count}")
    return {
        "artifact_names": artifact_names,
        "os_decl_count": os_decl_count,
    }


def main() -> int:
    smoke = _load_smoke()
    classify = smoke._classify_stdout
    results: dict = {}
    failures: list[str] = []

    for vname, fn in [
        ("V1_skip_prefix", lambda: v1_skip_prefix(classify)),
        ("V2_warn_no_false_skip", lambda: v2_warn_no_false_skip(classify)),
        ("V3_regression", lambda: v3_regression(classify)),
        ("V4_artifact_os_axis", v4_artifact_os_axis),
    ]:
        try:
            results[vname] = {"status": "PASS", "detail": fn()}
            print(f"[{vname}] PASS")
        except AssertionError as e:
            results[vname] = {"status": "FAIL", "error": str(e)}
            failures.append(f"{vname}: {e}")
            print(f"[{vname}] FAIL: {e}")

    summary = {
        "feature": "infra-019",
        "status": "PASS" if not failures else "FAIL",
        "results": results,
        "failures": failures,
    }
    out_path = EVIDENCE_DIR / "verify_summary.json"
    out_path.write_text(
        json.dumps(summary, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {out_path}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
