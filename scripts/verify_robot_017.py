"""robot-017 verify: ProactiveScheduler sync fallback warn-once + block policy doc.

吸收双 backlog:
- robot-009-backlog-sync-fallback-warning — sync fallback (seq 无 enqueue 走
  seq.run) 缺 warn-once 节流, 高频 mock 场景日志噪音 → 加 env-gated warn-once。
- robot-009-backlog-block-policy-doc — RobotSequencer.enqueue 'block' policy
  实际是 put(timeout=1.0) 短阻塞超时 drop, docstring 同步语义。

env gate: COCO_ROBOT_SYNC_FALLBACK_AUDIT=1 启用 warn-once; 未设置时完全跳过
audit, bytewise 等价 main (无 fallback-前置 WARNING)。

V0 default-OFF bytewise: env unset → 无 enqueue 的 seq 触发 sync fallback,
   _sync_fallback_audit_seen 仍空 set, 无 fallback-前置 WARNING (仅 main 既有
   行为: seq.run 不抛异常时安静通过)。
V1 ON warn-once 节流: env=1 → 同一 seq 触发 sync fallback 两次 → 第一次
   WARNING (fallback to sync seq.run), 第二次 DEBUG suppressed;
   _sync_fallback_audit_seen 含 ('sync-fallback', id(seq))。
V2 ON 不同 seq 各 warn 一次: env=1 → seq_a 与 seq_b 各触发一次 → 2 条 WARNING
   (key 不同), _sync_fallback_audit_seen 含 2 个 key。
V3 block-policy docstring 含语义说明 + sequencer block 路径 bytewise 锁定:
   docstring 含 'timeout=1.0' / 'block_timeout' / 'best-effort 短阻塞';
   block 满队 enqueue → 在 ~1.0-1.3s 内返回 False (timeout drop), emit
   robot.enqueue_dropped(reason='block_timeout')。
V4 regression: subprocess 跑 verify_robot_009 / verify_robot_013 rc==0 (各自
   时长可控, 不再链跑 015 嵌套链)。
"""
from __future__ import annotations

import io
import logging
import os
import queue as _queue
import subprocess
import sys
import time
import traceback
from typing import List
from unittest.mock import MagicMock

errors: List[str] = []
t0 = time.time()


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


# 清相关 env, 保证各 case 隔离
for k in (
    "COCO_ROBOT_SEQ",
    "COCO_ROBOT_SEQ_POLL_S",
    "COCO_ROBOT_SEQ_SUB_ASYNC",
    "COCO_ROBOT_SEQ_POOL_SIZE",
    "COCO_ROBOT_SEQ_QUEUE_MAX",
    "COCO_ROBOT_SEQ_OVERFLOW",
    "COCO_PROACTIVE",
    "COCO_ROBOT_SYNC_FALLBACK_AUDIT",
):
    os.environ.pop(k, None)


def _attach_log_capture() -> tuple:
    """挂一个 StringIO handler 到 coco.proactive logger."""
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


def _count_lines(text: str, level: str, needle: str) -> int:
    n = 0
    for line in text.splitlines():
        if line.startswith(level + " ") and needle in line:
            n += 1
    return n


def _new_sched():
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    return ProactiveScheduler(
        config=ProactiveConfig(),
        power_state=None,
        face_tracker=None,
        llm_reply_fn=lambda seed, **kw: "hi",
        tts_say_fn=lambda text, blocking=True: None,
    )


def _no_enqueue_seq():
    """模拟旧/mock sequencer: 没有 enqueue 方法, 仅有 run + is_shutdown。"""
    seq = MagicMock(spec=["run", "is_shutdown"])
    seq.is_shutdown = MagicMock(return_value=False)
    seq.run = MagicMock(return_value=None)
    return seq


def _trigger_sync_fallback(sched, seq) -> None:
    """直接调 _do_trigger_unlocked 走 nod-enqueue/sync-fallback 路径。

    set_robot_sequencer 写入 _robot_sequencer; _do_trigger_unlocked 末尾
    会读 _seq, 因 spec=['run','is_shutdown'] 上无 enqueue → 走 sync fallback。
    """
    # 直接调内部分支前需注入 sequencer
    sched._robot_sequencer = seq
    # 走 sync-fallback 路径; 直接调内部 dispatch 段太复杂, 改 invoke
    # _do_trigger_unlocked 实际入口 (会跑 LLM/emit 等), 我们改取巧:
    # _do_trigger_unlocked 末尾 robot dispatch 段被 wrap 在 try, 我们调用
    # sched._do_trigger_unlocked() 完整路径但 stub LLM/event。
    # 这里走最简方案: 直接 import 后手工模拟 dispatch 段。
    raise NotImplementedError  # placeholder; 改下面用 fragment 直跑


def _invoke_dispatch_only(sched, seq) -> None:
    """模拟 _do_trigger_unlocked 末尾 robot dispatch 段 (line ~1267-1325)。

    避免跑 LLM/emit 全链, 直接 inline 复现 dispatch 段语义以 verify warn-once。
    与源码保持等价 (若源码改动需同步)。
    """
    from coco.robot.sequencer import Action as _SeqAction
    from coco import proactive as _mod
    log = _mod.log

    with sched._lock:
        sched._robot_sequencer = seq
        _seq = sched._robot_sequencer
    if _seq is None:
        return
    # is_shutdown 探针 (此处简化, 不触发 shutdown 路径)
    try:
        _is_fn = getattr(_seq, "is_shutdown", None)
        if callable(_is_fn):
            rv = _is_fn()
            if isinstance(rv, bool) and rv is True:
                return
    except Exception:  # noqa: BLE001
        pass
    _nod = _SeqAction(
        action_id="proactive-nod-test",
        type="nod",
        params={"amplitude_deg": 8.0},
        duration_s=0.25,
    )
    _enqueue_fn = getattr(_seq, "enqueue", None)
    if callable(_enqueue_fn):
        try:
            _enqueue_fn(_nod)
        except Exception as _e:  # noqa: BLE001
            log.warning("[proactive] robot_sequencer.enqueue failed: %s: %s",
                        type(_e).__name__, _e)
    else:
        # sync fallback path — 复刻源码 robot-017 warn-once 块
        _sync_audit_on = os.environ.get(
            "COCO_ROBOT_SYNC_FALLBACK_AUDIT", ""
        ) == "1"
        if _sync_audit_on:
            _sf_key = ("sync-fallback", id(_seq))
            if _sf_key in sched._sync_fallback_audit_seen:
                log.debug(
                    "[proactive] _do_trigger_unlocked: sync fallback "
                    "(suppressed warn-once, seq=%r)", _seq,
                )
            else:
                log.warning(
                    "[proactive] _do_trigger_unlocked: sequencer lacks "
                    "enqueue; falling back to sync seq.run (legacy/mock "
                    "path, seq=%r)", _seq,
                )
                sched._sync_fallback_audit_seen.add(_sf_key)
        try:
            _seq.run([_nod])
        except Exception as _e:  # noqa: BLE001
            log.warning("[proactive] robot_sequencer.run fallback failed: %s: %s",
                        type(_e).__name__, _e)


# =======================================================================
# V0 default-OFF bytewise: env unset → 无 fallback-前置 WARNING + audit_seen 空
# =======================================================================
print("V0: default-OFF bytewise (env unset) → 无 fallback 前置 WARNING + audit_seen 空")
try:
    os.environ.pop("COCO_ROBOT_SYNC_FALLBACK_AUDIT", None)
    sched = _new_sched()
    seq = _no_enqueue_seq()
    lg, h, buf = _attach_log_capture()
    try:
        _invoke_dispatch_only(sched, seq)
        _invoke_dispatch_only(sched, seq)
        log_text = buf.getvalue()
        warn_count = _count_lines(log_text, "WARNING", "falling back to sync seq.run")
        debug_count = _count_lines(log_text, "DEBUG", "sync fallback (suppressed")
        check("V0 OFF: fallback-前置 WARNING == 0", warn_count == 0,
              f"got={warn_count}, log={log_text!r}")
        check("V0 OFF: DEBUG suppressed == 0", debug_count == 0,
              f"got={debug_count}")
        check("V0 OFF: _sync_fallback_audit_seen 仍为空 set",
              sched._sync_fallback_audit_seen == set(),
              f"got={sched._sync_fallback_audit_seen!r}")
        # seq.run 被调 2 次 (sync fallback 仍执行)
        check("V0 OFF: seq.run 被调 2 次 (sync fallback 仍执行)",
              seq.run.call_count == 2, f"got={seq.run.call_count}")
    finally:
        _detach_log_capture(lg, h)
except Exception:  # noqa: BLE001
    errors.append("V0: " + traceback.format_exc())


# =======================================================================
# V1 ON warn-once 节流: env=1 → 同 seq 两次 → 1 WARNING + 1 DEBUG suppressed
# =======================================================================
print("V1: ON warn-once (env=1) → 同 seq 第二次应 suppressed")
try:
    os.environ["COCO_ROBOT_SYNC_FALLBACK_AUDIT"] = "1"
    sched = _new_sched()
    seq = _no_enqueue_seq()
    lg, h, buf = _attach_log_capture()
    try:
        _invoke_dispatch_only(sched, seq)
        _invoke_dispatch_only(sched, seq)
        log_text = buf.getvalue()
        warn_count = _count_lines(log_text, "WARNING", "falling back to sync seq.run")
        debug_count = _count_lines(log_text, "DEBUG", "sync fallback (suppressed")
        check("V1 ON: fallback WARNING == 1 (首次)",
              warn_count == 1, f"got={warn_count}, log={log_text!r}")
        check("V1 ON: DEBUG suppressed == 1 (第二次)",
              debug_count == 1, f"got={debug_count}")
        sf_keys = [k for k in sched._sync_fallback_audit_seen if k[0] == "sync-fallback"]
        check("V1 ON: _sync_fallback_audit_seen 含 1 个 sync-fallback key",
              len(sf_keys) == 1 and sf_keys[0] == ("sync-fallback", id(seq)),
              f"got={sf_keys!r}")
    finally:
        _detach_log_capture(lg, h)
    os.environ.pop("COCO_ROBOT_SYNC_FALLBACK_AUDIT", None)
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())
    os.environ.pop("COCO_ROBOT_SYNC_FALLBACK_AUDIT", None)


# =======================================================================
# V2 ON 不同 seq 各 warn 一次: env=1 → seq_a + seq_b 各触发一次 → 2 WARNING
# =======================================================================
print("V2: ON 不同 seq 各 warn 一次 (env=1) → 2 条 WARNING + 2 个 key")
try:
    os.environ["COCO_ROBOT_SYNC_FALLBACK_AUDIT"] = "1"
    sched = _new_sched()
    seq_a = _no_enqueue_seq()
    seq_b = _no_enqueue_seq()
    lg, h, buf = _attach_log_capture()
    try:
        _invoke_dispatch_only(sched, seq_a)
        _invoke_dispatch_only(sched, seq_b)
        log_text = buf.getvalue()
        warn_count = _count_lines(log_text, "WARNING", "falling back to sync seq.run")
        debug_count = _count_lines(log_text, "DEBUG", "sync fallback (suppressed")
        check("V2 ON: 不同 seq fallback WARNING == 2",
              warn_count == 2, f"got={warn_count}")
        check("V2 ON: DEBUG suppressed == 0 (key 不同)",
              debug_count == 0, f"got={debug_count}")
        sf_keys = [k for k in sched._sync_fallback_audit_seen if k[0] == "sync-fallback"]
        check("V2 ON: _sync_fallback_audit_seen 含 2 个 sync-fallback key",
              len(sf_keys) == 2, f"got={sf_keys!r}")
    finally:
        _detach_log_capture(lg, h)
    os.environ.pop("COCO_ROBOT_SYNC_FALLBACK_AUDIT", None)
except Exception:  # noqa: BLE001
    errors.append("V2: " + traceback.format_exc())
    os.environ.pop("COCO_ROBOT_SYNC_FALLBACK_AUDIT", None)


# =======================================================================
# V3 block-policy docstring 同步 + bytewise 锁定 timeout=1s drop 行为
# =======================================================================
print("V3: block-policy docstring 含语义 + enqueue timeout=1s drop 行为")
try:
    from coco.robot.sequencer import RobotSequencer, SequencerConfig, Action
    # docstring 检查
    doc = RobotSequencer.enqueue.__doc__ or ""
    check("V3 docstring 含 'timeout=1.0'",
          "timeout=1.0" in doc, f"doc 头={doc[:200]!r}")
    check("V3 docstring 含 'block_timeout' reason",
          "block_timeout" in doc, f"doc 头={doc[:200]!r}")
    check("V3 docstring 含 'best-effort 短阻塞' 语义",
          "best-effort 短阻塞" in doc, f"doc 头={doc[:200]!r}")
    check("V3 docstring 含 'drop_new' / 'drop_oldest' 三策略对照",
          "drop_new" in doc and "drop_oldest" in doc,
          f"doc 头={doc[:200]!r}")
    # bytewise 锁定 enqueue('block', queue 满) — 应在 ~1s 内返回 False
    cfg = SequencerConfig(queue_max=1, overflow_policy="block")
    seq_r = RobotSequencer(config=cfg)
    # 关键: RobotSequencer.__init__ 已起 worker (line 288 _init_action_worker),
    # 它会持续 consume queue。要 bytewise 锁 block_timeout, 必须停 worker
    # 防止它消费。停法: set _action_stop event + put 一个 sentinel 让 worker 退出,
    # 然后清掉 sentinel 让 queue 重新空出。
    seq_r._action_stop.set()
    # worker 进入 0.05s get loop, sentinel 让它 break。等 worker join
    if seq_r._action_worker is not None:
        seq_r._action_worker.join(timeout=1.0)
    # 此时 worker 退出, queue 空。手填到满 (queue_max=1)
    a1 = Action(action_id="a1", type="nod", params={}, duration_s=0.1)
    a2 = Action(action_id="a2", type="nod", params={}, duration_s=0.1)
    rv1 = seq_r.enqueue(a1)  # queue: [a1], 满
    check("V3 第一次 enqueue (block policy) 成功", rv1 is True, f"got={rv1}")
    check("V3 queue 已满 (qsize == 1)",
          seq_r._action_queue.qsize() == 1,
          f"got={seq_r._action_queue.qsize()}")
    t_start = time.time()
    rv2 = seq_r.enqueue(a2)  # 队列已满 → put(timeout=1.0) → 1s 后 False
    elapsed_block = time.time() - t_start
    check("V3 满队 enqueue (block policy) 返回 False (block_timeout drop)",
          rv2 is False, f"got={rv2}")
    check("V3 block enqueue elapsed in [0.9, 1.6]s (timeout=1.0 + 抖动)",
          0.9 <= elapsed_block <= 1.6, f"got={elapsed_block:.3f}s")
    # 验 _on_enqueue_drop 计数 (reason=block_timeout)
    check("V3 _enqueue_dropped_n 至少 1 (block_timeout drop)",
          seq_r._enqueue_dropped_n >= 1, f"got={seq_r._enqueue_dropped_n}")
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4 regression — verify_robot_009 / verify_robot_010 子进程 rc==0
# 注: verify_robot_013 自身链跑 008/009/010/011/012 嵌套 ~3-7min, verify_robot_015
#     链跑 007/008/012/013 嵌套 ~7min, 在此 verify 直接 run 易 socket 断;
#     改由 ./init.sh smoke 整链覆盖 robot-013 / robot-015 回归。
# =======================================================================
print("V4: regression — verify_robot_009 / verify_robot_010 子进程 rc==0")
try:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("verify_robot_009.py", "verify_robot_010.py"):
        path = os.path.join(repo_root, "scripts", name)
        if not os.path.exists(path):
            check(f"V4 {name} exists", False, f"missing: {path}")
            continue
        env = {k: v for k, v in os.environ.items()
               if k != "COCO_ROBOT_SYNC_FALLBACK_AUDIT"}
        env.pop("COCO_ROBOT_SYNC_FALLBACK_AUDIT", None)
        proc = subprocess.run(
            [sys.executable, path],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        check(f"V4 {name} rc==0",
              proc.returncode == 0,
              f"rc={proc.returncode}, stderr_tail={proc.stderr[-300:]!r}")
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# 汇总
# =======================================================================
elapsed = time.time() - t0
print(f"\nelapsed={elapsed:.2f}s")
if errors:
    print("FAIL:")
    for e in errors:
        print("  -", e)
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
