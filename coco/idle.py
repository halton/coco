"""coco.idle — 陪伴动作循环（companion-001）.

设计目标：
- Coco 在 idle 状态下不显得僵硬：每隔几秒做一次微动（呼吸感），
  偶尔触发一次完整环顾（look_left / look_right）。
- 必须可被 stop_event 立刻打断（< 200ms 内退出循环）。
- 线程安全；不阻塞 ReachyMiniApp.run() 主循环（自己跑后台线程）。
- 幅度永远小于 robot-002 的 look_* 默认值，避免疯狂抖动；周期上限保证不扰人。

类型分两档：
- micro：~每 2-4s 一次，head 微抖 ±2-3° yaw/pitch 或天线小摆，0.6s 完成
- glance：~每 12-25s 一次，完整 look_left 或 look_right（amp=15°）

调度全部用 stop_event.wait(timeout=...) 计时，
任何时候 set() stop_event 都能在当前 wait/动作完成内退出。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from coco.actions import (
    INIT_HEAD_POSE,
    MAX_PITCH_DEG,
    MAX_YAW_DEG,
    euler_pose,
    look_left,
    look_right,
)

if TYPE_CHECKING:  # pragma: no cover
    from reachy_mini import ReachyMini
    from coco.perception.face_tracker import FaceTracker
    from coco.power_state import PowerStateMachine
    from coco.emotion import Emotion


log = logging.getLogger(__name__)


@dataclass
class IdleConfig:
    """Idle 循环参数。所有上限严格小于 robot-002 安全上限。"""

    # 微动间隔（秒）— 在 [min, max] 间均匀采样
    micro_interval_min: float = 2.0
    micro_interval_max: float = 4.5
    # 微动幅度（度）— head yaw/pitch 微抖
    micro_yaw_amp_deg: float = 2.5
    micro_pitch_amp_deg: float = 2.0
    # 单次微动时长
    micro_duration: float = 0.6
    # 天线微摆幅度（弧度）
    micro_antenna_amp_rad: float = 0.15
    # 微动各子类型概率（必须和 = 1）：head_wobble / antenna_wave / breathe(回中)
    micro_p_head: float = 0.5
    micro_p_antenna: float = 0.3
    micro_p_breathe: float = 0.2

    # 环顾间隔（秒）— glance 是 amp=15° 的完整 look_left/right
    glance_interval_min: float = 12.0
    glance_interval_max: float = 25.0
    glance_amp_deg: float = 15.0
    glance_duration: float = 0.5

    # --- companion-002: face presence 加权 ---
    # 当 FaceTracker.latest().present == True 时，把 micro_interval 缩短、
    # glance_interval 缩短，整体把行为分布推向"更频繁 glance（看人）"。
    # face 离开后自动恢复默认。
    face_micro_interval_scale: float = 0.7   # micro 间隔 * 0.7 → 略密
    face_glance_interval_scale: float = 0.35  # glance 间隔 * 0.35 → 显著加密
    # face 存在时 glance 的最大幅度（度）；走 actions.look_left/right 安全边界
    face_glance_amp_deg: float = 25.0

    # --- interact-006: emotion bias ---
    # IdleAnimator.set_current_emotion(label) 注入后，按 label 缩放
    # micro_amp（head 微动幅度）与 glance_prob（每轮 glance 触发概率）。
    # spec：happy=1.3x / sad=0.7x / 其它=1.0x。键为 emotion 字符串值
    # （'happy' / 'sad' / 'angry' / 'surprised' / 'neutral'）。
    # 缺失键 → 1.0（fallback）。COCO_EMOTION 未启用时 IdleAnimator 不接
    # set_current_emotion，整段 bias 路径完全不走，行为等价 phase-3。
    emotion_bias: dict = field(default_factory=lambda: {
        "happy": 1.3,
        "sad": 0.7,
        "angry": 1.0,
        "surprised": 1.0,
        "neutral": 1.0,
    })

    # 安全检查
    def validate(self) -> None:
        if not (0.5 <= self.micro_interval_min <= self.micro_interval_max <= 30.0):
            raise ValueError("micro_interval_{min,max} 不合法")
        if not (5.0 <= self.glance_interval_min <= self.glance_interval_max <= 120.0):
            raise ValueError("glance_interval_{min,max} 不合法")
        if not (0.0 < self.micro_yaw_amp_deg <= MAX_YAW_DEG / 4):
            raise ValueError(f"micro_yaw_amp_deg 应 ∈ (0, {MAX_YAW_DEG/4}]")
        if not (0.0 < self.micro_pitch_amp_deg <= MAX_PITCH_DEG / 4):
            raise ValueError(f"micro_pitch_amp_deg 应 ∈ (0, {MAX_PITCH_DEG/4}]")
        if not (0.0 < self.glance_amp_deg <= MAX_YAW_DEG):
            raise ValueError("glance_amp_deg 越界")
        if not (0.0 < self.face_glance_amp_deg <= MAX_YAW_DEG):
            raise ValueError("face_glance_amp_deg 越界")
        if not (0.05 <= self.face_micro_interval_scale <= 1.0):
            raise ValueError("face_micro_interval_scale 应 ∈ [0.05, 1.0]")
        if not (0.05 <= self.face_glance_interval_scale <= 1.0):
            raise ValueError("face_glance_interval_scale 应 ∈ [0.05, 1.0]")
        s = self.micro_p_head + self.micro_p_antenna + self.micro_p_breathe
        if abs(s - 1.0) > 1e-3:
            raise ValueError(f"micro_p_* 概率和 = {s}, 应 = 1.0")


@dataclass
class IdleStats:
    """运行时统计，便于 verify 与 evidence。"""

    micro_count: int = 0
    glance_count: int = 0
    error_count: int = 0
    skipped_paused: int = 0
    micro_kinds: dict[str, int] = field(default_factory=lambda: {"head": 0, "antenna": 0, "breathe": 0})
    # companion-002
    vision_glance_count: int = 0       # face present 期间触发的 glance 数
    vision_biased_glance_count: int = 0  # 实际朝 face 方向 glance 的数
    face_present_ticks: int = 0        # 主循环中观察到 present=True 的次数
    started_at: float = 0.0
    stopped_at: float = 0.0


class IdleAnimator:
    """陪伴动作循环。

    用法：
        animator = IdleAnimator(robot, stop_event)
        animator.start()         # 起后台线程
        ...                       # 主循环干别的事
        stop_event.set()
        animator.join(timeout=2)

    线程模型：
    - run() 是后台 daemon 线程，循环以 stop_event.wait(timeout=micro_interval) 计时
    - 每次 wait 返回后：若 stop_event 被 set 立刻 break；否则随机选 micro 或（到点）glance
    - SDK 调用异常被吞掉只 log + stats.error_count++，避免线程崩溃
    """

    def __init__(
        self,
        robot: "ReachyMini",
        stop_event: threading.Event,
        config: Optional[IdleConfig] = None,
        rng: Optional[random.Random] = None,
        face_tracker: Optional["FaceTracker"] = None,
        power_state: Optional["PowerStateMachine"] = None,
    ) -> None:
        self.robot = robot
        self.stop_event = stop_event
        self.config = config or IdleConfig()
        self.config.validate()
        self.rng = rng or random.Random()
        self.stats = IdleStats()
        self.face_tracker = face_tracker
        self.power_state = power_state
        self._thread: Optional[threading.Thread] = None
        self._next_glance_at: float = 0.0
        # idle/interact 互斥：interact 占用机器人时 set，IdleAnimator 在每次
        # 动作前检查并跳过本轮 micro/glance；不互锁 SDK 命令本身（避免 deadlock）
        self._paused = threading.Event()
        # interact-006: 当前情绪（默认 None；set_current_emotion 注入后生效）。
        # None 等价 phase-3 行为（一切 bias 路径不走）。
        self._current_emotion: Optional[str] = None

    # --- interact-006: emotion bias 钩子 ---
    def set_current_emotion(self, emotion: Any) -> None:
        """注入当前情绪。emotion 可以是 ``coco.emotion.Emotion`` 枚举、
        其 ``.value``（'happy' 等字符串），或 None（清除）。

        线程安全：dataclass 字段读写在 CPython 上是原子的；不需要 lock。
        """
        if emotion is None:
            self._current_emotion = None
            return
        # 兼容枚举与字符串
        v = getattr(emotion, "value", emotion)
        if not isinstance(v, str):
            log.warning("set_current_emotion: 非法类型 %r → 忽略", type(emotion).__name__)
            return
        self._current_emotion = v.lower()

    def get_current_emotion(self) -> Optional[str]:
        return self._current_emotion

    def _emotion_scale(self) -> float:
        """返回当前 emotion 对应的缩放系数。无注入或未知 emotion → 1.0。"""
        if self._current_emotion is None:
            return 1.0
        return float(self.config.emotion_bias.get(self._current_emotion, 1.0))

    # --- idle/interact 互斥 ---
    def pause(self) -> None:
        """让 idle 暂停发新动作（不打断已 in-flight 的单次 SDK 调用）。"""
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            log.warning("IdleAnimator already running")
            return
        self.stats = IdleStats(started_at=time.time())
        self._next_glance_at = time.time() + self._sample_glance_interval()
        self._thread = threading.Thread(
            target=self._run,
            name="coco-idle",
            daemon=True,
        )
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- internals ---
    def _face_present(self) -> bool:
        if self.face_tracker is None:
            return False
        try:
            return bool(self.face_tracker.latest().present)
        except Exception:  # noqa: BLE001
            return False

    def _power_micro_scale(self) -> float:
        """companion-003: drowsy 时 micro/glance 间隔放大；sleep 时返回特大值（外层会跳过本轮）。"""
        if self.power_state is None:
            return 1.0
        try:
            from coco.power_state import PowerState as _PS  # 局部 import 避免循环
            st = self.power_state.current_state
            if st == _PS.DROWSY:
                return float(self.power_state.config.drowsy_micro_scale)
            if st == _PS.SLEEP:
                # sleep 状态下不应再 sample 任何动作；用一个保险的"被 skip"指示
                return float("inf")
        except Exception:  # noqa: BLE001
            return 1.0
        return 1.0

    def _sample_micro_interval(self) -> float:
        base = self.rng.uniform(self.config.micro_interval_min, self.config.micro_interval_max)
        if self._face_present():
            base *= self.config.face_micro_interval_scale
        scale = self._power_micro_scale()
        if scale == float("inf"):
            # SLEEP：用一个比 wait 上限大的 sentinel；_run 里会 detect 并改用短 wait + skip
            return base
        return base * scale

    def _sample_glance_interval(self) -> float:
        base = self.rng.uniform(self.config.glance_interval_min, self.config.glance_interval_max)
        if self._face_present():
            base *= self.config.face_glance_interval_scale
        scale = self._power_micro_scale()
        if scale == float("inf"):
            return base
        return base * scale

    def _is_power_sleep(self) -> bool:
        if self.power_state is None:
            return False
        try:
            from coco.power_state import PowerState as _PS
            return self.power_state.current_state == _PS.SLEEP
        except Exception:  # noqa: BLE001
            return False

    def _run(self) -> None:
        log.info("IdleAnimator started cfg=%s vision=%s power=%s",
                 self.config, self.face_tracker is not None, self.power_state is not None)
        try:
            while not self.stop_event.is_set():
                wait = self._sample_micro_interval()
                # wait() 返回 True 表示被 set —— 立刻退出，不再起任何动作
                if self.stop_event.wait(timeout=wait):
                    break
                # 互斥：interact 占用时跳过本轮（间隔继续走）
                if self._paused.is_set():
                    self.stats.skipped_paused += 1
                    continue
                # companion-003: power_state == SLEEP 时跳过任何动作
                if self._is_power_sleep():
                    self.stats.skipped_paused += 1
                    continue
                if self._face_present():
                    self.stats.face_present_ticks += 1

                now = time.time()
                if now >= self._next_glance_at:
                    self._do_glance()
                    self._next_glance_at = now + self._sample_glance_interval()
                else:
                    self._do_micro()
        finally:
            self.stats.stopped_at = time.time()
            log.info("IdleAnimator stopped stats=%s", self.stats)

    def _safe(self, label: str, fn) -> None:
        if self.stop_event.is_set():
            return
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            self.stats.error_count += 1
            log.warning("idle %s failed: %s: %s", label, type(e).__name__, e)

    def _do_micro(self) -> None:
        cfg = self.config
        r = self.rng.random()
        if r < cfg.micro_p_head:
            self._micro_head()
            self.stats.micro_kinds["head"] += 1
        elif r < cfg.micro_p_head + cfg.micro_p_antenna:
            self._micro_antenna()
            self.stats.micro_kinds["antenna"] += 1
        else:
            self._breathe()
            self.stats.micro_kinds["breathe"] += 1
        self.stats.micro_count += 1

    def _micro_head(self) -> None:
        cfg = self.config
        scale = self._emotion_scale()
        yaw = self.rng.uniform(-cfg.micro_yaw_amp_deg * scale, cfg.micro_yaw_amp_deg * scale)
        pitch = self.rng.uniform(-cfg.micro_pitch_amp_deg * scale, cfg.micro_pitch_amp_deg * scale)
        target = euler_pose(pitch_deg=pitch, yaw_deg=yaw)
        self._safe("micro_head", lambda: self.robot.goto_target(head=target, duration=cfg.micro_duration))
        # 不立刻回中位；下一次 micro 会自然带回附近

    def _micro_antenna(self) -> None:
        cfg = self.config
        amp = cfg.micro_antenna_amp_rad
        left = self.rng.uniform(-amp, amp)
        right = self.rng.uniform(-amp, amp)
        # SDK: set_target_antenna_joint_positions([right, left])
        self._safe(
            "micro_antenna",
            lambda: self.robot.set_target_antenna_joint_positions([right, left]),
        )

    def _breathe(self) -> None:
        """呼吸感：回中位的 head + antenna 归零。"""
        cfg = self.config
        self._safe("breathe_head", lambda: self.robot.goto_target(head=INIT_HEAD_POSE, duration=cfg.micro_duration))
        self._safe("breathe_antenna", lambda: self.robot.set_target_antenna_joint_positions([0.0, 0.0]))

    def _do_glance(self) -> None:
        cfg = self.config
        # companion-002: face present 时按 face 方向 bias
        snap = self.face_tracker.latest() if self.face_tracker is not None else None
        if snap is not None and snap.present:
            self.stats.vision_glance_count += 1
            x_ratio = snap.x_ratio()
            if x_ratio is not None:
                # ratio: 负 = face 在画面左侧 → 头向左转（look_left, yaw 正）
                #        正 = face 在画面右侧 → 头向右转（look_right）
                amp = abs(x_ratio) * cfg.face_glance_amp_deg
                # 安全下限：太小的 amp 也走 amp=actions 默认下限
                amp = max(2.0, min(amp, MAX_YAW_DEG))
                # x_ratio is not None 已保证 primary 存在（见 FaceSnapshot.x_ratio）
                face_x_log = snap.primary.cx
                if x_ratio < 0:
                    log.info("idle glance toward face_x=%d ratio=%.2f amp=%.1f° dir=left",
                             face_x_log, x_ratio, amp)
                    self._safe(
                        "glance_face_left",
                        lambda: look_left(self.robot, amplitude_deg=amp,
                                          duration=cfg.glance_duration, return_to_center=True),
                    )
                else:
                    log.info("idle glance toward face_x=%d ratio=%.2f amp=%.1f° dir=right",
                             face_x_log, x_ratio, amp)
                    self._safe(
                        "glance_face_right",
                        lambda: look_right(self.robot, amplitude_deg=amp,
                                           duration=cfg.glance_duration, return_to_center=True),
                    )
                self.stats.vision_biased_glance_count += 1
                self.stats.glance_count += 1
                return
            # 有 present 但 x_ratio 拿不到 → 退化到默认 glance
        amp = cfg.glance_amp_deg
        if self.rng.random() < 0.5:
            self._safe("glance_left", lambda: look_left(self.robot, amplitude_deg=amp, duration=cfg.glance_duration, return_to_center=True))
        else:
            self._safe("glance_right", lambda: look_right(self.robot, amplitude_deg=amp, duration=cfg.glance_duration, return_to_center=True))
        self.stats.glance_count += 1


__all__ = ["IdleConfig", "IdleStats", "IdleAnimator"]
