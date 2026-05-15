"""interact-016 verification: interact-015 backlog 升级 — stage 标签反转修 +
jsonl 跨日/并发防御 + summary CLI 健壮性。

跑法::

    uv run python scripts/verify_interact_016.py

子项：

V1 _next_priority_boost True/False stage 标签语义固定（修后 boost True 抑制
   路径必标 fusion_boost；mm-only 抑制路径必标 mm_proactive）
V2 llm_usage jsonl 跨日 rollover：mock clock 跨日界两条 ts，两条目落到不同
   日期文件
V3 并发两 process append：100 条交错 append 无撕裂、无丢行
V4 emit_trace reserved kwarg：业务侧用 extra={"stage": "x"} 覆写时被忽略并
   WARN once
V5 summary CLI 缺文件：--trace-jsonl /nonexistent → stderr 含 WARN + rc != 0
V6 interact-015 V1..V7 回归 PASS

retval：0 全 PASS；1 任一失败
evidence 落 evidence/interact-016/verify_summary.json
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_016] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}")


class FakeClock:
    def __init__(self, t0: float = 0.0) -> None:
        self.t = float(t0)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _FacePresent:
    present = True


class _FaceTracker:
    def latest(self) -> Any:
        return _FacePresent()


def _make_proactive(clock: FakeClock, *, cooldown_s: float = 100.0,
                    idle_threshold_s: float = 30.0):
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    cfg = ProactiveConfig(
        enabled=True,
        idle_threshold_s=idle_threshold_s,
        cooldown_s=cooldown_s,
        max_topics_per_hour=60,
    )
    emits: List[Any] = []

    def emit_fn(event, **kw):
        emits.append((event, kw))

    sched = ProactiveScheduler(
        config=cfg,
        clock=clock,
        llm_reply_fn=lambda seed, **kw: f"reply<{seed[:10]}>",
        tts_say_fn=lambda text, blocking=False: None,
        face_tracker=_FaceTracker(),
        emit_fn=emit_fn,
    )
    sched._last_interaction_ts = clock.t - (idle_threshold_s + 100.0)  # noqa: SLF001
    return sched, emits


def _unset_all_env() -> None:
    for k in ("COCO_PROACTIVE_TRACE", "COCO_LLM_USAGE_LOG",
              "COCO_PROACTIVE_ARBIT", "COCO_MM_PROACTIVE_LLM"):
        os.environ.pop(k, None)


def _capture_trace_emits(trace_emits_list):
    from coco.proactive_trace import set_emit_override

    def _hook(event, **kw):
        trace_emits_list.append((event, kw))

    set_emit_override(_hook)


def _release_trace_override():
    from coco.proactive_trace import set_emit_override
    set_emit_override(None)


# ---------------------------------------------------------------------------
# V1 stage 标签语义固定
# ---------------------------------------------------------------------------


def v1_stage_label_semantics():
    """boost True + emotion_alert 抢占 → fusion_boost；mm-only + emotion_alert 抢占 → mm_proactive。"""
    # case A: boost True 抑制
    _unset_all_env()
    os.environ["COCO_PROACTIVE_TRACE"] = "1"
    os.environ["COCO_PROACTIVE_ARBIT"] = "1"
    trace_emits: List[Any] = []
    _capture_trace_emits(trace_emits)
    try:
        clk = FakeClock(70000.0)
        sched, _ = _make_proactive(clk)
        sched.record_emotion_alert_trigger("sad", ratio=0.8, window_size=10)
        sched._next_priority_boost = True  # noqa: SLF001
        sched._next_priority_boost_level = "dark_silence"  # noqa: SLF001
        trace_emits.clear()
        sched.maybe_trigger()
        a_preempt = [kw for ev, kw in trace_emits if ev == "proactive.trace"
                     and kw.get("decision") == "reject"
                     and kw.get("reason") == "arbit_emotion_preempt"]
        ok_a = len(a_preempt) >= 1 and a_preempt[0].get("stage") == "fusion_boost"
    finally:
        _release_trace_override()
        _unset_all_env()

    # case B: mm-only 抑制
    os.environ["COCO_PROACTIVE_TRACE"] = "1"
    os.environ["COCO_PROACTIVE_ARBIT"] = "1"
    trace_emits = []
    _capture_trace_emits(trace_emits)
    try:
        clk = FakeClock(71000.0)
        sched, _ = _make_proactive(clk)
        sched.record_emotion_alert_trigger("sad", ratio=0.8, window_size=10)
        # 只设 mm_llm_context，不设 boost
        sched._mm_llm_context = {"hint": "看到一只小猫", "rule_id": "curious_idle",  # noqa: SLF001
                                  "caption": "客厅里有只猫", "emotion_label": "happy",
                                  "face_ids": ["u1"]}
        trace_emits.clear()
        sched.maybe_trigger()
        b_preempt = [kw for ev, kw in trace_emits if ev == "proactive.trace"
                     and kw.get("decision") == "reject"
                     and kw.get("reason") == "arbit_emotion_preempt"]
        ok_b = len(b_preempt) >= 1 and b_preempt[0].get("stage") == "mm_proactive"
    finally:
        _release_trace_override()
        _unset_all_env()

    _record(
        "V1 stage label semantics fixed (boost->fusion_boost / mm-only->mm_proactive)",
        ok_a and ok_b,
        f"boost_case_stage={a_preempt[0].get('stage') if a_preempt else None} "
        f"mm_case_stage={b_preempt[0].get('stage') if b_preempt else None}",
    )


# ---------------------------------------------------------------------------
# V2 jsonl 跨日 rollover
# ---------------------------------------------------------------------------


def v2_jsonl_day_rollover():
    _unset_all_env()
    os.environ["COCO_LLM_USAGE_LOG"] = "1"
    tmpdir = tempfile.mkdtemp(prefix="coco_v016_roll_")
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = tmpdir
    try:
        from coco.proactive_trace import record_llm_usage, _llm_usage_log_path

        # 跨日界两个 ts（local time）：
        #   2026-05-14 23:59:30 → 文件 llm_usage_20260514.jsonl
        #   2026-05-15 00:00:30 → 文件 llm_usage_20260515.jsonl
        # 用 datetime.timestamp() 解决 UTC vs local 差异（按 local naive 构造）
        t1 = _dt.datetime(2026, 5, 14, 23, 59, 30).timestamp()
        t2 = _dt.datetime(2026, 5, 15, 0, 0, 30).timestamp()
        record_llm_usage("mm_proactive", prompt_tokens=10, completion_tokens=5, ts=t1)
        record_llm_usage("mm_proactive", prompt_tokens=20, completion_tokens=7, ts=t2)
        p1 = _llm_usage_log_path(t1)
        p2 = _llm_usage_log_path(t2)
        ok_paths_differ = p1 != p2
        ok_both_exist = p1.exists() and p2.exists()
        # p1 应只含 t1 一行；p2 应只含 t2 一行
        ln1 = [l for l in p1.read_text().splitlines() if l.strip()] if p1.exists() else []
        ln2 = [l for l in p2.read_text().splitlines() if l.strip()] if p2.exists() else []
        ok_counts = len(ln1) == 1 and len(ln2) == 1
        _record(
            "V2 llm_usage jsonl day rollover (two ts cross day -> two files)",
            ok_paths_differ and ok_both_exist and ok_counts,
            f"p1={p1.name} p2={p2.name} differ={ok_paths_differ} "
            f"exists=({p1.exists()},{p2.exists()}) counts=({len(ln1)},{len(ln2)})",
        )
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        else:
            os.environ.pop("HOME", None)
        _unset_all_env()


# ---------------------------------------------------------------------------
# V3 并发两 process append
# ---------------------------------------------------------------------------


def v3_concurrent_append():
    """两 subprocess 各 append 50 行，断言总 100 行 + JSON 都可解析。"""
    _unset_all_env()
    tmpdir = tempfile.mkdtemp(prefix="coco_v016_concur_")

    # 子进程脚本：用 _append_with_filelock 写 50 行
    worker_src = (
        "import os, sys, json\n"
        f"sys.path.insert(0, {repr(str(ROOT))})\n"
        f"os.environ['HOME'] = {repr(tmpdir)}\n"
        "os.environ['COCO_LLM_USAGE_LOG'] = '1'\n"
        "from coco.proactive_trace import record_llm_usage\n"
        "import datetime\n"
        "tag = sys.argv[1]\n"
        "ts = datetime.datetime(2026,5,14,12,0,0).timestamp()\n"
        "for i in range(50):\n"
        "    record_llm_usage('mm_proactive', prompt_tokens=i, completion_tokens=i,\n"
        "                     ts=ts, worker=tag, idx=i)\n"
    )
    worker_path = Path(tmpdir) / "worker.py"
    worker_path.write_text(worker_src)
    cmd_a = [sys.executable, str(worker_path), "A"]
    cmd_b = [sys.executable, str(worker_path), "B"]
    pa = subprocess.Popen(cmd_a)
    pb = subprocess.Popen(cmd_b)
    pa.wait()
    pb.wait()
    rc_a = pa.returncode
    rc_b = pb.returncode

    # 路径
    coco_dir = Path(tmpdir) / ".coco"
    files = list(coco_dir.glob("llm_usage_*.jsonl")) if coco_dir.exists() else []
    total_lines = 0
    bad_json = 0
    by_worker = {"A": 0, "B": 0}
    for f in files:
        for ln in f.read_text().splitlines():
            ln = ln.strip()
            if not ln:
                continue
            total_lines += 1
            try:
                obj = json.loads(ln)
                w = obj.get("worker")
                if w in by_worker:
                    by_worker[w] += 1
            except json.JSONDecodeError:
                bad_json += 1
    ok = (
        rc_a == 0 and rc_b == 0
        and total_lines == 100
        and bad_json == 0
        and by_worker["A"] == 50 and by_worker["B"] == 50
    )
    _record(
        "V3 concurrent append filelock: 100 lines, no tearing, no loss",
        ok,
        f"rc=({rc_a},{rc_b}) files={len(files)} lines={total_lines} "
        f"bad_json={bad_json} by_worker={by_worker}",
    )


# ---------------------------------------------------------------------------
# V4 emit_trace reserved kwarg WARN once
# ---------------------------------------------------------------------------


def v4_reserved_kwarg_warn_once():
    _unset_all_env()
    os.environ["COCO_PROACTIVE_TRACE"] = "1"

    # 重置 module-level WARN-once flag（直接戳内部 state，方便单测）
    import coco.proactive_trace as pt
    pt._RESERVED_WARN_ONCE["warned"] = False  # noqa: SLF001

    # 抓 log warnings
    handler_records: List[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record):  # noqa: D401
            handler_records.append(record)

    logger = logging.getLogger("coco.proactive_trace")
    h = _Handler(level=logging.WARNING)
    logger.addHandler(h)
    prev_level = logger.level
    logger.setLevel(logging.WARNING)

    emits: List[Any] = []

    def _hook(event, **kw):
        emits.append((event, kw))

    from coco.proactive_trace import set_emit_override, emit_trace
    set_emit_override(_hook)
    try:
        # extra 中带 reserved 名（logging stdlib reserved，如 "message" / "msg"
        # —— 这些是 logger.info(extra=) 的 reserved，从 extra 注入会让 emit
        # 抛 KeyError 把事件吞掉。schema reserved 也在同一集合）。
        # 三次都应 only WARN once。
        emit_trace("normal", "cid-1", "admit",
                   **{"message": "evil-1", "msg": "x"})  # type: ignore[arg-type]
        emit_trace("normal", "cid-2", "admit",
                   **{"levelname": "FAKE"})  # type: ignore[arg-type]
        emit_trace("normal", "cid-3", "admit",
                   **{"created": 0, "module": "evil"})  # type: ignore[arg-type]
        # reserved extra 应被过滤 → payload 不含这些键，事件正常 emit
        ok_payload = all(
            kw.get("stage") == "normal" and kw.get("decision") == "admit"
            and "message" not in kw and "msg" not in kw
            and "levelname" not in kw and "created" not in kw
            for ev, kw in emits if ev == "proactive.trace"
        )
        # 且三次都成功 emit
        ok_count = sum(1 for ev, _ in emits if ev == "proactive.trace") == 3
        warn_msgs = [r for r in handler_records
                     if "reserved kwarg" in r.getMessage()]
        ok_warn_once = len(warn_msgs) == 1
        _record(
            "V4 emit_trace reserved kwarg WARN once + extra ignored",
            ok_payload and ok_warn_once and ok_count,
            f"warn_count={len(warn_msgs)} payload_ok={ok_payload} emit_count_ok={ok_count}",
        )
    finally:
        set_emit_override(None)
        logger.removeHandler(h)
        logger.setLevel(prev_level)
        _unset_all_env()


# ---------------------------------------------------------------------------
# V5 summary CLI 缺文件 → stderr warn + rc != 0
# ---------------------------------------------------------------------------


def v5_summary_cli_missing_file():
    missing = Path("/tmp/coco_v016_does_not_exist_xyz.jsonl")
    if missing.exists():
        missing.unlink()
    rc = subprocess.run(
        ["uv", "run", "python",
         str(ROOT / "scripts" / "proactive_trace_summary.py"),
         "--trace-jsonl", str(missing)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    ok_rc = rc.returncode != 0
    ok_stderr = "WARN" in rc.stderr and "not found" in rc.stderr
    _record(
        "V5 summary CLI missing file: stderr WARN + rc != 0",
        ok_rc and ok_stderr,
        f"rc={rc.returncode} stderr_head={rc.stderr[:160]!r}",
    )


# ---------------------------------------------------------------------------
# V6 interact-015 regression
# ---------------------------------------------------------------------------


def v6_interact015_regression():
    rc = subprocess.run(
        ["uv", "run", "python", str(ROOT / "scripts" / "verify_interact_015.py")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    ok = rc.returncode == 0
    _record(
        "V6 interact-015 V1..V7 regression PASS",
        ok,
        f"rc={rc.returncode} stdout_tail={rc.stdout[-200:]!r}",
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    _unset_all_env()
    v1_stage_label_semantics()
    v2_jsonl_day_rollover()
    v3_concurrent_append()
    v4_reserved_kwarg_warn_once()
    v5_summary_cli_missing_file()
    v6_interact015_regression()

    overall_ok = all(r["ok"] for r in _results)
    summary = {
        "feature": "interact-016",
        "overall_ok": overall_ok,
        "ts": time.time(),
        "results": _results,
    }
    out_dir = ROOT / "evidence" / "interact-016"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    _print("DONE", f"overall_ok={overall_ok}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
