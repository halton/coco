# Coco 真机 UAT 执行 checklist（uat-phase4）

> 适用范围：phase-1~4 全部 sim-passing feature 在真机上的累积验收。本单按部就班执行，遇到 partial PASS / FAIL 直接照 D 段回填 evidence。

---

## A. 部署前 checklist

### A.1 硬件物理检查
- [ ] Reachy Mini 电源 / 指示灯
- [ ] USB-C 数据线（不是仅充电）
- [ ] USB 麦克插好、物理静音键关
- [ ] 扬声器通路（Reachy USB 音频 or Mac 默认输出）
- [ ] 摄像头线序 / 外接 USB cam
- [ ] 工作台 30cm 净空

### A.2 macOS 权限（System Settings → Privacy & Security）
- [ ] 麦克风 ✓ Terminal + Control.app
- [ ] 摄像头 ✓ Terminal + Control.app
- [ ] 辅助功能 / 输入监控（PTT 需要）

### A.3 仓库状态
```bash
cd /Users/halton/work/coco
pwd
git log --oneline -1
git status
./init.sh
```

### A.4 端口冲突排查
```bash
lsof -nP -iTCP:7447 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
pgrep -fl desktop-app-daemon
```

### A.5 设备识别 smoke
```bash
.venv/bin/python -c "import sounddevice as sd; print(sd.query_devices())"
.venv/bin/python -c "import cv2; c=cv2.VideoCapture(0); ok,f=c.read(); print('cam ok=',ok,'shape=',None if f is None else f.shape); c.release()"
.venv/bin/python -c "from reachy_mini import ReachyMini; r=ReachyMini(); print(r.get_current_head_pose()); r.disconnect()"
```

### A.6 环境变量
| 变量 | 默认 | 真机建议 |
|---|---|---|
| `COCO_CAMERA` | `usb:0` | 多 cam 时 `usb:1` |
| `COCO_VAD_DISABLE` | 0 | 验 PTT 时设 1 |
| `COCO_WAKE_DISABLE` | 1 | 验唤醒词时设 0 |
| `COCO_LLM_BACKEND` | fallback | 验 LLM 时设 `openai` + key |

---

## B. 部署步骤（路线 C 双模式）

### B.1 开发态自测（兜底）
```bash
pgrep -fl desktop-app-daemon && pkill -f desktop-app-daemon
.venv/bin/python -m coco
```

### B.2 publish 到 Control.app
```bash
.venv/bin/python -m reachy_mini.apps.app check .
.venv/bin/python -m reachy_mini.apps.app publish .
```

### B.3 Control.app 启动 Coco
- App 列表找 Coco → Start
- 看到 IdleAnimator 心跳，无 traceback
- 首次启动权限弹窗全部"允许"

---

## C. UAT 执行单（22 项）

### C.1 audio
1. 说"你好可可" → ASR 转写含"你好"
2. 说"今天天气真好" → 转写含"天气"或"真好"
3. **[核心]** 听到普通话女声 TTS
4. TTS 期间继续说话 → 不自激

### C.2 robot
5. **[核心]** 说"看左边" → 头左转 ~25°
6. 说"看右边" → 头右转 ~25°
7. 触发 nod → 点头 pitch ~15°
8. **[核心]** Ctrl-C → goto_sleep 头部缓慢下垂

### C.3 vision
9. 正对站立 → face_present=True ≥ 2s
10. 缓慢左右移动 → 跟踪稳定不丢
11. 离开 5s 后返回 → presence 过渡平滑
12. **[核心]** `enroll_face.py --label me` → identify=me

### C.4 interact
13. `COCO_WAKE_DISABLE=0` + "可可" → wake=hit
14. **[核心]** wake 后"今天学了什么" → ASR→LLM→TTS 闭环
15. "再讲一个" → 多轮 history 透传
16. "我今天好开心" → emotion=happy idle 幅度放大
17. 待机 ≥ 90s + face_present ≥ 30s → 主动话题
18. 主动话题 15s 内回应 → 进入正常 reply
19. PTT 模式 Enter 后说话 → 闭环

### C.5 视觉-运动闭环（sim 不能证）
20. **[核心]** 人到左 1/3 → 头自动左转，视野跟上
21. 人到右 1/3 → 头自动右转
22. 快速左右走动 5 次 → 跟随但不抖

---

## D. evidence 回填模板

```json
{
  "kind": "real_machine_uat",
  "feature": "uat-phase4",
  "ts": "2026-05-24",
  "main_head_sha": "<7+ hex>",
  "device_info": {
    "host": "MacBook",
    "os": "macOS 15.x",
    "robot": "Reachy Mini Lite fw vX.Y",
    "mic": "...",
    "speaker": "...",
    "camera": "..."
  },
  "deploy_path": "control_app | dev_python_m_coco",
  "results": {
    "audio_001": "PASS - ...",
    "audio_002": "PASS",
    "audio_003": "PASS",
    "robot_001": "PASS",
    "robot_002": "PASS",
    "vision_001": "PASS",
    "vision_002": "PASS",
    "vision_003": "PASS",
    "interact_001": "PASS",
    "interact_002": "PASS",
    "interact_003": "PASS",
    "interact_004": "PASS",
    "interact_005": "PASS / SKIP",
    "interact_006": "PASS",
    "interact_007": "PASS",
    "vision_motor_loop": "PASS - latency~0.7s"
  },
  "sample_logs": [],
  "issues": [],
  "reviewer": {
    "reviewer_kind": "human_uat",
    "operator": "halton"
  }
}
```

**状态切换**：
- 22 项全 PASS → `passing`
- 核心 #3/#5/#8/#12/#14/#20 任一 FAIL → `blocked`
- 非核心 partial → `passing` + `known_debt`

---

## E. 故障兜底

| 现象 | 修复 |
|---|---|
| 端口 7447/8000 占用 | `pkill -f desktop-app-daemon` |
| `python -m coco` 报 gstreamer | 走 Control.app；或确认 `media_backend='no_media'` |
| 麦克 PortAudio 错 | 重插 + 权限 + 重跑 `sd.query_devices()` |
| ASR 转写全空 | `bash scripts/fetch_asr_models.sh` |
| TTS 无声 | Mac 默认输出切对；或听 `tests/fixtures/audio/tts_out/local_kokoro.wav` |
| 摄像头黑屏 | `COCO_CAMERA=usb:1`；或 `OPENCV_VIDEOIO_PRIORITY_AVFOUNDATION=999` |
| Control.app 启动立即 crash | Control.app log 看子进程 traceback；回退 dev 模式 |
| 唤醒词不命中 | smoke `smoke_wake_word`；或 `COCO_WAKE_DISABLE=1` |
| Proactive 不主动 | 静坐画面前 ≥ 90s 不说话 |
| 整体 hang | Ctrl-C → `pkill -f coco.main` → 重启 daemon |
