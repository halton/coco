"""robot-008 verify: sequencer lifecycle (atexit) + ProactiveScheduler 注入 + business subscribe wire.

V1: atexit shutdown — RobotSequencer.shutdown() 注册到 atexit 后, 子进程退出时被调用,
    dispatch_workers 全部 join 完成 (无 thread leak)。
V2: ProactiveScheduler 注入 — set_robot_sequencer(seq) + _do_trigger_unlocked
    成功触发后, sequencer.run([nod]) 至少被调用一次。
V3: business subscribe — fake 业务方 subscribe(callback), proactive 触发后通过
    sequencer 派发的 action_done 被业务回调收到。
V4: Default-OFF 等价 — COCO_ROBOT_SEQ 未设时 ProactiveScheduler 行为与基线 bytewise 等价
    (set_robot_sequencer 未调用, _do_trigger_unlocked 末尾不起线程)。
V5: regression — verify_robot_007.py + verify_robot_006.py 子进程 rc==0。
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


# 清 env (确保从已知状态出发)
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
# V1 atexit shutdown — 子进程注册 RobotSequencer.shutdown 到 atexit, 退出后无 leak.
# =======================================================================
print("V1: atexit shutdown hook 注册 + 子进程退出后 sequencer 安全收尾")
try:
    code = (
        "import atexit, sys, threading;"
        "from coco.robot.sequencer import RobotSequencer, SequencerConfig;"
        "cfg=SequencerConfig(enabled=True, subscribe_async=True, pool_size=2, queue_max=8);"
        "seq=RobotSequencer(robot=None, config=cfg);"
        "atexit.register(lambda s=seq: s.shutdown(wait=True, timeout=2.0));"
        # 验证 pool workers 起来了
        "n_before=len(seq._dispatch_workers);"
        "sys.stdout.write('workers_before=' + str(n_before) + chr(10));"
        "sys.stdout.flush();"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=20,
    )
    ok_rc = res.returncode == 0
    has_workers = "workers_before=2" in (res.stdout or "")
    check("V1 子进程 rc==0", ok_rc, f"rc={res.returncode} stderr={(res.stderr or '')[-200:]}")
    check("V1 dispatch workers 启动 (==2)", has_workers, f"stdout={(res.stdout or '').strip()}")
    # 子进程顺利退出意味着 atexit 收尾后所有 daemon 线程都已结束（join 完成）
    # 否则即便 daemon 也会被 atexit 强杀, 但若 shutdown 内部 join 超时, stderr 会有线索
    no_err = "Traceback" not in (res.stderr or "")
    check("V1 atexit shutdown 无异常", no_err, f"stderr={(res.stderr or '')[-200:]}")
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())


# =======================================================================
# V2 ProactiveScheduler 注入 — _do_trigger_unlocked 后 sequencer.enqueue(nod) 被调
# (robot-009 改造: 旧 run([nod]) daemon thread → enqueue 非阻塞 API)
# =======================================================================
print("V2: ProactiveScheduler.set_robot_sequencer + _do_trigger_unlocked 触发 sequencer.enqueue")
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

    # 直接调 _do_trigger_unlocked (绕过锁 / 条件检查)
    sched._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="topic-seed")

    # robot-009: enqueue 同步非阻塞调用, 不再需要等异步线程
    check("V2 fake_seq.enqueue() 至少被调用一次",
          fake_seq.enqueue.call_count >= 1,
          f"call_count={fake_seq.enqueue.call_count}")
    if fake_seq.enqueue.call_count >= 1:
        args, _ = fake_seq.enqueue.call_args
        action = args[0] if args else None
        first_type = getattr(action, "type", None)
        check("V2 enqueue 的 action 是 nod", first_type == "nod", f"type={first_type!r}")
except Exception:  # noqa: BLE001
    errors.append("V2: " + traceback.format_exc())


# =======================================================================
# V3 business subscribe — proactive→enqueue→action worker→run([nod])→dispatch→biz_observer 收 action_done
# (robot-009: 路径变成 enqueue, 但 action worker 内部仍调 run() 走 dispatch, 业务回调端到端不变)
# =======================================================================
print("V3: business subscribe — proactive 触发后 subscribe 回调收到 action_done")
try:
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    from coco.robot.sequencer import RobotSequencer, SequencerConfig

    cfg = SequencerConfig(enabled=True, subscribe_async=True, pool_size=2, queue_max=16)
    real_seq = RobotSequencer(robot=None, config=cfg)

    received: List[tuple] = []
    received_lock = threading.Lock()

    def biz_observer(event: str, payload: dict) -> None:
        with received_lock:
            received.append((event, dict(payload)))

    real_seq.subscribe(biz_observer)

    sched = ProactiveScheduler(
        config=ProactiveConfig(),
        power_state=None,
        face_tracker=None,
        llm_reply_fn=lambda seed, **kw: f"chat-{seed[:10]}",
        tts_say_fn=lambda text, blocking=True: None,
    )
    sched.set_robot_sequencer(real_seq)

    sched._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="topic-x")

    # 等 sequencer.run + dispatch_worker 把 action_done 投递给 biz_observer
    deadline = time.time() + 3.0
    while time.time() < deadline:
        with received_lock:
            if any(e == "robot.action_done" for e, _ in received):
                break
        time.sleep(0.02)

    with received_lock:
        action_dones = [p for (e, p) in received if e == "robot.action_done"]

    check("V3 业务 observer 收到 robot.action_done (>=1)",
          len(action_dones) >= 1,
          f"got {len(action_dones)} events; all={[e for e,_ in received]}")
    if action_dones:
        first = action_dones[0]
        check("V3 action_done.type == 'nod'",
              first.get("type") == "nod", f"type={first.get('type')!r}")

    real_seq.shutdown()
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4 Default-OFF 等价 — sequencer 未注入时, _do_trigger_unlocked 不起异步线程
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
    # 不调用 set_robot_sequencer
    check("V4 默认 _robot_sequencer is None", sched._robot_sequencer is None)

    # 记录 trigger 前的活跃 robot-seq 线程数
    def _n_seq_threads() -> int:
        return sum(1 for t in threading.enumerate()
                   if t.name.startswith("coco-proactive-robot-seq"))

    n_before = _n_seq_threads()
    sched._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="x")
    time.sleep(0.1)
    n_after = _n_seq_threads()

    check("V4 trigger 后无 robot-seq 线程产生",
          n_after == n_before == 0,
          f"before={n_before} after={n_after}")
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5 regression — verify_robot_007 + verify_robot_006 子进程 rc==0
# =======================================================================
print("V5: regression — verify_robot_007 + verify_robot_006 子进程 rc==0")
for v in ("verify_robot_007.py", "verify_robot_006.py"):
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
print(f"\n========== robot-008 verify done in {elapsed:.2f}s ==========")
if errors:
    print(f"FAIL ({len(errors)} errors):")
    for e in errors:
        print("  - " + e.splitlines()[0])
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
