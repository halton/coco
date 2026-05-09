# 进度日志

## 关键决策导航

按时间顺序，遇到分歧时回到这里看过去做过什么决定与为什么：

| 决策 | 来源 | 简述 |
|---|---|---|
| MVP 聚焦"桌面学习搭子"（场景 1） | `BACKLOG.md` | 4 个候选场景评估后，1 最能验证用户粘性；2/3/4 暂缓 |
| audio 与 robot 解耦 | `research/spike-audio-attempt.md` | audio 走 sounddevice 直连本机麦克；robot 走 ReachyMini/Zenoh + mockup-sim。两路独立 |
| 测试策略：audio 用 wav 直喂，robot 用 mockup-sim | `research/spike-audio-attempt.md` | 绕开 audio backend 复杂度；模拟器够用就不上真机 |
| 真机验收作为 milestone gate | `AGENTS.md` 子系统约定 | 真机不卡住模拟开发，但每个 milestone 必须真机过 UAT |
| harness engineering 工作流 | `AGENTS.md` / `CLAUDE.md` | 依据 walkinglabs/learn-harness-engineering，仓库为唯一事实来源、单功能推进、evidence 才能 passing |
| 跨平台支持（macOS / Linux / Windows） | `pyproject.toml` / `init.sh` / `init.ps1` | reachy-mini Lite SDK 三平台都有 cp313 wheel；`required-environments` 显式列出三平台 |
| app 部署模型：路线 C 双模式 | `research/control-app-deployment-research.md` | Coco 是 ReachyMiniApp 子类；开发用 `python -m coco`，UAT/发布走 Control.app 经 HF Space。infra-001 是其他 feature 的前置 |
| 中文 ASR/TTS 本地优先选型 | `research/chinese-asr-tts-selection.md` | 统一 runtime 用 sherpa-onnx（跨平台 NEON 加速、ARM 友好）；ASR=SenseVoice-Small INT8（60MB / CER 3-5% / ~70ms）；TTS=Kokoro-82M-zh（82MB / Apache-2.0 / CPU 友好）；edge-tts 作联网兜底（不强制依赖网络） |
| multi-role harness（4 角色 + Reviewer 必走 sub-agent） | `AGENTS.md` 角色段；`feature_list.json` rules.reviewer_required_for_passing | 4 角色 Engineer/Researcher/Reviewer/Robot UAT；按 area 自动组合；硬规则：feature `in_progress` → `passing` 前必须一次 Reviewer sub-agent fresh-context 评审；Reviewer/Researcher 不能在主 context 自审。依据 commit ac43436 fresh-context 抓到的 audio-003 edge-tts 语义滑坡 |
| 撤回 audio-only 解耦定位 | 用户口述（2026-05-09）；`feature_list.json` robot-001 notes | 产品目标含视频检测、双向语音通话、Reachy Mini 全部零件操作，不是 audio-only。spike 期"audio 与 robot 解耦"原文保留作早期路径选择记录，但 smoke 默认 `media_backend='no_media'` 仅作**临时 workaround**（Lite SDK 缺 GStreamer），待 robot-003 视频链路落地后撤回 |

## 连续开发模式（2026-05-09 起生效）

用户已授权"自走完所有 feature"模式，规则如下：

| 项 | 决策 |
|---|---|
| Commit 策略 | **B. 完全放权**：feature passing 后我自行 commit，不再每次问；用户在 PR / milestone 处看 |
| 真机依赖门槛 | mockup-sim 通过即标 `passing`；真机 UAT 单独留 backlog（每个 milestone 末批量做） |
| Reviewer 找出问题 | auto-fix 后重 review，最多 **20 轮**；超过则停下来等用户 |
| "全部完成"定义 | `feature_list.json` 现有 features 全部 `passing` 即完工；真机 UAT 在 milestone backlog 单独跟 |
| 中途发现缺失依赖 | 我自动加新 feature 继续，不停下确认（priority 紧贴当前任务后） |
| Windows 验证 | 暂不要求；`init.ps1` 与 Windows-only verification 不阻断 passing |

**执行守则**：
- 真机 UAT 类 verification 项遇到时，写入 `backlog/real-robot-uat.md`，本 feature evidence 记 "skipped: real-robot only, tracked in backlog/real-robot-uat.md"
- 自动新增的 feature 在 `_change_log` 注明 "auto-added during <feature-id> execution: <reason>"
- 每个 feature 推进开始与结束都更新 `claude-progress.md` 会话记录段
- Reviewer sub-agent 必须用 `Task` 工具 fresh-context 调起，主 context 不自审

## 环境基线

记录"已知此组合下 smoke 通过"。每次依赖升级后更新。

| 时间 | Python | OS | reachy-mini | sounddevice | numpy |
|---|---|---|---|---|---|
| 2026-05-08 | 3.13.12 | Darwin arm64 | 1.4.0 | 0.5.5 | 2.4.4 |

## 当前已验证状态

- 仓库根目录：repo 根（含 `pyproject.toml` 与 `feature_list.json`）；本机本会话路径为 `/Users/halton/work/reachhy-mini`
- 标准启动路径：`./init.sh`（Windows: `.\init.ps1`）
- 标准验证路径：`./init.sh` / `.\init.ps1` + 按 feature 的 `verification` 字段执行
- 当前最高优先级未完成功能：`infra-001`（ReachyMiniApp 双模式骨架）
- 当前 blocker：无

## 会话记录

### Session 001 — 2026-05-08（spike 阶段）

- **本轮目标**：验证 audio 路径选型，决定走 sounddevice 还是 reachy-mini daemon backend。
- **已完成**：
  - 评估 4 个候选场景，决定 MVP 做场景 1
  - 完成 audio spike：sounddevice 直连 mac 麦克验通
  - 决策 audio / robot 子系统解耦
  - 解决环境坑：venv shebang、gstreamer、mockup-sim daemon 启动姿势
- **运行过的验证**：`spike_audio.py`（按 Enter 录 3s）
- **已记录证据**：`research/spike-audio-attempt.md`、`research/simulator-audio-findings.md`
- **提交记录**：`4999f03 feat: spike audio path — decouple audio from robot control`
- **更新过的文件或工件**：spike_audio.py、pyproject.toml、uv.lock、SESSION-BOOTSTRAP.md（即将被 progress.md 取代）
- **已知风险或未解决问题**：robot 子系统通路尚未固化为可重复 smoke
- **下一步最佳动作**：开始 `robot-001` —— 把 mockup-sim daemon ping 固化进 init.sh 的可选模式（已落地，待验证）

### Session 002 — 2026-05-08（harness 落地）

- **本轮目标**：按 harness engineering 方法重构仓库结构，建立 AGENTS.md / CLAUDE.md / init.sh / feature_list.json / claude-progress.md。
- **已完成**：
  - 创建 5 个 harness 核心文件
  - 把 BACKLOG 场景 1 拆成 6 个 features
  - 把已通过的 audio-001 标 passing 并附 evidence
- **运行过的验证**：feature_list.json JSON 合法性已验
- **已记录证据**：本次 commit（待用户确认）
- **提交记录**：（待用户确认后提交）
- **更新过的文件或工件**：AGENTS.md、CLAUDE.md、init.sh、feature_list.json、claude-progress.md（新建）
- **已知风险或未解决问题**：
  - `init.sh` 的 audio smoke 还没在干净环境跑过（写完即测）
  - `SESSION-BOOTSTRAP.md` 信息已迁移，需决定是否删除或保留作 alias
- **下一步最佳动作**：
  1. 跑 `./init.sh` 验证 smoke 通过
  2. 决定 `SESSION-BOOTSTRAP.md` 处置
  3. 用户 review 后提交，进入 `robot-001`

### Session 003 — 2026-05-08（跨平台 + 决策导航）

- **本轮目标**：让 harness 跨 macOS / Linux / Windows 工作；在 progress 顶部建立决策导航。
- **已完成**：
  - 顶部新增"关键决策导航"段（6 条核心决策一表索引）
  - `pyproject.toml` 删除 `tool.uv.required-environments` 限制
  - 抽出 `scripts/smoke.py`（核心 smoke 逻辑唯一一份，跨平台）
  - `init.sh` 简化为 uv sync + 调用 smoke.py，新增 `--daemon` 透传
  - 新增 `init.ps1`（Windows 等价入口）
  - `AGENTS.md` / `CLAUDE.md` 路径表述去掉本机硬编码，加 Windows 用法
- **运行过的验证**：（待 ./init.sh 验证 smoke.py 路径仍通）
- **已记录证据**：（本 session 提交后填）
- **提交记录**：（待用户确认）
- **更新过的文件或工件**：pyproject.toml、init.sh、scripts/smoke.py（新）、init.ps1（新）、AGENTS.md、CLAUDE.md、claude-progress.md
- **已知风险或未解决问题**：
  - `init.ps1` 在本机（macOS）无法验证，需 Windows 机器实测
  - reachy-mini Lite SDK 跨平台 wheel 已确认存在（PyPI 查证），但真机相关功能在 Linux/Windows 行为未知
- **下一步最佳动作**：
  1. 在 macOS 跑 `./init.sh` 验证 smoke 通过
  2. 评估 memex / logex / opc 是否值得集成（用户提问）
  3. 用户 review 后提交，进入 `robot-001`

### Session 005 — 2026-05-09（连续开发模式启动 / checkpoint）

- **本轮目标**：进入连续开发模式前的策略对齐 + 决策落地
- **已完成**：
  - 顶部新增"连续开发模式"段（6 条决策：commit 放权 / mockup-sim 即 passing / Reviewer 20 轮上限 / feature 走完即完工 / 缺依赖自动加 feature / 暂不要求 Windows 验证）
  - 守则配套（backlog/real-robot-uat.md 收集真机项；auto-added feature 在 _change_log 注明）
- **下一步最佳动作**：
  1. 起新 session（context 充足）
  2. 进入 `infra-001`：第一步 `python -m reachy_mini.apps.app create coco_spike .` 拿官方骨架，临时目录生成、对照后丢弃
  3. 按 7 条 verification 走完，Reviewer sub-agent 评审通过后切 `passing`
  4. 自走进入 `audio-002`（infra-001 后下一个 not_started）

### Session 004 — 2026-05-08（harness 加固 + 部署模型决策）

- **本轮目标**：补 harness 缺失件；调研 Reachy Mini Control.app 部署模型，决定路线。
- **已完成**：
  - 新增 `clean-state-checklist.md`（收尾自检 6 项 + 子系统专项）
  - 新增 `tests/fixtures/audio/README.md`（audio-002 wav 素材规范）
  - 新增 `docs/uat-real-robot.md`（真机验收剧本骨架）
  - `scripts/smoke.py` 增加环境基线打印（python / OS / 关键包版本）
  - `AGENTS.md` 增加 Git 工作流、依赖升级策略、app 部署约定、增强工具引用
  - `CLAUDE.md` 同步增加 Git 工作流、依赖升级策略、app 部署约定
  - `claude-progress.md` 决策导航表增加"app 部署路线 C"一行；新增"环境基线"段
  - 调研 `reachy_mini.apps` 框架并产出 `research/control-app-deployment-research.md`
  - 决定走路线 C（双模式：开发 `python -m coco`；UAT/发布走 Control.app）
  - `feature_list.json`：新增 `infra-001`（priority 0）；调整 `audio-001` 与 `interact-001` 的 verification 引入 ReachyMiniApp 框架与 Control.app 模式覆盖
  - 修正 `pyproject.toml`：保留 `[tool.uv] required-environments` 列三平台（reachy-mini 1.7 引入 gstreamer-bundle 硬依赖，不显式列出无法 resolve）
- **运行过的验证**：`./init.sh`（smoke 通过；本机 darwin/arm64）
- **已记录证据**：（本 session 提交后填 commit hash）
- **提交记录**：（待用户确认）
- **更新过的文件或工件**：clean-state-checklist.md（新）、tests/fixtures/audio/README.md（新）、docs/uat-real-robot.md（新）、research/control-app-deployment-research.md（新）、scripts/smoke.py、AGENTS.md、CLAUDE.md、claude-progress.md、feature_list.json、pyproject.toml
- **已知风险或未解决问题**：
  - 路线 C 的核心假设——"在 ReachyMiniApp 框架下 audio 解耦仍成立"——未验证；这是 `infra-001` 的核心 verification 项
  - 还没建 `coco/` 包（infra-001 第一步）
  - `init.ps1` 仍未在 Windows 机实测
  - 没决定 `SESSION-BOOTSTRAP.md` 处置（信息已迁移到 claude-progress.md，但文件还在）
- **下一步最佳动作**：
  1. 用户 review 本轮变更
  2. 处置 `SESSION-BOOTSTRAP.md`（删除 / 留作历史 / 改为指针）
  3. commit 后进入 `infra-001`：先用 `python -m reachy_mini.apps.app create coco_spike .` 看官方骨架，再决定如何重构
