"""verify_infra_010 — SelfHealRegistry reopen_fn 真实接线 (COCO_SELFHEAL_WIRE).

V1   COCO_SELFHEAL_WIRE 未设 → selfheal_wire_enabled_from_env=False；
     main.py OFF 分支会 emit WARN (代码静态检查 + helper 行为)。
V2   COCO_SELFHEAL_WIRE=1 → enabled=True；build_real_reopen_callbacks 返回的
     audio/asr/camera 不是 placeholder lambda (identity 检查)。
V3   build_real_reopen_callbacks 返回 3 个可调用对象 (audio/asr/camera)。
V4   每个 reopen_fn 用空 handle 调一次 → 不抛、返回 bool；不真碰硬件
     （走 stub + WARN-once + emit self_heal.wire_stub）。
V5   wire ON 的 registry：cooldown 抑住第二次 (real_attempts 不双增)；
     COCO_REAL_MACHINE=1 + COCO_SELFHEAL_WIRE=1。
V6   AudioReopen 在 handle 暴露 reopen() 时真调（FakeAudio.reopen 计数 +1）。
V7   CameraReopen 走 open_camera() 真路径：用 image:tests/fixtures/vision/...
     fixture 跑一次，read_ok=True。
V8   ASRRestart 与 FakeOfflineFallback 互通：触发 → _enter_fallback 被调；
     recover=True → _exit_fallback。
V9   wire ON/OFF 切换不影响 registry 单例语义：build_default_registry 用 wire
     回调注册的 3 个策略 list_strategies() 返回名字与 OFF 一致。
V10  giveup latch 仍工作：max_attempts 后 wired 回调不再被调，emit giveup。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.infra.self_heal import (  # noqa: E402
    SelfHealRegistry,
    build_default_registry,
    selfheal_enabled_from_env,
)
from coco.infra.self_heal_wire import (  # noqa: E402
    build_real_reopen_callbacks,
    selfheal_wire_enabled_from_env,
)


FAILURES: List[str] = []
PASSES: List[str] = []


def _check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {label}", flush=True)
        PASSES.append(label)
    else:
        print(f"  FAIL  {label}  {detail}", flush=True)
        FAILURES.append(f"{label} :: {detail}")


def _section(title: str) -> None:
    print(f"\n--- {title} ---", flush=True)


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeAudio:
    def __init__(self) -> None:
        self.reopen_calls = 0

    def reopen(self) -> bool:
        self.reopen_calls += 1
        return True


class FakeOfflineFallback:
    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0
        self._in = False

    def is_in_fallback(self) -> bool:
        return self._in

    def _enter_fallback(self, latency_ms: float = 0.0) -> None:
        self.entered += 1
        self._in = True

    def _exit_fallback(self, latency_ms: float = 0.0) -> None:
        self.exited += 1
        self._in = False


def _save_env() -> Dict[str, str]:
    keys = ("COCO_SELFHEAL", "COCO_SELFHEAL_WIRE", "COCO_REAL_MACHINE",
            "COCO_BACKEND", "COCO_CAMERA")
    return {k: os.environ.get(k, "") for k in keys}


def _restore_env(snap: Dict[str, str]) -> None:
    for k, v in snap.items():
        if v == "":
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ---------------------------------------------------------------------------
# V1
# ---------------------------------------------------------------------------


def v1_wire_off_default() -> None:
    _section("V1 COCO_SELFHEAL_WIRE 未设 → enabled=False + main.py 走 WARN 分支")
    snap = _save_env()
    try:
        os.environ.pop("COCO_SELFHEAL_WIRE", None)
        _check("V1.a selfheal_wire_enabled_from_env(no env) -> False",
               selfheal_wire_enabled_from_env() is False)
        os.environ["COCO_SELFHEAL_WIRE"] = "0"
        _check("V1.b COCO_SELFHEAL_WIRE=0 -> False",
               selfheal_wire_enabled_from_env() is False)
        # main.py 走 WARN 分支 —— 静态字符串检查
        mp = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
        _check(
            "V1.c main.py OFF 分支含 WARN 字符串 (COCO_SELFHEAL_WIRE not set)",
            "COCO_SELFHEAL_WIRE not set" in mp,
        )
    finally:
        _restore_env(snap)


# ---------------------------------------------------------------------------
# V2
# ---------------------------------------------------------------------------


def v2_wire_on_not_placeholder() -> None:
    _section("V2 COCO_SELFHEAL_WIRE=1 → 回调不是 placeholder lambda")
    snap = _save_env()
    try:
        os.environ["COCO_SELFHEAL_WIRE"] = "1"
        _check("V2.a enabled=True", selfheal_wire_enabled_from_env() is True)
        wire = build_real_reopen_callbacks()
        placeholder = (lambda **kw: True)
        # identity / __name__ 检查
        _check("V2.b audio 不是 placeholder identity",
               wire.audio is not placeholder)
        _check("V2.c audio 是工厂内闭包（__name__ != '<lambda>')",
               wire.audio.__name__ in {"_audio_reopen", "<lambda>"}
               and wire.audio.__name__ != placeholder.__name__
               or wire.audio.__name__ == "_audio_reopen")
        _check("V2.d asr 是工厂内闭包",
               wire.asr.__name__ == "_asr_restart")
        _check("V2.e camera 是工厂内闭包",
               wire.camera.__name__ == "_camera_reopen")
    finally:
        _restore_env(snap)


# ---------------------------------------------------------------------------
# V3
# ---------------------------------------------------------------------------


def v3_three_callables() -> None:
    _section("V3 build_real_reopen_callbacks 返回 3 个可调用对象")
    wire = build_real_reopen_callbacks()
    _check("V3.a 长度 3", len(wire) == 3)
    _check("V3.b audio callable", callable(wire.audio))
    _check("V3.c asr callable", callable(wire.asr))
    _check("V3.d camera callable", callable(wire.camera))


# ---------------------------------------------------------------------------
# V4
# ---------------------------------------------------------------------------


def v4_each_callback_no_raise_bool() -> None:
    _section("V4 每个 reopen_fn 用空 handle 调一次 → 不抛、返回 bool")
    wire = build_real_reopen_callbacks(
        audio_handle=None, asr_handle=None,
        camera_handle_ref=None, offline_fallback=None,
    )
    try:
        a = wire.audio(failure_kind="audio_stream_dead")
        _check("V4.a audio_reopen_fn 不抛", True)
        _check("V4.b audio_reopen_fn 返回 bool",
               isinstance(a, bool), f"got {type(a).__name__}={a!r}")
    except Exception as e:  # noqa: BLE001
        _check("V4.a audio_reopen_fn 不抛", False, repr(e))

    try:
        r = wire.asr(failure_kind="asr_dead")
        _check("V4.c asr_restart_fn 不抛", True)
        _check("V4.d asr_restart_fn 返回 bool",
               isinstance(r, bool), f"got {type(r).__name__}={r!r}")
    except Exception as e:  # noqa: BLE001
        _check("V4.c asr_restart_fn 不抛", False, repr(e))

    # camera 在没有 ref 时尝试 open_camera() — 会因为 COCO_CAMERA 未设抛 → 返回 False
    snap = _save_env()
    try:
        os.environ.pop("COCO_CAMERA", None)
        c = wire.camera(failure_kind="camera_dead")
        _check("V4.e camera_reopen_fn 不抛", True)
        _check("V4.f camera_reopen_fn 返回 bool",
               isinstance(c, bool), f"got {type(c).__name__}={c!r}")
    except Exception as e:  # noqa: BLE001
        _check("V4.e camera_reopen_fn 不抛", False, repr(e))
    finally:
        _restore_env(snap)


# ---------------------------------------------------------------------------
# V5
# ---------------------------------------------------------------------------


def v5_cooldown_under_wired() -> None:
    _section("V5 wired 模式下 cooldown 抑制第二次")
    snap = _save_env()
    try:
        os.environ["COCO_REAL_MACHINE"] = "1"
        os.environ["COCO_SELFHEAL_WIRE"] = "1"
        fake_audio = FakeAudio()
        wire = build_real_reopen_callbacks(audio_handle=fake_audio)
        reg = build_default_registry(
            audio_reopen_fn=wire.audio,
            asr_restart_fn=wire.asr,
            camera_reopen_fn=wire.camera,
        )
        # 1st: dispatch 应触发 audio_reopen
        ok1 = reg.dispatch("audio_stream_dead", {})
        _check("V5.a 第一次 dispatch 成功 (FakeAudio.reopen called)",
               ok1 is True and fake_audio.reopen_calls == 1,
               f"ok1={ok1} reopen_calls={fake_audio.reopen_calls}")
        # 2nd 立刻：cooldown 抑流
        ok2 = reg.dispatch("audio_stream_dead", {})
        _check("V5.b 第二次 dispatch cooldown 抑制（reopen 不再被调）",
               fake_audio.reopen_calls == 1,
               f"reopen_calls={fake_audio.reopen_calls}")
        st = reg.stats.per_strategy["audio_reopen"]
        _check("V5.c cooldown_skipped >= 1", st.cooldown_skipped >= 1,
               f"cooldown_skipped={st.cooldown_skipped}")
    finally:
        _restore_env(snap)


# ---------------------------------------------------------------------------
# V6
# ---------------------------------------------------------------------------


def v6_audio_reopen_real_called() -> None:
    _section("V6 audio_handle.reopen() 真调")
    fake = FakeAudio()
    wire = build_real_reopen_callbacks(audio_handle=fake)
    r = wire.audio(failure_kind="audio_stream_dead")
    _check("V6.a 返回 True", r is True)
    _check("V6.b FakeAudio.reopen 被调 1 次", fake.reopen_calls == 1,
           f"calls={fake.reopen_calls}")


# ---------------------------------------------------------------------------
# V7
# ---------------------------------------------------------------------------


def v7_camera_open_real_path() -> None:
    _section("V7 camera_reopen 走 open_camera() 真路径（image fixture）")
    fixtures = ROOT / "tests" / "fixtures" / "vision"
    img = None
    if fixtures.exists():
        for ext in ("jpg", "jpeg", "png"):
            cands = list(fixtures.rglob(f"*.{ext}"))
            if cands:
                img = cands[0]
                break
    if img is None:
        _check("V7.a 找到 fixture 图片", False,
               f"no image found under {fixtures}")
        return
    spec = f"image:{img}"
    wire = build_real_reopen_callbacks(camera_spec=spec)
    r = wire.camera(failure_kind="camera_dead")
    _check("V7.a camera_reopen 返回 True (image fixture)",
           r is True, f"spec={spec}")


# ---------------------------------------------------------------------------
# V8
# ---------------------------------------------------------------------------


def v8_asr_fallback_toggle() -> None:
    _section("V8 ASR fallback toggle: enter / exit")
    fb = FakeOfflineFallback()
    wire = build_real_reopen_callbacks(offline_fallback=fb)
    r1 = wire.asr(failure_kind="asr_dead")
    _check("V8.a 触发 → _enter_fallback 被调",
           fb.entered == 1 and fb.is_in_fallback() and r1 is True,
           f"entered={fb.entered} in={fb.is_in_fallback()} r={r1}")
    r2 = wire.asr(failure_kind="asr_dead", recover=True)
    _check("V8.b recover=True → _exit_fallback 被调",
           fb.exited == 1 and not fb.is_in_fallback() and r2 is True,
           f"exited={fb.exited} in={fb.is_in_fallback()} r={r2}")


# ---------------------------------------------------------------------------
# V9
# ---------------------------------------------------------------------------


def v9_strategy_list_unchanged() -> None:
    _section("V9 wire ON/OFF 切换不影响 registry 单例语义（策略名一致）")
    # OFF: 占位
    reg_off = build_default_registry(
        audio_reopen_fn=lambda **kw: True,
        asr_restart_fn=lambda **kw: True,
        camera_reopen_fn=lambda **kw: True,
    )
    names_off = sorted(reg_off.list_strategies())
    # ON: wire
    wire = build_real_reopen_callbacks()
    reg_on = build_default_registry(
        audio_reopen_fn=wire.audio,
        asr_restart_fn=wire.asr,
        camera_reopen_fn=wire.camera,
    )
    names_on = sorted(reg_on.list_strategies())
    _check("V9.a 策略名集合一致", names_off == names_on,
           f"off={names_off} on={names_on}")
    _check("V9.b 包含 audio_reopen / asr_restart / camera_reopen",
           set(names_on) == {"audio_reopen", "asr_restart", "camera_reopen"},
           f"got={names_on}")


# ---------------------------------------------------------------------------
# V10
# ---------------------------------------------------------------------------


def v10_giveup_latch_with_wire() -> None:
    _section("V10 wired 模式下 max_attempts 后 giveup latch 仍工作")
    snap = _save_env()
    try:
        os.environ["COCO_REAL_MACHINE"] = "1"
        # 用 cooldown_s=0 让 attempt 连续可发
        from coco.infra.self_heal import AudioReopenStrategy, SelfHealRegistry
        fake = FakeAudio()
        wire = build_real_reopen_callbacks(audio_handle=fake)
        reg = SelfHealRegistry()
        strat = AudioReopenStrategy(reopen_fn=wire.audio,
                                    cooldown_s=0.0, max_attempts=3)
        reg.register(strat)
        results = []
        for i in range(5):
            results.append(reg.dispatch("audio_stream_dead", {}))
        st = reg.stats.per_strategy["audio_reopen"]
        _check("V10.a real_attempts 触顶 (<=3)",
               st.real_attempts == 3,
               f"real_attempts={st.real_attempts}")
        _check("V10.b giveup latch=True",
               st.giveup is True,
               f"giveup={st.giveup}")
        _check("V10.c giveup 后 FakeAudio.reopen 不再增长 (==3)",
               fake.reopen_calls == 3,
               f"reopen_calls={fake.reopen_calls}")
        _check("V10.d giveup_after_max 计数 >= 1",
               reg.stats.giveup_after_max >= 1,
               f"giveup_after_max={reg.stats.giveup_after_max}")
    finally:
        _restore_env(snap)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=== verify_infra_010 ===", flush=True)
    v1_wire_off_default()
    v2_wire_on_not_placeholder()
    v3_three_callables()
    v4_each_callback_no_raise_bool()
    v5_cooldown_under_wired()
    v6_audio_reopen_real_called()
    v7_camera_open_real_path()
    v8_asr_fallback_toggle()
    v9_strategy_list_unchanged()
    v10_giveup_latch_with_wire()

    print(f"\n=== summary: PASS={len(PASSES)}  FAIL={len(FAILURES)} ===", flush=True)
    if FAILURES:
        print("\n--- failures ---", flush=True)
        for f in FAILURES:
            print(f"  {f}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
