# 模拟器与音频能力调研

**日期**：2026-05-08
**状态**：初步调研，仍有未验证项

## 关键发现

### 模拟器实际位置
- 不是独立的 `reachy-mini-control` 包
- 是 `reachy-mini` 包（v1.4.0）内置的 backend
- 路径：`reachy_mini/daemon/backend/`
- 三个后端可选：
  - `mockup_sim` — 轻量动作仿真（无物理）
  - `mujoco` — MuJoCo 物理仿真
  - `robot` — 真机（串口）

### 音频能力（基类 API 已定义）

文件：`reachy_mini/media/audio_base.py`

输出：
- `play_sound(sound_file)` ✅
- `push_audio_sample(data)` ✅（可推 TTS 流）
- `clear_output_buffer()`、`set_max_output_buffers()`

输入：
- `start_recording()` / `stop_recording()` ✅ API 存在
- `get_audio_sample()` → `np.float32` ✅
- `get_input_audio_samplerate()`、`get_input_channels()`
- `get_DoA()` → 声源定位（角度 + 是否有效）

实现后端：
- `audio_gstreamer.py`（默认 `MediaBackend.GSTREAMER_NO_VIDEO`）
- `audio_sounddevice.py`（备选）

## 未验证项（需 5 行代码 spike）

1. **mockup_sim 后端下，录音 API 是否可用？**
   - 推测：模拟器不模拟麦克风，直接走主机系统麦克（sounddevice/gstreamer 拿真实输入）
   - 验证方式：跑 mockup_sim，调 `start_recording() + get_audio_sample()`，看是否拿到 mac 的麦克数据
2. **能否用 wav 文件代替麦克风输入**（用于 CI/自动化测试）
   - 需要看 audio backend 是否支持文件源
   - 备选方案：在测试中直接调 ASR，跳过 audio backend

## 对开发策略的影响

### 当 mockup_sim 走真实麦克（推测大概率成立）
- ✅ 本地开发可用：戴耳机/对着 mac 麦克即可调试
- ❌ CI/agent 自动测试不能用麦克风
- 解决方案：测试层绕开 audio backend，直接喂 wav → ASR → LLM → TTS（验证逻辑链路），audio I/O 单独做"集成 smoke test"

### 自治深度建议
- **动作 + 状态机层**：mockup_sim 完全自治验证（agent 可写可测）
- **语音逻辑层**：用录音文件单元测试，agent 自治
- **真机 audio I/O 集成**：你手动验收

## 摄像头
- ❌ 模拟器无摄像头模拟
- 摄像头相关功能（如视觉感知专注度）必须真机，列入 v2

## 参考代码位置
- Backend 基类：`.venv/lib/python3.13/site-packages/reachy_mini/daemon/backend/abstract.py`
- Mockup sim：`.venv/lib/python3.13/site-packages/reachy_mini/daemon/backend/mockup_sim/`
- Audio 基类：`.venv/lib/python3.13/site-packages/reachy_mini/media/audio_base.py`
- Media Manager：`.venv/lib/python3.13/site-packages/reachy_mini/media/media_manager.py`
