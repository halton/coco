# Spike: 音频路径决策（已结案）

**日期**：2026-05-08
**状态**：✅ 完成 — 决定 audio 与 robot 解耦

## 最终决定

**架构**：robot 控制走 ReachyMini/Zenoh，音频走 sounddevice 直连 mac 麦克。两条路径独立，不通过 daemon 中转。

**理由**：
- `--mockup-sim` daemon 仍要求 USB 音频硬件（"No Reachy Mini Audio USB device found"）
- 加 `--deactivate-audio` 后 daemon 起来了，但 `ReachyMini(spawn_daemon=False)` 客户端 init 仍挂死（推测客户端也要做 media setup）
- sounddevice 一行验通：MacBook Air Mic 可采到非零数据，权限 OK

**已安装依赖**：`sounddevice` (via `uv add`)

## 调试时踩到的坑（环境层）

### 1. Backend 区分
| flag | backend |
|---|---|
| `--sim` | MuJoCo（需 `pip install reachy_mini[mujoco]`） |
| `--mockup-sim` | mockup_sim（轻量、无物理） |
| 无 | 真机 |

### 2. venv shebang 锁路径
旧 venv 的脚本 shebang 写死了 `reachy-mini`（单 h）路径；目录实际是 `reachhy-mini`（双 h）。
→ 修复：`rm -rf .venv && uv sync`
→ 教训：`reachy-mini-daemon` 这种入口脚本不可移植，子进程一律用 `.venv/bin/python -m reachy_mini.daemon.app.main`

### 3. GStreamer
mockup-sim daemon 启动需要 `gi`（PyGObject）。
→ `brew install gstreamer` + `uv pip install --index-url https://gitlab.freedesktop.org/api/v4/projects/1340/packages/pypi/simple gstreamer==1.28.0`
→ 即便如此，audio backend 仍要求硬件 USB 音频。

### 4. Reachy Mini Control.app
其 daemon 带 `--desktop-app-daemon` flag，外部 Zenoh 客户端连不上。
→ 干净 spike 时必须先退出该 app。

## 给 spec 的输入

- **架构原则**：audio 子系统独立，可测；robot 子系统独立，可在 mockup-sim 验动作
- **测试策略**：audio 用 wav 文件直喂；robot 用 mockup-sim daemon
- **真机验收**：作为 milestone gate，不卡住模拟开发
