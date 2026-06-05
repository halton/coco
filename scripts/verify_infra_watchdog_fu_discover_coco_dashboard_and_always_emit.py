#!/usr/bin/env python3
"""verify infra-watchdog-fu discover coco/dashboard + always emit health event

V0  hash lock on coco/watchdog/process_registry.py + coco/watchdog/monitor.py
V1  import 不抛 (process_registry + monitor)
V2  discover_by_cmdline('coco', proc_lister=...) 识别 'python -m coco' 进程 → 注册
V3  discover_by_cmdline('dashboard', proc_lister=...) 识别 'python -m coco.dashboard'
V4  discover 顺序: discover_all 优先识别 coco.dashboard, 不会把它误判为 'coco'
V5  HealthCheck.check_coco frame mtime + PID alive 真生效 (mtime fresh + pid 活 → ok)
V6  HealthCheck.check_dashboard HTTP 200 + PID alive 真生效
V7  env COCO_WATCHDOG_ALWAYS_EMIT=1 (默认) → 每次 tick 都 emit health.healthy
V8  env COCO_WATCHDOG_ALWAYS_EMIT=0 → 只在状态变化时 emit (旧行为, 第二轮不再 emit)
V9  mock tick_once 跑 3 次 healthy → 第 1+2+3 次都 emit (always emit mode)

Exit: rc=0 全 PASS; rc=1 任一 FAIL.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO = pathlib.Path(__file__).resolve().parent.parent
PR_PY = REPO / "coco" / "watchdog" / "process_registry.py"
MON_PY = REPO / "coco" / "watchdog" / "monitor.py"

EXPECTED_PR_SHA1 = "881ffa27c8625ce3d432e7f076c7465aa1cf6402"
EXPECTED_MON_SHA1 = "28b32358a14d495c8a3cf27ed6dc1ab2e9dab6b8"

sys.path.insert(0, str(REPO))

results: List[Tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}: {detail}")


# ---------------------------------------------------------------------------
# V0  hash lock
# ---------------------------------------------------------------------------
try:
    h1 = hashlib.sha1(PR_PY.read_bytes()).hexdigest()
    h2 = hashlib.sha1(MON_PY.read_bytes()).hexdigest()
    ok = h1 == EXPECTED_PR_SHA1 and h2 == EXPECTED_MON_SHA1
    record(
        "V0 hash lock",
        ok,
        f"pr_expected={EXPECTED_PR_SHA1} pr_actual={h1} "
        f"mon_expected={EXPECTED_MON_SHA1} mon_actual={h2}",
    )
except Exception as e:  # noqa: BLE001
    record("V0 hash lock", False, f"exc={e!r}")


# ---------------------------------------------------------------------------
# V1  import 不抛
# ---------------------------------------------------------------------------
try:
    from coco.watchdog import process_registry as pr
    from coco.watchdog import monitor as m

    record(
        "V1 import",
        True,
        f"SERVICE_NAMES={m.SERVICE_NAMES} rules={list(pr.CMDLINE_RULES)}",
    )
except Exception as e:  # noqa: BLE001
    record("V1 import", False, f"exc={e!r}")
    print("FATAL: import failed, abort.")
    sys.exit(1)


def fake_lister(rows: List[Tuple[int, List[str]]]) -> Callable[[], List[Tuple[int, List[str]]]]:
    return lambda: list(rows)


def _fresh_registry() -> "pr.Registry":
    tmp = tempfile.NamedTemporaryFile(
        prefix="reg-", suffix=".json", delete=False, dir=tempfile.gettempdir()
    )
    tmp.close()
    p = pathlib.Path(tmp.name)
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    return pr.Registry(path=p)


# ---------------------------------------------------------------------------
# V2  discover_by_cmdline('coco', ...) 识别 'python -m coco'
# ---------------------------------------------------------------------------
try:
    reg = _fresh_registry()
    lister = fake_lister(
        [
            (11111, ["/usr/bin/python", "-m", "coco"]),
            (22222, ["/usr/bin/python", "-m", "some.other"]),
        ]
    )
    entry = reg.discover_by_cmdline("coco", proc_lister=lister)
    ok = entry is not None and entry.get("pid") == 11111 and "-m" in entry["cmdline"]
    record("V2 discover coco", ok, f"entry_pid={entry and entry.get('pid')}")
except Exception as e:  # noqa: BLE001
    record("V2 discover coco", False, f"exc={e!r}")


# ---------------------------------------------------------------------------
# V3  discover_by_cmdline('dashboard', ...) 识别 'python -m coco.dashboard'
# ---------------------------------------------------------------------------
try:
    reg = _fresh_registry()
    lister = fake_lister(
        [
            (33333, ["/usr/bin/python", "-m", "coco.dashboard"]),
            (44444, ["/usr/bin/python", "-m", "coco"]),
        ]
    )
    entry = reg.discover_by_cmdline(
        "dashboard", ports=[8765], proc_lister=lister
    )
    ok = (
        entry is not None
        and entry.get("pid") == 33333
        and entry.get("ports") == [8765]
    )
    record(
        "V3 discover dashboard",
        ok,
        f"entry_pid={entry and entry.get('pid')} ports={entry and entry.get('ports')}",
    )
except Exception as e:  # noqa: BLE001
    record("V3 discover dashboard", False, f"exc={e!r}")


# ---------------------------------------------------------------------------
# V4  discover_all 顺序: dashboard > watchdog > coco (避免误判 coco.dashboard 为 coco)
# ---------------------------------------------------------------------------
try:
    reg = _fresh_registry()
    rows = [
        (33333, ["python", "-m", "coco.dashboard"]),
        (69519, ["python", "-m", "coco.watchdog", "--discover"]),
        (11111, ["python", "-m", "coco"]),
    ]
    out = reg.discover_all(proc_lister=fake_lister(rows))
    ok_dash = out.get("dashboard") and out["dashboard"]["pid"] == 33333
    ok_wd = out.get("watchdog") and out["watchdog"]["pid"] == 69519
    ok_coco = out.get("coco") and out["coco"]["pid"] == 11111
    ok = bool(ok_dash and ok_wd and ok_coco)
    record(
        "V4 discover_all order",
        ok,
        f"dash={out.get('dashboard') and out['dashboard']['pid']} "
        f"wd={out.get('watchdog') and out['watchdog']['pid']} "
        f"coco={out.get('coco') and out['coco']['pid']}",
    )
except Exception as e:  # noqa: BLE001
    record("V4 discover_all order", False, f"exc={e!r}")


# ---------------------------------------------------------------------------
# V5  HealthCheck.check_coco frame mtime fresh + pid alive -> ok
# ---------------------------------------------------------------------------
try:
    tmp_frame = pathlib.Path(tempfile.gettempdir()) / "v_wd_fu2_frame.jpg"
    tmp_frame.write_bytes(b"x")
    os.utime(tmp_frame, None)

    reg = _fresh_registry()
    # 用当前 python 自己的 pid 模拟 alive registered pid
    reg.register(
        "coco",
        pid=os.getpid(),
        cmdline=["python", "-m", "coco"],
        env={},
        ports=[],
    )
    cfg = m.WatchdogConfig(
        events_path=str(pathlib.Path(tempfile.gettempdir()) / "v_wd_fu2_events.log"),
        frame_path=str(tmp_frame),
        frame_max_age_s=60.0,
    )
    hc = m.HealthCheck(config=cfg, registry=reg)
    ok_state, reason = hc.check_coco()

    # 再验 PID dead 时变 unhealthy
    reg.register(
        "coco",
        pid=99999999,  # 几乎肯定不存在
        cmdline=["python", "-m", "coco"],
        env={},
        ports=[],
    )
    bad_state, bad_reason = hc.check_coco()
    ok = ok_state and (not bad_state) and bad_reason == "registered_pid_dead"
    record(
        "V5 check_coco mtime+pid",
        ok,
        f"alive=({ok_state},{reason}) dead=({bad_state},{bad_reason})",
    )
    try:
        tmp_frame.unlink()
    except FileNotFoundError:
        pass
except Exception as e:  # noqa: BLE001
    record("V5 check_coco mtime+pid", False, f"exc={e!r}")


# ---------------------------------------------------------------------------
# V6  HealthCheck.check_dashboard HTTP 200 + PID alive
# ---------------------------------------------------------------------------
try:
    reg = _fresh_registry()
    reg.register(
        "dashboard",
        pid=os.getpid(),
        cmdline=["python", "-m", "coco.dashboard"],
        env={},
        ports=[8765],
    )
    cfg = m.WatchdogConfig()

    def http_ok(url: str, t: float) -> bool:
        return url == m.DASHBOARD_URL

    def http_fail(url: str, t: float) -> bool:
        return False

    hc_ok = m.HealthCheck(config=cfg, registry=reg, http_getter=http_ok)
    s_ok, r_ok = hc_ok.check_dashboard()

    hc_bad = m.HealthCheck(config=cfg, registry=reg, http_getter=http_fail)
    s_bad, r_bad = hc_bad.check_dashboard()

    # PID dead 的情况
    reg.register(
        "dashboard",
        pid=99999999,
        cmdline=["python", "-m", "coco.dashboard"],
        env={},
        ports=[8765],
    )
    s_dead, r_dead = hc_ok.check_dashboard()

    ok = (
        s_ok
        and (not s_bad)
        and r_bad == "http_8765_not_200"
        and (not s_dead)
        and r_dead == "registered_pid_dead"
    )
    record(
        "V6 check_dashboard HTTP+PID",
        ok,
        f"ok=({s_ok},{r_ok}) http_fail=({s_bad},{r_bad}) pid_dead=({s_dead},{r_dead})",
    )
except Exception as e:  # noqa: BLE001
    record("V6 check_dashboard HTTP+PID", False, f"exc={e!r}")


# ---------------------------------------------------------------------------
# V7 / V9  COCO_WATCHDOG_ALWAYS_EMIT=1 → 每次 tick 都 emit
# ---------------------------------------------------------------------------
try:
    cfg = m.WatchdogConfig(
        events_path=str(pathlib.Path(tempfile.gettempdir()) / "v_wd_fu2_v7.log")
    )
    reg = _fresh_registry()
    pol = m.RestartPolicy(max_restarts=3)

    class FakeHC:
        def check(self, name: str) -> Tuple[bool, str]:
            return True, "ok"

    events: List[Dict[str, Any]] = []

    def sink(path: str, kind: str, **payload: Any) -> None:
        events.append({"kind": kind, **payload})

    saved = os.environ.get("COCO_WATCHDOG_ALWAYS_EMIT")
    os.environ["COCO_WATCHDOG_ALWAYS_EMIT"] = "1"
    try:
        for _ in range(3):
            m.tick_once(
                cfg,
                reg,
                pol,
                FakeHC(),  # type: ignore[arg-type]
                services=("daemon",),
                event_sink=sink,
            )
    finally:
        if saved is None:
            os.environ.pop("COCO_WATCHDOG_ALWAYS_EMIT", None)
        else:
            os.environ["COCO_WATCHDOG_ALWAYS_EMIT"] = saved

    healthy_evs = [e for e in events if e["kind"] == "health.healthy"]
    ok = len(healthy_evs) == 3 and all(e["service"] == "daemon" for e in healthy_evs)
    record(
        "V7 always_emit=1 every tick",
        ok,
        f"events={len(events)} healthy={len(healthy_evs)}",
    )
    record(
        "V9 3 ticks healthy → 3 emits",
        len(healthy_evs) == 3,
        f"healthy_count={len(healthy_evs)}",
    )
except Exception as e:  # noqa: BLE001
    record("V7 always_emit=1 every tick", False, f"exc={e!r}")
    record("V9 3 ticks healthy → 3 emits", False, f"exc={e!r}")


# ---------------------------------------------------------------------------
# V8  COCO_WATCHDOG_ALWAYS_EMIT=0 → 仅状态变化时 emit
# ---------------------------------------------------------------------------
try:
    cfg = m.WatchdogConfig(
        events_path=str(pathlib.Path(tempfile.gettempdir()) / "v_wd_fu2_v8.log")
    )
    reg = _fresh_registry()
    pol = m.RestartPolicy(max_restarts=3)

    class FakeHC2:
        def check(self, name: str) -> Tuple[bool, str]:
            return True, "ok"

    events2: List[Dict[str, Any]] = []

    def sink2(path: str, kind: str, **payload: Any) -> None:
        events2.append({"kind": kind, **payload})

    saved = os.environ.get("COCO_WATCHDOG_ALWAYS_EMIT")
    os.environ["COCO_WATCHDOG_ALWAYS_EMIT"] = "0"
    try:
        for _ in range(3):
            m.tick_once(
                cfg,
                reg,
                pol,
                FakeHC2(),  # type: ignore[arg-type]
                services=("daemon",),
                event_sink=sink2,
            )
    finally:
        if saved is None:
            os.environ.pop("COCO_WATCHDOG_ALWAYS_EMIT", None)
        else:
            os.environ["COCO_WATCHDOG_ALWAYS_EMIT"] = saved

    healthy_evs2 = [e for e in events2 if e["kind"] == "health.healthy"]
    # 仅第一次 (None -> True) 算变化, 后两次 True -> True 不变 = 不 emit
    ok = len(healthy_evs2) == 1
    record(
        "V8 always_emit=0 only on change",
        ok,
        f"events={len(events2)} healthy={len(healthy_evs2)} (expect 1)",
    )
except Exception as e:  # noqa: BLE001
    record("V8 always_emit=0 only on change", False, f"exc={e!r}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print()
fails = [r for r in results if not r[1]]
print(
    f"summary: {len(results) - len(fails)}/{len(results)} PASS, "
    f"{len(fails)} FAIL"
)
for n, ok, d in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {n}  {d}")

sys.exit(0 if not fails else 1)
