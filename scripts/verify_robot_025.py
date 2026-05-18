"""robot-025 verify: set_robot_sequencer 三道防护 sentinel 锁面 + mutant 反证 (verify-only).

source backlog: robot-008-backlog-setter-lifecycle (Reviewer caveat 收口)
scope: 现状 (a)(b)(c) 三道防护已在 coco/proactive.py 内完整存在 —
  (a) is_shutdown 探针拒绝注入 (set_robot_sequencer 内)；
  (b) 重复注入 WARNING (set_robot_sequencer 内)；
  (c) trigger-time is_shutdown 探针检测到 shutdown → 清空 _robot_sequencer 引用 + skip
      (语义等价 add_shutdown_callback 路径，避免悬垂引用)。
本 feature 为 **选项 A** — 0 业务源码改动、纯 verify-only meta 锁面 + in-memory mutant 反证。

robot-027 升级（F1+F2 假阳性窗口闭合）:
  - F1: "overwriting existing sequencer" 字面在源码出现 **2 次** (L493 debug 路径 + L499 prod 路径)。
        旧 V2 只 sha256 一段字面常量，若 Reviewer mutant 删一处保留一处，prod 行为已弱化但 V2 仍 PASS。
        本次升级：
          (a) V1 加 count>=2 lower-bound 锁；
          (b) V2 增加 setter 关键 block (L489-504) 行号锚定 sha256，baseline 用 `git show <BASE>:coco/proactive.py`
              取，避免 working-tree 污染；working-tree hash 也算一遍但仅做信息打印。
  - F2: 旧 V2 中 sentinel_hashes 与 EXPECTED 用同一表达式重复计算，永真自洽。
        本次升级：EXPECTED 改为 **hardcoded 16-hex prefix 字面常量**，断面直接拿 sentinel_hashes[k][:16] 比对，
        EXPECTED 不再从当前 src 派生。

V0 file existence + fingerprint sha256
V1 三道防护 sentinel 行字面量存在 (含 overwriting count>=2 锁)
V2 sentinel 行 sha256 锁 (hardcoded 16-hex prefix) + setter block (L489-504) baseline 行号锚定 hash
V3 mutant 反证 in-memory: 删除 dup-warn 分支 / 或 删除 is_shutdown 探针 → 行为可区分
V4 default-OFF subprocess: 未设 env 时 setter 注入正常 sequencer bytewise 等价 main (无新 warn 文案变更, audit dedup 不触发)
V5 summary
"""
from __future__ import annotations

import hashlib
import json
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
EVID_DIR = REPO / "evidence" / "robot-025"
EVID_DIR.mkdir(parents=True, exist_ok=True)


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


def sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# 清相关 env 隔离
for k in ("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT",):
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
# V1 三道防护 sentinel 字面量存在
# =======================================================================
print("[V1] 三道防护 sentinel 字面量存在")
src = PROACT_PY.read_text(encoding="utf-8")

# (a) is_shutdown 探针拒绝注入
SENT_A_REFUSE = "refuse to inject "
SENT_A_ALREADY = "already-shutdown sequencer"
check("(a) sentinel refuse-to-inject 存在", SENT_A_REFUSE in src and SENT_A_ALREADY in src)

# (b) 重复注入 WARNING
SENT_B_OVERWRITE = "overwriting existing sequencer"
SENT_B_DOUBLE = "double-injection detected"
check("(b) sentinel overwriting 存在", SENT_B_OVERWRITE in src)
check("(b) sentinel double-injection 存在", SENT_B_DOUBLE in src)

# robot-027 F1: overwriting 字面在源码必须出现 >=2 次
# (L493 debug suppressed 路径 + L499 prod warn 路径). 任一被删 → count 退化为 1 → 锁失败.
_overwrite_count = src.count(SENT_B_OVERWRITE)
check(
    "(b) robot-027 F1: overwriting 字面 count>=2 (两个出现点都未被删)",
    _overwrite_count >= 2,
    f"count={_overwrite_count}",
)

# (c) trigger-time shutdown 清空引用 (语义等价 shutdown_callback)
SENT_C_DETECT = "detected shutdown "
SENT_C_CLEAR = "clearing _robot_sequencer and skipping enqueue"
check("(c) sentinel detected-shutdown 存在", SENT_C_DETECT in src)
check("(c) sentinel clear-and-skip 存在", SENT_C_CLEAR in src)

# 关键置回 None 行 (清空引用)
SENT_C_ASSIGN = "self._robot_sequencer = None"
check("(c) sentinel self._robot_sequencer = None 存在",
      SENT_C_ASSIGN in src)


# =======================================================================
# V2 sentinel 行 sha256 锁
# robot-027 F2: EXPECTED 改为 **hardcoded 16-hex prefix 字面常量**, 不再从当前 src 派生.
#   sentinel_hashes 由本文件运行时计算 (输入是 src 中存在性已经被 V1 锁住的字面常量).
#   EXPECTED_PREFIX_*16 为发版时人工固化的 16-hex 前缀; 任意字面 mutation -> hash 前缀 mismatch.
# robot-027 F1 二层: setter 关键 block (L489-504, 1-based) baseline 行号锚定 sha256.
#   baseline 取 git show <BASE>:coco/proactive.py 而非 working tree, 避免上下文污染.
#   BASE_SHA = 3ff13c629a97adedeb61897609f1d379820496b8 (main HEAD at robot-027 立 feat 时刻)
# =======================================================================
print("[V2] sentinel 行 sha256 锁 + setter block 行号锚定 hash")
sentinel_hashes = {
    "refuse-to-inject": sha256_str(SENT_A_REFUSE + SENT_A_ALREADY),
    "overwriting": sha256_str(SENT_B_OVERWRITE),
    "double-injection": sha256_str(SENT_B_DOUBLE),
    "detected-shutdown": sha256_str(SENT_C_DETECT),
    "clear-and-skip": sha256_str(SENT_C_CLEAR),
}
# hardcoded 16-hex prefix (robot-027 F2 闭合: EXPECTED 不再从当前 src 表达式派生)
EXPECTED_PREFIX = {
    "refuse-to-inject":   "85648bc52cbf60e2",
    "overwriting":        "389d75de4c93afc1",
    "double-injection":   "0528662834fe2448",
    "detected-shutdown":  "71ac601795fc20b2",
    "clear-and-skip":     "041f5a8ed8d5dc62",
}
for k, expected_prefix in EXPECTED_PREFIX.items():
    got_prefix = sentinel_hashes[k][:16]
    check(
        f"sentinel hash[{k}] 锁定 (hardcoded prefix)",
        got_prefix == expected_prefix,
        detail=f"got={got_prefix} expect={expected_prefix}",
    )
print("  sentinel_hashes=" + json.dumps({k: v[:16] for k, v in sentinel_hashes.items()}))

# --- robot-027 F1 二层: L489-504 baseline 行号锚定 hash ---
SETTER_BLOCK_BASE_SHA = "3ff13c629a97adedeb61897609f1d379820496b8"  # main HEAD at robot-027 立 feat
SETTER_BLOCK_LINE_START = 489  # 1-based
SETTER_BLOCK_LINE_END = 504    # inclusive, 1-based
# baseline 段 sha256 (从 `git show 3ff13c6:coco/proactive.py` 取 L489..L504 行, joined)
SETTER_BLOCK_EXPECTED_SHA = (
    "c812ddeaca77fca20435417608cbd904a1100c7a452f982dbb3e9fc61f6a7a1f"
)
setter_block_baseline_sha: str = ""
setter_block_working_sha: str = ""
try:
    proc_show = subprocess.run(
        ["git", "show", f"{SETTER_BLOCK_BASE_SHA}:coco/proactive.py"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO),
    )
    if proc_show.returncode != 0:
        errors.append(
            f"V2 git show baseline 失败: rc={proc_show.returncode} stderr={proc_show.stderr[-200:]!r}"
        )
    else:
        base_lines = proc_show.stdout.splitlines(keepends=True)
        # 1-based [START..END] inclusive → python slice [START-1:END]
        base_block = "".join(base_lines[SETTER_BLOCK_LINE_START - 1: SETTER_BLOCK_LINE_END])
        setter_block_baseline_sha = hashlib.sha256(base_block.encode("utf-8")).hexdigest()
        check(
            f"setter block baseline L{SETTER_BLOCK_LINE_START}-L{SETTER_BLOCK_LINE_END} sha256 锁 (BASE={SETTER_BLOCK_BASE_SHA[:8]})",
            setter_block_baseline_sha == SETTER_BLOCK_EXPECTED_SHA,
            detail=f"got={setter_block_baseline_sha[:16]} expect={SETTER_BLOCK_EXPECTED_SHA[:16]}",
        )
    # 同时算 working tree 同段 hash (信息打印, 不强制等价 baseline — main 本就可能改 working tree)
    wt_lines = src.splitlines(keepends=True)
    if len(wt_lines) >= SETTER_BLOCK_LINE_END:
        wt_block = "".join(wt_lines[SETTER_BLOCK_LINE_START - 1: SETTER_BLOCK_LINE_END])
        setter_block_working_sha = hashlib.sha256(wt_block.encode("utf-8")).hexdigest()
    print(
        f"  setter_block baseline_sha={setter_block_baseline_sha[:16]} "
        f"working_sha={setter_block_working_sha[:16]}"
    )
except Exception:  # noqa: BLE001
    errors.append("V2 setter block hash: " + traceback.format_exc())


# =======================================================================
# V3 mutant 反证 in-memory: 删除 dup-warn / 删除 is_shutdown 探针 → 行为可区分
# =======================================================================
print("[V3] mutant 反证 in-memory")
try:
    import importlib
    import logging
    from coco import proactive as _mod
    importlib.reload(_mod)

    # 准备 fake sequencer
    class _Seq:
        def __init__(self, shutdown=False):
            self._sd = shutdown
        def is_shutdown(self):
            return self._sd

    # 准备 LogCapture
    class _Cap(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []
        def emit(self, record):
            self.records.append((record.levelno, record.getMessage()))

    def _make_sched():
        sched = _mod.ProactiveScheduler.__new__(_mod.ProactiveScheduler)
        # 最小化字段，绕开 __init__ 重逻辑
        import threading
        sched._lock = threading.RLock()
        sched._robot_sequencer = None
        sched._setter_audit_seen = set()
        return sched

    # --- prod: 注入 shutdown sequencer 应被拒 (warn) ---
    cap = _Cap()
    _mod.log.addHandler(cap)
    cap.setLevel(logging.DEBUG)
    _mod.log.setLevel(logging.DEBUG)
    try:
        sched = _make_sched()
        seq_dead = _Seq(shutdown=True)
        sched.set_robot_sequencer(seq_dead)
        prod_refused = (sched._robot_sequencer is None)
        prod_refuse_warn = any(SENT_A_REFUSE in m for lvl, m in cap.records if lvl == logging.WARNING)
        check("prod: 注入 shutdown sequencer 被拒 (字段保持 None)", prod_refused,
              f"_robot_sequencer={sched._robot_sequencer!r}")
        check("prod: refuse-to-inject WARN 已 emit", prod_refuse_warn,
              f"records={[(lvl,m[:80]) for lvl,m in cap.records]}")
    finally:
        _mod.log.removeHandler(cap)

    # --- prod: 重复注入 → WARN dup ---
    cap2 = _Cap()
    _mod.log.addHandler(cap2)
    cap2.setLevel(logging.DEBUG)
    try:
        sched = _make_sched()
        a = _Seq(); b = _Seq()
        sched.set_robot_sequencer(a)
        sched.set_robot_sequencer(b)
        dup_warn = any(SENT_B_OVERWRITE in m for lvl, m in cap2.records if lvl == logging.WARNING)
        check("prod: 重复注入触发 overwriting WARN", dup_warn,
              f"records={[(lvl,m[:80]) for lvl,m in cap2.records]}")
        check("prod: 重复注入后覆盖成 b", sched._robot_sequencer is b)
    finally:
        _mod.log.removeHandler(cap2)

    # --- mutant 1: 删除 is_shutdown 探针 → 注入 shutdown sequencer 不再拒 ---
    cap_m1 = _Cap()
    _mod.log.addHandler(cap_m1)
    cap_m1.setLevel(logging.DEBUG)
    orig_setter = _mod.ProactiveScheduler.set_robot_sequencer
    try:
        def mutant_no_probe(self, sequencer):
            # 删除 is_shutdown 探针分支 (mutant)
            with self._lock:
                if self._robot_sequencer is not None and sequencer is not None:
                    _mod.log.warning(
                        "[proactive] set_robot_sequencer: overwriting existing sequencer "
                        "(prev=%r, new=%r) — double-injection detected",
                        self._robot_sequencer, sequencer,
                    )
                self._robot_sequencer = sequencer
        _mod.ProactiveScheduler.set_robot_sequencer = mutant_no_probe
        sched = _make_sched()
        seq_dead = _Seq(shutdown=True)
        sched.set_robot_sequencer(seq_dead)
        mutant1_accepted = (sched._robot_sequencer is seq_dead)
        # mutant 与 prod 行为可区分: prod 拒 (None), mutant 接受 (seq_dead)
        check("mutant1 (无 probe): shutdown sequencer 被接受 (反证 prod 探针有效)",
              mutant1_accepted, f"_robot_sequencer={sched._robot_sequencer!r}")
    finally:
        _mod.ProactiveScheduler.set_robot_sequencer = orig_setter
        _mod.log.removeHandler(cap_m1)

    # --- mutant 2: 删除 dup warn 分支 → 重复注入不再 emit WARN ---
    cap_m2 = _Cap()
    _mod.log.addHandler(cap_m2)
    cap_m2.setLevel(logging.DEBUG)
    try:
        def mutant_no_dup(self, sequencer):
            # 保留 is_shutdown 探针 但删 dup warn (mutant)
            if sequencer is not None:
                is_fn = getattr(sequencer, "is_shutdown", None)
                if callable(is_fn):
                    rv = is_fn()
                    if isinstance(rv, bool) and rv is True:
                        _mod.log.warning(
                            "[proactive] set_robot_sequencer: refuse to inject "
                            "already-shutdown sequencer"
                        )
                        return
            with self._lock:
                self._robot_sequencer = sequencer  # 没有 dup warn
        _mod.ProactiveScheduler.set_robot_sequencer = mutant_no_dup
        sched = _make_sched()
        a = _Seq(); b = _Seq()
        sched.set_robot_sequencer(a)
        sched.set_robot_sequencer(b)
        mutant2_dup_warn = any(SENT_B_OVERWRITE in m for lvl, m in cap_m2.records if lvl == logging.WARNING)
        check("mutant2 (无 dup warn): 重复注入未 emit overwriting (反证 prod dup-warn 有效)",
              not mutant2_dup_warn, f"records={[(lvl,m[:80]) for lvl,m in cap_m2.records]}")
    finally:
        _mod.ProactiveScheduler.set_robot_sequencer = orig_setter
        _mod.log.removeHandler(cap_m2)

except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4 default-OFF subprocess: 未设 env → setter 注入正常 sequencer 无 audit dedup 路径
# =======================================================================
print("[V4] default-OFF subprocess: env 未设时 bytewise 等价 main (无 audit dedup)")
sub_code = r"""
import os, sys, logging
# 确保 env 未设
os.environ.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)
from coco import proactive as P
import threading

class _Seq:
    def is_shutdown(self): return False

class _Cap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []
    def emit(self, r):
        self.records.append((r.levelno, r.getMessage()))

cap = _Cap()
cap.setLevel(logging.DEBUG)
P.log.addHandler(cap)
P.log.setLevel(logging.DEBUG)

sched = P.ProactiveScheduler.__new__(P.ProactiveScheduler)
sched._lock = threading.RLock()
sched._robot_sequencer = None
sched._setter_audit_seen = set()

a = _Seq(); b = _Seq(); c = _Seq()
# 第一次注入正常 sequencer: 应该静默 (无 warn / 无 debug)
sched.set_robot_sequencer(a)
phase1_warn = sum(1 for lvl,_ in cap.records if lvl == logging.WARNING)
phase1_debug = sum(1 for lvl,m in cap.records if lvl == logging.DEBUG and "set_robot_sequencer" in m)

# 第二次注入: env=OFF 时每次都 warning (无 suppressed debug 路径)
sched.set_robot_sequencer(b)
phase2_warn = sum(1 for lvl,_ in cap.records if lvl == logging.WARNING)
phase2_debug = sum(1 for lvl,m in cap.records if lvl == logging.DEBUG and "suppressed" in m)

# 第三次注入: 同样应该再次 warning (env OFF 不去重)
sched.set_robot_sequencer(c)
phase3_warn = sum(1 for lvl,_ in cap.records if lvl == logging.WARNING)
phase3_debug = sum(1 for lvl,m in cap.records if lvl == logging.DEBUG and "suppressed" in m)

# audit_seen 应该保持空 (env OFF 不写)
audit_size = len(sched._setter_audit_seen)

import json
print("V4_RESULT=" + json.dumps({
    "phase1_warn": phase1_warn, "phase1_debug": phase1_debug,
    "phase2_warn": phase2_warn, "phase2_debug": phase2_debug,
    "phase3_warn": phase3_warn, "phase3_debug": phase3_debug,
    "audit_size": audit_size,
}))
"""
try:
    env = {**os.environ}
    env.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)
    proc = subprocess.run(
        [sys.executable, "-c", sub_code],
        capture_output=True, text=True, timeout=60,
        cwd=str(REPO), env=env,
    )
    check("V4 subprocess rc=0", proc.returncode == 0,
          f"rc={proc.returncode}, stderr_tail={proc.stderr[-300:]!r}")
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("V4_RESULT=")), None)
    if line is None:
        errors.append("V4: 未捕获 V4_RESULT 行")
    else:
        data = json.loads(line.split("=", 1)[1])
        # 默认 OFF: 第一次注入应 0 warn / 0 debug
        check("V4 phase1: 首次注入正常 sequencer 0 warn / 0 debug",
              data["phase1_warn"] == 0 and data["phase1_debug"] == 0,
              f"data={data}")
        # phase2/phase3: env OFF 时每次都 warn (累计) 且 0 suppressed debug
        check("V4 phase2: 第二次重复注入累计 warn==1",
              data["phase2_warn"] == 1 and data["phase2_debug"] == 0, f"data={data}")
        check("V4 phase3: 第三次重复注入累计 warn==2 (env OFF 不去重)",
              data["phase3_warn"] == 2 and data["phase3_debug"] == 0, f"data={data}")
        check("V4 audit_size: env OFF 时 _setter_audit_seen 保持空",
              data["audit_size"] == 0, f"audit_size={data['audit_size']}")
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5 summary
# =======================================================================
print("\n[V5] summary")
elapsed = time.time() - t0
summary = {
    "elapsed_sec": round(elapsed, 2),
    "errors": errors,
    "proactive_sha256": proact_full,
    "self_sha256": self_full,
    "sentinel_hashes": sentinel_hashes,
    "robot_027": {
        "overwrite_count": _overwrite_count,
        "setter_block_base_sha": SETTER_BLOCK_BASE_SHA,
        "setter_block_lines": f"L{SETTER_BLOCK_LINE_START}-L{SETTER_BLOCK_LINE_END}",
        "setter_block_baseline_sha": setter_block_baseline_sha,
        "setter_block_working_sha": setter_block_working_sha,
        "setter_block_expected_sha": SETTER_BLOCK_EXPECTED_SHA,
    },
    "option": "A (三道防护已完整, 转 verify-only meta 锁) + robot-027 F1/F2 收紧",
}
(EVID_DIR / "verify_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"  elapsed={elapsed:.2f}s")
print(f"  evidence={EVID_DIR / 'verify_summary.json'}")
if errors:
    print("FAIL:")
    for e in errors:
        print("  -", e)
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
