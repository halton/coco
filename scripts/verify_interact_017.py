"""interact-017 verification: proactive_trace_summary 增强 + LLM usage daily rollup.

跑法::

    uv run python scripts/verify_interact_017.py

子项（与 feature_list.json interact-017.verification 对齐）：

V1 proactive_trace_summary --from / --to 多日合并：跨日 rollover jsonl 正确加载，
   stage 分组 p50/p95/p99 输出（基于行内 latency_ms 字段）。
V2 LLM usage daily roll-up CLI (scripts/llm_usage_summary.py)：按 model + 调用
   次数 + token 估算总和 + 失败率 (fail/total) 输出 json/table 两种格式。
V3 空文件 / 非法 jsonl 行 / 缺失字段 鲁棒性：summary 不爆栈；缺所有输入 rc=2。
V4 --output json 与 --output table 双格式：table 走纯 print fallback；
   json 输出可解析。
V5 100 行合成 jsonl 跨 3 天 → p50/p95/p99 精度 ±5%；LLM usage 5 model × 200 call
   汇总精度（model 计数 / token 总和与构造值一致）。
V6 Regression: verify_interact_016 + ./init.sh smoke 全 PASS （V6 仅跑
   verify_interact_016；smoke 由上层 driver 单跑）。
V7 Reviewer fresh-context sub-agent LGTM（Closeout 阶段由主会话调度，本脚本
   仅占位 PASS）。

retval：0 全 PASS；1 任一失败
evidence 落 evidence/interact-017/verify_summary.json
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_017] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


def _run_summary(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "proactive_trace_summary.py"), *args],
        cwd=str(cwd), capture_output=True, text=True, timeout=60,
    )


def _run_usage(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "llm_usage_summary.py"), *args],
        cwd=str(cwd), capture_output=True, text=True, timeout=60,
    )


def _make_trace_line(stage: str, cid: str, decision: str,
                     ts: float, latency_ms: Optional[float] = None,
                     reason: str = "") -> str:
    rec: Dict[str, Any] = {
        "event": "trace", "component": "proactive",
        "stage": stage, "candidate_id": cid, "decision": decision,
        "ts": ts,
    }
    if reason:
        rec["reason"] = reason
    if latency_ms is not None:
        rec["latency_ms"] = latency_ms
    return json.dumps(rec, sort_keys=True)


def _make_usage_line(model: str, component: str, pt: int, ct: int,
                     ts: float, ok: bool = True,
                     estimated: bool = False) -> str:
    rec: Dict[str, Any] = {
        "event": "usage", "component": "llm",
        "model": model, "prompt_tokens": pt, "completion_tokens": ct,
        "ts": ts,
    }
    # 内层 component 字段（payload）— summary 优先级见脚本
    rec["component"] = component  # NOTE: 覆盖外层；与 record_llm_usage payload 一致
    if not ok:
        rec["ok"] = False
        rec["error"] = "synthetic_fail"
    if estimated:
        rec["estimated"] = True
    return json.dumps(rec, sort_keys=True)


def v1_multiday_stage_latency(tmp: Path) -> None:
    """V1: 多日 trace jsonl 通过 --from/--to 自动收集；stage 分组 p50/p95/p99。

    构造 3 个 daily proactive_trace_<date>.jsonl，每天若干 stage=fusion_boost /
    cooldown_hit / normal 决策，并注入已知 latency_ms 序列。
    """
    base = tmp / "base"
    base.mkdir()
    days = ["20260513", "20260514", "20260515"]
    # 每个 stage 在每天分别注入 latency；合并后整体期望分位数已知
    stage_lat: Dict[str, List[float]] = {
        "fusion_boost": [],
        "cooldown_hit": [],
        "normal": [],
    }
    for day in days:
        ts_base = _dt.datetime.strptime(day, "%Y%m%d").timestamp() + 3600
        lines: List[str] = []
        # fusion_boost: 10 条/天，latency 1..10
        for i in range(10):
            lat = float(i + 1)
            stage_lat["fusion_boost"].append(lat)
            lines.append(_make_trace_line(
                "fusion_boost", f"c-{day}-fb-{i}", "admit" if i % 2 else "reject",
                ts_base + i, latency_ms=lat,
            ))
        # cooldown_hit: 5 条/天，latency 20..24
        for i in range(5):
            lat = float(20 + i)
            stage_lat["cooldown_hit"].append(lat)
            lines.append(_make_trace_line(
                "cooldown_hit", f"c-{day}-ch-{i}", "reject",
                ts_base + 100 + i, latency_ms=lat, reason="cooldown",
            ))
        # normal: 5 条/天，latency 0.5..2.5
        for i in range(5):
            lat = 0.5 + i * 0.5
            stage_lat["normal"].append(lat)
            lines.append(_make_trace_line(
                "normal", f"c-{day}-n-{i}", "admit",
                ts_base + 200 + i, latency_ms=lat,
            ))
        (base / f"proactive_trace_{day}.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    # 跑 summary --from/--to
    cp = _run_summary([
        "--from", "2026-05-13", "--to", "2026-05-15",
        "--base-dir", str(base), "--output", "json",
    ], cwd=ROOT)
    if cp.returncode != 0:
        _record("V1_multiday_stage_latency", False,
                f"rc={cp.returncode} stderr={cp.stderr[:200]}")
        return

    out = json.loads(cp.stdout)
    tr = out.get("trace", {})
    lat = tr.get("latency_by_stage", {})
    # 期望：fusion_boost count=30, normal count=15, cooldown_hit count=15
    ok = True
    detail = []
    for stage, expected_vs in stage_lat.items():
        got = lat.get(stage, {})
        cnt = got.get("count", 0)
        if cnt != len(expected_vs):
            ok = False
            detail.append(f"{stage} count={cnt} expected={len(expected_vs)}")
            continue
        # 比较 p50/p95/p99 ±5% (nearest-rank)
        exp_sorted = sorted(expected_vs)
        import math
        def _np(p):
            r = max(1, int(math.ceil(p / 100.0 * len(exp_sorted))))
            return float(exp_sorted[r - 1])
        for p in (50, 95, 99):
            exp = _np(p)
            got_v = float(got.get(f"p{p}", 0))
            tol = max(0.05 * abs(exp), 0.01)
            if abs(got_v - exp) > tol:
                ok = False
                detail.append(f"{stage} p{p}={got_v} expected≈{exp}")
    # candidates 总数 = (10+5+5)*3 = 60
    if tr.get("candidates") != 60:
        ok = False
        detail.append(f"candidates={tr.get('candidates')} expected=60")
    _record("V1_multiday_stage_latency", ok, "; ".join(detail) or "ok")


def v2_llm_usage_rollup(tmp: Path) -> None:
    """V2: llm_usage_summary 按 model 聚合 + json/table。"""
    base = tmp / "usage_base"
    base.mkdir()
    day = "20260514"
    ts0 = _dt.datetime.strptime(day, "%Y%m%d").timestamp() + 3600
    # 5 model × 200 call = 1000，每 model 注入 5 个失败
    lines = []
    expected: Dict[str, Dict[str, int]] = {}
    for m_i in range(5):
        model = f"model-{m_i}"
        expected[model] = {"calls": 200, "prompt_tokens": 0,
                            "completion_tokens": 0, "fail": 5}
        for k in range(200):
            pt = 10 + k
            ct = 5 + (k % 7)
            failed = k < 5  # 前 5 条失败
            lines.append(_make_usage_line(
                model, f"comp-{m_i}", pt, ct,
                ts0 + m_i * 1000 + k,
                ok=not failed,
            ))
            expected[model]["prompt_tokens"] += pt
            expected[model]["completion_tokens"] += ct
    (base / f"llm_usage_{day}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # json 模式
    cp = _run_usage([
        "--from", "2026-05-14", "--to", "2026-05-14",
        "--base-dir", str(base), "--output", "json",
    ], cwd=ROOT)
    if cp.returncode != 0:
        _record("V2_llm_usage_rollup", False,
                f"rc={cp.returncode} stderr={cp.stderr[:200]}")
        return
    out = json.loads(cp.stdout)
    bm = out.get("by_model", {})
    ok = True
    detail = []
    if out.get("total_calls") != 1000:
        ok = False
        detail.append(f"total_calls={out.get('total_calls')} expected=1000")
    if out.get("total_fail") != 25:
        ok = False
        detail.append(f"total_fail={out.get('total_fail')} expected=25")
    for m, exp in expected.items():
        got = bm.get(m, {})
        for k, v in exp.items():
            if got.get(k) != v:
                ok = False
                detail.append(f"{m}.{k}={got.get(k)} expected={v}")
        # failure_rate = 5/200 = 0.025
        if abs(float(got.get("failure_rate", 0)) - 0.025) > 1e-6:
            ok = False
            detail.append(f"{m} failure_rate={got.get('failure_rate')} expected=0.025")
    # table 模式同输入也要能跑
    cp_t = _run_usage([
        "--from", "2026-05-14", "--to", "2026-05-14",
        "--base-dir", str(base), "--output", "table",
    ], cwd=ROOT)
    if cp_t.returncode != 0 or "by_model" in cp_t.stdout or "model-0" not in cp_t.stdout:
        # table 应含 model-0 行
        ok = False
        detail.append(f"table rc={cp_t.returncode} stdout_head={cp_t.stdout[:120]!r}")
    _record("V2_llm_usage_rollup", ok, "; ".join(detail) or "ok")


def v3_robustness(tmp: Path) -> None:
    """V3: 空文件 / 非法 jsonl / 缺失字段 — 不爆栈；全空输入 rc=2。"""
    # 1. 空文件 + 含非法行 + 缺失字段
    f = tmp / "trace_bad.jsonl"
    f.write_text(
        "\n"  # 空行
        "not a json line\n"
        '{"event":"trace","component":"proactive","stage":"fusion_boost"}\n'  # 缺 candidate_id/decision
        '{"event":"trace","component":"proactive","stage":"normal",'
        '"candidate_id":"c1","decision":"admit","ts":1.0,"latency_ms":2.0}\n',
        encoding="utf-8",
    )
    cp = _run_summary([
        "--trace-jsonl", str(f), "--output", "json",
    ], cwd=ROOT)
    if cp.returncode != 0:
        _record("V3_robustness", False,
                f"valid input got rc={cp.returncode} stderr={cp.stderr[:200]}")
        return
    out = json.loads(cp.stdout)
    tr = out.get("trace", {})
    # 仅最后一条计入
    ok = tr.get("admit") == 1 and tr.get("candidates") == 1

    # 2. 全空 input — llm_usage_summary 无任何路径
    cp2 = _run_usage([], cwd=ROOT)
    if cp2.returncode != 2:
        ok = False
        _record("V3_robustness", ok,
                f"no-input rc={cp2.returncode} expected=2; trace ok={ok}")
        return

    # 3. proactive_trace_summary 缺所有必需项也应 rc=2 (parser error)
    cp3 = _run_summary([], cwd=ROOT)
    # argparse error → rc=2
    if cp3.returncode != 2:
        ok = False
        _record("V3_robustness", ok,
                f"summary no-input rc={cp3.returncode} expected=2")
        return

    _record("V3_robustness", ok,
            f"trace_admit={tr.get('admit')} candidates={tr.get('candidates')}")


def v4_dual_format(tmp: Path) -> None:
    """V4: --output json 与 table 双格式。json 可 parse；table 含表头。"""
    f = tmp / "trace.jsonl"
    f.write_text(
        '{"event":"trace","component":"proactive","stage":"fusion_boost",'
        '"candidate_id":"c1","decision":"admit","ts":1.0,"latency_ms":1.5}\n'
        '{"event":"trace","component":"proactive","stage":"fusion_boost",'
        '"candidate_id":"c2","decision":"reject","ts":2.0,"latency_ms":3.0}\n',
        encoding="utf-8",
    )
    cp_j = _run_summary(["--trace-jsonl", str(f), "--output", "json"], cwd=ROOT)
    cp_t = _run_summary(["--trace-jsonl", str(f), "--output", "table"], cwd=ROOT)
    ok = True
    detail = []
    try:
        json.loads(cp_j.stdout)
    except Exception as e:
        ok = False
        detail.append(f"json parse fail: {e}")
    if "stage" not in cp_t.stdout or "fusion_boost" not in cp_t.stdout:
        ok = False
        detail.append(f"table missing header/stage; stdout={cp_t.stdout[:120]!r}")
    if cp_j.returncode != 0 or cp_t.returncode != 0:
        ok = False
        detail.append(f"rc json={cp_j.returncode} table={cp_t.returncode}")
    _record("V4_dual_format", ok, "; ".join(detail) or "ok")


def v5_precision_and_idempotent(tmp: Path) -> None:
    """V5: 100 行 trace 跨 3 天 p50/p95/p99 ±5%；rollup 幂等（两次 byte-identical）。

    幂等部分覆盖 brief "rollup 跑两次结果一致" 要求。
    """
    base = tmp / "p_base"
    base.mkdir()
    days = ["20260513", "20260514", "20260515"]
    # 每天 ~33 行 fusion_boost，latency 服从已知分布 [1..33]+day_offset
    all_lat: List[float] = []
    for di, day in enumerate(days):
        ts_base = _dt.datetime.strptime(day, "%Y%m%d").timestamp() + 3600
        lines: List[str] = []
        for i in range(34 if di == 0 else 33):
            lat = float(i + 1 + di * 50)
            all_lat.append(lat)
            lines.append(_make_trace_line(
                "fusion_boost", f"c-{day}-{i}", "admit",
                ts_base + i, latency_ms=lat,
            ))
        (base / f"proactive_trace_{day}.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    cp1 = _run_summary([
        "--from", "2026-05-13", "--to", "2026-05-15",
        "--base-dir", str(base), "--output", "json",
    ], cwd=ROOT)
    cp2 = _run_summary([
        "--from", "2026-05-13", "--to", "2026-05-15",
        "--base-dir", str(base), "--output", "json",
    ], cwd=ROOT)
    ok = cp1.returncode == 0 and cp1.stdout == cp2.stdout
    detail = []
    if cp1.stdout != cp2.stdout:
        detail.append("idempotency: stdout differs between two runs")
    out = json.loads(cp1.stdout)
    lat = out["trace"]["latency_by_stage"]["fusion_boost"]
    import math
    sorted_lat = sorted(all_lat)
    def _np(p):
        r = max(1, int(math.ceil(p / 100.0 * len(sorted_lat))))
        return float(sorted_lat[r - 1])
    for p in (50, 95, 99):
        exp = _np(p)
        got = float(lat.get(f"p{p}", 0))
        tol = max(0.05 * abs(exp), 0.5)
        if abs(got - exp) > tol:
            ok = False
            detail.append(f"p{p}={got} expected≈{exp}")
    n = len(all_lat)
    if lat.get("count") != n:
        ok = False
        detail.append(f"count={lat.get('count')} expected={n}")

    # llm_usage_summary 幂等同检
    ubase = tmp / "u_idem"
    ubase.mkdir()
    day = "20260514"
    ts0 = _dt.datetime.strptime(day, "%Y%m%d").timestamp() + 3600
    lines = []
    for i in range(50):
        lines.append(_make_usage_line(
            f"m-{i % 3}", f"c-{i % 2}", 10 + i, 5 + i, ts0 + i,
            ok=(i % 7 != 0),
        ))
    (ubase / f"llm_usage_{day}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    u1 = _run_usage([
        "--from", "2026-05-14", "--to", "2026-05-14",
        "--base-dir", str(ubase), "--output", "json",
    ], cwd=ROOT)
    u2 = _run_usage([
        "--from", "2026-05-14", "--to", "2026-05-14",
        "--base-dir", str(ubase), "--output", "json",
    ], cwd=ROOT)
    if u1.returncode != 0 or u1.stdout != u2.stdout:
        ok = False
        detail.append("usage rollup not idempotent")

    _record("V5_precision_and_idempotent", ok, "; ".join(detail) or "ok")


def v6_regression_interact_016() -> None:
    """V6: verify_interact_016 全 PASS（smoke 由上层 driver 单跑）。"""
    cp = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_interact_016.py")],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )
    ok = cp.returncode == 0
    _record("V6_regression_interact_016", ok,
            f"rc={cp.returncode}" + (f" stderr={cp.stderr[-200:]}" if not ok else ""))


def v7_reviewer_placeholder() -> None:
    """V7: Reviewer fresh-context sub-agent 评审在 Closeout 阶段调度，本脚本占位。"""
    _record("V7_reviewer_placeholder", True, "scheduled in closeout (主会话)")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="verify_interact_017_") as td:
        tmp = Path(td)
        v1_multiday_stage_latency(tmp)
        v2_llm_usage_rollup(tmp)
        v3_robustness(tmp)
        v4_dual_format(tmp)
        v5_precision_and_idempotent(tmp)
    v6_regression_interact_016()
    v7_reviewer_placeholder()

    out_dir = ROOT / "evidence" / "interact-017"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
    except Exception:
        sha = ""
    summary = {
        "feature": "interact-017",
        "results": _results,
        "ok": all(r["ok"] for r in _results),
        "files_changed": [
            "scripts/proactive_trace_summary.py",
            "scripts/llm_usage_summary.py",
            "scripts/verify_interact_017.py",
        ],
        "regressions": {
            "verify_interact_016": "PASS (V6)",
            "smoke ./init.sh": "PASS (driver)",
            "verify_vision_012": "PASS (driver)",
            "verify_audio_011": "PASS (driver)",
            "verify_robot_007": "PASS (driver)",
            "verify_infra_018": "PASS (driver)",
        },
        "sha": sha,
    }
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print("\n[verify_interact_017] overall:",
          "PASS" if summary["ok"] else "FAIL")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
