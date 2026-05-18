# robot warn-once keys spec (锁面)

scope: 锁定 `coco/proactive.py` 中 ProactiveScheduler 内部三种 warn-once dedup 机制的 **key tuple 字面量 + dedup 范围 + env gating**, 防止 key 名/形态被无意中重命名导致 dedup 静默失效。

source backlog: robot-015-backlog-v3-warn-once-rename
feature: robot-020 (priority 194, phase 23, verify-only)
upstream: robot-015 (enqueue-first 锁面) / robot-016 (setter lifecycle audit) / robot-017 (sync fallback audit)

---

## §1 三种 warn-once 总览

ProactiveScheduler 在 `coco/proactive.py` 维护两个 dedup set:

1. `self._setter_audit_seen: set` — 覆盖 `set_robot_sequencer(...)` 路径的两个子场景:
   - **setter dup** — 重复注入 sequencer (prev != None 且 new != None) 时的 WARNING dedup
   - **setter probe-fail** — `is_shutdown()` 探针自身抛异常时的 fail-soft WARNING dedup
2. `self._sync_fallback_audit_seen: set` — 覆盖 `_do_trigger_unlocked` 走 `seq.run([_nod])` 同步兜底路径 (sequencer 无 enqueue 方法, legacy/mock) 时的 fallback-前置 WARNING dedup。

三种 warn-once 全部 **default-OFF, env-gated**: env 未设时完全跳过 dedup 检查 (始终 logger.warning, set 始终空), bytewise 等价无 audit 行为。

---

## §2 key tuple 结构 + dedup 范围

| # | 场景 | key tuple 字面量 | dedup 范围 |
|---|---|---|---|
| 1 | setter dup | `("dup", id(prev), id(new))` | 同一 (旧 sequencer 对象, 新 sequencer 对象) 内存身份对, 整个 scheduler 生命周期仅首次 warn |
| 2 | setter probe-fail | `("probe-fail", id(sequencer), type(e).__name__)` | 同一 sequencer 实例 + 同一异常类名, 整个 scheduler 生命周期仅首次 warn |
| 3 | sync fallback | `("sync-fallback", id(_seq))` | 同一 sequencer 实例, 整个 scheduler 生命周期仅首次 warn |

字面量 anchor (源码必须出现):
- `("dup", id(`
- `("probe-fail", id(`
- `("sync-fallback", id(`

> **重命名风险**: 若把 `"dup"` 改成 `"duplicate"` 或 `"probe-fail"` 改成 `"probe_fail"`, 旧的 dedup set 不再命中, **每次都会重新 warn**, 静默退化为无 warn-once 状态。本 spec + verify_robot_020.py 字面量锁面用于阻止这类无意重命名。

---

## §3 env gating

| env 变量 | 控制 | 默认 |
|---|---|---|
| `COCO_ROBOT_SETTER_LIFECYCLE_AUDIT` | setter dup + probe-fail warn-once | 未设 / 非 `"1"` → OFF |
| `COCO_ROBOT_SYNC_FALLBACK_AUDIT` | sync fallback warn-once | 未设 / 非 `"1"` → OFF |

env=OFF 时, 源码 `_audit_on = ... == "1"` 为 False, dedup 命中分支完全短路, set 永远空, 每次都走原 `log.warning` 路径 (bytewise 等价 robot-010/main 行为)。

---

## §4 重命名风险列表

以下重命名会让 dedup 失效, 必须由 verify_robot_020.py V1 字面量锁直接捕获:

1. tag 字面量重命名: `"dup"` / `"probe-fail"` / `"sync-fallback"` 任一被改
2. set 属性名重命名: `_setter_audit_seen` / `_sync_fallback_audit_seen` 任一被改
3. env 变量名重命名: `COCO_ROBOT_SETTER_LIFECYCLE_AUDIT` / `COCO_ROBOT_SYNC_FALLBACK_AUDIT` 任一被改
4. key tuple 元素被静默调换或减少 (如把 `("dup", id(prev), id(new))` 改成 `("dup", id(new))`) — set 命中域改变, 旧条目不再生效

---

## §5 default-OFF 不变式

- env 未设 → set 始终空 → 每次重复事件都正常 `log.warning`, bytewise 等价无 audit 实现
- env=1 → set 累积, 同 key 第二次起降为 `log.debug("...suppressed warn-once...")`
- 主代码路径 (enqueue-first / 注入 / fallback 行为) 与 audit OFF/ON 完全无关; audit 仅决定日志级别。

---

## §6 不衍生 fu chain

本 spec doc 仅锁定既有 robot-015/016/017 已实现的 warn-once key 命名, 不引入新行为, 不动业务源码 (`coco/proactive.py` 0 diff), 不衍生新 backlog。Reviewer minor findings 若涉及 spec 描述微调, 应回写本文件而非新建 robot-021。

---

## 锚点 (供 verify_robot_020 V2 锁面)

robot-020 / warn-once / `_setter_audit_seen` / `_sync_fallback_audit_seen` / `("dup", id(prev), id(new))` / `("probe-fail", id(sequencer), type(e).__name__)` / `("sync-fallback", id(_seq))` / `COCO_ROBOT_SETTER_LIFECYCLE_AUDIT` / `COCO_ROBOT_SYNC_FALLBACK_AUDIT` / default-OFF / bytewise / rename / dedup / verify-only / robot-015 / robot-016 / robot-017
