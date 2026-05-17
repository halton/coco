"""robot-013 verify: RobotSequencer busy/drop metric — busy_count + policy 字段.

V0: fingerprint — _on_enqueue_drop emit 含 busy_count / policy / COCO_ROBOT_BUSY_METRIC token
V1: Default-OFF bytewise — env unset → drop emit 无 busy_count 字段; policy 字段 additive 常开
V2: env ON — 连续触发 N drop, busy_count 单调 1..N
V3: policy 字段值在 drop_oldest / drop_new 下不同; block 策略不触发 drop
V4: 多线程并发 enqueue 触发 drop: busy_count 单调不重复
V5: regression — verify_robot_008/009/010/011/012 子进程 rc==0
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Any, List

errors: List[str] = []
t0 = time.time()


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


# 清 env (V0/V1 起步 default-OFF)
for k in (
    "COCO_ROBOT_BUSY_METRIC",
    "COCO_ROBOT_SEQ",
    "COCO_ROBOT_SEQ_OVERFLOW",
    "COCO_ROBOT_SEQ_QUEUE_MAX",
):
    os.environ.pop(k, None)


# --------- V0: fingerprint ---------
print("[V0] fingerprint")
try:
    import coco.robot.sequencer as _seq_mod
    src = open(_seq_mod.__file__, encoding="utf-8").read()
    check(
        "token COCO_ROBOT_BUSY_METRIC present in sequencer.py",
        "COCO_ROBOT_BUSY_METRIC" in src,
    )
    check(
        "token busy_count present in _on_enqueue_drop emit",
        "busy_count" in src,
    )
    check(
        "token policy present in _on_enqueue_drop emit payload",
        'policy=self.config.overflow_policy' in src or '"policy"' in src or "'policy'" in src,
    )
    check(
        "robot-013 marker present",
        "robot-013" in src,
    )
except Exception as e:  # noqa: BLE001
    check("V0 fingerprint", False, f"{type(e).__name__}: {e}")
    print("ABORT V0 failed")
    sys.exit(1)


# --------- 工具: 构造一个 sequencer + 捕获 emit ---------
def _make_seq(policy: str = "drop_oldest", queue_max: int = 2):
    from coco.robot.sequencer import RobotSequencer, SequencerConfig
    events: List[tuple] = []
    lock = threading.Lock()

    def _emit(name: str, **payload: Any) -> None:
        with lock:
            events.append((name, dict(payload)))

    seq = RobotSequencer(
        config=SequencerConfig(
            enabled=False,
            overflow_policy=policy,
            queue_max=queue_max,
            subscribe_async=False,  # 简化, 不需要 dispatch pool
        ),
        emit_fn=_emit,
    )
    return seq, events


# --------- V1: Default-OFF bytewise (env unset) ---------
print("[V1] Default-OFF bytewise (env unset → drop 事件无 busy_count, policy 字段可有)")
try:
    from coco.robot.sequencer import Action
    os.environ.pop("COCO_ROBOT_BUSY_METRIC", None)
    seq, events = _make_seq(policy="drop_new", queue_max=1)
    # 直接 触发 _on_enqueue_drop 3 次
    for _ in range(3):
        seq._on_enqueue_drop(reason="drop_new")
    drops = [e for e in events if e[0] == "robot.enqueue_dropped"]
    check("V1 三次 emit 全到", len(drops) == 3, f"got={len(drops)}")
    for i, (_, p) in enumerate(drops):
        check(
            f"V1[{i}] busy_count 字段不存在 (default-OFF)",
            "busy_count" not in p,
            f"keys={list(p.keys())}",
        )
        check(
            f"V1[{i}] policy 字段=drop_new (additive 常开)",
            p.get("policy") == "drop_new",
            f"got={p.get('policy')}",
        )
        check(
            f"V1[{i}] 历史字段 reason/queue_max/dropped_n 在",
            "reason" in p and "queue_max" in p and "dropped_n" in p,
        )
    seq.shutdown(wait=True, timeout=0.5)
except Exception as e:  # noqa: BLE001
    check("V1 default-off", False, f"{type(e).__name__}: {e}")


# --------- V2: env ON 时 busy_count 单调 1..N ---------
print("[V2] env ON → busy_count 单调 1..N")
try:
    from coco.robot.sequencer import Action
    os.environ["COCO_ROBOT_BUSY_METRIC"] = "1"
    seq, events = _make_seq(policy="drop_new", queue_max=1)
    N = 5
    for _ in range(N):
        seq._on_enqueue_drop(reason="drop_new")
    drops = [e for e in events if e[0] == "robot.enqueue_dropped"]
    check("V2 emit 个数==N", len(drops) == N, f"got={len(drops)}")
    busy_seq = [p.get("busy_count") for (_, p) in drops]
    check(
        "V2 busy_count 序列 == [1..N]",
        busy_seq == list(range(1, N + 1)),
        f"got={busy_seq}",
    )
    # 同时 dropped_n 也应单调
    dn_seq = [p.get("dropped_n") for (_, p) in drops]
    check("V2 dropped_n 单调 1..N", dn_seq == list(range(1, N + 1)), f"got={dn_seq}")
    seq.shutdown(wait=True, timeout=0.5)
    os.environ.pop("COCO_ROBOT_BUSY_METRIC", None)
except Exception as e:  # noqa: BLE001
    check("V2 env-on", False, f"{type(e).__name__}: {e}")
    os.environ.pop("COCO_ROBOT_BUSY_METRIC", None)


# --------- V3: policy 字段在 drop_oldest / drop_new 下不同; block 不触发 drop ---------
print("[V3] policy 字段值随 overflow_policy 变化; block 策略不触发 drop")
try:
    from coco.robot.sequencer import Action

    # drop_oldest: 真实 enqueue 触发
    seq_o, ev_o = _make_seq(policy="drop_oldest", queue_max=1)
    # 暂停 worker 消费: 设 _running=True 让 worker putback (但更直接: 直接灌满 queue)
    # 我们直接调用 enqueue 满后再 enqueue
    seq_o._action_stop.set()  # 停 worker, 不消费
    # 等 worker 真正退出
    if seq_o._action_worker is not None:
        seq_o._action_worker.join(timeout=1.0)
    a1 = Action("a1", "head_turn", {"yaw_deg": 0}, 0.01)
    a2 = Action("a2", "head_turn", {"yaw_deg": 0}, 0.01)
    a3 = Action("a3", "head_turn", {"yaw_deg": 0}, 0.01)
    seq_o._action_queue.put_nowait(a1)  # queue 满 (queue_max=1)
    r2 = seq_o.enqueue(a2)
    r3 = seq_o.enqueue(a3)
    drops_o = [e for e in ev_o if e[0] == "robot.enqueue_dropped"]
    check(
        "V3 drop_oldest 触发 emit",
        len(drops_o) >= 1,
        f"got={len(drops_o)}, evs={[e[1] for e in drops_o]}",
    )
    if drops_o:
        check(
            "V3 drop_oldest policy 字段=='drop_oldest'",
            all(p.get("policy") == "drop_oldest" for (_, p) in drops_o),
            f"got={[p.get('policy') for (_, p) in drops_o]}",
        )
        check(
            "V3 drop_oldest reason in {drop_oldest, drop_oldest_retry_full}",
            all(p.get("reason") in ("drop_oldest", "drop_oldest_retry_full") for (_, p) in drops_o),
        )
    # 不显式 shutdown — _is_shutdown=False, _action_stop 已 set; 直接弃用即可

    # drop_new
    seq_n, ev_n = _make_seq(policy="drop_new", queue_max=1)
    seq_n._action_stop.set()
    if seq_n._action_worker is not None:
        seq_n._action_worker.join(timeout=1.0)
    seq_n._action_queue.put_nowait(a1)
    seq_n.enqueue(a2)
    seq_n.enqueue(a3)
    drops_n = [e for e in ev_n if e[0] == "robot.enqueue_dropped"]
    check(
        "V3 drop_new policy 字段=='drop_new'",
        len(drops_n) >= 1
        and all(p.get("policy") == "drop_new" for (_, p) in drops_n),
        f"got policies={[p.get('policy') for (_, p) in drops_n]}",
    )

    # block 策略 — put 阻塞 ~1s 后 timeout drop reason=block_timeout
    # 为节约时间, 我们不真等 block 超时, 只检查空 queue 时 block 不 drop
    seq_b, ev_b = _make_seq(policy="block", queue_max=2)
    seq_b._action_stop.set()
    if seq_b._action_worker is not None:
        seq_b._action_worker.join(timeout=1.0)
    # 入 2 个 (queue_max=2) — 在容量内不会 drop
    seq_b._action_queue.put_nowait(a1)
    seq_b._action_queue.put_nowait(a2)
    # 此时 queue 满, 调 enqueue(a3) 会 put(timeout=1.0), 1s 后超时 emit block_timeout
    t_b0 = time.time()
    r_b = seq_b.enqueue(a3)
    t_be = time.time() - t_b0
    drops_b = [e for e in ev_b if e[0] == "robot.enqueue_dropped"]
    check(
        "V3 block 策略 queue 满 → 阻塞 ~1s 后 emit reason=block_timeout",
        r_b is False
        and len(drops_b) == 1
        and drops_b[0][1].get("reason") == "block_timeout"
        and drops_b[0][1].get("policy") == "block"
        and 0.5 < t_be < 2.0,
        f"r={r_b} drops={drops_b} dt={t_be:.2f}s",
    )
except Exception as e:  # noqa: BLE001
    check("V3 policy 字段", False, f"{type(e).__name__}: {e}")


# --------- V4: 多线程并发 → busy_count 单调不重复 ---------
print("[V4] 多线程并发 _on_enqueue_drop → busy_count 单调不重复")
try:
    os.environ["COCO_ROBOT_BUSY_METRIC"] = "1"
    seq, events = _make_seq(policy="drop_new", queue_max=1)
    THREADS = 8
    PER = 25
    barrier = threading.Barrier(THREADS)

    def _worker():
        barrier.wait()
        for _ in range(PER):
            seq._on_enqueue_drop(reason="drop_new")

    ths = [threading.Thread(target=_worker) for _ in range(THREADS)]
    for th in ths:
        th.start()
    for th in ths:
        th.join(timeout=10)
    drops = [e for e in events if e[0] == "robot.enqueue_dropped"]
    expected = THREADS * PER
    check("V4 emit 总数==THREADS*PER", len(drops) == expected, f"got={len(drops)}")
    busy_set = sorted(p.get("busy_count") for (_, p) in drops if p.get("busy_count") is not None)
    check(
        "V4 busy_count 唯一不重复 == {1..N}",
        busy_set == list(range(1, expected + 1)),
        f"got len={len(busy_set)} min={busy_set[:3] if busy_set else None} max={busy_set[-3:] if busy_set else None}",
    )
    seq.shutdown(wait=True, timeout=0.5)
    os.environ.pop("COCO_ROBOT_BUSY_METRIC", None)
except Exception as e:  # noqa: BLE001
    check("V4 multi-thread", False, f"{type(e).__name__}: {e}")
    os.environ.pop("COCO_ROBOT_BUSY_METRIC", None)


# --------- V5: regression ---------
print("[V5] regression verify_robot_008/009/010/011/012")
for vid in ("008", "009", "010", "011", "012"):
    try:
        env = os.environ.copy()
        env.setdefault("PYTHONPATH", os.getcwd())
        env.pop("COCO_ROBOT_BUSY_METRIC", None)
        p = subprocess.run(
            [sys.executable, f"scripts/verify_robot_{vid}.py"],
            capture_output=True, text=True, timeout=180, env=env,
        )
        check(
            f"verify_robot_{vid} rc==0",
            p.returncode == 0,
            f"rc={p.returncode} tail={p.stdout[-200:]} err={p.stderr[-200:]}",
        )
    except Exception as e:  # noqa: BLE001
        check(f"verify_robot_{vid}", False, f"{type(e).__name__}: {e}")


elapsed = time.time() - t0
print(f"\nelapsed={elapsed:.2f}s errors={len(errors)}")
if errors:
    for e in errors:
        print("  - " + e)
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
