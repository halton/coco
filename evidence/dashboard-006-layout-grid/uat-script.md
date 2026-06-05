# dashboard-006 — UAT 脚本 (浏览器硬刷新)

## 前置
- daemon (45165) / coco (59817) / dashboard (68463) / copilot-api (38079) / watchdog (72094) 持续运行 — 不动
- 本 feature 仅改 `coco/dashboard/app.py` (HTML/CSS/JS)，dashboard 服务**未重启**，需用户在浏览器侧硬刷新看效果

## 步骤

1. 打开浏览器到 `http://localhost:<dashboard_port>/` (默认 8765 或当前 dashboard 监听端口)
2. **硬刷新** Cmd+Shift+R (macOS) / Ctrl+F5 (Win/Linux) 清缓存
3. 观察：

### A. 三栏布局 (desktop ≥ 1024px 宽)
- [ ] 左栏：camera MJPEG 流 (`/stream/camera.mjpg`) 占左侧 ~1fr
- [ ] 中栏：性能折线图 `<canvas id="perf-chart">` 自适应宽度（不再被压扁到 800px 写死后右侧大白边）+ 事件 timeline `<ul id="events">` 在下
- [ ] 右栏 (320px 固定宽)：4 个 `<details>` 折叠面板按顺序排列

### B. <details> 折叠
- [ ] **手动动作** 默认展开 (`open`)：10 个按钮 ← → ↑ ↓ 点头 摇头 歪左 歪右 睡觉 起来
- [ ] **头部姿态 (rad)** 默认折叠，点击 summary 展开后看到 Pitch/Yaw/Roll 三滑条 + 回中按钮
- [ ] **LLM Model** 默认折叠，展开后下拉框含 gpt-4o-mini / gpt-4o / gpt-4.1 / claude-sonnet-4.5 / claude-opus-4.7 / gemini-2.5-pro 6 项
- [ ] **watchdog 状态** 默认折叠，展开后看到「✓ 无告警 (last poll HH:MM:SS)」或最近 degraded 信息

### C. perf-chart 动态宽度
- [ ] 浏览器窗口宽度从 1400px 慢慢拖到 1100px：canvas 跟随中栏宽度自动重绘，无横向白边、无内容被裁
- [ ] DevTools Network/Console 无 `drawChart` JS 错误

### D. 响应式 (≤ 1024px 单栏堆叠)
- [ ] DevTools 切到 iPhone 14 Pro (390x844) 或拖窗口到 < 1024px
- [ ] 布局变为：camera 顶部 → timeline 中部 → control-panel 底部 (CSS `order:3` 保证)
- [ ] 仍可点开 4 个 `<details>`，按钮可点

### E. 旧功能回归
- [ ] 点「点头」按钮 → action-status 显示 `nod -> ok`
- [ ] 拖 Pitch 滑条 → pose-status 显示 `pose -> ok` (debounced 200ms)
- [ ] 切 LLM Model → llm-status 显示 `ok (~30s 生效)`
- [ ] 若主动 `kill -9 <copilot-api-pid>` 触发 watchdog degraded：顶部红条 (`#watchdog-bar`) 出现且 watchdog 状态 panel 显示同步告警；恢复后红条消失

## 通过条件
A/B/C/D/E 全部勾选即视觉验收 PASS。

## 备注 (Sim-First 异步项)
- 真机扬声器 / 麦克风 / 摄像头 / 电机表现不在本 feature scope（仅 UI 布局变更）
- dashboard 服务需重启 (`./init.sh --daemon` 或重启 coco) 才能拉到新 HTML；本 feature 仅 commit on feat 分支，不重启 dashboard
