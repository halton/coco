"""robot-014 verify: SequencerConfig.shutdown_timeout_s 输入硬化 — 拒绝 inf/nan.

来源: robot-012-backlog-shutdown-timeout-inf-nan-hardening
背景: robot-012 中 COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S 输入降级只 try/except ValueError
      兜底负值, 未拦 float('inf') / float('nan'); inf 会让 thread.join() 永久阻塞,
      nan 比较未定义。本 feature 加 math.isfinite() 检查 + 非有限值降级到默认 2.0。

V0: fingerprint — math.isfinite + token COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S + 默认 2.0
V1: 合法 env 等价 — 0.5/1.0/2.0/30.0 直通; 'abc'/''/'-1'/'0' → 2.0 (与 main 等价)
V2: inf 拒绝 — env='inf' / 'Infinity' → 2.0
V3: nan 拒绝 — env='nan' / 'NaN' → 2.0
V4: dataclass 直接构造路径 — SequencerConfig(shutdown_timeout_s=float('inf')/nan/-inf/0/-1) → 2.0
V5: regression — verify_robot_008..013 子进程 rc==0
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
import time
from typing import List

errors: List[str] = []
t0 = time.time()


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


# 清相关 env, 保证用例隔离
for k in (
    "COCO_ROBOT_SEQ",
    "COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S",
):
    os.environ.pop(k, None)


# --------- V0: fingerprint ---------
print("[V0] fingerprint")
try:
    from coco.robot.sequencer import (
        SequencerConfig,
        sequencer_config_from_env,
    )
    import coco.robot.sequencer as _seq_mod

    cfg = SequencerConfig()
    check(
        "SequencerConfig.shutdown_timeout_s default 2.0",
        cfg.shutdown_timeout_s == 2.0,
        f"got={cfg.shutdown_timeout_s}",
    )
    src = open(_seq_mod.__file__, encoding="utf-8").read()
    check(
        "import math present in sequencer.py",
        "\nimport math" in src or "import math\n" in src,
    )
    check(
        "math.isfinite() guard present",
        "math.isfinite" in src,
    )
    check(
        "token COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S in source",
        "COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S" in src,
    )
    check(
        "robot-014 marker in source",
        "robot-014" in src,
    )
except Exception as e:
    check("import sequencer", False, repr(e))


# --------- V1: 合法 env 等价 + 既有降级行为 ---------
print("[V1] legal values pass-through + existing fallback")
legal_cases = [
    ("0.5", 0.5),
    ("1.0", 1.0),
    ("2.0", 2.0),
    ("30.0", 30.0),
    ("0.001", 0.001),
]
fallback_cases = [
    ("abc", 2.0),    # ValueError → 2.0
    ("", 2.0),       # empty → 2.0
    ("-1", 2.0),     # <=0 → 2.0
    ("-2.5", 2.0),
    ("0", 2.0),      # <=0 → 2.0
    ("0.0", 2.0),
]
for env_val, expected in legal_cases + fallback_cases:
    cfg = sequencer_config_from_env(env={"COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S": env_val})
    check(
        f"env={env_val!r} → {expected}",
        cfg.shutdown_timeout_s == expected,
        f"got={cfg.shutdown_timeout_s}",
    )


# --------- V2: inf 拒绝 ---------
print("[V2] inf rejection")
for env_val in ("inf", "Infinity", "+inf", "INF", "-inf", "-Infinity"):
    cfg = sequencer_config_from_env(env={"COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S": env_val})
    check(
        f"env={env_val!r} → 2.0 (not inf)",
        cfg.shutdown_timeout_s == 2.0 and math.isfinite(cfg.shutdown_timeout_s),
        f"got={cfg.shutdown_timeout_s}",
    )


# --------- V3: nan 拒绝 ---------
print("[V3] nan rejection")
for env_val in ("nan", "NaN", "NAN", "+nan", "-nan"):
    cfg = sequencer_config_from_env(env={"COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S": env_val})
    check(
        f"env={env_val!r} → 2.0 (not nan)",
        cfg.shutdown_timeout_s == 2.0 and math.isfinite(cfg.shutdown_timeout_s),
        f"got={cfg.shutdown_timeout_s}",
    )


# --------- V4: dataclass 直接构造路径 ---------
print("[V4] direct SequencerConfig(...) construction hardening")
direct_cases = [
    (float("inf"), 2.0),
    (float("-inf"), 2.0),
    (float("nan"), 2.0),
    (0, 2.0),
    (-1.0, 2.0),
    (0.0, 2.0),
    # 合法值原样
    (0.5, 0.5),
    (1.0, 1.0),
    (5.0, 5.0),
]
for input_v, expected in direct_cases:
    cfg = SequencerConfig(shutdown_timeout_s=input_v)
    check(
        f"SequencerConfig(shutdown_timeout_s={input_v!r}) → {expected}",
        cfg.shutdown_timeout_s == expected and math.isfinite(cfg.shutdown_timeout_s),
        f"got={cfg.shutdown_timeout_s}",
    )

# 非数值类型 (str/None) — TypeError 也应被兜住
for bad in ("not-a-number", None):
    try:
        cfg = SequencerConfig(shutdown_timeout_s=bad)  # type: ignore[arg-type]
        check(
            f"SequencerConfig(shutdown_timeout_s={bad!r}) → 2.0 fallback",
            cfg.shutdown_timeout_s == 2.0,
            f"got={cfg.shutdown_timeout_s}",
        )
    except Exception as e:
        check(
            f"SequencerConfig(shutdown_timeout_s={bad!r}) no-raise",
            False,
            repr(e),
        )


# --------- V5: regression ---------
print("[V5] regression — verify_robot_008..013 rc==0")
here = os.path.dirname(os.path.abspath(__file__))
for fid in ("008", "009", "010", "011", "012", "013"):
    script = os.path.join(here, f"verify_robot_{fid}.py")
    if not os.path.exists(script):
        check(f"verify_robot_{fid}.py exists", False, "missing")
        continue
    try:
        r = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True, timeout=600,
        )
        check(
            f"verify_robot_{fid}.py rc==0",
            r.returncode == 0,
            f"rc={r.returncode} stderr_tail={r.stderr[-200:] if r.stderr else ''}",
        )
    except subprocess.TimeoutExpired:
        check(f"verify_robot_{fid}.py timeout<600s", False, "TIMEOUT")


# --------- summary ---------
dt = time.time() - t0
print(f"\n[robot-014] elapsed={dt:.2f}s errors={len(errors)}")
if errors:
    print("FAIL: " + "; ".join(errors))
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
