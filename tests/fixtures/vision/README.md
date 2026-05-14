# vision fixtures

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
