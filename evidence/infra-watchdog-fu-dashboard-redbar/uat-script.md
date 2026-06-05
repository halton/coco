# UAT script — infra-watchdog-fu dashboard red bar

## 目的
真机/真 daemon 下验证：watchdog 检测到 daemon down → dashboard 顶部 30s 内显示红条。

## 步骤

1. 启动 dashboard:
   ```
   /Users/halton/work/coco/.venv/bin/python -m coco.dashboard
   ```
   浏览器开 http://localhost:8765/ — 顶部无红条。

2. 启动 watchdog (另一终端):
   ```
   /Users/halton/work/coco/.venv/bin/python -m coco.watchdog
   ```

3. kill 一个被 watchdog 监控的 service (e.g. daemon):
   ```
   pgrep -f 'desktop-app-daemon' | xargs kill
   ```

4. 等 30 秒内, 观察 dashboard 顶部:
   - **预期**: 红色横条出现, 文字含 `⚠ health.degraded service=... at ...` (或 restart.failed / restart.give_up)
   - **不预期**: 红条不出现 / dashboard 卡死

5. watchdog 重启 service 后, 等下一次 10s poll, 红条消失。

## 异步 UAT 项
真机 UAT 不阻 merge (Sim-First); 此 UAT 由用户在方便时手工执行, 结果回填至本目录 `uat-real.log`。
