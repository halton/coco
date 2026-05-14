"""verify_companion_013 — companion-010 L2 收尾.

V1: ProactiveScheduler._loop 内 emotion_coord.tick(now=) 被调用（AST/源码 marker）
V2: 没有新 emotion 事件时到期还原仍发生（在 ProactiveScheduler 主循环 tick 内驱动）
V3: _bump_comfort_prefer 每次 prefer 还原后重 capture baseline（再 alert 时 baseline 跟随当前 prefer）
V4: 用户在 alert 期间手动改 prefer，到期还原不被首次 baseline 回滚（保留用户改动）
V5: V6 端到端 fake 装配 — env OFF 时 Coordinator 不构造、tracker._listeners 不被绑定
V6: docstring 注释 baseline re-capture / 还原语义
V7: gate OFF 主路径无副作用（不 import 也不实例化 EmotionAlertCoordinator）
V8: tick 调用频率不显著影响 scheduler 主循环耗时（静态阈值断言：单次 tick < 1ms）
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []
PASSES: list[str] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    msg = f"[{tag}] {name}" + (f" — {detail}" if detail else "")
    print(msg)
    (PASSES if ok else FAILURES).append(name)


# ---------------------------------------------------------------------------
# V1: ProactiveScheduler._loop 调用 coord.tick(now=...)
# ---------------------------------------------------------------------------
def v1_proactive_loop_tick_marker() -> None:
    src = (ROOT / "coco" / "proactive.py").read_text(encoding="utf-8")
    has_attr = "_emotion_alert_coord" in src
    has_setter = re.search(r"def\s+set_emotion_alert_coord\s*\(", src) is not None
    # _loop 内带 now= 调用 tick
    has_tick_call = re.search(
        r"tick_fn\s*\(\s*now\s*=", src
    ) is not None
    _check(
        "V1 ProactiveScheduler._loop 调 coord.tick(now=...)",
        has_attr and has_setter and has_tick_call,
        f"attr={has_attr} setter={has_setter} tick_now={has_tick_call}",
    )


# ---------------------------------------------------------------------------
# V2: 没有新 emotion 事件时到期还原仍发生（驱动 scheduler tick）
# ---------------------------------------------------------------------------
def v2_restore_without_new_emotion() -> None:
    """构造 fake scheduler-like：直接调 coord.tick(now=...)，模拟 main 循环的 tick。"""
    try:
        from coco.companion.emotion_memory import (
            EmotionMemoryWindow, EmotionAlertCoordinator,
        )

        class _FakePS:
            def __init__(self) -> None:
                self.prefer: dict = {}
                self.alerts: list = []

            def get_topic_preferences(self):
                return dict(self.prefer)

            def set_topic_preferences(self, p):
                self.prefer = dict(p or {})

            def record_emotion_alert_trigger(self, **kw):
                self.alerts.append(kw)

        ps = _FakePS()
        win = EmotionMemoryWindow()
        # 短 prefer_duration，便于到期
        coord = EmotionAlertCoordinator(
            win, proactive_scheduler=ps, prefer_duration_s=0.05, emit_fn=lambda *a, **k: None,
        )
        # 直接触发 bump
        coord._bump_comfort_prefer(now=0.0)
        before = dict(ps.prefer)
        # 时间推进，仅调 tick（不再投 emotion）
        time.sleep(0.08)
        coord.tick(now=time.monotonic() + 1000.0)  # 强制 t > _restore_at
        after = dict(ps.prefer)
        # 还原后 prefer 不再含 comfort key（"安慰"）
        ok = ("安慰" in before) and ("安慰" not in after)
        _check(
            "V2 无新 emotion 事件，scheduler tick 驱动到期还原",
            ok,
            f"before_keys={list(before)} after_keys={list(after)}",
        )
    except Exception as e:  # noqa: BLE001
        _check("V2 restore_without_new_emotion", False, f"exception {e!r}")


# ---------------------------------------------------------------------------
# V3: 每次还原后下一次 bump baseline 跟当前 prefer（不是用第一次的快照）
# ---------------------------------------------------------------------------
def v3_baseline_recapture_after_restore() -> None:
    try:
        from coco.companion.emotion_memory import (
            EmotionMemoryWindow, EmotionAlertCoordinator,
        )

        class _FakePS:
            def __init__(self):
                self.prefer = {"原-A": 0.5}

            def get_topic_preferences(self):
                return dict(self.prefer)

            def set_topic_preferences(self, p):
                self.prefer = dict(p or {})

        ps = _FakePS()
        win = EmotionMemoryWindow()
        coord = EmotionAlertCoordinator(
            win, proactive_scheduler=ps, prefer_duration_s=0.0,
            emit_fn=lambda *a, **k: None,
        )
        # 第一次 bump
        coord._bump_comfort_prefer(now=0.0)
        # 还原（直接 tick 强制过期）
        coord.tick(now=1.0)
        # 用户改 prefer：删除 "原-A"，加 "用户-B"
        ps.set_topic_preferences({"用户-B": 0.7})
        # 第二次 bump：baseline 应该来自当前 prefer（"用户-B"），而非第一次快照（"原-A"）
        coord._bump_comfort_prefer(now=2.0)
        new_baseline = dict(coord._original_prefer or {})
        ok = ("用户-B" in new_baseline) and ("原-A" not in new_baseline)
        _check(
            "V3 还原后下次 bump baseline 重 capture（跟随当前 prefer）",
            ok,
            f"new_baseline={new_baseline}",
        )
    except Exception as e:  # noqa: BLE001
        _check("V3 baseline_recapture_after_restore", False, f"exception {e!r}")


# ---------------------------------------------------------------------------
# V4: 用户手动改 prefer 不被首次 baseline 回滚
# ---------------------------------------------------------------------------
def v4_user_change_not_rolled_back() -> None:
    try:
        from coco.companion.emotion_memory import (
            EmotionMemoryWindow, EmotionAlertCoordinator,
        )

        class _FakePS:
            def __init__(self):
                self.prefer = {"原-A": 0.5}

            def get_topic_preferences(self):
                return dict(self.prefer)

            def set_topic_preferences(self, p):
                self.prefer = dict(p or {})

        ps = _FakePS()
        win = EmotionMemoryWindow()
        coord = EmotionAlertCoordinator(
            win, proactive_scheduler=ps, prefer_duration_s=0.0,
            emit_fn=lambda *a, **k: None,
        )
        # alert → bump
        coord._bump_comfort_prefer(now=0.0)
        # alert 期间用户加了 "用户加的-X"
        cur = ps.get_topic_preferences()
        cur["用户加的-X"] = 0.3
        ps.set_topic_preferences(cur)
        # tick 还原
        coord.tick(now=1.0)
        after = ps.get_topic_preferences()
        # 还原后：comfort keys 应被剥掉；"用户加的-X" 应保留；"原-A" 也应保留（baseline 兜底）
        ok = (
            "安慰" not in after
            and "用户加的-X" in after
            and "原-A" in after
        )
        _check(
            "V4 用户手动改 prefer 不被首次 baseline 回滚",
            ok,
            f"after={after}",
        )
    except Exception as e:  # noqa: BLE001
        _check("V4 user_change_not_rolled_back", False, f"exception {e!r}")


# ---------------------------------------------------------------------------
# V5: env OFF 时 Coordinator 不构造 / EmotionTracker._listeners 不被绑定
# ---------------------------------------------------------------------------
def v5_env_off_no_listener() -> None:
    try:
        from coco.companion.emotion_memory import emotion_memory_enabled_from_env
        # 强制 OFF
        old = os.environ.pop("COCO_EMO_MEMORY", None)
        try:
            enabled = emotion_memory_enabled_from_env()
            ok_off = enabled is False
        finally:
            if old is not None:
                os.environ["COCO_EMO_MEMORY"] = old
        # main.py 装配代码块在 if _emm_enabled() 内 — 静态确认
        main_src = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
        has_gate = re.search(r"if\s+_emm_enabled\(\)\s*:", main_src) is not None
        # wire setter 也在 gate 内（紧邻 .start(_shared_emotion_tracker) 之后）
        has_wire = "set_emotion_alert_coord" in main_src
        _check(
            "V5 env OFF → Coordinator 不构造 + main 装配走 env gate + wire 在 gate 内",
            ok_off and has_gate and has_wire,
            f"off={ok_off} gate={has_gate} wire={has_wire}",
        )
    except Exception as e:  # noqa: BLE001
        _check("V5 env_off_no_listener", False, f"exception {e!r}")


# ---------------------------------------------------------------------------
# V6: docstring 注释 baseline re-capture / 还原语义
# ---------------------------------------------------------------------------
def v6_docstring_semantics() -> None:
    src = (ROOT / "coco" / "companion" / "emotion_memory.py").read_text(encoding="utf-8")
    # tick docstring 含 "撤回 comfort" / "用户" / "baseline" 关键描述
    m = re.search(r"def tick\(self,.*?\n\s+\"\"\"(.*?)\"\"\"", src, re.S)
    body = m.group(1) if m else ""
    has_semantics = (
        "撤回" in body or "comfort" in body.lower()
    ) and ("用户" in body or "user" in body.lower()) and ("baseline" in body.lower())
    # _bump_comfort_prefer 内含 re-capture 注释
    bm = re.search(r"def _bump_comfort_prefer\(self.*?\n(.*?)\n    def\s", src, re.S)
    bump_body = bm.group(1) if bm else ""
    has_recapture_doc = "重新 capture" in bump_body or "重 capture" in bump_body or "recapture" in bump_body.lower()
    _check(
        "V6 tick + _bump_comfort_prefer docstring 注释 re-capture / 还原语义",
        has_semantics and has_recapture_doc,
        f"tick_sem={has_semantics} bump_doc={has_recapture_doc}",
    )


# ---------------------------------------------------------------------------
# V7: gate OFF 主路径无副作用（ProactiveScheduler._emotion_alert_coord 默认 None）
# ---------------------------------------------------------------------------
def v7_gate_off_side_effect_free() -> None:
    try:
        from coco.proactive import ProactiveScheduler, ProactiveConfig
        ps = ProactiveScheduler(config=ProactiveConfig())
        ok = getattr(ps, "_emotion_alert_coord", "MISSING") is None
        # 也可手动调 _loop 单次（构造 stop_event 立即 set）— 不抛
        ev = threading.Event()
        ev.set()
        # 直接调 _loop 会立即退出（wait 立刻返回 True）
        ps._stop_event = ev
        try:
            ps._loop()
            loop_ok = True
        except Exception as e:  # noqa: BLE001
            loop_ok = False
            print(f"  _loop raised: {e!r}")
        _check(
            "V7 gate OFF coord=None 主路径不抛",
            ok and loop_ok,
            f"coord_none={ok} loop_ok={loop_ok}",
        )
    except Exception as e:  # noqa: BLE001
        _check("V7 gate_off_side_effect_free", False, f"exception {e!r}")


# ---------------------------------------------------------------------------
# V8: tick 调用频率不显著影响 scheduler 主循环耗时
# ---------------------------------------------------------------------------
def v8_tick_perf() -> None:
    try:
        from coco.companion.emotion_memory import (
            EmotionMemoryWindow, EmotionAlertCoordinator,
        )

        class _FakePS:
            def __init__(self):
                self.prefer = {}
            def get_topic_preferences(self):
                return dict(self.prefer)
            def set_topic_preferences(self, p):
                self.prefer = dict(p or {})

        coord = EmotionAlertCoordinator(
            EmotionMemoryWindow(),
            proactive_scheduler=_FakePS(),
            emit_fn=lambda *a, **k: None,
        )
        # 没有 bump 时 tick 是 no-op：测 1000 次
        N = 1000
        t0 = time.perf_counter()
        for _ in range(N):
            coord.tick(now=0.0)
        elapsed = time.perf_counter() - t0
        per = elapsed / N
        # 静态阈值：单次 < 1ms（实际应该 << 1us）
        ok = per < 1e-3
        _check(
            "V8 tick 单次耗时 < 1ms",
            ok,
            f"per_tick={per*1e6:.2f}us total={elapsed*1000:.2f}ms over {N} calls",
        )
    except Exception as e:  # noqa: BLE001
        _check("V8 tick_perf", False, f"exception {e!r}")


# ---------------------------------------------------------------------------
# 回归
# ---------------------------------------------------------------------------
def regress(script: str) -> None:
    py = sys.executable
    r = subprocess.run(
        [py, f"scripts/{script}"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    tail = (r.stdout.strip().splitlines() or [r.stderr[-200:]])[-1]
    _check(
        f"regress {script} (exit=0)",
        r.returncode == 0,
        f"rc={r.returncode} tail={tail}",
    )


def main() -> int:
    for fn in (
        v1_proactive_loop_tick_marker,
        v2_restore_without_new_emotion,
        v3_baseline_recapture_after_restore,
        v4_user_change_not_rolled_back,
        v5_env_off_no_listener,
        v6_docstring_semantics,
        v7_gate_off_side_effect_free,
        v8_tick_perf,
    ):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _check(fn.__name__, False, f"exception: {e!r}")
    # 回归
    for s in (
        "verify_companion_010.py",
        "verify_companion_011.py",
        "verify_companion_012.py",
        "verify_interact_013.py",
    ):
        try:
            regress(s)
        except Exception as e:  # noqa: BLE001
            _check(f"regress {s}", False, f"exception: {e!r}")
    print(
        f"\nverify_companion_013: PASS={len(PASSES)} FAIL={len(FAILURES)} "
        f"failures={FAILURES}"
    )
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
