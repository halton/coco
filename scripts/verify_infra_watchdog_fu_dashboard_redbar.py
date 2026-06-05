#!/usr/bin/env python3
"""verify infra-watchdog-fu dashboard red bar (clean rewrite)

V0  hash lock on coco/dashboard/app.py
V1  import + /api/watchdog/recent route exists
V2  mock log with kind=health.degraded → endpoint returns the event
V3  log missing → returns {"events":[], "count":0} without raise
V4  limit clamp [1, 100]
V5  log with corrupt JSON line → skip without raise
V6  HTML contains watchdog-bar div + pollWatchdog + setInterval 10000
V7  textContent escape (msg via textContent, not innerHTML)
V8  d001/d002/d003/d004/d005 existing routes intact
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
APP_PY = REPO / "coco" / "dashboard" / "app.py"

EXPECTED_SHA1 = "613b0fed8d15938ceb8c9de85002ba3e3e0f5433"

results = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}: {detail}")


# V0 hash lock
try:
    actual = hashlib.sha1(APP_PY.read_bytes()).hexdigest()
    record("V0 hash lock", actual == EXPECTED_SHA1, f"expected={EXPECTED_SHA1} actual={actual}")
except Exception as e:
    record("V0 hash lock", False, f"exc={e!r}")

# V1 import + route exists
try:
    sys.path.insert(0, str(REPO))
    from coco.dashboard.app import app

    paths = [r.path for r in app.routes if hasattr(r, "path")]
    record("V1 import + route", "/api/watchdog/recent" in paths, f"routes_count={len(paths)}")
except Exception as e:
    record("V1 import + route", False, f"exc={e!r}")

# V2 mock log with kind=health.degraded
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import _WATCHDOG_LOG_PATH

    backup = None
    if _WATCHDOG_LOG_PATH.exists():
        backup = _WATCHDOG_LOG_PATH.read_bytes()
    try:
        ev = {"kind": "health.degraded", "service": "daemon", "ts": "2026-06-05T00:00:00Z"}
        _WATCHDOG_LOG_PATH.write_text(json.dumps(ev) + "\n")
        client = TestClient(app)
        r = client.get("/api/watchdog/recent?limit=10")
        d = r.json()
        ok = (
            r.status_code == 200
            and d["count"] == 1
            and d["events"][0]["kind"] == "health.degraded"
            and d["events"][0]["service"] == "daemon"
        )
        record("V2 mock event", ok, f"status={r.status_code} count={d.get('count')} kind={d.get('events',[{}])[0].get('kind') if d.get('events') else None}")
    finally:
        if backup is not None:
            _WATCHDOG_LOG_PATH.write_bytes(backup)
        else:
            try:
                _WATCHDOG_LOG_PATH.unlink()
            except FileNotFoundError:
                pass
except Exception as e:
    record("V2 mock event", False, f"exc={e!r}")

# V3 log missing → empty
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import _WATCHDOG_LOG_PATH

    backup = None
    if _WATCHDOG_LOG_PATH.exists():
        backup = _WATCHDOG_LOG_PATH.read_bytes()
        _WATCHDOG_LOG_PATH.unlink()
    try:
        client = TestClient(app)
        r = client.get("/api/watchdog/recent")
        d = r.json()
        ok = r.status_code == 200 and d == {"events": [], "count": 0}
        record("V3 missing log", ok, f"resp={d}")
    finally:
        if backup is not None:
            _WATCHDOG_LOG_PATH.write_bytes(backup)
except Exception as e:
    record("V3 missing log", False, f"exc={e!r}")

# V4 limit clamp
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import _WATCHDOG_LOG_PATH

    backup = None
    if _WATCHDOG_LOG_PATH.exists():
        backup = _WATCHDOG_LOG_PATH.read_bytes()
    try:
        # write 150 events
        lines = [json.dumps({"kind": "noop", "i": i}) for i in range(150)]
        _WATCHDOG_LOG_PATH.write_text("\n".join(lines) + "\n")
        client = TestClient(app)
        r1 = client.get("/api/watchdog/recent?limit=0")
        d1 = r1.json()
        r2 = client.get("/api/watchdog/recent?limit=500")
        d2 = r2.json()
        ok = d1["count"] == 1 and d2["count"] == 100
        record("V4 limit clamp", ok, f"low_count={d1['count']} high_count={d2['count']}")
    finally:
        if backup is not None:
            _WATCHDOG_LOG_PATH.write_bytes(backup)
        else:
            try:
                _WATCHDOG_LOG_PATH.unlink()
            except FileNotFoundError:
                pass
except Exception as e:
    record("V4 limit clamp", False, f"exc={e!r}")

# V5 corrupt JSON line
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import _WATCHDOG_LOG_PATH

    backup = None
    if _WATCHDOG_LOG_PATH.exists():
        backup = _WATCHDOG_LOG_PATH.read_bytes()
    try:
        lines = [
            json.dumps({"kind": "ok", "i": 1}),
            "this is not json {{",
            json.dumps({"kind": "ok", "i": 2}),
        ]
        _WATCHDOG_LOG_PATH.write_text("\n".join(lines) + "\n")
        client = TestClient(app)
        r = client.get("/api/watchdog/recent?limit=10")
        d = r.json()
        ok = r.status_code == 200 and d["count"] == 2 and all(e.get("kind") == "ok" for e in d["events"])
        record("V5 corrupt line", ok, f"count={d['count']} events={d['events']}")
    finally:
        if backup is not None:
            _WATCHDOG_LOG_PATH.write_bytes(backup)
        else:
            try:
                _WATCHDOG_LOG_PATH.unlink()
            except FileNotFoundError:
                pass
except Exception as e:
    record("V5 corrupt line", False, f"exc={e!r}")

# V6 HTML contains red bar + pollWatchdog + setInterval 10000
try:
    txt = APP_PY.read_text()
    ok = (
        'id="watchdog-bar"' in txt
        and 'pollWatchdog' in txt
        and 'setInterval(pollWatchdog, 10000)' in txt
        and 'id="watchdog-msg"' in txt
    )
    record("V6 HTML+JS pieces", ok, f"watchdog-bar={('id=\"watchdog-bar\"' in txt)} pollWatchdog={'pollWatchdog' in txt} setInterval={'setInterval(pollWatchdog, 10000)' in txt}")
except Exception as e:
    record("V6 HTML+JS pieces", False, f"exc={e!r}")

# V7 textContent escape
try:
    txt = APP_PY.read_text()
    # find pollWatchdog block and ensure it uses textContent for msg, not innerHTML
    idx_poll = txt.find("async function pollWatchdog")
    idx_end = txt.find("setInterval(pollWatchdog,", idx_poll)
    poll_block = txt[idx_poll:idx_end] if idx_poll != -1 else ""
    uses_textcontent = "getElementById('watchdog-msg').textContent" in poll_block
    no_innerhtml = "watchdog-msg').innerHTML" not in poll_block and 'watchdog-msg").innerHTML' not in poll_block
    ok = uses_textcontent and no_innerhtml
    record("V7 textContent escape", ok, f"textContent={uses_textcontent} no_innerHTML={no_innerhtml}")
except Exception as e:
    record("V7 textContent escape", False, f"exc={e!r}")

# V8 d001..d005 existing routes intact
try:
    from coco.dashboard.app import app as _app2

    paths = {r.path for r in _app2.routes if hasattr(r, "path")}
    needed = {"/frame.jpg", "/api/action", "/api/pose", "/api/config/llm_model"}
    missing = needed - paths
    ok = not missing
    record("V8 d001..d005 routes", ok, f"missing={missing}")
except Exception as e:
    record("V8 d001..d005 routes", False, f"exc={e!r}")

fail = [n for n, ok, _ in results if not ok]
print()
print(f"=== Summary: {len(results)-len(fail)}/{len(results)} PASS, {len(fail)} FAIL ===")
if fail:
    print("FAILED:", fail)
    sys.exit(1)
sys.exit(0)
