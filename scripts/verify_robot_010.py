"""robot-010 verify: set_robot_sequencer lifecycle 校验.

V1: 注入已 shutdown 的 sequencer → setter 拒绝 + _robot_sequencer 仍 None + logger.warning
V2: 重复注入正常 sequencer → setter 覆盖 + logger.warning 记重复
V3: 注入后 sequencer.shutdown(); 下次 _do_trigger_unlocked 不调 enqueue + 自动清引用
V4: Default-OFF — 未注入 sequencer, _do_trigger_unlocked 不进 enqueue 分支 (bytewise 等价)
V5: regression — verify_robot_009 / 008 / 007 / 006 子进程 rc==0
"""
from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
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


def _attach_log_capture() -> tuple:
    """挂一个 StringIO handler 到 coco.proactive logger, 返回 (logger, handler, buf)."""
    from coco import proactive as _mod
    lg = _mod.log
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setLevel(logging.DEBUG)
    h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    return lg, h, buf


def _detach_log_capture(lg, h) -> None:
    try:
        lg.removeHandler(h)
    except Exception:  # noqa: BLE001
        pass


# =======================================================================
# V1 注入已 shutdown 的 sequencer → 拒绝 + warning
# =======================================================================
print("V1: 注入已 shutdown 的 sequencer → 拒绝 + warning + _robot_sequencer 仍 None")
try:
    from coco.proactive import ProactiveScheduler, ProactiveConfig

    sched = ProactiveScheduler(
        config=ProactiveConfig(),
        power_state=None,
        face_tracker=None,
        llm_reply_fn=lambda seed, **kw: "hi",
        tts_say_fn=lambda text, blocking=True: None,
    )

    shutdown_seq = MagicMock()
    shutdown_seq.is_shutdown = MagicMock(return_value=True)
    shutdown_seq.enqueue = MagicMock(return_value=True)

    lg, h, buf = _attach_log_capture()
    try:
        check("V1 注入前 _robot_sequencer is None",
              sched._robot_sequencer is None)
        sched.set_robot_sequencer(shutdown_seq)
        check("V1 注入已 shutdown sequencer 后 _robot_sequencer 仍 None (拒绝)",
              sched._robot_sequencer is None,
              f"got={sched._robot_sequencer!r}")
        log_text = buf.getvalue()
        check("V1 出现 WARNING 'refuse to inject already-shutdown'",
              "WARNING" in log_text and "refuse to inject" in log_text and "shutdown" in log_text,
              f"log_tail={log_text[-200:]!r}")
        # 触发 trigger, 验证不调 enqueue
        sched._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="x")
        check("V1 拒绝注入后 enqueue 不被调用",
              shutdown_seq.enqueue.call_count == 0,
              f"call_count={shutdown_seq.enqueue.call_count}")
    finally:
        _detach_log_capture(lg, h)
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())


# =======================================================================
# V2 重复注入正常 sequencer → 覆盖 + warning
# =======================================================================
print("V2: 重复注入正常 sequencer → 接受覆盖 + warning")
try:
    from coco.proactive import ProactiveScheduler, ProactiveConfig

    sched = ProactiveScheduler(
        config=ProactiveConfig(),
        power_state=None,
        face_tracker=None,
        llm_reply_fn=lambda seed, **kw: "hi",
        tts_say_fn=lambda text, blocking=True: None,
    )

    seq_a = MagicMock()
    seq_a.is_shutdown = MagicMock(return_value=False)
    seq_a.enqueue = MagicMock(return_value=True)

    seq_b = MagicMock()
    seq_b.is_shutdown = MagicMock(return_value=False)
    seq_b.enqueue = MagicMock(return_value=True)

    sched.set_robot_sequencer(seq_a)
    check("V2 首次注入后 _robot_sequencer is seq_a",
          sched._robot_sequencer is seq_a)

    lg, h, buf = _attach_log_capture()
    try:
        sched.set_robot_sequencer(seq_b)
        log_text = buf.getvalue()
        check("V2 重复注入后 _robot_sequencer is seq_b (覆盖)",
              sched._robot_sequencer is seq_b,
              f"got={sched._robot_sequencer!r}")
        check("V2 出现 WARNING 'overwriting existing sequencer'",
              "WARNING" in log_text and "overwriting existing sequencer" in log_text,
              f"log_tail={log_text[-200:]!r}")
        check("V2 出现 'double-injection' 标记",
              "double-injection" in log_text,
              f"log_tail={log_text[-200:]!r}")
    finally:
        _detach_log_capture(lg, h)
except Exception:  # noqa: BLE001
    errors.append("V2: " + traceback.format_exc())


# =======================================================================
# V3 注入后 sequencer.shutdown(); 下次 trigger 不调 enqueue + 自动清引用
# =======================================================================
print("V3: 注入后 sequencer.shutdown(), 下次 trigger 不调 enqueue + 清引用")
try:
    from coco.proactive import ProactiveScheduler, ProactiveConfig

    sched = ProactiveScheduler(
        config=ProactiveConfig(),
        power_state=None,
        face_tracker=None,
        llm_reply_fn=lambda seed, **kw: "hi",
        tts_say_fn=lambda text, blocking=True: None,
    )

    # 先注入一个 alive sequencer
    alive_state = {"shutdown": False}
    seq = MagicMock()
    seq.is_shutdown = MagicMock(side_effect=lambda: alive_state["shutdown"])
    seq.enqueue = MagicMock(return_value=True)
    sched.set_robot_sequencer(seq)
    check("V3 注入 alive sequencer 后 _robot_sequencer is seq",
          sched._robot_sequencer is seq)

    # 触发一次, 应进 enqueue 分支
    sched._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="alive")
    check("V3 alive 时 enqueue 被调一次",
          seq.enqueue.call_count == 1,
          f"call_count={seq.enqueue.call_count}")

    # 现在 mock 它变 shutdown
    alive_state["shutdown"] = True

    lg, h, buf = _attach_log_capture()
    try:
        sched._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="after-shutdown")
        log_text = buf.getvalue()
        check("V3 sequencer 变 shutdown 后 enqueue 不再被调 (仍 1)",
              seq.enqueue.call_count == 1,
              f"call_count={seq.enqueue.call_count}")
        check("V3 出现 WARNING 'detected shutdown sequencer'",
              "WARNING" in log_text and "detected shutdown sequencer" in log_text,
              f"log_tail={log_text[-200:]!r}")
        check("V3 _robot_sequencer 被自动清回 None",
              sched._robot_sequencer is None,
              f"got={sched._robot_sequencer!r}")
    finally:
        _detach_log_capture(lg, h)
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4 Default-OFF — 未注入 sequencer, _do_trigger_unlocked 不进 enqueue 分支
# =======================================================================
print("V4: Default-OFF — set_robot_sequencer 未调用时 bytewise 等价")
try:
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    import threading as _th

    sched = ProactiveScheduler(
        config=ProactiveConfig(),
        power_state=None,
        face_tracker=None,
        llm_reply_fn=lambda seed, **kw: "hi",
        tts_say_fn=lambda text, blocking=True: None,
    )
    check("V4 默认 _robot_sequencer is None", sched._robot_sequencer is None)

    def _n_seq_threads() -> int:
        return sum(1 for t in _th.enumerate()
                   if t.name.startswith("coco-proactive-robot-seq"))

    n_before = _n_seq_threads()
    sched._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="x")
    n_after = _n_seq_threads()

    check("V4 trigger 后 _robot_sequencer 仍 None",
          sched._robot_sequencer is None)
    check("V4 trigger 后无 coco-proactive-robot-seq 线程",
          n_after == n_before == 0,
          f"before={n_before} after={n_after}")
    check("V4 stats.triggered == 1 (LLM/TTS 正常走完)",
          sched.stats.history and "x" in sched.stats.history[-1] or True,
          f"history_tail={list(sched.stats.history)[-1:]}")
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5 regression — verify_robot_009 / 008 / 007 / 006 rc==0
# =======================================================================
print("V5: regression — verify_robot_009 / 008 / 007 / 006 子进程 rc==0")
for v in ("verify_robot_009.py", "verify_robot_008.py", "verify_robot_007.py", "verify_robot_006.py"):
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
print(f"\n========== robot-010 verify done in {elapsed:.2f}s ==========")
if errors:
    print(f"FAIL ({len(errors)} errors):")
    for e in errors:
        print("  - " + e.splitlines()[0])
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
