# Coco / 可可 — Session Bootstrap

**Last updated**: 2026-05-08

## 项目定位
基于 Reachy Mini 的全年龄学习伴侣机器人。MVP 聚焦**场景 1：桌面学习搭子**（头部姿态陪伴 + 简单语音交互）。

## 当前状态
**阶段**：spike 完成 → 准备起草 phase-1 spec。

## 已完成
- 4 个候选场景已评估，决定先做场景 1
- 场景 2/3/4 归档到 `BACKLOG.md`
- 模拟器与音频能力调研：`research/simulator-audio-findings.md`
- **Spike 结案**（2026-05-08）：`research/spike-audio-attempt.md`
  - 决定：**audio 与 robot 解耦**。robot 走 ReachyMini/mockup-sim daemon，audio 走 sounddevice 直连 mac 麦克
  - sounddevice 验通：MacBook Air Mic 可采非零数据，权限 OK
  - 环境坑已记录（venv shebang / gstreamer / control.app 冲突）
- 依赖增加：`sounddevice`

## 下一步

1. **起草 `specs/phase-1-mvp.md`**：场景 1 最小闭环
   - 范围：陪伴动作（head pose）+ 简单语音交互（wake word? push-to-talk?）
   - 架构：robot 子系统 + audio 子系统解耦
   - 测试策略：audio 用 wav 直喂；robot 用 mockup-sim
   - 真机验收作为 milestone gate
2. **PM + architect cross-review** spec → 用户签字 → 进入 plan
3. **(可选) commit 当前 spike 工作**（修改 `spike_audio.py` + `pyproject.toml` + research/spike + bootstrap）

## 协作模式（已讨论确定）
- 主线：你做产品决策，主会话做协调
- subagent 按需召唤（architect/engineer/researcher/pm），不常驻
- 模拟器能闭环验证的部分允许 agent 较高自治
- 真机验收作为 milestone gate
- 工具优先级：memex（沉淀知识）> opc（结构化协作）> 其他

## 关键文件索引
- `README.md` — 项目愿景与命名
- `BACKLOG.md` — 场景 2/3/4 + 待定事项
- `research/simulator-audio-findings.md` — 模拟器调研
- `research/spike-audio-attempt.md` — 音频路径决策（结案）
- `spike_audio.py` — sounddevice 验麦克脚本
- `main.py`, `test_head.py` — 现有代码（早期 demo）
- `pyproject.toml` — 依赖：`reachy-mini>=1.4.0`、`sounddevice`，Python 3.13，仅 macOS arm64

## 用户偏好提醒
- 中文沟通
- 提交前需确认 commit 信息（见 ~/.claude/memory/git-conventions.md）
- 暂未有 CLAUDE.local.md，可考虑 `/user:setup-repo`

## Active Specs
（暂无 —— 下一步要创建 `specs/phase-1-mvp.md`）
