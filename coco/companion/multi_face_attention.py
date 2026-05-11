"""coco.companion.multi_face_attention — vision-004b 多人主动致意状态机.

设计目标
========

vision-004 (AttentionSelector) 负责"L0 选择层"：在多张活跃 track 间选 primary。
vision-004b 在其上加 "L1 主动行为层"：当镜头同时出现 >=2 张人脸、primary 已识别
（name 非空但本期更宽松：primary 存在即可）、且 primary 沉默足够长时，向另一个
**已识别** secondary 人脸短暂"打招呼"（一次短 glance + 一句 TTS『你好 <name>』），
然后 RETURN_PRIMARY 回到 primary。

状态机
------

    SINGLE  (tracks < 2 or primary None)
       │
       │  发现 >=2 tracks 且 primary 存在
       ▼
    MULTI_IDLE
       │  primary 沉默 >= silence_threshold_s
       │  且 secondary candidate 在视野持续 >= secondary_visible_s
       │  且距上次 greet >= greet_cooldown_s
       │  且 conv_state == IDLE（不在 LISTENING/THINKING/SPEAKING/TEACHING）
       │  且 proactive_recent=False（距 proactive 主动话题 >= proactive_block_window_s）
       ▼
    GREET_SECONDARY  (输出 ActionSpec)
       │  greet_duration_s 后
       ▼
    RETURN_PRIMARY
       │  return_duration_s 后
       ▼
    MULTI_IDLE (or SINGLE if tracks<2)

输入
----

每次 ``tick(*, tracks, primary, conv_state, proactive_recent=False)`` 接收：

- tracks: Sequence[TrackedFace-like]，需暴露 track_id / name / last_seen_ts
- primary: Optional[AttentionTarget]（vision-004 selector 输出）
- conv_state: 当前 ConvState 字符串值或 enum（只比对 == "idle"）
- proactive_recent: 是否距上次 proactive_topic 仍在抑制窗口（由调用方维护）

输出
----

返回 ``Optional[GreetAction]``。仅在状态进入 ``GREET_SECONDARY`` 的那一 tick 返回
非 None，业务层据此触发 glance + TTS；其他状态返回 None。

事件
----

可选 ``emit_fn(event, **kw)``：状态变化时 emit
``companion.multi_face_attention_state``；触发 greet 时 emit
``companion.greet_secondary``。

线程
----

RLock 保护状态；tick 可被任意线程调用，回调在锁外触发。

注：本模块不直接 import TTS / ExpressionPlayer / EventBus；与 vision-004 的解耦
风格一致，便于单测注入。业务层在 main.py 把回调挂上。
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Optional, Sequence

log = logging.getLogger(__name__)


class MFAState(str, enum.Enum):
    SINGLE = "single"
    MULTI_IDLE = "multi_idle"
    GREET_SECONDARY = "greet_secondary"
    RETURN_PRIMARY = "return_primary"


@dataclass(frozen=True)
class MFAConfig:
    """多人致意状态机配置。

    所有阈值在 ``_validate`` 中 clamp 到安全范围；env 解析见
    ``mfa_config_from_env``。
    """

    enabled: bool = False
    silence_threshold_s: float = 8.0      # primary 沉默触发阈值
    secondary_visible_s: float = 3.0      # secondary 在视野持续时长阈值
    greet_cooldown_s: float = 30.0        # 距上一次 greet_secondary 的冷却
    greet_duration_s: float = 1.2         # GREET 状态持续（短 glance + TTS 大致时长）
    return_duration_s: float = 0.8        # RETURN 状态持续（回 primary 的过渡）
    proactive_block_window_s: float = 3.0 # 距 proactive_topic 的抑制窗口（调用方据此窗口计算 proactive_recent 后传入 tick）
    require_named_secondary: bool = True  # 仅 named secondary 才致意

    def __post_init__(self) -> None:
        # frozen dataclass：用 object.__setattr__ 做 clamp
        def _clamp(name: str, lo: float, hi: float) -> None:
            v = float(getattr(self, name))
            if v < lo:
                log.warning("[mfa] %s=%.2f <%.2f, clamp", name, v, lo)
                object.__setattr__(self, name, lo)
            elif v > hi:
                log.warning("[mfa] %s=%.2f >%.2f, clamp", name, v, hi)
                object.__setattr__(self, name, hi)

        _clamp("silence_threshold_s", 1.0, 600.0)
        _clamp("secondary_visible_s", 0.5, 60.0)
        _clamp("greet_cooldown_s", 5.0, 3600.0)
        _clamp("greet_duration_s", 0.1, 10.0)
        _clamp("return_duration_s", 0.1, 10.0)
        _clamp("proactive_block_window_s", 0.0, 60.0)


def _bool_env(env, key: str, default: bool) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _float_env(env, key: str, default: float, lo: float, hi: float) -> float:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        log.warning("[mfa] %s=%r 非数字，回退 %.2f", key, raw, default)
        return default
    if v < lo:
        log.warning("[mfa] %s=%.2f <%.2f, clamp", key, v, lo)
        return lo
    if v > hi:
        log.warning("[mfa] %s=%.2f >%.2f, clamp", key, v, hi)
        return hi
    return v


def mfa_config_from_env(env=None) -> MFAConfig:
    """从环境变量构造 MFAConfig。COCO_MFA=1 启用。"""
    import os
    env = env if env is not None else os.environ
    return MFAConfig(
        enabled=_bool_env(env, "COCO_MFA", False),
        silence_threshold_s=_float_env(env, "COCO_MFA_SILENCE_S", 8.0, 1.0, 600.0),
        secondary_visible_s=_float_env(env, "COCO_MFA_SECONDARY_VIS_S", 3.0, 0.5, 60.0),
        greet_cooldown_s=_float_env(env, "COCO_MFA_COOLDOWN_S", 30.0, 5.0, 3600.0),
        greet_duration_s=_float_env(env, "COCO_MFA_GREET_DUR_S", 1.2, 0.1, 10.0),
        return_duration_s=_float_env(env, "COCO_MFA_RETURN_DUR_S", 0.8, 0.1, 10.0),
        proactive_block_window_s=_float_env(env, "COCO_MFA_PROACTIVE_BLOCK_S", 3.0, 0.0, 60.0),
        require_named_secondary=_bool_env(env, "COCO_MFA_REQUIRE_NAMED", True),
    )


@dataclass(frozen=True)
class GreetAction:
    """致意动作 spec。业务层据此触发 short glance + TTS。

    - secondary_track_id: 致意目标 track_id
    - secondary_name: 致意目标姓名（用于 TTS 模板填充）
    - utterance: TTS 文本（含 name 替换；调用方可重写）
    - glance_hint: 'left'/'right' 提示，业务层据此选 glance 方向
    - ts: 触发时刻 monotonic
    """

    secondary_track_id: int
    secondary_name: str
    utterance: str
    glance_hint: str
    ts: float


# 回调签名
StateChangeCallback = Callable[[MFAState, MFAState], None]   # (prev, curr)
ActionCallback = Callable[[GreetAction], None]
EmitFn = Callable[..., None]


class MultiFaceAttention:
    """多人主动致意状态机。

    用法
    ----
    mfa = MultiFaceAttention(config=MFAConfig(enabled=True), emit_fn=bus.emit)
    while running:
        action = mfa.tick(
            tracks=snapshot.tracks,
            primary=selector.current(),
            conv_state=conv_sm.current_state,
            proactive_recent=False,
        )
        if action is not None:
            glance(action.glance_hint)
            tts.say(action.utterance)

    线程安全：所有公开方法走 RLock。
    """

    def __init__(
        self,
        config: Optional[MFAConfig] = None,
        *,
        clock: Optional[Callable[[], float]] = None,
        emit_fn: Optional[EmitFn] = None,
        on_state_change: Optional[StateChangeCallback] = None,
        on_action: Optional[ActionCallback] = None,
        utterance_template: str = "你好 {name}",
    ) -> None:
        self.config = config or MFAConfig()
        self._clock = clock or time.monotonic
        self._emit_fn = emit_fn
        self._on_state_change = on_state_change
        self._on_action = on_action
        self._utterance_template = utterance_template

        self._lock = threading.RLock()
        self._state: MFAState = MFAState.SINGLE
        self._state_entered_ts: float = self._clock()
        # primary 沉默起点：每次 primary 切换 / conv_state 离开 IDLE 时刷新
        self._primary_silence_start_ts: float = self._clock()
        self._last_primary_id: Optional[int] = None
        self._last_conv_state_idle: bool = True
        # secondary 候选可视追踪：track_id -> first_seen_ts
        self._secondary_visible_since: dict[int, float] = {}
        # 上次 greet 时刻
        self._last_greet_ts: float = -1e9
        # 当前 GREET 选中的 secondary
        self._current_greet_target: Optional[GreetAction] = None

    # ------------ properties ------------
    @property
    def state(self) -> MFAState:
        with self._lock:
            return self._state

    @property
    def last_greet_ts(self) -> float:
        with self._lock:
            return self._last_greet_ts

    # ------------ main entry ------------
    def tick(
        self,
        *,
        tracks: Iterable[Any],
        primary: Optional[Any],
        conv_state: Any,
        proactive_recent: bool = False,
    ) -> Optional[GreetAction]:
        """推进状态机一拍。返回 GreetAction（仅 GREET 触发那一拍）或 None。

        ``primary`` 鸭子类型：需要 ``track_id`` 属性（兼容 vision-004 的
        AttentionTarget）。``tracks`` 元素需要 ``track_id`` / ``name`` /
        ``last_seen_ts`` 属性（兼容 TrackedFace）。

        ``conv_state``：可为 str（已经是 enum.value）或 enum 对象（取 .value）。
        判断"沉默" = conv_state.value == 'idle'。
        """
        if not self.config.enabled:
            return None

        now = self._clock()
        track_list = list(tracks)
        is_idle = self._is_conv_idle(conv_state)

        pending_state_change: Optional[tuple] = None  # (prev, curr)
        pending_action: Optional[GreetAction] = None

        with self._lock:
            # 1) 维护 primary 沉默计时
            primary_id = int(primary.track_id) if primary is not None else None
            if primary_id != self._last_primary_id:
                # primary 切换 → 重置沉默起点
                self._primary_silence_start_ts = now
                self._last_primary_id = primary_id
            if not is_idle:
                # 不在 IDLE → 重置沉默起点（说话/思考/听都打断沉默）
                self._primary_silence_start_ts = now

            # 2) 维护 secondary 可视计时（仅 named，若 require_named_secondary）
            self._update_secondary_visible(track_list, primary_id, now)

            # 3) 状态机推进
            prev_state = self._state
            new_state = self._advance(
                track_list=track_list,
                primary=primary,
                primary_id=primary_id,
                is_idle=is_idle,
                proactive_recent=proactive_recent,
                now=now,
            )

            if new_state is not self._state:
                self._state = new_state
                self._state_entered_ts = now
                pending_state_change = (prev_state, new_state)
                if new_state is MFAState.GREET_SECONDARY:
                    pending_action = self._current_greet_target
                    if pending_action is not None:
                        self._last_greet_ts = now

        # 锁外回调
        if pending_state_change is not None:
            self._fire_state_change(pending_state_change[0], pending_state_change[1])
            self._emit_state(pending_state_change[0], pending_state_change[1])
        if pending_action is not None:
            self._fire_action(pending_action)
            self._emit_action(pending_action)
        return pending_action

    # ------------ state advance ------------
    def _advance(
        self,
        *,
        track_list: List[Any],
        primary: Optional[Any],
        primary_id: Optional[int],
        is_idle: bool,
        proactive_recent: bool,
        now: float,
    ) -> MFAState:
        cfg = self.config
        cur = self._state

        # SINGLE → MULTI_IDLE 条件：>=2 tracks + primary 存在
        if cur is MFAState.SINGLE:
            if len(track_list) >= 2 and primary is not None:
                return MFAState.MULTI_IDLE
            return MFAState.SINGLE

        # GREET_SECONDARY → RETURN_PRIMARY 时长到期
        if cur is MFAState.GREET_SECONDARY:
            if (now - self._state_entered_ts) >= cfg.greet_duration_s:
                return MFAState.RETURN_PRIMARY
            return MFAState.GREET_SECONDARY

        # RETURN_PRIMARY → MULTI_IDLE/SINGLE 时长到期
        if cur is MFAState.RETURN_PRIMARY:
            if (now - self._state_entered_ts) >= cfg.return_duration_s:
                if len(track_list) >= 2 and primary is not None:
                    return MFAState.MULTI_IDLE
                return MFAState.SINGLE
            return MFAState.RETURN_PRIMARY

        # MULTI_IDLE：判断是否触发 greet
        assert cur is MFAState.MULTI_IDLE
        if len(track_list) < 2 or primary is None:
            return MFAState.SINGLE

        if not is_idle:
            return MFAState.MULTI_IDLE
        if proactive_recent:
            return MFAState.MULTI_IDLE

        silence_elapsed = now - self._primary_silence_start_ts
        if silence_elapsed < cfg.silence_threshold_s:
            return MFAState.MULTI_IDLE

        if (now - self._last_greet_ts) < cfg.greet_cooldown_s:
            return MFAState.MULTI_IDLE

        # 找 secondary 候选：在视野持续 >= secondary_visible_s 的 named track
        candidate = self._pick_secondary(track_list, primary_id, now)
        if candidate is None:
            return MFAState.MULTI_IDLE

        # 触发 greet：构造 GreetAction
        name = getattr(candidate, "name", None) or ""
        utterance = self._utterance_template.format(name=name)
        if not name:
            # name 为空时模板会留下尾部空格（如 "你好 "），rstrip 退化为 "你好"
            utterance = utterance.rstrip()
        glance_hint = self._compute_glance_hint(candidate, primary, track_list)
        action = GreetAction(
            secondary_track_id=int(candidate.track_id),
            secondary_name=name,
            utterance=utterance,
            glance_hint=glance_hint,
            ts=now,
        )
        self._current_greet_target = action
        return MFAState.GREET_SECONDARY

    # ------------ helpers ------------
    def _is_conv_idle(self, conv_state: Any) -> bool:
        if conv_state is None:
            return True
        # 支持 enum / str
        v = getattr(conv_state, "value", conv_state)
        return str(v).lower() == "idle"

    def _is_eligible_secondary(
        self,
        t: Any,
        primary_id: Optional[int],
    ) -> Optional[int]:
        """判定一个 track 是否够格作 secondary candidate。

        返回其 track_id（int）若合格，否则 None。
        合格条件：能解析出 int track_id、非 primary、若
        ``require_named_secondary`` 则 name 非空。
        被 _update_secondary_visible 与 _pick_secondary 共享，避免两处逻辑漂移。
        """
        try:
            tid = int(getattr(t, "track_id"))
        except (TypeError, ValueError, AttributeError):
            return None
        if tid == primary_id:
            return None
        name = getattr(t, "name", None)
        if self.config.require_named_secondary and not name:
            return None
        return tid

    def _update_secondary_visible(
        self,
        tracks: Sequence[Any],
        primary_id: Optional[int],
        now: float,
    ) -> None:
        """维护 secondary candidates 的 first_seen_ts；离开视野则清除。"""
        present_ids: set[int] = set()
        for t in tracks:
            tid = self._is_eligible_secondary(t, primary_id)
            if tid is None:
                continue
            present_ids.add(tid)
            if tid not in self._secondary_visible_since:
                self._secondary_visible_since[tid] = now
        # 清理消失的
        gone = [tid for tid in self._secondary_visible_since if tid not in present_ids]
        for tid in gone:
            del self._secondary_visible_since[tid]

    def _pick_secondary(
        self,
        tracks: Sequence[Any],
        primary_id: Optional[int],
        now: float,
    ) -> Optional[Any]:
        """从持续可视 >= secondary_visible_s 的 named candidates 中挑一个。

        策略：last_seen_ts 最大的（最"新"出现的稳定候选），平手用 track_id 升序。
        """
        cfg = self.config
        eligible: List[Any] = []
        for t in tracks:
            tid = self._is_eligible_secondary(t, primary_id)
            if tid is None:
                continue
            first_seen = self._secondary_visible_since.get(tid)
            if first_seen is None:
                continue
            if (now - first_seen) < cfg.secondary_visible_s:
                continue
            eligible.append(t)
        if not eligible:
            return None
        return max(
            eligible,
            key=lambda t: (float(getattr(t, "last_seen_ts", 0.0)), -int(t.track_id)),
        )

    def _compute_glance_hint(
        self,
        secondary: Any,
        primary: Any,
        tracks: Sequence[Any],
    ) -> str:
        """根据 secondary 与 primary 中心 x 比较，返回 'left'/'right'。

        鸭子类型：secondary / primary 暴露 ``box.cx`` 或 ``smoothed_cx``。
        若都拿不到，默认 'right'（保底不抛）。
        """
        s_cx = self._extract_cx(secondary)
        p_cx = self._extract_cx(primary)
        if s_cx is None or p_cx is None:
            return "right"
        return "left" if s_cx < p_cx else "right"

    def _extract_cx(self, obj: Any) -> Optional[float]:
        if obj is None:
            return None
        # TrackedFace.smoothed_cx
        cx = getattr(obj, "smoothed_cx", None)
        if cx is not None:
            try:
                return float(cx)
            except (TypeError, ValueError):
                pass
        # TrackedFace.box.cx
        box = getattr(obj, "box", None)
        if box is not None:
            cx = getattr(box, "cx", None)
            if cx is not None:
                try:
                    return float(cx)
                except (TypeError, ValueError):
                    pass
        # AttentionTarget 没有 cx；caller 应传 track 对象
        return None

    # ------------ callbacks ------------
    def _fire_state_change(self, prev: MFAState, curr: MFAState) -> None:
        if self._on_state_change is None:
            return
        try:
            self._on_state_change(prev, curr)
        except Exception as e:  # noqa: BLE001
            log.warning("[mfa] on_state_change failed: %s: %s", type(e).__name__, e)

    def _fire_action(self, action: GreetAction) -> None:
        if self._on_action is None:
            return
        try:
            self._on_action(action)
        except Exception as e:  # noqa: BLE001
            log.warning("[mfa] on_action failed: %s: %s", type(e).__name__, e)

    def _emit_state(self, prev: MFAState, curr: MFAState) -> None:
        if self._emit_fn is None:
            return
        try:
            self._emit_fn(
                "companion.multi_face_attention_state",
                component="companion",
                prev=prev.value,
                curr=curr.value,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[mfa] emit state failed: %s: %s", type(e).__name__, e)

    def _emit_action(self, action: GreetAction) -> None:
        if self._emit_fn is None:
            return
        try:
            self._emit_fn(
                "companion.greet_secondary",
                component="companion",
                secondary_track_id=action.secondary_track_id,
                secondary_name=action.secondary_name,
                glance_hint=action.glance_hint,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[mfa] emit greet failed: %s: %s", type(e).__name__, e)


__all__ = [
    "MFAState",
    "MFAConfig",
    "GreetAction",
    "MultiFaceAttention",
    "mfa_config_from_env",
]
