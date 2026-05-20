# Verify-Script PR Template

> 适用于新增 / 修改 `scripts/verify_*.py` 的 PR。
> 用 `?template=verify-script.md` query 触发。
> 必须勾选「决策矩阵」, 否则视为流程不合规。
> 决策矩阵依据见 `docs/verify_sha_lock_strategy.md`。

## 1. Verify-script 概况
- 目标 verify-script: `scripts/verify_<feature_id>.py`
- 关联 feature: `<feature-id>`
- 类型: [ ] 新建  [ ] 修改  [ ] 删除

## 2. 决策矩阵 (必须勾选, 引用 docs/verify_sha_lock_strategy.md)

### 锁类型选择 (单选, 必选一项)
- [ ] **file-level sha** — 锁整个 verify-script 文件 sha
  - 理由: ___________
- [ ] **func-level sha** — 锁某个 function 的 canonical source sha
  - 理由: ___________
- [ ] **混合** — 既有 file-sha 又有 func-sha
  - 理由: ___________

### 决策依据 (勾选适用项, 可多选)
- [ ] 目标 verify-script 长期稳定, 内部 helper 难抽离 → file-sha
- [ ] 目标 verify-script 含可复用 helper, 多 verify 共用 → func-sha
- [ ] 业务/技术变更频繁, 用 func-sha 解耦
- [ ] verify-script 本身是 docs-only / scaffolding, 锁文档 sentinel + 自 checker func sha
- [ ] 其他: ___________

### 反向引用扫描 (cascade 影响)
- [ ] 已用 `scripts/dump_v4_sha_graph.py` 扫描过本 PR 涉及的所有 verify-script 反向 sha 引用
- [ ] 已确认所有反向引用同步 bump (列出受影响文件): ___________

## 3. 验证证据
- [ ] 本 verify-script 自身 rc=0
- [ ] 所有反向引用 verify-script rc=0
- [ ] `./init.sh` smoke 11/11
- [ ] Reviewer fresh-context LGTM (sub-agent, 不能主 context 自审)

## 4. backlog (新发现, 入 feature_list.json)
- _________

## 引用
- `docs/verify_sha_lock_strategy.md` — file-sha vs func-sha 决策矩阵
- `scripts/_verify_lib.py` — verify 共用 helper
- `scripts/dump_v4_sha_graph.py` — V4 sha-lock graph dump 工具
