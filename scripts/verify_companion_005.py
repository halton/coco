#!/usr/bin/env python3
"""verify_companion_005.py — 情境化 idle 行为验证（companion-005）.

V1  默认 OFF：situational_idle_enabled_from_env() == False；
    IdleAnimator(situational_modulator=None) 不调 modulator，行为退化到 phase-4。
V2  COCO_SIT_IDLE=1 时 modulator 能构造成功，配置默认值满足 validate。
V3  face_present + focus_stable ≥ threshold → micro_amp_scale > 1（且 ≤ scale_max）。
V4  face 丢失（face_present=False）→ micro_amp_scale 出现 face_absent_damp 衰减。
V5  interaction_recent_s 内 → glance_prob_scale < 1（专注模式）。
V6  长时间无交互（>= interaction_stale_s）+ power=ACTIVE → idle 衰减
    （micro 与 glance_prob 都 < 1）。
V7  power=DROWSY → idle 进一步衰减（drowsy_damp）。
V8  emotion=happy 与情境叠加：IdleAnimator 内 emotion_scale × situational micro_amp_scale
    > 各自单独。
V9  modulator 抛异常 → IdleAnimator fail-soft 回退 (1.0, 1.0, 1.0)。
V10 env clamp：COCO_SIT_IDLE_SCALE_MAX=0.5 时 micro_amp_scale ≤ 0.5。
V11 emit "companion.idle_situation_changed"：component "companion" ∈ AUTHORITATIVE_COMPONENTS。
    bias 变化时 emit_cb 被触发，payload 含 micro_amp_scale 等字段。
V12 IdleAnimator 集成不破坏 companion-003/-004 接口（power=SLEEP 还能 skip；构造签名向后兼容）。
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, List, Optional

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from coco.companion.situational_idle import (
    IdleBias,
    IdleSituation,
    SituationalIdleConfig,
    SituationalIdleModulator,
    situational_idle_config_from_env,
    situational_idle_enabled_from_env,
)
from coco.idle import IdleAnimator, IdleConfig
from coco.logging_setup import AUTHORITATIVE_COMPONENTS, emit
from coco.power_state import PowerConfig, PowerState, PowerStateMachine

errors: List[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok   {msg}")
    else:
        errors.append(msg)
        print(f"  FAIL {msg}")


# ----- 测试用 stubs -------------------------------------------------------


class FakeClock:
    def __init__(self, t0: float = 1000.0) -> None:
        self.t = t0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.t

    def advance(self, dt: float) -> None:
        with self._lock:
            self.t += dt


class FakeRobot:
    def __init__(self) -> None:
        self.calls: List[str] = []

    def goto_target(self, head=None, duration: float = 0.5) -> None:
        self.calls.append("goto_target")

    def set_target_antenna_joint_positions(self, vals) -> None:
        self.calls.append("antenna")

    def goto_sleep(self) -> None:
        self.calls.append("goto_sleep")

    def wake_up(self) -> None:
        self.calls.append("wake_up")


class FakeFaceSnapshot:
    def __init__(self, present: bool) -> None:
        self.present = present
        self.primary = None

    def x_ratio(self):
        return None


class FakeFaceTracker:
    def __init__(self, present: bool) -> None:
        self._present = present

    def latest(self) -> FakeFaceSnapshot:
        return FakeFaceSnapshot(self._present)

    def set_present(self, v: bool) -> None:
        self._present = v


class FakeAttentionTarget:
    def __init__(self, track_id: int) -> None:
        self.track_id = track_id


class FakeSelector:
    def __init__(self, target_id: Optional[int]) -> None:
        self._target_id = target_id

    def current(self):
        return None if self._target_id is None else FakeAttentionTarget(self._target_id)

    def set(self, tid: Optional[int]) -> None:
        self._target_id = tid


class FakeEmotionTracker:
    def __init__(self, label: Optional[str]) -> None:
        self._label = label

    @property
    def current(self):
        return self._label


# ----- V1: default OFF -----
print("\n--- V1: default OFF ---")
saved = os.environ.pop("COCO_SIT_IDLE", None)
try:
    check(situational_idle_enabled_from_env() is False, "COCO_SIT_IDLE 默认 OFF")
    cfg = situational_idle_config_from_env()
    check(cfg.enabled is False, "config.enabled 默认 False")
    # IdleAnimator(situational_modulator=None) 不破坏
    stop = threading.Event()
    robot = FakeRobot()
    anim = IdleAnimator(robot=robot, stop_event=stop)  # no modulator
    check(anim.situational_modulator is None, "默认 situational_modulator=None")
    # _situational_bias 应直接返回 (1,1,1)
    m, gp, ga = anim._situational_bias()
    check(m == 1.0 and gp == 1.0 and ga == 1.0, "modulator=None → bias 默认 1.0")
finally:
    if saved is not None:
        os.environ["COCO_SIT_IDLE"] = saved


# ----- V2: COCO_SIT_IDLE=1 构造 -----
print("\n--- V2: enabled + construct modulator ---")
os.environ["COCO_SIT_IDLE"] = "1"
try:
    check(situational_idle_enabled_from_env() is True, "enabled_from_env=True")
    cfg = situational_idle_config_from_env()
    check(cfg.enabled is True, "cfg.enabled=True")
    mod = SituationalIdleModulator(config=cfg)
    check(isinstance(mod.tick(), IdleBias), "tick() 返回 IdleBias")
finally:
    os.environ.pop("COCO_SIT_IDLE", None)


# ----- V3: focus_stable boost -----
print("\n--- V3: focus stable → micro_amp boost ---")
cfg = SituationalIdleConfig(enabled=True)
sit = IdleSituation(
    face_present=True,
    focus_stable_s=5.0,
    time_since_interaction_s=100.0,  # 不触发 recent/stale
    power_state="active",
    emotion=None,
)
mod = SituationalIdleModulator(config=cfg)
bias = mod.compute(sit)
check(bias.micro_amp_scale > 1.0, f"micro_amp_scale={bias.micro_amp_scale} > 1.0")
check(bias.micro_amp_scale <= cfg.scale_max, "micro_amp_scale ≤ scale_max")
check(bias.glance_prob_scale < 1.0, f"focus stable damp glance_prob={bias.glance_prob_scale}")


# ----- V4: face 丢失 -----
print("\n--- V4: face absent damp ---")
sit_abs = IdleSituation(
    face_present=False,
    focus_stable_s=0.0,
    time_since_interaction_s=100.0,
    power_state="active",
)
bias_abs = mod.compute(sit_abs)
check(bias_abs.micro_amp_scale < 1.0, f"face absent → micro_amp_scale={bias_abs.micro_amp_scale} < 1.0")


# ----- V5: interaction recent -----
print("\n--- V5: interaction recent → glance damp ---")
sit_recent = IdleSituation(
    face_present=True,
    focus_stable_s=0.0,
    time_since_interaction_s=5.0,  # < 30s
    power_state="active",
)
bias_recent = mod.compute(sit_recent)
check(bias_recent.glance_prob_scale < 1.0,
      f"recent → glance_prob_scale={bias_recent.glance_prob_scale} < 1.0")
check(bias_recent.micro_amp_scale >= 1.0,
      f"recent → micro_amp_scale={bias_recent.micro_amp_scale} ≥ 1.0 (boost)")


# ----- V6: stale + active -----
print("\n--- V6: long stale + active ---")
sit_stale = IdleSituation(
    face_present=True,
    focus_stable_s=0.0,
    time_since_interaction_s=600.0,  # > 180s stale
    power_state="active",
)
bias_stale = mod.compute(sit_stale)
check(bias_stale.micro_amp_scale < 1.0,
      f"stale → micro_amp_scale={bias_stale.micro_amp_scale} < 1.0")
check(bias_stale.glance_prob_scale < 1.0,
      f"stale → glance_prob_scale={bias_stale.glance_prob_scale} < 1.0")


# ----- V7: drowsy -----
print("\n--- V7: drowsy further damp ---")
sit_drowsy = IdleSituation(
    face_present=True,
    focus_stable_s=0.0,
    time_since_interaction_s=100.0,
    power_state="drowsy",
)
bias_drowsy = mod.compute(sit_drowsy)
sit_active = IdleSituation(
    face_present=True,
    focus_stable_s=0.0,
    time_since_interaction_s=100.0,
    power_state="active",
)
bias_active = mod.compute(sit_active)
check(bias_drowsy.micro_amp_scale < bias_active.micro_amp_scale,
      f"drowsy micro={bias_drowsy.micro_amp_scale} < active micro={bias_active.micro_amp_scale}")


# ----- V8: emotion happy + situational 叠加（在 IdleAnimator 层）-----
print("\n--- V8: emotion × situational 叠加 ---")
# IdleAnimator 内：emotion_scale * sit_micro_amp_scale 共同决定 micro_yaw_amp_deg
class _MicroProbeAnimator(IdleAnimator):
    """劫持 _safe 防止真的下发 SDK，记录每次 yaw amp。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.last_yaw_range = (0.0, 0.0)

    def _safe(self, label, fn):
        return None


# 1) modulator=None + emotion=None → scale=1
stop = threading.Event()
anim_baseline = _MicroProbeAnimator(robot=FakeRobot(), stop_event=stop)
import random as _r
anim_baseline.rng = _r.Random(0)
# 直接调 _emotion_scale * _situational_bias() 比对
emo_base = anim_baseline._emotion_scale()
m_base, _, _ = anim_baseline._situational_bias()
check(emo_base == 1.0 and m_base == 1.0, "baseline emo=1.0 sit=1.0")

# 2) emotion=happy → emotion_scale=1.3
anim_emo = _MicroProbeAnimator(robot=FakeRobot(), stop_event=stop)
anim_emo.set_current_emotion("happy")
emo_h = anim_emo._emotion_scale()
check(emo_h > 1.0, f"emotion=happy → emotion_scale={emo_h} > 1.0")

# 3) modulator focus_stable → sit_micro > 1
cfg2 = SituationalIdleConfig(enabled=True)
mod_focus = SituationalIdleModulator(config=cfg2)
# 强制 snapshot 返回 focus_stable
def _fake_snap():
    return IdleSituation(face_present=True, focus_stable_s=5.0, time_since_interaction_s=100.0,
                         power_state="active", emotion="happy")
mod_focus.snapshot = _fake_snap  # type: ignore[method-assign]
anim_both = _MicroProbeAnimator(robot=FakeRobot(), stop_event=stop,
                                 situational_modulator=mod_focus)
anim_both.set_current_emotion("happy")
emo_b = anim_both._emotion_scale()
m_b, _, _ = anim_both._situational_bias()
combined = emo_b * m_b
check(combined > emo_b, f"combined={combined} > emotion-only={emo_b}")
check(combined > m_b, f"combined={combined} > situational-only={m_b}")


# ----- V9: modulator 异常 fail-soft -----
print("\n--- V9: modulator exception fail-soft ---")
class BoomModulator:
    def tick(self):
        raise RuntimeError("boom")


anim_boom = IdleAnimator(robot=FakeRobot(), stop_event=stop, situational_modulator=BoomModulator())
m, gp, ga = anim_boom._situational_bias()
check(m == 1.0 and gp == 1.0 and ga == 1.0, "modulator 抛异常 → bias 回退 1.0")

# modulator.compute 内部异常也 fail-soft
class HalfBroken:
    def __init__(self):
        self._cfg = SituationalIdleConfig(enabled=True)

    def snapshot(self):
        raise RuntimeError("snap boom")

    def compute(self, sit=None):
        return SituationalIdleModulator.compute(self, sit)  # 走真实 compute，会调坏的 snapshot


half = SituationalIdleModulator(config=SituationalIdleConfig(enabled=True))
half.snapshot = lambda: (_ for _ in ()).throw(RuntimeError("snap boom"))  # type: ignore[assignment]
b = half.compute()
check(b == IdleBias(), f"snapshot 抛 → compute 返回默认 IdleBias，实际={b}")


# ----- V10: env clamp -----
print("\n--- V10: env clamp ---")
os.environ["COCO_SIT_IDLE"] = "1"
os.environ["COCO_SIT_IDLE_SCALE_MAX"] = "0.5"
try:
    cfg_clamp = situational_idle_config_from_env()
    check(cfg_clamp.scale_max == 0.5, f"scale_max={cfg_clamp.scale_max}")
    mod_c = SituationalIdleModulator(config=cfg_clamp)
    sit_high = IdleSituation(
        face_present=True, focus_stable_s=10.0,
        time_since_interaction_s=5.0,
        power_state="active", emotion="happy",
    )
    b = mod_c.compute(sit_high)
    check(b.micro_amp_scale <= 0.5 + 1e-9, f"clamp max → micro_amp_scale={b.micro_amp_scale} ≤ 0.5")
    check(b.glance_prob_scale <= 0.5 + 1e-9, f"clamp max → glance_prob_scale={b.glance_prob_scale} ≤ 0.5")
finally:
    os.environ.pop("COCO_SIT_IDLE_SCALE_MAX", None)
    os.environ.pop("COCO_SIT_IDLE", None)

# scale_min clamp
cfg_floor = SituationalIdleConfig(enabled=True, scale_min=0.3, scale_max=2.0)
mod_floor = SituationalIdleModulator(config=cfg_floor)
sit_low = IdleSituation(
    face_present=False, focus_stable_s=0.0,
    time_since_interaction_s=10000.0,
    power_state="drowsy",
)
b_low = mod_floor.compute(sit_low)
check(b_low.micro_amp_scale >= 0.3 - 1e-9, f"clamp min → micro_amp_scale={b_low.micro_amp_scale} ≥ 0.3")


# ----- V11: emit + AUTHORITATIVE_COMPONENTS -----
print("\n--- V11: emit companion.idle_situation_changed ---")
check("companion" in AUTHORITATIVE_COMPONENTS, "'companion' ∈ AUTHORITATIVE_COMPONENTS")

# Hook a handler to capture jsonl emit
log_root = logging.getLogger()
buf = io.StringIO()
handler = logging.StreamHandler(buf)
from coco.logging_setup import JsonlFormatter
handler.setFormatter(JsonlFormatter())
log_root.addHandler(handler)
prev_level = log_root.level
log_root.setLevel(logging.INFO)

emitted: List[dict] = []

def _emit_cb(prev, curr, sit):
    emit(
        "companion.idle_situation_changed",
        micro_amp_scale=curr.micro_amp_scale,
        glance_prob_scale=curr.glance_prob_scale,
        glance_amp_scale=curr.glance_amp_scale,
        face_present=sit.face_present,
        focus_stable_s=sit.focus_stable_s,
        power_state=sit.power_state,
    )

try:
    mod_emit = SituationalIdleModulator(config=SituationalIdleConfig(enabled=True),
                                         emit_cb=_emit_cb)
    # Force two different bias values via patched snapshot
    seq = [
        IdleSituation(face_present=True, focus_stable_s=0.0, time_since_interaction_s=100.0, power_state="active"),
        IdleSituation(face_present=True, focus_stable_s=10.0, time_since_interaction_s=5.0, power_state="active"),
    ]
    idx = [0]
    def _snap2(s=seq, i=idx):
        v = s[min(i[0], len(s)-1)]
        i[0] += 1
        return v
    mod_emit.snapshot = _snap2  # type: ignore[method-assign]
    b1 = mod_emit.tick()
    b2 = mod_emit.tick()
    check(b1 != b2 or b2.glance_prob_scale != b1.glance_prob_scale, "两轮 bias 不同")
finally:
    log_root.removeHandler(handler)
    log_root.setLevel(prev_level)

# Parse the captured jsonl
lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
parsed = []
for ln in lines:
    try:
        parsed.append(json.loads(ln))
    except json.JSONDecodeError:
        pass
events = [p for p in parsed if p.get("component") == "companion" and p.get("event") == "idle_situation_changed"]
check(len(events) >= 1, f"emit 至少一条 companion.idle_situation_changed，实际 {len(events)} 条")
if events:
    e = events[-1]
    check("micro_amp_scale" in e, f"payload 含 micro_amp_scale, keys={list(e.keys())}")
    check("glance_prob_scale" in e, "payload 含 glance_prob_scale")


# ----- V12: IdleAnimator 集成不破坏 003/004 -----
print("\n--- V12: integration 不破坏 power=SLEEP skip + 构造签名向后兼容 ---")
# 构造一个 power_state machine 推到 SLEEP
clk = FakeClock()
psm = PowerStateMachine(config=PowerConfig(drowsy_after=10.0, sleep_after=20.0, tick_interval=1.0),
                       clock=clk)
clk.advance(25.0); psm.tick()
check(psm.current_state == PowerState.SLEEP, "psm in SLEEP")

# IdleAnimator with modulator + power=SLEEP；运行 0.5s 应该被 skip（_is_power_sleep True）
stop = threading.Event()
robot = FakeRobot()
mod_sleep = SituationalIdleModulator(
    config=SituationalIdleConfig(enabled=True),
    power_state=psm,
)
# 用极短 interval 加快测试
fast_cfg = IdleConfig(micro_interval_min=0.5, micro_interval_max=0.6)
anim_int = IdleAnimator(robot=robot, stop_event=stop, config=fast_cfg,
                        power_state=psm, situational_modulator=mod_sleep)
anim_int.start()
time.sleep(0.8)
stop.set()
anim_int.join(timeout=2.0)
# 在 SLEEP 状态下不应该有 goto_target / antenna 调用
sdk_calls = [c for c in robot.calls if c in ("goto_target", "antenna")]
check(len(sdk_calls) == 0, f"SLEEP 时 IdleAnimator skip 所有动作；实际 calls={sdk_calls}")

# 旧调用方式（不传 situational_modulator）仍能构造
stop2 = threading.Event()
anim_legacy = IdleAnimator(robot=FakeRobot(), stop_event=stop2,
                           power_state=psm)
check(anim_legacy.situational_modulator is None, "向后兼容：不传 situational_modulator 默认 None")


# ----- summary -----
print("\n" + "=" * 60)
if errors:
    print(f"FAILED: {len(errors)} 项")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print("ALL PASS")
# evidence
ev_dir = REPO / "evidence" / "companion-005"
ev_dir.mkdir(parents=True, exist_ok=True)
(ev_dir / "verify_summary.json").write_text(
    json.dumps({"status": "pass", "checks": ["V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9", "V10", "V11", "V12"]},
               indent=2), encoding="utf-8")
print(f"evidence written to {ev_dir}/verify_summary.json")
