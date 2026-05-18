# vision GC 时钟选型设计文档 (vision-015 锁面)

scope: 锁定 `coco/perception/face_tracker.py` 中 `_gc_last_time` 与 `run_gc_cycle`
TTL 计算各自使用的时钟来源, 以及不切回单一时钟的设计理由。

本文档为 **verify-only 契约文档**, 由 `scripts/verify_vision_015.py` V2/V_n
锁面校验, 任何对相关字面量 (`time.monotonic` / `time.time` 在 GC 路径上的使用)
的修改都必须同步更新本文与 verify。

## 1. 现状 (vision-013 后已固定)

`coco/perception/face_tracker.py` GC 相关路径混用两路时钟:

- `_gc_last_time` (周期 GC 触发计时, "距上次 GC 多久"):
  使用 **`time.monotonic()`**。仅作进程内相对差值, 不持久化。
- `run_gc_cycle(now=...)` 内 TTL 过期判定 (entry 是否超过 TTL_DAYS):
  使用 **`time.time()`** (wall clock / POSIX epoch), 与 entry 持久化字段
  `last_seen` 同时基。`last_seen` 写到 `data/face_id_map.json`, 跨进程 / 跨
  会话仍需可比较。
- `_maybe_run_periodic_gc()` 内部调用链:
  `now_mono = time.monotonic()` → due check 用 monotonic;
  `self.run_gc_cycle(now=time.time())` → TTL 用 wall。

## 2. 三维对比 (wall vs monotonic)

| 维度 | wall clock (`time.time`) | monotonic (`time.monotonic`) |
|---|---|---|
| NTP 回拨 / step-jump | 受影响, 可能造成 due 永远到 / 永远不到 | 不受影响 |
| 持久化跨进程比较 | 可比较 (POSIX epoch 全局一致) | 不可比较 (每个进程 0 起点) |
| 启动后单调性 | 不保证 (NTP / 用户改时间会跳) | 严格单调非递减 |

结论:

- `last_seen` 必须持久化 → 必须 wall, 否则跨进程不可比。
- `_gc_last_time` 不持久化, 只算相对差 → 用 monotonic 最稳, 完全免疫 NTP 跳变。
- 因此 GC 路径上 **wall + monotonic 混用是有意设计**, 不是 bug。

## 3. 不切回单一时钟的设计选择

**A. 为何不把 TTL 也切到 monotonic**:

- `last_seen` 必须跨进程可比, monotonic 在每个进程都从 0 开始, 跨会话比较即出错。
- 持久化字段切语义需要数据迁移 + 兼容期, 改动面远超本 phase 的 verify-only 范围。

**B. 为何不把 `_gc_last_time` 切回 wall**:

- 切 wall 后 NTP 大幅回拨 (从未来时间被纠正到当前) 会让 `(now - _gc_last_time)`
  瞬间变负, time_due 永远 false, GC 周期阻塞直到再过一个完整窗口。
- 当前 monotonic 实现已经稳定通过 verify_vision_013 / 014b 测试, 切换需要同步
  迁移 verify monkey-patch (test 里 mock `time.monotonic` 需切到 mock `time.time`),
  迁移代价 != 收益。

**C. 当前混用的成本**:

- 阅读者需要理解 "两路时钟各管一摊", 设计本文档 + `face_tracker.py` module
  docstring L14-28 + `_maybe_run_periodic_gc` docstring L1142-1145 三处锁面。

## 4. GC 300s 默认窗口对 NTP 回拨的不敏感性

- `COCO_FACE_ID_MAP_GC_PERIOD_S` 默认 300 (5 分钟), 触发使用 monotonic, 不受 NTP 影响。
- TTL 默认 30 天 (`COCO_FACE_ID_MAP_TTL_DAYS=30`), 单位粒度日级, 即使 NTP 把 wall
  调动数小时也基本不会跨过 30 天阈值大量失效 / 大量保留。仅当 step-jump 跨度数月
  级别时才会出现 docstring L22-28 描述的批量清空 / 停止 GC 现象。
- 因此 300s 窗口的设计在 NTP 常规 slew (< 数秒) 场景下完全不敏感; step-jump 是
  运维问题, 文档已给出建议 (gradual slew, 或人工 `run_gc_cycle()` 校准)。

## 5. 未来若必须把 `_gc_last_time` 切到 wall 的迁移代价

仅作 evidence, 此 phase **不动手**:

1. 源码改动: `coco/perception/face_tracker.py` 三处 `time.monotonic()` 调用
   (L1150, L1153, L1161) → `time.time()`, 删去 `now_mono` 变量名;
   docstring (L14-28, L1142-1145) 同步更新。
2. verify 同步迁移代价:
   - `scripts/verify_vision_013.py` 与 `verify_vision_014b.py` 中所有 monkey-patch
     `time.monotonic` 的 fixture 需切到 `time.time`。
   - 新增 NTP 回拨场景测试 (mock time.time 突然回退), 断言 GC 不会卡死。
3. 兼容性: `_gc_last_time` 不持久化, 切换不影响磁盘文件; 但 process restart 后
   `_gc_last_time=None` 走 None-init 路径, 与现行行为等价。
4. 风险: NTP step-jump 场景 GC 周期可能丢一窗或多触发一次, 业务侧无副作用
   (run_gc_cycle 是幂等清理), 但 verify 需要新增 jitter tolerance。

**结论**: 当前 monotonic 选型稳定, 无业务驱动切换, vision-015 不动源码, 仅锁面。

## 6. 锁面约束 (verify 抓的契约)

`scripts/verify_vision_015.py` 校验以下契约:

- V0: 关键文件 sha256 fingerprint。
- V1: `_gc_last_time` 写入的 RHS 字面量 = `time.monotonic()` (反证: 不得是
  `time.time()`)。
- V2: 本文档关键短语存在 (wall vs monotonic / 300s / NTP 回拨 / 不迁移)。
- V3: 相邻 verify (vision-012/013/014b) regression rc=0。
- V4: 迁移 evidence-only note 文件存在。

任何破坏上述契约的 PR 都应被 verify 抓到 FAIL。

vision-015 作为 verify-only 契约审计 + 设计文档锁面到此为止, 不衍生 fu chain,
不动源码。
