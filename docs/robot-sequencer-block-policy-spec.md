# RobotSequencer overflow_policy='block' 语义 Spec

**Source feature**: robot-019 (P191), backlog source = robot-009-backlog-block-policy-doc.
**Scope**: verify-only doc, 0 业务源码改动. 锁定既有 robot-017 实现行为 (commit a5b76ce 之前的 sequencer.py L342-L347 docstring + L357-L363 实现路径).
**Status**: evidence-only spec; not a behavior change request.

---

## §1 三种 overflow_policy 概述

`RobotSequencer` 在 `enqueue(action)` 路径上 (`coco/robot/sequencer.py`)
针对内部有界队列 `_action_queue` (capacity = `SequencerConfig.queue_max`,
默认 64) 满载时, 由 `SequencerConfig.overflow_policy` 决定回压策略.
合法集合见 `_VALID_OVERFLOW = frozenset({"drop_oldest", "drop_new", "block"})`.

| policy | 队列满时行为 | 阻塞 | drop reason |
|---|---|---|---|
| `drop_oldest` (默认) | `q.put_nowait` Full → `q.get_nowait` 出队最旧 → 再 `put_nowait` 入新 | 否 | `drop_oldest` (或 `drop_oldest_retry_full`) |
| `drop_new` | `q.put_nowait` Full → 立即丢新 action | 否 | `drop_new` |
| `block` | `q.put(action, timeout=1.0)` 最多阻塞 ~1s; 超时仍满 → drop | **是, 但有界 (1.0s)** | `block_timeout` |

默认值: `overflow_policy: str = "drop_oldest"` (sequencer.py L83).

---

## §2 block policy 行为契约

**实现锚点**: `coco/robot/sequencer.py` `RobotSequencer.enqueue()` L357-L363.

```python
if policy == "block":
    try:
        q.put(action, timeout=1.0)
        return True
    except _queue.Full:
        self._on_enqueue_drop(reason="block_timeout")
        return False
```

契约要点:

1. **timeout=1.0 (秒, 硬编码)**: `block` **不是无限阻塞**. enqueue 调用方最多被
   阻塞约 1.0 秒. 该数值在 sequencer.py 源码字面量写死, 当前 spec 范围内不暴露
   为 config 字段.
2. **超时后 drop, 返回 False**: 1.0s 内队列仍满 → 走 `_on_enqueue_drop(reason="block_timeout")`,
   `enqueue` 返回 `False`. 调用方据此判断该 action 未入队.
3. **emit `robot.enqueue_dropped`**: payload 含
   `reason='block_timeout'`, `queue_max`, `dropped_n`, `policy='block'` (additive 常开).
4. **busy_count (default-OFF)**: `block_timeout` 属于 `_busy_reasons`
   集合; 当 env `COCO_ROBOT_BUSY_METRIC` ∈ {1,true,yes,on} 时附加 `busy_count`
   字段 (robot-013). 未开 env → bytewise 等价 main, payload 不变.
5. **enqueue 返回 bool**: True = 已入队 (worker 异步执行), False = 被 drop.
   调用方不应假定 `block` 永远成功.

---

## §3 与 drop_new / drop_oldest 语义对比

| 维度 | block | drop_new | drop_oldest |
|---|---|---|---|
| put 调用形态 | `put(action, timeout=1.0)` | `put_nowait(action)` | `put_nowait(action)` |
| 满队列时阻塞调用方 | 是 (最多 1.0s) | 否 | 否 |
| 满队列时谁被丢 | 新 action (超时后) | 新 action (立即) | 最旧 action, 然后入新 |
| drop reason | `block_timeout` | `drop_new` | `drop_oldest` / `drop_oldest_retry_full` |
| 适合场景 | 调用方愿意短阻塞换 backpressure | 调用方对新事件可丢, 偏老优先 | 调用方对实时性敏感, 老事件可丢 |

**关键差异**: 仅 `block` 走 `q.put(..., timeout=1.0)` 路径; `drop_new` / `drop_oldest`
都走 `q.put_nowait` + `_queue.Full` 兜底. 三策略共享同一 `_on_enqueue_drop`
emit 通路 (sequencer.py L384+).

---

## §4 调用方 guidance

**默认推荐**: `drop_oldest` (config 默认, sequencer.py L83). 适合大多数 robot
触发源 — ProactiveScheduler / GroupModeCoord / Gesture — 这些场景下"最新一次
触发更具相关性, 老事件丢弃可接受".

**何时切到 `block`**:
- 调用方**愿意承担最多 ~1s 的同步阻塞** 以换取 backpressure 反馈.
- 业务上"新事件比老事件更重要, 不能丢新", 同时调用方有降级路径处理 `enqueue → False`.
- 注意: enqueue 是**非异步** API; 在 asyncio 协程里直接调用会同步阻塞事件循环.

**何时不要用 `block`**:
- 实时性敏感、调用方在 hot loop 里高频 enqueue (会引入 1s 级抖动).
- 调用方在 signal handler / atexit / shutdown 路径 (此时 sequencer 已
  `_is_shutdown`, enqueue 立刻 drop reason=`shutdown`, 与 policy 无关).

**何时切到 `drop_new`**: 业务上"老事件已经在队列里, 新事件可以丢", 例如
心跳/poll 类重复信号.

---

## §5 default-OFF 不变式

本 spec **不引入任何 env / config 改动**, 不修改业务源码. 实现侧:

- `overflow_policy` 默认 `"drop_oldest"`, 未设 env 时 enqueue 走 nowait 路径,
  bytewise 等价 main HEAD bef3ef6.
- `block` 路径仅在用户显式 `SequencerConfig(overflow_policy="block")` 或
  env 设置时启用.
- `busy_count` 字段仍受 `COCO_ROBOT_BUSY_METRIC` env-gate (robot-013), 与本
  spec 正交.

verify_robot_019.py 的所有锁面均为**静态字面量 + spec doc 短语 + 单次行为
subprocess 实证**, 不修改全局 sequencer 状态, 不依赖网络/真机.

---

## §6 不衍生 fu chain

本 spec 仅为现有 robot-017 docstring + robot-009 enqueue 实现的**外部固化**.
未观察到新 caveat:

- block policy 的 1.0s timeout 字面量化由 robot-017 docstring 已说明.
- `block_timeout` reason 已在 robot-013 `_busy_reasons` 集合, 行为已稳定.
- 若未来需要把 1.0s 暴露为 config 字段, 新立 feature, 不归 robot-019.

backlog 新增数 = 0.

---

## Cross-Reference

- 源码: `coco/robot/sequencer.py` L77-L103 (SequencerConfig.overflow_policy + _VALID_OVERFLOW),
  L333-L382 (enqueue 三策略路径), L384-L424 (_on_enqueue_drop + busy_count gate).
- 前置 feature: robot-007 (overflow_policy 初版), robot-009 (enqueue 统一入口),
  robot-013 (busy_count default-OFF), robot-014 (shutdown_timeout_s 硬化),
  robot-017 (block 语义 docstring 文档化).
- verify: `scripts/verify_robot_019.py`.
- evidence: `evidence/robot-019/verify_summary.json` + `migration_note.md`.
