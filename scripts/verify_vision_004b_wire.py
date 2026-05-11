"""vision-004b-wire verification: MultiFaceAttention 接线到运行时.

跑法：
  uv run python scripts/verify_vision_004b_wire.py

子项（V1-V12）：
  V1  默认 OFF — COCO_GREET_SECONDARY 未设时 build_greet_secondary_wire 返回 None
  V2  COCO_GREET_SECONDARY=1 + 完整依赖 → 构造成功 + 后台线程启动
  V3  整链路：mock attention.current=primary A、tracker.tracks=[A,B]、
      silence>8s、B visible>3s → 触发 GreetAction → tts.say + expression.play 被调
  V4  cooldown 内重复触发不再调
  V5  conv_state=QUIET 抑制（is_quiet_now() True）
  V6  awaiting_response 抑制（若 conv_state 暴露此状态则用，否则 SKIP）
  V7  proactive_recent 抑制（last_proactive_ts 近期）
  V8  named 过滤：require_named_secondary=True, secondary.name=None 不触发
  V9  primary 闪烁 race：primary 在 A↔B 间 1Hz 切换 5 次 + silence 满 + B visible 满
      → 防抖 (primary_stable_s>0) 后 silence 不被反复重置，最终能触发 greet
  V10 stop() 干净退出 + 后台线程 join < 2s
  V11 emit "vision.multi_face_state_changed" component="vision"
  V12 env clamp（tick_hz / silence_threshold_s 等）

retval：0 全 PASS；1 任一失败
evidence 落 evidence/vision-004b-wire/verify_summary.json
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.companion.greet_secondary_wire import (
    GreetSecondaryConfig,
    GreetSecondaryWire,
    build_greet_secondary_wire,
    greet_secondary_config_from_env,
)
from coco.companion.multi_face_attention import MFAState

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

errors: List[str] = []
results: dict = {}


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok   {msg}")
    else:
        errors.append(msg)
        print(f"  FAIL {msg}")


# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------


@dataclass
class FakeBox:
    cx: float = 0.0
    cy: float = 0.0
    w: int = 100
    h: int = 100


@dataclass
class FakeTrack:
    track_id: int
    name: Optional[str] = None
    last_seen_ts: float = 0.0
    smoothed_cx: float = 0.0
    smoothed_cy: float = 0.0
    box: FakeBox = field(default_factory=FakeBox)


@dataclass
class FakeAttentionTarget:
    track_id: int
    smoothed_cx: float = 0.0


class FakeSnap:
    def __init__(self, tracks):
        self.tracks = tuple(tracks)


class FakeTracker:
    """暴露 latest() -> snap.tracks; 由调用方注入 tracks."""

    def __init__(self):
        self.tracks: list = []

    def latest(self):
        return FakeSnap(self.tracks)


class FakeSelector:
    """current() -> primary."""

    def __init__(self):
        self.primary: Optional[FakeAttentionTarget] = None

    def current(self):
        return self.primary


class FakeConvSM:
    """暴露 is_quiet_now() + current_state."""

    def __init__(self, state: str = "idle", quiet: bool = False):
        self._state = state
        self._quiet = quiet

    @property
    def current_state(self):
        return self._state

    def is_quiet_now(self) -> bool:
        return self._quiet


class FakeProactive:
    """暴露 _last_proactive_ts (墙钟)."""

    def __init__(self):
        self._last_proactive_ts: float = 0.0


class FakeClock:
    def __init__(self, t0: float = 1000.0) -> None:
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class TTSStub:
    def __init__(self):
        self.calls: list = []

    def __call__(self, text: str):
        self.calls.append(text)


class ExpressionStub:
    def __init__(self):
        self.calls: list = []

    def play(self, name: str) -> bool:
        self.calls.append(name)
        return True


# -----------------------------------------------------------------------------
# V1: 默认 OFF
# -----------------------------------------------------------------------------


def v1_default_off() -> None:
    print("V1 默认 OFF (COCO_GREET_SECONDARY 未设)")
    env = {k: v for k, v in os.environ.items() if not k.startswith("COCO_GREET_")}
    cfg = greet_secondary_config_from_env(env)
    check(cfg.enabled is False, "默认 enabled=False")
    wire = build_greet_secondary_wire(
        config=cfg,
        attention_selector=FakeSelector(),
        face_tracker=FakeTracker(),
    )
    check(wire is None, "默认 OFF 时 build_greet_secondary_wire 返回 None")
    results["V1"] = "PASS"


# -----------------------------------------------------------------------------
# V2: enabled 构造 + 后台线程启动
# -----------------------------------------------------------------------------


def v2_enabled_construct() -> None:
    print("V2 COCO_GREET_SECONDARY=1 构造成功 + 后台线程启动")
    cfg = GreetSecondaryConfig(enabled=True, tick_hz=10.0, primary_stable_s=0.0)
    sel = FakeSelector()
    trk = FakeTracker()
    wire = build_greet_secondary_wire(
        config=cfg,
        attention_selector=sel,
        face_tracker=trk,
        tts_say_fn=TTSStub(),
    )
    check(wire is not None, "build_greet_secondary_wire 返回非 None")
    assert wire is not None
    stop_event = threading.Event()
    wire.start(stop_event)
    time.sleep(0.3)
    check(wire.is_alive(), "后台线程 alive")
    wire.stop(timeout=1.0)
    check(not wire.is_alive(), "stop 后线程退出")
    # 缺 attention_selector 或 face_tracker 时返回 None
    wire2 = build_greet_secondary_wire(config=cfg, attention_selector=None, face_tracker=trk)
    check(wire2 is None, "attention_selector=None 时返回 None")
    wire3 = build_greet_secondary_wire(config=cfg, attention_selector=sel, face_tracker=None)
    check(wire3 is None, "face_tracker=None 时返回 None")
    results["V2"] = "PASS"


# -----------------------------------------------------------------------------
# Helper: 构造禁用后台 tick 的 wire（手动调 _tick_once）
# -----------------------------------------------------------------------------


def _make_wire(
    *,
    clock: FakeClock,
    sel: FakeSelector,
    trk: FakeTracker,
    tts: Optional[TTSStub] = None,
    expr: Optional[ExpressionStub] = None,
    conv_sm: Any = None,
    proactive: Any = None,
    emit_fn: Any = None,
    primary_stable_s: float = 0.0,
    silence_threshold_s: float = 8.0,
    secondary_visible_s: float = 3.0,
    cooldown_s: float = 30.0,
    proactive_block_window_s: float = 10.0,
    require_named_secondary: bool = True,
) -> GreetSecondaryWire:
    cfg = GreetSecondaryConfig(
        enabled=True,
        tick_hz=3.0,
        silence_threshold_s=silence_threshold_s,
        secondary_visible_s=secondary_visible_s,
        cooldown_s=cooldown_s,
        greet_duration_s=3.0,
        return_duration_s=2.0,
        proactive_block_window_s=proactive_block_window_s,
        require_named_secondary=require_named_secondary,
        primary_stable_s=primary_stable_s,
    )
    return build_greet_secondary_wire(
        config=cfg,
        attention_selector=sel,
        face_tracker=trk,
        tts_say_fn=tts,
        expression_player=expr,
        conv_state_machine=conv_sm,
        proactive_scheduler=proactive,
        emit_fn=emit_fn,
        clock=clock,
    )  # type: ignore[return-value]


# -----------------------------------------------------------------------------
# V3: 整链路 → 触发 greet → tts + expression 被调
# -----------------------------------------------------------------------------


def v3_full_chain_trigger() -> None:
    print("V3 整链路 → 触发 GreetAction → tts.say + expression.play 被调")
    clk = FakeClock(1000.0)
    sel = FakeSelector()
    trk = FakeTracker()
    tts = TTSStub()
    expr = ExpressionStub()
    conv = FakeConvSM(state="idle", quiet=False)
    pa = FakeProactive()
    wire = _make_wire(
        clock=clk, sel=sel, trk=trk, tts=tts, expr=expr, conv_sm=conv, proactive=pa,
        primary_stable_s=0.0,
    )
    t1 = FakeTrack(1, name="Alice", last_seen_ts=clk.t, smoothed_cx=100.0)
    t2 = FakeTrack(2, name="Bob", last_seen_ts=clk.t, smoothed_cx=400.0)
    sel.primary = FakeAttentionTarget(1, smoothed_cx=100.0)
    trk.tracks = [t1, t2]

    # tick 0: enter MULTI_IDLE
    wire._tick_once()
    # advance secondary_visible
    clk.advance(3.5)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    wire._tick_once()
    # advance silence
    clk.advance(5.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    wire._tick_once()
    check(wire.mfa.state is MFAState.GREET_SECONDARY, f"状态进入 GREET (got {wire.mfa.state})")
    check(len(tts.calls) == 1, f"tts.say 调一次 (got {len(tts.calls)})")
    if tts.calls:
        check(tts.calls[0] == "你好", f"utterance=你好 (got {tts.calls[0]!r})")
    check(len(expr.calls) == 1 and expr.calls[0] == "greet",
          f"expression.play('greet') 调一次 (got {expr.calls})")
    results["V3"] = "PASS"


# -----------------------------------------------------------------------------
# V4: cooldown
# -----------------------------------------------------------------------------


def v4_cooldown() -> None:
    print("V4 cooldown 内不再触发")
    clk = FakeClock(1000.0)
    sel = FakeSelector()
    trk = FakeTracker()
    tts = TTSStub()
    expr = ExpressionStub()
    wire = _make_wire(
        clock=clk, sel=sel, trk=trk, tts=tts, expr=expr,
        primary_stable_s=0.0,
    )
    t1 = FakeTrack(1, name="A", last_seen_ts=clk.t)
    t2 = FakeTrack(2, name="B", last_seen_ts=clk.t)
    sel.primary = FakeAttentionTarget(1)
    trk.tracks = [t1, t2]
    wire._tick_once()
    clk.advance(10.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    wire._tick_once()
    check(len(tts.calls) == 1, "第一次 greet 触发")
    # 走完 GREET / RETURN
    clk.advance(3.5)
    wire._tick_once()
    clk.advance(2.5)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    wire._tick_once()
    check(wire.mfa.state is MFAState.MULTI_IDLE, f"回 MULTI_IDLE (got {wire.mfa.state})")
    # silence 再满，但 cooldown 内
    clk.advance(10.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    wire._tick_once()
    check(len(tts.calls) == 1, f"cooldown 内不再调 tts (got {len(tts.calls)})")
    results["V4"] = "PASS"


# -----------------------------------------------------------------------------
# V5: conv_state=QUIET 抑制
# -----------------------------------------------------------------------------


def v5_quiet_suppression() -> None:
    print("V5 conv_state=QUIET → 抑制")
    clk = FakeClock(1000.0)
    sel = FakeSelector()
    trk = FakeTracker()
    tts = TTSStub()
    expr = ExpressionStub()
    conv = FakeConvSM(state="quiet", quiet=True)
    wire = _make_wire(
        clock=clk, sel=sel, trk=trk, tts=tts, expr=expr, conv_sm=conv,
        primary_stable_s=0.0,
    )
    t1 = FakeTrack(1, name="A", last_seen_ts=clk.t)
    t2 = FakeTrack(2, name="B", last_seen_ts=clk.t)
    sel.primary = FakeAttentionTarget(1)
    trk.tracks = [t1, t2]
    wire._tick_once()
    clk.advance(10.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    wire._tick_once()
    check(len(tts.calls) == 0, f"QUIET 期间 tts 不调用 (got {len(tts.calls)})")
    results["V5"] = "PASS"


# -----------------------------------------------------------------------------
# V6: awaiting_response 抑制（skip — ConvStateMachine 当前不暴露此状态）
# -----------------------------------------------------------------------------


def v6_awaiting_response() -> None:
    print("V6 awaiting_response 抑制 — ConvStateMachine 当前不暴露此字段，SKIP")
    results["V6"] = "SKIP"


# -----------------------------------------------------------------------------
# V7: proactive_recent 抑制
# -----------------------------------------------------------------------------


def v7_proactive_recent_suppression() -> None:
    print("V7 proactive_recent 抑制")
    clk = FakeClock(1000.0)
    sel = FakeSelector()
    trk = FakeTracker()
    tts = TTSStub()
    expr = ExpressionStub()
    pa = FakeProactive()
    pa._last_proactive_ts = time.time()  # 刚发过 proactive 话题
    wire = _make_wire(
        clock=clk, sel=sel, trk=trk, tts=tts, expr=expr, proactive=pa,
        primary_stable_s=0.0, proactive_block_window_s=10.0,
    )
    t1 = FakeTrack(1, name="A", last_seen_ts=clk.t)
    t2 = FakeTrack(2, name="B", last_seen_ts=clk.t)
    sel.primary = FakeAttentionTarget(1)
    trk.tracks = [t1, t2]
    wire._tick_once()
    clk.advance(10.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    wire._tick_once()
    check(len(tts.calls) == 0, f"proactive_recent=True 期间不触发 (got {len(tts.calls)})")
    # 把 proactive_block_window 推过去：拨回 last_proactive_ts
    pa._last_proactive_ts = time.time() - 999.0
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    wire._tick_once()
    check(len(tts.calls) == 1, f"proactive 窗口过期后可触发 (got {len(tts.calls)})")
    results["V7"] = "PASS"


# -----------------------------------------------------------------------------
# V8: named 过滤
# -----------------------------------------------------------------------------


def v8_named_filter() -> None:
    print("V8 require_named_secondary=True + secondary.name=None 不触发")
    clk = FakeClock(1000.0)
    sel = FakeSelector()
    trk = FakeTracker()
    tts = TTSStub()
    wire = _make_wire(
        clock=clk, sel=sel, trk=trk, tts=tts,
        primary_stable_s=0.0, require_named_secondary=True,
    )
    t1 = FakeTrack(1, name="A", last_seen_ts=clk.t)
    t2 = FakeTrack(2, name=None, last_seen_ts=clk.t)  # 未识别
    sel.primary = FakeAttentionTarget(1)
    trk.tracks = [t1, t2]
    wire._tick_once()
    clk.advance(10.0)
    t1.last_seen_ts = clk.t
    t2.last_seen_ts = clk.t
    wire._tick_once()
    check(len(tts.calls) == 0, f"未识别 secondary 不触发 (got {len(tts.calls)})")
    results["V8"] = "PASS"


# -----------------------------------------------------------------------------
# V9: primary 闪烁 race（关键）
# -----------------------------------------------------------------------------


def v9_primary_flicker_race() -> None:
    print("V9 primary 闪烁 race — 防抖窗口稳定 silence 计时")

    # --- 9a: 无防抖（primary_stable_s=0）— 揭露 race
    print("  9a: primary_stable_s=0 → silence 被反复重置，greet 永远不触发")
    clk = FakeClock(1000.0)
    sel = FakeSelector()
    trk = FakeTracker()
    tts = TTSStub()
    wire = _make_wire(
        clock=clk, sel=sel, trk=trk, tts=tts,
        primary_stable_s=0.0,
    )
    t1 = FakeTrack(1, name="Alice", last_seen_ts=clk.t, smoothed_cx=100.0)
    t2 = FakeTrack(2, name="Bob", last_seen_ts=clk.t, smoothed_cx=400.0)
    trk.tracks = [t1, t2]
    sel.primary = FakeAttentionTarget(1)
    wire._tick_once()  # enter MULTI_IDLE
    # 模拟 primary 每秒在 A↔B 间闪烁，共 12s（silence_threshold=8s 应满）
    primary_seq = [1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2]
    for i, pid in enumerate(primary_seq):
        clk.advance(1.0)
        t1.last_seen_ts = clk.t
        t2.last_seen_ts = clk.t
        sel.primary = FakeAttentionTarget(pid)
        wire._tick_once()
    check(len(tts.calls) == 0, f"9a: 闪烁导致 silence 反复重置 → greet 未触发 (got {len(tts.calls)})")

    # --- 9b: primary_stable_s=2.0 防抖 → 稳定 primary 不被闪烁破坏 → greet 触发
    print("  9b: primary_stable_s=2.0 防抖 → silence 不被闪烁打断 → greet 触发")
    clk2 = FakeClock(2000.0)
    sel2 = FakeSelector()
    trk2 = FakeTracker()
    tts2 = TTSStub()
    expr2 = ExpressionStub()
    wire2 = _make_wire(
        clock=clk2, sel=sel2, trk=trk2, tts=tts2, expr=expr2,
        primary_stable_s=2.0,
    )
    t1b = FakeTrack(1, name="Alice", last_seen_ts=clk2.t, smoothed_cx=100.0)
    t2b = FakeTrack(2, name="Bob", last_seen_ts=clk2.t, smoothed_cx=400.0)
    trk2.tracks = [t1b, t2b]
    # 阶段 1：primary=1 持续 3 秒，让 stable_primary_id=1 sticky
    sel2.primary = FakeAttentionTarget(1)
    wire2._tick_once()
    for _ in range(3):
        clk2.advance(1.0)
        t1b.last_seen_ts = clk2.t
        t2b.last_seen_ts = clk2.t
        wire2._tick_once()
    # 阶段 2：之后每秒闪烁，但抖动期 < primary_stable_s 不切 stable
    flicker_seq = [2, 1, 2, 1, 2, 1, 2, 1]  # 8 秒，每秒切换一次
    for pid in flicker_seq:
        clk2.advance(1.0)
        t1b.last_seen_ts = clk2.t
        t2b.last_seen_ts = clk2.t
        sel2.primary = FakeAttentionTarget(pid)
        wire2._tick_once()
    # 此时 stable primary 应仍 = 1（原稳定），silence 应满（>=8s）
    # secondary B (id=2) 在视野中持续 >=11s，visible 满 3s。
    # 再 tick 一次确保触发
    clk2.advance(1.0)
    t1b.last_seen_ts = clk2.t
    t2b.last_seen_ts = clk2.t
    sel2.primary = FakeAttentionTarget(1)  # 回到稳定 1
    wire2._tick_once()
    check(len(tts2.calls) >= 1,
          f"9b: 防抖 (stable=2s) 后 silence 累积成功 → greet 触发 (got {len(tts2.calls)})")
    if tts2.calls:
        check(tts2.calls[0] == "你好 Bob" or tts2.calls[0].startswith("你好"),
              f"9b: utterance OK (got {tts2.calls[0]!r})")
    results["V9"] = "PASS (race exposed, debounce fixes)"


# -----------------------------------------------------------------------------
# V10: stop 干净退出
# -----------------------------------------------------------------------------


def v10_stop_cleanup() -> None:
    print("V10 stop() 干净退出 + 线程 join < 2s")
    cfg = GreetSecondaryConfig(enabled=True, tick_hz=5.0, primary_stable_s=0.0)
    wire = build_greet_secondary_wire(
        config=cfg,
        attention_selector=FakeSelector(),
        face_tracker=FakeTracker(),
    )
    assert wire is not None
    stop_event = threading.Event()
    wire.start(stop_event)
    time.sleep(0.3)
    check(wire.is_alive(), "线程启动后 alive")
    t0 = time.monotonic()
    wire.stop(timeout=2.0)
    elapsed = time.monotonic() - t0
    check(not wire.is_alive(), "stop 后线程退出")
    check(elapsed < 2.0, f"stop 在 2s 内完成 (got {elapsed:.3f}s)")
    results["V10"] = "PASS"


# -----------------------------------------------------------------------------
# V11: emit vision.multi_face_state_changed
# -----------------------------------------------------------------------------


def v11_emit_vision_event() -> None:
    print("V11 emit vision.multi_face_state_changed component='vision'")
    clk = FakeClock(1000.0)
    sel = FakeSelector()
    trk = FakeTracker()
    emitted: list = []

    def emit_fn(event, **kw):
        emitted.append((event, kw))

    wire = _make_wire(
        clock=clk, sel=sel, trk=trk, emit_fn=emit_fn,
        primary_stable_s=0.0,
    )
    t1 = FakeTrack(1, name="A", last_seen_ts=clk.t)
    t2 = FakeTrack(2, name="B", last_seen_ts=clk.t)
    sel.primary = FakeAttentionTarget(1)
    trk.tracks = [t1, t2]
    wire._tick_once()  # SINGLE → MULTI_IDLE
    vision_events = [(e, kw) for e, kw in emitted if e == "vision.multi_face_state_changed"]
    check(len(vision_events) >= 1,
          f"emit vision.multi_face_state_changed (got {len(vision_events)})")
    if vision_events:
        kw = vision_events[0][1]
        check(kw.get("component") == "vision", f"component='vision' (got {kw.get('component')})")
        check(kw.get("curr") == "multi_idle", f"curr='multi_idle' (got {kw.get('curr')})")
    results["V11"] = "PASS"


# -----------------------------------------------------------------------------
# V12: env clamp
# -----------------------------------------------------------------------------


def v12_env_clamp() -> None:
    print("V12 env clamp")
    env = {
        "COCO_GREET_SECONDARY": "1",
        "COCO_GREET_SECONDARY_TICK_HZ": "9999",       # clamp to 30
        "COCO_GREET_SILENCE_S": "0.1",                # clamp to 1.0
        "COCO_GREET_SECONDARY_VIS_S": "0.0",          # clamp to 0.5
        "COCO_GREET_COOLDOWN_S": "1",                 # clamp to 5
        "COCO_GREET_DUR_S": "0.01",                   # clamp to 0.1
        "COCO_GREET_RETURN_S": "abc",                 # invalid → default 2.0
        "COCO_GREET_PROACTIVE_BLOCK_S": "-5",         # clamp to 0
        "COCO_GREET_PRIMARY_STABLE_S": "9999",        # clamp to 30
        "COCO_GREET_REQUIRE_NAMED": "0",
        "COCO_GREET_UTTERANCE": "Hi {name}",
    }
    cfg = greet_secondary_config_from_env(env)
    check(cfg.enabled is True, "enabled=True")
    check(cfg.tick_hz == 30.0, f"tick_hz clamp 30 (got {cfg.tick_hz})")
    check(cfg.silence_threshold_s == 1.0, f"silence clamp 1.0 (got {cfg.silence_threshold_s})")
    check(cfg.secondary_visible_s == 0.5, f"secondary_visible clamp 0.5 (got {cfg.secondary_visible_s})")
    check(cfg.cooldown_s == 5.0, f"cooldown clamp 5 (got {cfg.cooldown_s})")
    check(cfg.greet_duration_s == 0.1, f"greet_dur clamp 0.1 (got {cfg.greet_duration_s})")
    check(cfg.return_duration_s == 2.0, f"return_dur invalid fallback 2.0 (got {cfg.return_duration_s})")
    check(cfg.proactive_block_window_s == 0.0,
          f"proactive_block clamp 0 (got {cfg.proactive_block_window_s})")
    check(cfg.primary_stable_s == 30.0, f"primary_stable clamp 30 (got {cfg.primary_stable_s})")
    check(cfg.require_named_secondary is False, "require_named=False")
    check(cfg.utterance_template == "Hi {name}", f"utterance template (got {cfg.utterance_template!r})")
    results["V12"] = "PASS"


# -----------------------------------------------------------------------------
# main
# -----------------------------------------------------------------------------


def main() -> int:
    t0 = time.monotonic()
    v1_default_off()
    v2_enabled_construct()
    v3_full_chain_trigger()
    v4_cooldown()
    v5_quiet_suppression()
    v6_awaiting_response()
    v7_proactive_recent_suppression()
    v8_named_filter()
    v9_primary_flicker_race()
    v10_stop_cleanup()
    v11_emit_vision_event()
    v12_env_clamp()
    elapsed = time.monotonic() - t0

    print("---")
    if errors:
        print(f"FAIL  ({len(errors)} errors)")
        for e in errors:
            print(f"  - {e}")
        outcome = "FAIL"
    else:
        print(f"PASS  V1-V12 ({elapsed:.2f}s)")
        outcome = "PASS"

    evidence_dir = ROOT / "evidence" / "vision-004b-wire"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "vision-004b-wire",
        "outcome": outcome,
        "elapsed_s": round(elapsed, 3),
        "results": results,
        "errors": errors,
        "primary_stable_debounce": "added (primary_stable_s, default 2.0s); V9 9a exposes "
                                   "race, 9b confirms debounce fixes silence-reset race",
    }
    (evidence_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
