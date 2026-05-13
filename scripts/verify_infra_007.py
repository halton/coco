"""verify_infra_007 — SelfHealRegistry: 注册 / 调度 / 退避 / 内置策略.

V1   指数退避数列正确（5/10/20/40/80, cap 120） — 用零 jitter
V2   jitter 在 ±10% 内
V3   attempts 上限 → giveup latch
V4   giveup 后不再重试
V5   AudioReopenStrategy fake reopen_fn 调用计数（真机模式下）
V6   ASRRestartStrategy 连续失败 N 切 fallback（reopen_fn 失败 → fail 计数）
V7   CameraReopenStrategy fake 连续 None → reopen 计数
V8   与 infra-005 daemon restart 不冲突（daemon failure_kind 不被 dispatch）
V9   default-OFF 时 selfheal_enabled_from_env=False
V10  COCO_SELFHEAL=1 才注入 — env helper
V11  真机模式（COCO_REAL_MACHINE=1）真调 apply；sim 模式 dry-run（且不消耗 giveup 配额）
V12  回归 infra-005 V1-V12 全 PASS （由 init.sh 之外的 verify_infra_005.py 跑；这里仅 import 不冲突 + 行为不退化的烟囱）
V13  sim → 真机切换：sim N 次 dry-run 不进 latch；切真机仍可正常 attempt max_attempts 次（L1-b rework gate）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.logging_setup import AUTHORITATIVE_COMPONENTS  # noqa: E402
from coco.infra.self_heal import (  # noqa: E402
    AudioReopenStrategy,
    ASRRestartStrategy,
    CameraReopenStrategy,
    BaseSelfHealStrategy,
    SelfHealRegistry,
    backoff_for,
    build_default_registry,
    selfheal_enabled_from_env,
    DEFAULT_BACKOFF_BASE_S,
    DEFAULT_BACKOFF_CAP_S,
    DEFAULT_MAX_ATTEMPTS,
)
from coco.infra.health_monitor import HealthMonitor  # noqa: E402


FAILURES: List[str] = []
PASSES: List[str] = []


def _check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {label}", flush=True)
        PASSES.append(label)
    else:
        print(f"  FAIL  {label}  {detail}", flush=True)
        FAILURES.append(f"{label} :: {detail}")


def _section(title: str) -> None:
    print(f"\n--- {title} ---", flush=True)


class _Recorder:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def __call__(self, topic: str, **payload: Any) -> None:
        self.events.append({"topic": topic, **payload})

    def topics(self) -> List[str]:
        return [e["topic"] for e in self.events]

    def by_topic(self, topic: str) -> List[Dict[str, Any]]:
        return [e for e in self.events if e["topic"] == topic]


# ---------------------------------------------------------------------------
# V1: 退避数列
# ---------------------------------------------------------------------------

def v1_backoff_sequence() -> None:
    _section("V1: 指数退避数列 (5/10/20/40/80, cap 120)")
    # 零 jitter — 用 mid-point rand
    seq = [backoff_for(i, jitter=0.0) for i in range(7)]
    expected = [5.0, 10.0, 20.0, 40.0, 80.0, 120.0, 120.0]
    _check("数列对齐", seq == expected, f"got={seq}")
    _check("cap 不被突破", max(seq) <= DEFAULT_BACKOFF_CAP_S)
    _check("base 正确", abs(seq[0] - DEFAULT_BACKOFF_BASE_S) < 1e-6)


# ---------------------------------------------------------------------------
# V2: jitter 范围
# ---------------------------------------------------------------------------

def v2_jitter_in_range() -> None:
    _section("V2: jitter 在 ±10% 内")
    # 大量采样：jitter=0.10 时所有 backoff 都在 [0.9*raw, 1.1*raw]
    raws = [5.0, 10.0, 20.0, 40.0, 80.0, 120.0]
    all_in = True
    for idx, raw in enumerate(raws):
        for _ in range(100):
            v = backoff_for(idx, jitter=0.10)
            if not (raw * 0.9 - 1e-9 <= v <= raw * 1.1 + 1e-9):
                all_in = False
                break
        if not all_in:
            break
    _check("100x 采样全部在 ±10% 内", all_in)
    # 确定性 rand_fn (lo+hi)/2 = 1.0 → 与零 jitter 等价
    v = backoff_for(0, jitter=0.10, rand_fn=lambda a, b: (a + b) / 2)
    _check("rand_fn 注入生效（mid → 与 0 jitter 等价）", abs(v - 5.0) < 1e-6)


# ---------------------------------------------------------------------------
# V3 / V4: attempts 上限 + giveup latch
# ---------------------------------------------------------------------------

class _FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def v3_v4_attempts_giveup() -> None:
    _section("V3/V4: max_attempts → giveup latch + 之后不再重试")
    clk = _FakeClock()
    rec = _Recorder()
    reg = SelfHealRegistry(
        is_real_machine_fn=lambda: True,  # 真机：真调 apply
        emit_fn=rec,
        now_fn=clk,
        rand_fn=lambda a, b: (a + b) / 2,
    )
    calls = {"n": 0}

    def fake_fn(**kw: Any) -> bool:
        calls["n"] += 1
        return False  # 永远失败

    strat = BaseSelfHealStrategy(
        name="t1",
        failure_kinds={"fk1"},
        cooldown_s=0.0,  # 不被 cooldown 拦
        max_attempts=3,
        reopen_fn=fake_fn,
    )
    reg.register(strat)

    # 跑 5 次 dispatch；前 3 次 attempt，第 4 次进 giveup latch（attempts >= max），其后 no-op
    for _ in range(5):
        clk.advance(1.0)
        reg.dispatch("fk1", {})

    _check("apply 实际被调 3 次", calls["n"] == 3, f"got={calls['n']}")
    _check("attempts (observed) 计数 = 3", reg.stats.per_strategy["t1"].attempts == 3)
    _check("real_attempts 计数 = 3（真机路径推进）", reg.stats.per_strategy["t1"].real_attempts == 3)
    _check("failed 计数 = 3", reg.stats.per_strategy["t1"].failed == 3)
    _check("giveup latch 已置位", reg.stats.per_strategy["t1"].giveup is True)
    _check("giveup_after_max stat +1", reg.stats.giveup_after_max >= 1)
    # V4: latch 后再 dispatch 不该再增 attempts
    before = reg.stats.per_strategy["t1"].attempts
    reg.dispatch("fk1", {})
    _check("giveup 后 attempts 不再增", reg.stats.per_strategy["t1"].attempts == before)
    # reset_strategy 解锁
    _check("reset_strategy 返回 True", reg.reset_strategy("t1") is True)
    _check("reset 后 giveup=False", reg.stats.per_strategy["t1"].giveup is False)
    _check("reset 后 real_attempts=0", reg.stats.per_strategy["t1"].real_attempts == 0)


# ---------------------------------------------------------------------------
# V5: AudioReopenStrategy 真机 fake reopen_fn 计数
# ---------------------------------------------------------------------------

def v5_audio_strategy() -> None:
    _section("V5: AudioReopenStrategy reopen_fn 被调用")
    calls = {"n": 0}

    def fake_audio_reopen(**kw: Any) -> bool:
        calls["n"] += 1
        return True

    rec = _Recorder()
    clk = _FakeClock()
    reg = SelfHealRegistry(is_real_machine_fn=lambda: True, emit_fn=rec, now_fn=clk)
    reg.register(AudioReopenStrategy(reopen_fn=fake_audio_reopen))

    reg.dispatch("audio_stream_lost", {})
    _check("audio_stream_lost → audio_reopen 命中", calls["n"] == 1)
    # 其他 failure_kind 不该被命中
    clk.advance(1000)  # 跨 cooldown
    reg.dispatch("foobar", {})
    _check("unknown failure_kind 不触发", calls["n"] == 1)
    _check("no_strategy_total 计数 +1", reg.stats.no_strategy_total == 1)
    _check("self_heal.success emit 出现", len(rec.by_topic("self_heal.success")) == 1)


# ---------------------------------------------------------------------------
# V6: ASRRestartStrategy 连续失败计数
# ---------------------------------------------------------------------------

def v6_asr_strategy() -> None:
    _section("V6: ASRRestartStrategy 连续失败 N → fail 计数")
    calls = {"n": 0}

    def fake_asr(**kw: Any) -> bool:
        calls["n"] += 1
        return False  # 持续失败

    rec = _Recorder()
    clk = _FakeClock()
    reg = SelfHealRegistry(is_real_machine_fn=lambda: True, emit_fn=rec, now_fn=clk)
    reg.register(ASRRestartStrategy(
        reopen_fn=fake_asr, cooldown_s=0.0, max_attempts=DEFAULT_MAX_ATTEMPTS,
    ))
    for _ in range(DEFAULT_MAX_ATTEMPTS + 2):
        clk.advance(1.0)
        reg.dispatch("asr_latency_high", {})
    _check(
        f"reopen_fn 被调 {DEFAULT_MAX_ATTEMPTS} 次后进 giveup",
        calls["n"] == DEFAULT_MAX_ATTEMPTS,
        f"got={calls['n']}",
    )
    _check("failed 计数等于 max_attempts", reg.stats.per_strategy["asr_restart"].failed == DEFAULT_MAX_ATTEMPTS)
    _check("self_heal.giveup emit", len(rec.by_topic("self_heal.giveup")) >= 1)


# ---------------------------------------------------------------------------
# V7: CameraReopenStrategy
# ---------------------------------------------------------------------------

def v7_camera_strategy() -> None:
    _section("V7: CameraReopenStrategy fake reopen_fn 计数")
    calls = {"n": 0}

    def fake_cam(**kw: Any) -> bool:
        calls["n"] += 1
        return True

    rec = _Recorder()
    clk = _FakeClock()
    reg = SelfHealRegistry(is_real_machine_fn=lambda: True, emit_fn=rec, now_fn=clk)
    reg.register(CameraReopenStrategy(reopen_fn=fake_cam))

    reg.dispatch("camera_dead", {})
    clk.advance(1000)  # 跨 cooldown
    reg.dispatch("camera_read_none", {})
    _check("两个 camera failure_kind 都命中", calls["n"] == 2, f"got={calls['n']}")


# ---------------------------------------------------------------------------
# V8: 与 infra-005 daemon 自愈不冲突
# ---------------------------------------------------------------------------

def v8_no_conflict_with_infra005() -> None:
    _section("V8: 与 infra-005 daemon 自愈不冲突")
    rec_hm = _Recorder()
    rec_sh = _Recorder()
    clk = _FakeClock()

    reg = build_default_registry(
        is_real_machine_fn=lambda: True, emit_fn=rec_sh, now_fn=clk,
    )

    # 用 fake heartbeat probe 让 daemon silent，并注入 fake restart_fn
    restart_calls = {"n": 0}

    def fake_restart() -> Any:
        restart_calls["n"] += 1
        return None

    hm = HealthMonitor(
        tick_s=1.0,
        daemon_silence_threshold_s=1.0,
        restart_cooldown_s=0.0,
        max_restart_retries=5,
        daemon_heartbeat_probe=lambda: None,
        stream_active_probe=None,
        daemon_restart_fn=fake_restart,
        is_real_machine_fn=lambda: False,  # sim → 真重启 daemon
        emit_fn=rec_hm,
        now_fn=clk,
        self_heal_registry=reg,
    )
    hm.tick_once()
    clk.advance(2.0)
    hm.tick_once()
    # daemon component degraded：但 daemon 不映射到任何 failure_kind → 不应 dispatch
    daemon_dispatch_topics = [t for t in rec_sh.topics() if t.startswith("self_heal.")]
    # 可能有 self_heal.no_strategy？不应该有：因为 _component_to_failure_kind('daemon', ...) 返回 None
    _check("daemon degraded 不触发 self_heal 路径", daemon_dispatch_topics == [], f"got={daemon_dispatch_topics}")
    _check("infra-005 daemon restart 正常被调", restart_calls["n"] >= 1)


# ---------------------------------------------------------------------------
# V9 / V10: default-OFF / env helper
# ---------------------------------------------------------------------------

def v9_v10_env_default_off() -> None:
    _section("V9/V10: default-OFF + COCO_SELFHEAL env helper")
    _check("env 空 → False", selfheal_enabled_from_env({}) is False)
    _check("COCO_SELFHEAL=0 → False", selfheal_enabled_from_env({"COCO_SELFHEAL": "0"}) is False)
    _check("COCO_SELFHEAL=1 → True", selfheal_enabled_from_env({"COCO_SELFHEAL": "1"}) is True)
    _check("COCO_SELFHEAL=on → True", selfheal_enabled_from_env({"COCO_SELFHEAL": "on"}) is True)
    _check("AUTHORITATIVE_COMPONENTS 含 self_heal", "self_heal" in AUTHORITATIVE_COMPONENTS)

    # HealthMonitor 未注入 registry 时 self_heal 路径 no-op：行为同 infra-005
    rec = _Recorder()
    clk = _FakeClock()
    hm = HealthMonitor(
        tick_s=1.0,
        daemon_silence_threshold_s=1.0,
        restart_cooldown_s=0.0,
        max_restart_retries=2,
        daemon_heartbeat_probe=lambda: None,
        stream_active_probe=lambda: False,  # 触发 sounddevice degraded
        daemon_restart_fn=lambda: None,
        is_real_machine_fn=lambda: False,
        emit_fn=rec,
        now_fn=clk,
        # 不传 self_heal_registry
    )
    hm.tick_once()
    sh_topics = [t for t in rec.topics() if t.startswith("self_heal.")]
    _check("未注入 registry 时不 emit self_heal.*", sh_topics == [])


# ---------------------------------------------------------------------------
# V11: real-machine gate — sim dry-run / 真机 apply
# ---------------------------------------------------------------------------

def v11_real_machine_gate() -> None:
    _section("V11: real-machine gate (sim 走 dry-run, 真机真调 apply)")
    calls = {"n": 0}

    def fake_fn(**kw: Any) -> bool:
        calls["n"] += 1
        return True

    # sim
    rec_sim = _Recorder()
    clk = _FakeClock()
    reg_sim = SelfHealRegistry(is_real_machine_fn=lambda: False, emit_fn=rec_sim, now_fn=clk)
    reg_sim.register(AudioReopenStrategy(reopen_fn=fake_fn))
    reg_sim.dispatch("audio_stream_lost", {})
    _check("sim 模式 apply 不被真调", calls["n"] == 0)
    _check("sim 模式 emit self_heal.dry_run", len(rec_sim.by_topic("self_heal.dry_run")) == 1)
    _check("sim 模式 dry_run_total += 1", reg_sim.stats.dry_run_total == 1)
    # attempts (observed) 仍计数；real_attempts 不动
    _check("sim 模式 attempts (observed) +1", reg_sim.stats.per_strategy["audio_reopen"].attempts == 1)
    _check("sim 模式 real_attempts = 0（不消耗 giveup 配额）",
           reg_sim.stats.per_strategy["audio_reopen"].real_attempts == 0)
    _check("sim 模式不进 giveup latch",
           reg_sim.stats.per_strategy["audio_reopen"].giveup is False)

    # 真机
    calls["n"] = 0
    rec_real = _Recorder()
    clk2 = _FakeClock()
    reg_real = SelfHealRegistry(is_real_machine_fn=lambda: True, emit_fn=rec_real, now_fn=clk2)
    reg_real.register(AudioReopenStrategy(reopen_fn=fake_fn))
    reg_real.dispatch("audio_stream_lost", {})
    _check("真机模式 apply 被真调", calls["n"] == 1)
    _check("真机模式 emit self_heal.success", len(rec_real.by_topic("self_heal.success")) == 1)
    _check("真机模式 dry_run_total = 0", reg_real.stats.dry_run_total == 0)


# ---------------------------------------------------------------------------
# V12: 回归 infra-005 烟囱 — import + 基本行为不退化
# ---------------------------------------------------------------------------

def v12_regression_infra005() -> None:
    _section("V12: 回归 infra-005 不退化（未注入 registry 行为不变）")
    rec = _Recorder()
    clk = _FakeClock()
    restart_calls = {"n": 0}
    hm = HealthMonitor(
        tick_s=1.0,
        daemon_silence_threshold_s=1.0,
        restart_cooldown_s=0.0,
        max_restart_retries=2,
        daemon_heartbeat_probe=lambda: None,
        stream_active_probe=None,
        daemon_restart_fn=lambda: restart_calls.__setitem__("n", restart_calls["n"] + 1),
        is_real_machine_fn=lambda: False,
        emit_fn=rec,
        now_fn=clk,
    )
    hm.tick_once()
    clk.advance(2.0)
    hm.tick_once()
    clk.advance(2.0)
    hm.tick_once()
    _check("infra-005 daemon restart 被调 (>=1)", restart_calls["n"] >= 1)
    _check("infra-005 emit health.degraded", len(rec.by_topic("health.degraded")) >= 1)
    _check("无任何 self_heal.* emit", not any(t.startswith("self_heal.") for t in rec.topics()))

    # cooldown 检测：同策略冷却内不重复 apply
    _section("  bonus: SelfHealRegistry cooldown_skip")
    clk2 = _FakeClock()
    rec2 = _Recorder()
    reg = SelfHealRegistry(is_real_machine_fn=lambda: True, emit_fn=rec2, now_fn=clk2)
    n = {"k": 0}
    reg.register(BaseSelfHealStrategy(
        name="cd", failure_kinds={"fk"}, cooldown_s=10.0, max_attempts=10,
        reopen_fn=lambda **kw: (n.__setitem__("k", n["k"] + 1) or True),
    ))
    reg.dispatch("fk", {})
    clk2.advance(1.0)  # 仍在 cooldown 内
    reg.dispatch("fk", {})
    _check("cooldown 内 apply 不重复", n["k"] == 1, f"got={n['k']}")
    _check("cooldown_skipped_total += 1", reg.stats.cooldown_skipped_total == 1)
    clk2.advance(20.0)  # 跨 cooldown
    reg.dispatch("fk", {})
    _check("跨 cooldown 后可再 apply", n["k"] == 2)


# ---------------------------------------------------------------------------
# V13: sim → 真机切换（L1-b rework gate）
# ---------------------------------------------------------------------------

def v13_sim_dryrun_does_not_starve_real() -> None:
    _section("V13: sim N 次 dry-run 不进 latch；切真机仍可 attempt max_attempts 次")
    calls = {"n": 0}

    def fake_fn(**kw: Any) -> bool:
        calls["n"] += 1
        return True

    rec = _Recorder()
    clk = _FakeClock()
    # is_real 可切换：先 False（sim），后续翻 True
    mode = {"real": False}
    reg = SelfHealRegistry(
        is_real_machine_fn=lambda: mode["real"],
        emit_fn=rec,
        now_fn=clk,
    )
    strat = BaseSelfHealStrategy(
        name="t13",
        failure_kinds={"fk13"},
        cooldown_s=0.0,  # 不被 cooldown 拦
        max_attempts=DEFAULT_MAX_ATTEMPTS,  # 5
        reopen_fn=fake_fn,
    )
    reg.register(strat)

    # 1) sim 模式跑 10 次（远超 max_attempts）
    for _ in range(10):
        clk.advance(1.0)
        reg.dispatch("fk13", {})

    st = reg.stats.per_strategy["t13"]
    _check("sim 跑 10 次 apply 未被真调", calls["n"] == 0)
    _check("sim 跑 10 次 dry_run_total = 10", reg.stats.dry_run_total == 10)
    _check("sim 跑 10 次 attempts (observed) = 10", st.attempts == 10)
    _check("sim 跑 10 次 real_attempts = 0", st.real_attempts == 0)
    _check("sim 跑 10 次 giveup 仍为 False（未消耗配额）", st.giveup is False)
    _check("sim 跑 10 次 giveup_after_max = 0", reg.stats.giveup_after_max == 0)

    # 2) 切到真机模式（运维场景：env 切换 / 部署到真机）
    mode["real"] = True
    # 跨 cooldown（cooldown_s=0 ⇒ 立即可行）
    clk.advance(1.0)

    # 真机模式应能 attempt 5 次（max_attempts）后才进 latch
    for _ in range(DEFAULT_MAX_ATTEMPTS + 2):
        clk.advance(1.0)
        reg.dispatch("fk13", {})

    _check(
        f"切真机后 apply 被真调 {DEFAULT_MAX_ATTEMPTS} 次",
        calls["n"] == DEFAULT_MAX_ATTEMPTS,
        f"got={calls['n']}",
    )
    _check("切真机后 real_attempts = max_attempts",
           st.real_attempts == DEFAULT_MAX_ATTEMPTS)
    _check("切真机后 giveup 置位", st.giveup is True)
    _check("切真机后 giveup_after_max += 1", reg.stats.giveup_after_max >= 1)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    print("== verify_infra_007 (SelfHealRegistry) ==", flush=True)

    v1_backoff_sequence()
    v2_jitter_in_range()
    v3_v4_attempts_giveup()
    v5_audio_strategy()
    v6_asr_strategy()
    v7_camera_strategy()
    v8_no_conflict_with_infra005()
    v9_v10_env_default_off()
    v11_real_machine_gate()
    v12_regression_infra005()
    v13_sim_dryrun_does_not_starve_real()

    print(f"\n== summary: PASS={len(PASSES)} FAIL={len(FAILURES)} ==", flush=True)
    if FAILURES:
        for f in FAILURES:
            print(f"  - {f}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
