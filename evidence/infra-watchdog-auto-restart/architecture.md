# infra-watchdog-auto-restart 架构

## 设计目标

之前 daemon 在用户没看到的情况下死过 14h, coco 没自愈也没告警。一个 reset
整栈过程要走 4 步 (清理 / 启 daemon / 启 copilot-api / 启 coco)。watchdog 把
这个事自动化:

- 每 30s 一轮健康检查
- 发现某 service 死 → 自动重启 (max 3 次失败后放弃)
- 写事件日志供 dashboard 显示红条 (后者留 backlog)
- dashboard 不在监控范围 (是观察者)
- 默认不启 (用户手动 `python -m coco.watchdog`), 避免无意干预跑着的栈

## 监控范围 (3 service, dashboard 不含)

| service | 健康指标 | restart 命令源 |
|---------|---------|---------------|
| daemon (reachy-mini) | TCP `localhost:7447` (IPv4+IPv6 双试) + HTTP `http://localhost:8000/` 2xx | registry.cmdline+env |
| coco (主进程) | `/tmp/coco-frame.jpg` mtime < 60s + (有 PID 注册时) `os.kill(pid,0)` | registry.cmdline+env |
| copilot-api | HTTP `http://localhost:4141/v1/models` 2xx | registry.cmdline+env |

`localhost` 走 `getaddrinfo` 同时尝 IPv4+IPv6 — 当前实测 daemon 仅 bind `[::1]:7447`,
若只用 `127.0.0.1` 会误报 down (Step 1 实证)。

## 模块拆分

```
coco/watchdog/
├── __init__.py          # 包 + __version__
├── __main__.py          # `python -m coco.watchdog` argparse + run_loop
├── process_registry.py  # ~/.cache/coco/processes.json 持久化 (atomic write)
└── monitor.py           # HealthCheck + RestartPolicy + launch_from_registry + tick_once + run_loop
```

### Registry

schema (单 JSON 文件, atomic 写 via tempfile + os.replace):

```json
{
  "daemon": {
    "pid": 45165,
    "started_at": "2026-06-05T12:34:56+00:00",
    "cmdline": ["python", "-m", "reachy_mini.daemon.app.main", "--no-wake-up-on-start", "--no-goto-sleep-on-stop"],
    "env": {"PATH": "...", ...},
    "ports": [7447, 8000]
  },
  "coco": {...},
  "copilot-api": {...}
}
```

发现机制 (`discover(name, port)`):
1. lsof / psutil 通过端口找 PID
2. /proc/{pid}/cmdline + /proc/{pid}/environ (Linux) → ps / psutil (mac)
3. 自动 `register()` + `save()`

降级: 找不到 → 返回 None, registry 留空, restart 时走 `restart.skipped_no_registry` 路径
而非真启子进程。**这是安全 default**: 没注册不重启, 避免乱启错命令搞坏 live 栈。

### HealthCheck

每 service 一个 `check_*` 方法, 返回 `(ok: bool, reason: str)`. 所有外部依赖
(port_checker / http_getter / mtime_checker / now_fn) 都注入, verify 路径完全
在内存里跑。

### RestartPolicy

- `failures_count[name]`: 连续失败计数
- `>= max_restarts` → `given_up[name] = True`, 之后 tick_once 不再 attempt restart
- `record_success(name)` 清零 + 解除 give_up

### tick_once / run_loop

`tick_once` 跑一轮所有 service. healthy → record_success; degraded → emit event +
(若未 give_up) launch_from_registry + emit restart.attempted; give_up → emit
restart.give_up.

`run_loop(once=, max_iters=, sleep_fn=)` 包 tick_once + KeyboardInterrupt 处理 +
配置驱动间隔。

## env 配置 (`WatchdogConfig.from_env`)

| env | 默认 | 范围 |
|-----|------|------|
| `COCO_WATCHDOG_INTERVAL_S` | 30 | clamp 5..600 |
| `COCO_WATCHDOG_MAX_RESTARTS` | 3 | clamp 1..20 |
| `COCO_WATCHDOG_FRAME_MAX_AGE_S` | 60 | clamp 5..3600 |
| `COCO_WATCHDOG_HTTP_TIMEOUT_S` | 5 | clamp 0.5..60 |
| `COCO_WATCHDOG_EVENTS_PATH` | `/tmp/coco-watchdog-events.log` | str |
| `COCO_WATCHDOG_FRAME_PATH` | `/tmp/coco-frame.jpg` | str |

非法值 (abc / -1 等) → clamp 或回退 default, 不抛。

## 事件日志

JSON line 追加到 `COCO_WATCHDOG_EVENTS_PATH`. 格式:

```json
{"ts":"2026-06-05T12:34:56+00:00","kind":"health.degraded","service":"daemon","reason":"tcp_7447_closed","failures":1}
{"ts":"...","kind":"restart.attempted","service":"daemon","attempt":1,"new_pid":99999}
{"ts":"...","kind":"restart.give_up","service":"daemon","failures":3}
{"ts":"...","kind":"watchdog.started","config":{...}}
{"ts":"...","kind":"watchdog.stopped","reason":"loop_exit","iters":2}
```

dashboard 顶部红条消费这个文件 — 留 backlog `infra-watchdog-fu-dashboard-redbar`。

## 安全性 / 不动当前栈

- 默认不启
- `--once` 单 tick smoke 不会 restart (registry 空 → `restart.skipped_no_registry`)
- 即使 `--discover` 抓到 daemon/copilot-api, 第一轮 health 全 PASS 也不会重启
- give_up 后不再 attempt, 仅继续观测 + emit (避免无限重启风暴)
- launch 用 `start_new_session=True` + stdout 重定向 `/tmp/coco-{name}.log`,
  watchdog 自己挂了不影响新起的子进程

## 已知 follow-up

- coco 没端口 → `--discover` 抓不到; 需要 coco main 启动时主动 register
- dashboard 红条消费 watchdog event log (留 backlog)
- daemon restart 后行为是否与 Control.app 启的 daemon 等价 (cmdline 可能差异) — 真机 UAT 校准
- watchdog 自身 self-supervised (谁监控 watchdog) — 留架构 follow-up, 当前 default-OFF 不迫切
