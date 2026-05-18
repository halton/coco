# ProactiveScheduler Block / Fallback / Cooldown 策略

> robot-032 / robot-009-backlog-block-policy-doc
>
> 本文系统化记录 `coco/proactive.py` 中 `ProactiveScheduler` 的
> block、cooldown、fallback、setter lifecycle 策略，以及相关 env gate。
> docs-only，不改业务源码。

## 概述

`ProactiveScheduler` 负责在用户长时间无交互、或多模态融合（vision-007 /
interact-014 / emotion alert）发出 boost 信号时，主动触发一次问候 / 关怀
话题。为了避免噪声、抖动和泛滥 WARNING，调度器在多处设置了
block、cooldown、fallback warn-once 节流机制。

设计原则：

- **default-OFF**：所有新增策略默认与 main bytewise 等价；只有显式
  env gate=1 时才启用增强行为
- **warn-once**：失败 / 异常路径首次 logger.warning，后续同 key 降级
  logger.debug，避免长生命周期 hot-restart 场景下日志泛滥
- **boost 不绕过 cooldown**：interact-014 boost level 仅缩放 cooldown
  系数，最小裁剪后仍执行 `since < cooldown` 检查

## Block 策略

| Block 来源 | 触发条件 | 决策 reason |
|---|---|---|
| idle_threshold | `idle_for < idle_threshold_s` | `idle` |
| rate_limit | 1 小时窗口内 trigger 数 ≥ `max_per_hour` | `rate_limit` |
| cooldown | `since < cooldown_s`（含 boost 缩放） | `cooldown` |
| arbit emotion_alert window | `ARBIT 开 且 t - _last_emotion_alert_ts < ARBIT_EMOTION_WINDOW_S` | `cooldown_hit`（特化） |

所有 reject 路径在 `interact-015 trace` 下统一打 stage 标签；
`cooldown` 在 trace 出口被特化为 `cooldown_hit` 以便观测。

外部 trigger 源（gesture bridge 等）通过
`register_external_trigger(t)` 把自己写入 `_last_proactive_ts` +
`_last_interaction_ts`，使下一轮 `_should_trigger` 自动识别为 cooldown。

## Cooldown 策略

三个时间戳，各自语义独立：

### `_last_proactive_ts`

- 上一次成功 trigger 的时刻
- 写入点：`maybe_trigger` 命中、`register_external_trigger`、
  `register_caption_trigger`
- 读出点：`_should_trigger` 的 cooldown 分支、`is_in_cooldown` 公开 API
- 缩放：interact-014 boost level → `_ARBIT_BOOST_COOLDOWN_SCALE`
  系数（0.5 / 其他=0.5）；boost 不绕过最小 cooldown 裁剪

### `_last_interaction_ts`

- 上一次"用户活动"时刻，重置 idle 计时
- 写入点：`note_interaction`、`maybe_trigger` 命中、外部 trigger 注册
- 读出点：`idle_for = t - _last_interaction_ts`

### `_last_emotion_alert_ts`

- 上一次 emotion_alert 时刻（情绪窗口聚合命中）
- 30 分钟 cooldown 由 `EmotionMemoryWindow` 自带，不叠加 scheduler cooldown
- ARBIT 开时，alert 窗口（`ARBIT_EMOTION_WINDOW_S`）内强制走普通路径，
  多半因 scheduler cooldown skip，避免与 idle 路径双触

## Fallback 策略

### sync fallback warn-once (robot-017)

- 触发：注入的 sequencer 对象无 `enqueue` 方法，回退到 `seq.run([_nod])`
  同步路径（legacy / mock 兜底）
- 节流：`_sync_fallback_audit_seen` 集合
  - key 形态 `("sync-fallback", id(seq))`
  - 首次：`logger.warning`
  - 再次：`logger.debug`（suppressed warn-once）
- default-OFF 等价 main：仅在 `seq.run` 抛异常时才发原始 WARNING

### enqueue fallback warn-once (robot-030)

- 触发：`robot_sequencer.enqueue` 抛异常
- 节流：`_fallback_warned: bool`（per-instance one-shot）
  - 首次：`log.warning("[proactive] robot_sequencer.enqueue failed ...")`
  - 后续：`log.debug("(suppressed warn-once): ...")`
- 避免持续失败时大量噪音

### offline fallback (interact-012)

- 标志位：`_offline_fallback_active`（由 `OfflineDialogFallback` 在离线
  降级期间 set True）
- MM 路径若 `_offline_fallback_active=True` 则 `skip_llm=True`，走
  seed（mm hint 模板），不调 LLM
- 统计：`stats.mm_llm_fallback_offline` 自增

## Setter lifecycle 策略 (robot-016)

- env gate：`COCO_ROBOT_SETTER_LIFECYCLE_AUDIT=1`
- default-OFF 时：与 main bytewise 等价（每次 setter 异常都 WARNING）
- ON 时：`_setter_audit_seen` 集合做 warn-once 去重
  - 首次：`logger.warning("setter ...")`
  - 再次：`logger.debug("(suppressed warn-once): ...")` 或
    `logger.debug("(suppressed warn-once, prev=..., new=...)")`
- 适用场景：长生命周期 hot-restart 下，setter 反复触发 WARNING 日志泛滥

## Env gate 一览

| Env Var | 默认 | 作用 |
|---|---|---|
| `COCO_PROACTIVE_ARBIT` | OFF | interact-014 boost-level cooldown 缩放 + emotion alert 仲裁 |
| `COCO_PROACTIVE_TRACE` | OFF | interact-015 trace 输出 stage / decision / reason |
| `COCO_ROBOT_SETTER_LIFECYCLE_AUDIT` | OFF | robot-016 setter lifecycle warn-once 去重 |
| `COCO_PROACTIVE_COOLDOWN_S` | `DEFAULT_COOLDOWN_S` | 全局 cooldown 秒数（10.0~7200.0 裁剪） |

所有 gate 均 default-OFF，OFF 时与 main bytewise 等价。

## 章节标题列表（供 verify_robot_032 用）

- `## 概述`
- `## Block 策略`
- `## Cooldown 策略`
- `### _last_proactive_ts`
- `### _last_interaction_ts`
- `### _last_emotion_alert_ts`
- `## Fallback 策略`
- `### sync fallback warn-once (robot-017)`
- `### enqueue fallback warn-once (robot-030)`
- `### offline fallback (interact-012)`
- `## Setter lifecycle 策略 (robot-016)`
- `## Env gate 一览`
