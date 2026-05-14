"""coco.companion.group_mode — companion-011 多人共处 group_mode.

设计目标
========

当 ``FaceTracker`` 同一帧检出 ≥2 个**已识别**的 face_id（``TrackedFace.name``
非空）持续 ``enter_hold_s`` 秒，进入 ``group_mode``：

- emit ``companion.group_present(members=[...], reason="enter")``；
- 把所有在场用户的 ``prefer_topics`` 用 **并集 + 交集加权** 合并，调用
  ``ProactiveScheduler.set_topic_preferences``，让主动话题偏向"大家"共同兴趣
  而非某一个人；
- ``ProactiveScheduler.set_group_template_override`` 注入 group 句式前缀
  ("大家好" / "一起聊聊")，避免称呼单个 profile；
- ``PersistentProfileStore`` 各成员 profile 追加 ``group_sessions`` 条目
  （append + cap），跨会话可见 group 历史。

离开条件：连续 ``exit_hold_s`` 秒只剩 ≤1 个 known face_id → emit
``companion.group_present(reason="exit")``，还原 prefer / template。

设计选择（详见 task brief）
---------------------------

- **env**: 复用 ``COCO_MULTI_USER=1`` 作为主开关（feature_list.json
  指定）；额外 ``COCO_GROUP_MODE=0`` 可在 multi_user=on 时单独关掉
  group_mode（V7 "未设 env 不构造" 由调用方 ``main.py`` 通过
  ``group_mode_enabled_from_env`` 一道 gate）。
- **prefer 合并 = 并集 + 交集加权**：每个成员 prefer_topics ⇒ 全部 keyword
  union 进入候选；对每个 keyword 计 ``sum(member_weight) * (1 + α *
  (n_members_having_it - 1))``。α=0.5。这样：
    * 单个成员独有的爱好仍在候选（并集）
    * 多个成员共有的爱好被加权（交集加权 bonus）
    * 比纯交集鲁棒（若两人毫无交集，纯交集会清空）
- **caption / proactive 标签**：进入 group_mode 时把 ``last_group_members``
  与 ``in_group`` 状态记到 stats，由 ``ProactiveScheduler`` / SceneCaption
  自己 emit 时附带（本模块只暴露 ``is_active() / current_members()``）。

线程模型
--------

``observe(snapshot)`` 可由 vision tick 线程任意频率调用（典型 1-3 Hz）；
内部 RLock 串行化状态；触发 / 退出的 side-effect（emit / prefer set /
profile write）在锁外执行，避免反向加锁。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

log = logging.getLogger(__name__)


DEFAULT_ENTER_HOLD_S = 3.0  # 连续 3s 看到 ≥2 known face → 进入
DEFAULT_EXIT_HOLD_S = 7.0   # 连续 7s 只剩 ≤1 known face → 退出
DEFAULT_INTERSECT_BONUS = 0.5  # 交集 keyword 每多一个 member 加权 +0.5
DEFAULT_MAX_GROUP_SESSIONS_PER_PROFILE = 50

# group 句式前缀（避免单 profile 称呼）。ProactiveScheduler 通过
# set_group_template_override 注入；本模块只提供 default。
DEFAULT_GROUP_PHRASES: Tuple[str, ...] = (
    "大家好，一起聊聊",
    "你们好啊",
    "大家一起",
    "我们一起",
    "大家",
)


EmitFn = Callable[..., None]


# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------


def _bool_env(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def group_mode_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    """``COCO_MULTI_USER=1`` 且 ``COCO_GROUP_MODE`` ≠ 0 → 启用。默认 OFF。

    feature_list.json companion-011 ``default_off_env`` = COCO_MULTI_USER=1。
    额外 COCO_GROUP_MODE=0 用于在 multi-user-on 但**不想要** group_mode 的
    场景（向后兼容老 companion-006 用户）。
    """
    e = env if env is not None else os.environ
    if not _bool_env(e, "COCO_MULTI_USER", False):
        return False
    return _bool_env(e, "COCO_GROUP_MODE", True)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class GroupModeStats:
    enter_count: int = 0
    exit_count: int = 0
    observe_count: int = 0
    in_group_observe_count: int = 0
    last_members: Tuple[str, ...] = ()
    last_enter_ts: float = 0.0
    last_exit_ts: float = 0.0
    last_merged_prefer_size: int = 0


# ---------------------------------------------------------------------------
# prefer 合并算法
# ---------------------------------------------------------------------------


def merge_prefer_union_intersect(
    per_member: Sequence[Mapping[str, float]],
    *,
    intersect_bonus: float = DEFAULT_INTERSECT_BONUS,
    topk: int = 20,
) -> Dict[str, float]:
    """合并 N 个成员的 prefer_topics → 单一 dict.

    算法：union of keys；每个 keyword 的最终 weight 为
    ``sum(member_w) * (1 + α * (n_members_having_it - 1))``，再按整体最大 weight
    归一化到 [0, 1]，截 TopK。

    - n=1（只有一个成员）退化为该成员的 prefer 本身（归一化）
    - 空输入返回 {}
    """
    if not per_member:
        return {}
    n = len(per_member)
    if n == 1:
        only = per_member[0] or {}
        if not only:
            return {}
        max_w = max(only.values()) or 1.0
        ranked = sorted(only.items(), key=lambda kv: kv[1], reverse=True)[:topk]
        return {k: round(float(v) / max_w, 6) for k, v in ranked}

    sums: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for prefer in per_member:
        if not prefer:
            continue
        for k, v in prefer.items():
            try:
                w = float(v)
            except (TypeError, ValueError):
                continue
            if not k or w <= 0:
                continue
            sums[k] = sums.get(k, 0.0) + w
            counts[k] = counts.get(k, 0) + 1
    if not sums:
        return {}
    α = max(0.0, float(intersect_bonus))
    weighted: Dict[str, float] = {}
    for k, s in sums.items():
        c = counts.get(k, 1)
        bonus = 1.0 + α * max(0, c - 1)
        weighted[k] = s * bonus
    max_w = max(weighted.values()) or 1.0
    ranked = sorted(weighted.items(), key=lambda kv: kv[1], reverse=True)[:topk]
    return {k: round(float(v) / max_w, 6) for k, v in ranked}


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class GroupModeCoordinator:
    """状态机 + 协调器：observe(snapshot) → 在场已知 face_id ≥2 → group_mode.

    ``main.py`` 应当：

    1. ``coord = GroupModeCoordinator(proactive_scheduler=ps, persist_store=pp,
       profile_id_resolver=...)``
    2. 在 vision tick 回调里 ``coord.observe(snapshot)``（或直接 wire 到
       FaceTracker 的 latest() poll）

    依赖（duck-typed，全可 None）：

    - ``proactive_scheduler``：暴露 ``get_topic_preferences`` /
      ``set_topic_preferences``，可选 ``set_group_template_override``（缺失
      则 group 句式不生效，emit 仍发）。
    - ``persist_store``：companion-008 ``PersistentProfileStore``；写
      ``rec.group_sessions``。
    - ``profile_id_resolver(face_id_name) -> Optional[profile_id]``：把
      face_id name 映射成 12 hex profile_id（缺则跳过该 member 的 prefer
      合并 / 写盘）。
    """

    def __init__(
        self,
        *,
        proactive_scheduler: Any = None,
        persist_store: Any = None,
        profile_id_resolver: Optional[Callable[[str], Optional[str]]] = None,
        emit_fn: Optional[EmitFn] = None,
        enter_hold_s: float = DEFAULT_ENTER_HOLD_S,
        exit_hold_s: float = DEFAULT_EXIT_HOLD_S,
        intersect_bonus: float = DEFAULT_INTERSECT_BONUS,
        group_phrases: Optional[Sequence[str]] = None,
        max_group_sessions: int = DEFAULT_MAX_GROUP_SESSIONS_PER_PROFILE,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.proactive = proactive_scheduler
        self.persist_store = persist_store
        self.profile_id_resolver = profile_id_resolver
        self._emit_fn = emit_fn
        self.enter_hold_s = max(0.0, float(enter_hold_s))
        self.exit_hold_s = max(0.0, float(exit_hold_s))
        self.intersect_bonus = max(0.0, float(intersect_bonus))
        self.group_phrases: Tuple[str, ...] = tuple(group_phrases or DEFAULT_GROUP_PHRASES)
        self.max_group_sessions = max(1, int(max_group_sessions))
        self._clock = clock or time.monotonic

        self._lock = threading.RLock()
        self._in_group: bool = False
        self._current_members: Tuple[str, ...] = ()
        # 等待进入：连续看到 ≥2 known face 的起始 ts
        self._enter_candidate_since: float = 0.0
        self._enter_candidate_members: Tuple[str, ...] = ()
        # 等待退出：连续看到 ≤1 known face 的起始 ts
        self._exit_candidate_since: float = 0.0
        # prefer 还原快照（进入 group_mode 前 proactive.get_topic_preferences()）
        self._saved_prefer: Optional[Dict[str, float]] = None
        # template override 是否安装（用于 stop 时清理）
        self._template_overridden: bool = False

        self.stats = GroupModeStats()

    # ------------------------------------------------------------------
    # 公开查询
    # ------------------------------------------------------------------

    def is_active(self) -> bool:
        with self._lock:
            return self._in_group

    def current_members(self) -> Tuple[str, ...]:
        with self._lock:
            return self._current_members

    # ------------------------------------------------------------------
    # 主输入：observe(snapshot)
    # ------------------------------------------------------------------

    def observe(self, snapshot: Any, *, now: Optional[float] = None) -> None:
        """喂入 ``FaceSnapshot``（或任何带 ``tracks`` 属性的对象）。

        **cheap path: no inline IO** — observe 仅做内存状态机迁移，不做文件/网络/
        日志 IO；persistent_profile_store 写入由 enter/exit action 路径异步触发。

        从 ``snapshot.tracks`` 取所有 ``name`` 非空的 ``TrackedFace`` → 视为
        当前帧 known 在场用户集合。按 enter/exit hold 计时迁移状态。
        """
        if snapshot is None:
            return
        t = float(now) if now is not None else float(self._clock())
        names = _extract_known_names(snapshot)

        action: Optional[str] = None  # "enter" | "exit"
        members_for_action: Tuple[str, ...] = ()

        with self._lock:
            self.stats.observe_count += 1
            if self._in_group:
                self.stats.in_group_observe_count += 1

            if not self._in_group:
                # 等待进入
                if len(names) >= 2:
                    if self._enter_candidate_since <= 0:
                        self._enter_candidate_since = t
                        self._enter_candidate_members = names
                    else:
                        # 维持 candidate；更新 members 为最新（防止有人进有人出）
                        self._enter_candidate_members = names
                        if (t - self._enter_candidate_since) >= self.enter_hold_s:
                            self._in_group = True
                            self._current_members = names
                            self._enter_candidate_since = 0.0
                            self._exit_candidate_since = 0.0
                            self.stats.enter_count += 1
                            self.stats.last_members = names
                            self.stats.last_enter_ts = t
                            action = "enter"
                            members_for_action = names
                else:
                    self._enter_candidate_since = 0.0
                    self._enter_candidate_members = ()
            else:
                # 已在 group_mode，等待退出
                if len(names) >= 2:
                    # 还在 group：刷新成员（可能有人进有人出）
                    if names != self._current_members:
                        self._current_members = names
                        self.stats.last_members = names
                    self._exit_candidate_since = 0.0
                else:
                    if self._exit_candidate_since <= 0:
                        self._exit_candidate_since = t
                    elif (t - self._exit_candidate_since) >= self.exit_hold_s:
                        self._in_group = False
                        members_for_action = self._current_members
                        self._current_members = ()
                        self._enter_candidate_since = 0.0
                        self._exit_candidate_since = 0.0
                        self.stats.exit_count += 1
                        self.stats.last_exit_ts = t
                        action = "exit"

        # ---- side-effects 锁外 ----
        if action == "enter":
            self._on_enter(members_for_action, ts=t)
        elif action == "exit":
            self._on_exit(members_for_action, ts=t)

    # ------------------------------------------------------------------
    # 内部：enter / exit side-effects
    # ------------------------------------------------------------------

    def _on_enter(self, members: Tuple[str, ...], *, ts: float) -> None:
        # 1) emit
        self._emit_safe(
            "companion.group_present",
            members=list(members),
            reason="enter",
            ts=float(ts),
        )
        # 2) prefer 合并 + bump
        merged = self._merge_member_prefer(members)
        if self.proactive is not None:
            try:
                cur = {}
                get = getattr(self.proactive, "get_topic_preferences", None)
                if callable(get):
                    try:
                        cur = dict(get() or {})
                    except Exception:  # noqa: BLE001
                        cur = {}
                with self._lock:
                    self._saved_prefer = cur
                sett = getattr(self.proactive, "set_topic_preferences", None)
                if callable(sett) and merged:
                    sett(merged)
            except Exception as e:  # noqa: BLE001
                log.warning("[group_mode] set prefer on enter failed: %s: %s",
                            type(e).__name__, e)
        with self._lock:
            self.stats.last_merged_prefer_size = len(merged)
        # 3) template override
        if self.proactive is not None:
            try:
                ov = getattr(self.proactive, "set_group_template_override", None)
                if callable(ov):
                    ov(tuple(self.group_phrases))
                    with self._lock:
                        self._template_overridden = True
            except Exception as e:  # noqa: BLE001
                log.warning("[group_mode] set_group_template_override failed: %s: %s",
                            type(e).__name__, e)
        # 4) profile.group_sessions 写盘
        self._append_group_session(members, ts=ts, reason="enter")
        # 5) bump proactive stats（companion-011 计数）
        if self.proactive is not None:
            try:
                s = getattr(self.proactive, "stats", None)
                if s is not None and hasattr(s, "group_mode_trigger_count"):
                    s.group_mode_trigger_count = int(getattr(s, "group_mode_trigger_count", 0)) + 1
            except Exception:  # noqa: BLE001
                pass

    def _on_exit(self, members: Tuple[str, ...], *, ts: float) -> None:
        self._emit_safe(
            "companion.group_present",
            members=list(members),
            reason="exit",
            ts=float(ts),
        )
        # 还原 prefer
        if self.proactive is not None:
            try:
                with self._lock:
                    saved = self._saved_prefer
                    self._saved_prefer = None
                sett = getattr(self.proactive, "set_topic_preferences", None)
                if callable(sett):
                    sett(saved if saved is not None else {})
            except Exception as e:  # noqa: BLE001
                log.warning("[group_mode] restore prefer on exit failed: %s: %s",
                            type(e).__name__, e)
        # 清 template override
        if self.proactive is not None:
            try:
                with self._lock:
                    overridden = self._template_overridden
                    self._template_overridden = False
                ov = getattr(self.proactive, "set_group_template_override", None)
                if overridden and callable(ov):
                    ov(None)
            except Exception as e:  # noqa: BLE001
                log.warning("[group_mode] clear template override failed: %s: %s",
                            type(e).__name__, e)
        # profile.group_sessions exit 记录
        self._append_group_session(members, ts=ts, reason="exit")

    # ------------------------------------------------------------------
    # tick — 每次 proactive tick 顺手把 in_group_observe_count 累计
    # （main.py 可选；observe 已 cover）
    # ------------------------------------------------------------------

    def tick(self, now: Optional[float] = None) -> None:
        """供 ProactiveScheduler tick 调一次：累计 group_mode_active_total。"""
        with self._lock:
            in_group = self._in_group
        if in_group and self.proactive is not None:
            try:
                s = getattr(self.proactive, "stats", None)
                if s is not None and hasattr(s, "group_mode_active_total"):
                    s.group_mode_active_total = int(getattr(s, "group_mode_active_total", 0)) + 1
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _merge_member_prefer(self, members: Sequence[str]) -> Dict[str, float]:
        if not members or self.persist_store is None or self.profile_id_resolver is None:
            return {}
        per_member: List[Mapping[str, float]] = []
        for name in members:
            try:
                pid = self.profile_id_resolver(name)
            except Exception as e:  # noqa: BLE001
                log.warning("[group_mode] profile_id_resolver(%r) failed: %s: %s",
                            name, type(e).__name__, e)
                continue
            if not pid:
                continue
            try:
                rec = self.persist_store.load(pid)
            except Exception as e:  # noqa: BLE001
                log.warning("[group_mode] persist.load(%s) failed: %s: %s",
                            pid, type(e).__name__, e)
                continue
            if rec is None:
                continue
            pt = getattr(rec, "prefer_topics", None) or {}
            if pt:
                per_member.append(dict(pt))
        if not per_member:
            return {}
        return merge_prefer_union_intersect(
            per_member,
            intersect_bonus=self.intersect_bonus,
        )

    def _append_group_session(
        self, members: Tuple[str, ...], *, ts: float, reason: str
    ) -> None:
        if self.persist_store is None or self.profile_id_resolver is None:
            return
        if not members:
            return
        for name in members:
            try:
                pid = self.profile_id_resolver(name)
            except Exception:  # noqa: BLE001
                continue
            if not pid:
                continue
            try:
                rec = self.persist_store.load(pid)
                if rec is None:
                    continue
                sessions = list(getattr(rec, "group_sessions", []) or [])
                sessions.append({
                    "ts": float(ts),
                    "reason": str(reason),
                    "members": list(members),
                })
                if len(sessions) > self.max_group_sessions:
                    sessions = sessions[-self.max_group_sessions:]
                # 通过 setattr 兼容旧 PersistedProfile（缺字段时下面 save 会触发
                # to_dict；schema 默认序列化 dataclass 所有字段——下面保证
                # PersistedProfile.group_sessions 存在）
                rec.group_sessions = sessions
                rec.updated_ts = float(ts)
                self.persist_store.save(rec)
            except Exception as e:  # noqa: BLE001
                log.warning("[group_mode] append group_session pid=%s failed: %s: %s",
                            pid, type(e).__name__, e)

    def _emit_safe(self, event: str, **payload: Any) -> None:
        emit_fn = self._emit_fn
        if emit_fn is None:
            try:
                from coco.logging_setup import emit as _emit
                emit_fn = _emit
            except Exception:  # noqa: BLE001
                return
        try:
            emit_fn(event, component="group_mode", **payload)
        except Exception as e:  # noqa: BLE001
            log.warning("[group_mode] emit %s failed: %s: %s",
                        event, type(e).__name__, e)


def _extract_known_names(snapshot: Any) -> Tuple[str, ...]:
    """从 FaceSnapshot.tracks 抽 name 非空的成员，去重保稳定顺序。"""
    tracks = getattr(snapshot, "tracks", None) or ()
    seen: Set[str] = set()
    out: List[str] = []
    for tr in tracks:
        name = getattr(tr, "name", None)
        if not name:
            continue
        s = str(name).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return tuple(out)


__all__ = [
    "DEFAULT_ENTER_HOLD_S",
    "DEFAULT_EXIT_HOLD_S",
    "DEFAULT_INTERSECT_BONUS",
    "DEFAULT_GROUP_PHRASES",
    "DEFAULT_MAX_GROUP_SESSIONS_PER_PROFILE",
    "GroupModeCoordinator",
    "GroupModeStats",
    "group_mode_enabled_from_env",
    "merge_prefer_union_intersect",
]
