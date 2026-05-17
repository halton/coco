"""robot-015 verify: ProactiveScheduler→Sequencer callback 直 enqueue contract 锁定.

吸收 backlog robot-008-backlog-enqueue-not-daemon-thread.

背景: robot-009 已落地源码改造 (enqueue-first, 移除 daemon thread + seq.run 外部驱动).
robot-015 聚焦在 verify + evidence 锁定该 contract, 防止未来回归到 daemon thread spawn.

V1 contract: 注入后 trigger callback → seq.enqueue 真调 1 次 + 无 coco-proactive-robot-seq daemon thread spawn
V2 fallback: mock 无 enqueue 属性 → 走 seq.run 同步兜底 + 同样无 daemon thread spawn
V3 enqueue 抛异常: warn-once 吃掉 + 不起线程 + proactive emit 不阻断
V4 default-OFF: 未注入 sequencer 时 _do_trigger_unlocked 全程不访问 enqueue/run/daemon thread
V5 regression: verify_robot_007 / 008 / 012 / 013 子进程 rc==0
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock

errors: List[str] = []
t0 = time.time()


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


# 清掉可能影响 default-OFF 行为的 env
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


def _n_seq_threads() -> int:
    return sum(
        1 for t in threading.enumerate()
        if t.name.startswith("coco-proactive-robot-seq")
    )


def _make_sched():
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    return ProactiveScheduler(
        config=ProactiveConfig(),
        power_state=None,
        face_tracker=None,
        llm_reply_fn=lambda seed, **kw: f"hello-{seed[:20]}",
        tts_say_fn=lambda text, blocking=True: None,
    )


# =======================================================================
# V1 enqueue-first contract: enqueue 调一次 + 无 daemon thread spawn
# =======================================================================
print("V1: 注入 sequencer → trigger callback 走 enqueue, 无 daemon thread spawn")
try:
    fake_seq = MagicMock()
    fake_seq.enqueue = MagicMock(return_value=True)
    fake_seq.run = MagicMock(return_value={"executed": 1, "cancelled": False})
    # is_shutdown 显式 stub 为 False, 避免 MagicMock 默认返回 MagicMock(被视为非 bool 透传)
    fake_seq.is_shutdown = MagicMock(return_value=False)

    sched = _make_sched()
    sched.set_robot_sequencer(fake_seq)

    n_before = _n_seq_threads()
    sched._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="topic-seed")
    n_after = _n_seq_threads()

    check(
        "V1 enqueue.call_count == 1",
        fake_seq.enqueue.call_count == 1,
        f"got={fake_seq.enqueue.call_count}",
    )
    check(
        "V1 run.call_count == 0 (不再走 run 路径)",
        fake_seq.run.call_count == 0,
        f"got={fake_seq.run.call_count}",
    )
    check(
        "V1 无 coco-proactive-robot-seq daemon thread spawn",
        n_after == n_before == 0,
        f"before={n_before} after={n_after}",
    )
    if fake_seq.enqueue.call_count >= 1:
        args, _ = fake_seq.enqueue.call_args
        action = args[0] if args else None
        check(
            "V1 enqueue 投递 action.type == 'nod'",
            getattr(action, "type", None) == "nod",
            f"type={getattr(action, 'type', None)!r}",
        )
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())


# =======================================================================
# V2 fallback: 缺 enqueue → seq.run 同步兜底 + 无 daemon thread spawn
# =======================================================================
print("V2: fallback — sequencer 缺 enqueue → seq.run 同步兜底, 无 daemon thread spawn")
try:
    class LegacySeq:
        """无 enqueue, 仅有 run; 模拟 robot-009 之前的旧 sequencer 协议."""

        def __init__(self) -> None:
            self.run_calls: List[Any] = []
            self.is_shutdown_calls = 0

        def is_shutdown(self) -> bool:
            self.is_shutdown_calls += 1
            return False

        def run(self, actions):  # noqa: ANN001
            self.run_calls.append(list(actions))
            return {"executed": len(actions), "cancelled": False}

    legacy = LegacySeq()
    assert not hasattr(legacy, "enqueue"), "前置: LegacySeq 必须无 enqueue 属性"

    sched2 = _make_sched()
    sched2.set_robot_sequencer(legacy)

    n_before = _n_seq_threads()
    sched2._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="topic-seed-2")
    n_after = _n_seq_threads()

    check(
        "V2 legacy.run 至少被调用一次 (fallback)",
        len(legacy.run_calls) == 1,
        f"got={len(legacy.run_calls)}",
    )
    if legacy.run_calls:
        first = legacy.run_calls[0]
        check(
            "V2 fallback run actions 长度 == 1 且 type=='nod'",
            len(first) == 1 and getattr(first[0], "type", None) == "nod",
            f"actions={first}",
        )
    check(
        "V2 fallback 无 coco-proactive-robot-seq daemon thread spawn",
        n_after == n_before == 0,
        f"before={n_before} after={n_after}",
    )
except Exception:  # noqa: BLE001
    errors.append("V2: " + traceback.format_exc())


# =======================================================================
# V3 enqueue 抛异常: warn-once 吃掉, 不起线程, proactive 流程不阻断
# =======================================================================
print("V3: enqueue 抛异常 → fail-soft, 不起 daemon thread, proactive 不阻断")
try:
    boom_seq = MagicMock()
    boom_seq.enqueue = MagicMock(side_effect=RuntimeError("boom"))
    boom_seq.run = MagicMock(return_value={"executed": 0, "cancelled": False})
    boom_seq.is_shutdown = MagicMock(return_value=False)

    sched3 = _make_sched()
    sched3.set_robot_sequencer(boom_seq)

    n_before = _n_seq_threads()
    raised = False
    try:
        sched3._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="topic-seed-3")
    except Exception:  # noqa: BLE001
        raised = True
    n_after = _n_seq_threads()

    check("V3 _do_trigger_unlocked 不向外抛 (异常被吃掉)", raised is False)
    check(
        "V3 enqueue 仍被调用一次 (走了 enqueue 分支)",
        boom_seq.enqueue.call_count == 1,
        f"got={boom_seq.enqueue.call_count}",
    )
    check(
        "V3 enqueue 抛异常后不再回退到 run",
        boom_seq.run.call_count == 0,
        f"got={boom_seq.run.call_count}",
    )
    check(
        "V3 异常路径无 daemon thread spawn",
        n_after == n_before == 0,
        f"before={n_before} after={n_after}",
    )
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4 default-OFF: 未注入 sequencer → 全程不访问 enqueue/run/daemon thread
# =======================================================================
print("V4: default-OFF — 未注入 sequencer, _do_trigger_unlocked 不进 enqueue 分支")
try:
    sched4 = _make_sched()
    # 显式不 set_robot_sequencer

    n_before = _n_seq_threads()
    raised = False
    try:
        sched4._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="topic-seed-4")
    except Exception:  # noqa: BLE001
        raised = True
    n_after = _n_seq_threads()

    check("V4 default-OFF 不抛异常", raised is False)
    check(
        "V4 default-OFF 无 daemon thread spawn",
        n_after == n_before == 0,
        f"before={n_before} after={n_after}",
    )
    check(
        "V4 _robot_sequencer 仍为 None",
        sched4._robot_sequencer is None,
        f"got={sched4._robot_sequencer!r}",
    )
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5 regression: verify_robot_007 / 008 / 012 / 013 子进程 rc==0
# =======================================================================
print("V5: regression — verify_robot_007/008/012/013 子进程 rc==0")
regression_targets = [
    "scripts/verify_robot_007.py",
    "scripts/verify_robot_008.py",
    "scripts/verify_robot_012.py",
    "scripts/verify_robot_013.py",
]
regression_rcs: dict = {}
for script in regression_targets:
    try:
        rc = subprocess.call(
            [sys.executable, script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=300,
        )
        regression_rcs[script] = rc
        check(f"V5 {script} rc==0", rc == 0, f"rc={rc}")
    except subprocess.TimeoutExpired:
        regression_rcs[script] = "timeout"
        check(f"V5 {script} rc==0", False, "timeout")
    except Exception as e:  # noqa: BLE001
        regression_rcs[script] = f"exc:{type(e).__name__}"
        check(f"V5 {script} rc==0", False, f"exc={e}")


# =======================================================================
# 汇总 + 写 evidence
# =======================================================================
elapsed = time.time() - t0
print()
if errors:
    print(f"FAIL ({len(errors)} errors, {elapsed:.2f}s)")
    for e in errors:
        print(f"  - {e[:300]}")
else:
    print(f"PASS (all checks, {elapsed:.2f}s)")

evidence_dir = Path(__file__).resolve().parent.parent / "evidence" / "robot-015"
evidence_dir.mkdir(parents=True, exist_ok=True)
summary = {
    "feature": "robot-015",
    "title": "ProactiveScheduler→Sequencer callback 直 enqueue contract 锁定",
    "elapsed_s": round(elapsed, 3),
    "errors": errors,
    "regression_rcs": regression_rcs,
    "contract": {
        "enqueue_first": "callable(seq.enqueue) → 调一次 enqueue(nod_action)",
        "fallback_sync_run": "缺 enqueue → 同步 seq.run([nod_action]), 不起线程",
        "no_daemon_thread_spawn": "_do_trigger_unlocked 不再 spawn coco-proactive-robot-seq daemon thread",
        "exception_fail_soft": "enqueue 抛异常 → warn-once, 不回退 run, proactive emit 不阻断",
        "default_off": "未注入 _robot_sequencer → 全程 no-op",
    },
    "source_backlog": ["robot-008-backlog-enqueue-not-daemon-thread"],
    "source_code_note": (
        "源码改造已在 robot-009 落地 (coco/proactive.py:1226-1283). "
        "robot-015 为 verify-only contract 锁定 + docstring 锁定 + evidence 固化."
    ),
}
(evidence_dir / "verify_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

sys.exit(1 if errors else 0)
