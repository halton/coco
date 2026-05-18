# ProactiveScheduler → RobotSequencer setter lifecycle spec (锁面)

scope: 锁定 `coco/proactive.py` 中 `ProactiveScheduler.set_robot_sequencer(...)` 的 **lifecycle 状态机 + 调用顺序约束 + 多次注入语义 + probe 失败回退 + atexit 协作**, 防止跨 feature 重构静默打破注入语义或 default-OFF 不变式。

source backlog: robot-008-backlog-setter-lifecycle
feature: robot-021 (priority 201, phase 24, verify-only)
upstream: robot-008 (注入入口 + atexit) / robot-010 (lifecycle 校验: shutdown 拒绝 / 重复注入 warn) / robot-015 (enqueue-first 串行) / robot-016 (setter audit warn-once) / robot-017 (sync fallback audit) / robot-020 (warn-once key 字面量锁)

---

## §1 setter 角色 (ProactiveScheduler → RobotSequencer)

`ProactiveScheduler.set_robot_sequencer(sequencer)` 是 ProactiveScheduler 与 RobotSequencer 间**唯一**注入入口。语义:

- **注入**: 把外部已构造的 `RobotSequencer` 实例 (或 mock / None) 写到 `self._robot_sequencer`, 由 `_do_trigger_unlocked` 在主动开口后 best-effort 触发轻量 nod 动作 (enqueue-first; 无 enqueue 方法时走 `seq.run([_nod])` 同步 fallback)。
- **清除**: `set_robot_sequencer(None)` 把 `_robot_sequencer` 还原为 None, 后续 emit 跳过 robot 路径, bytewise 与 default-OFF 等价。
- **唯一入口**: 业务代码不应直接读写 `_robot_sequencer` 字段, 必须经过 setter (受 lifecycle 校验 + warn-once 路径覆盖)。

调用顺序约束 (main.py 当前布局已满足):
1. 构造 `ProactiveScheduler` 与 `RobotSequencer` 各自实例
2. 调用 `set_robot_sequencer(seq)` 注入 (在 `_loop` 启动**前**)
3. 调用 `ProactiveScheduler.start()` 起背景线程
4. 进程退出时 `RobotSequencer.atexit` (robot-008) 处理本地 worker 卸载; ProactiveScheduler 不主动 unset。

---

## §2 lifecycle 状态机

setter 状态机 (按调用前后 `self._robot_sequencer` 取值 + 入参 `sequencer` 组合):

| # | 调用前 prev | 入参 new | 行为 | 结果 _robot_sequencer | warn-once key (env=1) |
|---|---|---|---|---|---|
| S0 | None | None | no-op (无 dup 分支, 无 probe) | None | (无) |
| S1 (first-set) | None | seq(健康) | probe is_shutdown → False → 写入 | seq | (无) |
| S2 (first-set, shutdown 拒绝) | None | seq(`is_shutdown()==True`) | logger.warning 拒绝, **不写入** | None | (无, 直接 return) |
| S3 (re-set-same) | seq | seq (同一对象) | dup 分支命中: `id(prev)==id(new)`, 字面 dup WARNING + 覆盖 | seq (不变) | `("dup", id(seq), id(seq))` |
| S4 (re-set-diff) | seq_a | seq_b (新对象) | dup 分支 WARNING + 覆盖 | seq_b | `("dup", id(seq_a), id(seq_b))` |
| S5 (clear) | seq | None | dup 分支跳过 (new is None), 直接覆盖为 None | None | (无) |
| S6 (probe-fail) | None | seq(`is_shutdown()` 抛异常) | fail-soft → WARNING + 仍按"未 shutdown"继续注入 | seq | `("probe-fail", id(seq), type(e).__name__)` |
| S7 (probe-fail, env=1 重复) | None | seq (同 exc 类型, set 已含) | 降为 logger.debug suppressed, 继续注入 | seq | (set 已含, debug) |

> 状态转移的**唯一**触发点是 setter 入口; `_do_trigger_unlocked` 等消费侧只读 `_robot_sequencer` snapshot, 不改写, 不参与 lifecycle 校验。

---

## §3 warn-once 行为 (引用 robot-020 spec)

setter 路径覆盖两种 warn-once key (字面量见 `docs/robot-warn-once-keys-spec.md`):

- **setter dup**: `("dup", id(prev), id(new))` — 仅在 `prev is not None and new is not None` 进入此分支。包含 S3/S4。
- **setter probe-fail**: `("probe-fail", id(sequencer), type(e).__name__)` — 仅当 `is_shutdown()` 探针自身抛异常时触发, 不阻塞注入。

env gating:
- `COCO_ROBOT_SETTER_LIFECYCLE_AUDIT=1` → 启用 dedup, 同 key 第二次起降为 `log.debug("...suppressed warn-once...")`。
- env 未设 / 非 "1" → 完全跳过 dedup, set 始终空, 每次都走 `log.warning`, bytewise 等价 robot-010 行为。

robot-020 已锁字面量重命名风险 (key tag / set 属性名 / env 名), 本 spec 不再重复, 仅锁 lifecycle 维度。

---

## §4 env gating: COCO_ROBOT_SETTER_LIFECYCLE_AUDIT

唯一控制变量: `COCO_ROBOT_SETTER_LIFECYCLE_AUDIT`。

| env 值 | dedup | set 内容 | 主路径副作用 |
|---|---|---|---|
| 未设 / `""` / 任意非 `"1"` | OFF | `_setter_audit_seen` 始终空 | 无 (bytewise 等价 main) |
| `"1"` | ON | 累积 dup / probe-fail key tuple | 无 (仅决定日志级别) |

主路径 (拒绝 shutdown / 写入 _robot_sequencer / dup 覆盖) 与 env 完全无关 — env 只控制**日志级别**(WARNING ↔ DEBUG)。

---

## §5 default-OFF 不变式

- `ProactiveScheduler()` 构造时 `_robot_sequencer = None`, `_setter_audit_seen = set()`。
- 不注入 sequencer → `_do_trigger_unlocked` robot 路径整段 no-op, bytewise 等价无 robot-008 实现。
- 注入 + env=OFF → 主路径行为完全等同 robot-010 (始终 WARNING dup / probe-fail), 仅 set 永远空。
- 注入 + env=ON → 主路径行为不变, set 累积, 同 key 降级 DEBUG。

> 不变式核心: lifecycle audit 是**纯日志层**特性, 不影响 `_robot_sequencer` 取值轨迹、不影响 `_do_trigger_unlocked` 执行路径、不影响 RobotSequencer 自身 atexit。

---

## §6 atexit 协作 (引用 robot-008)

- RobotSequencer 在自己构造时 `atexit.register(self.shutdown)` (robot-008), 进程退出时本地 worker 自然卸载, **不需要** ProactiveScheduler 反向 unset `_robot_sequencer`。
- ProactiveScheduler.stop() 只停自己的 `_loop` 背景线程, 不调 `RobotSequencer.shutdown` (避免双重 stop)。
- 进程退出顺序: Python 解释器关停 → atexit → RobotSequencer.shutdown → daemon worker 停; `_robot_sequencer` 字段在此期间引用过期对象, **但永不会被再次消费** (loop 已停)。

---

## §7 不衍生 fu chain

本 spec doc 仅锁定既有 robot-008/010/015/016 已实现的 setter lifecycle 状态机, 不引入新行为, 不动业务源码 (`coco/proactive.py` 0 diff), 不衍生新 backlog。Reviewer minor findings 若涉及 spec 描述微调, 应回写本文件而非新建 robot-022。

---

## 锚点 (供 verify_robot_021 V2 锁面)

robot-021 / setter lifecycle / state machine / first-set / re-set-same / re-set-diff / clear / probe-fail / atexit / COCO_ROBOT_SETTER_LIFECYCLE_AUDIT / default-OFF / bytewise / verify-only / robot-008 / robot-010 / robot-015 / robot-016 / robot-020 / RobotSequencer / ProactiveScheduler / set_robot_sequencer / _robot_sequencer / _setter_audit_seen / refuse to inject / overwriting existing sequencer
