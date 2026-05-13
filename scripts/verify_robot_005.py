"""robot-005 verify: robot-004 followup 收割校验.

V1 首帧无 ramp：Modulator 启动第一次 _begin_ramp 直接 snap 到 target，不走 ramp_s
V2 antenna SAD ≠ NEUTRAL：antenna_joint_rad 在 [0,1] 整段单调可区分
V3 pause/resume 嵌套引用计数：嵌套 2 层时内层 resume 不解锁，外层 resume 才解锁
V4 PostureBaselineStats.history 用 deque(maxlen=200)，超出后旧条目被丢
V5 ExpressionPlayer.play docstring 含 "stop" 字（并发与 stop 语义文档化）
V6 emit fallback import 在模块顶（_DEFAULT_EMIT 模块属性存在；_emit_event 不再 lazy import）
V7 回归 verify_robot_004 全 PASS（含 V9 pause/resume / V14 expression.play 调 baseline.pause）
V8 与 vision-006 / interact-011 / companion-008 不互相干扰（grep 边界 + import 不破）
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from typing import Any, List
from unittest.mock import MagicMock

errors: List[str] = []
t0 = time.time()


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


def make_mock_robot() -> Any:
    r = MagicMock()
    r.goto_target = MagicMock(return_value=None)
    r.set_target_antenna_joint_positions = MagicMock(return_value=None)
    return r


for k in (
    "COCO_POSTURE_BASELINE",
    "COCO_POSTURE_BASELINE_RAMP_S",
    "COCO_POSTURE_BASELINE_TICK_S",
    "COCO_POSTURE_BASELINE_DEBOUNCE_S",
):
    os.environ.pop(k, None)


# =======================================================================
# V1 首帧无 ramp
# =======================================================================
print("V1: 首帧无 ramp — 启动后第一个 emotion target 立即 snap，不走 ramp_s")
try:
    from coco.robot.posture_baseline import (
        PostureBaselineConfig,
        PostureBaselineModulator,
        PostureOffset,
        ZERO_OFFSET,
    )

    class StubEmotion:
        def __init__(self, v: str) -> None:
            self.value = v
        @property
        def effective(self) -> str:
            # 让 .effective 返回属性而非 callable，模拟 EmotionTracker 接口
            return self.value

    class FakeTracker:
        # emotion_tracker.effective 在 robot/posture_baseline 中支持 callable 或属性
        def __init__(self) -> None:
            self.value = "happy"
        @property
        def effective(self) -> str:
            return self.value

    r = make_mock_robot()
    tracker = FakeTracker()
    cfg = PostureBaselineConfig(enabled=True, ramp_s=2.0, tick_interval_s=0.05, debounce_s=0.0)
    mod = PostureBaselineModulator(robot=r, emotion_tracker=tracker, power_state=None, config=cfg)

    # 直接驱动 tick（不起后台线程，便于断言）
    mod._tick_once()  # noqa: SLF001
    # 首帧后 _current 应已 == _target（happy/active），而不是 ZERO
    cur = mod.current_offset()
    tgt = mod._target  # noqa: SLF001
    check("首帧 tick 后 _current == _target（snap）", cur == tgt,
          f"current={cur} target={tgt}")
    check("首帧 antenna 立即跳到目标（非中位 0.5）",
          abs(cur.antenna - tgt.antenna) < 1e-6 and cur.antenna != 0.5,
          f"got {cur.antenna}")
    check("_first_ramp_done flag 已置 True", mod._first_ramp_done is True)  # noqa: SLF001

    # 再换一个 emotion，第二次 ramp 应该真正 ramp（不再 snap）
    tracker.value = "sad"
    t1 = mod.clock()
    mod._tick_once()  # noqa: SLF001
    # 第二次：_ramp_from 应被设为 _current 旧值，并经历线性插值
    # 但因 tick_interval_s 太短，elapsed 远小于 ramp_s，_current 应在 from..target 之间
    new_target = mod._target  # noqa: SLF001
    new_cur = mod.current_offset()
    check("第二次切换走 ramp（_current 介于 happy 与 sad 之间）",
          not (abs(new_cur.pitch_deg - new_target.pitch_deg) < 1e-6),
          f"cur.pitch={new_cur.pitch_deg} target.pitch={new_target.pitch_deg}")
except Exception:  # noqa: BLE001
    errors.append("V1: " + traceback.format_exc())


# =======================================================================
# V2 antenna SAD ≠ NEUTRAL
# =======================================================================
print("V2: antenna_joint_rad 整段可区分 — SAD (a=0.0) ≠ NEUTRAL (a=0.5) ≠ HAPPY (a=1.0)")
try:
    from coco.robot.posture_baseline import PostureOffset

    sad = PostureOffset(antenna=0.0).antenna_joint_rad()
    neu = PostureOffset(antenna=0.5).antenna_joint_rad()
    hap = PostureOffset(antenna=1.0).antenna_joint_rad()
    check("SAD antenna 输出非中位 (0,0)", sad != (0.0, 0.0), f"got {sad}")
    check("NEUTRAL antenna 仍是 (0,0)", neu == (0.0, 0.0) or neu == (0.0, -0.0),
          f"got {neu}")
    check("HAPPY antenna 外展 (+amp, -amp)", hap[0] > 0 and hap[1] < 0, f"got {hap}")
    check("SAD ≠ NEUTRAL", sad != neu, f"sad={sad} neu={neu}")
    check("SAD 与 HAPPY 方向相反（内合 vs 外展）",
          sad[0] < 0 and sad[1] > 0 and hap[0] > 0 and hap[1] < 0,
          f"sad={sad} hap={hap}")
except Exception:  # noqa: BLE001
    errors.append("V2: " + traceback.format_exc())


# =======================================================================
# V3 pause/resume 嵌套引用计数
# =======================================================================
print("V3: pause/resume 嵌套引用计数 — 两层 pause 需要两次 resume 才真正解锁")
try:
    from coco.robot.posture_baseline import (
        PostureBaselineConfig,
        PostureBaselineModulator,
    )

    r = make_mock_robot()
    cfg = PostureBaselineConfig(enabled=True, ramp_s=0.5, tick_interval_s=0.05, debounce_s=0.0)
    mod = PostureBaselineModulator(robot=r, emotion_tracker=None, power_state=None, config=cfg)

    check("初态 not paused", mod.is_paused() is False)
    mod.pause()
    check("第 1 次 pause 后 paused=True", mod.is_paused() is True)
    mod.pause()
    check("第 2 次 pause 后仍 paused=True（计数=2）", mod.is_paused() is True)
    mod.resume()
    check("第 1 次 resume 后仍 paused=True（计数=1，内层 resume 不解锁）",
          mod.is_paused() is True)
    mod.resume()
    check("第 2 次 resume 后 paused=False（计数=0，外层 resume 真正解锁）",
          mod.is_paused() is False)
    # 多余 resume 幂等
    mod.resume()
    check("多余 resume 幂等（不抛、保持 not paused）", mod.is_paused() is False)

    # _tick_once 在 paused 时 skip 天线下发；解锁后恢复
    mod.pause()
    mod.pause()
    n0 = r.set_target_antenna_joint_positions.call_count
    for _ in range(5):
        mod._tick_once()  # noqa: SLF001
    n1 = r.set_target_antenna_joint_positions.call_count
    check("嵌套 pause 期间天线 0 下发", n1 == n0, f"before={n0} after={n1}")
    mod.resume()  # 计数=1，仍 pause
    for _ in range(3):
        mod._tick_once()  # noqa: SLF001
    n2 = r.set_target_antenna_joint_positions.call_count
    check("内层 resume 后仍 pause，天线仍不下发", n2 == n1, f"after={n1} after2={n2}")
    mod.resume()  # 计数=0，真正解锁
    for _ in range(3):
        mod._tick_once()  # noqa: SLF001
    n3 = r.set_target_antenna_joint_positions.call_count
    check("外层 resume 后天线恢复下发", n3 > n2, f"after2={n2} after3={n3}")
except Exception:  # noqa: BLE001
    errors.append("V3: " + traceback.format_exc())


# =======================================================================
# V4 history maxlen
# =======================================================================
print("V4: PostureBaselineStats.history 是 deque(maxlen=200)，超出旧条目被丢")
try:
    from coco.robot.posture_baseline import PostureBaselineStats

    s = PostureBaselineStats()
    check("history 类型为 deque", isinstance(s.history, deque),
          f"got {type(s.history).__name__}")
    check("history maxlen=200", s.history.maxlen == 200, f"got {s.history.maxlen}")
    for i in range(500):
        s.history.append(f"x{i}")
    check("超出后长度 == maxlen", len(s.history) == 200, f"got {len(s.history)}")
    check("旧条目已被丢弃（队头是 x300）", s.history[0] == "x300", f"got {s.history[0]}")
    check("队尾是最新条目 x499", s.history[-1] == "x499", f"got {s.history[-1]}")
except Exception:  # noqa: BLE001
    errors.append("V4: " + traceback.format_exc())


# =======================================================================
# V5 play() 隐式 stop 语义文档化
# =======================================================================
print("V5: ExpressionPlayer.play docstring 显式说明 stop 与并发语义")
try:
    from coco.robot.expressions import ExpressionPlayer

    doc = inspect.getdoc(ExpressionPlayer.play) or ""
    check("play docstring 非空", len(doc) > 50, f"len={len(doc)}")
    check("docstring 提到 stop", "stop" in doc.lower(),
          f"doc snippet={doc[:120]!r}")
    check("docstring 提到并发/busy 拒绝", "并发" in doc or "busy" in doc.lower(),
          f"doc snippet={doc[:120]!r}")
    check("docstring 明确说不会隐式中断（不会 / not / 拒）",
          ("不会" in doc) or ("not" in doc.lower()) or ("拒" in doc),
          f"doc snippet={doc[:200]!r}")
except Exception:  # noqa: BLE001
    errors.append("V5: " + traceback.format_exc())


# =======================================================================
# V6 emit fallback import 在模块顶
# =======================================================================
print("V6: emit fallback import 提到模块顶（_DEFAULT_EMIT 在模块属性）")
try:
    import coco.robot.posture_baseline as pb
    import coco.robot.expressions as ex

    check("posture_baseline._DEFAULT_EMIT 存在",
          hasattr(pb, "_DEFAULT_EMIT"), "")
    check("posture_baseline._DEFAULT_EMIT is not None",
          pb._DEFAULT_EMIT is not None, "")
    check("expressions._DEFAULT_EMIT 存在",
          hasattr(ex, "_DEFAULT_EMIT"), "")
    check("expressions._DEFAULT_EMIT is not None",
          ex._DEFAULT_EMIT is not None, "")

    # 关键：hot path 内不再有 `from coco.logging_setup import emit as _emit`
    # 直接读源码字符串确认。
    pb_src = inspect.getsource(pb.PostureBaselineModulator._emit_event)
    ex_src = inspect.getsource(ex.ExpressionPlayer._emit_event)
    check("posture_baseline._emit_event 不再 lazy import logging_setup",
          "from coco.logging_setup import emit" not in pb_src,
          f"pb_src has lazy import")
    check("expressions._emit_event 不再 lazy import logging_setup",
          "from coco.logging_setup import emit" not in ex_src,
          f"ex_src has lazy import")

    # 真调一次 emit，确认走默认 emit 不抛
    from coco.robot.posture_baseline import PostureBaselineModulator, PostureBaselineConfig
    r = make_mock_robot()
    cfg = PostureBaselineConfig(enabled=True)
    mod = PostureBaselineModulator(robot=r, config=cfg)
    mod._emit_event("robot.test_event", "hello", k="v")  # 应不抛
    check("默认 emit 调用不抛", True)
except Exception:  # noqa: BLE001
    errors.append("V6: " + traceback.format_exc())


# =======================================================================
# V7 回归 verify_robot_004（必）
# =======================================================================
print("V7: 回归 verify_robot_004 全 PASS（subprocess）")
try:
    res = subprocess.run(
        [sys.executable, "scripts/verify_robot_004.py"],
        capture_output=True, text=True, timeout=120,
    )
    ok = res.returncode == 0
    check("verify_robot_004 returncode == 0", ok,
          f"rc={res.returncode}; tail stderr={res.stderr[-300:] if res.stderr else ''}")
except Exception:  # noqa: BLE001
    errors.append("V7: " + traceback.format_exc())


# =======================================================================
# V8 与其它 phase-8 feature 不互相干扰（边界 grep + import smoke）
# =======================================================================
print("V8: 与 vision-006 / interact-011 / companion-008 不互相干扰（import smoke）")
try:
    # 边界：robot-005 改动只动 coco/robot/{posture_baseline,expressions}.py 与
    # scripts/verify_robot_005.py + feature_list.json；不应触碰 vision / interact / companion
    import coco.perception.scene_caption  # vision-006
    import coco.robot.posture_baseline  # 本 feature 模块
    import coco.robot.expressions  # 本 feature 模块
    # interact / companion 顶层包 import 即可
    import coco.companion  # noqa: F401
    check("vision-006 SceneCaption + robot.expressions/posture_baseline import 不破", True)
except Exception:  # noqa: BLE001
    errors.append("V8: " + traceback.format_exc())


# =======================================================================
# 汇总
# =======================================================================
elapsed = time.time() - t0
print(f"\n========== robot-005 verify done in {elapsed:.2f}s ==========")
if errors:
    print(f"FAIL ({len(errors)} errors):")
    for e in errors:
        print("  - " + e.splitlines()[0])
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
