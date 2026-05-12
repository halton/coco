# vision-005 gesture fixtures

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
