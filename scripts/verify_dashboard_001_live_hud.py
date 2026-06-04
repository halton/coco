"""dashboard-001 live HUD — verification (V0-V7).

跑法:  /Users/halton/work/coco/.venv/bin/python scripts/verify_dashboard_001_live_hud.py

V0  hash lock — coco/dashboard/app.py + event_parser.py 存在
V1  import + routes — / , /frame.jpg , /stream/camera.mjpg , /ws/events , /healthz
V2  event_parser 解析 [coco][vad] transcript / reply
V3  event_parser 解析 wake.hit / FaceTracker primary / [tts] first_chunk_ms
V4  /frame.jpg 文件存在 → 返回 jpeg bytes (200)
V5  /frame.jpg 缺失 → 返回 placeholder png 而非 500
V6  多事件批量解析无丢
V7  app 导入不抛 / TestClient 简单跑通
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "evidence" / "dashboard-001"
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

# V1 import + routes
v1_ok = False
v1_detail = ""
try:
    from coco.dashboard.app import app as dash_app  # noqa
    paths = {getattr(r, "path", None) for r in dash_app.routes}
    needed = {"/", "/frame.jpg", "/stream/camera.mjpg", "/ws/events", "/healthz"}
    missing = needed - paths
    v1_ok = not missing
    v1_detail = f"routes={sorted(p for p in paths if p)} missing={sorted(missing)}"
except Exception as e:  # noqa: BLE001
    v1_detail = f"import err: {type(e).__name__}: {e}"
step("V1 import + key routes present", v1_ok, v1_detail)

# V2 parser transcript+reply
v2_ok = False
v2_detail = ""
try:
    from coco.dashboard.event_parser import parse_line
    line = "2025-06-04 10:00:00 [coco][vad] transcript='你好可可' reply='你好呀!'"
    evt = parse_line(line)
    v2_ok = (
        evt is not None
        and evt.get("type") == "transcript"
        and evt.get("transcript") == "你好可可"
        and evt.get("reply") == "你好呀!"
    )
    v2_detail = f"evt={evt}"
except Exception as e:  # noqa: BLE001
    v2_detail = f"err: {e}"
step("V2 parse_line vad transcript+reply", v2_ok, v2_detail)

# V3 wake / face primary / tts
v3_ok = False
v3_detail = ""
try:
    from coco.dashboard.event_parser import parse_line
    e_wake = parse_line("[wake] hit kw='hi-coco'")
    e_face = parse_line("FaceTracker primary track_id=42 cx=0.5")
    e_tts = parse_line("[tts] first_chunk_ms=237 voice=...")
    v3_ok = (
        e_wake and e_wake.get("type") == "wake"
        and e_face and e_face.get("type") == "vision"
        and e_tts and e_tts.get("type") == "tts" and e_tts.get("first_chunk_ms") == 237
    )
    v3_detail = f"wake={e_wake and e_wake.get('type')} face={e_face and e_face.get('type')} tts={e_tts and e_tts.get('first_chunk_ms')}"
except Exception as e:  # noqa: BLE001
    v3_detail = f"err: {e}"
step("V3 parse_line wake/face/tts", v3_ok, v3_detail)

# V4 + V5: TestClient against /frame.jpg
v4_ok = v5_ok = False
v4_detail = v5_detail = ""
tmpdir = Path(tempfile.mkdtemp(prefix="dash_verify_"))
fake_frame = tmpdir / "frame.jpg"
fake_log = tmpdir / "log"
fake_log.write_text("")
# 1x1 jpeg minimal bytes via cv2
try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    ok, buf = cv2.imencode(".jpg", np.zeros((4, 4, 3), dtype=np.uint8))
    assert ok
    fake_frame.write_bytes(buf.tobytes())
except Exception:
    # 退路: 直接拿 jpeg magic bytes 也行
    fake_frame.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"\xff\xd9")

os.environ["COCO_DASHBOARD_FRAME_PATH"] = str(fake_frame)
os.environ["COCO_DASHBOARD_LOG_PATH"] = str(fake_log)
# 强制重新 import app (env 在 import time 取值)
import importlib
import coco.dashboard.app as _dash_app_mod
importlib.reload(_dash_app_mod)

try:
    from fastapi.testclient import TestClient
    client = TestClient(_dash_app_mod.app)
    r = client.get("/frame.jpg")
    v4_ok = r.status_code == 200 and len(r.content) > 0 and r.headers.get("content-type", "").startswith("image/jpeg")
    v4_detail = f"status={r.status_code} ctype={r.headers.get('content-type')} len={len(r.content)}"
except Exception as e:  # noqa: BLE001
    v4_detail = f"err: {e}"
step("V4 /frame.jpg with file present", v4_ok, v4_detail)

# remove file → placeholder
try:
    fake_frame.unlink()
    from fastapi.testclient import TestClient
    client = TestClient(_dash_app_mod.app)
    r = client.get("/frame.jpg")
    v5_ok = (
        r.status_code == 200
        and len(r.content) > 0
        and r.headers.get("content-type", "").startswith("image/png")
    )
    v5_detail = f"status={r.status_code} ctype={r.headers.get('content-type')} len={len(r.content)}"
except Exception as e:  # noqa: BLE001
    v5_detail = f"err: {e}"
step("V5 /frame.jpg missing → placeholder png", v5_ok, v5_detail)

# V6 多事件批量
v6_ok = False
v6_detail = ""
try:
    from coco.dashboard.event_parser import parse_line
    lines = [
        "[coco][vad] transcript='hello' reply='hi'",
        "[wake] hit kw='hi'",
        "FaceTracker primary track_id=7",
        "[tts] first_chunk_ms=100",
        "[coco][vad] transcript='bye'",  # transcript only
        "noise line should be ignored",
    ]
    parsed = [parse_line(l) for l in lines]
    recognized = [p for p in parsed if p is not None]
    v6_ok = len(recognized) == 5
    v6_detail = f"recognized={len(recognized)}/5 types={[p['type'] for p in recognized]}"
except Exception as e:  # noqa: BLE001
    v6_detail = f"err: {e}"
step("V6 batch parse 5 events recognized", v6_ok, v6_detail)

# V7 root html + healthz
v7_ok = False
v7_detail = ""
try:
    from fastapi.testclient import TestClient
    client = TestClient(_dash_app_mod.app)
    r1 = client.get("/")
    r2 = client.get("/healthz")
    v7_ok = (
        r1.status_code == 200
        and "可可 Live HUD" in r1.text
        and r2.status_code == 200
        and r2.json().get("ok") is True
    )
    v7_detail = f"index={r1.status_code} healthz={r2.status_code}"
except Exception as e:  # noqa: BLE001
    v7_detail = f"err: {e}"
step("V7 / + /healthz served", v7_ok, v7_detail)


passed = sum(1 for r in results if r["status"] == "PASS")
total = len(results)
all_pass = passed == total
print(f"\n=== dashboard-001 verify: {passed}/{total} PASS ===")

SUMMARY.write_text(json.dumps({
    "feature": "dashboard-001-live-hud",
    "passed": passed,
    "total": total,
    "results": results,
}, ensure_ascii=False, indent=2))

sys.exit(0 if all_pass else 1)
