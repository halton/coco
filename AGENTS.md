# AGENTS.md

本仓库（Coco / 可可，基于 Reachy Mini 的学习伴侣机器人）面向长时运行的 coding agent 工作流。目标不是尽可能快地产出代码，而是让每一轮会话结束后，下一个会话仍然能无猜测地继续工作。

> 给 Claude Code 的同等内容见 `CLAUDE.md`。两份文件内容等价，更新时同步改。

## 开工流程

写代码前先做这些事：

1. 用 `pwd`（Windows: `Get-Location`）确认当前在 repo 根目录（含 `pyproject.toml` 与 `feature_list.json` 的目录）。
2. 读取 `claude-progress.md`，了解最新已验证状态和下一步。
3. 读取 `feature_list.json`，选择优先级最高的未完成功能。
4. 用 `git log --oneline -5` 看最近提交。
5. 运行 `./init.sh`（Windows: `.\init.ps1`）。它会跑 `uv sync` + `scripts/smoke.py`。
6. 在开始新功能前，确认 smoke 通过；mockup-sim 类功能在工作前用 `./init.sh --daemon` 单独验 Zenoh 通路。

如果基础验证一开始就失败，先修基础状态，不要在坏的起点上继续叠新功能。

## 工作规则

- 一次只做一个功能（`feature_list.json` 中只能有一个 `in_progress`）。
- 不要因为"代码已经写了"就把功能标记为完成。
- 除非为了消除当前 blocker 的窄范围修复，否则不要扩大到其他功能。
- 实现过程中不要悄悄改弱验证规则。
- 优先依赖仓库里的持久化文件，而不是聊天记录。
- 中文沟通；commit 信息可英文可中文，但要遵守 `~/.claude/memory/git-conventions.md`（用户先确认才提交）。

## Git 工作流

- 每个 in_progress feature 在分支 `feat/<feature-id>` 上做（如 `feat/robot-001`）
- feature passing 后 merge 回 main，分支删除
- main 永远保持 `./init.sh` 通过的状态
- 例外：harness 加固、文档、依赖升级等基础设施改动可直接在 main 做（短促、低风险）

## 依赖升级策略

- 不主动升核心 SDK（`reachy-mini`、`reachy-mini-motor-controller`、`reachy-mini-rust-kinematics`）
- 必须升级时单独立 feature（`infra-NNN` 或 `dep-upgrade-NNN`）：
  1. 跑全量 smoke + 当前 in_progress feature 的 verification
  2. 把"已知通过组合"记到 `claude-progress.md` 决策导航的"环境基线"
  3. 任何因升级引入的 breaking change 必须在 evidence 中记录处理方式
- 例：`reachy-mini` 1.4 → 1.7 引入 `gstreamer-bundle` 硬依赖，需要 `[tool.uv] required-environments` 显式列出三平台

## 子系统约定

- **audio 子系统**：输入直连本机麦克、输出走本机默认输出设备，全部通过 sounddevice，不通过 reachy-mini daemon 的 audio backend / media 子系统。测试：输入用 wav 文件直喂 ASR；输出把 TTS 合成的 wav 用 sounddevice 播放。跨平台。真机扬声器（Reachy Mini USB 音频）作 milestone gate，不卡住开发期模拟。
- **robot 子系统**：通过 `ReachyMini` + Zenoh + mockup-sim daemon 验动作。reachy-mini Lite SDK 提供 macOS / Linux / Windows 的 cp313 wheel，开发跨平台；真机硬件相关功能（USB 音频等）可能仍受限。真机验收作为 milestone gate，不卡住模拟开发。
- 两个子系统独立，仅在应用层汇合。详见 `research/spike-audio-attempt.md`。

### app 部署模型（路线 C：双模式）

- Coco 是 `ReachyMiniApp` 子类（继承自 `reachy_mini.ReachyMiniApp`），`pyproject.toml` 声明 `[project.entry-points."reachy_mini_apps"]`
- **开发模式**：`python -m coco`（绕开 Control.app，快速迭代）
- **UAT / 发布模式**：`reachy_mini.apps.app publish` 上 HF Space → Control.app 启动；真机验收必须走此模式
- 在 ReachyMiniApp 框架下需保持 audio 解耦：app 类设 `request_media_backend` 为不依赖 reachy-mini media 的值，主循环里只用 sounddevice
- 详见 `research/control-app-deployment-research.md`

## 角色（multi-role harness）

本仓库定义 4 个角色，每个 feature 按 area 自动决定上场组合。**Reviewer 与 Researcher 必须由独立 context 的 sub-agent 执行**（fresh-context 评审，避免主 context loaded 时的自审盲点）；Engineer 是主 context；Robot UAT 是物理动作（mockup-sim 或真机），动作本身就是 fresh evidence。

| 角色 | 视角 | 实现机制 | 典型产物 |
|---|---|---|---|
| **Engineer** | 实现、跨平台兼容、可维护性 | 主 context | 代码、PR |
| **Researcher** | 选型、SDK 行为、过往坑、技术不确定性消除 | sub-agent（独立 context） | `research/*.md` |
| **Reviewer** | 对照 verification 字段挑刺、抓 loaded-context 盲点 | sub-agent（独立 context） | `evidence` 中的 review verdict + findings |
| **Robot UAT** | mockup-sim 或真机的实际行为 | 物理动作（在 daemon / Control.app 上跑） | log、joint 状态、wav 录音 |

### 触发规则（按 feature `area`）

| area | 默认上场 |
|---|---|
| `infra` | Engineer + Researcher |
| `audio` | Engineer + Researcher（选型阶段）+ Reviewer |
| `robot` | Engineer + Robot UAT + Reviewer |
| `companion` / `interact` | Engineer + Robot UAT + Reviewer（全员，闭环 feature） |

特殊情况：feature 可在 `roles` 字段显式覆盖默认（保留扩展位，目前不强制）。

### 硬规则（不破例）

- 任何 feature 从 `in_progress` → `passing` 之前，**必须**经过一次 Reviewer sub-agent fresh-context 评审，evidence 中包含 `Reviewer (sub-agent): LGTM | LGTM with findings | Block + 关键 findings 摘要 + sub-agent run 链接或 transcript 摘要`
- 文档 / harness 加固类改动也走同样规则（豁免会让仪式失去意义）
- Reviewer 不能是主 context 自审（"换帽子"伪 fresh-context）——必须 Task tool delegate 独立 context

### Reviewer 材料包（每次 delegate 时显式传入）

- `feature_id`
- 该 feature 完整 verification 字段
- 待 review 的文件路径列表（git diff 范围）
- evidence 候选列表
- 上一轮 Reviewer 反馈（如果是迭代 review）
- 用户原始诉求（防止 Reviewer 只对照 verification 而忽略 user-visible 目标）

材料包不全时，Reviewer 会要求补——而不是凭推测下结论。

### 为什么这样设计

参见 commit `ac43436`：今天首次 Reviewer dry-run 抓到的关键盲点是 `audio-003` 的 edge-tts 在 notes 写"不联网必须能跑"，但 verification 第 4 条又要求"edge-tts 一次合成"——主 context 因为深度参与决策，loaded 状态下看不见这个语义滑坡。这是 fresh-context sub-agent 不可替代的证据。

## 增强工具（按需）

- **memex**：开工时若任务触及过往踩过的坑（环境、依赖、SDK 行为），先 `memex-recall`；完成有方法论价值的工作后用 `memex-retro` 沉淀卡片
- **opc**：feature 进入 in_progress 前若设计有争议（多种合理路径、跨子系统决策），跑 `/opc <task>` 走多角色独立评估；不强制
- **logex**：milestone 完成后可选，把 session transcript 转成博客文章（暂缓）

## 必需文件

- `feature_list.json`：功能状态的唯一事实来源
- `claude-progress.md`：会话进度和当前已验证状态
- `init.sh`：统一的启动与验证入口
- `session-handoff.md`：较长会话可选的交接摘要
- `BACKLOG.md`：暂缓场景与未来候选（不进 feature_list 直到激活）

## 完成定义

一个功能只有在以下条件都满足时才算 `passing`：

- 目标行为已经实现
- 要求的验证真的跑过
- 证据记录在 `feature_list.json` 的 `evidence` 字段（可链 commit hash、log 片段、或 `research/` 文件路径）
- 仓库仍然能按 `./init.sh` 重新开始工作

## 收尾

结束会话前：

1. 更新 `claude-progress.md`（追加一条 Session 记录）
2. 更新 `feature_list.json`（状态、evidence）
3. 记录仍未解决的风险或 blocker
4. 在工作处于安全状态后，按用户确认流程提交（不要自动 commit）
5. 保证下一轮会话可以直接运行 `./init.sh`
