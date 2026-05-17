"""interact-022 verification: status_fail strict-match 契约再确认 + type-strict 锁。

源 backlog: interact-018-backlog-status-fail-strict-match。
该 backlog 的核心需求 (token 白名单精确匹配 + case-insensitive + strip + 反例
no_failure/failsafe/failover) 实际已由 interact-019 实现并 interact-020 文档化。
本 feature 为 **contract reaffirmation + type-strict 锁定**, 无运行时改动:

  - V0: STATUS_FAIL_TOKENS fingerprint 锁 (frozenset 内容固定为 5 个原子 token)
  - V1: 正向 token 全命中 (含大小写 / 前后空白; 内部空格 / 复合形态不命中)
  - V2: 反例不误判 (no_failure / no_fail / no_fail_today / failsafe / fail_safe /
        fail-something / failover / no_error / error_handled / errorless / success / "")
  - V3: type-strict — status 为非 str (None / int / float / bool / list / dict)
        is_fail 安全返回 False, 不抛, 不依赖 truthy。锁住当前行为, 防漂移。
  - V4: emit-site audit — coco/ 下业务代码 grep 不到任何 `"status": "<fail-token>"`
        主动 emit; status 字段仅为历史 jsonl 调试兼容口 (docstring 约定)。
  - V5: regression — 三口主路径 (ok=False / error / failure_reason) 仍正确;
        interact-018/019/021 verify 不需重跑 (本 feature 不改运行时), 但本脚本
        自检三口确保 is_fail 契约一字未动。

跑法::

    uv run python scripts/verify_interact_022.py

retval：0 全 PASS；1 任一失败
evidence 落 evidence/interact-022/verify_summary.json
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_022] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


# ---------------------------------------------------------------------------
# V0: STATUS_FAIL_TOKENS fingerprint 锁 (frozenset 内容 + 类型)
# ---------------------------------------------------------------------------


def v0_fingerprint_lock() -> None:
    from coco.proactive_trace import STATUS_FAIL_TOKENS

    expected = frozenset({"fail", "failed", "failure", "error", "errored"})
    detail_parts = []
    ok = True
    if not isinstance(STATUS_FAIL_TOKENS, frozenset):
        ok = False
        detail_parts.append(f"type={type(STATUS_FAIL_TOKENS).__name__} expected=frozenset")
    if STATUS_FAIL_TOKENS != expected:
        ok = False
        detail_parts.append(
            f"tokens={sorted(STATUS_FAIL_TOKENS)} expected={sorted(expected)}"
        )
    # 每个 token 都必须是 lower-case 原子 (无空格 / 无连字符 / 无下划线)
    for t in STATUS_FAIL_TOKENS:
        if not isinstance(t, str) or t != t.lower() or not re.fullmatch(r"[a-z]+", t):
            ok = False
            detail_parts.append(f"non-atomic token: {t!r}")
    detail = "; ".join(detail_parts) or (
        f"frozenset locked: {sorted(STATUS_FAIL_TOKENS)}"
    )
    _record("V0_fingerprint_lock", ok, detail)


# ---------------------------------------------------------------------------
# V1: 正向 token 全命中 (case-insensitive + strip; 内部空格/复合形态不命中)
# ---------------------------------------------------------------------------


def v1_positive_strict_match() -> None:
    from coco.proactive_trace import is_fail

    positives = [
        "fail", "FAIL", "Fail",
        "failed", "FAILED",
        "failure", "FAILURE",
        "error", "ERROR",
        "errored", "ERRORED",
        "  fail  ", "\tFAILURE\n", "\r\nerror\r\n",
    ]
    # 内部空格 / 复合 token 必须不命中 (strict-match: strip 只剥两端)
    negatives_strict = [
        "fa il",          # 内部空格
        "fail ed",
        " fa il ",
        "fail_safe",      # 复合 (V2 也覆盖, 此处再锁一次 strict 维度)
    ]
    pos_missed = [c for c in positives if not is_fail({"status": c})]
    neg_misfired = [c for c in negatives_strict if is_fail({"status": c})]
    ok = not pos_missed and not neg_misfired
    detail = []
    if pos_missed:
        detail.append(f"positives missed: {pos_missed}")
    if neg_misfired:
        detail.append(f"internal-space/compound misfired: {neg_misfired}")
    if not detail:
        detail.append(
            f"{len(positives)} positives hit + {len(negatives_strict)} strict-negatives rejected"
        )
    _record("V1_positive_strict_match", ok, "; ".join(detail))


# ---------------------------------------------------------------------------
# V2: 反例不误判 (历史 substring 会误判)
# ---------------------------------------------------------------------------


def v2_negative_cases() -> None:
    from coco.proactive_trace import is_fail

    cases = [
        "no_failure", "NO_FAILURE", "No_Failure",
        "no_fail", "no_fail_today",
        "failsafe", "FAILSAFE", "fail_safe",
        "fail-something", "fail-over",
        "failover",                  # backlog 原文点名
        "no_error", "error_handled", "errorless",
        "success", "ok", "passing", "done",
        "", "   ",
    ]
    misfired = [c for c in cases if is_fail({"status": c})]
    ok = not misfired
    detail = (f"misfired={misfired}"
              if misfired
              else f"all {len(cases)} negative-status cases correctly rejected")
    _record("V2_negative_cases", ok, detail)


# ---------------------------------------------------------------------------
# V3: type-strict — status 非 str 时 is_fail safe-False, 不抛
# ---------------------------------------------------------------------------


def v3_type_strict() -> None:
    from coco.proactive_trace import is_fail

    # 非 str 类型: 不允许误判 fail, 不允许抛
    cases = [
        ("None",       {"status": None}),
        ("int_0",      {"status": 0}),
        ("int_1",      {"status": 1}),
        ("float",      {"status": 1.5}),
        ("bool_True",  {"status": True}),
        ("bool_False", {"status": False}),
        ("list",       {"status": ["fail"]}),
        ("dict",       {"status": {"fail": True}}),
        ("tuple",      {"status": ("fail",)}),
        ("bytes",      {"status": b"fail"}),
    ]
    failed_label: List[str] = []
    for label, rec in cases:
        try:
            got = is_fail(rec)
        except Exception as e:  # pragma: no cover - 任何抛即 FAIL
            failed_label.append(f"{label}: raised {type(e).__name__}")
            continue
        if got is not False:
            failed_label.append(f"{label}: got={got!r} expected=False")
    ok = not failed_label
    detail = (
        "; ".join(failed_label)
        if failed_label
        else f"all {len(cases)} non-str status types → False (no raise)"
    )
    _record("V3_type_strict_non_str_status", ok, detail)


# ---------------------------------------------------------------------------
# V4: emit-site audit — coco/ 业务码无主动 status emit
# ---------------------------------------------------------------------------


def v4_emit_site_audit() -> None:
    # grep coco/ 业务码, 排除 proactive_trace.py 自身 (docstring/实现) +
    # scripts/ + tests/。允许的引用: 仅 proactive_trace.py 自身。
    coco_dir = ROOT / "coco"
    pattern = re.compile(r'"status"\s*:|status\s*=\s*["\']')
    offending: List[str] = []
    for p in coco_dir.rglob("*.py"):
        if p.name == "proactive_trace.py":
            continue
        try:
            for ln, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                # 排除注释行
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if pattern.search(line):
                    offending.append(f"{p.relative_to(ROOT)}:{ln}: {line.strip()[:120]}")
        except Exception as e:  # pragma: no cover
            offending.append(f"{p}: read-err {e}")
    ok = not offending
    detail = (
        f"emit-site audit clean — status 字段仅由 proactive_trace.is_fail 消费, "
        "无业务码主动 emit"
        if ok
        else f"{len(offending)} 处可疑: " + " | ".join(offending[:5])
    )
    _record("V4_emit_site_audit", ok, detail)


# ---------------------------------------------------------------------------
# V5: regression — 三口主路径 (ok=False / error / failure_reason) 仍工作
# ---------------------------------------------------------------------------


def v5_three_signals_regression() -> None:
    from coco.proactive_trace import is_fail

    checks = [
        ({"ok": False},                            True,  "ok=False"),
        ({"ok": True},                             False, "ok=True"),
        ({"error": "TimeoutError"},                True,  "error non-empty"),
        ({"error": ""},                            False, "error empty"),
        ({"error": None},                          False, "error None"),
        ({"failure_reason": "rate_limited"},       True,  "failure_reason non-empty"),
        ({"failure_reason": ""},                   False, "failure_reason empty"),
        # status fail-token 与 ok=False 共存 → True
        ({"ok": False, "status": "no_failure"},    True,  "ok=False overrides status FP"),
        # 仅 status fail-token (历史兼容口仍工作)
        ({"status": "FAILURE"},                    True,  "status=FAILURE alone"),
        # 空 rec
        ({},                                       False, "empty rec"),
    ]
    mismatched = []
    for rec, expected, label in checks:
        got = is_fail(rec)
        if got != expected:
            mismatched.append(f"{label}: got={got} expected={expected}")
    ok = not mismatched
    detail = (
        "; ".join(mismatched)
        if mismatched
        else f"{len(checks)} three-signal regression cases all PASS"
    )
    _record("V5_three_signals_regression", ok, detail)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    v0_fingerprint_lock()
    v1_positive_strict_match()
    v2_negative_cases()
    v3_type_strict()
    v4_emit_site_audit()
    v5_three_signals_regression()

    out_dir = ROOT / "evidence" / "interact-022"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
    except Exception:
        sha = ""
    summary = {
        "feature": "interact-022",
        "results": _results,
        "ok": all(r["ok"] for r in _results),
        "files_changed": [
            "scripts/verify_interact_022.py",
        ],
        "sha": sha,
        "direction": "contract-reaffirmation + type-strict 锁",
        "runtime_change": False,
        "status_fail_tokens": ["error", "errored", "fail", "failed", "failure"],
    }
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print("\n[verify_interact_022] overall:",
          "PASS" if summary["ok"] else "FAIL")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
