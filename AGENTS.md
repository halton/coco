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

## 主会话编排模式（硬规则）

主会话是**编排者**，不是干活的人。目的：把主 context 压力降下来，避免一两轮就 80%+ 触发压缩或被迫 checkpoint。该规则与下文「角色」段配套：Engineer 实现 / Researcher 调研 / Reviewer 评审 全部在 sub-agent 独立 context 跑，主会话只做综合与决策。

**主会话只做这几件事**：

- 读用户意图、拆任务、决定派给哪个角色 sub-agent
- 把 sub-agent 返回的结构化结果综合成下一步决策
- 决定 commit / push / feature 状态切换（`in_progress` ↔ `passing` ↔ `blocked`）
- 跨子系统协调（audio / robot / app 边界判断）
- 与用户对话、确认完成门槛

**一律派 sub-agent 做的工作**（用 Task 工具 delegate，`subagent_type` 选合适角色，general-purpose 也可；中文沟通；brief 必含目标 + 已知上下文摘要 + 期望返回格式；sub-agent 返回结构化摘要，主会话**不重读** sub-agent 已经读过的文件）：

- 所有多文件读、跨文件综合分析
- 所有实现编辑（代码 / 文档大段改动）→ Engineer sub-agent
- 所有 bash 验证 / 多步脚本（`./init.sh`、smoke、daemon 起停等）
- 所有调研（SDK 行为、选型、踩坑历史）→ Researcher
- 所有 fresh-context 评审 → Reviewer（本来就是硬规则，见下文）
- Robot UAT 真机动作由用户执行，主会话不代办

**例外（主会话可直接做的 trivial 单点操作）**：

- 改一行配置 / 单个 typo
- 看一个短文件确认一个事实
- 单条 git 命令（status / log / commit / push）
- 单次 `feature_list.json` 状态字段更新

**累计阈值**：连续 3 个 trivial 操作之后，下一个不论大小一律派 sub-agent，强制刷新 context 卫生。

**不允许的反模式**：主会话连读多个文件做综合、主会话 bash 跑多步验证、主会场亲自做实现编辑。这些都是 sub-agent 的活，被发现需在 `claude-progress.md` 里记一笔流程违规。

## Git 工作流

- 每个 in_progress feature 在分支 `feat/<feature-id>` 上做（如 `feat/robot-001`）
- feature passing 后 merge 回 main，分支删除
- main 永远保持 `./init.sh` 通过的状态
- 例外：harness 加固、文档、依赖升级等基础设施改动可直接在 main 做（短促、低风险）
- **commit 例外**：本仓库的 `git commit` 一律由 sub-agent 直接执行，主会话不再向用户确认草稿（覆盖全局 `~/.claude/memory/git-conventions.md` 中"commit 前用户确认"默认）。仍须遵守：Co-Authored-By 行、conventional commit 格式。
- **push 策略——默认只 commit 不 push**：sub-agent 在 closeout 中完成 `commit` + `merge --no-ff` 到 main 后即停，**不再自动 `git push origin main`、不 push feat 分支**。push 改为用户在合适时机统一发起，或仅在用户显式发出 "push" 指令时执行。该规则覆盖此前 "closeout 自动 push origin main + feat/xxx、失败 3 轮重试 sleep 30s" 的行为。push 命令模板（仅按需）：`git push origin main` / `git push origin feat/<feature-id>`。

## 依赖升级策略

- 不主动升核心 SDK（`reachy-mini`、`reachy-mini-motor-controller`、`reachy-mini-rust-kinematics`）
- 必须升级时单独立 feature（`infra-NNN` 或 `dep-upgrade-NNN`）：
  1. 跑全量 smoke + 当前 in_progress feature 的 verification
  2. 把"已知通过组合"记到 `claude-progress.md` 决策导航的"环境基线"
  3. 任何因升级引入的 breaking change 必须在 evidence 中记录处理方式
- 例：`reachy-mini` 1.4 → 1.7 引入 `gstreamer-bundle` 硬依赖，需要 `[tool.uv] required-environments` 显式列出三平台

## 子系统约定

- **audio 子系统**：输入直连本机麦克、输出走本机默认输出设备，全部通过 sounddevice，不通过 reachy-mini daemon 的 audio backend / media 子系统。测试：输入用 wav 文件直喂 ASR；输出把 TTS 合成的 wav 用 sounddevice 播放。跨平台。真机扬声器（Reachy Mini USB 音频）作**异步 UAT 项**（见下文 Sim-First 原则），不阻 merge。
- **robot 子系统**：通过 `ReachyMini` + Zenoh + mockup-sim daemon 验动作。reachy-mini Lite SDK 提供 macOS / Linux / Windows 的 cp313 wheel，开发跨平台；真机硬件相关功能（USB 音频等）可能仍受限。真机验收作**异步 UAT 项**（见下文 Sim-First 原则），不阻 merge。
- 两个子系统独立，仅在应用层汇合。详见 `research/spike-audio-attempt.md`。

## Sim-First 开发原则（默认启用）

本规则**覆盖**先前 AGENTS.md / CLAUDE.md 中任何"真机 UAT 作为 milestone gate 阻塞 phase 推进"或"phase 末停下等真机 UAT"的说法。与 `CLAUDE.md` 同名段语义一致，更新时同步改。

1. **默认 sim-first**：所有 feature 的开发、verification、Reviewer 评审、close-out、merge 一律在 sim / mockup-sim / fake / fixture 环境下完成。`./init.sh` smoke + 该 feature 的 `scripts/verify_*.py` 全 PASS（含 Reviewer fresh-context LGTM）即可将 status 切到 `passing` 并 merge 回 main。
2. **真机 UAT 不阻塞 phase 推进**：phase 内所有 sim-feature 走完后立即继续下一 phase 规划与执行。
3. **真机 UAT 是显式 milestone gate，但异步**：真机验收单独立项为 `uat-*` feature，或在相关 feature 的 evidence 中加 `real_machine_uat: pending` 字段，由用户在方便时执行；结果回填 evidence，不阻断软件迭代。
4. **以下能力 sim 不可证明，最终需真机确认**（仅作记录，不阻 merge）：真扬声器 TTS 听感 / USB 音频；真麦克风 ASR/VAD 信噪比；真摄像头光照下 face_id 区分力；Reachy Mini 真硬件电机 / 头部姿态 / goto_sleep；视觉-运动闭环（看到 → 转头 → 视野更新）。
5. 主会话不得以"等真机 UAT"为由停下持续开发模式。

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
