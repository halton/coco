# dashboard-004 pose sliders — 真机 UAT 脚本

## 前置

- 当前栈正常运行：daemon (mockup-sim 或真机) / coco / dashboard
- 浏览器打开 http://127.0.0.1:8001/ （或 dashboard 端口）

## 步骤

1. **硬刷新** dashboard (Cmd+Shift+R / Ctrl+Shift+R) 确保拿到最新 HTML
2. 右上 action panel 下方应出现 **pose panel**，含：
   - 标题 "头部姿态 (rad)"
   - Pitch / Yaw / Roll 三个滑条（range -0.5 ~ +0.5，step 0.01）
   - 回中按钮
   - pose-status 提示位
3. 滑动 **Pitch** 至 +0.3
   - 期望：值显示 `0.30`
   - 期望：拖动停止 200ms 后 pose-status 显示 `pose -> ok`
   - 期望：reachy 头部向上点起 (真机 / mockup-sim 视觉)
4. 滑动 **Yaw** 至 -0.3
   - 期望：头部左转
5. 滑动 **Roll** 至 +0.3
   - 期望：头部右倾
6. 点击 **回中**
   - 期望：三轴回 0.00 + reachy 头部回中
7. 快速来回拖动滑条 5 次
   - 期望：throttle 生效，只发 1-2 次 POST（不轰炸 daemon）
   - 期望：dashboard 网络面板 POST /api/pose 数 << 拖动次数

## 安全 clamp

8. 浏览器 DevTools Console:
   ```js
   fetch('/api/pose', {method:'POST', headers:{'Content-Type':'application/json'},
                       body: JSON.stringify({pitch: 10, yaw: -99, roll: 99})})
     .then(r => r.json()).then(console.log)
   ```
   - 期望：response 含 `pitch: 0.6, yaw: -0.6, roll: 0.6`（clamp 到 ±0.6）
   - 期望：头部未做剧烈/越界动作（只去到 ±0.6 rad）

## 回归

9. dashboard-001 / 002 仍正常：相机流、事件 timeline、10 个手动按钮、watchdog redbar 不受影响

## 记录

- [ ] 滑条三轴各动一次：拍照 / 录屏
- [ ] 回中按钮：reachy 复位
- [ ] clamp 测试：response body 截屏
- [ ] dashboard 整体回归通过

real_machine_uat: pending
