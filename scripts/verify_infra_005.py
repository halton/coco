"""verify_infra_005 — HealthMonitor: daemon 自愈 + 多源观测.

V1   COCO_HEALTH 默认 OFF：health_enabled_from_env({}) == False
V2   sim daemon 60s 无心跳 → restart 被触发 + cooldown 期内不重复触发
V3   ASR p95 超阈值 → emit health.degraded，恢复 → emit health.recovered
V4   主线程 watchdog 卡 lag → emit health.tick_lag + health.degraded(watchdog)
V5   真机 mode（is_real_machine_fn=True）→ daemon 无心跳只 emit degraded，不 restart
V6   max retry=3 后停止重启 + emit health.daemon_giveup
V7   stop() 清理 ring buffer + tick 线程 join
V8   ring buffer 上限 200 条：写入 300 条后 latency_p50_p95 不读 OOM 且窗口截断
V9   AUTHORITATIVE_COMPONENTS 含 "health"
V10  sounddevice stream_active_probe=False → emit degraded；True → recovered
V11  emit 风暴防抖：tick 多次 degraded 同 reason 只 emit 一次
V12  daemon child handle：restart_fn 返回 fake child；stop() 调用其 terminate
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.logging_setup import AUTHORITATIVE_COMPONENTS  # noqa: E402
from coco.infra.health_monitor import (  # noqa: E402
    HealthMonitor,
    build_health_monitor,
    health_enabled_from_env,
    DEFAULT_LATENCY_WINDOW,
)


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
    """收集所有 emit；emit_fn 注入用。"""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def __call__(self, topic: str, **payload: Any) -> None:
        self.events.append({"topic": topic, **payload})

    def topics(self) -> List[str]:
        return [e["topic"] for e in self.events]

    def by_topic(self, topic: str) -> List[Dict[str, Any]]:
        return [e for e in self.events if e["topic"] == topic]


# ---------------------------------------------------------------------------
# V1
# ---------------------------------------------------------------------------


def v1_default_off() -> None:
    _section("V1: COCO_HEALTH 默认 OFF")
    _check("env 空 → disabled", health_enabled_from_env({}) is False)
    _check("env COCO_HEALTH=0 → disabled", health_enabled_from_env({"COCO_HEALTH": "0"}) is False)
    _check("env COCO_HEALTH=1 → enabled", health_enabled_from_env({"COCO_HEALTH": "1"}) is True)
    _check("env COCO_HEALTH=true → enabled", health_enabled_from_env({"COCO_HEALTH": "true"}) is True)


# ---------------------------------------------------------------------------
# V2 / V6: daemon restart + cooldown + giveup
# ---------------------------------------------------------------------------


def v2_daemon_restart_with_cooldown() -> None:
    _section("V2: daemon 60s 无心跳 → restart + cooldown 期内不重复触发")

    rec = _Recorder()
    fake_now = [1000.0]
    restart_calls = []

    def fake_restart() -> None:
        restart_calls.append(fake_now[0])

    hm = HealthMonitor(
        tick_s=5.0,
        daemon_silence_threshold_s=60.0,
        restart_cooldown_s=30.0,
        max_restart_retries=3,
        daemon_heartbeat_probe=lambda: None,  # 永远静默
        daemon_restart_fn=fake_restart,
        is_real_machine_fn=lambda: False,
        emit_fn=rec,
        now_fn=lambda: fake_now[0],
    )
    # tick 1：触发 restart attempt #1
    hm.tick_once()
    _check("第一次 silence → restart_attempted emit", "health.restart_attempted" in rec.topics())
    _check("第一次 restart 被调用", len(restart_calls) == 1, f"calls={restart_calls}")

    # cooldown 内（+10s < 30s cooldown）再 tick：不应再 restart
    fake_now[0] += 10.0
    hm.tick_once()
    _check("cooldown 内不重复 restart", len(restart_calls) == 1, f"calls={restart_calls}")

    # cooldown 过后（+25s 累计 35s > 30s）再 tick：应 restart attempt #2
    fake_now[0] += 25.0
    hm.tick_once()
    _check("cooldown 过后触发 attempt #2", len(restart_calls) == 2, f"calls={restart_calls}")
    _check(
        "degraded emit 含 daemon",
        any(
            e.get("component") == "daemon"
            for e in rec.by_topic("health.degraded")
        ),
    )


# ---------------------------------------------------------------------------
# V3: ASR p95
# ---------------------------------------------------------------------------


def v3_asr_p95_degrade_recover() -> None:
    _section("V3: ASR p95 超阈值 → degrade；恢复 → recover")

    rec = _Recorder()
    hm = HealthMonitor(
        tick_s=5.0,
        asr_p95_threshold_ms=1000.0,
        daemon_heartbeat_probe=lambda: time.time(),  # daemon 永远健康
        is_real_machine_fn=lambda: False,
        emit_fn=rec,
    )
    # 喂入 50 条 p95 远超阈值的延迟
    for _ in range(50):
        hm.record_latency("asr", 2500.0)
    hm.tick_once()
    degrades = [
        e for e in rec.by_topic("health.degraded")
        if e.get("component") == "asr_latency"
    ]
    _check("asr_latency degraded emit", len(degrades) >= 1, f"events={rec.topics()}")
    if degrades:
        _check(
            "degraded payload 含 p95/p50 + threshold",
            "value" in degrades[0] and "threshold" in degrades[0],
            f"payload={degrades[0]}",
        )

    # 清空 → 喂入低延迟 → recover
    rec.events.clear()
    # 由于 deque(maxlen=200) 仍含旧高延迟，需要喂入 N>=200 的低延迟把窗口刷掉
    # 实现上：record_latency 用 deque(maxlen=N)，先喂 200 低延迟即可全替换
    for _ in range(220):
        hm.record_latency("asr", 100.0)
    hm.tick_once()
    recovers = [
        e for e in rec.by_topic("health.recovered")
        if e.get("component") == "asr_latency"
    ]
    _check("asr_latency recovered emit", len(recovers) >= 1, f"events={rec.topics()}")


# ---------------------------------------------------------------------------
# V4: watchdog
# ---------------------------------------------------------------------------


def v4_watchdog_tick_lag() -> None:
    _section("V4: 主线程 watchdog 卡 lag → emit tick_lag + degraded(watchdog)")

    rec = _Recorder()
    fake_now = [1000.0]
    hm = HealthMonitor(
        tick_s=5.0,
        watchdog_lag_threshold_s=3.0,
        daemon_heartbeat_probe=lambda: fake_now[0],
        is_real_machine_fn=lambda: False,
        emit_fn=rec,
        now_fn=lambda: fake_now[0],
    )
    # 第一次 tick：建立 last_tick_ts，不触发 lag（last_tick_ts=0）
    hm.tick_once()
    rec.events.clear()
    # 模拟卡了 15s（tick=5s，预期间隔 5s；实际 15s 即 lag=10s > 3s threshold）
    fake_now[0] += 15.0
    hm.tick_once()
    _check(
        "health.tick_lag emit",
        "health.tick_lag" in rec.topics(),
        f"events={rec.topics()}",
    )
    watchdog_degrades = [
        e for e in rec.by_topic("health.degraded")
        if e.get("component") == "watchdog"
    ]
    _check("watchdog degraded emit", len(watchdog_degrades) >= 1, f"events={rec.topics()}")


# ---------------------------------------------------------------------------
# V5: 真机 mode 仅告警不重启
# ---------------------------------------------------------------------------


def v5_real_machine_no_restart() -> None:
    _section("V5: 真机模式 → daemon 无心跳只告警不 restart")

    rec = _Recorder()
    restart_calls = []

    def fake_restart() -> None:
        restart_calls.append(1)

    hm = HealthMonitor(
        tick_s=5.0,
        daemon_silence_threshold_s=60.0,
        daemon_heartbeat_probe=lambda: None,
        daemon_restart_fn=fake_restart,
        is_real_machine_fn=lambda: True,  # 真机
        emit_fn=rec,
        now_fn=lambda: 1000.0,
    )
    hm.tick_once()
    _check("真机模式 restart 未被调用", len(restart_calls) == 0)
    _check(
        "真机模式仅 emit health.degraded(daemon)",
        any(e.get("component") == "daemon" for e in rec.by_topic("health.degraded")),
    )
    _check("真机模式无 restart_attempted emit", "health.restart_attempted" not in rec.topics())


# ---------------------------------------------------------------------------
# V6: max retry → giveup
# ---------------------------------------------------------------------------


def v6_max_retry_giveup() -> None:
    _section("V6: max retry=3 后停止重启 + emit daemon_giveup")

    rec = _Recorder()
    fake_now = [1000.0]
    restart_calls = []

    def fake_restart() -> None:
        restart_calls.append(fake_now[0])

    hm = HealthMonitor(
        tick_s=5.0,
        daemon_silence_threshold_s=60.0,
        restart_cooldown_s=30.0,
        max_restart_retries=3,
        daemon_heartbeat_probe=lambda: None,
        daemon_restart_fn=fake_restart,
        is_real_machine_fn=lambda: False,
        emit_fn=rec,
        now_fn=lambda: fake_now[0],
    )
    # tick 3 次，每次推进 35s（绕过 cooldown）
    for i in range(3):
        hm.tick_once()
        fake_now[0] += 35.0
    _check("restart 调用 3 次", len(restart_calls) == 3, f"calls={restart_calls}")
    # 第 4 次 tick：应触发 giveup
    hm.tick_once()
    _check(
        "health.daemon_giveup emit",
        "health.daemon_giveup" in rec.topics(),
        f"events={rec.topics()}",
    )
    _check("restart 不再被调用", len(restart_calls) == 3, f"calls={restart_calls}")
    # 第 5 次 tick：依然不 restart 且 giveup 不重复 emit
    rec.events.clear()
    fake_now[0] += 100.0
    hm.tick_once()
    _check(
        "giveup 后不再 emit daemon_giveup",
        "health.daemon_giveup" not in rec.topics(),
    )
    _check("giveup 后 restart 不再被调用", len(restart_calls) == 3)


# ---------------------------------------------------------------------------
# V7: stop() 清理
# ---------------------------------------------------------------------------


def v7_stop_clean() -> None:
    _section("V7: stop() 清理 ring buffer + tick 线程 join")

    rec = _Recorder()
    hm = HealthMonitor(
        tick_s=0.2,  # 快 tick 便于触发
        daemon_heartbeat_probe=lambda: time.time(),
        is_real_machine_fn=lambda: False,
        emit_fn=rec,
    )
    for _ in range(50):
        hm.record_latency("asr", 100.0)
        hm.record_latency("llm", 200.0)
    hm.start()
    _check("线程 running", hm.is_running())
    time.sleep(0.6)
    hm.stop(timeout=2.0)
    _check("线程 stopped", not hm.is_running())
    # ring buffer 已清空
    _check(
        "ring buffer asr 清空",
        len(hm._latencies["asr"]) == 0,
        f"asr len={len(hm._latencies['asr'])}",
    )
    _check(
        "ring buffer llm 清空",
        len(hm._latencies["llm"]) == 0,
        f"llm len={len(hm._latencies['llm'])}",
    )
    _check("stats.ticks > 0", hm.stats.ticks > 0, f"ticks={hm.stats.ticks}")


# ---------------------------------------------------------------------------
# V8: ring buffer 上限
# ---------------------------------------------------------------------------


def v8_ring_buffer_cap() -> None:
    _section("V8: ring buffer 上限 200 条")

    hm = HealthMonitor(
        latency_window_n=DEFAULT_LATENCY_WINDOW,
        daemon_heartbeat_probe=lambda: time.time(),
        is_real_machine_fn=lambda: False,
        emit_fn=lambda *a, **kw: None,
    )
    for i in range(300):
        hm.record_latency("asr", float(i))
    _check(
        "asr buffer == 200 (FIFO 丢旧)",
        len(hm._latencies["asr"]) == 200,
        f"len={len(hm._latencies['asr'])}",
    )
    samples = list(hm._latencies["asr"])
    _check("最老条目被淘汰", samples[0] == 100.0, f"first={samples[0]}")
    _check("最新条目保留", samples[-1] == 299.0, f"last={samples[-1]}")
    # p95 计算正常工作
    p = hm.latency_p50_p95("asr")
    _check("p50/p95 可计算", p is not None and p[0] > 0 and p[1] > 0, f"p={p}")


# ---------------------------------------------------------------------------
# V9: AUTHORITATIVE_COMPONENTS
# ---------------------------------------------------------------------------


def v9_authoritative_components() -> None:
    _section("V9: AUTHORITATIVE_COMPONENTS 含 'health'")
    _check(
        "health 在 AUTHORITATIVE_COMPONENTS",
        "health" in AUTHORITATIVE_COMPONENTS,
        f"set={sorted(AUTHORITATIVE_COMPONENTS)}",
    )


# ---------------------------------------------------------------------------
# V10: sounddevice stream
# ---------------------------------------------------------------------------


def v10_sounddevice_stream() -> None:
    _section("V10: sounddevice stream_active_probe False → degrade; True → recover")

    rec = _Recorder()
    stream_state = [False]
    hm = HealthMonitor(
        tick_s=5.0,
        daemon_heartbeat_probe=lambda: time.time(),
        stream_active_probe=lambda: stream_state[0],
        is_real_machine_fn=lambda: False,
        emit_fn=rec,
    )
    hm.tick_once()
    sd_degrades = [
        e for e in rec.by_topic("health.degraded")
        if e.get("component") == "sounddevice"
    ]
    _check("sounddevice degraded emit (active=False)", len(sd_degrades) >= 1, f"events={rec.topics()}")

    rec.events.clear()
    stream_state[0] = True
    hm.tick_once()
    sd_recovers = [
        e for e in rec.by_topic("health.recovered")
        if e.get("component") == "sounddevice"
    ]
    _check("sounddevice recovered emit (active=True)", len(sd_recovers) >= 1, f"events={rec.topics()}")


# ---------------------------------------------------------------------------
# V11: 风暴防抖
# ---------------------------------------------------------------------------


def v11_dedup_storm() -> None:
    _section("V11: 多 tick 同 reason 只 emit 一次 degraded")

    rec = _Recorder()
    hm = HealthMonitor(
        tick_s=5.0,
        daemon_silence_threshold_s=60.0,
        restart_cooldown_s=300.0,  # 防止 restart 路径污染 emits
        daemon_heartbeat_probe=lambda: None,
        daemon_restart_fn=lambda: None,
        is_real_machine_fn=lambda: True,  # 真机 → 仅 emit 不 restart，断言更纯净
        emit_fn=rec,
        now_fn=lambda: 1000.0,
    )
    hm.tick_once()
    hm.tick_once()
    hm.tick_once()
    daemon_degrades = [
        e for e in rec.by_topic("health.degraded")
        if e.get("component") == "daemon"
    ]
    _check(
        "3 tick 同 reason 只 1 次 emit",
        len(daemon_degrades) == 1,
        f"degrades={daemon_degrades}",
    )


# ---------------------------------------------------------------------------
# V12: child handle terminate on stop
# ---------------------------------------------------------------------------


def v12_child_handle_terminate() -> None:
    _section("V12: restart_fn 返回 child handle，stop() terminate")

    terminated = []

    class FakeChild:
        def terminate(self) -> None:
            terminated.append(1)

    rec = _Recorder()

    def fake_restart() -> FakeChild:
        return FakeChild()

    hm = HealthMonitor(
        tick_s=5.0,
        daemon_silence_threshold_s=60.0,
        restart_cooldown_s=30.0,
        daemon_heartbeat_probe=lambda: None,
        daemon_restart_fn=fake_restart,
        is_real_machine_fn=lambda: False,
        emit_fn=rec,
        now_fn=lambda: 1000.0,
    )
    hm.tick_once()
    _check("child handle 已保存", hm._daemon_child is not None)
    hm.stop(timeout=1.0)
    _check("stop() 调用了 child.terminate", len(terminated) == 1, f"calls={terminated}")
    _check("stop() 后 child handle 清空", hm._daemon_child is None)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=== verify_infra_005: HealthMonitor (daemon 自愈 + 多源观测) ===")
    v1_default_off()
    v2_daemon_restart_with_cooldown()
    v3_asr_p95_degrade_recover()
    v4_watchdog_tick_lag()
    v5_real_machine_no_restart()
    v6_max_retry_giveup()
    v7_stop_clean()
    v8_ring_buffer_cap()
    v9_authoritative_components()
    v10_sounddevice_stream()
    v11_dedup_storm()
    v12_child_handle_terminate()

    print(f"\nPASS={len(PASSES)} FAIL={len(FAILURES)}")
    # 落 evidence
    evidence_dir = ROOT / "evidence" / "infra-005"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "infra-005",
        "pass": len(PASSES),
        "fail": len(FAILURES),
        "checks": PASSES,
        "failures": FAILURES,
    }
    (evidence_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if FAILURES:
        for f in FAILURES:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
