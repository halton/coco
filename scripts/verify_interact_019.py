"""interact-019 verification: is_fail status token 白名单精确匹配.

承接 interact-018 backlog: trace _is_fail 函数对 status 字段当前用 substring
匹配会把 "no_failure" / "failsafe" / "no_fail_today" 等 token 误判 failure。
改为 token 精确匹配 (status.strip().lower() ∈ STATUS_FAIL_TOKENS) 消除误判。

跑法::

    uv run python scripts/verify_interact_019.py

子项：

V1 status="failed" / "fail" / "failure" / "error" / "errored" (大小写不敏感)
   全部识别为 fail (is_fail 返回 True)。

V2 status="no_failure" / "failsafe" / "no_fail_today" / "fail-something"
   不再误判 (is_fail 返回 False; 历史 substring 实现会错判为 True)。

V3 三口主路径 (ok=False / error 非空 str / failure_reason 非空 str) 仍正确
   识别为 fail (不被本次改动破坏)。

V4 缺字段 / ok=True / status="success" / status="" / 空 rec 不误判
   (is_fail 返回 False)。

V5 regression: llm_usage_summary.py 在含 "no_failure" status 的 fixture 上,
   failure_rate 不再多算 (旧 substring 实现会把 no_failure 算成 fail,
   本次 token 白名单消除该误判)。

retval：0 全 PASS；1 任一失败
evidence 落 evidence/interact-019/verify_summary.json
"""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_019] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


# ---------------------------------------------------------------------------
# V1: 白名单 token 全部识别为 fail (case-insensitive)
# ---------------------------------------------------------------------------


def v1_status_token_positives() -> None:
    from coco.proactive_trace import is_fail, STATUS_FAIL_TOKENS

    # 白名单 token 大小写混合 + 前后空白
    cases = [
        "fail", "FAIL", "Fail",
        "failed", "FAILED", "Failed",
        "failure", "FAILURE", "Failure",
        "error", "ERROR", "Error",
        "errored", "ERRORED", "Errored",
        "  fail  ",   # strip
        "\tFAILURE\n",
    ]
    failures = [c for c in cases if not is_fail({"status": c})]
    ok = not failures
    # 白名单内容也校验一遍，避免后续被人误改
    expected_tokens = {"fail", "failed", "failure", "error", "errored"}
    if STATUS_FAIL_TOKENS != expected_tokens:
        ok = False
        detail = f"STATUS_FAIL_TOKENS={sorted(STATUS_FAIL_TOKENS)} expected={sorted(expected_tokens)}"
    else:
        detail = (f"missed={failures}" if failures
                  else f"tokens={sorted(STATUS_FAIL_TOKENS)} all {len(cases)} cases hit")
    _record("V1_status_token_positives", ok, detail)


# ---------------------------------------------------------------------------
# V2: 历史 substring 误判用例改后不再命中
# ---------------------------------------------------------------------------


def v2_status_no_false_positive() -> None:
    from coco.proactive_trace import is_fail

    # 这些字符串含 "fail" / "error" 子串但语义并非失败
    cases = [
        "no_failure",
        "no_fail",
        "no_fail_today",
        "failsafe",
        "fail_safe",
        "fail-something",   # 复合 token, 旧 substring 会命中, 新 token 不命中
        "no_error",
        "error_handled",
        "errorless",
    ]
    misfired = [c for c in cases if is_fail({"status": c})]
    ok = not misfired
    detail = (f"misfired={misfired}"
              if misfired
              else f"all {len(cases)} historical-substring cases correctly rejected")
    _record("V2_status_no_false_positive", ok, detail)


# ---------------------------------------------------------------------------
# V3: 三口主路径仍工作 (不被本次改动破坏)
# ---------------------------------------------------------------------------


def v3_three_signals_still_work() -> None:
    from coco.proactive_trace import is_fail

    checks = [
        ({"ok": False}, True, "ok=False"),
        ({"error": "TimeoutError"}, True, "error non-empty"),
        ({"failure_reason": "rate_limited"}, True, "failure_reason non-empty"),
        # 与 status fail token 共存
        ({"ok": False, "status": "no_failure"}, True,
         "ok=False overrides false-positive status"),
    ]
    mismatched = []
    for rec, expected, label in checks:
        got = is_fail(rec)
        if got != expected:
            mismatched.append(f"{label}: got={got} expected={expected}")
    ok = not mismatched
    detail = "; ".join(mismatched) if mismatched else "ok=False / error / failure_reason 全部正确命中"
    _record("V3_three_signals_still_work", ok, detail)


# ---------------------------------------------------------------------------
# V4: 缺字段 / truthy 字符串 / 空 status 不误判
# ---------------------------------------------------------------------------


def v4_negative_cases() -> None:
    from coco.proactive_trace import is_fail

    checks = [
        ({}, False, "empty rec"),
        ({"ok": True}, False, "ok=True"),
        ({"ok": "ok"}, False, "ok=string truthy"),
        ({"ok": "success"}, False, "ok=success"),
        ({"status": "success"}, False, "status=success"),
        ({"status": "ok"}, False, "status=ok"),
        ({"status": ""}, False, "status empty"),
        ({"status": "   "}, False, "status whitespace"),
        ({"error": ""}, False, "error empty str"),
        ({"error": None}, False, "error None"),
        ({"failure_reason": ""}, False, "failure_reason empty"),
        ({"status": "passing"}, False, "status=passing"),
    ]
    mismatched = []
    for rec, expected, label in checks:
        got = is_fail(rec)
        if got != expected:
            mismatched.append(f"{label}: got={got}")
    ok = not mismatched
    detail = "; ".join(mismatched) if mismatched else f"all {len(checks)} negative cases pass"
    _record("V4_negative_cases", ok, detail)


# ---------------------------------------------------------------------------
# V5: llm_usage_summary regression -- "no_failure" 不再被多算
# ---------------------------------------------------------------------------


def v5_llm_usage_no_failure_regression(tmp: Path) -> None:
    """构造 10 条 record:
      - 2 条 ok=False (真 fail)
      - 3 条 status="no_failure" (历史 substring 会误判 fail, 改后必须不算)
      - 5 条 status="ok" (真 ok)

    旧 substring 实现 → fail=5, rate=0.5
    新 token 白名单   → fail=2, rate=0.2
    """
    base = tmp / "v5_base"
    base.mkdir()
    day = "20260515"
    ts_base = _dt.datetime.strptime(day, "%Y%m%d").timestamp() + 3600
    model = "gpt-x"
    lines = []
    # 2 真 fail
    for i in range(2):
        lines.append(json.dumps({
            "event": "usage", "component": "mm_proactive",
            "model": model, "prompt_tokens": 10, "completion_tokens": 5,
            "ts": ts_base + i, "ok": False,
        }, sort_keys=True))
    # 3 条 no_failure (新实现下不算 fail)
    for i in range(3):
        lines.append(json.dumps({
            "event": "usage", "component": "mm_proactive",
            "model": model, "prompt_tokens": 10, "completion_tokens": 5,
            "ts": ts_base + 10 + i, "status": "no_failure",
        }, sort_keys=True))
    # 5 条 ok
    for i in range(5):
        lines.append(json.dumps({
            "event": "usage", "component": "mm_proactive",
            "model": model, "prompt_tokens": 10, "completion_tokens": 5,
            "ts": ts_base + 20 + i, "status": "ok",
        }, sort_keys=True))
    (base / f"llm_usage_{day}.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")

    cp = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "llm_usage_summary.py"),
         "--from", "2026-05-15", "--to", "2026-05-15",
         "--base-dir", str(base), "--output", "json"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )
    if cp.returncode != 0:
        _record("V5_llm_usage_no_failure_regression", False,
                f"rc={cp.returncode} stderr={cp.stderr[:200]}")
        return
    out = json.loads(cp.stdout)
    ok = True
    detail = []
    if out.get("total_calls") != 10:
        ok = False
        detail.append(f"total_calls={out.get('total_calls')} expected=10")
    if out.get("total_fail") != 2:
        ok = False
        detail.append(f"total_fail={out.get('total_fail')} expected=2 (旧 substring 会算 5)")
    rate = out.get("overall_failure_rate")
    if rate is None or abs(float(rate) - 0.2) > 1e-6:
        ok = False
        detail.append(f"overall_failure_rate={rate} expected=0.2 (旧 substring 会算 0.5)")
    _record("V5_llm_usage_no_failure_regression", ok,
            "; ".join(detail) or "total=10 fail=2 rate=0.2 (no_failure 正确不算 fail)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="verify_interact_019_") as td:
        tmp = Path(td)
        v1_status_token_positives()
        v2_status_no_false_positive()
        v3_three_signals_still_work()
        v4_negative_cases()
        v5_llm_usage_no_failure_regression(tmp)

    out_dir = ROOT / "evidence" / "interact-019"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
    except Exception:
        sha = ""
    summary = {
        "feature": "interact-019",
        "results": _results,
        "ok": all(r["ok"] for r in _results),
        "files_changed": [
            "coco/proactive_trace.py",
            "scripts/verify_interact_019.py",
        ],
        "sha": sha,
        "status_fail_tokens": ["error", "errored", "fail", "failed", "failure"],
    }
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print("\n[verify_interact_019] overall:",
          "PASS" if summary["ok"] else "FAIL")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
