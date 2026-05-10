"""companion-002 verification: vision-触发的 idle 微动.

不连真 mockup-sim daemon（companion-001 V1 已覆盖姿态采样路径），本脚本聚焦
vision hook 与 IdleAnimator 的集成：

  V1: image:single_face.jpg —— FaceTracker 持续 detect 命中，IdleAnimator 至少触发
       1 次 vision-biased glance；stats.face_present_ticks > 0
  V2: image:no_one.jpg     —— FaceTracker 几乎全 miss，IdleAnimator 退化到默认随机
       glance（vision_biased_glance_count == 0；常规 glance_count 可以为 0+）
  V3: video:user_walks_away.mp4 —— 从有脸到没脸过渡稳定无 race（无 error，stop 干净）

robot 用 FakeRobot stub，记录每次 SDK 调用，让我们能精确断言 glance 朝向 log。
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.idle import IdleAnimator, IdleConfig
from coco.perception.face_tracker import FaceTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

errors: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok   {msg}")
    else:
        errors.append(msg)
        print(f"  FAIL {msg}")


class FakeRobot:
    """模拟 ReachyMini 子集：goto_target / set_target_antenna_joint_positions。

    每次 goto_target 阻塞 duration 秒（模拟真实节奏），保证主循环节奏不被失真。
    线程安全计数。
    """

    def __init__(self) -> None:
        self.goto_count = 0
        self.antenna_count = 0
        self.last_head_pose: Optional[np.ndarray] = None
        self._lock = threading.Lock()

    def goto_target(self, head=None, duration: float = 0.5) -> None:
        # 不真睡那么久，避免 verification 跑很慢；保持有节奏感
        time.sleep(min(0.05, duration))
        with self._lock:
            self.goto_count += 1
            self.last_head_pose = head

    def set_target_antenna_joint_positions(self, vals) -> None:
        time.sleep(0.01)
        with self._lock:
            self.antenna_count += 1


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "vision"
SINGLE_FACE = FIXTURE_DIR / "single_face.jpg"
NO_ONE = FIXTURE_DIR / "no_one.jpg"
WALK_AWAY = FIXTURE_DIR / "user_walks_away.mp4"

for p in (SINGLE_FACE, NO_ONE, WALK_AWAY):
    if not p.exists():
        sys.exit(f"FAIL: fixture missing {p}")


def run_scenario(
    label: str,
    camera_spec: str,
    duration_s: float,
    cfg_overrides: Optional[dict] = None,
) -> dict:
    print(f"\n[{label}] camera={camera_spec} duration={duration_s}s")
    stop_event = threading.Event()
    tracker = FaceTracker(
        stop_event,
        camera_spec=camera_spec,
        fps=5.0,
        presence_window=5,
        presence_min_hits=2,
        absence_min_misses=3,
    )
    tracker.start()

    cfg_kwargs = dict(
        # 把间隔压短让 verification 在合理时长内能观察到行为
        micro_interval_min=0.5,
        micro_interval_max=0.8,
        glance_interval_min=5.0,
        glance_interval_max=6.0,
        face_micro_interval_scale=0.7,
        face_glance_interval_scale=0.4,
    )
    if cfg_overrides:
        cfg_kwargs.update(cfg_overrides)
    cfg = IdleConfig(**cfg_kwargs)

    robot = FakeRobot()
    animator = IdleAnimator(robot, stop_event, config=cfg, face_tracker=tracker)
    animator.start()

    t0 = time.time()
    while time.time() - t0 < duration_s:
        time.sleep(0.1)

    elapsed = time.time() - t0
    stop_t0 = time.time()
    stop_event.set()
    animator.join(timeout=2.0)
    tracker.join(timeout=2.0)
    stop_dt = time.time() - stop_t0

    snap = tracker.latest()
    out = {
        "label": label,
        "elapsed_s": float(elapsed),
        "stop_dt_s": float(stop_dt),
        "animator_alive_after": animator.is_alive(),
        "tracker_alive_after": tracker.is_alive(),
        "tracker": {
            "detect_count": tracker.stats.detect_count,
            "hit_count": tracker.stats.hit_count,
            "frames_dropped": tracker.stats.frames_dropped,
            "error_count": tracker.stats.error_count,
            "last_present": bool(snap.present),
            "last_frame_w": snap.frame_w,
            "last_frame_h": snap.frame_h,
        },
        "idle": {
            "micro_count": animator.stats.micro_count,
            "glance_count": animator.stats.glance_count,
            "vision_glance_count": animator.stats.vision_glance_count,
            "vision_biased_glance_count": animator.stats.vision_biased_glance_count,
            "face_present_ticks": animator.stats.face_present_ticks,
            "error_count": animator.stats.error_count,
        },
        "robot": {
            "goto_count": robot.goto_count,
            "antenna_count": robot.antenna_count,
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


# -- V1: single face present --
print("\n========== V1: image:single_face.jpg ==========")
v1 = run_scenario("V1-single-face", f"image:{SINGLE_FACE}", duration_s=8.0)
check(v1["tracker"]["hit_count"] >= 5,
      f"V1 tracker.hit_count={v1['tracker']['hit_count']} ≥ 5")
check(v1["tracker"]["last_present"] is True,
      f"V1 last_present={v1['tracker']['last_present']} == True")
check(v1["idle"]["face_present_ticks"] >= 1,
      f"V1 face_present_ticks={v1['idle']['face_present_ticks']} ≥ 1")
check(v1["idle"]["vision_biased_glance_count"] >= 1,
      f"V1 vision_biased_glance_count={v1['idle']['vision_biased_glance_count']} ≥ 1")
check(v1["idle"]["error_count"] == 0,
      f"V1 idle.error_count={v1['idle']['error_count']} == 0")
check(v1["tracker"]["error_count"] == 0,
      f"V1 tracker.error_count={v1['tracker']['error_count']} == 0")
check(v1["stop_dt_s"] < 2.0 and not v1["animator_alive_after"] and not v1["tracker_alive_after"],
      f"V1 stop 干净 dt={v1['stop_dt_s']:.2f}s alive=(idle={v1['animator_alive_after']}, "
      f"tracker={v1['tracker_alive_after']})")

# -- V2: no face --
print("\n========== V2: image:no_one.jpg ==========")
v2 = run_scenario("V2-no-one", f"image:{NO_ONE}", duration_s=8.0)
check(v2["tracker"]["hit_count"] == 0,
      f"V2 tracker.hit_count={v2['tracker']['hit_count']} == 0")
check(v2["tracker"]["last_present"] is False,
      f"V2 last_present={v2['tracker']['last_present']} == False")
check(v2["idle"]["face_present_ticks"] == 0,
      f"V2 face_present_ticks={v2['idle']['face_present_ticks']} == 0 (从未 present)")
check(v2["idle"]["vision_biased_glance_count"] == 0,
      f"V2 vision_biased_glance_count={v2['idle']['vision_biased_glance_count']} == 0")
check(v2["idle"]["error_count"] == 0,
      f"V2 idle.error_count={v2['idle']['error_count']} == 0")
check(v2["stop_dt_s"] < 2.0 and not v2["animator_alive_after"] and not v2["tracker_alive_after"],
      f"V2 stop 干净 dt={v2['stop_dt_s']:.2f}s")

# -- V3: video walk-away (有脸 → 没脸过渡) --
print("\n========== V3: video:user_walks_away.mp4 ==========")
v3 = run_scenario("V3-walk-away", f"video:{WALK_AWAY}", duration_s=12.0)
check(v3["tracker"]["detect_count"] >= 10,
      f"V3 tracker.detect_count={v3['tracker']['detect_count']} ≥ 10")
# 视频 fixture 是程序合成（详情见 fixture README），face 区段一定要被命中过一次
check(v3["tracker"]["hit_count"] >= 1,
      f"V3 tracker.hit_count={v3['tracker']['hit_count']} ≥ 1 (有脸帧应被命中)")
check(v3["idle"]["error_count"] == 0,
      f"V3 idle.error_count={v3['idle']['error_count']} == 0")
check(v3["tracker"]["error_count"] == 0,
      f"V3 tracker.error_count={v3['tracker']['error_count']} == 0")
check(v3["stop_dt_s"] < 2.0 and not v3["animator_alive_after"] and not v3["tracker_alive_after"],
      f"V3 stop 干净 dt={v3['stop_dt_s']:.2f}s")

# -- 落 evidence --
trace_path = Path("evidence/companion-002/verify_trace.json")
trace_path.parent.mkdir(parents=True, exist_ok=True)
with open(trace_path, "w") as f:
    json.dump({"V1": v1, "V2": v2, "V3": v3}, f, ensure_ascii=False, indent=2)
print(f"\n  trace -> {trace_path}")

if errors:
    print(f"\n[companion-002] FAIL ({len(errors)}):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print(f"\n[companion-002] PASS")
sys.exit(0)
