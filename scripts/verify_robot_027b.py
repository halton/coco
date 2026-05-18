"""robot-027b verify: verify_robot_016 V3 exc_type 区分性反证 meta-lock.

robot-027b 是纯 verify-only backlog 收口 (robot-016 V3 Reviewer caveat):
  原 V3 仅在同一 sequencer + 同一 exc_type 下证 warn-once;
  未对"同 sequencer 不同 exc_type 各 1 次 WARNING"做正面反证。
  本 feature 在 verify_robot_016.py 新增 V_exc_type_discrim 档完成反证.

meta-lock 6 档:
  V0 sys.path 锚 — repo root 在前缀
  V1 字面 sentinel — verify_robot_016.py 含新增 V_exc_type_discrim 字面与关键断言
  V2 关键锁 — 升级后 V_exc_type_discrim 段必须含 (RuntimeError, ValueError 各 1)
              + audit_seen 2 条 probe-fail key + 不被首次抑制
  V3 mutant 反证 — 用 mutant patch 临时把 ValueError 改为 RuntimeError, 期望升级后 verify 失败
  V4 sha256 锁 — verify_robot_016.py working-tree sha256 锚定
  V5 端到端 — subprocess invoke verify_robot_016.py rc==0 且 stdout 含 V_exc_type_discrim PASS
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import time
import traceback
from typing import List

errors: List[str] = []
t0 = time.time()


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(REPO_ROOT, "scripts", "verify_robot_016.py")


# =======================================================================
# V0 sys.path — repo root 在前缀
# =======================================================================
print("V0: sys.path repo-root 前缀锚")
try:
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    check("V0: repo_root in sys.path", REPO_ROOT in sys.path)
    check("V0: target exists", os.path.exists(TARGET), f"path={TARGET}")
except Exception:  # noqa: BLE001
    errors.append("V0: " + traceback.format_exc())


# 读 target 源码
try:
    with open(TARGET, "r", encoding="utf-8") as f:
        SRC = f.read()
except Exception:  # noqa: BLE001
    SRC = ""
    errors.append("read TARGET: " + traceback.format_exc())


# =======================================================================
# V1 字面 sentinel — 升级后 verify_robot_016.py 必含 V_exc_type_discrim 字面
# =======================================================================
print("V1: 字面 sentinel — V_exc_type_discrim 段标识")
try:
    sentinels = [
        "V_exc_type_discrim",
        "RuntimeError",
        "ValueError",
        "rt-boom",
        "vl-boom",
        "is_shutdown probe failed: RuntimeError",
        "is_shutdown probe failed: ValueError",
    ]
    for s in sentinels:
        check(f"V1: sentinel {s!r} 出现", s in SRC)
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())


# =======================================================================
# V2 关键锁 — V_exc_type_discrim 段断言形状
# =======================================================================
print("V2: 关键锁 — V_exc_type_discrim 段断言齐全")
try:
    # 两种 exc_type 各 1 次 WARNING
    check("V2: 锚 WARNING == 2 (RuntimeError + ValueError 各 1)",
          "probe-fail WARNING == 2 (RuntimeError + ValueError 各 1)" in SRC)
    # 不被首次抑制
    check("V2: 锚 DEBUG suppressed == 0 (不应被首次抑制)",
          "probe-fail DEBUG suppressed == 0 (不应被首次抑制)" in SRC)
    # RuntimeError WARNING == 1
    check("V2: 锚 RuntimeError WARNING == 1",
          "RuntimeError WARNING == 1" in SRC)
    # ValueError WARNING == 1
    check("V2: 锚 ValueError WARNING == 1",
          "ValueError WARNING == 1" in SRC)
    # audit_seen 含 2 条 probe-fail key
    check("V2: 锚 audit_seen 含 2 条 probe-fail key",
          "audit_seen 含 2 条 probe-fail key" in SRC)
    # exc_type 集合断言
    check("V2: 锚 exc_type 集合 == {RuntimeError, ValueError}",
          "exc_type 集合 == {RuntimeError, ValueError}" in SRC)
    # 同 id 锚
    check("V2: 锚 两 key 同 id(seq)",
          "两 key 同 id(seq)" in SRC)
    # 必须 env=1 才能复现
    check("V2: 段内 env=1 set",
          'os.environ["COCO_ROBOT_SETTER_LIFECYCLE_AUDIT"] = "1"' in SRC)
except Exception:  # noqa: BLE001
    errors.append("V2: " + traceback.format_exc())


# =======================================================================
# V3 mutant 反证 — 把 V_exc_type_discrim 段内 ValueError 替换为 RuntimeError 后,
#   端到端跑 verify_robot_016.py 期望失败 (rc != 0)
# =======================================================================
print("V3: mutant 反证 — ValueError → RuntimeError 后 verify 应 FAIL")
try:
    # 定位 V_exc_type_discrim 段 (从段头到下一个 V5 段头)
    head = "# V_exc_type_discrim ON"
    tail = "# V5 regression"
    i = SRC.find(head)
    j = SRC.find(tail, i + 1) if i >= 0 else -1
    check("V3: 能定位 V_exc_type_discrim 段", i >= 0 and j > i,
          f"i={i}, j={j}")
    if i >= 0 and j > i:
        before = SRC[:i]
        seg = SRC[i:j]
        after = SRC[j:]
        # mutant: 把段内的 ValueError side_effect 换成 RuntimeError
        # (这样两次注入 exc_type 相同, 第二次会被首次 RuntimeError 抑制 → DEBUG, WARNING 只 1 条 → FAIL)
        mutant_seg = seg.replace(
            'side_effect=ValueError("vl-boom")',
            'side_effect=RuntimeError("vl-boom-as-rt")',
        )
        check("V3: mutant 段内有替换",
              mutant_seg != seg)
        mutant_src = before + mutant_seg + after
        mutant_path = TARGET + ".mutant_027b.tmp"
        with open(mutant_path, "w", encoding="utf-8") as f:
            f.write(mutant_src)
        try:
            env = dict(os.environ)
            env.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)
            proc = subprocess.run(
                [sys.executable, mutant_path],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            check("V3: mutant verify rc != 0 (反证)",
                  proc.returncode != 0,
                  f"rc={proc.returncode}, stdout_tail={proc.stdout[-400:]!r}")
            # 反证更精准: stdout 应含 V_exc_type_discrim FAIL
            check("V3: mutant stdout 含 V_exc_type_discrim FAIL 痕迹",
                  "FAIL" in proc.stdout and "V_exc_type_discrim" in proc.stdout,
                  f"stdout_tail={proc.stdout[-400:]!r}")
        finally:
            try:
                os.unlink(mutant_path)
            except OSError:
                pass
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4 sha256 锁 — verify_robot_016.py 当前 working-tree sha256
# =======================================================================
print("V4: sha256 锁 — verify_robot_016.py")
try:
    expected_sha = "037fd24670b8396fc8e2c32b01332933d8e26c64c959630eb99e90b0b883199a"
    actual_sha = hashlib.sha256(SRC.encode("utf-8")).hexdigest()
    check(f"V4: sha256 == {expected_sha}",
          actual_sha == expected_sha,
          f"actual={actual_sha}")
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5 端到端 — subprocess invoke verify_robot_016.py rc==0
#   且 stdout 含 V_exc_type_discrim 段 PASS 标记
# =======================================================================
print("V5: 端到端 — verify_robot_016.py rc==0 且 V_exc_type_discrim PASS")
try:
    env = dict(os.environ)
    env.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)
    proc = subprocess.run(
        [sys.executable, TARGET],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    check("V5: rc == 0",
          proc.returncode == 0,
          f"rc={proc.returncode}, stderr_tail={proc.stderr[-300:]!r}")
    check("V5: stdout 含 V_exc_type_discrim 段头",
          "V_exc_type_discrim:" in proc.stdout)
    check("V5: stdout 含 V_exc_type_discrim WARNING == 2 PASS",
          re.search(r"\[PASS\].*V_exc_type_discrim.*WARNING == 2", proc.stdout) is not None,
          f"stdout_tail={proc.stdout[-400:]!r}")
    check("V5: stdout 含 ALL PASS",
          "ALL PASS" in proc.stdout)
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
