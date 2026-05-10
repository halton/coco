"""robot-001 V3b: 真实 SDK API 名 - wake_up / set_target_head_pose / get_current_*."""
import sys, time, traceback
from reachy_mini import ReachyMini

t0 = time.time()
robot = ReachyMini(spawn_daemon=False, media_backend="no_media")
print(f"connect OK dt={time.time()-t0:.2f}s")

# 读
try:
    pos = robot.get_current_joint_positions()
    print(f"get_current_joint_positions -> {pos!r}")
except Exception as e:
    print(f"get_current_joint_positions FAIL: {type(e).__name__}: {e}")

try:
    pose = robot.get_current_head_pose()
    print(f"get_current_head_pose -> shape={getattr(pose,'shape',None)} val={pose!r}")
except Exception as e:
    print(f"get_current_head_pose FAIL: {type(e).__name__}: {e}")

try:
    ant = robot.get_present_antenna_joint_positions()
    print(f"antenna_pos -> {ant!r}")
except Exception as e:
    print(f"antenna_pos FAIL: {type(e).__name__}: {e}")

# 动 — wake_up 是最 high-level、最安全的入口
moved = False
try:
    print("call wake_up()...")
    t1 = time.time()
    robot.wake_up()
    moved = True
    print(f"  wake_up OK dt={time.time()-t1:.2f}s")
except Exception as e:
    print(f"  wake_up FAIL: {type(e).__name__}: {e}")
    traceback.print_exc()

# 再读一次确认动了
try:
    pos2 = robot.get_current_joint_positions()
    print(f"after wake_up joint_pos -> {pos2!r}")
except Exception as e:
    print(f"post-read FAIL: {e}")

# antenna 小动作 — 验 actuator 单关节通路
try:
    print("set_target_antenna_joint_positions([0.3, -0.3])...")
    robot.set_target_antenna_joint_positions([0.3, -0.3])
    time.sleep(0.5)
    ant2 = robot.get_present_antenna_joint_positions()
    print(f"  antenna after move -> {ant2!r}")
    robot.set_target_antenna_joint_positions([0.0, 0.0])
    moved = True
except Exception as e:
    print(f"  antenna move FAIL: {type(e).__name__}: {e}")

# 收尾 sleep
try:
    robot.goto_sleep()
    print("goto_sleep OK")
except Exception as e:
    print(f"goto_sleep FAIL: {e}")

print(f"DONE moved={moved} total={time.time()-t0:.2f}s")
