"""interact-017: LLM usage daily roll-up CLI.

读取 ``~/.coco/llm_usage_<YYYYMMDD>.jsonl``，按 ``model`` 聚合：

  - calls：调用次数
  - prompt_tokens / completion_tokens：累计 token
  - estimated_calls：``estimated=True`` 的调用数（启发式估算，非精确）
  - fail / total / failure_rate：失败统计（行内 ``ok=False`` 或 ``error``/``status``
    含 fail 字样）

用法::

    uv run python scripts/llm_usage_summary.py \\
        --from 2026-05-13 --to 2026-05-15 \\
        --base-dir ~/.coco --output table

    # 或显式指定单/多个 jsonl
    uv run python scripts/llm_usage_summary.py \\
        --usage-jsonl logs/llm_usage_20260514.jsonl --output json

幂等：同一组输入跑两次 JSON byte-identical（dict key sort + 浮点 round + 行排序）。

interact-017 V2/V3/V4 验证项覆盖：
  - V2 按 model + calls + token + failure_rate（json / table）
  - V3 空文件 / 非法 jsonl 行 / 缺字段 鲁棒：跳过坏行不爆栈；输入全空 rc=2
  - V4 ``--output json|table``，table 用纯 print fallback（无 tabulate 依赖）

字段约定（与 ``coco.proactive_trace.record_llm_usage`` 对齐）：
  - ``component``：调用方（如 ``mm_proactive``）— rollup key 之一
  - ``model``：模型名（如 ``gpt-4o-mini``）— 主 rollup key；缺失时 fallback ``"unknown"``
  - ``prompt_tokens`` / ``completion_tokens`` / ``estimated`` / ``ts``
  - ``ok`` (bool, optional) / ``error`` (str, optional)：失败判定
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# interact-018: 让脚本能 import coco.proactive_trace.is_fail
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


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
                continue  # V3: 跳过非法行


def _parse_date(s: str) -> _dt.date:
    return _dt.datetime.strptime(s, "%Y-%m-%d").date()


def _date_range(start: _dt.date, end: _dt.date) -> List[_dt.date]:
    if end < start:
        return []
    out: List[_dt.date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur = cur + _dt.timedelta(days=1)
    return out


def collect_usage_files(
    from_d: Optional[_dt.date],
    to_d: Optional[_dt.date],
    base_dir: Path,
) -> List[Path]:
    if from_d is None or to_d is None:
        return []
    out: List[Path] = []
    for d in _date_range(from_d, to_d):
        p = base_dir / f"llm_usage_{d.strftime('%Y%m%d')}.jsonl"
        if p.exists():
            out.append(p)
    return out


def _is_fail(rec: Dict[str, Any]) -> bool:
    # interact-018: 委托给 coco.proactive_trace.is_fail（三口标准约定）。
    # 失败 import（旧 env / 部分 fixture）时退回旧本地实现，保持兼容。
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


def summarize(paths: List[Path]) -> Dict[str, Any]:
    by_model: Dict[str, Dict[str, int]] = {}
    total_calls = 0
    total_pt = 0
    total_ct = 0
    total_fail = 0
    total_estimated = 0
    for path in paths:
        for rec in _iter_jsonl(path):
            ev = rec.get("event") or ""
            comp_outer = rec.get("component") or ""
            # 与 proactive_trace_summary 一致：兼容 events.jsonl + 直 payload
            if ev == "usage" and comp_outer == "llm":
                pass
            elif "prompt_tokens" in rec and "completion_tokens" in rec:
                pass
            else:
                continue
            model = str(rec.get("model") or "unknown")
            try:
                pt = int(rec.get("prompt_tokens") or 0)
                ct = int(rec.get("completion_tokens") or 0)
            except (TypeError, ValueError):
                continue
            failed = _is_fail(rec)
            estimated = bool(rec.get("estimated"))
            mb = by_model.setdefault(model, {
                "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "fail": 0, "estimated_calls": 0,
            })
            mb["calls"] += 1
            mb["prompt_tokens"] += pt
            mb["completion_tokens"] += ct
            if failed:
                mb["fail"] += 1
                total_fail += 1
            if estimated:
                mb["estimated_calls"] += 1
                total_estimated += 1
            total_calls += 1
            total_pt += pt
            total_ct += ct

    # 计算 failure_rate（round 6 位，幂等）
    model_summary: Dict[str, Dict[str, Any]] = {}
    for m, c in by_model.items():
        total = c["calls"]
        rate = round((c["fail"] / total), 6) if total else 0.0
        model_summary[m] = {
            "calls": c["calls"],
            "prompt_tokens": c["prompt_tokens"],
            "completion_tokens": c["completion_tokens"],
            "fail": c["fail"],
            "estimated_calls": c["estimated_calls"],
            "failure_rate": rate,
        }
    overall_rate = round((total_fail / total_calls), 6) if total_calls else 0.0
    return {
        "total_calls": total_calls,
        "total_prompt_tokens": total_pt,
        "total_completion_tokens": total_ct,
        "total_fail": total_fail,
        "total_estimated_calls": total_estimated,
        "overall_failure_rate": overall_rate,
        "by_model": model_summary,
    }


def _format_table(out: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("== llm usage rollup ==")
    lines.append(
        f"total_calls={out.get('total_calls', 0)}  "
        f"prompt_tokens={out.get('total_prompt_tokens', 0)}  "
        f"completion_tokens={out.get('total_completion_tokens', 0)}  "
        f"fail={out.get('total_fail', 0)}  "
        f"failure_rate={out.get('overall_failure_rate', 0.0):.4f}"
    )
    bm = out.get("by_model", {}) or {}
    if bm:
        lines.append("")
        lines.append(f"{'model':<24} {'calls':>8} {'prompt_tok':>12} "
                     f"{'compl_tok':>12} {'fail':>6} {'fail_rate':>10}")
        lines.append("-" * 76)
        for m in sorted(bm.keys()):
            c = bm[m]
            lines.append(
                f"{m:<24} {c.get('calls', 0):>8} "
                f"{c.get('prompt_tokens', 0):>12} "
                f"{c.get('completion_tokens', 0):>12} "
                f"{c.get('fail', 0):>6} "
                f"{c.get('failure_rate', 0.0):>10.4f}"
            )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="LLM usage daily rollup CLI")
    p.add_argument("--usage-jsonl", type=Path, action="append", default=[],
                   help="path to llm_usage_*.jsonl (repeatable)")
    p.add_argument("--from", dest="from_date", type=str, default=None,
                   help="YYYY-MM-DD lower bound (auto-collect from --base-dir)")
    p.add_argument("--to", dest="to_date", type=str, default=None,
                   help="YYYY-MM-DD upper bound (inclusive)")
    p.add_argument("--base-dir", type=Path,
                   default=Path(os.path.expanduser("~/.coco")),
                   help="directory containing llm_usage_<date>.jsonl")
    p.add_argument("--output", choices=("json", "table"), default="json",
                   help="output format (default: json)")
    args = p.parse_args(argv)

    from_d: Optional[_dt.date] = None
    to_d: Optional[_dt.date] = None
    try:
        if args.from_date:
            from_d = _parse_date(args.from_date)
        if args.to_date:
            to_d = _parse_date(args.to_date)
    except ValueError as e:
        print(f"[llm_usage_summary] ERR: bad --from/--to date: {e}", file=sys.stderr)
        return 2
    if (from_d is None) ^ (to_d is None):
        print("[llm_usage_summary] ERR: --from and --to must be used together",
              file=sys.stderr)
        return 2

    paths: List[Path] = list(args.usage_jsonl)
    if from_d is not None and to_d is not None:
        paths.extend(collect_usage_files(from_d, to_d, args.base_dir))

    if not paths:
        print("[llm_usage_summary] ERR: no input files (use --usage-jsonl or --from/--to)",
              file=sys.stderr)
        return 2

    # 缺文件 warn（仅显式指定的）
    missing = [str(u) for u in args.usage_jsonl if not u.exists()]
    for m in missing:
        print(f"[llm_usage_summary] WARN: input file not found: {m}", file=sys.stderr)

    out = summarize(paths)

    if args.output == "json":
        # 幂等：sort_keys + 固定 indent
        print(json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(_format_table(out))

    return 2 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
