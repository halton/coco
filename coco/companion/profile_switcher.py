"""coco.companion.profile_switcher — companion-006 多用户档案切换.

设计目标
========

vision-003 ``FaceIDClassifier.identify(crop) -> (name|None, conf)`` 给出当前
primary 用户的 face_id name。本模块在 attention 切换 / face_id 变化时把
``ProfileStore`` 的"当前 active user"切到对应的 per-user JSON 档案，并通过短
TTS"欢迎回来 X"致意。下游（``InteractSession`` / ``ProactiveScheduler`` /
``DialogStateMachine`` 等）继续以同一 ``ProfileStore`` 句柄读 ``.load()``，
即可拿到新 user 的 ``UserProfile``——下游零改动。

设计要点
--------

- **MultiProfileStore**：rich wrapper，与 ``coco.profile.ProfileStore`` 的对外
  API 同名同签名（``load`` / ``save`` / ``set_name`` / ``add_interest`` /
  ``add_goal`` / ``update_field`` / ``reset``）。内部按 ``active_user_id``
  路由到 ``<root>/profile_<sanitized>.json`` 文件。``active_user_id=None``
  时退化到 default 文件 ``<root>/profile.json``（与 companion-004 默认行为
  完全一致，迁移友好）。
- **ProfileSwitcher**：纯状态机 + 防抖。``observe(name|None)`` 接受最新观察
  到的 face_id name；只有当某 name 持续 >= ``debounce_s`` 才真的切换；短暂
  遮挡（A→unknown→A）期间不切。
- **致意 cooldown**：同一 user 在 ``greet_cooldown_s`` 内只致意一次；切回
  default（name=None）从不致意。
- **fail-soft**：face_id backend 抛错 / store 写盘失败 → 留在当前 active
  profile，不崩；事件层用 ``log.warning``。
- **线程安全**：``MultiProfileStore`` 与 ``ProfileSwitcher`` 各自 RLock；
  observe 可能从 vision tick 线程触发，主交互线程读 profile。
- **default-OFF**：``COCO_MULTI_USER`` 默认 0；main.py 在 build 时若关则不
  构造 switcher、``_profile_store`` 仍是单 ``ProfileStore``。

事件
----

- ``companion.user_profile_switched`` (component="companion")
  payload: ``from_user`` / ``to_user`` / ``reason``
- ``companion.user_profile_greeted`` (component="companion")
  payload: ``user`` / ``utterance``

注意：本期 dialog history 只 in-session per-profile 隔离（切换时 clear
DialogMemory），不持久化（feature_list notes 已声明 followup）。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from coco.profile import (
    ProfileStore,
    UserProfile,
    default_profile_path,
)

log = logging.getLogger(__name__)


DEFAULT_DEBOUNCE_S = 5.0
DEFAULT_GREET_COOLDOWN_S = 1800.0  # 30min
DEFAULT_GREET_TEMPLATE = "欢迎回来 {name}"

_NAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9_\-]+")


def _sanitize_name(name: str) -> str:
    """把 name 转成文件名安全的 slug；保留 ascii 字母数字 _ -；其余替换成 '_'。

    长度上限 32（避免文件系统路径过长）；空 → ``"default"``。
    支持 unicode 名字（如中文）：先 lower，再 ``_NAME_SAFE_RE`` 替换。中文/emoji
    会被替换成 ``_``——为了避免不同名字 collide 到同一 slug，附加 hash 短后缀。
    """
    raw = (name or "").strip()
    if not raw:
        return "default"
    base = _NAME_SAFE_RE.sub("_", raw.lower())
    base = base.strip("_") or "u"
    if len(base) > 24:
        base = base[:24]
    # 短 hash 后缀消歧（4 hex chars，足以 1k 用户低碰撞；冲突影响仅是文件名不直观）
    h = hex(abs(hash(raw)) & 0xFFFF)[2:].zfill(4)
    return f"{base}_{h}"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MultiUserConfig:
    enabled: bool = False
    debounce_s: float = DEFAULT_DEBOUNCE_S
    greet_cooldown_s: float = DEFAULT_GREET_COOLDOWN_S
    greet_enabled: bool = True
    greet_template: str = DEFAULT_GREET_TEMPLATE


def _bool_env(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _float_env(env: Mapping[str, str], key: str, default: float) -> float:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        log.warning("[multi_user] %s=%r 非数字，回退 %.2f", key, raw, default)
        return default
    return v


def multi_user_config_from_env(env: Optional[Mapping[str, str]] = None) -> MultiUserConfig:
    e = env if env is not None else os.environ
    return MultiUserConfig(
        enabled=_bool_env(e, "COCO_MULTI_USER", False),
        debounce_s=max(0.0, _float_env(e, "COCO_MULTI_USER_DEBOUNCE_S", DEFAULT_DEBOUNCE_S)),
        greet_cooldown_s=max(0.0, _float_env(e, "COCO_MULTI_USER_GREET_COOLDOWN_S", DEFAULT_GREET_COOLDOWN_S)),
        greet_enabled=_bool_env(e, "COCO_MULTI_USER_GREET", True),
        greet_template=(e.get("COCO_MULTI_USER_GREET_TEMPLATE") or DEFAULT_GREET_TEMPLATE),
    )


def multi_user_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    return multi_user_config_from_env(env).enabled


# ---------------------------------------------------------------------------
# MultiProfileStore
# ---------------------------------------------------------------------------


class MultiProfileStore:
    """rich wrapper，按 ``active_user_id`` 路由到 per-user JSON 文件。

    与 ``coco.profile.ProfileStore`` 接口对齐（duck-typing），下游
    （InteractSession / ProactiveScheduler）无须感知。

    路径策略
    --------

    - ``active_user_id=None`` → ``<root>/profile.json``（与 companion-004
      默认 store 路径一致；从 single-user 升级 multi-user 后老 profile 仍可读）。
    - ``active_user_id="alice"`` → ``<root>/profile_<sanitize(alice)>.json``。

    线程安全
    --------

    内部 RLock 串行化 ``set_active_user`` 与 ``load``/``save``——切换瞬间下游
    若同时 ``load()`` 也只会拿到完整新文件或完整旧文件，永不读到中途态。
    """

    def __init__(
        self,
        root: Optional[Path] = None,
        active_user_id: Optional[str] = None,
    ) -> None:
        # root 决策：若 caller 给空，复用 default_profile_path() 的父目录。
        if root is None:
            root = default_profile_path().parent
        self.root: Path = Path(root)
        self._lock = threading.RLock()
        self._active_user_id: Optional[str] = active_user_id
        # cache: user_id → ProfileStore（避免每次 load 都新建对象）
        self._stores: Dict[Optional[str], ProfileStore] = {}

    # -------------------- routing helpers --------------------
    def _path_for(self, user_id: Optional[str]) -> Path:
        if user_id is None:
            return self.root / "profile.json"
        return self.root / f"profile_{_sanitize_name(user_id)}.json"

    def _store_for(self, user_id: Optional[str]) -> ProfileStore:
        with self._lock:
            st = self._stores.get(user_id)
            if st is None:
                st = ProfileStore(path=self._path_for(user_id))
                self._stores[user_id] = st
            return st

    @property
    def active_user_id(self) -> Optional[str]:
        with self._lock:
            return self._active_user_id

    def set_active_user(self, user_id: Optional[str]) -> None:
        """切换 active user。``None`` → default profile。

        立即返回；不预热 .load()（下游下一次 load 自然走新文件）。
        """
        with self._lock:
            self._active_user_id = user_id

    def active_path(self) -> Path:
        with self._lock:
            return self._path_for(self._active_user_id)

    # -------------------- ProfileStore-compatible API --------------------
    def load(self) -> UserProfile:
        return self._store_for(self.active_user_id).load()

    def save(self, profile: UserProfile) -> None:
        self._store_for(self.active_user_id).save(profile)

    def update_field(self, **kwargs: Any) -> UserProfile:
        return self._store_for(self.active_user_id).update_field(**kwargs)

    def add_interest(self, item: str) -> UserProfile:
        return self._store_for(self.active_user_id).add_interest(item)

    def add_goal(self, item: str) -> UserProfile:
        return self._store_for(self.active_user_id).add_goal(item)

    def set_name(self, name: str) -> UserProfile:
        return self._store_for(self.active_user_id).set_name(name)

    def reset(self) -> None:
        """只 reset 当前 active user 的 store；其他 user 文件保留。"""
        self._store_for(self.active_user_id).reset()

    # ProfileStore.path is read by some debug code; expose dynamic.
    @property
    def path(self) -> Path:
        return self.active_path()


# ---------------------------------------------------------------------------
# ProfileSwitcher
# ---------------------------------------------------------------------------


EmitFn = Callable[..., None]
TTSFn = Callable[[str], Any]


class ProfileSwitcher:
    """状态机：观察 face_id name → 防抖 → 切换 ``MultiProfileStore`` active user。

    线程模型
    --------

    - ``observe(name)`` 可由 vision tick 线程或 attention on_change 回调线程
      调用，频率不限（典型 1-3 Hz）。
    - 内部 RLock 保护 ``_pending_*`` / ``_active_*`` / ``_last_greet_at``。
    - 切换 / 致意 / dialog clear 在锁外触发（避免下游回调反向加锁）。

    切换条件
    --------

    1. 观察到的 name != active_user_id；
    2. 同一 name 持续 ≥ ``debounce_s``（避免 A→unknown→A 短暂遮挡触发切换）；
    3. ``name=None`` (face 消失 / 未识别) 不会主动把 active 切回 default——
       feature_list V4 要求"未识别脸 → 保持上一个 profile 或 default"。这里
       严格按"保持上一个"实现：observe(None) 是 no-op（不重置 pending，不
       切换）；首次 observe(name) 之前 active 一直是 None（构造时的 default）。

    致意
    ----

    - 触发切换且 ``new_user != None`` → 检查 ``_last_greet_at[new_user]``；
      若距今 ≥ ``greet_cooldown_s`` （或没记录）→ 调 ``tts_say_fn``，更新时
      间戳。
    - 切换到 default (None) 不致意。
    - greet 失败 fail-soft（仅 log.warning，仍记录 ts 防 spam 重试）。
    """

    def __init__(
        self,
        *,
        store: MultiProfileStore,
        config: Optional[MultiUserConfig] = None,
        tts_say_fn: Optional[TTSFn] = None,
        emit_fn: Optional[EmitFn] = None,
        on_switch: Optional[Callable[[Optional[str], Optional[str]], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.store = store
        self.config = config if config is not None else MultiUserConfig()
        self.tts_say_fn = tts_say_fn
        self._emit_fn = emit_fn
        self._on_switch = on_switch  # 业务回调（如 dialog_memory.clear）
        self._clock = clock or time.monotonic

        self._lock = threading.RLock()
        # pending：连续观察到同一非 None name 的起始时刻（用于 debounce）
        self._pending_name: Optional[str] = None
        self._pending_since: float = 0.0
        # 致意 cooldown：name → wall-clock 上次致意时间戳
        self._last_greet_at: Dict[str, float] = {}

    # ----------------- public API -----------------
    @property
    def active_user(self) -> Optional[str]:
        return self.store.active_user_id

    def observe(self, name: Optional[str]) -> Optional[str]:
        """喂入最新 face_id name；返回当前（可能更新后的）active user。

        ``name=None`` （未识别 / 多人无 primary / face 消失）→ 重置 pending
        但 **不切换** active（保持上一次的 active profile 不变）。

        Returns
        -------
        当前 active user_id（可能与传入 name 不同，因为 debounce 未达 / 未识别）。
        """
        # 规范化 name：None / 空串 → None；strip
        norm: Optional[str] = None
        if name:
            s = str(name).strip()
            if s:
                norm = s

        now = self._clock()
        switch_pair: Optional[tuple] = None  # (prev, curr)
        do_greet_for: Optional[str] = None
        utterance: Optional[str] = None

        with self._lock:
            cur_active = self.store.active_user_id

            if norm is None:
                # 未识别：清 pending，但不切 active。
                self._pending_name = None
                self._pending_since = 0.0
                return cur_active

            if norm == cur_active:
                # 已经是当前 active：清 pending（无需切换）
                self._pending_name = None
                self._pending_since = 0.0
                return cur_active

            # name != cur_active → 启动 / 累积 debounce
            if norm != self._pending_name:
                self._pending_name = norm
                self._pending_since = now
                return cur_active  # 还需等满 debounce

            elapsed = now - self._pending_since
            if elapsed < self.config.debounce_s:
                # debounce 未达
                return cur_active

            # 触发切换
            self.store.set_active_user(norm)
            switch_pair = (cur_active, norm)
            self._pending_name = None
            self._pending_since = 0.0

            # 致意 cooldown 检查（仅切到 named user 才致意）
            if self.config.greet_enabled and self.tts_say_fn is not None:
                wall_now = time.time()
                last_greet = self._last_greet_at.get(norm, 0.0)
                if wall_now - last_greet >= self.config.greet_cooldown_s:
                    do_greet_for = norm
                    self._last_greet_at[norm] = wall_now
                    try:
                        utterance = self.config.greet_template.format(name=norm)
                    except (KeyError, IndexError, ValueError):
                        utterance = f"欢迎回来 {norm}"

        # ----------- 锁外 fire side-effects -----------
        if switch_pair is not None:
            prev, curr = switch_pair
            # business callback（如 DialogMemory.clear，确保 history 隔离）
            if self._on_switch is not None:
                try:
                    self._on_switch(prev, curr)
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "[profile_switcher] on_switch callback failed: %s: %s",
                        type(e).__name__, e,
                    )
            # emit
            self._emit_safe(
                "companion.user_profile_switched",
                from_user=prev,
                to_user=curr,
                reason="face_id_debounced",
            )
            # greet
            if do_greet_for is not None and utterance is not None:
                try:
                    if self.tts_say_fn is not None:
                        self.tts_say_fn(utterance)
                    self._emit_safe(
                        "companion.user_profile_greeted",
                        user=do_greet_for,
                        utterance=utterance,
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "[profile_switcher] greet tts failed: %s: %s",
                        type(e).__name__, e,
                    )

        return self.store.active_user_id

    def force_set(self, name: Optional[str]) -> None:
        """绕过 debounce，直接切到 ``name``（管理 / 测试用）。

        触发 on_switch 回调和 ``companion.user_profile_switched`` 事件，但不
        触发 greet（避免人工 force 也吵到用户）。
        """
        with self._lock:
            prev = self.store.active_user_id
            self.store.set_active_user(name)
            self._pending_name = None
            self._pending_since = 0.0
        if prev != name:
            if self._on_switch is not None:
                try:
                    self._on_switch(prev, name)
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "[profile_switcher] on_switch (force) failed: %s: %s",
                        type(e).__name__, e,
                    )
            self._emit_safe(
                "companion.user_profile_switched",
                from_user=prev,
                to_user=name,
                reason="force",
            )

    # ----------------- internal -----------------
    def _emit_safe(self, event: str, **payload: Any) -> None:
        if self._emit_fn is None:
            return
        try:
            self._emit_fn(event, component="companion", **payload)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[profile_switcher] emit %s failed: %s: %s",
                event, type(e).__name__, e,
            )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_multi_profile_store(
    root: Optional[Path] = None,
    initial_user_id: Optional[str] = None,
) -> MultiProfileStore:
    """便捷构造。"""
    return MultiProfileStore(root=root, active_user_id=initial_user_id)


def build_profile_switcher(
    *,
    store: Optional[MultiProfileStore] = None,
    config: Optional[MultiUserConfig] = None,
    tts_say_fn: Optional[TTSFn] = None,
    emit_fn: Optional[EmitFn] = None,
    on_switch: Optional[Callable[[Optional[str], Optional[str]], None]] = None,
    clock: Optional[Callable[[], float]] = None,
    root: Optional[Path] = None,
) -> Optional[ProfileSwitcher]:
    """主入口；config.enabled=False → 返回 None（零开销）。"""
    cfg = config if config is not None else multi_user_config_from_env()
    if not cfg.enabled:
        return None
    s = store if store is not None else build_multi_profile_store(root=root)
    return ProfileSwitcher(
        store=s,
        config=cfg,
        tts_say_fn=tts_say_fn,
        emit_fn=emit_fn,
        on_switch=on_switch,
        clock=clock,
    )


__all__ = [
    "DEFAULT_DEBOUNCE_S",
    "DEFAULT_GREET_COOLDOWN_S",
    "DEFAULT_GREET_TEMPLATE",
    "MultiProfileStore",
    "MultiUserConfig",
    "ProfileSwitcher",
    "build_multi_profile_store",
    "build_profile_switcher",
    "multi_user_config_from_env",
    "multi_user_enabled_from_env",
]
