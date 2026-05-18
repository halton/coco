# robot-021 migration note

- **feature**: robot-021 (priority 201, phase 24, verify-only)
- **source backlog**: robot-008-backlog-setter-lifecycle
- **scope**: ProactiveScheduler→RobotSequencer setter lifecycle 文档锁面 + verify
- **业务源码改动**: **0** (`coco/proactive.py` 未触碰)
- **新增/修改文件**:
  - `docs/robot-scheduler-sequencer-lifecycle-spec.md` (NEW)
  - `scripts/verify_robot_021.py` (NEW)
  - `evidence/robot-021/verify_summary.json` (NEW)
  - `evidence/robot-021/migration_note.md` (本文件)
  - `feature_list.json` (status: in_progress → passing)
  - `claude-progress.md` (Session append)
- **新增 backlog**: 0 (本任务为 verify-only spec 锁面, 不衍生 fu chain)
- **default-OFF 不变式**: env `COCO_ROBOT_SETTER_LIFECYCLE_AUDIT` 未设时 `_setter_audit_seen` 始终空, 主路径 (拒绝 shutdown / 写入 / dup 覆盖) bytewise 等价 robot-010。
- **migration impact**: 无 runtime 变更, 仅锁定 setter lifecycle 状态机 (S0-S7) 与字面量 anchor, 防止后续重构静默打破 setter 语义。
