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
from typing import TYPE_CHECKING, Optional

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
        s = self.micro_p_head + self.micro_p_antenna + self.micro_p_breathe
        if abs(s - 1.0) > 1e-3:
            raise ValueError(f"micro_p_* 概率和 = {s}, 应 = 1.0")


@dataclass
class IdleStats:
    """运行时统计，便于 verify 与 evidence。"""

    micro_count: int = 0
    glance_count: int = 0
    error_count: int = 0
    micro_kinds: dict[str, int] = field(default_factory=lambda: {"head": 0, "antenna": 0, "breathe": 0})
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
    ) -> None:
        self.robot = robot
        self.stop_event = stop_event
        self.config = config or IdleConfig()
        self.config.validate()
        self.rng = rng or random.Random()
        self.stats = IdleStats()
        self._thread: Optional[threading.Thread] = None
        self._next_glance_at: float = 0.0

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
    def _sample_micro_interval(self) -> float:
        return self.rng.uniform(self.config.micro_interval_min, self.config.micro_interval_max)

    def _sample_glance_interval(self) -> float:
        return self.rng.uniform(self.config.glance_interval_min, self.config.glance_interval_max)

    def _run(self) -> None:
        log.info("IdleAnimator started cfg=%s", self.config)
        try:
            while not self.stop_event.is_set():
                wait = self._sample_micro_interval()
                # wait() 返回 True 表示被 set —— 立刻退出，不再起任何动作
                if self.stop_event.wait(timeout=wait):
                    break

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
        yaw = self.rng.uniform(-cfg.micro_yaw_amp_deg, cfg.micro_yaw_amp_deg)
        pitch = self.rng.uniform(-cfg.micro_pitch_amp_deg, cfg.micro_pitch_amp_deg)
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
        amp = cfg.glance_amp_deg
        if self.rng.random() < 0.5:
            self._safe("glance_left", lambda: look_left(self.robot, amplitude_deg=amp, duration=cfg.glance_duration, return_to_center=True))
        else:
            self._safe("glance_right", lambda: look_right(self.robot, amplitude_deg=amp, duration=cfg.glance_duration, return_to_center=True))
        self.stats.glance_count += 1


__all__ = ["IdleConfig", "IdleStats", "IdleAnimator"]
