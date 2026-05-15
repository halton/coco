"""interact-015/016/017: proactive trace + LLM usage 离线汇总 CLI.

用法::

    uv run python scripts/proactive_trace_summary.py \\
        --trace-jsonl <path-to-emit-jsonl-with-proactive.trace> \\
        --usage-jsonl <path-to-llm_usage_*.jsonl>

或 interact-017 多日合并模式（从 ``~/.coco/`` 自动按日期范围加载）::

    uv run python scripts/proactive_trace_summary.py \\
        --from 2026-05-13 --to 2026-05-15

至少需指定一个 jsonl（或 --from/--to）；都给则两份汇总都输出。
``--trace-jsonl`` 可读 logs/events.jsonl（CocoStartup 默认 sink）；
``--usage-jsonl`` 读 ``~/.coco/llm_usage_<date>.jsonl`` 滚动文件。

输出（默认 ``--output json``，``--output table`` 走纯 print 表格 fallback）::

    {
      "trace": {
        "candidates": int,
        "admit": int,
        "reject": int,
        "by_stage": {                # stage → {admit, reject}
          "fusion_boost": {"admit": 1, "reject": 2},
          ...
        },
        "rejection_pct_by_stage": {
          "fusion_boost": 66.7, ...
        },
        "latency_by_stage": {        # interact-017: 行内含 latency_ms 字段时聚合
          "fusion_boost": {"count": 12, "p50": 1.2, "p95": 5.8, "p99": 9.0},
          ...
        }
      },
      "usage": {...},
    }

interact-017 新增：
- ``--from`` / ``--to`` 日期范围 + ``~/.coco/`` 下 daily glob 自动加载
- ``latency_by_stage`` p50/p95/p99（基于行 ``latency_ms`` extra 字段）
- ``--output table|json``（默认 json，table 用纯 print fallback）
- 鲁棒性：空文件 / 非法 jsonl 行 / 缺字段 不爆栈；输入完全空 / 非法 rc=2
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# interact-018: 让脚本能 import coco.proactive_trace.is_fail
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _is_fail(rec: Dict[str, Any]) -> bool:
    """interact-018: emit-end 标准 fail 三口约定（委托 coco.proactive_trace.is_fail）。

    供 trace summary 与 llm_usage summary 复用同一约定:
      ok=False / error=非空 str / failure_reason=非空 str（兼容 status 含 fail）。
    """
    try:
        from coco.proactive_trace import is_fail as _shared_is_fail
    except Exception:  # noqa: BLE001
        _shared_is_fail = None
    if _shared_is_fail is not None:
        return bool(_shared_is_fail(rec))
    ok = rec.get("ok")
    if ok is False:
        return True
    err = rec.get("error")
    if isinstance(err, str) and err.strip():
        return True
    fr = rec.get("failure_reason")
    if isinstance(fr, str) and fr.strip():
        return True
    status = rec.get("status")
    if isinstance(status, str) and "fail" in status.lower():
        return True
    return False


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # interact-017 V3: 非法 jsonl 行跳过，不爆栈
                continue


def _percentile(values: List[float], p: float) -> float:
    """nearest-rank percentile（与 numpy 略异，但无依赖且对小样本稳定）。"""
    if not values:
        return 0.0
    s = sorted(values)
    if p <= 0:
        return float(s[0])
    if p >= 100:
        return float(s[-1])
    # nearest-rank: ceil(p/100 * N)
    rank = max(1, int(math.ceil((p / 100.0) * len(s))))
    return float(s[rank - 1])


def summarize_trace(paths: List[Path]) -> Dict[str, Any]:
    candidates: set[str] = set()
    admit = 0
    reject = 0
    by_stage: Dict[str, Dict[str, int]] = {}
    latency_by_stage: Dict[str, List[float]] = {}
    # interact-018: emit-end fail 统计（三口标准 OR）；与 reject 解耦——
    # reject 是仲裁决策（idle/cooldown/...），fail 是 emit 端"这次行为本身失败"。
    fail_by_stage: Dict[str, int] = {}
    total_fail = 0
    for path in paths:
        for rec in _iter_jsonl(path):
            ev = rec.get("event") or ""
            comp = rec.get("component") or ""
            is_trace = (ev == "trace" and comp == "proactive") or (
                "stage" in rec and "candidate_id" in rec and "decision" in rec
            )
            if not is_trace:
                continue
            stage = str(rec.get("stage") or "")
            cid = str(rec.get("candidate_id") or "")
            decision = str(rec.get("decision") or "")
            if cid:
                candidates.add(cid)
            bucket = by_stage.setdefault(stage, {"admit": 0, "reject": 0})
            if decision == "admit":
                admit += 1
                bucket["admit"] += 1
            elif decision == "reject":
                reject += 1
                bucket["reject"] += 1
            # interact-017: latency 聚合（行内可选 latency_ms 字段）
            lat = rec.get("latency_ms")
            if lat is not None:
                try:
                    latency_by_stage.setdefault(stage, []).append(float(lat))
                except (TypeError, ValueError):
                    pass
            # interact-018: emit-end fail 三口判定（_is_fail 函数）
            if _is_fail(rec):
                fail_by_stage[stage] = fail_by_stage.get(stage, 0) + 1
                total_fail += 1

    rejection_pct: Dict[str, float] = {}
    for stage, c in by_stage.items():
        total = c["admit"] + c["reject"]
        rejection_pct[stage] = round((c["reject"] / total) * 100.0, 2) if total else 0.0

    latency_summary: Dict[str, Dict[str, float]] = {}
    for stage, vs in latency_by_stage.items():
        latency_summary[stage] = {
            "count": len(vs),
            "p50": round(_percentile(vs, 50.0), 3),
            "p95": round(_percentile(vs, 95.0), 3),
            "p99": round(_percentile(vs, 99.0), 3),
        }

    return {
        "candidates": len(candidates),
        "admit": admit,
        "reject": reject,
        "by_stage": by_stage,
        "rejection_pct_by_stage": rejection_pct,
        "latency_by_stage": latency_summary,
        "fail_by_stage": fail_by_stage,
        "total_fail": total_fail,
    }


def summarize_usage(paths: List[Path]) -> Dict[str, Any]:
    total_calls = 0
    total_pt = 0
    total_ct = 0
    by_component: Dict[str, Dict[str, int]] = {}
    daily: Dict[str, Dict[str, int]] = {}
    for path in paths:
        for rec in _iter_jsonl(path):
            ev = rec.get("event") or ""
            comp_outer = rec.get("component") or ""
            if ev == "usage" and comp_outer == "llm":
                pass
            elif "prompt_tokens" in rec and "completion_tokens" in rec:
                pass
            else:
                continue
            comp = str(rec.get("component") or comp_outer or "unknown")
            if comp == "llm":
                comp = "mm_proactive"
            try:
                pt = int(rec.get("prompt_tokens") or 0)
                ct = int(rec.get("completion_tokens") or 0)
                ts = float(rec.get("ts") or 0.0)
            except (TypeError, ValueError):
                continue
            total_calls += 1
            total_pt += pt
            total_ct += ct
            cb = by_component.setdefault(
                comp, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
            )
            cb["calls"] += 1
            cb["prompt_tokens"] += pt
            cb["completion_tokens"] += ct
            if ts > 0:
                date_str = _dt.datetime.fromtimestamp(ts).strftime("%Y%m%d")
                db = daily.setdefault(
                    date_str, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
                )
                db["calls"] += 1
                db["prompt_tokens"] += pt
                db["completion_tokens"] += ct
    return {
        "total_calls": total_calls,
        "total_prompt_tokens": total_pt,
        "total_completion_tokens": total_ct,
        "by_component": by_component,
        "daily_avg": daily,
    }


# ---------------------------------------------------------------------------
# interact-017: 多日范围 + glob 自动收集
# ---------------------------------------------------------------------------


def _parse_date(s: str) -> _dt.date:
    return _dt.datetime.strptime(s, "%Y-%m-%d").date()


def _date_range(start: _dt.date, end: _dt.date) -> List[_dt.date]:
    if end < start:
        return []
    days: List[_dt.date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur = cur + _dt.timedelta(days=1)
    return days


def collect_daily(
    kind: str,
    from_d: Optional[_dt.date],
    to_d: Optional[_dt.date],
    base_dir: Path,
) -> List[Path]:
    """收集 ``base_dir`` 下匹配日期范围的 daily jsonl。

    kind=``trace`` → 文件名 ``proactive_trace_<YYYYMMDD>.jsonl``
    kind=``usage`` → 文件名 ``llm_usage_<YYYYMMDD>.jsonl``
    （trace 当前 runtime 滚动落盘 by interact-016 仅 usage 一种；trace 多日
    合并以 ``events.jsonl`` 或显式 ``--trace-jsonl`` 为主，本 helper 仅按
    约定模板尝试 glob，缺失静默跳过。）
    """
    if from_d is None or to_d is None:
        return []
    if kind == "trace":
        template = "proactive_trace_{date}.jsonl"
    elif kind == "usage":
        template = "llm_usage_{date}.jsonl"
    else:
        return []
    out: List[Path] = []
    for d in _date_range(from_d, to_d):
        ds = d.strftime("%Y%m%d")
        candidate = base_dir / template.format(date=ds)
        if candidate.exists():
            out.append(candidate)
    return out


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------


def _format_table(out: Dict[str, Any]) -> str:
    """纯 print 表格 fallback（不依赖 tabulate）。"""
    lines: List[str] = []
    if "trace" in out:
        tr = out["trace"]
        lines.append("== trace ==")
        lines.append(f"candidates={tr.get('candidates', 0)}  "
                     f"admit={tr.get('admit', 0)}  reject={tr.get('reject', 0)}")
        bs = tr.get("by_stage", {}) or {}
        if bs:
            lines.append("")
            lines.append(f"{'stage':<24} {'admit':>8} {'reject':>8} {'rej%':>8}")
            lines.append("-" * 52)
            rp = tr.get("rejection_pct_by_stage", {}) or {}
            for stage in sorted(bs.keys()):
                c = bs[stage]
                pct = rp.get(stage, 0.0)
                lines.append(f"{stage:<24} {c.get('admit', 0):>8} "
                             f"{c.get('reject', 0):>8} {pct:>7.1f}%")
        lat = tr.get("latency_by_stage", {}) or {}
        if lat:
            lines.append("")
            lines.append(f"{'stage':<24} {'n':>6} {'p50_ms':>10} "
                         f"{'p95_ms':>10} {'p99_ms':>10}")
            lines.append("-" * 64)
            for stage in sorted(lat.keys()):
                s = lat[stage]
                lines.append(f"{stage:<24} {s.get('count', 0):>6} "
                             f"{s.get('p50', 0):>10.3f} "
                             f"{s.get('p95', 0):>10.3f} "
                             f"{s.get('p99', 0):>10.3f}")
    if "usage" in out:
        us = out["usage"]
        if lines:
            lines.append("")
        lines.append("== usage ==")
        lines.append(f"total_calls={us.get('total_calls', 0)}  "
                     f"prompt_tokens={us.get('total_prompt_tokens', 0)}  "
                     f"completion_tokens={us.get('total_completion_tokens', 0)}")
        bc = us.get("by_component", {}) or {}
        if bc:
            lines.append("")
            lines.append(f"{'component':<24} {'calls':>8} "
                         f"{'prompt_tok':>12} {'compl_tok':>12}")
            lines.append("-" * 60)
            for comp in sorted(bc.keys()):
                c = bc[comp]
                lines.append(f"{comp:<24} {c.get('calls', 0):>8} "
                             f"{c.get('prompt_tokens', 0):>12} "
                             f"{c.get('completion_tokens', 0):>12}")
        daily = us.get("daily_avg", {}) or {}
        if daily:
            lines.append("")
            lines.append(f"{'date':<12} {'calls':>8} {'prompt_tok':>12} {'compl_tok':>12}")
            lines.append("-" * 48)
            for d in sorted(daily.keys()):
                c = daily[d]
                lines.append(f"{d:<12} {c.get('calls', 0):>8} "
                             f"{c.get('prompt_tokens', 0):>12} "
                             f"{c.get('completion_tokens', 0):>12}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Proactive trace + LLM usage summarizer")
    p.add_argument("--trace-jsonl", type=Path, default=None,
                   help="path to events.jsonl (or proactive.trace dump) for trace summary")
    p.add_argument("--usage-jsonl", type=Path, action="append", default=[],
                   help="path to llm_usage_*.jsonl (repeatable)")
    # interact-017
    p.add_argument("--from", dest="from_date", type=str, default=None,
                   help="YYYY-MM-DD lower bound for daily jsonl auto-collection from --base-dir")
    p.add_argument("--to", dest="to_date", type=str, default=None,
                   help="YYYY-MM-DD upper bound (inclusive)")
    p.add_argument("--base-dir", type=Path,
                   default=Path(os.path.expanduser("~/.coco")),
                   help="directory containing llm_usage_<date>.jsonl / proactive_trace_<date>.jsonl")
    p.add_argument("--output", choices=("json", "table"), default="json",
                   help="output format (default: json)")
    args = p.parse_args(argv)

    # parse 日期
    from_d: Optional[_dt.date] = None
    to_d: Optional[_dt.date] = None
    try:
        if args.from_date:
            from_d = _parse_date(args.from_date)
        if args.to_date:
            to_d = _parse_date(args.to_date)
    except ValueError as e:
        print(f"[proactive_trace_summary] ERR: bad --from/--to date: {e}",
              file=sys.stderr)
        return 2
    if (from_d is None) ^ (to_d is None):
        print("[proactive_trace_summary] ERR: --from and --to must be used together",
              file=sys.stderr)
        return 2

    has_range = from_d is not None and to_d is not None
    if args.trace_jsonl is None and not args.usage_jsonl and not has_range:
        p.error("at least one of --trace-jsonl / --usage-jsonl / (--from + --to) required")

    # 收集 trace 输入
    trace_paths: List[Path] = []
    if args.trace_jsonl is not None:
        trace_paths.append(args.trace_jsonl)
    if has_range:
        trace_paths.extend(collect_daily("trace", from_d, to_d, args.base_dir))

    # 收集 usage 输入
    usage_paths: List[Path] = list(args.usage_jsonl)
    if has_range:
        usage_paths.extend(collect_daily("usage", from_d, to_d, args.base_dir))

    # 缺文件 warn（V3：仅显式指定但找不到才视为 missing；range 模式静默跳过缺日）
    missing: list[str] = []
    if args.trace_jsonl is not None and not args.trace_jsonl.exists():
        missing.append(str(args.trace_jsonl))
    for u in args.usage_jsonl:
        if not u.exists():
            missing.append(str(u))
    for m in missing:
        print(f"[proactive_trace_summary] WARN: input file not found: {m}",
              file=sys.stderr)

    out: Dict[str, Any] = {}
    if trace_paths:
        out["trace"] = summarize_trace(trace_paths)
    if usage_paths:
        out["usage"] = summarize_usage(usage_paths)

    if not out and has_range:
        # range 模式但无任何 daily 文件命中：仍输出空骨架而非裸 rc=2，方便 CI 区分
        out = {"trace": {"candidates": 0, "admit": 0, "reject": 0,
                          "by_stage": {}, "rejection_pct_by_stage": {},
                          "latency_by_stage": {},
                          "fail_by_stage": {}, "total_fail": 0},
               "usage": {"total_calls": 0, "total_prompt_tokens": 0,
                          "total_completion_tokens": 0,
                          "by_component": {}, "daily_avg": {}}}

    if args.output == "json":
        print(json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(_format_table(out))

    return 2 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
