"""vision-005: 手势 fixture 程序合成器.

生成位置：``tests/fixtures/vision/gestures/``

- ``wave_synthetic.mp4``       横向震荡圆形（模拟手在挥动）
- ``thumbs_up_synthetic.jpg``  上半画面竖直长条 + 顶端圆（模拟竖大拇指）
- ``nod_synthetic.mp4``        前景块在 Y 方向上下位移（模拟点头）
- ``shake_synthetic.mp4``      前景块在 X 方向位移（频率低，区分于 wave）
- ``heart_synthetic.jpg``      左右两侧对称两个浅色块（占位）
- ``empty_synthetic.jpg``      空背景（无前景）

跨平台 codec：mp4v 与 vision fixture 一致。

跑法：``uv run python scripts/gen_gesture_fixtures.py``
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import cv2
import numpy as np

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "vision" / "gestures"
OUT.mkdir(parents=True, exist_ok=True)

W, H = 320, 240
SKIN = (200, 215, 230)  # BGR 浅色


def _bg(color=(40, 60, 80)) -> np.ndarray:
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[:] = color
    return img


def gen_wave(seconds: float = 1.6, fps: float = 15.0) -> Path:
    """合成"挥手"短视频：圆心在 X 方向正弦震荡，幅度 ~ 0.35 * W。

    频率取 2.5 Hz，8 帧 @ 15fps 窗口（≈0.53s）内可捕捉 ~1.3 个周期，
    产生 ≥ 2 次方向反转，足以触发 WAVE 而非 SHAKE。
    """
    p = OUT / "wave_synthetic.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(str(p), fourcc, fps, (W, H))
    if not vw.isOpened():
        raise RuntimeError(f"VideoWriter 打不开：{p}")
    n = int(seconds * fps)
    cy = H // 2
    amp = int(0.35 * W)
    for i in range(n):
        t = i / max(1, fps)
        cx = W // 2 + int(amp * math.sin(2 * math.pi * 2.5 * t))
        img = _bg()
        cv2.circle(img, (cx, cy), 28, SKIN, -1)
        # 注意：不画 putText 文本——任何亮像素都会污染 _centroid bbox
        vw.write(img)
    vw.release()
    return p


def gen_thumbs_up() -> Path:
    """合成"竖大拇指"单帧：上半画面竖直长条 + 顶端圆块。"""
    p = OUT / "thumbs_up_synthetic.jpg"
    img = _bg()
    # 长条（手）
    bar_x, bar_y = W // 2 - 12, 20
    bar_w, bar_h = 24, 80
    cv2.rectangle(img, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), SKIN, -1)
    # 顶端拇指（圆）
    cv2.circle(img, (W // 2, bar_y - 5), 20, SKIN, -1)
    cv2.putText(img, "thumbs_up", (8, H - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    ok = cv2.imwrite(str(p), img)
    assert ok, f"imwrite failed: {p}"
    return p


def gen_nod(seconds: float = 1.4, fps: float = 15.0) -> Path:
    """合成"点头"短视频：椭圆中心 Y 上下震荡，X 不动。"""
    p = OUT / "nod_synthetic.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(str(p), fourcc, fps, (W, H))
    if not vw.isOpened():
        raise RuntimeError(f"VideoWriter 打不开：{p}")
    n = int(seconds * fps)
    cx = W // 2
    amp = int(0.20 * H)
    for i in range(n):
        t = i / max(1, fps)
        cy = H // 2 + int(amp * math.sin(2 * math.pi * 1.0 * t))
        img = _bg()
        cv2.ellipse(img, (cx, cy), (40, 50), 0, 0, 360, SKIN, -1)
        vw.write(img)
    vw.release()
    return p


def gen_shake(seconds: float = 1.4, fps: float = 15.0) -> Path:
    """合成"摇头"短视频：椭圆中心 X 较慢往复（频率 0.7Hz，比 wave 低）。"""
    p = OUT / "shake_synthetic.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(str(p), fourcc, fps, (W, H))
    if not vw.isOpened():
        raise RuntimeError(f"VideoWriter 打不开：{p}")
    n = int(seconds * fps)
    cy = H // 2
    amp = int(0.18 * W)
    for i in range(n):
        t = i / max(1, fps)
        cx = W // 2 + int(amp * math.sin(2 * math.pi * 0.7 * t))
        img = _bg()
        cv2.ellipse(img, (cx, cy), (40, 50), 0, 0, 360, SKIN, -1)
        vw.write(img)
    vw.release()
    return p


def gen_heart() -> Path:
    """合成"比心"占位：左右两侧对称浅色块 + 中间一段窄空隙。"""
    p = OUT / "heart_synthetic.jpg"
    img = _bg()
    # 左块
    cv2.rectangle(img, (40, 80), (130, 160), SKIN, -1)
    # 右块
    cv2.rectangle(img, (190, 80), (280, 160), SKIN, -1)
    cv2.putText(img, "heart", (8, H - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    ok = cv2.imwrite(str(p), img)
    assert ok, f"imwrite failed: {p}"
    return p


def gen_empty() -> Path:
    """无前景：纯暗背景。"""
    p = OUT / "empty_synthetic.jpg"
    img = _bg(color=(30, 30, 30))
    cv2.putText(img, "empty", (8, H - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
    ok = cv2.imwrite(str(p), img)
    assert ok, f"imwrite failed: {p}"
    return p


README = """# vision-005 gesture fixtures

全部由 ``scripts/gen_gesture_fixtures.py`` 程序合成，无外部下载、无版权风险。

## 文件清单

- ``wave_synthetic.mp4``       — 圆形横向震荡（频率 ~1.5Hz，振幅 ~0.35W）。模拟挥手。
- ``thumbs_up_synthetic.jpg``  — 上半区域竖直长条 + 顶端圆块。模拟竖大拇指。
- ``nod_synthetic.mp4``        — 椭圆 Y 方向上下震荡（频率 ~1Hz，振幅 ~0.20H）。模拟点头。
- ``shake_synthetic.mp4``      — 椭圆 X 方向较慢往复（频率 ~0.7Hz，振幅 ~0.18W）。模拟摇头；
  与 wave 区别在反转次数较少。
- ``heart_synthetic.jpg``      — 左右两侧对称浅色块。占位实现，仅满足 mask 双块 + 中缝特征。
- ``empty_synthetic.jpg``      — 纯暗背景，期望 detect 返回 None。

## 设计原则

- 全部用几何形状 + 浅色块（BGR≈(200,215,230)），不模仿真实人脸 / 手解剖学。
- ``HeuristicGestureBackend`` 依赖的是亮度阈值 + 前景块 bbox + 帧间位移，
  阈值与本目录 fixture 校准；真机相机几乎肯定不可用。

## sim-only / 真机 UAT

本 feature **sim-only**。真机手势识别（mediapipe / DNN）属未来 feature；
本期不实现。仓库 ``feature_list.json`` 中归 vision-005 sim-only。

## 重新生成

```bash
uv run python scripts/gen_gesture_fixtures.py
```

幂等。文件会被覆盖。
"""


def main() -> int:
    paths = [
        gen_wave(),
        gen_thumbs_up(),
        gen_nod(),
        gen_shake(),
        gen_heart(),
        gen_empty(),
    ]
    (OUT / "README.md").write_text(README, encoding="utf-8")
    for p in paths:
        size = p.stat().st_size
        print(f"  ok: {p.name}  {size} bytes")
    print(f"  ok: README.md")
    print(f"OUT: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
