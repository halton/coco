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
    if args.daemon:
        smoke_daemon()
    print()
    print("==> Smoke 通过。继续工作前请：1) 读 claude-progress.md 2) 读 feature_list.json")


if __name__ == "__main__":
    main()
