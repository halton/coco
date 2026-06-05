"""coco.watchdog: 独立 watchdog 进程，监控并自动重启 daemon / coco / copilot-api。

feature: infra-watchdog-auto-restart (phase-68)

设计要点
========

- 独立进程，不耦合到 coco 主进程（主 down 也能存活）
- 每 ``COCO_WATCHDOG_INTERVAL_S`` (默认 30s) 一轮健康检查
- 单服务连续失败 ``COCO_WATCHDOG_MAX_RESTARTS`` (默认 3) 次重启后放弃 (give-up)
- dashboard 不在监控范围（dashboard 是观察者，不参与自愈）
- 默认不启（用户手动 ``python -m coco.watchdog``）

入口::

    python -m coco.watchdog [--once] [--interval N] [--max-restarts N]
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
