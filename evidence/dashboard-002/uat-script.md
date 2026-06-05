# dashboard-002 真机 UAT 浏览器手测脚本

**前置**：daemon (mockup-sim 或真机) + coco 主进程 + dashboard 已启动；浏览器开 `http://localhost:8765`。

注意：feat 分支上的本次 evidence 仅验证 sim/fake 闭环。本 UAT 文档描述真按按钮的预期行为，作为 user_pending 真机 UAT。

## 步骤

1. 打开 `http://localhost:8765`
2. 页面右上角应见 "手动动作" 面板，含 10 个按钮：
   - 方向：← → ↑ ↓
   - 头部：点头 / 摇头 / 歪左 / 歪右
   - 状态：睡觉 / 起来
3. 依次点击每个按钮：
   - 按钮下方的 `#action-status` 应在 ~0.5s 内变为 `<action> ...` → `<action> -> ok`
   - dashboard 进程不应崩
   - daemon (mockup-sim 或真机) 应能观察到对应动作（mockup-sim: zenoh 上有 target update；真机：reachy 头部物理移动）

## 预期 / 验收

- 按钮 click → POST /api/action → 200 + `{status:"ok", rc:0}`
- 期间 dashboard 不阻塞 (不卡 timeline 渲染)
- 多按钮快速连击：各 subprocess 独立，不互相干扰
- 错按 (URL 直接 POST `{action:"x"}`) → 400

## 已知风险 / 异步项

- subprocess 跑 ReachyMini connect ~1-2s，按钮反馈延迟 ≈ 该时长
- 若 daemon 不可达 (端口被占 / spawn_daemon=False)，subprocess 会 timeout → 返回 `{status:"timeout"}` 或 stderr 含 connect 错；dashboard 不崩
- timeline 显示 `action=` 字段需要 coco 主进程实际触发动作时打印 `[coco][vad] ... action=<name>`（dashboard 只解析展示，不主动生成）

## status

- sim verify: PASS (verify_dashboard_002_action_buttons 9/9)
- 真机 UAT: pending (用户在方便时浏览器手测)
