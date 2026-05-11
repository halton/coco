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

## 持续开发模式（默认启用）

用户启动会话或下达 phase 任务后，主会话默认进入"持续开发模式"：

- 完成一个 feature 的 close-out 后**不询问用户**，直接派下一个 candidate（按 `feature_list.json` 中 priority 最低数字的 `not_started`）
- phase 内所有 in-flight feature 走完后，自动进入下一 phase 规划（候选写入 `feature_list.json`），立即开始执行第一个
- 仅在以下情况停下并等用户输入：
  - (a) 用户已显式发出 "暂停" / "停" / "wait" / "hold" 指令
  - (b) sub-agent 多次 socket / 网络失败无法恢复且需要外部协助
  - (c) 决策本身需要用户偏好（例如 phase-N+1 候选间二选一且无客观依据）
- 注意：**真机 UAT 不再是阻塞 gate**，详见下文「Sim-First 开发原则」。
- 每个 feature close-out 后用一行回复说"[feature-id] DONE，main HEAD=xxx，继续 [next-feature]"，不再追加 "等通知" 或问句
- 该规则覆盖全局 "ask user before commit/push" 默认（与本仓库现有 commit/push 例外一致）

## Sim-First 开发原则（默认启用）

本规则**覆盖**先前 CLAUDE.md / AGENTS.md 中任何"phase-N 末停下等真机 UAT"或"真机 UAT 作为 milestone gate 阻塞 phase 推进"的说法。

1. **默认 sim-first**：所有 feature 的开发、verification、Reviewer 评审、close-out、merge 一律在 sim / mockup-sim / fake / fixture 环境下完成。`./init.sh` smoke + 该 feature 的 `scripts/verify_*.py` 全 PASS（含 Reviewer fresh-context LGTM）即可将 status 切到 `passing` 并 merge 回 main。
2. **真机 UAT 不阻塞 phase 推进**：phase 内所有 sim-feature 走完后**立即继续下一 phase 规划与执行**，不再停下等真机操作。
3. **真机 UAT 是显式 milestone gate，但异步**：真机验收单独立项为 `uat-*` feature（或在相关 feature 的 evidence 中加 `real_machine_uat: pending` 字段），由用户在方便时物理执行；执行结果回填 evidence，不阻断软件迭代。
4. **以下能力 sim 不可证明，最终需真机确认**（仅作记录，不阻 merge）：
   - 真扬声器 TTS 听感 / USB 音频通路
   - 真麦克风 ASR / VAD 在实际信噪比下的鲁棒性
   - 真摄像头在实际光照下 face_id 的区分力与误判率
   - Reachy Mini 真硬件电机扭矩 / 头部姿态 / goto_sleep 物理表现
   - 视觉-运动闭环（看到 → 转头 → 视野更新）的整体延迟与抖动
5. 主会话不得以"等真机 UAT"为由停下持续开发模式；遇到 sim 已通过、真机未跑的情况，直接登记 `uat-*` 异步项，主线推进。

## 规则

- 同一时间只能有一个 active feature（`in_progress`）
- 没有可运行证据时，不要声称完成
- 不要通过重写功能清单来隐藏未完成工作
- 不要为了"看起来完成"而删除或削弱测试
- 以仓库内文件作为唯一事实来源
- 中文沟通；遵守 `~/.claude/memory/git-conventions.md`，但 commit/push 例外见下文 Git 工作流
- Control.app daemon 处理：当 Control.app 自带的 reachy-mini daemon 占用 8000/7447 端口阻塞 `./init.sh --daemon` 或 verification 时，sub-agent 可直接 kill 该进程（`pgrep -f 'desktop-app-daemon'`），不需要再向用户确认。

## Git 工作流

- **本项目 commit / push 例外**：本仓库的 `git commit` 与 `git push` 一律由 sub-agent 直接执行，主会话**不再向用户确认草稿**。此条**覆盖**全局 `~/.claude/memory/git-conventions.md` 中"commit 前用户确认"的默认规则。仍须遵守：Co-Authored-By 行、conventional commit 格式、push 到 `origin`（若区分 fork 与 upstream，则只 push 到 `origin`，不直推 upstream）。
- 每个 in_progress feature 在 `feat/<feature-id>` 分支上做；passing 后 merge 回 main
- main 永远保持 `./init.sh` 通过的状态
- 例外：harness 加固、文档、依赖升级等基础设施改动可直接在 main 做（短促、低风险）

## 依赖升级策略

- 不主动升核心 SDK（`reachy-mini` 等）；必须升级时单独立 feature
- 升级后跑全量 smoke + 当前 in_progress 的 verification，把"已知通过组合"记到 `claude-progress.md` 的环境基线

## 子系统边界

- **audio**：sounddevice 直连本机麦克与扬声器（输入采麦、输出播 TTS wav），不走 reachy-mini daemon 的 audio backend / media 子系统。跨平台。测试：输入 wav 直喂 ASR，输出 TTS wav 用 sounddevice 播放。真机扬声器（USB 音频）作异步 UAT 项（不阻 merge，见 Sim-First 段）。
- **robot**：ReachyMini + Zenoh + `--mockup-sim` daemon。reachy-mini Lite SDK 跨平台（mac / Linux / Windows，cp313 wheel）；真机硬件相关功能可能仍受限。真机验收作异步 UAT 项（不阻 merge，见 Sim-First 段）
- **vision**：业务层一律走 `coco.perception.open_camera()` / `CameraSource` Protocol，不直接 `cv2.VideoCapture`。通过 `COCO_CAMERA` 环境变量切换三档：`image:<jpg>`（A：单图循环）/ `video:<mp4>`（B/C：视频文件循环）/ `usb:<idx>`（真机，默认 `usb:0`）。fixture 在 `tests/fixtures/vision/` 下，全部程序合成。视觉-运动闭环（看到 → 转头 → 视野更新）fixture 不能 sim，必须真机 UAT。详见 `coco/perception/camera_source.py` 与 `tests/fixtures/vision/README.md`。
- 三路独立，应用层汇合。背景见 `research/spike-audio-attempt.md`

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
