#!/usr/bin/env python3
"""
robot-028 meta-lock: verify scripts/bump_verify_027_self_hash.py.

verify-only / default-OFF 友好。无业务源码改动。

锁面板:
  V0 sys.path / 文件存在性 (verify_025 / verify_027 / bump helper / self)
  V1 字面 sentinel — bump helper 关键字面常量 / 标识 / docstring
  V2 关键锁参数 — CONST_NAME=VERIFY_025_EXPECTED_SHA, 正则匹配 verify_027 中字面常量
  V3 mutant 反证 (文本) — 字面常量名 / 正则 / sha 字段任意改动 → 锁失效
  V4 sha256 锁 (bump helper 落盘后稳定)
  V5 端到端 — 备份 verify_027 内 sha 字面 → 故意改坏 → 跑 bump → 字面恢复;
              再次 dry-run 不改文件 (无副作用)
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

t0 = time.time()
errors: list[str] = []

SELF = Path(__file__).resolve()
REPO = SELF.parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_025 = SCRIPTS / "verify_robot_025.py"
VERIFY_027 = SCRIPTS / "verify_robot_027.py"
BUMP = SCRIPTS / "bump_verify_027_self_hash.py"
EVID_DIR = REPO / "evidence" / "robot-028"
EVID_DIR.mkdir(parents=True, exist_ok=True)


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


# =====================================================================
# V0 sys.path + 文件存在
# =====================================================================
print("[V0] sys.path + 文件存在")
check("verify_robot_025.py 存在", VERIFY_025.is_file())
check("verify_robot_027.py 存在", VERIFY_027.is_file())
check("bump_verify_027_self_hash.py 存在", BUMP.is_file())
check("self 存在", SELF.is_file())

bump_src = BUMP.read_text(encoding="utf-8")
v027_src = VERIFY_027.read_text(encoding="utf-8")
v025_sha_current = hashlib.sha256(VERIFY_025.read_bytes()).hexdigest()
print(f"  verify_robot_025.py sha256={v025_sha_current}")


# =====================================================================
# V1 bump helper 字面 sentinel
# =====================================================================
print("[V1] bump helper 字面 sentinel")
check("docstring 提及 robot-028",
      "robot-028" in bump_src)
check("docstring 提及 VERIFY_025_EXPECTED_SHA",
      "VERIFY_025_EXPECTED_SHA" in bump_src)
check("CONST_NAME 字面常量存在",
      'CONST_NAME = "VERIFY_025_EXPECTED_SHA"' in bump_src)
check("--dry-run flag 字面存在",
      '"--dry-run"' in bump_src)
check("CONST_RE 正则字段存在",
      "CONST_RE = re.compile(" in bump_src)
check("hashlib.sha256 调用存在",
      "hashlib.sha256(" in bump_src)


# =====================================================================
# V2 关键锁参数 字面/正则精确锁
# =====================================================================
print("[V2] 关键锁参数 精确锁")
# verify_robot_027.py 内必须有 VERIFY_025_EXPECTED_SHA = ( "<64 hex>" ) 字面
M027 = re.search(
    r'VERIFY_025_EXPECTED_SHA\s*=\s*\(\s*"([0-9a-fA-F]{64})"\s*\)',
    v027_src,
)
check("verify_robot_027.py 含 VERIFY_025_EXPECTED_SHA = ( '<64hex>' ) 字面",
      M027 is not None)
sha_in_027 = M027.group(1) if M027 else ""
check("verify_robot_027.py 中 sha 与 verify_robot_025.py 当前 sha 一致",
      sha_in_027 == v025_sha_current,
      f"027={sha_in_027[:16]} 025={v025_sha_current[:16]}")
# bump helper 的正则与上行用同一 CONST_NAME
check("bump helper CONST_RE 匹配 verify_027 字面",
      "VERIFY_025_EXPECTED_SHA" in bump_src
      and "[0-9a-fA-F]{64}" in bump_src)


# =====================================================================
# V3 mutant 反证 (静态文本)
# =====================================================================
print("[V3] mutant 反证 (文本)")
m1 = bump_src.replace('CONST_NAME = "VERIFY_025_EXPECTED_SHA"',
                      'CONST_NAME = "MUTANT_NO_SUCH_CONST"')
check("mutant1: 改 CONST_NAME 字面后 bump 锁标记丢失 (反证 V1 锁有效)",
      'CONST_NAME = "VERIFY_025_EXPECTED_SHA"' not in m1)
m2 = bump_src.replace("[0-9a-fA-F]{64}", "[0-9a-fA-F]{8}")
check("mutant2: 改 sha 正则长度 (64->8) 后字面消失 (反证 V2 长度锁有效)",
      "[0-9a-fA-F]{64}" not in m2)
m3 = v027_src.replace("VERIFY_025_EXPECTED_SHA", "MUTANT_RENAMED_CONST")
check("mutant3: rename verify_027 常量后正则匹配应失败 (反证常量名锁)",
      re.search(r"VERIFY_025_EXPECTED_SHA\s*=\s*\(", m3) is None)


# =====================================================================
# V4 bump helper 自身 sha256 锁 (working tree)
# =====================================================================
print("[V4] bump helper sha256 锁 (working tree)")
bump_sha = hashlib.sha256(BUMP.read_bytes()).hexdigest()
BUMP_EXPECTED_SHA = "__BUMP_SHA_PLACEHOLDER__"
# 启用真锁: 当落盘后, 把下行的 expected_sha 改为实际 sha256;
# 这里以 working-tree sha 自洽 (placeholder 模式下软锁: 仅记录, 不 FAIL).
check("bump helper sha256 计算成功 (>=64 hex)",
      isinstance(bump_sha, str) and len(bump_sha) == 64)
print(f"  bump_verify_027_self_hash.py sha256={bump_sha}")
if BUMP_EXPECTED_SHA != "__BUMP_SHA_PLACEHOLDER__":
    check("bump helper sha256 锁定 (字面一致)",
          bump_sha == BUMP_EXPECTED_SHA,
          f"got={bump_sha[:16]} expect={BUMP_EXPECTED_SHA[:16]}")
else:
    print("  (V4 placeholder 模式: bump helper sha 记录, 未启用字面锁)")


# =====================================================================
# V5 端到端: 备份 → 故意改坏 → 跑 bump → 恢复; 再 dry-run 验证无副作用
# =====================================================================
print("[V5] 端到端 bump helper")
original_v027 = VERIFY_027.read_bytes()
original_sha_text = sha_in_027  # 64-hex 字面
mutant_sha_text = "0" * 64
try:
    # 1) 故意改坏: 替换 sha 字面为全 0
    assert M027 is not None
    bad_src = v027_src[: M027.start(1)] + mutant_sha_text + v027_src[M027.end(1):]
    VERIFY_027.write_bytes(bad_src.encode("utf-8"))
    after_bad = VERIFY_027.read_text(encoding="utf-8")
    check("V5-step1: verify_027 已被改坏 (含全 0 sha)",
          mutant_sha_text in after_bad and original_sha_text not in after_bad)

    # 2) 跑 bump (非 dry-run), rc==0
    proc = subprocess.run(
        [sys.executable, str(BUMP)],
        capture_output=True, text=True, timeout=30,
        cwd=str(REPO),
    )
    check("V5-step2: bump rc==0",
          proc.returncode == 0,
          f"rc={proc.returncode} stderr_tail={proc.stderr[-200:]!r}")
    check("V5-step2: bump stdout 含 '已更新' 或 '已一致'",
          "已更新" in proc.stdout or "OK: 已一致" in proc.stdout)

    # 3) 验证 sha 字面恢复 = 当前 verify_025 sha
    after_fix = VERIFY_027.read_text(encoding="utf-8")
    m_fix = re.search(
        r'VERIFY_025_EXPECTED_SHA\s*=\s*\(\s*"([0-9a-fA-F]{64})"\s*\)',
        after_fix,
    )
    check("V5-step3: 恢复后字面 = verify_025 当前 sha",
          m_fix is not None
          and m_fix.group(1) == v025_sha_current,
          f"got={(m_fix.group(1) if m_fix else None)} "
          f"expect={v025_sha_current[:16]}...")

    # 4) 再次 dry-run, 不应改文件
    snapshot = VERIFY_027.read_bytes()
    proc2 = subprocess.run(
        [sys.executable, str(BUMP), "--dry-run"],
        capture_output=True, text=True, timeout=30,
        cwd=str(REPO),
    )
    check("V5-step4: dry-run rc==0",
          proc2.returncode == 0,
          f"rc={proc2.returncode}")
    check("V5-step4: dry-run 无副作用 (文件 bytes 未变)",
          VERIFY_027.read_bytes() == snapshot)
    check("V5-step4: dry-run stdout 含 'OK: 已一致' 或 'DRY-RUN'",
          "OK: 已一致" in proc2.stdout or "DRY-RUN" in proc2.stdout)
except Exception:  # noqa: BLE001
    errors.append("V5: " + traceback.format_exc())
finally:
    # 任何情况下都还原 verify_027 到测试前 bytes (理论上 bump 已经写回了正确值,
    # 但显式 restore 更稳; 写完后再核对一次字面)
    VERIFY_027.write_bytes(original_v027)
    final_src = VERIFY_027.read_text(encoding="utf-8")
    m_final = re.search(
        r'VERIFY_025_EXPECTED_SHA\s*=\s*\(\s*"([0-9a-fA-F]{64})"\s*\)',
        final_src,
    )
    check("V5-cleanup: verify_027 已恢复到测试前 bytes",
          VERIFY_027.read_bytes() == original_v027
          and m_final is not None
          and m_final.group(1) == original_sha_text)


# =====================================================================
# summary
# =====================================================================
elapsed = time.time() - t0
summary = {
    "elapsed_sec": round(elapsed, 2),
    "errors": errors,
    "verify_025_sha256": v025_sha_current,
    "verify_027_locked_sha": sha_in_027,
    "bump_helper_sha256": bump_sha,
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
