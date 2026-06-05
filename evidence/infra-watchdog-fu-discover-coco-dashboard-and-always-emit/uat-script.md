# UAT: infra-watchdog-fu discover coco/dashboard + always emit

## 真机 / 端到端验收 (用户手动)

### 前置
- daemon (7447/8000) 已起
- coco 主进程 (`python -m coco`) 已起 (产 frame.jpg)
- dashboard (`python -m coco.dashboard`, 8765) 已起
- copilot-api (4141) 已起
- 旧 watchdog 已停

### 步骤
1. 启动新 watchdog 并 discover:
   ```
   COCO_WATCHDOG_INTERVAL_S=10 /Users/halton/work/coco/.venv/bin/python -m coco.watchdog --discover &
   sleep 2
   cat ~/.cache/coco/processes.json
   ```
   预期: registry 含 4 service (daemon + copilot-api + coco + dashboard), 各自 pid/cmdline/ports 正确

2. 看 event log 30s 后有 health.healthy 行 (always emit mode):
   ```
   sleep 35
   tail -20 /tmp/coco-watchdog-events.log | grep health.healthy
   ```
   预期: 看到 daemon / coco / dashboard / copilot-api 的 health.healthy 行 (各至少 3 条, 因为 10s 间隔 30s 跑 3 轮)

3. 关闭 always emit, 验证旧行为:
   ```
   COCO_WATCHDOG_ALWAYS_EMIT=0 COCO_WATCHDOG_INTERVAL_S=10 \
     /Users/halton/work/coco/.venv/bin/python -m coco.watchdog --discover &
   sleep 35
   tail -20 /tmp/coco-watchdog-events.log
   ```
   预期: health.healthy 仅在第一次 (None→True) 出现, 后续 healthy 不再 emit

### 验证点
- [ ] registry 含 `coco` entry (pid 来自 python -m coco)
- [ ] registry 含 `dashboard` entry (pid 来自 python -m coco.dashboard, ports=[8765])
- [ ] 30s 内 events.log 出现 health.healthy 行 (always emit=1)
- [ ] dashboard 红条 filter 仍兼容 health.degraded 事件 (无视 health.healthy)

### 状态
user_pending — 待用户在物理环境跑一次确认 registry 4 service + 30s healthy event
