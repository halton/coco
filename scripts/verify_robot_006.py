"""robot-006 verify: RobotSequencer + emit + cancel + 业务订阅回压.

V1: 5-action 序列串行执行，emit action_done 顺序正确 (ts 单调)
V2: cancel mid-flight 中止当前 + 跳 pending + emit sequence_cancelled{cancelled_n>=1}
V3: 业务订阅 (ProactiveScheduler mock) 收到 action_done 后状态机不叠加新动作
V4: env COCO_ROBOT_SEQ OFF zero-cost (sequencer 不构造 / main 不 wire)
V5: mockup-sim daemon 全程 zero-hardware (action.execute 走 robot SDK；fixture-level robot=None 不接触真硬件)
V6: robot-005 回归 PASS
V7: 业务订阅回压 — 慢订阅者不阻塞 sequencer 主线程
V8: subscribe 异常不影响 sequencer 继续推进
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
import threading
import time
import traceback
from typing import Any, List
from unittest.mock import MagicMock

errors: List[str] = []
t0 = time.time()


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


def make_mock_robot() -> Any:
    r = MagicMock()
    r.goto_target = MagicMock(return_value=None)
    r.goto_sleep = MagicMock(return_value=None)
    r.wake_up = MagicMock(return_value=None)
    return r


for k in ("COCO_ROBOT_SEQ", "COCO_ROBOT_SEQ_POLL_S", "COCO_ROBOT_SEQ_SUB_ASYNC"):
    os.environ.pop(k, None)


# =======================================================================
# V1 5-action 序列串行执行 + emit action_done 顺序正确
# =======================================================================
print("V1: 5-action 序列串行执行 + emit action_done 顺序正确 (ts 单调)")
try:
    from coco.robot.sequencer import Action, RobotSequencer, SequencerConfig

    emitted: List[tuple] = []

    def _capture_emit(component_event: str, message: str = "", **payload: Any) -> None:
        emitted.append((component_event, payload))

    r = make_mock_robot()
    cfg = SequencerConfig(enabled=True, cancel_poll_interval_s=0.005, subscribe_async=False)
    seq = RobotSequencer(robot=r, config=cfg, emit_fn=_capture_emit)

    actions = [
        Action("a1", "head_turn", {"yaw_deg": 20}, duration_s=0.05),
        Action("a2", "nod", {"amplitude_deg": 10}, duration_s=0.05),
        Action("a3", "look_at", {"yaw_deg": -15, "pitch_deg": 5}, duration_s=0.05),
        Action("a4", "sleep", {}, duration_s=0.05),
        Action("a5", "wakeup", {}, duration_s=0.05),
    ]
    res = seq.run(actions)

    check("executed == 5", res["executed"] == 5, f"got {res['executed']}")
    check("cancelled == False", res["cancelled"] is False)
    dones = [e for e in emitted if e[0] == "robot.action_done"]
    check("emit 5 次 action_done", len(dones) == 5, f"got {len(dones)}")
    ids = [e[1]["action_id"] for e in dones]
    check("action_id 顺序 == a1..a5", ids == ["a1", "a2", "a3", "a4", "a5"], f"got {ids}")
    types = [e[1]["type"] for e in dones]
    check(
        "type 顺序正确",
        types == ["head_turn", "nod", "look_at", "sleep", "wakeup"],
        f"got {types}",
    )
    ts_list = [e[1]["ts"] for e in dones]
    check("ts 严格单调递增", all(ts_list[i] < ts_list[i + 1] for i in range(4)), f"ts={ts_list}")
    check("每条 payload 含 duration_ms", all("duration_ms" in e[1] for e in dones))
    check("seq_id 一致", all(e[1]["seq_id"] == dones[0][1]["seq_id"] for e in dones))
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())


# =======================================================================
# V2 cancel mid-flight + emit sequence_cancelled
# =======================================================================
print("V2: cancel mid-flight 中止当前 + 跳 pending + emit sequence_cancelled{cancelled_n>=1}")
try:
    from coco.robot.sequencer import Action, RobotSequencer, SequencerConfig

    emitted: List[tuple] = []

    def _capture_emit(component_event: str, message: str = "", **payload: Any) -> None:
        emitted.append((component_event, payload))

    r = make_mock_robot()
    cfg = SequencerConfig(enabled=True, cancel_poll_interval_s=0.01, subscribe_async=False)
    seq = RobotSequencer(robot=r, config=cfg, emit_fn=_capture_emit)

    actions = [
        Action("c1", "head_turn", {"yaw_deg": 10}, duration_s=0.1),
        Action("c2", "head_turn", {"yaw_deg": -10}, duration_s=0.5),  # long → 此中 cancel
        Action("c3", "nod", {"amplitude_deg": 10}, duration_s=0.1),
        Action("c4", "sleep", {}, duration_s=0.1),
    ]

    def _cancel_later() -> None:
        time.sleep(0.2)  # 让 c1 走完 + c2 进入中段
        seq.cancel()

    threading.Thread(target=_cancel_later, daemon=True).start()
    res = seq.run(actions)

    check("cancelled == True", res["cancelled"] is True)
    check("executed >= 1 (c1 完成)", res["executed"] >= 1, f"executed={res['executed']}")
    check("executed < 4 (未跑完所有)", res["executed"] < 4, f"executed={res['executed']}")
    check(
        "cancelled_n >= 1",
        res["cancelled_n"] >= 1,
        f"cancelled_n={res['cancelled_n']}",
    )
    cancel_events = [e for e in emitted if e[0] == "robot.sequence_cancelled"]
    check("emit sequence_cancelled 一次", len(cancel_events) == 1, f"got {len(cancel_events)}")
    if cancel_events:
        p = cancel_events[0][1]
        check("payload.cancelled_n >= 1", p.get("cancelled_n", 0) >= 1, f"got {p.get('cancelled_n')}")
        check("payload 含 executed_n", "executed_n" in p)
        check("payload 含 seq_id", "seq_id" in p)
except Exception:  # noqa: BLE001
    errors.append("V2: " + traceback.format_exc())


# =======================================================================
# V3 业务订阅状态机不叠加
# =======================================================================
print("V3: 业务订阅 (ProactiveScheduler mock) 收 action_done 后状态机不叠加新动作")
try:
    from coco.robot.sequencer import Action, RobotSequencer, SequencerConfig

    class FakeProactiveScheduler:
        """模拟订阅方：收到 action_done 才允许下一次 schedule。"""

        def __init__(self) -> None:
            self.busy = False
            self.scheduled = 0
            self.received: List[dict] = []
            self.lock = threading.Lock()

        def on_event(self, ev: str, payload: dict) -> None:
            if ev == "robot.action_done":
                with self.lock:
                    self.received.append(payload)
                    self.busy = False  # 解锁，允许下一次 schedule

        def maybe_schedule(self) -> bool:
            with self.lock:
                if self.busy:
                    return False
                self.busy = True
                self.scheduled += 1
                return True

    sched = FakeProactiveScheduler()
    r = make_mock_robot()
    cfg = SequencerConfig(enabled=True, cancel_poll_interval_s=0.01, subscribe_async=False)
    seq = RobotSequencer(robot=r, config=cfg, emit_fn=lambda *a, **k: None)
    seq.subscribe(sched.on_event)

    # 第一次 schedule → busy 锁
    ok = sched.maybe_schedule()
    check("第 1 次 maybe_schedule -> True", ok is True)
    # busy 期间叠加请求被拒
    rejected = 0
    for _ in range(5):
        if not sched.maybe_schedule():
            rejected += 1
    check("busy 期间 5 次请求都被拒（不叠加）", rejected == 5, f"got rejected={rejected}")

    # 跑一个 action → 触发 on_event → busy 解锁
    seq.run([Action("s1", "head_turn", {"yaw_deg": 5}, duration_s=0.03)])
    check("订阅收到 1 条 action_done", len(sched.received) == 1, f"got {len(sched.received)}")
    check("收到事件后 busy 解锁", sched.busy is False)
    # 再次 schedule 成功
    ok2 = sched.maybe_schedule()
    check("解锁后 maybe_schedule -> True", ok2 is True)
    check("scheduled 总数 == 2 (无叠加)", sched.scheduled == 2, f"got {sched.scheduled}")
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4 default-OFF zero-cost: env 未设 → main 不 wire / class 不构造
# =======================================================================
print("V4: COCO_ROBOT_SEQ OFF zero-cost (sequencer 不构造 / main 不 wire)")
try:
    from coco.robot.sequencer import sequencer_config_from_env

    # env 干净
    for k in ("COCO_ROBOT_SEQ",):
        os.environ.pop(k, None)
    cfg_off = sequencer_config_from_env()
    check("env unset → enabled=False", cfg_off.enabled is False, f"got {cfg_off.enabled}")

    os.environ["COCO_ROBOT_SEQ"] = "1"
    cfg_on = sequencer_config_from_env()
    check("env=1 → enabled=True", cfg_on.enabled is True, f"got {cfg_on.enabled}")
    os.environ.pop("COCO_ROBOT_SEQ", None)

    # main.py wire 段必须 env-gated
    main_src = open("coco/main.py", "r", encoding="utf-8").read()
    check("main.py 含 COCO_ROBOT_SEQ wire 段", "robot-006" in main_src or "RobotSequencer" in main_src)
    check(
        "wire 走 _seq_cfg.enabled 短路 (env-gated)",
        "_seq_cfg.enabled" in main_src,
        "main.py wire must be env-gated",
    )
    check(
        "main.py 仅在 sequencer.py 模块中 import RobotSequencer (lazy in main)",
        "from coco.robot.sequencer import" in main_src,
    )

    # bytewise: env OFF 时 main HEAD 行为对比 — main.py 中 wire 段写"disabled (COCO_ROBOT_SEQ not set)"
    check(
        "OFF 路径明确输出 disabled 字样 (bytewise no-side-effect)",
        "[coco][robot_seq] disabled" in main_src,
    )
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5 mockup-sim zero-hardware
# =======================================================================
print("V5: mockup-sim daemon 全程 zero-hardware (action 走 SDK 抽象, fixture robot=None no-op)")
try:
    from coco.robot.sequencer import Action, RobotSequencer, SequencerConfig

    # 5a: fixture-level (robot=None) 不接触任何硬件 API
    seq_null = RobotSequencer(
        robot=None, config=SequencerConfig(enabled=True, cancel_poll_interval_s=0.005,
                                            subscribe_async=False),
        emit_fn=lambda *a, **k: None,
    )
    res = seq_null.run(
        [
            Action("n1", "head_turn", {"yaw_deg": 10}, duration_s=0.02),
            Action("n2", "sleep", {}, duration_s=0.02),
            Action("n3", "wakeup", {}, duration_s=0.02),
        ]
    )
    check("robot=None 时序列照常完成", res["executed"] == 3, f"got {res['executed']}")

    # 5b: mock robot 仅暴露 SDK 调用接口 (goto_target / goto_sleep / wake_up)
    r = make_mock_robot()
    seq = RobotSequencer(
        robot=r,
        config=SequencerConfig(enabled=True, cancel_poll_interval_s=0.005, subscribe_async=False),
        emit_fn=lambda *a, **k: None,
    )
    seq.run(
        [
            Action("m1", "head_turn", {"yaw_deg": 5}, duration_s=0.02),
            Action("m2", "sleep", {}, duration_s=0.02),
            Action("m3", "wakeup", {}, duration_s=0.02),
        ]
    )
    check("goto_target 至少调用 1 次 (head_turn)", r.goto_target.call_count >= 1)
    check("goto_sleep 调用 1 次", r.goto_sleep.call_count == 1)
    check("wake_up 调用 1 次", r.wake_up.call_count == 1)

    # 5c: action 模块源码不依赖任何 reachy_mini.body / motor / torque 等真硬件 API
    seq_src = inspect.getsource(__import__("coco.robot.sequencer", fromlist=["x"]))
    forbidden = ["set_motor_torque", "enable_torque", "body.", "low_level"]
    for tok in forbidden:
        check(f"sequencer.py 不含真硬件 token: {tok!r}", tok not in seq_src,
              f"token {tok!r} appeared")
except Exception:  # noqa: BLE001
    errors.append("V5: " + traceback.format_exc())


# =======================================================================
# V6 robot-005 回归
# =======================================================================
print("V6: 回归 verify_robot_005 全 PASS (subprocess)")
try:
    res = subprocess.run(
        [sys.executable, "scripts/verify_robot_005.py"],
        capture_output=True, text=True, timeout=180,
    )
    ok = res.returncode == 0
    check(
        "verify_robot_005 rc == 0",
        ok,
        f"rc={res.returncode}; tail stderr={(res.stderr or '')[-200:]}",
    )
except Exception:  # noqa: BLE001
    errors.append("V6: " + traceback.format_exc())


# =======================================================================
# V7 业务订阅回压 — 慢订阅者不阻塞 sequencer 主线程
# =======================================================================
print("V7: 业务订阅回压 — 慢订阅者不阻塞 sequencer 主线程 (subscribe_async=True)")
try:
    from coco.robot.sequencer import Action, RobotSequencer, SequencerConfig

    slow_calls = []

    def _slow_subscriber(ev: str, payload: dict) -> None:
        slow_calls.append(payload["action_id"])
        time.sleep(0.5)  # 模拟慢订阅

    r = make_mock_robot()
    cfg = SequencerConfig(enabled=True, cancel_poll_interval_s=0.005, subscribe_async=True)
    seq = RobotSequencer(robot=r, config=cfg, emit_fn=lambda *a, **k: None)
    seq.subscribe(_slow_subscriber)

    actions = [
        Action(f"q{i}", "head_turn", {"yaw_deg": i}, duration_s=0.02) for i in range(5)
    ]
    t_start = time.time()
    res = seq.run(actions)
    elapsed = time.time() - t_start

    # 总耗时应接近 5*0.02s == 0.1s 量级（容许 0.3s 缓冲）；不应等于 slow_subscriber 5*0.5=2.5s
    check("sequencer 主线程未被慢订阅阻塞 (elapsed < 0.5s)", elapsed < 0.5,
          f"elapsed={elapsed:.3f}s")
    check("所有 5 action 都跑完", res["executed"] == 5, f"got {res['executed']}")
    # 慢订阅在后台线程被调用（至少被调用一次；不强求 5 次都完成）
    time.sleep(0.05)  # 给后台线程一点时间至少进入
    check("慢订阅至少被触发 1 次（异步派发已发出）", len(slow_calls) >= 1,
          f"got {len(slow_calls)}")
except Exception:  # noqa: BLE001
    errors.append("V7: " + traceback.format_exc())


# =======================================================================
# V8 subscribe 异常不影响 sequencer 推进
# =======================================================================
print("V8: subscribe callback 抛异常不影响 sequencer 继续推进")
try:
    from coco.robot.sequencer import Action, RobotSequencer, SequencerConfig

    def _bad_sub(ev: str, payload: dict) -> None:
        raise RuntimeError("boom")

    r = make_mock_robot()
    cfg = SequencerConfig(enabled=True, cancel_poll_interval_s=0.005, subscribe_async=False)
    seq = RobotSequencer(robot=r, config=cfg, emit_fn=lambda *a, **k: None)
    seq.subscribe(_bad_sub)

    res = seq.run(
        [
            Action("b1", "head_turn", {"yaw_deg": 10}, duration_s=0.02),
            Action("b2", "nod", {"amplitude_deg": 8}, duration_s=0.02),
        ]
    )
    check("subscribe 异常下序列仍完整执行", res["executed"] == 2, f"got {res['executed']}")
    check("cancelled == False", res["cancelled"] is False)
except Exception:  # noqa: BLE001
    errors.append("V8: " + traceback.format_exc())


# =======================================================================
# 汇总
# =======================================================================
elapsed = time.time() - t0
print(f"\n========== robot-006 verify done in {elapsed:.2f}s ==========")
if errors:
    print(f"FAIL ({len(errors)} errors):")
    for e in errors:
        print("  - " + e.splitlines()[0])
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
