"""一次性 fixture 生成器：合成 single_face.jpg / no_one.jpg / user_walks_away.mp4。

跨平台 codec：mp4 用 ``mp4v`` (FOURCC) 写 H.264-baseline 兼容容器，OpenCV 在
macOS / Linux 默认 build 都能解码。所有 fixture 完全程序合成，不依赖外部下载。
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "vision"
OUT.mkdir(parents=True, exist_ok=True)

W, H = 320, 240


def _bg(color=(40, 60, 80)) -> np.ndarray:
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[:] = color  # BGR
    return img


def draw_face(img: np.ndarray, cx: int, cy: int, scale: float = 1.0) -> None:
    """在 img 上画一个简易的"人脸"几何（椭圆 + 双眼 + 嘴）。"""
    color_skin = (180, 200, 220)
    color_eye = (30, 30, 30)
    color_mouth = (40, 40, 120)
    a = int(40 * scale)
    b = int(55 * scale)
    cv2.ellipse(img, (cx, cy), (a, b), 0, 0, 360, color_skin, -1)
    # 眼
    eye_dx = int(15 * scale)
    eye_dy = int(15 * scale)
    eye_r = max(2, int(5 * scale))
    cv2.circle(img, (cx - eye_dx, cy - eye_dy), eye_r, color_eye, -1)
    cv2.circle(img, (cx + eye_dx, cy - eye_dy), eye_r, color_eye, -1)
    # 嘴
    cv2.ellipse(img, (cx, cy + int(20 * scale)), (int(15 * scale), int(6 * scale)), 0, 0, 180, color_mouth, 2)


def gen_single_face() -> Path:
    img = _bg()
    draw_face(img, W // 2, H // 2, scale=1.0)
    cv2.putText(img, "single_face fixture", (8, H - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    p = OUT / "single_face.jpg"
    ok = cv2.imwrite(str(p), img)
    assert ok, f"imwrite failed: {p}"
    return p


def gen_no_one() -> Path:
    img = _bg(color=(60, 60, 60))
    cv2.putText(img, "no_one fixture (empty room)", (8, H - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    # 几何家具示意：一道地平线 + 一把椅子轮廓
    cv2.line(img, (0, int(H * 0.65)), (W, int(H * 0.65)), (90, 90, 90), 2)
    cv2.rectangle(img, (200, 130), (260, 200), (80, 100, 120), 2)
    cv2.line(img, (200, 130), (200, 100), (80, 100, 120), 2)
    p = OUT / "no_one.jpg"
    ok = cv2.imwrite(str(p), img)
    assert ok, f"imwrite failed: {p}"
    return p


def _gen_face_id_person(label: str, seed: int, eye_color: tuple, mouth_offset: int,
                        skin_color: tuple, n: int = 5, scale_jitter: float = 0.05) -> list[Path]:
    """vision-003: 生成单人 N 张 100x100 灰度脸 fixture（不同高斯噪声 + 微仿射变形）。

    每"人"用唯一组合（皮肤色 / 眼色 / 嘴位置 / 噪声种子）确保 histogram 统计上可区分。
    """
    rng = np.random.default_rng(seed)
    out_dir = OUT / "face_id" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i in range(n):
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        img[:] = (60, 70, 90)  # 背景
        # 仿射偏移（模拟同人多角度）
        scale = 1.0 + rng.uniform(-scale_jitter, scale_jitter)
        cx = 50 + int(rng.integers(-3, 4))
        cy = 50 + int(rng.integers(-3, 4))
        a = int(28 * scale)
        b = int(36 * scale)
        cv2.ellipse(img, (cx, cy), (a, b), 0, 0, 360, skin_color, -1)
        # 双眼
        eye_dx = int(10 * scale)
        eye_dy = int(8 * scale)
        eye_r = max(2, int(3 * scale))
        cv2.circle(img, (cx - eye_dx, cy - eye_dy), eye_r, eye_color, -1)
        cv2.circle(img, (cx + eye_dx, cy - eye_dy), eye_r, eye_color, -1)
        # 嘴
        cv2.ellipse(img, (cx, cy + mouth_offset),
                    (int(10 * scale), int(4 * scale)), 0, 0, 180, (40, 40, 120), 2)
        # 高斯噪声（同人不同采样）
        noise = rng.normal(0, 8, img.shape).astype(np.int16)
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        p = out_dir / f"{i + 1}.jpg"
        ok = cv2.imwrite(str(p), img)
        assert ok, f"imwrite failed: {p}"
        paths.append(p)
    return paths


def gen_face_id_fixtures() -> list[Path]:
    """生成 alice / bob 各 5 张 + unknown_face.jpg 1 张，全程序合成。"""
    all_paths: list[Path] = []
    # alice：浅肤色 + 黑眼 + 嘴较低
    all_paths.extend(_gen_face_id_person(
        "alice", seed=42,
        eye_color=(20, 20, 20), mouth_offset=18,
        skin_color=(200, 215, 230),
    ))
    # bob：偏黄肤色 + 棕眼 + 嘴较高
    all_paths.extend(_gen_face_id_person(
        "bob", seed=137,
        eye_color=(40, 60, 100), mouth_offset=10,
        skin_color=(150, 180, 200),
    ))
    # unknown：第三种组合，alice/bob 训练集外
    rng = np.random.default_rng(999)
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[:] = (40, 50, 70)
    cv2.ellipse(img, (50, 50), (30, 38), 0, 0, 360, (100, 130, 160), -1)
    cv2.circle(img, (40, 40), 4, (200, 200, 200), -1)
    cv2.circle(img, (60, 40), 4, (200, 200, 200), -1)
    cv2.rectangle(img, (40, 65), (60, 70), (50, 50, 50), -1)
    noise = rng.normal(0, 10, img.shape).astype(np.int16)
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    unk = OUT / "unknown_face.jpg"
    ok = cv2.imwrite(str(unk), img)
    assert ok, f"imwrite failed: {unk}"
    all_paths.append(unk)
    return all_paths


def gen_two_faces(seconds: float = 4.0, fps: float = 10.0) -> Path:
    """vision-008: 合成 2 张人脸同时在场的视频。

    左右两张几何"脸"，分别有不同肤色 / 眼色 / 嘴位置，在画面里轻微位移，
    模拟两个不同用户同时被看到的场景。FaceDetector cascade 在这种合成几何
    上检出率不高，但本 fixture 主要用于：

    - 上层 ``feed_detections`` 注入 + 手工指定 name 时的 face_id 区分测试
    - VideoFileSource 可正常解码（与 user_walks_away.mp4 同 codec）
    """
    p = OUT / "two_faces.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(str(p), fourcc, fps, (W, H))
    if not vw.isOpened():
        raise RuntimeError(f"VideoWriter 打不开：{p}")
    n = int(seconds * fps)
    color_skin_a = (200, 215, 230)
    color_eye_a = (20, 20, 20)
    color_skin_b = (150, 180, 200)
    color_eye_b = (40, 60, 100)
    for i in range(n):
        img = _bg()
        # left face (alice-like)
        cx_a = 90 + int(5 * np.sin(i * 0.3))
        cy_a = H // 2
        cv2.ellipse(img, (cx_a, cy_a), (35, 45), 0, 0, 360, color_skin_a, -1)
        cv2.circle(img, (cx_a - 12, cy_a - 12), 4, color_eye_a, -1)
        cv2.circle(img, (cx_a + 12, cy_a - 12), 4, color_eye_a, -1)
        cv2.ellipse(img, (cx_a, cy_a + 18), (12, 5), 0, 0, 180, (40, 40, 120), 2)
        # right face (bob-like)
        cx_b = 230 + int(5 * np.cos(i * 0.3))
        cy_b = H // 2
        cv2.ellipse(img, (cx_b, cy_b), (35, 45), 0, 0, 360, color_skin_b, -1)
        cv2.circle(img, (cx_b - 12, cy_b - 12), 4, color_eye_b, -1)
        cv2.circle(img, (cx_b + 12, cy_b - 12), 4, color_eye_b, -1)
        cv2.ellipse(img, (cx_b, cy_b + 10), (12, 5), 0, 0, 180, (40, 40, 120), 2)
        cv2.putText(img, f"two_faces {i+1}/{n}", (8, H - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
        vw.write(img)
    vw.release()
    return p


def gen_user_walks_away(seconds: float = 3.0, fps: float = 15.0) -> Path:
    """合成"用户从画面中央走向远处"短视频。"""
    p = OUT / "user_walks_away.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(str(p), fourcc, fps, (W, H))
    if not vw.isOpened():
        raise RuntimeError(f"VideoWriter 打不开（codec mp4v 不可用？）：{p}")
    n = int(seconds * fps)
    for i in range(n):
        t = i / max(1, n - 1)  # 0 → 1
        # 比例从 1.0 缩到 0.35（远离）；y 略往上（透视）
        scale = 1.0 - 0.65 * t
        cx = W // 2
        cy = int(H // 2 - 30 * t)
        img = _bg()
        draw_face(img, cx, cy, scale=scale)
        cv2.putText(img, f"walks_away frame {i+1}/{n}", (8, H - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
        vw.write(img)
    vw.release()
    return p


README = """# vision fixtures

本目录所有 fixture 均由 `scripts/gen_vision_fixtures.py` 程序合成，无任何外部下载、无版权风险。

## 文件清单

- `single_face.jpg` — 320×240 BGR JPG，画面中央一张几何"人脸"（椭圆+双眼+嘴）。用于测试 ImageLoopSource 与未来"画面中有一个人"语义。
- `no_one.jpg` — 320×240 BGR JPG，空房间示意（地平线 + 椅子轮廓）。用于测试"画面中没人"。
- `user_walks_away.mp4` — 320×240 mp4v 编码，约 3 秒 @ 15fps。"人脸"从画面中央由近到远缩小同时上移。用于测试 VideoFileSource 与未来"用户走开"语义。
- `two_faces.mp4` — 320×240 mp4v 编码，约 4 秒 @ 10fps。画面左右各一张几何"人脸"，肤色 / 眼色 / 嘴位置不同。用于 vision-008 multi face_id 场景（fixture VideoFileSource 解码 + 上层注入 name → face_id 区分）。

## 编码注意

mp4v FOURCC 是 OpenCV 在 macOS / Linux 上默认 build 都自带的解码器（MPEG-4 Part 2），不依赖系统专有 codec（如 H.264 GPL 包）。在 cv2.VideoCapture 下可正常解码。

## fixture 不能 sim 的部分（必须真机 UAT）

视觉-运动闭环：相机视角随头部转动而变化。fixture 是预录画面，无法响应 reachy_mini 的动作。
凡涉及"看到 → 转头 → 视野更新"的功能，必须真机 UAT，不能仅用本目录 fixture 通过。
此约束对应 infra-vision-source feature notes 第二段。

## 重新生成

```bash
uv run python scripts/gen_vision_fixtures.py
```

幂等。文件会被覆盖。
"""


def main() -> int:
    paths = [gen_single_face(), gen_no_one(), gen_user_walks_away(), gen_two_faces()]
    face_id_paths = gen_face_id_fixtures()
    (OUT / "README.md").write_text(README, encoding="utf-8")
    for p in paths:
        size = p.stat().st_size
        print(f"  ok: {p.name}  {size} bytes")
    print(f"  ok (face_id): {len(face_id_paths)} files")
    print(f"  ok: README.md")
    print(f"OUT: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
