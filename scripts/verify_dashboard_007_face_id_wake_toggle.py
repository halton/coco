"""dashboard-007 face_id + wake-word toggle (hot-reload) — verification (V0-V10).

跑法:  /Users/halton/work/coco/.venv/bin/python scripts/verify_dashboard_007_face_id_wake_toggle.py

V0   hash lock — coco/perception/face_id.py + coco/wake_word.py + coco/dashboard/app.py 存在
V1   imports OK + FaceIDClassifier.enabled / set_enabled 存在
V2   FaceIDClassifier(enabled=False).identify(crop) → (None, 0.0) early return
V3   FaceIDClassifier(enabled=True).identify(crop) → 走旧路径 (store 空 → (None, 0.0))
V4   wake_word.WakeWordDetector.mute/unmute/_muted 仍存在 (regression)
V5   _maybe_reload_face_id_runtime / _maybe_reload_wake_runtime 节流 30s 不抛 + 切 enabled/mute
V6   POST /api/config/face_id_enabled {"enabled": false} → 200 + 写 runtime_config.json
V7   POST /api/config/wake_enabled {"enabled": true} → 200 + 写 runtime_config.json
V8   GET /api/config/face_id_enabled / wake_enabled 返 {"key", "enabled": bool}
V9   HTML 含 face-id-toggle + wake-toggle + changePerception() + loadPerception()
V10  regression: GET /api/config/llm_model 仍返 {"model": ""}（不被 generic 路由吞掉）
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "evidence" / "dashboard-007-face-id-wake-toggle"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY = EVIDENCE_DIR / "verify_summary.json"

APP_PY = ROOT / "coco" / "dashboard" / "app.py"
FACE_ID_PY = ROOT / "coco" / "perception" / "face_id.py"
WAKE_PY = ROOT / "coco" / "wake_word.py"

# 把 runtime_config 指到临时目录，避免污染用户真 cache
_TMP_DIR = Path(tempfile.mkdtemp(prefix="coco_d007_"))
_TMP_CFG = _TMP_DIR / "runtime_config.json"
os.environ["COCO_RUNTIME_CONFIG_PATH"] = str(_TMP_CFG)

results = []


def step(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name} :: {detail}")
    results.append({"name": name, "status": status, "detail": detail})


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# V0 hash lock
v0_ok = APP_PY.is_file() and FACE_ID_PY.is_file() and WAKE_PY.is_file()
v0_detail = ""
if v0_ok:
    v0_detail = (
        f"app.sha={_sha(APP_PY)[:12]} "
        f"face_id.sha={_sha(FACE_ID_PY)[:12]} "
        f"wake.sha={_sha(WAKE_PY)[:12]}"
    )
step("V0 hash lock app+face_id+wake exist", v0_ok, v0_detail)


# V1 imports + FaceIDClassifier.enabled / set_enabled
v1_ok = False
v1_detail = ""
try:
    from coco.perception.face_id import FaceIDClassifier  # noqa: E402
    clf = FaceIDClassifier()
    has_enabled_attr = hasattr(clf, "enabled") and isinstance(clf.enabled, bool)
    has_set_enabled = callable(getattr(clf, "set_enabled", None))
    default_on = clf.enabled is True
    v1_ok = has_enabled_attr and has_set_enabled and default_on
    v1_detail = (
        f"enabled={clf.enabled} has_set_enabled={has_set_enabled} default_on={default_on}"
    )
except Exception as e:  # noqa: BLE001
    v1_detail = f"err: {type(e).__name__}: {e}"
step("V1 FaceIDClassifier.enabled + set_enabled", v1_ok, v1_detail)


# V2 identify enabled=False → (None, 0.0) early return
v2_ok = False
v2_detail = ""
try:
    from coco.perception.face_id import FaceIDClassifier  # noqa: E402
    clf = FaceIDClassifier()
    clf.set_enabled(False)
    fake_crop = np.zeros((80, 80), dtype=np.uint8)
    result = clf.identify(fake_crop)
    v2_ok = (
        result == (None, 0.0)
        and clf.enabled is False
    )
    v2_detail = f"result={result} enabled={clf.enabled}"
except Exception as e:  # noqa: BLE001
    v2_detail = f"err: {type(e).__name__}: {e}"
step("V2 identify enabled=False → (None, 0.0) early return", v2_ok, v2_detail)


# V3 identify enabled=True → 走旧路径（store 空 → (None, 0.0)）
v3_ok = False
v3_detail = ""
try:
    from coco.perception.face_id import FaceIDClassifier  # noqa: E402
    clf = FaceIDClassifier()
    # 默认 enabled=True，store 默认空（未 enroll）
    fake_crop = np.zeros((80, 80), dtype=np.uint8)
    result = clf.identify(fake_crop)
    # store 空时旧逻辑返 (None, 0.0)，跟 enabled=False 行为巧合一致
    # 关键是: enabled=True 且 self.store.all_records() 走判断
    v3_ok = (
        clf.enabled is True
        and result == (None, 0.0)
    )
    v3_detail = f"enabled={clf.enabled} result={result}"
except Exception as e:  # noqa: BLE001
    v3_detail = f"err: {type(e).__name__}: {e}"
step("V3 identify enabled=True 走旧路径", v3_ok, v3_detail)


# V4 wake_word.mute/unmute/_muted regression
v4_ok = False
v4_detail = ""
try:
    from coco.wake_word import WakeWordDetector  # noqa: E402
    has_mute = callable(getattr(WakeWordDetector, "mute", None))
    has_unmute = callable(getattr(WakeWordDetector, "unmute", None))
    has_is_muted = callable(getattr(WakeWordDetector, "is_muted", None))
    has_feed = callable(getattr(WakeWordDetector, "feed", None))
    v4_ok = has_mute and has_unmute and has_is_muted and has_feed
    v4_detail = (
        f"mute={has_mute} unmute={has_unmute} is_muted={has_is_muted} feed={has_feed}"
    )
except Exception as e:  # noqa: BLE001
    v4_detail = f"err: {type(e).__name__}: {e}"
step("V4 wake_word.WakeWordDetector mute/unmute/is_muted/feed 仍存在", v4_ok, v4_detail)


# V5 _maybe_reload_face_id_runtime + _maybe_reload_wake_runtime 节流 + 切状态
v5_ok = False
v5_detail = ""
try:
    import coco.perception.face_id as _face_mod  # noqa: E402
    import coco.wake_word as _wake_mod  # noqa: E402
    from coco.perception.face_id import FaceIDClassifier  # noqa: E402

    has_face_helper = callable(
        getattr(_face_mod, "_maybe_reload_face_id_runtime", None)
    )
    has_wake_helper = callable(
        getattr(_wake_mod, "_maybe_reload_wake_runtime", None)
    )

    # 节流测试: face_id
    _orig_face_path = _face_mod._RUNTIME_CONFIG_PATH
    _orig_face_interval = _face_mod._FACE_ID_RELOAD_INTERVAL_S
    _face_mod._RUNTIME_CONFIG_PATH = str(_TMP_CFG)
    _face_mod._FACE_ID_RELOAD_INTERVAL_S = 60.0
    _face_mod._last_face_id_check_ts = 0.0
    face_throttle_ok = False
    try:
        _TMP_CFG.write_text(json.dumps({"face_id_enabled": False}))
        clf = FaceIDClassifier()
        assert clf.enabled is True
        _face_mod._maybe_reload_face_id_runtime(clf)
        # 第一次 → 切 False
        first_state = clf.enabled
        # 第二次节流，即使改 config 也不应触发
        _TMP_CFG.write_text(json.dumps({"face_id_enabled": True}))
        _face_mod._maybe_reload_face_id_runtime(clf)
        second_state = clf.enabled
        # 重置节流，再 check → 切回 True
        _face_mod._last_face_id_check_ts = 0.0
        _face_mod._maybe_reload_face_id_runtime(clf)
        third_state = clf.enabled
        face_throttle_ok = (
            first_state is False
            and second_state is False  # 节流
            and third_state is True
        )
    finally:
        _face_mod._RUNTIME_CONFIG_PATH = _orig_face_path
        _face_mod._FACE_ID_RELOAD_INTERVAL_S = _orig_face_interval
        _face_mod._last_face_id_check_ts = 0.0
        if _TMP_CFG.exists():
            _TMP_CFG.unlink()

    # 节流测试: wake (用 _muted 状态直接测，跳过 KWS 实例化)
    class _FakeDetector:
        def __init__(self) -> None:
            self._muted = False

        def mute(self) -> None:
            self._muted = True

        def unmute(self) -> None:
            self._muted = False

    _orig_wake_path = _wake_mod._RUNTIME_CONFIG_PATH
    _orig_wake_interval = _wake_mod._WAKE_RELOAD_INTERVAL_S
    _wake_mod._RUNTIME_CONFIG_PATH = str(_TMP_CFG)
    _wake_mod._WAKE_RELOAD_INTERVAL_S = 60.0
    _wake_mod._last_wake_check_ts = 0.0
    wake_throttle_ok = False
    try:
        _TMP_CFG.write_text(json.dumps({"wake_enabled": False}))
        det = _FakeDetector()
        _wake_mod._maybe_reload_wake_runtime(det)
        first_muted = det._muted
        # 节流：改 config 不应生效
        _TMP_CFG.write_text(json.dumps({"wake_enabled": True}))
        _wake_mod._maybe_reload_wake_runtime(det)
        second_muted = det._muted
        # 重置节流，再 check → unmute
        _wake_mod._last_wake_check_ts = 0.0
        _wake_mod._maybe_reload_wake_runtime(det)
        third_muted = det._muted
        wake_throttle_ok = (
            first_muted is True  # wake_enabled=False → mute
            and second_muted is True  # 节流
            and third_muted is False  # wake_enabled=True → unmute
        )
    finally:
        _wake_mod._RUNTIME_CONFIG_PATH = _orig_wake_path
        _wake_mod._WAKE_RELOAD_INTERVAL_S = _orig_wake_interval
        _wake_mod._last_wake_check_ts = 0.0
        if _TMP_CFG.exists():
            _TMP_CFG.unlink()

    v5_ok = (
        has_face_helper
        and has_wake_helper
        and face_throttle_ok
        and wake_throttle_ok
    )
    v5_detail = (
        f"face_helper={has_face_helper} wake_helper={has_wake_helper} "
        f"face_throttle_ok={face_throttle_ok} wake_throttle_ok={wake_throttle_ok}"
    )
except Exception as e:  # noqa: BLE001
    v5_detail = f"err: {type(e).__name__}: {e}"
step("V5 hot-reload helpers 节流 + 切状态", v5_ok, v5_detail)


# V6 POST /api/config/face_id_enabled {"enabled": false} → 200 + 写文件
v6_ok = False
v6_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a, _RUNTIME_CONFIG_PATH as _cfg
    if _cfg.exists():
        _cfg.unlink()
    client = TestClient(_a)
    r = client.post("/api/config/face_id_enabled", json={"enabled": False})
    body = (
        r.json()
        if r.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    wrote = _cfg.exists()
    disk = json.loads(_cfg.read_text()) if wrote else {}
    v6_ok = (
        r.status_code == 200
        and body.get("status") == "saved"
        and body.get("key") == "face_id_enabled"
        and body.get("enabled") is False
        and wrote
        and disk.get("face_id_enabled") is False
    )
    v6_detail = f"status={r.status_code} body={body} disk={disk}"
    if _cfg.exists():
        _cfg.unlink()
except Exception as e:  # noqa: BLE001
    v6_detail = f"err: {type(e).__name__}: {e}"
step("V6 POST /api/config/face_id_enabled {false} 200 + writes", v6_ok, v6_detail)


# V7 POST /api/config/wake_enabled {"enabled": true} → 200 + 写文件
v7_ok = False
v7_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a, _RUNTIME_CONFIG_PATH as _cfg
    if _cfg.exists():
        _cfg.unlink()
    client = TestClient(_a)
    r = client.post("/api/config/wake_enabled", json={"enabled": True})
    body = (
        r.json()
        if r.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    wrote = _cfg.exists()
    disk = json.loads(_cfg.read_text()) if wrote else {}
    v7_ok = (
        r.status_code == 200
        and body.get("status") == "saved"
        and body.get("key") == "wake_enabled"
        and body.get("enabled") is True
        and wrote
        and disk.get("wake_enabled") is True
    )
    v7_detail = f"status={r.status_code} body={body} disk={disk}"
    if _cfg.exists():
        _cfg.unlink()
except Exception as e:  # noqa: BLE001
    v7_detail = f"err: {type(e).__name__}: {e}"
step("V7 POST /api/config/wake_enabled {true} 200 + writes", v7_ok, v7_detail)


# V8 GET /api/config/face_id_enabled / wake_enabled
v8_ok = False
v8_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a, _RUNTIME_CONFIG_PATH as _cfg
    if _cfg.exists():
        _cfg.unlink()
    client = TestClient(_a)
    # 8a 无 config 文件 → 默认 enabled=true
    r1 = client.get("/api/config/face_id_enabled")
    b1 = (
        r1.json()
        if r1.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    r2 = client.get("/api/config/wake_enabled")
    b2 = (
        r2.json()
        if r2.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    # 8b POST 后读回应一致
    client.post("/api/config/face_id_enabled", json={"enabled": False})
    client.post("/api/config/wake_enabled", json={"enabled": False})
    r3 = client.get("/api/config/face_id_enabled")
    b3 = (
        r3.json()
        if r3.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    r4 = client.get("/api/config/wake_enabled")
    b4 = (
        r4.json()
        if r4.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    # 8c unknown key → 404
    r5 = client.get("/api/config/unknown_xxx")
    v8_ok = (
        r1.status_code == 200
        and b1 == {"key": "face_id_enabled", "enabled": True}
        and r2.status_code == 200
        and b2 == {"key": "wake_enabled", "enabled": True}
        and r3.status_code == 200
        and b3 == {"key": "face_id_enabled", "enabled": False}
        and r4.status_code == 200
        and b4 == {"key": "wake_enabled", "enabled": False}
        and r5.status_code == 404
    )
    v8_detail = (
        f"b1={b1} b2={b2} b3={b3} b4={b4} unknown_status={r5.status_code}"
    )
    if _cfg.exists():
        _cfg.unlink()
except Exception as e:  # noqa: BLE001
    v8_detail = f"err: {type(e).__name__}: {e}"
step("V8 GET /api/config/{face_id,wake}_enabled defaults+readback+404", v8_ok, v8_detail)


# V9 HTML 含 face-id-toggle + wake-toggle + changePerception + loadPerception
v9_ok = False
v9_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    r = client.get("/")
    html = r.text
    has_face_toggle = 'id="face-id-toggle"' in html
    has_wake_toggle = 'id="wake-toggle"' in html
    has_change_fn = "function changePerception(" in html
    has_load_fn = "function loadPerception(" in html
    has_panel = 'id="perception-panel"' in html
    has_call = "loadPerception();" in html
    v9_ok = (
        r.status_code == 200
        and has_face_toggle
        and has_wake_toggle
        and has_change_fn
        and has_load_fn
        and has_panel
        and has_call
    )
    v9_detail = (
        f"face_toggle={has_face_toggle} wake_toggle={has_wake_toggle} "
        f"changePerception={has_change_fn} loadPerception={has_load_fn} "
        f"panel={has_panel} call={has_call}"
    )
except Exception as e:  # noqa: BLE001
    v9_detail = f"err: {type(e).__name__}: {e}"
step("V9 HTML face-id-toggle+wake-toggle+changePerception+loadPerception", v9_ok, v9_detail)


# V10 regression: dashboard-005 LLM model 端点仍正常（generic 路由不吞掉）
v10_ok = False
v10_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a, _RUNTIME_CONFIG_PATH as _cfg
    if _cfg.exists():
        _cfg.unlink()
    client = TestClient(_a)
    # GET 空 config → {"model": ""}
    r1 = client.get("/api/config/llm_model")
    b1 = (
        r1.json()
        if r1.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    # POST 合法 model → 200 + status=saved
    r2 = client.post("/api/config/llm_model", json={"model": "gpt-4o"})
    b2 = (
        r2.json()
        if r2.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    # POST 非法 model → 400
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
step("V10 regression: llm_model 端点仍 OK (generic 路由不吞)", v10_ok, v10_detail)


passed = sum(1 for r in results if r["status"] == "PASS")
total = len(results)
all_pass = passed == total
print(f"\n=== dashboard-007 verify: {passed}/{total} PASS ===")

SUMMARY.write_text(json.dumps({
    "feature": "dashboard-007-face-id-wake-toggle",
    "passed": passed,
    "total": total,
    "results": results,
    "ts": time.time(),
}, ensure_ascii=False, indent=2))

# 清临时目录
try:
    import shutil
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
except Exception:
    pass

sys.exit(0 if all_pass else 1)
