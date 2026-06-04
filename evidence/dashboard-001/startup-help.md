# dashboard-001 启动帮助

## 两条命令

```bash
# 终端 A: 启动 coco 主进程并开启 frame tap (default-OFF)
COCO_DASHBOARD_FRAME_TAP=1 /Users/halton/work/coco/.venv/bin/python -m coco

# 终端 B: 启动 dashboard 独立进程
/Users/halton/work/coco/.venv/bin/python -m coco.dashboard
```

浏览器: <http://localhost:8765/>

## env 调参

| env | 默认 | 说明 |
|---|---|---|
| `COCO_DASHBOARD_FRAME_TAP` | (unset) | `1` 让 coco 主进程 dump jpeg 到磁盘 |
| `COCO_DASHBOARD_FRAME_STRIDE` | `3` | 每 N 帧 dump 一次 (5fps cam → ~1.7Hz dump) |
| `COCO_DASHBOARD_FRAME_PATH` | `/tmp/coco-frame.jpg` | dump / 读取路径 |
| `COCO_DASHBOARD_FRAME_QUALITY` | `70` | jpeg 质量 1-100 |
| `COCO_DASHBOARD_LOG_PATH` | `/tmp/coco-stdout.log` | 事件 tail 源 |
| `COCO_DASHBOARD_HOST` | `127.0.0.1` | dashboard 监听 host |
| `COCO_DASHBOARD_PORT` | `8765` | dashboard 监听端口 |
| `COCO_DASHBOARD_STREAM_FPS` | `10` | MJPEG 推帧上限 |

## 架构

- coco 主进程: face_tracker `_tick` 每 N 帧原子写 `/tmp/coco-frame.jpg`
- dashboard 进程: FastAPI + uvicorn
  - `GET /` → HTML 单页
  - `GET /frame.jpg` → 当前帧 (缺失返回 1x1 placeholder PNG)
  - `GET /stream/camera.mjpg` → multipart MJPEG 流
  - `GET /ws/events` → WebSocket 推结构化事件 (tail /tmp/coco-stdout.log)
  - `GET /healthz` → liveness
- 两进程解耦, dashboard 起停不影响 coco 主进程
