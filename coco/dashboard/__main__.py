"""coco.dashboard.__main__ — uvicorn launcher.

启动: python -m coco.dashboard
env:
  COCO_DASHBOARD_PORT (默认 8765)
  COCO_DASHBOARD_HOST (默认 127.0.0.1)
"""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("COCO_DASHBOARD_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("COCO_DASHBOARD_PORT", "8765"))
    except ValueError:
        port = 8765
    uvicorn.run("coco.dashboard.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
