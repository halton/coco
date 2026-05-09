"""Coco ReachyMiniApp 入口。

双模式：
- 开发：`python -m coco.main`
- UAT/发布：通过 entry-point 被 Reachy Mini Control.app 发现并启动

audio 解耦：run() 内只用 sounddevice 采麦，不调用 reachy_mini.media。
"""

from __future__ import annotations

import signal
import threading
import time
from types import FrameType

import numpy as np
import sounddevice as sd
from reachy_mini import ReachyMini, ReachyMiniApp


SAMPLE_RATE = 16000
BLOCK_SECONDS = 0.5


class Coco(ReachyMiniApp):
    # 不需要自定义 settings 页
    custom_app_url: str | None = None
    # audio 解耦：macOS 跳过 GStreamer/camera 初始化；Coco 的 audio 走 sounddevice 直连
    # 类型注解需匹配父类 ReachyMiniApp.request_media_backend: str | None
    request_media_backend: str | None = "no_media"

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        block_frames = int(SAMPLE_RATE * BLOCK_SECONDS)

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
                # 占位：后续 feature（audio-002 ASR / companion-001 idle 动作）在此扩展。
                # 当前仅打印 rms 证明采集链路活着，且不触碰 reachy_mini.media。
                print(f"[coco] rms={rms:.4f}", flush=True)
                # 让出循环，给 stop_event 检查机会。
                time.sleep(0.05)


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
