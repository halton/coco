#!/usr/bin/env python3
"""
robot-031 meta-lock: verify scripts/bump_verify_028_self_hash.py +
verify_robot_028.py V4 字面硬锁升级.

verify-only / default-OFF 友好。无业务源码改动。

锁面板:
  V0 sys.path / 文件存在性 (verify_028 / bump_027 / bump_028 / self)
  V1 字面 sentinel — 关键字面常量 / 标识 / docstring (含 hardcoded 64hex 短语)
  V2 关键锁参数 — verify_028 V4 中 BUMP_EXPECTED_SHA 字面常量
                  + bump_028 中 CONST_NAME 字面 + 64hex 正则
  V3 mutant 反证 — BUMP_EXPECTED_SHA 字面改 1 位 → verify_028 V4 FAIL
  V4 sha256 锁 — verify_robot_028.py 升级后 hash + bump_verify_028 自身 hash
  V5 端到端 — subprocess invoke verify_robot_028.py rc==0
              + bump_verify_028_self_hash.py --dry-run rc==0
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
VERIFY_028 = SCRIPTS / "verify_robot_028.py"
BUMP_027 = SCRIPTS / "bump_verify_027_self_hash.py"
BUMP_028 = SCRIPTS / "bump_verify_028_self_hash.py"
EVID_DIR = REPO / "evidence" / "robot-031"
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
check("verify_robot_028.py 存在", VERIFY_028.is_file())
check("bump_verify_027_self_hash.py 存在", BUMP_027.is_file())
check("bump_verify_028_self_hash.py 存在", BUMP_028.is_file())
check("self 存在", SELF.is_file())

v028_src = VERIFY_028.read_text(encoding="utf-8")
b028_src = BUMP_028.read_text(encoding="utf-8")
bump027_sha_current = hashlib.sha256(BUMP_027.read_bytes()).hexdigest()
v028_sha_current = hashlib.sha256(VERIFY_028.read_bytes()).hexdigest()
b028_sha_current = hashlib.sha256(BUMP_028.read_bytes()).hexdigest()
print(f"  bump_verify_027_self_hash.py sha256={bump027_sha_current}")


# =====================================================================
# V1 字面 sentinel
# =====================================================================
print("[V1] 字面 sentinel")
check("verify_028 含 BUMP_EXPECTED_SHA 字面",
      "BUMP_EXPECTED_SHA" in v028_src)
check("verify_028 已脱离 placeholder 模式",
      "__BUMP_SHA_PLACEHOLDER__" not in v028_src)
check("verify_028 含 robot-031 升级注释 (hardcoded 标记)",
      "robot-031" in v028_src and "硬锁" in v028_src)
check("bump_028 docstring 提及 robot-031",
      "robot-031" in b028_src)
check("bump_028 提及 bump_verify_028_self_hash",
      "bump_verify_028_self_hash" in b028_src)
check("bump_028 CONST_NAME = BUMP_EXPECTED_SHA 字面存在",
      'CONST_NAME = "BUMP_EXPECTED_SHA"' in b028_src)
check("bump_028 --dry-run flag 字面存在",
      '"--dry-run"' in b028_src)
check("bump_028 CONST_RE 正则字段 (64 hex) 存在",
      "CONST_RE" in b028_src and "[0-9a-fA-F]{64}" in b028_src)
check("bump_028 hashlib.sha256 调用存在",
      "hashlib.sha256(" in b028_src)


# =====================================================================
# V2 关键锁参数 精确锁
# =====================================================================
print("[V2] 关键锁参数 精确锁")
M028 = re.search(
    r'BUMP_EXPECTED_SHA\s*=\s*\(\s*"([0-9a-fA-F]{64})"\s*\)',
    v028_src,
)
check("verify_028 V4 含 BUMP_EXPECTED_SHA = ( '<64hex>' ) 字面硬锁",
      M028 is not None)
sha_in_028 = M028.group(1) if M028 else ""
check("verify_028 V4 硬锁 sha == bump_verify_027 当前 sha",
      sha_in_028 == bump027_sha_current,
      f"locked={sha_in_028[:16]} actual={bump027_sha_current[:16]}")
check("bump_028 CONST_RE 与 verify_028 字面同名同正则",
      "BUMP_EXPECTED_SHA" in b028_src
      and "[0-9a-fA-F]{64}" in b028_src)


# =====================================================================
# V3 mutant 反证: BUMP_EXPECTED_SHA 字面改 1 位 → verify_028 V4 FAIL
# =====================================================================
print("[V3] mutant 反证 (BUMP_EXPECTED_SHA 改 1 位 → V4 FAIL)")
assert M028 is not None
# 把硬锁 sha 第 1 位 0/1 翻转一下
first_ch = sha_in_028[0]
flipped = ("1" if first_ch == "0" else "0") + sha_in_028[1:]
mutant_v028 = (
    v028_src[: M028.start(1)] + flipped + v028_src[M028.end(1):]
)
original_v028_bytes = VERIFY_028.read_bytes()
try:
    VERIFY_028.write_text(mutant_v028, encoding="utf-8")
    proc_m = subprocess.run(
        [sys.executable, str(VERIFY_028)],
        capture_output=True, text=True, timeout=30,
        cwd=str(REPO),
    )
    check(
        "mutant verify_028: rc != 0 (V4 硬锁应失效)",
        proc_m.returncode != 0,
        f"rc={proc_m.returncode}",
    )
    check(
        "mutant verify_028 stdout 含 V4 FAIL 行",
        "[FAIL] bump helper sha256 硬锁" in proc_m.stdout
        or "FAIL" in proc_m.stdout,
    )
except Exception:  # noqa: BLE001
    errors.append("V3 mutant: " + traceback.format_exc())
finally:
    VERIFY_028.write_bytes(original_v028_bytes)
    # 复核还原
    check(
        "V3 cleanup: verify_028 已恢复到测试前 bytes",
        VERIFY_028.read_bytes() == original_v028_bytes,
    )


# =====================================================================
# V4 sha256 锁: verify_028 + bump_028 working tree
# =====================================================================
print("[V4] sha256 锁 (working tree)")
# 这两个常量在 robot-031 commit 落盘后稳定, 由后续 feature 维护.
VERIFY_028_EXPECTED_SHA = (
    "c116a3d6e0338456369dab92ba828cbda89341ded4cbd7d70518bcf25af5679f"
)
BUMP_028_EXPECTED_SHA = (
    "25e31634925d2a2ec22202728fc8938db9a35b097ba85e07e08fe20cac5837eb"
)
check("verify_028 sha256 锁定 (字面一致)",
      v028_sha_current == VERIFY_028_EXPECTED_SHA,
      f"got={v028_sha_current[:16]} expect={VERIFY_028_EXPECTED_SHA[:16]}")
check("bump_028 sha256 锁定 (字面一致)",
      b028_sha_current == BUMP_028_EXPECTED_SHA,
      f"got={b028_sha_current[:16]} expect={BUMP_028_EXPECTED_SHA[:16]}")


# =====================================================================
# V5 端到端: subprocess invoke verify_028 + bump_028 --dry-run
# =====================================================================
print("[V5] 端到端 subprocess")
proc_v028 = subprocess.run(
    [sys.executable, str(VERIFY_028)],
    capture_output=True, text=True, timeout=60,
    cwd=str(REPO),
)
check("V5: verify_robot_028.py rc==0",
      proc_v028.returncode == 0,
      f"rc={proc_v028.returncode} stderr_tail={proc_v028.stderr[-200:]!r}")
check("V5: verify_028 stdout 含 'ALL PASS'",
      "ALL PASS" in proc_v028.stdout)

proc_b028_dry = subprocess.run(
    [sys.executable, str(BUMP_028), "--dry-run"],
    capture_output=True, text=True, timeout=30,
    cwd=str(REPO),
)
check("V5: bump_028 --dry-run rc==0",
      proc_b028_dry.returncode == 0,
      f"rc={proc_b028_dry.returncode}")
check("V5: bump_028 --dry-run stdout 含 'OK: 已一致' 或 'DRY-RUN'",
      "OK: 已一致" in proc_b028_dry.stdout
      or "DRY-RUN" in proc_b028_dry.stdout)


# =====================================================================
# summary
# =====================================================================
elapsed = time.time() - t0
summary = {
    "elapsed_sec": round(elapsed, 2),
    "errors": errors,
    "bump_verify_027_sha256": bump027_sha_current,
    "verify_028_locked_bump_sha": sha_in_028,
    "verify_028_sha256": v028_sha_current,
    "bump_028_sha256": b028_sha_current,
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
