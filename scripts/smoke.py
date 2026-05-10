"""Smoke test for Coco / 可可.

跑法：
  uv run python scripts/smoke.py            # 仅 audio 子系统
  uv run python scripts/smoke.py --daemon   # 同时验 mockup-sim daemon

被 init.sh / init.ps1 调用，也可以独立跑。

平台：macOS / Linux / Windows（reachy-mini 本身仅 Lite SDK 跨平台；
真机硬件相关功能可能仍受限，但 smoke 仅验通路）。
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import platform
import subprocess
import sys
import time
from pathlib import Path


def print_env_baseline() -> None:
    """打印环境基线，方便在 claude-progress.md 中对照"已知通过"组合。"""
    print("==> 环境基线")
    print(f"  python: {platform.python_version()}")
    print(f"  platform: {platform.system()} {platform.release()} {platform.machine()}")
    for pkg in ("reachy-mini", "sounddevice", "numpy"):
        try:
            print(f"  {pkg}: {md.version(pkg)}")
        except md.PackageNotFoundError:
            print(f"  {pkg}: <not installed>")


def smoke_audio() -> None:
    """采 0.3s 麦克数据，确认 sounddevice 可读。"""
    print("==> Smoke: audio (sounddevice 0.3s)")
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as e:
        sys.exit(f"FAIL: import 失败 ({e})。先跑 uv sync？")

    try:
        rec = sd.rec(int(16000 * 0.3), samplerate=16000, channels=1, dtype="float32")
        sd.wait()
    except Exception as e:
        sys.exit(f"FAIL: 无法录音 ({e})。检查麦克权限。")

    if rec.size == 0:
        sys.exit("FAIL: 录到 0 个样本")

    rms = float(np.sqrt(np.mean(rec ** 2)))
    print(f"  ok: shape={rec.shape} rms={rms:.6f}  (rms 非零即视为通过)")


def smoke_asr() -> None:
    """跑 sense-voice wav 主验脚本一次；模型缺失则 WARN 跳过、不阻断。"""
    print("==> Smoke: ASR (sense-voice wav)")
    model_path = Path.home() / ".cache" / "coco" / "asr" / "sense-voice-2024-07-17" / "model.int8.onnx"
    if not model_path.exists():
        print("  WARN: ASR model not downloaded, skipped (run scripts/fetch_asr_models.sh)")
        return

    script = Path(__file__).resolve().parent / "verify_asr_wav.py"
    sys.stdout.flush()
    rc = subprocess.call([sys.executable, str(script)])
    if rc != 0:
        sys.exit(f"FAIL: ASR smoke 退出码 {rc}")


def smoke_tts() -> None:
    """加载 Kokoro TTS 并合成短句一次；模型缺失则 WARN 跳过、不阻断。

    只验合成路径不放音，避免开发期声卡噪声；播放路径由 verify_audio003_tts.py 覆盖。
    """
    print("==> Smoke: TTS (kokoro-zh)")
    model_path = Path.home() / ".cache" / "coco" / "tts" / "kokoro-int8-multi-lang-v1_1" / "model.int8.onnx"
    if not model_path.exists():
        print("  WARN: TTS model not downloaded, skipped (run scripts/fetch_tts_models.sh)")
        return

    try:
        from coco.tts import synthesize  # 延迟 import 避免 PortAudio init 干扰
    except ImportError as e:
        sys.exit(f"FAIL: import coco.tts 失败 ({e})")

    t0 = time.time()
    try:
        samples, sr = synthesize("你好")
    except Exception as e:
        sys.exit(f"FAIL: Kokoro 合成失败 ({e})")
    dt = time.time() - t0
    if samples.size == 0:
        sys.exit("FAIL: Kokoro 合成 0 个样本")
    print(f"  ok: samples={samples.size} sr={sr} dt={dt:.2f}s")


def smoke_vision() -> None:
    """对 single_face.jpg 调一次 face detect，断言 ≥1 张脸。

    vision-001 的快速健康检查；fixture 自带于 tests/fixtures/vision/，
    cv2 由 reachy-mini 间接安装；不引新依赖。
    """
    print("==> Smoke: vision (face detect on single_face.jpg)")
    fixture = (
        Path(__file__).resolve().parent.parent
        / "tests" / "fixtures" / "vision" / "single_face.jpg"
    )
    if not fixture.exists():
        sys.exit(f"FAIL: fixture not found: {fixture}")
    try:
        import cv2  # noqa: F401  # 仅探测可用性
        from coco.perception import FaceDetector
    except ImportError as e:
        sys.exit(f"FAIL: import 失败 ({e})")
    import cv2 as _cv2
    img = _cv2.imread(str(fixture))
    if img is None:
        sys.exit(f"FAIL: 无法加载 fixture: {fixture}")
    det = FaceDetector()
    boxes = det.detect(img)
    if len(boxes) < 1:
        sys.exit(f"FAIL: 期望 ≥1 张脸，实际 {len(boxes)}")
    print(f"  ok: detected {len(boxes)} face(s) in single_face.jpg")


def smoke_companion_vision() -> None:
    """跑 FaceTracker 2s 验后台线程通路 + 干净停。

    companion-002 的快速健康检查：起 tracker 2s（image fixture），断言 ≥1 次
    detect 命中、stop 干净。不连真 robot daemon，不带 IdleAnimator。
    """
    print("==> Smoke: companion-vision (FaceTracker 2s)")
    fixture = (
        Path(__file__).resolve().parent.parent
        / "tests" / "fixtures" / "vision" / "single_face.jpg"
    )
    if not fixture.exists():
        sys.exit(f"FAIL: fixture not found: {fixture}")
    try:
        from coco.perception import FaceTracker
    except ImportError as e:
        sys.exit(f"FAIL: import 失败 ({e})")
    import threading as _th
    stop = _th.Event()
    tracker = FaceTracker(stop, camera_spec=f"image:{fixture}", fps=5.0,
                          presence_window=3, presence_min_hits=2, absence_min_misses=2)
    tracker.start()
    time.sleep(2.0)
    stop.set()
    tracker.join(timeout=2.0)
    if tracker.is_alive():
        sys.exit("FAIL: FaceTracker 2s 后未退出")
    if tracker.stats.hit_count < 1:
        sys.exit(f"FAIL: 期望 ≥1 detect 命中，实际 {tracker.stats.hit_count}")
    print(f"  ok: detect={tracker.stats.detect_count} hit={tracker.stats.hit_count} "
          f"present={tracker.latest().present}")


def smoke_face_tracker() -> None:
    """vision-002: FaceTracker 跑 5 帧验 primary_track 稳定。

    用 single_face.jpg image fixture，喂 tracker 5+ 帧，断言：
      - 至少 1 个 active track
      - primary_track.hit_count >= 3 (单脸不应被换 track_id)
      - primary_switches <= 1 (首次无 → 设定算 1 次)
    """
    print("==> Smoke: face-tracker (primary stability 5 frames)")
    fixture = (
        Path(__file__).resolve().parent.parent
        / "tests" / "fixtures" / "vision" / "single_face.jpg"
    )
    if not fixture.exists():
        sys.exit(f"FAIL: fixture not found: {fixture}")
    try:
        from coco.perception import FaceTracker
    except ImportError as e:
        sys.exit(f"FAIL: import 失败 ({e})")
    import threading as _th
    stop = _th.Event()
    tracker = FaceTracker(
        stop, camera_spec=f"image:{fixture}", fps=10.0,
        presence_window=5, presence_min_hits=2, absence_min_misses=5,
        primary_strategy="area", primary_switch_min_frames=3,
    )
    tracker.start()
    time.sleep(1.5)
    stop.set()
    tracker.join(timeout=2.0)
    snap = tracker.latest()
    if snap.primary_track is None:
        sys.exit(f"FAIL: primary_track is None (detect={tracker.stats.detect_count} hit={tracker.stats.hit_count})")
    if snap.primary_track.hit_count < 3:
        sys.exit(f"FAIL: primary.hit_count={snap.primary_track.hit_count} < 3")
    if tracker.stats.primary_switches > 1:
        sys.exit(f"FAIL: primary_switches={tracker.stats.primary_switches} > 1 (单脸不应切)")
    print(f"  ok: tracks={len(snap.tracks)} primary_id={snap.primary_track.track_id} "
          f"hits={snap.primary_track.hit_count} switches={tracker.stats.primary_switches}")


def smoke_vad() -> None:
    """interact-003: VAD trigger 不依赖真麦，喂 fixture wav → 断 callback 触发 1 次。

    模型缺失则 WARN 跳过、不阻断（与 smoke_asr 一致）。
    """
    print("==> Smoke: VAD trigger (fixture wav)")
    silero_path = Path.home() / ".cache" / "coco" / "asr" / "silero_vad" / "silero_vad.onnx"
    if not silero_path.exists():
        print("  WARN: silero_vad.onnx not downloaded, skipped (run scripts/fetch_asr_models.sh)")
        return
    fix_wav = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "audio" / "zh-001-walk-park.wav"
    if not fix_wav.exists():
        print(f"  WARN: fixture missing {fix_wav}, skipped")
        return
    try:
        import numpy as np
        import scipy.io.wavfile as wavfile
        from coco.vad_trigger import VADConfig, VADTrigger
    except Exception as e:  # noqa: BLE001
        sys.exit(f"FAIL: VAD smoke import 失败 ({e})")
    sr, a = wavfile.read(str(fix_wav))
    audio_f32 = (a.astype(np.float32) / 32768.0) if a.dtype == np.int16 else a.astype(np.float32)
    if audio_f32.ndim > 1:
        audio_f32 = audio_f32.mean(axis=1)
    captured: list[int] = []
    trigger = VADTrigger(lambda audio, _sr: captured.append(1), config=VADConfig(cooldown_seconds=0.0))
    chunk = 1600
    for i in range(0, len(audio_f32), chunk):
        trigger.feed(audio_f32[i : i + chunk])
    trigger.flush()
    if len(captured) != 1:
        sys.exit(f"FAIL: VAD trigger 期望 1 次，实际 {len(captured)} 次")
    print(f"  ok: VAD trigger fired {len(captured)} time(s) on fixture")


def smoke_wake_word() -> None:
    """interact-005: KWS 命中 fixture wav '可可，今天天气真好' → wake 1 次。

    模型缺失则 WARN 跳过、不阻断（与 smoke_asr / smoke_vad 一致）。
    """
    print("==> Smoke: wake-word (KWS on wake_keke fixture)")
    kws_dir = (
        Path.home() / ".cache" / "coco" / "kws"
        / "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
    )
    if not (kws_dir / "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx").exists():
        print("  WARN: KWS model not downloaded, skipped (run scripts/fetch_kws_models.sh)")
        return
    fix_wav = (
        Path(__file__).resolve().parents[1]
        / "tests" / "fixtures" / "audio" / "wake_keke.wav"
    )
    if not fix_wav.exists():
        print(f"  WARN: fixture missing {fix_wav}, skipped")
        return
    try:
        import numpy as np
        import scipy.io.wavfile as wavfile
        from coco.wake_word import WakeConfig, WakeWordDetector
    except Exception as e:  # noqa: BLE001
        sys.exit(f"FAIL: wake-word smoke import 失败 ({e})")
    sr, a = wavfile.read(str(fix_wav))
    audio_f32 = (a.astype(np.float32) / 32768.0) if a.dtype == np.int16 else a.astype(np.float32)
    if audio_f32.ndim > 1:
        audio_f32 = audio_f32.mean(axis=1)
    hits: list[str] = []
    det = WakeWordDetector(on_wake=lambda t: hits.append(t), config=WakeConfig())
    chunk = 1600
    for i in range(0, len(audio_f32), chunk):
        det.feed(audio_f32[i : i + chunk])
    # tail to flush
    det.feed(np.zeros(int(0.5 * 16000), dtype=np.float32))
    if len(hits) < 1:
        sys.exit(f"FAIL: wake-word 期望 ≥1 次命中，实际 {len(hits)}")
    print(f"  ok: wake hits={hits}")


def smoke_power_state() -> None:
    """companion-003: PowerStateMachine FakeClock 推进 active→drowsy→sleep + 唤醒."""
    print("==> Smoke: power-state (companion-003)")
    import threading as _th
    from coco.power_state import (
        PowerConfig,
        PowerState,
        PowerStateMachine,
    )

    class _Clk:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            return self.t

    clk = _Clk()
    psm = PowerStateMachine(
        config=PowerConfig(drowsy_after=60.0, sleep_after=120.0),
        clock=clk,
    )
    sleep_calls = [0]
    wake_calls = [0]
    psm.on_enter_sleep = lambda m: sleep_calls.__setitem__(0, sleep_calls[0] + 1)
    psm.on_enter_active = lambda m, prev: wake_calls.__setitem__(0, wake_calls[0] + 1) if prev == PowerState.SLEEP else None

    clk.t = 70.0; psm.tick()
    if psm.current_state != PowerState.DROWSY:
        sys.exit(f"FAIL: 70s 后期望 DROWSY, got {psm.current_state}")
    clk.t = 200.0; psm.tick()
    if psm.current_state != PowerState.SLEEP:
        sys.exit(f"FAIL: 200s 后期望 SLEEP, got {psm.current_state}")
    if sleep_calls[0] != 1:
        sys.exit(f"FAIL: on_enter_sleep should fire once, got {sleep_calls[0]}")
    psm.record_interaction("smoke")
    if psm.current_state != PowerState.ACTIVE:
        sys.exit(f"FAIL: record_interaction 后期望 ACTIVE, got {psm.current_state}")
    if wake_calls[0] != 1:
        sys.exit(f"FAIL: on_enter_active(prev=SLEEP) should fire, got {wake_calls[0]}")
    print(f"  ok: ACTIVE→DROWSY@70s→SLEEP@200s→ACTIVE; sleep_cb={sleep_calls[0]} wake_cb={wake_calls[0]}")


def smoke_publish() -> None:
    """infra-publish-flow 最轻量自检：entry_points + class import。

    不跑 reachy_mini.apps.app check（含 ~30s 临时 venv 安装/卸载，太慢）；
    完整 dry-run 见 scripts/verify_publish.py。
    """
    print("==> Smoke: publish (entry_points + Coco class import)")
    import tomllib as _toml  # py3.11+

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with open(pyproject, "rb") as f:
        data = _toml.load(f)
    eps = (
        data.get("project", {})
        .get("entry-points", {})
        .get("reachy_mini_apps", {})
    )
    expected = "coco.main:Coco"
    if eps.get("coco") != expected:
        sys.exit(f"FAIL: entry-point 期望 coco={expected}，实际 {eps}")

    try:
        from coco.main import Coco  # noqa: F401
        from reachy_mini import ReachyMiniApp
    except Exception as e:  # noqa: BLE001
        sys.exit(f"FAIL: import coco.main:Coco 失败 ({e})")

    if not issubclass(Coco, ReachyMiniApp):
        sys.exit("FAIL: Coco 不继承 ReachyMiniApp")
    print("  ok: entry-point 正确 + Coco 可加载并继承 ReachyMiniApp")


def smoke_daemon() -> None:
    """起 mockup-sim daemon，用 ReachyMini 客户端 ping，关 daemon。

    要求：先关掉 Reachy Mini Control.app（或其他占用 Zenoh 7447 的进程）。
    """
    print("==> Smoke: robot mockup-sim daemon")
    log_path = Path("/tmp" if platform.system() != "Windows" else ".") / "coco-daemon.log"

    proc = subprocess.Popen(
        [sys.executable, "-m", "reachy_mini.daemon.app.main",
         "--mockup-sim", "--deactivate-audio"],
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
    )
    try:
        time.sleep(8)  # daemon 起来需要时间
        try:
            from reachy_mini import ReachyMini  # type: ignore
        except ImportError as e:
            sys.exit(f"FAIL: import reachy_mini 失败 ({e})")

        try:
            # 临时 workaround：no_media 绕开 Lite SDK 上 GStreamer/`gi` 缺失。
            # 产品目标含视频/媒体，待装 GStreamer 后撤回此豁免（见 robot-001 notes）。
            mini = ReachyMini(spawn_daemon=False, media_backend="no_media", timeout=10.0)
            print("  ok: Zenoh 通")
        except Exception as e:
            sys.exit(f"FAIL: ReachyMini 客户端连不上 ({e})。查看 {log_path}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description="Coco smoke test")
    parser.add_argument("--daemon", action="store_true",
                        help="同时验 mockup-sim daemon（需先关 Reachy Mini Control.app）")
    args = parser.parse_args()

    print_env_baseline()
    smoke_audio()
    smoke_asr()
    smoke_tts()
    smoke_vision()
    smoke_companion_vision()
    smoke_face_tracker()
    smoke_vad()
    smoke_wake_word()
    smoke_power_state()
    smoke_publish()
    if args.daemon:
        smoke_daemon()
    print()
    print("==> Smoke 通过。继续工作前请：1) 读 claude-progress.md 2) 读 feature_list.json")


if __name__ == "__main__":
    main()
