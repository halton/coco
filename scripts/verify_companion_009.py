"""companion-009 verification: PreferenceLearner + ProactiveScheduler.set_topic_preferences.

跑法::

    uv run python scripts/verify_companion_009.py

子项 V1-V10：

V1   启发式 bigram + TopK：高频关键词出现 + 停用词被过滤
V2   时间衰减：旧 turn 权重 < 新 turn 权重（半衰期生效）
V3   prefer_topics 写入 PersistentProfileStore + load 后能读回（含跨"会话"模拟）
V4   ProactiveScheduler.set_topic_preferences 改变 select_topic_seed 选择 + stats 计数
V5   default-OFF：未设 COCO_PREFER_LEARN 时 preference_learn_enabled_from_env=False
V6   schema 兼容：旧 v1 profile JSON 无 prefer_topics 字段时 load 无报错（=={}）
V7   多 profile 切换时 learner 独立更新各 profile（profile_id 不串）
V8   停用词过滤：高频虚词不进 TopK
V9   stats 正确：updated_count / extracted_keywords_total / persist_skipped_count
V10  与 vision-006/-007/interact-011/companion-008 接口共存不互相干扰

retval：0 全 PASS；1 任一失败
evidence 落 evidence/companion-009/verify_summary.json
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
    print(f"[verify_companion_009] {tag} {msg}", flush=True)


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
        self.t += dt


# A tiny DialogMemory stand-in (we don't need full impl)
class FakeDM:
    def __init__(self, turns: List[tuple], summary: str = "") -> None:
        self._turns = list(turns)
        self.summary = summary

    def recent_turns(self) -> List[tuple]:
        return list(self._turns)


def _new_store(tmp: Path):
    from coco.companion.profile_persist import PersistentProfileStore
    return PersistentProfileStore(root=tmp / "profiles")


def _make_rec(pid: str, summary_lines=None):
    from coco.companion.profile_persist import PersistedProfile
    return PersistedProfile(
        profile_id=pid,
        nickname="alice",
        interests=[],
        goals=[],
        dialog_summary=list(summary_lines or []),
    )


# ---------------------------------------------------------------------------
# V1
# ---------------------------------------------------------------------------


def v1_topk_bigram():
    from coco.companion.preference_learner import PreferenceLearner, TurnEntry
    learner = PreferenceLearner(topk=5, half_life_s=10_000.0, persist_every_n_turns=0)
    now = 1000.0
    entries = [
        TurnEntry(text="我喜欢做菜，做菜很有趣", ts=now),
        TurnEntry(text="周末经常去做菜，做菜很疗愈", ts=now),
        TurnEntry(text="跑步也喜欢，跑步十公里", ts=now),
        TurnEntry(text="今天天气很好", ts=now),
    ]
    kw = learner.extract_keywords(entries, now=now)
    # "做菜" 必须出现且 weight ≈ 1.0（被吸收+排第一）
    ok = ("做菜" in kw) and (kw["做菜"] >= 0.9) and ("跑步" in kw)
    # 长度 <= topk
    ok = ok and len(kw) <= 5
    _record("V1 bigram topk + 重复词高权重", ok, f"kw={dict(list(kw.items())[:6])}")


# ---------------------------------------------------------------------------
# V2 time decay
# ---------------------------------------------------------------------------


def v2_time_decay():
    from coco.companion.preference_learner import PreferenceLearner, TurnEntry
    half_life = 1000.0
    learner = PreferenceLearner(topk=10, half_life_s=half_life, persist_every_n_turns=0)
    now = 10_000.0
    # 旧 turn 1 个半衰期前；新 turn 现在
    entries = [
        TurnEntry(text="阅读小说很享受", ts=now - half_life),  # 旧 -> weight ~0.5
        TurnEntry(text="编程是日常工作", ts=now),  # 新 -> weight ~1.0
    ]
    kw = learner.extract_keywords(entries, now=now)
    # 找到代表 "阅读" 与 "编程" 的 bigram
    old_score = max((v for k, v in kw.items() if "阅读" in k or "小说" in k or "享受" in k), default=0.0)
    new_score = max((v for k, v in kw.items() if "编程" in k or "日常" in k or "工作" in k), default=0.0)
    ok = (new_score > old_score > 0)
    _record("V2 time decay 旧 turn 权重低于新 turn",
            ok, f"new={new_score:.3f} old={old_score:.3f}")


# ---------------------------------------------------------------------------
# V3 write + read across "session"
# ---------------------------------------------------------------------------


def v3_persist_round_trip():
    from coco.companion.preference_learner import PreferenceLearner
    from coco.companion.profile_persist import (
        PersistentProfileStore, PersistedProfile, compute_profile_id,
    )
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        store = PersistentProfileStore(root=td_p / "profiles")
        pid = compute_profile_id("alice", "alice")
        rec0 = PersistedProfile(profile_id=pid, nickname="alice",
                                dialog_summary=["前面聊到：做菜 做菜 做菜 跑步"])
        store.save(rec0)

        learner = PreferenceLearner(topk=5, half_life_s=100_000.0, persist_every_n_turns=0)
        kw = learner.rebuild_for_profile(
            persist_store=store,
            profile_id=pid,
            dialog_memory=None,
        )
        ok1 = bool(kw) and ("做菜" in kw)

        # 模拟新 "session"：新 store 实例读同一目录
        store2 = PersistentProfileStore(root=td_p / "profiles")
        loaded = store2.load(pid)
        ok2 = loaded is not None and bool(loaded.prefer_topics) and "做菜" in loaded.prefer_topics
        _record(
            "V3 prefer_topics 写入并跨会话读回",
            ok1 and ok2,
            f"kw={kw} loaded.prefer_topics={loaded.prefer_topics if loaded else None}",
        )


# ---------------------------------------------------------------------------
# V4 ProactiveScheduler integration
# ---------------------------------------------------------------------------


def v4_proactive_set_pref():
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    ps = ProactiveScheduler(config=ProactiveConfig(enabled=True))
    # 无 prefer → 回退默认 seed
    default_pick = ps.select_topic_seed(["A 聊聊跑步", "B 聊聊编程"])
    ok_default = (default_pick == ps.config.topic_seed) and (ps.stats.prefer_weighted_select_count == 0)

    ps.set_topic_preferences({"跑步": 1.0, "编程": 0.1})
    pick1 = ps.select_topic_seed(["A 聊聊跑步", "B 聊聊编程"])
    ok_pick = "跑步" in pick1 and ps.stats.prefer_weighted_select_count == 1

    # 改 prefer → 倾向变化
    ps.set_topic_preferences({"编程": 1.0})
    pick2 = ps.select_topic_seed(["A 聊聊跑步", "B 聊聊编程"])
    ok_swap = "编程" in pick2 and ps.stats.prefer_weighted_select_count == 2

    # _build_system_prompt 含 prefer 提示
    sp = ps._build_system_prompt()
    ok_sp = sp is not None and "编程" in sp

    _record(
        "V4 ProactiveScheduler.set_topic_preferences 影响选择 + stats",
        ok_default and ok_pick and ok_swap and ok_sp,
        f"default_pick=default? {ok_default} pick1={pick1!r} pick2={pick2!r} "
        f"select_count={ps.stats.prefer_weighted_select_count} sp_has_prefer={ok_sp}",
    )


# ---------------------------------------------------------------------------
# V5 default-OFF
# ---------------------------------------------------------------------------


def v5_default_off():
    from coco.companion.preference_learner import preference_learn_enabled_from_env
    e0 = dict(os.environ)
    try:
        os.environ.pop("COCO_PREFER_LEARN", None)
        off = preference_learn_enabled_from_env()
        os.environ["COCO_PREFER_LEARN"] = "1"
        on = preference_learn_enabled_from_env()
        os.environ["COCO_PREFER_LEARN"] = "0"
        off2 = preference_learn_enabled_from_env()
        ok = (off is False) and (on is True) and (off2 is False)
        _record("V5 default-OFF env semantics", ok,
                f"unset={off} =1={on} =0={off2}")
    finally:
        os.environ.clear()
        os.environ.update(e0)


# ---------------------------------------------------------------------------
# V6 schema compat — 旧 v1 profile 无 prefer_topics 字段
# ---------------------------------------------------------------------------


def v6_schema_compat():
    from coco.companion.profile_persist import (
        PersistentProfileStore, PROFILE_ID_LEN, compute_profile_id,
    )
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td) / "profiles"
        td_p.mkdir(parents=True, exist_ok=True)
        pid = compute_profile_id("alice", "alice")
        # 手写一个旧 v1 文件（无 prefer_topics 键）
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
        ok_loaded = rec is not None and rec.prefer_topics == {}

        # default-OFF V6 验证：rec.prefer_topics 为空时 to_dict 不含字段
        d = rec.to_dict() if rec else {}
        ok_nofield = "prefer_topics" not in d

        _record(
            "V6 旧 v1 schema 兼容（无 prefer_topics 字段不报错 + 默认 empty）",
            bool(rec) and ok_loaded and ok_nofield,
            f"loaded={bool(rec)} prefer_topics={rec.prefer_topics if rec else None} "
            f"to_dict_has_field={'prefer_topics' in d}",
        )


# ---------------------------------------------------------------------------
# V7 multiple profiles isolated
# ---------------------------------------------------------------------------


def v7_multi_profile_isolated():
    from coco.companion.preference_learner import PreferenceLearner
    from coco.companion.profile_persist import (
        PersistentProfileStore, PersistedProfile, compute_profile_id,
    )
    with tempfile.TemporaryDirectory() as td:
        store = PersistentProfileStore(root=Path(td) / "profiles")
        pid_a = compute_profile_id("alice", "alice")
        pid_b = compute_profile_id("bob", "bob")
        store.save(PersistedProfile(profile_id=pid_a, nickname="alice",
                                    dialog_summary=["做菜 做菜 做菜"]))
        store.save(PersistedProfile(profile_id=pid_b, nickname="bob",
                                    dialog_summary=["跑步 跑步 跑步"]))
        learner = PreferenceLearner(topk=5, persist_every_n_turns=0)
        kw_a = learner.rebuild_for_profile(persist_store=store, profile_id=pid_a)
        kw_b = learner.rebuild_for_profile(persist_store=store, profile_id=pid_b)

        rec_a = store.load(pid_a)
        rec_b = store.load(pid_b)
        ok = (
            bool(rec_a) and bool(rec_b)
            and "做菜" in (rec_a.prefer_topics or {})
            and "跑步" in (rec_b.prefer_topics or {})
            and "跑步" not in (rec_a.prefer_topics or {})
            and "做菜" not in (rec_b.prefer_topics or {})
        )
        _record(
            "V7 多 profile 独立更新 prefer_topics 不串扰",
            ok,
            f"a={rec_a.prefer_topics if rec_a else None} "
            f"b={rec_b.prefer_topics if rec_b else None}",
        )


# ---------------------------------------------------------------------------
# V8 stopwords filter
# ---------------------------------------------------------------------------


def v8_stopwords_filter():
    from coco.companion.preference_learner import PreferenceLearner, TurnEntry
    learner = PreferenceLearner(topk=10, half_life_s=10_000.0, persist_every_n_turns=0)
    now = 1000.0
    # 大量"我们"/"然后"/"the"/"a"——单字虚词不会被 bigram 命中，但"我们""然后"会被 bigram 切到
    entries = [
        TurnEntry(text="我们 然后 我们 然后 我们 the the a a", ts=now),
        TurnEntry(text="阅读 阅读 阅读", ts=now),
    ]
    kw = learner.extract_keywords(entries, now=now)
    # "我们" / "然后" / "the" / "a" 都不应在 TopK
    bad = {"我们", "然后", "the", "a"}
    ok = not (bad & set(kw.keys())) and any("阅读" in k for k in kw.keys())
    _record("V8 停用词过滤生效", ok, f"kw_keys={list(kw.keys())}")


# ---------------------------------------------------------------------------
# V9 stats
# ---------------------------------------------------------------------------


def v9_stats_correct():
    from coco.companion.preference_learner import PreferenceLearner
    from coco.companion.profile_persist import (
        PersistentProfileStore, PersistedProfile, compute_profile_id,
    )
    with tempfile.TemporaryDirectory() as td:
        store = PersistentProfileStore(root=Path(td) / "profiles")
        pid = compute_profile_id("alice", "alice")
        store.save(PersistedProfile(profile_id=pid, nickname="alice",
                                    dialog_summary=["做菜 做菜 做菜"]))
        learner = PreferenceLearner(topk=5, persist_every_n_turns=3)
        # 不存在 profile → persist_skipped_count++
        bad_pid = "0" * 12
        learner.rebuild_for_profile(persist_store=store, profile_id=bad_pid)
        skip0 = learner.stats.persist_skipped_count

        learner.rebuild_for_profile(persist_store=store, profile_id=pid)
        learner.rebuild_for_profile(persist_store=store, profile_id=pid)

        # on_turn 计数
        learner.on_turn(user_text="x")
        learner.on_turn(user_text="x")
        before = learner.stats.on_turn_count
        due = learner.on_turn(user_text="x")  # 第 3 次到 N=3

        ok = (
            learner.stats.updated_count == 2
            and learner.stats.extracted_keywords_total > 0
            and learner.stats.last_input_summaries == 1
            and skip0 >= 1
            and learner.stats.on_turn_count == before + 1
            and due is True
        )
        _record(
            "V9 stats 计数（updated_count / extracted_total / on_turn / persist_skipped）",
            ok,
            f"updated={learner.stats.updated_count} "
            f"extracted={learner.stats.extracted_keywords_total} "
            f"skipped={skip0} on_turn={learner.stats.on_turn_count} due={due}",
        )


# ---------------------------------------------------------------------------
# V10 coexist with vision-006/007/interact-011/companion-008
# ---------------------------------------------------------------------------


def v10_coexist():
    """确保 set_topic_preferences / select_topic_seed 与 record_caption_trigger /
    record_multimodal_trigger / pause-resume 互不影响。"""
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    ps = ProactiveScheduler(config=ProactiveConfig(enabled=True))

    # vision-006: caption proactive
    ps.record_caption_trigger("画面变暗")
    # vision-007: mm fusion
    ps.record_multimodal_trigger("dark_silence", "test")
    # interact-011: pause / resume
    ps.pause("test")
    ps.resume("test")
    # companion-009: 偏好选 topic
    ps.set_topic_preferences({"做菜": 1.0})
    pick = ps.select_topic_seed(["random", "聊做菜"])
    ok = (
        ps.stats.caption_proactive == 1
        and ps.stats.mm_triggered == 1
        and ps.stats.mm_per_rule.get("dark_silence") == 1
        and "做菜" in pick
        and ps.stats.prefer_weighted_select_count == 1
        and ps.is_paused() is False
    )

    # companion-008: PersistentProfileStore.save / load with prefer_topics 共存
    import tempfile
    from coco.companion.profile_persist import (
        PersistentProfileStore, PersistedProfile, compute_profile_id,
    )
    with tempfile.TemporaryDirectory() as td:
        store = PersistentProfileStore(root=Path(td) / "profiles")
        pid = compute_profile_id("alice", "alice")
        rec = PersistedProfile(
            profile_id=pid, nickname="alice",
            interests=["看书"], goals=["每天读 30 分钟"],
            dialog_summary=["前面聊到：做菜"], prefer_topics={"做菜": 1.0},
        )
        store.save(rec)
        loaded = store.load(pid)
        ok_pp = (
            loaded is not None
            and loaded.interests == ["看书"]
            and loaded.goals == ["每天读 30 分钟"]
            and loaded.dialog_summary == ["前面聊到：做菜"]
            and loaded.prefer_topics == {"做菜": 1.0}
        )
    _record(
        "V10 共存（vision-006/-007/interact-011/companion-008）",
        ok and ok_pp,
        f"ps.stats(caption={ps.stats.caption_proactive} mm={ps.stats.mm_triggered} "
        f"prefer_sel={ps.stats.prefer_weighted_select_count}) profile_persist_ok={ok_pp}",
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    cases = [
        v1_topk_bigram,
        v2_time_decay,
        v3_persist_round_trip,
        v4_proactive_set_pref,
        v5_default_off,
        v6_schema_compat,
        v7_multi_profile_isolated,
        v8_stopwords_filter,
        v9_stats_correct,
        v10_coexist,
    ]
    for fn in cases:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            _record(fn.__name__, False,
                    f"raised {type(e).__name__}: {e} | tb={traceback.format_exc()[:400]}")

    ok_all = all(r["ok"] for r in _results)
    summary = {
        "feature": "companion-009",
        "ok": ok_all,
        "results": _results,
    }

    out_dir = ROOT / "evidence" / "companion-009"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print(
        f"[verify_companion_009] {'ALL PASS' if ok_all else 'FAILED'}"
        f" {sum(1 for r in _results if r['ok'])}/{len(_results)}"
    )
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
