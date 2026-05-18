"""robot-024 verify: verify_robot_016 env-string mutant-lock 反证补全 (verify-only).

source: robot-016-backlog-env-string-mutant-lock (Reviewer nit-1).

目标: 锁住 COCO_ROBOT_SETTER_LIFECYCLE_AUDIT gate 是**严格字符串 '1'** 单值,
非 lower-truthy 集合; 任何 'true'/'yes'/'on'/'True'/' 1 '/'1\\n' 等真值字符串
**均不命中** gate (= 走 default-OFF bytewise 分支)。防 future false-positive
扩展 (例如有人随手改成 lower() in {"1","true","yes","on"} 形态)。

实际 gate 实现 (coco/proactive.py:455):
    _audit_on = os.environ.get("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", "") == "1"

V0 file existence: scripts/verify_robot_024.py & scripts/verify_robot_016.py & coco/proactive.py
V1 gate 严格 '1' 反证: 列表中真值字符串 env 注入后, ProactiveScheduler.set_robot_sequencer
   行为应与 unset (default-OFF bytewise) 一致 — toggle A→B→A→B 应产 3 条 dup WARNING
   (无 DEBUG suppressed), _setter_audit_seen 仍为空 set。
V2 gate 仅 '1' 命中正例: env="1" 时 toggle A→B→A→B 第二次 A→B 应 DEBUG suppressed,
   _setter_audit_seen 含 2 个 dup key (确认 ON 分支真的在用)。
V3 subprocess 跑 verify_robot_016 rc=0 (保 V0-V5 原 verify 未回归)。
V4 in-memory mutant 反证: 模拟把 gate 实现改成 lower-truthy-set, 喂 env='true'
   应跳进 ON 分支 (DEBUG suppressed 出现), 与现网 gate 行为不同 — 锁住"严格 =='1'"
   不能被 lower-truthy 扩展替代 (否则 mutant 与 prod 行为一致, lock 失败)。
V5 summary


运行环境约定 (infra-036)
------------------------
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

import io
import logging
import os
import subprocess
import sys
import time
import traceback
from typing import List
from unittest.mock import MagicMock

errors: List[str] = []
t0 = time.time()
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


# 清相关 env, 保证各 case 隔离 (复用 verify_robot_016 同一组)
for k in (
    "COCO_ROBOT_SEQ",
    "COCO_ROBOT_SEQ_POLL_S",
    "COCO_ROBOT_SEQ_SUB_ASYNC",
    "COCO_ROBOT_SEQ_POOL_SIZE",
    "COCO_ROBOT_SEQ_QUEUE_MAX",
    "COCO_ROBOT_SEQ_OVERFLOW",
    "COCO_PROACTIVE",
    "COCO_ROBOT_SETTER_LIFECYCLE_AUDIT",
):
    os.environ.pop(k, None)


def _attach_log_capture():
    from coco import proactive as _mod
    lg = _mod.log
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setLevel(logging.DEBUG)
    h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    return lg, h, buf


def _detach_log_capture(lg, h) -> None:
    try:
        lg.removeHandler(h)
    except Exception:  # noqa: BLE001
        pass


def _count_lines(text: str, level: str, needle: str) -> int:
    n = 0
    for line in text.splitlines():
        if line.startswith(level + " ") and needle in line:
            n += 1
    return n


def _new_sched():
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    return ProactiveScheduler(
        config=ProactiveConfig(),
        power_state=None,
        face_tracker=None,
        llm_reply_fn=lambda seed, **kw: "hi",
        tts_say_fn=lambda text, blocking=True: None,
    )


def _normal_seq():
    seq = MagicMock()
    seq.is_shutdown = MagicMock(return_value=False)
    seq.enqueue = MagicMock(return_value=True)
    return seq


def _toggle_4(sched, seq_a, seq_b):
    """注入 A→B→A→B; 返回 (warn_count, debug_count, dup_keys_in_seen)."""
    lg, h, buf = _attach_log_capture()
    try:
        sched.set_robot_sequencer(seq_a)
        sched.set_robot_sequencer(seq_b)
        sched.set_robot_sequencer(seq_a)
        sched.set_robot_sequencer(seq_b)
        log_text = buf.getvalue()
        warn = _count_lines(log_text, "WARNING", "overwriting existing sequencer")
        debug = _count_lines(log_text, "DEBUG", "suppressed warn-once")
        dups = [k for k in sched._setter_audit_seen if k[0] == "dup"]
        return warn, debug, dups, log_text
    finally:
        _detach_log_capture(lg, h)


# =======================================================================
# V0 file existence
# =======================================================================
print("V0: file existence — verify_robot_024 / verify_robot_016 / coco/proactive.py")
for rel in (
    "scripts/verify_robot_024.py",
    "scripts/verify_robot_016.py",
    "coco/proactive.py",
):
    p = os.path.join(REPO_ROOT, rel)
    check(f"V0 {rel} exists", os.path.exists(p), f"path={p}")

# V0b: 锁源码层 gate 字面量 `== "1"` (防 prod 偷偷换成 lower-truthy)
gate_line_ok = False
try:
    src = open(os.path.join(REPO_ROOT, "coco/proactive.py"), encoding="utf-8").read()
    needle = 'os.environ.get("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", "") == "1"'
    gate_line_ok = needle in src
    check("V0b coco/proactive.py 含严格 gate 字面量 == \"1\"",
          gate_line_ok,
          "missing: " + needle if not gate_line_ok else "")
except Exception:  # noqa: BLE001
    errors.append("V0b: " + traceback.format_exc())


# =======================================================================
# V1 gate 严格 '1' 反证: 真值字符串均不命中 (走 OFF bytewise 分支)
# =======================================================================
print("V1: env in {truthy 字符串变体} 均不命中 gate — 应等价 default-OFF bytewise")
TRUTHY_BUT_NOT_ONE = [
    "true", "True", "TRUE",
    "yes", "Yes", "YES",
    "on", "On", "ON",
    "y", "Y",
    "1 ",        # 尾空格
    " 1",        # 头空格
    " 1 ",       # 两端空格
    "1\n",       # 换行
    "01",        # 前导 0
    "2",         # 其他数字
    "",          # 空串 (也是 default)
    "0",         # 显式 0
    "false",     # 反真值不应误命中 (sanity)
]
for v in TRUTHY_BUT_NOT_ONE:
    try:
        os.environ["COCO_ROBOT_SETTER_LIFECYCLE_AUDIT"] = v
        sched = _new_sched()
        seq_a = _normal_seq()
        seq_b = _normal_seq()
        warn, debug, dups, _log = _toggle_4(sched, seq_a, seq_b)
        # OFF bytewise: 3 dup 切换 → 3 WARNING, 0 DEBUG suppressed, audit_seen 仍空
        ok_warn = (warn == 3)
        ok_debug = (debug == 0)
        ok_seen = (sched._setter_audit_seen == set())
        check(
            f"V1 env={v!r} 不命中 gate (warn=3/debug=0/seen=empty)",
            ok_warn and ok_debug and ok_seen,
            f"warn={warn}, debug={debug}, seen={sched._setter_audit_seen!r}",
        )
    except Exception:  # noqa: BLE001
        errors.append(f"V1 env={v!r}: " + traceback.format_exc())
    finally:
        os.environ.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)


# =======================================================================
# V2 gate 仅 '1' 命中正例 (sanity, 锁 ON 分支真的在用)
# =======================================================================
print("V2: env='1' 命中 gate — toggle 应触发 warn-once suppressed")
try:
    os.environ["COCO_ROBOT_SETTER_LIFECYCLE_AUDIT"] = "1"
    sched = _new_sched()
    seq_a = _normal_seq()
    seq_b = _normal_seq()
    warn, debug, dups, log_text = _toggle_4(sched, seq_a, seq_b)
    # ON 分支: A→B (warn) → B→A (warn 新 key) → A→B (DEBUG suppressed)
    check("V2 ON env='1': warn == 2", warn == 2, f"got={warn}, log={log_text!r}")
    check("V2 ON env='1': debug suppressed >= 1", debug >= 1, f"got={debug}")
    check("V2 ON env='1': dup_keys in _setter_audit_seen == 2", len(dups) == 2, f"dups={dups!r}")
except Exception:  # noqa: BLE001
    errors.append("V2: " + traceback.format_exc())
finally:
    os.environ.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)


# =======================================================================
# V3 subprocess: verify_robot_016 rc==0 (无回归)
# =======================================================================
print("V3: subprocess verify_robot_016 rc==0")
try:
    path = os.path.join(REPO_ROOT, "scripts", "verify_robot_016.py")
    env = {k: v for k, v in os.environ.items()
           if k != "COCO_ROBOT_SETTER_LIFECYCLE_AUDIT"}
    env.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)
    proc = subprocess.run(
        [sys.executable, path],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    check("V3 verify_robot_016 rc==0",
          proc.returncode == 0,
          f"rc={proc.returncode}, stderr_tail={proc.stderr[-300:]!r}")
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4 in-memory mutant 反证: 把 gate 实现替换成 lower-truthy-set, 喂 env='true'
#   应走进 ON 分支 (出现 DEBUG suppressed); 这证明 prod 严格 == "1" 与 lower-truthy
#   行为可区分, 锁面有效。若 mutant 与 prod 行为一致, lock 失败。
# =======================================================================
print("V4 mutant 反证: lower-truthy-set 实现喂 env='true' 应走 ON 分支 (与 prod 严格 '1' 区分)")
try:
    import importlib
    from coco import proactive as _mod
    importlib.reload(_mod)  # 干净环境

    # 备份原 set_robot_sequencer
    orig_setter = _mod.ProactiveScheduler.set_robot_sequencer
    orig_src_line = 'os.environ.get("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", "") == "1"'

    # 用 monkey-patch 注入 mutant: gate 改为 lower-truthy-set
    TRUTHY = {"1", "true", "yes", "on"}

    def mutant_setter(self, sequencer):
        # 复制原函数体逻辑, 但 gate 用 lower-truthy-set
        _audit_on = os.environ.get("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", "").strip().lower() in TRUTHY
        # is_shutdown 探针 (复制原 fail-soft 行为)
        if sequencer is not None:
            try:
                is_fn = getattr(sequencer, "is_shutdown", None)
                if callable(is_fn):
                    rv = is_fn()
                    if rv is True:
                        _mod.log.warning(
                            "robot-010 set_robot_sequencer: refused — sequencer.is_shutdown()=True"
                        )
                        return
            except Exception as exc:  # noqa: BLE001
                key = ("probe-fail", id(sequencer), type(exc).__name__)
                if _audit_on and key in self._setter_audit_seen:
                    _mod.log.debug(
                        "robot-010 set_robot_sequencer: is_shutdown probe failed (suppressed warn-once)"
                    )
                else:
                    _mod.log.warning(
                        "robot-010 set_robot_sequencer: is_shutdown probe failed exc=%s",
                        exc,
                    )
                    if _audit_on:
                        self._setter_audit_seen.add(key)
        # dup detection
        prev = getattr(self, "_robot_sequencer", None)
        if prev is not None and sequencer is not None and prev is not sequencer:
            key = ("dup", id(prev), id(sequencer))
            if _audit_on and key in self._setter_audit_seen:
                _mod.log.debug(
                    "robot-010 set_robot_sequencer: overwriting existing sequencer (suppressed warn-once)"
                )
            else:
                _mod.log.warning(
                    "robot-010 set_robot_sequencer: overwriting existing sequencer"
                )
                if _audit_on:
                    self._setter_audit_seen.add(key)
        self._robot_sequencer = sequencer

    _mod.ProactiveScheduler.set_robot_sequencer = mutant_setter
    try:
        # 喂 env='true' — prod 严格 '1' 不命中, mutant lower-truthy 命中
        os.environ["COCO_ROBOT_SETTER_LIFECYCLE_AUDIT"] = "true"
        sched = _new_sched()
        seq_a = _normal_seq()
        seq_b = _normal_seq()
        warn, debug, dups, log_text = _toggle_4(sched, seq_a, seq_b)
        # mutant 下 'true' 应走 ON 分支 (DEBUG suppressed 出现)
        mutant_on_branch = (debug >= 1 and warn == 2 and len(dups) == 2)
        check(
            "V4 mutant lower-truthy-set + env='true' → 走 ON 分支 (debug>=1 / warn==2)",
            mutant_on_branch,
            f"warn={warn}, debug={debug}, dups={dups!r}",
        )

        # 反过来确认 mutant 行为可区分于 prod: prod 同样 env='true' 行为应是 OFF
        # (V1 已覆盖 prod env='true' → warn=3/debug=0)。两组数据不同即锁面有效。
        check(
            "V4 mutant 与 prod env='true' 行为可区分 (mutant debug>=1 vs prod debug==0)",
            debug >= 1,
            f"mutant debug={debug} (prod debug 在 V1 已锁为 0)",
        )
    finally:
        os.environ.pop("COCO_ROBOT_SETTER_LIFECYCLE_AUDIT", None)
        _mod.ProactiveScheduler.set_robot_sequencer = orig_setter
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5 summary
# =======================================================================
elapsed = time.time() - t0
print(f"\nelapsed={elapsed:.2f}s")
if errors:
    print("FAIL:")
    for e in errors:
        print("  -", e)
    sys.exit(1)
print("V5 summary: gate 严格 '1' 单值锁面有效 (16 个非 '1' 字符串均反证 + mutant 区分)")
print("ALL PASS")
sys.exit(0)
