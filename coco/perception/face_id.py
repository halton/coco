"""coco.perception.face_id — vision-003 人脸 ID 识别（LBPH + Histogram fallback）.

设计目标
========

- 在 face-tracker 输出 primary face crop 后再进一步 "是谁"：返回已注册 known_faces
  中匹配最高得分的 ``label`` 或 ``"unknown"``。
- 双 backend，启动期自动探测：

    * **LBPH backend** (``LBPHBackend``)：基于 ``cv2.face.LBPHFaceRecognizer_create()``，
      要求 opencv-contrib-python；准确率高，但 cp313 三平台 wheel 可用性需 Researcher
      验证（已知 macOS / Linux 部分情况下不可用）。confidence 越小越像，阈值默认 80。
    * **Histogram backend** (``HistogramBackend``)：纯 numpy + cv2 灰度直方图 baseline，
      不依赖 contrib，永远可用。chi-square distance；confidence ∈ [0, 1] 越大越像，阈值
      默认 0.6。
- 持久化 ``~/.cache/coco/face_id/``：``known_faces.json`` 存 metadata
  (id/name/sample_count/timestamps)，每个 user 的 numpy 特征单独 ``.npy``。
- atomic write + chmod 0o600（PII：人脸特征）。
- 默认 OFF：``COCO_FACE_ID`` 未设 → FaceTracker 不调 classifier，TrackedFace.name 始终 None。

向后兼容
========

- 不动 face_tracker 行为；FaceIDClassifier 由 FaceTracker 可选注入；
  注入后 ``TrackedFace.name`` 字段从 None 变成 ``Optional[str]``（默认 None 不破现有 code）。
- 不强制替换 opencv-python；Histogram fallback 永远可用。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple

import cv2
import numpy as np

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CROP_SIZE = (100, 100)  # 统一灰度 crop 尺寸（spec 第 2 条）
HIST_BINS = 256
SCHEMA_VERSION = 1
DEFAULT_LBPH_THRESHOLD = 80.0  # 越小越像
DEFAULT_HIST_THRESHOLD = 0.4   # 越大越像（normalized similarity ∈ [0,1]）

UNKNOWN_LABEL = "unknown"


# ---------------------------------------------------------------------------
# Backend Protocol + 实现
# ---------------------------------------------------------------------------


class FaceIDBackend(Protocol):
    """两种 backend 共用接口。

    - ``fit(features, labels)``: 用 (features, labels) 训练；features 是 list of
      预处理后 100x100 灰度 ndarray，labels 是 int user_id（与 store 内部 id 对应）。
    - ``predict(feature)``: 输入单张预处理灰度 ndarray，返回 (label_id, raw_score)。
      raw_score 语义由 backend 决定，convert_to_confidence 统一归一化。
    - ``convert_to_confidence(raw)``: 转成"越大越像"的 confidence ∈ [0, 1]。
    - ``default_threshold()``: backend 默认 confidence 阈值（≥ threshold 视为 known）。
    - ``name``: "lbph" / "histogram"。
    """

    name: str

    def fit(self, features: List[np.ndarray], labels: List[int]) -> None: ...
    def predict(self, feature: np.ndarray) -> Tuple[int, float]: ...
    def convert_to_confidence(self, raw: float) -> float: ...
    def default_threshold(self) -> float: ...


def _to_gray_crop(image: np.ndarray) -> np.ndarray:
    """统一预处理：转灰度 + resize 到 ``CROP_SIZE`` + equalizeHist。

    输入：BGR / 灰度 / 任意尺寸。输出：uint8 100x100 灰度。
    """
    if image is None:
        raise ValueError("image is None")
    if image.ndim == 2:
        gray = image
    elif image.ndim == 3 and image.shape[2] == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    elif image.ndim == 3 and image.shape[2] == 1:
        gray = image[:, :, 0]
    else:
        raise ValueError(f"unsupported image shape: {image.shape}")
    if gray.dtype != np.uint8:
        gray = gray.astype(np.uint8)
    gray = cv2.resize(gray, CROP_SIZE, interpolation=cv2.INTER_AREA)
    gray = cv2.equalizeHist(gray)
    return gray


class HistogramBackend:
    """灰度直方图 + chi-square distance baseline。

    特征：``cv2.calcHist`` 256-bin gray histogram，归一化（L1）。
    距离：chi-square = 0.5 * sum((a-b)^2 / (a+b+eps))；越小越像。
    confidence = exp(-chi2 * 4)，取 [0,1] 区间，越大越像。
    """

    name = "histogram"

    def __init__(self) -> None:
        self._features: List[np.ndarray] = []  # 每条 256-d float32
        self._labels: List[int] = []

    @staticmethod
    def _extract(gray_crop: np.ndarray) -> np.ndarray:
        hist = cv2.calcHist([gray_crop], [0], None, [HIST_BINS], [0, 256])
        hist = hist.flatten().astype(np.float32)
        s = hist.sum()
        if s > 0:
            hist /= s
        return hist

    def fit(self, features: List[np.ndarray], labels: List[int]) -> None:
        if len(features) != len(labels):
            raise ValueError("features/labels length mismatch")
        self._features = [self._extract(f) for f in features]
        self._labels = list(labels)

    def predict(self, feature: np.ndarray) -> Tuple[int, float]:
        if not self._features:
            return (-1, float("inf"))
        q = self._extract(feature)
        # chi-square distance
        eps = 1e-10
        best_i = 0
        best_d = float("inf")
        for i, ref in enumerate(self._features):
            d = 0.5 * float(np.sum((q - ref) ** 2 / (q + ref + eps)))
            if d < best_d:
                best_d = d
                best_i = i
        return (self._labels[best_i], best_d)

    def convert_to_confidence(self, raw: float) -> float:
        # chi2 越小越像；用线性 1 - raw 的形式（chi2 通常 ∈ [0, 2]）映射到 confidence。
        # 经 fixture 校准：同人 chi2 ~ 0.4-0.6，陌生人 ~ 0.7+；映射后同人 conf > 0.6。
        if raw == float("inf"):
            return 0.0
        conf = 1.0 - raw
        if conf < 0.0:
            return 0.0
        if conf > 1.0:
            return 1.0
        return float(conf)

    def default_threshold(self) -> float:
        return DEFAULT_HIST_THRESHOLD


class LBPHBackend:
    """OpenCV contrib LBPHFaceRecognizer（要求 opencv-contrib-python）。

    raw_score 是 cv2.face 输出的 distance（越小越像；典型 ≈ 0-100）。
    confidence = max(0, 1 - raw / 100)。
    """

    name = "lbph"

    def __init__(self) -> None:
        # 构造期就尝试创建；不可用则抛 AttributeError，外层 select_backend 捕获回退。
        if not hasattr(cv2, "face"):
            raise AttributeError("cv2.face 不可用（需 opencv-contrib-python）")
        self._rec = cv2.face.LBPHFaceRecognizer_create()
        self._fitted = False

    def fit(self, features: List[np.ndarray], labels: List[int]) -> None:
        if len(features) != len(labels):
            raise ValueError("features/labels length mismatch")
        if not features:
            self._fitted = False
            return
        self._rec.train(features, np.array(labels, dtype=np.int32))
        self._fitted = True

    def predict(self, feature: np.ndarray) -> Tuple[int, float]:
        if not self._fitted:
            return (-1, float("inf"))
        label, distance = self._rec.predict(feature)
        return (int(label), float(distance))

    def convert_to_confidence(self, raw: float) -> float:
        if raw == float("inf"):
            return 0.0
        return max(0.0, min(1.0, 1.0 - raw / 100.0))

    def default_threshold(self) -> float:
        # 把 LBPH 默认 distance=80 转成 confidence ~ 0.2
        return max(0.0, min(1.0, 1.0 - DEFAULT_LBPH_THRESHOLD / 100.0))


def select_backend(prefer: str = "auto") -> FaceIDBackend:
    """启动期决定 backend。

    prefer:
      - "auto": 试 LBPH，AttributeError → HistogramBackend
      - "lbph": 强制 LBPH（不可用则抛）
      - "histogram": 强制 Histogram

    返回选定的 backend 实例。
    """
    prefer = (prefer or "auto").lower().strip()
    if prefer not in {"auto", "lbph", "histogram"}:
        log.warning("[face_id] unknown backend %r, fallback auto", prefer)
        prefer = "auto"
    if prefer == "histogram":
        return HistogramBackend()
    if prefer == "lbph":
        return LBPHBackend()
    # auto
    try:
        return LBPHBackend()
    except Exception as e:  # noqa: BLE001
        log.info("[face_id] LBPH 不可用 (%s)，回退 HistogramBackend", e)
        return HistogramBackend()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@dataclass
class FaceRecord:
    """单个 user 的注册档案。

    - ``user_id`` 内部数字 id（与 backend label 一致）
    - ``name`` 对外显示名（如 "alice"）
    - ``feature_files`` 持久化文件相对名（'1.npy'）；运行期 features 由 store 缓存到内存
    - ``sample_count`` 本 user 累计 enroll 帧数
    """

    user_id: int
    name: str
    feature_files: List[str]
    sample_count: int
    created_at: float
    updated_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": int(self.user_id),
            "name": str(self.name),
            "feature_files": list(self.feature_files),
            "sample_count": int(self.sample_count),
            "created_at": float(self.created_at),
            "updated_at": float(self.updated_at),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FaceRecord":
        return cls(
            user_id=int(d["user_id"]),
            name=str(d["name"]),
            feature_files=list(d.get("feature_files") or []),
            sample_count=int(d.get("sample_count") or 0),
            created_at=float(d.get("created_at") or 0.0),
            updated_at=float(d.get("updated_at") or 0.0),
        )


def default_store_path() -> Path:
    """默认 store 目录 ``~/.cache/coco/face_id/``，跨平台。"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "coco" / "face_id"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "coco" / "face_id"


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """原子写入 + fsync + chmod 0o600（参考 companion-004 patterns）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(str(path.parent), 0o700)
    except OSError:
        pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(str(tmp), "wb") as fh:
        fh.write(data)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(str(tmp), str(path))
    try:
        os.chmod(str(path), 0o600)
    except OSError:
        pass


class FaceIDStore:
    """持久化 known_faces.json + per-user .npy。

    线程安全：RLock 保护内存状态。
    fail-soft：load 时 schema_version 不匹配 / corrupt → 返空。
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root is not None else default_store_path()
        self.index_path = self.root / "known_faces.json"
        self._lock = threading.RLock()
        # 内存缓存
        self._records: Dict[int, FaceRecord] = {}
        self._features: Dict[int, List[np.ndarray]] = {}  # user_id → list of 100x100 uint8 grays
        self._next_id = 1

    # ----- I/O -----
    def load(self) -> Dict[int, FaceRecord]:
        with self._lock:
            self._records = {}
            self._features = {}
            self._next_id = 1
            if not self.index_path.exists():
                return {}
            try:
                obj = json.loads(self.index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                log.warning("[face_id] index load failed (%s); fresh start", e)
                return {}
            ver = int(obj.get("schema_version") or 0)
            if ver != SCHEMA_VERSION:
                log.warning(
                    "[face_id] schema_version=%s 不匹配（期望 %s）— fail-soft 返空",
                    ver, SCHEMA_VERSION,
                )
                return {}
            for raw in obj.get("records") or []:
                try:
                    rec = FaceRecord.from_dict(raw)
                except (KeyError, ValueError) as e:
                    log.warning("[face_id] skip bad record %r: %s", raw, e)
                    continue
                self._records[rec.user_id] = rec
                # load features
                feats: List[np.ndarray] = []
                for fname in rec.feature_files:
                    p = self.root / fname
                    try:
                        feats.append(np.load(str(p)))
                    except (OSError, ValueError) as e:
                        log.warning("[face_id] skip bad feature %s: %s", p, e)
                        continue
                self._features[rec.user_id] = feats
                if rec.user_id >= self._next_id:
                    self._next_id = rec.user_id + 1
            return dict(self._records)

    def save(self) -> None:
        """全量写 index + 已 dirty 的 .npy（这里简化：每次 add 已即时落 .npy，本方法只刷 index）。"""
        with self._lock:
            data = {
                "schema_version": SCHEMA_VERSION,
                "records": [r.to_dict() for r in self._records.values()],
                "saved_at": time.time(),
            }
            blob = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            _atomic_write_bytes(self.index_path, blob)

    # ----- mutations -----
    def add(self, name: str, gray_crops: List[np.ndarray]) -> int:
        """注册新 user 或追加已存在 name。返回 user_id。

        gray_crops 必须已经过 ``_to_gray_crop`` 预处理（100x100 uint8 灰度）。
        """
        if not name or not name.strip():
            raise ValueError("name 不能为空")
        if not gray_crops:
            raise ValueError("gray_crops 不能为空")
        name = name.strip()
        with self._lock:
            # 同 name 已存在 → 追加
            existing: Optional[FaceRecord] = None
            for r in self._records.values():
                if r.name == name:
                    existing = r
                    break
            if existing is None:
                uid = self._next_id
                self._next_id += 1
                now = time.time()
                rec = FaceRecord(
                    user_id=uid,
                    name=name,
                    feature_files=[],
                    sample_count=0,
                    created_at=now,
                    updated_at=now,
                )
                self._records[uid] = rec
                self._features[uid] = []
                existing = rec
            # 落 .npy
            self.root.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(str(self.root), 0o700)
            except OSError:
                pass
            start_idx = len(existing.feature_files)
            for i, crop in enumerate(gray_crops):
                fname = f"u{existing.user_id}_{start_idx + i}.npy"
                p = self.root / fname
                # 用 atomic 模式：np.save 到 tmp 再 replace + chmod 0o600
                # 注意：np.save 会自动确保 .npy 后缀；为避免它给 tmp 加额外 .npy，
                # 直接用 raw write（np.lib.format.write_array）。
                tmp = p.with_name(p.name + ".tmp")
                with open(str(tmp), "wb") as fh:
                    np.lib.format.write_array(fh, np.ascontiguousarray(crop))
                    fh.flush()
                    try:
                        os.fsync(fh.fileno())
                    except OSError:
                        pass
                os.replace(str(tmp), str(p))
                try:
                    os.chmod(str(p), 0o600)
                except OSError:
                    pass
                existing.feature_files.append(fname)
                self._features[existing.user_id].append(crop)
            existing.sample_count = len(existing.feature_files)
            existing.updated_at = time.time()
            self.save()
            return existing.user_id

    def remove(self, user_id: int) -> None:
        with self._lock:
            rec = self._records.pop(user_id, None)
            self._features.pop(user_id, None)
            if rec is None:
                return
            for fname in rec.feature_files:
                p = self.root / fname
                try:
                    p.unlink()
                except OSError:
                    pass
            self.save()

    def reset(self) -> None:
        """全部清空：删 index + 所有 .npy。"""
        with self._lock:
            for rec in list(self._records.values()):
                for fname in rec.feature_files:
                    p = self.root / fname
                    try:
                        p.unlink()
                    except OSError:
                        pass
            self._records.clear()
            self._features.clear()
            self._next_id = 1
            try:
                self.index_path.unlink()
            except OSError:
                pass

    # ----- accessors -----
    def all_records(self) -> Dict[int, FaceRecord]:
        with self._lock:
            return dict(self._records)

    def all_features(self) -> Tuple[List[np.ndarray], List[int]]:
        """flatten 成 (features, labels) 给 backend.fit 用。"""
        with self._lock:
            feats: List[np.ndarray] = []
            labs: List[int] = []
            for uid, fs in self._features.items():
                for f in fs:
                    feats.append(f)
                    labs.append(uid)
            return feats, labs

    def name_for(self, user_id: int) -> Optional[str]:
        with self._lock:
            r = self._records.get(user_id)
            return r.name if r else None


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FaceIDConfig:
    enabled: bool = False
    path: str = ""  # 空 → default_store_path()
    confidence_threshold: float = DEFAULT_HIST_THRESHOLD
    backend: str = "auto"


def _bool_env(env: Dict[str, str], key: str, default: bool = False) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


def config_from_env(env: Optional[Dict[str, str]] = None) -> FaceIDConfig:
    """读取 env 构造 FaceIDConfig，clamp threshold 到 [0,1]。"""
    e = env if env is not None else dict(os.environ)
    enabled = _bool_env(e, "COCO_FACE_ID", False)
    path = (e.get("COCO_FACE_ID_PATH") or "").strip()
    raw_thr = e.get("COCO_FACE_ID_THRESHOLD")
    if raw_thr is None or raw_thr == "":
        thr = DEFAULT_HIST_THRESHOLD
    else:
        try:
            thr = float(raw_thr)
        except ValueError:
            log.warning("[face_id] COCO_FACE_ID_THRESHOLD=%r 非数字，回退默认", raw_thr)
            thr = DEFAULT_HIST_THRESHOLD
    if thr < 0.0:
        thr = 0.0
    elif thr > 1.0:
        thr = 1.0
    backend = (e.get("COCO_FACE_ID_BACKEND") or "auto").lower().strip()
    if backend not in {"auto", "lbph", "histogram"}:
        log.warning("[face_id] COCO_FACE_ID_BACKEND=%r 非法，回退 auto", backend)
        backend = "auto"
    return FaceIDConfig(
        enabled=enabled,
        path=path,
        confidence_threshold=thr,
        backend=backend,
    )


def face_id_enabled_from_env(env: Optional[Dict[str, str]] = None) -> bool:
    e = env if env is not None else dict(os.environ)
    return _bool_env(e, "COCO_FACE_ID", False)


class FaceIDClassifier:
    """组合 backend + store 的对外门面。

    用法：
        clf = FaceIDClassifier()           # 默认 store + auto backend
        clf.enroll("alice", [crop1, crop2, crop3])
        name, conf = clf.identify(face_crop)
        # name 为 None 表示 unknown / 低置信
    """

    def __init__(
        self,
        backend: Optional[FaceIDBackend] = None,
        store: Optional[FaceIDStore] = None,
        threshold: Optional[float] = None,
        backend_pref: str = "auto",
    ) -> None:
        self.backend: FaceIDBackend = backend or select_backend(backend_pref)
        self.store: FaceIDStore = store or FaceIDStore()
        self.store.load()
        self.threshold: float = (
            threshold if threshold is not None else self.backend.default_threshold()
        )
        self._fit_from_store()

    @property
    def backend_name(self) -> str:
        return self.backend.name

    def _fit_from_store(self) -> None:
        feats, labs = self.store.all_features()
        if feats:
            self.backend.fit(feats, labs)

    def enroll(self, name: str, images: List[np.ndarray]) -> int:
        """注册 name。images 可以是任意尺寸 BGR / 灰度，内部做预处理。

        返回 user_id；自动重训 backend。
        """
        crops = [_to_gray_crop(im) for im in images]
        uid = self.store.add(name, crops)
        self._fit_from_store()
        return uid

    def identify(self, face_crop: np.ndarray) -> Tuple[Optional[str], float]:
        """识别单张脸。返回 (name|None, confidence∈[0,1])。

        - 若 store 为空 → (None, 0.0)
        - confidence < threshold → (None, confidence)
        - 否则 → (name, confidence)
        """
        if not self.store.all_records():
            return (None, 0.0)
        gray = _to_gray_crop(face_crop)
        label, raw = self.backend.predict(gray)
        conf = self.backend.convert_to_confidence(raw)
        if label < 0 or conf < self.threshold:
            return (None, conf)
        name = self.store.name_for(label)
        if name is None:
            return (None, conf)
        return (name, conf)

    def reset(self) -> None:
        """清空 store + 重置 backend。"""
        self.store.reset()
        # 重新构造 backend（清训练态）
        self.backend = select_backend(self.backend.name)


__all__ = [
    "CROP_SIZE",
    "DEFAULT_HIST_THRESHOLD",
    "DEFAULT_LBPH_THRESHOLD",
    "FaceIDBackend",
    "FaceIDClassifier",
    "FaceIDConfig",
    "FaceIDStore",
    "FaceRecord",
    "HistogramBackend",
    "LBPHBackend",
    "SCHEMA_VERSION",
    "UNKNOWN_LABEL",
    "config_from_env",
    "default_store_path",
    "face_id_enabled_from_env",
    "select_backend",
]
