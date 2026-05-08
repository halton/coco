# Coco / 可可

> 一个会陪你一起好奇、一起进步的小伙伴。
> 基于 [Reachy Mini Lite](https://www.pollen-robotics.com/reachy-mini/) 的开源学习伴侣机器人。

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Made with Reachy Mini](https://img.shields.io/badge/made%20with-Reachy%20Mini-ff6b6b)](https://www.pollen-robotics.com/reachy-mini/)
[![Hugging Face](https://img.shields.io/badge/🤗-Hugging%20Face-yellow)](https://huggingface.co/pollen-robotics)

---

## 为什么是 Coco

> 学习不必严肃。

桌面学习软件给的是"信息"，但学习需要"陪伴"。
手机 App 给的是"通知"，但人需要"在场感"。

**Coco** 想做的不是另一个会说话的智能音箱，而是一个**会侧过头看你、会点头回应你、会安静陪着你**的小伙伴。

它坐在你桌上，比 ChatGPT Voice 多了一个真实的"在场"——头会转向声音的方向，会在你专注时安静、在你休息时调皮地逗你做拉伸。

不教你，**和你一起学**。

---

## MVP 场景：桌面学习搭子

第一阶段聚焦一个最小但完整的体验：

- 🎧 **听得到你**：本机麦克实时拾音、ASR 转写
- 👀 **看得到你**：头部姿态跟随交互节奏（点头、左看、右看）
- 🌬 **陪得住你**：idle 时的微动作循环（呼吸感、偶尔环顾）
- 🗣 **能回应你**：push-to-talk 触发完整的 听 → 转 → 动 闭环

更多场景（语言陪练、番茄钟教练、家庭信息播报员）见 [`BACKLOG.md`](./BACKLOG.md)。

## 为什么选 Reachy Mini

[Reachy Mini](https://www.pollen-robotics.com/reachy-mini/) 是 [Pollen Robotics](https://www.pollen-robotics.com/)（已被 [Hugging Face](https://huggingface.co/) 收购）推出的开源桌面机器人：

- **可负担**：开源 + 自组装，远比工业陪伴机器人门槛低
- **可拓展**：[Lite SDK 跨平台](https://github.com/pollen-robotics/reachy_mini)（macOS / Linux / Windows，cp313 wheel），支持 mockup-sim 模拟器无硬件开发
- **有生态**：可发布到 [Hugging Face Spaces](https://huggingface.co/spaces) 让所有 Reachy Mini 用户一键安装
- **形态对**：头部 + 双天线的拟人化外形，软萌不让人有距离感

Coco 是这个生态里的一个 **app**——通过 Reachy Mini 的 [Control App](https://github.com/pollen-robotics/reachy-mini-desktop-app) 启动，未来计划发布到官方 [App Store](https://huggingface.co/pollen-robotics/reachy-mini-official-app-store)。

---

## 工程方法

本仓库采用 [Harness Engineering](https://walkinglabs.github.io/learn-harness-engineering/zh) 工作流：

- **仓库为唯一事实来源** — [`feature_list.json`](./feature_list.json) 是唯一的功能状态机
- **单功能推进** — 同一时间只允许一个 `in_progress`
- **Evidence 才算 passing** — 每个完成的功能必须有可重复的验证记录
- **跨会话连续** — [`claude-progress.md`](./claude-progress.md) 让 AI agent 能在多个会话间无缝接力

详见 [`AGENTS.md`](./AGENTS.md) / [`CLAUDE.md`](./CLAUDE.md)。

## 快速开始

### 前置

- Python 3.13+
- [uv](https://github.com/astral-sh/uv)
- 本机麦克权限（macOS 会弹窗请求）
- （可选）Reachy Mini Lite 真机；无真机时通过 [mockup-sim](https://github.com/pollen-robotics/reachy_mini) 即可开发完整流程

### 安装

```bash
git clone https://github.com/halton/coco.git
cd coco

# macOS / Linux
./init.sh

# Windows
.\init.ps1
```

`init` 会自动 `uv sync` 并跑一次 audio smoke test，看到非零 RMS 表示麦克正常。

### 跑动作模拟（可选）

```bash
# 关闭 Reachy Mini Control.app（避免 Zenoh 端口冲突）
./init.sh --daemon
```

通过则可在 mockup-sim 中开发头部姿态控制。

---

## 项目状态

🚧 **早期开发** — 当前在搭工程基础设施（harness）。
👉 实时进度看 [`claude-progress.md`](./claude-progress.md)。
👉 当前优先级看 [`feature_list.json`](./feature_list.json)。

## 路线图

- **Phase 1 / MVP** — 桌面学习搭子（场景 1）
- **Phase 2** — 沉浸式语言陪练（场景 2，候选）
- **Phase 3** — 番茄钟教练（场景 3，融入 Phase 1）
- **Phase 4** — 家庭信息播报员（场景 4，候选）

## 贡献 / 反馈

项目在早期阶段，欢迎 issue 讨论。代码贡献请先在 issue 里聊清场景，避免双方做无用功。

## 致谢

- [Pollen Robotics](https://www.pollen-robotics.com/) 提供的 Reachy Mini 硬件与 SDK
- [Hugging Face](https://huggingface.co/) 收购后的开源延续
- [Harness Engineering](https://walkinglabs.github.io/learn-harness-engineering/) 提供的工程方法论

## 命名

**Coco / 可可** —— 一个可爱、亲切、中英文双关的名字。

- **可** 取自"可教、可学、可亲、可爱"，寓意一个能陪你一起学习、值得信赖的小伙伴
- **可可** 的叠字读起来软萌亲切，符合 Reachy Mini 小巧可爱的外形气质
- 也让人联想到温暖的可可饮品 —— 学习时陪在身边的那杯热饮

英文 **Coco** 简短好记，自带俏皮、灵动的气质。

## License

[Apache License 2.0](./LICENSE) — 与 [Reachy Mini SDK](https://github.com/pollen-robotics/reachy_mini) 等核心生态一致。
