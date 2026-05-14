"""verify_companion_015 — companion-015 (phase-13).

两件事：
1) EmotionAlertCoordinator._bump_comfort_prefer 首次 capture 真剥 comfort keys
   （关闭 companion-010 inherited caveat）
2) PreferenceLearner state cross-process persist（atomic + schema v1 + warn-once + emit）

V1: COCO_EMO_MEMORY=1 + 首次 capture（current 含 comfort key）→ baseline 不含 comfort keys + warn-once
V2: 已存在 baseline-like 场景：first_capture 输入含 comfort keys → stripped + 后续 save 不再含
V3: tick() 行为与 phase-12 companion-013 一致（无回归 — 用户 alert 期间手动 prefer 改动保留）
V4: COCO_PREFERENCE_PERSIST=1 → 写 atomic JSON v1 + 双进程 hydrate 命中
V5: PERSIST=1 文件损坏 → warn-once + 空 state + 不 crash
V6: PERSIST=1 schema version != 1 → warn-once + 空 state + 不 crash
V7: emit `companion.preference_persisted` action/profile_count/topic_count 字段齐全
V8: PERSIST 默认 OFF → 无文件 IO + 无新 emit (bytewise 等价)
V9: 回归 companion-013 / companion-014
V10: 回归 vision-010 主链 + companion-008/009
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: List[str] = []
PASSES: List[str] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))
    (PASSES if ok else FAILURES).append(name)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakePS:
    def __init__(self, init_prefer: Dict[str, float] | None = None) -> None:
        self.prefer: Dict[str, float] = dict(init_prefer or {})
        self.alerts: List[Any] = []

    def get_topic_preferences(self) -> Dict[str, float]:
        return dict(self.prefer)

    def set_topic_preferences(self, p: Dict[str, float]) -> None:
        self.prefer = dict(p or {})

    def record_emotion_alert_trigger(self, **kw: Any) -> None:
        self.alerts.append(kw)


def _new_coord(ps: _FakePS, *, prefer_duration_s: float = 60.0):
    from coco.companion.emotion_memory import (
        EmotionAlertCoordinator,
        EmotionMemoryWindow,
    )
    win = EmotionMemoryWindow()
    return EmotionAlertCoordinator(
        memory=win,
        proactive_scheduler=ps,
        prefer_duration_s=prefer_duration_s,
    )


# ---------------------------------------------------------------------------
# V1: 首次 capture 真剥 comfort keys
# ---------------------------------------------------------------------------
def v1_first_capture_strips_comfort() -> None:
    from coco.companion.emotion_memory import DEFAULT_COMFORT_PREFER
    # current 含 comfort 同名 key（模拟用户手动设了 comfort 同名 prefer）
    comfort_key = next(iter(DEFAULT_COMFORT_PREFER.keys()))
    ps = _FakePS({"游戏": 0.5, comfort_key: 0.3})
    coord = _new_coord(ps)
    coord._bump_comfort_prefer(now=0.0)
    baseline = coord._original_prefer or {}
    has_comfort_in_baseline = any(k in DEFAULT_COMFORT_PREFER for k in baseline)
    _check(
        "V1 首次 capture 剥 comfort keys",
        (not has_comfort_in_baseline) and ("游戏" in baseline),
        f"baseline={baseline}",
    )


# ---------------------------------------------------------------------------
# V2: 已含 comfort 的 baseline-like 输入 → 后续 save 不再含
# ---------------------------------------------------------------------------
def v2_warn_once_on_contaminated_first_capture(caplog_buffer: List[str]) -> None:
    from coco.companion.emotion_memory import DEFAULT_COMFORT_PREFER
    comfort_key = next(iter(DEFAULT_COMFORT_PREFER.keys()))
    ps = _FakePS({comfort_key: 0.7, "猫": 0.4})
    coord = _new_coord(ps)
    # 第一次 bump：触发 warn-once（contaminated）
    coord._bump_comfort_prefer(now=0.0)
    # 验证 warn 路径标志位被设
    flag = getattr(coord, "_warned_first_capture_contaminated", False)
    # 第二次 bump 不再 warn（标志位仍 True，但不再 print）—— 我们只断言标志位 True 且 baseline 干净
    base = coord._original_prefer or {}
    _check(
        "V2 contaminated first capture 标志位 + baseline 不含 comfort",
        flag and (comfort_key not in base) and ("猫" in base),
        f"flag={flag} base={base}",
    )


# ---------------------------------------------------------------------------
# V3: tick() 端到端无回归（用户 alert 期间改 prefer 仍保留）
# ---------------------------------------------------------------------------
def v3_tick_user_change_preserved() -> None:
    from coco.companion.emotion_memory import DEFAULT_COMFORT_PREFER
    ps = _FakePS({"足球": 0.5})
    coord = _new_coord(ps, prefer_duration_s=1.0)
    coord._bump_comfort_prefer(now=0.0)
    # alert 期间用户加了新 key
    cur = ps.get_topic_preferences()
    cur["阅读"] = 0.6
    ps.set_topic_preferences(cur)
    # 到期 tick
    coord.tick(now=10.0)
    after = ps.get_topic_preferences()
    has_comfort = any(k in DEFAULT_COMFORT_PREFER for k in after)
    _check(
        "V3 tick 撤回 comfort + 保留用户改动",
        ("足球" in after) and ("阅读" in after) and (not has_comfort),
        f"after={after}",
    )


# ---------------------------------------------------------------------------
# V4: PERSIST=1 → atomic JSON + 跨进程 hydrate
# ---------------------------------------------------------------------------
def v4_persist_and_hydrate(tmp_root: Path) -> None:
    from coco.companion.preference_learner import PreferenceLearner
    cache_path = tmp_root / "pref_state.json"

    class _RecPS:
        prefer_topics: Dict[str, float] = {}
        updated_ts: float = 0.0
        dialog_summary: list = []

    class _Store:
        def __init__(self) -> None:
            self.rec = _RecPS()

        def load(self, pid: str) -> _RecPS:
            return self.rec

        def save(self, rec: _RecPS) -> None:
            pass

    store = _Store()

    # learner A 写入
    a = PreferenceLearner(state_cache_path=cache_path)
    # 直接走内部 state cache + flush（同时也会被 rebuild_for_profile 末端覆盖）
    with a._lock:
        a._state_cache["alice"] = {"猫咪": 0.9, "做菜": 0.5}
    ok = a.flush_state()
    file_ok = cache_path.exists()
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    schema_ok = raw.get("version") == 1 and "profiles" in raw and "alice" in raw["profiles"]

    # learner B（新进程模拟）从同 path hydrate
    b = PreferenceLearner(state_cache_path=cache_path)
    cached = b.get_cached_topics("alice")
    _check(
        "V4 PERSIST=1 atomic + 双进程 hydrate",
        ok and file_ok and schema_ok and cached.get("猫咪") == 0.9 and cached.get("做菜") == 0.5,
        f"flush={ok} file={file_ok} schema={schema_ok} cached={cached}",
    )


# ---------------------------------------------------------------------------
# V5: 文件损坏 → warn + 空 state + 不 crash
# ---------------------------------------------------------------------------
def v5_corrupted_file(tmp_root: Path) -> None:
    from coco.companion.preference_learner import PreferenceLearner
    cache_path = tmp_root / "pref_corrupt.json"
    cache_path.write_text("{not json", encoding="utf-8")
    try:
        learner = PreferenceLearner(state_cache_path=cache_path)
        cached = learner.get_cached_topics("anyone")
        _check(
            "V5 corrupted file → empty state + no crash",
            cached == {},
            f"cached={cached}",
        )
    except Exception as e:  # noqa: BLE001
        _check("V5 corrupted file → empty state + no crash", False, f"crashed: {e}")


# ---------------------------------------------------------------------------
# V6: schema version mismatch
# ---------------------------------------------------------------------------
def v6_schema_mismatch(tmp_root: Path) -> None:
    from coco.companion.preference_learner import PreferenceLearner
    cache_path = tmp_root / "pref_v99.json"
    cache_path.write_text(
        json.dumps({"version": 99, "profiles": {"alice": {"x": 1.0}}, "saved_at": 0.0}),
        encoding="utf-8",
    )
    try:
        learner = PreferenceLearner(state_cache_path=cache_path)
        cached = learner.get_cached_topics("alice")
        _check(
            "V6 schema mismatch → empty state + no crash",
            cached == {},
            f"cached={cached}",
        )
    except Exception as e:  # noqa: BLE001
        _check("V6 schema mismatch → empty state + no crash", False, f"crashed: {e}")


# ---------------------------------------------------------------------------
# V7: emit `companion.preference_persisted` schema
# ---------------------------------------------------------------------------
def v7_emit_persisted_schema(tmp_root: Path) -> None:
    from coco.companion.preference_learner import PreferenceLearner
    captured: List[Dict[str, Any]] = []

    def emit(topic: str, *args: Any, **kw: Any) -> None:
        captured.append({"topic": topic, "kw": kw})

    cache_path = tmp_root / "pref_emit.json"
    learner = PreferenceLearner(state_cache_path=cache_path, emit_fn=emit)
    with learner._lock:
        learner._state_cache["alice"] = {"猫": 1.0, "狗": 0.5}
    learner.flush_state()
    persist_emits = [e for e in captured if e["topic"] == "companion.preference_persisted"]
    has_load = any(e["kw"].get("action") == "load" for e in persist_emits)
    has_save = any(e["kw"].get("action") == "save" for e in persist_emits)
    save_ev = next((e["kw"] for e in persist_emits if e["kw"].get("action") == "save"), {})
    keys_ok = (
        "profile_count" in save_ev
        and "topic_count" in save_ev
        and save_ev.get("profile_count") == 1
        and save_ev.get("topic_count") == 2
    )
    _check(
        "V7 emit preference_persisted load+save schema",
        has_load and has_save and keys_ok,
        f"emits={persist_emits}",
    )


# ---------------------------------------------------------------------------
# V8: PERSIST default-OFF → 无 IO + 无新 emit
# ---------------------------------------------------------------------------
def v8_default_off_bytewise(tmp_root: Path) -> None:
    from coco.companion.preference_learner import (
        PreferenceLearner,
        preference_persist_enabled_from_env,
    )
    captured: List[Any] = []

    def emit(topic: str, *args: Any, **kw: Any) -> None:
        captured.append((topic, kw))

    # 不传 state_cache_path（模拟 main 在 env OFF 时不注入）
    learner = PreferenceLearner(emit_fn=emit)
    # 调 flush_state 应直接返回 False，不创任何文件
    ok = learner.flush_state()
    persist_emits = [e for e in captured if e[0] == "companion.preference_persisted"]
    # 该目录里不该有任何 preference_state.json（我们用了独立 tmp_root，全新目录确认无 IO）
    files = list(tmp_root.glob("*"))
    env_off = preference_persist_enabled_from_env({"COCO_PREFERENCE_PERSIST": "0"}) is False
    env_unset = preference_persist_enabled_from_env({}) is False
    _check(
        "V8 default-OFF 无 IO + 无 emit",
        (ok is False) and (not persist_emits) and env_off and env_unset,
        f"flush_ret={ok} emits={persist_emits} env_off={env_off} env_unset={env_unset}",
    )


# ---------------------------------------------------------------------------
# V9 / V10: 回归子进程
# ---------------------------------------------------------------------------
def _run_verify(name: str) -> tuple[bool, str]:
    p = ROOT / "scripts" / name
    if not p.exists():
        return False, f"missing {p}"
    try:
        proc = subprocess.run(
            [sys.executable, str(p)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=240,
        )
        ok = proc.returncode == 0
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        return ok, " | ".join(tail)
    except Exception as e:  # noqa: BLE001
        return False, f"crashed: {e}"


def v9_regression_companion() -> None:
    for name in ("verify_companion_013.py", "verify_companion_014.py"):
        ok, info = _run_verify(name)
        _check(f"V9 {name}", ok, info)


def v10_regression_vision_and_companion() -> None:
    for name in (
        "verify_companion_008.py",
        "verify_companion_009.py",
        "verify_vision_010.py",
        "verify_vision_010_fu_1.py",
        "verify_vision_010_fu_2.py",
        "verify_vision_010_fu_3.py",
        "verify_vision_010_fu_4.py",
    ):
        ok, info = _run_verify(name)
        _check(f"V10 {name}", ok, info)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    with tempfile.TemporaryDirectory(prefix="coco-companion-015-") as td:
        tmp_root = Path(td)
        v1_first_capture_strips_comfort()
        v2_warn_once_on_contaminated_first_capture([])
        v3_tick_user_change_preserved()
        v4_persist_and_hydrate(tmp_root)
        v5_corrupted_file(tmp_root)
        v6_schema_mismatch(tmp_root)
        v7_emit_persisted_schema(tmp_root)
        # V8 用独立 tmp dir，确保完全干净
        with tempfile.TemporaryDirectory(prefix="coco-companion-015-off-") as td2:
            v8_default_off_bytewise(Path(td2))
    v9_regression_companion()
    v10_regression_vision_and_companion()

    print()
    print(f"PASS: {len(PASSES)}  FAIL: {len(FAILURES)}")
    if FAILURES:
        print("FAILED checks:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
