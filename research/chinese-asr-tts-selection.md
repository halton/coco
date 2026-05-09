# 中文 ASR / TTS 本地优先选型

**日期**：2026-05-08
**Session**：005
**触发问题**：Coco / 可可 是中文学习伴侣机器人，语音必须支持中文（普通话）。原 `audio-002` 把 whisper.cpp / faster-whisper / 云端混在一起作为候选，没有把"中文"钉为硬约束，且未涉及 TTS。本文档落实选型决策与依据。

## 结论（写进 `claude-progress.md` 决策导航）

| 组件 | 主选 | 兜底 |
|---|---|---|
| **统一 runtime** | sherpa-onnx（Apache-2.0；C++/Python；mac/Linux/Windows ARM/x86 wheel；NEON 加速） | — |
| **ASR** | SenseVoice-Small INT8（60MB，CER 3-5%，~70ms / 10s 音频，Apache-2.0） | Paraformer-zh-small（30MB，更轻） |
| **TTS** | Kokoro-82M-zh（82MB，MOS ~4.2，Apache-2.0，CPU 友好，固定角色不需 voice cloning） | edge-tts（微软在线，免费，联网时使用，**不强制依赖网络**） |

## 候选对比

### ASR（10s 中文音频，AISHELL-1 量级 CER）

| 模型 | CER (%) | 大小 | CPU 延迟 | License |
|---|---|---|---|---|
| **SenseVoice-Small INT8** | ~3-5 | 60MB | **~70ms** | Apache-2.0 |
| Paraformer-zh | ~2-5 | 30-600MB | ~120ms | Apache-2.0 |
| Whisper-large-v3 | ~5-6 | ~3GB | ~1050ms | MIT |
| faster-whisper | 同 Whisper | 同 | ~250ms | MIT |
| sherpa-onnx Zipformer-zh | ~4 | 14MB | 极快 | Apache-2.0 |
| Whisper-tiny | >10 | 75MB | 快 | MIT |

**选 SenseVoice-Small 的理由**：
- 中文 CER 接近 Paraformer，但比 Whisper 系列体积小一个数量级
- 非自回归架构 → 端到端延迟最低
- sherpa-onnx 直接支持，跨三平台 wheel 完整
- 自带情感/事件检测（对学习伴侣场景潜在有用，但非 MVP 需要）

### TTS（中文 MOS / 体积 / 首音延迟）

| 模型 | MOS | 大小 | 首音延迟 | Voice Cloning | License |
|---|---|---|---|---|---|
| **Kokoro-82M-v1.1-zh** | ~4.2 | **82MB** | ~100ms | 否 | Apache-2.0 |
| edge-tts（云端） | ~4.2 | N/A | ~300ms | 否 | 免费（MS） |
| CosyVoice 2 | ~4.0 | ~1GB | ~150ms | 是 | Apache-2.0 |
| GPT-SoVITS v4 | ~4.0 | ~500MB | ~200ms | 是 | MIT |
| ChatTTS | ~4.0 | ~300MB | ~150ms | 否 | Apache-2.0 |
| F5-TTS | ~4.1 | ~1.6GB | RTF 0.15 | 是 | MIT |

**选 Kokoro-82M-zh 的理由**：
- 体积最小（82MB），CPU 跑得动，对 Reachy Mini 的算力友好
- MOS 与 edge-tts 持平，质量足够日常对话
- Coco 是固定角色（不需要 voice cloning），CosyVoice / GPT-SoVITS / F5-TTS 的 cloning 能力是浪费
- Apache-2.0，商用友好

**edge-tts 仅作联网兜底**：
- 免费、无本地算力、质量稳定
- 但**不能作强依赖**——离线时必须能跑（Coco 是桌面伴侣，可能脱网）
- 实际使用：联网时优先 edge-tts 提质，离线时降级到 Kokoro

## Reachy Mini 适配点

- sherpa-onnx 在 Apple Silicon 用 NEON 加速（M1 上比 x86 CPU 快 1.5-2×），与 macOS arm64 主开发环境对齐
- Python wheel 三平台齐全（mac/Linux/Windows），与 `pyproject.toml` 的 `[tool.uv] required-environments` 对齐
- 同一 runtime 同时跑 ASR 和 TTS（Kokoro 也能在 sherpa-onnx 里跑），少一份依赖、少一份学习成本
- 与现有 audio 解耦决策一致：sounddevice 采麦 / 播放，sherpa-onnx 仅做模型推理，互不影响

## 与 feature 的对应关系

| feature | 用到的本文档结论 |
|---|---|
| `audio-002` | ASR = SenseVoice-Small INT8 via sherpa-onnx；CER 阈值（wav <0.1 / 麦克 <0.15） |
| `audio-003` | TTS = Kokoro-82M-zh via sherpa-onnx + edge-tts 联网兜底；真机扬声器 milestone gate |
| `interact-001` | 闭环用上述两者 + robot-002 |

## 留给后续的不确定性

1. **CER 阈值需实测校准**：官方 benchmark 是干净数据集，Coco 真机麦克 + 室内噪声未必能稳定到 < 0.1。`audio-002` verification 已分场景设阈值（wav < 0.1，麦克 < 0.15），首次实测后可能需要再调整。
2. **edge-tts 网络稳定性**：跨地区使用的 latency 与可用性未实测，离线降级路径必须真正能跑。
3. **真机扬声器**：Reachy Mini 的 USB 音频在 Linux/Windows 真机上的 sounddevice 兼容性未验，UAT 阶段才会暴露。
4. **sherpa-onnx 版本与 reachy-mini 的依赖兼容**：`audio-002` 引入时需在 `pyproject.toml` resolve 时验一次三平台 wheel 都能装上。

## 数据来源

- 腾讯云《国内外主流 ASR 开源模型对比》
- CSDN《2025 中文 ASR 选型指南》
- CodeSOTA《TTS Models Guide 2026》
- 技术栈《2026 主流中文 TTS 对比》
- sherpa-onnx GitHub（k2-fsa/sherpa-onnx）
- Kokoro Hugging Face（hexgrad/Kokoro-82M）
- SenseVoice ModelScope（iic/SenseVoiceSmall）
