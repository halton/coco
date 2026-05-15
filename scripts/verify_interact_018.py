"""interact-018 verification: ProactiveScheduler emit 端 latency_ms wire + _is_fail 标准化.

跑法::

    uv run python scripts/verify_interact_018.py

子项（与 feature_list.json interact-018.verification + brief 对齐）：

V1 ProactiveScheduler.maybe_trigger 触发后, 至少一条 proactive.* emit 含
   latency_ms (float, >= 0)。生产路径中 emit_trace 自带 latency_ms extra
   kwarg (interact-018 wire)。开启 COCO_PROACTIVE_TRACE 后断言。

V2 _is_fail 三口约定: 对人工注入的 ``ok=False`` / ``error=...`` /
   ``failure_reason=...`` 三种独立 record, is_fail 都返回 True。

V3 _is_fail 缺失字段 / 字段为 truthy 字符串 ("ok"/"success") 时返回 False
   （不被误判为 fail）。

V4 proactive_trace_summary.py 喂含 latency_ms 的 jsonl, 按 stage 聚合输出
   p50/p95/p99 数字 (数值与构造一致 ±5%)。

V5 llm_usage_summary.py 在含三种 fail 形态的 jsonl 上 failure_rate 计算正确,
   人工算一遍能对得上 (三种 fail 各 1 条 + 7 条 ok → fail=3/total=10=0.3)。

retval：0 全 PASS；1 任一失败
evidence 落 evidence/interact-018/verify_summary.json
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_018] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


# ---------------------------------------------------------------------------
# V1: 生产 maybe_trigger 路径 emit 含 latency_ms
# ---------------------------------------------------------------------------


def v1_latency_wire_in_production() -> None:
    """V1: ProactiveScheduler 真触发后, emit_trace 至少一条带 latency_ms (float, >=0)。"""
    # 启用 trace gate, 收集 emit
    os.environ["COCO_PROACTIVE_TRACE"] = "1"
    from coco import proactive_trace as pt
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    from coco.power_state import PowerState

    captured: List[Dict[str, Any]] = []

    def _emit(event: str, **payload: Any) -> None:
        captured.append({"event": event, **payload})

    pt.set_emit_override(_emit)

    try:
        # 简单 fake power_state / face_tracker
        class _FakePS:
            current_state = PowerState.ACTIVE

        class _FakeFace:
            def latest(self):
                class _S:
                    present = True
                return _S()

        # fake llm/tts
        def _llm(text, *, system_prompt=None):
            return "你好呀，今天聊点什么"

        def _tts(text, blocking=True):
            return None

        cfg = ProactiveConfig(
            enabled=True,
            idle_threshold_s=10.0,
            cooldown_s=10.0,
            max_topics_per_hour=10,
            tick_s=1.0,
        )
        sched = ProactiveScheduler(
            config=cfg,
            power_state=_FakePS(),
            face_tracker=_FakeFace(),
            llm_reply_fn=_llm,
            tts_say_fn=_tts,
            emit_fn=_emit,
        )
        # 把 last_interaction 推到 60s 前, 让 idle 条件满足
        sched._last_interaction_ts = sched.clock() - 60.0

        ok_trigger = sched.maybe_trigger()
        if not ok_trigger:
            _record("V1_latency_wire_in_production", False,
                    f"maybe_trigger returned False; captured={len(captured)}")
            return

        # 期望至少一条 proactive.trace 含 latency_ms
        trace_events = [
            e for e in captured
            if e.get("event") == "proactive.trace" and "latency_ms" in e
        ]
        if not trace_events:
            _record("V1_latency_wire_in_production", False,
                    f"no proactive.trace events with latency_ms; "
                    f"events={[e.get('event') for e in captured]}")
            return

        # 至少一条 latency_ms 是 float 且 >= 0
        any_valid = False
        for e in trace_events:
            lat = e.get("latency_ms")
            if isinstance(lat, (int, float)) and lat >= 0:
                any_valid = True
                break
        if not any_valid:
            _record("V1_latency_wire_in_production", False,
                    f"trace events have latency_ms but none float>=0; "
                    f"sample={trace_events[0]}")
            return

        # 应同时见到 arbit_winner (admit) stage 带 latency
        winners = [e for e in trace_events if e.get("stage") == "arbit_winner"]
        if not winners:
            _record("V1_latency_wire_in_production", False,
                    f"no arbit_winner stage in trace events; "
                    f"stages={[e.get('stage') for e in trace_events]}")
            return

        _record("V1_latency_wire_in_production", True,
                f"{len(trace_events)} trace events with latency_ms; "
                f"sample lat={trace_events[0].get('latency_ms')} "
                f"stage={trace_events[0].get('stage')}; "
                f"arbit_winner={len(winners)}")
    finally:
        pt.set_emit_override(None)
        os.environ.pop("COCO_PROACTIVE_TRACE", None)


# ---------------------------------------------------------------------------
# V2: _is_fail 三口约定 (ok=False / error / failure_reason)
# ---------------------------------------------------------------------------


def v2_is_fail_three_signals() -> None:
    """V2: is_fail 在 ok=False / error=非空 / failure_reason=非空 三口下都返回 True。"""
    from coco.proactive_trace import is_fail

    rec_ok_false = {"ok": False, "topic": "x"}
    rec_error = {"error": "ConnectionError: timeout", "topic": "x"}
    rec_failure_reason = {"failure_reason": "llm_or_tts", "topic": "x"}
    rec_status_fail = {"status": "RPC_FAILURE", "topic": "x"}  # 历史兼容

    ok = True
    detail = []
    for label, rec in [
        ("ok=False", rec_ok_false),
        ("error=...", rec_error),
        ("failure_reason=...", rec_failure_reason),
        ("status~fail (legacy)", rec_status_fail),
    ]:
        got = is_fail(rec)
        if not got:
            ok = False
            detail.append(f"{label} → False (expected True)")
    _record("V2_is_fail_three_signals", ok, "; ".join(detail) or "三口+legacy 全识别为 fail")


# ---------------------------------------------------------------------------
# V3: _is_fail 误判防御 (缺字段 / 字段为 truthy 字符串)
# ---------------------------------------------------------------------------


def v3_is_fail_not_false_positive() -> None:
    """V3: 字段缺失 / ok="ok" 等 truthy 字符串 / error="" 不被误判为 fail。"""
    from coco.proactive_trace import is_fail

    not_fails = [
        ("empty", {}),
        ("ok=True", {"ok": True}),
        ('ok="ok"', {"ok": "ok"}),
        ('ok="success"', {"ok": "success"}),
        ("error=empty", {"error": ""}),
        ("error=None", {"error": None}),
        ("failure_reason=empty", {"failure_reason": ""}),
        ("status=success", {"status": "success"}),
        ("just_topic", {"topic": "你好"}),
    ]
    ok = True
    detail = []
    for label, rec in not_fails:
        got = is_fail(rec)
        if got:
            ok = False
            detail.append(f"{label} → True (expected False)")
    _record("V3_is_fail_not_false_positive", ok,
            "; ".join(detail) or "9 种非 fail 输入全返回 False")


# ---------------------------------------------------------------------------
# V4: proactive_trace_summary p50/p95/p99 数值正确
# ---------------------------------------------------------------------------


def v4_trace_summary_latency_aggregation(tmp: Path) -> None:
    """V4: 喂含 latency_ms 的 jsonl, 按 stage 聚合 p50/p95/p99 数字正确 (±5%)。"""
    base = tmp / "v4_base"
    base.mkdir()
    day = "20260515"
    ts_base = _dt.datetime.strptime(day, "%Y%m%d").timestamp() + 3600
    # arbit_winner stage 注入 latency_ms 1..20 (整数), p50≈10, p95=19, p99=20
    lines = []
    for i in range(20):
        lat = float(i + 1)
        lines.append(json.dumps({
            "event": "trace",
            "component": "proactive",
            "stage": "arbit_winner",
            "candidate_id": f"c-v4-{i}",
            "decision": "admit",
            "ts": ts_base + i,
            "latency_ms": lat,
        }, sort_keys=True))
    (base / f"proactive_trace_{day}.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")

    cp = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "proactive_trace_summary.py"),
         "--from", "2026-05-15", "--to", "2026-05-15",
         "--base-dir", str(base), "--output", "json"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )
    if cp.returncode != 0:
        _record("V4_trace_summary_latency_aggregation", False,
                f"rc={cp.returncode} stderr={cp.stderr[:200]}")
        return
    out = json.loads(cp.stdout)
    lat = out.get("trace", {}).get("latency_by_stage", {}).get("arbit_winner", {})
    ok = True
    detail = []
    # nearest-rank: ceil(p/100 * 20) p50→10, p95→19, p99→20
    expected = {"p50": 10.0, "p95": 19.0, "p99": 20.0, "count": 20}
    for k, exp in expected.items():
        got = lat.get(k)
        if got is None:
            ok = False
            detail.append(f"{k} missing")
            continue
        if k == "count":
            if got != exp:
                ok = False
                detail.append(f"count={got} expected={exp}")
        else:
            tol = max(0.05 * exp, 0.5)
            if abs(float(got) - exp) > tol:
                ok = False
                detail.append(f"{k}={got} expected≈{exp}")
    _record("V4_trace_summary_latency_aggregation", ok,
            "; ".join(detail) or f"count={lat.get('count')} p50={lat.get('p50')} "
            f"p95={lat.get('p95')} p99={lat.get('p99')}")


# ---------------------------------------------------------------------------
# V5: llm_usage_summary failure_rate 计算 (三口 fail 各 1 + 7 ok)
# ---------------------------------------------------------------------------


def v5_llm_usage_failure_rate(tmp: Path) -> None:
    """V5: 含 ok=False / error / failure_reason 各 1 条 + 7 条 ok → failure_rate = 3/10 = 0.3。"""
    base = tmp / "v5_base"
    base.mkdir()
    day = "20260515"
    ts_base = _dt.datetime.strptime(day, "%Y%m%d").timestamp() + 3600
    lines = []
    model = "gpt-x"
    # 3 条 fail (三口各 1)
    lines.append(json.dumps({
        "event": "usage", "component": "mm_proactive",
        "model": model, "prompt_tokens": 10, "completion_tokens": 5,
        "ts": ts_base, "ok": False,
    }, sort_keys=True))
    lines.append(json.dumps({
        "event": "usage", "component": "mm_proactive",
        "model": model, "prompt_tokens": 10, "completion_tokens": 5,
        "ts": ts_base + 1, "error": "TimeoutError",
    }, sort_keys=True))
    lines.append(json.dumps({
        "event": "usage", "component": "mm_proactive",
        "model": model, "prompt_tokens": 10, "completion_tokens": 5,
        "ts": ts_base + 2, "failure_reason": "rate_limited",
    }, sort_keys=True))
    # 7 条 ok
    for i in range(7):
        lines.append(json.dumps({
            "event": "usage", "component": "mm_proactive",
            "model": model, "prompt_tokens": 10 + i, "completion_tokens": 5 + i,
            "ts": ts_base + 10 + i,
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
        _record("V5_llm_usage_failure_rate", False,
                f"rc={cp.returncode} stderr={cp.stderr[:200]}")
        return
    out = json.loads(cp.stdout)
    ok = True
    detail = []
    if out.get("total_calls") != 10:
        ok = False
        detail.append(f"total_calls={out.get('total_calls')} expected=10")
    if out.get("total_fail") != 3:
        ok = False
        detail.append(f"total_fail={out.get('total_fail')} expected=3")
    rate = out.get("overall_failure_rate")
    if rate is None or abs(float(rate) - 0.3) > 1e-6:
        ok = False
        detail.append(f"overall_failure_rate={rate} expected=0.3")
    by_model = out.get("by_model", {}).get(model, {})
    if by_model.get("fail") != 3 or abs(float(by_model.get("failure_rate", 0)) - 0.3) > 1e-6:
        ok = False
        detail.append(f"by_model[{model}]={by_model}")
    _record("V5_llm_usage_failure_rate", ok,
            "; ".join(detail) or f"total=10 fail=3 rate=0.3 by_model={by_model}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="verify_interact_018_") as td:
        tmp = Path(td)
        v1_latency_wire_in_production()
        v2_is_fail_three_signals()
        v3_is_fail_not_false_positive()
        v4_trace_summary_latency_aggregation(tmp)
        v5_llm_usage_failure_rate(tmp)

    out_dir = ROOT / "evidence" / "interact-018"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
    except Exception:
        sha = ""
    summary = {
        "feature": "interact-018",
        "results": _results,
        "ok": all(r["ok"] for r in _results),
        "files_changed": [
            "coco/proactive.py",
            "coco/proactive_trace.py",
            "scripts/proactive_trace_summary.py",
            "scripts/llm_usage_summary.py",
            "scripts/verify_interact_018.py",
        ],
        "sha": sha,
    }
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print("\n[verify_interact_018] overall:",
          "PASS" if summary["ok"] else "FAIL")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
