# 干净状态检查清单

每轮会话结束前过一遍。30 秒。任何一项不勾，不能宣告会话完成。

## 必查项

- [ ] 标准启动路径仍然可用（`./init.sh` 或 `.\init.ps1` 能跑通到 smoke 通过）
- [ ] 标准验证路径仍然可运行（当前 in_progress feature 的 verification 步骤没有被破坏）
- [ ] 当前进度已经记录到 `claude-progress.md`（追加了本轮 Session 段）
- [ ] `feature_list.json` 真实反映了 passing 与未验证的边界
  - 没有把 `not_started` 直接跳到 `passing` 而没附 evidence
  - 没有把已经 broken 的 feature 留在 `passing`
- [ ] 没有任何半成品步骤处于未记录状态（未提交的 WIP 改动有 note 或干脆 stash）
- [ ] 下一轮会话无需人工修复即可继续（git 工作树干净或 WIP 状态明确记录）
- [ ] 如果触及版本相关变更（`pyproject.toml`、`uv.lock`、SDK 升级），记录到决策导航

## 子系统专项（按需）

仅在本轮动了对应子系统时检查：

### audio
- [ ] sounddevice 仍能采到非零数据（smoke 通过即满足）
- [ ] 测试 wav 素材路径未变（如改动 `tests/fixtures/audio/`）

### robot
- [ ] mockup-sim daemon 启动方式与 `init.sh --daemon` 仍一致
- [ ] 没有把 `--mockup-sim` 改成 `--sim`（后者要 MuJoCo，未装）

### dependencies
- [ ] 没有手动 `uv pip install` 进 venv 而不更新 `pyproject.toml`
  （会话间会被 `uv sync` 卸载）
- [ ] 升级核心 SDK（reachy-mini）已记录决策

## 不通过怎么办

- 启动 / 验证不通：先修，不要在坏起点上叠新功能
- 状态不真实：把对应 feature 改回 `in_progress` 或 `blocked`，写明原因
- WIP 未记录：要么提交，要么 stash + 在 `claude-progress.md` 写明 stash 名
