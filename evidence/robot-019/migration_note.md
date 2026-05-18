# robot-019 migration note

**Feature**: robot-019 (P191) — RobotSequencer overflow_policy='block' 语义文档锁面 + verify.
**Source backlog**: robot-009-backlog-block-policy-doc.
**Scope**: verify-only doc, 0 业务源码改动.

## Why

robot-009 enqueue 路径 + robot-017 docstring 已在 sequencer.py 内描述
`overflow_policy='block'` 的"短阻塞 1s 后 drop"语义, 但缺少外部 spec doc
+ 静态锁面 verify 防止后续 silent regression. 本 feature 把该语义固化为
`docs/robot-sequencer-block-policy-spec.md` 并附 verify_robot_019.py 锁面.

## What changed

- `docs/robot-sequencer-block-policy-spec.md` (新增) — 三策略对比 + block
  契约 + caller guidance + default-OFF 不变式 + 不衍生 fu chain.
- `scripts/verify_robot_019.py` (新增) — V0 fingerprint sha256, V1 源码字面量,
  V2 spec doc 短语 (18 项), V3 行为 subprocess 实证 (queue_max=1 + 长 duration
  逼满 → r3 drop reason='block_timeout' elapsed≈1.0s), V4 邻近 verify (008/017/018)
  rc==0, V5 smoke 占位.
- `evidence/robot-019/verify_summary.json` (新增) — 全 verify 结果 + sha256 +
  behavior 详情.

## What did NOT change

- `coco/robot/sequencer.py` — **0 byte diff**.
- `coco/**/*.py` — **0 byte diff**.
- env / config / feature flag — 无新引入. spec 标记为 evidence-only.

## Default-OFF invariant

env 未开 (`COCO_ROBOT_SEQ` 未设 / `COCO_ROBOT_BUSY_METRIC` 未设) → bytewise
等价 main HEAD bef3ef6. `overflow_policy` 默认仍为 `drop_oldest`, `block`
路径需要调用方显式 `SequencerConfig(overflow_policy='block')` 才走.

## V4 邻居 trim 说明

verify_robot_012 (~145s) / verify_robot_014 (~573s) / verify_robot_015 (~457s)
单次运行已超 5 min 单 Bash 硬上限. V4 取轻量子集 verify_robot_008/017/018
(累计 ~90s). 三个 heavy 邻居在 closeout `./init.sh` smoke 阶段间接覆盖
(sequencer.py 同源); 若它们 silent regression, smoke 也会先挂.

## 不衍生 fu chain

本 spec 是 robot-017 docstring + robot-009 enqueue 实现的外部固化, 未观察到
新 caveat. backlog 新增数 = 0.
