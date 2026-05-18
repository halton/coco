# robot-018 migration note (verify-only doc 锁面)

## 决策
robot-014 (commit cefa1d0, priority 160) 已对 `SHUTDOWN_TIMEOUT_S` **双路径硬化**:

1. env 路径 `sequencer_config_from_env()` — `math.isfinite()` 检查 + `<=0` 兜底 → 2.0
2. dataclass 路径 `SequencerConfig.__post_init__` — 同样检查 → 2.0

故 robot-018 退化为 **verify-only doc 锁面**:

- 0 业务源码改动 (`coco/robot/sequencer.py` 与 main bytewise 等价)
- 新增 `docs/robot-shutdown-timeout-hardening-spec.md` (权威 spec)
- 新增 `scripts/verify_robot_018.py` (跨路径双覆盖 + spec doc 锁面)

## 覆盖增量 (relative to verify_robot_014.py)

| 维度 | robot-014 verify | robot-018 verify |
|---|---|---|
| env 路径 inf/nan | V2/V3 ✓ | V2 ✓ (11 case 含正负 INF/Infinity) |
| dataclass 路径 inf/nan | V4 ✓ | V3 ✓ (含 None / 'not-a-number') |
| 合法值直通 | V1 ✓ | V4 ✓ (env+dataclass 双路径) |
| 边界 <=0 | V1/V4 ✓ | V5 ✓ (env+dataclass 双路径) |
| spec doc 锁面 | (无 doc) | V1 ✓ 12 关键短语 |
| 下游 verify 行为回归 | V5 ✓ (含 robot_008..013) | V6 静态锁面 (避 N² 时间爆炸) |

robot-018 不替代 robot-014 verify, 二者并行存在, 互补覆盖。

## Default-OFF 不变式

- 合法 float (含 2.0 默认) bytewise 等价 main
- 不引入新 env gate; 硬化是缺陷修复 / fail-safe
- env 未设 → 2.0 (默认), 与 main 等价

## Fu chain

不衍生。任何后续降级行为变更 (改默认 / 加 WARN / env gate) 单独立 feature。
