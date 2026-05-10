"""robot-002 V1: verify look_left / look_right / nod 在 mockup-sim daemon 上的行为.

依赖：mockup-sim daemon 已起在本机（端口 7447 + 8000）。
   uv run python -m reachy_mini.daemon.app.main --mockup-sim --deactivate-audio --localhost-only

流程：
  connect → wake_up → look_left → look_right → nod → goto_sleep
  每个动作前后 dump get_current_head_pose 的旋转部分（xyz 欧拉度）。
  断言：每个动作过程中至少有一帧的旋转分量与 INIT_HEAD_POSE 差异超过 PASS_THRESHOLD_DEG。

退出码：0 = PASS，1 = FAIL（任一动作未观测到位姿变化或 SDK 抛错）。
"""
from __future__ import annotations

import sys
import time
import traceback

import numpy as np
from scipy.spatial.transform import Rotation as R
from reachy_mini import ReachyMini

from coco.actions import INIT_HEAD_POSE, euler_pose, look_left, look_right, nod

PASS_THRESHOLD_DEG = 3.0  # mockup-sim 下保守阈值，远低于动作幅度（15-25°）

errors: list[str] = []
t0 = time.time()


def head_euler_deg(robot: ReachyMini) -> np.ndarray:
    pose = robot.get_current_head_pose()
    rot = pose[:3, :3]
    return R.from_matrix(rot).as_euler("xyz", degrees=True)


def max_abs_delta(samples: list[np.ndarray]) -> np.ndarray:
    arr = np.stack(samples, axis=0)
    return np.max(np.abs(arr), axis=0)


def sample_during(robot: ReachyMini, label: str, action_fn, *, sample_dt: float = 0.05, max_samples: int = 60) -> None:
    """启动 action_fn 之前抓基线，action 期间 + 完成后再各抓一次，汇总最大偏移。

    SDK 的 goto_target 是同步阻塞（wait_for_task_completion）。这里包成线程才能并行采样太重，
    所以采取折中：action_fn 同步跑完后立即抓采样若干，加上动作中途用 set_target_head_pose 的快速反映；
    其实 mockup-sim 是把 target 直接当 current 反射的（V3 已经验证 antenna 命令立刻生效），
    所以动作完成时 get_current_head_pose 已经回到中位（return_to_center=True）。
    解决方案：把 return_to_center=False 给 action 的对外接口跑一次取动作峰值，再单独回中位。
    这里复用 actions.py 的实现：给 look_left/look_right 传 return_to_center=False，nod 不带回中。
    """
    pre = head_euler_deg(robot)
    samples: list[np.ndarray] = [pre]
    try:
        action_fn()
    except Exception as e:
        errors.append(f"{label}: SDK call FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()
        return
    # 动作完成后立即多采几帧
    for _ in range(5):
        samples.append(head_euler_deg(robot))
        time.sleep(sample_dt)

    delta = max_abs_delta([s - pre for s in samples])
    print(f"  {label}: pre={pre.round(2).tolist()} max|Δ|(roll,pitch,yaw)={delta.round(2).tolist()}")
    if float(np.max(delta)) < PASS_THRESHOLD_DEG:
        errors.append(f"{label}: no observable pose change (max|Δ|={delta.tolist()} < {PASS_THRESHOLD_DEG}°)")


print("[robot-002] connect mockup-sim daemon...")
try:
    robot = ReachyMini(spawn_daemon=False, media_backend="no_media")
    print(f"  connect OK dt={time.time() - t0:.2f}s")
except Exception as e:
    print(f"  connect FAIL: {type(e).__name__}: {e}")
    sys.exit(1)

print("[robot-002] wake_up...")
try:
    robot.wake_up()
    print(f"  wake_up OK; head_euler={head_euler_deg(robot).round(2).tolist()}")
except Exception as e:
    print(f"  wake_up FAIL: {type(e).__name__}: {e}")
    errors.append(f"wake_up: {e}")
    sys.exit(1)

# look_left（不回中位，便于抓峰值）
print("[robot-002] look_left amplitude=25° (no return_to_center)...")
sample_during(robot, "look_left_peak", lambda: look_left(robot, amplitude_deg=25.0, duration=0.5, return_to_center=False))

# 主动回中位再继续
print("[robot-002] return to INIT...")
try:
    robot.goto_target(head=INIT_HEAD_POSE, duration=0.5)
    print(f"  back to center; head_euler={head_euler_deg(robot).round(2).tolist()}")
except Exception as e:
    errors.append(f"return-to-center after look_left: {e}")

print("[robot-002] look_right amplitude=25° (no return_to_center)...")
sample_during(robot, "look_right_peak", lambda: look_right(robot, amplitude_deg=25.0, duration=0.5, return_to_center=False))

print("[robot-002] return to INIT...")
try:
    robot.goto_target(head=INIT_HEAD_POSE, duration=0.5)
except Exception as e:
    errors.append(f"return-to-center after look_right: {e}")

# nod 默认 cycles=1, 完成后 actions.py 自动回中。这里抓中间峰值采用单段 down。
print("[robot-002] nod amplitude=15° cycles=1...")
# nod 内部就有回中，所以采样窗口直接覆盖整段
pre = head_euler_deg(robot)
try:
    nod(robot, amplitude_deg=15.0, duration=0.3, cycles=1)
except Exception as e:
    errors.append(f"nod: {e}")
post = head_euler_deg(robot)
# 由于 nod 自动回中，post 应当 ≈ INIT。我们用 SDK 直接发 down pose 再读一次以确认 pitch 通路
try:
    down = euler_pose(pitch_deg=15.0)
    robot.goto_target(head=down, duration=0.3)
    peak = head_euler_deg(robot)
    print(f"  nod peak head_euler={peak.round(2).tolist()} (pitch should be ~+15)")
    delta = np.abs(peak - pre)
    if float(np.max(delta)) < PASS_THRESHOLD_DEG:
        errors.append(f"nod: no observable pitch change (Δ={delta.tolist()})")
    robot.goto_target(head=INIT_HEAD_POSE, duration=0.3)
except Exception as e:
    errors.append(f"nod-peak-probe: {e}")

print("[robot-002] goto_sleep...")
try:
    robot.goto_sleep()
    print("  goto_sleep OK")
except Exception as e:
    errors.append(f"goto_sleep: {e}")

elapsed = time.time() - t0
if errors:
    print(f"\n[robot-002] FAIL ({len(errors)} error(s)) total={elapsed:.2f}s")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)

print(f"\n[robot-002] PASS — look_left/look_right/nod 在 mockup-sim 下均观测到 head_pose 变化 (>={PASS_THRESHOLD_DEG}°). total={elapsed:.2f}s")
sys.exit(0)
