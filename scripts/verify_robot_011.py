"""robot-011 verify: GroupModeCoordinator → RobotSequencer wire (default-OFF).

V0: fingerprint — module 改动指纹
V1: Default-OFF (no env) — 即使注入 sequencer, enter 不 enqueue (bytewise 等价基线)
V2: Wire ON (COCO_GROUP_ROBOT_WIRE=1) — enter 调用 sequencer.enqueue Action(type=head_turn)
V3: Wire ON — exit 也调用 sequencer.enqueue (回中立位)
V4: Negative — set_robot_sequencer(shutdown_seq) 拒绝 + warning; sequencer 注入后 shutdown
    → 下次 enter 跳过 enqueue + 自动清引用
V5: Regression — verify_robot_010 / 009 / 008 子进程 rc==0
"""
from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
import time
from typing import Any, List
from unittest.mock import MagicMock

errors: List[str] = []
t0 = time.time()


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


# 清 env (确保起点 OFF)
for k in (
    "COCO_GROUP_ROBOT_WIRE",
    "COCO_MULTI_USER",
    "COCO_GROUP_MODE",
    "COCO_FACE_ID_ARBIT",
):
    os.environ.pop(k, None)


def _attach_log_capture() -> tuple:
    from coco.companion import group_mode as _mod
    lg = _mod.log
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setLevel(logging.WARNING)
    lg.addHandler(h)
    prev = lg.level
    lg.setLevel(logging.WARNING)
    return lg, h, buf, prev


def _detach_log_capture(lg, h, prev) -> None:
    lg.removeHandler(h)
    lg.setLevel(prev)


# --------- V0: fingerprint ---------
print("[V0] fingerprint")
try:
    from coco.companion.group_mode import GroupModeCoordinator
    check(
        "GroupModeCoordinator.set_robot_sequencer exists",
        callable(getattr(GroupModeCoordinator, "set_robot_sequencer", None)),
    )
    check(
        "GroupModeCoordinator._enqueue_robot_action exists",
        callable(getattr(GroupModeCoordinator, "_enqueue_robot_action", None)),
    )
except Exception as e:  # noqa: BLE001
    check("import GroupModeCoordinator", False, f"{type(e).__name__}: {e}")
    print("ABORT V0 failed")
    sys.exit(1)


# 简易 fake snapshot:
class _TF:
    def __init__(self, name: str) -> None:
        self.name = name


class _Snap:
    def __init__(self, names: List[str]) -> None:
        self.tracks = [_TF(n) for n in names]


def _new_coord(*, sequencer: Any = None, wire_env: str = "0"):
    """新建 coord; 通过 monkey patch os.environ 控制 wire env."""
    # env 直接改, 因为 _group_robot_wire_enabled 在 __init__ 读
    if wire_env == "1":
        os.environ["COCO_GROUP_ROBOT_WIRE"] = "1"
    else:
        os.environ.pop("COCO_GROUP_ROBOT_WIRE", None)
    coord = GroupModeCoordinator(
        enter_hold_s=0.0,  # 立即进入
        exit_hold_s=0.0,
        clock=lambda: time.monotonic(),
    )
    if sequencer is not None:
        coord.set_robot_sequencer(sequencer)
    return coord


# --------- V1: Default-OFF ---------
print("[V1] default-OFF: 注入 sequencer 但 env 未设 → enter 不 enqueue")
try:
    fake_seq = MagicMock()
    # is_shutdown 返回 MagicMock (非严格 bool True), 视为未 shutdown
    fake_seq.is_shutdown.return_value = False
    coord = _new_coord(sequencer=fake_seq, wire_env="0")
    check("coord._robot_sequencer is fake_seq", coord._robot_sequencer is fake_seq)
    check("coord._group_robot_wire_enabled is False", coord._group_robot_wire_enabled is False)
    # 触发 enter
    coord.observe(_Snap(["alice", "bob"]))
    coord.observe(_Snap(["alice", "bob"]))  # 第二次 tick 应跨过 enter_hold_s=0
    check("enter 已触发", coord.is_active())
    # default-OFF: enqueue 不应被调
    check(
        "default-OFF: fake_seq.enqueue NOT called",
        not fake_seq.enqueue.called,
        f"call_count={fake_seq.enqueue.call_count}",
    )
    # exit
    coord.observe(_Snap([]))
    coord.observe(_Snap([]))
    check("exit 已触发", not coord.is_active())
    check(
        "default-OFF: fake_seq.enqueue still NOT called after exit",
        not fake_seq.enqueue.called,
    )
except Exception as e:  # noqa: BLE001
    check("V1 exception", False, f"{type(e).__name__}: {e}")


# --------- V2: Wire ON enter ---------
print("[V2] COCO_GROUP_ROBOT_WIRE=1: enter 调用 sequencer.enqueue (head_turn)")
try:
    from coco.robot.sequencer import Action as _SeqAction
    fake_seq2 = MagicMock()
    fake_seq2.is_shutdown.return_value = False
    coord2 = _new_coord(sequencer=fake_seq2, wire_env="1")
    check("coord2._group_robot_wire_enabled is True", coord2._group_robot_wire_enabled is True)
    coord2.observe(_Snap(["alice", "bob"]))
    coord2.observe(_Snap(["alice", "bob"]))
    check("enter active", coord2.is_active())
    check(
        "fake_seq2.enqueue called once on enter",
        fake_seq2.enqueue.call_count == 1,
        f"count={fake_seq2.enqueue.call_count}",
    )
    if fake_seq2.enqueue.call_count >= 1:
        args, _ = fake_seq2.enqueue.call_args
        action = args[0]
        check("enqueue arg is Action", isinstance(action, _SeqAction))
        check(
            "enter action.type == head_turn",
            getattr(action, "type", None) == "head_turn",
            f"type={getattr(action, 'type', None)!r}",
        )
        check(
            "enter action_id startswith 'group-enter-'",
            getattr(action, "action_id", "").startswith("group-enter-"),
            f"id={getattr(action, 'action_id', None)!r}",
        )
except Exception as e:  # noqa: BLE001
    check("V2 exception", False, f"{type(e).__name__}: {e}")


# --------- V3: Wire ON exit ---------
print("[V3] COCO_GROUP_ROBOT_WIRE=1: exit 也 enqueue (回中立位)")
try:
    fake_seq3 = MagicMock()
    fake_seq3.is_shutdown.return_value = False
    coord3 = _new_coord(sequencer=fake_seq3, wire_env="1")
    coord3.observe(_Snap(["alice", "bob"]))
    coord3.observe(_Snap(["alice", "bob"]))
    enter_count = fake_seq3.enqueue.call_count
    # exit
    coord3.observe(_Snap([]))
    coord3.observe(_Snap([]))
    check("exit 已触发", not coord3.is_active())
    check(
        "fake_seq3.enqueue called twice total (enter + exit)",
        fake_seq3.enqueue.call_count == enter_count + 1,
        f"count={fake_seq3.enqueue.call_count} expect={enter_count + 1}",
    )
    args, _ = fake_seq3.enqueue.call_args  # 最后一次
    action_exit = args[0]
    check(
        "exit action.action_id startswith 'group-exit-'",
        getattr(action_exit, "action_id", "").startswith("group-exit-"),
    )
    check(
        "exit action.params.yaw_deg == 0.0 (中立位)",
        action_exit.params.get("yaw_deg") == 0.0,
        f"params={action_exit.params}",
    )
except Exception as e:  # noqa: BLE001
    check("V3 exception", False, f"{type(e).__name__}: {e}")


# --------- V4: Negative — shutdown 拒绝 + 注入后 shutdown 自清 ---------
print("[V4] negative lifecycle: shutdown 拒绝 + 后 shutdown 自清")
try:
    # 4a: 注入已 shutdown 的 sequencer → 拒绝
    lg, h, buf, prev = _attach_log_capture()
    try:
        bad_seq = MagicMock()
        bad_seq.is_shutdown.return_value = True  # 严格 bool True
        coord4a = _new_coord(sequencer=None, wire_env="1")
        check("coord4a._robot_sequencer is None pre-inject", coord4a._robot_sequencer is None)
        coord4a.set_robot_sequencer(bad_seq)
        check(
            "shutdown sequencer 注入被拒绝 (仍 None)",
            coord4a._robot_sequencer is None,
        )
        log_text = buf.getvalue()
        check(
            "拒绝 log.warning emitted",
            "refuse to inject" in log_text,
            f"log_text head: {log_text[:200]}",
        )
    finally:
        _detach_log_capture(lg, h, prev)

    # 4b: 正常注入, 之后 sequencer shutdown → enter 时跳过 + 清引用
    lg, h, buf, prev = _attach_log_capture()
    try:
        good_seq = MagicMock()
        good_seq.is_shutdown.return_value = False  # 注入时未 shutdown
        coord4b = _new_coord(sequencer=good_seq, wire_env="1")
        check("4b: coord._robot_sequencer is good_seq", coord4b._robot_sequencer is good_seq)
        # 现在改 mock 让 is_shutdown 返回 True
        good_seq.is_shutdown.return_value = True
        coord4b.observe(_Snap(["a", "b"]))
        coord4b.observe(_Snap(["a", "b"]))
        check(
            "4b: enqueue NOT called when post-shutdown",
            not good_seq.enqueue.called,
            f"count={good_seq.enqueue.call_count}",
        )
        check(
            "4b: _robot_sequencer auto-cleared",
            coord4b._robot_sequencer is None,
        )
        log_text = buf.getvalue()
        check(
            "4b: shutdown 自清 log emitted",
            "detected shutdown" in log_text,
            f"log_text head: {log_text[:200]}",
        )
    finally:
        _detach_log_capture(lg, h, prev)

    # 4c: 重复注入 warning
    lg, h, buf, prev = _attach_log_capture()
    try:
        s1 = MagicMock(); s1.is_shutdown.return_value = False
        s2 = MagicMock(); s2.is_shutdown.return_value = False
        coord4c = _new_coord(sequencer=s1, wire_env="1")
        coord4c.set_robot_sequencer(s2)
        check("4c: 二次注入后 _robot_sequencer is s2", coord4c._robot_sequencer is s2)
        log_text = buf.getvalue()
        check(
            "4c: overwriting warning emitted",
            "overwriting existing sequencer" in log_text,
            f"log_text head: {log_text[:200]}",
        )
    finally:
        _detach_log_capture(lg, h, prev)
except Exception as e:  # noqa: BLE001
    check("V4 exception", False, f"{type(e).__name__}: {e}")


# --------- V5: regression ---------
print("[V5] regression: verify_robot_010 / 009 / 008")
for script in ("verify_robot_010.py", "verify_robot_009.py", "verify_robot_008.py"):
    try:
        rc = subprocess.run(
            [sys.executable, f"scripts/{script}"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            timeout=120,
            capture_output=True,
        )
        check(
            f"{script} rc==0",
            rc.returncode == 0,
            f"rc={rc.returncode} stderr_tail={rc.stderr.decode(errors='ignore')[-200:]}",
        )
    except Exception as e:  # noqa: BLE001
        check(f"{script} run", False, f"{type(e).__name__}: {e}")

# 收尾
elapsed = time.time() - t0
print(f"\n[robot-011] elapsed={elapsed:.2f}s errors={len(errors)}")
if errors:
    print("[robot-011] FAIL")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print("[robot-011] PASS")
sys.exit(0)
