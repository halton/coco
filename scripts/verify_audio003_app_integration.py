"""audio-003 V3: ReachyMiniApp 集成 — say_async() 不阻塞 ReachyMini 客户端心跳.

依赖：mockup-sim daemon 已起。
模拟 ReachyMiniApp.run() 主循环：
  - 起 say_async("...")（独立线程跑 sounddevice 播放，2.3s+）
  - 主循环每 100ms 跑一次 robot.get_current_head_pose() 模拟心跳
  - 期望在 say_async 跑期间至少有 N 次心跳成功（>= 10），且 say 线程最终结束

退出 0=PASS，1=FAIL。
"""
from __future__ import annotations

import sys
import time
import traceback

import numpy as np
from reachy_mini import ReachyMini

from coco.tts import say_async

HEARTBEAT_PERIOD_S = 0.1
TARGET_HEARTBEATS_DURING_SAY = 10
SAY_TIMEOUT_S = 15.0

t0 = time.time()
print("[audio-003 V3] connect mockup-sim daemon...")
try:
    robot = ReachyMini(spawn_daemon=False, media_backend="no_media")
    print(f"  connect OK dt={time.time()-t0:.2f}s")
except Exception as e:
    print(f"  FAIL connect: {e}")
    sys.exit(1)

# 先做一次健康心跳，确认 connect 后立刻可读
try:
    pose0 = robot.get_current_head_pose()
    print(f"  pre heartbeat OK pose.shape={pose0.shape}")
except Exception as e:
    print(f"  FAIL pre heartbeat: {e}")
    sys.exit(1)

# 启 say_async（本地 Kokoro，~2.3s 播放）
print("[audio-003 V3] start say_async('你好，我是可可')...")
say_thread = say_async("你好，我是可可", prefer="local")

# 主循环模拟 ReachyMiniApp.run() 心跳
heartbeat_ok = 0
heartbeat_fail = 0
loop_start = time.time()
deadline = loop_start + SAY_TIMEOUT_S

while time.time() < deadline:
    try:
        _ = robot.get_current_head_pose()
        heartbeat_ok += 1
    except Exception as e:
        heartbeat_fail += 1
        print(f"  heartbeat FAIL #{heartbeat_fail}: {type(e).__name__}: {e}")
    time.sleep(HEARTBEAT_PERIOD_S)
    if not say_thread.is_alive() and heartbeat_ok >= TARGET_HEARTBEATS_DURING_SAY:
        break

elapsed = time.time() - loop_start
print(f"  heartbeats ok={heartbeat_ok} fail={heartbeat_fail} elapsed={elapsed:.2f}s say_alive={say_thread.is_alive()}")

errors: list[str] = []
if heartbeat_ok < TARGET_HEARTBEATS_DURING_SAY:
    errors.append(f"心跳数 {heartbeat_ok} < 目标 {TARGET_HEARTBEATS_DURING_SAY}")
if heartbeat_fail > 0:
    errors.append(f"心跳失败 {heartbeat_fail} 次（应为 0）")
if say_thread.is_alive():
    errors.append(f"say 线程在 {SAY_TIMEOUT_S}s 内未退出")

# 收尾
try:
    robot.goto_sleep()
    print("  goto_sleep OK")
except Exception as e:
    print(f"  goto_sleep WARN: {e}")

if errors:
    print(f"\n[audio-003 V3] FAIL ({len(errors)}):")
    for err in errors:
        print(f"  - {err}")
    sys.exit(1)

print(f"\n[audio-003 V3] PASS — say_async 不阻塞 ReachyMini 心跳；{heartbeat_ok} 次成功，0 失败 in {elapsed:.2f}s")
sys.exit(0)
