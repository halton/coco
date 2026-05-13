"""coco.companion.profile_persist_bridge — companion-008 端到端 write-through 桥接.

把 companion-006 的 ``MultiProfileStore``（in-session per-user JSON）和
companion-008 的 ``PersistentProfileStore``（跨 session ``~/.coco/profiles/<sha1>.json``）
连起来：

- ``ProfileSwitcher`` 切到新 user 时（on_switch 回调）：从 MultiProfileStore
  读当前 ``UserProfile``，compute_profile_id，构造 ``PersistedProfile``（含
  ``last_seen=now`` 和 ``DialogMemory._summary`` 拷贝），调
  ``PersistentProfileStore.save()`` 落盘。
- 进程退出 finally：再 flush 一次当前 active profile。
- 启动 hydrate_all() 后：把 hydrated ``PersistedProfile`` **回灌**到
  ``MultiProfileStore``（按 nickname → set_name + add_interest），让现有
  ProfileSwitcher 链路看到上次状态。

整段全部 fail-soft：任何步骤抛异常 → log.warning + 继续，不破对话主路径。

整段不引入新线程；复用 ``ProfileSwitcher`` 现有 on_switch 回调线程
（通常为 attention tick 线程）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from coco.companion.profile_persist import (
    PersistedProfile,
    PersistentProfileStore,
    compute_profile_id,
    merge_lists_lru,
)

log = logging.getLogger(__name__)


DialogSummaryProvider = Callable[[], Optional[str]]


class ProfilePersistBridge:
    """把 MultiProfileStore + ProfileSwitcher + DialogMemory 写入
    PersistentProfileStore 的胶水层。

    Parameters
    ----------
    persist_store
        ``PersistentProfileStore`` 实例（``~/.coco/profiles/`` 落盘）。
    multi_store
        ``MultiProfileStore`` 实例（companion-006 in-session per-user 路由）。
    dialog_summary_fn
        无参回调，返回当前 ``DialogMemory._summary`` 文本（``None``/空 → 不写）。
        允许为 ``None``（无 dialog memory 时）。
    face_id_for_user_fn
        给定 user_id（=昵称）→ 对应 face_id 字符串；用来稳定 sha1 输入。
        可为 ``None``，则 face_id 退化为 user_id 本身。
    """

    def __init__(
        self,
        *,
        persist_store: PersistentProfileStore,
        multi_store: Any,  # duck-typed: load() / set_name / add_interest / add_goal
        dialog_summary_fn: Optional[DialogSummaryProvider] = None,
        face_id_for_user_fn: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        self.persist_store = persist_store
        self.multi_store = multi_store
        self.dialog_summary_fn = dialog_summary_fn
        self.face_id_for_user_fn = face_id_for_user_fn

    # ------------------------------------------------------------------ persist
    def persist_for_user(self, user_id: Optional[str]) -> Optional[str]:
        """把 ``user_id`` 对应的当前 in-session UserProfile 落盘。

        - ``user_id=None`` → no-op（default user 不持久化，避免与 hydrate
          冲突）。
        - 任何步骤抛异常 → log.warning，返回 None。

        Returns
        -------
        落盘成功的 profile_id；失败返回 None。
        """
        if not user_id:
            return None
        try:
            # 切到目标 user 路由再 load（多用户场景下 active 可能已变）
            prev_active = None
            try:
                prev_active = self.multi_store.active_user_id
                if prev_active != user_id:
                    self.multi_store.set_active_user(user_id)
                up = self.multi_store.load()
            finally:
                # 恢复原 active（避免影响 caller 的状态判断）
                try:
                    if prev_active is not None and prev_active != user_id:
                        self.multi_store.set_active_user(prev_active)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[profile_persist_bridge] read multi_store user=%r failed: %s: %s",
                user_id, type(e).__name__, e,
            )
            return None

        try:
            face_id = None
            if self.face_id_for_user_fn is not None:
                try:
                    face_id = self.face_id_for_user_fn(user_id)
                except Exception:  # noqa: BLE001
                    face_id = None
            if not face_id:
                face_id = user_id
            nickname = up.name or user_id
            pid = compute_profile_id(face_id, nickname)

            dialog_summary = []
            if self.dialog_summary_fn is not None:
                try:
                    s = self.dialog_summary_fn()
                    if s:
                        dialog_summary = [str(s)]
                except Exception:  # noqa: BLE001
                    dialog_summary = []

            now = time.time()
            # 若已有上次记录，沿用 created_ts，并合并 dialog_summary（保 LRU）
            existing = None
            try:
                existing = self.persist_store.load(pid)
            except Exception:  # noqa: BLE001
                existing = None

            created_ts = existing.created_ts if existing is not None else now
            merged_summary = list(dialog_summary)
            if existing is not None and existing.dialog_summary:
                merged_summary = merge_lists_lru(
                    existing.dialog_summary, dialog_summary,
                    cap=self.persist_store.keep_dialog_summary,
                )

            rec = PersistedProfile(
                profile_id=pid,
                nickname=nickname,
                interests=list(up.interests or []),
                goals=list(up.goals or []),
                created_ts=created_ts,
                updated_ts=now,
                dialog_summary=merged_summary,
            )
            self.persist_store.save(rec)
            return pid
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[profile_persist_bridge] persist user=%r failed: %s: %s",
                user_id, type(e).__name__, e,
            )
            return None

    # ----------------------------------------------------------- on_switch hook
    def on_switch(self, prev: Optional[str], curr: Optional[str]) -> None:
        """ProfileSwitcher.on_switch 回调；持久化 prev 的最终状态 + 预热 curr。

        - prev: 离开的 user，立即落盘其最终 profile（含最新 interests / 摘要）；
          ``prev=None`` 跳过。
        - curr: 进入的 user；当前不主动 persist（由后续 update 触发）。
        """
        if prev:
            try:
                self.persist_for_user(prev)
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "[profile_persist_bridge] on_switch prev=%r persist failed: %s: %s",
                    prev, type(e).__name__, e,
                )

    # --------------------------------------------------------- hydrate-back
    def hydrate_into_multi_store(self) -> int:
        """把 PersistentProfileStore 中已有 PersistedProfile 回灌到
        MultiProfileStore，让现有 ProfileSwitcher 链路看到上次状态。

        - 按 nickname 路由（active_user_id = nickname）；
        - 调 ``set_name`` + ``add_interest`` + ``add_goal``；
        - 不修改 PersistentProfileStore；
        - 完成后 active_user 恢复到 None（default）；
        - 返回成功回灌的 profile 数。

        fail-soft：单个 profile 失败不影响其他。
        """
        try:
            hydrated = self.persist_store.hydrate_all()
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[profile_persist_bridge] hydrate_all failed: %s: %s",
                type(e).__name__, e,
            )
            return 0

        prev_active = None
        try:
            prev_active = self.multi_store.active_user_id
        except Exception:  # noqa: BLE001
            prev_active = None

        n_ok = 0
        for pid, rec in hydrated.items():
            uid = rec.nickname or pid
            try:
                self.multi_store.set_active_user(uid)
                if rec.nickname:
                    self.multi_store.set_name(rec.nickname)
                for it in rec.interests or []:
                    try:
                        self.multi_store.add_interest(it)
                    except Exception:  # noqa: BLE001
                        pass
                for g in rec.goals or []:
                    try:
                        self.multi_store.add_goal(g)
                    except Exception:  # noqa: BLE001
                        pass
                n_ok += 1
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "[profile_persist_bridge] hydrate-back user=%r failed: %s: %s",
                    uid, type(e).__name__, e,
                )

        # 恢复 active
        try:
            self.multi_store.set_active_user(prev_active)
        except Exception:  # noqa: BLE001
            pass

        return n_ok


__all__ = ["ProfilePersistBridge"]
