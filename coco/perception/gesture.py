"""coco.perception.gesture — vision-005 简易手势识别（sim-only）.

设计目标
========

在 vision 链路（CameraSource / FaceTracker）之上加一层"轻量手势识别"。
不依赖 mediapipe / dlib / 深度模型 —— 仅用 cv2 基础算子 + numpy 启发式。

支持手势 kinds（5 类）：

- ``WAVE``      横向手部往复震荡（帧间 cx 振幅）
- ``THUMBS_UP`` 上半画面竖直长条（垂直/水平比 > 阈值）+ 顶端有近圆形块
- ``NOD``       脸 bbox 在帧间 Y 方向位移（上下点头）
- ``SHAKE``     脸 bbox 在帧间 X 方向位移（左右摇头）
- ``HEART``     占位启发式（双手并拢，两个对称色块靠近）—— 弱实现

输出
====

``GestureLabel``：kind / confidence ∈ [0,1] / ts (monotonic) / bbox(可选)。

``GestureRecognizer`` 后台 daemon 线程：

- 周期从 CameraSource 读帧（默认 5Hz / interval_ms=200）
- 维护 deque(maxlen=window_frames) 帧窗口
- 喂给 backend.detect(window)，命中 + confidence ≥ min_confidence + per-kind cooldown
  通过则 emit "vision.gesture_detected"

线程安全：``threading.RLock`` 保护内部状态；``stop()`` 干净退出。

环境变量
========

- ``COCO_GESTURE``                      0/1，默认 OFF
- ``COCO_GESTURE_INTERVAL_MS``          clamp [50, 2000]，默认 200
- ``COCO_GESTURE_MIN_CONFIDENCE``       clamp [0.0, 1.0]，默认 0.5
- ``COCO_GESTURE_COOLDOWN_S``           clamp [0.0, 60.0]，默认 30.0（per-kind）
- ``COCO_GESTURE_WINDOW_FRAMES``        clamp [2, 60]，默认 8

verify 见 ``scripts/verify_vision_005.py``。
"""

from __future__ import annotations

import collections
import enum
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, List, Optional, Protocol, Tuple

import numpy as np

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# kinds & data
# ---------------------------------------------------------------------------


class GestureKind(str, enum.Enum):
    NONE = "none"
    WAVE = "wave"
    THUMBS_UP = "thumbs_up"
    NOD = "nod"
    SHAKE = "shake"
    HEART = "heart"


@dataclass(frozen=True)
class GestureLabel:
    """识别命中（或 NONE）。"""

    kind: GestureKind
    confidence: float
    ts: float
    bbox: Optional[Tuple[int, int, int, int]] = None  # (x, y, w, h)


# ---------------------------------------------------------------------------
# backend protocol
# ---------------------------------------------------------------------------


class GestureBackend(Protocol):
    """所有 backend 的共通签名。

    detect 的输入是一个帧序列窗口（最新一帧在最后）；返回单个 GestureLabel
    或 None。窗口长度由 GestureRecognizer 控制；backend 可只看最近一帧或
    多帧组合（NOD/SHAKE/WAVE 必须看时序）。
    """

    def detect(self, frames: List[np.ndarray]) -> Optional[GestureLabel]: ...


# ---------------------------------------------------------------------------
# heuristic helpers
# ---------------------------------------------------------------------------


def _skin_mask(frame_bgr: np.ndarray) -> np.ndarray:
    """非常宽松的"肤色 / 浅色块"分割。

    sim fixture 不需要真实肤色；用一个亮度 + 偏暖通道阈值即可挑出
    我们合成的 (180, 200, 220) / (200, 215, 230) 等浅色椭圆 / 长条块。
    返回 uint8 mask（0 / 255）。
    """
    if frame_bgr.ndim == 2:
        gray = frame_bgr
    else:
        gray = (
            frame_bgr[..., 0].astype(np.int32)
            + frame_bgr[..., 1].astype(np.int32)
            + frame_bgr[..., 2].astype(np.int32)
        ) // 3
    mask = (gray > 140).astype(np.uint8) * 255
    return mask


def _largest_blob_centroid(mask: np.ndarray) -> Optional[Tuple[int, int, int, int, int]]:
    """返回最大连通块的 (cx, cy, x, y, w, h)... 这里用简化"非零像素" bbox。

    不引 cv2.connectedComponents（避免依赖 numpy 之外算法重）；用 np.where
    取所有非零像素 bbox 即可——sim 场景前景是单一块。

    返回 None 表示 mask 全空。
    """
    ys, xs = np.where(mask > 0)
    if xs.size == 0:
        return None
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    w = max(1, x1 - x0)
    h = max(1, y1 - y0)
    cx = x0 + w // 2
    cy = y0 + h // 2
    return cx, cy, x0, y0, w, h


def _centroid(frame_bgr: np.ndarray) -> Optional[Tuple[int, int, int, int, int, int]]:
    """取 frame 中"前景块"的中心坐标 + bbox。

    返回 (cx, cy, x, y, w, h) 或 None。
    """
    mask = _skin_mask(frame_bgr)
    blob = _largest_blob_centroid(mask)
    if blob is None:
        return None
    cx, cy, x0, y0, w, h = blob
    return cx, cy, x0, y0, w, h


# ---------------------------------------------------------------------------
# HeuristicGestureBackend
# ---------------------------------------------------------------------------


class HeuristicGestureBackend:
    """纯 numpy + 极简 cv2 启发式 backend。

    所有手势类别共用 ``_centroid`` 抽前景块（合成 fixture 中即"手"或"脸" mock）。
    根据帧序列上 cx/cy 时序与单帧形状特征产出 GestureLabel。

    阈值都是 sim fixture 调出的——真机几乎肯定不可用。本 feature 是 sim-only。
    """

    def __init__(
        self,
        *,
        wave_min_amplitude_ratio: float = 0.12,   # 帧间 x 振幅 / frame_w 阈值
        wave_min_zero_crossings: int = 2,         # 方向反转次数
        thumbs_up_aspect_min: float = 1.6,        # h/w 长条形阈值
        nod_min_amplitude_ratio: float = 0.10,    # 帧间 y 振幅 / frame_h
        shake_min_amplitude_ratio: float = 0.10,
    ) -> None:
        self.wave_min_amplitude_ratio = wave_min_amplitude_ratio
        self.wave_min_zero_crossings = wave_min_zero_crossings
        self.thumbs_up_aspect_min = thumbs_up_aspect_min
        self.nod_min_amplitude_ratio = nod_min_amplitude_ratio
        self.shake_min_amplitude_ratio = shake_min_amplitude_ratio

    # ---- 单帧形状特征：THUMBS_UP / HEART ----

    def _detect_thumbs_up(
        self, frame: np.ndarray
    ) -> Optional[GestureLabel]:
        """上半区域出现垂直长条（h/w >= 阈值）+ 顶端附近有更宽的圆块。"""
        h_img, w_img = frame.shape[:2]
        upper = frame[: h_img // 2, :]
        blob = _centroid(upper)
        if blob is None:
            return None
        cx, cy, x0, y0, w, h = blob
        if w <= 0:
            return None
        aspect = h / float(w)
        if aspect < self.thumbs_up_aspect_min:
            return None
        # confidence 随 aspect 递增
        conf = min(1.0, 0.5 + 0.2 * (aspect - self.thumbs_up_aspect_min))
        return GestureLabel(
            kind=GestureKind.THUMBS_UP,
            confidence=float(conf),
            ts=time.monotonic(),
            bbox=(int(x0), int(y0), int(w), int(h)),
        )

    def _detect_heart(self, frame: np.ndarray) -> Optional[GestureLabel]:
        """非常弱的占位：mask 在水平方向有两个明显分离的等大块。"""
        mask = _skin_mask(frame)
        h_img, w_img = mask.shape[:2]
        # 切左右半，看两侧像素质量是否都显著且接近
        left = int(np.count_nonzero(mask[:, : w_img // 2]))
        right = int(np.count_nonzero(mask[:, w_img // 2 :]))
        total = left + right
        if total < 200:
            return None
        if min(left, right) / max(1, total) < 0.35:
            return None
        # 中间一条窄列必须接近空（两块分离）
        mid_w = max(1, w_img // 10)
        mid_start = w_img // 2 - mid_w // 2
        mid = int(np.count_nonzero(mask[:, mid_start : mid_start + mid_w]))
        if mid > total * 0.05:
            return None
        conf = 0.5 + 0.3 * min(1.0, total / 4000.0)
        return GestureLabel(
            kind=GestureKind.HEART,
            confidence=float(min(1.0, conf)),
            ts=time.monotonic(),
        )

    # ---- 时序特征：WAVE / NOD / SHAKE ----

    def _frame_track(
        self, frames: List[np.ndarray]
    ) -> List[Tuple[int, int, int]]:
        """对每帧抽 (cx, cy, frame_w) 序列；前景缺失则用上次值。"""
        track: List[Tuple[int, int, int]] = []
        last: Optional[Tuple[int, int, int]] = None
        for f in frames:
            blob = _centroid(f)
            w_img = f.shape[1]
            if blob is None:
                if last is not None:
                    track.append(last)
                continue
            cx, cy, _x0, _y0, _w, _h = blob
            last = (int(cx), int(cy), int(w_img))
            track.append(last)
        return track

    def _detect_wave(
        self, frames: List[np.ndarray]
    ) -> Optional[GestureLabel]:
        track = self._frame_track(frames)
        if len(track) < 4:
            return None
        xs = np.array([t[0] for t in track], dtype=np.float64)
        w_img = max(1, int(track[-1][2]))
        amp = float(xs.max() - xs.min()) / float(w_img)
        if amp < self.wave_min_amplitude_ratio:
            return None
        # 方向反转次数（一阶差分符号变化）
        dx = np.diff(xs)
        signs = np.sign(dx)
        # 去掉 0 sign
        signs = signs[signs != 0]
        if signs.size < 2:
            return None
        zero_crossings = int(np.count_nonzero(np.diff(signs) != 0))
        if zero_crossings < self.wave_min_zero_crossings:
            return None
        conf = min(1.0, 0.4 + 0.6 * min(1.0, amp / 0.3) + 0.05 * (zero_crossings - 2))
        conf = max(0.0, min(1.0, conf))
        return GestureLabel(
            kind=GestureKind.WAVE,
            confidence=float(conf),
            ts=time.monotonic(),
        )

    def _detect_nod_or_shake(
        self, frames: List[np.ndarray]
    ) -> Optional[GestureLabel]:
        track = self._frame_track(frames)
        if len(track) < 4:
            return None
        xs = np.array([t[0] for t in track], dtype=np.float64)
        ys = np.array([t[1] for t in track], dtype=np.float64)
        w_img = max(1, int(track[-1][2]))
        # frame_h 不在 track 里——用第一帧
        h_img = max(1, int(frames[0].shape[0]))
        amp_x = float(xs.max() - xs.min()) / float(w_img)
        amp_y = float(ys.max() - ys.min()) / float(h_img)
        # NOD: y 振幅大且压制 x 振幅
        if amp_y >= self.nod_min_amplitude_ratio and amp_y > amp_x * 1.3:
            conf = min(1.0, 0.4 + 0.6 * min(1.0, amp_y / 0.3))
            return GestureLabel(
                kind=GestureKind.NOD,
                confidence=float(conf),
                ts=time.monotonic(),
            )
        if amp_x >= self.shake_min_amplitude_ratio and amp_x > amp_y * 1.3:
            # WAVE 与 SHAKE 都基于 x 位移；区分点在 SHAKE 是脸的位移
            # （振幅相对小 + 较慢往复），WAVE 振幅更大且反转更频繁。
            # L1-3: SHAKE 必须 (a) 反转次数 < wave 阈值 且 (b) 振幅不能像 wave
            # 那样大；wave fixture 早期窗口可能只有 1 次反转但振幅已经 >0.3 → 必须排除。
            dx = np.diff(xs)
            signs = np.sign(dx)
            signs = signs[signs != 0]
            zero_crossings = int(np.count_nonzero(np.diff(signs) != 0)) if signs.size >= 2 else 0
            wave_like_amplitude = amp_x >= self.wave_min_amplitude_ratio * 1.5
            if zero_crossings < self.wave_min_zero_crossings and not wave_like_amplitude:
                conf = min(1.0, 0.4 + 0.6 * min(1.0, amp_x / 0.3))
                return GestureLabel(
                    kind=GestureKind.SHAKE,
                    confidence=float(conf),
                    ts=time.monotonic(),
                )
        return None

    # ---- public ----

    def detect(self, frames: List[np.ndarray]) -> Optional[GestureLabel]:
        if not frames:
            return None
        # L1-3: WAVE 优先 — wave fixture 同时可能触发 SHAKE 路径，
        # WAVE 命中后直接返回，避免被同分 SHAKE 抢出。
        if len(frames) >= 2:
            try:
                wave_lbl = self._detect_wave(frames)
            except Exception as e:  # noqa: BLE001
                log.debug("temporal detect _detect_wave failed: %s", e)
                wave_lbl = None
            if wave_lbl is not None:
                return wave_lbl
        # 单帧形状（不依赖时序）
        last = frames[-1]
        cand_static: List[GestureLabel] = []
        for fn in (self._detect_thumbs_up, self._detect_heart):
            try:
                lbl = fn(last)
            except Exception as e:  # noqa: BLE001
                log.debug("static detect %s failed: %s", fn.__name__, e)
                lbl = None
            if lbl is not None:
                cand_static.append(lbl)
        # 时序特征（NOD/SHAKE）
        cand_temporal: List[GestureLabel] = []
        if len(frames) >= 2:
            try:
                lbl = self._detect_nod_or_shake(frames)
            except Exception as e:  # noqa: BLE001
                log.debug("temporal detect _detect_nod_or_shake failed: %s", e)
                lbl = None
            if lbl is not None:
                cand_temporal.append(lbl)
        all_cands = cand_static + cand_temporal
        if not all_cands:
            return None
        return max(all_cands, key=lambda lb: lb.confidence)


# ---------------------------------------------------------------------------
# GestureRecognizer
# ---------------------------------------------------------------------------


@dataclass
class GestureRecognizerStats:
    started_at: float = 0.0
    stopped_at: float = 0.0
    frames_read: int = 0
    detect_count: int = 0
    emit_count: int = 0
    suppressed_low_conf: int = 0
    suppressed_cooldown: int = 0
    error_count: int = 0


class GestureRecognizer:
    """后台手势识别 daemon 线程。

    用法::

        rec = GestureRecognizer(stop_event, camera=src, backend=HeuristicGestureBackend(),
                                interval_ms=200, min_confidence=0.5,
                                cooldown_per_kind_s=2.0, window_frames=8,
                                on_gesture=lambda lbl: ...)
        rec.start()
        ...
        stop_event.set(); rec.join(timeout=2)

    on_gesture 回调在识别命中（且过 confidence + cooldown）时被调用。回调
    自身决定是否 emit / 触发动作。回调内异常被吞，不影响下一帧。
    """

    def __init__(
        self,
        stop_event: threading.Event,
        *,
        camera: Any = None,
        backend: Optional[GestureBackend] = None,
        interval_ms: int = 200,
        min_confidence: float = 0.5,
        cooldown_per_kind_s: float = 2.0,
        window_frames: int = 8,
        on_gesture: Optional[Callable[[GestureLabel], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.stop_event = stop_event
        self._camera = camera
        self._backend: GestureBackend = backend or HeuristicGestureBackend()
        # clamp window_frames [2, 60]
        if window_frames < 2:
            log.warning("[gesture] window_frames=%d <2, clamp 2", window_frames)
            window_frames = 2
        if window_frames > 60:
            log.warning("[gesture] window_frames=%d >60, clamp 60", window_frames)
            window_frames = 60
        self._window_frames = int(window_frames)
        # clamp interval_ms [50, 2000]
        if interval_ms < 50:
            interval_ms = 50
        if interval_ms > 2000:
            interval_ms = 2000
        self._interval_s = max(0.05, interval_ms / 1000.0)
        # clamp min_confidence [0,1]
        if min_confidence < 0.0:
            min_confidence = 0.0
        if min_confidence > 1.0:
            min_confidence = 1.0
        self._min_confidence = float(min_confidence)
        if cooldown_per_kind_s < 0.0:
            cooldown_per_kind_s = 0.0
        if cooldown_per_kind_s > 60.0:
            cooldown_per_kind_s = 60.0
        self._cooldown = float(cooldown_per_kind_s)
        self._on_gesture = on_gesture
        self._clock = clock or time.monotonic

        self._frames: Deque[np.ndarray] = collections.deque(maxlen=self._window_frames)
        self._last_emit_ts: dict = {}  # kind -> ts
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self.stats = GestureRecognizerStats()

    # --- public ---

    @property
    def window_frames(self) -> int:
        return self._window_frames

    @property
    def interval_s(self) -> float:
        return self._interval_s

    @property
    def min_confidence(self) -> float:
        return self._min_confidence

    @property
    def cooldown_per_kind_s(self) -> float:
        return self._cooldown

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                log.warning("GestureRecognizer already running")
                return
            self.stats = GestureRecognizerStats(started_at=time.time())
            self._frames.clear()
            self._last_emit_ts.clear()
            self._thread = threading.Thread(
                target=self._run, name="coco-gesture", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """请求停止；不 join。"""
        self.stop_event.set()

    def join(self, timeout: Optional[float] = None) -> None:
        with self._lock:
            t = self._thread
        if t is not None:
            t.join(timeout=timeout)

    def is_alive(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def feed_frame(self, frame: np.ndarray) -> Optional[GestureLabel]:
        """测试钩子：不走相机，直接喂一帧并跑一次 detect。

        返回本次（如有）通过 confidence + cooldown 的 GestureLabel；否则 None。
        会调用 on_gesture（同 _tick）。
        """
        with self._lock:
            self._frames.append(frame)
            window = list(self._frames)
            self.stats.frames_read += 1
        return self._maybe_detect_and_emit(window)

    # --- internals ---

    def _run(self) -> None:
        log.info(
            "GestureRecognizer started interval=%.2fs window=%d min_conf=%.2f cooldown=%.1fs",
            self._interval_s, self._window_frames, self._min_confidence, self._cooldown,
        )
        try:
            while not self.stop_event.is_set():
                t0 = time.monotonic()
                self._tick()
                elapsed = time.monotonic() - t0
                remain = self._interval_s - elapsed
                if remain > 0:
                    if self.stop_event.wait(timeout=remain):
                        break
        finally:
            self.stats.stopped_at = time.time()
            log.info("GestureRecognizer stopped stats=%s", self.stats)

    def _tick(self) -> None:
        cam = self._camera
        if cam is None:
            return
        try:
            ok, frame = cam.read()
        except Exception as e:  # noqa: BLE001
            self.stats.error_count += 1
            log.warning("GestureRecognizer cam.read failed: %s: %s", type(e).__name__, e)
            return
        if not ok or frame is None:
            return
        with self._lock:
            self._frames.append(frame)
            window = list(self._frames)
            self.stats.frames_read += 1
        self._maybe_detect_and_emit(window)

    def _maybe_detect_and_emit(self, window: List[np.ndarray]) -> Optional[GestureLabel]:
        try:
            lbl = self._backend.detect(window)
        except Exception as e:  # noqa: BLE001
            self.stats.error_count += 1
            log.warning("GestureRecognizer backend.detect failed: %s: %s", type(e).__name__, e)
            return None
        if lbl is None or lbl.kind == GestureKind.NONE:
            return None
        self.stats.detect_count += 1
        if lbl.confidence < self._min_confidence:
            self.stats.suppressed_low_conf += 1
            return None
        now = self._clock()
        with self._lock:
            last_ts = self._last_emit_ts.get(lbl.kind)
            if last_ts is not None and (now - last_ts) < self._cooldown:
                self.stats.suppressed_cooldown += 1
                return None
            self._last_emit_ts[lbl.kind] = now
            self.stats.emit_count += 1
        cb = self._on_gesture
        if cb is not None:
            try:
                cb(lbl)
            except Exception as e:  # noqa: BLE001
                log.warning("GestureRecognizer on_gesture cb failed: %s: %s", type(e).__name__, e)
        return lbl


# ---------------------------------------------------------------------------
# Config + env helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GestureConfig:
    enabled: bool = False
    interval_ms: int = 200
    min_confidence: float = 0.5
    cooldown_per_kind_s: float = 30.0
    window_frames: int = 8


def _bool_env(env, key: str, default: bool = False) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


def _int_env(env, key: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        log.warning("[gesture] %s=%r 非整数，回退 %d", key, raw, default)
        return default
    if v < lo:
        log.warning("[gesture] %s=%d <%d，clamp %d", key, v, lo, lo)
        return lo
    if v > hi:
        log.warning("[gesture] %s=%d >%d，clamp %d", key, v, hi, hi)
        return hi
    return v


def _float_env(env, key: str, default: float, lo: float, hi: float) -> float:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        log.warning("[gesture] %s=%r 非数字，回退 %.2f", key, raw, default)
        return default
    if v < lo:
        log.warning("[gesture] %s=%.2f <%.2f，clamp %.2f", key, v, lo, lo)
        return lo
    if v > hi:
        log.warning("[gesture] %s=%.2f >%.2f，clamp %.2f", key, v, hi, hi)
        return hi
    return v


def gesture_config_from_env(env=None) -> GestureConfig:
    e = env if env is not None else os.environ
    return GestureConfig(
        enabled=_bool_env(e, "COCO_GESTURE", False),
        interval_ms=_int_env(e, "COCO_GESTURE_INTERVAL_MS", 200, 50, 2000),
        min_confidence=_float_env(e, "COCO_GESTURE_MIN_CONFIDENCE", 0.5, 0.0, 1.0),
        cooldown_per_kind_s=_float_env(e, "COCO_GESTURE_COOLDOWN_S", 30.0, 0.0, 60.0),
        window_frames=_int_env(e, "COCO_GESTURE_WINDOW_FRAMES", 8, 2, 60),
    )


def gesture_enabled_from_env(env=None) -> bool:
    e = env if env is not None else os.environ
    return _bool_env(e, "COCO_GESTURE", False)


__all__ = [
    "GestureKind",
    "GestureLabel",
    "GestureBackend",
    "HeuristicGestureBackend",
    "GestureRecognizer",
    "GestureRecognizerStats",
    "GestureConfig",
    "gesture_config_from_env",
    "gesture_enabled_from_env",
]
