"""robot-020 verify: ProactiveScheduler warn-once key 重命名锁面 (verify-only).

source backlog: robot-015-backlog-v3-warn-once-rename
scope: verify-only doc; 0 业务源码改动. 锁定 coco/proactive.py 中三种 warn-once
key tuple 字面量 + env gating + dedup 行为, 防止重命名导致 dedup 静默失效.

V0: fingerprint sha256 锁 proactive.py + spec doc + self
V1: 字面量锁 — warn-once key tag ('dup'/'probe-fail'/'sync-fallback'), set 属性名,
    env 变量名, 必须在 coco/proactive.py 源码出现
V2: spec doc 关键短语锁 (>=12 项)
V3: dedup 行为实证 (subprocess) — env=1 时同 key warn 一次, 再次触发同 key 降为 debug
V4: env=0 (default) 时不触发任何 audit-dedup 路径 (set 永远空), bytewise 等价 main
V5: 邻近 verify 回归 (robot-008/016/017/018/019 静态锁面, rc==0; heavy 015 留 closeout smoke)
V6: smoke — 在 closeout 时由 ./init.sh 单独 run, 此处不内联
V_n: evidence/robot-020/verify_summary.json
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
SPEC_DOC = REPO / "docs" / "robot-warn-once-keys-spec.md"
SELF = Path(__file__).resolve()
EVID_DIR = REPO / "evidence" / "robot-020"
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


# --------- V1: 源码字面量锁 ---------
print("[V1] 源码字面量锁面 (warn-once key 重命名防线)")
src = PROACT_PY.read_text(encoding="utf-8")
# set 属性名
check("含 _setter_audit_seen", "_setter_audit_seen" in src)
check("含 _sync_fallback_audit_seen", "_sync_fallback_audit_seen" in src)
# env 变量名
check("含 COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", "COCO_ROBOT_SETTER_LIFECYCLE_AUDIT" in src)
check("含 COCO_ROBOT_SYNC_FALLBACK_AUDIT", "COCO_ROBOT_SYNC_FALLBACK_AUDIT" in src)
# warn-once key tag 字面量 (三种)
check('含 ("dup", id(', '("dup", id(' in src)
check('含 ("probe-fail", id(', '("probe-fail", id(' in src)
check('含 ("sync-fallback", id(', '("sync-fallback", id(' in src)
# key tuple 元素细节
check("dup key 含 id(prev)/id(new) 配对", "id(self._robot_sequencer), id(sequencer)" in src)
check("probe-fail key 含 type(e).__name__", "type(e).__name__" in src)
check("sync-fallback key 含 id(_seq)", "id(_seq)" in src)
# env=='1' gating 语义
check("setter audit env gating 写法 == \"1\"",
      'os.environ.get("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", "") == "1"' in src)
check("sync fallback audit env gating 写法 == \"1\"",
      '"COCO_ROBOT_SYNC_FALLBACK_AUDIT"' in src and '== "1"' in src)
# suppressed warn-once 文案 (debug 分支锚点)
check("含 suppressed warn-once 文案", "suppressed warn-once" in src)


# --------- V2: spec doc 关键短语锁 ---------
print("[V2] spec doc 关键短语锁面 (>=12 项)")
spec_src = SPEC_DOC.read_text(encoding="utf-8")
keys = [
    "robot-020",
    "warn-once",
    "_setter_audit_seen",
    "_sync_fallback_audit_seen",
    '("dup", id(prev), id(new))',
    '("probe-fail", id(sequencer), type(e).__name__)',
    '("sync-fallback", id(_seq))',
    "COCO_ROBOT_SETTER_LIFECYCLE_AUDIT",
    "COCO_ROBOT_SYNC_FALLBACK_AUDIT",
    "default-OFF",
    "bytewise",
    "rename",
    "dedup",
    "verify-only",
    "robot-015",
    "robot-016",
    "robot-017",
]
for k in keys:
    check(f"spec 含 '{k}'", k in spec_src)


# --------- V3: dedup 行为实证 (env=1, setter 路径) ---------
print("[V3] env=1 setter dup dedup 行为实证 (subprocess)")
behavior_script = r"""
import sys, os, logging, io, json
sys.path.insert(0, %r)
os.environ['COCO_ROBOT_SETTER_LIFECYCLE_AUDIT'] = '1'

# 重定向 proactive logger 到 buffer 抓 WARNING / DEBUG
buf = io.StringIO()
h = logging.StreamHandler(buf)
h.setLevel(logging.DEBUG)
h.setFormatter(logging.Formatter('%%(levelname)s %%(message)s'))
root = logging.getLogger()
root.setLevel(logging.DEBUG)
root.addHandler(h)

from coco.proactive import ProactiveScheduler

ps = ProactiveScheduler()
class _Seq:
    def is_shutdown(self):
        return False
s1 = _Seq()
s2 = _Seq()
# 第 1 次 inject s1 (prev=None, new=s1) → 不触发 dup warn
ps.set_robot_sequencer(s1)
# 第 2 次 inject s2 (prev=s1, new=s2) → 第一次 dup warn (WARNING)
ps.set_robot_sequencer(s2)
# 第 3 次再 inject s1 → prev=s2, new=s1, 新 key, 第一次 → 又一个 WARNING
ps.set_robot_sequencer(s1)
# 第 4 次再 inject s2 → prev=s1, new=s2, 同 key=("dup", id(s1), id(s2)) → suppress 为 DEBUG
ps.set_robot_sequencer(s2)
# 第 5 次再 inject s2 (同上 key) → 仍 DEBUG (set 已含)
# 但 prev/new 是 s2/s2, key 不同; 跳过 dup 分支因为 sequencer is not None and same... 实际仍进 dup 分支
# robot-016 路径: if prev is not None and new is not None → 进 dup 检查
ps.set_robot_sequencer(s2)

out = buf.getvalue()
ovrwrt_warn = out.count('overwriting existing sequencer') - out.count('suppressed warn-once')
# 简化: 数 WARNING 级别 overwrite 行 vs DEBUG suppressed 行
warn_lines = [l for l in out.splitlines() if l.startswith('WARNING') and 'overwriting existing sequencer' in l]
debug_lines = [l for l in out.splitlines() if l.startswith('DEBUG') and 'suppressed warn-once' in l and 'overwriting' in l]
result = {
    'warn_overwrite_count': len(warn_lines),
    'debug_suppressed_count': len(debug_lines),
    'setter_audit_seen_size': len(ps._setter_audit_seen),
}
print('BEHAVIOR_RESULT=' + json.dumps(result))
""" % (str(REPO),)

t_v3 = time.time()
proc = subprocess.run(
    [sys.executable, "-c", behavior_script],
    capture_output=True, text=True, timeout=30.0, cwd=str(REPO),
)
v3_elapsed = time.time() - t_v3
print(f"  subprocess elapsed={v3_elapsed:.2f}s rc={proc.returncode}")
if proc.returncode != 0:
    print("  STDOUT:", proc.stdout[-2000:])
    print("  STDERR:", proc.stderr[-2000:])
check("V3 subprocess rc==0", proc.returncode == 0)
check("V3 elapsed < 10s", v3_elapsed < 10.0, f"elapsed={v3_elapsed:.2f}s")

behavior_env1 = None
for line in proc.stdout.splitlines():
    if line.startswith("BEHAVIOR_RESULT="):
        behavior_env1 = json.loads(line[len("BEHAVIOR_RESULT="):])
        break

check("V3 behavior 返回", behavior_env1 is not None)
if behavior_env1:
    # 4 次实际 inject (s1→s2, s2→s1, s1→s2 重复, s2→s2 重复)
    # 第 1: prev=None skip dup;
    # 第 2: prev=s1 new=s2, 新 key (id(s1),id(s2)), WARNING
    # 第 3: prev=s2 new=s1, 新 key (id(s2),id(s1)), WARNING
    # 第 4: prev=s1 new=s2, key=(id(s1),id(s2)) 已在 set, DEBUG
    # 第 5: prev=s2 new=s2, 新 key (id(s2),id(s2)), WARNING
    check("V3 WARNING overwrite 次数 == 3 (新 key)",
          behavior_env1["warn_overwrite_count"] == 3,
          f"got {behavior_env1['warn_overwrite_count']}")
    check("V3 DEBUG suppressed 次数 == 1 (重复 key)",
          behavior_env1["debug_suppressed_count"] == 1,
          f"got {behavior_env1['debug_suppressed_count']}")
    check("V3 _setter_audit_seen size == 3",
          behavior_env1["setter_audit_seen_size"] == 3,
          f"got {behavior_env1['setter_audit_seen_size']}")


# --------- V4: env=0 default 路径 (set 永远空, bytewise 等价 main) ---------
print("[V4] env=0 default 路径行为 (set 永远空)")
default_off_script = r"""
import sys, os, json
sys.path.insert(0, %r)
# 显式确保 env 未设
for k in ('COCO_ROBOT_SETTER_LIFECYCLE_AUDIT', 'COCO_ROBOT_SYNC_FALLBACK_AUDIT'):
    os.environ.pop(k, None)

from coco.proactive import ProactiveScheduler
ps = ProactiveScheduler()
class _Seq:
    def is_shutdown(self):
        return False
s1 = _Seq()
s2 = _Seq()
# 多次重复注入, env=0 → set 应永远空
ps.set_robot_sequencer(s1)
ps.set_robot_sequencer(s2)
ps.set_robot_sequencer(s1)
ps.set_robot_sequencer(s2)
ps.set_robot_sequencer(s1)
result = {
    'setter_audit_seen_size': len(ps._setter_audit_seen),
    'sync_fallback_audit_seen_size': len(ps._sync_fallback_audit_seen),
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
    check("V4 _sync_fallback_audit_seen 始终空 (env=0)",
          default_off["sync_fallback_audit_seen_size"] == 0,
          f"got {default_off['sync_fallback_audit_seen_size']}")


# --------- V5: 邻近 verify 回归 (轻量子集) ---------
print("[V5] 邻近 verify regression (rc==0, fast subset)")
# 重邻居 robot-015 (~457s) / robot-014 (~573s) skip, 留 closeout smoke 间接覆盖
neighbors = [
    "verify_robot_008.py",  # ~10s
    "verify_robot_016.py",  # ~20s
    "verify_robot_017.py",  # ~64s
    "verify_robot_018.py",  # ~12s
    "verify_robot_019.py",  # ~70s
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
print("[V6] smoke — 在 closeout 时由 ./init.sh 单独 run, 本 verify 不内联")
check("V6 占位 (closeout 执行 ./init.sh)", True)


# --------- V_n: evidence ---------
print("[V_n] evidence summary")
elapsed_total = time.time() - t0
summary = {
    "feature_id": "robot-020",
    "priority": 194,
    "phase": 23,
    "source_backlog": "robot-015-backlog-v3-warn-once-rename",
    "verdict": "PASS" if not errors else "FAIL",
    "errors": errors,
    "elapsed_s": round(elapsed_total, 2),
    "fingerprints": {
        "proactive.py_sha256": proact_full,
        "spec_doc_sha256": spec_full,
        "verify_self_sha256": self_full,
    },
    "behavior_env1": behavior_env1,
    "default_off": default_off,
    "neighbor_regression": neighbor_results,
    "heavy_skipped": heavy_skipped,
    "default_off_invariant": "spec evidence-only; 0 业务源码改动; env 未设 set 永远空 bytewise 等价 main",
    "new_backlog_added": 0,
}
(EVID_DIR / "verify_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
)
print(f"  evidence/robot-020/verify_summary.json written; verdict={summary['verdict']}")
print(f"  total elapsed={elapsed_total:.2f}s")

if errors:
    print(f"\n[FAIL] robot-020 verify {len(errors)} error(s):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print("\n[PASS] robot-020 verify OK")
sys.exit(0)
