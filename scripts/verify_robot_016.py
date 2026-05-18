"""robot-016 verify: set_robot_sequencer lifecycle audit warn-once (default-OFF).

env gate: COCO_ROBOT_SETTER_LIFECYCLE_AUDIT=1 启用 warn-once 去重; 未设置时
完全跳过 dedup, bytewise 等价 robot-010 行为 (始终 WARNING + _setter_audit_seen 仍空 set)。

V1 default-OFF bytewise: env unset → 注入 A, 再注入 B (覆盖) → 2 条 WARNING (dup 不抑制) +
   _setter_audit_seen 仍为空 set
V2 ON warn-once dup: env=1 → 注入 A, 再注入 B, 再注入 B (相同 key) → 第一次 dup 1 条 WARNING,
   第二次 dup 1 条 DEBUG (suppressed); _setter_audit_seen 含 ('dup', ...)
V3 ON probe-raise warn-once: env=1 → is_shutdown 抛 → 第一次 WARNING; 再注入同 sequencer (同
   exc-type) → DEBUG (suppressed); _setter_audit_seen 含 ('probe-fail', id(seq), 'RuntimeError')
V4 OFF probe-raise: env unset → is_shutdown 抛 → 每次 WARNING, _setter_audit_seen 仍空
V5 regression: subprocess 跑 verify_robot_010 / verify_robot_015 rc==0
"""
from __future__ import annotations

import io
import logging
import os
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
    "COCO_ROBOT_SETTER_LIFECYCLE_AUDIT",
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


def _normal_seq():
    seq = MagicMock()
    seq.is_shutdown = MagicMock(return_value=False)
    seq.enqueue = MagicMock(return_value=True)
    return seq


# =======================================================================
# V1 default-OFF bytewise: env unset → dup 始终 WARNING, _setter_audit_seen 空
# =======================================================================
print("V1: default-OFF bytewise (env unset) → 注入 A 再注入 B 应 1 条 dup WARNING + audit_seen 空")
try:
    os.environ.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)
    sched = _new_sched()
    seq_a = _normal_seq()
    seq_b = _normal_seq()

    lg, h, buf = _attach_log_capture()
    try:
        sched.set_robot_sequencer(seq_a)  # 首次, 无 dup warning
        sched.set_robot_sequencer(seq_b)  # 第二次, dup warning
        log_text = buf.getvalue()
        warn_count = _count_lines(log_text, "WARNING", "overwriting existing sequencer")
        debug_count = _count_lines(log_text, "DEBUG", "suppressed warn-once")
        check("V1 OFF: dup overwrite WARNING == 1",
              warn_count == 1, f"got={warn_count}, log={log_text!r}")
        check("V1 OFF: DEBUG suppressed == 0",
              debug_count == 0, f"got={debug_count}")
        check("V1 OFF: _setter_audit_seen 仍为空 set",
              sched._setter_audit_seen == set(),
              f"got={sched._setter_audit_seen!r}")
        check("V1 OFF: 当前 sequencer 已是 seq_b",
              sched._robot_sequencer is seq_b)
    finally:
        _detach_log_capture(lg, h)
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())


# =======================================================================
# V2 ON warn-once dup: env=1 → 注入 A, 再注 B, 再注 B (相同 dup key)
#   注意 key=(id(prev), id(new)) — 第二次 dup 时 prev=seq_b, new=seq_b → 不同 key
#   要复现"相同 key"必须先 A→B→A→B (来回 toggle), 这样第二次 A→B 与第一次 A→B
#   有相同 key (id(A), id(B))。
# =======================================================================
print("V2: ON warn-once dup (env=1) → toggle A→B→A→B 第二次 A→B 应 suppressed")
try:
    os.environ["COCO_ROBOT_SETTER_LIFECYCLE_AUDIT"] = "1"
    sched = _new_sched()
    seq_a = _normal_seq()
    seq_b = _normal_seq()

    lg, h, buf = _attach_log_capture()
    try:
        sched.set_robot_sequencer(seq_a)        # 首次, 无 dup
        sched.set_robot_sequencer(seq_b)        # dup key=(id(a),id(b)) → WARNING
        sched.set_robot_sequencer(seq_a)        # dup key=(id(b),id(a)) → WARNING (新 key)
        sched.set_robot_sequencer(seq_b)        # dup key=(id(a),id(b)) → DEBUG suppressed
        log_text = buf.getvalue()
        warn_count = _count_lines(log_text, "WARNING", "overwriting existing sequencer")
        debug_count = _count_lines(log_text, "DEBUG", "suppressed warn-once")
        check("V2 ON: dup WARNING == 2 (两个不同 key 各一条)",
              warn_count == 2, f"got={warn_count}")
        check("V2 ON: DEBUG suppressed >= 1",
              debug_count >= 1, f"got={debug_count}, log={log_text!r}")
        # _setter_audit_seen 应含 2 个 dup key
        dup_keys = [k for k in sched._setter_audit_seen if k[0] == "dup"]
        check("V2 ON: _setter_audit_seen 含 2 个 dup key",
              len(dup_keys) == 2, f"got={dup_keys!r}")
    finally:
        _detach_log_capture(lg, h)
    os.environ.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)
except Exception:  # noqa: BLE001
    errors.append("V2: " + traceback.format_exc())
    os.environ.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)


# =======================================================================
# V3 ON probe-raise warn-once: env=1 → is_shutdown 抛 RuntimeError →
#   第一次 WARNING + audit_seen 含 ('probe-fail', id(seq), 'RuntimeError');
#   再次注入"同一 sequencer 对象"(同 id + 同 exc-type) → DEBUG suppressed
# =======================================================================
print("V3: ON probe-raise warn-once (env=1) → 同 sequencer 第二次注入应 suppressed")
try:
    os.environ["COCO_ROBOT_SETTER_LIFECYCLE_AUDIT"] = "1"
    sched = _new_sched()
    raise_seq = MagicMock()
    raise_seq.is_shutdown = MagicMock(side_effect=RuntimeError("boom"))
    raise_seq.enqueue = MagicMock(return_value=True)

    lg, h, buf = _attach_log_capture()
    try:
        sched.set_robot_sequencer(raise_seq)  # 第一次 probe-fail WARNING
        # 第一次会注入成功 (fail-soft "未 shutdown"), _robot_sequencer = raise_seq
        # 第二次注入同一对象 — 探针仍抛, key 相同 → DEBUG suppressed
        # 但此时 _robot_sequencer 已 != None, 还会触发 dup warning (key=(id(raise_seq),id(raise_seq)))
        sched.set_robot_sequencer(raise_seq)
        log_text = buf.getvalue()
        warn_probe = _count_lines(log_text, "WARNING", "is_shutdown probe failed")
        debug_probe = _count_lines(log_text, "DEBUG", "probe failed (suppressed warn-once)")
        check("V3 ON: probe-fail WARNING == 1 (首次)",
              warn_probe == 1, f"got={warn_probe}, log={log_text!r}")
        check("V3 ON: probe-fail DEBUG suppressed == 1 (第二次)",
              debug_probe == 1, f"got={debug_probe}")
        probe_keys = [k for k in sched._setter_audit_seen if k[0] == "probe-fail"]
        check("V3 ON: _setter_audit_seen 含 ('probe-fail', id(seq), 'RuntimeError')",
              len(probe_keys) == 1 and probe_keys[0] == ("probe-fail", id(raise_seq), "RuntimeError"),
              f"got={probe_keys!r}")
    finally:
        _detach_log_capture(lg, h)
    os.environ.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())
    os.environ.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)


# =======================================================================
# V4 OFF probe-raise bytewise: env unset → 每次都 WARNING, audit_seen 仍空
# =======================================================================
print("V4: OFF probe-raise (env unset) → 每次都 WARNING, audit_seen 仍空")
try:
    os.environ.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)
    sched = _new_sched()
    raise_seq = MagicMock()
    raise_seq.is_shutdown = MagicMock(side_effect=RuntimeError("boom"))
    raise_seq.enqueue = MagicMock(return_value=True)

    lg, h, buf = _attach_log_capture()
    try:
        sched.set_robot_sequencer(raise_seq)
        sched.set_robot_sequencer(raise_seq)
        log_text = buf.getvalue()
        warn_probe = _count_lines(log_text, "WARNING", "is_shutdown probe failed")
        debug_probe = _count_lines(log_text, "DEBUG", "probe failed (suppressed warn-once)")
        check("V4 OFF: probe-fail WARNING == 2 (每次都 warn)",
              warn_probe == 2, f"got={warn_probe}")
        check("V4 OFF: probe-fail DEBUG suppressed == 0",
              debug_probe == 0, f"got={debug_probe}")
        check("V4 OFF: _setter_audit_seen 仍为空 set",
              sched._setter_audit_seen == set(),
              f"got={sched._setter_audit_seen!r}")
    finally:
        _detach_log_capture(lg, h)
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5 regression — verify_robot_010 子进程 rc==0
# 注: verify_robot_015 自身链跑 007/008/012/013 嵌套 ~7min, 不在此 verify
#     直接 run; 改由 ./init.sh smoke 覆盖 robot-015 回归。
# =======================================================================
print("V5: regression — verify_robot_010 子进程 rc==0")
try:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("verify_robot_010.py",):
        path = os.path.join(repo_root, "scripts", name)
        if not os.path.exists(path):
            check(f"V5 {name} exists", False, f"missing: {path}")
            continue
        env = {k: v for k, v in os.environ.items()
               if k != "COCO_ROBOT_SETTER_LIFECYCLE_AUDIT"}
        env.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)
        proc = subprocess.run(
            [sys.executable, path],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        check(f"V5 {name} rc==0",
              proc.returncode == 0,
              f"rc={proc.returncode}, stderr_tail={proc.stderr[-300:]!r}")
except Exception:  # noqa: BLE001
    errors.append("V5: " + traceback.format_exc())


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
