"""coco.dashboard — Live HUD (dashboard-001).

独立进程，通过：
- /tmp/coco-frame.jpg  (frame tap, 需 COCO_DASHBOARD_FRAME_TAP=1)
- /tmp/coco-stdout.log + ~/.cache/coco/metrics.jsonl (event tap)

启动:  python -m coco.dashboard
"""
