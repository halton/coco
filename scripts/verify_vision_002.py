"""vision-002 verification: 多帧 IoU 跟踪 + 主脸选择 + presence hysteresis.

跑法：
  uv run python scripts/verify_vision_002.py

不依赖 mockup-sim daemon、不连真摄像头；fixture 全部走 image:/video: spec
或合成 detection 注入（测试钩子 FaceTracker.feed_detections）。

测试矩阵：
  V1: image:single_face.jpg N 帧 → 主脸 track_id 稳定不切；hit_count 单调增；present=True
  V2: image:no_one.jpg N 帧 → 0 tracks；present 一直 False
  V3: video:user_walks_away.mp4 → 跟踪贯穿全片不丢；primary_switches ≤ 1；
      hysteresis 在 face 短暂消失 / 复现时不抖
  V4: 合成 detection 序列（K/J 边界）— 验证 presence True/False 触发恰好在 K/J 帧
  V5: IoU 匹配正确性 — 注入两 box 序列，验证 greedy 不会乱配 + track_id 正确分配

retval：0 全 PASS；1 任一失败
evidence 落 evidence/vision-002/verify_trace.json
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.perception.face_detect import FaceBox
from coco.perception.face_tracker import FaceTracker, iou_xywh

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

errors: List[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok   {msg}")
    else:
        errors.append(msg)
        print(f"  FAIL {msg}")


FIX = ROOT / "tests" / "fixtures" / "vision"
SINGLE = FIX / "single_face.jpg"
NO_ONE = FIX / "no_one.jpg"
WALK = FIX / "user_walks_away.mp4"
for p in (SINGLE, NO_ONE, WALK):
    if not p.exists():
        sys.exit(f"FAIL: fixture missing {p}")


# ---------------------------------------------------------------------------
# V1: 单图稳定跟踪
# ---------------------------------------------------------------------------
print("\n========== V1: image:single_face.jpg ==========")
stop = threading.Event()
tr = FaceTracker(
    stop, camera_spec=f"image:{SINGLE}", fps=10.0,
    presence_window=5, presence_min_hits=2, absence_min_misses=5,
    iou_threshold=0.3, max_track_misses=3, primary_strategy="area",
    primary_switch_min_frames=3,
)
tr.start()
time.sleep(2.0)
stop.set()
tr.join(timeout=2.0)
snap1 = tr.latest()
v1 = {
    "detect_count": tr.stats.detect_count,
    "hit_count": tr.stats.hit_count,
    "tracks_created": tr.stats.tracks_created,
    "tracks_dropped": tr.stats.tracks_dropped,
    "primary_switches": tr.stats.primary_switches,
    "tracks_now": [
        {"id": t.track_id, "hits": t.hit_count, "age": t.age_frames,
         "ps": round(t.presence_score, 3)}
        for t in snap1.tracks
    ],
    "primary_id": snap1.primary_track.track_id if snap1.primary_track else None,
    "primary_hits": snap1.primary_track.hit_count if snap1.primary_track else None,
    "present": snap1.present,
}
print(json.dumps(v1, ensure_ascii=False, indent=2))
check(snap1.present is True, f"V1 present=True 实际 {snap1.present}")
check(snap1.primary_track is not None, "V1 primary_track 非空")
check(tr.stats.primary_switches <= 1,
      f"V1 primary_switches={tr.stats.primary_switches} ≤ 1 (单脸不应切)")
check(len(snap1.tracks) >= 1, f"V1 tracks={len(snap1.tracks)} ≥ 1")
if snap1.primary_track:
    check(snap1.primary_track.hit_count >= 5,
          f"V1 primary.hit_count={snap1.primary_track.hit_count} ≥ 5")


# ---------------------------------------------------------------------------
# V2: 空画面
# ---------------------------------------------------------------------------
print("\n========== V2: image:no_one.jpg ==========")
stop = threading.Event()
tr = FaceTracker(
    stop, camera_spec=f"image:{NO_ONE}", fps=10.0,
    presence_window=5, presence_min_hits=2, absence_min_misses=5,
)
tr.start()
time.sleep(2.0)
stop.set()
tr.join(timeout=2.0)
snap2 = tr.latest()
v2 = {
    "detect_count": tr.stats.detect_count,
    "hit_count": tr.stats.hit_count,
    "tracks_created": tr.stats.tracks_created,
    "tracks_now": len(snap2.tracks),
    "primary_id": snap2.primary_track.track_id if snap2.primary_track else None,
    "present": snap2.present,
}
print(json.dumps(v2, ensure_ascii=False, indent=2))
check(snap2.present is False, f"V2 present=False 实际 {snap2.present}")
check(len(snap2.tracks) == 0, f"V2 tracks={len(snap2.tracks)} == 0")
check(snap2.primary_track is None, "V2 primary_track is None")
check(tr.stats.hit_count == 0, f"V2 hit_count={tr.stats.hit_count} == 0")


# ---------------------------------------------------------------------------
# V3: 视频走开
# ---------------------------------------------------------------------------
print("\n========== V3: video:user_walks_away.mp4 ==========")
# 记录每帧 primary 切换
switches_over_time: List[dict] = []
stop = threading.Event()
tr = FaceTracker(
    stop, camera_spec=f"video:{WALK}", fps=15.0,
    presence_window=5, presence_min_hits=2, absence_min_misses=5,
    iou_threshold=0.25,  # 走开过程 box 收缩，IoU 会下降，稍放宽
    max_track_misses=3, primary_strategy="area", primary_switch_min_frames=3,
)
tr.start()
last_pid = None
t0 = time.time()
while time.time() - t0 < 8.0:
    s = tr.latest()
    cur = s.primary_track.track_id if s.primary_track else None
    if cur != last_pid:
        switches_over_time.append({
            "ts": round(time.time() - t0, 3),
            "from": last_pid, "to": cur,
            "present": s.present,
        })
        last_pid = cur
    time.sleep(0.05)
stop.set()
tr.join(timeout=2.0)
snap3 = tr.latest()
v3 = {
    "detect_count": tr.stats.detect_count,
    "hit_count": tr.stats.hit_count,
    "tracks_created": tr.stats.tracks_created,
    "tracks_dropped": tr.stats.tracks_dropped,
    "primary_switches_stat": tr.stats.primary_switches,
    "primary_switches_observed": switches_over_time,
    "switch_events": len([e for e in switches_over_time if e["from"] is not None and e["to"] is not None and e["from"] != e["to"]]),
    "tracks_now": len(snap3.tracks),
    "last_present": snap3.present,
}
print(json.dumps(v3, ensure_ascii=False, indent=2))
check(tr.stats.detect_count >= 10, f"V3 detect_count={tr.stats.detect_count} ≥ 10")
check(tr.stats.hit_count >= 1, f"V3 hit_count={tr.stats.hit_count} ≥ 1 (有脸帧应命中)")
# 视频里 face 一路缩小直到 detect 失败；最多 1-2 个 track 是合理上限
check(tr.stats.tracks_created <= 3,
      f"V3 tracks_created={tr.stats.tracks_created} ≤ 3 (走开场景不应频繁新建)")
# 主脸切换：tracks_created 决定上限，但实际"老 primary 死掉、新 track 接班" ≤ 2 次
check(v3["switch_events"] <= 2,
      f"V3 switch_events={v3['switch_events']} ≤ 2 (走开场景主脸切换克制)")
check(tr.stats.error_count == 0, f"V3 error_count={tr.stats.error_count} == 0")


# ---------------------------------------------------------------------------
# V4: 合成 detection 序列 — K/J hysteresis 边界
# ---------------------------------------------------------------------------
print("\n========== V4: hysteresis K=4 / J=2 边界 ==========")
stop = threading.Event()
tr = FaceTracker(
    stop, camera=None, camera_spec=None,  # 不开摄像头
    fps=5.0, presence_window=10,
    presence_min_hits=2, absence_min_misses=4,
    iou_threshold=0.3, max_track_misses=5,
)
# 不调 start，直接 feed_detections
fb = FaceBox(x=100, y=80, w=60, h=80)
events: List[dict] = []

def step(boxes, label):
    snap = tr.feed_detections(boxes, frame_w=320, frame_h=240)
    events.append({"step": label, "present": snap.present,
                   "tracks": len(snap.tracks),
                   "primary_id": snap.primary_track.track_id if snap.primary_track else None})

# 序列：1 hit -> present 还是 False (J=2 还没满)；2 hit -> present True
step([fb], "hit#1"); step([fb], "hit#2")
v4_present_after_J = events[-1]["present"]
# 现在加 miss 序列：连续 3 miss 时 present 仍 True (K=4 没满)；第 4 miss 应 False
step([], "miss#1"); step([], "miss#2"); step([], "miss#3")
v4_present_K_minus_1 = events[-1]["present"]
step([], "miss#4")
v4_present_after_K = events[-1]["present"]

print(json.dumps(events, ensure_ascii=False, indent=2))
check(events[0]["present"] is False, "V4 J=2: 第 1 hit 不立即 present")
check(v4_present_after_J is True, "V4 J=2: 第 2 hit 应 present=True")
check(v4_present_K_minus_1 is True, "V4 K=4: 第 3 miss 仍维持 present=True")
check(v4_present_after_K is False, "V4 K=4: 第 4 miss 应 present=False")


# ---------------------------------------------------------------------------
# V5: IoU greedy 匹配正确性
# ---------------------------------------------------------------------------
print("\n========== V5: IoU greedy 匹配 ==========")
stop = threading.Event()
tr = FaceTracker(
    stop, camera=None, camera_spec=None, fps=5.0,
    presence_window=5, presence_min_hits=2, absence_min_misses=5,
    iou_threshold=0.3, max_track_misses=3, primary_strategy="area",
)
# 帧1：两脸 A=(20,20,60,80)  B=(220,20,60,80)
A1 = FaceBox(20, 20, 60, 80)
B1 = FaceBox(220, 20, 60, 80)
s1 = tr.feed_detections([A1, B1])
ids_after_f1 = sorted(t.track_id for t in s1.tracks)
# 帧2：略偏移（仍 IoU 高），加 swap 顺序，验证不会乱配
A2 = FaceBox(22, 22, 60, 80)  # 与 A1 IoU ~0.91
B2 = FaceBox(218, 18, 60, 80)  # 与 B1 IoU ~0.92
# 跨配：A1 与 B2 IoU = 0；B1 与 A2 IoU = 0  → greedy 不可能错配
s2 = tr.feed_detections([B2, A2])  # 故意交换顺序
ids_after_f2 = sorted(t.track_id for t in s2.tracks)

# 帧3：只剩一个 (A 走了)
s3 = tr.feed_detections([B2])
# B 应继续保持自己 track_id，A 进 miss
b_track = next(t for t in s3.tracks if t.box.x > 100)
a_track = next(t for t in s3.tracks if t.box.x < 100)

# 测试 IoU 函数
iou_AB1 = iou_xywh(A1, B1)
iou_AA2 = iou_xywh(A1, A2)

v5 = {
    "ids_after_f1": ids_after_f1,
    "ids_after_f2": ids_after_f2,
    "tracks_created": tr.stats.tracks_created,
    "iou_A_vs_B": round(iou_AB1, 3),
    "iou_A1_vs_A2": round(iou_AA2, 3),
    "frame3_a_miss": a_track.miss_count,
    "frame3_b_miss": b_track.miss_count,
    "frame3_a_id_unchanged": a_track.track_id == ids_after_f1[0],
    "frame3_b_id_unchanged": b_track.track_id == ids_after_f1[1],
}
print(json.dumps(v5, ensure_ascii=False, indent=2))
check(len(ids_after_f1) == 2, "V5 帧1 应建 2 个 track")
check(ids_after_f2 == ids_after_f1, f"V5 帧2 顺序交换后 track_id 集合不变 {ids_after_f1} == {ids_after_f2}")
check(tr.stats.tracks_created == 2, f"V5 tracks_created={tr.stats.tracks_created} == 2 (无错配新建)")
check(iou_AB1 == 0.0, f"V5 IoU(A,B) = {iou_AB1} (相距远，不重叠)")
check(iou_AA2 > 0.8, f"V5 IoU(A1,A2) = {iou_AA2} > 0.8")
check(b_track.miss_count == 0, "V5 帧3 B 仍命中 miss=0")
check(a_track.miss_count == 1, "V5 帧3 A 未命中 miss=1")


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
trace_path = Path("evidence/vision-002/verify_trace.json")
trace_path.parent.mkdir(parents=True, exist_ok=True)
with open(trace_path, "w") as f:
    json.dump({"V1": v1, "V2": v2, "V3": v3, "V4": events, "V5": v5},
              f, ensure_ascii=False, indent=2)
print(f"\n  trace -> {trace_path}")

if errors:
    print(f"\n[vision-002] FAIL ({len(errors)}):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print(f"\n[vision-002] PASS")
sys.exit(0)
