"""coco.perception.attention — vision-004 多目标人脸注视切换.

设计目标
========

vision-002 (FaceTracker) 维护 N 张人脸的稳定 track_id；vision-003 (FaceID)
给主脸贴 name 标签。vision-004 在此之上加一层 "attention selector"：

- 把 FaceTracker.latest().tracks 当成输入候选集；
- 按 policy 决定当前 "face of attention"（focus track_id）；
- 维护焦点保持时间（min_focus_s，防抖）+ 切换冷却（switch_cooldown_s）；
- focus 变化时调用 on_change 回调（业务层 emit "vision.attention_changed"）。

不做事件总线绑定 / 不直接 emit；由 main.py 把回调挂上，方便单测注入。

Policy
------

- ROUND_ROBIN：按 track_id 升序循环。落到当前 focus 上时，待 min_focus_s
  到期后切到下一个仍存在的 track_id（环回最小）。
- LARGEST_FACE：选 bbox 面积最大的 track。
- NEWEST：选 last_seen_ts 最大的 track。
- NAMED_FIRST：有 name 的 track 优先；若同时存在多个 named，取面积最大；
  若全无 name，回退到面积最大。

线程安全
--------

AttentionSelector 用 RLock 包内部状态；select() / tick() 可并发调用。
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence

log = logging.getLogger(__name__)


class AttentionPolicy(str, enum.Enum):
    ROUND_ROBIN = "round_robin"
    LARGEST_FACE = "largest_face"
    NEWEST = "newest"
    NAMED_FIRST = "named_first"


@dataclass(frozen=True)
class AttentionTarget:
    """当前焦点的快照。"""

    track_id: int
    name: Optional[str]
    score: float            # policy 相关的"打分"（面积 / 时间戳 / 名字优先级权重）
    last_focused_ts: float  # 本次成为 focus 的起始 monotonic 时刻


# 回调签名：(prev, curr) → None
AttentionChangeCallback = Callable[[Optional[AttentionTarget], Optional[AttentionTarget]], None]


# ---- 工具：从 tracks 序列里挑 best ---------------------------------------


def _pick_largest(tracks: Sequence) -> Optional[object]:
    if not tracks:
        return None
    return max(tracks, key=lambda t: int(getattr(t, "area", 0)))


def _pick_newest(tracks: Sequence) -> Optional[object]:
    if not tracks:
        return None
    return max(tracks, key=lambda t: float(getattr(t, "last_seen_ts", 0.0)))


def _pick_named_first(tracks: Sequence) -> Optional[object]:
    if not tracks:
        return None
    named = [t for t in tracks if getattr(t, "name", None)]
    if named:
        return max(named, key=lambda t: int(getattr(t, "area", 0)))
    return _pick_largest(tracks)


def _pick_round_robin(tracks: Sequence, last_focus_id: Optional[int]) -> Optional[object]:
    """track_id 升序循环：取严格大于 last_focus_id 的最小 id；若无则取最小 id。"""
    if not tracks:
        return None
    sorted_tracks = sorted(tracks, key=lambda t: int(t.track_id))
    if last_focus_id is None:
        return sorted_tracks[0]
    for t in sorted_tracks:
        if int(t.track_id) > int(last_focus_id):
            return t
    return sorted_tracks[0]


class AttentionSelector:
    """根据 policy 在多张活跃 track 之间选择当前 focus。

    Parameters
    ----------
    policy
        见 AttentionPolicy。
    min_focus_s
        焦点保持时间下限（秒）。已成为 focus 的 track 至少要保持 min_focus_s
        才允许被 ROUND_ROBIN 主动切走（其他 policy 在最优 != 当前 focus 时也
        必须等满 min_focus_s 再切，避免抖动）。
    switch_cooldown_s
        两次"切换"动作之间的最小冷却时间。switch_cooldown_s 内即便最优变了
        也不切。
    clock
        monotonic 时间函数（默认 time.monotonic），便于测试注入。
    on_change
        focus 实际改变（含 None ↔ target）时调一次的回调。
    """

    def __init__(
        self,
        *,
        policy: AttentionPolicy = AttentionPolicy.ROUND_ROBIN,
        min_focus_s: float = 3.0,
        switch_cooldown_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        on_change: Optional[AttentionChangeCallback] = None,
    ) -> None:
        if not isinstance(policy, AttentionPolicy):
            policy = AttentionPolicy(str(policy))
        if min_focus_s < 0:
            raise ValueError(f"min_focus_s={min_focus_s} 必须 >= 0")
        if switch_cooldown_s < 0:
            raise ValueError(f"switch_cooldown_s={switch_cooldown_s} 必须 >= 0")
        self._policy = policy
        self._min_focus_s = float(min_focus_s)
        self._cooldown_s = float(switch_cooldown_s)
        self._clock = clock
        self._on_change = on_change

        self._lock = threading.RLock()
        self._current: Optional[AttentionTarget] = None
        self._last_switch_ts: float = -1e9  # 远古，首次切换不被 cooldown 卡

    # ----- 只读 ------
    @property
    def policy(self) -> AttentionPolicy:
        return self._policy

    @property
    def min_focus_s(self) -> float:
        return self._min_focus_s

    @property
    def switch_cooldown_s(self) -> float:
        return self._cooldown_s

    def current(self) -> Optional[AttentionTarget]:
        with self._lock:
            return self._current

    # ----- 主入口 -----
    def select(self, tracks: Iterable) -> Optional[AttentionTarget]:
        """传入当前活跃 tracks（TrackedFace 或鸭子类型），返回当前 focus。

        - 若 tracks 为空：focus → None；若之前非 None，触发一次 on_change。
        - 若有 tracks：按 policy 算 best；当前 focus 仍在 tracks 中时考虑
          min_focus_s / cooldown 决定是否切换；当前 focus 已消失时直接切到 best
          （但仍受 cooldown 限制——cooldown 内强制持有 None 以避免抖出）。

        线程安全说明：on_change 回调在 self._lock 释放之后才被调用，避免下游
        回调里反向回到 selector 上的同步调用（例如再 emit / log / 取 current()）
        触发自死锁或长持锁。
        """
        track_list = list(tracks)
        now = self._clock()
        # 锁内只决定状态转移，把要 fire 的 (prev, curr) 攒到 pending_change
        # 上，锁外再发；on_change 回调通常会做 emit/log/IO，不应在锁内执行。
        pending_change: Optional[tuple] = None  # (prev, curr) 或 None
        result: Optional[AttentionTarget]

        with self._lock:
            prev = self._current

            # 1) 空集：清焦点
            if not track_list:
                if prev is not None:
                    self._current = None
                    self._last_switch_ts = now
                    pending_change = (prev, None)
                result = None
            else:
                # 2) 当前 focus 是否仍在 tracks 中
                cur_still_present = (
                    prev is not None
                    and any(int(getattr(t, "track_id", -1)) == prev.track_id for t in track_list)
                )

                # 3) 按 policy 选 best 候选
                best = self._pick_best(track_list, prev, now)
                if best is None:
                    # 极端：tracks 非空但 picker 没返回（不应发生）；清焦点
                    if prev is not None:
                        self._current = None
                        self._last_switch_ts = now
                        pending_change = (prev, None)
                    result = None
                else:
                    best_target = self._to_target(best, now)

                    # 4) 决定是否切换
                    if prev is None:
                        # 无 focus → 直接采纳 best；不受 cooldown 限制（首次抓住）
                        self._current = best_target
                        self._last_switch_ts = now
                        pending_change = (None, best_target)
                        result = best_target
                    elif cur_still_present and int(best.track_id) == prev.track_id:
                        # best 就是当前 focus，无切换
                        result = prev
                    else:
                        # best 与当前 focus 不同 → 检查门槛
                        in_min_focus = (now - prev.last_focused_ts) < self._min_focus_s
                        in_cooldown = (now - self._last_switch_ts) < self._cooldown_s

                        if cur_still_present and (in_min_focus or in_cooldown):
                            # 当前还在 tracks 里，且未到切换条件 → 维持
                            result = prev
                        else:
                            # (not cur_still_present) and in_cooldown 时：
                            # prev 已从 tracks 中消失，cooldown 是"切换之间"的冷却，
                            # 但 prev 消失属事件强制——若卡死会让 focus 永远落不到
                            # 新 track；故 fallthrough 到下方"触发切换"分支。
                            self._current = best_target
                            self._last_switch_ts = now
                            pending_change = (prev, best_target)
                            result = best_target

        # 锁外 fire：on_change 回调里可以安全地反向调 selector (current() 等)，
        # 也不会因为下游 emit/log 阻塞而长持 selector 锁。
        if pending_change is not None:
            self._fire_change(pending_change[0], pending_change[1])
        return result

    # ----- helpers -----
    def _pick_best(self, tracks: Sequence, prev: Optional[AttentionTarget], now: Optional[float] = None):
        p = self._policy
        if p is AttentionPolicy.LARGEST_FACE:
            return _pick_largest(tracks)
        if p is AttentionPolicy.NEWEST:
            return _pick_newest(tracks)
        if p is AttentionPolicy.NAMED_FIRST:
            return _pick_named_first(tracks)
        # ROUND_ROBIN
        if prev is None:
            return _pick_round_robin(tracks, None)
        # 若当前 focus 仍在 tracks 中且 min_focus_s 未到 → best 仍为它
        # （让外层 select() 看到 best.track_id == prev.track_id 直接维持）
        # now 从 select() 透传过来，与外层判断保持同源时钟；旧调用方未传时回退。
        if now is None:
            now = self._clock()
        if (now - prev.last_focused_ts) < self._min_focus_s and any(
            int(t.track_id) == prev.track_id for t in tracks
        ):
            for t in tracks:
                if int(t.track_id) == prev.track_id:
                    return t
        # 到期 → 切下一个
        return _pick_round_robin(tracks, prev.track_id)

    def _to_target(self, track, now: float) -> AttentionTarget:
        # last_focused_ts: 若当前 focus 还是同一个 track，沿用旧的；否则 now
        existing = self._current
        if existing is not None and existing.track_id == int(track.track_id):
            ts = existing.last_focused_ts
        else:
            ts = now
        return AttentionTarget(
            track_id=int(track.track_id),
            name=getattr(track, "name", None),
            score=float(self._score_for(track)),
            last_focused_ts=ts,
        )

    def _score_for(self, track) -> float:
        p = self._policy
        if p is AttentionPolicy.LARGEST_FACE:
            return float(getattr(track, "area", 0))
        if p is AttentionPolicy.NEWEST:
            return float(getattr(track, "last_seen_ts", 0.0))
        if p is AttentionPolicy.NAMED_FIRST:
            return 1.0 if getattr(track, "name", None) else 0.0
        return float(getattr(track, "track_id", 0))  # ROUND_ROBIN

    def _fire_change(
        self,
        prev: Optional[AttentionTarget],
        curr: Optional[AttentionTarget],
    ) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(prev, curr)
        except Exception as e:  # noqa: BLE001
            log.warning("AttentionSelector on_change callback failed: %s: %s",
                        type(e).__name__, e)


__all__ = [
    "AttentionPolicy",
    "AttentionSelector",
    "AttentionTarget",
]
