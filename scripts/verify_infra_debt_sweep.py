"""verify_infra_debt_sweep — 验 phase-3 infra-debt-sweep 的几个 fix。

不依赖真硬件，只跑代码/属性/反射层校验。把"该有"的 hook 都点一遍：

- M2  vad_trigger.feed → callback 在 self._lock 之外被调用（不死锁）
- M3  start_microphone 幂等：连调两次只起一份，第二次被 mic_lock 拦截 + warning
- audio-002 M1：fixture 路径不存在时 _run_fixture_asr_once 不 raise，仅 print skip
- init.ps1 存在且包含核心步骤
- runbook 增强段（"6.6 infra-debt-sweep 注意事项"）存在

期望最后一行：``==> PASS: infra-debt-sweep verification 全部通过``。
任何 FAIL 不要切 passing。
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n--- {title} ---", flush=True)


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", flush=True)
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok: {msg}", flush=True)


# ---------------------------------------------------------------------------
# M2: feed → callback 在锁外
# ---------------------------------------------------------------------------


def check_m2_callback_outside_lock() -> None:
    _section("M2 — VADTrigger.feed callback 在 self._lock 之外执行")

    from coco.vad_trigger import VADTrigger, VADConfig

    captured: dict[str, object] = {}

    cfg = VADConfig()

    def on_utterance(audio_int16, sr):  # type: ignore[no-untyped-def]
        # callback 真正被调用时反向尝试拿 trigger._lock；如果 feed 还持锁就死锁
        # 这里用 acquire(timeout) 来证明：
        acquired = trigger._lock.acquire(timeout=1.0)
        captured["acquired_inside_callback"] = acquired
        if acquired:
            trigger._lock.release()
        # 同时直接调 reset_buffer / stop（它们内部 with self._lock）
        # 在锁外 callback 应可正常调用
        try:
            trigger.reset_buffer()
            captured["reset_buffer_ok"] = True
        except Exception as exc:  # noqa: BLE001
            captured["reset_buffer_ok"] = False
            captured["reset_exc"] = repr(exc)

    trigger = VADTrigger(on_utterance=on_utterance, config=cfg)

    # 直接调内部 helper：构造一个假 segment 喂给 _fire_segments，模拟 feed pop 后的链路
    fake_seg = (np.ones(int(cfg.sample_rate * (cfg.min_speech_seconds + 0.1)), dtype=np.float32)
                * 0.5)
    trigger._fire_segments([fake_seg])

    if not captured:
        _fail("on_utterance 未被触发，_fire_segments 路径异常")
    if not captured.get("acquired_inside_callback"):
        _fail("callback 内拿不到 self._lock —— 说明 feed 路径仍在持锁调 callback (M2 失败)")
    if not captured.get("reset_buffer_ok"):
        _fail(f"callback 内调 reset_buffer 失败：{captured.get('reset_exc')}")

    _ok("callback 内可拿 self._lock + 调 reset_buffer，未死锁")


# ---------------------------------------------------------------------------
# M3: start_microphone 幂等
# ---------------------------------------------------------------------------


def check_m3_start_microphone_idempotent() -> None:
    _section("M3 — start_microphone 幂等（mic_lock 守卫，重复启动只 warning）")

    from coco.vad_trigger import VADTrigger

    trigger = VADTrigger(on_utterance=lambda *a, **k: None)

    # 不真起 sounddevice：我们 monkeypatch _mic_loop 让它不消费麦克
    barrier = threading.Event()

    def fake_mic_loop(block_seconds: float) -> None:  # noqa: ARG001
        barrier.set()
        # 等 stop_event 退出
        while not trigger._stop_event.wait(0.05):
            pass

    trigger._mic_loop = fake_mic_loop  # type: ignore[assignment]

    # 收 warning
    log = logging.getLogger("coco.vad_trigger")
    records: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
            records.append(record)

    h = _Handler(level=logging.WARNING)
    log.addHandler(h)
    old_level = log.level
    log.setLevel(logging.WARNING)

    try:
        trigger.start_microphone(block_seconds=0.1)
        if not barrier.wait(2.0):
            _fail("第一次 start_microphone 后 fake_mic_loop 未启动")

        first_thread = trigger._mic_thread
        # 再调两次，应该被拦
        trigger.start_microphone(block_seconds=0.1)
        trigger.start_microphone(block_seconds=0.1)

        if trigger._mic_thread is not first_thread:
            _fail("重复 start_microphone 起了新的线程 —— 幂等失败")

        warning_msgs = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
        if not any("already running" in m for m in warning_msgs):
            _fail(
                f"重复 start_microphone 没看到 'already running' warning："
                f"{warning_msgs!r}"
            )
        _ok(f"重复 start 被拦 + 看到 warning（共 {sum('already running' in m for m in warning_msgs)} 条）")

    finally:
        log.removeHandler(h)
        log.setLevel(old_level)
        trigger.stop(timeout=2.0)


# ---------------------------------------------------------------------------
# audio-002 M1: fixture 缺失 publish 模式不 raise
# ---------------------------------------------------------------------------


def check_audio002_m1_fixture_missing() -> None:
    _section("audio-002 M1 — fixture 缺失时 _run_fixture_asr_once 不 raise")

    from coco.main import _run_fixture_asr_once

    bogus = ROOT / "tests" / "fixtures" / "audio" / "_definitely_missing_zh.wav"
    if bogus.exists():
        _fail(f"测试 fixture 路径意外存在：{bogus}（请改测试 stub 名）")

    try:
        _run_fixture_asr_once(bogus)  # 期望不抛
    except Exception as exc:  # noqa: BLE001
        _fail(f"fixture 缺失但函数 raise：{exc!r}")
    _ok("fixture 缺失时 _run_fixture_asr_once 静默 skip，不 raise")


# ---------------------------------------------------------------------------
# init.ps1 存在 + 关键步骤
# ---------------------------------------------------------------------------


def check_init_ps1() -> None:
    _section("init.ps1 — Windows 入口存在并含核心步骤")

    p = ROOT / "init.ps1"
    if not p.exists():
        _fail(f"init.ps1 不存在：{p}")
    text = p.read_text(encoding="utf-8")
    needles = ["uv sync", "scripts/smoke.py"]
    missing = [n for n in needles if n not in text]
    if missing:
        _fail(f"init.ps1 缺关键步骤：{missing}")
    if "Windows" not in text and "windows" not in text:
        _fail("init.ps1 缺 Windows / UAT 注释，看不出与 init.sh 的边界")
    _ok("init.ps1 含 uv sync + smoke + Windows 边界注释")


# ---------------------------------------------------------------------------
# Runbook 增强：6.6 infra-debt-sweep 注意事项
# ---------------------------------------------------------------------------


def check_runbook_enhanced() -> None:
    _section("docs/uat-runbook.md — 含 infra-debt-sweep 小节")

    p = ROOT / "docs" / "uat-runbook.md"
    if not p.exists():
        _fail(f"runbook 不存在：{p}")
    text = p.read_text(encoding="utf-8")
    needles = ["infra-debt-sweep", "init.ps1"]
    missing = [n for n in needles if n not in text]
    if missing:
        _fail(f"runbook 缺关键提示：{missing}")
    _ok("runbook 含 infra-debt-sweep 段 + init.ps1 提示")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"==> Repo: {ROOT}", flush=True)
    check_m2_callback_outside_lock()
    check_m3_start_microphone_idempotent()
    check_audio002_m1_fixture_missing()
    check_init_ps1()
    check_runbook_enhanced()
    print("\n==> PASS: infra-debt-sweep verification 全部通过", flush=True)


if __name__ == "__main__":
    main()
