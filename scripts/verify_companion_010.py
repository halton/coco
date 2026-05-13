"""companion-010 verification: EmotionMemoryWindow + EmotionAlertCoordinator.

跑法::

    uv run python scripts/verify_companion_010.py

子项 V1-V10：

V1   deque maxlen=20 维护（超出旧条目被丢）
V2   ratio 计算正确 + sad 比例 ≥ 0.6 触发 alert
V3   cooldown 内不重复触发 alert（cooldown_skipped 递增）
V4   alert 写入 ProfilePersist.emotion_alerts（append + cap）
V5   ProactiveScheduler 命中安慰话题（set_topic_preferences 注入 + select_topic_seed）
V6   default-OFF：未设 COCO_EMO_MEMORY 时 emotion_memory_enabled_from_env=False
V7   schema 兼容：旧 v1 profile JSON 无 emotion_alerts 字段时 load 不报错（=[])
V8   stats 正确（samples_total / alerts_triggered / alerts_per_kind / cooldown_skipped）
V9   与 companion-007 prosody / companion-009 prefer / vision-007 mm fusion 共存
V10  start/stop 干净（listener 绑定 + 解绑 + prefer 还原）

retval：0 全 PASS；1 任一失败
evidence 落 evidence/companion-010/verify_summary.json
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_companion_010] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": ok, "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}")


class FakeClock:
    def __init__(self, t0: float = 1_700_000_000.0) -> None:
        self.t = float(t0)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += float(dt)


# ---------------------------------------------------------------------------
# V1 deque maxlen
# ---------------------------------------------------------------------------


def v1_deque_maxlen():
    from coco.companion.emotion_memory import EmotionMemoryWindow
    w = EmotionMemoryWindow(window_size=5, min_samples_k=1, ratio_threshold=0.6,
                            alert_cooldown_s=60.0, clock=FakeClock(1000.0))
    for i in range(7):
        w.on_emotion("happy", score=0.5, ts=1000.0 + i)
    snap = w.snapshot()
    ok = len(snap) == 5 and snap[0].ts == 1002.0 and snap[-1].ts == 1006.0
    _record("V1 deque maxlen 超出旧条目被丢",
            ok, f"len={len(snap)} first.ts={snap[0].ts} last.ts={snap[-1].ts}")


# ---------------------------------------------------------------------------
# V2 ratio + sad trigger
# ---------------------------------------------------------------------------


def v2_ratio_and_trigger():
    from coco.companion.emotion_memory import EmotionMemoryWindow
    clock = FakeClock(2000.0)
    w = EmotionMemoryWindow(window_size=20, min_samples_k=10, ratio_threshold=0.6,
                            alert_cooldown_s=60.0, clock=clock)
    # 10 条 sad + 5 条 happy → ratio sad = 10/15 ≈ 0.666 ≥ 0.6
    for _ in range(10):
        clock.advance(1.0)
        w.on_emotion("sad", score=0.8, ts=clock.t)
    for _ in range(5):
        clock.advance(1.0)
        w.on_emotion("happy", score=0.5, ts=clock.t)
    r_sad = w.ratio("sad")
    r_happy = w.ratio("happy")
    fire, kind, ratio_at = w.should_alert(now=clock.t)
    ok = (
        abs(r_sad - 10 / 15) < 1e-6
        and abs(r_happy - 5 / 15) < 1e-6
        and fire is True
        and kind == "persistent_sad"
        and ratio_at >= 0.6
    )
    _record("V2 ratio 正确 + sad ratio ≥ 0.6 触发 alert",
            ok, f"r_sad={r_sad:.3f} r_happy={r_happy:.3f} fire={fire} kind={kind}")


# ---------------------------------------------------------------------------
# V3 cooldown 抑制重复
# ---------------------------------------------------------------------------


def v3_cooldown():
    from coco.companion.emotion_memory import EmotionMemoryWindow
    clock = FakeClock(3000.0)
    w = EmotionMemoryWindow(window_size=10, min_samples_k=5, ratio_threshold=0.6,
                            alert_cooldown_s=100.0, clock=clock)
    for _ in range(8):
        clock.advance(1.0)
        w.on_emotion("sad", ts=clock.t)
    fire1, kind1, _r = w.should_alert(now=clock.t)
    w.record_alert(kind=kind1, now=clock.t)
    # 还在 cooldown 内 → 不触发
    clock.advance(10.0)
    fire2, _, _ = w.should_alert(now=clock.t)
    # 跨过 cooldown → 可再触发
    clock.advance(120.0)
    fire3, _, _ = w.should_alert(now=clock.t)
    ok = fire1 is True and fire2 is False and fire3 is True and w.stats.cooldown_skipped >= 1
    _record("V3 cooldown 抑制重复 + 时间到再次触发",
            ok, f"fire1={fire1} fire2={fire2} fire3={fire3} skipped={w.stats.cooldown_skipped}")


# ---------------------------------------------------------------------------
# V4 alert 写入 ProfilePersist
# ---------------------------------------------------------------------------


def v4_persist_alert():
    from coco.companion.emotion_memory import (
        EmotionMemoryWindow, EmotionAlertCoordinator,
    )
    from coco.companion.profile_persist import (
        PersistentProfileStore, PersistedProfile, compute_profile_id,
    )
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    clock = FakeClock(4000.0)
    with tempfile.TemporaryDirectory() as td:
        store = PersistentProfileStore(root=Path(td) / "profiles")
        pid = compute_profile_id("alice", "alice")
        store.save(PersistedProfile(profile_id=pid, nickname="alice"))

        w = EmotionMemoryWindow(window_size=10, min_samples_k=5, ratio_threshold=0.6,
                                alert_cooldown_s=10.0, clock=clock)
        ps = ProactiveScheduler(config=ProactiveConfig(enabled=True), clock=clock)

        def _provider(_store=store, _pid=pid):
            return (_store, _pid)

        coord = EmotionAlertCoordinator(
            w, proactive_scheduler=ps,
            profile_store_provider=_provider, clock=clock,
            prefer_duration_s=60.0,
        )

        # 直接调 on_emotion 触发（不必绑 tracker，listener 仅控开关）
        for _ in range(8):
            clock.advance(1.0)
            coord.on_emotion("sad", score=0.8, ts=clock.t)

        # 触发了至少 1 次 alert，写盘 emotion_alerts
        loaded = store.load(pid)
        ok_load = (
            loaded is not None
            and isinstance(loaded.emotion_alerts, list)
            and len(loaded.emotion_alerts) >= 1
            and loaded.emotion_alerts[-1]["kind"] == "persistent_sad"
            and 0.6 <= loaded.emotion_alerts[-1]["ratio"] <= 1.0
        )
        ok_ps = ps.stats.emotion_alert_triggered >= 1
        ok_emit = w.stats.alerts_triggered >= 1
        _record(
            "V4 alert 写入 ProfilePersist.emotion_alerts + ProactiveScheduler 记账",
            ok_load and ok_ps and ok_emit,
            f"alerts={loaded.emotion_alerts if loaded else None} "
            f"ps.alert_triggered={ps.stats.emotion_alert_triggered} "
            f"window.alerts={w.stats.alerts_triggered}",
        )


# ---------------------------------------------------------------------------
# V5 prefer 注入 + 选 topic + 到期还原
# ---------------------------------------------------------------------------


def v5_prefer_bump_and_restore():
    from coco.companion.emotion_memory import (
        EmotionMemoryWindow, EmotionAlertCoordinator, DEFAULT_COMFORT_PREFER,
    )
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    clock = FakeClock(5000.0)
    w = EmotionMemoryWindow(window_size=10, min_samples_k=5, ratio_threshold=0.6,
                            alert_cooldown_s=10.0, clock=clock)
    ps = ProactiveScheduler(config=ProactiveConfig(enabled=True), clock=clock)
    # 先注入用户原 prefer（模拟 companion-009 学到的偏好）
    ps.set_topic_preferences({"做菜": 0.5})

    coord = EmotionAlertCoordinator(
        w, proactive_scheduler=ps,
        clock=clock, prefer_duration_s=60.0,
    )

    for _ in range(8):
        clock.advance(1.0)
        coord.on_emotion("sad", ts=clock.t)

    bumped = ps.get_topic_preferences()
    # 安慰类 keyword 应已注入；用户原 prefer 仍保留
    ok_bumped = (
        "安慰" in bumped and bumped["安慰"] >= 1.0
        and bumped.get("做菜", 0.0) == 0.5
    )

    # 选 topic：含"安慰"的 candidate 应胜出
    pick = ps.select_topic_seed(["A 聊聊跑步", "B 安慰你一下"])
    ok_pick = "安慰" in pick

    # 推进时钟跨过 prefer_duration → tick 应还原
    clock.advance(70.0)
    coord.tick(now=clock.t)
    restored = ps.get_topic_preferences()
    ok_restored = (
        "安慰" not in restored
        and restored.get("做菜", 0.0) == 0.5
    )
    _record(
        "V5 prefer bump + select_topic_seed 选安慰 + 到期还原",
        ok_bumped and ok_pick and ok_restored,
        f"bumped_keys={sorted(bumped.keys())} pick={pick!r} "
        f"restored_keys={sorted(restored.keys())} prefer_bumps={coord.stats.prefer_bumps} "
        f"prefer_restores={coord.stats.prefer_restores}",
    )


# ---------------------------------------------------------------------------
# V6 default-OFF
# ---------------------------------------------------------------------------


def v6_default_off():
    from coco.companion.emotion_memory import emotion_memory_enabled_from_env
    e0 = dict(os.environ)
    try:
        os.environ.pop("COCO_EMO_MEMORY", None)
        off = emotion_memory_enabled_from_env()
        os.environ["COCO_EMO_MEMORY"] = "1"
        on = emotion_memory_enabled_from_env()
        os.environ["COCO_EMO_MEMORY"] = "0"
        off2 = emotion_memory_enabled_from_env()
        ok = (off is False) and (on is True) and (off2 is False)
        _record("V6 default-OFF env semantics", ok, f"unset={off} =1={on} =0={off2}")
    finally:
        os.environ.clear()
        os.environ.update(e0)


# ---------------------------------------------------------------------------
# V7 schema compat — 旧 v1 profile 无 emotion_alerts 字段
# ---------------------------------------------------------------------------


def v7_schema_compat():
    from coco.companion.profile_persist import (
        PersistentProfileStore, compute_profile_id,
    )
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td) / "profiles"
        td_p.mkdir(parents=True, exist_ok=True)
        pid = compute_profile_id("alice", "alice")
        old_doc = {
            "profile_id": pid,
            "nickname": "alice",
            "interests": ["看书"],
            "goals": [],
            "created_ts": 1700000000.0,
            "updated_ts": 1700000000.0,
            "dialog_summary": [],
            "schema_version": 1,
        }
        (td_p / f"{pid}.json").write_text(json.dumps(old_doc), encoding="utf-8")
        store = PersistentProfileStore(root=td_p)
        rec = store.load(pid)
        ok_loaded = rec is not None and rec.emotion_alerts == []
        d = rec.to_dict() if rec else {}
        ok_nofield = "emotion_alerts" not in d
        _record(
            "V7 旧 v1 schema 兼容（无 emotion_alerts 字段 + 默认 empty）",
            bool(rec) and ok_loaded and ok_nofield,
            f"loaded={bool(rec)} emotion_alerts={rec.emotion_alerts if rec else None} "
            f"to_dict_has_field={'emotion_alerts' in d}",
        )


# ---------------------------------------------------------------------------
# V8 stats 正确
# ---------------------------------------------------------------------------


def v8_stats():
    from coco.companion.emotion_memory import (
        EmotionMemoryWindow, EmotionAlertCoordinator,
    )
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    clock = FakeClock(8000.0)
    w = EmotionMemoryWindow(window_size=10, min_samples_k=5, ratio_threshold=0.6,
                            alert_cooldown_s=50.0, clock=clock)
    ps = ProactiveScheduler(config=ProactiveConfig(enabled=True), clock=clock)
    coord = EmotionAlertCoordinator(w, proactive_scheduler=ps, clock=clock,
                                    prefer_duration_s=10.0)
    # 6 条 sad 触发 1 次 alert
    for _ in range(6):
        clock.advance(1.0)
        coord.on_emotion("sad", ts=clock.t)
    # cooldown 内再发 4 条 sad，应被压制
    for _ in range(4):
        clock.advance(1.0)
        coord.on_emotion("sad", ts=clock.t)
    ok = (
        w.stats.samples_total == 10
        and w.stats.alerts_triggered == 1
        and w.stats.alerts_per_kind.get("persistent_sad") == 1
        and w.stats.cooldown_skipped >= 1
    )
    _record("V8 stats samples/alerts/per_kind/cooldown_skipped",
            ok, f"samples={w.stats.samples_total} alerts={w.stats.alerts_triggered} "
                f"per_kind={w.stats.alerts_per_kind} skipped={w.stats.cooldown_skipped}")


# ---------------------------------------------------------------------------
# V9 coexist with companion-007 / -009 / vision-007 / interact-011
# ---------------------------------------------------------------------------


def v9_coexist():
    from coco.companion.emotion_memory import (
        EmotionMemoryWindow, EmotionAlertCoordinator,
    )
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    clock = FakeClock(9000.0)
    ps = ProactiveScheduler(config=ProactiveConfig(enabled=True), clock=clock)
    # 模拟 companion-009 已注入 prefer
    ps.set_topic_preferences({"做菜": 1.0})
    # 模拟 vision-006/-007/interact-011 各自的 trigger 记账
    ps.record_caption_trigger("画面变暗")
    ps.record_multimodal_trigger("dark_silence", "")
    ps.pause("offline")
    ps.resume("offline")

    w = EmotionMemoryWindow(window_size=10, min_samples_k=5, ratio_threshold=0.6,
                            alert_cooldown_s=20.0, clock=clock)
    coord = EmotionAlertCoordinator(w, proactive_scheduler=ps, clock=clock,
                                    prefer_duration_s=5.0)
    for _ in range(6):
        clock.advance(1.0)
        coord.on_emotion("sad", ts=clock.t)
    # alert bump 后 prefer 含安慰 + 做菜
    after = ps.get_topic_preferences()
    ok_bump = "安慰" in after and after.get("做菜", 0.0) == 1.0
    # 还原后 → 仅"做菜"
    clock.advance(10.0)
    coord.tick(now=clock.t)
    after2 = ps.get_topic_preferences()
    ok_restored = after2 == {"做菜": 1.0}
    # 其他子系统 stats 不被破坏
    ok_others = (
        ps.stats.caption_proactive == 1
        and ps.stats.mm_triggered == 1
        and ps.stats.emotion_alert_triggered == 1
        and ps.is_paused() is False
    )
    _record(
        "V9 共存 companion-007/-009 + vision-006/-007 + interact-011",
        ok_bump and ok_restored and ok_others,
        f"after_keys={sorted(after.keys())} after2={after2} "
        f"caption={ps.stats.caption_proactive} mm={ps.stats.mm_triggered} "
        f"alert={ps.stats.emotion_alert_triggered}",
    )


# ---------------------------------------------------------------------------
# V10 start/stop clean — listener bind + unbind + restore on stop
# ---------------------------------------------------------------------------


def v10_start_stop():
    from coco.companion.emotion_memory import (
        EmotionMemoryWindow, EmotionAlertCoordinator,
    )
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    from coco.emotion import EmotionTracker, EmotionLabel, Emotion
    clock = FakeClock(10000.0)
    tracker = EmotionTracker(decay_s=60.0, clock=clock)
    ps = ProactiveScheduler(config=ProactiveConfig(enabled=True), clock=clock)
    ps.set_topic_preferences({"做菜": 0.5})

    w = EmotionMemoryWindow(window_size=10, min_samples_k=5, ratio_threshold=0.6,
                            alert_cooldown_s=10.0, clock=clock)
    coord = EmotionAlertCoordinator(w, proactive_scheduler=ps, clock=clock,
                                    prefer_duration_s=120.0)
    coord.start(tracker)
    ok_bound = coord.stats.listener_bound is True

    # tracker.record 触发 listener → 进窗
    for _ in range(6):
        clock.advance(1.0)
        tracker.record(EmotionLabel(Emotion.SAD, 0.8, ["难过"]), now=clock.t)

    ok_window = w.stats.samples_total == 6
    ok_alert = ps.stats.emotion_alert_triggered >= 1
    # prefer 已 bump（含安慰）
    bumped = ps.get_topic_preferences()
    ok_bumped = "安慰" in bumped

    # stop → unbind + restore prefer
    coord.stop()
    # stop 后 tracker.record 不再进窗
    pre = w.stats.samples_total
    tracker.record(EmotionLabel(Emotion.SAD, 0.8, ["难过"]), now=clock.t + 5)
    ok_unbound = (
        w.stats.samples_total == pre
        and coord.stats.listener_bound is False
    )
    restored = ps.get_topic_preferences()
    ok_restored = restored == {"做菜": 0.5}
    _record(
        "V10 start/stop clean（listener bind/unbind + prefer 还原）",
        ok_bound and ok_window and ok_alert and ok_bumped and ok_unbound and ok_restored,
        f"bound={ok_bound} window={w.stats.samples_total} alert={ps.stats.emotion_alert_triggered} "
        f"bumped={'安慰' in bumped} unbound_ok={ok_unbound} restored={restored}",
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    cases = [
        v1_deque_maxlen,
        v2_ratio_and_trigger,
        v3_cooldown,
        v4_persist_alert,
        v5_prefer_bump_and_restore,
        v6_default_off,
        v7_schema_compat,
        v8_stats,
        v9_coexist,
        v10_start_stop,
    ]
    for fn in cases:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            _record(fn.__name__, False,
                    f"raised {type(e).__name__}: {e} | tb={traceback.format_exc()[:600]}")

    ok_all = all(r["ok"] for r in _results)
    summary = {
        "feature": "companion-010",
        "ok": ok_all,
        "results": _results,
    }
    out_dir = ROOT / "evidence" / "companion-010"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print(
        f"[verify_companion_010] {'ALL PASS' if ok_all else 'FAILED'} "
        f"{sum(1 for r in _results if r['ok'])}/{len(_results)}"
    )
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
