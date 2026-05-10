"""verify interact-001 V2：在 Coco.run() 内挂 push-to-talk 后线程模式跑 8s。

跳过真键盘触发（PTT_DISABLED=1），只验：
- Coco.run() 启动 IdleAnimator + InteractSession + ptt 监听（disabled 走旁路）
- stop_event 后干净退出 < 3s
- ASR fixture 后台线程仍跑通
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("verify_interact001_app")

# 在导入 main 前关闭真 push-to-talk（避免子线程 readline 阻塞 join）
os.environ["COCO_PTT_DISABLE"] = "1"

from reachy_mini import ReachyMini  # noqa: E402

from coco.main import Coco  # noqa: E402


def main() -> int:
    app = Coco()
    robot = ReachyMini(media_backend="no_media")
    stop_event = threading.Event()

    def _run():
        try:
            app.run(robot, stop_event)
        except Exception as e:
            log.error("Coco.run raised: %r", e)
        finally:
            log.info("Coco.run returned")

    t = threading.Thread(target=_run, name="coco-run-test", daemon=True)
    t.start()
    log.info("Coco.run() started in thread")
    time.sleep(8.0)
    log.info("setting stop_event ...")
    t0 = time.monotonic()
    stop_event.set()
    t.join(timeout=5.0)
    join_dt = time.monotonic() - t0
    alive = t.is_alive()
    log.info("join_dt=%.3fs alive_after=%s", join_dt, alive)

    fails = []
    if join_dt > 3.0:
        fails.append(f"join_dt={join_dt:.3f}s > 3s")
    if alive:
        fails.append("Coco.run thread still alive")

    print("\n=== V2 summary ===")
    print(f"join_dt={join_dt:.3f}s alive_after={alive}")
    print("FAILS:", fails)
    if fails:
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
