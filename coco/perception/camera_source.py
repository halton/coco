"""CameraSource 抽象与三档实现。

设计目标：业务层（perception / interact / companion）不直接依赖 cv2.VideoCapture。
通过 COCO_CAMERA 环境变量切换三档：

- ``image:<path>``      A 档：单图无限循环出帧，用于 sim 下"画面静止"语义测试
- ``video:<path>``      B/C 档：mp4 文件按 fps 循环回放，用于"有动作"语义测试
- ``usb:<idx>``         真机：cv2.VideoCapture(idx) 包装，默认 ``usb:0``

接口契约（Protocol，鸭子类型）：

- ``read() -> tuple[bool, np.ndarray | None]``：模仿 cv2.VideoCapture.read()
- ``release() -> None``：释放底层资源；幂等
- 上下文管理器友好（``__enter__`` / ``__exit__`` 可选）

帧形状统一为 BGR ``np.ndarray`` shape=(H, W, 3) dtype=uint8（OpenCV 默认），
保证业务层切换源码 0 改动。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, Tuple

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# 协议
# ---------------------------------------------------------------------------


class CameraSource(Protocol):
    """统一相机源接口。read/release 与 cv2.VideoCapture 兼容。"""

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:  # pragma: no cover - protocol
        ...

    def release(self) -> None:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# A 档：单图循环
# ---------------------------------------------------------------------------


class ImageLoopSource:
    """把一张静态图片当作"恒定画面"的相机。

    每次 ``read()`` 返回同一帧的拷贝，可选按 ``fps`` 阻塞节流，
    模拟真实摄像头的恒定帧率行为。
    """

    def __init__(self, path: str | Path, fps: float = 30.0) -> None:
        path = str(path)
        if not Path(path).exists():
            raise FileNotFoundError(f"ImageLoopSource: image not found: {path}")
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"ImageLoopSource: cv2 failed to decode: {path}")
        self._frame = img  # BGR uint8
        self._fps = max(0.1, float(fps))
        self._period = 1.0 / self._fps
        self._last_t: Optional[float] = None
        self._closed = False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._closed:
            return False, None
        # 节流：与上一帧间隔不足 _period 则补睡
        now = time.monotonic()
        if self._last_t is not None:
            dt = now - self._last_t
            if dt < self._period:
                time.sleep(self._period - dt)
        self._last_t = time.monotonic()
        return True, self._frame.copy()

    def release(self) -> None:
        self._closed = True

    def __enter__(self) -> "ImageLoopSource":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


# ---------------------------------------------------------------------------
# B/C 档：视频文件循环
# ---------------------------------------------------------------------------


class VideoFileSource:
    """循环回放本地 mp4/mov 等视频文件。

    - 自动按视频自身 fps 节流（也可被 ``fps`` 覆盖）
    - 到末尾后 seek 回 0 继续，永不返回 (False, None)（除非已 release 或解码失败）
    """

    def __init__(self, path: str | Path, fps: Optional[float] = None) -> None:
        path = str(path)
        if not Path(path).exists():
            raise FileNotFoundError(f"VideoFileSource: video not found: {path}")
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise ValueError(f"VideoFileSource: cv2 failed to open: {path}")
        self._cap = cap
        self._path = path
        native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        if not native_fps or native_fps != native_fps:  # NaN guard
            native_fps = 30.0
        self._fps = float(fps) if fps else float(native_fps)
        self._period = 1.0 / max(0.1, self._fps)
        self._last_t: Optional[float] = None
        self._closed = False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._closed:
            return False, None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            # 末尾 → seek 回 0 重试一次
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._cap.read()
            if not ok or frame is None:
                return False, None
        # 节流
        now = time.monotonic()
        if self._last_t is not None:
            dt = now - self._last_t
            if dt < self._period:
                time.sleep(self._period - dt)
        self._last_t = time.monotonic()
        return True, frame

    def release(self) -> None:
        if not self._closed:
            try:
                self._cap.release()
            except Exception:
                pass
            self._closed = True

    def __enter__(self) -> "VideoFileSource":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


# ---------------------------------------------------------------------------
# 真机：USB 摄像头
# ---------------------------------------------------------------------------


class UsbCameraSource:
    """``cv2.VideoCapture(idx)`` 的薄封装。

    注意：与 ImageLoopSource / VideoFileSource 不同，本类**不在 Python 层节流**——
    依赖底层驱动 / V4L2 / AVFoundation 自带的帧率控制。如果业务层在 tight loop
    里调用 read()，CPU 占用取决于驱动行为；建议业务层自己加 sleep(1/fps_target)。
    """

    def __init__(self, index: int = 0, fps: Optional[float] = None) -> None:
        cap = cv2.VideoCapture(int(index))
        if not cap.isOpened():
            try:
                cap.release()
            except Exception:
                pass
            raise RuntimeError(
                f"UsbCameraSource: cv2.VideoCapture({index}) 打不开（设备占用 / 未授权 / 不存在）"
            )
        self._cap = cap
        self._index = int(index)
        if fps:
            cap.set(cv2.CAP_PROP_FPS, float(fps))
        self._closed = False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._closed:
            return False, None
        ok, frame = self._cap.read()
        return bool(ok), frame if ok else None

    def release(self) -> None:
        if not self._closed:
            try:
                self._cap.release()
            except Exception:
                pass
            self._closed = True

    def __enter__(self) -> "UsbCameraSource":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CameraSpec:
    """COCO_CAMERA 解析结果。"""

    kind: str  # "image" | "video" | "usb"
    target: str  # path 或 index 字符串

    @property
    def index(self) -> int:
        return int(self.target)


def parse_camera_env(spec: Optional[str]) -> CameraSpec:
    """解析 COCO_CAMERA 字符串。

    支持：
        image:/path/to.jpg
        video:/path/to.mp4
        usb:0           （默认）
        None 或空串     → usb:0
    """

    if not spec:
        return CameraSpec(kind="usb", target="0")
    spec = spec.strip()
    if ":" not in spec:
        raise ValueError(f"COCO_CAMERA 格式错误，需 'kind:target'：{spec!r}")
    kind, _, target = spec.partition(":")
    kind = kind.strip().lower()
    target = target.strip()
    if kind not in {"image", "video", "usb"}:
        raise ValueError(f"COCO_CAMERA kind 必须是 image/video/usb：{kind!r}")
    if not target:
        raise ValueError(f"COCO_CAMERA target 不能为空：{spec!r}")
    if kind == "usb":
        try:
            int(target)
        except ValueError as e:
            raise ValueError(f"COCO_CAMERA usb:<idx> idx 必须是整数：{target!r}") from e
    return CameraSpec(kind=kind, target=target)


def open_camera(spec: Optional[str] = None, *, fps: Optional[float] = None) -> CameraSource:
    """根据 spec（或 ``COCO_CAMERA`` 环境变量）打开对应相机源。"""

    if spec is None:
        spec = os.environ.get("COCO_CAMERA")
    parsed = parse_camera_env(spec)
    if parsed.kind == "image":
        return ImageLoopSource(parsed.target, fps=fps if fps else 30.0)
    if parsed.kind == "video":
        return VideoFileSource(parsed.target, fps=fps)
    # usb
    return UsbCameraSource(index=parsed.index, fps=fps)


__all__ = [
    "CameraSource",
    "CameraSpec",
    "ImageLoopSource",
    "VideoFileSource",
    "UsbCameraSource",
    "parse_camera_env",
    "open_camera",
]
