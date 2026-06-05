# dashboard-007 face_id + wake-word toggle UAT 脚本

## 目标

验证浏览器点 dashboard 右栏 toggle 后, 30s 内 coco 主进程感知状态切换并在日志可见。

## 前置

- 当前栈活: daemon 45165 / coco 83117 / dashboard 83387 / copilot-api 38079 / watchdog 83191
- 本 feature 已 commit 在 `feat/dashboard-007-face-id-wake-toggle`, 未 merge
- 真机 UAT 由用户在合并到 main 并重启 coco/dashboard 后再执行（合并前栈跑的是 old code，没有 hot-reload 钩子）

## 步骤

### A. face_id toggle

1. 浏览器开 `http://127.0.0.1:8788/`（或 dashboard 实际端口）
2. 右栏展开「感知设置」`<details id="perception-panel">`
3. 默认两个 checkbox 都 checked
4. 取消勾选「人脸识别 (face_id)」
5. 期望: `#perception-status` 文字变为 "saved face_id_enabled=false (~30s 生效)"
6. 等 30s 内, `tail -F ~/.cache/coco/coco.log | grep face_id` 应见 `face_id.hot_reload enabled True -> False`
7. 此时主进程 `face_id.identify(crop)` 全部返回 `(None, 0.0)`, face-tracker 输出 name 永远 None
8. 重新勾选「人脸识别」, 30s 内日志见 `face_id.hot_reload enabled False -> True`, 识别恢复

### B. wake-word toggle

1. 取消勾选「语音唤醒 (wake-word)」
2. 期望: `#perception-status` 文字变为 "saved wake_enabled=false (~30s 生效)"
3. 等 30s 内, `tail -F ~/.cache/coco/coco.log | grep wake.hot_reload` 应见 `wake.hot_reload enabled True -> False`
4. 此时对着麦克风喊「可可」, KWS 仍 decode 但 `is_muted()=True`, 命中视为 muted-drop, 不触发 wake 窗口
5. 重新勾选「语音唤醒」, 30s 内日志见 `wake.hot_reload enabled False -> True`, 喊「可可」唤醒恢复

### C. 状态持久 + 重启幂等

1. 关掉 face_id, 关掉 wake, 看 `~/.cache/coco/runtime_config.json`:
   ```json
   {
     "llm_model": "...",
     "face_id_enabled": false,
     "wake_enabled": false
   }
   ```
2. 重启 coco（kill + 重新 `python -m coco`）
3. 30s 内主进程读到 runtime_config → face_id / wake 自动切到 disabled 状态
4. 浏览器刷新 dashboard, `loadPerception()` 异步 GET 回填两个 checkbox 仍为 unchecked

### D. regression: LLM model 不受影响

1. 切个 LLM 模型（如 gpt-4o → claude-sonnet-4.5）
2. `#llm-status` 仍正常显示 "saved (~30s 生效)"
3. runtime_config.json 三个字段 (`llm_model` + `face_id_enabled` + `wake_enabled`) 共存

## 完成判据

- A.6, B.3 日志各见 ≥1 条 hot_reload 行
- A.7 face_id.identify enabled=False 时返 None
- B.4 wake 喊「可可」disabled 时不触发（不进 awake 窗口）
- D.3 三字段共存
- 5 分钟内全部步骤可复现（无需重启栈, 仅依赖 30s 节流轮询）
