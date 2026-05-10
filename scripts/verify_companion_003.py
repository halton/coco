"""companion-003 verification: 节能 idle 状态机.

跑法:
  uv run python scripts/verify_companion_003.py

V1: FSM 时间推进 — active → drowsy → sleep 时间线（FakeClock）
V2: wake event 重置（wake-word / face / interact 三种 source 都把状态拉回 active）
V3: drowsy 时 IdleAnimator interval scale 验证（_sample_micro_interval 受 power state 调制）
V4: 进入 sleep 调 robot.goto_sleep；离开 sleep 调 robot.wake_up（FakeRobot 断言）
V5: 默认 COCO_POWER_IDLE=0 时 power_idle_enabled_from_env() == False（向后兼容）
V6: env clamp（drowsy_after 负数 / 超大；sleep <= drowsy 自动修正）
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from coco.idle import IdleAnimator, IdleConfig
from coco.power_state import (
    PowerConfig,
    PowerState,
    PowerStateMachine,
    config_from_env,
    power_idle_enabled_from_env,
)


errors: List[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok   {msg}")
    else:
        errors.append(msg)
        print(f"  FAIL {msg}")


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
        self.goto_sleep_calls = 0
        self.wake_up_calls = 0
        self.goto_target_calls = 0
        self.antenna_calls = 0
        self._lock = threading.Lock()

    def goto_sleep(self) -> None:
        with self._lock:
            self.goto_sleep_calls += 1

    def wake_up(self) -> None:
        with self._lock:
            self.wake_up_calls += 1

    def goto_target(self, head=None, duration: float = 0.5) -> None:
        time.sleep(min(0.02, duration))
        with self._lock:
            self.goto_target_calls += 1

    def set_target_antenna_joint_positions(self, vals) -> None:
        with self._lock:
            self.antenna_calls += 1


# ---------------------------------------------------------------------------
# V1: 时间推进 active → drowsy → sleep
# ---------------------------------------------------------------------------
print("\n--- V1: FSM time progression active→drowsy→sleep ---")
clk = FakeClock()
psm = PowerStateMachine(
    config=PowerConfig(drowsy_after=60.0, sleep_after=120.0, tick_interval=1.0),
    clock=clk,
)
robot = FakeRobot()
psm.on_enter_sleep = lambda m, _r=robot: _r.goto_sleep()
psm.on_enter_active = lambda m, prev, _r=robot: _r.wake_up() if prev == PowerState.SLEEP else None

check(psm.current_state == PowerState.ACTIVE, "init state == ACTIVE")

clk.advance(30.0); psm.tick()
check(psm.current_state == PowerState.ACTIVE, "after 30s still ACTIVE")

clk.advance(35.0); psm.tick()  # total 65s > 60
check(psm.current_state == PowerState.DROWSY, "after 65s -> DROWSY")
check(psm.stats.transitions_to_drowsy == 1, "transitions_to_drowsy == 1")

clk.advance(60.0); psm.tick()  # total 125s > 120
check(psm.current_state == PowerState.SLEEP, "after 125s -> SLEEP")
check(psm.stats.transitions_to_sleep == 1, "transitions_to_sleep == 1")
check(robot.goto_sleep_calls == 1, "robot.goto_sleep called once")
check(robot.wake_up_calls == 0, "robot.wake_up not yet called")


# ---------------------------------------------------------------------------
# V2: wake event 重置（三个 source）
# ---------------------------------------------------------------------------
print("\n--- V2: record_interaction resets to ACTIVE from any source ---")
for source in ["wake_word", "face", "interact"]:
    clk2 = FakeClock()
    psm2 = PowerStateMachine(
        config=PowerConfig(drowsy_after=10.0, sleep_after=20.0),
        clock=clk2,
    )
    robot2 = FakeRobot()
    psm2.on_enter_sleep = lambda m, _r=robot2: _r.goto_sleep()
    psm2.on_enter_active = lambda m, prev, _r=robot2: _r.wake_up() if prev == PowerState.SLEEP else None

    clk2.advance(25.0); psm2.tick()
    check(psm2.current_state == PowerState.SLEEP, f"[{source}] in SLEEP before wake")
    psm2.record_interaction(source=source)
    check(psm2.current_state == PowerState.ACTIVE, f"[{source}] back to ACTIVE")
    check(robot2.wake_up_calls == 1, f"[{source}] robot.wake_up called once on leaving SLEEP")
    check(psm2.stats.interactions_recorded == 1, f"[{source}] interactions_recorded == 1")

# DROWSY → ACTIVE 不应触发 wake_up
clk3 = FakeClock()
psm3 = PowerStateMachine(config=PowerConfig(drowsy_after=10.0, sleep_after=20.0), clock=clk3)
robot3 = FakeRobot()
psm3.on_enter_active = lambda m, prev, _r=robot3: _r.wake_up() if prev == PowerState.SLEEP else None
clk3.advance(15.0); psm3.tick()
check(psm3.current_state == PowerState.DROWSY, "DROWSY before interaction")
psm3.record_interaction("touch")
check(psm3.current_state == PowerState.ACTIVE, "DROWSY -> ACTIVE")
check(robot3.wake_up_calls == 0, "wake_up not called when leaving DROWSY (only SLEEP triggers)")


# ---------------------------------------------------------------------------
# V3: drowsy 时 IdleAnimator interval scale
# ---------------------------------------------------------------------------
print("\n--- V3: IdleAnimator interval scaled in DROWSY ---")
import random as _r
clk4 = FakeClock()
psm4 = PowerStateMachine(
    config=PowerConfig(drowsy_after=10.0, sleep_after=20.0, drowsy_micro_scale=2.0),
    clock=clk4,
)
robot4 = FakeRobot()
stop4 = threading.Event()
# 固定区间 [2, 2] 让对比可重复
cfg4 = IdleConfig(micro_interval_min=2.0, micro_interval_max=2.0,
                  glance_interval_min=10.0, glance_interval_max=10.0)
anim4 = IdleAnimator(robot4, stop4, config=cfg4, rng=_r.Random(42),
                     power_state=psm4)
# ACTIVE
i_active = anim4._sample_micro_interval()
check(abs(i_active - 2.0) < 1e-6, f"ACTIVE micro interval == 2.0 (got {i_active})")
# 推到 DROWSY
clk4.advance(15.0); psm4.tick()
i_drowsy = anim4._sample_micro_interval()
check(abs(i_drowsy - 4.0) < 1e-6, f"DROWSY micro interval == 4.0 (got {i_drowsy})")
i_drowsy_g = anim4._sample_glance_interval()
check(abs(i_drowsy_g - 20.0) < 1e-6, f"DROWSY glance interval == 20.0 (got {i_drowsy_g})")
# 推到 SLEEP
clk4.advance(20.0); psm4.tick()
check(psm4.current_state == PowerState.SLEEP, "psm in SLEEP for V3 sleep skip check")


# ---------------------------------------------------------------------------
# V4: SLEEP 状态下 IdleAnimator skip 动作（不调 goto_target）
# ---------------------------------------------------------------------------
print("\n--- V4: IdleAnimator skips actions while SLEEP ---")
clk5 = FakeClock()
psm5 = PowerStateMachine(
    config=PowerConfig(drowsy_after=2.0, sleep_after=4.0, tick_interval=0.2),
    clock=clk5,
)
robot5 = FakeRobot()
psm5.on_enter_sleep = lambda m, _r=robot5: _r.goto_sleep()
psm5.on_enter_active = lambda m, prev, _r=robot5: _r.wake_up() if prev == PowerState.SLEEP else None
# 强制设到 SLEEP
clk5.advance(5.0); psm5.tick()
assert psm5.current_state == PowerState.SLEEP, "precondition: psm5 SLEEP"
check(robot5.goto_sleep_calls == 1, "V4 entered SLEEP, goto_sleep called")

stop5 = threading.Event()
cfg5 = IdleConfig(micro_interval_min=0.5, micro_interval_max=0.6,
                  glance_interval_min=10.0, glance_interval_max=20.0)
anim5 = IdleAnimator(robot5, stop5, config=cfg5, power_state=psm5)
anim5.start()
time.sleep(2.0)  # 多次 tick (interval 0.5-0.6, 应有 ~3 次)
stop5.set(); anim5.join(timeout=2.0)
check(robot5.goto_target_calls == 0, f"SLEEP: no goto_target during 2s (got {robot5.goto_target_calls})")
check(robot5.antenna_calls == 0, f"SLEEP: no antenna calls (got {robot5.antenna_calls})")
check(anim5.stats.skipped_paused >= 2, f"SLEEP: skipped_paused >= 2 (got {anim5.stats.skipped_paused})")

# 唤醒后 idle 立刻能动作
psm5.record_interaction("test_wake")
check(psm5.current_state == PowerState.ACTIVE, "after record_interaction -> ACTIVE")
check(robot5.wake_up_calls == 1, "wake_up called once on leaving SLEEP")

stop6 = threading.Event()
anim6 = IdleAnimator(robot5, stop6, config=cfg5, power_state=psm5)
anim6.start()
time.sleep(2.0)
stop6.set(); anim6.join(timeout=2.0)
check(robot5.goto_target_calls + robot5.antenna_calls > 0,
      f"ACTIVE: idle resumed actions (goto={robot5.goto_target_calls} antenna={robot5.antenna_calls})")


# ---------------------------------------------------------------------------
# V5: 默认 COCO_POWER_IDLE 未设 → enabled_from_env False（向后兼容）
# ---------------------------------------------------------------------------
print("\n--- V5: env default OFF (backward compat) ---")
old = os.environ.pop("COCO_POWER_IDLE", None)
try:
    check(power_idle_enabled_from_env() is False, "default power_idle_enabled_from_env() == False")
    os.environ["COCO_POWER_IDLE"] = "1"
    check(power_idle_enabled_from_env() is True, "COCO_POWER_IDLE=1 enabled")
    os.environ["COCO_POWER_IDLE"] = "0"
    check(power_idle_enabled_from_env() is False, "COCO_POWER_IDLE=0 disabled")
finally:
    os.environ.pop("COCO_POWER_IDLE", None)
    if old is not None:
        os.environ["COCO_POWER_IDLE"] = old


# ---------------------------------------------------------------------------
# V6: env clamp
# ---------------------------------------------------------------------------
print("\n--- V6: env clamp drowsy/sleep ---")
saved = {k: os.environ.get(k) for k in ("COCO_POWER_DROWSY_AFTER", "COCO_POWER_SLEEP_AFTER")}
try:
    os.environ["COCO_POWER_DROWSY_AFTER"] = "-100"
    os.environ["COCO_POWER_SLEEP_AFTER"] = "9999999"
    cfg6 = config_from_env()
    check(cfg6.drowsy_after >= 5.0, f"drowsy_after clamp lo (got {cfg6.drowsy_after})")
    check(cfg6.sleep_after <= 7200.0, f"sleep_after clamp hi (got {cfg6.sleep_after})")

    # sleep <= drowsy 自动修正
    os.environ["COCO_POWER_DROWSY_AFTER"] = "100"
    os.environ["COCO_POWER_SLEEP_AFTER"] = "50"
    cfg7 = config_from_env()
    check(cfg7.sleep_after > cfg7.drowsy_after,
          f"sleep_after auto-bumped (drowsy={cfg7.drowsy_after} sleep={cfg7.sleep_after})")

    # invalid float
    os.environ["COCO_POWER_DROWSY_AFTER"] = "abc"
    cfg8 = config_from_env()
    check(cfg8.drowsy_after == PowerConfig().drowsy_after,
          f"invalid float falls back to default (got {cfg8.drowsy_after})")
finally:
    for k, v in saved.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v


# ---------------------------------------------------------------------------
# V7: driver thread 真实跑（短间隔）
# ---------------------------------------------------------------------------
print("\n--- V7: real driver thread tick ---")
clk7 = FakeClock()
psm7 = PowerStateMachine(
    config=PowerConfig(drowsy_after=2.0, sleep_after=5.0, tick_interval=0.1),
    clock=clk7,
)
robot7 = FakeRobot()
psm7.on_enter_sleep = lambda m, _r=robot7: _r.goto_sleep()
stop7 = threading.Event()
psm7.start_driver(stop7)
clk7.advance(10.0)  # 一次性跨过 sleep 阈值
time.sleep(0.4)  # driver 应在几次 tick 内观察到
stop7.set(); psm7.join_driver(timeout=2.0)
check(psm7.current_state == PowerState.SLEEP, f"driver thread reached SLEEP (state={psm7.current_state})")
check(robot7.goto_sleep_calls == 1, "driver thread invoked goto_sleep")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
summary = {
    "v1_active_drowsy_sleep_progression": True,
    "v2_record_interaction_resets": True,
    "v3_drowsy_interval_scale": True,
    "v4_sleep_skips_idle_and_wake_resumes": True,
    "v5_env_default_off": True,
    "v6_env_clamp": True,
    "v7_driver_thread": True,
    "errors": errors,
}
out_dir = REPO / "evidence" / "companion-003"
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "verify_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"\n--- Summary written to {out_dir/'verify_summary.json'} ---")

if errors:
    print(f"\nFAIL: {len(errors)} check(s) failed:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)

print("\nALL PASS — companion-003 verification")
sys.exit(0)
