"""vision-004b verification: 多人主动致意状态机 greet_secondary.

跑法：
  uv run python scripts/verify_vision_004b.py

子项：
  V1  默认 OFF — COCO_MFA 未设时 enabled=False，tick 返回 None 不切状态
  V2  COCO_MFA=1 + env 构造 MFAConfig 各字段 clamp 正确
  V3  SINGLE → MULTI_IDLE：>=2 tracks + primary 存在
  V4  MULTI_IDLE → GREET_SECONDARY：silence + secondary_visible + cooldown + IDLE
                                    且返回 GreetAction，name/utterance 正确
  V5  silence_threshold_s 未到 → 不触发
  V6  secondary_visible_s 未到 → 不触发
  V7  greet_cooldown_s 内 → 第二次不触发
  V8  conv_state != IDLE → 不触发（awaiting_response 抑制）
  V9  proactive_recent=True → 不触发
  V10 未识别脸（name=None）不参与（require_named_secondary=True）
  V11 GREET → RETURN_PRIMARY → MULTI_IDLE 状态机时序
  V12 on_state_change / on_action 回调在锁外触发；回调抛异常状态机仍可继续
  + emit_fn 触发 companion.greet_secondary 事件

retval：0 全 PASS；1 任一失败
evidence 落 evidence/vision-004b/verify_summary.json
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.companion.multi_face_attention import (
    GreetAction,
    MFAConfig,
    MFAState,
    MultiFaceAttention,
    mfa_config_from_env,
)

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

errors: List[str] = []
results: dict = {}


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok   {msg}")
    else:
        errors.append(msg)
        print(f"  FAIL {msg}")


@dataclass
class FakeBox:
    cx: float = 0.0
    cy: float = 0.0
    w: int = 100
    h: int = 100


@dataclass
class FakeTrack:
    track_id: int
    name: Optional[str] = None
    last_seen_ts: float = 0.0
    smoothed_cx: float = 0.0
    smoothed_cy: float = 0.0
    box: FakeBox = field(default_factory=FakeBox)


@dataclass
class FakePrimary:
    track_id: int
    smoothed_cx: float = 0.0


class FakeClock:
    def __init__(self, t0: float = 1000.0) -> None:
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# -----------------------------------------------------------------------------
# V1: 默认 OFF
# -----------------------------------------------------------------------------

def v1_default_off() -> None:
    print("V1 默认 OFF (COCO_MFA 未设)")
    env = {k: v for k, v in os.environ.items() if not k.startswith("COCO_MFA")}
    cfg = mfa_config_from_env(env)
    check(cfg.enabled is False, "默认 enabled=False")
    mfa = MultiFaceAttention(config=cfg)
    t1 = FakeTrack(1, name="A", last_seen_ts=0)
    t2 = FakeTrack(2, name="B", last_seen_ts=0)
    primary = FakePrimary(1)
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(out is None, "禁用时 tick 返回 None")
    check(mfa.state is MFAState.SINGLE, "禁用时状态不变（保持 SINGLE）")
    results["V1"] = "PASS"


# -----------------------------------------------------------------------------
# V2: env clamp
# -----------------------------------------------------------------------------

def v2_env_clamp() -> None:
    print("V2 env 构造 + clamp")
    env = {
        "COCO_MFA": "1",
        "COCO_MFA_SILENCE_S": "9999",         # clamp to 600
        "COCO_MFA_SECONDARY_VIS_S": "0.0",    # clamp to 0.5
        "COCO_MFA_COOLDOWN_S": "1",           # clamp to 5
        "COCO_MFA_GREET_DUR_S": "0.05",       # clamp to 0.1
        "COCO_MFA_RETURN_DUR_S": "abc",       # invalid → default
        "COCO_MFA_PROACTIVE_BLOCK_S": "-5",   # clamp to 0
        "COCO_MFA_REQUIRE_NAMED": "0",
    }
    cfg = mfa_config_from_env(env)
    check(cfg.enabled is True, "enabled=True")
    check(cfg.silence_threshold_s == 600.0, f"silence clamp to 600 (got {cfg.silence_threshold_s})")
    check(cfg.secondary_visible_s == 0.5, f"secondary_visible clamp to 0.5 (got {cfg.secondary_visible_s})")
    check(cfg.greet_cooldown_s == 5.0, f"cooldown clamp to 5 (got {cfg.greet_cooldown_s})")
    check(cfg.greet_duration_s == 0.1, f"greet_dur clamp to 0.1 (got {cfg.greet_duration_s})")
    check(cfg.return_duration_s == 0.8, f"return_dur invalid fallback 0.8 (got {cfg.return_duration_s})")
    check(cfg.proactive_block_window_s == 0.0, f"proactive_block clamp to 0 (got {cfg.proactive_block_window_s})")
    check(cfg.require_named_secondary is False, "require_named=False")
    results["V2"] = "PASS"


# -----------------------------------------------------------------------------
# Helpers for state-machine V3-V11
# -----------------------------------------------------------------------------

def _make_mfa(clock: FakeClock, **overrides) -> MultiFaceAttention:
    cfg_kwargs = dict(
        enabled=True,
        silence_threshold_s=8.0,
        secondary_visible_s=3.0,
        greet_cooldown_s=30.0,
        greet_duration_s=1.2,
        return_duration_s=0.8,
        proactive_block_window_s=3.0,
        require_named_secondary=True,
    )
    cfg_kwargs.update(overrides)
    return MultiFaceAttention(config=MFAConfig(**cfg_kwargs), clock=clock)


# -----------------------------------------------------------------------------
# V3: SINGLE → MULTI_IDLE
# -----------------------------------------------------------------------------

def v3_single_to_multi() -> None:
    print("V3 SINGLE → MULTI_IDLE on >=2 tracks + primary")
    clk = FakeClock(1000.0)
    mfa = _make_mfa(clk)
    # 一张脸时
    t1 = FakeTrack(1, name="A", last_seen_ts=clk.t, smoothed_cx=100.0)
    primary = FakePrimary(1, smoothed_cx=100.0)
    out = mfa.tick(tracks=[t1], primary=primary, conv_state="idle")
    check(mfa.state is MFAState.SINGLE, f"单脸 SINGLE (got {mfa.state})")
    check(out is None, "单脸不触发")
    # 加一张
    t2 = FakeTrack(2, name="B", last_seen_ts=clk.t, smoothed_cx=300.0)
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(mfa.state is MFAState.MULTI_IDLE, f"双脸 MULTI_IDLE (got {mfa.state})")
    check(out is None, "进入 MULTI_IDLE 不立即触发 greet")
    results["V3"] = "PASS"


# -----------------------------------------------------------------------------
# V4: 触发 GREET
# -----------------------------------------------------------------------------

def v4_trigger_greet() -> None:
    print("V4 触发 GREET_SECONDARY")
    clk = FakeClock(1000.0)
    mfa = _make_mfa(clk)
    captured_actions: list[GreetAction] = []
    captured_states: list[tuple] = []
    mfa._on_action = captured_actions.append
    mfa._on_state_change = lambda p, c: captured_states.append((p, c))

    emitted: list[tuple] = []

    def emit(event, **kw):
        emitted.append((event, kw))

    mfa._emit_fn = emit

    t1 = FakeTrack(1, name="Alice", last_seen_ts=clk.t, smoothed_cx=100.0)
    t2 = FakeTrack(2, name="Bob", last_seen_ts=clk.t, smoothed_cx=400.0)
    primary = FakePrimary(1, smoothed_cx=100.0)

    # tick 0: 进入 MULTI_IDLE, 启动 secondary 计时
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    # 推进 secondary_visible 阈值
    clk.advance(3.5)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(mfa.state is MFAState.MULTI_IDLE, f"secondary 满 silence 未满 — 仍 MULTI_IDLE (got {mfa.state})")

    # 再推进 silence 阈值
    clk.advance(5.0)  # 总 silence 8.5s > 8s
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(mfa.state is MFAState.GREET_SECONDARY, f"GREET (got {mfa.state})")
    check(out is not None, "返回 GreetAction")
    if out is not None:
        check(out.secondary_track_id == 2, f"secondary_track_id=2 (got {out.secondary_track_id})")
        check(out.secondary_name == "Bob", f"secondary_name=Bob (got {out.secondary_name})")
        check(out.utterance == "你好 Bob", f"utterance=你好 Bob (got {out.utterance!r})")
        check(out.glance_hint == "right", f"glance_hint=right (Bob 在 primary 右 cx=400>100, got {out.glance_hint})")
    check(len(captured_actions) == 1, f"on_action 回调一次 (got {len(captured_actions)})")
    check((MFAState.MULTI_IDLE, MFAState.GREET_SECONDARY) in captured_states,
          "state change MULTI_IDLE→GREET 被回调")
    greet_emit = [e for e in emitted if e[0] == "companion.greet_secondary"]
    check(len(greet_emit) == 1, f"emit companion.greet_secondary 一次 (got {len(greet_emit)})")
    if greet_emit:
        kw = greet_emit[0][1]
        check(kw.get("secondary_name") == "Bob", "emit kw secondary_name=Bob")
        check(kw.get("glance_hint") == "right", "emit kw glance_hint=right")
    results["V4"] = "PASS"


# -----------------------------------------------------------------------------
# V5: silence 未到
# -----------------------------------------------------------------------------

def v5_silence_not_reached() -> None:
    print("V5 silence_threshold_s 未到 → 不触发")
    clk = FakeClock(1000.0)
    mfa = _make_mfa(clk)
    t1 = FakeTrack(1, name="A", last_seen_ts=clk.t)
    t2 = FakeTrack(2, name="B", last_seen_ts=clk.t)
    primary = FakePrimary(1)
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    clk.advance(5.0)  # secondary_visible 满 (>3s) 但 silence < 8s
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(out is None, "silence 5s < 8s 不触发")
    check(mfa.state is MFAState.MULTI_IDLE, "仍 MULTI_IDLE")
    results["V5"] = "PASS"


# -----------------------------------------------------------------------------
# V6: secondary 未到
# -----------------------------------------------------------------------------

def v6_secondary_not_reached() -> None:
    print("V6 secondary_visible_s 未到 → 不触发")
    clk = FakeClock(1000.0)
    mfa = _make_mfa(clk)
    t1 = FakeTrack(1, name="A", last_seen_ts=clk.t)
    primary = FakePrimary(1)
    # 单脸阶段，silence 计时由 _primary_silence_start_ts 起步 = 1000
    mfa.tick(tracks=[t1], primary=primary, conv_state="idle")
    # 9s 后第二张脸刚出现，secondary_visible=0
    clk.advance(9.0)
    t2 = FakeTrack(2, name="B", last_seen_ts=clk.t)
    t1.last_seen_ts = clk.t
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(out is None, "secondary 刚出现不触发 (silence 满 但 visible<3s)")
    check(mfa.state is MFAState.MULTI_IDLE, "进入 MULTI_IDLE 不触发 GREET")
    # 再推进 1s（visible=1s）
    clk.advance(1.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(out is None, "visible=1s 仍不触发")
    results["V6"] = "PASS"


# -----------------------------------------------------------------------------
# V7: cooldown
# -----------------------------------------------------------------------------

def v7_cooldown() -> None:
    print("V7 greet_cooldown_s 内不再触发")
    clk = FakeClock(1000.0)
    mfa = _make_mfa(clk)
    t1 = FakeTrack(1, name="A", last_seen_ts=clk.t)
    t2 = FakeTrack(2, name="B", last_seen_ts=clk.t)
    primary = FakePrimary(1)
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    # 让第一次 greet 触发
    clk.advance(10.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    out1 = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(out1 is not None, "第一次 greet 触发")
    # 推进 GREET / RETURN 走完
    clk.advance(1.5)  # GREET 1.2s expire
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    clk.advance(1.0)  # RETURN 0.8s expire
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(mfa.state is MFAState.MULTI_IDLE, f"回到 MULTI_IDLE (got {mfa.state})")
    # 再推进 silence 满，但 cooldown 仍内（last_greet 后 ~2.5s < 30s）
    clk.advance(10.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    out2 = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(out2 is None, "cooldown 内不再触发")
    check(mfa.state is MFAState.MULTI_IDLE, "状态保持 MULTI_IDLE")
    # 推进 cooldown 后再试
    clk.advance(20.0)  # 总距 last_greet ~32s > 30s
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    out3 = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(out3 is not None, "cooldown 结束后再次触发")
    results["V7"] = "PASS"


# -----------------------------------------------------------------------------
# V8: conv_state != IDLE 抑制
# -----------------------------------------------------------------------------

def v8_conv_state_suppression() -> None:
    print("V8 conv_state != IDLE → 抑制")
    clk = FakeClock(1000.0)
    mfa = _make_mfa(clk)
    t1 = FakeTrack(1, name="A", last_seen_ts=clk.t)
    t2 = FakeTrack(2, name="B", last_seen_ts=clk.t)
    primary = FakePrimary(1)
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    # silence 计时不会推进（非 IDLE 期间会被重置）
    clk.advance(10.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="listening")
    check(out is None, "listening 中不触发")
    # 再切回 idle 但 silence 已被重置（应再从 0 起算）
    clk.advance(2.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(out is None, "刚回 idle silence 重置 不触发")
    results["V8"] = "PASS"


# -----------------------------------------------------------------------------
# V9: proactive_recent 抑制
# -----------------------------------------------------------------------------

def v9_proactive_suppression() -> None:
    print("V9 proactive_recent=True → 抑制")
    clk = FakeClock(1000.0)
    mfa = _make_mfa(clk)
    t1 = FakeTrack(1, name="A", last_seen_ts=clk.t)
    t2 = FakeTrack(2, name="B", last_seen_ts=clk.t)
    primary = FakePrimary(1)
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    clk.advance(10.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle", proactive_recent=True)
    check(out is None, "proactive_recent=True 不触发")
    # proactive_recent=False 立即可触发
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle", proactive_recent=False)
    check(out is not None, "proactive_recent=False 触发")
    results["V9"] = "PASS"


# -----------------------------------------------------------------------------
# V10: 未识别脸不参与
# -----------------------------------------------------------------------------

def v10_unnamed_skipped() -> None:
    print("V10 未识别 secondary 不参与（require_named_secondary=True）")
    clk = FakeClock(1000.0)
    mfa = _make_mfa(clk)
    t1 = FakeTrack(1, name="A", last_seen_ts=clk.t)
    t2 = FakeTrack(2, name=None, last_seen_ts=clk.t)  # 未识别
    primary = FakePrimary(1)
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    clk.advance(10.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(out is None, "未识别 secondary 不参与")
    # 给 t2 加 name 后 visible 计时从此刻才起算（require_named=True 时
    # 未识别帧不积累 visible），需要再等满 secondary_visible_s 才能触发
    t2.name = "Carol"
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(out is None, "刚加名 secondary_visible=0 不触发")
    clk.advance(3.5)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(out is not None, "Carol visible 满后触发")
    if out is not None:
        check(out.secondary_name == "Carol", "name=Carol")
    results["V10"] = "PASS"


# -----------------------------------------------------------------------------
# V11: GREET → RETURN → MULTI_IDLE 时序
# -----------------------------------------------------------------------------

def v11_state_timing() -> None:
    print("V11 GREET → RETURN_PRIMARY → MULTI_IDLE 状态机时序")
    clk = FakeClock(1000.0)
    mfa = _make_mfa(clk)
    state_log: list[tuple] = []
    mfa._on_state_change = lambda p, c: state_log.append((p.value, c.value))
    t1 = FakeTrack(1, name="A", last_seen_ts=clk.t)
    t2 = FakeTrack(2, name="B", last_seen_ts=clk.t)
    primary = FakePrimary(1)
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    clk.advance(10.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")  # → GREET
    check(mfa.state is MFAState.GREET_SECONDARY, f"GREET (got {mfa.state})")

    clk.advance(0.5)  # GREET 未到期
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(mfa.state is MFAState.GREET_SECONDARY, "GREET 未到期 仍 GREET")

    clk.advance(1.0)  # 共 1.5s 到期
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(mfa.state is MFAState.RETURN_PRIMARY, f"RETURN_PRIMARY (got {mfa.state})")

    clk.advance(0.5)
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(mfa.state is MFAState.RETURN_PRIMARY, "RETURN 未到期 仍 RETURN")

    clk.advance(0.5)  # 共 1.0s > 0.8s
    mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(mfa.state is MFAState.MULTI_IDLE, f"回 MULTI_IDLE (got {mfa.state})")

    # 状态序列应包含 SINGLE→MULTI_IDLE→GREET→RETURN→MULTI_IDLE
    seq = [(p, c) for p, c in state_log]
    expected_pairs = {
        ("single", "multi_idle"),
        ("multi_idle", "greet_secondary"),
        ("greet_secondary", "return_primary"),
        ("return_primary", "multi_idle"),
    }
    missing = expected_pairs - set(seq)
    check(not missing, f"状态转移完整：missing={missing}")
    results["V11"] = "PASS"


# -----------------------------------------------------------------------------
# V12: 回调异常 + emit
# -----------------------------------------------------------------------------

def v12_callback_robustness() -> None:
    print("V12 on_state_change / on_action 回调异常不影响状态机")
    clk = FakeClock(1000.0)
    mfa = _make_mfa(clk)

    def bad_state(p, c):
        raise RuntimeError("boom-state")

    def bad_action(a):
        raise RuntimeError("boom-action")

    mfa._on_state_change = bad_state
    mfa._on_action = bad_action

    emitted: list = []
    mfa._emit_fn = lambda event, **kw: emitted.append((event, kw))

    t1 = FakeTrack(1, name="A", last_seen_ts=clk.t)
    t2 = FakeTrack(2, name="B", last_seen_ts=clk.t)
    primary = FakePrimary(1)

    # 第一次 tick：SINGLE→MULTI_IDLE 触发 bad_state，应被吞掉
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(mfa.state is MFAState.MULTI_IDLE, "回调异常后状态仍正确推进到 MULTI_IDLE")

    clk.advance(10.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    out = mfa.tick(tracks=[t1, t2], primary=primary, conv_state="idle")
    check(out is not None, "回调异常时 GreetAction 仍返回")
    check(mfa.state is MFAState.GREET_SECONDARY, "回调异常时仍进入 GREET")

    state_emits = [e for e, _ in emitted if e == "companion.multi_face_attention_state"]
    action_emits = [e for e, _ in emitted if e == "companion.greet_secondary"]
    check(len(state_emits) >= 2, f"emit state >=2 次 (got {len(state_emits)})")
    check(len(action_emits) == 1, f"emit greet 1 次 (got {len(action_emits)})")
    results["V12"] = "PASS"


# -----------------------------------------------------------------------------
# main
# -----------------------------------------------------------------------------

def main() -> int:
    t0 = time.monotonic()
    v1_default_off()
    v2_env_clamp()
    v3_single_to_multi()
    v4_trigger_greet()
    v5_silence_not_reached()
    v6_secondary_not_reached()
    v7_cooldown()
    v8_conv_state_suppression()
    v9_proactive_suppression()
    v10_unnamed_skipped()
    v11_state_timing()
    v12_callback_robustness()
    elapsed = time.monotonic() - t0

    print("---")
    if errors:
        print(f"FAIL  ({len(errors)} errors)")
        for e in errors:
            print(f"  - {e}")
        outcome = "FAIL"
    else:
        print(f"PASS  all V1-V12 ({elapsed:.2f}s)")
        outcome = "PASS"

    evidence_dir = ROOT / "evidence" / "vision-004b"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "vision-004b",
        "outcome": outcome,
        "elapsed_s": round(elapsed, 3),
        "results": results,
        "errors": errors,
    }
    (evidence_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
