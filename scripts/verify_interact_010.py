#!/usr/bin/env python3
"""verify_interact_010.py — 手势驱动对话回合 (interact-010) 验证.

V1 default OFF：COCO_GESTURE_DIALOG 未设/=0 → bridge inactive；事件被吞但不入 ConvStateMachine
V2 WAVE@IDLE → 触发 proactive prompt（注册 awaiting + dialog_memory append）
V3 WAVE@AWAITING 被抑制（state 不变，无新 dialog 写入，stats.skipped_state++）
V4 THUMBS_UP@AWAITING 5s 内当 yes → 调 inject_user_text_fn 注入 "好的"
V5 NOD@AWAITING_yesno → "是"；SHAKE@AWAITING_yesno → "不是"
V6 NOD/SHAKE@IDLE 不动作（且 NOD@AWAITING 但 last assistant 非 yes/no → 不动作）
V7 30s cooldown 与 ProactiveScheduler 共享：bridge 触发后 ProactiveScheduler.is_in_cooldown=True；
   反之 proactive.record_trigger 后 bridge 也跳过
V8 env clamp（COCO_GESTURE_DIALOG 非法值 → OFF）
V9 回归 vision-005 + interact-008 + interact-009 verify 子进程
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.conversation import (  # noqa: E402
    ConvState,
    ConversationStateMachine,
    ConversationConfig,
)
from coco.dialog import DialogMemory  # noqa: E402
from coco.gesture_dialog import (  # noqa: E402
    GestureDialogBridge,
    GestureDialogConfig,
    DEFAULT_AWAITING_WINDOW_S,
    DEFAULT_COOLDOWN_S,
    config_from_env,
    gesture_dialog_enabled_from_env,
    is_yes_no_question,
)
from coco.perception.gesture import GestureKind, GestureLabel  # noqa: E402
from coco.proactive import ProactiveScheduler, ProactiveConfig  # noqa: E402
from coco.logging_setup import setup_logging  # noqa: E402


EVIDENCE_DIR = ROOT / "evidence" / "interact-010"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

PASSES: List[str] = []
FAILURES: List[str] = []


def ok(msg: str) -> None:
    print(f"  PASS {msg}", flush=True)
    PASSES.append(msg)


def fail(msg: str) -> None:
    print(f"  FAIL {msg}", flush=True)
    FAILURES.append(msg)


def assert_true(cond: bool, label: str) -> bool:
    if cond:
        ok(label)
        return True
    fail(label)
    return False


def assert_eq(actual: Any, expected: Any, label: str) -> bool:
    if actual == expected:
        ok(f"{label} (={actual!r})")
        return True
    fail(f"{label} expected={expected!r} actual={actual!r}")
    return False


# --------------------------------------------------------------------------- helpers


class FakeClock:
    def __init__(self, t0: float = 1000.0) -> None:
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make_label(kind: GestureKind, ts: float = 0.0, conf: float = 0.9) -> GestureLabel:
    return GestureLabel(kind=kind, confidence=conf, ts=ts, bbox=None)


def make_bridge(
    *,
    enabled: bool = True,
    conv_sm: Optional[ConversationStateMachine] = None,
    proactive: Optional[ProactiveScheduler] = None,
    dialog_memory: Optional[DialogMemory] = None,
    inject_fn=None,
    clock: Optional[FakeClock] = None,
    awaiting_window_s: float = DEFAULT_AWAITING_WINDOW_S,
    cooldown_s: float = DEFAULT_COOLDOWN_S,
):
    cfg = GestureDialogConfig(
        enabled=enabled,
        awaiting_window_s=awaiting_window_s,
        cooldown_s=cooldown_s,
    )
    events: List[dict] = []

    def emit_fn(name: str, **fields):
        events.append({"name": name, **fields})

    bridge = GestureDialogBridge(
        config=cfg,
        conv_state_machine=conv_sm,
        dialog_memory=dialog_memory,
        proactive_scheduler=proactive,
        emit_fn=emit_fn,
        clock=clock if clock is not None else None,
        inject_user_text_fn=inject_fn,
    )
    return bridge, events


# --------------------------------------------------------------------------- V1


def v1_default_off() -> None:
    print("\n[V1] default OFF — COCO_GESTURE_DIALOG 未设 → bridge inactive", flush=True)
    # env 未设 → enabled_from_env = False
    saved = os.environ.pop("COCO_GESTURE_DIALOG", None)
    try:
        assert_true(not gesture_dialog_enabled_from_env(), "COCO_GESTURE_DIALOG 未设 → False")
        cfg = config_from_env()
        assert_true(not cfg.enabled, "config_from_env().enabled = False")
        bridge, _ = make_bridge(enabled=False)
        result = bridge.on_gesture_event(make_label(GestureKind.WAVE))
        assert_true(result is None, "disabled bridge.on_gesture_event 返回 None")
        assert_eq(bridge.stats.skipped_disabled, 1, "stats.skipped_disabled++")
    finally:
        if saved is not None:
            os.environ["COCO_GESTURE_DIALOG"] = saved


# --------------------------------------------------------------------------- V2


def v2_wave_idle_triggers_proactive() -> None:
    print("\n[V2] WAVE@IDLE → 触发 proactive prompt + dialog 写入", flush=True)
    clk = FakeClock()
    sm = ConversationStateMachine(clock=clk)
    dm = DialogMemory(clock=clk)
    inject_calls: List[dict] = []

    def inject_fn(text, *, kind, source):
        inject_calls.append({"text": text, "kind": kind, "source": source})
        return "你好呀"

    bridge, events = make_bridge(
        conv_sm=sm, dialog_memory=dm, inject_fn=inject_fn, clock=clk,
    )
    # IDLE 默认
    assert_eq(sm.current_state, ConvState.IDLE, "起始 state=IDLE")
    res = bridge.on_gesture_event(make_label(GestureKind.WAVE))
    assert_eq(res, "proactive", "WAVE@IDLE → proactive")
    assert_eq(bridge.stats.triggered_proactive, 1, "stats.triggered_proactive=1")
    assert_true(len(inject_calls) == 1, "inject_user_text_fn 被调一次")
    assert_eq(inject_calls[0]["source"], "proactive", "inject source=proactive")
    # dialog_memory 写入了 turn（assistant 端 = prompt）
    turns = dm.recent_turns()
    assert_true(len(turns) == 1 and turns[0][1] == "你想聊点什么？",
                "dialog_memory 含 (空 user, prompt assistant)")
    # emit 事件
    triggered = [e for e in events if e["name"] == "interact.gesture_dialog_triggered"]
    assert_true(len(triggered) == 1, "emit interact.gesture_dialog_triggered")
    assert_eq(triggered[0]["action"], "proactive", "emit action=proactive")
    assert_eq(triggered[0]["gesture_kind"], "wave", "emit gesture_kind=wave")


# --------------------------------------------------------------------------- V3


def v3_wave_awaiting_suppressed() -> None:
    print("\n[V3] WAVE@AWAITING → 抑制，无新 trigger", flush=True)
    clk = FakeClock()
    sm = ConversationStateMachine(clock=clk)
    dm = DialogMemory(clock=clk)
    bridge, events = make_bridge(conv_sm=sm, dialog_memory=dm, clock=clk)
    # 模拟 assistant 刚说完一句话 → 进入 awaiting 窗口
    bridge.register_assistant_utterance("我们去哪儿玩呢？")
    assert_true(bridge._is_awaiting_now(), "is_awaiting_now=True")
    n_before = len(dm.recent_turns())
    res = bridge.on_gesture_event(make_label(GestureKind.WAVE))
    assert_true(res is None, "WAVE@AWAITING → None")
    assert_eq(bridge.stats.skipped_state, 1, "stats.skipped_state=1")
    assert_eq(len(dm.recent_turns()), n_before, "dialog_memory 未新增")
    triggered = [e for e in events if e["name"] == "interact.gesture_dialog_triggered"]
    assert_true(len(triggered) == 0, "无 emit")


# --------------------------------------------------------------------------- V4


def v4_thumbs_up_awaiting_yes() -> None:
    print("\n[V4] THUMBS_UP@AWAITING 5s 内 → 注入 '好的'", flush=True)
    clk = FakeClock()
    sm = ConversationStateMachine(clock=clk)
    dm = DialogMemory(clock=clk)
    inject_calls: List[dict] = []

    def inject_fn(text, *, kind, source):
        inject_calls.append({"text": text, "kind": kind, "source": source})
        return "好的，那我们..."

    bridge, _ = make_bridge(
        conv_sm=sm, dialog_memory=dm, inject_fn=inject_fn, clock=clk,
    )
    # 注册 assistant 刚说话
    bridge.register_assistant_utterance("要继续吗？")
    clk.advance(2.0)  # 2s 后用户竖大拇指
    res = bridge.on_gesture_event(make_label(GestureKind.THUMBS_UP))
    assert_eq(res, "yes", "THUMBS_UP@AWAITING → yes")
    assert_eq(len(inject_calls), 1, "inject 调一次")
    assert_eq(inject_calls[0]["text"], "好的", "inject text='好的'")
    assert_eq(inject_calls[0]["kind"], "thumbs_up", "inject kind=thumbs_up")
    # dialog_memory 中带 [手势:thumbs_up] 前缀
    turns = dm.recent_turns()
    assert_true(any("[手势:thumbs_up]" in u for u, _a in turns),
                "dialog_memory user 端含 '[手势:thumbs_up]' 前缀")
    # 5s 窗口外 → 失效
    clk.advance(10.0)
    bridge2, _ = make_bridge(conv_sm=sm, dialog_memory=dm, inject_fn=inject_fn, clock=clk)
    bridge2.register_assistant_utterance("再确认一次？")
    clk.advance(10.0)  # 超出 5s 窗口
    res2 = bridge2.on_gesture_event(make_label(GestureKind.THUMBS_UP))
    assert_true(res2 is None, "THUMBS_UP 超出 awaiting 窗口 → None")


# --------------------------------------------------------------------------- V5


def v5_nod_shake_yesno() -> None:
    print("\n[V5] NOD/SHAKE@AWAITING_yesno → '是' / '不是'", flush=True)
    # NOD: yes
    clk = FakeClock()
    sm = ConversationStateMachine(clock=clk)
    dm = DialogMemory(clock=clk)
    nod_inject: List[dict] = []

    def nod_fn(text, *, kind, source):
        nod_inject.append({"text": text, "kind": kind})
        return "明白了"

    bridge_n, _ = make_bridge(
        conv_sm=sm, dialog_memory=dm, inject_fn=nod_fn, clock=clk,
    )
    bridge_n.register_assistant_utterance("你喜欢这首歌吗？")  # is_yes_no=True
    clk.advance(1.0)
    res = bridge_n.on_gesture_event(make_label(GestureKind.NOD))
    assert_eq(res, "yes", "NOD@yesno → yes")
    assert_eq(nod_inject[0]["text"], "是", "inject text='是'")
    assert_eq(bridge_n.stats.triggered_nod_yes, 1, "stats.triggered_nod_yes=1")

    # SHAKE: no
    clk2 = FakeClock()
    sm2 = ConversationStateMachine(clock=clk2)
    dm2 = DialogMemory(clock=clk2)
    shake_inject: List[dict] = []

    def shake_fn(text, *, kind, source):
        shake_inject.append({"text": text, "kind": kind})
        return "好的"

    bridge_s, _ = make_bridge(
        conv_sm=sm2, dialog_memory=dm2, inject_fn=shake_fn, clock=clk2,
    )
    bridge_s.register_assistant_utterance("是不是要换一首？")
    clk2.advance(1.0)
    res2 = bridge_s.on_gesture_event(make_label(GestureKind.SHAKE))
    assert_eq(res2, "no", "SHAKE@yesno → no")
    assert_eq(shake_inject[0]["text"], "不是", "inject text='不是'")
    assert_eq(bridge_s.stats.triggered_shake_no, 1, "stats.triggered_shake_no=1")


# --------------------------------------------------------------------------- V6


def v6_nod_shake_idle_or_no_yesno_flag() -> None:
    print("\n[V6] NOD/SHAKE@IDLE 不动作；AWAITING 但非 yes/no 提问也不动作", flush=True)
    # 6a: NOD@IDLE
    clk = FakeClock()
    sm = ConversationStateMachine(clock=clk)
    dm = DialogMemory(clock=clk)
    bridge, _ = make_bridge(conv_sm=sm, dialog_memory=dm, clock=clk)
    res = bridge.on_gesture_event(make_label(GestureKind.NOD))
    assert_true(res is None, "NOD@IDLE → None")
    assert_eq(bridge.stats.triggered_nod_yes, 0, "无 nod_yes 触发")

    res2 = bridge.on_gesture_event(make_label(GestureKind.SHAKE))
    assert_true(res2 is None, "SHAKE@IDLE → None")

    # 6b: AWAITING 但 last assistant 不是 yes/no 提问（陈述句）
    bridge.register_assistant_utterance("今天天气真不错。")
    assert_true(bridge._is_awaiting_now(), "is_awaiting_now=True")
    res3 = bridge.on_gesture_event(make_label(GestureKind.NOD))
    assert_true(res3 is None, "NOD@AWAITING_非yesno → None")
    assert_eq(bridge.stats.skipped_yesno, 1, "stats.skipped_yesno=1")

    res4 = bridge.on_gesture_event(make_label(GestureKind.SHAKE))
    assert_true(res4 is None, "SHAKE@AWAITING_非yesno → None")
    assert_eq(bridge.stats.skipped_yesno, 2, "stats.skipped_yesno=2")

    # is_yes_no_question 单元
    assert_true(is_yes_no_question("要继续吗？"), "is_yes_no_question('要继续吗？')")
    assert_true(is_yes_no_question("是不是该睡觉了"), "is_yes_no_question('是不是该睡觉了')")
    assert_true(not is_yes_no_question("今天天气不错。"), "陈述句 → False")
    assert_true(not is_yes_no_question("你叫什么名字？"), "wh-question → False")


# --------------------------------------------------------------------------- V7


def v7_cooldown_shared() -> None:
    print("\n[V7] 30s cooldown 共享：bridge ↔ ProactiveScheduler", flush=True)
    clk = FakeClock()
    sm = ConversationStateMachine(clock=clk)
    dm = DialogMemory(clock=clk)
    proactive = ProactiveScheduler(
        config=ProactiveConfig(enabled=True, idle_threshold_s=10.0,
                               cooldown_s=30.0, max_topics_per_hour=10),
        clock=clk,
    )
    inject_calls: List[dict] = []

    def inject_fn(text, *, kind, source):
        inject_calls.append({"text": text, "kind": kind, "source": source})
        return "嗯嗯"

    bridge, _ = make_bridge(
        conv_sm=sm, dialog_memory=dm, proactive=proactive,
        inject_fn=inject_fn, clock=clk, cooldown_s=30.0,
    )
    # bridge 触发 → proactive.is_in_cooldown=True
    assert_true(not proactive.is_in_cooldown(), "起始 proactive 不在 cooldown")
    bridge.on_gesture_event(make_label(GestureKind.WAVE))
    assert_true(proactive.is_in_cooldown(), "bridge 触发后 proactive.is_in_cooldown=True")

    # 在 30s 内再次 WAVE@IDLE → cooldown skip
    bridge_state_before = bridge.stats.skipped_cooldown
    # 重置 awaiting：register 让窗口过期（>5s）
    clk.advance(10.0)
    res = bridge.on_gesture_event(make_label(GestureKind.WAVE))
    assert_true(res is None, "30s 内再次 WAVE@IDLE → 被 cooldown skip")
    assert_true(bridge.stats.skipped_cooldown > bridge_state_before,
                "stats.skipped_cooldown++")

    # 反向：proactive.record_trigger 写穿 → bridge.is_in_cooldown 也认
    clk.advance(40.0)  # 窗口过期
    assert_true(not proactive.is_in_cooldown(), "40s 后 proactive 出 cooldown")
    proactive.record_trigger(source="proactive_self")
    assert_true(bridge._is_in_shared_cooldown(),
                "proactive.record_trigger 后 bridge._is_in_shared_cooldown=True")
    res2 = bridge.on_gesture_event(make_label(GestureKind.WAVE))
    assert_true(res2 is None, "proactive 触发后 bridge WAVE@IDLE 被 skip")


# --------------------------------------------------------------------------- V8


def v8_env_clamp() -> None:
    print("\n[V8] env clamp：COCO_GESTURE_DIALOG 非法值 → OFF", flush=True)
    saved = os.environ.pop("COCO_GESTURE_DIALOG", None)
    try:
        for bad in ("garbage", "maybe", "2", " "):
            os.environ["COCO_GESTURE_DIALOG"] = bad
            ok_str = f"COCO_GESTURE_DIALOG={bad!r} → enabled=False"
            assert_true(not gesture_dialog_enabled_from_env(), ok_str)
        # legit ON
        os.environ["COCO_GESTURE_DIALOG"] = "1"
        assert_true(gesture_dialog_enabled_from_env(), "='1' → True")
        os.environ["COCO_GESTURE_DIALOG"] = "true"
        assert_true(gesture_dialog_enabled_from_env(), "='true' → True")
        os.environ["COCO_GESTURE_DIALOG"] = "0"
        assert_true(not gesture_dialog_enabled_from_env(), "='0' → False")
    finally:
        if saved is not None:
            os.environ["COCO_GESTURE_DIALOG"] = saved
        else:
            os.environ.pop("COCO_GESTURE_DIALOG", None)


# --------------------------------------------------------------------------- V9


def _run_subprocess(script: Path, env_extra: Optional[dict] = None,
                    timeout: int = 180) -> tuple[int, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    if env_extra:
        env.update(env_extra)
    try:
        r = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, (r.stdout or "")[-1500:] + "\n" + (r.stderr or "")[-500:]
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return 2, f"{type(e).__name__}: {e}"


def v9_regression_subprocess() -> None:
    print("\n[V9] 回归 vision-005 + interact-008 + interact-009 verify 子进程", flush=True)
    targets = [
        ("vision-005", ROOT / "scripts" / "verify_vision_005.py"),
        ("interact-008", ROOT / "scripts" / "verify_interact_008.py"),
        ("interact-009", ROOT / "scripts" / "verify_interact_009.py"),
    ]
    for name, script in targets:
        if not script.exists():
            fail(f"V9 {name} script 不存在: {script}")
            continue
        rc, tail = _run_subprocess(script)
        if rc == 0:
            ok(f"V9 {name} verify exit=0")
        else:
            fail(f"V9 {name} verify exit={rc} tail=\n{tail}")


# --------------------------------------------------------------------------- main


def main() -> int:
    setup_logging(jsonl=False, level="WARNING")
    t0 = time.time()
    v1_default_off()
    v2_wave_idle_triggers_proactive()
    v3_wave_awaiting_suppressed()
    v4_thumbs_up_awaiting_yes()
    v5_nod_shake_yesno()
    v6_nod_shake_idle_or_no_yesno_flag()
    v7_cooldown_shared()
    v8_env_clamp()
    v9_regression_subprocess()
    dt = time.time() - t0

    summary = {
        "feature": "interact-010",
        "passes": len(PASSES),
        "failures": len(FAILURES),
        "failure_messages": FAILURES,
        "duration_s": round(dt, 2),
    }
    (EVIDENCE_DIR / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== interact-010 verify: {len(PASSES)} PASS / {len(FAILURES)} FAIL "
          f"in {dt:.2f}s ===", flush=True)
    if FAILURES:
        for m in FAILURES:
            print(f"  FAIL {m}", flush=True)
        return 1
    print("ALL PASS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
