# interact-026 migration note

## 背景
backlog: `interact-021-backlog-stale-doc-numbers` (interact-021 Reviewer caveat).

interact-018 把 `latency_ms` wire 落地到 emit 端后, interact-021 docstring 与
`research/proactive_trace_contract.md` §5.7/§6 内字面"5 处 emit / 5 个 stage 名"
与代码真实值 (4 处 `latency_ms=` kwarg / 6 个 stage 名) 不一致。下游统计代码
若按 doc 字面 5 处去校验, 会得到 stage 漏覆盖结论 (实际 normal 字面也在 set 中)。

## 数字纠偏对照

| 项 | 旧字面 (stale) | 新字面 (代码真实) | 来源 |
|---|---|---|---|
| `latency_ms=` kwarg 数量 | 5 处 | **4 处** | `coco/proactive.py` `src.count("latency_ms=") == 4` |
| stage 名清单大小 | 5 个 | **6 个** | `verify_interact_021.STAGE_NAMES` set 真实大小 |
| emit 站点构成 | (未明示) | 1 emit_emotion_alert + 3 maybe_trigger 内 `_trace_emit` | `interact-018 V2 锁` |
| stage 名 (按 §5.3) | (未明示) | `{emotion_alert, fusion_boost, mm_proactive, cooldown_hit, arbit_winner, normal}` | `verify_interact_021.STAGE_NAMES` |

## 改动范围 (verify-only)
- `scripts/verify_interact_021.py`: docstring 5→4/5→6 文字纠偏 + STAGE_NAMES 注释 5→6 (注释/docstring 纯文字, 无运行时逻辑改动; V0-V5 仍 PASS)
- `research/proactive_trace_contract.md`: §5.7 / §6 文字纠偏 (4 处 emit / 6 个 stage 名 / 4 个 latency_ms emit 站点); 补 verify_interact_026 锚点
- `scripts/verify_interact_026.py`: 新建, V0-V4 锁面
- `evidence/interact-026/`: 本 feature 的 evidence

**0 业务源码改动**: `git diff --stat coco/` 空。不动 `coco/proactive.py` 实现, 不动 `coco/proactive_trace.py`, 不动任何运行时逻辑或字段语义。

## verify 锁面

`scripts/verify_interact_026.py` 5 子项:

- V0 fingerprint sha256 锁 (verify_021 + contract doc + proactive.py)
- V1 代码常量 == doc 字面量: `latency_ms=` count == 4 且 STAGE_NAMES count == 6
- V2 doc 关键短语锁: 新字面 (4 处 emit / 6 个 stage 名) 命中, 旧 stale (5 处 / 5 个) 清零
- V3 邻近 verify 静态回归: 018/021/022/023/024/025 全 rc=0
- V4 smoke 11/11 PASS

## Default-OFF / bytewise 等价
不引新 env, 不改运行时, 既有 main 路径 bytewise 等价 (`git diff coco/` 空).

## 不衍生 fu chain
本 feature 修复后无新发现的 caveat (V0-V4 全 PASS 干净 + Reviewer 待评审).
