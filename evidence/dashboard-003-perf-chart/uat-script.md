# UAT: dashboard-003 perf chart

## 目的
真实浏览器里看 timeline 上方折线图 (turn dt 蓝 + tts first_chunk_ms 橙)
随对话/PTT 实时滚动更新。

## 前置
- 当前栈活着: daemon 45165 / coco 59817 / dashboard 52681 / copilot-api 38079
- 浏览器: Safari 或 Chrome

## 步骤
1. 开 `http://localhost:8765`,**硬刷新**: macOS Safari `Cmd+Option+R`, Chrome `Cmd+Shift+R`
2. 确认右半 side pane 顶部出现一张深色 canvas:
   - 标题 "性能 (dt & tts_first_chunk_ms)"
   - 下方 legend: "dt 蓝 (左 Y 0-10s)" + "first_chunk_ms 橙 (右 Y 0-3000ms)"
   - 灰色 0/3s/10s 标 + 0/1500/3000ms 标
3. 跟 coco 说一句话 (或按 PTT) 触发一次完整 turn:
   - 期待: 蓝色 dt 点出现 (1~5s 之间)
   - 期待: 橙色 first_chunk_ms 点出现 (500~2500ms 之间)
4. 连续触发 5+ 次, 观察:
   - 折线连成段
   - 数值落在合理 grid 范围 (蓝低于 10s 上界, 橙低于 3000ms 上界)
5. 触发 50+ 次后, 看到 x 轴左侧最早的点被推走 (50 点滚动窗口)

## 通过标准
- canvas 出现且不报 JS error (DevTools Console 干净)
- 蓝/橙折线随对话出现
- 50 点窗口生效 (>50 turn 后老点丢)

## 失败排查
- canvas 不显示: 硬刷新 dashboard, 确认 `dashboard` 进程是新版 (`curl -s http://localhost:8265 | grep perf-chart`)
- 折线不动: tail `/tmp/coco-stdout.log` 看是否真有 `dt=` / `first_chunk_ms=` 行; 没有则 coco 主链路问题
- 数值溢出: 当前 cap 10s/3000ms; 若实际 > 该值, 折线压顶 (clamp 实现) — 可接受
