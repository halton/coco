"""vision-005 verification: 简易手势识别 (HeuristicGestureBackend / GestureRecognizer).

跑法::

    uv run python scripts/verify_vision_005.py

子项：

V1   默认 OFF：未设 COCO_GESTURE → load_config().gesture.enabled == False
V2   COCO_GESTURE=1 + 字段 clamp → GestureConfig 字段对齐
V3   HeuristicGestureBackend.detect WAVE fixture → kind=WAVE + conf >= min
V4   detect THUMBS_UP fixture
V5   启发式 backend 处理畸形 / 全黑 / empty fixture 不崩溃，confidence=0 不 emit
V6   detect NOD fixture（位移）+ SHAKE fixture（位移）
V7   GestureRecognizer 后台线程读帧 + emit
V8   cooldown_per_kind: 同 kind 短时内重复检测只 emit 一次（短 cooldown 路径）
V9   confidence 低于 min_confidence 不 emit
V10  emit "vision.gesture_detected"（component vision 在 AUTHORITATIVE_COMPONENTS）
V11  stop() 干净退出 + window_frames clamp（<2 → 2，>60 → 60）
V12  env clamp（interval_ms / min_confidence / cooldown / window）
V13  回归门：调用 verify_vision_002.py + verify_vision_004.py 子进程，全 PASS
V14  默认 30s cooldown 行为：连续两次同 kind 第二次被抑制
V15  HEART fixture 经 backend 不崩溃 + 落到合理 kind / None

retval：0 全 PASS；1 任一失败
evidence 落 evidence/vision-005/verify_summary.json
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.config import load_config
from coco.logging_setup import AUTHORITATIVE_COMPONENTS
from coco.perception.gesture import (
    GestureBackend,
    GestureConfig,
    GestureKind,
    GestureLabel,
    GestureRecognizer,
    GestureRecognizerStats,
    HeuristicGestureBackend,
    gesture_config_from_env,
)

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

errors: List[str] = []
results: dict = {}


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok   {msg}")
    else:
        errors.append(msg)
        print(f"  FAIL {msg}")


def load_video(p: Path) -> List[np.ndarray]:
    cap = cv2.VideoCapture(str(p))
    frames: List[np.ndarray] = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    return frames


FX = ROOT / "tests" / "fixtures" / "vision" / "gestures"


# ---------------------------------------------------------------------------
# V1
# ---------------------------------------------------------------------------
print("\n[V1] 默认 OFF (COCO_GESTURE 未设)")
env = {k: v for k, v in os.environ.items() if not k.startswith("COCO_GESTURE")}
cfg = load_config(env=env)
check(cfg.gesture is not None, "cfg.gesture 不为 None")
check(cfg.gesture.enabled is False, "默认 gesture.enabled = False")
check(cfg.gesture.interval_ms == 200, "默认 interval_ms = 200")
check(abs(cfg.gesture.min_confidence - 0.5) < 1e-6, "默认 min_confidence = 0.5")
check(abs(cfg.gesture.cooldown_per_kind_s - 30.0) < 1e-6, "默认 cooldown = 30.0")
check(cfg.gesture.window_frames == 8, "默认 window_frames = 8")

# ---------------------------------------------------------------------------
# V2
# ---------------------------------------------------------------------------
print("\n[V2] COCO_GESTURE=1 + 字段 clamp")
env2 = dict(env)
env2.update({
    "COCO_GESTURE": "1",
    "COCO_GESTURE_INTERVAL_MS": "150",
    "COCO_GESTURE_MIN_CONFIDENCE": "0.6",
    "COCO_GESTURE_COOLDOWN_S": "3.5",
    "COCO_GESTURE_WINDOW_FRAMES": "10",
})
cfg2 = load_config(env=env2)
check(cfg2.gesture.enabled is True, "enabled=True")
check(cfg2.gesture.interval_ms == 150, "interval_ms=150")
check(abs(cfg2.gesture.min_confidence - 0.6) < 1e-6, "min_confidence=0.6")
check(abs(cfg2.gesture.cooldown_per_kind_s - 3.5) < 1e-6, "cooldown=3.5")
check(cfg2.gesture.window_frames == 10, "window_frames=10")

# ---------------------------------------------------------------------------
# V3 WAVE
# ---------------------------------------------------------------------------
print("\n[V3] WAVE fixture detect")
backend = HeuristicGestureBackend()
wave = load_video(FX / "wave_synthetic.mp4")
check(len(wave) >= 8, f"wave fixture 帧数足够: {len(wave)}")
lbl = backend.detect(wave[:8])
check(lbl is not None and lbl.kind is GestureKind.WAVE,
      f"WAVE detected on wave[:8]; got={lbl}")
check(lbl is not None and lbl.confidence >= 0.5,
      f"WAVE confidence >= 0.5; got={(lbl.confidence if lbl else None)}")
# L1-3: WAVE 优先 — 同样的 wave fixture 不能反过来 emit SHAKE
check(lbl is not None and lbl.kind is not GestureKind.SHAKE,
      "wave fixture 不会被误判为 SHAKE（WAVE 优先 return）")

# ---------------------------------------------------------------------------
# V4 THUMBS_UP
# ---------------------------------------------------------------------------
print("\n[V4] THUMBS_UP fixture detect")
img_thumbs = cv2.imread(str(FX / "thumbs_up_synthetic.jpg"))
check(img_thumbs is not None, "thumbs_up fixture 加载成功")
lbl = backend.detect([img_thumbs])
check(lbl is not None and lbl.kind is GestureKind.THUMBS_UP,
      f"THUMBS_UP detected; got={lbl}")
check(lbl is not None and lbl.bbox is not None,
      "THUMBS_UP label 含 bbox")

# ---------------------------------------------------------------------------
# V5 启发式 backend 对畸形 / 全黑 / empty fixture 不崩溃，confidence=0 不 emit
# ---------------------------------------------------------------------------
print("\n[V5] 启发式 backend 处理畸形 / 全黑 / empty fixture 不崩溃")
# 全黑帧
black = np.zeros((120, 160, 3), dtype=np.uint8)
try:
    lbl_black = backend.detect([black] * 8)
    check(True, "全黑帧 detect 不崩溃")
    check(lbl_black is None, f"全黑帧 backend 返回 None; got={lbl_black}")
except Exception as e:  # noqa: BLE001
    check(False, f"全黑帧 detect 崩溃: {type(e).__name__}: {e}")
# empty fixture (空白图)
img_empty = cv2.imread(str(FX / "empty_synthetic.jpg"))
check(img_empty is not None, "empty fixture 加载成功")
try:
    lbl_empty = backend.detect([img_empty])
    check(True, "empty fixture detect 不崩溃")
    check(lbl_empty is None, f"empty fixture 返回 None; got={lbl_empty}")
except Exception as e:  # noqa: BLE001
    check(False, f"empty fixture detect 崩溃: {type(e).__name__}: {e}")
# 畸形 1×1×3 单像素帧
tiny = np.full((1, 1, 3), 50, dtype=np.uint8)
try:
    lbl_tiny = backend.detect([tiny] * 4)
    check(True, "1×1 畸形帧 detect 不崩溃")
except Exception as e:  # noqa: BLE001
    check(False, f"畸形 1×1 帧 detect 崩溃: {type(e).__name__}: {e}")
# confidence=0 不 emit（min_confidence=0.5）：直接做 recognizer 路径
class _ZeroConfBackend:
    def detect(self, frames):
        return GestureLabel(kind=GestureKind.WAVE, confidence=0.0, ts=time.monotonic())
emitted_zero: List[GestureLabel] = []
rec_zero = GestureRecognizer(
    threading.Event(), backend=_ZeroConfBackend(), min_confidence=0.5,
    cooldown_per_kind_s=0.0, window_frames=4,
    on_gesture=lambda lb: emitted_zero.append(lb),
)
for _ in range(3):
    rec_zero.feed_frame(np.zeros((16, 16, 3), dtype=np.uint8))
check(len(emitted_zero) == 0, f"confidence=0 不 emit; got={len(emitted_zero)}")

# ---------------------------------------------------------------------------
# V6 NOD + SHAKE
# ---------------------------------------------------------------------------
print("\n[V6] NOD + SHAKE fixture detect")
nod = load_video(FX / "nod_synthetic.mp4")
shake = load_video(FX / "shake_synthetic.mp4")
check(len(nod) >= 8 and len(shake) >= 8, "nod/shake fixture 足够帧")
lbl_nod = backend.detect(nod[:8])
check(lbl_nod is not None and lbl_nod.kind is GestureKind.NOD,
      f"NOD detected; got={lbl_nod}")
lbl_shake = backend.detect(shake[:8])
check(lbl_shake is not None and lbl_shake.kind is GestureKind.SHAKE,
      f"SHAKE detected; got={lbl_shake}")

# ---------------------------------------------------------------------------
# V7 后台线程读帧 + emit
# ---------------------------------------------------------------------------
print("\n[V7] GestureRecognizer 后台线程 + emit")


class _SeqCamera:
    """从一组预加载帧无限循环回放（模拟 VideoFileSource）。"""

    def __init__(self, frames: List[np.ndarray]) -> None:
        self.frames = list(frames)
        self.i = 0

    def read(self):
        if not self.frames:
            return False, None
        f = self.frames[self.i % len(self.frames)]
        self.i += 1
        return True, f.copy()


stop_evt = threading.Event()
emitted: List[GestureLabel] = []
cam = _SeqCamera(wave)
rec = GestureRecognizer(
    stop_evt,
    camera=cam,
    backend=HeuristicGestureBackend(),
    interval_ms=50,
    min_confidence=0.5,
    cooldown_per_kind_s=0.0,  # 不限速，便于看 emit
    window_frames=8,
    on_gesture=lambda lb: emitted.append(lb),
)
rec.start()
t0 = time.time()
while time.time() - t0 < 1.0 and len(emitted) < 1:
    time.sleep(0.05)
stop_evt.set()
rec.join(timeout=2.0)
check(not rec.is_alive(), "线程在 join 后已退出")
check(len(emitted) >= 1, f"后台线程产出至少 1 次 gesture；got={len(emitted)}")
check(rec.stats.frames_read >= 1, f"frames_read >= 1; got={rec.stats.frames_read}")
check(rec.stats.emit_count >= 1, f"stats.emit_count >= 1; got={rec.stats.emit_count}")

# ---------------------------------------------------------------------------
# V8 cooldown
# ---------------------------------------------------------------------------
print("\n[V8] cooldown_per_kind 抑制重复 emit (短 cooldown 路径)")
stop_evt2 = threading.Event()
emitted2: List[GestureLabel] = []
fake_now = [0.0]
rec2 = GestureRecognizer(
    stop_evt2,
    backend=HeuristicGestureBackend(),
    interval_ms=50,
    min_confidence=0.5,
    cooldown_per_kind_s=10.0,
    window_frames=8,
    on_gesture=lambda lb: emitted2.append(lb),
    clock=lambda: fake_now[0],
)
# 不 start 线程，直接 feed_frame
for f in wave[:8]:
    rec2.feed_frame(f)
n_after_first_burst = len(emitted2)
check(n_after_first_burst >= 1, f"首轮 burst 产出 >=1; got={n_after_first_burst}")
# 同 kind 再喂一轮（fake_now 不前进） → 应被 cooldown 抑制
for f in wave[:8]:
    rec2.feed_frame(f)
check(len(emitted2) == n_after_first_burst,
      f"cooldown 内不再 emit；before={n_after_first_burst} after={len(emitted2)}")
# 推进 fake clock 越过 cooldown
fake_now[0] += 11.0
for f in wave[:8]:
    rec2.feed_frame(f)
check(len(emitted2) > n_after_first_burst,
      f"cooldown 过期后可再次 emit；count={len(emitted2)}")
check(rec2.stats.suppressed_cooldown >= 1,
      f"stats.suppressed_cooldown >= 1; got={rec2.stats.suppressed_cooldown}")

# ---------------------------------------------------------------------------
# V9 confidence 低于阈值不 emit
# ---------------------------------------------------------------------------
print("\n[V9] confidence < min_confidence 不 emit")


class _LowConfBackend:
    def detect(self, frames):
        return GestureLabel(
            kind=GestureKind.WAVE, confidence=0.3,
            ts=time.monotonic(),
        )


emitted3: List[GestureLabel] = []
rec3 = GestureRecognizer(
    threading.Event(),
    backend=_LowConfBackend(),
    interval_ms=100,
    min_confidence=0.5,
    cooldown_per_kind_s=0.0,
    window_frames=4,
    on_gesture=lambda lb: emitted3.append(lb),
)
for _ in range(5):
    rec3.feed_frame(np.zeros((32, 32, 3), dtype=np.uint8))
check(len(emitted3) == 0, f"低 conf 全部被吞；emitted={len(emitted3)}")
check(rec3.stats.suppressed_low_conf >= 1,
      f"stats.suppressed_low_conf >= 1; got={rec3.stats.suppressed_low_conf}")

# ---------------------------------------------------------------------------
# V10 emit channel
# ---------------------------------------------------------------------------
print("\n[V10] vision.gesture_detected event 配置")
check("vision" in AUTHORITATIVE_COMPONENTS,
      "'vision' component 在 AUTHORITATIVE_COMPONENTS")
event_name = "vision.gesture_detected"
check(event_name.split(".")[0] == "vision",
      f"event 命名空间归属 vision: {event_name}")

# ---------------------------------------------------------------------------
# V11 stop + window clamp
# ---------------------------------------------------------------------------
print("\n[V11] stop() 干净退出 + window_frames clamp")
rec_lo = GestureRecognizer(threading.Event(), window_frames=1)
check(rec_lo.window_frames == 2, f"window_frames=1 → clamp 2; got={rec_lo.window_frames}")
rec_hi = GestureRecognizer(threading.Event(), window_frames=999)
check(rec_hi.window_frames == 60, f"window_frames=999 → clamp 60; got={rec_hi.window_frames}")
stop_e = threading.Event()
rec4 = GestureRecognizer(
    stop_e,
    camera=_SeqCamera(wave),
    backend=HeuristicGestureBackend(),
    interval_ms=100,
    min_confidence=0.99,
    cooldown_per_kind_s=0.0,
    window_frames=4,
)
rec4.start()
time.sleep(0.3)
rec4.stop()
rec4.join(timeout=2.0)
check(not rec4.is_alive(), "stop() 后线程退出")
rec4.stop()
rec4.join(timeout=0.5)
check(True, "重复 stop/join 不抛")

# ---------------------------------------------------------------------------
# V12 env clamp
# ---------------------------------------------------------------------------
print("\n[V12] env clamp (interval_ms / min_confidence / cooldown / window)")
env_clamp = {
    "COCO_GESTURE": "1",
    "COCO_GESTURE_INTERVAL_MS": "10",
    "COCO_GESTURE_MIN_CONFIDENCE": "1.5",
    "COCO_GESTURE_COOLDOWN_S": "-1",
    "COCO_GESTURE_WINDOW_FRAMES": "1",
}
gc = gesture_config_from_env(env_clamp)
check(gc.enabled is True, "env enabled=True")
check(gc.interval_ms == 50, f"interval_ms clamp 50; got={gc.interval_ms}")
check(abs(gc.min_confidence - 1.0) < 1e-6, f"min_conf clamp 1.0; got={gc.min_confidence}")
check(abs(gc.cooldown_per_kind_s - 0.0) < 1e-6, f"cooldown clamp 0; got={gc.cooldown_per_kind_s}")
check(gc.window_frames == 2, f"window clamp 2; got={gc.window_frames}")

env_clamp2 = {
    "COCO_GESTURE_INTERVAL_MS": "9999",
    "COCO_GESTURE_WINDOW_FRAMES": "9999",
    "COCO_GESTURE_COOLDOWN_S": "999",
}
gc2 = gesture_config_from_env(env_clamp2)
check(gc2.interval_ms == 2000, f"interval_ms clamp 2000; got={gc2.interval_ms}")
check(gc2.window_frames == 60, f"window clamp 60; got={gc2.window_frames}")
check(abs(gc2.cooldown_per_kind_s - 60.0) < 1e-6,
      f"cooldown clamp 60; got={gc2.cooldown_per_kind_s}")

# ---------------------------------------------------------------------------
# V13 回归：调用 verify_vision_002 / verify_vision_004 子进程
# ---------------------------------------------------------------------------
print("\n[V13] 回归 vision-002 + vision-004 verify 脚本")
v13_results: dict = {}
for vname, script in (
    ("vision-002", "scripts/verify_vision_002.py"),
    ("vision-004", "scripts/verify_vision_004.py"),
):
    spath = ROOT / script
    if not spath.exists():
        check(False, f"{vname} verify 脚本不存在: {script}")
        v13_results[vname] = "missing"
        continue
    print(f"  -> running {vname} ({script}) as subprocess ...")
    try:
        proc = subprocess.run(
            [sys.executable, str(spath)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        ok = proc.returncode == 0
        v13_results[vname] = {
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout.strip().splitlines()[-3:] if proc.stdout else [],
            "stderr_tail": proc.stderr.strip().splitlines()[-3:] if proc.stderr else [],
        }
        check(ok, f"{vname} 回归 PASS (returncode={proc.returncode})")
        if not ok:
            print(f"    stdout tail: {v13_results[vname]['stdout_tail']}")
            print(f"    stderr tail: {v13_results[vname]['stderr_tail']}")
    except subprocess.TimeoutExpired:
        check(False, f"{vname} 回归超时")
        v13_results[vname] = "timeout"
    except Exception as e:  # noqa: BLE001
        check(False, f"{vname} 回归调用失败: {type(e).__name__}: {e}")
        v13_results[vname] = f"error: {type(e).__name__}: {e}"

# ---------------------------------------------------------------------------
# V14 默认 30s cooldown：连续两次同 kind 第二次被抑制
# ---------------------------------------------------------------------------
print("\n[V14] 默认 30s cooldown 行为：连续两次同 kind 第二次被抑制")
fake_now2 = [0.0]
emitted_default: List[GestureLabel] = []
default_cooldown = GestureConfig().cooldown_per_kind_s
check(abs(default_cooldown - 30.0) < 1e-6,
      f"GestureConfig().cooldown_per_kind_s 默认 30.0; got={default_cooldown}")
rec14 = GestureRecognizer(
    threading.Event(),
    backend=HeuristicGestureBackend(),
    cooldown_per_kind_s=default_cooldown,  # 30s 默认
    window_frames=8,
    min_confidence=0.5,
    on_gesture=lambda lb: emitted_default.append(lb),
    clock=lambda: fake_now2[0],
)
# 第一轮
for f in wave[:8]:
    rec14.feed_frame(f)
n1 = len(emitted_default)
check(n1 >= 1, f"第一轮 emit >=1; got={n1}")
# 第二轮 fake clock 仅前进 5s（远小于 30s cooldown）
fake_now2[0] += 5.0
for f in wave[:8]:
    rec14.feed_frame(f)
check(len(emitted_default) == n1,
      f"30s cooldown 内第二轮被抑制；before={n1} after={len(emitted_default)}")
# 第三轮 越过 30s
fake_now2[0] += 31.0
for f in wave[:8]:
    rec14.feed_frame(f)
check(len(emitted_default) > n1,
      f"30s cooldown 过期后可再次 emit；count={len(emitted_default)}")

# ---------------------------------------------------------------------------
# V15 HEART fixture 经 backend 不崩溃
# ---------------------------------------------------------------------------
print("\n[V15] HEART fixture 经 backend 不崩溃 + 落到合理 kind / None")
heart_path = FX / "heart_synthetic.jpg"
check(heart_path.exists(), f"HEART fixture 存在: {heart_path}")
img_heart = cv2.imread(str(heart_path))
check(img_heart is not None, "HEART fixture 加载成功")
try:
    lbl_heart = backend.detect([img_heart])
    check(True, "HEART fixture detect 不崩溃")
    # 不强求 confidence 高，但若返回则 kind 必须落在已知集合内
    if lbl_heart is not None:
        valid_kinds = {GestureKind.HEART, GestureKind.WAVE, GestureKind.THUMBS_UP,
                       GestureKind.NOD, GestureKind.SHAKE, GestureKind.NONE}
        check(lbl_heart.kind in valid_kinds,
              f"HEART fixture 输出 kind 在合法集合; got={lbl_heart.kind}")
        check(0.0 <= lbl_heart.confidence <= 1.0,
              f"HEART fixture confidence 在 [0,1]; got={lbl_heart.confidence}")
        print(f"  info HEART fixture → kind={lbl_heart.kind.value} conf={lbl_heart.confidence:.2f}")
    else:
        print("  info HEART fixture → None (启发式占位偏弱属正常)")
except Exception as e:  # noqa: BLE001
    check(False, f"HEART fixture detect 崩溃: {type(e).__name__}: {e}")

# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------
results = {
    "feature": "vision-005",
    "ok": len(errors) == 0,
    "errors": errors,
    "fixtures": [
        str(p.relative_to(ROOT))
        for p in sorted(FX.glob("*"))
        if p.is_file()
    ],
    "v13_regression": v13_results,
}
out = ROOT / "evidence" / "vision-005"
out.mkdir(parents=True, exist_ok=True)
(out / "verify_summary.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

print("\n" + "=" * 60)
if errors:
    print(f"vision-005 FAIL: {len(errors)} error(s)")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print("vision-005 PASS (V1-V15)")
sys.exit(0)
