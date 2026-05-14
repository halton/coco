"""interact-014 verification: ProactiveScheduler 真消费 vision-007 priority_boost.

跑法::

    uv run python scripts/verify_interact_014.py

子项：

V1   default-OFF：COCO_PROACTIVE_ARBIT 未设 → 仲裁层关闭，行为与 vision-007 现状
     bytewise 等价（cooldown 缩放 0.5 全规则、emit 不带 boost_level、emotion_alert
     不抢占同帧 fusion/mm）
V2   arbit ON + emotion_alert 抢占：record_emotion_alert_trigger 后窗口内的
     fusion priority_boost 被抑制（标志位被清，stats.arbit_skipped_for_emotion +1）
V3   arbit ON + fusion_boost 按 level 缩放 cooldown：dark_silence=0.3 /
     motion_greet=0.5 / curious_idle=0.7；stats.priority_boost_level_consumed
     按 rule_id 分桶
V4   arbit ON + mm_proactive：fusion_boost 触发后仍按现有路径走 mm_ctx；
     在没有 fusion_boost 也没有 emotion_alert 时退化普通路径
V5   boost 不绕过全局 cooldown：boost 缩放后若 since < cooldown 仍 skip，
     stats.arbit_cooldown_with_boost 递增
V6   trigger emit schema：arbit ON 且成功 trigger 时 emit interact.proactive_topic
     附 boost_level 字段；OFF 时不附
V7   AST/grep marker：proactive.py / multimodal_fusion.py 含 interact-014
     标识 + proactive_arbitration_enabled_from_env + _next_priority_boost_level
V8   并发：同帧先记 emotion_alert 再写 boost（arbit ON）→ maybe_trigger 内
     emotion_alert 抢占；boost 标志被清 + stats.arbit_skipped_for_emotion +1

retval：0 全 PASS；1 任一失败
evidence 落 evidence/interact-014/verify_summary.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_014] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": ok, "detail": detail})
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
    # 让 idle 立刻满足
    sched._last_interaction_ts = clock.t - (idle_threshold_s + 100.0)  # noqa: SLF001
    return sched, emits


def _unset_arbit_env() -> None:
    os.environ.pop("COCO_PROACTIVE_ARBIT", None)


def _set_arbit_env() -> None:
    os.environ["COCO_PROACTIVE_ARBIT"] = "1"


# ---------------------------------------------------------------------------
# V1 default-OFF 等价
# ---------------------------------------------------------------------------


def v1_default_off_equivalence() -> None:
    _unset_arbit_env()
    clk = FakeClock(10000.0)
    sched, emits = _make_proactive(clk, cooldown_s=100.0)
    # 写 boost flag（不写 level）—— 模拟 vision-007 现状写法
    sched._next_priority_boost = True  # noqa: SLF001
    fired = sched.maybe_trigger()
    ok_fired = fired is True
    # OFF 时 emit 不应带 boost_level
    proactive_emits = [kw for ev, kw in emits if ev == "interact.proactive_topic"]
    ok_no_level_field = bool(proactive_emits) and ("boost_level" not in proactive_emits[0])
    # 立即第二次 trigger：cooldown 缩放 0.5（idle 已满足；since=0 < cooldown*0.5=50.0 → skip）
    clk.advance(10.0)
    # 让 idle 仍然满足
    sched._last_interaction_ts = clk.t - 1000.0  # noqa: SLF001
    sched._next_priority_boost = True  # noqa: SLF001
    fired2 = sched.maybe_trigger()
    ok_skip_in_cooldown = fired2 is False
    # OFF 模式下 arbit_* 计数器应保持 0
    ok_arbit_silent = (
        sched.stats.arbit_skipped_for_emotion == 0
        and sched.stats.arbit_cooldown_with_boost == 0
        and dict(sched.stats.priority_boost_level_consumed) == {}
    )
    _record(
        "V1 default-OFF: equivalent to vision-007 (no boost_level emit, no arbit stats, cooldown*0.5)",
        ok_fired and ok_no_level_field and ok_skip_in_cooldown and ok_arbit_silent,
        f"fired1={fired} emit_keys={list(proactive_emits[0].keys()) if proactive_emits else []} "
        f"fired2={fired2} arbit_emotion={sched.stats.arbit_skipped_for_emotion} "
        f"arbit_cd={sched.stats.arbit_cooldown_with_boost} "
        f"lvl_consumed={dict(sched.stats.priority_boost_level_consumed)}",
    )


# ---------------------------------------------------------------------------
# V2 arbit ON + emotion_alert 抢占
# ---------------------------------------------------------------------------


def v2_emotion_alert_preempts_fusion() -> None:
    _set_arbit_env()
    try:
        clk = FakeClock(20000.0)
        sched, emits = _make_proactive(clk, cooldown_s=100.0)
        # 先记 emotion_alert（独立路径，已发告警）
        sched.record_emotion_alert_trigger("sad", ratio=0.8, window_size=10)
        # 紧跟同帧 fusion 写 boost
        sched._next_priority_boost = True  # noqa: SLF001
        sched._next_priority_boost_level = "dark_silence"  # noqa: SLF001
        fired = sched.maybe_trigger()  # arbit 应抑制 fusion，本帧不再因 boost 触发
        # 因为 emotion_alert 已 set _last_proactive_ts? 实际不会——record_emotion_alert
        # 不动 cooldown。但 idle 已满足，cooldown 也未生效（since=0 第一次）→ 普通路径仍 trigger
        # 但 boost flag 应被清，arbit_skipped_for_emotion +1
        ok_clean = (
            sched._next_priority_boost is False  # noqa: SLF001
            and sched._next_priority_boost_level is None  # noqa: SLF001
            and sched.stats.arbit_skipped_for_emotion == 1
        )
        # emit 不应带 boost_level（boost 已被清）
        proactive_emits = [kw for ev, kw in emits if ev == "interact.proactive_topic"]
        ok_no_boost_level_emit = (not proactive_emits) or ("boost_level" not in proactive_emits[0])
        _record(
            "V2 arbit ON: emotion_alert preempts fusion same-frame (boost cleared)",
            ok_clean and ok_no_boost_level_emit,
            f"fired={fired} boost_after={sched._next_priority_boost!r} "  # noqa: SLF001
            f"level_after={sched._next_priority_boost_level!r} "  # noqa: SLF001
            f"arbit_emotion={sched.stats.arbit_skipped_for_emotion} "
            f"emit_keys={list(proactive_emits[0].keys()) if proactive_emits else []}",
        )
    finally:
        _unset_arbit_env()


# ---------------------------------------------------------------------------
# V3 fusion_boost 按 level 缩放 cooldown
# ---------------------------------------------------------------------------


def v3_boost_level_cooldown_scaling() -> None:
    _set_arbit_env()
    try:
        # dark_silence scale=0.3：cooldown_s=100 → 实际 30 → since=35 应通过
        clk = FakeClock(30000.0)
        sched, _emits = _make_proactive(clk, cooldown_s=100.0)
        # 模拟最近一次 proactive 触发在 35s 前
        sched._last_proactive_ts = clk.t - 35.0  # noqa: SLF001
        sched._last_interaction_ts = clk.t - 1000.0  # noqa: SLF001
        sched._next_priority_boost = True  # noqa: SLF001
        sched._next_priority_boost_level = "dark_silence"  # noqa: SLF001
        fired_dark = sched.maybe_trigger()
        # 重置一遍：since=35s，scale=0.7 → 实际 70s → 应被 cooldown 抑制
        clk.advance(1.0)
        sched._last_proactive_ts = clk.t - 35.0  # noqa: SLF001
        sched._last_interaction_ts = clk.t - 1000.0  # noqa: SLF001
        sched._next_priority_boost = True  # noqa: SLF001
        sched._next_priority_boost_level = "curious_idle"  # noqa: SLF001
        fired_curious = sched.maybe_trigger()
        # motion_greet scale=0.5：since=60 → 实际 50 → 应通过
        clk.advance(1.0)
        sched._last_proactive_ts = clk.t - 60.0  # noqa: SLF001
        sched._last_interaction_ts = clk.t - 1000.0  # noqa: SLF001
        sched._next_priority_boost = True  # noqa: SLF001
        sched._next_priority_boost_level = "motion_greet"  # noqa: SLF001
        fired_motion = sched.maybe_trigger()

        lvl_consumed = dict(sched.stats.priority_boost_level_consumed)
        ok = (
            fired_dark is True
            and fired_curious is False
            and fired_motion is True
            and lvl_consumed.get("dark_silence") == 1
            and lvl_consumed.get("motion_greet") == 1
            # curious_idle 没成功 trigger 因此不递增 level_consumed（与 priority_boost_consumed 一致）
            and lvl_consumed.get("curious_idle") is None
        )
        _record(
            "V3 arbit ON: boost level scales cooldown (dark=0.3 / motion=0.5 / curious=0.7)",
            ok,
            f"fired dark/curious/motion={fired_dark}/{fired_curious}/{fired_motion} "
            f"lvl_consumed={lvl_consumed} "
            f"arbit_cd_with_boost={sched.stats.arbit_cooldown_with_boost}",
        )
    finally:
        _unset_arbit_env()


# ---------------------------------------------------------------------------
# V4 arbit ON 无 boost / 无 emotion → 普通路径
# ---------------------------------------------------------------------------


def v4_no_boost_no_emotion_normal_path() -> None:
    _set_arbit_env()
    try:
        clk = FakeClock(40000.0)
        sched, emits = _make_proactive(clk, cooldown_s=100.0)
        # 无 boost，无 emotion_alert：since=0（无历史），idle 满足 → 普通触发
        fired = sched.maybe_trigger()
        proactive_emits = [kw for ev, kw in emits if ev == "interact.proactive_topic"]
        # 普通路径 emit 不应带 boost_level（arbit ON 但本次未消费 boost）
        ok = (
            fired is True
            and bool(proactive_emits)
            and ("boost_level" not in proactive_emits[0])
            and sched.stats.arbit_skipped_for_emotion == 0
            and dict(sched.stats.priority_boost_level_consumed) == {}
        )
        _record(
            "V4 arbit ON: no boost no emotion -> normal path (no boost_level emit)",
            ok,
            f"fired={fired} emit_keys={list(proactive_emits[0].keys()) if proactive_emits else []} "
            f"arbit_emotion={sched.stats.arbit_skipped_for_emotion}",
        )
    finally:
        _unset_arbit_env()


# ---------------------------------------------------------------------------
# V5 boost 不绕过 cooldown
# ---------------------------------------------------------------------------


def v5_boost_does_not_bypass_cooldown() -> None:
    _set_arbit_env()
    try:
        clk = FakeClock(50000.0)
        sched, _emits = _make_proactive(clk, cooldown_s=100.0)
        # since=5s；dark_silence scale=0.3 → 实际 cooldown=30 → 仍 < cooldown → skip
        sched._last_proactive_ts = clk.t - 5.0  # noqa: SLF001
        sched._next_priority_boost = True  # noqa: SLF001
        sched._next_priority_boost_level = "dark_silence"  # noqa: SLF001
        fired = sched.maybe_trigger()
        ok = (
            fired is False
            and sched.stats.arbit_cooldown_with_boost == 1
            # boost 标志在 _should_trigger 返回 "cooldown" 时不消费（只在 trigger 成功时消费）
            and sched._next_priority_boost is True  # noqa: SLF001
        )
        _record(
            "V5 arbit ON: boost does NOT bypass global cooldown (still skip when since<scaled_cd)",
            ok,
            f"fired={fired} arbit_cd_with_boost={sched.stats.arbit_cooldown_with_boost} "
            f"boost_flag_after={sched._next_priority_boost!r}",  # noqa: SLF001
        )
    finally:
        _unset_arbit_env()


# ---------------------------------------------------------------------------
# V6 emit schema
# ---------------------------------------------------------------------------


def v6_emit_schema_boost_level() -> None:
    _set_arbit_env()
    try:
        clk = FakeClock(60000.0)
        sched, emits = _make_proactive(clk, cooldown_s=100.0)
        sched._next_priority_boost = True  # noqa: SLF001
        sched._next_priority_boost_level = "motion_greet"  # noqa: SLF001
        fired = sched.maybe_trigger()
        topic_emits = [kw for ev, kw in emits if ev == "interact.proactive_topic"]
        ok = (
            fired is True
            and len(topic_emits) == 1
            and topic_emits[0].get("boost_level") == "motion_greet"
            and topic_emits[0].get("source") == "scheduler"
            and "topic" in topic_emits[0]
        )
        _record(
            "V6 arbit ON: emit interact.proactive_topic carries boost_level",
            ok,
            f"fired={fired} emit_payload={topic_emits[0] if topic_emits else None}",
        )
    finally:
        _unset_arbit_env()


# ---------------------------------------------------------------------------
# V7 AST/grep markers
# ---------------------------------------------------------------------------


def v7_markers_present() -> None:
    proactive_src = (ROOT / "coco" / "proactive.py").read_text(encoding="utf-8")
    fusion_src = (ROOT / "coco" / "multimodal_fusion.py").read_text(encoding="utf-8")
    missing: List[str] = []
    needles_proactive = [
        "interact-014",
        "proactive_arbitration_enabled_from_env",
        "_next_priority_boost_level",
        "_ARBIT_BOOST_COOLDOWN_SCALE",
        "ARBIT_EMOTION_WINDOW_S",
        "arbit_skipped_for_emotion",
        "arbit_cooldown_with_boost",
        "priority_boost_level_consumed",
        "COCO_PROACTIVE_ARBIT",
        "_last_emotion_alert_ts",
    ]
    for n in needles_proactive:
        if n not in proactive_src:
            missing.append(f"proactive.py:{n}")
    needles_fusion = [
        "interact-014",
        "_next_priority_boost_level",
    ]
    for n in needles_fusion:
        if n not in fusion_src:
            missing.append(f"multimodal_fusion.py:{n}")
    ok = not missing
    _record(
        "V7 AST/grep markers present in proactive.py / multimodal_fusion.py",
        ok,
        f"missing={missing}",
    )


# ---------------------------------------------------------------------------
# V8 并发：同帧 emotion + boost
# ---------------------------------------------------------------------------


def v8_concurrent_emotion_and_boost() -> None:
    _set_arbit_env()
    try:
        clk = FakeClock(70000.0)
        sched, emits = _make_proactive(clk, cooldown_s=100.0)
        # 同帧：先 emotion_alert（已发告警），再 fusion 写 boost
        sched.record_emotion_alert_trigger("sad", ratio=0.9, window_size=10)
        # multimodal_fusion 写 priority_boost
        sched._next_priority_boost = True  # noqa: SLF001
        sched._next_priority_boost_level = "dark_silence"  # noqa: SLF001
        # mm_ctx 也来一份（验证仲裁层把 mm_ctx 一并清掉）
        sched.set_mm_llm_context({"rule_id": "dark_silence", "hint": "要不要开灯？",
                                  "caption": "外面很暗", "ts": clk.t})
        fired = sched.maybe_trigger()
        ok = (
            sched.stats.arbit_skipped_for_emotion == 1
            and sched._next_priority_boost is False  # noqa: SLF001
            and sched._next_priority_boost_level is None  # noqa: SLF001
            and sched._mm_llm_context is None  # noqa: SLF001
        )
        # 同时 emit 不应带 boost_level
        topic_emits = [kw for ev, kw in emits if ev == "interact.proactive_topic"]
        ok2 = (not topic_emits) or ("boost_level" not in topic_emits[0])
        _record(
            "V8 arbit ON: same-frame emotion + boost + mm_ctx -> all suppressed for fusion/mm path",
            ok and ok2,
            f"fired={fired} arbit_emotion={sched.stats.arbit_skipped_for_emotion} "
            f"boost={sched._next_priority_boost!r} lvl={sched._next_priority_boost_level!r} "  # noqa: SLF001
            f"mm_ctx={sched._mm_llm_context!r}",  # noqa: SLF001
        )
    finally:
        _unset_arbit_env()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    _unset_arbit_env()
    checks = [
        v1_default_off_equivalence,
        v2_emotion_alert_preempts_fusion,
        v3_boost_level_cooldown_scaling,
        v4_no_boost_no_emotion_normal_path,
        v5_boost_does_not_bypass_cooldown,
        v6_emit_schema_boost_level,
        v7_markers_present,
        v8_concurrent_emotion_and_boost,
    ]
    for fn in checks:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _record(fn.__name__, False, f"exception: {type(e).__name__}: {e}")

    n_pass = sum(1 for r in _results if r["ok"])
    n_total = len(_results)
    _print("SUMMARY", f"{n_pass}/{n_total} PASS")

    out_dir = ROOT / "evidence" / "interact-014"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(
            {
                "feature": "interact-014",
                "ts": time.time(),
                "n_pass": n_pass,
                "n_total": n_total,
                "results": _results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
