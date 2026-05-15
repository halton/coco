#!/usr/bin/env python3
"""infra-016: health_summary CLI — 解析 verify/smoke history jsonl 输出趋势.

用法：
  python scripts/health_summary.py                # 默认：最近 20 次 verify + smoke
  python scripts/health_summary.py --last 50      # 改窗口
  python scripts/health_summary.py --topk 5       # Top-K failing verify
  python scripts/health_summary.py --kind verify  # 只看 verify
  python scripts/health_summary.py --kind smoke   # 只看 smoke
  python scripts/health_summary.py --json         # 机读：结构化输出而不是 table

读 evidence/_history/{verify,smoke}_history.jsonl，输出：
  - 最近 N 次 PASS rate
  - 平均 duration_s
  - Top-K 失败 verify 频次（verify 才有 failed_names）
  - 各 area smoke 趋势（最近 N 次每 area PASS 比例）

任何 jsonl 缺失或为空，对应段输出 "no data"，整体返回 rc=0（开发态正常）。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
from _history_writer import (  # noqa: E402
    SMOKE_JSONL,
    VERIFY_JSONL,
    load_records,
)


def _summarize_verify(records: list[dict], topk: int) -> dict:
    if not records:
        return {"count": 0}
    n = len(records)
    pass_total = sum(r.get("pass", 0) for r in records)
    total_total = sum(r.get("total", 0) for r in records)
    pass_rate = (pass_total / total_total) if total_total else 0.0
    avg_dur = sum(r.get("duration_s", 0.0) for r in records) / n
    # Top-K failing verify by occurrence count
    fail_counter: Counter[str] = Counter()
    for r in records:
        for name in r.get("failed_names", []) or []:
            fail_counter[name] += 1
    top_fails = fail_counter.most_common(topk)
    runs_with_fail = sum(1 for r in records if r.get("fail", 0) > 0)
    return {
        "count": n,
        "pass_rate": round(pass_rate, 4),
        "total_runs_with_failure": runs_with_fail,
        "avg_duration_s": round(avg_dur, 2),
        "top_failing": [{"name": name, "occurrences": cnt} for name, cnt in top_fails],
        "first_ts": records[0].get("ts"),
        "last_ts": records[-1].get("ts"),
    }


def _summarize_smoke(records: list[dict]) -> dict:
    if not records:
        return {"count": 0}
    n = len(records)
    pass_total = sum(r.get("pass", 0) for r in records)
    total_total = sum(r.get("total", 0) for r in records)
    pass_rate = (pass_total / total_total) if total_total else 0.0
    avg_dur = sum(r.get("duration_s", 0.0) for r in records) / n
    # 各 area PASS 比例
    area_pass: Counter[str] = Counter()
    area_total: Counter[str] = Counter()
    for r in records:
        for area, status in (r.get("areas") or {}).items():
            area_total[area] += 1
            if status == "PASS":
                area_pass[area] += 1
    area_trend = {
        area: {
            "pass": area_pass[area],
            "total": area_total[area],
            "pass_rate": round(area_pass[area] / area_total[area], 4) if area_total[area] else 0.0,
        }
        for area in sorted(area_total)
    }
    return {
        "count": n,
        "pass_rate": round(pass_rate, 4),
        "avg_duration_s": round(avg_dur, 2),
        "areas": area_trend,
        "first_ts": records[0].get("ts"),
        "last_ts": records[-1].get("ts"),
    }


def _print_table(title: str, summary: dict) -> None:
    print(f"\n=== {title} ===")
    if summary.get("count", 0) == 0:
        print("  no data")
        return
    for k, v in summary.items():
        if k in ("top_failing", "areas"):
            continue
        print(f"  {k:>22}: {v}")
    if "top_failing" in summary and summary["top_failing"]:
        print("  top_failing:")
        for entry in summary["top_failing"]:
            print(f"    - {entry['name']:<40s}  x{entry['occurrences']}")
    if "areas" in summary and summary["areas"]:
        print("  areas:")
        for area, stat in summary["areas"].items():
            print(f"    {area:<20s}  pass={stat['pass']}/{stat['total']}  rate={stat['pass_rate']:.2%}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Summarize verify/smoke history jsonl trends (infra-016)")
    p.add_argument("--last", type=int, default=20, help="最近 N 次（默认 20）")
    p.add_argument("--topk", type=int, default=5, help="Top-K failing verify (默认 5)")
    p.add_argument("--kind", choices=("verify", "smoke", "both"), default="both",
                   help="只看 verify / smoke / both（默认 both）")
    p.add_argument("--json", action="store_true", help="结构化 json 输出（不打 table）")
    p.add_argument("--verify-jsonl", type=Path, default=VERIFY_JSONL,
                   help="覆盖 verify jsonl 路径（测试用）")
    p.add_argument("--smoke-jsonl", type=Path, default=SMOKE_JSONL,
                   help="覆盖 smoke jsonl 路径（测试用）")
    args = p.parse_args(argv)

    out: dict = {}
    if args.kind in ("verify", "both"):
        v_recs = load_records(args.verify_jsonl)
        v_window = v_recs[-args.last:] if args.last > 0 else v_recs
        out["verify"] = _summarize_verify(v_window, args.topk)
    if args.kind in ("smoke", "both"):
        s_recs = load_records(args.smoke_jsonl)
        s_window = s_recs[-args.last:] if args.last > 0 else s_recs
        out["smoke"] = _summarize_smoke(s_window)

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if "verify" in out:
            _print_table(f"verify history (last {args.last})", out["verify"])
        if "smoke" in out:
            _print_table(f"smoke history (last {args.last})", out["smoke"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
