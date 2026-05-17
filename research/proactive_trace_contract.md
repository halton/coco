# Proactive Trace 字段 Contract

> 适用范围: `coco.proactive_trace` 模块及所有 `proactive.emit_end` / `emit_trace`
> 调用方（包括业务侧 ProactiveScheduler、mm_proactive、emotion_alert、外部接入方）。
>
> 来源: interact-015 (trace 框架) → interact-018 (emit-end 3 口标准化) →
> interact-019 (status substring → token 白名单) → **interact-020 (本文档化)**。

---

## 1. emit-end 失败判定 (`is_fail`) — 4 个权威字段

`coco.proactive_trace.is_fail(rec)` 按 **OR** 语义判定一条 jsonl 记录是否为失败：

| 字段 | 类型 | 命中条件 | 备注 |
|---|---|---|---|
| `ok` | `bool` | `ok is False` (严格 bool, 不接受字符串 "false") | interact-018 推荐主口 |
| `error` | `str` | `isinstance(str) and .strip() != ""` | 任意非空错误描述 |
| `failure_reason` | `str` | `isinstance(str) and .strip() != ""` | 结构化失败原因, 与 `reason` 区分 |
| `status` | `str` | `status.strip().lower() ∈ STATUS_FAIL_TOKENS` | **白名单原子 token, 见 §2** |

四项任一为 truthy 即视为 fail。其余形态（字段缺失 / truthy 字符串如 `ok="success"` /
`error=""` / `status="no_failure"`）均返回 False。

### 推荐写法（emit 端）

**首选**：使用 `ok=False` (类型强、语义明确、不会被任何字符串误判命中)：

```python
emit_end(stage="mm_proactive", ok=False, error="llm_timeout")
```

**避免**：用 `status="failed"` 等字符串口承载主失败信号。`status` 字段保留为
**历史兼容/调试可读字段**，不是主信号。

---

## 2. `status` 字段白名单 (whitelist) 原子 token (interact-019/020)

`status` 字段在 `is_fail` 中走 **token 精确匹配**（**case-insensitive, strip**），
匹配集合 `STATUS_FAIL_TOKENS`：

```
{ fail, failed, failure, error, errored }
```

匹配语义（interact-019 后）：

```python
status.strip().lower() in STATUS_FAIL_TOKENS
```

### 2.1 命中示例（被识别为 fail）

| 写法 | strip().lower() | 命中? |
|---|---|---|
| `"fail"` | `fail` | YES |
| `"FAILED"` | `failed` | YES |
| `"  Error  "` | `error` | YES |
| `"errored"` | `errored` | YES |
| `"Failure"` | `failure` | YES |

### 2.2 不命中示例（**正确不被误判**）

interact-019 改成 token 精确匹配的核心动机：以下值历史在 substring 模式下被误判：

| 写法 | 含义 | 是否 fail |
|---|---|---|
| `"no_failure"` | 安全/无故障状态 | **NO** ✅ |
| `"failsafe"` | failsafe 安全模式 | **NO** ✅ |
| `"no_fail_today"` | 状态描述 | **NO** ✅ |
| `"success"` | 成功 | NO ✅ |
| `""` / 缺字段 | 未填 | NO ✅ |

### 2.3 **禁止**的复合写法（外部接入方约定）

`is_fail` 走 token 精确匹配后，**以下复合字符串不会被识别为 fail**，会导致
失败被静默吞掉。emit 调用方必须避免：

| 禁止写法 | 原因 | 推荐改写 |
|---|---|---|
| `"RPC_FAILURE"` | 多 token, 不命中白名单 | `ok=False, error="rpc_failure"` |
| `"TASK_FAILED_RETRY"` | 多 token, 不命中 | `ok=False, failure_reason="task_failed_retry"` |
| `"ERROR_5XX"` | 后缀污染 | `ok=False, error="5xx"` |
| `"failed_after_retry"` | 多 token + 下划线 | `ok=False, failure_reason="failed_after_retry"` |
| `"check_failed"` | 多 token | `ok=False, failure_reason="check_failed"` |

**规则简述**：`status` 只允许整段就等于白名单 5 个原子 token 之一（大小写/空格可），
任何复合写法走 `ok` / `error` / `failure_reason` 三口。

---

## 3. 决策表（emit 端选哪个口）

| 场景 | 推荐字段 | 示例 |
|---|---|---|
| 二值成功/失败 | `ok` | `ok=False` |
| 已有 exception/字符串描述 | `error` | `error=str(e)` |
| 业务结构化失败原因 | `failure_reason` | `failure_reason="quiet_state"` |
| 历史 jsonl 调试可读字段 | `status` | `status="failed"` (仅 5 个原子 token 之一) |

reject 路径的 `reason` 字段 (`disabled` / `paused` / `quiet_state` / `cooldown` /
`rate_limit` / ...) **不是** 失败信号，是 `decision=reject` 的归因, 与 `is_fail`
无关。

---

## 4. 兼容性 / 演进规则

- `STATUS_FAIL_TOKENS` 是 **frozenset, 单源真理**（`coco/proactive_trace.py`）。
  本文档列出的 5 个 token 与代码同步；若未来扩展，先改代码常量再改文档。
- 不在白名单的新 token 必须先经过 V0 fingerprint 锁更新 + 验证集回归后才能加入。
- 外部接入方接 emit_end / emit_trace 时，按本文档 §1/§2/§3 约定字段语义。

## 5. `latency_ms` 字段语义按 stage 分类 (interact-021)

来源: interact-018 emit 端 `latency_ms` wire 落地后, Reviewer caveat —
**同一字段名 `latency_ms` 在不同 emit 站点下语义粒度不同**, 下游 metric
聚合 (例如 `scripts/proactive_trace_summary.py` 的 `latency_by_stage`) 时
若不区分 stage 会把 "判定即出" 与 "端到端" 混在一起求 p50/p95, 误导调优方向。

### 5.1 测量起点 — `_lat_start` (单源真理)

`coco/proactive.py::ProactiveScheduler.maybe_trigger` 入口处:

```python
_lat_start = time.monotonic()           # 锁外, 覆盖整段 maybe_trigger
def _lat_ms() -> float:
    return round((time.monotonic() - _lat_start) * 1000.0, 3)
```

- **时钟**: `time.monotonic()` (不受墙钟回拨影响)。
- **单位**: 毫秒 float, 精度 0.001 ms (`round(..., 3)`)。
- **起点**: 每次 `maybe_trigger(now=...)` 调用开始时重置；**与**每次外部 tick
  对应。同一次 `maybe_trigger` 内多次 `_trace_emit` 共享同一起点, **后发的
  emit 的 `latency_ms` ≥ 前发的 emit** (单调非降, "cumulative" / 累积语义)。
- **独立路径** (`emit_emotion_alert`): 用本地 `_ea_lat_start`, 与 maybe_trigger
  无关；语义是该独立调用内自己的端到端 (内部仅一次 `emit_trace`, 数值贴近 0)。

### 5.2 emit 站点 / stage 标签

`maybe_trigger` 调用链上共 **3** 个 `_trace_emit` 站点 + **1** 个 `emit_emotion_alert`
独立路径 `_et` 站点, 合计 **4** 个 emit 站点。`stage` 字段值是动态的（按运行时分支
决定）, 文档按 **emit 站点** 列, 每站点列出可能的 stage 值:

| # | 文件:行 (main HEAD) | 触发条件 | `decision` | 可能的 `stage` 值 | `latency_ms` 语义 |
|---|---|---|---|---|---|
| 1 | `proactive.py:584-592` (emit_emotion_alert 独立路径 `_et`) | 情绪告警告诉 | `admit` | `emotion_alert` | **独立路径自测量**: `_ea_lat_start` → `_et` 本调用内部, 不走 `_lat_start`。语义是单次 emit 自身耗时, 贴近 0 ms |
| 2 | `proactive.py:~943` (arbit_emotion_preempt 抑制 fusion/mm) | emotion_alert 窗口内抢占 fusion_boost / mm_proactive | `reject` | `fusion_boost` 或 `mm_proactive` (按抢占前快照 `_preempt_boost` / `_preempt_mm` 决定) | **判定即出 (reject 前段)**: 锁内, 入口 → 抑制判定完成的累积耗时 |
| 3 | `proactive.py:~973` (should_trigger 拒绝) | `_should_trigger` 返回非 None reason (paused/cooldown/quiet/rate_limit/...) | `reject` | `cooldown_hit` (reason=cooldown 时) / `fusion_boost` / `mm_proactive` / `normal` (按 `_stage_in` 决定) | **判定即出 (reject 终)**: 锁内, 入口 → should_trigger 决定的累积耗时, **不含** LLM/TTS |
| 4 | `proactive.py:~1004` (arbit_winner 预占成功) | 预占成功, 准备进入锁外 LLM/TTS | `admit` (锁内预占点) | `arbit_winner` | **预占即出 (admit 前段)**: 锁内, 入口 → 预占完成的累积耗时, **不含** 锁外 LLM/TTS。**注意**: arbit_winner 不是"端到端", 是 admit 链路的中点 |

> 注 1: admit 端到端 (LLM/TTS 完成后) 当前由 `_do_trigger_unlocked` 内部 `emit_end`
> 负责（见 `coco/proactive_trace.py`）, 不在 `maybe_trigger` 内显式 `_trace_emit`,
> 因此本表只列 3 个 `_trace_emit` + 1 个独立 `_et`, 合计 **4 个 emit 站点**, **6 个
> stage 名** (见 §5.3 清单)。与 interact-018 V2 锁的 4 处 `latency_ms=` kwarg 字面同步。
>
> 注 2: stage 名 `cooldown_hit` 是 reason=cooldown 的特化值; 其余 reject reason
> （`paused` / `quiet_state` / `rate_limit` / `disabled` 等）仍走入口 `_stage_in`
> （`normal` / `fusion_boost` / `mm_proactive`）, **不再细分到独立 stage 名**。

### 5.3 stage 名权威清单 (6 个)

下游统计代码必须假设以下 6 个 stage 名都可能出现在 `latency_ms` 字段携带的
trace 行中:

```
emotion_alert       # 站点 #1 (独立路径), decision=admit
fusion_boost        # 站点 #2/#3, decision=reject (#4 入口快照亦可能出现)
mm_proactive        # 站点 #2/#3, decision=reject (#4 入口快照亦可能出现)
cooldown_hit        # 站点 #3 (reason=cooldown 特化), decision=reject
arbit_winner        # 站点 #4 (锁内预占成功), decision=admit
normal              # 站点 #3 (default 入口快照, 非 fusion / mm), decision=reject
```

注: `normal` 实际是站点 #3 在 `_stage_in == "normal"` 分支下的 emit, 历史上
interact-015/016 已落地, 此处一并登记以让下游 stage 聚合覆盖全集。

### 5.4 admit 端到端 vs reject 判定即出 — 语义差异 (核心 caveat)

`latency_ms` 在不同 stage 下采样点不同, 直接 p50/p95 混合统计会误导:

| stage | 路径 | `latency_ms` 含义 | 典型量级 (sim, 顺序参考) |
|---|---|---|---|
| `emotion_alert` (独立) | 独立 emit 自测量 | 单次 `_et` 调用栈耗时 | ~0.0x ms |
| `cooldown_hit` (reject) | 锁内, 入口 → should_trigger 决定 | **判定即出**, 不含 LLM | < 1 ms (sub-ms) |
| `fusion_boost`/`mm_proactive`/`normal` (reject) | 同上 | **判定即出** | < 1 ms |
| `fusion_boost`/`mm_proactive` (站点 #2 抑制) | 锁内, 入口 → arbit_emotion 抑制 | **判定即出** | < 1 ms |
| `arbit_winner` (admit, 锁内预占) | 锁内, 入口 → 预占完成 | **预占即出**, 不含锁外 LLM/TTS | 1~5 ms |
| (admit 端到端, 由 `_do_trigger_unlocked` emit_end) | 锁外 LLM/TTS 完成后 | **端到端**, 含 LLM/TTS | 数百~数千 ms |

### 5.5 下游聚合规则

`scripts/proactive_trace_summary.py` `latency_by_stage` 输出时:

1. **不可** 跨 stage 求总 p50/p95 — 必须按 stage 单独聚合。
2. **不可** 把 `arbit_winner` 当端到端 admit — 它只是 admit 链路的锁内预占点。
3. **不可** 把 reject 路径 `latency_ms` 当作"主动话题响应时间" — 它只反映
   判定耗时, 与用户感知无关。
4. 真正的"端到端 admit 延迟"看 `emit_end` 一族 (`stage=mm_proactive` 等带
   `ok=True` 与 `duration_ms` 字段的终态记录), 见 `coco/proactive_trace.py`
   `emit_end` 调用方约定。

### 5.6 单调性约定 (cumulative)

同一次 `maybe_trigger(now=t)` 调用内, 多个 emit 共享 `_lat_start`:

- 若同 tick emit_alert (站点 #1) + arbit_emotion 抢占 (#2) + arbit_winner (#4)
  都触发, 则按 emit 顺序, 后发 emit 的 `latency_ms` ≥ 前发 emit 的 `latency_ms`。
- 独立路径 `emit_emotion_alert` 用 `_ea_lat_start`, 不参与 `maybe_trigger` 调用
  内的单调链; 但它本身只有一次 emit, 没有同链多 emit 比较问题。
- monotonic 时钟在 OS 层保证非倒退, `round(..., 3)` 可能让同一 microsecond
  内的两个 emit 出现相等值, 因此约束是 **单调非降** (>=), 不是严格单调递增。

### 5.7 单元锚点 / 验证

- `scripts/verify_interact_018.py`: V2/V3 锁"5 处 emit 都附 latency_ms";
  V4 锁 `latency_by_stage` 按 stage 聚合。
- `scripts/verify_interact_021.py`: 锁本节 5 个 stage 名清单 + 单调非降 +
  文档关键词。

---

## 6. 相关源

- 实现: `coco/proactive_trace.py` (常量 `STATUS_FAIL_TOKENS`, 函数 `is_fail`,
  `emit_trace`, `emit_end`); `coco/proactive.py` (5 个 `latency_ms` emit 站点)
- 验证: `scripts/verify_interact_019.py` (token 白名单回归),
  `scripts/verify_interact_020.py` (status contract),
  `scripts/verify_interact_021.py` (latency stage 语义)
- 历史: interact-015 / interact-018 / interact-019 / interact-020 / interact-021
