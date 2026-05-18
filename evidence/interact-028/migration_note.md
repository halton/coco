# interact-028 migration note

## Source
- backlog: `interact-016-backlog-doc-polish`
- 范围: doc polish + verify 锁面 (verify-only, 0 业务运行时变更)

## Direction
针对 interact-016 留下的 doc polish backlog (N-1 / N-2) 做精炼;
C-1 / C-5 仍为 doc-only marker (接入真 LLM backend hook 时再处理, 本任务不动)。

## Changes

### `coco/proactive_trace.py` (doc polish + N-1 sync)

1. **N-1 修复**: `_RESERVED_TRACE_KEYS` 集合新增 `"taskName"`,
   与 `coco.logging_setup.JsonlFormatter._RESERVED` 双向同步。
   - `taskName` 是 Python 3.12+ `logging.LogRecord` 新增字段 (asyncio task 名);
     若业务从 `extra` 渠道注入同名 key, stdlib 在 `LogRecord.__init__` 内
     `raise KeyError(...)` 会把整条 emit 事件吞掉。
   - default-OFF 不变式保持: `emit_trace` 在 `COCO_PROACTIVE_TRACE` 未设时
     立即 return, 不触达 `_RESERVED_TRACE_KEYS` 过滤逻辑;
     ON 时仅多过滤 1 个 key (更安全, 不泄漏新字段)。

2. **N-2 修复**: emit_trace reserved kwargs 段头注释精炼。
   - **旧**: "logging reserved: logging.LogRecord 的内置字段 ... 抛 KeyError 把事件吞掉"
     (隐含"Python 3.13 KeyError"的过时描述, 让读者误以为是 3.13 specific)。
   - **新**: 明确"`logger.info(..., extra=...)` 若 extra 中含与 LogRecord 内置同名的
     key, stdlib 在 `LogRecord.__init__` 内显式 `raise KeyError(...)`(行为在所有受支持
     CPython 版本上一致: 3.10/3.11/3.12/3.13; 非 3.13 特有)"。
   - 加 cross-ref 说明: "本集合与 `coco.logging_setup.JsonlFormatter._RESERVED`
     同源, 新增字段须**双向同步**"。

### `scripts/verify_interact_028.py` (新建 verify 锁面)

6 项 V 子项:
- **V0** fingerprint sha256 锁 (proactive_trace + logging_setup + self)
- **V1** doc polish 短语锁 (5 个关键短语: 所有受支持 CPython 版本 / 双向同步 /
  interact-028 / Python 3.12+ asyncio task / interact-016 C-4)
- **V2** `_RESERVED_TRACE_KEYS ⊇ logging_setup._RESERVED` 同源校验 (集合差 == ∅
  且 `taskName` 在两个集合都在)
- **V3** cross-ref 完整性 (`JsonlFormatter._RESERVED` 实体存在 / type 是
  set|frozenset / 含 LogRecord 最小公共子集)
- **V4** 邻近 verify 回归 (interact-018/021..027 全 rc=0)
- **V5** smoke 11/11 PASS (`./init.sh` rc=0)
- **V_n** evidence summary 自写

## Non-changes (重要)

- **0 业务源码 runtime 行为变更**: default-OFF 路径字节级等价
- 未改 `emit_trace` / `record_llm_usage` / `_emit` 函数体
- 未改 `KNOWN_STAGES` / `KNOWN_DECISIONS` / `STATUS_FAIL_TOKENS` / `is_fail`
- 未升级 SDK
- 0 测试削弱: 全部新增 V 子项 (additive), 邻近 verify 全 rc=0 回归

## Backlog status

- N-1 ✅ closed (taskName 入 `_RESERVED_TRACE_KEYS`)
- N-2 ✅ closed (注释精炼 + cross-ref 强化)
- C-1 ⏸ doc-only marker (cooldown_hit boost 重复入账精度, 真 LLM backend hook 接入再处理)
- C-5 ⏸ doc-only marker (token chars/2 估算, 真 LLM usage hook 接入改用 backend 真值)

C-1/C-5 留为 backlog `interact-028-backlog-c1-c5-real-llm-hook` 候选?
本任务范围内未新增 backlog (sim-first 无真 LLM backend, polish 无意义)。

## Verify result
全 6 子项 PASS, evidence: `evidence/interact-028/verify_summary.json`。
