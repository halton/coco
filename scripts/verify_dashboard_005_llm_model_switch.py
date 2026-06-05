"""dashboard-005 LLM model 切换 + hot-reload — verification (V0-V7).

跑法:  /Users/halton/work/coco/.venv/bin/python scripts/verify_dashboard_005_llm_model_switch.py

V0  hash lock — coco/dashboard/app.py + coco/llm.py 存在
V1  imports OK + coco.llm._load_runtime_config / _maybe_reload_model 模块级 symbol
V2  _load_runtime_config() 解析正确 JSON / 文件不存在返回 {} / 损坏 JSON 返回 {}
V3  _maybe_reload_model() 节流: 同一次窗口内连调两次仅第一次触发 reload
V4  GET /api/config/llm_model 无 config 文件时返回 {"model": ""}
V5  POST /api/config/llm_model {"model":"gpt-4o"} -> 200 + 写文件 + GET 回读一致
V6  POST 非法 model -> 400
V7  HTML 含 6 个 option + changeModel() + loadModel() + id=llm-model + id=llm-panel
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "evidence" / "dashboard-005-llm-model-switch"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY = EVIDENCE_DIR / "verify_summary.json"

APP_PY = ROOT / "coco" / "dashboard" / "app.py"
LLM_PY = ROOT / "coco" / "llm.py"

# 把 runtime_config 指到临时目录，避免污染用户真 cache
_TMP_DIR = Path(tempfile.mkdtemp(prefix="coco_d005_"))
_TMP_CFG = _TMP_DIR / "runtime_config.json"
os.environ["COCO_RUNTIME_CONFIG_PATH"] = str(_TMP_CFG)

results = []


def step(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name} :: {detail}")
    results.append({"name": name, "status": status, "detail": detail})


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# V0
v0_ok = APP_PY.is_file() and LLM_PY.is_file()
v0_detail = ""
if v0_ok:
    v0_detail = f"app.sha={_sha(APP_PY)[:12]} llm.sha={_sha(LLM_PY)[:12]}"
step("V0 hash lock app+llm exist", v0_ok, v0_detail)

# V1 imports + module-level symbols
v1_ok = False
v1_detail = ""
try:
    import coco.llm as _llm  # noqa
    from coco.dashboard.app import app as _a  # noqa
    has_load = callable(getattr(_llm, "_load_runtime_config", None))
    has_reload = callable(getattr(_llm, "_maybe_reload_model", None))
    v1_ok = has_load and has_reload
    v1_detail = f"_load_runtime_config={has_load} _maybe_reload_model={has_reload}"
except Exception as e:  # noqa: BLE001
    v1_detail = f"import err: {type(e).__name__}: {e}"
step("V1 imports + hot-reload symbols", v1_ok, v1_detail)

# V2 _load_runtime_config 行为
v2_ok = False
v2_detail = ""
try:
    import coco.llm as _llm

    # 重定向 helper 用的全局路径到临时文件
    _orig_path = _llm._RUNTIME_CONFIG_PATH
    _llm._RUNTIME_CONFIG_PATH = str(_TMP_CFG)
    try:
        # 2a 文件不存在 -> {}
        if _TMP_CFG.exists():
            _TMP_CFG.unlink()
        empty = _llm._load_runtime_config()
        # 2b 写正确 JSON
        _TMP_CFG.write_text(json.dumps({"llm_model": "gpt-4o", "other": 1}))
        ok_parse = _llm._load_runtime_config()
        # 2c 损坏 JSON -> {}
        _TMP_CFG.write_text("{not valid json")
        broken = _llm._load_runtime_config()
        v2_ok = (
            empty == {}
            and ok_parse.get("llm_model") == "gpt-4o"
            and ok_parse.get("other") == 1
            and broken == {}
        )
        v2_detail = f"empty={empty} ok_parse={ok_parse} broken={broken}"
    finally:
        _llm._RUNTIME_CONFIG_PATH = _orig_path
        if _TMP_CFG.exists():
            _TMP_CFG.unlink()
except Exception as e:  # noqa: BLE001
    v2_detail = f"err: {type(e).__name__}: {e}"
step("V2 _load_runtime_config (missing/ok/broken)", v2_ok, v2_detail)

# V3 _maybe_reload_model 节流 + 切换语义
v3_ok = False
v3_detail = ""
try:
    import coco.llm as _llm

    _orig_path = _llm._RUNTIME_CONFIG_PATH
    _orig_interval = _llm._LAST_CONFIG_RELOAD_INTERVAL_S
    _llm._RUNTIME_CONFIG_PATH = str(_TMP_CFG)
    _llm._LAST_CONFIG_RELOAD_INTERVAL_S = 60.0  # 大值确保节流必生效
    _llm._last_config_check_ts = 0.0
    try:
        _TMP_CFG.write_text(json.dumps({"llm_model": "claude-opus-4.7"}))

        class _FakeBackend:
            model = "gpt-4o-mini"

        b = _FakeBackend()
        # 第一次：触发 reload
        r1 = _llm._maybe_reload_model(b)
        first_model = b.model
        # 第二次：节流，应当不触发（即使我们再改 config）
        _TMP_CFG.write_text(json.dumps({"llm_model": "gemini-2.5-pro"}))
        r2 = _llm._maybe_reload_model(b)
        # 重置时间戳，强制再 check
        _llm._last_config_check_ts = 0.0
        r3 = _llm._maybe_reload_model(b)
        third_model = b.model
        # 同 model 再 check 应返回 None
        _llm._last_config_check_ts = 0.0
        r4 = _llm._maybe_reload_model(b)

        v3_ok = (
            r1 == "claude-opus-4.7"
            and first_model == "claude-opus-4.7"
            and r2 is None  # 节流
            and r3 == "gemini-2.5-pro"
            and third_model == "gemini-2.5-pro"
            and r4 is None  # 同 model 不重复 reload
        )
        v3_detail = (
            f"r1={r1} r2={r2} r3={r3} r4={r4} "
            f"first={first_model} third={third_model}"
        )
    finally:
        _llm._RUNTIME_CONFIG_PATH = _orig_path
        _llm._LAST_CONFIG_RELOAD_INTERVAL_S = _orig_interval
        _llm._last_config_check_ts = 0.0
        if _TMP_CFG.exists():
            _TMP_CFG.unlink()
except Exception as e:  # noqa: BLE001
    v3_detail = f"err: {type(e).__name__}: {e}"
step("V3 _maybe_reload_model throttle + switch", v3_ok, v3_detail)

# V4 GET /api/config/llm_model 无 config 文件时返回 {"model": ""}
v4_ok = False
v4_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a, _RUNTIME_CONFIG_PATH as _cfg
    # 重置临时 config 路径状态
    if _cfg.exists():
        _cfg.unlink()
    client = TestClient(_a)
    r = client.get("/api/config/llm_model")
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    v4_ok = r.status_code == 200 and body == {"model": ""}
    v4_detail = f"status={r.status_code} body={body} cfg_exists={_cfg.exists()}"
except Exception as e:  # noqa: BLE001
    v4_detail = f"err: {type(e).__name__}: {e}"
step("V4 GET /api/config/llm_model empty when no config", v4_ok, v4_detail)

# V5 POST /api/config/llm_model {"model":"gpt-4o"} -> 200 + 写文件 + GET 回读
v5_ok = False
v5_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a, _RUNTIME_CONFIG_PATH as _cfg
    if _cfg.exists():
        _cfg.unlink()
    client = TestClient(_a)
    r = client.post("/api/config/llm_model", json={"model": "gpt-4o"})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    wrote = _cfg.exists()
    disk = json.loads(_cfg.read_text()) if wrote else {}
    rg = client.get("/api/config/llm_model")
    gbody = rg.json() if rg.headers.get("content-type", "").startswith("application/json") else {}
    v5_ok = (
        r.status_code == 200
        and body.get("status") == "saved"
        and body.get("model") == "gpt-4o"
        and wrote
        and disk.get("llm_model") == "gpt-4o"
        and rg.status_code == 200
        and gbody == {"model": "gpt-4o"}
    )
    v5_detail = (
        f"post_status={r.status_code} post_body={body} wrote={wrote} "
        f"disk={disk} get_body={gbody}"
    )
    # 清理为后续 verify 用例
    if _cfg.exists():
        _cfg.unlink()
except Exception as e:  # noqa: BLE001
    v5_detail = f"err: {type(e).__name__}: {e}"
step("V5 POST /api/config/llm_model writes + GET reads back", v5_ok, v5_detail)

# V6 非法 model -> 400
v6_ok = False
v6_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    r = client.post("/api/config/llm_model", json={"model": "fake-model-xx"})
    v6_ok = r.status_code == 400 and "unknown model" in r.text
    v6_detail = f"status={r.status_code} body={r.text[:200]}"
except Exception as e:  # noqa: BLE001
    v6_detail = f"err: {type(e).__name__}: {e}"
step("V6 POST invalid model -> 400", v6_ok, v6_detail)

# V7 HTML 含 6 个 option + changeModel() + loadModel() + id=llm-model + id=llm-panel
v7_ok = False
v7_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    r = client.get("/")
    html = r.text
    expected_models = [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1",
        "claude-sonnet-4.5",
        "claude-opus-4.7",
        "gemini-2.5-pro",
    ]
    missing = [m for m in expected_models if f'value="{m}"' not in html]
    has_change = "function changeModel(" in html
    has_load = "function loadModel(" in html
    has_panel = 'id="llm-panel"' in html
    has_select = 'id="llm-model"' in html
    has_call = "loadModel();" in html
    v7_ok = (
        r.status_code == 200
        and not missing
        and has_change
        and has_load
        and has_panel
        and has_select
        and has_call
    )
    v7_detail = (
        f"missing={missing} changeModel={has_change} loadModel={has_load} "
        f"panel={has_panel} select={has_select} call={has_call}"
    )
except Exception as e:  # noqa: BLE001
    v7_detail = f"err: {type(e).__name__}: {e}"
step("V7 HTML has 6 options + changeModel/loadModel + ids", v7_ok, v7_detail)


passed = sum(1 for r in results if r["status"] == "PASS")
total = len(results)
all_pass = passed == total
print(f"\n=== dashboard-005 verify: {passed}/{total} PASS ===")

SUMMARY.write_text(json.dumps({
    "feature": "dashboard-005-llm-model-switch",
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
