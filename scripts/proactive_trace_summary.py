"""interact-015: proactive trace + LLM usage 离线汇总 CLI.

用法::

    uv run python scripts/proactive_trace_summary.py \\
        --trace-jsonl <path-to-emit-jsonl-with-proactive.trace> \\
        --usage-jsonl <path-to-llm_usage_*.jsonl>

至少需指定一个 jsonl；都给则两份汇总都输出。--trace-jsonl 可读 logs/events.jsonl
（CocoStartup 默认 sink）；--usage-jsonl 读 ~/.coco/llm_usage_<date>.jsonl 滚动文件。

输出（stdout，单 JSON object，便于 fixture 比对）::

    {
      "trace": {
        "candidates": int,           # 总 candidate_id 数
        "admit": int,
        "reject": int,
        "by_stage": {                # stage → {admit, reject}
          "fusion_boost": {"admit": 1, "reject": 2},
          ...
        },
        "rejection_pct_by_stage": {  # stage → reject / (admit+reject) * 100
          "fusion_boost": 66.7,
          ...
        }
      },
      "usage": {
        "total_calls": int,
        "total_prompt_tokens": int,
        "total_completion_tokens": int,
        "by_component": {            # component → {calls, prompt_tokens, completion_tokens}
          "mm_proactive": {...},
        },
        "daily_avg": {               # date → {calls, prompt_tokens, completion_tokens}
          "20260514": {...}
        }
      }
    }
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


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
                continue


def summarize_trace(path: Path) -> Dict[str, Any]:
    candidates: set[str] = set()
    admit = 0
    reject = 0
    by_stage: Dict[str, Dict[str, int]] = {}
    for rec in _iter_jsonl(path):
        # 两种来源：(1) emit logs/events.jsonl 行（component=proactive event=trace）
        # (2) 直接 dump 的 proactive.trace payload（{stage,candidate_id,decision,...}）
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
    rejection_pct: Dict[str, float] = {}
    for stage, c in by_stage.items():
        total = c["admit"] + c["reject"]
        rejection_pct[stage] = round((c["reject"] / total) * 100.0, 2) if total else 0.0
    return {
        "candidates": len(candidates),
        "admit": admit,
        "reject": reject,
        "by_stage": by_stage,
        "rejection_pct_by_stage": rejection_pct,
    }


def summarize_usage(paths: list[Path]) -> Dict[str, Any]:
    total_calls = 0
    total_pt = 0
    total_ct = 0
    by_component: Dict[str, Dict[str, int]] = {}
    daily: Dict[str, Dict[str, int]] = {}
    for path in paths:
        for rec in _iter_jsonl(path):
            # 来源：(1) ~/.coco/llm_usage_*.jsonl（直接 payload）
            #       (2) logs/events.jsonl 中 event=usage component=llm
            ev = rec.get("event") or ""
            comp_outer = rec.get("component") or ""
            if ev == "usage" and comp_outer == "llm":
                pass  # ok, payload fields embed at top level
            elif "prompt_tokens" in rec and "completion_tokens" in rec:
                pass
            else:
                continue
            comp = str(rec.get("component") or comp_outer or "unknown")
            # 优先用内层 component 字段（payload）；如果外层 component=llm 而 payload 内
            # 显式写了 component=mm_proactive，取 payload 的；上面 rec.get 已经只能拿到 payload
            # 实际嵌入的 key（emit 把 payload kwargs 都写到 record 顶层），所以 record["component"]
            # 会被 payload 的 component 覆盖（emit 写 payload 时如果与 reserved 冲突会按
            # JsonlFormatter 处理）。这里 fallback 到 mm_proactive 兜底。
            if comp == "llm":
                comp = "mm_proactive"
            pt = int(rec.get("prompt_tokens") or 0)
            ct = int(rec.get("completion_tokens") or 0)
            ts = float(rec.get("ts") or 0.0)
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


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Proactive trace + LLM usage summarizer")
    p.add_argument("--trace-jsonl", type=Path, default=None,
                   help="path to events.jsonl (or proactive.trace dump) for trace summary")
    p.add_argument("--usage-jsonl", type=Path, action="append", default=[],
                   help="path to llm_usage_*.jsonl (repeatable)")
    args = p.parse_args(argv)

    if args.trace_jsonl is None and not args.usage_jsonl:
        p.error("at least one of --trace-jsonl / --usage-jsonl required")

    out: Dict[str, Any] = {}
    if args.trace_jsonl is not None:
        out["trace"] = summarize_trace(args.trace_jsonl)
    if args.usage_jsonl:
        out["usage"] = summarize_usage(args.usage_jsonl)

    print(json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
