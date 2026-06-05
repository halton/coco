# dashboard-007-extend UAT 脚本

适用 feature: `dashboard-007-extend-all-perception-toggles`
对象: 浏览器手动 toggle 关闭对应 perception 模块后，30s 内 hot-reload 生效。

## 前置

- daemon / coco / dashboard / copilot-api / watchdog 五件套已起 (即 sim-first 默认栈)
- 浏览器打开 dashboard (`http://localhost:8000/` 或对应端口)
- COCO_CAMERA 已设 (任一档：image/video/usb)；否则 FaceTracker / GestureRecognizer / SceneCaptionEmitter 可能根本未起，toggle 无 observable 行为可证

## 场景 A: gesture toggle 关闭后 WAVE 不再自言自语"你好"

1. 触发一次 wave 手势 (对摄像头挥手；image/video 档可手动 cat 一个 wave fixture 到 COCO_CAMERA 指向的路径)，确认 timeline 出现 `vision.gesture_detected kind=wave`，扬声器/log 出现 `[coco][gesture] WAVE → glance + 你好`
2. 打开 dashboard 右栏「感知设置」面板
3. 找到 "手势识别 (gesture)" checkbox，取消勾选
4. status 应显示 `saved gesture_enabled=false (~30s 生效)`
5. 等 ≥ 30s (hot-reload 节流默认 30s)
6. 再触发一次 wave (或保持 fixture 持续供帧)
7. 期望: timeline 不再有 `vision.gesture_detected`，log 不再有 `[coco][gesture] WAVE → glance + 你好`；可能看到 `[coco][gesture] behavior skipped: recognizer.enabled=False` (handler 双保险路径)

## 场景 B: face_detection toggle 关闭后 timeline 不再有 face primary 切换

1. 站到摄像头前，确认 timeline 出现 `vision.face_present=true` / `vision.face_primary_switch`
2. 打开 dashboard 右栏「感知设置」
3. 找到 "人脸检测 (face_detection)" checkbox，取消勾选
4. status 应显示 `saved face_detection_enabled=false (~30s 生效)`
5. 等 ≥ 30s
6. 移动 / 站起坐下让脸位置变化
7. 期望: timeline 不再有 `vision.face_primary_switch`；snapshot.primary 保持为旧值 (since _tick early return，frame 不读 / detect 不跑)

## 场景 C: scene_caption toggle 关闭后场景描述事件停止

1. 等待 ≥ 60s (SceneCaptionEmitter 默认 interval=60s)，确认 timeline 出现 `vision.scene_caption`
2. 打开 dashboard，取消勾选 "场景描述 (scene_caption)"
3. status: `saved scene_caption_enabled=false (~30s 生效)`
4. 等 ≥ 30s + ≥ 60s (一次 emit 周期)
5. 期望: 当前 emit 周期跳过 (stats.ticks 不再增长 / 无新 `vision.scene_caption` 事件)

## 场景 D: 重新勾选恢复正常

对场景 A/B/C 任一，勾回 checkbox → 等 30s → 期望模块恢复 detect/emit。

## 场景 E: regression — dashboard-007 原 face_id + wake toggle 不破

1. 取消勾选 "人脸识别 (face_id)" → 等 30s → 摄像头前已注册的脸应不再被 identify
2. 取消勾选 "语音唤醒 (wake-word)" → 等 30s → 喊 "可可"，机器人不再被唤醒

## 回填位置

evidence/dashboard-007-extend-all-perception-toggles/uat-script.md (本文件)；执行结果写到同目录 `uat-result-*.md` 或 dashboard `real_machine_uat:` 字段。
