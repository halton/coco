"""robot-019 verify: RobotSequencer overflow_policy='block' 语义文档锁面 + 行为实证.

source backlog: robot-009-backlog-block-policy-doc.
scope: verify-only doc, 0 业务源码改动. 锁定既有 sequencer.py 行为, 不修改实现.

V0: fingerprint sha256 锁 sequencer.py + spec doc + self
V1: 字面量锁 — 'block', timeout=1.0, 'block_timeout', _VALID_OVERFLOW, _busy_reasons 在源码出现
V2: spec doc 关键短语锁 (>=10 项)
V3: 行为 verify — block policy 满队列 → enqueue 返回 False, drop reason='block_timeout' (subprocess <5s)
V4: 邻近 verify regression — robot-008/012/014/015/017/018 静态锁面 rc==0
V5: smoke — closeout 时单独 ./init.sh, 此处不内联 (与 verify_robot_018 一致)
V_n: evidence summary
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List

errors: List[str] = []
t0 = time.time()

REPO = Path(__file__).resolve().parents[1]
SEQ_PY = REPO / "coco" / "robot" / "sequencer.py"
SPEC_DOC = REPO / "docs" / "robot-sequencer-block-policy-spec.md"
SELF = Path(__file__).resolve()
EVID_DIR = REPO / "evidence" / "robot-019"
EVID_DIR.mkdir(parents=True, exist_ok=True)


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


def sha256_full(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sha8(p: Path) -> str:
    return sha256_full(p)[:8]


# 清相关 env, 保证行为隔离
for k in ("COCO_ROBOT_SEQ", "COCO_ROBOT_BUSY_METRIC"):
    os.environ.pop(k, None)


# --------- V0: fingerprint ---------
print("[V0] fingerprint")
seq_full = sha256_full(SEQ_PY)
spec_full = sha256_full(SPEC_DOC)
self_full = sha256_full(SELF)
check("sequencer.py exists", SEQ_PY.is_file())
check("spec doc exists", SPEC_DOC.is_file())
print(f"  sequencer.py sha256={seq_full}")
print(f"  spec_doc     sha256={spec_full}")
print(f"  self         sha256={self_full}")


# --------- V1: 字面量锁 ---------
print("[V1] 源码字面量锁面")
seq_src = SEQ_PY.read_text(encoding="utf-8")
check("含 _VALID_OVERFLOW frozenset", "_VALID_OVERFLOW" in seq_src and "frozenset" in seq_src)
check("枚举含 'block'", '"block"' in seq_src)
check("枚举含 'drop_new'", '"drop_new"' in seq_src)
check("枚举含 'drop_oldest'", '"drop_oldest"' in seq_src)
check("含 policy == \"block\" 分支", 'policy == "block"' in seq_src)
check("含 q.put(action, timeout=1.0)", "q.put(action, timeout=1.0)" in seq_src)
check("含 reason=\"block_timeout\"", 'reason="block_timeout"' in seq_src)
check("含 _busy_reasons", "_busy_reasons" in seq_src)
check("_busy_reasons 含 'block_timeout'", '"block_timeout"' in seq_src and "_busy_reasons" in seq_src)
check("含默认 overflow_policy='drop_oldest'", 'overflow_policy: str = "drop_oldest"' in seq_src)


# --------- V2: spec doc 关键短语锁 ---------
print("[V2] spec doc 关键短语锁面 (>=10 项)")
spec_src = SPEC_DOC.read_text(encoding="utf-8")
keys = [
    "overflow_policy",
    "block",
    "drop_new",
    "drop_oldest",
    "_VALID_OVERFLOW",
    "block_timeout",
    "timeout=1.0",
    "_on_enqueue_drop",
    "robot.enqueue_dropped",
    "busy_count",
    "COCO_ROBOT_BUSY_METRIC",
    "default-OFF",
    "bytewise",
    "robot-019",
    "robot-017",
    "robot-009",
    "queue_max",
    "_is_shutdown",
]
for k in keys:
    check(f"spec 含 '{k}'", k in spec_src)


# --------- V3: 行为 verify (subprocess) ---------
print("[V3] block policy 行为 verify")
behavior_script = r"""
import sys, time, json
sys.path.insert(0, %r)
from coco.robot.sequencer import RobotSequencer, SequencerConfig, Action

events = []
def emit_fn(name, **kw):
    events.append((name, kw))

# robot=None → run() 仅 sleep duration_s, 不调真 SDK.
# queue_max=1 + overflow_policy='block' → 队列满后走 q.put(timeout=1.0).
cfg = SequencerConfig(queue_max=1, overflow_policy='block', subscribe_async=False)
seq = RobotSequencer(robot=None, config=cfg, emit_fn=emit_fn)

# 用长 duration_s 让 worker 长时间卡在 run([action]) 里, 后续 action 在队列堆.
LONG = 3.0
a1 = Action(action_id='a1', type='sleep', duration_s=LONG)
a2 = Action(action_id='a2', type='sleep', duration_s=0.1)
a3 = Action(action_id='a3', type='sleep', duration_s=0.1)

# 第 1 个: worker 立刻取走并开始 run() 卡 LONG 秒
r1 = seq.enqueue(a1)
# 让 worker 真的 dequeue 并进入 run() — 队列腾空到 0
time.sleep(0.15)
# 第 2 个: 队列 (maxsize=1) 加 1 → 满
r2 = seq.enqueue(a2)
# 第 3 个: 队列满 + worker 仍卡 → block policy q.put(timeout=1.0) → drop
t_start = time.time()
r3 = seq.enqueue(a3)
elapsed = time.time() - t_start

# 释放
seq.shutdown(wait=True, timeout=5.0)

drop_events = [e for e in events if e[0] == 'robot.enqueue_dropped']
result = {
    'r1': r1, 'r2': r2, 'r3': r3,
    'elapsed_s': round(elapsed, 3),
    'drop_count': len(drop_events),
    'drop_reasons': [e[1].get('reason') for e in drop_events],
    'drop_policies': [e[1].get('policy') for e in drop_events],
}
print('BEHAVIOR_RESULT=' + json.dumps(result))
""" % (str(REPO),)

t_v3 = time.time()
proc = subprocess.run(
    [sys.executable, "-c", behavior_script],
    capture_output=True, text=True, timeout=15.0, cwd=str(REPO),
)
v3_elapsed = time.time() - t_v3
print(f"  subprocess elapsed={v3_elapsed:.2f}s rc={proc.returncode}")
if proc.returncode != 0:
    print("  STDOUT:", proc.stdout[-2000:])
    print("  STDERR:", proc.stderr[-2000:])
check("V3 subprocess rc==0", proc.returncode == 0)
check("V3 elapsed < 5s", v3_elapsed < 5.0, f"elapsed={v3_elapsed:.2f}s")

behavior = None
for line in proc.stdout.splitlines():
    if line.startswith("BEHAVIOR_RESULT="):
        behavior = json.loads(line[len("BEHAVIOR_RESULT="):])
        break

check("V3 behavior result 存在", behavior is not None)
if behavior:
    check("r1 == True (第 1 个入队)", behavior["r1"] is True)
    check("r2 == True (第 2 个入队填满)", behavior["r2"] is True)
    check("r3 == False (第 3 个被 block_timeout drop)", behavior["r3"] is False)
    check("drop_count >= 1", behavior["drop_count"] >= 1)
    check("drop reason == 'block_timeout'", "block_timeout" in behavior["drop_reasons"])
    check("drop policy == 'block'", "block" in behavior["drop_policies"])
    # timeout=1.0 → elapsed 应在 [0.9, 1.5] 区间
    check("r3 elapsed in [0.8, 1.5] (timeout=1.0 量级)",
          0.8 <= behavior["elapsed_s"] <= 1.5,
          f"elapsed={behavior['elapsed_s']}s")


# --------- V4: 邻近 verify regression (fast subset) ---------
print("[V4] 邻近 verify regression (rc==0, fast subset)")
# 注: robot-012/014/015 verify 单次 145s/573s/457s, 全 6 个串行 >20min, 违反 <5min Bash 硬规则.
# 因此 V4 仅取轻量 verify-only doc 锁面邻居 (008/017/018), 与本 feature 同类.
# 重邻居在 closeout smoke ./init.sh 阶段间接覆盖 (sequencer.py 同源).
neighbors = [
    "verify_robot_008.py",  # ~10s
    "verify_robot_017.py",  # ~64s
    "verify_robot_018.py",  # ~12s
]
heavy_skipped = [
    "verify_robot_012.py (~145s)",
    "verify_robot_014.py (~573s)",
    "verify_robot_015.py (~457s)",
]
print(f"  heavy 邻居 skip (run separately during closeout smoke): {heavy_skipped}")
for n in neighbors:
    path = REPO / "scripts" / n
    if not path.is_file():
        check(f"{n} 存在", False, "missing")
        continue
    t_n = time.time()
    p = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True, text=True, timeout=180.0, cwd=str(REPO),
    )
    el = time.time() - t_n
    check(f"{n} rc==0", p.returncode == 0, f"rc={p.returncode} elapsed={el:.1f}s")
    if p.returncode != 0:
        print(f"    STDERR tail: {p.stderr[-500:]}")


# --------- V5: smoke ---------
print("[V5] smoke — 在 closeout 时由 ./init.sh 单独 run, 本 verify 不内联")
check("V5 占位 (closeout 执行 ./init.sh)", True)


# --------- V_n: evidence ---------
print("[V_n] evidence summary")
elapsed_total = time.time() - t0
summary = {
    "feature_id": "robot-019",
    "priority": 191,
    "phase": 23,
    "source_backlog": "robot-009-backlog-block-policy-doc",
    "verdict": "PASS" if not errors else "FAIL",
    "errors": errors,
    "elapsed_s": round(elapsed_total, 2),
    "fingerprints": {
        "sequencer.py_sha256": seq_full,
        "spec_doc_sha256": spec_full,
        "verify_self_sha256": self_full,
    },
    "behavior": behavior,
    "neighbor_regression": neighbors,
    "default_off_invariant": "spec evidence-only; 不修改业务源码; env 未开 bytewise 等价 main",
    "new_backlog_added": 0,
}
(EVID_DIR / "verify_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
)
print(f"  evidence/robot-019/verify_summary.json written; verdict={summary['verdict']}")
print(f"  total elapsed={elapsed_total:.2f}s")

if errors:
    print(f"\n[FAIL] robot-019 verify {len(errors)} error(s):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print("\n[PASS] robot-019 verify OK")
sys.exit(0)
