"""coco.perception.face_detect — 人脸检测（vision-001）.

设计：
- FaceDetector 包装 cv2 haar cascade，detect(frame) -> list[FaceBox]
- 不下大模型；haar cascade 自带于 cv2.data.haarcascades
- 参数可调（scale_factor / min_neighbors / min_size），默认值在 infra-vision-source
  程序合成 fixture 与真实人脸上都通过；真机阶段如需要更鲁棒可换 mediapipe，
  但优先级低（本 feature notes 已声明）

线程模型：
- detect() 是同步的；调用方在自己的线程里跑（companion-002 / vision-002 等）
- CascadeClassifier 不保证 thread-safe，每个线程各持有自己的 FaceDetector 实例
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FaceBox:
    """检测到的人脸框，左上角 + 宽高 + 置信度。

    haar cascade 不直接输出置信度；本字段为 1.0（命中即算）。
    未来若换 mediapipe / DNN 检测器，score 可携带 backend 自带置信度。
    """

    x: int
    y: int
    w: int
    h: int
    score: float = 1.0

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2

    def as_xywh(self) -> tuple:
        return (self.x, self.y, self.w, self.h)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


_DEFAULT_SCALE_FACTOR = 1.1
_DEFAULT_MIN_NEIGHBORS = 3
_DEFAULT_MIN_SIZE = (30, 30)


def _default_cascade_path() -> str:
    return os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")


class FaceDetector:
    """OpenCV haar cascade 人脸检测器。

    用法：
        det = FaceDetector()
        boxes = det.detect(frame_bgr)   # list[FaceBox]

    参数：
        cascade_path: xml 路径；默认走 cv2.data.haarcascades 自带 frontal_face
        scale_factor / min_neighbors / min_size: 透传 cv2.detectMultiScale
    """

    def __init__(
        self,
        cascade_path: Optional[str] = None,
        *,
        scale_factor: float = _DEFAULT_SCALE_FACTOR,
        min_neighbors: int = _DEFAULT_MIN_NEIGHBORS,
        min_size: tuple = _DEFAULT_MIN_SIZE,
    ) -> None:
        path = cascade_path or _default_cascade_path()
        if not os.path.exists(path):
            raise FileNotFoundError(f"haar cascade not found: {path}")
        self._cascade = cv2.CascadeClassifier(path)
        if self._cascade.empty():
            raise RuntimeError(f"failed to load haar cascade: {path}")
        self.scale_factor = float(scale_factor)
        self.min_neighbors = int(min_neighbors)
        self.min_size = (int(min_size[0]), int(min_size[1]))

    def detect(self, frame_bgr: np.ndarray) -> List[FaceBox]:
        """检测一帧 BGR 图像中的人脸框。

        - frame_bgr: shape=(H, W, 3) dtype=uint8 BGR (cv2 默认)
        - 返回: list[FaceBox]，可能为空
        - 不抛异常（保护调用线程）；输入非法时 log + 返回 []
        """
        if frame_bgr is None:
            return []
        if not isinstance(frame_bgr, np.ndarray):
            log.warning("[face] non-ndarray input, type=%s", type(frame_bgr).__name__)
            return []
        if frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
            log.warning("[face] expected (H,W,3) BGR, got shape=%s", frame_bgr.shape)
            return []
        try:
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            raw = self._cascade.detectMultiScale(
                gray,
                scaleFactor=self.scale_factor,
                minNeighbors=self.min_neighbors,
                minSize=self.min_size,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[face] detect failed: %s: %s", type(e).__name__, e)
            return []
        if raw is None or len(raw) == 0:
            return []
        out: List[FaceBox] = []
        for (x, y, w, h) in raw:
            out.append(FaceBox(x=int(x), y=int(y), w=int(w), h=int(h), score=1.0))
        return out
