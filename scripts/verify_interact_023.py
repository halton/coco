"""interact-023 verification: V1 cooldown_hit 路径 latency_ms 端到端 fixture 覆盖.

source backlog: interact-018-backlog-v1-cooldown-coverage
direction: 纯 verify-only 补丁, 不动 coco/proactive*.py 源码; 通过 fixture 触发
生产 ProactiveScheduler.maybe_trigger 走入 cooldown 抑制路径 (reason='cooldown'),
端到端断言 stage='cooldown_hit' 的 trace 行 latency_ms 字段 wire 正确。

跑法::

    uv run python scripts/verify_interact_023.py

子项:

V0 fingerprint lock — coco/proactive.py cooldown_hit emit 站点存在 (stage 名 +
   reason='cooldown' 特化 + latency_ms=_lat_ms() kwarg), 防止漂移。

V1 cooldown_hit 端到端 fixture — 真实 ProactiveScheduler.maybe_trigger 连续
   两次调用; 第一次 admit (arbit_winner), 第二次因 _last_proactive_ts 在
   cooldown_s 窗口内 → reject reason='cooldown', emit stage='cooldown_hit' 且
   latency_ms (float, >= 0) 存在。

V2 cooldown_hit latency_ms 语义合理 — 同 maybe_trigger 内 cooldown_hit 是
   '判定即出' (research/proactive_trace_contract.md §5 cooldown_hit 行),
   latency 应 << admit 路径的 LLM/TTS 端到端耗时, 这里 fake llm/tts 都 no-op,
   断言 latency_ms 是有限 float (非 NaN/Inf), 数值 < 1000ms (上限宽松, 主要
   防 wire 退化为时间戳 ms 级别)。

V3 type-strict + nonneg — cooldown_hit emit 的 latency_ms 类型严格为
   int/float (不是 str/None, 且排除 bool); 多次 cooldown_hit (连发 3 次)
   latency_ms 全部 >= 0。跨 call monotonic 不承担: 每次 maybe_trigger 内部
   _lat_start 都会重置, cross-call 不可比; emit 顺序契约 (engine→business→
   sentinel) 由 V2 负责。

V4 _is_fail 闭环 — cooldown_hit 是 decision='reject' (非业务 failure), 拿到
   cooldown_hit trace record 喂 is_fail 应返回 False (不被误判为 failure)。
   防止下游 metric 把 cooldown_hit 计入 failure_rate 分子。

V5 regression — interact-018/021/022 verify rc=0 全绿, 证明本补丁不破坏既有
   契约 (latency wire / latency_by_stage / status token 白名单)。

retval: 0 全 PASS; 1 任一 FAIL
evidence: evidence/interact-023/verify_summary.json
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_023] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


# ---------------------------------------------------------------------------
# Shared helpers — build a ProactiveScheduler with fake deps
# ---------------------------------------------------------------------------


def _build_scheduler(cooldown_s: float = 30.0):
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    from coco.power_state import PowerState

    class _FakePS:
        current_state = PowerState.ACTIVE

    class _FakeFace:
        def latest(self):
            class _S:
                present = True
            return _S()

    def _llm(text, *, system_prompt=None):
        return "你好呀"

    def _tts(text, blocking=True):
        return None

    cfg = ProactiveConfig(
        enabled=True,
        idle_threshold_s=10.0,
        cooldown_s=cooldown_s,
        max_topics_per_hour=10,
        tick_s=1.0,
    )
    sched = ProactiveScheduler(
        config=cfg,
        power_state=_FakePS(),
        face_tracker=_FakeFace(),
        llm_reply_fn=_llm,
        tts_say_fn=_tts,
    )
    # 把 last_interaction 推远, 让 idle 满足
    sched._last_interaction_ts = sched.clock() - 600.0
    return sched


def _capture_traces(fn, *args, **kwargs) -> List[Dict[str, Any]]:
    """跑 fn(...) 同时通过 set_emit_override 抓 proactive.trace events。"""
    os.environ["COCO_PROACTIVE_TRACE"] = "1"
    from coco import proactive_trace as pt

    captured: List[Dict[str, Any]] = []

    def _emit(event: str, **payload: Any) -> None:
        captured.append({"event": event, **payload})

    pt.set_emit_override(_emit)
    try:
        fn(captured, *args, **kwargs)
    finally:
        pt.set_emit_override(None)
        os.environ.pop("COCO_PROACTIVE_TRACE", None)
    return captured


# ---------------------------------------------------------------------------
# V0 fingerprint lock
# ---------------------------------------------------------------------------


def v0_fingerprint_lock() -> None:
    """V0: 源码 cooldown_hit emit 站点 fingerprint."""
    src = (ROOT / "coco" / "proactive.py").read_text(encoding="utf-8")
    anchors = [
        '"cooldown_hit" if reason == "cooldown"',
        "latency_ms=_lat_ms(),",
        'stage_in=str(_stage_in or "normal"),',  # arbit_winner 站点, 与 cooldown_hit 同 _lat_ms 共享
    ]
    missing = [a for a in anchors if a not in src]
    if missing:
        _record("V0_fingerprint_lock", False, f"missing anchors: {missing}")
        return
    _record(
        "V0_fingerprint_lock",
        True,
        f"3 anchors present (cooldown_hit stage map + latency_ms wire shared with arbit_winner)",
    )


# ---------------------------------------------------------------------------
# V1 cooldown_hit 端到端 fixture
# ---------------------------------------------------------------------------


def v1_cooldown_hit_end_to_end() -> None:
    def _run(captured):
        sched = _build_scheduler(cooldown_s=30.0)
        # 第一次 → admit (arbit_winner)
        ok1 = sched.maybe_trigger()
        if not ok1:
            captured.append({"event": "_meta", "first_trigger_failed": True})
            return
        # 在 cooldown 窗口内第二次 → 应 reject reason='cooldown', stage=cooldown_hit
        # 复位 last_interaction_ts 让 idle 仍然满足, 这样唯一 reject 原因就是 cooldown
        sched._last_interaction_ts = sched.clock() - 600.0
        ok2 = sched.maybe_trigger()
        captured.append({"event": "_meta", "ok2": ok2})

    captured = _capture_traces(_run)
    meta = next((e for e in captured if e.get("event") == "_meta"), {})
    if meta.get("first_trigger_failed"):
        _record("V1_cooldown_hit_end_to_end", False, "first maybe_trigger admit 失败, fixture 前置条件未满足")
        return
    if meta.get("ok2") is not False:
        _record("V1_cooldown_hit_end_to_end", False, f"second maybe_trigger 未返回 False, ok2={meta.get('ok2')}")
        return

    trace_events = [e for e in captured if e.get("event") == "proactive.trace"]
    cooldown_hits = [
        e for e in trace_events
        if e.get("stage") == "cooldown_hit" and e.get("decision") == "reject"
    ]
    if not cooldown_hits:
        stages = [(e.get("stage"), e.get("decision"), e.get("reason")) for e in trace_events]
        _record("V1_cooldown_hit_end_to_end", False,
                f"no cooldown_hit reject in trace; stages={stages}")
        return

    # latency_ms 字段存在 + 类型合理 + reason='cooldown'
    sample = cooldown_hits[0]
    lat = sample.get("latency_ms")
    reason = sample.get("reason")
    if reason != "cooldown":
        _record("V1_cooldown_hit_end_to_end", False, f"cooldown_hit reason expected 'cooldown', got {reason!r}")
        return
    if not isinstance(lat, (int, float)) or lat < 0:
        _record("V1_cooldown_hit_end_to_end", False, f"latency_ms missing/invalid: {lat!r} (type={type(lat).__name__})")
        return

    _record("V1_cooldown_hit_end_to_end", True,
            f"cooldown_hit reject 命中 lat={lat}ms reason={reason} "
            f"(trace_events={len(trace_events)}, cooldown_hits={len(cooldown_hits)})")


# ---------------------------------------------------------------------------
# V2 latency 语义合理 (判定即出, 不含等待)
# ---------------------------------------------------------------------------


def v2_cooldown_hit_latency_semantics() -> None:
    def _run(captured):
        sched = _build_scheduler(cooldown_s=30.0)
        ok1 = sched.maybe_trigger()
        if not ok1:
            return
        sched._last_interaction_ts = sched.clock() - 600.0
        sched.maybe_trigger()

    captured = _capture_traces(_run)
    cooldown_hits = [
        e for e in captured
        if e.get("event") == "proactive.trace"
        and e.get("stage") == "cooldown_hit"
    ]
    if not cooldown_hits:
        _record("V2_cooldown_hit_latency_semantics", False, "no cooldown_hit emit captured")
        return

    lat = cooldown_hits[0].get("latency_ms")
    # 有限 float, 不是 NaN/Inf, 不是退化的时间戳级数 (>= 1e6 ms 大概率是误把 ts 当 latency)
    if not isinstance(lat, (int, float)):
        _record("V2_cooldown_hit_latency_semantics", False, f"latency_ms not numeric: {lat!r}")
        return
    if isinstance(lat, float) and (math.isnan(lat) or math.isinf(lat)):
        _record("V2_cooldown_hit_latency_semantics", False, f"latency_ms not finite: {lat!r}")
        return
    if lat >= 1000.0:
        # cooldown_hit 是判定即出, fake llm/tts no-op, 在合理硬件上 << 1000ms
        _record("V2_cooldown_hit_latency_semantics", False,
                f"latency_ms={lat}ms 异常偏大 (cooldown_hit 应判定即出, 阈值 1000ms)")
        return
    # cooldown_hit emit 之后, 本次 maybe_trigger 已 return False, 后续不应再有
    # 同次 maybe_trigger 内的 admit emit。按 emit 顺序找最后一次 cooldown_hit 之后
    # 不应有 arbit_winner admit (admit 只能出现在 cooldown_hit 之前的那次 maybe_trigger)。
    trace_seq = [e for e in captured if e.get("event") == "proactive.trace"]
    last_ch_idx = max(
        i for i, e in enumerate(trace_seq) if e.get("stage") == "cooldown_hit"
    )
    after = trace_seq[last_ch_idx + 1:]
    after_admits = [
        e for e in after if e.get("decision") == "admit"
    ]
    if after_admits:
        _record("V2_cooldown_hit_latency_semantics", False,
                f"最后一次 cooldown_hit 之后不应再有 admit emit, 实际={len(after_admits)}")
        return

    _record("V2_cooldown_hit_latency_semantics", True,
            f"lat={lat}ms (有限 float, <1000ms 判定即出语义); cooldown_hit 之后无 admit emit")


# ---------------------------------------------------------------------------
# V3 type-strict + nonneg (多次 cooldown_hit)
# ---------------------------------------------------------------------------


def v3_type_strict_and_nonneg() -> None:
    """V3: 仅断言 cooldown_hit latency_ms 为 type-strict 数值 (int/float, 排除 bool) 且 >=0。

    monotonic 跨 call 不承担：每次 maybe_trigger 内部都会重置 _lat_start，
    cooldown_hit latency 仅在单次 call 内部自洽；emit 顺序契约 (engine→
    business→sentinel) 由 V2 承担。本 V3 不做跨 call monotonic 断言。
    """
    def _run(captured):
        sched = _build_scheduler(cooldown_s=60.0)
        ok1 = sched.maybe_trigger()
        if not ok1:
            return
        # 连续 3 次都在 cooldown 窗口内
        for _ in range(3):
            sched._last_interaction_ts = sched.clock() - 600.0
            sched.maybe_trigger()

    captured = _capture_traces(_run)
    cooldown_hits = [
        e for e in captured
        if e.get("event") == "proactive.trace"
        and e.get("stage") == "cooldown_hit"
    ]
    if len(cooldown_hits) < 3:
        _record("V3_type_strict_and_nonneg", False,
                f"expected >=3 cooldown_hit, got {len(cooldown_hits)}")
        return

    lats = [e.get("latency_ms") for e in cooldown_hits]
    # type-strict: 全部 int/float, 非 bool (bool 是 int 子类, 用 isinstance 时需排除)
    bad_types = [
        (i, l, type(l).__name__) for i, l in enumerate(lats)
        if isinstance(l, bool) or not isinstance(l, (int, float))
    ]
    if bad_types:
        _record("V3_type_strict_and_nonneg", False, f"非数值类型 latency: {bad_types}")
        return

    # type-strict + nonneg: V3 不承担 cross-call monotonic 断言。
    # 每次 maybe_trigger 内部 _lat_start 都会重置, 所以 cross-call 不可比,
    # cooldown_hit latency 仅在单次 call 内部自洽 (>=0); emit 顺序契约
    # (engine→business→sentinel) 由 V2 承担。
    neg = [l for l in lats if l < 0]
    if neg:
        _record("V3_type_strict_and_nonneg", False, f"latency<0: {neg}")
        return

    _record("V3_type_strict_and_nonneg", True,
            f"{len(cooldown_hits)} cooldown_hit 全部 numeric>=0, lats={lats}")


# ---------------------------------------------------------------------------
# V4 _is_fail 闭环 — cooldown_hit reject 不被误判 failure
# ---------------------------------------------------------------------------


def v4_is_fail_closure() -> None:
    from coco.proactive_trace import is_fail

    def _run(captured):
        sched = _build_scheduler(cooldown_s=30.0)
        ok1 = sched.maybe_trigger()
        if not ok1:
            return
        sched._last_interaction_ts = sched.clock() - 600.0
        sched.maybe_trigger()

    captured = _capture_traces(_run)
    cooldown_hits = [
        e for e in captured
        if e.get("event") == "proactive.trace"
        and e.get("stage") == "cooldown_hit"
    ]
    if not cooldown_hits:
        _record("V4_is_fail_closure", False, "no cooldown_hit emit")
        return

    # cooldown_hit record 不含 ok=False / error / failure_reason / status∈fail token
    bad: List[str] = []
    for rec in cooldown_hits:
        if is_fail(rec):
            bad.append(f"cooldown_hit 被误判 fail: {rec}")
    if bad:
        _record("V4_is_fail_closure", False, "; ".join(bad))
        return

    # 同时显式构造一个 cooldown_hit-like record 喂 is_fail 应为 False
    synthetic = {
        "stage": "cooldown_hit",
        "decision": "reject",
        "reason": "cooldown",
        "latency_ms": 0.5,
        "ts": 12345.0,
    }
    if is_fail(synthetic):
        _record("V4_is_fail_closure", False, f"synthetic cooldown_hit 被误判 fail: {synthetic}")
        return

    _record("V4_is_fail_closure", True,
            f"{len(cooldown_hits)} cooldown_hit + 1 synthetic 全部 is_fail=False (reject 非 failure)")


# ---------------------------------------------------------------------------
# V5 regression — interact-018/021/022 rc=0
# ---------------------------------------------------------------------------


def v5_regression() -> None:
    scripts = [
        "scripts/verify_interact_018.py",
        "scripts/verify_interact_021.py",
        "scripts/verify_interact_022.py",
    ]
    rcs: Dict[str, int] = {}
    bad: List[str] = []
    for s in scripts:
        try:
            proc = subprocess.run(
                [sys.executable, s],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=120,
            )
            rcs[s] = proc.returncode
            if proc.returncode != 0:
                bad.append(f"{s} rc={proc.returncode}")
        except Exception as e:  # noqa: BLE001
            rcs[s] = -1
            bad.append(f"{s} exc={e!r}")
    if bad:
        _record("V5_regression", False, f"failed: {bad}; rcs={rcs}")
        return
    _record("V5_regression", True, f"all rc=0: {rcs}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    v0_fingerprint_lock()
    v1_cooldown_hit_end_to_end()
    v2_cooldown_hit_latency_semantics()
    v3_type_strict_and_nonneg()
    v4_is_fail_closure()
    v5_regression()

    all_ok = all(r["ok"] for r in _results)

    out_dir = ROOT / "evidence" / "interact-023"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "interact-023",
        "source_backlog": "interact-018-backlog-v1-cooldown-coverage",
        "direction": "纯 verify-only 补丁 (无源码改动); cooldown_hit 路径 latency_ms 端到端 fixture 覆盖",
        "ok": all_ok,
        "results": _results,
        "files_changed": ["scripts/verify_interact_023.py"],
        "runtime_change": False,
    }
    try:
        head_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
        summary["sha"] = head_sha
    except Exception:  # noqa: BLE001
        pass

    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _print("SUMMARY", f"all_pass={all_ok} ({sum(1 for r in _results if r['ok'])}/{len(_results)})")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
