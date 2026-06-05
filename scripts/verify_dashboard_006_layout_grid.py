"""dashboard-006 三栏 grid 布局 + 折叠 details + 响应式 — verification (V0-V9).

跑法:  /Users/halton/work/coco/.venv/bin/python scripts/verify_dashboard_006_layout_grid.py

V0  hash lock — coco/dashboard/app.py 存在 + 打印新 sha
V1  imports OK + app.routes 含 7 个旧 endpoint
V2  HTML 含 viewport meta
V3  HTML 含 grid-template-columns (CSS Grid 三栏)
V4  HTML 中 position:fixed 使用 ≤1 次 (只允许 watchdog-bar inline style)
V5  HTML 含 <details> 且 action/pose/llm/watchdog-status 4 panel 均为 <details>
V6  perf-chart canvas + JS resizeCanvas() 函数存在
V7  media query @media max-width: 1024px 存在
V8  textContent escape (d001 P1) 没被破坏 — 仍含 mkSpan/mkBold + 不存在 innerHTML 写用户数据
V9  TestClient 调 / + /frame.jpg + /api/watchdog/recent 都活
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "evidence" / "dashboard-006-layout-grid"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY = EVIDENCE_DIR / "verify_summary.json"

APP_PY = ROOT / "coco" / "dashboard" / "app.py"

results: list[dict] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name} :: {detail}")
    results.append({"name": name, "status": status, "detail": detail})


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# V0
v0_ok = APP_PY.is_file()
v0_detail = f"app.sha={_sha(APP_PY)[:12]}" if v0_ok else "missing"
step("V0 hash lock app.py exists", v0_ok, v0_detail)

# Load HTML once
HTML = ""
try:
    from coco.dashboard.app import HTML_PAGE as _HTML, app as _APP

    HTML = _HTML
    _ROUTES = sorted({x.path for x in _APP.routes if hasattr(x, "path")})
except Exception as e:  # noqa: BLE001
    step("LOAD HTML/app", False, f"{type(e).__name__}: {e}")
    _ROUTES = []

# V1 imports + 7 endpoints intact
required_routes = {
    "/",
    "/frame.jpg",
    "/stream/camera.mjpg",
    "/api/action",
    "/api/pose",
    "/api/config/llm_model",
    "/api/watchdog/recent",
    "/ws/events",
}
missing = sorted(required_routes - set(_ROUTES))
v1_ok = not missing
step(
    "V1 imports + 8 endpoints intact",
    v1_ok,
    f"missing={missing or 'none'} have={[r for r in _ROUTES if r.startswith(('/api','/ws','/frame','/stream','/'))][:12]}",
)

# V2 viewport meta
v2_ok = (
    '<meta name="viewport"' in HTML
    and "width=device-width" in HTML
    and "initial-scale=1" in HTML
)
step("V2 viewport meta present", v2_ok, "viewport=device-width,initial-scale=1")

# V3 grid-template-columns (3 列)
v3_ok = "grid-template-columns:" in HTML and "display:grid" in HTML
# 必须至少出现一次 3-列定义 (matches "grid-template-columns: minmax(...) minmax(...) 320px")
m = re.search(
    r"grid-template-columns:\s*minmax\([^)]*\)\s+minmax\([^)]*\)\s+\d+px", HTML
)
v3_ok = v3_ok and (m is not None)
step("V3 CSS Grid 3-column", v3_ok, f"3col-pattern={'match' if m else 'miss'}")

# V4 position:fixed ≤1 (only watchdog-bar inline style allowed)
# 排除 CSS 注释 (/* ... position:fixed ... */)
fixed_lines = []
in_comment = False
for ln in HTML.splitlines():
    s = ln
    # crude /* */ stripper (per-line)
    s_no_comment = re.sub(r"/\*.*?\*/", "", s)
    if "position:fixed" in s_no_comment:
        fixed_lines.append(ln.strip()[:120])
v4_ok = len(fixed_lines) <= 1 and all("watchdog-bar" in ln for ln in fixed_lines)
step(
    "V4 position:fixed count ≤1 (only watchdog-bar)",
    v4_ok,
    f"count={len(fixed_lines)} lines={fixed_lines}",
)

# V5 <details> 4 panels
required_details = [
    '<details id="action-panel"',
    '<details id="pose-panel"',
    '<details id="llm-panel"',
    '<details id="watchdog-status"',
]
miss5 = [d for d in required_details if d not in HTML]
v5_ok = not miss5 and HTML.count("<details") >= 4
step(
    "V5 4 <details> panels",
    v5_ok,
    f"details_count={HTML.count('<details')} missing={miss5}",
)

# V6 perf-chart canvas + resizeCanvas()
has_canvas = 'id="perf-chart"' in HTML
has_resize_fn = "function resizeCanvas" in HTML
has_resize_listener = "addEventListener('resize'" in HTML and "resizeCanvas" in HTML
v6_ok = has_canvas and has_resize_fn and has_resize_listener
step(
    "V6 perf-chart canvas + resizeCanvas",
    v6_ok,
    f"canvas={has_canvas} fn={has_resize_fn} listener={has_resize_listener}",
)

# V7 media query
v7_ok = "@media (max-width: 1024px)" in HTML or "@media(max-width:1024px)" in HTML
step("V7 media query <=1024px", v7_ok, "responsive 1-col stack")

# V8 textContent escape (d001 P1 regression guard)
# 必须仍有 mkSpan/mkBold 风格 (textContent= 而非 innerHTML 写 user data)
has_mkspan = "mkSpan" in HTML and "mkBold" in HTML
# innerHTML 不能用于写来自 ws 的 e.*  字段
bad_innerhtml = bool(
    re.search(r"\.innerHTML\s*=\s*[^;]*e\.\w+", HTML)
)
v8_ok = has_mkspan and not bad_innerhtml
step(
    "V8 textContent escape preserved",
    v8_ok,
    f"mkSpan={has_mkspan} bad_innerHTML_with_e={bad_innerhtml}",
)

# V9 TestClient — 3 endpoint live
v9_ok = False
v9_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _APP2

    with TestClient(_APP2) as client:
        r1 = client.get("/")
        r2 = client.get("/frame.jpg")
        r3 = client.get("/api/watchdog/recent?limit=3")
    rc1 = r1.status_code
    rc2 = r2.status_code
    rc3 = r3.status_code
    v9_ok = (
        rc1 == 200
        and rc2 == 200
        and rc3 == 200
        and "Live HUD" in r1.text
        and "grid-template-columns" in r1.text
    )
    v9_detail = f"/={rc1} /frame.jpg={rc2} /api/watchdog/recent={rc3} HTML_grid={'grid-template-columns' in r1.text}"
except Exception as e:  # noqa: BLE001
    v9_detail = f"err: {type(e).__name__}: {e}"
step("V9 TestClient endpoints live", v9_ok, v9_detail)

# Summary
all_ok = all(r["status"] == "PASS" for r in results)
SUMMARY.write_text(
    json.dumps(
        {
            "feature": "dashboard-006-layout-grid",
            "app_sha": _sha(APP_PY),
            "all_pass": all_ok,
            "steps": results,
        },
        indent=2,
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
print(f"\nSUMMARY: {'ALL PASS' if all_ok else 'FAIL'} ({sum(1 for r in results if r['status']=='PASS')}/{len(results)})")
print(f"summary written: {SUMMARY}")
sys.exit(0 if all_ok else 1)
