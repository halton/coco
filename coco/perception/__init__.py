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

__all__ = [
    "CameraSource",
    "CameraSpec",
    "ImageLoopSource",
    "UsbCameraSource",
    "VideoFileSource",
    "open_camera",
    "parse_camera_env",
]
