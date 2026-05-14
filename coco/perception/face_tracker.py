"""coco.perception.face_tracker — 后台人脸跟踪线程（companion-002 + vision-002）.

设计目标：
- 把"开摄像头 + 周期 detect"独立成一个 daemon 线程，不阻塞 IdleAnimator 的主循环。
- 暴露线程安全 snapshot：``latest() -> FaceSnapshot``，IdleAnimator 在自己节奏下读。
- 多帧 IoU 跟踪（vision-002）：每个检测框跨帧绑定 ``track_id``，提供
  ``TrackedFace``（id / box / age_frames / smoothed_cx,cy / presence_score）。
- 主脸选择策略（vision-002）：默认按 box 面积最大；可选 "nearest_to_last"（与上一帧
  primary 中心距离最近）/ "longest_lived"。primary 切换需 ``primary_switch_min_frames``
  连续帧支持新候选才允许，避免抖动。
- presence hysteresis（vision-002 强化）：True→False 需 K 帧（默认 K=10）连续 0 face；
  False→True 需 J 帧（默认 J=2）连续 ≥1 face。环境变量可调。
- 默认关闭：仅在 ``COCO_VISION_IDLE=1`` 或显式注入时启动，避免 smoke 默认路径
  引入新依赖 / 摄像头权限提示。

线程模型：
- run() 是 daemon 线程，循环 ``stop_event.wait(timeout=1/fps)`` 节流；任何时候
  ``stop_event.set()`` 都能在 ≤ 1/fps 内退出。
- 共享 state 用 ``threading.Lock`` 保护；snapshot 是不可变 dataclass 拷贝。
- CascadeClassifier 不保证 thread-safe → 本线程独占自己的 ``FaceDetector``。

向后兼容（companion-002）：
- ``FaceSnapshot.faces`` / ``.present`` / ``.primary`` / ``.x_ratio()`` 行为不变。
- 旧调用方（idle.py）无须修改。新字段 ``tracks`` / ``primary_track`` 可选消费。
"""

from __future__ import annotations

import collections
import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from coco.perception.camera_source import CameraSource, open_camera
from coco.perception.face_detect import FaceBox, FaceDetector

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackedFace:
    """跨帧追踪到的一张脸。

    - ``track_id``: 单调递增整数，跟踪生命周期内不变
    - ``box``: 当前帧的 FaceBox（最近一次匹配上的 raw 检测）
    - ``age_frames``: 累计参与匹配的帧数（hit + miss）
    - ``hit_count``: 累计被 detect 命中的帧数
    - ``miss_count``: 当前连续 miss 帧数（命中即清零）
    - ``smoothed_cx`` / ``smoothed_cy``: EMA 平滑中心坐标
    - ``presence_score``: 最近窗口内命中比例 ∈ [0, 1]，用于稳定性判断
    - ``first_seen_ts`` / ``last_seen_ts``: monotonic 时钟戳
    """

    track_id: int
    box: FaceBox
    age_frames: int
    hit_count: int
    miss_count: int
    smoothed_cx: float
    smoothed_cy: float
    presence_score: float
    first_seen_ts: float
    last_seen_ts: float
    # vision-003: 可选 face-id 识别结果（默认 None 向后兼容）
    name: Optional[str] = None
    name_confidence: float = 0.0

    @property
    def area(self) -> int:
        return int(self.box.w) * int(self.box.h)


@dataclass(frozen=True)
class FaceSnapshot:
    """线程安全的最新检测快照。

    向后兼容字段（companion-002 在用）：
    - ``faces``: 最近一次 detect 的 raw 结果（可能为空）
    - ``frame_w`` / ``frame_h``: 最近一帧尺寸
    - ``present``: 经过 hysteresis 判定的"是否有人在场"
    - ``primary``: 主 FaceBox（取自 ``primary_track.box``）；无则 None
    - ``ts`` / ``detect_count`` / ``hit_count``: 同 v1

    新字段（vision-002）：
    - ``tracks``: 当前活跃 TrackedFace 列表
    - ``primary_track``: 主脸 TrackedFace（含 track_id / age 等），便于上层判断切换
    """

    faces: tuple = ()
    frame_w: int = 0
    frame_h: int = 0
    present: bool = False
    primary: Optional[FaceBox] = None
    ts: float = 0.0
    detect_count: int = 0
    hit_count: int = 0
    tracks: tuple = ()  # tuple[TrackedFace, ...]
    primary_track: Optional[TrackedFace] = None

    def x_ratio(self) -> Optional[float]:
        """primary face 中心 x 相对帧中心的偏移比例 ∈ [-1, 1]。"""
        if self.primary is None or self.frame_w <= 0:
            return None
        cx = self.primary.cx
        center = self.frame_w / 2.0
        ratio = (cx - center) / center
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
    frames_dropped: int = 0
    # vision-002
    tracks_created: int = 0       # 累计新建 track 数
    tracks_dropped: int = 0       # 累计销毁 track 数（连续 miss 超阈值）
    primary_switches: int = 0     # primary track_id 实际切换次数
    # vision-009: classifier 后注入 / 替换时被 lock-once 跳过的累计次数
    face_id_classifier_late_inject_skipped: int = 0


# ---------------------------------------------------------------------------
# IoU 工具
# ---------------------------------------------------------------------------


def iou_xywh(a: FaceBox, b: FaceBox) -> float:
    """两个 xywh box 的 IoU。"""
    ax2, ay2 = a.x + a.w, a.y + a.h
    bx2, by2 = b.x + b.w, b.y + b.h
    ix1, iy1 = max(a.x, b.x), max(a.y, b.y)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0, a.w) * max(0, a.h)
    area_b = max(0, b.w) * max(0, b.h)
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return float(inter) / float(union)


# ---------------------------------------------------------------------------
# 内部可变跟踪状态
# ---------------------------------------------------------------------------


class _TrackState:
    """单 track 的可变累加状态；最终序列化成不可变 TrackedFace 进入 snapshot。"""

    __slots__ = (
        "track_id", "box", "age_frames", "hit_count", "miss_count",
        "smoothed_cx", "smoothed_cy", "_hit_history",
        "first_seen_ts", "last_seen_ts",
    )

    def __init__(self, track_id: int, box: FaceBox, ts: float, window: int) -> None:
        self.track_id = track_id
        self.box = box
        self.age_frames = 1
        self.hit_count = 1
        self.miss_count = 0
        self.smoothed_cx = float(box.cx)
        self.smoothed_cy = float(box.cy)
        self._hit_history: collections.deque = collections.deque([True], maxlen=window)
        self.first_seen_ts = ts
        self.last_seen_ts = ts

    def update_hit(self, box: FaceBox, ts: float, alpha: float) -> None:
        self.box = box
        self.age_frames += 1
        self.hit_count += 1
        self.miss_count = 0
        self.smoothed_cx = (1.0 - alpha) * self.smoothed_cx + alpha * float(box.cx)
        self.smoothed_cy = (1.0 - alpha) * self.smoothed_cy + alpha * float(box.cy)
        self._hit_history.append(True)
        self.last_seen_ts = ts

    def update_miss(self) -> None:
        self.age_frames += 1
        self.miss_count += 1
        self._hit_history.append(False)

    def presence_score(self) -> float:
        if not self._hit_history:
            return 0.0
        return sum(1 for h in self._hit_history if h) / float(len(self._hit_history))

    def to_tracked(self) -> TrackedFace:
        return TrackedFace(
            track_id=self.track_id,
            box=self.box,
            age_frames=self.age_frames,
            hit_count=self.hit_count,
            miss_count=self.miss_count,
            smoothed_cx=self.smoothed_cx,
            smoothed_cy=self.smoothed_cy,
            presence_score=self.presence_score(),
            first_seen_ts=self.first_seen_ts,
            last_seen_ts=self.last_seen_ts,
        )


# ---------------------------------------------------------------------------
# 默认环境变量解析
# ---------------------------------------------------------------------------


def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    v = os.environ.get(key)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _bool_env_face_id_real(env: Any) -> bool:
    """vision-008: ``COCO_FACE_ID_REAL=1/true/yes/on`` → True，否则 False。

    default-OFF：未设 / 任意其它值 → False，``get_face_id`` 返回 None（与
    companion-012 stub 路径 bytewise 等价）。
    """
    raw = (env.get("COCO_FACE_ID_REAL") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# FaceTracker
# ---------------------------------------------------------------------------


_PRIMARY_STRATEGIES = ("area", "nearest_to_last", "longest_lived")


class FaceTracker:
    """后台人脸跟踪。

    用法：
        tracker = FaceTracker(stop_event, camera_spec="image:.../single_face.jpg")
        tracker.start()
        snap = tracker.latest()
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
        # presence hysteresis (vision-002: K/J 与原 absence_min_misses/presence_min_hits 等价)
        presence_window: int = 5,
        presence_min_hits: Optional[int] = None,    # J: False→True 触发
        absence_min_misses: Optional[int] = None,   # K: True→False 触发
        # vision-002 IoU tracking
        iou_threshold: float = 0.3,
        max_track_misses: int = 3,
        track_history_window: int = 10,
        smoothing_alpha: float = 0.4,
        primary_strategy: str = "area",
        primary_switch_min_frames: int = 3,
        # vision-003 face-id（可选注入；默认 None 不识别）
        face_id_classifier: Optional[Any] = None,
        # vision-008: face_id 真接 emit hook（None 时 fail-soft 不 emit）
        emit_fn: Optional[Callable[..., None]] = None,
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

        # presence hysteresis 参数（环境变量可覆盖默认值）
        if not (1 <= presence_window <= 60):
            raise ValueError(f"presence_window={presence_window} 不合法 [1,60]")
        # vision-002 默认：J=2 (False→True), K=10 (True→False)
        # 兼容 companion-002：若调用方仍传 absence_min_misses，限其 ≤ presence_window
        env_J = _env_int("COCO_FACE_PRESENCE_MIN_HITS",
                         presence_min_hits if presence_min_hits is not None else 2)
        env_K = _env_int("COCO_FACE_ABSENCE_MIN_MISSES",
                         absence_min_misses if absence_min_misses is not None else 10)
        if not (1 <= env_J <= max(presence_window, env_J)):
            raise ValueError(f"presence_min_hits={env_J} 不合法")
        if not (1 <= env_K):
            raise ValueError(f"absence_min_misses={env_K} 不合法 (>=1)")
        self._presence_window = max(presence_window, env_J, min(env_K, 60))
        self._presence_min_hits = env_J  # J
        # M1 fix: K 必须 ≤ presence_window，否则窗口永远无法累积到 K 个 miss，
        # presence 永不衰减回 False（用户设 K>60 时尤其明显）
        self._absence_min_misses = min(env_K, self._presence_window)  # K

        # IoU tracking 参数
        self._iou_threshold = float(_env_float("COCO_FACE_IOU_THRESHOLD", iou_threshold))
        if not (0.05 <= self._iou_threshold <= 0.95):
            raise ValueError(f"iou_threshold={self._iou_threshold} 不合法 [0.05,0.95]")
        self._max_track_misses = int(_env_int("COCO_FACE_MAX_TRACK_MISSES", max_track_misses))
        if self._max_track_misses < 1:
            raise ValueError("max_track_misses 必须 >= 1")
        self._track_history_window = max(1, int(track_history_window))
        self._smoothing_alpha = float(smoothing_alpha)
        if not (0.0 < self._smoothing_alpha <= 1.0):
            raise ValueError(f"smoothing_alpha={self._smoothing_alpha} 不合法 (0,1]")
        if primary_strategy not in _PRIMARY_STRATEGIES:
            raise ValueError(f"primary_strategy={primary_strategy} 不在 {_PRIMARY_STRATEGIES}")
        self._primary_strategy = os.environ.get(
            "COCO_FACE_PRIMARY_STRATEGY", primary_strategy
        )
        if self._primary_strategy not in _PRIMARY_STRATEGIES:
            self._primary_strategy = "area"
        self._primary_switch_min_frames = int(
            _env_int("COCO_FACE_PRIMARY_SWITCH_MIN_FRAMES", primary_switch_min_frames)
        )
        if self._primary_switch_min_frames < 1:
            self._primary_switch_min_frames = 1

        self.stats = FaceTrackerStats()

        self._lock = threading.Lock()
        self._snapshot = FaceSnapshot()
        # 全局 hit 历史（presence hysteresis 输入，向后兼容旧实现）
        self._hit_history: collections.deque = collections.deque(maxlen=self._presence_window)
        self._present = False

        # tracks 状态
        self._tracks: List[_TrackState] = []
        self._next_track_id = 1
        self._current_primary_id: Optional[int] = None
        # primary 切换候选累计：candidate_id → 连续帧支持数
        self._primary_candidate_id: Optional[int] = None
        self._primary_candidate_frames: int = 0
        # 上一帧 primary 中心，用于 nearest_to_last 策略
        self._last_primary_center: Optional[Tuple[float, float]] = None

        self._thread: Optional[threading.Thread] = None

        # vision-003 face-id（可选）
        self._face_id_classifier = face_id_classifier

        # vision-008: name → stable face_id 映射 + emit hook + env gate
        # default-OFF：未设 COCO_FACE_ID_REAL=1 时 get_face_id 始终返回 None
        # （与 companion-012 stub 路径 bytewise 等价）。
        self._emit_fn: Optional[Callable[..., None]] = emit_fn
        self._face_id_map: Dict[str, str] = {}
        self._face_id_lock = threading.Lock()
        self._face_id_real_enabled: bool = _bool_env_face_id_real(os.environ)

    # --- public ---
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            log.warning("FaceTracker already running")
            return
        if self._camera is None:
            self._camera = open_camera(self._camera_spec)
        self.stats = FaceTrackerStats(started_at=time.time())
        self._hit_history.clear()
        self._present = False
        self._tracks = []
        self._next_track_id = 1
        self._current_primary_id = None
        self._primary_candidate_id = None
        self._primary_candidate_frames = 0
        self._last_primary_center = None
        with self._lock:
            self._snapshot = FaceSnapshot()
        self._thread = threading.Thread(target=self._run, name="coco-face-tracker", daemon=True)
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        if not self._camera_external and self._camera is not None:
            try:
                self._camera.release()
            except Exception:  # noqa: BLE001
                pass
            self._camera = None

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def latest(self) -> FaceSnapshot:
        with self._lock:
            return self._snapshot

    # infra-012-fu-1: 真共享 camera ref API。
    #
    # 背景：infra-012 用 mutable list[0] write-back 让 self_heal reopen 后把新
    # CameraSource 透回 FaceTracker；但 FaceTracker 内部 _tick 读的是
    # ``self._camera`` 属性，list 与属性并不是 alias —— sim 通过是因为同进程
    # 内 fake CameraSource 复用了同一对象 id，真机 USB 路径下 release 老
    # handle 再 open 新 handle 时，FaceTracker 仍读旧 ref（已释放）就会崩。
    #
    # 修复：暴露公开 API ``swap_camera(new_cam)``，在 ``self._lock`` 内原子
    # 替换 ``self._camera``；旧 handle 关闭的责任由调用方决定（self_heal_wire
    # 已在 swap 前 release 老 handle，详见 coco/infra/self_heal_wire.py
    # _camera_reopen 路径）。``new_cam`` 允许 None（用于 teardown 路径）。
    #
    # 线程安全：``_tick`` 读取 ``self._camera`` 不持锁 —— Python 属性赋值是
    # 原子的（PEP 8 / CPython 实现），所以即使 swap 与 read 并发，最坏只是
    # _tick 这一帧拿到旧 ref（已 release）触发一次 read 失败被
    # FaceTracker 自己的 ``stats.error_count`` 兜底，下一帧自然切到新 ref。
    # 我们仍走 ``self._lock`` 防御多线程并发 swap。
    def swap_camera(self, new_camera: Optional[CameraSource]) -> Optional[CameraSource]:
        """原子替换内部 camera 引用。

        Returns
        -------
        Optional[CameraSource]
            原先持有的 camera 实例（调用方据此决定是否 release；
            self_heal_wire 已先 release 老 handle，本方法不重复 release）。
        """
        with self._lock:
            old = self._camera
            self._camera = new_camera
            # swap 后 external 语义保留：外部注入 handle 时，FaceTracker
            # 之前是不负责 release 的；swap 进来的新 handle 同样视为外部
            # 持有（self_heal_wire 持有 ref 并负责生命周期）。
            self._camera_external = True
        return old

    # vision-008: face_id 真接接口 + default-OFF gate。
    # vision-009: classifier vs sha1 分歧 lock-once policy + emit 注入分歧统计。
    #
    # 设计：
    #   - default-OFF：未设 ``COCO_FACE_ID_REAL=1`` → 返回 None（与 companion-012
    #     fu-2 stub 路径 bytewise 等价；上层 resolver 仍走 fallback to name）。
    #   - 启用后：维护 name → stable face_id 字符串映射。
    #     * 若**首次**为 ``name`` 解析时 ``face_id_classifier`` 已注入且 store 中
    #       能查到该 name → 缓存 ``"fid_<user_id>"`` （跨进程稳定 by FaceIDStore）。
    #     * 否则首次解析时为该 name 生成 ``"fid_<sha1(name)[:8]>"`` 并缓存
    #       （进程内确定、跨进程也确定，因为只依赖 name）。
    #     * 同一 ``name`` 在同一进程内始终返回同一 face_id。
    #
    # vision-009 lock-once policy（caveat #2 polish）：
    #   一旦某 ``name`` 已绑定 face_id（无论 classifier 还是 sha1 路径），后续
    #   classifier **注入 / 替换 / 失效**都**不再重新绑定**。理由：
    #     1. face_id 是跨子系统的稳定 id（GroupMode / preference / memory 都会
    #        以它为 key 持久化），重绑会导致历史绑定失效或被 silent 错配；
    #     2. 真机典型场景里 classifier store 在构造前 hydrate 完毕，运行期 swap
    #        是异常路径，与其重发 ``vision.face_id_resolved`` event 让下游处理
    #        id 迁移，不如硬锁；
    #     3. 想要刷新 → 重启进程或清空 ``_face_id_map``（不暴露公开 API，
    #        避免被业务层误用）。
    #   注入分歧（即 classifier 后注入但 cache 已有 sha1）发生时记一次
    #   ``stats.face_id_classifier_late_inject_skipped`` 计数 + warn log 一次，
    #   便于运维发现配置异常。
    #
    # vision-009 emit_fn wire（caveat #3 polish）：
    #   - emit_fn 的签名约定为 ``emit_fn(component_event: str, message: str = "",
    #     **payload) -> None``，与 ``coco.logging_setup.emit`` 完全对齐。
    #     调用方（``coco/main.py``）直接把 ``logging_setup.emit`` 透传即可。
    #   - 首次为某 name 解析出 face_id 时 emit
    #     ``"vision.face_id_resolved"``（component=vision, event=face_id_resolved,
    #     payload: name=<str>, face_id=<str>, source=<"classifier"|"sha1">）。
    #     emit_fn 为 None 时 fail-soft 不 emit，不破坏 sim / 默认路径。
    #
    # schema (face_id payload)：
    #   - ``face_id``: str，形如 ``"fid_<token>"``；同 name 多次解析稳定不变
    #   - 上层 GroupModeCoordinator 通过 ``profile_id_resolver(name)`` 调用本方法
    def get_face_id(self, name: Optional[str]) -> Optional[str]:
        """根据已识别 name 返回稳定 face_id。

        Default-OFF（``COCO_FACE_ID_REAL`` 未设）→ 始终返回 None，
        与 companion-012 fu-2 stub 路径 bytewise 等价。

        ``name_confidence`` (TrackedFace 字段) 与 ``face_id`` **正交**：
        前者是 classifier 给出该帧识别 name 的置信度（vision-003），仅影响
        是否把 name 写回 snapshot；后者是基于已经写回的 name 计算的稳定 id，
        一旦绑定 lock-once。两者读路径独立——下游想看置信度看 TrackedFace.name_confidence，
        想看稳定 id 调 face_tracker.get_face_id(name)。
        """
        if not name:
            return None
        if not self._face_id_real_enabled:
            return None
        # vision-009 lock-once：已缓存直接返回，不论 classifier 状态如何变更
        with self._face_id_lock:
            cached = self._face_id_map.get(name)
            if cached is not None:
                # 注入分歧检测：如果 cached 是 sha1 路径，且现在 classifier 已能
                # 查到该 name，记一次跳过事件（不重绑，仅 stats + warn 一次）
                if (
                    cached.startswith("fid_")
                    and not cached[4:].isdigit()  # sha1 hex；fid_<user_id> 是数字
                    and self._face_id_classifier is not None
                ):
                    self._maybe_log_late_inject_skip(name, cached)
                return cached
        # 计算 face_id（首次解析）
        fid: Optional[str] = None
        source: str = "sha1"
        clf = self._face_id_classifier
        if clf is not None:
            try:
                store = getattr(clf, "store", None)
                recs = store.all_records() if store is not None else {}
                for uid, rec in recs.items():
                    if getattr(rec, "name", None) == name:
                        fid = f"fid_{int(uid)}"
                        source = "classifier"
                        break
            except Exception as e:  # noqa: BLE001
                log.warning("FaceTracker get_face_id store lookup failed: %s: %s",
                            type(e).__name__, e)
        if fid is None:
            # fallback：sha1(name)[:8] —— 进程内 / 跨进程都确定，与 classifier 解耦
            fid = "fid_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
            source = "sha1"
        # 写入缓存 + emit (lock-once：极少数并发首解析竞态时以已存在的为准)
        with self._face_id_lock:
            existed = self._face_id_map.get(name)
            if existed is None:
                self._face_id_map[name] = fid
                first_time = True
            else:
                fid = existed
                first_time = False
        if first_time and self._emit_fn is not None:
            try:
                # vision-009: emit_fn 签名对齐 coco.logging_setup.emit
                #   emit("vision.face_id_resolved", message="", **payload)
                self._emit_fn(
                    "vision.face_id_resolved",
                    "",
                    name=name,
                    face_id=fid,
                    source=source,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("FaceTracker emit face_id_resolved failed: %s: %s",
                            type(e).__name__, e)
        return fid

    def _maybe_log_late_inject_skip(self, name: str, cached_fid: str) -> None:
        """vision-009: 注入分歧统计 — classifier 后注入但 cache 已锁 sha1 时记一次。

        每个 name 仅 warn 一次（用 stats set），避免日志风暴。stats 计数
        会持续累加（便于运维监控）。
        """
        if not hasattr(self.stats, "face_id_classifier_late_inject_skipped"):
            return
        seen = getattr(self, "_late_inject_warned_names", None)
        if seen is None:
            seen = set()
            self._late_inject_warned_names = seen
        self.stats.face_id_classifier_late_inject_skipped += 1
        if name not in seen:
            seen.add(name)
            log.warning(
                "FaceTracker classifier late-inject ignored for name=%r "
                "(cached=%s, lock-once policy)",
                name, cached_fid,
            )

    # --- 测试钩子：纯函数地喂 detections，便于合成测试不依赖摄像头 ---
    def feed_detections(
        self,
        boxes: List[FaceBox],
        frame_w: int = 320,
        frame_h: int = 240,
        ts: Optional[float] = None,
    ) -> FaceSnapshot:
        """直接注入 detect 结果，跑一遍 tracking + presence + snapshot 更新。

        verification 用此口子做 IoU / hysteresis / primary 切换的确定性测试，
        不必经过摄像头与 cv2 detect。
        """
        if ts is None:
            ts = time.monotonic()
        self._process_detections(boxes, frame_w, frame_h, ts)
        return self.latest()

    # --- internals ---
    def _run(self) -> None:
        log.info(
            "FaceTracker started fps=%.1f window=%d J=%d K=%d iou=%.2f miss=%d strat=%s",
            self._fps, self._presence_window, self._presence_min_hits,
            self._absence_min_misses, self._iou_threshold, self._max_track_misses,
            self._primary_strategy,
        )
        try:
            while not self.stop_event.is_set():
                t0 = time.monotonic()
                self._tick()
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

        h, w = frame.shape[:2]
        self._process_detections(list(faces), int(w), int(h), time.monotonic())
        # vision-003: primary face → identify
        self._maybe_identify(frame, faces)

    def _maybe_identify(self, frame, faces) -> None:
        """对 primary face 跑一次 face-id；patch snapshot.primary_track.name/confidence。"""
        if self._face_id_classifier is None:
            return
        with self._lock:
            snap = self._snapshot
        pt = snap.primary_track
        if pt is None or snap.primary is None:
            return
        try:
            box = snap.primary
            x1, y1 = max(0, int(box.x)), max(0, int(box.y))
            x2, y2 = min(frame.shape[1], int(box.x + box.w)), min(frame.shape[0], int(box.y + box.h))
            if x2 <= x1 or y2 <= y1:
                return
            crop = frame[y1:y2, x1:x2]
            name, conf = self._face_id_classifier.identify(crop)
        except Exception as e:  # noqa: BLE001
            log.warning("FaceTracker face-id identify failed: %s: %s", type(e).__name__, e)
            return
        # vision-003 L1 fix: identify() 跑在锁外，回填时必须重新按 track_id
        # 在最新快照里查找 TrackedFace 实例；若 track 已被淘汰或 id 已变，
        # 丢弃这次识别结果，避免 lost-update / patch 到错误对象。
        with self._lock:
            cur_snap = self._snapshot
            cur_pt = cur_snap.primary_track
            if cur_pt is None or cur_pt.track_id != pt.track_id:
                return
            # 也要保证 tracks 里仍存在该 track_id（防御性）
            if not any(t.track_id == pt.track_id for t in cur_snap.tracks):
                return
            new_pt = TrackedFace(
                track_id=cur_pt.track_id,
                box=cur_pt.box,
                age_frames=cur_pt.age_frames,
                hit_count=cur_pt.hit_count,
                miss_count=cur_pt.miss_count,
                smoothed_cx=cur_pt.smoothed_cx,
                smoothed_cy=cur_pt.smoothed_cy,
                presence_score=cur_pt.presence_score,
                first_seen_ts=cur_pt.first_seen_ts,
                last_seen_ts=cur_pt.last_seen_ts,
                name=name,
                name_confidence=float(conf),
            )
            new_tracks = tuple(
                new_pt if t.track_id == cur_pt.track_id else t
                for t in cur_snap.tracks
            )
            new_snap = FaceSnapshot(
                faces=cur_snap.faces,
                frame_w=cur_snap.frame_w,
                frame_h=cur_snap.frame_h,
                present=cur_snap.present,
                primary=cur_snap.primary,
                ts=cur_snap.ts,
                detect_count=cur_snap.detect_count,
                hit_count=cur_snap.hit_count,
                tracks=new_tracks,
                primary_track=new_pt,
            )
            self._snapshot = new_snap

    def _process_detections(
        self,
        faces: List[FaceBox],
        frame_w: int,
        frame_h: int,
        ts: float,
    ) -> None:
        self.stats.detect_count += 1
        hit = len(faces) > 0
        if hit:
            self.stats.hit_count += 1
        self._hit_history.append(hit)

        # 1) IoU greedy 匹配 detections ↔ existing tracks
        self._match_and_update_tracks(faces, ts)

        # 2) presence hysteresis（基于全局帧级命中历史）
        self._update_presence()

        # 3) 主脸选择（含切换迟滞）
        primary_track = self._select_primary()

        # 4) 拼装 snapshot（向后兼容字段不变）
        primary_box = primary_track.box if primary_track is not None else None
        # companion-002 行为：primary 仅在 present=True 时暴露
        if not self._present:
            primary_box = None
        snap = FaceSnapshot(
            faces=tuple(faces),
            frame_w=frame_w,
            frame_h=frame_h,
            present=self._present,
            primary=primary_box,
            ts=ts,
            detect_count=self.stats.detect_count,
            hit_count=self.stats.hit_count,
            tracks=tuple(t.to_tracked() for t in self._tracks),
            primary_track=primary_track if self._present else None,
        )
        with self._lock:
            self._snapshot = snap

    def _match_and_update_tracks(self, faces: List[FaceBox], ts: float) -> None:
        """Greedy IoU 匹配：每次取剩余 (track, det) 对中 IoU 最大且 >= 阈值的一对绑定。"""
        if not self._tracks and not faces:
            return

        unmatched_tracks = list(range(len(self._tracks)))
        unmatched_dets = list(range(len(faces)))

        # 计算所有候选 IoU
        candidates: List[Tuple[float, int, int]] = []
        for ti in unmatched_tracks:
            for di in unmatched_dets:
                v = iou_xywh(self._tracks[ti].box, faces[di])
                if v >= self._iou_threshold:
                    candidates.append((v, ti, di))
        # 按 IoU 降序贪心
        candidates.sort(key=lambda x: x[0], reverse=True)
        assigned_t: set = set()
        assigned_d: set = set()
        for v, ti, di in candidates:
            if ti in assigned_t or di in assigned_d:
                continue
            self._tracks[ti].update_hit(faces[di], ts, self._smoothing_alpha)
            assigned_t.add(ti)
            assigned_d.add(di)

        # 未匹配 track → miss++
        for ti in range(len(self._tracks)):
            if ti not in assigned_t:
                self._tracks[ti].update_miss()

        # 未匹配 detection → 新 track
        for di in range(len(faces)):
            if di not in assigned_d:
                tid = self._next_track_id
                self._next_track_id += 1
                self._tracks.append(_TrackState(tid, faces[di], ts, self._track_history_window))
                self.stats.tracks_created += 1

        # 清理连续 miss 过阈值的 track
        kept: List[_TrackState] = []
        for t in self._tracks:
            if t.miss_count >= self._max_track_misses:
                self.stats.tracks_dropped += 1
                if self._current_primary_id == t.track_id:
                    self._current_primary_id = None
                if self._primary_candidate_id == t.track_id:
                    self._primary_candidate_id = None
                    self._primary_candidate_frames = 0
            else:
                kept.append(t)
        self._tracks = kept

    def _update_presence(self) -> None:
        """全局 hysteresis：True→False 需 K 连续 miss；False→True 需 J 连续 hit。

        注意：这里"连续"基于 _hit_history 末尾连续段，比"窗口内总数"更符合
        spec "K 帧连续 0 face / J 帧连续 ≥1 face" 描述。
        """
        if not self._hit_history:
            return
        # 计算末尾连续段
        last = self._hit_history[-1]
        run = 0
        for v in reversed(self._hit_history):
            if v == last:
                run += 1
            else:
                break
        if not self._present and last is True and run >= self._presence_min_hits:
            self._present = True
            log.info("FaceTracker presence ↑ TRUE (consecutive hits=%d/J=%d)",
                     run, self._presence_min_hits)
        elif self._present and last is False and run >= self._absence_min_misses:
            self._present = False
            log.info("FaceTracker presence ↓ FALSE (consecutive misses=%d/K=%d)",
                     run, self._absence_min_misses)

    def _select_primary(self) -> Optional[TrackedFace]:
        """根据策略选主脸；切换需 ``primary_switch_min_frames`` 连续支持。"""
        if not self._tracks:
            self._current_primary_id = None
            self._primary_candidate_id = None
            self._primary_candidate_frames = 0
            self._last_primary_center = None
            return None

        # 候选最优 track
        best = self._compute_best_track()
        if best is None:
            return None

        # 当前 primary 仍存在？
        cur: Optional[_TrackState] = None
        if self._current_primary_id is not None:
            for t in self._tracks:
                if t.track_id == self._current_primary_id:
                    cur = t
                    break

        if cur is None:
            # 没有 primary（首次 / 上一 primary 已 drop）→ 直接采纳 best
            self._current_primary_id = best.track_id
            self._primary_candidate_id = None
            self._primary_candidate_frames = 0
            self._last_primary_center = (best.smoothed_cx, best.smoothed_cy)
            self.stats.primary_switches += 1
            return best.to_tracked()

        if best.track_id == cur.track_id:
            # 当前 primary 仍是最优 → 重置候选
            self._primary_candidate_id = None
            self._primary_candidate_frames = 0
            self._last_primary_center = (cur.smoothed_cx, cur.smoothed_cy)
            return cur.to_tracked()

        # 出现挑战者 → 累计连续支持帧
        if self._primary_candidate_id == best.track_id:
            self._primary_candidate_frames += 1
        else:
            self._primary_candidate_id = best.track_id
            self._primary_candidate_frames = 1

        if self._primary_candidate_frames >= self._primary_switch_min_frames:
            log.info("FaceTracker primary switch %s → %s (after %d frames)",
                     self._current_primary_id, best.track_id, self._primary_candidate_frames)
            self._current_primary_id = best.track_id
            self._primary_candidate_id = None
            self._primary_candidate_frames = 0
            self._last_primary_center = (best.smoothed_cx, best.smoothed_cy)
            self.stats.primary_switches += 1
            return best.to_tracked()

        # 还没切，继续维持当前 primary
        self._last_primary_center = (cur.smoothed_cx, cur.smoothed_cy)
        return cur.to_tracked()

    def _compute_best_track(self) -> Optional[_TrackState]:
        if not self._tracks:
            return None
        strat = self._primary_strategy
        if strat == "longest_lived":
            return max(self._tracks, key=lambda t: (t.hit_count, t.box.w * t.box.h))
        if strat == "nearest_to_last" and self._last_primary_center is not None:
            lx, ly = self._last_primary_center
            return min(
                self._tracks,
                key=lambda t: (t.smoothed_cx - lx) ** 2 + (t.smoothed_cy - ly) ** 2,
            )
        # default: area
        return max(self._tracks, key=lambda t: t.box.w * t.box.h)


__all__ = [
    "FaceSnapshot",
    "FaceTracker",
    "FaceTrackerStats",
    "TrackedFace",
    "iou_xywh",
]
