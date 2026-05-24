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
- **push 策略——commit 后必须尝试 push 一次，失败忽略继续**：sub-agent 在 closeout（或直接在 main 上的基础设施 commit）后必须执行一次 `git push origin main`；若仍在 `feat/<feature-id>` 分支上，也要 push 该分支。push **不重试、不 sleep**：网络/认证/socket/超时/拒绝等任何失败都只在返回报告里记录原因，**立即继续下一步任务**，不阻塞流程。此规则覆盖此前 "closeout 自动 push origin main + feat/xxx、失败 3 轮重试 sleep 30s" 与 "默认只 commit 不 push、push 等用户指令" 两套旧规则。push 命令模板（每条只跑一次）：`git push origin main` / `git push origin feat/<feature-id>`。

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

### Sub-agent Evidence Report Accuracy（P273 防御，硬规则）

P273 暴露过一类失真模式：sub-agent 报告 "verify PASS" 但并未真正执行该 verify 脚本 / 未读 stdout / 凭印象编造，并把失败错误归因为 "pre-existing"。为防御此模式，**所有 sub-agent 上报 verify 结果时必须**：

1. **实际执行**该 verify 脚本（`.venv/bin/python scripts/verify_xxx.py`），Bash 真正跑过；
2. 报告中**附实测 stdout 尾行**，即 `[verify_infra_NNN][SUMMARY] ALL PASS (N checks)` 字面行（FAIL 时附 `FAIL k/N: [...]` 行 + 关键 FAIL emit 行）；
3. **不允许"pre-existing"归因**，除非显式在 main HEAD 复现并附两份 stdout 对比（main vs. feat 分支）；
4. 推荐用 `scripts/_verify_lib.assert_verify_passed(stdout, verify_name)` 机器辅助校验 stdout，并在 evidence 中贴出 `res["summary_line"]` + `res["passed"]` 双字段。

新建 verify 脚本时应在 V4 行为段为本 helper 做正/反例 round-trip（PASS stdout 进 → passed=True；FAIL stdout 进 → passed=False；缺 SUMMARY 进 → passed=False）。当前参考实现：`scripts/verify_infra_055.py`。

### Sub-agent fact-vs-blame checklist (P288 防御，硬规则)

P275 / P278 / P285 三次复现过同一类失真：sub-agent 把**自身回填错误 / 中间脏快照 / 占位未更新** 误归因为 "pre-existing FAIL"（典型句式："NEW_MAIN 上某 verify 本来就 FAIL，与本 feature 无关"）。本 checklist 把"fact"（main HEAD 实测）与 "blame"（pre-existing 归因）解耦，把后者门槛抬高到必须举证：

**任何 sub-agent 在 closeout / verify 报告中作出"pre-existing FAIL"声明前，必须同时附齐以下三项；缺一即视为 sub-agent 自身错误优先排查，归因无效**：

1. **当前 main HEAD sha**（7+ hex），并保证报告中的 main HEAD 与 `git -C <repo> rev-parse main` 实测一致；
2. **在该 main HEAD 上的 verify 实测 rc + 完整 stdout 尾行**（`[verify_xxx][SUMMARY] FAIL k/N: [...]` 字面行 + 关键 FAIL emit 行）；不允许只贴 rc 数字、不贴 stdout；
3. **涉及 `EXPECTED_*_FUNC_SHA` / 锁值类 FAIL 时**：附 main HEAD 上的 `ast.unparse` / file-sha 实测值快照（`actual_func_sha` 与 `expected` 双字段），证明 expected 与 actual 在 main HEAD 上确实不一致，而非 feat 分支中间编辑产生的脏快照。

三项齐备才允许在归因栏写 `pre_existing_baseline_sha: <main HEAD>` + `pre_existing_baseline_evidence: <stdout 尾行>`；否则只能写 `attributed_to: sub_agent_self_error_suspected`，并优先在 feat 分支头部重跑、清理中间编辑产物后再判定。

此规则与「Closeout-verify-trustworthy 硬规则 (P278)」并列：P278 锁的是"closeout 报告字段齐备性"（机械化由 `scripts/verify_infra_062.py`），本段锁的是"pre-existing 归因举证完整性"（机械化由 `scripts/verify_infra_P288.py`，正文检查 AGENTS.md 内本段 marker 与三项要求字面存在）。

### New verify-script self-checker bootstrap protocol (P275)

P273 / P275 还暴露过另一类失真：**新建 `scripts/verify_infra_NNN.py`（含 V1 self-lock）时，作者把 `EXPECTED_V4_CHECKER_FUNC_SHA` 锁的是"自身 v4_behavior 实现写完之前"的 sha**，因为之后又改了 V4 实现，导致 self-check 首跑 FAIL；作者再错误归因到 pre-existing 而非自己锁错。本协议机械化掉这一步：

1. **先写所有 check 函数 + `_v4_behavior_*` 实现 + `main()`**，最后一步才填 `EXPECTED_V4_CHECKER_FUNC_SHA` 常量；占位用 `"__BUMP_ME__"` 或 `"__FILL_ME_AFTER_FIRST_RUN__"`，**不允许**用全 0 / 全 f 的 silent 占位（让 V1 静默通过）。
2. **回填前必须先跑一次脚本**（让 V1 因 placeholder/sha 不一致 FAIL，从 FAIL 信息读 actual func sha），或直接跑 `python scripts/bootstrap_verify_self_checker.py --verify-script scripts/verify_infra_NNN.py --func-name v4_behavior` 取真值。
3. **回填后必须再跑一次确认 PASS**（`.venv/bin/python scripts/verify_infra_NNN.py`，末行须 `[verify_infra_NNN][SUMMARY] ALL PASS (M checks)`）。
4. **推荐脚手架步骤模板**（伪码）：

   ```text
   # step 1: 占位
   EXPECTED_V4_CHECKER_FUNC_SHA = "__BUMP_ME__"

   # step 2: 写完所有 V0..V5 + main()

   # step 3: bootstrap helper 取真值
   $ python scripts/bootstrap_verify_self_checker.py \\
       --verify-script scripts/verify_infra_NNN.py --json
   # → {"actual_func_sha": "abcd...", "paste_line": "EXPECTED_V4_CHECKER_FUNC_SHA = \\"abcd...\\""}

   # step 4: paste paste_line 回填本脚本

   # step 5: 再跑一次确认 ALL PASS
   $ .venv/bin/python scripts/verify_infra_NNN.py
   ```

当前参考实现：`scripts/bootstrap_verify_self_checker.py` + `scripts/verify_infra_056.py`。

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

## Closeout-verify-trustworthy 硬规则 (P278)

Closeout sub-agent 提交的 verify 报告必须满足以下 6 条机械化校验，否则视为不可信：

1. **main HEAD 显式锁**: evidence.closeout_verify.main_head_sha 存在且 >=7 hex (merge 后必须在 main HEAD 上跑，不能在 working tree)
2. **verify_runs 完整尾行**: evidence.closeout_verify.verify_runs 非空，每项含 script + 非空 tail_stdout + status ∈ {PASS, FAIL}
3. **pre-existing FAIL 需独立复现**: 任一 FAIL 必须配套 pre_existing_baseline_sha + baseline_tail_stdout 字段（在 pre-merge main baseline 上 stash 后独立复现）
4. **smoke 尾行**: evidence.closeout_verify.smoke_tail_stdout 非空
5. **Reviewer fresh-context**: evidence.reviewer.reviewer_kind == "sub_agent_fresh_context" 且 lgtm == True（主 context 自审不算）
6. **baseline_head_echo 一致性 (P286-followup3, phase-42 #1.42)**: 若 evidence.closeout_verify.reviewer.baseline_head_echo 字段存在，必须与 closeout_verify.baseline_head_sha 前 7 hex 等值匹配 (大小写不敏感)。Default-OFF 渐进 promote：老 feature 缺字段 → soft_skipped (不 retro fix)；缺 baseline_head_sha → soft_skipped (老 schema)。该信号防御 P286 round-1 baseline-HEAD mismatch (Reviewer 在 feat HEAD 上跑 baseline verify 却 echo 成 baseline sha 的伪证)。

机械化校验由 `scripts/verify_infra_062.py` 调用 `_verify_lib.verify_closeout_evidence_trustworthy(evidence)` (信号 1-5) + `_verify_lib.assert_baseline_head_echo_present_and_matches(feature_list)` (信号 6) 实施。Closeout sub-agent 提交前应自检 dogfood。`scripts/verify_infra_082.py` 静态 ast-lock 锁 062 必须 wire 信号 6 helper, 删调用即 FAIL。

**已知边界 (P278 round-2 显式承认)**: 当前 helper **只验 schema 不验 stdout 字符串真伪** —— 即 Engineer 可在 evidence 里写任意 `tail_stdout = "ALL PASS"` 字符串而 helper 不会反查 main HEAD 实测。tail_stdout 真伪验证留待 backlog `infra-P294-closeout-stdout-sha-verification` (sha256-of-stdout + main HEAD re-run 比对) 与 `infra-P294-R4-fail-baseline-cross-check` (FAIL run 的 baseline_tail_stdout 交叉校验)。当前阶段 trust gate 仍由 Reviewer fresh-context 人工对照实测 stdout 把关。

## Shell verify rc 读取硬规则 (P299, phase-49 #5.49)

INFRA_P299_SHELL_VERIFY_RC_DOC_SENTINEL —— 此 sentinel 由 `scripts/verify_infra_100.py` 静态锁定，本节文案不得静默漂移。

P294-Rx round-1 Engineer 把真 FAIL 误判为 PASS, 根因不是 verify 脚本 bug, 而是 shell 调用形态:
`python scripts/verify_xxx.py | tail; echo $?` 输出的 rc **是 `tail` 的 rc (恒为 0)**, 上游 verify 脚本的真 rc 被 pipe 吞掉。这条 shell pitfall 必须显式禁止并文档化。

**强制规范 (三选一; 在 evidence / sub-agent brief / 报告里都按此格式)**:

- (a) **不 pipe, 直接读 `$?`**:
  `python scripts/verify_xxx.py; rc=$?; echo "rc=$rc"` (verify 全量 stdout 直接进终端, rc 来自 verify 本体)
- (b) **`set -o pipefail` 后才允许 pipe**:
  `set -o pipefail; python scripts/verify_xxx.py | tail -n 20; rc=$?` (pipefail 后 `$?` 反映管道中任一段 non-zero, verify FAIL 不会被 tail 吞)
- (c) **stdout 重定向到文件, 读完整再 tail**:
  `python scripts/verify_xxx.py > /tmp/v.log 2>&1; rc=$?; tail -n 20 /tmp/v.log; echo "rc=$rc"` (rc 来自 verify 本体, tail 只读文件不影响)

**显式禁止 (anti-pattern)**:
`python scripts/verify_xxx.py | tail; echo $?` —— **rc 来自 tail 不来自 verify**, 把真 FAIL 当 PASS 报告即视为 evidence 不可信, 由 Reviewer fresh-context 直接 reject。

**同步要求**: 派 sub-agent 跑 verify 时, brief 模板里须复述以上三选一形态之一; sub-agent 返回 verify tail 同时返回 `rc=<int>` 字段并显式声明用了 (a) / (b) / (c) 哪种形态。

## Cascade RESULT sentinel 约定 (infra-V24, phase-67 #17)

`scripts/bump_reverse_sha_lock.py` (反向 sha lock bump helper) 在 main() 末尾输出一行 `RESULT: APPLIED holders=<N>` 或 `RESULT: NOOP reason=<text>` 供父 cascade (例 `bump_strict_unknown_sha.py --cascade`) subprocess parse。两条容易踩坑、必须写死的约定:

1. **`SENTINEL_CASE_SENSITIVE`**: kind token `APPLIED` / `NOOP` 必须大写。父 cascade 用子串 `"APPLIED" in result_line` / `"NOOP" in result_line` 匹配, 大小写敏感; 输出端 f-string 也用字面大写, 不允许 title-case 或小写改写。
2. **`ARGPARSE_FAILURE_SKIPS_RESULT`**: argparse 自身或 pre-arg-parse 阶段失败 (例: 必须的 `--target` 缺失, 未识别 flag) 触发 `ap.error()` → `sys.exit(2)`, 此时 main() 体未执行到 print RESULT 行, **stdout 不会含 RESULT 行**。父 cascade 解析端必须在 "末尾找不到 RESULT 行" 时 fallback 到 "报告非 0 rc + 透传原 stdout/stderr", 不允许把 "RESULT 缺失" 当 NOOP 静默吞。参考实现: `bump_strict_unknown_sha.py` cascade 段 rc=7 显式表示 "RESULT sentinel 缺失"。

机械化锁: `scripts/verify_infra_V24.py` V1 在 `scripts/bump_reverse_sha_lock.py` 模块 docstring 与本节同时 grep sentinel 词 (`INFRA_V24_CASCADE_RESULT_SENTINEL_CONVENTIONS`, `SENTINEL_CASE_SENSITIVE`, `ARGPARSE_FAILURE_SKIPS_RESULT`); V3 mutant 任删一词 V1 应 FAIL。

## 收尾

结束会话前：

1. 更新 `claude-progress.md`（追加一条 Session 记录）
2. 更新 `feature_list.json`（状态、evidence）
3. 记录仍未解决的风险或 blocker
4. 在工作处于安全状态后，按用户确认流程提交（不要自动 commit）
5. 保证下一轮会话可以直接运行 `./init.sh`
