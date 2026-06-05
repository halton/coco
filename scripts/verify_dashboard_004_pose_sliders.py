"""dashboard-004 pose sliders + /api/pose — verification (V0-V7).

跑法:
  /Users/halton/work/coco/.venv/bin/python scripts/verify_dashboard_004_pose_sliders.py > /tmp/v_d004.log 2>&1; rc=$?; tail -30 /tmp/v_d004.log; echo "rc=$rc"

V0  hash lock — coco/dashboard/app.py 存在 + 含 dashboard-004 标记
V1  import + POST /api/pose 路由存在
V2  PoseRequest 字段 pitch/yaw/roll 默认 0.0
V3  POST /api/pose body {pitch:0.1, yaw:0.2, roll:0} → 200 + fake=True
V4  clamp: pitch=10, yaw=-99, roll=99 → 200 但实际值 clamp 到 ±0.6
V5  HTML 含 3 个 input range + onPose() + sendPose() + resetPose() + pose-panel
V6  textContent escape 回归 dashboard-001 P1 (createTextNode 仍在用)
V7  throttle 200ms (grep setTimeout(sendPose, 200))
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "evidence" / "dashboard-004-pose-sliders"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY = EVIDENCE_DIR / "verify_summary.json"

APP_PY = ROOT / "coco" / "dashboard" / "app.py"

# fake 模式：避免 verify 时真启 ReachyMini subprocess
os.environ["COCO_DASHBOARD_FAKE_POSE"] = "1"

results = []


def step(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name} :: {detail}")
    results.append({"name": name, "status": status, "detail": detail})


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# V0  hash lock
v0_ok = APP_PY.is_file()
v0_detail = ""
if v0_ok:
    src = APP_PY.read_text()
    has_marker = "dashboard-004: pose sliders" in src and "_POSE_MAX_RAD" in src
    v0_ok = has_marker
    v0_detail = f"app.sha={_sha(APP_PY)[:12]} has_marker={has_marker}"
step("V0 hash lock app.py + dashboard-004 marker", v0_ok, v0_detail)

# V1 import + POST /api/pose route
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
    api_pose = [m for (p, m) in routes if p == "/api/pose"]
    v1_ok = bool(api_pose) and any("POST" in m for m in api_pose)
    v1_detail = f"/api/pose methods={api_pose}"
except Exception as e:  # noqa: BLE001
    v1_detail = f"import err: {type(e).__name__}: {e}"
step("V1 POST /api/pose route present", v1_ok, v1_detail)

# V2 PoseRequest default fields
v2_ok = False
v2_detail = ""
try:
    from coco.dashboard.app import PoseRequest
    req = PoseRequest()
    fields = list(PoseRequest.model_fields.keys())
    v2_ok = (
        set(fields) == {"pitch", "yaw", "roll"}
        and req.pitch == 0.0 and req.yaw == 0.0 and req.roll == 0.0
    )
    v2_detail = f"fields={fields} defaults=({req.pitch},{req.yaw},{req.roll})"
except Exception as e:  # noqa: BLE001
    v2_detail = f"err: {type(e).__name__}: {e}"
step("V2 PoseRequest pitch/yaw/roll default 0.0", v2_ok, v2_detail)

# V3 POST /api/pose 正常路径 (fake)
v3_ok = False
v3_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    r = client.post("/api/pose", json={"pitch": 0.1, "yaw": 0.2, "roll": 0.0})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    v3_ok = (
        r.status_code == 200
        and body.get("status") == "ok"
        and body.get("rc") == 0
        and body.get("fake") is True
        and abs(body.get("pitch", -999) - 0.1) < 1e-6
        and abs(body.get("yaw", -999) - 0.2) < 1e-6
        and abs(body.get("roll", -999) - 0.0) < 1e-6
    )
    v3_detail = f"status={r.status_code} body={body}"
except Exception as e:  # noqa: BLE001
    v3_detail = f"err: {type(e).__name__}: {e}"
step("V3 POST /api/pose normal (fake) → 200 echo pitch/yaw/roll", v3_ok, v3_detail)

# V4 clamp 超界
v4_ok = False
v4_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a, _POSE_MAX_RAD
    client = TestClient(_a)
    r = client.post("/api/pose", json={"pitch": 10.0, "yaw": -99.0, "roll": 99.0})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    v4_ok = (
        r.status_code == 200
        and abs(body.get("pitch", 0) - _POSE_MAX_RAD) < 1e-9
        and abs(body.get("yaw", 0) + _POSE_MAX_RAD) < 1e-9
        and abs(body.get("roll", 0) - _POSE_MAX_RAD) < 1e-9
    )
    v4_detail = f"status={r.status_code} body={body} POSE_MAX_RAD={_POSE_MAX_RAD}"
except Exception as e:  # noqa: BLE001
    v4_detail = f"err: {type(e).__name__}: {e}"
step("V4 clamp out-of-range → ±_POSE_MAX_RAD", v4_ok, v4_detail)

# V5 HTML 含滑条 + JS 函数
v5_ok = False
v5_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    r = client.get("/")
    html = r.text
    range_inputs = html.count('type="range"')
    has_pitch = 'id="pitch"' in html
    has_yaw = 'id="yaw"' in html
    has_roll = 'id="roll"' in html
    has_panel = 'id="pose-panel"' in html
    has_onpose = 'function onPose()' in html
    has_sendpose = 'async function sendPose()' in html
    has_resetpose = 'function resetPose()' in html
    has_api_pose = "fetch('/api/pose'" in html
    v5_ok = (
        r.status_code == 200
        and range_inputs >= 3
        and has_pitch and has_yaw and has_roll
        and has_panel and has_onpose and has_sendpose
        and has_resetpose and has_api_pose
    )
    v5_detail = (
        f"range_inputs={range_inputs} pitch/yaw/roll=({has_pitch},{has_yaw},{has_roll}) "
        f"panel={has_panel} onPose={has_onpose} sendPose={has_sendpose} "
        f"resetPose={has_resetpose} api_pose={has_api_pose}"
    )
except Exception as e:  # noqa: BLE001
    v5_detail = f"err: {type(e).__name__}: {e}"
step("V5 HTML has 3 sliders + onPose/sendPose/resetPose + pose-panel", v5_ok, v5_detail)

# V6 regression dashboard-001 P1: createTextNode 仍在用 (没被破坏)
v6_ok = False
v6_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    r = client.get("/")
    html = r.text
    uses_create_text_node = "createTextNode" in html
    # 不应该用 innerHTML 灌 server 事件
    no_inner_html_event = "innerHTML = e." not in html and ".innerHTML = ev" not in html
    v6_ok = uses_create_text_node and no_inner_html_event
    v6_detail = (
        f"createTextNode={uses_create_text_node} no_innerHTML_event={no_inner_html_event}"
    )
except Exception as e:  # noqa: BLE001
    v6_detail = f"err: {type(e).__name__}: {e}"
step("V6 regression: createTextNode escape still in place", v6_ok, v6_detail)

# V7 throttle 200ms
v7_ok = False
v7_detail = ""
try:
    src = APP_PY.read_text()
    has_throttle = "setTimeout(sendPose, 200)" in src
    has_clear_prev = "clearTimeout(poseTimer)" in src
    v7_ok = has_throttle and has_clear_prev
    v7_detail = f"setTimeout_200={has_throttle} clearTimeout_prev={has_clear_prev}"
except Exception as e:  # noqa: BLE001
    v7_detail = f"err: {type(e).__name__}: {e}"
step("V7 throttle 200ms (setTimeout(sendPose, 200) + clearTimeout)", v7_ok, v7_detail)


passed = sum(1 for r in results if r["status"] == "PASS")
total = len(results)
all_pass = passed == total
print(f"\n=== dashboard-004 verify: {passed}/{total} PASS ===")

SUMMARY.write_text(json.dumps({
    "feature": "dashboard-004-pose-sliders",
    "passed": passed,
    "total": total,
    "results": results,
}, ensure_ascii=False, indent=2))

sys.exit(0 if all_pass else 1)
