# Reachy Mini App / Control.app 部署模型调研

**日期**：2026-05-08
**状态**：✅ 调研完成 — 路线选择待用户决定

## 核心发现

### Control.app 的 app 是什么

Reachy Mini 有一套**官方的 app 框架**，由 `reachy_mini.apps` 模块定义。任何符合该规范的 Python 包都可以：

1. 本地运行（开发模式）
2. 发布到 Hugging Face Spaces
3. 由 Control.app 从 HF / 本地加载并启动

### App 的最小骨架

源码：`.venv/lib/python3.13/site-packages/reachy_mini/apps/`

```
my-app/
├── pyproject.toml          # 必须声明 entry-points."reachy_mini_apps"
├── README.md
└── my_app/
    ├── __init__.py
    └── main.py             # 含一个继承 ReachyMiniApp 的类
```

`pyproject.toml` 关键段（来自 `apps/templates/pyproject.toml.j2`）：

```toml
[project]
keywords = ["reachy-mini-app"]    # 用于 HF 发现
dependencies = ["reachy-mini"]

[project.entry-points."reachy_mini_apps"]
my_app = "my_app.main:MyApp"
```

App 类骨架（来自 `apps/app.py` + `templates/main.py.j2`）：

```python
import threading
from reachy_mini import ReachyMini, ReachyMiniApp

class MyApp(ReachyMiniApp):
    custom_app_url: str | None = "http://0.0.0.0:8042"   # 可选 settings 页
    request_media_backend: str | None = None              # 可选 backend

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event):
        while not stop_event.is_set():
            # 主循环
            ...
            time.sleep(0.02)

if __name__ == "__main__":
    app = MyApp()
    try:
        app.wrapped_run()    # 自动管理 ReachyMini 上下文 + settings webserver
    except KeyboardInterrupt:
        app.stop()
```

`ReachyMiniApp.wrapped_run()` 做了什么：
- 自动 `with ReachyMini(...) as reachy_mini:` 上下文
- 自动检测 daemon 在不在 localhost（`_check_daemon_on_localhost`）选择 `localhost_only` 还是 `network` 连接模式
- 如果 `custom_app_url` 不为 None，启动 FastAPI settings 服务（用于配置 UI）
- 调用用户的 `run(reachy_mini, stop_event)`
- 处理优雅停止

### Control.app 怎么发现/启动 app

源码：`reachy_mini/apps/manager.py` + `sources/local_common_venv.py`

- **发现**：扫 Python entry points group `reachy_mini_apps`
- **隔离**：每个 app 可以装在独立 venv（`_should_use_separate_venvs`），也可以共享当前 venv
- **启动**：以子进程方式 `python -u -m my_app.main`（**必须可独立 `-m` 跑**）
- **HF Space 安装**：`huggingface_hub` 下载 repo → 装到独立 venv

### 发布

`python -m reachy_mini.apps.app create [name] [path]`：用 Jinja 模板生成骨架
`python -m reachy_mini.apps.app check [path]`：验证 pyproject.toml 与目录结构
`python -m reachy_mini.apps.app publish [path]`：推到 HF Space（需要 hf login）

官方 app store 索引：`pollen-robotics/reachy-mini-official-app-store/app-list.json`

## 与 Coco 当前架构的对比

| 维度 | Coco 现状 | ReachyMiniApp 规范 | 兼容性 |
|---|---|---|---|
| 包管理 | uv + pyproject.toml | pip-installable + entry points | 兼容（uv 也写 pyproject.toml） |
| 入口 | `spike_audio.py`（独立脚本） | `module.main:Class` 子类 ReachyMiniApp | 需重构 |
| ReachyMini 实例管理 | 每个脚本自己 with | `wrapped_run()` 自动管 | 需重构 |
| daemon 连接 | `ReachyMini(spawn_daemon=False)` | 自动检测 localhost / network | 兼容（ReachyMiniApp 更智能） |
| Audio | sounddevice 直连 | 通过 `reachy_mini.media`（gstreamer 后端）或独立 | **解耦决策可能与规范冲突** |
| 主循环 | 暂未实现 | `while not stop_event.is_set(): ...` | 兼容 |
| Settings UI | 暂未实现 | 可选 FastAPI on `custom_app_url` | 兼容（可选） |

## 风险点

### 🔴 风险 1：sounddevice 解耦决策可能与 wrapped_run 冲突

`wrapped_run` 会调用 `ReachyMini(media_backend=...)`，传入 `request_media_backend`（默认 `"default"`）。我们之前发现 reachy-mini 的 audio backend 在没有真机 USB 音频时会失败。

**潜在出路**：
- App 类里设 `request_media_backend = "none"` 或类似（需查 backend 选项）
- 或在 `run()` 里完全不用 `reachy_mini.media`，自己 import sounddevice
- 但 `ReachyMini.__init__` 仍可能尝试初始化 media → 需测

**需验证**：能否在 ReachyMiniApp 框架下完全绕开 reachy-mini 的 media 子系统。

### 🟡 风险 2：venv 隔离可能让 sounddevice 缺失

如果 Control.app 给 app 装独立 venv，需要 `pyproject.toml` 显式声明 `sounddevice` 依赖。我们已经声明了，OK。

### 🟡 风险 3：Mac 麦克权限

App 由 Control.app 子进程启动时，麦克权限是 inherit 自 Control.app 还是子进程独立申请？需测。

### 🟢 兼容点

- entry points 是 Python 标准机制，uv 完全支持
- ReachyMiniApp 框架不要求强制使用所有内置能力（settings、media 都可选）
- `custom_app_url` 可选；不需要 GUI 的 app 可以纯命令行风格

## 三种部署路线

### 路线 A：独立 Python app（当前默认）

- 用户从终端跑 `python -m coco`
- 不进 Control.app 生态
- 不上 HF
- **优点**：简单、自由度高、不受规范束缚
- **缺点**：普通用户用不来；放弃 HF app 生态曝光

### 路线 B：纯 ReachyMiniApp（规范化）

- 用 `reachy_mini.apps.app create` 生成骨架，把 Coco 重构进去
- 发布到 HF Space
- 用户从 Control.app 一键启动
- **优点**：用户友好、生态曝光、自动管 daemon 连接
- **缺点**：需要适配规范；audio 解耦决策可能踩坑

### 路线 C：双模式（推荐）

- 仓库根目录是 Coco 包；既能 `python -m coco`，也能 `pip install .` 后由 Control.app 通过 entry points 启动
- 主类同时是 `__main__` 入口和 `ReachyMiniApp` 子类
- 开发用 A 模式（快速迭代），UAT / 发布用 B 模式
- **优点**：开发 + 发布兼顾；不放弃任何生态
- **缺点**：需要早期就遵守规范（entry point + 类继承），否则后期改造工作量大

## 建议

**路线 C**。因为：

1. 规范化的成本不高（就是加 entry point + 继承一个 base class），但越早做越省事
2. 真机验收时 UAT 走 Control.app 是更真实的环境
3. 双模式不影响开发速度，反而强迫接口边界更清晰

## 给 harness 的输入

如果选 C，下面这些事要进 `feature_list.json`：

- 新增 `infra-001 部署模型决策与骨架`（priority 0，排在 robot-001 之前）
  - verification：用 `reachy_mini.apps.app create` 生成参考骨架；把 Coco 重构成 ReachyMiniApp 子类；`python -m reachy_mini.apps.app check .` 通过
- 修改 `audio-001` 的 evidence：补一条"在 ReachyMiniApp 框架下 sounddevice 仍可用"的验证
- `interact-001` verification 增加"Control.app 启动模式下能完整跑通"

`AGENTS.md` 子系统约定加一段：

```markdown
### app 部署模型
- Coco 是 ReachyMiniApp 子类（继承 reachy_mini.ReachyMiniApp）
- pyproject.toml 声明 entry-points."reachy_mini_apps"
- 开发：python -m coco（绕开 Control.app）
- UAT/发布：reachy_mini.apps.app publish 上 HF → Control.app 启动
- 真机验收必须走 Control.app 模式
```
