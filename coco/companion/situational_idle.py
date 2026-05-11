"""coco.companion.situational_idle — 情境化 idle 调节（companion-005）.

设计目标
========

在 ``IdleAnimator`` 的 ``IdleConfig.emotion_bias`` 之上叠加一层"情境"调节：

- 焦点目标稳定关注 Coco（gaze attention 持续 ≥ ``focus_stable_threshold_s``）→
  micro_amp 略增（"被注视，活跃一点"）。
- 最近一次交互距今 < ``interaction_recent_s``（默认 30s）→ glance_prob 缩小，
  Coco 看上去专注、不外漂；超过 ``interaction_stale_s`` 长时无交互 → idle 衰减。
- ``PowerState`` 已经在 IdleAnimator 里影响 interval；这里再额外缩 micro_amp
  让 DROWSY/SLEEP 状态下 idle 显得更"懒"。
- face_present == False 持续若干秒 → 衰减；face 刚出现 → 略增。
- ``ProfileStore`` 有兴趣词 → 输出 ``profile_has_interests=True`` 标签，
  本模块本身只把它打到 emit payload，便于未来 proactive 钩子使用。

接口契约
========

- 输入：``IdleSituation`` 快照（dataclass，全字段都允许为 ``None``，便于
  test 注入与 main 渐进接线）。
- 输出：``IdleBias``（micro_amp_scale / glance_prob_scale / glance_amp_scale），
  各字段被 clamp 到 ``[scale_min, scale_max]``。
- IdleAnimator 接收一个 ``SituationalIdleModulator`` 实例（可选）；构造时未注入则
  完全走原 phase-4 路径（向后兼容）。

向后兼容
========

- 默认 OFF（``COCO_SIT_IDLE`` 未设或 ``=0``）。main 在 enabled 时才构造 modulator。
- 任何 snapshot/计算抛异常都 fail-soft：返回 ``IdleBias(1.0, 1.0, 1.0)``。

线程模型
========

- ``compute()`` 是纯计算（不打 IO、不调下游）；线程安全。
- ``snapshot()`` 在 IdleAnimator 后台线程被调用，会读 power_state /
  face_tracker / attention_selector / emotion_tracker / profile_store。
  每个子组件读取都 try-except 包裹，单点故障不影响其他维度。
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# IdleSituation / IdleBias dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdleSituation:
    """情境快照。所有字段允许为 ``None`` —— 缺失时该维度不参与计算。"""

    face_present: Optional[bool] = None
    # focus 在当前 attention target 上保持了多久（秒）。
    # None 表示 attention 未启用或当前无 focus。
    focus_stable_s: Optional[float] = None
    # 距离最近一次交互（wake-word / VAD 触发 / interact 完成）多少秒。
    # None 表示 power_state 未启用或从未交互过。
    time_since_interaction_s: Optional[float] = None
    # power_state 字符串值（'active' / 'drowsy' / 'sleep'）；与 coco.power_state.PowerState 对齐。
    power_state: Optional[str] = None
    # emotion 字符串值；与 coco.emotion.Emotion.value 对齐。
    emotion: Optional[str] = None
    profile_has_interests: bool = False


@dataclass(frozen=True)
class IdleBias:
    """情境调节输出。各字段是乘子，由 IdleAnimator 与 emotion_scale 相乘。"""

    micro_amp_scale: float = 1.0
    glance_prob_scale: float = 1.0
    # glance_amp_scale 当前未直接使用（IdleAnimator 的 glance amp 由 face_tracker 计算），
    # 留作未来扩展位 + verify 锁住接口形状。
    glance_amp_scale: float = 1.0


# ---------------------------------------------------------------------------
# SituationalIdleConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SituationalIdleConfig:
    """情境化 idle 调节参数。"""

    enabled: bool = False
    # focus stable >= 此阈值 → 算"被稳定注视"
    focus_stable_threshold_s: float = 2.0
    # 最近一次交互距今 < 此 → "刚交互完，专注模式"
    interaction_recent_s: float = 30.0
    # 距上次交互 >= 此 → "长时间无交互，idle 衰减"
    interaction_stale_s: float = 180.0

    # 各情境的乘子（叠加：最终 scale = 1.0 * 每个 trigger 的乘子，再 clamp）。
    focus_stable_micro_boost: float = 1.25       # 被注视 → micro 活跃一点
    focus_stable_glance_damp: float = 0.7        # 被注视 → 少四处看
    interaction_recent_glance_damp: float = 0.5  # 刚交互 → glance 频率折半
    interaction_recent_micro_boost: float = 1.1  # 刚交互 → micro 略活
    interaction_stale_damp: float = 0.6          # 长时无交互 → idle 衰减
    drowsy_damp: float = 0.7                     # power=DROWSY 额外衰减 micro_amp
    sleep_damp: float = 0.0                      # power=SLEEP 直接归零（IdleAnimator 内部也会 skip）
    face_absent_damp: float = 0.8                # face 不在 → idle 弱一点
    emotion_happy_extra_boost: float = 1.1       # emotion=happy 与情境叠加
    emotion_sad_extra_damp: float = 0.9

    # 输出 clamp 区间
    scale_min: float = 0.0
    scale_max: float = 2.0

    def validate(self) -> None:
        if not (0.1 <= self.focus_stable_threshold_s <= 60.0):
            raise ValueError(f"focus_stable_threshold_s={self.focus_stable_threshold_s} 越界")
        if not (1.0 <= self.interaction_recent_s <= 3600.0):
            raise ValueError(f"interaction_recent_s={self.interaction_recent_s} 越界")
        if not (self.interaction_recent_s < self.interaction_stale_s <= 86400.0):
            raise ValueError(
                f"interaction_stale_s={self.interaction_stale_s} 必须 > interaction_recent_s"
            )
        if not (0.0 <= self.scale_min <= self.scale_max <= 10.0):
            raise ValueError(f"scale_min/max=[{self.scale_min},{self.scale_max}] 不合法")


# ---------------------------------------------------------------------------
# env helpers
# ---------------------------------------------------------------------------


def _bool_env(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


def _float_env(env: Mapping[str, str], key: str, default: float, lo: float, hi: float) -> float:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        log.warning("[sit_idle] %s=%r 非数字，回退默认 %.2f", key, raw, default)
        return default
    if v < lo:
        log.warning("[sit_idle] %s=%.2f <%.2f，clamp", key, v, lo)
        return lo
    if v > hi:
        log.warning("[sit_idle] %s=%.2f >%.2f，clamp", key, v, hi)
        return hi
    return v


def situational_idle_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    e = env if env is not None else os.environ
    return _bool_env(e, "COCO_SIT_IDLE", False)


def situational_idle_config_from_env(env: Optional[Mapping[str, str]] = None) -> SituationalIdleConfig:
    e = env if env is not None else os.environ
    cfg = SituationalIdleConfig(
        enabled=_bool_env(e, "COCO_SIT_IDLE", False),
        focus_stable_threshold_s=_float_env(e, "COCO_SIT_IDLE_FOCUS_STABLE_S", 2.0, 0.1, 60.0),
        interaction_recent_s=_float_env(e, "COCO_SIT_IDLE_RECENT_S", 30.0, 1.0, 3600.0),
        interaction_stale_s=_float_env(e, "COCO_SIT_IDLE_STALE_S", 180.0, 2.0, 86400.0),
        scale_min=_float_env(e, "COCO_SIT_IDLE_SCALE_MIN", 0.0, 0.0, 10.0),
        scale_max=_float_env(e, "COCO_SIT_IDLE_SCALE_MAX", 2.0, 0.1, 10.0),
    )
    try:
        cfg.validate()
    except ValueError as exc:
        log.warning("[sit_idle] config invalid, fallback to defaults: %s", exc)
        cfg = SituationalIdleConfig(enabled=cfg.enabled)
    return cfg


# ---------------------------------------------------------------------------
# SituationalIdleModulator
# ---------------------------------------------------------------------------


class SituationalIdleModulator:
    """主类：根据注入的 power_state / face_tracker / attention_selector /
    emotion_tracker / profile_store 实时输出 IdleBias。

    Parameters
    ----------
    config
        ``SituationalIdleConfig`` 实例。
    power_state, face_tracker, attention_selector, emotion_tracker, profile_store
        全部可选；缺失的维度不参与计算（snapshot 中对应字段为 None）。
    clock
        monotonic 时间函数（默认 time.monotonic），便于测试注入。
    emit_cb
        bias 输出与上轮不同（>=1% diff 任一字段）时回调；用于 main 发
        "companion.idle_situation_changed" event。回调签名 (prev, curr, situation)。
    """

    def __init__(
        self,
        *,
        config: SituationalIdleConfig,
        power_state: Any = None,
        face_tracker: Any = None,
        attention_selector: Any = None,
        emotion_tracker: Any = None,
        profile_store: Any = None,
        clock=time.monotonic,
        emit_cb=None,
    ) -> None:
        config.validate()
        self._cfg = config
        self._power = power_state
        self._face = face_tracker
        self._sel = attention_selector
        self._emo = emotion_tracker
        self._profile = profile_store
        self._clock = clock
        self._emit_cb = emit_cb
        self._last_bias: Optional[IdleBias] = None
        self._last_focus_id: Optional[int] = None
        self._focus_started_at: Optional[float] = None
        # 缓存最近一次成功读到的 profile 信号（避免每 tick 都打 IO）
        self._profile_has_interests: bool = False
        self._profile_checked: bool = False

    @property
    def config(self) -> SituationalIdleConfig:
        return self._cfg

    # ------------------------------------------------------------------
    # snapshot / compute
    # ------------------------------------------------------------------

    def snapshot(self) -> IdleSituation:
        """从注入的依赖里拼出 IdleSituation。单点失败 fail-soft。"""
        face_present: Optional[bool] = None
        focus_stable_s: Optional[float] = None
        time_since_interaction_s: Optional[float] = None
        power_state: Optional[str] = None
        emotion: Optional[str] = None

        # face_present
        if self._face is not None:
            try:
                face_present = bool(self._face.latest().present)
            except Exception:  # noqa: BLE001
                face_present = None

        # focus_stable_s
        if self._sel is not None:
            try:
                tgt = self._sel.current()
                now = self._clock()
                if tgt is not None:
                    tid = int(getattr(tgt, "track_id", -1))
                    if tid != self._last_focus_id or self._focus_started_at is None:
                        self._last_focus_id = tid
                        self._focus_started_at = now
                    focus_stable_s = max(0.0, now - self._focus_started_at)
                else:
                    self._last_focus_id = None
                    self._focus_started_at = None
                    focus_stable_s = 0.0
            except Exception:  # noqa: BLE001
                focus_stable_s = None

        # time_since_interaction_s & power_state
        if self._power is not None:
            try:
                power_state = str(self._power.current_state.value)
            except Exception:  # noqa: BLE001
                power_state = None
            try:
                time_since_interaction_s = float(self._power.idle_for)
            except Exception:  # noqa: BLE001
                time_since_interaction_s = None

        # emotion
        if self._emo is not None:
            try:
                cur = getattr(self._emo, "current", None)
                if callable(cur):
                    cur = cur()
                if cur is None:
                    emotion = None
                else:
                    v = getattr(cur, "value", cur)
                    emotion = str(v).lower() if isinstance(v, str) else None
            except Exception:  # noqa: BLE001
                emotion = None

        # profile_has_interests：只读一次（profile load 是同步 IO）
        if self._profile is not None and not self._profile_checked:
            try:
                p = self._profile.load()
                self._profile_has_interests = bool(getattr(p, "interests", None))
            except Exception:  # noqa: BLE001
                self._profile_has_interests = False
            self._profile_checked = True

        return IdleSituation(
            face_present=face_present,
            focus_stable_s=focus_stable_s,
            time_since_interaction_s=time_since_interaction_s,
            power_state=power_state,
            emotion=emotion,
            profile_has_interests=self._profile_has_interests,
        )

    def compute(self, sit: Optional[IdleSituation] = None) -> IdleBias:
        """根据 sit（默认调用 snapshot）算 IdleBias。"""
        try:
            if sit is None:
                sit = self.snapshot()
            return self._compute_inner(sit)
        except Exception as exc:  # noqa: BLE001
            log.warning("[sit_idle] compute failed: %s: %s — fallback 1.0×1.0", type(exc).__name__, exc)
            return IdleBias()

    def _compute_inner(self, sit: IdleSituation) -> IdleBias:
        cfg = self._cfg
        micro = 1.0
        glance_prob = 1.0
        glance_amp = 1.0

        # power_state
        if sit.power_state == "drowsy":
            micro *= cfg.drowsy_damp
            glance_prob *= cfg.drowsy_damp
        elif sit.power_state == "sleep":
            micro *= cfg.sleep_damp
            glance_prob *= cfg.sleep_damp
            glance_amp *= cfg.sleep_damp

        # focus stable
        if sit.focus_stable_s is not None and sit.focus_stable_s >= cfg.focus_stable_threshold_s:
            micro *= cfg.focus_stable_micro_boost
            glance_prob *= cfg.focus_stable_glance_damp

        # interaction recency / staleness
        tsi = sit.time_since_interaction_s
        if tsi is not None:
            if tsi < cfg.interaction_recent_s:
                micro *= cfg.interaction_recent_micro_boost
                glance_prob *= cfg.interaction_recent_glance_damp
            elif tsi >= cfg.interaction_stale_s:
                micro *= cfg.interaction_stale_damp
                glance_prob *= cfg.interaction_stale_damp

        # face presence
        if sit.face_present is False:
            micro *= cfg.face_absent_damp

        # emotion 叠加（与 IdleConfig.emotion_bias 是相互独立的两层，
        # IdleAnimator 把两层相乘）。
        if sit.emotion == "happy":
            micro *= cfg.emotion_happy_extra_boost
        elif sit.emotion == "sad":
            micro *= cfg.emotion_sad_extra_damp

        return IdleBias(
            micro_amp_scale=_clamp(micro, cfg.scale_min, cfg.scale_max),
            glance_prob_scale=_clamp(glance_prob, cfg.scale_min, cfg.scale_max),
            glance_amp_scale=_clamp(glance_amp, cfg.scale_min, cfg.scale_max),
        )

    # ------------------------------------------------------------------
    # tick — IdleAnimator 在每次 sample interval / micro 前调用
    # ------------------------------------------------------------------

    def tick(self) -> IdleBias:
        """计算 + diff 触发 emit_cb。返回当前 bias。"""
        sit = self.snapshot()
        bias = self.compute(sit)
        prev = self._last_bias
        if self._emit_cb is not None and _bias_changed(prev, bias):
            try:
                self._emit_cb(prev, bias, sit)
            except Exception as exc:  # noqa: BLE001
                log.warning("[sit_idle] emit_cb failed: %s: %s", type(exc).__name__, exc)
        self._last_bias = bias
        return bias


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _bias_changed(prev: Optional[IdleBias], curr: IdleBias, tol: float = 0.01) -> bool:
    if prev is None:
        return True
    return (
        abs(prev.micro_amp_scale - curr.micro_amp_scale) > tol
        or abs(prev.glance_prob_scale - curr.glance_prob_scale) > tol
        or abs(prev.glance_amp_scale - curr.glance_amp_scale) > tol
    )


__all__ = [
    "IdleBias",
    "IdleSituation",
    "SituationalIdleConfig",
    "SituationalIdleModulator",
    "situational_idle_config_from_env",
    "situational_idle_enabled_from_env",
]
