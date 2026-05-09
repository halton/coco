# Sim vs Real：mockup-sim 与真机 Reachy Mini 的验证 gap

**日期**：2026-05-09
**作者**：Engineer + Researcher（主 context）
**状态**：研究笔记，用于指导 feature 验证策略与 `feature_list.json` schema 演进

---

## 1. 背景

Coco 项目目前默认走 `reachy_mini.daemon --mockup-sim --deactivate-audio` 作为开发与 CI 默认环境。本笔记回答三个问题：

1. mockup-sim 与真机相比，有哪些能力 gap？哪些功能本质上无法在 sim 里测？
2. 开发机的麦克风/摄像头能否充当 sim 的输入源？
3. 用图像/视频文件作为 fixture 来模拟真机摄像头，能覆盖到什么程度？

结论先行：**多数应用层逻辑能在 sim + fixture 下完成开发和验证；少数闭环行为（视觉-运动闭环、电机扭矩、IMU、远场麦阵列、真机硬件错误）必须留到真机 UAT。**

---

## 2. Gap 矩阵（mockup-sim vs robot backend）

数据来源：直接阅读
`reachy_mini/daemon/backend/mockup_sim/backend.py` 与
`reachy_mini/daemon/backend/robot/backend.py` 源码。

| 能力 | mockup-sim | 真机 robot | sim 可覆盖度 |
|---|---|---|---|
| **运动学（FK/IK）** | Analytical / Placo / NN（同真机） | Analytical / Placo / NN | 完全覆盖。轨迹生成、姿态计算可在 sim 内验证。 |
| **目标到达** | `target = current`，下一帧立即到位（无物理） | 受电机速度/扭矩/重力影响 | sim 只能验"指令逻辑正确"；不能验"实际能否跟上、是否抖动、是否过冲"。 |
| **控制频率** | `50.0 Hz`（硬编码） | 通过 `ReachyMiniPyControlLoop` 串口实时跑 | sim 能验调用频率；不能验真实抖动与超时。 |
| **电机扭矩** | `set_motor_torque_ids` = `pass`（no-op） | Dynamixel 头部支持，Feetech 身体/天线**不支持**扭矩控制 | sim 完全无法验证扭矩、电流、过载。 |
| **电机操作模式 0/3/5** | 不存在 | `set_head_operation_mode` 切换 position/extended/PWM | 无法 sim。 |
| **重力补偿** | 无 | `compensate_head_gravity()`（Placo only，magic 常数 `1.47/0.52*1000`，correction_factor 4.0） | 无法 sim。 |
| **PID 增益** | 不存在 | 从 YAML 加载，写入 Dynamixel | 无法 sim。 |
| **硬件错误** | 不存在 | `read_hardware_errors()` 解码 XL330-M288 status bits（Input Voltage / Overheating / Electrical Shock / Overload） | 无法 sim。错误注入需 mock 一层。 |
| **IMU** | 不存在 | BMI088（**仅 wireless 版**） | 无法 sim。注意：有线版真机也没有 IMU。 |
| **WebRTC 控制通道** | 完整（`set_target` / `goto_target` / `wake_up` / `goto_sleep` / `play_sound` 等） | 完整 | 可完全在 sim 验。 |
| **Zenoh publishers** | `joint_positions` / `pose` / `recording` 完整；无 `imu` | 全部 | sim 缺 imu topic。 |
| **MediaManager / 摄像头** | 不实例化摄像头（源码注释：*Apps open the webcam/microphone directly (like with a real robot)*） | 头部 USB 摄像头通过 `cv2.VideoCapture` 由 app 打开 | sim 端摄像头由 **app 自己负责**——这是一个干净的注入点。 |
| **MediaManager / 音频** | 与真机一致：daemon 端 GStreamer/sounddevice 后端可关闭（`--deactivate-audio`） | sounddevice / GStreamer | Coco 已选择 audio decouple，daemon audio 一律关掉，由 app 直连本机麦/扬声器。 |
| **DoA（4-mic 阵列方向）** | 不存在 | `get_DoA() -> tuple[float, bool]` 由远场麦阵列硬件返回 | 单一开发机麦无法 sim。 |
| **wake_up / goto_sleep 仪式动作** | 调用栈一致；动作"瞬间到位" | 真实多关节插值，含安全减速 | sim 验调用与状态切换；不能验观感与时序。 |
| **part-of-motion / play_move** | 一致接口 | 一致接口 | 可 sim。 |
| **app deployment（Control.app entry-point）** | 一致 | 一致 | 可 sim。 |

---

## 3. 真机 UAT-only 验证清单

以下条目是 **mockup-sim 无论如何加 fixture 都不能验证** 的；只能挂在真机 milestone gate 上：

1. 头部到达目标位置的实际时间与抖动
2. 重力补偿在不同头部姿态下的稳态误差
3. 电机扭矩切换（torque on/off）与上电/掉电仪式
4. 电机过载、过热、欠压等硬件错误处理路径
5. PID 增益对响应曲线的影响
6. BMI088 IMU 数据流（仅 wireless 版机器有）
7. 4-mic 阵列 DoA 与远场拾音
8. 真机扬声器（USB 音频）输出音量、底噪、回声
9. 头部摄像头视角、白平衡、动作引起的抖动模糊
10. **视觉-运动闭环**：face tracking 驱动头部转动 → 视野变化 → 反馈 → 再次跟踪。fixture 帧不会随头部姿态变化，这条闭环必须真机验。

---

## 4. 麦克风：开发机 sim 的覆盖度

### 4.1 能覆盖

- ASR 流水线（sherpa-onnx SenseVoice-Small）端到端正确性
- TTS 流水线（Kokoro-82M-zh）端到端正确性
- 对话/意图/状态机逻辑
- 唤醒词（VAD/wake-word 模型）功能正确性
- 录音/重播 fixture 形成的回归集

实现方式：app 内 audio backend 走 sounddevice，对接默认输入设备；daemon 一律 `--deactivate-audio`。

### 4.2 不能覆盖

- DoA（方向角估计）—— 真机 4 麦阵列硬件特性
- 远场拾音 SNR、波束成形、噪声抑制
- 真机扬声器→真机麦克风的回声/啸叫
- 真机麦克风的频响、底噪、AGC 行为

### 4.3 推荐

DoA 与远场相关 feature 标 `gates: ["real_robot"]`；其余麦相关 feature 可在 dev-machine sim 上拿 passing。

---

## 5. 摄像头：用图像/视频 fixture 模拟真机

### 5.1 关键事实

`mockup_sim/backend.py` 源码注释：

> Apps open the webcam/microphone directly (like with a real robot).

也就是说 daemon 完全不管摄像头，app 自己 `cv2.VideoCapture(...)`。这给我们一个干净的抽象点：**只要在 app 这层引入 `CameraSource` 抽象，sim 与真机就能用同一份业务代码。**

### 5.2 三档 fixture 策略

| 档 | 做法 | 适用场景 |
|---|---|---|
| **A. 静态图片循环** | 单张 jpg/png 反复出帧 | 人脸检测、有人/无人、表情识别静态测试 |
| **B. 录制视频回放** | mp4 文件按真实帧率播放 | 走近/离开、挥手、转头等时序动作 |
| **C. 合成视频** | 用 OpenCV/Pillow 合成已知 ground truth 的片段 | 边缘/对抗用例、快速生成回归集 |

### 5.3 `CameraSource` Protocol（草案）

```python
# coco/perception/camera_source.py
from typing import Protocol
import numpy as np

class CameraSource(Protocol):
    def read(self) -> tuple[bool, np.ndarray]: ...
    def release(self) -> None: ...

class ImageLoopSource:
    """A 档：单图循环。"""

class VideoFileSource:
    """B/C 档：mp4/合成视频。"""

class UsbCameraSource:
    """真机：cv2.VideoCapture(0) 或头部摄像头索引。"""
```

通过环境变量或配置选择实现：

- `COCO_CAMERA=image:tests/fixtures/vision/single_face.jpg`
- `COCO_CAMERA=video:tests/fixtures/vision/user_walks_away.mp4`
- `COCO_CAMERA=usb:0`（默认真机）

### 5.4 Fixture 目录建议

```
tests/fixtures/vision/
├── single_face.jpg          # 一个人正脸
├── no_one.jpg                # 空场景
├── two_faces.jpg             # 多人
├── face_far.jpg              # 远距离小脸
├── user_walks_away.mp4       # 走出画面
├── user_approaches.mp4       # 走近
└── synthetic_lighting.mp4    # 合成：光照变化
```

### 5.5 视觉任务覆盖矩阵

| 视觉任务 | 静态图 (A) | 录制视频 (B) | 合成视频 (C) | 真机 UAT |
|---|---|---|---|---|
| 有/无人检测 | ✅ | ✅ | ✅ | gate |
| 人脸检测/计数 | ✅ | ✅ | ✅ | gate |
| 表情/年龄/性别 | ✅ | ✅ | – | gate |
| 走近/远离时序 | – | ✅ | ✅ | gate |
| 挥手/手势 | – | ✅ | – | gate |
| **face tracking → 头部转动 → 视野更新** | ❌ | ❌ | ❌ | **must-real** |
| 头部运动模糊 | ❌ | 仅录制时含 | – | **must-real** |
| 真机白平衡/曝光 | ❌ | ❌ | ❌ | **must-real** |

**核心 caveat**：fixture 帧不随头部姿态变化，所以"看到人 → 转头 → 看到人新位置"这个**闭环行为**只能真机验。sim 阶段可以验"看到人 → 发出转头指令"和"收到转头确认 → 状态机推进"，但中间那一段视野变化是断的。

---

## 6. 对仓库的建议

### 6.1 `feature_list.json` schema 演进

为每个 feature 增加 `gates` 字段：

```json
{
  "id": "interact-001",
  "gates": ["sim", "real_robot"],
  "verification": {
    "sim": "...",
    "real_robot": "..."
  }
}
```

- `gates: ["sim"]` → 拿 sim evidence 即可 passing
- `gates: ["sim", "real_robot"]` → sim 通过先标 `passing_sim`，真机通过后再升 `passing`
- `gates: ["real_robot"]` → 必须真机（如 IMU、DoA、扭矩相关）

这一步当前 milestone 不强求落地；提议放进 phase-2 之前的 schema 升级。

### 6.2 `CLAUDE.md` 子系统边界补充

在 robot 子系统说明里追加一行：

> mockup-sim 不实例化摄像头；app 通过 `coco.perception.CameraSource` 抽象在 sim 用 fixture（图片/视频），真机用 USB 摄像头。视觉-运动闭环必须真机 UAT。

### 6.3 应用层抽象先于 perception feature

在 `feature_list.json` 里有 `interact-001` / `companion-001` 涉及视觉前，先做一个小 infra-feature：

> **infra-vision-source**：引入 `CameraSource` Protocol + `ImageLoopSource` / `VideoFileSource` / `UsbCameraSource` 三实现 + `COCO_CAMERA` 环境变量切换 + 一组基础 fixture。

这样后续 perception/interact 类 feature 可以直接在 sim 拿 evidence，不用每个 feature 各搞一套 mock。

---

## 7. 一句话总结

> **运动学/对话/视觉感知逻辑可以在 sim+fixture 下迭代到 90%；电机物理、IMU、远场麦阵列、视觉-运动闭环这四类必须真机。`CameraSource` 抽象 + `gates` 字段是把这条线明确画出来的最小代价。**
