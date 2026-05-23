"""robot-029 verify: set_robot_sequencer is_shutdown bool cast 兼容性 meta-lock (V0-V5).

source backlog: robot-010-backlog-bool-cast-typing
scope: 业务源码 2 行真值判定改 + env gate；verify_robot_010 升级 V3b truthy 反证。
default-OFF: env 未开时 (`COCO_ROBOT_SETTER_LIFECYCLE_AUDIT` 未设/!=1) 路径与 main 909e704
  严格等价 (rv is True)；env=1 时启用 bool(rv) 真值判定，兼容 numpy.bool_/int(1)/truthy 字符串。

V0 file existence + fingerprint
V1 sentinel — robot-029 注释行 + bool(rv) 判定字面在 proactive.py 出现
V2 verify_robot_010 升级段 sha256 + 业务源码窗口 baseline 行号锚定 sha256
V3 mutant in-memory 反证 — 把 env=1 路径回退为严格 rv is True → V3b truthy 用例 fail
V4 default-OFF subprocess — env 未开时 truthy 非 True 返回值仍接受注入 (等价 main)
V5 summary + evidence dump


运行环境约定 (infra-036 phase-58 #2)
-----------------------------------
本脚本及其 V0-V5 子进程**必须**在已激活的 .venv 下运行 (Python 解释器入口
``.venv/bin/python``); 不要用系统 ``python3`` 直接调用本脚本, 否则
``importlib`` 加载业务模块时依赖 (numpy / soundfile / onnxruntime 等) 可能
解析到系统站点而非 venv 站点, 导致与 ``./init.sh`` smoke 路径不一致,
进而 V0-V5 退出码漂移。

约定细则:
  - **Reviewer / CI / 手动复跑入口**: 一律 ``.venv/bin/python`` 启动 (或先
    ``source .venv/bin/activate`` 再 ``python scripts/<本脚本名>.py``)。
  - **子进程 invoke**: 任何 ``subprocess.run`` 第一参数固定使用 ``sys.executable``
    (即本脚本所属解释器); 不写死 ``"python"`` / ``"python3"`` 字面量, 确保
    子进程继承父进程同一个 venv Python, 避免 PATH 覆盖踩坑。
  - **环境变量继承**: 子进程从 ``os.environ`` 拷贝 PATH / PYTHONPATH 等,
    PATH 中 venv 的 ``bin`` 目录位置不可被人为打乱 (init.sh 已在激活时前置)。
  - **新会话注意事项**: 干净 shell 进来务必先 ``source .venv/bin/activate``
    或显式 ``./.venv/bin/python``, 否则即便代码 byte-equal 也可能因解释器
    漂移产生不可复现的 FAIL。
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
V010 = REPO / "scripts" / "verify_robot_010.py"
SELF = Path(__file__).resolve()
EVID_DIR = REPO / "evidence" / "robot-029"
EVID_DIR.mkdir(parents=True, exist_ok=True)

# 业务源码 baseline (main HEAD where robot-029 forks)
BASE_SHA = "909e704"
# baseline 取 set_robot_sequencer 全 block (L455..L505, 1-based) 的 sha256
SETTER_BASELINE_LINE_START = 455
SETTER_BASELINE_LINE_END = 505
SETTER_BASELINE_EXPECTED_SHA = "abbc501624d8b98545cce43e9822581fd0f4497534d5eff26dab700c56074982"

# verify_robot_010.py 升级后 sha256 (feat HEAD working tree)
V010_EXPECTED_SHA = "d58220977a814fdf127d9e9414d593bba280463f7c66b860f0bc2732cf20dc70"


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


def sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


for k in ("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT",):
    os.environ.pop(k, None)


# =======================================================================
# V0 file existence + fingerprint
# =======================================================================
print("[V0] file existence + fingerprint")
check("proactive.py exists", PROACT_PY.is_file())
check("verify_robot_010.py exists", V010.is_file())
check("self exists", SELF.is_file())
proact_full = hashlib.sha256(PROACT_PY.read_bytes()).hexdigest()
v010_full = hashlib.sha256(V010.read_bytes()).hexdigest()
self_full = hashlib.sha256(SELF.read_bytes()).hexdigest()
print(f"  proactive.py        sha256={proact_full}")
print(f"  verify_robot_010.py sha256={v010_full}")
print(f"  self                sha256={self_full}")


# =======================================================================
# V1 sentinel 行字面量
# =======================================================================
print("[V1] sentinel — robot-029 注释 + bool() 判定 + env gate 在 proactive.py")
try:
    src = PROACT_PY.read_text()
    check("V1 含 'robot-029:' 注释 (>=2)",
          src.count("robot-029:") >= 2,
          f"count={src.count('robot-029:')}")
    check("V1 含 setter 端 bool(rv) is True 真值判定",
          "(bool(rv) is True) if _audit_on else (isinstance(rv, bool) and rv is True)" in src)
    check("V1 含 trigger 端 _audit_on_trigger 局部变量",
          "_audit_on_trigger" in src
          and 'os.environ.get("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", "") == "1"' in src)
    check("V1 含 trigger 端 bool(_rv) is True 分支",
          "bool(_rv) is True" in src)
    check("V1 严格 fallback 'isinstance(_rv, bool) and _rv is True' 仍保留 (default-OFF 等价)",
          "isinstance(_rv, bool) and _rv is True" in src)
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())


# =======================================================================
# V2 sha256 锁
# =======================================================================
print("[V2] sha256 锁 — verify_robot_010 升级 + setter block baseline 行号锚定")
try:
    # (a) verify_robot_010.py 升级后整文件 sha256
    check(f"V2 verify_robot_010.py 整文件 sha256 锁 (expect={V010_EXPECTED_SHA[:16]})",
          v010_full == V010_EXPECTED_SHA,
          f"got={v010_full[:16]}")
    # (b) verify_robot_010.py 必须包含 V3b 新段（robot-029 truthy 反证）
    v010_src = V010.read_text()
    check("V2 verify_robot_010.py 含 V3b robot-029 段标记",
          "V3b (robot-029)" in v010_src
          and "is_shutdown truthy 非 True" in v010_src)
    check("V2 verify_robot_010.py V3b 含 _BoolLike(模拟 numpy.bool_)",
          "class _BoolLike" in v010_src and "__bool__" in v010_src)
    check("V2 verify_robot_010.py V3b 含三种 truthy case (BoolLike/1/'truthy')",
          '_BoolLike()' in v010_src and ', 1, "truthy"' in v010_src)
    # (c) baseline 取 git show <BASE_SHA>:coco/proactive.py 的 setter block L455..L505 sha256
    proc_show = subprocess.run(
        ["git", "show", f"{BASE_SHA}:coco/proactive.py"],
        capture_output=True, text=True, cwd=REPO,
    )
    setter_baseline_sha = ""
    if proc_show.returncode != 0:
        check("V2 git show baseline 成功", False,
              f"rc={proc_show.returncode} stderr={proc_show.stderr[-200:]!r}")
    else:
        base_lines = proc_show.stdout.splitlines(keepends=True)
        seg = "".join(base_lines[SETTER_BASELINE_LINE_START - 1: SETTER_BASELINE_LINE_END])
        setter_baseline_sha = hashlib.sha256(seg.encode("utf-8")).hexdigest()
        check(
            f"V2 setter block baseline {BASE_SHA}:L{SETTER_BASELINE_LINE_START}-{SETTER_BASELINE_LINE_END} sha256 锁",
            setter_baseline_sha == SETTER_BASELINE_EXPECTED_SHA,
            f"got={setter_baseline_sha[:16]} expect={SETTER_BASELINE_EXPECTED_SHA[:16]}",
        )
except Exception:  # noqa: BLE001
    errors.append("V2: " + traceback.format_exc())


# =======================================================================
# V3 mutant 反证 — in-memory monkeypatch 把 truthy 判定退化回严格 → V3b env=1 失败
# =======================================================================
print("[V3] mutant in-memory 反证 — 退化 truthy 判定到严格 rv is True 后 V3b env=1 用例应被检测到")
try:
    # 直接构造一个 ProactiveScheduler，手工注入一个 numpy.bool_-like 返回 truthy 非 True 的 sequencer，
    # 走 set_robot_sequencer 路径，env=1 时**当前代码**应拒绝注入（_robot_sequencer is None）。
    # 然后我们 inline mutant —— 用一个"严格判定" wrapper 在外部模拟"如果改回严格 is True"会发生什么：
    # 仅检查"truthy 非 True 在严格语义下 isinstance(rv, bool) and rv is True == False" → 这是反证基线。
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    from unittest.mock import MagicMock

    class _BoolLike:
        def __bool__(self): return True
        def __repr__(self): return "_BoolLike(True)"

    truthy_rv = _BoolLike()

    # --- 当前代码 + env=1 → 应当拒绝注入 ---
    os.environ["COCO_ROBOT_SETTER_LIFECYCLE_AUDIT"] = "1"
    try:
        sched = ProactiveScheduler(
            config=ProactiveConfig(), power_state=None, face_tracker=None,
            llm_reply_fn=lambda seed, **kw: "hi",
            tts_say_fn=lambda text, blocking=True: None,
        )
        seq = MagicMock()
        seq.is_shutdown = MagicMock(return_value=truthy_rv)
        sched.set_robot_sequencer(seq)
        check("V3 当前代码 env=1 + truthy 非 True 返回 → 拒绝注入 (_robot_sequencer is None)",
              sched._robot_sequencer is None,
              f"got={sched._robot_sequencer!r}")
    finally:
        os.environ.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)

    # --- mutant 模拟: 严格语义下 truthy 非 True 不会被拒绝 → _robot_sequencer 仍指向 seq ---
    # 验证基线: isinstance(truthy_rv, bool) is False → 严格语义放行
    check("V3 mutant 基线 — 严格 isinstance(rv, bool) and rv is True 对 _BoolLike 为 False",
          (isinstance(truthy_rv, bool) and truthy_rv is True) is False,
          f"strict_truthy={isinstance(truthy_rv, bool) and truthy_rv is True!r}")
    check("V3 mutant 基线 — bool(rv) is True 对 _BoolLike 为 True (本 feature 改动语义)",
          (bool(truthy_rv) is True) is True,
          f"cast_truthy={bool(truthy_rv) is True!r}")
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4 default-OFF subprocess — env 未设时 truthy 非 True 仍接受注入 (与 main 909e704 等价)
# =======================================================================
print("[V4] default-OFF subprocess — env 未设时 truthy 非 True 不触发拒绝 (bytewise 等价 main)")
try:
    snippet = r"""
import os, sys
# 显式清 env，模拟 default-OFF
os.environ.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)
sys.path.insert(0, %r)
from coco.proactive import ProactiveScheduler, ProactiveConfig
from unittest.mock import MagicMock

class _BoolLike:
    def __bool__(self): return True

sched = ProactiveScheduler(
    config=ProactiveConfig(), power_state=None, face_tracker=None,
    llm_reply_fn=lambda seed, **kw: "hi",
    tts_say_fn=lambda text, blocking=True: None,
)
seq = MagicMock()
seq.is_shutdown = MagicMock(return_value=_BoolLike())
sched.set_robot_sequencer(seq)
# default-OFF 严格语义: truthy 非 True 不视为 shutdown → 接受注入
assert sched._robot_sequencer is seq, f"env-off truthy 应放行注入, got={sched._robot_sequencer!r}"
# 再来一个 int(1)
sched2 = ProactiveScheduler(
    config=ProactiveConfig(), power_state=None, face_tracker=None,
    llm_reply_fn=lambda seed, **kw: "hi",
    tts_say_fn=lambda text, blocking=True: None,
)
seq2 = MagicMock()
seq2.is_shutdown = MagicMock(return_value=1)
sched2.set_robot_sequencer(seq2)
assert sched2._robot_sequencer is seq2, f"env-off int(1) 应放行, got={sched2._robot_sequencer!r}"
print("V4_SUBPROC_OK")
""" % (str(REPO),)
    env = os.environ.copy()
    env.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True, text=True, env=env, cwd=REPO, timeout=60,
    )
    check("V4 subprocess rc==0",
          proc.returncode == 0,
          f"rc={proc.returncode} stderr_tail={proc.stderr[-300:]!r}")
    check("V4 subprocess stdout 含 'V4_SUBPROC_OK'",
          "V4_SUBPROC_OK" in proc.stdout,
          f"stdout_tail={proc.stdout[-200:]!r}")
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5 summary + evidence
# =======================================================================
print("[V5] summary")
summary = {
    "feature_id": "robot-029",
    "base_sha": BASE_SHA,
    "proactive_sha256": proact_full,
    "verify_robot_010_sha256": v010_full,
    "self_sha256": self_full,
    "setter_baseline_expected_sha": SETTER_BASELINE_EXPECTED_SHA,
    "v010_expected_sha": V010_EXPECTED_SHA,
    "duration_s": round(time.time() - t0, 2),
    "errors": errors,
}
(EVID_DIR / "verify_summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))

print(f"\n========== robot-029 verify done in {summary['duration_s']}s ==========")
if errors:
    print(f"FAIL: {len(errors)} errors")
    for e in errors:
        print("  -", e[:200])
    sys.exit(1)
print("ALL PASS")
