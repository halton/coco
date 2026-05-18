"""robot-030 verify: ProactiveScheduler enqueue fallback per-instance warn-once meta-lock (V0-V5).

source backlog: robot-009-backlog-sync-fallback-warning
scope: 业务源码少量行 — ProactiveScheduler.__init__ 加 self._fallback_warned=False;
       enqueue 异常 warn 站点改为 per-instance warn-once (首次 WARNING, 后续 DEBUG)。

default-OFF 等价 main: 本改动只在 enqueue 真实抛异常时生效, 触发条件与 main 完全一致
  (sequencer 注入 + enqueue() 抛 Exception), 不改 emit/decision/main path; main HEAD a299227
  在该路径每次异常都 WARNING (无 dedup), feat 改为 per-instance 首次 WARNING + 后续 DEBUG。

V0 file existence + fingerprint
V1 sentinel — robot-030 注释 + _fallback_warned 字面 + warn/debug 双分支
V2 sha256 锁 — baseline (a299227) enqueue warn block + 业务源码 _fallback_warned init
V3 行为锁 — 连续 N=3 次 enqueue 异常 → WARNING==1 + DEBUG==2 (per-instance);
   新建第二个 instance 重置 → 又 WARNING==1
V4 mutant 反证 — 若把 _fallback_warned 检查回退到无 dedup (mutant)，N=3 触发 WARNING==3
   (用 monkeypatch _fallback_warned property 模拟回退, 验证当前实现拒绝该 mutant)
V5 summary + evidence dump
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import List

errors: List[str] = []
t0 = time.time()

REPO = Path(__file__).resolve().parents[1]
PROACT_PY = REPO / "coco" / "proactive.py"
SELF = Path(__file__).resolve()
EVID_DIR = REPO / "evidence" / "robot-030"
EVID_DIR.mkdir(parents=True, exist_ok=True)

# 业务源码 baseline (main HEAD where robot-030 forks)
BASE_SHA = "a299227"
# baseline 取 enqueue warn block L1321..L1326 (1-based) 的 sha256
BLOCK_BASELINE_LINE_START = 1321
BLOCK_BASELINE_LINE_END = 1326
BLOCK_BASELINE_EXPECTED_SHA = "5b21effda24f28c435e2ff4b58c562b5aa068eb59d5b1bb1802283c25894b653"


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


def sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# 显式清 env 避免外部干扰
for k in ("COCO_ROBOT_SEQ", "COCO_PROACTIVE", "COCO_ROBOT_SYNC_FALLBACK_AUDIT"):
    os.environ.pop(k, None)


# =======================================================================
# V0 file existence + fingerprint
# =======================================================================
print("[V0] file existence + fingerprint")
check("proactive.py exists", PROACT_PY.is_file())
check("self exists", SELF.is_file())
proact_full = hashlib.sha256(PROACT_PY.read_bytes()).hexdigest()
self_full = hashlib.sha256(SELF.read_bytes()).hexdigest()
print(f"  proactive.py sha256={proact_full}")
print(f"  self         sha256={self_full}")


# =======================================================================
# V1 sentinel
# =======================================================================
print("[V1] sentinel — robot-030 注释 + _fallback_warned + warn/debug 双分支")
try:
    src = PROACT_PY.read_text()
    check("V1 含 'robot-030:' 注释 (>=1)",
          src.count("robot-030:") >= 1,
          f"count={src.count('robot-030:')}")
    check("V1 含 self._fallback_warned 初始化",
          "self._fallback_warned: bool = False" in src
          or "self._fallback_warned = False" in src)
    check("V1 含 warn 分支字面 (if not self._fallback_warned)",
          "if not self._fallback_warned:" in src)
    check("V1 含 enqueue failed WARNING 字面",
          'robot_sequencer.enqueue failed: %s: %s' in src)
    check("V1 含 suppressed warn-once DEBUG 分支字面",
          "suppressed warn-once" in src and "log.debug" in src)
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())


# =======================================================================
# V2 sha256 锁 — baseline block + 业务源码 init/warn 站点字面
# =======================================================================
print("[V2] sha256 锁 — baseline (a299227) enqueue warn block")
try:
    proc_show = subprocess.run(
        ["git", "show", f"{BASE_SHA}:coco/proactive.py"],
        capture_output=True, text=True, cwd=REPO,
    )
    block_baseline_sha = ""
    if proc_show.returncode != 0:
        check("V2 git show baseline 成功", False,
              f"rc={proc_show.returncode} stderr={proc_show.stderr[-200:]!r}")
    else:
        base_lines = proc_show.stdout.splitlines(keepends=True)
        seg = "".join(base_lines[BLOCK_BASELINE_LINE_START - 1: BLOCK_BASELINE_LINE_END])
        block_baseline_sha = hashlib.sha256(seg.encode("utf-8")).hexdigest()
        check(
            f"V2 enqueue warn block baseline {BASE_SHA}:L{BLOCK_BASELINE_LINE_START}-{BLOCK_BASELINE_LINE_END} sha256 锁",
            block_baseline_sha == BLOCK_BASELINE_EXPECTED_SHA,
            f"got={block_baseline_sha[:16]} expect={BLOCK_BASELINE_EXPECTED_SHA[:16]}",
        )
        # baseline 必须**不含** _fallback_warned (证明这是新加的)
        check("V2 baseline 不含 _fallback_warned (确认是 robot-030 新加)",
              "_fallback_warned" not in proc_show.stdout)
except Exception:  # noqa: BLE001
    errors.append("V2: " + traceback.format_exc())


# =======================================================================
# V3 行为锁 — N=3 次 enqueue 异常 → WARNING==1 + DEBUG==2 per-instance
# =======================================================================
print("[V3] 行为锁 — N=3 enqueue 异常 → WARNING==1 + DEBUG==2 per-instance; 新 instance 重置")
try:
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    from unittest.mock import MagicMock

    class _CountHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.warnings = []
            self.debugs = []

        def emit(self, record):
            msg = record.getMessage()
            if "robot_sequencer.enqueue failed" not in msg:
                return
            if record.levelno == logging.WARNING:
                self.warnings.append(msg)
            elif record.levelno == logging.DEBUG:
                self.debugs.append(msg)

    proactive_logger = logging.getLogger("coco.proactive")
    orig_level = proactive_logger.level
    orig_propagate = proactive_logger.propagate
    proactive_logger.setLevel(logging.DEBUG)
    proactive_logger.propagate = False
    handler = _CountHandler()
    handler.setLevel(logging.DEBUG)
    proactive_logger.addHandler(handler)

    try:
        # instance 1: 3 次 trigger, enqueue 全抛
        sched1 = ProactiveScheduler(
            config=ProactiveConfig(), power_state=None, face_tracker=None,
            llm_reply_fn=lambda seed, **kw: "hi",
            tts_say_fn=lambda text, blocking=True: None,
        )
        boom1 = MagicMock()
        boom1.enqueue = MagicMock(side_effect=RuntimeError("boom1"))
        boom1.run = MagicMock(return_value={"executed": 0, "cancelled": False})
        boom1.is_shutdown = MagicMock(return_value=False)
        sched1.set_robot_sequencer(boom1)

        for _ in range(3):
            sched1._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="seed-1")

        check("V3 instance1 enqueue 被调用 3 次",
              boom1.enqueue.call_count == 3,
              f"got={boom1.enqueue.call_count}")
        check("V3 instance1 WARNING == 1 (per-instance warn-once)",
              len(handler.warnings) == 1,
              f"got={len(handler.warnings)} msgs={handler.warnings}")
        check("V3 instance1 DEBUG == 2 (后续抑制)",
              len(handler.debugs) == 2,
              f"got={len(handler.debugs)}")
        check("V3 instance1 _fallback_warned 置位 True",
              getattr(sched1, "_fallback_warned", None) is True,
              f"got={getattr(sched1, '_fallback_warned', None)!r}")

        # instance 2: 新建一个, 应当 WARNING 再发一次 (per-instance 重置)
        handler.warnings.clear()
        handler.debugs.clear()
        sched2 = ProactiveScheduler(
            config=ProactiveConfig(), power_state=None, face_tracker=None,
            llm_reply_fn=lambda seed, **kw: "hi",
            tts_say_fn=lambda text, blocking=True: None,
        )
        boom2 = MagicMock()
        boom2.enqueue = MagicMock(side_effect=RuntimeError("boom2"))
        boom2.run = MagicMock(return_value={"executed": 0, "cancelled": False})
        boom2.is_shutdown = MagicMock(return_value=False)
        sched2.set_robot_sequencer(boom2)

        check("V3 instance2 初始 _fallback_warned == False",
              sched2._fallback_warned is False)

        for _ in range(2):
            sched2._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="seed-2")

        check("V3 instance2 WARNING == 1 (per-instance 重置)",
              len(handler.warnings) == 1,
              f"got={len(handler.warnings)}")
        check("V3 instance2 DEBUG == 1",
              len(handler.debugs) == 1,
              f"got={len(handler.debugs)}")
    finally:
        proactive_logger.removeHandler(handler)
        proactive_logger.setLevel(orig_level)
        proactive_logger.propagate = orig_propagate
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4 mutant 反证 — 强行清 _fallback_warned 后 N 次 → WARNING==N (无 dedup 等价 main)
# =======================================================================
print("[V4] mutant 反证 — 每次循环手动清 _fallback_warned → WARNING==N (反证 robot-030 dedup 有效)")
try:
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    from unittest.mock import MagicMock

    class _CountHandler2(logging.Handler):
        def __init__(self):
            super().__init__()
            self.warnings = []

        def emit(self, record):
            if record.levelno == logging.WARNING and "robot_sequencer.enqueue failed" in record.getMessage():
                self.warnings.append(record.getMessage())

    proactive_logger = logging.getLogger("coco.proactive")
    orig_level = proactive_logger.level
    orig_propagate = proactive_logger.propagate
    proactive_logger.setLevel(logging.WARNING)
    proactive_logger.propagate = False
    handler2 = _CountHandler2()
    proactive_logger.addHandler(handler2)
    try:
        sched_m = ProactiveScheduler(
            config=ProactiveConfig(), power_state=None, face_tracker=None,
            llm_reply_fn=lambda seed, **kw: "hi",
            tts_say_fn=lambda text, blocking=True: None,
        )
        boom_m = MagicMock()
        boom_m.enqueue = MagicMock(side_effect=RuntimeError("boom-m"))
        boom_m.run = MagicMock(return_value={"executed": 0, "cancelled": False})
        boom_m.is_shutdown = MagicMock(return_value=False)
        sched_m.set_robot_sequencer(boom_m)

        # mutant: 每次 trigger 前手动把 dedup flag 重置, 模拟"如果没有 dedup"会 N 次 WARNING
        N = 3
        for _ in range(N):
            sched_m._fallback_warned = False  # mutant: 主动撤销 dedup
            sched_m._do_trigger_unlocked(t=time.time(), system_prompt=None, seed="seed-m")

        check(f"V4 mutant (清 dedup) → WARNING == {N} (反证: 无 dedup 会 N 条)",
              len(handler2.warnings) == N,
              f"got={len(handler2.warnings)}")
    finally:
        proactive_logger.removeHandler(handler2)
        proactive_logger.setLevel(orig_level)
        proactive_logger.propagate = orig_propagate
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5 summary + evidence
# =======================================================================
print("[V5] summary")
summary = {
    "feature_id": "robot-030",
    "base_sha": BASE_SHA,
    "proactive_sha256": proact_full,
    "self_sha256": self_full,
    "block_baseline_expected_sha": BLOCK_BASELINE_EXPECTED_SHA,
    "block_baseline_lines": [BLOCK_BASELINE_LINE_START, BLOCK_BASELINE_LINE_END],
    "duration_s": round(time.time() - t0, 2),
    "errors": errors,
}
(EVID_DIR / "verify_summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))

print(f"\n========== robot-030 verify done in {summary['duration_s']}s ==========")
if errors:
    print(f"FAIL: {len(errors)} errors")
    for e in errors:
        print("  -", e[:200])
    sys.exit(1)
print("ALL PASS")
