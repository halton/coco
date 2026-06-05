"""``python -m coco.watchdog`` 入口.

CLI::

    python -m coco.watchdog                     # 持续 loop, 默认 30s 一轮
    python -m coco.watchdog --once              # 跑一轮即退出 (debug)
    python -m coco.watchdog --interval 10       # 自定义间隔 (>=5)
    python -m coco.watchdog --max-restarts 5    # 单服务放弃前的重启上限
    python -m coco.watchdog --discover          # 启动时尝试 discover daemon/coco/copilot-api
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from coco.watchdog.monitor import (
    HealthCheck,
    RestartPolicy,
    WatchdogConfig,
    run_loop,
)
from coco.watchdog.process_registry import Registry


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m coco.watchdog",
        description="Coco watchdog: daemon / coco / copilot-api 自动健康检查与重启",
    )
    p.add_argument("--once", action="store_true", help="跑一轮即退出 (debug 用)")
    p.add_argument(
        "--interval",
        type=float,
        default=None,
        help="健康检查间隔秒 (默认 30, env: COCO_WATCHDOG_INTERVAL_S)",
    )
    p.add_argument(
        "--max-restarts",
        type=int,
        default=None,
        help="单服务连续失败放弃前的重启次数 (默认 3, env: COCO_WATCHDOG_MAX_RESTARTS)",
    )
    p.add_argument(
        "--discover",
        action="store_true",
        help="启动时尝试通过端口 discover 现存 daemon (7447) / copilot-api (4141)",
    )
    p.add_argument(
        "--max-iters",
        type=int,
        default=None,
        help="最多跑 N 轮后退出 (默认无限; debug 用)",
    )
    p.add_argument("--quiet", "-q", action="store_true", help="只 print 错误")
    return p


def _maybe_discover(reg: Registry) -> None:
    """启动时一次性 discover 已知 service.

    历史: 早期仅按 port 找 daemon (7447) / copilot-api (4141), coco / dashboard
    / watchdog 都因没端口或非默认端口无法识别 (留 follow-up).

    现切到 ``Registry.discover_all()``: 内部按端口 (daemon / copilot-api) +
    cmdline (dashboard > watchdog > coco, 防 ``python -m coco`` 误吞子模块)
    多规则识别全部 5 service, 并把 entry 写回 ``reg._data``; 此处再 ``save``
    持久化. 不命中的 service 返回 None, 不阻塞其他识别 (兼容现存 verify).
    """
    reg.discover_all()
    try:
        reg.save()
    except OSError:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    level = logging.WARNING if args.quiet else logging.INFO
    logging.basicConfig(
        level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger("coco.watchdog")

    cfg = WatchdogConfig.from_env()
    if args.interval is not None:
        cfg.interval_s = max(5.0, min(600.0, float(args.interval)))
    if args.max_restarts is not None:
        cfg.max_restarts = max(1, min(20, int(args.max_restarts)))

    reg = Registry()
    reg.load()
    if args.discover:
        _maybe_discover(reg)

    pol = RestartPolicy(max_restarts=cfg.max_restarts)
    hc = HealthCheck(config=cfg, registry=reg)

    log.info(
        "watchdog start: interval=%.1fs max_restarts=%d frame_max_age=%.1fs events=%s",
        cfg.interval_s, cfg.max_restarts, cfg.frame_max_age_s, cfg.events_path,
    )

    rc = run_loop(
        config=cfg,
        registry=reg,
        policy=pol,
        health=hc,
        once=args.once,
        max_iters=args.max_iters,
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
