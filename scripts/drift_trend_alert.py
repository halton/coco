#!/usr/bin/env python3
"""drift_trend_alert.py — interact-036b drift 历史趋势回归告警.

读取 evidence/_history/interact_024_drift_history.jsonl 最近 N 条 record,
对 drift_report.{admit, reject_main, reject_preempt}.drift 各序列做:
  1. 单调上行检测 (strictly non-decreasing 且至少一次严格增大)
  2. OLS 一阶线性回归 slope, 与 SLOPE_THRESHOLD 比较

任一序列触发即 stdout dump WARN 行 (machine-readable JSON 单行 + 人读 banner).
本脚本**仅作早期预警**, 退出码恒为 0, 不阻 merge / smoke / verify gate.

接口:
  - analyze_drift_history(records, window=N, slope_threshold=T) -> AlertResult
  - load_drift_history(path) -> list[dict]
  - format_alert_line(alert) -> str (JSON 单行)

interact-036b sim-first; 仅 evidence pipeline + verify-only, 不改业务源码.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence

DEFAULT_WINDOW = 10
DEFAULT_SLOPE_THRESHOLD = 0.5
DRIFT_SUBJECTS = ("admit", "reject_main", "reject_preempt")


@dataclass
class SubjectAlert:
    subject: str
    values: List[float] = field(default_factory=list)
    monotonic_up: bool = False
    slope: float = 0.0
    slope_exceeded: bool = False

    def triggered(self) -> bool:
        return self.monotonic_up or self.slope_exceeded


@dataclass
class AlertResult:
    window: int
    slope_threshold: float
    sample_count: int
    subjects: List[SubjectAlert] = field(default_factory=list)

    def any_triggered(self) -> bool:
        return any(s.triggered() for s in self.subjects)


def load_drift_history(path: Path) -> List[Dict[str, Any]]:
    """Load JSONL drift history, return list of records (skip blank / bad lines)."""
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _ols_slope(values: Sequence[float]) -> float:
    """Plain OLS slope for y vs x=index. n<2 -> 0.0. No numpy dependency."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((xs[i] - mean_x) * (values[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if den == 0.0:
        return 0.0
    return num / den


def _is_monotonic_up(values: Sequence[float]) -> bool:
    """Strictly non-decreasing AND at least one strict increase (rules out all-equal)."""
    if len(values) < 2:
        return False
    saw_strict = False
    for a, b in zip(values, values[1:]):
        if b < a:
            return False
        if b > a:
            saw_strict = True
    return saw_strict


def _extract_subject(records: Sequence[Dict[str, Any]], subject: str) -> List[float]:
    out: List[float] = []
    for rec in records:
        dr = rec.get("drift_report")
        if not isinstance(dr, dict):
            continue
        sub = dr.get(subject)
        if not isinstance(sub, dict):
            continue
        v = sub.get("drift")
        if isinstance(v, (int, float)):
            out.append(float(v))
    return out


def analyze_drift_history(
    records: Sequence[Dict[str, Any]],
    window: int = DEFAULT_WINDOW,
    slope_threshold: float = DEFAULT_SLOPE_THRESHOLD,
) -> AlertResult:
    """Analyze last `window` records; per-subject monotonic + OLS slope detection."""
    if window <= 0:
        window = DEFAULT_WINDOW
    tail = list(records[-window:])
    result = AlertResult(
        window=window,
        slope_threshold=slope_threshold,
        sample_count=len(tail),
    )
    for subj in DRIFT_SUBJECTS:
        vals = _extract_subject(tail, subj)
        sa = SubjectAlert(subject=subj, values=vals)
        if len(vals) >= 2:
            sa.monotonic_up = _is_monotonic_up(vals)
            sa.slope = _ols_slope(vals)
            sa.slope_exceeded = sa.slope >= slope_threshold
        result.subjects.append(sa)
    return result


def format_alert_line(result: AlertResult) -> str:
    """Machine-readable single-line JSON alert payload."""
    payload: Dict[str, Any] = {
        "kind": "interact_036b_drift_trend_alert",
        "window": result.window,
        "sample_count": result.sample_count,
        "slope_threshold": result.slope_threshold,
        "any_triggered": result.any_triggered(),
        "subjects": [
            {
                "subject": s.subject,
                "monotonic_up": s.monotonic_up,
                "slope": round(s.slope, 6),
                "slope_exceeded": s.slope_exceeded,
                "values": s.values,
            }
            for s in result.subjects
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def emit_alert(result: AlertResult, stream=sys.stdout) -> None:
    """Emit human banner + JSON line iff any subject triggered (else single OK line)."""
    if result.any_triggered():
        print("[drift_trend_alert] WARN: drift trending UP", file=stream)
        for s in result.subjects:
            if s.triggered():
                reasons = []
                if s.monotonic_up:
                    reasons.append("monotonic_up")
                if s.slope_exceeded:
                    reasons.append(f"slope={s.slope:.4f}>=thresh={result.slope_threshold}")
                print(
                    f"[drift_trend_alert]   subject={s.subject} {' '.join(reasons)} values={s.values}",
                    file=stream,
                )
    else:
        print(
            f"[drift_trend_alert] OK window={result.window} sample={result.sample_count} no trend",
            file=stream,
        )
    print(format_alert_line(result), file=stream, flush=True)


def main(argv: List[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    default_path = root / "evidence" / "_history" / "interact_024_drift_history.jsonl"
    path = Path(argv[1]) if len(argv) > 1 else default_path
    window = int(argv[2]) if len(argv) > 2 else DEFAULT_WINDOW
    thresh = float(argv[3]) if len(argv) > 3 else DEFAULT_SLOPE_THRESHOLD
    records = load_drift_history(path)
    result = analyze_drift_history(records, window=window, slope_threshold=thresh)
    emit_alert(result)
    return 0  # never block; alert-only


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
