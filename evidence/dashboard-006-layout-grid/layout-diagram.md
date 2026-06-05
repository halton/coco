# dashboard-006 布局示意

## 前 (旧版 — 5 panel fixed 硬叠 + perf-chart 写死 800px)

```
+----------------------------------------------------------+
| watchdog-bar (fixed top:0, degraded 时)                  |
+----------------------------------------------------------+
| header                                                   |
+----------------------------------------------------------+
| #wrap (flex)                                             |
|                                          +-------------+ |
|                                          | action-     | |
|                                          | panel       | |
|                                          | fixed top:46| |
|  +-------------------+ +---------------+ +-------------+ |
|  | #cam-pane         | | #side         |               |
|  | flex:1 1 60%      | | flex:1 1 40%  |               |
|  | (MJPEG)           | |               |               |
|  |                   | | perf-chart    | +-------------+ |
|  |                   | | width=800     | | pose-panel  | |
|  |                   | | 写死, 被 40%  | | fixed top:  | |
|  |                   | | side 压扁     | |   260       | |
|  |                   | |               | +-------------+ |
|  |                   | | events ul     |                 |
|  |                   | |               | +-------------+ |
|  |                   | |               | | llm-panel   | |
|  |                   | |               | | fixed top:  | |
|  +-------------------+ +---------------+ |   430       | |
|                                          +-------------+ |
|                                          (768 高直接溢出) |
+----------------------------------------------------------+
```

问题：
- 3 个 fixed panel (action/pose/llm) top: 46 / 260 / 430，无 layout flow，屏幕 ≤ 768 高直接溢出
- perf-chart `width="800"` 写死 → side 40% 实宽 ~600px 时，canvas 800px 横向被压（实际显示宽 ≠ canvas 内部坐标系，图像变扁）
- 无 viewport meta，手机端打开缩放怪异

## 后 (新版 — 3 栏 grid + 右栏 details 折叠 + 响应式)

### desktop ≥ 1024px

```
+----------------------------------------------------------------+
| watchdog-bar (fixed top:0, degraded 时显示, 跨栏告警)            |
+----------------------------------------------------------------+
| header (Coco Live HUD)                                         |
+-------------------+--------------------+-----------------------+
| #cam-pane         | #side              | #control-panel        |
| minmax(400,1fr)   | minmax(400,1.2fr)  | 320px 固定            |
|                   |                    |                       |
|                   | perf-chart         | ▼ 手动动作 (open)     |
|                   | width:100% +       |   [← → ↑ ↓ ... ]      |
|                   | JS resizeCanvas    |   action-status       |
|   MJPEG live      | (auto fit)         |                       |
|   max-width:100%  |                    | ▶ 头部姿态 (collapsed) |
|   max-height:100% | events ul          |                       |
|                   | (timeline)         | ▶ LLM Model           |
|                   |                    |                       |
|                   | status: connected  | ▶ watchdog 状态        |
+-------------------+--------------------+-----------------------+
```

### mobile ≤ 1024px (media query 单栏堆叠)

```
+--------------------+
| watchdog-bar       |
+--------------------+
| header             |
+--------------------+
| #cam-pane          |
| (50vh)             |
+--------------------+
| #side              |
| perf-chart auto-w  |
| timeline           |
+--------------------+
| #control-panel     |
| (CSS order:3)      |
| ▼ 手动动作         |
| ▶ 头部姿态         |
| ▶ LLM Model        |
| ▶ watchdog 状态    |
+--------------------+
```

## 关键技术

| 旧 | 新 |
|---|---|
| `#wrap { display:flex }` 2 栏 60/40 | `#wrap { display:grid; grid-template-columns: minmax(400px,1fr) minmax(400px,1.2fr) 320px }` 3 栏 |
| 3 个 `position:fixed` 浮窗 (action/pose/llm) | 0 个 fixed (仅保留 watchdog-bar 顶部告警条) |
| `<canvas width="800" height="200">` 写死 | `<canvas width="800" height="200">` 初始值, JS `resizeCanvas()` 用 `c.clientWidth` 重设 + `addEventListener('resize',...)` |
| 4 panel 平铺占屏 | 4 `<details>`: action `open` / pose closed / llm closed / watchdog-status closed |
| 无 viewport meta | `<meta name="viewport" content="width=device-width, initial-scale=1">` |
| 无 media query | `@media (max-width:1024px) { #wrap { grid-template-columns: 1fr } }` 单栏 |

## 后端不变 (保留全部 endpoint)

`/`, `/healthz`, `/api/action`, `/api/pose`, `/api/config/llm_model` (GET+POST), `/frame.jpg`, `/stream/camera.mjpg`, `/api/watchdog/recent`, `/ws/events` — 一律未动。
