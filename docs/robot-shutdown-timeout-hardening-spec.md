# SHUTDOWN_TIMEOUT_S inf/nan 输入硬化 spec (权威)

来源: robot-018 (P183 phase-22), 锁面 robot-014 (priority 160) 既有硬化逻辑。

## 1. 背景

`coco.robot.sequencer.SequencerConfig.shutdown_timeout_s` 控制 `shutdown(wait=True, timeout=…)` 默认值；signal handler / atexit 共用。
未硬化前 (robot-012 时代), 输入降级仅 `try/except ValueError` 兜底负值, 未拦 `float('inf')` / `float('nan')`:

- `'inf'`: `float('inf')` 解析成功跳过 ValueError, 也通过 `<=0` 检查, 最终 `shutdown_to=inf`, 让 `thread.join(timeout=inf)` 永久阻塞;
- `'nan'`: 解析成功, `nan <= 0 == False` 漏网, 后续比较行为未定义。

robot-014 (commit `cefa1d0`) 完成双层硬化, robot-018 做 verify-only doc 锁面 + 跨路径完整覆盖 verify (无业务源码改动)。

## 2. 双层硬化路径 (robot-014 已实现, robot-018 锁面)

### 2.1 env 路径: `sequencer_config_from_env()`

文件 `coco/robot/sequencer.py`:

```python
raw_st = env.get("COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S", "").strip()
try:
    shutdown_to = float(raw_st) if raw_st else 2.0
    if not math.isfinite(shutdown_to) or shutdown_to <= 0:
        shutdown_to = 2.0
except ValueError:
    shutdown_to = 2.0
```

### 2.2 dataclass 路径: `SequencerConfig.__post_init__`

```python
def __post_init__(self) -> None:
    try:
        v = float(self.shutdown_timeout_s)
    except (TypeError, ValueError):
        v = 2.0
    if not math.isfinite(v) or v <= 0:
        v = 2.0
    object.__setattr__(self, "shutdown_timeout_s", v)
```

两条路径都返回**默认 2.0**, 不抛异常, 不告警 (静默降级)。

## 3. 降级矩阵 (锁面)

| 输入 (env 或 dataclass 参数) | 类型 | 结果 |
|---|---|---|
| `"0.5"` / `0.5` | 合法正 float | 0.5 (直通) |
| `"1.0"` / `1.0` | 合法正 float | 1.0 (直通) |
| `"30.0"` / `30.0` | 合法正 float | 30.0 (直通) |
| `"0.001"` / `0.001` | 合法极小正 float | 0.001 (直通) |
| `""` | 空字符串 (仅 env 路径) | 2.0 (默认) |
| `"abc"` | ValueError 路径 (env) | 2.0 (默认) |
| `"not-a-number"` | TypeError/ValueError (dataclass) | 2.0 |
| `"0"` / `0` / `0.0` | <=0 兜底 | 2.0 |
| `"-1"` / `-1` / `-2.5` | <=0 兜底 | 2.0 |
| `"inf"` / `"Infinity"` / `"+inf"` / `"INF"` / `float('inf')` | 非有限 | 2.0 |
| `"-inf"` / `"-Infinity"` / `float('-inf')` | 非有限 | 2.0 |
| `"nan"` / `"NaN"` / `"NAN"` / `"+nan"` / `"-nan"` / `float('nan')` | 非有限 | 2.0 |
| `None` (dataclass only) | TypeError | 2.0 |

## 4. Default-OFF 不变式

- 合法 float 路径 **bytewise 等价 main** (输入直通, 仅在异常输入路径降级)
- 不引入新 env gate; 硬化是缺陷修复 / fail-safe, 不需要旋钮
- env=未设 → `shutdown_timeout_s=2.0` (默认), 与 main 等价

## 5. 下游消费点

`coco/robot/sequencer.py` 中 `shutdown_timeout_s` 被以下站点消费:

- `RobotSequencer.shutdown(wait, timeout)`: `timeout` 缺省读 config.shutdown_timeout_s
- `install_signal_shutdown_handler(timeout_s=...)`: signal handler 调 shutdown 时传入
- `install_atexit_shutdown_handler(timeout_s=...)`: atexit hook 调 shutdown 时传入

确保非有限值不会传到 `thread.join(timeout=...)`, 杜绝永久阻塞。

## 6. verify 覆盖

`scripts/verify_robot_018.py` 跨路径双覆盖锁面:

- V0 fingerprint: `math.isfinite` import + `_post_init_` 钩子 + env 解析点同时命中
- V1 spec doc 锁面: 本文 12 个关键短语锁面 (`math.isfinite`, `2.0`, `__post_init__`, `COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S`, `thread.join`, `inf`, `nan`, `default-OFF`, `bytewise`, `robot-014`, `robot-018`, `verify-only`)
- V2 env 路径 inf/nan 降级 (subprocess)
- V3 dataclass 直接构造 inf/nan 降级 (subprocess)
- V4 合法值直通锁面 (env + dataclass 双路径)
- V5 边界 (0 / -1 / "" / None / -inf) 降级
- V6 smoke ./init.sh PASS
- V7 regression robot-012/013/014/015/016/017 verify rc=0

## 7. 不衍生 fu chain

robot-018 完成后无新 caveat 入 backlog。任何后续需要变更降级默认值 / 加 WARN 日志 / env gate 化的诉求, 单独立 feature 走规划。

## 8. 关联文件

- `coco/robot/sequencer.py` (业务源码, 不改)
- `scripts/verify_robot_014.py` (既有 verify)
- `scripts/verify_robot_018.py` (本任务新增 — 跨路径双覆盖 + doc 锁面)
- `docs/robot-shutdown-timeout-hardening-spec.md` (本文)
