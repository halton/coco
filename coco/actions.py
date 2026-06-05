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


# ---------------------------------------------------------------------------
# interact-039 (branch feat/interact-013): LLM tool calling actions
# 6 new语义化动作 + goto_sleep / wake_up SDK 桥。
# 参数签名风格与 look_left/right/nod 一致（首参 robot，amplitude_deg 可选）。
# 失败 try/except 不抛崩，与 LLM 工具调用语义匹配（坏值 → 中位回退而不是 raise）。
# ---------------------------------------------------------------------------


# interact-039 SAFE_AMPLITUDE_DEFAULTS：tool calling 路径默认幅度（保守）
SHAKE_YAW_DEG: float = 20.0
TILT_ROLL_DEG: float = 12.0
LOOK_UPDOWN_PITCH_DEG: float = 15.0
SLEEP_PITCH_DEG: float = 25.0


def shake(
    robot: "ReachyMini",
    amplitude_deg: float = SHAKE_YAW_DEG,
    duration: float = 0.35,
    cycles: int = 2,
) -> None:
    """摇头表否定：yaw 左右往返 cycles 次后回中位。

    序列：+yaw → -yaw → +yaw → -yaw → 中位（cycles=2 时）。
    """
    _check_amplitude(amplitude_deg, MAX_YAW_DEG, "amplitude_deg(yaw)")
    _check_duration(duration)
    if amplitude_deg < 0:
        raise ValueError("shake amplitude_deg must be non-negative.")
    if not (1 <= cycles <= 3):
        raise ValueError(f"cycles={cycles} out of range [1, 3]")

    left = euler_pose(yaw_deg=+amplitude_deg)
    right = euler_pose(yaw_deg=-amplitude_deg)
    for _ in range(cycles):
        robot.goto_target(head=left, duration=duration)
        robot.goto_target(head=right, duration=duration)
    robot.goto_target(head=INIT_HEAD_POSE, duration=duration)


def tilt_left(
    robot: "ReachyMini",
    amplitude_deg: float = TILT_ROLL_DEG,
    duration: float = 0.5,
    return_to_center: bool = True,
) -> None:
    """头向左倾（roll = +amplitude_deg）。"""
    _check_amplitude(amplitude_deg, MAX_PITCH_DEG, "amplitude_deg(roll)")
    _check_duration(duration)
    if amplitude_deg < 0:
        raise ValueError("tilt_left amplitude_deg must be non-negative; use tilt_right instead.")

    target = euler_pose(roll_deg=+amplitude_deg)
    robot.goto_target(head=target, duration=duration)
    if return_to_center:
        robot.goto_target(head=INIT_HEAD_POSE, duration=duration)


def tilt_right(
    robot: "ReachyMini",
    amplitude_deg: float = TILT_ROLL_DEG,
    duration: float = 0.5,
    return_to_center: bool = True,
) -> None:
    """头向右倾（roll = -amplitude_deg）。"""
    _check_amplitude(amplitude_deg, MAX_PITCH_DEG, "amplitude_deg(roll)")
    _check_duration(duration)
    if amplitude_deg < 0:
        raise ValueError("tilt_right amplitude_deg must be non-negative; use tilt_left instead.")

    target = euler_pose(roll_deg=-amplitude_deg)
    robot.goto_target(head=target, duration=duration)
    if return_to_center:
        robot.goto_target(head=INIT_HEAD_POSE, duration=duration)


def look_up(
    robot: "ReachyMini",
    amplitude_deg: float = LOOK_UPDOWN_PITCH_DEG,
    duration: float = 0.5,
    return_to_center: bool = True,
) -> None:
    """抬头：pitch = -amplitude_deg（xyz 欧拉约定：负 pitch = 抬头）。"""
    _check_amplitude(amplitude_deg, MAX_PITCH_DEG, "amplitude_deg(pitch)")
    _check_duration(duration)
    if amplitude_deg < 0:
        raise ValueError("look_up amplitude_deg must be non-negative.")

    target = euler_pose(pitch_deg=-amplitude_deg)
    robot.goto_target(head=target, duration=duration)
    if return_to_center:
        robot.goto_target(head=INIT_HEAD_POSE, duration=duration)


def look_down(
    robot: "ReachyMini",
    amplitude_deg: float = LOOK_UPDOWN_PITCH_DEG,
    duration: float = 0.5,
    return_to_center: bool = True,
) -> None:
    """低头：pitch = +amplitude_deg。"""
    _check_amplitude(amplitude_deg, MAX_PITCH_DEG, "amplitude_deg(pitch)")
    _check_duration(duration)
    if amplitude_deg < 0:
        raise ValueError("look_down amplitude_deg must be non-negative.")

    target = euler_pose(pitch_deg=+amplitude_deg)
    robot.goto_target(head=target, duration=duration)
    if return_to_center:
        robot.goto_target(head=INIT_HEAD_POSE, duration=duration)


def goto_sleep(robot: "ReachyMini", duration: float = 0.8) -> None:
    """睡眠姿态：优先调 SDK r.goto_sleep()，无则手动低头 + 短暂保持。

    LLM 路径下的"低头睡觉" / "休息" 触发；不 raise 异常（fail-soft）。
    """
    _check_duration(duration)
    # 优先 SDK 原生 emote
    sdk_method = getattr(robot, "goto_sleep", None)
    if callable(sdk_method):
        try:
            sdk_method()
            return
        except Exception:  # noqa: BLE001
            # SDK 路径失败 → 落到手动姿态（不抛）
            pass
    # 兜底：手动深度低头作为睡眠姿态
    try:
        target = euler_pose(pitch_deg=+SLEEP_PITCH_DEG)
        robot.goto_target(head=target, duration=duration)
        time.sleep(0.5)  # 让姿态可见
    except Exception:  # noqa: BLE001
        # 终极兜底：什么都不做，不影响主流程
        pass


def wake_up(robot: "ReachyMini", duration: float = 0.6) -> None:
    """醒来回中位：优先调 SDK r.wake_up()，无则 set_target(INIT_HEAD_POSE)。"""
    _check_duration(duration)
    sdk_method = getattr(robot, "wake_up", None)
    if callable(sdk_method):
        try:
            sdk_method()
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        robot.goto_target(head=INIT_HEAD_POSE, duration=duration)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# interact-042: antenna + body_yaw actions
# 直接走 SDK set_target(antennas=[L,R]) / set_target(body_yaw=X) joint-space API。
# 这两个自由度无法用 head 4x4 pose 表示，必须用 set_target 而非 goto_target(head=...)。
#
# 安全 clamp（保守，远低于 SDK 极限）：
#   antenna: ±1.5 rad   （SDK 极限 ±3.05 rad）
#   body_yaw: ±π/2 rad  （SDK 无显式限位，本层硬 clamp 防过转/电缆缠绕）
#
# 重要：Reachy Mini 没有轮子。body_yaw 是上半身绕垂直轴旋转，机不位移。
# 真机首次 body_yaw 必须人在场观察电缆缠绕风险。详见 evidence/.../safety-notes.md。
#
# 所有 method 失败 fail-soft（与 goto_sleep/wake_up 一致），与 LLM tool calling 语义匹配。
# ---------------------------------------------------------------------------

# interact-042 安全上限
ANTENNA_MAX_RAD: float = 1.5  # SDK ±3.05, 本层保守 ±1.5
BODY_YAW_MAX_RAD: float = math.pi / 2  # ±π/2 ≈ ±1.5708
ANTENNA_MIN_DURATION_S: float = 0.4  # 防抖：单段动作不少于 0.4s


def _clamp_antenna(v: float) -> float:
    """clamp 单根天线角度到 ±ANTENNA_MAX_RAD。"""
    return max(-ANTENNA_MAX_RAD, min(ANTENNA_MAX_RAD, float(v)))


def _clamp_body_yaw(v: float) -> float:
    """clamp body_yaw 到 ±π/2。"""
    return max(-BODY_YAW_MAX_RAD, min(BODY_YAW_MAX_RAD, float(v)))


def _safe_enable_motors(robot: "ReachyMini") -> None:
    """fail-soft enable_motors + 短暂 sleep（coco 主进程已 enable 过则 no-op 安全）。"""
    try:
        em = getattr(robot, "enable_motors", None)
        if callable(em):
            em()
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.2)


def wiggle_antennas(
    robot: "ReachyMini",
    amplitude_rad: float = 1.0,
    duration: float = 0.4,
    cycles: int = 3,
) -> None:
    """两根天线左右摇摆 cycles 次表达兴奋情绪。

    序列：(+L, -R) → (-L, +R) → ... 反向交替 cycles 次, 末态回 0。
    用 set_target(antennas=[L, R]) 直接驱 antenna joint-space, 不影响 head pose。
    幅度与 duration 经 clamp，越界用 fail-soft 兜底。
    """
    if not math.isfinite(duration) or duration < ANTENNA_MIN_DURATION_S:
        duration = ANTENNA_MIN_DURATION_S
    if not (1 <= cycles <= 5):
        cycles = 3
    amp = _clamp_antenna(amplitude_rad)
    _safe_enable_motors(robot)
    try:
        st = getattr(robot, "set_target", None)
        if not callable(st):
            return
        for i in range(cycles):
            if i % 2 == 0:
                st(antennas=[+amp, -amp])
            else:
                st(antennas=[-amp, +amp])
            time.sleep(duration)
        st(antennas=[0.0, 0.0])
        time.sleep(duration)
    except Exception:  # noqa: BLE001
        pass


def perk_up(
    robot: "ReachyMini",
    amplitude_rad: float = 1.2,
    duration: float = 0.5,
) -> None:
    """天线竖起来表达警觉 / 好奇 (两根同向高举, 保持几秒)。

    set_target(antennas=[+amp, +amp])，clamp 到 ±ANTENNA_MAX_RAD。
    """
    if not math.isfinite(duration) or duration < ANTENNA_MIN_DURATION_S:
        duration = ANTENNA_MIN_DURATION_S
    amp = _clamp_antenna(amplitude_rad)
    _safe_enable_motors(robot)
    try:
        st = getattr(robot, "set_target", None)
        if not callable(st):
            return
        st(antennas=[+amp, +amp])
        time.sleep(duration)
    except Exception:  # noqa: BLE001
        pass


def droop_antennas(
    robot: "ReachyMini",
    amplitude_rad: float = 1.2,
    duration: float = 0.5,
) -> None:
    """天线下垂表达失落 / 不开心 (两根同向低垂)。

    set_target(antennas=[-amp, -amp])，clamp 到 ±ANTENNA_MAX_RAD。
    """
    if not math.isfinite(duration) or duration < ANTENNA_MIN_DURATION_S:
        duration = ANTENNA_MIN_DURATION_S
    amp = _clamp_antenna(amplitude_rad)
    _safe_enable_motors(robot)
    try:
        st = getattr(robot, "set_target", None)
        if not callable(st):
            return
        st(antennas=[-amp, -amp])
        time.sleep(duration)
    except Exception:  # noqa: BLE001
        pass


def turn_body_left(
    robot: "ReachyMini",
    amplitude_rad: float = 0.5,
    duration: float = 0.6,
) -> None:
    """整个上半身转向左侧 (body_yaw = +amp, ~28° 默认)。

    重要：Reachy Mini 无轮子, body_yaw 是上半身绕垂直轴旋转, 机不位移。
    用 set_target(body_yaw=...) 直接驱底座 yaw joint。clamp 到 ±π/2。
    """
    if not math.isfinite(duration) or duration < ANTENNA_MIN_DURATION_S:
        duration = ANTENNA_MIN_DURATION_S
    angle = _clamp_body_yaw(amplitude_rad)
    _safe_enable_motors(robot)
    try:
        st = getattr(robot, "set_target", None)
        if not callable(st):
            return
        st(body_yaw=+abs(angle))
        time.sleep(duration)
    except Exception:  # noqa: BLE001
        pass


def turn_body_right(
    robot: "ReachyMini",
    amplitude_rad: float = 0.5,
    duration: float = 0.6,
) -> None:
    """整个上半身转向右侧 (body_yaw = -amp)。"""
    if not math.isfinite(duration) or duration < ANTENNA_MIN_DURATION_S:
        duration = ANTENNA_MIN_DURATION_S
    angle = _clamp_body_yaw(amplitude_rad)
    _safe_enable_motors(robot)
    try:
        st = getattr(robot, "set_target", None)
        if not callable(st):
            return
        st(body_yaw=-abs(angle))
        time.sleep(duration)
    except Exception:  # noqa: BLE001
        pass


def turn_body_center(
    robot: "ReachyMini",
    duration: float = 0.6,
) -> None:
    """身体回正 (body_yaw = 0)。"""
    if not math.isfinite(duration) or duration < ANTENNA_MIN_DURATION_S:
        duration = ANTENNA_MIN_DURATION_S
    _safe_enable_motors(robot)
    try:
        st = getattr(robot, "set_target", None)
        if not callable(st):
            return
        st(body_yaw=0.0)
        time.sleep(duration)
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "INIT_HEAD_POSE",
    "MAX_YAW_DEG",
    "MAX_PITCH_DEG",
    "MIN_DURATION_S",
    "MAX_DURATION_S",
    "SHAKE_YAW_DEG",
    "TILT_ROLL_DEG",
    "LOOK_UPDOWN_PITCH_DEG",
    "SLEEP_PITCH_DEG",
    "ANTENNA_MAX_RAD",
    "BODY_YAW_MAX_RAD",
    "ANTENNA_MIN_DURATION_S",
    "euler_pose",
    "look_left",
    "look_right",
    "nod",
    "shake",
    "tilt_left",
    "tilt_right",
    "look_up",
    "look_down",
    "goto_sleep",
    "wake_up",
    "wiggle_antennas",
    "perk_up",
    "droop_antennas",
    "turn_body_left",
    "turn_body_right",
    "turn_body_center",
]
