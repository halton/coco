# interact-025 migration note

**source backlog**: interact-018-backlog-v1-cooldown-coverage
**direction**: 纯 verify-only 补丁; 无业务源码改动 (coco/ 零 diff); 仅
`scripts/verify_interact_025.py` 与 `evidence/interact-025/` 新增。

## 背景

interact-018 V1 anchor 只验证了 `ProactiveScheduler.maybe_trigger()` 触发后
存在 `latency_ms` emit + `arbit_winner` stage; interact-023 进一步覆盖了
cooldown_hit 端到端、latency 语义 (<1000ms)、连发 type-strict numeric>=0、
is_fail 闭环。本任务 (interact-025) 在此基础上补 **cooldown 边界**:

1. `_should_trigger` 内部用 `since < cooldown` **严格小于** —— since == cooldown_s
   刚到期那一刻应进 admit, 而非 cooldown_hit (边界 case 之前无 verify)。
2. 连发 N 次 cooldown 抑制时, `sched.stats.skipped_cooldown` 内部计数器
   必须与 trace 端 `stage='cooldown_hit'` emit 数严格相等 (内外计数一致性,
   单边漂移会导致 failure_rate / arbit_skipped 等下游 metric 倾斜)。
3. 同一次 `maybe_trigger()` 调用内, `cooldown_hit` (reject) 与 `arbit_winner`
   (admit) 永不同时 emit (互斥分支锁面)。
4. `default-OFF` 不变式: env `COCO_PROACTIVE_TRACE` 未设 / `="0"` 时,
   cooldown 路径不 emit `proactive.trace` (与 interact-018/023 系列契约
   一致)。

## verify 子项与结果

| ID | 名称 | 含义 | 结果 |
| --- | --- | --- | --- |
| V0 | fingerprint sha256 lock | 锁住 `if since < cooldown:` / `"cooldown_hit" if reason == "cooldown"` / `latency_ms=_lat_ms(),` 三关键源码片段 sha256 | PASS |
| V1 | cooldown 刚到期边界 | case A (since=cd-0.5) → reject; case B (since==cd, strict-<) → admit; case C (since=cd+5) → admit | PASS |
| V2 | skipped_cooldown 内外计数一致 | N=5 连发, `stats.skipped_cooldown == trace cooldown_hit emits == 5` | PASS |
| V3 | cooldown_hit / arbit_winner 同次互斥 | 4 次 maybe_trigger 调用, 每次内 stage set 不同时含两者 | PASS |
| V4 | default-OFF 不变式 | env 未设 / `="0"` 时, cooldown 路径 `proactive.trace` emit 数 = 0 | PASS |
| V5 | 邻近 verify 回归 | interact-018/022/023/024 rc=0 | PASS |
| V6 | smoke 全绿 | `./init.sh` rc=0 | PASS |

`uv run python scripts/verify_interact_025.py` → 7/7 PASS。

## 实现要点

1. `_build_scheduler(cooldown_s)` helper 与 interact-023 同结构, 复用
   `ProactiveScheduler + ProactiveConfig + fake power/face/llm/tts`。
2. V1 边界用 `maybe_trigger(now=...)` 显式控制 t, 同时手动设
   `_last_proactive_ts = t0`, 避开 monotonic 不可控性。
3. V3 用 `set_emit_override(_emit)` + 每次调用前换桶, 收集每次 maybe_trigger
   内的 stage 集合 (NOTE: `candidate_id = int(t*1000)` 仅基于 ts, 同 1ms 内
   多次调用会复用同 cid, 不能用 cid 分组判互斥)。
4. V4 显式 pop `COCO_PROACTIVE_TRACE` env 与 `="0"` 两种场景, 与
   interact-018 系列 default-OFF gate 同源契约。
5. V0 用 plain string 锁源码片段后取 sha256 prefix 当 fingerprint —— 既保
   anchor 真存在 (substring match), 又输出指纹便于下次漂移 diff。

## 业务源码 diff 确认

`git diff main..HEAD coco/` → 空。本任务**未触碰 `coco/`** 任何文件,
contract / 行为 0 改动。新增范围:

- `scripts/verify_interact_025.py` (新)
- `evidence/interact-025/verify_summary.json` (新)
- `evidence/interact-025/migration_note.md` (本文)
- `feature_list.json` (interact-025 status not_started → passing + updated_at)
- `claude-progress.md` (新 Session 条目)

## backlog 增量

无。本任务覆盖 cooldown 边界 + 内外计数一致性 + 互斥锁面 + default-OFF
不变式四个维度, 暂无新发现的 follow-up caveat。

## 下一候选

robot-019 (P191, robot-009 block-policy 文档锁面 + verify, verify-only)。
