# infra-watchdog-auto-restart UAT 真机脚本

**default**: watchdog **不自动启**, 此 UAT 由用户手动触发。

## 前置

当前 live 栈 (示例 PID, 实跑前用 `lsof` 重查):

- daemon: PID 45165, listening `[::1]:7447` (IPv6 only) + `*:8000`
- coco main: PID 59817 (frame tap → `/tmp/coco-frame.jpg` 每 ~1s)
- dashboard: PID 52681 (观察者, 不监控)
- copilot-api: PID 38079, listening `*:4141` (IPv6)

## Step 1: 启 watchdog (single-tick smoke)

```bash
cd /Users/halton/work/coco
/Users/halton/work/coco/.venv/bin/python -m coco.watchdog --once --quiet
echo "rc=$?"
tail -10 /tmp/coco-watchdog-events.log
```

期望:
- rc=0
- events.log 出现 `watchdog.started` + `watchdog.stopped`, 无 `health.degraded` 行
  (因 IPv6 daemon + coco frame fresh + copilot-api 4141 都活)

## Step 2: discover + 持续 loop

```bash
/Users/halton/work/coco/.venv/bin/python -m coco.watchdog --discover --interval 30 &
WATCHDOG_PID=$!
echo "watchdog launched PID=$WATCHDOG_PID"
sleep 35  # 等一轮 tick
cat ~/.cache/coco/processes.json | python3 -m json.tool
```

期望:
- `~/.cache/coco/processes.json` 含 `daemon` 与 `copilot-api` 两项 (含 PID + cmdline + env + ports)
- `coco` 项可能为空 (没有端口 discover, 需要 main.py wire register, 见 Step 4 follow-up)

## Step 3: 故意 kill daemon, 观察 watchdog 30s 内 restart

```bash
kill 45165   # daemon
date
tail -F /tmp/coco-watchdog-events.log
# 等 30s 内出现:
#   {"kind":"health.degraded","service":"daemon","reason":"tcp_7447_closed",...}
#   {"kind":"restart.attempted","service":"daemon","attempt":1,"new_pid":<NEW>,...}
# 再等 30s, 应观察到 health 恢复 (degraded 不再出现)
lsof -iTCP:7447 -sTCP:LISTEN -n -P
# 应见新 PID
```

期望:
- watchdog 在下一轮 tick (<=30s + interval) 检测到 daemon down
- restart.attempted 出现, new_pid 写入
- 新 daemon 进程绑回 7447 (IPv6) + 8000

注: 当前 watchdog 用 registry 里的 cmdline+env 启动子进程; 若原 daemon 由 Control.app 启,
其 cmdline 可能与 user-shell 启动有差异 (尤其 Control.app daemon 走特殊路径), Step 3 可能
restart 后仍 down — 此为 follow-up: 需要 main.py 启动时主动 register (准确 cmdline).

## Step 4: max_restarts give_up

```bash
# 模拟一直 kill (用 max_restarts=2 加速)
/Users/halton/work/coco/.venv/bin/python -m coco.watchdog --discover --interval 5 --max-restarts 2 &
sleep 3
# 在另一终端循环 kill daemon 直到 watchdog give-up
for i in 1 2 3; do kill -9 $(lsof -ti:7447) 2>/dev/null; sleep 6; done
grep -E "(give_up|attempted)" /tmp/coco-watchdog-events.log | tail -10
```

期望:
- 2 次 restart.attempted 后出现 restart.give_up
- 第 3 次 kill 后不再 restart.attempted, 仅 health.degraded 持续

## 清理

```bash
kill $WATCHDOG_PID
rm -f /tmp/coco-watchdog-events.log
rm -f ~/.cache/coco/processes.json   # 若不希望持久化
```

## 已知限制

- coco 进程没有端口 → `--discover` 抓不到; 需要 coco main 启动时主动调
  `Registry.register("coco", pid=os.getpid(), cmdline=sys.argv, env=os.environ, ports=[])`
  并 `.save()`; 该 wire 留 follow-up (此 feature 仅做 watchdog 本体)
- watchdog 默认不启 — 是用户显式动作
- dashboard 不在监控列表 (是观察者); 红条显示留 backlog `infra-watchdog-fu-dashboard-redbar`
