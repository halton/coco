# 测试音频素材

为 audio-002（ASR）等 feature 的 verification 步骤准备的标注 wav。
路径不变是 verification 可重复的前提；改名要同步改 feature_list.json。

## 命名规范

`<lang>-<id>-<short-desc>.wav`

- `lang`：`zh` / `en`
- `id`：3 位数字，新增递增
- `short-desc`：英文短描述，kebab-case

## 录制规范

- 采样率：16000 Hz（与运行时一致，避免 resample 影响）
- 通道：mono
- 编码：PCM 16-bit
- 时长：3–10 秒
- 内容：日常对话 / 学习场景常用句

录制命令示例（macOS / Linux）：

```bash
.venv/bin/python -c "
import sounddevice as sd, scipy.io.wavfile as wav
sr = 16000
print('录 5 秒...')
rec = sd.rec(int(sr * 5), samplerate=sr, channels=1, dtype='int16')
sd.wait()
wav.write('zh-001-hello.wav', sr, rec)
print('done')
"
```

（运行时需要 `scipy`。当前 `pyproject.toml` 未直接声明，但 reachy-mini 传递依赖里有，能用。）

## 标注

每个 wav 在同名 `.txt` 里写人工转写文本（UTF-8，无 BOM）。

```
zh-001-hello.wav    -> zh-001-hello.txt: "你好，今天我们学英语"
```

## 当前清单

| 文件 | 语言 | 时长 | 内容 | 用于 |
|---|---|---|---|---|
| `zh-001-walk-park.wav` | zh | ~? | 见 `.txt` | audio-002 |
| `wake_keke.wav` | zh | ~2.91s | 可可，今天天气真好 | interact-005 KWS |
| `wake_keke_short.wav` | zh | ~0.95s | 可可 | interact-005 KWS |

### Wake Word fixture (interact-005)
- `wake_keke.wav` — TTS 合成 "可可，今天天气真好"，~2.91s
- `wake_keke_short.wav` — TTS 合成 "可可"，~0.95s
- 用于 KWS 单元验证；known-debt: 真人音色与多语速样本留 milestone gate 真机录音
