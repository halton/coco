"""Coco ReachyMiniApp 入口。

双模式：
- 开发：`python -m coco.main`
- UAT/发布：通过 entry-point 被 Reachy Mini Control.app 发现并启动

audio 解耦：run() 内只用 sounddevice 采麦，不调用 reachy_mini.media。
companion-001：run() 内挂 IdleAnimator 后台线程做 idle 微动 + 偶尔环顾。
"""

from __future__ import annotations

import signal
import threading
import time
from pathlib import Path
from types import FrameType

import numpy as np
import sounddevice as sd
from reachy_mini import ReachyMini, ReachyMiniApp

from coco.asr import transcribe_wav
from coco.idle import IdleAnimator, IdleConfig


SAMPLE_RATE = 16000
BLOCK_SECONDS = 0.5

# audio-002 V6：主循环启动时跑一次 fixture 转写，证明 ASR 在 ReachyMiniApp
# 主进程内可用且不阻塞心跳。后台线程保证 mic loop / stop_event 检查不被卡。
ASR_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "audio" / "zh-001-walk-park.wav"


def _run_fixture_asr_once(fixture_path: Path) -> None:
    """后台线程：跑一次 transcribe_wav，结果打到 stdout。失败只 print，不抛回主线程。"""
    try:
        text = transcribe_wav(fixture_path)
        print(f"[coco][asr] fixture={fixture_path.name} text={text!r}", flush=True)
    except Exception as exc:  # noqa: BLE001 — 后台线程兜底，避免炸主循环
        print(f"[coco][asr] fixture transcribe failed: {exc!r}", flush=True)


class Coco(ReachyMiniApp):
    # 不需要自定义 settings 页
    custom_app_url: str | None = None
    # audio 解耦：macOS 跳过 GStreamer/camera 初始化；Coco 的 audio 走 sounddevice 直连
    # 类型注解需匹配父类 ReachyMiniApp.request_media_backend: str | None
    request_media_backend: str | None = "no_media"

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        block_frames = int(SAMPLE_RATE * BLOCK_SECONDS)

        # audio-002 V6：把 ASR 一次性 fixture 验证放后台线程，避免阻塞心跳/stop_event
        asr_thread = threading.Thread(
            target=_run_fixture_asr_once,
            args=(ASR_FIXTURE_PATH,),
            name="coco-asr-fixture",
            daemon=True,
        )
        asr_thread.start()

        # companion-001：起 idle 动画后台线程。共用 stop_event；动作经 robot-002 的安全幅度封装。
        # 失败/异常只 log，不影响 mic loop 或主退出。
        idle_animator: IdleAnimator | None = None
        try:
            try:
                reachy_mini.wake_up()
            except Exception as exc:  # noqa: BLE001
                print(f"[coco][idle] wake_up failed (continuing without): {exc!r}", flush=True)
            idle_animator = IdleAnimator(reachy_mini, stop_event, config=IdleConfig())
            idle_animator.start()
            print("[coco][idle] IdleAnimator started", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[coco][idle] start failed: {exc!r}", flush=True)

        try:
            # sounddevice 直连本机麦：与 daemon 的 audio backend / media 无耦合。
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=block_frames,
            ) as mic:
                while not stop_event.is_set():
                    data, _overflow = mic.read(block_frames)
                    rms = float(np.sqrt(np.mean(np.square(data))))
                    # 占位：后续 feature（interact-001 push-to-talk → ASR → 中文回应）在此扩展。
                    print(f"[coco] rms={rms:.4f}", flush=True)
                    # 让出循环，给 stop_event 检查机会。
                    time.sleep(0.05)
        finally:
            # 确保 idle 线程退出干净；stop_event 已被外部或本循环 set
            if idle_animator is not None:
                stop_event.set()
                idle_animator.join(timeout=2.0)
                if idle_animator.is_alive():
                    print("[coco][idle] WARN: animator did not stop within 2s", flush=True)
                else:
                    print(f"[coco][idle] stopped stats={idle_animator.stats}", flush=True)


def main() -> None:
    app = Coco()

    def _graceful_stop(signum: int, _frame: FrameType | None) -> None:
        # 让 wrapped_run 内的 stop_event.wait() 返回，run() 循环看到 stop_event 后退出
        app.logger.info(f"Received signal {signum}, stopping gracefully")
        app.stop()

    signal.signal(signal.SIGINT, _graceful_stop)
    signal.signal(signal.SIGTERM, _graceful_stop)

    app.wrapped_run()


if __name__ == "__main__":
    main()
