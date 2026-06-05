"""interact-042 antenna + body_yaw — 真机 UAT 脚本

跑法：
  1) 启 daemon + coco + dashboard（按当前栈, 不必重启）
  2) 浏览器打开 http://localhost:8001
  3) 按 "手动动作" 面板下列 6 按钮, 观察天线 + 上半身
  4) 拖 "头部姿态" 面板 antenna_left/right + body_yaw 三根滑条
  5) 启动 coco interact, 喊以下短语（中文 ASR -> KEYWORD_ROUTES / LLM 路由）

按钮测试 (期望物理表现):
  - "摇摆天线"   -> 两根天线快速左右摇 3 次, 回 0
  - "天线竖起"   -> 两根天线同时上举到 ~1.2 rad
  - "天线下垂"   -> 两根天线同时下垂到 ~-1.2 rad
  - "转身向左"   -> 整个上半身向左转 ~0.5 rad (~28°), 机不位移
  - "转身向右"   -> 整个上半身向右转 ~0.5 rad
  - "身体回正"   -> body_yaw 回 0

滑条测试:
  - antenna_left  范围 -1.5..+1.5 rad
  - antenna_right 范围 -1.5..+1.5 rad
  - body_yaw      范围 -1.57..+1.57 rad (π/2)
  滑动后 200ms 节流 POST /api/pose, 观察物理实时响应

语音测试 (说完短语预期触发的 action):
  - "可可, 摇摆天线"        -> wiggle_antennas
  - "可可, 我好开心"        -> wiggle_antennas (KEYWORD: 兴奋)
  - "可可, 你看那是什么"    -> perk_up (LLM 工具调用; 也可能 look_*)
  - "可可, 我有点失落"      -> droop_antennas
  - "可可, 转身向左"        -> turn_body_left
  - "可可, 向右转身"        -> turn_body_right
  - "可可, 身体回正"        -> turn_body_center

安全检查 (必读 safety-notes.md):
  - body_yaw 首次启动必须人在场, 观察天线接线 / USB 摄像头线 / 音频线是否被绕
  - 任何卡顿 / 异常电流声立即 Ctrl-C interact 并 disable_motors
  - 一根天线下垂角度过大 (>1.5 rad) 物理上可能撞到 base, 已 clamp 阻止
"""
