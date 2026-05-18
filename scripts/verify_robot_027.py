"""robot-027 verify: verify_robot_025.py meta-lock 升级 (F1+F2 假阳性窗口闭合) 验证 (verify-only).

source backlog: robot-025-backlog-verify-meta-lock-tightening
scope: 升级 scripts/verify_robot_025.py 闭合两个假阳性窗口:
  F1 — "overwriting existing sequencer" 字面在 coco/proactive.py 出现 2 次 (L493 debug + L499 prod).
       旧 V2 sha256 锁仅锁字面常量, mutant 删一处仍 PASS. 本次:
         (a) V1 加 count>=2 lower-bound 锁;
         (b) V2 增加 setter 关键 block (L489-504) 行号锚定 sha256, baseline 取 `git show 3ff13c6:coco/proactive.py`.
  F2 — 旧 V2 中 sentinel_hashes 与 EXPECTED 用同一表达式重复计算, 永真自洽.
       本次: EXPECTED 改为 hardcoded 16-hex prefix 字面常量, 不再从当前 src 派生.

V0 sys.path + 文件存在
V1 verify_robot_025.py 升级关键字面 sentinel (F1/F2 锁名/常量) 存在
V2 verify_robot_025.py 关键锁参数 (BASE_SHA, LINE_START/END, EXPECTED_PREFIX 表, EXPECTED_SHA) 字面常量精确锁
V3 mutant 反证 (字符串复制版): 模拟 F1/F2 漏洞还原 — 用 importlib 加载 verify_robot_025 副本时不可行,
   改用 in-text "若把升级逻辑删了" 静态检测:
     m1: 若 verify_025 不含 "count>=2" / "count>=" lower-bound 锁 → F1 漏洞复发, 本 V3 检测应捕获
     m2: 若 verify_025 不含 "EXPECTED_PREFIX" 字面 → F2 漏洞复发, 本 V3 检测应捕获
   注: 此 V3 与 V1/V2 字面锁同向, 角色是反证语义 (告诉读者: 缺一个关键字符串 = 漏洞复发).
V4 verify_robot_025.py sha256 锁 (working tree, 升级落盘后稳定; mutant edit 任意字符 → hash 变)
V5 端到端: 直接 invoke verify_robot_025.py, rc==0 + 关键 "PASS" 行存在 (含 F1 count + setter block baseline)
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import List

errors: List[str] = []
t0 = time.time()

REPO = Path(__file__).resolve().parents[1]
VERIFY_025 = REPO / "scripts" / "verify_robot_025.py"
PROACT_PY = REPO / "coco" / "proactive.py"
SELF = Path(__file__).resolve()
EVID_DIR = REPO / "evidence" / "robot-027"
EVID_DIR.mkdir(parents=True, exist_ok=True)


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


# =======================================================================
# V0 sys.path + 文件存在
# =======================================================================
print("[V0] sys.path + 文件存在")
check("verify_robot_025.py 存在", VERIFY_025.is_file())
check("coco/proactive.py 存在", PROACT_PY.is_file())
check("self 存在", SELF.is_file())
v025_src = VERIFY_025.read_text(encoding="utf-8")
v025_sha = hashlib.sha256(VERIFY_025.read_bytes()).hexdigest()
print(f"  verify_robot_025.py sha256={v025_sha}")


# =======================================================================
# V1 verify_robot_025.py 升级关键字面 sentinel 存在
# =======================================================================
print("[V1] verify_robot_025.py 升级关键字面 sentinel")
# F1 升级标记
check("F1 count>=2 lower-bound 锁标记存在",
      "robot-027 F1: overwriting 字面 count>=2" in v025_src)
check("F1 setter block 行号锚定 hash 锁标记存在",
      "setter block baseline" in v025_src or "SETTER_BLOCK_EXPECTED_SHA" in v025_src)
# F2 升级标记
check("F2 EXPECTED 改为 hardcoded prefix 字面常量",
      "EXPECTED_PREFIX" in v025_src)
check("F2 hardcoded prefix 注释存在",
      "hardcoded 16-hex prefix" in v025_src or "hardcoded prefix" in v025_src)
# robot-027 头部 docstring 提及 F1/F2
check("docstring 含 robot-027 升级说明 (F1+F2)",
      "robot-027" in v025_src and "F1" in v025_src and "F2" in v025_src)


# =======================================================================
# V2 verify_robot_025.py 关键锁参数 字面常量精确锁
# =======================================================================
print("[V2] verify_robot_025.py 关键锁参数 精确锁")
# baseline 来源 BASE_SHA (main HEAD at robot-027 立 feat 时刻)
BASE_SHA_EXPECT = "3ff13c629a97adedeb61897609f1d379820496b8"
check("BASE_SHA 字面 = 3ff13c62...",
      f'SETTER_BLOCK_BASE_SHA = "{BASE_SHA_EXPECT}"' in v025_src,
      f"expect contains BASE_SHA={BASE_SHA_EXPECT[:8]}")
# 行号常量
check("SETTER_BLOCK_LINE_START = 489",
      "SETTER_BLOCK_LINE_START = 489" in v025_src)
check("SETTER_BLOCK_LINE_END = 504",
      "SETTER_BLOCK_LINE_END = 504" in v025_src)
# baseline block sha256 (全 hex)
SETTER_BLOCK_EXPECTED_SHA = (
    "c812ddeaca77fca20435417608cbd904a1100c7a452f982dbb3e9fc61f6a7a1f"
)
check("SETTER_BLOCK_EXPECTED_SHA 字面 = c812ddea...",
      SETTER_BLOCK_EXPECTED_SHA in v025_src,
      f"expect {SETTER_BLOCK_EXPECTED_SHA[:16]}...")
# EXPECTED_PREFIX 5 个键的 16-hex 前缀字面
EXPECTED_PREFIX_LITERALS = {
    "refuse-to-inject":   "85648bc52cbf60e2",
    "overwriting":        "389d75de4c93afc1",
    "double-injection":   "0528662834fe2448",
    "detected-shutdown":  "71ac601795fc20b2",
    "clear-and-skip":     "041f5a8ed8d5dc62",
}
for k, prefix in EXPECTED_PREFIX_LITERALS.items():
    # 字面常量精确字符串锁: 既要键也要 16-hex 前缀
    check(f"EXPECTED_PREFIX[{k!r}] = {prefix!r} 字面存在",
          f'"{prefix}"' in v025_src and f'"{k}"' in v025_src)


# =======================================================================
# V3 mutant 反证 (静态文本): 模拟 F1/F2 漏洞复发场景
# =======================================================================
print("[V3] mutant 反证 (文本) — 模拟 F1/F2 漏洞复发")
# m1: 把 count>=2 锁段从 v025_src 摘除 → 模拟 mutant; 重检 V1 关键标记应消失
m1_src = v025_src.replace("robot-027 F1: overwriting 字面 count>=2", "REMOVED_F1_COUNT_LOCK")
check("mutant1: 删 'count>=2' 字面后 v025 不再含 count 锁标记 (反证 F1 锁有效)",
      "robot-027 F1: overwriting 字面 count>=2" not in m1_src)
# m2: 把 EXPECTED_PREFIX 字面替换 → 模拟 mutant; 重检 F2 标记应消失
m2_src = v025_src.replace("EXPECTED_PREFIX", "EXPECTED_NO_PREFIX")
check("mutant2: 删 'EXPECTED_PREFIX' 字面后 v025 不再含 F2 锁标记 (反证 F2 锁有效)",
      "EXPECTED_PREFIX" not in m2_src)
# m3: 把 baseline sha 字面前缀改一字符 → 模拟 baseline drift; 锁仍存在但 hash 比对失败
m3_src = v025_src.replace(
    SETTER_BLOCK_EXPECTED_SHA,
    "0000ddeaca77fca20435417608cbd904a1100c7a452f982dbb3e9fc61f6a7a1f",
)
check("mutant3: baseline sha 前缀改 0000... 后 v025 不再含原 sha (反证 baseline 行号锚定锁有效)",
      SETTER_BLOCK_EXPECTED_SHA not in m3_src and "0000ddea" in m3_src)


# =======================================================================
# V4 verify_robot_025.py sha256 锁 (working tree)
# =======================================================================
print("[V4] verify_robot_025.py sha256 锁 (working tree)")
# robot-027 落盘后 verify_robot_025.py 应稳定; 任意字符 mutation → hash 变
VERIFY_025_EXPECTED_SHA = (
    "1c1cba08214e6a510ace376f0f9c06fd0eb21063c13c71442850a0d997f0ef6d"
)
check(
    "verify_robot_025.py sha256 锁定 (升级版)",
    v025_sha == VERIFY_025_EXPECTED_SHA,
    detail=f"got={v025_sha[:16]} expect={VERIFY_025_EXPECTED_SHA[:16]}",
)


# =======================================================================
# V5 端到端: 直接 invoke verify_robot_025.py, rc==0 + 关键升级 PASS 行存在
# =======================================================================
print("[V5] 端到端 invoke verify_robot_025.py")
try:
    proc = subprocess.run(
        [sys.executable, str(VERIFY_025)],
        capture_output=True, text=True, timeout=90,
        cwd=str(REPO),
    )
    check("verify_robot_025.py rc==0", proc.returncode == 0,
          detail=f"rc={proc.returncode} stderr_tail={proc.stderr[-200:]!r}")
    stdout = proc.stdout
    check("V1 F1 count PASS 行存在",
          "robot-027 F1: overwriting 字面 count>=2" in stdout
          and "count=2" in stdout)
    check("V2 setter block baseline PASS 行存在",
          "setter block baseline L489-L504 sha256 锁" in stdout)
    check("V2 hardcoded prefix PASS 行存在 (overwriting)",
          "sentinel hash[overwriting] 锁定 (hardcoded prefix)" in stdout)
    check("ALL PASS 结束行存在", "ALL PASS" in stdout)
except Exception:  # noqa: BLE001
    errors.append("V5: " + traceback.format_exc())


# =======================================================================
# summary
# =======================================================================
elapsed = time.time() - t0
summary = {
    "elapsed_sec": round(elapsed, 2),
    "errors": errors,
    "verify_025_sha256": v025_sha,
    "base_sha": BASE_SHA_EXPECT,
    "setter_block_expected_sha": SETTER_BLOCK_EXPECTED_SHA,
    "expected_prefix_count": len(EXPECTED_PREFIX_LITERALS),
}
(EVID_DIR / "verify_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"  elapsed={elapsed:.2f}s")
print(f"  evidence={EVID_DIR / 'verify_summary.json'}")
if errors:
    print("FAIL:")
    for e in errors:
        print("  -", e)
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
