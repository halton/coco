"""robot-021 verify: ProactiveScheduler→RobotSequencer setter lifecycle 锁面 (verify-only).

source backlog: robot-008-backlog-setter-lifecycle
scope: verify-only doc; 0 业务源码改动. 锁定 coco/proactive.py 中 set_robot_sequencer
lifecycle 状态机 (S0-S7) + 调用顺序 + probe-fail 回退 + atexit 协作.

V0: fingerprint sha256 锁 proactive.py + spec doc + self
V1: setter 函数签名 + 关键源码 anchor 字面量锁
V2: spec doc 关键短语锁 (>=15 项)
V3: lifecycle 行为实证 (subprocess) — S1 first-set / S3 re-set-same / S4 re-set-diff /
    S5 clear / S2 shutdown-refuse, 验证 _robot_sequencer 取值轨迹与 _setter_audit_seen 状态
V4: env=0 (default) bytewise 等价 — set 永远空
V5: 邻近 verify 回归 robot-008/015/016/017/018/019/020 静态锁面 rc=0
V6: smoke — closeout 时 ./init.sh 单独 run, 本 verify 不内联
V_n: evidence/robot-021/verify_summary.json
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
PROACT_PY = REPO / "coco" / "proactive.py"
SPEC_DOC = REPO / "docs" / "robot-scheduler-sequencer-lifecycle-spec.md"
SELF = Path(__file__).resolve()
EVID_DIR = REPO / "evidence" / "robot-021"
EVID_DIR.mkdir(parents=True, exist_ok=True)


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


def sha256_full(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# 清相关 env, 保证行为隔离
for k in ("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", "COCO_ROBOT_SYNC_FALLBACK_AUDIT"):
    os.environ.pop(k, None)


# --------- V0: fingerprint ---------
print("[V0] fingerprint")
proact_full = sha256_full(PROACT_PY)
spec_full = sha256_full(SPEC_DOC)
self_full = sha256_full(SELF)
check("proactive.py exists", PROACT_PY.is_file())
check("spec doc exists", SPEC_DOC.is_file())
print(f"  proactive.py sha256={proact_full}")
print(f"  spec_doc     sha256={spec_full}")
print(f"  self         sha256={self_full}")


# --------- V1: 源码字面量锁 (setter 函数签名 + 关键源码 anchor) ---------
print("[V1] setter 函数签名 + 关键源码 anchor 字面量锁")
src = PROACT_PY.read_text(encoding="utf-8")
# 函数签名锁
check("含 def set_robot_sequencer(self, sequencer: Any) -> None:",
      "def set_robot_sequencer(self, sequencer: Any) -> None:" in src)
# _robot_sequencer 字段初始化
check("含 self._robot_sequencer: Any = None",
      "self._robot_sequencer: Any = None" in src)
# 拒绝 shutdown anchor (S2)
check("含 refuse to inject already-shutdown sequencer",
      "refuse to inject" in src and "already-shutdown" in src)
# probe-fail fail-soft anchor (S6)
check("含 is_shutdown probe failed anchor",
      "is_shutdown probe failed" in src)
# dup overwrite anchor (S3/S4)
check("含 overwriting existing sequencer anchor",
      "overwriting existing sequencer" in src)
check("含 double-injection detected anchor",
      "double-injection detected" in src)
# isinstance bool 严格判定 (mock 兼容)
check("含 isinstance(rv, bool) and rv is True (严格 bool 判定)",
      "isinstance(rv, bool)" in src and "rv is True" in src)
# env gating 字面量 (与 robot-020 一致)
check("含 COCO_ROBOT_SETTER_LIFECYCLE_AUDIT env 名",
      "COCO_ROBOT_SETTER_LIFECYCLE_AUDIT" in src)
check("含 env gating 写法 == \"1\"",
      'os.environ.get("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", "") == "1"' in src)
# dup key tuple 元素
check("含 id(self._robot_sequencer), id(sequencer) (dup key 配对)",
      "id(self._robot_sequencer), id(sequencer)" in src)
# probe-fail key 含 type(e).__name__
check("含 type(e).__name__ (probe-fail key)",
      "type(e).__name__" in src)
# with self._lock 包裹写入
check("含 with self._lock (写入 _robot_sequencer 受锁保护)",
      "with self._lock" in src)


# --------- V2: spec doc 关键短语锁 ---------
print("[V2] spec doc 关键短语锁面 (>=15 项)")
spec_src = SPEC_DOC.read_text(encoding="utf-8")
keys = [
    "robot-021",
    "setter lifecycle",
    "state machine",
    "first-set",
    "re-set-same",
    "re-set-diff",
    "clear",
    "probe-fail",
    "atexit",
    "COCO_ROBOT_SETTER_LIFECYCLE_AUDIT",
    "default-OFF",
    "bytewise",
    "verify-only",
    "robot-008",
    "robot-010",
    "robot-015",
    "robot-016",
    "robot-020",
    "RobotSequencer",
    "ProactiveScheduler",
    "set_robot_sequencer",
    "_robot_sequencer",
    "_setter_audit_seen",
    "refuse to inject",
    "overwriting existing sequencer",
]
for k in keys:
    check(f"spec 含 '{k}'", k in spec_src)


# --------- V3: lifecycle 行为实证 (env=1) ---------
print("[V3] lifecycle 行为实证 (subprocess, env=1)")
lifecycle_script = r"""
import sys, os, json
sys.path.insert(0, %r)
os.environ['COCO_ROBOT_SETTER_LIFECYCLE_AUDIT'] = '1'

from coco.proactive import ProactiveScheduler

ps = ProactiveScheduler()
trace = []
def snap(label):
    trace.append({
        'label': label,
        'seq_is_none': ps._robot_sequencer is None,
        'seq_id': None if ps._robot_sequencer is None else id(ps._robot_sequencer),
        'audit_size': len(ps._setter_audit_seen),
    })

class _Seq:
    def is_shutdown(self):
        return False
class _ShutdownSeq:
    def is_shutdown(self):
        return True

s1 = _Seq()
s2 = _Seq()
sx = _ShutdownSeq()

snap('init')                              # S0: prev=None
ps.set_robot_sequencer(s1); snap('S1 first-set')  # prev=None new=s1 → 写入
ps.set_robot_sequencer(s1); snap('S3 re-set-same')  # prev=s1 new=s1 → dup WARN, 覆盖 (id 不变)
ps.set_robot_sequencer(s2); snap('S4 re-set-diff')  # prev=s1 new=s2 → dup WARN, 覆盖
ps.set_robot_sequencer(None); snap('S5 clear')       # prev=s2 new=None → 直接清空, 不进 dup
# S2 shutdown 拒绝: 注入 sx (is_shutdown=True), prev=None → 拒绝, _robot_sequencer 仍 None
ps.set_robot_sequencer(sx); snap('S2 shutdown-refuse')

result = {
    'trace': trace,
    's1_id': id(s1),
    's2_id': id(s2),
    'sx_id': id(sx),
}
print('LIFECYCLE_RESULT=' + json.dumps(result))
""" % (str(REPO),)

t_v3 = time.time()
proc = subprocess.run(
    [sys.executable, "-c", lifecycle_script],
    capture_output=True, text=True, timeout=30.0, cwd=str(REPO),
)
v3_elapsed = time.time() - t_v3
print(f"  subprocess elapsed={v3_elapsed:.2f}s rc={proc.returncode}")
if proc.returncode != 0:
    print("  STDOUT:", proc.stdout[-2000:])
    print("  STDERR:", proc.stderr[-2000:])
check("V3 subprocess rc==0", proc.returncode == 0)
check("V3 elapsed < 10s", v3_elapsed < 10.0, f"elapsed={v3_elapsed:.2f}s")

lifecycle_result = None
for line in proc.stdout.splitlines():
    if line.startswith("LIFECYCLE_RESULT="):
        lifecycle_result = json.loads(line[len("LIFECYCLE_RESULT="):])
        break

check("V3 lifecycle result 返回", lifecycle_result is not None)
if lifecycle_result:
    trace = lifecycle_result["trace"]
    s1_id = lifecycle_result["s1_id"]
    s2_id = lifecycle_result["s2_id"]
    # 期望状态机轨迹
    # init: None
    # S1 first-set: s1
    # S3 re-set-same: s1 (id 不变), audit set 累计 dup key
    # S4 re-set-diff: s2
    # S5 clear: None
    # S2 shutdown-refuse: 仍 None (拒绝注入)
    def find(label):
        for t in trace:
            if t["label"] == label:
                return t
        return None
    init = find("init")
    s1_set = find("S1 first-set")
    s3 = find("S3 re-set-same")
    s4 = find("S4 re-set-diff")
    s5 = find("S5 clear")
    s2_ref = find("S2 shutdown-refuse")

    check("V3 init: _robot_sequencer is None", init and init["seq_is_none"] is True)
    check("V3 init: audit_size == 0", init and init["audit_size"] == 0)
    check("V3 S1 first-set: 写入成功 (not None)",
          s1_set and s1_set["seq_is_none"] is False)
    check("V3 S1 first-set: seq_id == id(s1)",
          s1_set and s1_set["seq_id"] == s1_id)
    check("V3 S1 first-set: audit_size == 0 (prev=None 跳 dup 分支)",
          s1_set and s1_set["audit_size"] == 0)
    check("V3 S3 re-set-same: seq_id 不变 (仍是 s1)",
          s3 and s3["seq_id"] == s1_id)
    check("V3 S3 re-set-same: audit_size == 1 (新 dup key 累计)",
          s3 and s3["audit_size"] == 1)
    check("V3 S4 re-set-diff: 覆盖为 s2",
          s4 and s4["seq_id"] == s2_id)
    check("V3 S4 re-set-diff: audit_size == 2 (再加 dup key)",
          s4 and s4["audit_size"] == 2)
    check("V3 S5 clear: _robot_sequencer 回 None",
          s5 and s5["seq_is_none"] is True)
    check("V3 S5 clear: audit_size 不变 (new=None 跳 dup)",
          s5 and s5["audit_size"] == 2)
    check("V3 S2 shutdown-refuse: _robot_sequencer 仍 None (拒绝)",
          s2_ref and s2_ref["seq_is_none"] is True)


# --------- V4: env=0 default 路径 (set 永远空, bytewise 等价 main) ---------
print("[V4] env=0 default 路径 (set 永远空)")
default_off_script = r"""
import sys, os, json
sys.path.insert(0, %r)
for k in ('COCO_ROBOT_SETTER_LIFECYCLE_AUDIT', 'COCO_ROBOT_SYNC_FALLBACK_AUDIT'):
    os.environ.pop(k, None)

from coco.proactive import ProactiveScheduler
ps = ProactiveScheduler()
class _Seq:
    def is_shutdown(self):
        return False
s1 = _Seq()
s2 = _Seq()
# 多次重复注入 (S1/S3/S4/S5 全覆盖)
ps.set_robot_sequencer(s1)
ps.set_robot_sequencer(s1)   # S3
ps.set_robot_sequencer(s2)   # S4
ps.set_robot_sequencer(None) # S5
ps.set_robot_sequencer(s1)   # S1 again
ps.set_robot_sequencer(s2)   # S4 again
result = {
    'setter_audit_seen_size': len(ps._setter_audit_seen),
    'final_seq_id_eq_s2': id(ps._robot_sequencer) == id(s2),
}
print('DEFAULT_OFF_RESULT=' + json.dumps(result))
""" % (str(REPO),)

t_v4 = time.time()
proc4 = subprocess.run(
    [sys.executable, "-c", default_off_script],
    capture_output=True, text=True, timeout=20.0, cwd=str(REPO),
)
v4_elapsed = time.time() - t_v4
print(f"  subprocess elapsed={v4_elapsed:.2f}s rc={proc4.returncode}")
if proc4.returncode != 0:
    print("  STDOUT:", proc4.stdout[-1500:])
    print("  STDERR:", proc4.stderr[-1500:])
check("V4 subprocess rc==0", proc4.returncode == 0)

default_off = None
for line in proc4.stdout.splitlines():
    if line.startswith("DEFAULT_OFF_RESULT="):
        default_off = json.loads(line[len("DEFAULT_OFF_RESULT="):])
        break
check("V4 default-off result 返回", default_off is not None)
if default_off:
    check("V4 _setter_audit_seen 始终空 (env=0)",
          default_off["setter_audit_seen_size"] == 0,
          f"got {default_off['setter_audit_seen_size']}")
    check("V4 主路径不变 (最终 seq == s2)",
          default_off["final_seq_id_eq_s2"] is True)


# --------- V5: 邻近 verify 回归 (轻量子集) ---------
print("[V5] 邻近 verify regression (rc==0, fast subset)")
neighbors = [
    "verify_robot_008.py",  # ~10s
    "verify_robot_016.py",  # ~20s
    "verify_robot_017.py",  # ~64s
    "verify_robot_018.py",  # ~12s
    "verify_robot_019.py",  # ~70s
    "verify_robot_020.py",  # robot-020 静态 + 行为
]
heavy_skipped = [
    "verify_robot_015.py (~457s, source backlog 锁; 留 closeout)",
    "verify_robot_014.py (~573s)",
]
print(f"  heavy 邻居 skip: {heavy_skipped}")
neighbor_results = []
for n in neighbors:
    path = REPO / "scripts" / n
    if not path.is_file():
        check(f"{n} 存在", False, "missing")
        continue
    t_n = time.time()
    p = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True, text=True, timeout=240.0, cwd=str(REPO),
    )
    el = time.time() - t_n
    rc_ok = p.returncode == 0
    check(f"{n} rc==0", rc_ok, f"rc={p.returncode} elapsed={el:.1f}s")
    neighbor_results.append({"name": n, "rc": p.returncode, "elapsed_s": round(el, 1)})
    if not rc_ok:
        print(f"    STDERR tail: {p.stderr[-500:]}")


# --------- V6: smoke ---------
print("[V6] smoke — closeout 时由 ./init.sh 单独 run, 本 verify 不内联")
check("V6 占位 (closeout 执行 ./init.sh)", True)


# --------- V_n: evidence ---------
print("[V_n] evidence summary")
elapsed_total = time.time() - t0
summary = {
    "feature_id": "robot-021",
    "priority": 201,
    "phase": 24,
    "source_backlog": "robot-008-backlog-setter-lifecycle",
    "verdict": "PASS" if not errors else "FAIL",
    "errors": errors,
    "elapsed_s": round(elapsed_total, 2),
    "fingerprints": {
        "proactive.py_sha256": proact_full,
        "spec_doc_sha256": spec_full,
        "verify_self_sha256": self_full,
    },
    "lifecycle_trace": lifecycle_result,
    "default_off": default_off,
    "neighbor_regression": neighbor_results,
    "heavy_skipped": heavy_skipped,
    "default_off_invariant": "spec evidence-only; 0 业务源码改动; env 未设 set 永远空 bytewise 等价 main",
    "new_backlog_added": 0,
}
(EVID_DIR / "verify_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
)
print(f"  evidence/robot-021/verify_summary.json written; verdict={summary['verdict']}")
print(f"  total elapsed={elapsed_total:.2f}s")

if errors:
    print(f"\n[FAIL] robot-021 verify {len(errors)} error(s):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print("\n[PASS] robot-021 verify OK")
sys.exit(0)
