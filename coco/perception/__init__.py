"""coco.perception 子包：相机/感知抽象层。"""

from coco.perception.camera_source import (
    CameraSource,
    CameraSpec,
    ImageLoopSource,
    UsbCameraSource,
    VideoFileSource,
    open_camera,
    parse_camera_env,
)
from coco.perception.face_detect import FaceBox, FaceDetector
from coco.perception.face_tracker import FaceSnapshot, FaceTracker, FaceTrackerStats

__all__ = [
    "CameraSource",
    "CameraSpec",
    "ImageLoopSource",
    "UsbCameraSource",
    "VideoFileSource",
    "open_camera",
    "parse_camera_env",
    "FaceBox",
    "FaceDetector",
    "FaceSnapshot",
    "FaceTracker",
    "FaceTrackerStats",
]
