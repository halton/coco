# 真机验收剧本（UAT）

**触发**：`feature_list.json` 中 phase-1-mvp 全部 features `passing` 后，作为 milestone gate。
**前提**：mockup-sim 下整套功能已经跑通。
**目的**：把"在模拟器中正确"升级为"在真机中正确"。

---

## 准备

### 硬件
- [ ] Reachy Mini Lite 主体连电
- [ ] USB 连开发机
- [ ] Reachy Mini Audio USB device 已识别（`system_profiler SPUSBDataType | grep -i reachy`）
- [ ] 颈部、头部能自由活动（无线缆缠绕）

### 软件
- [ ] 关闭 Reachy Mini Control.app（避免 daemon 端口冲突）
- [ ] `./init.sh` 通过
- [ ] 真机模式下能用 `ReachyMini()`（不带 `--mockup-sim`）连上

---

## 子系统验收

### A. Audio（独立验）

- [ ] **A1. 麦克采集**：在静音环境录 3 秒，rms < 0.005；说话 3 秒，rms > 0.05
- [ ] **A2. 麦克方向性**：从机器人正前方说话和侧面说话，能听到响度差异
  - （只是观察性验证，不卡测试）
- [ ] **A3. 与 mac 麦克对比**：同样话同样距离，记录 rms 差异；判断真机麦克是否够用
  - 如果真机麦克明显差，audio 子系统可能要回退到 mac 麦克作输入源

### B. Robot（独立验）

- [ ] **B1. 头部基础姿态**：调用 `look_left` / `look_right` / `nod`
  - 物理动作幅度与 mockup-sim 中目标值一致（误差肉眼可接受）
  - 没有异响、没有卡顿、没有过冲
- [ ] **B2. 回中**：连续做 5 次随机姿态后回 home，最终位置与 home 偏差 < 5°
- [ ] **B3. 持续运行**：陪伴动作循环（companion-001）跑 5 分钟
  - 电机不过热（手摸不烫）
  - 动作不漂移
  - 真实场景下幅度感受是否过大或过小？记录主观感受

### C. 应用层端到端

- [ ] **C1. 完整闭环**（interact-001）：在真机上做一次 push-to-talk
  - 说话 → 看到转写 → 看到机器人头部回应动作
  - 全程延迟可接受（< 3 秒）
- [ ] **C2. 长会话稳定性**：连续做 5 次完整闭环，无崩溃、无内存膨胀

---

## 不通过的处理

- 子系统级问题：把对应 feature 改回 `blocked`，记录现象到 `claude-progress.md`，evidence 字段附上 UAT 失败位置
- 应用层问题但子系统验通：拆出新 feature，记录在 `feature_list.json`
- 主观感受类（动作幅度、声音大小）：进 `BACKLOG.md` 的"待定事项"，不卡 milestone

## 通过后

- [ ] 在 `claude-progress.md` 决策导航追加一行 "phase-1-mvp 真机验收通过 YYYY-MM-DD"
- [ ] 拍一段真机演示视频，路径记进 `claude-progress.md`
- [ ] 进入下一个 milestone（场景 2 候选评估，或 phase-1 加固）
