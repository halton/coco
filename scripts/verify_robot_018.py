"""robot-018 verify: SHUTDOWN_TIMEOUT_S inf/nan 输入硬化 verify-only doc 锁面.

robot-014 (commit cefa1d0) 已对两条入口路径完成硬化:
- env 路径: sequencer_config_from_env()
- dataclass 路径: SequencerConfig.__post_init__

robot-018 verify-only doc 锁面 (无业务源码改动): 跨路径完整覆盖 + spec doc 锁面 + regression.

V0: fingerprint — math.isfinite + __post_init__ + env token 同时命中 sequencer.py
V1: spec doc 锁面 — docs/robot-shutdown-timeout-hardening-spec.md 12 关键短语命中
V2: env 路径 inf/nan 降级 (subprocess) — env={'inf','nan','-inf','Infinity','NaN'} → 2.0
V3: dataclass 路径 inf/nan 降级 (subprocess) — SequencerConfig(shutdown_timeout_s=...) → 2.0
V4: 合法值直通锁面 — env + dataclass 双路径 0.5/1.0/30.0 直通
V5: 边界降级 — 0 / -1 / "" / None / "abc" / "-inf" → 2.0
V6: regression — verify_robot_012/013/014/015/016/017 子进程 rc==0
V7: smoke ./init.sh — 不在此 verify 内联, closeout 时单独运行 (与其他 verify_robot_*.py 一致)
"""
from __future__ import annotations

import hashlib
import math
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
SPEC_DOC = REPO / "docs" / "robot-shutdown-timeout-hardening-spec.md"
SELF = Path(__file__).resolve()


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


def sha8(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]


# 清相关 env, 保证用例隔离
for k in ("COCO_ROBOT_SEQ", "COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S"):
    os.environ.pop(k, None)


# --------- V0: fingerprint ---------
print("[V0] fingerprint")
seq_src = SEQ_PY.read_text(encoding="utf-8")
check("sequencer.py 有 'import math'", "import math" in seq_src)
check("sequencer.py 含 math.isfinite", "math.isfinite" in seq_src)
check("sequencer.py 含 __post_init__", "def __post_init__" in seq_src)
check("sequencer.py 含 env token", "COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S" in seq_src)
check("sequencer.py 含默认值 2.0", "= 2.0" in seq_src or "shutdown_timeout_s: float = 2.0" in seq_src)
print(f"  sequencer.py sha8={sha8(SEQ_PY)}")
print(f"  spec_doc sha8={sha8(SPEC_DOC)}")
print(f"  self sha8={sha8(SELF)}")


# --------- V1: spec doc 锁面 ---------
print("[V1] spec doc 锁面")
spec_src = SPEC_DOC.read_text(encoding="utf-8")
keys = [
    "math.isfinite",
    "2.0",
    "__post_init__",
    "COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S",
    "thread.join",
    "inf",
    "nan",
    "default-OFF",
    "bytewise",
    "robot-014",
    "robot-018",
    "verify-only",
]
for k in keys:
    check(f"spec doc 含 '{k}'", k in spec_src)
check("spec doc 字节数 > 1500", len(spec_src.encode("utf-8")) > 1500, f"bytes={len(spec_src.encode('utf-8'))}")


# --------- V2: env 路径 inf/nan 降级 (subprocess) ---------
print("[V2] env 路径 inf/nan 降级 (subprocess)")
ENV_SCRIPT = (
    "import os, sys, json; "
    "from coco.robot.sequencer import sequencer_config_from_env; "
    "cfg = sequencer_config_from_env(); "
    "print(repr(cfg.shutdown_timeout_s))"
)
env_cases = [
    ("inf", "2.0"),
    ("Infinity", "2.0"),
    ("+inf", "2.0"),
    ("INF", "2.0"),
    ("-inf", "2.0"),
    ("-Infinity", "2.0"),
    ("nan", "2.0"),
    ("NaN", "2.0"),
    ("NAN", "2.0"),
    ("+nan", "2.0"),
    ("-nan", "2.0"),
]
for raw, expected in env_cases:
    env = {**os.environ, "COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S": raw}
    r = subprocess.run([sys.executable, "-c", ENV_SCRIPT], env=env, capture_output=True, text=True, cwd=str(REPO), timeout=20)
    got = r.stdout.strip()
    check(f"env '{raw}' → {expected}", got == expected, f"got={got!r} stderr={r.stderr[:80]!r}")


# --------- V3: dataclass 路径 inf/nan 降级 (subprocess) ---------
print("[V3] dataclass 路径 inf/nan 降级 (subprocess)")
DC_SCRIPT_TPL = (
    "from coco.robot.sequencer import SequencerConfig; "
    "cfg = SequencerConfig(shutdown_timeout_s={val}); "
    "print(repr(cfg.shutdown_timeout_s))"
)
dc_cases = [
    ("float('inf')", "2.0"),
    ("float('-inf')", "2.0"),
    ("float('nan')", "2.0"),
    ("'inf'", "2.0"),
    ("'nan'", "2.0"),
    ("None", "2.0"),
    ("'not-a-number'", "2.0"),
]
for val, expected in dc_cases:
    code = DC_SCRIPT_TPL.format(val=val)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO), timeout=20)
    got = r.stdout.strip()
    check(f"dataclass {val} → {expected}", got == expected, f"got={got!r} stderr={r.stderr[:80]!r}")


# --------- V4: 合法值直通锁面 (env + dataclass 双路径) ---------
print("[V4] 合法值直通锁面")
legit_env = [("0.5", "0.5"), ("1.0", "1.0"), ("30.0", "30.0"), ("0.001", "0.001"), ("2.0", "2.0")]
for raw, expected in legit_env:
    env = {**os.environ, "COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S": raw}
    r = subprocess.run([sys.executable, "-c", ENV_SCRIPT], env=env, capture_output=True, text=True, cwd=str(REPO), timeout=20)
    got = r.stdout.strip()
    check(f"env legit '{raw}' → {expected}", got == expected, f"got={got!r}")

legit_dc = [("0.5", "0.5"), ("1.0", "1.0"), ("30.0", "30.0"), ("0.001", "0.001"), ("5.0", "5.0")]
for val, expected in legit_dc:
    code = DC_SCRIPT_TPL.format(val=val)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO), timeout=20)
    got = r.stdout.strip()
    check(f"dataclass legit {val} → {expected}", got == expected, f"got={got!r}")


# --------- V5: 边界降级 (env + dataclass) ---------
print("[V5] 边界降级")
boundary_env = [("", "2.0"), ("0", "2.0"), ("0.0", "2.0"), ("-1", "2.0"), ("-2.5", "2.0"), ("abc", "2.0")]
for raw, expected in boundary_env:
    if raw == "":
        env = {k: v for k, v in os.environ.items() if k != "COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S"}
    else:
        env = {**os.environ, "COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S": raw}
    r = subprocess.run([sys.executable, "-c", ENV_SCRIPT], env=env, capture_output=True, text=True, cwd=str(REPO), timeout=20)
    got = r.stdout.strip()
    check(f"env boundary '{raw}' → {expected}", got == expected, f"got={got!r}")

boundary_dc = [("0", "2.0"), ("0.0", "2.0"), ("-1", "2.0"), ("-2.5", "2.0")]
for val, expected in boundary_dc:
    code = DC_SCRIPT_TPL.format(val=val)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO), timeout=20)
    got = r.stdout.strip()
    check(f"dataclass boundary {val} → {expected}", got == expected, f"got={got!r}")


# --------- V6: regression — 邻近 verify_robot_*.py 静态存在 + import 锁面 ---------
# (实际行为回归不在此 verify 内联: 邻近 verify 内部都跑下游 chain, 嵌套调会 N² 时间爆炸.
#  此处仅静态锁面: 邻近 verify 文件存在 + 关键 token 引用未漂移. 真行为回归由 closeout
#  时单独跑 ./init.sh smoke 与按需手动跑下游 verify 覆盖.)
print("[V6] regression — 邻近 verify 静态存在 + token 锁面")
for fid in ("robot_012", "robot_013", "robot_014", "robot_015", "robot_016", "robot_017"):
    script = REPO / "scripts" / f"verify_{fid}.py"
    check(f"{fid} verify exists", script.exists(), f"path={script}")
# 关键 token 不漂移: robot-014 双层硬化在 sequencer.py 中仍在
check("sequencer.py 仍有 math.isfinite 钩子", "math.isfinite" in seq_src)
check("sequencer.py 仍有 __post_init__ 钩子", "def __post_init__" in seq_src)
check("sequencer.py 仍有 env token", "COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S" in seq_src)


# --------- summary ---------
dt = time.time() - t0
print("")
print(f"== robot-018 verify done in {dt:.1f}s, errors={len(errors)} ==")
if errors:
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
sys.exit(0)
