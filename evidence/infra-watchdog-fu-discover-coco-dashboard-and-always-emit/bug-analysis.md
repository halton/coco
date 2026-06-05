# Bug analysis: watchdog discover coverage + tick visibility

## 现象 1: discover 只识别 daemon + copilot-api
- `_maybe_discover` (`coco/watchdog/__main__.py:61-72`) 仅调用 `reg.discover('daemon', 7447)` 和 `reg.discover('copilot-api', 4141)`
- `Registry.discover` 实现严格基于端口 (`_pid_from_port`), 主进程 `python -m coco` 与 dashboard `python -m coco.dashboard` 没有(或没用) 监听端口 → 永远 discover 不到
- 现实栈: daemon=45165, coco=59817, dashboard=68463, copilot-api=38079, watchdog 自身=69519
  - registry 只录 2/4 service, 监控盲区: coco 主进程 & dashboard 没人盯

## 现象 2: 73s 无任何健康日志
- `tick_once` (旧版) 仅在 service unhealthy 时 emit `health.degraded` + 重启相关事件
- 所有 service healthy → 整轮 tick 完全静默
- watchdog 启动 73s 后 events.log 除了 `watchdog.started` 与可能的 daemon 启动事件外, 不再增长
- 调试 / 看护 / dashboard 红条都无 telemetry 可用

## 修复
1. `process_registry.py`
   - 新增 `discover_by_cmdline(name, *, ports, proc_lister)`: 按 cmdline 关键字找进程
   - 新增 `CMDLINE_RULES` (dashboard / watchdog / coco) + `_cmdline_has_module` 严格匹配 `-m <mod>`
   - 新增 `_list_python_processes` (psutil 优先, ps fallback)
   - 新增 `discover_all`: 调度端口 discover (daemon, copilot-api) + cmdline discover (顺序 dashboard > watchdog > coco)

2. `monitor.py`
   - `SERVICE_NAMES` 加 `dashboard`
   - `HealthCheck.check_dashboard`: HTTP localhost:8765 200 + PID alive (从 registry)
   - `tick_once` 新增 `always_emit` 参数 (None 时读 env `COCO_WATCHDOG_ALWAYS_EMIT`, 默认 True)
     - True → 每次 tick 都 emit health.healthy / health.degraded
     - False → 旧行为, 仅状态变化时 emit
   - `RestartPolicy` 加 `last_healthy` 字段 (per-service, 跨 tick 持久)

## 兼容性
- 事件 schema 不变: `{ts, kind, service, ...}`
- dashboard 红条 filter 看 `kind=health.degraded`, 新增 `health.healthy` 不打扰
- `COCO_WATCHDOG_ALWAYS_EMIT=0` 可一键回旧行为
- `check_coco` 旧逻辑保留 (frame + pid), `check_dashboard` 为新增不冲突

## 注意 (后续 follow-up)
- `__main__._maybe_discover` 仍只调用旧的端口 discover, 真正用 `discover_all` 需在 __main__ 里改 (本 PR 不动 __main__, 留下游)
- 真机 watchdog 重启需用户在 UAT 步骤执行才能验 registry 含 4 service
