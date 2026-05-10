# Coco UAT & Publish Runbook

**目标**：把 Coco 从开发机走通到 Reachy Mini 真机 + Control.app 发布全流程。本文档同时是 phase-2 milestone gate 收尾用的 UAT 剧本。

**适用范围**：infra-publish-flow（phase-2 最后一项）。开发流程的其他细节见 `claude-progress.md` 与 `feature_list.json`。

> ⚠️ **真机相关步骤标 milestone gate**：仅由用户在持有 Reachy Mini Lite 的开发机上执行；自动化（`scripts/verify_publish.py`）只覆盖静态/dry-run 部分。

---

## 0. 开发流程 vs UAT/发布流程

| 维度 | 开发流程 | UAT/发布流程 |
|---|---|---|
| 运行入口 | `uv run python -m coco.main` | Control.app 列表里点 Coco（entry-point 加载） |
| 依赖 | 仓库内 `.venv` | Control.app 自动建独立 venv，从 HF Space 装 |
| daemon | `./init.sh --daemon` 起 mockup-sim（或不起，单进程模式） | Control.app 自带 reachy-mini daemon，独占 8000/7447 |
| 音频 | sounddevice 直连 mac/Linux 麦/扬 | 真机 USB 音频（Reachy Mini Audio device） |
| 摄像头 | `COCO_CAMERA=image:...` / `video:...` 假数据 | `COCO_CAMERA=usb:0` 真摄像头 |
| 适用阶段 | 写代码、跑 verification、smoke | phase milestone gate；发布到 HF；用户日常使用 |

---

## 1. 环境前置

### 开发机（无真机）

- macOS 13+ / Ubuntu 22.04+ / Windows 11；Python 3.13；`uv` 已装
- `./init.sh` 通过（含 smoke）
- 真机相关步骤跳过即可；publish dry-run 不需要真机

### 真机（⚠️ milestone gate）

- [ ] Reachy Mini Lite 主体连电
- [ ] USB 连开发机；颈部/头部活动空间足够
- [ ] **Reachy Mini Audio USB device** 已识别：
  - macOS: `system_profiler SPUSBDataType | grep -i reachy`
  - Linux: `arecord -l | grep -i reachy`
- [ ] 头部 USB 摄像头能枚举（macOS: `system_profiler SPCameraDataType`；Linux: `ls /dev/video*`）
- [ ] HuggingFace 账号（仅 publish 步骤需要）：`hf auth whoami`

---

## 2. 开发模式自检

走 publish 之前先确认开发模式 OK，避免 publish 上去再发现是基线问题。

```bash
cd /path/to/coco
./init.sh                    # smoke 全通
./init.sh --daemon           # 验 mockup-sim daemon 通（先关 Control.app）
uv run python -m coco.main   # 起 Coco 开发模式；Ctrl+C 退出
```

**期望**：

- smoke 三档（audio + ASR + TTS + vision + companion-vision + VAD）全 ok
- `--daemon` 段：`Smoke: robot mockup-sim daemon` 通过（"Zenoh 通"）
- `python -m coco.main` 起来后能看到 idle 微动 / 视觉日志，无未捕获异常

---

## 3. Publish dry-run（自动化覆盖）

```bash
uv run python scripts/verify_publish.py
```

依次跑：

1. `reachy_mini.apps.app check .`（含临时 venv 安装/卸载，~30s）
2. 列 publish candidate artifacts 路径与大小
3. entry-point 静态校验（`coco = "coco.main:Coco"` + `keywords` 含 `reachy-mini-app`）
4. `import coco.main` 到 Class 定义阶段（不起 daemon）

**期望最后一行**：`==> PASS: infra-publish-flow dry-run 全部通过`

任何 FAIL 都是 publish 阻塞项；不要在 FAIL 状态下跑下面的真 publish。

### Publish artifacts 含义

`reachy_mini.apps.app publish` 实际机制：把仓库 git push 到 `https://huggingface.co/spaces/<user>/coco`（参见 `.venv/lib/python3.13/site-packages/reachy_mini/apps/assistant.py` `publish()` 函数）。所以 "publish artifacts" 不是 wheel，而是仓库里会被推上去的文件：

- `pyproject.toml` — 含 entry-point + dependencies + keywords
- `README.md` — 含 HF Space metadata 头
- `index.html` + `style.css` — Space landing page
- `coco/` 包整个目录 — 实际代码
- 其他根目录文件（按 .gitignore 过滤）

---

## 4. 发布到 Control.app（HuggingFace Space）

### 4.1 命令

```bash
# 已登录 HF 后：
uv run python -m reachy_mini.apps.app publish . "<commit message>"
```

参数：

- `[app_path]`：留 `.` 即仓库根
- `[commit_message]`：HF Space 的 git commit message，例如 `"v0.1.0 phase-1 mvp"`
- `--public` / `--private`：第一次发布选 `--public`（让 Control.app 列表里能看到）
- `--official`：仅 Pollen 官方应用使用，不要加
- `--nocheck`：不要加；让它先跑一遍 check

### 4.2 第一次发布前的检查清单

- [ ] `git status` 干净（publish 流程会要求 commit；不希望把脏改 push 到 HF）
- [ ] `hf auth whoami` 能返回用户名
- [ ] `verify_publish.py` PASS
- [ ] 确认仓库名（`Path(app_path).name`）就是 `coco`，不是 fork 后改名错乱
- [ ] 不含敏感数据（HF token / API key 都不能进 README / pyproject）

### 4.3 发布后

- HF Space URL：`https://huggingface.co/spaces/<user>/coco`
- 等 Space build 通过（HF 后台几分钟）
- Control.app 应用商店里搜 "coco" / 按 keyword `reachy-mini-app` 过滤

---

## 5. Control.app 注册 + 启动

### 5.1 在 Control.app 中安装

1. 启动 macOS Reachy Mini Control.app
2. 进入 Apps → 找到刚发布的 `<user>/coco` → 点 Install
3. Control.app 会建独立 venv，从 HF Space pip 安装；此期间不要中断

### 5.2 启动

1. App 列表点 Coco → Run
2. Control.app 把 daemon（8000 / 7447）独占给当前 app；这一过程是它自己管理的，不要再额外起 mockup-sim daemon
3. Coco 自身会通过 `coco.main:Coco.run()` 进 idle + interact 主循环

### 5.3 启动验证（真机 ⚠️ milestone gate）

参考 `docs/uat-real-robot.md` 子系统级 checklist。下面是 phase-2 收尾必须走完的端到端 UAT：

#### 5.3.1 idle + 视觉（companion-001 / companion-002 / vision-001）

- [ ] 起来后 5-10 秒内能看到 idle 微动（颈部小幅度）
- [ ] 站到正前方，机器人能转头朝向（vision-biased glance；companion-002）
- [ ] 离开视野 5 秒，回到默认 idle pattern

#### 5.3.2 PTT 入口（interact-001）

> 仅在 PTT 没被 VAD 接管的环境下走；当前默认是 VAD（interact-003），PTT 走的是 `COCO_PTT_DISABLE=0` + 无 VAD 的早期分支；按真机配置而定。

- [ ] 按 Enter（或 Control.app 暴露的 PTT 按钮，如有）触发录音 N 秒
- [ ] 看到 ASR 转写日志
- [ ] 听到 TTS 回应（USB 音频走真扬声器）
- [ ] 看到机器人头部回应动作（look_left / look_right / nod）

#### 5.3.3 VAD 入口（interact-003）

- [ ] 直接说话，VAD 自动触发（无需按键）
- [ ] cooldown 期内连续说话不重复触发；详见 `coco/vad_trigger.py`
- [ ] 静默场景（远场环境噪声）不误触发
- [ ] 触发后路径同 5.3.2（ASR → 模板回应 → TTS + 动作）

#### 5.3.4 LLM 入口（interact-002）

- [ ] LLM 模板分支启用时（`COCO_LLM_*` env），ASR 后走 LLM 回应而非模板
- [ ] LLM 回应在合理延迟（<5s）内出 TTS
- [ ] LLM 失败时降级到模板回应，不崩溃

#### 5.3.5 真机首次上电 + 关机仪式

- [ ] **wake_up**：Coco app 启动时调用 `reachy_mini.wake_up()`，头/身从松弛位置抬起到 home，无异响、无过冲
- [ ] **goto_sleep**：app 退出（Ctrl+C / Control.app Stop）时调用 `goto_sleep()`，头/身平滑回松弛位置；如果中途断电，下次上电由 wake_up 收拾
- [ ] 异常处置：wake_up 卡住或 goto_sleep 不完成 → kill app 进程后手动断电、重连 USB；记录到 `claude-progress.md`

---

## 6. 常见错误处理

### 6.1 端口占用：Control.app 自带 daemon 抢 8000 / 7447

**现象**：跑 `./init.sh --daemon` 或 `verify_publish.py` 时报端口占用 / Zenoh 连不上。

**原因**：Control.app 启动后会驻留一个 `desktop-app-daemon` 进程占用 8000（HTTP）和 7447（Zenoh）。

**处理**（按需要全干掉）：

```bash
# 看有没有
pgrep -fl 'desktop-app-daemon'
# 看端口
lsof -nP -iTCP:8000 -sTCP:LISTEN
lsof -nP -iTCP:7447 -sTCP:LISTEN
# 干掉（sub-agent 可直接执行，不需问用户；见 CLAUDE.md "Control.app daemon 处理"）
pkill -f 'desktop-app-daemon'
```

干完再跑 `./init.sh --daemon` / `verify_publish.py`。

### 6.2 daemon 未启或挂掉

**现象**：`Smoke: robot mockup-sim daemon` 报 `ReachyMini 客户端连不上`，`/tmp/coco-daemon.log` 含异常。

**两条恢复路径**：

1. **走 init.sh**：先确认没有 6.1 里的 desktop-app-daemon 残留 → `./init.sh --daemon`
2. **手动起 mockup-sim**（debug 时）：

   ```bash
   uv run python -m reachy_mini.daemon.app.main --mockup-sim --deactivate-audio
   # 另起一个 terminal:
   uv run python -c "from reachy_mini import ReachyMini; m=ReachyMini(spawn_daemon=False, media_backend='no_media', timeout=10); print('ok')"
   ```

如果 manual 通过、init.sh 不通过：检查 `scripts/smoke.py` 的 `smoke_daemon` 是否被环境变量篡改。

### 6.3 phase-1 临时豁免撤回时机

phase-1 为了把 Lite SDK 跑起来加了两个豁免：

- **`media_backend="no_media"`**：绕开 Lite SDK 上 GStreamer / `gi` 缺失（见 `scripts/smoke.py` `smoke_daemon`）
- **daemon `--deactivate-audio`**：跳过 daemon 的 audio backend；coco 本身不依赖它（audio 直走 sounddevice）

**何时可以撤回**：

| 豁免 | 撤回前置条件 | 验证方式 |
|---|---|---|
| `no_media` | 真机/开发机已装 GStreamer 1.x + `pygobject`；vision 子系统改走 `reachy_mini.media`（如果决定不再用独立 cv2 路径） | 把 smoke_daemon 中 `media_backend="no_media"` 删掉，`./init.sh --daemon` 仍 PASS；vision-001 verification 仍 PASS |
| `--deactivate-audio` | audio 子系统决定走 reachy-mini media（不在当前路线） | smoke_audio 切到 reachy-mini media 后端；TTS 路径仍 PASS |

当前路线（路线 C 双模式 + audio sounddevice 解耦）下，`--deactivate-audio` **永久保留**；`no_media` 仅在 vision 决定换后端时撤回。

### 6.4 publish 时 HF auth 失败

```bash
hf auth login            # 交互式输 token
# 或非交互：
HF_TOKEN=hf_xxx uv run python -m reachy_mini.apps.app publish . "msg" --public
```

token 在 https://huggingface.co/settings/tokens 生成，权限选 write。

### 6.5 Control.app 装上后启动报 ImportError

通常是 reachy-mini 版本不匹配 / Control.app venv 解析失败。

- 检查 `pyproject.toml` 的 `reachy-mini>=1.4.0` 是否与 Control.app 兼容
- 在 Control.app 日志里看具体 traceback（macOS: `~/Library/Logs/reachy-mini/`）
- 重装：Control.app 里 Uninstall → 再 Install

---

## 7. 回滚

### 7.1 回滚到上一版本

```bash
# 在仓库里：
git checkout <prev-tag>
uv run python -m reachy_mini.apps.app publish . "rollback to <prev-tag>" --public
```

publish 走的是 git push，HF Space 端会显示新 commit；Control.app 重启 app 即拿到回滚版本。

### 7.2 紧急下线

- HF Space 设为 private（`--private`）：Control.app 应用商店不再显示
- 删除 Space：`https://huggingface.co/spaces/<user>/coco/settings` → Delete repository

---

## 8. UAT 通过判据

phase-2 milestone gate 要求以下全部 OK：

- [ ] `verify_publish.py` PASS
- [ ] interact-002 / vision-001 / companion-002 / interact-003 全部 `passing`
- [ ] 真机走完 §5.3 所有 checkbox
- [ ] Reviewer fresh-context 评审 runbook 完整度（covers phase-1 全部 9 个 feature 真机最小 UAT 路径）
- [ ] `claude-progress.md` 决策导航追加一行 "phase-2 真机 UAT 通过 YYYY-MM-DD"

子系统级深度 UAT（audio / robot 各自的物理验收）见 `docs/uat-real-robot.md`，与本文档配合使用。

---

## 9. 参考

- `research/control-app-deployment-research.md` — 部署模型调研，路线 C 决策依据
- `docs/uat-real-robot.md` — 子系统级真机 UAT 剧本
- `.venv/lib/python3.13/site-packages/reachy_mini/apps/app.py` — `ReachyMiniApp` 基类 + CLI
- `.venv/lib/python3.13/site-packages/reachy_mini/apps/assistant.py` — `check()` / `publish()` 实现
- `scripts/verify_publish.py` — 本仓库 publish dry-run 自动化
