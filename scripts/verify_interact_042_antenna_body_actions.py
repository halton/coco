"""interact-042 antenna + body_yaw actions — verification (V0-V9).

跑法:
  /Users/halton/work/coco/.venv/bin/python scripts/verify_interact_042_antenna_body_actions.py > /tmp/v042.log 2>&1; rc=$?; tail -40 /tmp/v042.log; echo "rc=$rc"

V0 hash lock — 4 个目标文件存在 + 含 interact-042 marker
V1 import 6 个新动作不抛 (wiggle_antennas/perk_up/droop_antennas/turn_body_left/right/center)
V2 ACTION_TOOL_ENUM 含 6 个新 action (10+6=16)
V3 _do_action 含 6 个新 case 分发 (interact.py 源码 grep)
V4 KEYWORD_ROUTES 含中文关键词路由 (兴奋/好奇/失落 + 转身向左/向右/回正)
V5 dashboard HTML 含 6 个新按钮 (doAction('wiggle_antennas') 等)
V6 dashboard HTML 含 3 个新滑条 (antenna_left/right + body_yaw input range)
V7 POST /api/pose 接 antennas + body_yaw (fake 模式)
V8 actions.py 6 method 调 set_target 含 antennas= / body_yaw= 关键字
V9 clamp: antenna ±1.5 + body_yaw ±π/2 (字面值 1.5 / 1.5707 在 actions.py 与 dashboard/app.py)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "evidence" / "interact-042-antenna-body-actions"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY = EVIDENCE_DIR / "verify_summary.json"

ACTIONS_PY = ROOT / "coco" / "actions.py"
LLM_PY = ROOT / "coco" / "llm.py"
INTERACT_PY = ROOT / "coco" / "interact.py"
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


# ---------------------------------------------------------------------------
# V0 hash lock — 4 文件存在 + interact-042 marker
# ---------------------------------------------------------------------------
v0_ok = True
v0_detail_parts = []
for p in (ACTIONS_PY, LLM_PY, INTERACT_PY, APP_PY):
    if not p.is_file():
        v0_ok = False
        v0_detail_parts.append(f"{p.name}=MISSING")
        continue
    src = p.read_text()
    has_marker = "interact-042" in src
    if not has_marker:
        v0_ok = False
    v0_detail_parts.append(f"{p.name}.sha={_sha(p)[:12]} marker={has_marker}")
step("V0 hash lock 4 files + interact-042 marker", v0_ok, " | ".join(v0_detail_parts))


# ---------------------------------------------------------------------------
# V1 import 6 新动作不抛
# ---------------------------------------------------------------------------
v1_ok = False
v1_detail = ""
try:
    from coco.actions import (
        wiggle_antennas,
        perk_up,
        droop_antennas,
        turn_body_left,
        turn_body_right,
        turn_body_center,
    )
    callables = [wiggle_antennas, perk_up, droop_antennas,
                 turn_body_left, turn_body_right, turn_body_center]
    v1_ok = all(callable(f) for f in callables) and len(callables) == 6
    v1_detail = f"imported 6 callables: {[f.__name__ for f in callables]}"
except Exception as e:  # noqa: BLE001
    v1_detail = f"import err: {type(e).__name__}: {e}"
step("V1 import 6 new actions OK", v1_ok, v1_detail)


# ---------------------------------------------------------------------------
# V2 ACTION_TOOL_ENUM 含 6 个新 action
# ---------------------------------------------------------------------------
v2_ok = False
v2_detail = ""
try:
    from coco.llm import ACTION_TOOL_ENUM
    expected_new = {"wiggle_antennas", "perk_up", "droop_antennas",
                    "turn_body_left", "turn_body_right", "turn_body_center"}
    present = expected_new.issubset(set(ACTION_TOOL_ENUM))
    v2_ok = present and len(ACTION_TOOL_ENUM) == 16
    v2_detail = f"len={len(ACTION_TOOL_ENUM)} (expect 16) new_all_present={present} enum={list(ACTION_TOOL_ENUM)}"
except Exception as e:  # noqa: BLE001
    v2_detail = f"err: {type(e).__name__}: {e}"
step("V2 ACTION_TOOL_ENUM has 6 new actions (10+6=16)", v2_ok, v2_detail)


# ---------------------------------------------------------------------------
# V3 _do_action 含 6 个新 case 分发 (interact.py 源码 grep)
# ---------------------------------------------------------------------------
v3_ok = False
v3_detail = ""
try:
    src = INTERACT_PY.read_text()
    cases = [
        'name == "wiggle_antennas"',
        'name == "perk_up"',
        'name == "droop_antennas"',
        'name == "turn_body_left"',
        'name == "turn_body_right"',
        'name == "turn_body_center"',
    ]
    missing = [c for c in cases if c not in src]
    v3_ok = not missing
    v3_detail = f"missing_cases={missing}"
except Exception as e:  # noqa: BLE001
    v3_detail = f"err: {type(e).__name__}: {e}"
step("V3 _do_action has 6 new case branches", v3_ok, v3_detail)


# ---------------------------------------------------------------------------
# V4 KEYWORD_ROUTES 含中文关键词路由
# ---------------------------------------------------------------------------
v4_ok = False
v4_detail = ""
try:
    src = INTERACT_PY.read_text()
    needles = [
        # antenna 情绪
        ('"摇摆天线"', "wiggle_antennas"),
        ('"天线竖"', "perk_up"),
        ('"天线垂"', "droop_antennas"),
        # body_yaw 转身
        ('"转身向左"', "turn_body_left"),
        ('"转身向右"', "turn_body_right"),
        ('"身体回正"', "turn_body_center"),
    ]
    missing = []
    for kw, action in needles:
        if kw not in src or action not in src:
            missing.append((kw, action))
    v4_ok = not missing
    # 额外语义检查：route 实际可路由
    try:
        from coco.interact import route_reply
        _, a1 = route_reply("我要转身向左看看")
        _, a2 = route_reply("好开心呀")
        _, a3 = route_reply("我有点失落")
        route_ok = (a1 == "turn_body_left"
                    and a2 == "wiggle_antennas"
                    and a3 == "droop_antennas")
        v4_ok = v4_ok and route_ok
        v4_detail = f"missing={missing} routes={a1},{a2},{a3}"
    except Exception as e:  # noqa: BLE001
        v4_ok = False
        v4_detail = f"missing={missing} route_reply err: {type(e).__name__}: {e}"
except Exception as e:  # noqa: BLE001
    v4_detail = f"err: {type(e).__name__}: {e}"
step("V4 KEYWORD_ROUTES 中文关键词 + route_reply 验证", v4_ok, v4_detail)


# ---------------------------------------------------------------------------
# V5 dashboard HTML 含 6 个新按钮
# ---------------------------------------------------------------------------
v5_ok = False
v5_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    r = client.get("/")
    html = r.text
    btns = [
        "doAction('wiggle_antennas')",
        "doAction('perk_up')",
        "doAction('droop_antennas')",
        "doAction('turn_body_left')",
        "doAction('turn_body_right')",
        "doAction('turn_body_center')",
    ]
    missing = [b for b in btns if b not in html]
    v5_ok = r.status_code == 200 and not missing
    v5_detail = f"status={r.status_code} missing_btns={missing}"
except Exception as e:  # noqa: BLE001
    v5_detail = f"err: {type(e).__name__}: {e}"
step("V5 dashboard HTML has 6 new buttons", v5_ok, v5_detail)


# ---------------------------------------------------------------------------
# V6 dashboard HTML 含 3 个新滑条 (antenna_left/right + body_yaw)
# ---------------------------------------------------------------------------
v6_ok = False
v6_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    r = client.get("/")
    html = r.text
    sliders = [
        'id="antenna_left"',
        'id="antenna_right"',
        'id="body_yaw"',
    ]
    missing = [s for s in sliders if s not in html]
    range_count = html.count('type="range"')
    v6_ok = (r.status_code == 200
             and not missing
             and range_count >= 6)  # 3 旧 + 3 新
    v6_detail = f"status={r.status_code} missing={missing} range_inputs={range_count} (expect>=6)"
except Exception as e:  # noqa: BLE001
    v6_detail = f"err: {type(e).__name__}: {e}"
step("V6 dashboard HTML has 3 new sliders (antenna L/R + body_yaw)", v6_ok, v6_detail)


# ---------------------------------------------------------------------------
# V7 POST /api/pose 接 antennas + body_yaw (fake 模式)
# ---------------------------------------------------------------------------
v7_ok = False
v7_detail = ""
try:
    from fastapi.testclient import TestClient
    from coco.dashboard.app import app as _a
    client = TestClient(_a)
    # 正常路径
    r1 = client.post("/api/pose", json={
        "pitch": 0.1, "yaw": 0.0, "roll": 0.0,
        "antennas": [0.5, -0.5],
        "body_yaw": 0.3,
    })
    b1 = r1.json() if r1.headers.get("content-type", "").startswith("application/json") else {}
    cond1 = (r1.status_code == 200
             and b1.get("status") == "ok"
             and b1.get("fake") is True
             and b1.get("antennas") == [0.5, -0.5]
             and abs(b1.get("body_yaw", -999) - 0.3) < 1e-9)
    # clamp 超界 (antenna ±1.5, body_yaw ±π/2)
    r2 = client.post("/api/pose", json={
        "pitch": 0.0, "yaw": 0.0, "roll": 0.0,
        "antennas": [9.9, -9.9],
        "body_yaw": 99.0,
    })
    b2 = r2.json() if r2.headers.get("content-type", "").startswith("application/json") else {}
    cond2 = (r2.status_code == 200
             and b2.get("antennas") == [1.5, -1.5]
             and abs(b2.get("body_yaw", 0) - 1.5707963267948966) < 1e-9)
    # 向后兼容: 不传 antennas/body_yaw 时只有 pitch/yaw/roll
    r3 = client.post("/api/pose", json={"pitch": 0.0, "yaw": 0.0, "roll": 0.0})
    b3 = r3.json() if r3.headers.get("content-type", "").startswith("application/json") else {}
    cond3 = (r3.status_code == 200
             and b3.get("status") == "ok"
             and "antennas" not in b3
             and "body_yaw" not in b3)
    v7_ok = cond1 and cond2 and cond3
    v7_detail = f"cond1={cond1} cond2={cond2} cond3={cond3} b1={b1} b2={b2} b3={b3}"
except Exception as e:  # noqa: BLE001
    v7_detail = f"err: {type(e).__name__}: {e}"
step("V7 POST /api/pose accepts antennas + body_yaw + clamp + back-compat", v7_ok, v7_detail)


# ---------------------------------------------------------------------------
# V8 actions.py 6 method 调 set_target 含 antennas= / body_yaw=
# ---------------------------------------------------------------------------
v8_ok = False
v8_detail = ""
try:
    src = ACTIONS_PY.read_text()
    # 三个 antenna 动作必须用 set_target(antennas=...) 或经局部别名 st(antennas=...)
    # 三个 body 动作同理可用 set_target(body_yaw=...) 或 st(body_yaw=...)
    antenna_count = src.count("set_target(antennas=") + src.count("st(antennas=")
    body_count = src.count("set_target(body_yaw=") + src.count("st(body_yaw=")
    # 同时锁 getattr(robot, "set_target", ...) 抽取别名的存在 (防止有人去掉 set_target 名)
    has_set_target_lookup = 'getattr(robot, "set_target"' in src or "robot.set_target" in src
    # 函数定义必须全部存在
    funcs = [
        "def wiggle_antennas(",
        "def perk_up(",
        "def droop_antennas(",
        "def turn_body_left(",
        "def turn_body_right(",
        "def turn_body_center(",
    ]
    missing_funcs = [f for f in funcs if f not in src]
    v8_ok = (not missing_funcs
             and antenna_count >= 4   # wiggle 2 个分支 + perk + droop = 4 (wiggle 末态归零额外 1)
             and body_count >= 3      # turn_body_left/right/center
             and has_set_target_lookup)
    v8_detail = (f"missing_funcs={missing_funcs} "
                 f"antennas_calls={antenna_count} (expect>=4) "
                 f"body_yaw_calls={body_count} (expect>=3) "
                 f"set_target_lookup={has_set_target_lookup}")
except Exception as e:  # noqa: BLE001
    v8_detail = f"err: {type(e).__name__}: {e}"
step("V8 actions.py uses set_target(antennas=...) + set_target(body_yaw=...)", v8_ok, v8_detail)


# ---------------------------------------------------------------------------
# V9 clamp: antenna ±1.5 + body_yaw ±π/2
# ---------------------------------------------------------------------------
v9_ok = False
v9_detail = ""
try:
    actions_src = ACTIONS_PY.read_text()
    app_src = APP_PY.read_text()
    # actions.py 必须有 clamp 常量
    a_has_antenna_max = "ANTENNA_MAX_RAD" in actions_src and "1.5" in actions_src
    a_has_body_max = "BODY_YAW_MAX_RAD" in actions_src and "math.pi / 2" in actions_src
    a_has_clamp_fn = "_clamp_antenna" in actions_src and "_clamp_body_yaw" in actions_src
    # dashboard 必须有 clamp 常量 + 函数
    d_has_antenna_max = "_ANTENNA_MAX_RAD = 1.5" in app_src
    d_has_body_max = "_BODY_YAW_MAX_RAD" in app_src and "1.5707963267948966" in app_src
    d_has_clamp_fn = "_antenna_clamp" in app_src and "_body_yaw_clamp" in app_src
    # 运行时再验一次（防 typo）
    from coco.actions import (
        _clamp_antenna,
        _clamp_body_yaw,
        ANTENNA_MAX_RAD,
        BODY_YAW_MAX_RAD,
    )
    import math
    runtime_ok = (
        ANTENNA_MAX_RAD == 1.5
        and abs(BODY_YAW_MAX_RAD - math.pi / 2) < 1e-9
        and _clamp_antenna(99) == 1.5
        and _clamp_antenna(-99) == -1.5
        and abs(_clamp_body_yaw(99) - math.pi / 2) < 1e-9
        and abs(_clamp_body_yaw(-99) + math.pi / 2) < 1e-9
    )
    v9_ok = (a_has_antenna_max and a_has_body_max and a_has_clamp_fn
             and d_has_antenna_max and d_has_body_max and d_has_clamp_fn
             and runtime_ok)
    v9_detail = (f"actions:{a_has_antenna_max},{a_has_body_max},{a_has_clamp_fn} "
                 f"dashboard:{d_has_antenna_max},{d_has_body_max},{d_has_clamp_fn} "
                 f"runtime_ok={runtime_ok}")
except Exception as e:  # noqa: BLE001
    v9_detail = f"err: {type(e).__name__}: {e}"
step("V9 clamp: antenna ±1.5 + body_yaw ±π/2 (src + runtime)", v9_ok, v9_detail)


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------
passed = sum(1 for r in results if r["status"] == "PASS")
total = len(results)
all_pass = passed == total
print(f"\n=== interact-042 verify: {passed}/{total} PASS ===")

SUMMARY.write_text(json.dumps({
    "feature": "interact-042-antenna-body-actions",
    "passed": passed,
    "total": total,
    "results": results,
}, ensure_ascii=False, indent=2))

sys.exit(0 if all_pass else 1)
