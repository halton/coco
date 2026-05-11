#!/usr/bin/env python3
"""verify_interact_007.py — 主动话题发起 (interact-007) 验证.

覆盖：
  V1 默认 OFF：COCO_PROACTIVE 未设 → proactive_enabled_from_env=False；main 不构造 scheduler
  V2 COCO_PROACTIVE=1 → config_from_env().enabled=True；ProactiveScheduler 构造成功
  V3 触发条件全满足 → maybe_trigger 返回 True，stats.triggered=1
  V4 PowerState=DROWSY/SLEEP → 不触发 (skipped_power)
  V5 无 face presence → 不触发 (skipped_no_face)
  V6 idle 未到 → 不触发 (skipped_idle)
  V7 cooldown 未到 → 不触发 (skipped_cooldown)
  V8 限流 max_topics_per_hour 生效 → 第 N+1 次被拒 (skipped_rate_limit)
  V9 触发后 on_interaction 钩子被调用（即 record_interaction("proactive")）
  V10 emit "interact.proactive_topic" 事件被打出
  V11 UserProfile 注入 LLM system_prompt（profile_store 注入时 llm_reply_fn 收到 system_prompt kwarg）
  V12 LLM 失败 fail-soft：llm_reply_fn 抛异常 → 仍走 TTS（兜底文本）+ stats.llm_errors=1
  V13 env clamp：非法值回退默认；超界 clamp 到边界
  V14 集成 InteractSession.on_interaction → ProactiveScheduler.record_interaction：
      InteractSession.handle_audio 触发后，scheduler 的 last_interaction_ts 被刷新

evidence/interact-007/verify_summary.json 写确定性结果。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import MagicMock

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.proactive import (  # noqa: E402
    DEFAULT_COOLDOWN_S,
    DEFAULT_IDLE_S,
    DEFAULT_MAX_PER_HOUR,
    ProactiveConfig,
    ProactiveScheduler,
    config_from_env,
    proactive_enabled_from_env,
)
from coco.power_state import PowerConfig, PowerState, PowerStateMachine  # noqa: E402
from coco.interact import InteractSession  # noqa: E402
from coco.logging_setup import setup_logging  # noqa: E402


EVIDENCE_DIR = ROOT / "evidence" / "interact-007"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

PASSES: List[str] = []
FAILURES: List[str] = []


def ok(msg: str) -> None:
    print(f"  PASS {msg}", flush=True)
    PASSES.append(msg)


def fail(msg: str) -> None:
    print(f"  FAIL {msg}", flush=True)
    FAILURES.append(msg)


def assert_true(cond: bool, label: str) -> bool:
    if cond:
        ok(label)
        return True
    fail(label)
    return False


def assert_eq(actual: Any, expected: Any, label: str) -> bool:
    if actual == expected:
        ok(f"{label}: {actual!r} == {expected!r}")
        return True
    fail(f"{label}: actual={actual!r} expected={expected!r}")
    return False


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeSnap:
    present: bool = True


class FakeFaceTracker:
    def __init__(self, present: bool = True) -> None:
        self._present = present

    def latest(self) -> FakeSnap:
        return FakeSnap(present=self._present)


class FakeClock:
    def __init__(self, t0: float = 1000.0) -> None:
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class CapturedLLM:
    """记录调用参数；可设 fail=True 抛异常。"""

    def __init__(self, reply_text: str = "今天感觉怎么样？", fail: bool = False) -> None:
        self.reply_text = reply_text
        self.fail = fail
        self.calls: list[dict] = []

    def reply(self, text: str, *, system_prompt: Optional[str] = None) -> str:
        self.calls.append({"text": text, "system_prompt": system_prompt})
        if self.fail:
            raise RuntimeError("simulated llm failure")
        return self.reply_text


class CapturedTTS:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def say(self, text: str, *, blocking: bool = True) -> None:
        self.calls.append({"text": text, "blocking": blocking})


# ---------------------------------------------------------------------------
# V1
# ---------------------------------------------------------------------------


def v1_default_off() -> None:
    print("\n[V1] 默认 OFF：COCO_PROACTIVE 未设 → enabled=False", flush=True)
    saved = os.environ.pop("COCO_PROACTIVE", None)
    try:
        assert_eq(proactive_enabled_from_env(), False, "proactive_enabled_from_env() 未设")
        cfg = config_from_env()
        assert_eq(cfg.enabled, False, "config_from_env().enabled 未设")
    finally:
        if saved is not None:
            os.environ["COCO_PROACTIVE"] = saved
    # 也确认 main 集成路径：grep main.py 里的构造条件守门
    main_src = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    assert_true(
        "if _pcfg.enabled:" in main_src,
        "main.py 用 _pcfg.enabled 守门 ProactiveScheduler 构造",
    )


# ---------------------------------------------------------------------------
# V2
# ---------------------------------------------------------------------------


def v2_enabled_constructs() -> None:
    print("\n[V2] COCO_PROACTIVE=1 → 构造成功 + enabled=True", flush=True)
    os.environ["COCO_PROACTIVE"] = "1"
    try:
        cfg = config_from_env()
        assert_eq(cfg.enabled, True, "cfg.enabled")
        sched = ProactiveScheduler(config=cfg)
        assert_true(sched is not None, "ProactiveScheduler 构造成功")
        assert_eq(sched.stats.triggered, 0, "初始 triggered=0")
    finally:
        os.environ.pop("COCO_PROACTIVE", None)


# ---------------------------------------------------------------------------
# V3 — 触发全满足
# ---------------------------------------------------------------------------


def _build_sched_for_trigger(
    *,
    clock: FakeClock,
    enabled: bool = True,
    idle_s: float = 60.0,
    cooldown_s: float = 180.0,
    max_per_hour: int = 10,
    power: PowerStateMachine | None = None,
    face_present: bool = True,
    profile_store: Any = None,
    llm: CapturedLLM | None = None,
    tts: CapturedTTS | None = None,
    on_interaction=None,
    emit_calls: list | None = None,
) -> tuple[ProactiveScheduler, CapturedLLM, CapturedTTS]:
    cfg = ProactiveConfig(
        enabled=enabled,
        idle_threshold_s=idle_s,
        cooldown_s=cooldown_s,
        max_topics_per_hour=max_per_hour,
        tick_s=1.0,
    )
    llm = llm or CapturedLLM()
    tts = tts or CapturedTTS()

    def _emit(component_event: str, message: str = "", **payload):
        if emit_calls is not None:
            emit_calls.append({"event": component_event, **payload})

    sched = ProactiveScheduler(
        config=cfg,
        power_state=power,
        face_tracker=FakeFaceTracker(present=face_present),
        llm_reply_fn=llm.reply,
        tts_say_fn=tts.say,
        profile_store=profile_store,
        on_interaction=on_interaction,
        clock=clock,
        emit_fn=_emit,
    )
    return sched, llm, tts


def v3_trigger_when_all_satisfied() -> None:
    print("\n[V3] 全条件满足 → 触发主动话题", flush=True)
    clock = FakeClock(t0=1000.0)
    power = PowerStateMachine(config=PowerConfig(drowsy_after=300, sleep_after=600), clock=clock)
    sched, llm, tts = _build_sched_for_trigger(clock=clock, idle_s=60.0, power=power)
    # 把 last_interaction 拉远（>idle_threshold）
    clock.advance(120.0)
    triggered = sched.maybe_trigger()
    assert_true(triggered, "maybe_trigger() 返回 True")
    assert_eq(sched.stats.triggered, 1, "stats.triggered=1")
    assert_eq(len(llm.calls), 1, "llm 被调一次")
    assert_eq(len(tts.calls), 1, "tts 被调一次")
    assert_true(tts.calls[0]["text"] == llm.reply_text, "tts 文本与 llm reply 一致")


# ---------------------------------------------------------------------------
# V4 — power 非 ACTIVE 不发
# ---------------------------------------------------------------------------


def v4_power_drowsy_sleep_blocks() -> None:
    print("\n[V4] PowerState=DROWSY/SLEEP 不触发", flush=True)
    for state in (PowerState.DROWSY, PowerState.SLEEP):
        clock = FakeClock(t0=1000.0)
        power = PowerStateMachine(
            config=PowerConfig(drowsy_after=10, sleep_after=20),
            clock=clock,
        )
        # 强制 power 进入目标状态
        clock.advance(50.0)  # >sleep_after
        power.tick()
        # 若目标是 DROWSY，重设到 drowsy
        if state == PowerState.DROWSY:
            # reset 到 drowsy：先 record_interaction 回 active，再 advance 到 drowsy 区间
            power.record_interaction(source="test")
            clock.advance(15.0)
            power.tick()
            assert_eq(power.current_state, PowerState.DROWSY, f"power=DROWSY 准备态")
        else:
            assert_eq(power.current_state, PowerState.SLEEP, f"power=SLEEP 准备态")
        sched, llm, tts = _build_sched_for_trigger(clock=clock, power=power, idle_s=1.0)
        clock.advance(10.0)
        triggered = sched.maybe_trigger()
        assert_eq(triggered, False, f"power={state.value} 不触发")
        assert_true(sched.stats.skipped_power >= 1, f"skipped_power 计数 ({state.value})")
        assert_eq(len(llm.calls), 0, f"llm 未调 ({state.value})")


# ---------------------------------------------------------------------------
# V5 — 无人脸不发
# ---------------------------------------------------------------------------


def v5_no_face_blocks() -> None:
    print("\n[V5] 无 face presence 不触发", flush=True)
    clock = FakeClock(t0=1000.0)
    sched, llm, _ = _build_sched_for_trigger(clock=clock, face_present=False, idle_s=1.0)
    clock.advance(10.0)
    triggered = sched.maybe_trigger()
    assert_eq(triggered, False, "无人脸 → 不触发")
    assert_true(sched.stats.skipped_no_face >= 1, "skipped_no_face 计数")
    assert_eq(len(llm.calls), 0, "llm 未调")

    # face_tracker=None 也视为无人脸（保护性默认）
    cfg = ProactiveConfig(enabled=True, idle_threshold_s=1.0)
    sched2 = ProactiveScheduler(
        config=cfg,
        face_tracker=None,
        llm_reply_fn=CapturedLLM().reply,
        tts_say_fn=CapturedTTS().say,
        clock=clock,
    )
    clock.advance(10.0)
    assert_eq(sched2.maybe_trigger(), False, "face_tracker=None 不触发")


# ---------------------------------------------------------------------------
# V6 — idle 未到不发
# ---------------------------------------------------------------------------


def v6_idle_not_reached() -> None:
    print("\n[V6] idle 未到不触发", flush=True)
    clock = FakeClock(t0=1000.0)
    sched, llm, _ = _build_sched_for_trigger(clock=clock, idle_s=60.0)
    clock.advance(30.0)  # 仅 30s
    assert_eq(sched.maybe_trigger(), False, "idle 30s < 60s → 不触发")
    assert_true(sched.stats.skipped_idle >= 1, "skipped_idle 计数")
    assert_eq(len(llm.calls), 0, "llm 未调")


# ---------------------------------------------------------------------------
# V7 — cooldown 未到
# ---------------------------------------------------------------------------


def v7_cooldown_blocks() -> None:
    print("\n[V7] cooldown 未到不触发", flush=True)
    clock = FakeClock(t0=1000.0)
    sched, llm, _ = _build_sched_for_trigger(clock=clock, idle_s=10.0, cooldown_s=180.0)
    # 先发一次
    clock.advance(20.0)
    assert_true(sched.maybe_trigger(), "首次触发 OK")
    assert_eq(sched.stats.triggered, 1, "triggered=1")
    # 立刻再来一次（cooldown 未到）
    clock.advance(20.0)  # idle 又满足，但 cooldown 没满足
    triggered2 = sched.maybe_trigger()
    assert_eq(triggered2, False, "cooldown 内 → 不触发")
    assert_true(sched.stats.skipped_cooldown >= 1, "skipped_cooldown 计数")
    assert_eq(sched.stats.triggered, 1, "triggered 仍 1")
    # 推过 cooldown
    clock.advance(200.0)
    assert_true(sched.maybe_trigger(), "cooldown 过后再触发 OK")
    assert_eq(sched.stats.triggered, 2, "triggered=2")


# ---------------------------------------------------------------------------
# V8 — rate limit
# ---------------------------------------------------------------------------


def v8_rate_limit() -> None:
    print("\n[V8] 限流 max_topics_per_hour 生效", flush=True)
    clock = FakeClock(t0=1000.0)
    sched, llm, _ = _build_sched_for_trigger(
        clock=clock, idle_s=1.0, cooldown_s=1.0, max_per_hour=3,
    )
    # 触发 3 次（每次 cooldown=1s 间隔够）
    for i in range(3):
        clock.advance(5.0)
        assert_true(sched.maybe_trigger(), f"第{i+1}次触发 OK")
    assert_eq(sched.stats.triggered, 3, "triggered=3")
    # 第 4 次：cooldown 满足，但限流满
    clock.advance(5.0)
    assert_eq(sched.maybe_trigger(), False, "第4次被限流拒")
    assert_true(sched.stats.skipped_rate_limit >= 1, "skipped_rate_limit 计数")
    # 推过 1 小时，老条目过期
    clock.advance(3700.0)
    assert_true(sched.maybe_trigger(), "1h 后限流释放，可再触发")


# ---------------------------------------------------------------------------
# V9 — on_interaction 钩子
# ---------------------------------------------------------------------------


def v9_on_interaction_hook() -> None:
    print("\n[V9] 触发后调用 on_interaction 钩子", flush=True)
    clock = FakeClock(t0=1000.0)
    hook_calls: list[str] = []

    def _hook(src: str) -> None:
        hook_calls.append(src)

    sched, _, _ = _build_sched_for_trigger(
        clock=clock, idle_s=10.0, on_interaction=_hook,
    )
    clock.advance(20.0)
    assert_true(sched.maybe_trigger(), "触发 OK")
    assert_eq(hook_calls, ["proactive"], "hook 被调一次 source='proactive'")


# ---------------------------------------------------------------------------
# V10 — emit interact.proactive_topic
# ---------------------------------------------------------------------------


def v10_emit_event() -> None:
    print("\n[V10] emit interact.proactive_topic", flush=True)
    clock = FakeClock(t0=1000.0)
    emit_calls: list = []
    sched, _, _ = _build_sched_for_trigger(
        clock=clock, idle_s=10.0, emit_calls=emit_calls,
    )
    clock.advance(20.0)
    assert_true(sched.maybe_trigger(), "触发 OK")
    assert_eq(len(emit_calls), 1, "emit 调一次")
    assert_eq(emit_calls[0]["event"], "interact.proactive_topic", "event 名")
    assert_true("topic" in emit_calls[0], "payload 含 topic")


# ---------------------------------------------------------------------------
# V11 — profile 注入 system_prompt
# ---------------------------------------------------------------------------


def v11_profile_system_prompt() -> None:
    print("\n[V11] UserProfile 注入 LLM system_prompt", flush=True)
    from coco.profile import ProfileStore, UserProfile

    # 用临时 profile，避免污染用户的 profile.json
    tmp = ROOT / "evidence" / "interact-007" / "_tmp_profile.json"
    if tmp.exists():
        tmp.unlink()
    os.environ["COCO_PROFILE_PATH"] = str(tmp)
    try:
        store = ProfileStore()
        store.set_name("小明")
        store.add_interest("天文")
        store.add_goal("学英语")

        clock = FakeClock(t0=1000.0)
        sched, llm, _ = _build_sched_for_trigger(
            clock=clock, idle_s=10.0, profile_store=store,
        )
        clock.advance(20.0)
        assert_true(sched.maybe_trigger(), "触发 OK")
        assert_eq(len(llm.calls), 1, "llm 调一次")
        sp = llm.calls[0]["system_prompt"]
        assert_true(sp is not None and len(sp) > 0, "system_prompt 注入非空")
        assert_true("小明" in (sp or ""), "system_prompt 含 name=小明")
        assert_true("天文" in (sp or ""), "system_prompt 含 interest=天文")
        assert_true("学英语" in (sp or ""), "system_prompt 含 goal=学英语")
    finally:
        os.environ.pop("COCO_PROFILE_PATH", None)
        if tmp.exists():
            tmp.unlink()


# ---------------------------------------------------------------------------
# V12 — LLM 失败 fail-soft
# ---------------------------------------------------------------------------


def v12_llm_failure_failsoft() -> None:
    print("\n[V12] LLM 失败 → 兜底文本 + tts 仍调", flush=True)
    clock = FakeClock(t0=1000.0)
    bad_llm = CapturedLLM(fail=True)
    sched, _, tts = _build_sched_for_trigger(
        clock=clock, idle_s=10.0, llm=bad_llm,
    )
    clock.advance(20.0)
    assert_true(sched.maybe_trigger(), "触发 OK（不抛）")
    assert_eq(sched.stats.llm_errors, 1, "llm_errors=1")
    assert_eq(len(tts.calls), 1, "tts 仍调一次（兜底文本）")
    assert_true(len(tts.calls[0]["text"]) > 0, "兜底文本非空")


# ---------------------------------------------------------------------------
# V13 — env clamp
# ---------------------------------------------------------------------------


def v13_env_clamp() -> None:
    print("\n[V13] env clamp / 非法值回退", flush=True)
    saved = {k: os.environ.get(k) for k in [
        "COCO_PROACTIVE", "COCO_PROACTIVE_IDLE_S", "COCO_PROACTIVE_COOLDOWN_S",
        "COCO_PROACTIVE_MAX_PER_HOUR",
    ]}
    try:
        os.environ["COCO_PROACTIVE"] = "1"
        # 非法 → 默认
        os.environ["COCO_PROACTIVE_IDLE_S"] = "abc"
        os.environ["COCO_PROACTIVE_COOLDOWN_S"] = "xx"
        os.environ["COCO_PROACTIVE_MAX_PER_HOUR"] = "nope"
        cfg = config_from_env()
        assert_eq(cfg.idle_threshold_s, DEFAULT_IDLE_S, "idle 非法→默认")
        assert_eq(cfg.cooldown_s, DEFAULT_COOLDOWN_S, "cooldown 非法→默认")
        assert_eq(cfg.max_topics_per_hour, DEFAULT_MAX_PER_HOUR, "max/h 非法→默认")
        # 超界 → clamp
        os.environ["COCO_PROACTIVE_IDLE_S"] = "1"  # <10
        os.environ["COCO_PROACTIVE_COOLDOWN_S"] = "999999"  # >7200
        os.environ["COCO_PROACTIVE_MAX_PER_HOUR"] = "9999"  # >60
        cfg2 = config_from_env()
        assert_eq(cfg2.idle_threshold_s, 10.0, "idle clamp 下限")
        assert_eq(cfg2.cooldown_s, 7200.0, "cooldown clamp 上限")
        assert_eq(cfg2.max_topics_per_hour, 60, "max/h clamp 上限")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# V14 — InteractSession on_interaction 集成
# ---------------------------------------------------------------------------


def v14_session_integration() -> None:
    print("\n[V14] InteractSession.on_interaction → scheduler.record_interaction", flush=True)
    clock = FakeClock(t0=1000.0)
    sched, _, _ = _build_sched_for_trigger(clock=clock, idle_s=60.0)
    initial_ts = sched._last_interaction_ts

    def _asr(audio, sr):
        return "你好"

    tts_calls = []

    def _tts(text, blocking=True):
        tts_calls.append(text)

    robot = MagicMock()
    session = InteractSession(
        robot=robot,
        asr_fn=_asr,
        tts_say_fn=_tts,
        on_interaction=sched.record_interaction,
    )
    clock.advance(30.0)
    audio = np.zeros(16000, dtype=np.int16)
    session.handle_audio(audio, 16000, skip_action=True, skip_tts_play=True)
    new_ts = sched._last_interaction_ts
    assert_true(new_ts > initial_ts, f"last_interaction_ts 被刷新 ({initial_ts}->{new_ts})")
    # 推 50s（仍 < 60s idle threshold from new_ts）
    clock.advance(50.0)
    assert_eq(sched.maybe_trigger(), False, "session 后未到 idle → 不触发")
    # 再推到 idle 满足
    clock.advance(20.0)
    assert_true(sched.maybe_trigger(), "session 后到 idle → 触发")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=== verify_interact_007 ===", flush=True)
    setup_logging(jsonl=False, level="WARNING")
    v1_default_off()
    v2_enabled_constructs()
    v3_trigger_when_all_satisfied()
    v4_power_drowsy_sleep_blocks()
    v5_no_face_blocks()
    v6_idle_not_reached()
    v7_cooldown_blocks()
    v8_rate_limit()
    v9_on_interaction_hook()
    v10_emit_event()
    v11_profile_system_prompt()
    v12_llm_failure_failsoft()
    v13_env_clamp()
    v14_session_integration()

    print(f"\n--- 总结 ---", flush=True)
    print(f"PASS={len(PASSES)}  FAIL={len(FAILURES)}", flush=True)

    summary = {
        "verification": "verify_interact_007",
        "pass_count": len(PASSES),
        "fail_count": len(FAILURES),
        "passes": PASSES,
        "failures": FAILURES,
    }
    (EVIDENCE_DIR / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if FAILURES:
        print("==> FAIL: interact-007 有 failure", flush=True)
        for f in FAILURES:
            print(f"  - {f}", flush=True)
        return 1
    print("==> PASS: interact-007 verification 全部通过", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
