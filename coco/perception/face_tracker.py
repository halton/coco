"""coco.perception.face_tracker — 后台人脸跟踪线程（companion-002）.

设计目标：
- 把"开摄像头 + 周期 detect"独立成一个 daemon 线程，不阻塞 IdleAnimator 的主循环。
- 暴露线程安全 snapshot：``latest() -> FaceSnapshot``，IdleAnimator 在自己节奏下读。
- 防闪烁：N 帧滑动平均（``presence_window`` 内 ≥ ``presence_min_hits`` 帧
  检测到人脸才视为 "face present"），避免 fixture / 真机帧间检测抖动引起
  概率分布剧烈切换（companion-002 notes 已声明）。
- 默认关闭：仅在 ``COCO_VISION_IDLE=1`` 或显式注入时启动，避免 smoke 默认路径
  引入新依赖 / 摄像头权限提示。

线程模型：
- run() 是 daemon 线程，循环 ``stop_event.wait(timeout=1/fps)`` 节流；任何时候
  ``stop_event.set()`` 都能在 ≤ 1/fps 内退出。
- 共享 state 用 ``threading.Lock`` 保护；snapshot 是不可变 dataclass 拷贝。
- CascadeClassifier 不保证 thread-safe → 本线程独占自己的 ``FaceDetector``。
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from coco.perception.camera_source import CameraSource, open_camera
from coco.perception.face_detect import FaceBox, FaceDetector

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FaceSnapshot:
    """线程安全的最新检测快照。

    - ``faces``: 最近一次 detect 的结果（可能为空）
    - ``frame_w`` / ``frame_h``: 最近一帧尺寸（face 中心算 x_ratio 用）
    - ``present``: 经过滑动平均判定的"是否有人在场"
    - ``primary``: 当 ``present=True`` 时取最大面积的 FaceBox，否则 None
    - ``ts``: 单调时钟戳（最近一次 detect 完成时间）
    - ``detect_count``: 自启动以来 detect 调用次数（含空结果）
    - ``hit_count``: detect 返回 ≥1 face 的次数
    """

    faces: tuple = ()
    frame_w: int = 0
    frame_h: int = 0
    present: bool = False
    primary: Optional[FaceBox] = None
    ts: float = 0.0
    detect_count: int = 0
    hit_count: int = 0

    def x_ratio(self) -> Optional[float]:
        """primary face 中心 x 相对帧中心的偏移比例 ∈ [-1, 1]。

        左侧（cx < frame_w/2）= 负值；右侧 = 正值。
        无 primary / 帧宽未知时返回 None。
        """
        if self.primary is None or self.frame_w <= 0:
            return None
        cx = self.primary.cx
        center = self.frame_w / 2.0
        ratio = (cx - center) / center  # ∈ [-1, 1]
        # 限幅
        if ratio < -1.0:
            return -1.0
        if ratio > 1.0:
            return 1.0
        return float(ratio)


@dataclass
class FaceTrackerStats:
    """运行时统计。"""

    started_at: float = 0.0
    stopped_at: float = 0.0
    detect_count: int = 0
    hit_count: int = 0
    error_count: int = 0
    frames_dropped: int = 0  # camera.read() 返回 False 的次数


class FaceTracker:
    """后台人脸跟踪。

    用法：
        tracker = FaceTracker(stop_event, camera_spec="image:.../single_face.jpg")
        tracker.start()
        ...
        snap = tracker.latest()
        ...
        stop_event.set()
        tracker.join(timeout=2)
    """

    def __init__(
        self,
        stop_event: threading.Event,
        *,
        camera_spec: Optional[str] = None,
        camera: Optional[CameraSource] = None,
        detector: Optional[FaceDetector] = None,
        fps: float = 5.0,
        presence_window: int = 5,
        presence_min_hits: int = 3,
        absence_min_misses: int = 3,
    ) -> None:
        if camera is not None and camera_spec is not None:
            raise ValueError("camera 与 camera_spec 二选一")
        self.stop_event = stop_event
        self._camera_spec = camera_spec
        self._camera_external = camera is not None
        self._camera: Optional[CameraSource] = camera
        self._detector = detector or FaceDetector()
        self._fps = max(0.5, float(fps))
        self._period = 1.0 / self._fps
        if not (1 <= presence_window <= 30):
            raise ValueError(f"presence_window={presence_window} 不合法 [1,30]")
        if not (1 <= presence_min_hits <= presence_window):
            raise ValueError(f"presence_min_hits={presence_min_hits} 不合法")
        if not (1 <= absence_min_misses <= presence_window):
            raise ValueError(f"absence_min_misses={absence_min_misses} 不合法")
        self._presence_window = presence_window
        self._presence_min_hits = presence_min_hits
        self._absence_min_misses = absence_min_misses
        self.stats = FaceTrackerStats()

        self._lock = threading.Lock()
        self._snapshot = FaceSnapshot()
        # True = 该帧 detect 命中
        self._hit_history: collections.deque = collections.deque(maxlen=presence_window)
        self._present = False  # 滑动平均后的稳定态

        self._thread: Optional[threading.Thread] = None

    # --- public ---
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            log.warning("FaceTracker already running")
            return
        # 延迟打开相机，确保 start 时才占资源
        if self._camera is None:
            self._camera = open_camera(self._camera_spec)
        self.stats = FaceTrackerStats(started_at=time.time())
        self._hit_history.clear()
        self._present = False
        with self._lock:
            self._snapshot = FaceSnapshot()
        self._thread = threading.Thread(target=self._run, name="coco-face-tracker", daemon=True)
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        # camera 收尾（仅当我们打开的）
        if not self._camera_external and self._camera is not None:
            try:
                self._camera.release()
            except Exception:  # noqa: BLE001
                pass
            self._camera = None

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def latest(self) -> FaceSnapshot:
        """返回最新 snapshot 的拷贝（线程安全）。"""
        with self._lock:
            return self._snapshot

    # --- internals ---
    def _run(self) -> None:
        log.info(
            "FaceTracker started fps=%.1f window=%d min_hits=%d min_misses=%d",
            self._fps, self._presence_window, self._presence_min_hits, self._absence_min_misses,
        )
        try:
            while not self.stop_event.is_set():
                t0 = time.monotonic()
                self._tick()
                # 节流：剩余时间睡掉，但允许 stop_event 提前唤醒
                elapsed = time.monotonic() - t0
                remain = self._period - elapsed
                if remain > 0:
                    if self.stop_event.wait(timeout=remain):
                        break
        finally:
            self.stats.stopped_at = time.time()
            log.info("FaceTracker stopped stats=%s", self.stats)

    def _tick(self) -> None:
        cam = self._camera
        if cam is None:
            return
        try:
            ok, frame = cam.read()
        except Exception as e:  # noqa: BLE001
            self.stats.error_count += 1
            log.warning("FaceTracker camera.read failed: %s: %s", type(e).__name__, e)
            return
        if not ok or frame is None:
            self.stats.frames_dropped += 1
            return

        try:
            faces = self._detector.detect(frame)
        except Exception as e:  # noqa: BLE001
            self.stats.error_count += 1
            log.warning("FaceTracker detect failed: %s: %s", type(e).__name__, e)
            return

        self.stats.detect_count += 1
        hit = len(faces) > 0
        if hit:
            self.stats.hit_count += 1
        self._hit_history.append(hit)

        # 滑动平均判定 present 态切换（带迟滞）
        recent_hits = sum(1 for h in self._hit_history if h)
        recent_misses = len(self._hit_history) - recent_hits
        if not self._present and recent_hits >= self._presence_min_hits:
            self._present = True
            log.info("FaceTracker presence ↑ TRUE (hits=%d/%d)", recent_hits, len(self._hit_history))
        elif self._present and recent_misses >= self._absence_min_misses:
            self._present = False
            log.info("FaceTracker presence ↓ FALSE (misses=%d/%d)", recent_misses, len(self._hit_history))

        primary = max(faces, key=lambda b: b.w * b.h) if (self._present and faces) else None
        h, w = frame.shape[:2]
        snap = FaceSnapshot(
            faces=tuple(faces),
            frame_w=int(w),
            frame_h=int(h),
            present=self._present,
            primary=primary,
            ts=time.monotonic(),
            detect_count=self.stats.detect_count,
            hit_count=self.stats.hit_count,
        )
        with self._lock:
            self._snapshot = snap


__all__ = ["FaceSnapshot", "FaceTracker", "FaceTrackerStats"]
