"""dashboard-007-extend: face_detection / gesture / scene_caption toggle — verification (V0-V10).

跑法:  /Users/halton/work/coco/.venv/bin/python scripts/verify_dashboard_007_extend_all_perception_toggles.py

V0   hash lock — 5 个文件存在 (face_tracker / gesture / scene_caption / app.py / main.py)
V1   3 个 class 含 enabled + set_enabled (FaceTracker / GestureRecognizer / SceneCaptionEmitter)
V2   FaceTracker.set_enabled(False) → _tick early return (无 cam 也不抛)
V3   GestureRecognizer.set_enabled(False) → feed_frame 返 None + on_gesture 不触发 (WAVE 不响)
V4   SceneCaptionEmitter.set_enabled(False) → _tick early return (无 cam 也不抛)
V5   _ALLOWED_PERCEPTION_KEYS 含全部 5 keys
V6   POST /api/config/{face_detection,gesture,scene_caption}_enabled → 200 + 写文件
V7   GET /api/config/{5 keys} 默认 enabled=true + 读回与 POST 一致
V8   HTML 含 5 个 toggle id + changePerception map 5 keys + loadPerception map 5 keys
V9   regression: dashboard-007 原 face_id + wake toggle 端点未破坏 + HTML 仍有 toggle
V10  regression: dashboard-005 LLM model 端点仍正常 (POST/GET 不被 generic 路由吞)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "evidence" / "dashboard-007-extend-all-perception-toggles"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY = EVIDENCE_DIR / "verify_summary.json"

APP_PY = ROOT / "coco" / "dashboard" / "app.py"
FACE_TRACKER_PY = ROOT / "coco" / "perception" / "face_tracker.py"
GESTURE_PY = ROOT / "coco" / "perception" / "gesture.py"
SCENE_CAPTION_PY = ROOT / "coco" / "perception" / "scene_caption.py"
MAIN_PY = ROOT / "coco" / "main.py"

_TMP_DIR = Path(tempfile.mkdtemp(prefix="coco_d007e_"))
_TMP_CFG = _TMP_DIR / "runtime_config.json"

results = []


def step(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name} :: {detail}")
    results.append({"name": name, "status": status, "detail": detail})


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# V0 hash lock — 5 files
v0_ok = (
    APP_PY.is_file()
    and FACE_TRACKER_PY.is_file()
    and GESTURE_PY.is_file()
    and SCENE_CAPTION_PY.is_file()
    and MAIN_PY.is_file()
)
v0_detail = ""
if v0_ok:
    v0_detail = (
        f"face_tracker.sha={_sha(FACE_TRACKER_PY)[:12]} "
        f"gesture.sha={_sha(GESTURE_PY)[:12]} "
        f"scene_caption.sha={_sha(SCENE_CAPTION_PY)[:12]} "
        f"app.sha={_sha(APP_PY)[:12]} "
        f"main.sha={_sha(MAIN_PY)[:12]}"
    )
step("V0 hash lock 5 files exist", v0_ok, v0_detail)


# V1 3 classes have enabled + set_enabled
v1_ok = False
v1_detail = ""
try:
    from coco.perception.face_tracker import FaceTracker  # noqa: E402
    from coco.perception.gesture import GestureRecognizer  # noqa: E402
    from coco.perception.scene_caption import SceneCaptionEmitter  # noqa: E402

    se = threading.Event()
    ft = FaceTracker(se, camera_spec=None)
    gr = GestureRecognizer(se)
    sc = SceneCaptionEmitter(se)
    classes_ok = []
    for label, inst in [
        ("FaceTracker", ft),
        ("GestureRecognizer", gr),
        ("SceneCaptionEmitter", sc),
    ]:
        has_attr = hasattr(inst, "enabled") and isinstance(inst.enabled, bool)
        has_setter = callable(getattr(inst, "set_enabled", None))
        default_on = getattr(inst, "enabled", None) is True
        classes_ok.append(
            (label, has_attr and has_setter and default_on, has_attr, has_setter, default_on)
        )
    v1_ok = all(t[1] for t in classes_ok)
    v1_detail = "; ".join(
        f"{t[0]}(attr={t[2]} setter={t[3]} on={t[4]})" for t in classes_ok
    )
except Exception as e:  # noqa: BLE001
    v1_detail = f"err: {type(e).__name__}: {e}"
step("V1 3 classes have enabled + set_enabled", v1_ok, v1_detail)


# V2 FaceTracker.set_enabled(False) → _tick early return (无 cam 也不抛)
v2_ok = False
v2_detail = ""
try:
    from coco.perception.face_tracker import FaceTracker  # noqa: E402
    import coco.perception.face_tracker as _ft_mod  # noqa: E402

    # 防止 hot-reload helper 在 _tick 内意外覆盖 enabled
    _orig_interval = _ft_mod._FACE_TRACKER_RELOAD_INTERVAL_S
    _ft_mod._FACE_TRACKER_RELOAD_INTERVAL_S = 1e9
    try:
        se = threading.Event()
        ft = FaceTracker(se, camera_spec=None)
        ft.set_enabled(False)
        # _tick 应早 return：enabled=False；不抛
        ft._tick()
        v2_ok = ft.enabled is False
        v2_detail = f"enabled={ft.enabled} no exception"
    finally:
        _ft_mod._FACE_TRACKER_RELOAD_INTERVAL_S = _orig_interval
except Exception as e:  # noqa: BLE001
    v2_detail = f"err: {type(e).__name__}: {e}"
step("V2 FaceTracker.set_enabled(False) _tick early return", v2_ok, v2_detail)


# V3 GestureRecognizer.set_enabled(False) → feed_frame 返 None + on_gesture 不触发
v3_ok = False
v3_detail = ""
try:
    from coco.perception.gesture import (  # noqa: E402
        GestureRecognizer,
        GestureLabel,
        GestureKind,
    )
    import coco.perception.gesture as _g_mod  # noqa: E402

    _orig_interval = _g_mod._GESTURE_RELOAD_INTERVAL_S
    _g_mod._GESTURE_RELOAD_INTERVAL_S = 1e9
    try:
        triggered = []

        def _cb(lbl):  # noqa: ANN001
            triggered.append(lbl)

        se = threading.Event()

        class _AlwaysWaveBackend:
            def detect(self, frames):  # noqa: ANN001
                return GestureLabel(
                    kind=GestureKind.WAVE, confidence=1.0, ts=time.monotonic()
                )

        gr = GestureRecognizer(
            se, backend=_AlwaysWaveBackend(), on_gesture=_cb, cooldown_per_kind_s=0.0
        )
        gr.set_enabled(False)
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        ret = gr.feed_frame(frame)
        # _tick also early return (no camera, but enabled=False kicks in first)
        gr._tick()
        v3_ok = (
            ret is None
            and len(triggered) == 0
            and gr.enabled is False
        )
        v3_detail = (
            f"feed_frame_ret={ret} triggered_n={len(triggered)} enabled={gr.enabled}"
        )
    finally:
        _g_mod._GESTURE_RELOAD_INTERVAL_S = _orig_interval
except Exception as e:  # noqa: BLE001
    v3_detail = f"err: {type(e).__name__}: {e}"
step("V3 GestureRecognizer set_enabled(False) → feed_frame None + WAVE 不触发", v3_ok, v3_detail)


# V4 SceneCaptionEmitter.set_enabled(False) → _tick early return
v4_ok = False
v4_detail = ""
try:
    from coco.perception.scene_caption import SceneCaptionEmitter  # noqa: E402
    import coco.perception.scene_caption as _sc_mod  # noqa: E402

    _orig_interval = _sc_mod._SCENE_CAPTION_RELOAD_INTERVAL_S
    _sc_mod._SCENE_CAPTION_RELOAD_INTERVAL_S = 1e9
    try:
        se = threading.Event()
        sc = SceneCaptionEmitter(se)
        sc.set_enabled(False)
        sc._tick()  # 不抛；enabled=False 早 return
        # 验证 stats.ticks 不应被 ++（在 enabled=False 早 return 之前没机会自增）
        v4_ok = sc.enabled is False and sc.stats.ticks == 0
        v4_detail = f"enabled={sc.enabled} stats.ticks={sc.stats.ticks}"
    finally:
        _sc_mod._SCENE_CAPTION_RELOAD_INTERVAL_S = _orig_interval
except Exception as e:  # noqa: BLE001
    v4_detail = f"err: {type(e).__name__}: {e}"
step("V4 SceneCaptionEmitter set_enabled(False) _tick early return", v4_ok, v4_detail)


# V5 _ALLOWED_PERCEPTION_KEYS 含 5 keys
v5_ok = False
v5_detail = ""
try:
    from coco.dashboard.app import _ALLOWED_PERCEPTION_KEYS  # noqa: E402
    expected = {
        "face_id_enabled",
        "wake_enabled",
        "face_detection_enabled",
        "gesture_enabled",
        "scene_caption_enabled",
    }
    v5_ok = _ALLOWED_PERCEPTION_KEYS == expected
    v5_detail = f"keys={sorted(_ALLOWED_PERCEPTION_KEYS)}"
except Exception as e:  # noqa: BLE001
    v5_detail = f"err: {type(e).__name__}: {e}"
step("V5 _ALLOWED_PERCEPTION_KEYS 含 5 keys", v5_ok, v5_detail)


# V6 POST 3 new keys → 200 + 写文件
v6_ok = False
v6_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a, _RUNTIME_CONFIG_PATH as _cfg
    if _cfg.exists():
        _cfg.unlink()
    client = TestClient(_a)
    r_fd = client.post("/api/config/face_detection_enabled", json={"enabled": False})
    r_g = client.post("/api/config/gesture_enabled", json={"enabled": False})
    r_sc = client.post("/api/config/scene_caption_enabled", json={"enabled": True})
    wrote = _cfg.exists()
    disk = json.loads(_cfg.read_text()) if wrote else {}
    v6_ok = (
        r_fd.status_code == 200
        and r_g.status_code == 200
        and r_sc.status_code == 200
        and r_fd.json().get("status") == "saved"
        and r_g.json().get("status") == "saved"
        and r_sc.json().get("status") == "saved"
        and r_fd.json().get("enabled") is False
        and r_g.json().get("enabled") is False
        and r_sc.json().get("enabled") is True
        and disk.get("face_detection_enabled") is False
        and disk.get("gesture_enabled") is False
        and disk.get("scene_caption_enabled") is True
    )
    v6_detail = (
        f"fd={r_fd.status_code} g={r_g.status_code} sc={r_sc.status_code} disk={disk}"
    )
    if _cfg.exists():
        _cfg.unlink()
except Exception as e:  # noqa: BLE001
    v6_detail = f"err: {type(e).__name__}: {e}"
step("V6 POST 3 new keys → 200 + writes disk", v6_ok, v6_detail)


# V7 GET 5 keys 默认 enabled=true + 读回与 POST 一致
v7_ok = False
v7_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a, _RUNTIME_CONFIG_PATH as _cfg
    if _cfg.exists():
        _cfg.unlink()
    client = TestClient(_a)
    keys = [
        "face_id_enabled",
        "wake_enabled",
        "face_detection_enabled",
        "gesture_enabled",
        "scene_caption_enabled",
    ]
    # 7a 默认 true
    defaults_ok = True
    default_results = {}
    for k in keys:
        r = client.get(f"/api/config/{k}")
        body = r.json()
        default_results[k] = (r.status_code, body)
        if r.status_code != 200 or body != {"key": k, "enabled": True}:
            defaults_ok = False
    # 7b POST False → GET 读 False
    readback_ok = True
    for k in keys:
        client.post(f"/api/config/{k}", json={"enabled": False})
    for k in keys:
        r = client.get(f"/api/config/{k}")
        body = r.json()
        if r.status_code != 200 or body != {"key": k, "enabled": False}:
            readback_ok = False
            break
    v7_ok = defaults_ok and readback_ok
    v7_detail = f"defaults_ok={defaults_ok} readback_ok={readback_ok}"
    if _cfg.exists():
        _cfg.unlink()
except Exception as e:  # noqa: BLE001
    v7_detail = f"err: {type(e).__name__}: {e}"
step("V7 GET 5 keys defaults+readback", v7_ok, v7_detail)


# V8 HTML 含 5 个 toggle id + JS map 5 keys
v8_ok = False
v8_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    r = client.get("/")
    html = r.text
    toggle_ids = [
        'id="face-id-toggle"',
        'id="wake-toggle"',
        'id="face-detection-toggle"',
        'id="gesture-toggle"',
        'id="scene-caption-toggle"',
    ]
    js_keys = [
        "face_id: 'face_id_enabled'",
        "wake: 'wake_enabled'",
        "face_detection: 'face_detection_enabled'",
        "gesture: 'gesture_enabled'",
        "scene_caption: 'scene_caption_enabled'",
    ]
    ids_ok = all(s in html for s in toggle_ids)
    js_ok = all(s in html for s in js_keys)
    load_map_ok = all(
        s in html for s in [
            "face_id_enabled: 'face-id-toggle'",
            "face_detection_enabled: 'face-detection-toggle'",
            "gesture_enabled: 'gesture-toggle'",
            "scene_caption_enabled: 'scene-caption-toggle'",
        ]
    )
    v8_ok = r.status_code == 200 and ids_ok and js_ok and load_map_ok
    v8_detail = f"ids_ok={ids_ok} js_ok={js_ok} load_map_ok={load_map_ok}"
except Exception as e:  # noqa: BLE001
    v8_detail = f"err: {type(e).__name__}: {e}"
step("V8 HTML 5 toggle + JS map 5 keys + loadPerception map", v8_ok, v8_detail)


# V9 regression: dashboard-007 原 face_id + wake 端点 + HTML 仍 OK
v9_ok = False
v9_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a, _RUNTIME_CONFIG_PATH as _cfg
    if _cfg.exists():
        _cfg.unlink()
    client = TestClient(_a)
    r1 = client.post("/api/config/face_id_enabled", json={"enabled": True})
    r2 = client.post("/api/config/wake_enabled", json={"enabled": True})
    r3 = client.get("/api/config/face_id_enabled")
    r4 = client.get("/api/config/wake_enabled")
    html_r = client.get("/")
    v9_ok = (
        r1.status_code == 200
        and r2.status_code == 200
        and r3.status_code == 200
        and r4.status_code == 200
        and r3.json() == {"key": "face_id_enabled", "enabled": True}
        and r4.json() == {"key": "wake_enabled", "enabled": True}
        and 'id="face-id-toggle"' in html_r.text
        and 'id="wake-toggle"' in html_r.text
    )
    v9_detail = (
        f"r1={r1.status_code} r2={r2.status_code} r3={r3.json()} r4={r4.json()}"
    )
    if _cfg.exists():
        _cfg.unlink()
except Exception as e:  # noqa: BLE001
    v9_detail = f"err: {type(e).__name__}: {e}"
step("V9 regression dashboard-007 face_id+wake endpoints+HTML", v9_ok, v9_detail)


# V10 regression: dashboard-005 LLM model 端点仍 OK (不被 generic 路由吞)
v10_ok = False
v10_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a, _RUNTIME_CONFIG_PATH as _cfg
    if _cfg.exists():
        _cfg.unlink()
    client = TestClient(_a)
    r1 = client.get("/api/config/llm_model")
    b1 = r1.json()
    r2 = client.post("/api/config/llm_model", json={"model": "gpt-4o"})
    b2 = r2.json()
    r3 = client.post("/api/config/llm_model", json={"model": "fake-xxx"})
    v10_ok = (
        r1.status_code == 200
        and b1 == {"model": ""}
        and r2.status_code == 200
        and b2.get("status") == "saved"
        and b2.get("model") == "gpt-4o"
        and r3.status_code == 400
    )
    v10_detail = (
        f"get={b1} post_ok={b2} invalid_status={r3.status_code}"
    )
    if _cfg.exists():
        _cfg.unlink()
except Exception as e:  # noqa: BLE001
    v10_detail = f"err: {type(e).__name__}: {e}"
step("V10 regression dashboard-005 llm_model 端点 OK", v10_ok, v10_detail)


passed = sum(1 for r in results if r["status"] == "PASS")
total = len(results)
all_pass = passed == total
print(f"\n=== dashboard-007-extend verify: {passed}/{total} PASS ===")

SUMMARY.write_text(json.dumps({
    "feature": "dashboard-007-extend-all-perception-toggles",
    "passed": passed,
    "total": total,
    "results": results,
    "ts": time.time(),
}, ensure_ascii=False, indent=2))

try:
    import shutil
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
except Exception:
    pass

sys.exit(0 if all_pass else 1)
