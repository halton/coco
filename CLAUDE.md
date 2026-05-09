# CLAUDE.md

你正在 Coco / 可可（基于 Reachy Mini 的学习伴侣机器人）仓库中工作。优先保证可靠完成、跨会话连续性和显式验证，而不是表面上的速度。

## 固定工作循环

每轮会话开始时：

1. 运行 `pwd`（Windows: `Get-Location`），确认在 repo 根目录（含 `pyproject.toml` 与 `feature_list.json`）
2. 读取 `claude-progress.md`
3. 读取 `feature_list.json`
4. 用 `git log --oneline -5` 查看最近提交
5. 运行 `./init.sh`（Windows: `.\init.ps1`）
6. 检查 smoke 是否通过；动 robot 子系统前用 `./init.sh --daemon` 验 mockup-sim daemon 通

然后只选择一个未完成功能（`feature_list.json` 中 priority 最低数字的 `not_started`），围绕它工作，直到它被验证通过，或者被明确记录为 `blocked`。

## 主会话编排模式（硬规则）

主会话是**编排者**，不是干活的人。目的：把主 context 压力降下来，避免一两轮就 80%+ 触发压缩。

**主会话只做这几件事**：

- 读用户意图、拆任务、决定派给谁
- 把 sub-agent 返回的结构化结果综合成下一步决策
- 决定 commit / push / feature 状态切换（`in_progress` ↔ `passing` ↔ `blocked`）
- 跨子系统协调（audio / robot / app 之间的边界判断）
- 与用户对话、确认门槛

**所有任务一律派 sub-agent 执行，不分大小**（用 `Agent` / Task 工具，`subagent_type` 选合适角色，general-purpose 也可；中文沟通；brief 含目标 + 已知上下文摘要 + 期望返回格式；sub-agent 返回结构化摘要，主会话**不重读** sub-agent 已经读过的文件）：

- 所有文件读取（即使是单个短文件、确认一个事实）
- 所有文件编辑（即使是改一行配置 / 单个 typo）
- 所有多文件读、跨文件综合分析
- 所有实现编辑（代码 / 文档）
- 所有 bash 命令执行（包括 `git status` / `git log` / `git commit` / `git push`、`./init.sh`、smoke、daemon 起停等）
- 所有调研（SDK 行为、选型、踩坑历史）→ Researcher
- 所有 fresh-context 评审 → Reviewer
- 所有 `feature_list.json` / `claude-progress.md` 字段更新
- Robot UAT 真机动作由用户执行，主会话不代办

**主会话不直接调用任何执行类工具**（Read / Write / Edit / Bash / Grep / Glob 等）。需要这些动作时一律派 sub-agent。主会话只用 `Agent` / `AskUserQuestion` / `TaskCreate|Update|List` / `SendMessage` 这类编排工具。

**不允许的反模式**：主会话亲自 Read / Edit / Bash 任何东西。一旦发生即视为流程违规，需在 progress 里记一笔。

## 规则

- 同一时间只能有一个 active feature（`in_progress`）
- 没有可运行证据时，不要声称完成
- 不要通过重写功能清单来隐藏未完成工作
- 不要为了"看起来完成"而删除或削弱测试
- 以仓库内文件作为唯一事实来源
- 中文沟通；遵守 `~/.claude/memory/git-conventions.md`，但 commit/push 例外见下文 Git 工作流

## Git 工作流

- **本项目 commit / push 例外**：本仓库的 `git commit` 与 `git push` 一律由 sub-agent 直接执行，主会话**不再向用户确认草稿**。此条**覆盖**全局 `~/.claude/memory/git-conventions.md` 中"commit 前用户确认"的默认规则。仍须遵守：Co-Authored-By 行、conventional commit 格式、push 到 `origin`（若区分 fork 与 upstream，则只 push 到 `origin`，不直推 upstream）。
- 每个 in_progress feature 在 `feat/<feature-id>` 分支上做；passing 后 merge 回 main
- main 永远保持 `./init.sh` 通过的状态
- 例外：harness 加固、文档、依赖升级等基础设施改动可直接在 main 做（短促、低风险）

## 依赖升级策略

- 不主动升核心 SDK（`reachy-mini` 等）；必须升级时单独立 feature
- 升级后跑全量 smoke + 当前 in_progress 的 verification，把"已知通过组合"记到 `claude-progress.md` 的环境基线

## 子系统边界

- **audio**：sounddevice 直连本机麦克与扬声器（输入采麦、输出播 TTS wav），不走 reachy-mini daemon 的 audio backend / media 子系统。跨平台。测试：输入 wav 直喂 ASR，输出 TTS wav 用 sounddevice 播放。真机扬声器（USB 音频）作 milestone gate。
- **robot**：ReachyMini + Zenoh + `--mockup-sim` daemon。reachy-mini Lite SDK 跨平台（mac / Linux / Windows，cp313 wheel）；真机硬件相关功能可能仍受限。真机验收是 milestone gate
- 两路独立，应用层汇合。背景见 `research/spike-audio-attempt.md`

### app 部署模型（路线 C：双模式）

- Coco 是 `ReachyMiniApp` 子类，`pyproject.toml` 声明 `[project.entry-points."reachy_mini_apps"]`
- 开发：`python -m coco`；UAT/发布：`reachy_mini.apps.app publish` → Control.app 启动
- 在 ReachyMiniApp 框架下保持 audio 解耦（不走 reachy-mini media 子系统）
- 详见 `research/control-app-deployment-research.md`

## 角色（multi-role harness）

本仓库 4 角色，按 feature `area` 自动决定上场组合。详见 `AGENTS.md` 角色段。

| 角色 | 视角 | 机制 |
|---|---|---|
| Engineer | 实现、跨平台、可维护 | 主 context（你/我） |
| Researcher | 选型、SDK、坑、不确定性 | sub-agent（独立 context） |
| Reviewer | 对照 verification 挑刺、抓盲点 | sub-agent（独立 context） |
| Robot UAT | mockup-sim / 真机实际行为 | 物理动作 |

触发：infra → Eng+Res；audio → Eng+Res+Rev；robot → Eng+UAT+Rev；companion/interact → 全员。

**硬规则**：feature `in_progress` → `passing` 前必须一次 Reviewer sub-agent fresh-context 评审；evidence 必须含 `Reviewer (sub-agent): LGTM/findings + 摘要`。Reviewer 不能是主 context 自审。

## 必需文件

- `feature_list.json` — 唯一事实来源
- `claude-progress.md` — 进度日志
- `init.sh` — 启动与验证入口
- `session-handoff.md` — 会话交接（按需）

## 完成门槛

只有在要求的验证成功且 evidence 被记录后，功能状态才可以切换到 `passing`。

## 结束前

1. 更新进度日志（追加 Session 条目）
2. 更新功能状态与 evidence
3. 记录仍然损坏或未验证的内容
4. 在仓库可安全恢复后提交（先获用户确认）
5. 给下一轮会话留下干净的重启路径（`./init.sh` 必须可用）
