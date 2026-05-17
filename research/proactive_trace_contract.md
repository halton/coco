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

## 5. 相关源

- 实现: `coco/proactive_trace.py` (常量 `STATUS_FAIL_TOKENS`, 函数 `is_fail`)
- 验证: `scripts/verify_interact_019.py` (token 白名单回归), `scripts/verify_interact_020.py` (本 feature)
- 历史: interact-015 / interact-018 / interact-019 / interact-020
