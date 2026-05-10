"""coco.actions — robot-002 头部姿态基础动作.

封装三个语义化高层动作：look_left / look_right / nod。
基于 reachy_mini.ReachyMini.goto_target(head=4x4, duration=...) + INIT_HEAD_POSE 构造姿态。

设计要点：
- 不直接操作 7 维 head_joint_positions，避免越过 SDK 抽象。
- 全部走 task-space (head 4x4 pose) + min-jerk 插值，平滑且对接 reachy-mini SDK 标准。
- 幅度参数 amplitude_deg 默认采取保守安全值（yaw 25°, pitch 15°），低于 wake_up 自身使用的 ±20° 量级附近，远低于 spike 阶段观测的极限。
- duration 默认 0.5s，nod 因为是来回所以分两段 0.4s。调用方可以覆写。
- 每个动作完成后回到 INIT_HEAD_POSE（中性位），便于动作链顺序无副作用。
- 所有 SDK 调用默认透出异常，由调用方决定恢复策略；这一层只做编排，不吞错。

坐标约定（reachy-mini Lite SDK，xyz 欧拉）：
- yaw  = 绕 z 轴，正向 = 头向左转（look_left）
- pitch = 绕 y 轴，正向 = 头向下点（nod down）
- roll = 绕 x 轴 （wake_up emote 使用）
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

import numpy as np
from scipy.spatial.transform import Rotation as R

if TYPE_CHECKING:  # pragma: no cover
    from reachy_mini import ReachyMini


INIT_HEAD_POSE: np.ndarray = np.eye(4)

# 安全上限：超出即 raise ValueError，避免误调用伤到舵机或视觉跟踪丢失参考。
# 真机 milestone 之前以 mockup-sim 默认范围为准；真机标定后再回调。
MAX_YAW_DEG: float = 45.0
MAX_PITCH_DEG: float = 30.0
MIN_DURATION_S: float = 0.1
MAX_DURATION_S: float = 5.0


def euler_pose(roll_deg: float = 0.0, pitch_deg: float = 0.0, yaw_deg: float = 0.0) -> np.ndarray:
    """从 xyz 欧拉角（度）构造 4x4 head pose 矩阵，平移分量保持 0。

    公开 helper：companion 层做组合动作（如"左看 + 微微低头"）时可直接用。
    """
    pose = np.eye(4)
    pose[:3, :3] = R.from_euler("xyz", [roll_deg, pitch_deg, yaw_deg], degrees=True).as_matrix()
    return pose


# 向后兼容下划线别名（不会破坏已写脚本）
_euler_pose = euler_pose


def _check_amplitude(value_deg: float, max_deg: float, name: str) -> None:
    if not math.isfinite(value_deg) or abs(value_deg) > max_deg:
        raise ValueError(f"{name}={value_deg} out of safe range ±{max_deg}°")


def _check_duration(duration: float) -> None:
    if not math.isfinite(duration) or duration < MIN_DURATION_S or duration > MAX_DURATION_S:
        raise ValueError(f"duration={duration}s out of safe range [{MIN_DURATION_S}, {MAX_DURATION_S}]")


def look_left(
    robot: "ReachyMini",
    amplitude_deg: float = 25.0,
    duration: float = 0.5,
    return_to_center: bool = True,
) -> None:
    """头向左转 yaw=+amplitude_deg，平滑 min-jerk 插值。

    Args:
        robot: 已连上 daemon 的 ReachyMini 客户端实例。
        amplitude_deg: 左转角度（度），正数；安全上限 MAX_YAW_DEG。
        duration: 单段动作时长（秒）。
        return_to_center: True 时动作结束后回中位。
    """
    _check_amplitude(amplitude_deg, MAX_YAW_DEG, "amplitude_deg(yaw)")
    _check_duration(duration)
    if amplitude_deg < 0:
        raise ValueError("look_left amplitude_deg must be non-negative; use look_right instead.")

    target = euler_pose(yaw_deg=+amplitude_deg)
    robot.goto_target(head=target, duration=duration)
    if return_to_center:
        robot.goto_target(head=INIT_HEAD_POSE, duration=duration)


def look_right(
    robot: "ReachyMini",
    amplitude_deg: float = 25.0,
    duration: float = 0.5,
    return_to_center: bool = True,
) -> None:
    """头向右转 yaw=-amplitude_deg，平滑 min-jerk 插值。"""
    _check_amplitude(amplitude_deg, MAX_YAW_DEG, "amplitude_deg(yaw)")
    _check_duration(duration)
    if amplitude_deg < 0:
        raise ValueError("look_right amplitude_deg must be non-negative; use look_left instead.")

    target = euler_pose(yaw_deg=-amplitude_deg)
    robot.goto_target(head=target, duration=duration)
    if return_to_center:
        robot.goto_target(head=INIT_HEAD_POSE, duration=duration)


def nod(
    robot: "ReachyMini",
    amplitude_deg: float = 15.0,
    duration: float = 0.4,
    cycles: int = 1,
) -> None:
    """点头：pitch 下→上→中位，重复 cycles 次。

    Args:
        amplitude_deg: 单向 pitch 幅度，正数。下点 = +pitch（xyz 欧拉约定下）。
        duration: 每段（下/上/回中）单段时长。一个 cycle 总时长 ≈ 3 * duration。
        cycles: 完整点头次数，1..3。
    """
    _check_amplitude(amplitude_deg, MAX_PITCH_DEG, "amplitude_deg(pitch)")
    _check_duration(duration)
    if amplitude_deg < 0:
        raise ValueError("nod amplitude_deg must be non-negative.")
    if not (1 <= cycles <= 3):
        raise ValueError(f"cycles={cycles} out of range [1, 3]")

    down = euler_pose(pitch_deg=+amplitude_deg)
    up = euler_pose(pitch_deg=-amplitude_deg * 0.4)  # 抬头幅度小一点，自然
    for _ in range(cycles):
        robot.goto_target(head=down, duration=duration)
        robot.goto_target(head=up, duration=duration)
    robot.goto_target(head=INIT_HEAD_POSE, duration=duration)


__all__ = [
    "INIT_HEAD_POSE",
    "MAX_YAW_DEG",
    "MAX_PITCH_DEG",
    "MIN_DURATION_S",
    "MAX_DURATION_S",
    "euler_pose",
    "look_left",
    "look_right",
    "nod",
]
