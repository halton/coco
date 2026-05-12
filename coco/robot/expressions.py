"""coco.robot.expressions — robot-003 表情序列编排器（sim-only）.

设计目标
========

把"表情"抽象成由若干 :class:`ExpressionFrame` 组成的剧本
（:class:`ExpressionSequence`），上层用 ``player.play("welcome")``、
``say(text, expression="praise")`` 触发，无需各自硬编码 goto_target。

线程模型
--------

- 单一 _play_lock：同一时刻只允许一个 play 在执行；并发 play 直接 reject 并
  emit ``robot.expression_busy``。这避免了 ReachyMini SDK 在两个线程并发
  goto_target 时产生未定义行为。
- 与 IdleAnimator 用 ``pause()/resume()`` 协调：play 开始前 pause，结束/异常后
  resume（即便 idle_animator 为 None 也安全）。
- player 自身不起后台线程；play() 同步阻塞直到剧本结束（每帧 SDK 调用
  ``goto_target(duration=...)`` 已经是 wait_for_task_completion 的阻塞调用）。

安全
----

每帧 yaw/pitch 通过 ``coco.actions._check_amplitude`` 限幅（也是 actions.MAX_*）。
duration 通过 ``_check_duration``。库内预设全部走默认范围。
``global_speed_scale`` 对所有 frame 的 duration 做反向缩放并 clamp 到
[MIN_DURATION_S, MAX_DURATION_S]。

完全 sim 可验证：mockup-sim daemon 收到的一串 SetTarget 即可断言；不依赖真硬件。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

from coco.actions import (
    INIT_HEAD_POSE,
    MAX_DURATION_S,
    MAX_PITCH_DEG,
    MAX_YAW_DEG,
    MIN_DURATION_S,
    euler_pose,
    _check_amplitude,
    _check_duration,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpressionFrame:
    """一帧表情：head pose + duration + 可选 emotion 标注。

    - duration_s: 该帧 goto_target 的 duration（秒），clamp [MIN_DURATION_S, MAX_DURATION_S]
    - yaw_deg / pitch_deg: head 目标姿态（度）。validate 时按 MAX_YAW_DEG / MAX_PITCH_DEG 检查
    - head_speed_scale: 保留字段（>1 = 该帧动作意图更快；当前实现下不直接拉快 SDK 内部插值，
      仅作为元数据传递给未来 SDK 升级）。
    - micro_amp_scale / glance_prob_scale: 仅在 IdleAnimator pause 期间不生效；保留语义用于
      未来与 IdleBias / SituationalModulator 叠加。
    - emotion_label: 该帧情绪标签（log/emit 用，不影响 SDK 调用）。
    """

    duration_s: float
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    head_speed_scale: float = 1.0
    micro_amp_scale: float = 1.0
    glance_prob_scale: float = 1.0
    emotion_label: Optional[str] = None

    def validate(self) -> None:
        _check_amplitude(self.yaw_deg, MAX_YAW_DEG, "yaw_deg")
        _check_amplitude(self.pitch_deg, MAX_PITCH_DEG, "pitch_deg")
        _check_duration(self.duration_s)
        if not (0.1 <= self.head_speed_scale <= 5.0):
            raise ValueError(
                f"head_speed_scale={self.head_speed_scale} out of range [0.1, 5.0]"
            )
        if not (0.0 <= self.micro_amp_scale <= 5.0):
            raise ValueError(
                f"micro_amp_scale={self.micro_amp_scale} out of range [0.0, 5.0]"
            )
        if not (0.0 <= self.glance_prob_scale <= 5.0):
            raise ValueError(
                f"glance_prob_scale={self.glance_prob_scale} out of range [0.0, 5.0]"
            )


@dataclass(frozen=True)
class ExpressionSequence:
    """一段表情剧本。

    - name: 库内唯一名（小写，用于查找）
    - frames: 至少 1 帧
    - cooldown_s: 同名 expression 两次 play 之间的最小间隔；0 表示不限。
    - return_to_center: 最后一帧后是否自动回中位（额外 0.3s goto_target(INIT_HEAD_POSE)）
    """

    name: str
    frames: List[ExpressionFrame]
    cooldown_s: float = 1.0
    return_to_center: bool = True

    def validate(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise ValueError("ExpressionSequence.name must be non-empty str")
        if not self.frames:
            raise ValueError(f"ExpressionSequence({self.name}) has 0 frames")
        if self.cooldown_s < 0:
            raise ValueError(f"cooldown_s={self.cooldown_s} must be >=0")
        for i, f in enumerate(self.frames):
            try:
                f.validate()
            except ValueError as e:
                raise ValueError(
                    f"ExpressionSequence({self.name}) frame[{i}] invalid: {e}"
                ) from e


# ---------------------------------------------------------------------------
# 预设库
# ---------------------------------------------------------------------------


def _build_library() -> Dict[str, ExpressionSequence]:
    """构造内置 5 个预设 + 4 个别名/扩展，总计 9 个。

    与 feature_list 的 verification 对齐（welcome / thinking / praise / confused / shy
    必须存在），同时把 spec 中提到的 excited / agreeing / denying / looking_around
    作为额外预设落地，方便业务层语义对接。
    """
    lib: Dict[str, ExpressionSequence] = {}

    # ---- welcome：抬头微笑 + 左右轻摆 ----
    lib["welcome"] = ExpressionSequence(
        name="welcome",
        frames=[
            ExpressionFrame(duration_s=0.35, pitch_deg=-6.0, yaw_deg=+8.0,
                            emotion_label="welcome"),
            ExpressionFrame(duration_s=0.35, pitch_deg=-6.0, yaw_deg=-8.0,
                            emotion_label="welcome"),
            ExpressionFrame(duration_s=0.30, pitch_deg=-3.0, yaw_deg=0.0,
                            emotion_label="welcome"),
        ],
        cooldown_s=2.0,
    )

    # ---- thinking：缓慢右倾 + 低头长 micro ----
    lib["thinking"] = ExpressionSequence(
        name="thinking",
        frames=[
            ExpressionFrame(duration_s=0.55, yaw_deg=-8.0, pitch_deg=+5.0,
                            emotion_label="thinking"),
            ExpressionFrame(duration_s=0.50, yaw_deg=-10.0, pitch_deg=+7.0,
                            head_speed_scale=0.8, emotion_label="thinking"),
        ],
        cooldown_s=2.5,
    )

    # ---- praise：连续 2 次点头 ----
    lib["praise"] = ExpressionSequence(
        name="praise",
        frames=[
            ExpressionFrame(duration_s=0.25, pitch_deg=+12.0, emotion_label="praise"),
            ExpressionFrame(duration_s=0.25, pitch_deg=-5.0, emotion_label="praise"),
            ExpressionFrame(duration_s=0.25, pitch_deg=+12.0, emotion_label="praise"),
            ExpressionFrame(duration_s=0.25, pitch_deg=-5.0, emotion_label="praise"),
        ],
        cooldown_s=1.5,
    )

    # ---- confused：歪头 + 短摇头 ----
    lib["confused"] = ExpressionSequence(
        name="confused",
        frames=[
            ExpressionFrame(duration_s=0.30, yaw_deg=-7.0, pitch_deg=-4.0,
                            emotion_label="confused"),
            ExpressionFrame(duration_s=0.30, yaw_deg=+7.0, pitch_deg=-4.0,
                            emotion_label="confused"),
            ExpressionFrame(duration_s=0.30, yaw_deg=-5.0, pitch_deg=-2.0,
                            emotion_label="confused"),
        ],
        cooldown_s=2.0,
    )

    # ---- shy：低头 + 小幅左右 ----
    lib["shy"] = ExpressionSequence(
        name="shy",
        frames=[
            ExpressionFrame(duration_s=0.35, pitch_deg=+10.0, yaw_deg=-4.0,
                            emotion_label="shy"),
            ExpressionFrame(duration_s=0.35, pitch_deg=+10.0, yaw_deg=+4.0,
                            emotion_label="shy"),
            ExpressionFrame(duration_s=0.30, pitch_deg=+6.0, yaw_deg=0.0,
                            emotion_label="shy"),
        ],
        cooldown_s=2.0,
    )

    # ---- excited：快速点头 + yaw 摆动（spec 别名）----
    lib["excited"] = ExpressionSequence(
        name="excited",
        frames=[
            ExpressionFrame(duration_s=0.20, pitch_deg=+8.0, yaw_deg=+5.0,
                            head_speed_scale=1.4, emotion_label="excited"),
            ExpressionFrame(duration_s=0.20, pitch_deg=-4.0, yaw_deg=-5.0,
                            head_speed_scale=1.4, emotion_label="excited"),
            ExpressionFrame(duration_s=0.20, pitch_deg=+8.0, yaw_deg=0.0,
                            head_speed_scale=1.4, emotion_label="excited"),
        ],
        cooldown_s=1.5,
    )

    # ---- agreeing：单次清晰点头 ----
    lib["agreeing"] = ExpressionSequence(
        name="agreeing",
        frames=[
            ExpressionFrame(duration_s=0.30, pitch_deg=+10.0, emotion_label="agreeing"),
            ExpressionFrame(duration_s=0.30, pitch_deg=-3.0, emotion_label="agreeing"),
            ExpressionFrame(duration_s=0.30, pitch_deg=+10.0, emotion_label="agreeing"),
            ExpressionFrame(duration_s=0.30, pitch_deg=0.0, emotion_label="agreeing"),
        ],
        cooldown_s=1.5,
    )

    # ---- denying：连续 2 次摇头 ----
    lib["denying"] = ExpressionSequence(
        name="denying",
        frames=[
            ExpressionFrame(duration_s=0.25, yaw_deg=-12.0, emotion_label="denying"),
            ExpressionFrame(duration_s=0.25, yaw_deg=+12.0, emotion_label="denying"),
            ExpressionFrame(duration_s=0.25, yaw_deg=-12.0, emotion_label="denying"),
            ExpressionFrame(duration_s=0.25, yaw_deg=0.0, emotion_label="denying"),
        ],
        cooldown_s=1.5,
    )

    # ---- looking_around：3 次大幅 glance ----
    lib["looking_around"] = ExpressionSequence(
        name="looking_around",
        frames=[
            ExpressionFrame(duration_s=0.40, yaw_deg=+25.0, emotion_label="looking_around"),
            ExpressionFrame(duration_s=0.40, yaw_deg=-25.0, emotion_label="looking_around"),
            ExpressionFrame(duration_s=0.40, yaw_deg=0.0, emotion_label="looking_around"),
        ],
        cooldown_s=2.5,
    )

    # 自我校验：库构造时一次性 validate，提前暴露任何越界
    for seq in lib.values():
        seq.validate()
    return lib


EXPRESSION_LIBRARY: Dict[str, ExpressionSequence] = _build_library()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpressionsConfig:
    """robot-003 配置。

    - enabled: COCO_EXPRESSIONS=1 启用；默认 OFF（向后兼容 phase 内其它 feature）
    - cooldown_default_s: ExpressionSequence.cooldown_s 缺省（库内显式设置时不覆盖）
    - global_speed_scale: 对所有 frame.duration_s 做反向缩放（>1 = 整体更快），
      最终 duration clamp 到 [MIN_DURATION_S, MAX_DURATION_S]
    """

    enabled: bool = False
    cooldown_default_s: float = 1.0
    global_speed_scale: float = 1.0

    def __post_init__(self) -> None:
        # L2: 让非 env 构造路径（直接 ExpressionsConfig(...)）也安全 clamp。
        # frozen=True → 用 object.__setattr__ 绕过；env 路径已 clamp，这里幂等。
        cd = self.cooldown_default_s
        if cd < 0.0:
            cd = 0.0
        elif cd > 30.0:
            cd = 30.0
        if cd != self.cooldown_default_s:
            object.__setattr__(self, "cooldown_default_s", cd)

        sp = self.global_speed_scale
        if sp < 0.25:
            sp = 0.25
        elif sp > 4.0:
            sp = 4.0
        if sp != self.global_speed_scale:
            object.__setattr__(self, "global_speed_scale", sp)


def _bool_env(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


def _float_env(
    env: Mapping[str, str], key: str, default: float, lo: float, hi: float
) -> float:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        log.warning("[robot.expr] %s=%r 非数字，回退 %.2f", key, raw, default)
        return default
    if v < lo:
        log.warning("[robot.expr] %s=%.2f <%.2f，clamp 到 %.2f", key, v, lo, lo)
        return lo
    if v > hi:
        log.warning("[robot.expr] %s=%.2f >%.2f，clamp 到 %.2f", key, v, hi, hi)
        return hi
    return v


def expressions_config_from_env(
    env: Optional[Mapping[str, str]] = None,
) -> ExpressionsConfig:
    """从 env 构造 ExpressionsConfig。

    - ``COCO_EXPRESSIONS`` ∈ {0,1}（默认 0）
    - ``COCO_EXPRESSIONS_COOLDOWN_S`` clamp [0.0, 30.0]，默认 1.0
    - ``COCO_EXPRESSIONS_SPEED`` clamp [0.25, 4.0]，默认 1.0
    """
    e = env if env is not None else os.environ
    return ExpressionsConfig(
        enabled=_bool_env(e, "COCO_EXPRESSIONS", default=False),
        cooldown_default_s=_float_env(
            e, "COCO_EXPRESSIONS_COOLDOWN_S", default=1.0, lo=0.0, hi=30.0
        ),
        global_speed_scale=_float_env(
            e, "COCO_EXPRESSIONS_SPEED", default=1.0, lo=0.25, hi=4.0
        ),
    )


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------


@dataclass
class ExpressionPlayerStats:
    """运行时统计；verify / evidence 用。"""

    plays_started: int = 0
    plays_completed: int = 0
    plays_skipped_cooldown: int = 0
    plays_rejected_busy: int = 0
    plays_not_found: int = 0
    frames_dispatched: int = 0
    sdk_errors: int = 0
    last_played: Optional[str] = None
    last_played_ts: float = 0.0


class ExpressionPlayer:
    """表情剧本播放器。

    用法：
        player = ExpressionPlayer(robot, idle_animator=idle, config=ExpressionsConfig(enabled=True))
        player.play("welcome")
        ...
        player.stop()

    线程安全：
        - 单一 _play_lock 保证同时只有一个 play 在执行
        - 与 IdleAnimator 通过 pause()/resume() 协调
        - stop() 设置 _stopped 标志，在每帧前检查；不会强制中断 SDK 阻塞调用
    """

    def __init__(
        self,
        robot: Any,
        *,
        idle_animator: Any = None,
        config: Optional[ExpressionsConfig] = None,
        library: Optional[Dict[str, ExpressionSequence]] = None,
        emit_fn: Optional[Callable[..., None]] = None,
        clock: Optional[Callable[[], float]] = None,
        posture_baseline: Any = None,
    ) -> None:
        self.robot = robot
        self.idle_animator = idle_animator
        self.config = config or ExpressionsConfig()
        self.library: Dict[str, ExpressionSequence] = dict(library or EXPRESSION_LIBRARY)
        self.stats = ExpressionPlayerStats()
        self._emit = emit_fn  # None → 延迟 import logging_setup.emit
        self.clock = clock or time.monotonic
        self._play_lock = threading.Lock()
        # cooldown 记账：name -> 上次完成时刻
        self._last_play_ts: Dict[str, float] = {}
        self._stopped = False
        # robot-004: 可选 posture baseline modulator；play 期间 pause 其天线下发，
        # 避免与 expression 帧绝对值打架。
        self.posture_baseline = posture_baseline

    # ---- 公开接口 ----

    def stop(self) -> None:
        """请求停止当前/未来的 play。不会强制中断 SDK 阻塞调用；
        正在执行的 play 会在下一帧前检查并 return。
        """
        self._stopped = True

    def is_busy(self) -> bool:
        return self._play_lock.locked()

    def play(self, name: str) -> bool:
        """同步播放命名 expression。

        Returns:
            True：播放完成（或开始播放并自然结束）
            False：被 cooldown / busy / not_found / stopped 跳过
        """
        if self._stopped:
            return False
        if name not in self.library:
            self.stats.plays_not_found += 1
            log.warning("[robot.expr] expression %r not found", name)
            self._emit_event(
                "robot.expression_not_found", message=f"expression={name} 不在库内",
                expression=name,
            )
            return False

        # cooldown 检查（锁外读 _last_play_ts 是脏读但 fail-soft：跳一次没事）
        seq = self.library[name]
        now = self.clock()
        last = self._last_play_ts.get(name, 0.0)
        cooldown = seq.cooldown_s if seq.cooldown_s > 0 else self.config.cooldown_default_s
        if cooldown > 0 and last > 0 and (now - last) < cooldown:
            self.stats.plays_skipped_cooldown += 1
            log.info(
                "[robot.expr] cooldown skip name=%s since_last=%.2fs cooldown=%.2fs",
                name, now - last, cooldown,
            )
            self._emit_event(
                "robot.expression_cooldown_skip",
                message=f"expression={name} 冷却中（{cooldown:.1f}s）",
                expression=name,
                cooldown_s=float(cooldown),
                since_last_s=float(now - last),
            )
            return False

        # 并发：tryacquire；获取不到立刻拒（不排队）
        if not self._play_lock.acquire(blocking=False):
            self.stats.plays_rejected_busy += 1
            log.info("[robot.expr] busy reject name=%s", name)
            self._emit_event(
                "robot.expression_busy",
                message=f"expression={name} 被并发拒绝（player 忙）",
                expression=name,
            )
            return False

        try:
            return self._play_locked(seq)
        finally:
            self._play_lock.release()

    # ---- 内部 ----

    def _play_locked(self, seq: ExpressionSequence) -> bool:
        self.stats.plays_started += 1
        # 1) pause idle
        if self.idle_animator is not None:
            try:
                self.idle_animator.pause()
            except Exception as e:  # noqa: BLE001
                log.warning("[robot.expr] idle.pause failed: %s: %s", type(e).__name__, e)
        # robot-004: pause posture baseline antenna 下发（避免与 expression 帧 SetTarget 打架）
        if self.posture_baseline is not None:
            try:
                self.posture_baseline.pause()
            except Exception as e:  # noqa: BLE001
                log.warning("[robot.expr] posture_baseline.pause failed: %s: %s", type(e).__name__, e)

        t0 = self.clock()
        frames_done = 0
        try:
            for i, frame in enumerate(seq.frames):
                if self._stopped:
                    log.info("[robot.expr] stopped mid-play name=%s at frame=%d", seq.name, i)
                    break
                self._dispatch_frame(seq.name, i, frame)
                frames_done += 1
            # 收尾：可选回中位
            if seq.return_to_center and not self._stopped:
                try:
                    self.robot.goto_target(head=INIT_HEAD_POSE, duration=0.3)
                except Exception as e:  # noqa: BLE001
                    self.stats.sdk_errors += 1
                    log.warning(
                        "[robot.expr] return_to_center failed: %s: %s",
                        type(e).__name__, e,
                    )
        finally:
            # 2) resume idle
            if self.idle_animator is not None:
                try:
                    self.idle_animator.resume()
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "[robot.expr] idle.resume failed: %s: %s", type(e).__name__, e
                    )
            # robot-004: resume posture baseline 天线下发
            if self.posture_baseline is not None:
                try:
                    self.posture_baseline.resume()
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "[robot.expr] posture_baseline.resume failed: %s: %s", type(e).__name__, e
                    )

        t1 = self.clock()
        # L1-2: 仅在确有帧成功 dispatch 时才记 cooldown / 计 plays_completed。
        # frames_done==0（SDK 全失败或 stop() 在第一帧前命中）不应被记成"刚播过"，
        # 否则短暂故障会被放大成一整个 cooldown 周期的静默——同名表情会被 skip。
        if frames_done > 0:
            self._last_play_ts[seq.name] = t1
            self.stats.plays_completed += 1
        self.stats.last_played = seq.name
        self.stats.last_played_ts = t1
        self._emit_event(
            "robot.expression_played",
            message=f"expression={seq.name} frames={frames_done}",
            expression=seq.name,
            frames=frames_done,
            duration_s=round(t1 - t0, 3),
            cooldown_s=float(seq.cooldown_s),
        )
        return frames_done > 0

    def _dispatch_frame(self, name: str, idx: int, frame: ExpressionFrame) -> None:
        """把单帧翻译成 SDK 调用。失败 fail-soft：log + stats，不抛。"""
        # global_speed_scale: speed=2.0 → duration / 2.0
        scale = max(0.01, float(self.config.global_speed_scale))
        dur = frame.duration_s / scale
        # clamp
        if dur < MIN_DURATION_S:
            dur = MIN_DURATION_S
        elif dur > MAX_DURATION_S:
            dur = MAX_DURATION_S
        target = euler_pose(yaw_deg=frame.yaw_deg, pitch_deg=frame.pitch_deg)
        try:
            self.robot.goto_target(head=target, duration=dur)
            self.stats.frames_dispatched += 1
        except Exception as e:  # noqa: BLE001
            self.stats.sdk_errors += 1
            log.warning(
                "[robot.expr] frame[%d] of %s SDK failed: %s: %s",
                idx, name, type(e).__name__, e,
            )

    def _emit_event(self, component_event: str, message: str = "", **payload: Any) -> None:
        try:
            fn = self._emit
            if fn is None:
                from coco.logging_setup import emit as _emit
                fn = _emit
            fn(component_event, message, **payload)
        except Exception as e:  # noqa: BLE001
            log.warning("[robot.expr] emit failed: %s: %s", type(e).__name__, e)


__all__ = [
    "ExpressionFrame",
    "ExpressionSequence",
    "ExpressionPlayer",
    "ExpressionPlayerStats",
    "ExpressionsConfig",
    "EXPRESSION_LIBRARY",
    "expressions_config_from_env",
]
