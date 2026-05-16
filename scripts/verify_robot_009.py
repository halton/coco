"""robot-009 verify: ProactiveScheduler→RobotSequencer 注入改造.

V1: proactive trigger 走 enqueue 路径 — sequencer.enqueue 被调一次 + 无 coco-proactive-robot-seq daemon thread
V2: sequencer queue full 时 enqueue drop_oldest 行为 + emit robot.enqueue_dropped
V3: sequencer 已 shutdown 时 enqueue best-effort no-op + emit dropped
V4: Default-OFF — sequencer 未注入时 _do_trigger_unlocked 不进 enqueue 分支, bytewise 等价
V5: regression — verify_robot_008 + verify_robot_007 + verify_robot_006 子进程 rc==0
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


# 清 env
for k in (
    "COCO_ROBOT_SEQ",
    "COCO_ROBOT_SEQ_POLL_S",
    "COCO_ROBOT_SEQ_SUB_ASYNC",
    "COCO_ROBOT_SEQ_POOL_SIZE",
    "COCO_ROBOT_SEQ_QUEUE_MAX",
    "COCO_ROBOT_SEQ_OVERFLOW",
    "COCO_PROACTIVE",
):
    os.environ.pop(k, None)


# =======================================================================
# V1 proactive→sequencer.enqueue 路径 + 无 daemon thread leak
# =======================================================================
print("V1: proactive trigger 走 enqueue 路径 + 无 coco-proactive-robot-seq daemon thread")
try:
    from coco.proactive import ProactiveScheduler, ProactiveConfig

    fake_seq = MagicMock()
    fake_seq.enqueue = MagicMock(return_value=True)
    fake_seq.run = MagicMock(return_value={"executed": 1, "cancelled": False})

    sched = ProactiveScheduler(
        config=ProactiveConfig(),
        power_state=None,
        face_tracker=None,
        llm_reply_fn=lambda seed, **kw: f"hello-{seed[:20]}",
        tts_say_fn=lambda text, blocking=True: None,
    )
    sched.set_robot_sequencer(fake_seq)

    def _n_seq_threads() -> int:
        return sum(1 for t in threading.enumerate()
                   if t.name.startswith("coco-proactive-robot-seq"))

    n_before = _n_seq_threads()
    sched._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="topic-seed")
    # robot-009: enqueue 是非阻塞同步调用 — 不需 sleep 等异步线程
    n_after = _n_seq_threads()

    check("V1 fake_seq.enqueue 至少被调用一次",
          fake_seq.enqueue.call_count >= 1,
          f"call_count={fake_seq.enqueue.call_count}")
    if fake_seq.enqueue.call_count >= 1:
        args, _ = fake_seq.enqueue.call_args
        action = args[0] if args else None
        check("V1 enqueue 投递的 action.type == 'nod'",
              getattr(action, "type", None) == "nod",
              f"type={getattr(action, 'type', None)!r}")

    check("V1 fake_seq.run 不再被 proactive 调用 (run.call_count == 0)",
          fake_seq.run.call_count == 0,
          f"run.call_count={fake_seq.run.call_count}")

    check("V1 无 coco-proactive-robot-seq daemon thread 产生",
          n_after == n_before == 0,
          f"before={n_before} after={n_after}")
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())


# =======================================================================
# V2 sequencer queue full → drop_oldest + emit robot.enqueue_dropped
# =======================================================================
print("V2: queue full → drop_oldest + emit robot.enqueue_dropped")
try:
    from coco.robot.sequencer import RobotSequencer, SequencerConfig, Action

    emitted: List[tuple] = []
    emit_lock = threading.Lock()

    def cap_emit(event: str, message: str = "", **payload: Any) -> None:
        with emit_lock:
            emitted.append((event, dict(payload)))

    cfg = SequencerConfig(
        enabled=True,
        subscribe_async=False,  # 关掉 dispatch 影响, 聚焦 action queue
        pool_size=1,
        queue_max=2,
        overflow_policy="drop_oldest",
    )
    # robot=None 不真执行；但 worker 会调 run() 把 action 出队消费
    # 为了让 queue 真的能 full, 我们临时把 worker stop 住:
    seq = RobotSequencer(robot=None, config=cfg, emit_fn=cap_emit)
    # 暂停 worker（设 stop event, 但不 shutdown — 仅让 worker loop 退出 get）
    seq._action_stop.set()
    if seq._action_worker is not None:
        seq._action_worker.join(timeout=1.0)

    # 现在 queue 是 maxsize=2, 没人消费 → 投 3 个必触发 1 次 drop_oldest
    a1 = Action(action_id="a1", type="nod", params={}, duration_s=0.01)
    a2 = Action(action_id="a2", type="nod", params={}, duration_s=0.01)
    a3 = Action(action_id="a3", type="nod", params={}, duration_s=0.01)
    r1 = seq.enqueue(a1)
    r2 = seq.enqueue(a2)
    r3 = seq.enqueue(a3)

    check("V2 前两次 enqueue 返回 True", r1 is True and r2 is True, f"r1={r1} r2={r2}")
    check("V2 第三次 enqueue (queue 满) 仍 True (drop_oldest 腾位后塞入)",
          r3 is True, f"r3={r3}")

    with emit_lock:
        dropped_events = [p for (e, p) in emitted if e == "robot.enqueue_dropped"]
    check("V2 至少 emit 一条 robot.enqueue_dropped",
          len(dropped_events) >= 1,
          f"got {len(dropped_events)} events; all={[e for e,_ in emitted]}")
    if dropped_events:
        check("V2 dropped reason == 'drop_oldest'",
              dropped_events[0].get("reason") == "drop_oldest",
              f"reason={dropped_events[0].get('reason')!r}")

    # 收尾：直接 shutdown (worker 已停, shutdown 设 _is_shutdown 即可)
    seq.shutdown(wait=False, timeout=0.5)
except Exception:  # noqa: BLE001
    errors.append("V2: " + traceback.format_exc())


# =======================================================================
# V3 已 shutdown 的 sequencer enqueue → no-op + emit dropped
# =======================================================================
print("V3: sequencer.shutdown 后 enqueue best-effort no-op + emit dropped")
try:
    from coco.robot.sequencer import RobotSequencer, SequencerConfig, Action

    emitted_v3: List[tuple] = []
    emit_lock_v3 = threading.Lock()

    def cap_emit_v3(event: str, message: str = "", **payload: Any) -> None:
        with emit_lock_v3:
            emitted_v3.append((event, dict(payload)))

    cfg = SequencerConfig(
        enabled=True,
        subscribe_async=False,
        pool_size=1,
        queue_max=4,
        overflow_policy="drop_oldest",
    )
    seq = RobotSequencer(robot=None, config=cfg, emit_fn=cap_emit_v3)
    seq.shutdown(wait=True, timeout=1.0)

    check("V3 is_shutdown() == True", seq.is_shutdown() is True)

    a = Action(action_id="after-shutdown", type="nod", params={}, duration_s=0.01)
    raised = False
    rv = None
    try:
        rv = seq.enqueue(a)
    except Exception as exc:  # noqa: BLE001
        raised = True

    check("V3 shutdown 后 enqueue 不抛", raised is False, f"raised={raised}")
    check("V3 shutdown 后 enqueue 返回 False", rv is False, f"rv={rv}")

    with emit_lock_v3:
        dropped_v3 = [p for (e, p) in emitted_v3 if e == "robot.enqueue_dropped"]
    check("V3 至少 emit 一条 robot.enqueue_dropped (reason=shutdown)",
          any(p.get("reason") == "shutdown" for p in dropped_v3),
          f"events={[(e, p.get('reason')) for e,p in emitted_v3]}")
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4 Default-OFF — 未注入 sequencer, _do_trigger_unlocked 不进 enqueue 分支
# =======================================================================
print("V4: Default-OFF — set_robot_sequencer 未调用时无 robot 副作用")
try:
    from coco.proactive import ProactiveScheduler, ProactiveConfig

    sched = ProactiveScheduler(
        config=ProactiveConfig(),
        power_state=None,
        face_tracker=None,
        llm_reply_fn=lambda seed, **kw: "hello",
        tts_say_fn=lambda text, blocking=True: None,
    )
    check("V4 默认 _robot_sequencer is None", sched._robot_sequencer is None)

    def _n_seq_threads_v4() -> int:
        return sum(1 for t in threading.enumerate()
                   if t.name.startswith("coco-proactive-robot-seq"))

    n_before = _n_seq_threads_v4()
    sched._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="x")
    n_after = _n_seq_threads_v4()

    check("V4 trigger 后无 coco-proactive-robot-seq 线程产生",
          n_after == n_before == 0,
          f"before={n_before} after={n_after}")
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5 regression — verify_robot_008 + verify_robot_007 + verify_robot_006 rc==0
# =======================================================================
print("V5: regression — verify_robot_008 + verify_robot_007 + verify_robot_006 子进程 rc==0")
for v in ("verify_robot_008.py", "verify_robot_007.py", "verify_robot_006.py"):
    try:
        res = subprocess.run(
            [sys.executable, f"scripts/{v}"],
            capture_output=True, text=True, timeout=300,
        )
        ok = res.returncode == 0
        check(
            f"V5 {v} rc==0",
            ok,
            f"rc={res.returncode}; tail stderr={(res.stderr or '')[-200:]}",
        )
    except Exception:  # noqa: BLE001
        errors.append(f"V5 {v}: " + traceback.format_exc())


# =======================================================================
# 汇总
# =======================================================================
elapsed = time.time() - t0
print(f"\n========== robot-009 verify done in {elapsed:.2f}s ==========")
if errors:
    print(f"FAIL ({len(errors)} errors):")
    for e in errors:
        print("  - " + e.splitlines()[0])
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
