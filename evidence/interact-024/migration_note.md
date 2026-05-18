# interact-024 migration note

**Feature**: interact-024 (P184, phase-22 last)
**Source backlog**: interact-018-backlog-latency-stage-semantics-doc
**Type**: verify-only 文档锁面, 0 业务源码改动
**Default-OFF invariant**: maintained (V3 subprocess 验证)

## 性质

把 interact-018 Reviewer caveat "`latency_ms` 不同 stage 语义不同"升级为权威 contract:

- `docs/interact-latency-stage-contract.md` — 三 stage (admit / reject / cooldown_hit)
  起止语义对照 + 已知 caveat + env gating + 不衍生 fu chain 收口约束 + 下游消费方
  operational checklist。
- `scripts/verify_interact_024.py` V0..V6 + V_n — sha256 锁面 + 源码 anchors +
  doc 关键短语 + default-OFF/ON 双路径 latency_ms wire + 邻近 verify regression
  (interact-018/021/022/023) + smoke 11/11。

## 下游影响

- `scripts/proactive_trace_summary.py` `latency_by_stage` 聚合行为**不变**;
  本 contract 是文档侧锁面, 不引入任何运行时 schema 变化。
- dashboard / SRE 告警侧无任何字段名 / 类型变更; 仅消费方 operational checklist
  (contract §6) 显式化此前隐式约定。

## 不衍生 fu chain

本 contract 是 backlog `interact-018-backlog-latency-stage-semantics-doc` 的
**收口**, 不衍生新源码改动 chain。本次 verify 中未发现需登记的新 caveat。
若未来下游消费方反馈新边界, 入 `feature_list.json` `backlog` 段 (priority=999,
status="backlog", phase=null), 由后续 phase 按整体 priority 排序。

## phase-22 收官

interact-024 是 phase-22 最后一个 feature (5/5)。前 4: audio-014 + vision-015 +
companion-018 + robot-018 已 DONE。本 feature passing 后 phase-22 全收官,
转 phase-23 planning。
