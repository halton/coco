"""dashboard-001 真机 UAT 脚本.

Sim 不可证明: 真浏览器观感 + 实摄像头 + 实事件流 端到端体验.

步骤:
  1. 终端 A — 启动 coco 主进程 (开启 frame tap):
       COCO_DASHBOARD_FRAME_TAP=1 /Users/halton/work/coco/.venv/bin/python -m coco
     (frame tap default-OFF, 不开则 dashboard 看不到画面)

  2. 终端 B — 启动 dashboard:
       /Users/halton/work/coco/.venv/bin/python -m coco.dashboard
     (默认 127.0.0.1:8765, env COCO_DASHBOARD_PORT 可调)

  3. 浏览器打开 http://localhost:8765/
     - 左半: 摄像头 MJPEG 实时画面
     - 右半: 事件 timeline (transcript / reply / wake / vision / tts)

  4. 对着 reachy 说 "你好可可", 期望 1-2s 内:
     - 右半看到 wake 事件
     - 然后 transcript / reply 事件
     - 然后 tts first_chunk_ms 事件

  5. 关 dashboard (ctrl-C) coco 主进程不受影响, 可反复开关.

evidence 回填字段:
  real_machine_uat: passing | blocked + 浏览器截图路径 (放 evidence/dashboard-001/)
"""
