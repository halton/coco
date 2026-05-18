# latency_ms admit/reject/cooldown 三 stage 语义 Contract

> 适用范围: `coco.proactive` 模块 ProactiveScheduler 通过 `coco.proactive_trace.emit_trace`
> 输出的 `proactive.trace` 行中的 `latency_ms` 字段。
>
> 来源链: interact-015 (trace 框架) → interact-018 (`latency_ms` wire + `_is_fail`
> 标准化) → interact-020 (proactive_trace_contract.md 文档化) → interact-021
> (latency_by_stage 聚合) → interact-022/023 (stage 维度 fixture 覆盖) →
> **interact-024 (本文档: admit/reject/cooldown 三 stage 语义差异权威锁面)**。
>
> 性质: **verify-only 文档锁面**, 0 业务源码改动。本文档是 interact-018 Reviewer
> caveat 升级 backlog (`interact-018-backlog-latency-stage-semantics-doc`) 的收口。

---

## 1. 三 stage emit 路径

`latency_ms` 字段在 `coco/proactive.py` 中由共享起点 `_lat_start = time.monotonic()`
驱动, 通过闭包 `_lat_ms()` 在每个 emit 站点计算 `round((monotonic() - _lat_start) * 1000, 3)`。
本 contract 关注 `decision` 维度的三类终点 (与 `proactive_trace_contract.md` §5.3
六 stage 全集相容):

### 1.1 admit 路径 (decision="admit")

源码站点: `coco/proactive.py` `ProactiveScheduler.maybe_trigger` 锁内预占成功后:

```python
_trace_emit(
    "arbit_winner", _candidate_id, "admit",
    ts=t,
    stage_in=str(_stage_in or "normal"),
    boost_level=(consumed_boost_level if consumed_boost_level else ""),
    latency_ms=_lat_ms(),
)
```

stage 字面量: `arbit_winner`。还有独立路径 `emotion_alert` (decision="admit",
独立 `_ea_lat_start = time.monotonic()` 自测量, 见 §2.4)。

### 1.2 reject 路径 (decision="reject", 非 cooldown)

源码站点: `coco/proactive.py` `_should_trigger` 返回非空 reason 且 reason != "cooldown":

```python
_stage_out = "cooldown_hit" if reason == "cooldown" else (_stage_in or "normal")
_trace_emit(
    _stage_out, _candidate_id, "reject",
    reason=str(reason), ts=t,
    latency_ms=_lat_ms(),
)
```

stage 字面量: `fusion_boost` / `mm_proactive` / `normal` 其中之一 (按 `_stage_in`
入口侦测决定, 不命中时 default `normal`)。reason 取值 (来自 `_should_trigger`):
`paused` / `no_face` / `low_power` / `not_idle` / `rate_limit` 等 (cooldown
排除)。

### 1.3 cooldown_hit 路径 (decision="reject", reason="cooldown")

源码站点: 与 §1.2 同一 emit, 但 reason == "cooldown" 时被 `_stage_out` 三元式
特化为字面量 `cooldown_hit`:

```python
_stage_out = "cooldown_hit" if reason == "cooldown" else (_stage_in or "normal")
```

stage 字面量: `cooldown_hit` (与 §1.2 reject 共用 emit 行, 仅 stage 名不同)。

---

## 2. latency_ms 起点 / 终点语义对照表

| stage | decision | 起点 (`_lat_start` 设定时机) | 终点 (`_lat_ms()` 求值时机) | 语义 |
|---|---|---|---|---|
| `arbit_winner` | admit | `maybe_trigger` 入口, 锁外, `time.monotonic()` | 锁内预占成功后 emit; **不含锁外 LLM/TTS** | 调度判定 + 锁内预占耗时 (微秒级到几 ms) |
| `fusion_boost` / `mm_proactive` / `normal` | reject | 同上 (共享 `_lat_start`) | `_should_trigger` 返回非空 reason 后 emit | 调度判定耗时 (判定即出) |
| `cooldown_hit` | reject | 同上 (共享 `_lat_start`) | `_should_trigger` 返回 reason="cooldown" 后 emit | 同上, cooldown 命中也是判定即出 |
| `emotion_alert` | admit | **独立** `_ea_lat_start = time.monotonic()` (告警发完后) | 同一 try 块内 emit | 仅 trace emit 自身耗时 (≈ 0), 不含告警业务 |

**关键差异**:

- `arbit_winner` (admit) 与三 reject stage 共享同一个 `_lat_start`, **绝不**包含
  锁外 LLM/TTS 等真实业务执行 (锁内预占后即 emit, 业务在 emit 之后才发生)。
- 三 reject stage 是"判定即出": 从 `_lat_start` 到 emit 之间只有锁内 `_should_trigger`
  调用 + 一些计数器自增, 无 IO, 无业务。
- `emotion_alert` 是独立路径自测量 (与上述 `_lat_start` 无关), `_ea_lat_start`
  设在告警业务**之后**, emit 在其紧邻 try 内, 因此 `latency_ms` 仅反映 emit_trace
  自身, ≈ 0。

**单调非降**: 同一 `maybe_trigger` 调用内若发生多次 emit (如 arbit 抢占 + 后续
入口 trace + 最终 emit), 共享 `_lat_start` 保证后发 emit 的 `latency_ms ≥` 前
发 (单调非降, 不要求严格递增, 因为 monotonic 在同一调用内可能 round 后相等)。

**跨 maybe_trigger 不可比**: 每次 `maybe_trigger` 入口重置 `_lat_start`, 跨调
用的 `latency_ms` 之间无单调关系。

---

## 3. 已知 caveat (下游聚合方必读)

### 3.1 `latency_ms` 不可跨 stage 求总 p50/p95

理由: admit 与 reject 起止语义完全不同 (admit 含预占 + 锁竞争, reject 仅判定),
混合分布无运营意义。`scripts/proactive_trace_summary.py` 的 `latency_by_stage`
聚合已按 stage 维度分桶, 下游消费方 (dashboards / SRE 告警) 必须延续此约定。

### 3.2 admit 路径 `latency_ms` **不**包含 LLM/TTS

`arbit_winner` admit emit 在锁内预占完成后立即触发, 紧接其后的锁外 LLM 调用
+ TTS 播放耗时 (通常数秒) **不**计入 `latency_ms`。若下游需要端到端用户感知
延迟, 必须在 LLM/TTS 完成站点另开 emit (本仓库目前未开此 emit, 由 backlog
未来 feature 决定是否引入, 本 contract 不衍生 fu chain)。

### 3.3 cooldown_hit `latency_ms` 同样不包含决策等待时间

`cooldown_hit` 反映"判定即出"耗时, 不包含**距上次 trigger 还剩多少 cooldown 时间**。
若下游需要 cooldown 剩余等待时间, 必须读取 `_last_proactive_ts` 与 `cooldown_s`
自行计算, **不要**误读 `latency_ms` 为剩余等待。

### 3.4 reject 路径 stage 名取决于入口侦测 `_stage_in`

reject (非 cooldown) 的 stage 字面量 (`fusion_boost` / `mm_proactive` / `normal`)
取决于 `_stage_in` 入口侦测时的 state, 不是 reject 原因本身。下游若要按 reject
"原因" 聚合, 应用 `reason` 字段 (取值见 §1.2), 不要按 stage。

### 3.5 emotion_alert `latency_ms` 是 trace 自测量, 非告警业务耗时

`emotion_alert` 的 `_ea_lat_start` 在告警业务 (emit_end) **之后**设定, 因此
`latency_ms` 仅反映 trace emit 自身 (≈ 0)。**不要**用 `emotion_alert.latency_ms`
评估告警业务延迟。

---

## 4. env gating 与 default-OFF 不变式

| env | default | 行为 |
|---|---|---|
| `COCO_PROACTIVE_TRACE` | unset / "0" | `emit_trace` 立即 return, 不写 trace jsonl, 无 IO 副作用 |
| `COCO_PROACTIVE_TRACE=1` | (opt-in) | 六 stage 全部 emit 带 `latency_ms` kwarg |

**default-OFF 不变式**: env 未设或为 "0" 时, 本仓库与未引入 latency_ms wire 之前
bytewise 等价 (业务源码逻辑分支无变化, monotonic 测量在 `_lat_start = ...` 行
确实执行, 但 emit_trace no-op 不产生任何外部可观察副作用)。本 contract 的 verify
脚本 V3 通过 subprocess 启 ProactiveScheduler 一次 maybe_trigger, 断言 OFF 时
**无** `proactive.trace` jsonl 行写出 (set_emit_override 捕捉为空)。

---

## 5. 不衍生 fu chain (interact-024 收口约束)

本 contract 是 interact-018 Reviewer caveat 的**文档化收口**, **不**衍生新的源
码改动 chain。本次 verify 中若发现新 caveat:

- 只入 `feature_list.json` `backlog` 段 (priority=999, status="backlog",
  phase=null), **不**立即开新 feature。
- 由 phase-N+1 (或更后) 按 priority 整体排序时再考虑是否升级为 active feature。

理由: phase-22 收官在即, 后续 fu chain 风险高于收益; interact-018 已有
proactive_trace_contract.md 单源文档, 本 contract 是按 stage 维度的子视图,
不重复造轮子。

---

## 6. 下游消费方注意点 (operational checklist)

1. `latency_by_stage` 聚合时**禁止**跨 stage 求 total p50/p95。
2. admit 与 reject 的 `latency_ms` 分布不可比 (前者含锁内预占, 后者仅判定)。
3. 评估"用户感知调度延迟"必须另开 LLM/TTS 完成站点 emit, **不能**复用
   `arbit_winner.latency_ms`。
4. 评估 cooldown 剩余等待时间必须读取 `_last_proactive_ts` + `cooldown_s`,
   **不能**用 `cooldown_hit.latency_ms`。
5. reject 按"原因"聚合必须用 `reason` 字段, **不能**用 stage 字面量。
6. `emotion_alert.latency_ms` 仅反映 trace emit 自身, **不**反映告警业务延迟。

---

**Last updated**: interact-024 (P184, phase-22)
**Source**: `coco/proactive.py` `_lat_start` / `_lat_ms()` 闭包, emit 站点见 §1。
**Single source of truth (邻近)**: `research/proactive_trace_contract.md` §5。
