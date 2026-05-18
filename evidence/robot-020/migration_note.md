# robot-020 migration note

**feature_id**: robot-020
**priority**: 194
**phase**: 23
**source_backlog**: robot-015-backlog-v3-warn-once-rename
**verdict**: PASS
**scope**: verify-only, 0 业务源码改动

## 做了什么

- 新增 `docs/robot-warn-once-keys-spec.md`: 锁定 `coco/proactive.py` 三种 warn-once dedup 机制 (setter dup / setter probe-fail / sync fallback) 的 key tuple 字面量 / dedup 范围 / env gating / 重命名风险。
- 新增 `scripts/verify_robot_020.py` V0~V_n 验证:
  - V0 fingerprint sha256
  - V1 源码字面量锁面 (key tag/set 属性名/env 变量名/key tuple 元素)
  - V2 spec doc 关键短语锁 (>=17 项)
  - V3 env=1 setter dup dedup 行为实证 (WARNING 3 + DEBUG suppressed 1, set size==3)
  - V4 env=0 default 路径 (两个 set 始终空)
  - V5 邻近 verify 回归 (008/016/017/018/019 rc==0)
  - V6 smoke 占位 (closeout `./init.sh`)
- evidence/robot-020/verify_summary.json (verdict=PASS, 总耗时 ~222s)

## 业务源码 diff
0. 不动 `coco/proactive.py`, 不动其他业务模块。仅新增 docs/ + scripts/ + evidence/ + feature_list.json + claude-progress.md。

## default-OFF 不变式
两个 env (`COCO_ROBOT_SETTER_LIFECYCLE_AUDIT` / `COCO_ROBOT_SYNC_FALLBACK_AUDIT`) 默认未设, 此时两个 dedup set 永远空, bytewise 等价 robot-010/main 行为。V4 已实证。

## 新增 backlog
0. 不衍生 fu chain。

## fingerprints
- proactive.py sha256: b35d47f503c533920ff9a43bc0cfeef0d2fa9cd5e384dd1ace968c96766f2136
- spec_doc sha256:    4e8f816518a208e31a76ae7a929a4e377d3808236a5b3f769e47264b1a6a7b1c

## smoke
11/11 PASS (在 closeout 阶段执行)

## phase-23 进度
本 feature 是 phase-23 第 5 / 5 个收官 feature, 收官后进入 phase-24 planning。
