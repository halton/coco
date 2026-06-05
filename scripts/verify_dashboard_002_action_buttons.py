"""dashboard-002 action buttons + timeline action 字段 — verification (V0-V8).

跑法:  /Users/halton/work/coco/.venv/bin/python scripts/verify_dashboard_002_action_buttons.py

V0  hash lock — coco/dashboard/app.py + event_parser.py 存在
V1  import + POST /api/action 路由存在
V2  event_parser 解析含 action= 的 [coco][vad] 行 → evt 含 action 字段
V3  event_parser 无 action 时 evt 不含 action key（回归 dashboard-001 V2）
V4  POST /api/action {"action":"shake"} → 200 (fake mode, rc=0)
V5  POST /api/action {"action":"invalid_xx"} → 400
V6  POST /api/action 缺 action 字段 → 422 (Pydantic validation)
V7  HTML 含 10 个 doAction 按钮调用
V8  textContent escape 没被破坏 (regression dashboard-001 P1：渲染 transcript/reply
    用 createTextNode 而非 innerHTML)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "evidence" / "dashboard-002"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY = EVIDENCE_DIR / "verify_summary.json"

APP_PY = ROOT / "coco" / "dashboard" / "app.py"
PARSER_PY = ROOT / "coco" / "dashboard" / "event_parser.py"

# fake 模式：避免 verify 时真启 ReachyMini subprocess
os.environ["COCO_DASHBOARD_ACTION_FAKE"] = "1"

results = []


def step(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name} :: {detail}")
    results.append({"name": name, "status": status, "detail": detail})


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# V0
v0_ok = APP_PY.is_file() and PARSER_PY.is_file()
v0_detail = ""
if v0_ok:
    v0_detail = f"app.sha={_sha(APP_PY)[:12]} parser.sha={_sha(PARSER_PY)[:12]}"
step("V0 hash lock app+parser exist", v0_ok, v0_detail)

# V1 import + POST /api/action 路由存在
v1_ok = False
v1_detail = ""
try:
    from coco.dashboard.app import app as dash_app  # noqa
    routes = []
    for r in dash_app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None) or set()
        if path:
            routes.append((path, methods))
    api_action = [m for (p, m) in routes if p == "/api/action"]
    v1_ok = bool(api_action) and any("POST" in m for m in api_action)
    v1_detail = f"/api/action methods={api_action}"
except Exception as e:  # noqa: BLE001
    v1_detail = f"import err: {type(e).__name__}: {e}"
step("V1 POST /api/action route present", v1_ok, v1_detail)

# V2 parser action 抽取
v2_ok = False
v2_detail = ""
try:
    from coco.dashboard.event_parser import parse_line
    cases = [
        # 无引号 (coco/main.py 实际格式)
        "[coco][vad] transcript='你好' reply='你好呀' action=nod dt=1.23s",
        # 双引号
        "[coco][vad] transcript='hi' reply='hello' action=\"shake\"",
        # 单引号
        "[coco][vad] transcript='hi' reply='hello' action='look_left'",
    ]
    parsed = [parse_line(c) for c in cases]
    actions = [p.get("action") if p else None for p in parsed]
    expected = ["nod", "shake", "look_left"]
    v2_ok = actions == expected and all(p and p.get("type") == "transcript" for p in parsed)
    v2_detail = f"actions={actions} expected={expected}"
except Exception as e:  # noqa: BLE001
    v2_detail = f"err: {type(e).__name__}: {e}"
step("V2 parse_line extracts action (no/dq/sq quotes)", v2_ok, v2_detail)

# V3 无 action 时不该有 action key（回归 dashboard-001）
v3_ok = False
v3_detail = ""
try:
    from coco.dashboard.event_parser import parse_line
    # 原 dashboard-001 V2 用例 + 各种空 action
    cases = [
        "2025-06-04 10:00:00 [coco][vad] transcript='你好可可' reply='你好呀!'",
        "[coco][vad] transcript='hi' reply='hello' action=None dt=1s",
        "[coco][vad] transcript='hi' reply='hello' action= dt=1s",
        "[coco][vad] transcript='bye'",
    ]
    parsed = [parse_line(c) for c in cases]
    no_action_keys = [(p is not None) and ("action" not in p) for p in parsed]
    types_ok = all(p and p.get("type") == "transcript" for p in parsed)
    v3_ok = all(no_action_keys) and types_ok
    v3_detail = f"no_action_keys={no_action_keys} types_ok={types_ok}"
except Exception as e:  # noqa: BLE001
    v3_detail = f"err: {type(e).__name__}: {e}"
step("V3 parse_line omits action when absent/None/empty", v3_ok, v3_detail)

# V4 POST /api/action shake → 200 (fake)
v4_ok = False
v4_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    r = client.post("/api/action", json={"action": "shake"})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    v4_ok = (
        r.status_code == 200
        and body.get("status") == "ok"
        and body.get("rc") == 0
        and body.get("fake") is True
    )
    v4_detail = f"status={r.status_code} body={body}"
except Exception as e:  # noqa: BLE001
    v4_detail = f"err: {type(e).__name__}: {e}"
step("V4 POST /api/action shake (fake) → 200", v4_ok, v4_detail)

# V5 invalid action → 400
v5_ok = False
v5_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    r = client.post("/api/action", json={"action": "rm_rf_slash"})
    v5_ok = r.status_code == 400 and "unknown action" in r.text
    v5_detail = f"status={r.status_code} body={r.text[:200]}"
except Exception as e:  # noqa: BLE001
    v5_detail = f"err: {type(e).__name__}: {e}"
step("V5 POST /api/action invalid → 400", v5_ok, v5_detail)

# V6 缺 action 字段 → 422
v6_ok = False
v6_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    r = client.post("/api/action", json={})
    v6_ok = r.status_code == 422
    v6_detail = f"status={r.status_code} body={r.text[:200]}"
except Exception as e:  # noqa: BLE001
    v6_detail = f"err: {type(e).__name__}: {e}"
step("V6 POST /api/action missing field → 422", v6_ok, v6_detail)

# V7 HTML 含 10 个 doAction 按钮调用
v7_ok = False
v7_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    r = client.get("/")
    html = r.text
    expected_actions = [
        "look_left", "look_right", "look_up", "look_down",
        "nod", "shake",
        "tilt_left", "tilt_right",
        "goto_sleep", "wake_up",
    ]
    missing = [a for a in expected_actions if f"doAction('{a}')" not in html]
    do_action_calls = html.count("doAction(")
    v7_ok = (
        r.status_code == 200
        and not missing
        and do_action_calls >= 10  # 10 button onclick + 1 函数定义 = 11
        and 'id="action-panel"' in html
    )
    v7_detail = f"missing={missing} doAction_calls={do_action_calls}"
except Exception as e:  # noqa: BLE001
    v7_detail = f"err: {type(e).__name__}: {e}"
step("V7 HTML has 10 doAction buttons + action-panel", v7_ok, v7_detail)

# V8 regression dashboard-001 P1：textContent / createTextNode 仍在用
v8_ok = False
v8_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    r = client.get("/")
    html = r.text
    uses_text_content = "s.textContent = text" in html or ".textContent =" in html
    uses_create_text_node = "createTextNode" in html
    # 不应该用 innerHTML 把 server 返回内容直接灌
    no_inner_html_for_event = "innerHTML = e." not in html and ".innerHTML = ev" not in html
    v8_ok = uses_text_content and uses_create_text_node and no_inner_html_for_event
    v8_detail = (
        f"textContent={uses_text_content} createTextNode={uses_create_text_node} "
        f"no_innerHTML_event={no_inner_html_for_event}"
    )
except Exception as e:  # noqa: BLE001
    v8_detail = f"err: {type(e).__name__}: {e}"
step("V8 regression: textContent/createTextNode escape still in place", v8_ok, v8_detail)


passed = sum(1 for r in results if r["status"] == "PASS")
total = len(results)
all_pass = passed == total
print(f"\n=== dashboard-002 verify: {passed}/{total} PASS ===")

SUMMARY.write_text(json.dumps({
    "feature": "dashboard-002-action-buttons",
    "passed": passed,
    "total": total,
    "results": results,
}, ensure_ascii=False, indent=2))

sys.exit(0 if all_pass else 1)
