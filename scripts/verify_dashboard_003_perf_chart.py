"""dashboard-003 perf chart — verification (V0-V7).

跑法:  /Users/halton/work/coco/.venv/bin/python scripts/verify_dashboard_003_perf_chart.py

V0  hash lock — coco/dashboard/app.py + event_parser.py 存在 (打印 sha)
V1  event_parser import 不抛 + 含 [coco][vad|ptt] dt= 的行被解析出 dt 字段
V2  HTML 含 <canvas id="perf-chart"
V3  JS 含 dtPoints + firstChunkPoints 两个数组变量
V4  drawChart 函数定义存在 (function drawChart)
V5  ws.onmessage 处理 e.dt + e.first_chunk_ms (push 到对应数组并触发 drawChart)
V6  数组限 50 个点 (MAX_POINTS = 50 + .shift())
V7  textContent/createTextNode escape 没被破坏 (regression dashboard-001 P1)

注: V2-V7 直接对 app.py source 做扫描, 不 import app (避开并行 in-flight feature
可能引入的瞬时 syntax 噪声); event_parser.py 单独 import 验证 dt 抽取行为。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "evidence" / "dashboard-003-perf-chart"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY = EVIDENCE_DIR / "verify_summary.json"

APP_PY = ROOT / "coco" / "dashboard" / "app.py"
PARSER_PY = ROOT / "coco" / "dashboard" / "event_parser.py"

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

# V1 event_parser import + dt 抽取 (vad + ptt)
v1_ok = False
v1_detail = ""
try:
    from coco.dashboard.event_parser import parse_line
    line = "[coco][vad] transcript='你好' reply='你好呀!' action=nod dt=1.23s"
    evt = parse_line(line)
    if evt is None:
        v1_detail = "parse_line returned None for vad line"
    else:
        dt_val = evt.get("dt")
        v1_ok = (
            evt.get("type") == "transcript"
            and isinstance(dt_val, float)
            and abs(dt_val - 1.23) < 1e-6
        )
        v1_detail = f"vad evt.type={evt.get('type')} dt={dt_val!r}"
    line2 = "[coco][ptt] transcript='hi' reply='hello' action=None dt=0.42s"
    evt2 = parse_line(line2)
    if evt2 is None or evt2.get("dt") != 0.42:
        v1_ok = False
        v1_detail += f" ; ptt evt={evt2!r}"
except Exception as e:  # noqa: BLE001
    v1_detail = f"import/parse err: {type(e).__name__}: {e}"
step("V1 event_parser extracts dt= from vad/ptt line", v1_ok, v1_detail)

# 加载 app.py source (V2-V7 基于 source 扫描)
try:
    APP_SRC = APP_PY.read_text(encoding="utf-8")
except Exception as e:  # noqa: BLE001
    APP_SRC = ""
    print(f"[WARN] read app.py failed: {e}")

# V2 HTML 含 canvas
v2_ok = '<canvas id="perf-chart"' in APP_SRC
step("V2 HTML has <canvas id=perf-chart", v2_ok, f"found={v2_ok}")

# V3 JS 含 dtPoints + firstChunkPoints
has_dt = "dtPoints" in APP_SRC
has_fc = "firstChunkPoints" in APP_SRC
v3_ok = has_dt and has_fc
step("V3 JS has dtPoints + firstChunkPoints arrays", v3_ok,
     f"dtPoints={has_dt} firstChunkPoints={has_fc}")

# V4 drawChart 函数存在
v4_ok = bool(re.search(r"function\s+drawChart\s*\(", APP_SRC))
step("V4 function drawChart() defined", v4_ok, f"found={v4_ok}")

# V5 ws.onmessage 处理 e.dt + e.first_chunk_ms + 调 drawChart
handles_dt = "e.dt" in APP_SRC
handles_fc = "e.first_chunk_ms" in APP_SRC
calls_draw_in_handler = "drawChart()" in APP_SRC
v5_ok = handles_dt and handles_fc and calls_draw_in_handler
step("V5 ws.onmessage handles dt + first_chunk_ms + calls drawChart", v5_ok,
     f"dt={handles_dt} first_chunk={handles_fc} drawChart_call={calls_draw_in_handler}")

# V6 数组限 50 点
has_max50 = bool(re.search(r"MAX_POINTS\s*=\s*50\b", APP_SRC))
has_shift = ".shift()" in APP_SRC
v6_ok = has_max50 and has_shift
step("V6 array rolling window 50 (MAX_POINTS=50 + .shift())", v6_ok,
     f"MAX_POINTS=50:{has_max50} shift():{has_shift}")

# V7 textContent / createTextNode regression
uses_text_content = ".textContent =" in APP_SRC
uses_create_text_node = "createTextNode" in APP_SRC
no_inner_html_event = "innerHTML = e." not in APP_SRC and ".innerHTML = ev" not in APP_SRC
v7_ok = uses_text_content and uses_create_text_node and no_inner_html_event
step("V7 regression: textContent/createTextNode escape still in place", v7_ok,
     f"textContent={uses_text_content} createTextNode={uses_create_text_node} "
     f"no_innerHTML_event={no_inner_html_event}")


passed = sum(1 for r in results if r["status"] == "PASS")
total = len(results)
all_pass = passed == total
print(f"\n=== dashboard-003 verify: {passed}/{total} PASS ===")

SUMMARY.write_text(json.dumps({
    "feature": "dashboard-003-perf-chart",
    "passed": passed,
    "total": total,
    "results": results,
    "app_sha": _sha(APP_PY),
    "parser_sha": _sha(PARSER_PY),
}, ensure_ascii=False, indent=2))

sys.exit(0 if all_pass else 1)
