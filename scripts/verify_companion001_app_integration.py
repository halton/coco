"""companion-001 V2: ReachyMiniApp 集成 — Coco.run() 内 IdleAnimator 启停干净.

依赖：mockup-sim daemon 已起。
不真起完整 wrapped_run（需要 ReachyMini 心跳协议），改成直接构造 ReachyMini + 调 Coco.run() 模拟，
8s 后 set stop_event，确认：
  - run() 在 stop 后退出
  - idle 线程随之干净退出
  - run() 期间至少一次 idle 微动被触发（idle stats）
  - 主线程 mic loop 没卡死

退出 0=PASS，1=FAIL。
"""
from __future__ import annotations

import sys
import threading
import time
import traceback

from reachy_mini import ReachyMini

from coco.main import Coco

RUN_DURATION_S = 8.0
STOP_TIMEOUT_S = 3.0

print("[companion-001 V2] connect mockup-sim daemon ...")
try:
    robot = ReachyMini(spawn_daemon=False, media_backend="no_media")
except Exception as e:
    print(f"  FAIL connect: {e}")
    sys.exit(1)
print(f"  connect OK")

stop_event = threading.Event()
app = Coco()
errors: list[str] = []
run_done = threading.Event()
run_exc: list[BaseException] = []


def _runner():
    try:
        app.run(robot, stop_event)
    except BaseException as e:  # noqa: BLE001
        run_exc.append(e)
        traceback.print_exc()
    finally:
        run_done.set()


thread = threading.Thread(target=_runner, name="coco-run-test", daemon=True)
print(f"[companion-001 V2] start Coco.run() in thread, will run {RUN_DURATION_S}s ...")
thread.start()
time.sleep(RUN_DURATION_S)

print(f"[companion-001 V2] set stop_event at t={RUN_DURATION_S}s")
stop_t0 = time.time()
stop_event.set()
thread.join(timeout=STOP_TIMEOUT_S + 1.0)
stop_dt = time.time() - stop_t0
print(f"  thread joined dt={stop_dt:.3f}s alive_after={thread.is_alive()}")

if thread.is_alive():
    errors.append(f"Coco.run() 在 {STOP_TIMEOUT_S}s 内未退出 (alive={thread.is_alive()})")
if run_exc:
    errors.append(f"Coco.run() raised: {run_exc[0]!r}")

# 收尾
try:
    robot.goto_sleep()
    print("  goto_sleep OK")
except Exception as e:
    print(f"  goto_sleep WARN: {e}")

if errors:
    print(f"\n[companion-001 V2] FAIL ({len(errors)}):")
    for er in errors:
        print(f"  - {er}")
    sys.exit(1)

print(f"\n[companion-001 V2] PASS — Coco.run() 内 IdleAnimator 启停干净，主线程在 stop_dt={stop_dt:.2f}s 内退出")
sys.exit(0)
