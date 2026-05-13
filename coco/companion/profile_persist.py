"""coco.companion.profile_persist — companion-008 跨 session UserProfile 持久化.

设计目标
========

把 companion-004 的 ``UserProfile``（昵称/兴趣/目标）和 interact-009 的
``DialogMemory._summary``（最近 N 轮对话摘要）落盘到 ``~/.coco/profiles/``，
重启 Coco 后按 face_id → profile_id 自动 hydrate 回内存。

关键约束（来自 feature_list.json companion-008.verification）
------------------------------------------------------------

- env ``COCO_PROFILE_PERSIST=1``（默认 OFF）控制开关；env=0 时本模块完全不
  介入：Coco 仍走 companion-004 的 ``ProfileStore`` / companion-006 的
  ``MultiProfileStore`` 既有路径，行为零变化。
- profile_id = ``sha1(face_id + nickname_normalized).hexdigest()[:12]``——
  跨进程稳定（companion-006 的 Python ``hash()`` 不稳定，PYTHONHASHSEED 每次
  启动随机），收割 companion-006 L1-3 followup。
- 持久化路径 ``~/.coco/profiles/<profile_id>.json``；schema_version=1。
- 持久化字段：profile_id / nickname / interests / created_ts / updated_ts /
  dialog_summary（最近 N 轮摘要，N=10）。
- atomic write：tmp + ``os.replace``；并发 save 串行化（threading.RLock）。
- hydrate：扫描 ``~/.coco/profiles/*.json``：
  - JSON 解析失败 → 移到 ``~/.coco/profiles/_corrupt/<id>.json.bak`` + emit
    ``profile.corrupt``；
  - schema_version 不匹配 → 移到 ``~/.coco/profiles/_legacy_v<n>/<id>.json``
    + emit ``profile.schema_mismatch``；
  - 启动继续，绝不阻塞。
- 路径 sanitize：profile_id 必须匹配 ``^[0-9a-f]{12}$``，否则拒绝 save/load
  （防 ``../etc/passwd`` 注入）。
- emit ``profile.hydrated`` / ``profile.persisted``。

线程模型
--------

- 写入可能在 ``ProfileSwitcher`` 切换瞬间或 vision tick 线程触发；本模块
  内部 RLock 串行 save/load/hydrate。
- 写入是同步的（fsync + os.replace），切换瞬间可能阻塞几 ms；可接受
  （ProfileSwitcher 切换非热路径）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional


log = logging.getLogger(__name__)


SCHEMA_VERSION = 1
PROFILE_ID_LEN = 12
PROFILE_ID_RE = re.compile(r"^[0-9a-f]{" + str(PROFILE_ID_LEN) + r"}$")
DEFAULT_DIALOG_SUMMARY_KEEP = 10  # 最近 N 条摘要保留（含 dialog_summary 文本本身或派生）


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def _bool_env(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def profile_persist_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    """COCO_PROFILE_PERSIST=1 → 启用持久化层；默认 OFF。"""
    e = env if env is not None else os.environ
    return _bool_env(e, "COCO_PROFILE_PERSIST", False)


def default_persist_root(env: Optional[Mapping[str, str]] = None) -> Path:
    """默认根目录 ``~/.coco/profiles/``；env ``COCO_PROFILE_PERSIST_ROOT`` 可覆盖
    （绝对/相对均可，相对路径相对 cwd）。
    """
    e = env if env is not None else os.environ
    override = e.get("COCO_PROFILE_PERSIST_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".coco" / "profiles"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def normalize_nickname(name: Optional[str]) -> str:
    """nickname 规范化：strip + NFKC + lower。空 → ""。

    这一步不是文件名 sanitize；只是把 "Alice"/"alice"/"ＡＬＩＣＥ" 映射到同
    一字符串，给 sha1 喂入。允许任何 unicode 字符。
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", str(name)).strip().lower()
    return s


def compute_profile_id(face_id: Optional[str], nickname: Optional[str]) -> str:
    """profile_id = sha1(face_id + "\\x00" + nickname_normalized).hexdigest()[:12]

    - 跨进程稳定（不依赖 Python ``hash()`` PYTHONHASHSEED）
    - 用 NUL 分隔 face_id 与 nickname，避免 ("ab", "c") 与 ("a", "bc") 撞 sha
    - face_id=None / "" 与 nickname=None / "" 都允许（hydrate 仍可拼出 id）
    - 输出固定 12 hex；满足 ``PROFILE_ID_RE``
    """
    fid = (face_id or "").strip()
    nick = normalize_nickname(nickname)
    payload = f"{fid}\x00{nick}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:PROFILE_ID_LEN]


def is_valid_profile_id(pid: str) -> bool:
    return bool(pid) and PROFILE_ID_RE.match(pid) is not None


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


@dataclass
class PersistedProfile:
    profile_id: str
    nickname: Optional[str] = None
    interests: List[str] = field(default_factory=list)
    goals: List[str] = field(default_factory=list)
    created_ts: float = 0.0
    updated_ts: float = 0.0
    dialog_summary: List[str] = field(default_factory=list)  # 最近 N 条 summary 文本
    # companion-009: 偏好关键词 → weight（归一化到 [0,1]）。
    # default-OFF（COCO_PREFER_LEARN）；空 dict 视为"未学过"。
    # 向后兼容：旧 v1 文件无该字段时 from_dict 自动补 {}，schema_version 不变。
    prefer_topics: Dict[str, float] = field(default_factory=dict)
    # companion-010: 情绪告警历史 [{kind, ts, ratio}]。default-OFF
    # （COCO_EMO_MEMORY）；空列表视为"未告警过"。向后兼容：旧文件无该字段
    # 时 from_dict 自动补 []，schema_version 不变。
    emotion_alerts: List[Dict[str, Any]] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # companion-009: prefer_topics 为空 dict 时不落盘 (向后兼容 + V6
        # "default-OFF 时 profile 文件不含 prefer_topics 字段")
        if not self.prefer_topics:
            d.pop("prefer_topics", None)
        # companion-010: 同上，emotion_alerts 空列表不落盘
        if not self.emotion_alerts:
            d.pop("emotion_alerts", None)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PersistedProfile":
        # companion-009: 兼容旧 v1 文件——prefer_topics 缺失视为 {}
        pt_raw = d.get("prefer_topics") or {}
        if isinstance(pt_raw, dict):
            pt: Dict[str, float] = {}
            for k, v in pt_raw.items():
                try:
                    pt[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
        else:
            pt = {}
        # companion-010: 兼容旧文件——emotion_alerts 缺失或非 list 视为 []
        ea_raw = d.get("emotion_alerts") or []
        ea: List[Dict[str, Any]] = []
        if isinstance(ea_raw, list):
            for item in ea_raw:
                if not isinstance(item, dict):
                    continue
                kind = item.get("kind")
                ts = item.get("ts")
                ratio = item.get("ratio")
                if not kind:
                    continue
                try:
                    ea.append({
                        "kind": str(kind),
                        "ts": float(ts) if ts is not None else 0.0,
                        "ratio": float(ratio) if ratio is not None else 0.0,
                    })
                except (TypeError, ValueError):
                    continue
        return cls(
            profile_id=str(d.get("profile_id") or ""),
            nickname=(d.get("nickname") or None),
            interests=list(d.get("interests") or []),
            goals=list(d.get("goals") or []),
            created_ts=float(d.get("created_ts") or 0.0),
            updated_ts=float(d.get("updated_ts") or 0.0),
            dialog_summary=[str(s) for s in (d.get("dialog_summary") or [])],
            prefer_topics=pt,
            emotion_alerts=ea,
            schema_version=int(d.get("schema_version") or 0),
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


EmitFn = Callable[..., None]


class PersistentProfileStore:
    """``~/.coco/profiles/`` 跨 session JSON 持久化层（companion-008）.

    - ``save(record)``：把 ``PersistedProfile`` atomic 写入
      ``<root>/<profile_id>.json``。
    - ``load(profile_id)``：读对应文件；不存在 → None；损坏 → 移到
      ``_corrupt/`` + emit + 返回 None；schema 不匹配 → 移到
      ``_legacy_v<n>/`` + emit + 返回 None。
    - ``hydrate_all()``：批量扫 ``<root>/*.json``，返回 dict[profile_id ->
      PersistedProfile]。损坏 / 不匹配文件按上面规则隔离。
    - 隔离目录写入失败本身 fail-soft（log warning，原文件保留）。
    """

    def __init__(
        self,
        root: Optional[Path] = None,
        *,
        emit_fn: Optional[EmitFn] = None,
        keep_dialog_summary: int = DEFAULT_DIALOG_SUMMARY_KEEP,
    ) -> None:
        self.root: Path = Path(root) if root is not None else default_persist_root()
        self._emit_fn = emit_fn
        self.keep_dialog_summary = max(0, int(keep_dialog_summary))
        self._lock = threading.RLock()

    # -------------------- paths --------------------
    def _path_for(self, profile_id: str) -> Path:
        if not is_valid_profile_id(profile_id):
            raise ValueError(f"invalid profile_id={profile_id!r} (expected ^[0-9a-f]{{{PROFILE_ID_LEN}}}$)")
        return self.root / f"{profile_id}.json"

    def _corrupt_dir(self) -> Path:
        return self.root / "_corrupt"

    def _legacy_dir(self, ver: int) -> Path:
        return self.root / f"_legacy_v{ver}"

    # -------------------- save --------------------
    def save(self, record: PersistedProfile) -> Path:
        """atomic write；返回最终落盘路径。"""
        if not is_valid_profile_id(record.profile_id):
            raise ValueError(f"invalid profile_id={record.profile_id!r}")
        # 保证 schema_version 与 keep_dialog_summary 截断
        record.schema_version = SCHEMA_VERSION
        if self.keep_dialog_summary and len(record.dialog_summary) > self.keep_dialog_summary:
            record.dialog_summary = record.dialog_summary[-self.keep_dialog_summary:]
        if not record.created_ts:
            record.created_ts = time.time()
        record.updated_ts = time.time()

        path = self._path_for(record.profile_id)
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(str(self.root), 0o700)
            except OSError:
                pass
            tmp = path.with_suffix(path.suffix + ".tmp")
            data = json.dumps(record.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
            with open(str(tmp), "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(str(tmp), str(path))
            try:
                os.chmod(str(path), 0o600)
            except OSError:
                pass
        self._emit("profile.persisted", profile_id=record.profile_id, path=str(path))
        return path

    # -------------------- load --------------------
    def load(self, profile_id: str) -> Optional[PersistedProfile]:
        """单个 load。损坏 / schema 不匹配按规则隔离 + emit + 返回 None。"""
        if not is_valid_profile_id(profile_id):
            log.warning("[profile_persist] load rejected invalid profile_id=%r", profile_id)
            return None
        path = self._path_for(profile_id)
        with self._lock:
            if not path.exists():
                return None
            try:
                raw = path.read_text(encoding="utf-8")
                obj = json.loads(raw)
            except Exception as e:  # noqa: BLE001
                log.warning("[profile_persist] %s parse failed: %s: %s — quarantine to _corrupt",
                            path, type(e).__name__, e)
                self._quarantine_corrupt(path, profile_id, reason=f"{type(e).__name__}: {e}")
                return None
            if not isinstance(obj, dict):
                log.warning("[profile_persist] %s root not dict (%r) — quarantine to _corrupt",
                            path, type(obj).__name__)
                self._quarantine_corrupt(path, profile_id, reason="root_not_dict")
                return None
            ver = int(obj.get("schema_version") or 0)
            if ver != SCHEMA_VERSION:
                log.warning("[profile_persist] %s schema_version=%s != %s — quarantine to _legacy_v%s",
                            path, ver, SCHEMA_VERSION, ver)
                self._quarantine_legacy(path, profile_id, ver)
                return None
            try:
                rec = PersistedProfile.from_dict(obj)
            except Exception as e:  # noqa: BLE001
                log.warning("[profile_persist] %s from_dict failed: %s: %s — quarantine to _corrupt",
                            path, type(e).__name__, e)
                self._quarantine_corrupt(path, profile_id, reason=f"from_dict {type(e).__name__}")
                return None
            # profile_id 字段与文件名一致性
            if rec.profile_id != profile_id:
                log.warning("[profile_persist] %s profile_id mismatch (file=%s vs body=%s) — quarantine",
                            path, profile_id, rec.profile_id)
                self._quarantine_corrupt(path, profile_id, reason="profile_id_mismatch")
                return None
            return rec

    # -------------------- hydrate_all --------------------
    def hydrate_all(self) -> Dict[str, PersistedProfile]:
        """批量扫 ``<root>/*.json``，返回 {profile_id: PersistedProfile}。

        子目录（_corrupt / _legacy_v*）跳过；非 12-hex 文件名跳过；
        损坏 / schema 不匹配按规则隔离。emit ``profile.hydrated`` 一条聚合事件。
        """
        out: Dict[str, PersistedProfile] = {}
        with self._lock:
            if not self.root.exists():
                self._emit("profile.hydrated", count=0, root=str(self.root))
                return out
            for p in sorted(self.root.iterdir()):
                if not p.is_file():
                    continue
                if p.suffix != ".json":
                    continue
                stem = p.stem
                if not is_valid_profile_id(stem):
                    # 不是合法 profile_id 文件名 → 不动它（也许是用户手动放的）
                    log.warning("[profile_persist] skip non-profile file: %s", p)
                    continue
                rec = self.load(stem)
                if rec is not None:
                    out[stem] = rec
        self._emit("profile.hydrated", count=len(out), root=str(self.root))
        return out

    # -------------------- quarantine --------------------
    def _quarantine_corrupt(self, src: Path, profile_id: str, *, reason: str) -> None:
        try:
            target_dir = self._corrupt_dir()
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{profile_id}.json.bak"
            # 多次 corrupt：覆盖同名 bak（最新错误优先）
            shutil.move(str(src), str(target))
            # L1: quarantine 后限制权限（与正常 profile 同 0o600）
            try:
                os.chmod(str(target), 0o600)
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            log.warning("[profile_persist] quarantine corrupt %s failed: %s: %s — leaving in place",
                        src, type(e).__name__, e)
            return
        self._emit("profile.corrupt", profile_id=profile_id, reason=reason,
                   moved_to=str(target))

    def _quarantine_legacy(self, src: Path, profile_id: str, ver: int) -> None:
        try:
            target_dir = self._legacy_dir(ver)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{profile_id}.json"
            shutil.move(str(src), str(target))
        except Exception as e:  # noqa: BLE001
            log.warning("[profile_persist] quarantine legacy %s failed: %s: %s — leaving in place",
                        src, type(e).__name__, e)
            return
        self._emit("profile.schema_mismatch", profile_id=profile_id,
                   schema_version=ver, expected=SCHEMA_VERSION, moved_to=str(target))

    # -------------------- emit --------------------
    def _emit(self, event: str, **payload: Any) -> None:
        if self._emit_fn is None:
            return
        try:
            self._emit_fn(event, component="companion", **payload)
        except Exception as e:  # noqa: BLE001
            log.warning("[profile_persist] emit %s failed: %s: %s",
                        event, type(e).__name__, e)


# ---------------------------------------------------------------------------
# Merge helper
# ---------------------------------------------------------------------------


def merge_lists_lru(existing: List[str], new: List[str], cap: Optional[int] = None) -> List[str]:
    """interests/goals/dialog_summary 合并：去重保 LRU；可选 cap 截尾保新。"""
    out: List[str] = list(existing)
    for item in new:
        if not item:
            continue
        if item in out:
            out.remove(item)
        out.append(item)
    if cap is not None and cap > 0 and len(out) > cap:
        out = out[-cap:]
    return out


__all__ = [
    "SCHEMA_VERSION",
    "PROFILE_ID_LEN",
    "PROFILE_ID_RE",
    "DEFAULT_DIALOG_SUMMARY_KEEP",
    "PersistedProfile",
    "PersistentProfileStore",
    "compute_profile_id",
    "default_persist_root",
    "is_valid_profile_id",
    "merge_lists_lru",
    "normalize_nickname",
    "profile_persist_enabled_from_env",
]
