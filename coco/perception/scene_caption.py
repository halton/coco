"""coco.perception.scene_caption — vision-006 看图说话（sim-first）.

设计目标
========

在 vision 链路（CameraSource）之上加一层"轻量场景描述"。
不依赖云 VLM / 本地大模型 —— 仅用 cv2 基础算子 + numpy 启发式提取
颜色直方图、亮度均值、运动剪影 / 帧差等基本特征，再用中文模板拼成一句描述。

后台 daemon 线程 :class:`SceneCaptionEmitter` 周期采样：

- 默认 60s 一次（``interval_s``，clamp [5, 3600]）；
- 命中后 emit ``vision.scene_caption``（component='vision'）；
- 同描述（文本相似度 ≥ ``min_change_threshold``）+ cooldown 窗口内不重复 emit；
- ``on_caption`` 回调让外部接管（例如 ProactiveScheduler.record_caption_trigger）。

线程安全：``threading.RLock`` 保护内部状态；``stop()`` 干净退出。

环境变量
========

- ``COCO_SCENE_CAPTION``               0/1，默认 OFF
- ``COCO_SCENE_CAPTION_INTERVAL_S``    clamp [5, 3600]，默认 60.0
- ``COCO_SCENE_CAPTION_COOLDOWN_S``    clamp [0, 3600]，默认 60.0
- ``COCO_SCENE_CAPTION_MIN_CHANGE``    clamp [0.0, 1.0]，默认 0.8

verify 见 ``scripts/verify_vision_006.py``。

vision-006 / phase-8。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, Optional, Protocol

import numpy as np

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SceneCaption:
    """一次场景描述结果。

    - ``text``: 中文描述（短句）
    - ``ts``: 命中时刻（monotonic 秒）
    - ``features``: backend 提取的原始特征（亮度均值、是否运动等），可观测
    """

    text: str
    ts: float
    features: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


class CaptionBackend(Protocol):
    """所有 backend 的共通签名。

    输入：当前帧（HxWx3 BGR uint8，可能为 None / 极小尺寸 / 非 BGR）。
    可选输入：上一帧（用于运动检测；首帧时为 None）。

    返回：``SceneCaption`` 或 None（无法描述时）。backend 内部应 fail-soft：
    异常一律转 None，由 Emitter 计入 ``stats.backend_errors``。
    """

    def caption(
        self,
        frame: Optional[np.ndarray],
        prev_frame: Optional[np.ndarray] = None,
    ) -> Optional[SceneCaption]: ...


# ---------------------------------------------------------------------------
# HeuristicCaptionBackend
# ---------------------------------------------------------------------------


def _safe_bgr(frame: Optional[np.ndarray]) -> Optional[np.ndarray]:
    """转成可处理的 BGR uint8；过小 / shape 异常时返回 None。"""
    if frame is None:
        return None
    if not isinstance(frame, np.ndarray):
        return None
    if frame.ndim == 2:
        # 灰度 → BGR
        try:
            return np.dstack([frame, frame, frame]).astype(np.uint8)
        except Exception:  # noqa: BLE001
            return None
    if frame.ndim != 3 or frame.shape[2] < 3:
        return None
    if frame.shape[0] < 8 or frame.shape[1] < 8:
        return None
    try:
        if frame.dtype != np.uint8:
            return frame.astype(np.uint8)
        return frame
    except Exception:  # noqa: BLE001
        return None


def _mean_luma(bgr: np.ndarray) -> float:
    # 经典 BT.601 luma 近似，避免引 cv2 转换开销
    b = bgr[:, :, 0].astype(np.float32)
    g = bgr[:, :, 1].astype(np.float32)
    r = bgr[:, :, 2].astype(np.float32)
    return float((0.114 * b + 0.587 * g + 0.299 * r).mean())


def _frame_diff_metrics(
    bgr: np.ndarray, prev: Optional[np.ndarray]
) -> Dict[str, float]:
    """计算运动剪影指标：mean abs diff + 重心 x 位置（左/中/右）。"""
    if prev is None or prev.shape != bgr.shape:
        return {"diff_mean": 0.0, "motion_cx_ratio": 0.5, "has_motion": 0.0}
    try:
        cur_g = bgr.mean(axis=2)
        prv_g = prev.mean(axis=2)
        diff = np.abs(cur_g - prv_g)
        m = float(diff.mean())
        # 找到 diff 大的列重心
        col_sum = diff.sum(axis=0)
        total = col_sum.sum()
        if total > 0:
            xs = np.arange(col_sum.shape[0], dtype=np.float32)
            cx = float((col_sum * xs).sum() / total)
            cx_ratio = cx / max(1, col_sum.shape[0] - 1)
        else:
            cx_ratio = 0.5
        return {
            "diff_mean": m,
            "motion_cx_ratio": cx_ratio,
            "has_motion": 1.0 if m >= 6.0 else 0.0,
        }
    except Exception:  # noqa: BLE001
        return {"diff_mean": 0.0, "motion_cx_ratio": 0.5, "has_motion": 0.0}


def _luma_band(mean_luma: float) -> str:
    if mean_luma < 60.0:
        return "dark"
    if mean_luma > 180.0:
        return "bright"
    return "normal"


def _motion_side(cx_ratio: float) -> str:
    if cx_ratio < 0.35:
        return "left"
    if cx_ratio > 0.65:
        return "right"
    return "center"


class HeuristicCaptionBackend:
    """启发式 backend：基于亮度 + 帧差 + 颜色直方图生成中文描述。

    不依赖 mediapipe / 深度模型；纯 cv2/numpy 算子。fail-soft：任何异常都返回 None。
    """

    def caption(
        self,
        frame: Optional[np.ndarray],
        prev_frame: Optional[np.ndarray] = None,
    ) -> Optional[SceneCaption]:
        bgr = _safe_bgr(frame)
        if bgr is None:
            return None
        prev = _safe_bgr(prev_frame) if prev_frame is not None else None
        try:
            luma = _mean_luma(bgr)
            motion = _frame_diff_metrics(bgr, prev)
            band = _luma_band(luma)
            has_motion = motion["has_motion"] > 0.5
            side = _motion_side(motion["motion_cx_ratio"]) if has_motion else None
        except Exception as e:  # noqa: BLE001
            log.warning("[scene_caption] heuristic 失败: %s: %s", type(e).__name__, e)
            return None

        # 拼中文描述
        if band == "dark":
            lead = "画面整体偏暗"
        elif band == "bright":
            lead = "画面偏亮"
        else:
            lead = "画面亮度适中"

        if has_motion:
            side_zh = {"left": "左侧", "right": "右侧", "center": "中间"}.get(
                side or "center", "中间"
            )
            tail = f"，有一个移动物体在{side_zh}"
        else:
            tail = "，主体大致居中静止"

        text = lead + tail

        return SceneCaption(
            text=text,
            ts=time.monotonic(),
            features={
                "mean_luma": float(luma),
                "luma_band": band,
                "has_motion": bool(has_motion),
                "motion_side": side,
                "diff_mean": float(motion["diff_mean"]),
                "motion_cx_ratio": float(motion["motion_cx_ratio"]),
            },
        )


# ---------------------------------------------------------------------------
# LLMCaptionBackend (stub)
# ---------------------------------------------------------------------------


class LLMCaptionBackend:
    """占位 stub —— 未来接入本地/云 VLM 时实现。

    本期（vision-006）**不实现**。保留 docstring 与构造签名作为契约。
    未来候选：

    - 本地小模型：blip / blip2 / qwen-vl-2b（受设备性能限制）
    - 云：通义千问 VL / GPT-4o-mini-vision / claude-haiku-vision（隐私 / 流量考量）

    引入时：
    1. 单独立 feature（`vision-007` 等）；
    2. 评估推理延迟 vs interval_s（避免后台线程内调超过周期的 LLM）；
    3. 评估 token 成本与失败回退（fail-soft 退到 Heuristic）。

    infra-009 / vision-006 L2-4：删除冗余的 caption() 占位方法（__init__ 已经
    raise NotImplementedError，到不了 caption；保留 caption 反而误导 IDE 类型推导）。
    """

    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise NotImplementedError(
            "LLMCaptionBackend is a stub — see docstring for future plan"
        )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class SceneCaptionEmitterStats:
    started_at: float = 0.0
    stopped_at: float = 0.0
    ticks: int = 0
    frames_read: int = 0
    backend_returned: int = 0
    backend_errors: int = 0
    suppressed_similar: int = 0
    suppressed_cooldown: int = 0
    emitted: int = 0
    error_count: int = 0


# ---------------------------------------------------------------------------
# SceneCaptionEmitter
# ---------------------------------------------------------------------------


class SceneCaptionEmitter:
    """周期场景描述 emitter（后台 daemon 线程）.

    用法::

        em = SceneCaptionEmitter(
            stop_event,
            camera=src,
            backend=HeuristicCaptionBackend(),
            interval_s=60.0,
            cooldown_s=60.0,
            min_change_threshold=0.8,
            on_caption=lambda cap: ...,
        )
        em.start()
        ...
        stop_event.set(); em.join(timeout=2)

    抑制规则：

    - 与最近一次 emitted caption 文本相似度 ≥ min_change_threshold → 视为重复（suppressed_similar）
    - 距最近一次 emitted 不足 cooldown_s（基于 emit 时刻；与 interval 解耦）→ 抑制
      （suppressed_cooldown）；min_change_threshold == 0 时不做相似度抑制；cooldown_s == 0
      时不做冷却抑制（仍按 interval 周期采样）。

    `on_caption(cap)` 仅在通过抑制后才调用，回调内异常被吞，不影响下一轮。
    """

    def __init__(
        self,
        stop_event: threading.Event,
        *,
        camera: Any = None,
        backend: Optional[CaptionBackend] = None,
        interval_s: float = 60.0,
        cooldown_s: float = 60.0,
        min_change_threshold: float = 0.8,
        on_caption: Optional[Callable[[SceneCaption], None]] = None,
        emit_fn: Optional[Callable[..., None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.stop_event = stop_event
        self._camera = camera
        self._backend: CaptionBackend = backend or HeuristicCaptionBackend()
        # clamp interval_s [5, 3600]
        if interval_s < 5.0:
            log.warning("[scene_caption] interval_s=%.2f <5, clamp 5", interval_s)
            interval_s = 5.0
        if interval_s > 3600.0:
            log.warning("[scene_caption] interval_s=%.2f >3600, clamp 3600", interval_s)
            interval_s = 3600.0
        self._interval_s = float(interval_s)
        # clamp cooldown_s [0, 3600]
        if cooldown_s < 0.0:
            cooldown_s = 0.0
        if cooldown_s > 3600.0:
            cooldown_s = 3600.0
        self._cooldown_s = float(cooldown_s)
        # clamp min_change_threshold [0, 1]
        if min_change_threshold < 0.0:
            min_change_threshold = 0.0
        if min_change_threshold > 1.0:
            min_change_threshold = 1.0
        self._min_change = float(min_change_threshold)
        self._on_caption = on_caption
        self._emit_fn = emit_fn
        self._clock = clock or time.monotonic

        self._lock = threading.RLock()
        self._prev_frame: Optional[np.ndarray] = None
        self._last_text: str = ""
        self._last_emit_ts: float = 0.0
        self._thread: Optional[threading.Thread] = None
        self.stats = SceneCaptionEmitterStats()

    # --- public ---

    @property
    def interval_s(self) -> float:
        return self._interval_s

    @property
    def cooldown_s(self) -> float:
        return self._cooldown_s

    @property
    def min_change_threshold(self) -> float:
        return self._min_change

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                log.warning("[scene_caption] emitter already running")
                return
            self.stats = SceneCaptionEmitterStats(started_at=time.time())
            self._prev_frame = None
            self._last_text = ""
            self._last_emit_ts = 0.0
            self._thread = threading.Thread(
                target=self._run, name="coco-scene-caption", daemon=True
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

    def feed_frame(self, frame: np.ndarray) -> Optional[SceneCaption]:
        """测试钩子：不走相机，直接喂一帧并跑一次 caption + 抑制判定。

        返回本轮被 emit 出去的 ``SceneCaption``，被抑制 / backend 返回 None 时返回 None。
        会调用 on_caption 与 emit_fn（同 _tick）。
        """
        with self._lock:
            self.stats.frames_read += 1
        return self._caption_and_maybe_emit(frame)

    # --- internals ---

    def _run(self) -> None:
        log.info(
            "SceneCaptionEmitter started interval=%.1fs cooldown=%.1fs min_change=%.2f",
            self._interval_s, self._cooldown_s, self._min_change,
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
            log.info("SceneCaptionEmitter stopped stats=%s", self.stats)

    def _tick(self) -> None:
        with self._lock:
            self.stats.ticks += 1
        cam = self._camera
        if cam is None:
            return
        try:
            ok, frame = cam.read()
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self.stats.error_count += 1
            log.warning("[scene_caption] cam.read failed: %s: %s", type(e).__name__, e)
            return
        if not ok or frame is None:
            return
        with self._lock:
            self.stats.frames_read += 1
        self._caption_and_maybe_emit(frame)

    def _caption_and_maybe_emit(
        self, frame: np.ndarray
    ) -> Optional[SceneCaption]:
        # 跑 backend
        try:
            cap = self._backend.caption(frame, prev_frame=self._prev_frame)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self.stats.backend_errors += 1
            log.warning("[scene_caption] backend.caption raised: %s: %s",
                        type(e).__name__, e)
            cap = None
        # 更新 prev_frame（无论 backend 是否返回，都推进窗口）
        # infra-009 / vision-006 L2-1: copy 防 buffer 复用 —— 外部相机驱动
        # 常常复用同一块底层 ndarray buffer 反复填，下次 cam.read() 会就地改写
        # 我们手上的引用；不 copy 就会导致下一轮 _frame_diff_metrics 拿到
        # 跟当前帧"一模一样"的 prev，运动检测整段失效。
        try:
            if isinstance(frame, np.ndarray):
                self._prev_frame = frame.copy()
            else:
                self._prev_frame = frame
        except Exception:  # noqa: BLE001
            pass
        if cap is None:
            return None
        with self._lock:
            self.stats.backend_returned += 1
            now = self._clock()
            # cooldown：基于上次 emit 时刻
            if self._cooldown_s > 0 and self._last_emit_ts > 0:
                if (now - self._last_emit_ts) < self._cooldown_s:
                    self.stats.suppressed_cooldown += 1
                    return None
            # min_change：相似度抑制
            if self._min_change > 0 and self._last_text:
                sim = SequenceMatcher(None, cap.text, self._last_text).ratio()
                if sim >= self._min_change:
                    self.stats.suppressed_similar += 1
                    return None
            self._last_text = cap.text
            self._last_emit_ts = now
            self.stats.emitted += 1

        # 回调 + emit（锁外）
        cb = self._on_caption
        if cb is not None:
            try:
                cb(cap)
            except Exception as e:  # noqa: BLE001
                log.warning("[scene_caption] on_caption cb failed: %s: %s",
                            type(e).__name__, e)
        try:
            emit_fn = self._emit_fn
            if emit_fn is None:
                from coco.logging_setup import emit as _emit
                emit_fn = _emit
            emit_fn(
                "vision.scene_caption",
                component="vision",
                text=cap.text[:200],
                has_motion=bool(cap.features.get("has_motion", False)),
                luma_band=str(cap.features.get("luma_band", "")),
                motion_side=cap.features.get("motion_side"),
                mean_luma=float(cap.features.get("mean_luma", 0.0)),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[scene_caption] emit failed: %s: %s", type(e).__name__, e)

        return cap


# ---------------------------------------------------------------------------
# Config + env helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SceneCaptionConfig:
    enabled: bool = False
    interval_s: float = 60.0
    cooldown_s: float = 60.0
    min_change_threshold: float = 0.8


def _bool_env(env, key: str, default: bool = False) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


def _float_env(env, key: str, default: float, lo: float, hi: float) -> float:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        log.warning("[scene_caption] %s=%r 非数字，回退 %.2f", key, raw, default)
        return default
    if v < lo:
        log.warning("[scene_caption] %s=%.2f <%.2f，clamp %.2f", key, v, lo, lo)
        return lo
    if v > hi:
        log.warning("[scene_caption] %s=%.2f >%.2f，clamp %.2f", key, v, hi, hi)
        return hi
    return v


def scene_caption_config_from_env(env=None) -> SceneCaptionConfig:
    e = env if env is not None else os.environ
    return SceneCaptionConfig(
        enabled=_bool_env(e, "COCO_SCENE_CAPTION", False),
        interval_s=_float_env(e, "COCO_SCENE_CAPTION_INTERVAL_S", 60.0, 5.0, 3600.0),
        cooldown_s=_float_env(e, "COCO_SCENE_CAPTION_COOLDOWN_S", 60.0, 0.0, 3600.0),
        min_change_threshold=_float_env(
            e, "COCO_SCENE_CAPTION_MIN_CHANGE", 0.8, 0.0, 1.0
        ),
    )


def scene_caption_enabled_from_env(env=None) -> bool:
    e = env if env is not None else os.environ
    return _bool_env(e, "COCO_SCENE_CAPTION", False)


__all__ = [
    "SceneCaption",
    "CaptionBackend",
    "HeuristicCaptionBackend",
    "LLMCaptionBackend",
    "SceneCaptionEmitter",
    "SceneCaptionEmitterStats",
    "SceneCaptionConfig",
    "scene_caption_config_from_env",
    "scene_caption_enabled_from_env",
]
