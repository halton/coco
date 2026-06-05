#!/usr/bin/env python3
"""verify_infra_watchdog_auto_restart V0-V8 — feature: infra-watchdog-auto-restart (phase-68).

Each V independent try/except; rc=0 全 PASS.

- V0  hash lock on coco/watchdog/*.py (4 files)
- V1  import coco.watchdog.{monitor,process_registry,__main__} 不抛 + 关键符号在
- V2  Registry.save() + load() round-trip (临时文件; 含特殊字符)
- V3  HealthCheck.daemon: port_checker False -> (False, tcp_7447_closed);
       port True + http True -> (True, ok); port True + http False -> (False, http_8000_not_200)
- V4  HealthCheck.coco: frame mtime 60s+ -> False; 1s 前 -> True
- V5  HealthCheck.copilot-api: http_getter True -> True; False -> False
- V6  RestartPolicy: 3 次失败后 give_up True; record_success 清零
- V7  monitor.run_loop(max_iters=2, 全 healthy mock) 不抛, 返回 rc=0
- V8  env COCO_WATCHDOG_INTERVAL_S=10 + MAX_RESTARTS=5 经 WatchdogConfig.from_env 生效;
       默认值正确
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# 期望 sha (V0 锁; 文件改动后需重新计算并更新)
EXPECT_HASH = {
    "coco/watchdog/__init__.py": "2223b6e7353e0f9b5b390b1a65bf20c295ae38d090ac6d43cf7c544a4a4de31a",
    "coco/watchdog/__main__.py": "145c367fccfbb32acac5e66cdd53c3107f99e16a485bde8d60f6696962057903",
    "coco/watchdog/process_registry.py": "917ba73be3fbf4810225f9a86d9dca452fab9a8b450784033283e811907cb893",
    "coco/watchdog/monitor.py": "6b436af472f58f27388be0afcee77c650993aa4dd597c4281d23cf716163b2b0",
}

results: list[tuple[str, str, str]] = []


def _record(name: str, ok: bool, msg: str = "") -> None:
    results.append((name, "PASS" if ok else "FAIL", msg))


# ---- V0: hash lock ----
try:
    bad = []
    for rel, expect in EXPECT_HASH.items():
        h = hashlib.sha256((REPO / rel).read_bytes()).hexdigest()
        if h != expect:
            bad.append(f"{rel}: got={h} expect={expect}")
    ok = len(bad) == 0
    msg = "all-4-locked" if ok else " | ".join(bad)
    _record("V0_hash_lock", ok, msg)
except Exception as e:
    _record("V0_hash_lock", False, repr(e))


# ---- V1: import surface ----
try:
    from coco.watchdog import __version__ as wd_version  # noqa: F401
    from coco.watchdog import process_registry as pr
    from coco.watchdog import monitor as mon
    from coco.watchdog import __main__ as wd_main
    # 关键符号
    assert hasattr(pr, "Registry"), "Registry missing"
    assert hasattr(pr, "pid_alive"), "pid_alive missing"
    assert hasattr(mon, "HealthCheck"), "HealthCheck missing"
    assert hasattr(mon, "RestartPolicy"), "RestartPolicy missing"
    assert hasattr(mon, "WatchdogConfig"), "WatchdogConfig missing"
    assert hasattr(mon, "run_loop"), "run_loop missing"
    assert hasattr(mon, "tick_once"), "tick_once missing"
    assert hasattr(mon, "launch_from_registry"), "launch_from_registry missing"
    assert hasattr(wd_main, "main"), "wd_main.main missing"
    assert hasattr(wd_main, "build_parser"), "build_parser missing"
    _record("V1_import_surface", True, f"version={wd_version}")
except Exception as e:
    _record("V1_import_surface", False, repr(e))


# ---- V2: registry round-trip ----
try:
    from coco.watchdog.process_registry import Registry
    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "processes.json"
        r = Registry(rp)
        r.register(
            "daemon",
            pid=12345,
            cmdline=["python", "-m", "reachy_mini.daemon.app.main", "--no-wake-up-on-start"],
            env={"PATH": "/usr/bin:/bin", "FOO": "bar with space", "ZH": "你好"},
            ports=[7447, 8000],
        )
        r.register("coco", pid=23456, cmdline=["python", "-m", "coco"], env={}, ports=[])
        out = r.save()
        assert out == rp, f"save returned {out!r} != {rp!r}"
        # 重新构造、load、比对
        r2 = Registry(rp)
        loaded = r2.load()
        assert "daemon" in loaded and "coco" in loaded
        d = loaded["daemon"]
        assert d["pid"] == 12345
        assert d["cmdline"][0] == "python"
        assert d["env"]["FOO"] == "bar with space"
        assert d["env"]["ZH"] == "你好"
        assert d["ports"] == [7447, 8000]
        assert "started_at" in d
        # 文件不存在 -> 空 dict, 不抛
        r3 = Registry(Path(td) / "nope.json")
        assert r3.load() == {}
    _record("V2_registry_roundtrip", True, "save+load+特殊字符+不存在 都 OK")
except Exception as e:
    _record("V2_registry_roundtrip", False, repr(e))


# ---- V3: HealthCheck.daemon (port + http combinations) ----
try:
    from coco.watchdog.monitor import HealthCheck, WatchdogConfig
    from coco.watchdog.process_registry import Registry
    cfg = WatchdogConfig()
    reg = Registry(Path(tempfile.mkdtemp()) / "p.json")

    # Case 1: port False -> (False, tcp_7447_closed)
    hc1 = HealthCheck(
        config=cfg, registry=reg,
        port_checker=lambda h, p, t: False,
        http_getter=lambda u, t: True,
    )
    ok1, why1 = hc1.check_daemon()
    assert ok1 is False and why1 == "tcp_7447_closed", f"case1 got=({ok1},{why1})"

    # Case 2: port True + http True -> (True, ok)
    hc2 = HealthCheck(
        config=cfg, registry=reg,
        port_checker=lambda h, p, t: True,
        http_getter=lambda u, t: True,
    )
    ok2, why2 = hc2.check_daemon()
    assert ok2 is True and why2 == "ok", f"case2 got=({ok2},{why2})"

    # Case 3: port True + http False
    hc3 = HealthCheck(
        config=cfg, registry=reg,
        port_checker=lambda h, p, t: True,
        http_getter=lambda u, t: False,
    )
    ok3, why3 = hc3.check_daemon()
    assert ok3 is False and why3 == "http_8000_not_200", f"case3 got=({ok3},{why3})"

    _record("V3_daemon_check", True, "tcp_closed / tcp+http ok / http_fail 三例正确")
except Exception as e:
    _record("V3_daemon_check", False, repr(e))


# ---- V4: HealthCheck.coco frame mtime ----
try:
    from coco.watchdog.monitor import HealthCheck, WatchdogConfig
    from coco.watchdog.process_registry import Registry
    with tempfile.TemporaryDirectory() as td:
        fpath = Path(td) / "frame.jpg"
        fpath.write_bytes(b"\xff\xd8\xff\xe0")  # fake jpg header
        cfg = WatchdogConfig(frame_path=str(fpath), frame_max_age_s=10.0)
        reg = Registry(Path(td) / "p.json")
        # 没注册 coco pid -> 只信 frame mtime
        hc = HealthCheck(config=cfg, registry=reg)
        # Case A: now = mtime + 1s -> fresh
        mtime = os.stat(fpath).st_mtime
        hc.now_fn = lambda: mtime + 1.0
        ok_a, why_a = hc.check_coco()
        assert ok_a is True and why_a == "ok", f"A=({ok_a},{why_a})"
        # Case B: now = mtime + 999s -> stale
        hc.now_fn = lambda: mtime + 999.0
        ok_b, why_b = hc.check_coco()
        assert ok_b is False and why_b == "frame_stale_or_missing", f"B=({ok_b},{why_b})"
        # Case C: 文件不存在
        fpath.unlink()
        hc.now_fn = time.time
        ok_c, why_c = hc.check_coco()
        assert ok_c is False, f"C={ok_c}"
    _record("V4_coco_frame_mtime", True, "fresh PASS / stale FAIL / missing FAIL 三例正确")
except Exception as e:
    _record("V4_coco_frame_mtime", False, repr(e))


# ---- V5: HealthCheck.copilot-api ----
try:
    from coco.watchdog.monitor import HealthCheck, WatchdogConfig
    from coco.watchdog.process_registry import Registry
    cfg = WatchdogConfig()
    reg = Registry(Path(tempfile.mkdtemp()) / "p.json")
    # 200 OK
    hc_ok = HealthCheck(config=cfg, registry=reg, http_getter=lambda u, t: True)
    ok1, why1 = hc_ok.check_copilot_api()
    assert ok1 is True and why1 == "ok"
    # 404 / down
    hc_bad = HealthCheck(config=cfg, registry=reg, http_getter=lambda u, t: False)
    ok2, why2 = hc_bad.check_copilot_api()
    assert ok2 is False and why2 == "http_4141_not_200"
    _record("V5_copilot_api_check", True, "200 ok / fail 两例正确")
except Exception as e:
    _record("V5_copilot_api_check", False, repr(e))


# ---- V6: RestartPolicy give_up after max_restarts ----
try:
    from coco.watchdog.monitor import RestartPolicy
    pol = RestartPolicy(max_restarts=3)
    assert pol.give_up("daemon") is False
    pol.record_failure("daemon")  # 1
    assert pol.give_up("daemon") is False
    pol.record_failure("daemon")  # 2
    assert pol.give_up("daemon") is False
    pol.record_failure("daemon")  # 3 -> give_up
    assert pol.give_up("daemon") is True
    assert pol.attempts("daemon") == 3
    # 不同 service 独立
    assert pol.give_up("coco") is False
    pol.record_failure("coco")
    assert pol.give_up("coco") is False
    # record_success 清零
    pol.record_success("daemon")
    assert pol.give_up("daemon") is False
    assert pol.attempts("daemon") == 0
    _record("V6_restart_policy_give_up", True, "3 次 give_up + success 清零 + 多服务独立 OK")
except Exception as e:
    _record("V6_restart_policy_give_up", False, repr(e))


# ---- V7: run_loop max_iters=2 全 healthy mock 不抛 ----
try:
    from coco.watchdog.monitor import (
        HealthCheck, RestartPolicy, WatchdogConfig, run_loop,
    )
    from coco.watchdog.process_registry import Registry
    with tempfile.TemporaryDirectory() as td:
        events_path = str(Path(td) / "events.log")
        fpath = Path(td) / "frame.jpg"
        fpath.write_bytes(b"x")
        cfg = WatchdogConfig(
            interval_s=5.0,
            max_restarts=3,
            frame_max_age_s=999.0,  # frame 永远 fresh
            events_path=events_path,
            frame_path=str(fpath),
        )
        reg = Registry(Path(td) / "p.json")
        pol = RestartPolicy(max_restarts=cfg.max_restarts)
        hc = HealthCheck(
            config=cfg, registry=reg,
            port_checker=lambda h, p, t: True,
            http_getter=lambda u, t: True,
        )
        rc = run_loop(
            config=cfg, registry=reg, policy=pol, health=hc,
            max_iters=2,
            sleep_fn=lambda s: None,  # 不真 sleep
            launcher=lambda *a, **kw: 99999,  # 没用到 (全 healthy)
        )
        assert rc == 0, f"rc={rc}"
        # events.log 至少有 watchdog.started + watchdog.stopped
        log_text = Path(events_path).read_text(encoding="utf-8")
        assert "watchdog.started" in log_text
        assert "watchdog.stopped" in log_text
        # 全 healthy 不应有 health.degraded
        assert "health.degraded" not in log_text, "全 healthy 不应有 degraded"
        # 全 healthy 不应有 restart.attempted
        assert "restart.attempted" not in log_text
    _record("V7_run_loop_2_iters_healthy", True, f"rc={rc}, events 含 started+stopped, 无 degraded/restart")
except Exception as e:
    _record("V7_run_loop_2_iters_healthy", False, repr(e))


# ---- V7b (bonus): degraded path triggers restart ----
try:
    from coco.watchdog.monitor import (
        HealthCheck, RestartPolicy, WatchdogConfig, tick_once,
    )
    from coco.watchdog.process_registry import Registry
    with tempfile.TemporaryDirectory() as td:
        events_path = str(Path(td) / "events.log")
        cfg = WatchdogConfig(events_path=events_path, frame_path=str(Path(td) / "missing.jpg"))
        reg = Registry(Path(td) / "p.json")
        # 注册 daemon 以让重启走到 launcher
        reg.register("daemon", pid=99, cmdline=["echo", "fake-restart"], env={}, ports=[7447])
        pol = RestartPolicy(max_restarts=2)
        # daemon port closed -> degraded
        hc = HealthCheck(
            config=cfg, registry=reg,
            port_checker=lambda h, p, t: False,
            http_getter=lambda u, t: False,
        )
        launched: list[str] = []
        def fake_launcher(name, registry, **kw):
            launched.append(name)
            return 12345
        # iter 1: fail=1 (<max_restarts=2) -> restart attempted
        s1 = tick_once(cfg, reg, pol, hc, launcher=fake_launcher)
        assert s1["daemon"]["restart_attempted"] is True, s1
        assert launched == ["daemon"], launched
        # iter 2: fail=2 (>=max_restarts) -> give_up set in same tick, skip restart
        s2 = tick_once(cfg, reg, pol, hc, launcher=fake_launcher)
        assert s2["daemon"]["give_up"] is True, s2
        assert s2["daemon"]["restart_attempted"] is False, s2
        assert launched == ["daemon"], f"only 1 restart should fire before give_up: {launched}"
        # iter 3: still give_up, no further restart
        s3 = tick_once(cfg, reg, pol, hc, launcher=fake_launcher)
        assert s3["daemon"]["restart_attempted"] is False, s3
        assert launched == ["daemon"], launched
        log_text = Path(events_path).read_text(encoding="utf-8")
        assert "restart.attempted" in log_text
        assert "restart.give_up" in log_text
    _record("V7b_degraded_triggers_restart_and_give_up", True, "iter1 restart -> iter2 give_up -> iter3 skip")
except Exception as e:
    _record("V7b_degraded_triggers_restart_and_give_up", False, repr(e))


# ---- V8: env -> WatchdogConfig.from_env ----
try:
    from coco.watchdog.monitor import (
        WatchdogConfig, DEFAULT_INTERVAL_S, DEFAULT_MAX_RESTARTS,
        DEFAULT_FRAME_MAX_AGE_S,
    )
    # 默认 (空 env)
    cfg_def = WatchdogConfig.from_env(env={})
    assert cfg_def.interval_s == DEFAULT_INTERVAL_S, cfg_def.interval_s
    assert cfg_def.max_restarts == DEFAULT_MAX_RESTARTS, cfg_def.max_restarts
    assert cfg_def.frame_max_age_s == DEFAULT_FRAME_MAX_AGE_S, cfg_def.frame_max_age_s
    # env override
    cfg = WatchdogConfig.from_env(env={
        "COCO_WATCHDOG_INTERVAL_S": "10",
        "COCO_WATCHDOG_MAX_RESTARTS": "5",
        "COCO_WATCHDOG_FRAME_MAX_AGE_S": "20",
    })
    assert cfg.interval_s == 10.0, cfg.interval_s
    assert cfg.max_restarts == 5, cfg.max_restarts
    assert cfg.frame_max_age_s == 20.0, cfg.frame_max_age_s
    # 非法值 -> clamp 或回退默认
    cfg_bad = WatchdogConfig.from_env(env={
        "COCO_WATCHDOG_INTERVAL_S": "abc",     # -> default
        "COCO_WATCHDOG_MAX_RESTARTS": "9999",  # -> clamp 20
        "COCO_WATCHDOG_FRAME_MAX_AGE_S": "0.1",  # -> clamp lo (5.0)
    })
    assert cfg_bad.interval_s == DEFAULT_INTERVAL_S, cfg_bad.interval_s
    assert cfg_bad.max_restarts == 20, cfg_bad.max_restarts
    assert cfg_bad.frame_max_age_s == 5.0, cfg_bad.frame_max_age_s
    _record("V8_env_config_parse", True, "默认/override/clamp 三例正确")
except Exception as e:
    _record("V8_env_config_parse", False, repr(e))


# ---- summary ----
print("=" * 64)
fail = 0
for name, status, msg in results:
    print(f"[{status}] {name}: {msg}")
    if status != "PASS":
        fail += 1
print("=" * 64)
print(f"summary: total={len(results)} pass={len(results)-fail} fail={fail} rc={0 if fail==0 else 1}")
sys.exit(0 if fail == 0 else 1)
