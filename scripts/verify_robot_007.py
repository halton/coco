"""robot-007 verify: subscribe dispatch ThreadPoolExecutor + 有界回压.

V1: pool_size=2, 5 fast subscriber, 全部 dispatch 完成无遗漏
V2: queue_max=2 + slow subscriber + drop_oldest, 触发 subscribe_dropped + dropped_n 单调
V3: overflow=drop_new 模式: drop 新进入 event, 旧的保留
V4: overflow=block 模式: emit 端阻塞直到队列空位
V5: 真 RobotSequencer mockup-sim 集成: cancel 仍工作 + 序列 emit 单调
V6: regression — verify_robot_006 全 PASS
"""
from __future__ import annotations

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


# 清 env
for k in (
    "COCO_ROBOT_SEQ",
    "COCO_ROBOT_SEQ_POLL_S",
    "COCO_ROBOT_SEQ_SUB_ASYNC",
    "COCO_ROBOT_SEQ_POOL_SIZE",
    "COCO_ROBOT_SEQ_QUEUE_MAX",
    "COCO_ROBOT_SEQ_OVERFLOW",
):
    os.environ.pop(k, None)


# =======================================================================
# V1 pool_size=2, 5 fast subscriber, 全部 dispatch 完成
# =======================================================================
print("V1: pool_size=2, 5 fast subscribers, 全部 dispatch 完成 (无遗漏)")
try:
    from coco.robot.sequencer import Action, RobotSequencer, SequencerConfig

    received: List[tuple] = []
    received_lock = threading.Lock()

    def _fast_sub_factory(idx: int):
        def _cb(ev: str, payload: dict) -> None:
            with received_lock:
                received.append((idx, payload["action_id"]))
        return _cb

    cfg = SequencerConfig(
        enabled=True,
        cancel_poll_interval_s=0.005,
        subscribe_async=True,
        pool_size=2,
        queue_max=64,
        overflow_policy="drop_oldest",
    )
    r = make_mock_robot()
    seq = RobotSequencer(robot=r, config=cfg, emit_fn=lambda *a, **k: None)
    for i in range(5):
        seq.subscribe(_fast_sub_factory(i))

    actions = [
        Action(f"a{i}", "head_turn", {"yaw_deg": i}, duration_s=0.01) for i in range(5)
    ]
    res = seq.run(actions)
    # 等 dispatch 全部完成
    time.sleep(0.5)

    check("executed == 5", res["executed"] == 5, f"got {res['executed']}")
    # 5 subscribers * 5 actions = 25 callbacks
    check("dispatch 全部完成 25 次 (5 sub * 5 ev)", len(received) == 25, f"got {len(received)}")
    seq.shutdown()
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())


# =======================================================================
# V2 queue_max=2 + slow subscriber + drop_oldest
# =======================================================================
print("V2: queue_max=2 + slow subscriber + drop_oldest -> emit subscribe_dropped + dropped_n 单调")
try:
    from coco.robot.sequencer import Action, RobotSequencer, SequencerConfig

    emitted: List[tuple] = []
    emit_lock = threading.Lock()

    def _capture_emit(component_event: str, message: str = "", **payload: Any) -> None:
        with emit_lock:
            emitted.append((component_event, dict(payload)))

    def _slow_sub(ev: str, payload: dict) -> None:
        time.sleep(0.3)

    cfg = SequencerConfig(
        enabled=True,
        cancel_poll_interval_s=0.005,
        subscribe_async=True,
        pool_size=2,
        queue_max=2,
        overflow_policy="drop_oldest",
    )
    r = make_mock_robot()
    seq = RobotSequencer(robot=r, config=cfg, emit_fn=_capture_emit)
    seq.subscribe(_slow_sub)

    # 快速 emit 10 条
    actions = [
        Action(f"q{i}", "head_turn", {"yaw_deg": i}, duration_s=0.005) for i in range(10)
    ]
    seq.run(actions)
    time.sleep(0.2)  # 给 dispatch 一点处理时间

    drops = [e for e in emitted if e[0] == "robot.subscribe_dropped"]
    check("触发 subscribe_dropped (>=1 次)", len(drops) >= 1, f"got {len(drops)}")
    if drops:
        ns = [e[1].get("dropped_n", 0) for e in drops]
        check(
            "dropped_n 单调非降",
            all(ns[i] <= ns[i + 1] for i in range(len(ns) - 1)),
            f"got {ns}",
        )
        check(
            "首条 dropped_n>=1",
            ns[0] >= 1,
            f"got {ns[0]}",
        )
        check(
            "每条 payload 含 queue_max",
            all("queue_max" in e[1] for e in drops),
        )
        reasons = {e[1].get("reason") for e in drops}
        check("reason 为 drop_oldest", reasons == {"drop_oldest"}, f"got {reasons}")
    seq.shutdown()
except Exception:  # noqa: BLE001
    errors.append("V2: " + traceback.format_exc())


# =======================================================================
# V3 overflow=drop_new: drop 新进入, 旧的保留
# =======================================================================
print("V3: overflow=drop_new -> drop 新进入 event, 旧的保留")
try:
    from coco.robot.sequencer import Action, RobotSequencer, SequencerConfig

    emitted_v3: List[tuple] = []
    received_v3: List[str] = []
    rcv_lock = threading.Lock()

    def _capture_emit_v3(component_event: str, message: str = "", **payload: Any) -> None:
        emitted_v3.append((component_event, dict(payload)))

    def _slow_sub_v3(ev: str, payload: dict) -> None:
        time.sleep(0.15)
        with rcv_lock:
            received_v3.append(payload["action_id"])

    cfg = SequencerConfig(
        enabled=True,
        cancel_poll_interval_s=0.005,
        subscribe_async=True,
        pool_size=1,  # 单 worker 强制堆积
        queue_max=2,
        overflow_policy="drop_new",
    )
    r = make_mock_robot()
    seq = RobotSequencer(robot=r, config=cfg, emit_fn=_capture_emit_v3)
    seq.subscribe(_slow_sub_v3)

    actions = [
        Action(f"d{i}", "head_turn", {"yaw_deg": i}, duration_s=0.005) for i in range(8)
    ]
    seq.run(actions)
    time.sleep(2.0)  # 给 slow sub 完成时间

    drops = [e for e in emitted_v3 if e[0] == "robot.subscribe_dropped"]
    reasons = {e[1].get("reason") for e in drops}
    check("触发 drop_new (>=1 次)", len(drops) >= 1, f"got {len(drops)}")
    check("reason 为 drop_new", reasons == {"drop_new"}, f"got {reasons}")
    # drop_new 时，最早入队的 d0 / d1 应该被处理（不丢旧）
    check(
        "drop_new: 最早 action_id 在 received 内 (旧的保留)",
        "d0" in received_v3 or "d1" in received_v3,
        f"received={received_v3}",
    )
    seq.shutdown()
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4 overflow=block: emit 端阻塞直到队列空位
# =======================================================================
print("V4: overflow=block -> emit 端阻塞直到队列空位 (无 drop)")
try:
    from coco.robot.sequencer import Action, RobotSequencer, SequencerConfig

    emitted_v4: List[tuple] = []
    received_v4: List[str] = []
    rcv_lock_v4 = threading.Lock()

    def _capture_emit_v4(component_event: str, message: str = "", **payload: Any) -> None:
        emitted_v4.append((component_event, dict(payload)))

    def _slow_sub_v4(ev: str, payload: dict) -> None:
        time.sleep(0.05)
        with rcv_lock_v4:
            received_v4.append(payload["action_id"])

    cfg = SequencerConfig(
        enabled=True,
        cancel_poll_interval_s=0.005,
        subscribe_async=True,
        pool_size=1,
        queue_max=2,
        overflow_policy="block",
    )
    r = make_mock_robot()
    seq = RobotSequencer(robot=r, config=cfg, emit_fn=_capture_emit_v4)
    seq.subscribe(_slow_sub_v4)

    actions = [
        Action(f"b{i}", "head_turn", {"yaw_deg": i}, duration_s=0.001) for i in range(6)
    ]
    t_start = time.time()
    seq.run(actions)
    elapsed_run = time.time() - t_start
    time.sleep(1.0)

    drops_v4 = [e for e in emitted_v4 if e[0] == "robot.subscribe_dropped"]
    check("block 模式 无 drop", len(drops_v4) == 0, f"got {len(drops_v4)}")
    check("block 模式 全部 received (6 ev)", len(received_v4) == 6, f"got {len(received_v4)}")
    # elapsed_run 应至少 ~ 6 * 0.05 = 0.3s 量级，因为 emit 端被 block 等 slow sub
    check(
        "block 模式 run 整体被回压到 >= ~0.15s",
        elapsed_run >= 0.15,
        f"elapsed={elapsed_run:.3f}s",
    )
    seq.shutdown()
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5 真 RobotSequencer mockup-sim 集成: cancel 仍工作 + 序列 emit 单调
# =======================================================================
print("V5: 真 RobotSequencer mockup-sim 集成 — cancel 仍工作 + 序列 emit 时间戳单调")
try:
    from coco.robot.sequencer import Action, RobotSequencer, SequencerConfig

    emitted_v5: List[tuple] = []
    received_v5: List[dict] = []
    rcv_lock_v5 = threading.Lock()

    def _capture_emit_v5(component_event: str, message: str = "", **payload: Any) -> None:
        emitted_v5.append((component_event, dict(payload)))

    def _sub_v5(ev: str, payload: dict) -> None:
        with rcv_lock_v5:
            received_v5.append({"ev": ev, "p": dict(payload)})

    cfg = SequencerConfig(
        enabled=True,
        cancel_poll_interval_s=0.005,
        subscribe_async=True,
        pool_size=2,
        queue_max=32,
        overflow_policy="drop_oldest",
    )
    r = make_mock_robot()
    seq = RobotSequencer(robot=r, config=cfg, emit_fn=_capture_emit_v5)
    seq.subscribe(_sub_v5)

    actions = [
        Action("v5_1", "head_turn", {"yaw_deg": 10}, duration_s=0.05),
        Action("v5_2", "head_turn", {"yaw_deg": -10}, duration_s=0.4),  # mid-cancel
        Action("v5_3", "nod", {"amplitude_deg": 8}, duration_s=0.05),
        Action("v5_4", "sleep", {}, duration_s=0.05),
    ]

    def _cancel_later() -> None:
        time.sleep(0.15)
        seq.cancel()

    threading.Thread(target=_cancel_later, daemon=True).start()
    res = seq.run(actions)
    time.sleep(0.3)

    check("V5 cancelled == True", res["cancelled"] is True, f"got {res['cancelled']}")
    check("V5 executed >= 1", res["executed"] >= 1, f"executed={res['executed']}")
    cancel_evs = [e for e in emitted_v5 if e[0] == "robot.sequence_cancelled"]
    check("V5 emit sequence_cancelled 一次", len(cancel_evs) == 1, f"got {len(cancel_evs)}")
    done_evs = [e for e in emitted_v5 if e[0] == "robot.action_done"]
    if len(done_evs) >= 2:
        ts_list = [e[1]["ts"] for e in done_evs]
        check(
            "V5 action_done ts 严格单调",
            all(ts_list[i] < ts_list[i + 1] for i in range(len(ts_list) - 1)),
            f"ts={ts_list}",
        )
    # 订阅方应收到至少 executed 次 action_done + 1 次 sequence_cancelled
    sub_dones = [r for r in received_v5 if r["ev"] == "robot.action_done"]
    check(
        "V5 订阅方收到 action_done (>=1 条)",
        len(sub_dones) >= 1,
        f"got {len(sub_dones)}",
    )
    seq.shutdown()
except Exception:  # noqa: BLE001
    errors.append("V5: " + traceback.format_exc())


# =======================================================================
# V6 regression — verify_robot_006 全 PASS
# =======================================================================
print("V6: regression — verify_robot_006 子进程 rc==0")
try:
    res = subprocess.run(
        [sys.executable, "scripts/verify_robot_006.py"],
        capture_output=True, text=True, timeout=240,
    )
    ok = res.returncode == 0
    check(
        "verify_robot_006 rc == 0",
        ok,
        f"rc={res.returncode}; tail stderr={(res.stderr or '')[-300:]}",
    )
except Exception:  # noqa: BLE001
    errors.append("V6: " + traceback.format_exc())


# =======================================================================
# 汇总
# =======================================================================
elapsed = time.time() - t0
print(f"\n========== robot-007 verify done in {elapsed:.2f}s ==========")
if errors:
    print(f"FAIL ({len(errors)} errors):")
    for e in errors:
        print("  - " + e.splitlines()[0])
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
