"""companion-014 verification.

消化 companion-009 L2 3 条非阻塞：
  (a) PreferenceLearner 真 emit ``companion.preference_updated`` 事件
      (含 topic / delta / new_score / old_score / profile_id)；去抖：仅当 prev/new
      不一致才发。
  (b) ProactiveScheduler.set_topic_seed_provider(...) 公开 API wire 到
      后台 _do_trigger_unlocked 路径，select_topic_seed(candidates=...) 真被调用。
  (c) _on_interaction_combined async rebuild 选项（COCO_COMPANION_ASYNC_REBUILD=1）
      不阻主回调线程；default-OFF 同步行为 bytewise 等价 companion-009。

跑法::

    uv run python scripts/verify_companion_014.py

子项 V1-V8：
  V1   PreferenceLearner 真 emit `companion.preference_updated` schema 校验
       (topic/delta/new_score/old_score/profile_id)
  V2   去抖：rebuild 二次（无新输入）→ emit 不再发；分数变 → 真发
  V3   set_topic_seed_provider 后 maybe_trigger 后台路径调
       select_topic_seed(candidates=provider())
  V4   provider 注入 candidates → 实际改变选种结果（按 prefer 加权挑）
       + stats.prefer_weighted_select_count 递增
  V5   COCO_COMPANION_ASYNC_REBUILD=1 走 async 路径不阻主线程；OFF 走同步
  V6   default-OFF：emit_fn=None 时 emit 不发；provider=None 时 select_topic_seed
       未被走 candidates 路径（与 companion-009 bytewise 等价）
  V7   AST/grep marker：rebuild_for_profile 含 _maybe_emit_preference_updated 调用；
       proactive.py 含 set_topic_seed_provider 与 _topic_seed_provider 调用
  V8   AUTHORITATIVE_COMPONENTS 包含 'companion' (用于 emit 的 component)

retval：0 全 PASS；1 任一失败
evidence 落 evidence/companion-014/verify_summary.json
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_companion_014] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": ok, "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeEmitter:
    """收集 emit(component_event, message, **payload) 调用。"""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, str, Dict[str, Any]]] = []
        self.lock = threading.Lock()

    def __call__(self, component_event: str, message: str = "", **payload: Any) -> None:
        with self.lock:
            self.calls.append((component_event, message, dict(payload)))

    def by_event(self, component_event: str) -> List[Dict[str, Any]]:
        with self.lock:
            return [c[2] for c in self.calls if c[0] == component_event]


def _make_store_with_summary(td: Path, profile_uid: str, summary_lines: List[str]) -> Tuple[Any, str]:
    from coco.companion.profile_persist import (
        PersistentProfileStore, PersistedProfile, compute_profile_id,
    )
    store = PersistentProfileStore(root=td / "profiles")
    pid = compute_profile_id(profile_uid, profile_uid)
    rec = PersistedProfile(profile_id=pid, nickname=profile_uid,
                           dialog_summary=list(summary_lines))
    store.save(rec)
    return store, pid


class _FaceSnap:
    def __init__(self, present: bool) -> None:
        self.present = present
        self.primary = None

    def x_ratio(self):
        return None


class _FaceTracker:
    def __init__(self, present: bool = True) -> None:
        self._present = present

    def latest(self) -> _FaceSnap:
        return _FaceSnap(self._present)


# ---------------------------------------------------------------------------
# V1 emit schema
# ---------------------------------------------------------------------------


def v1_emit_schema() -> None:
    from coco.companion.preference_learner import PreferenceLearner
    em = FakeEmitter()
    with tempfile.TemporaryDirectory() as td:
        store, pid = _make_store_with_summary(
            Path(td), "alice",
            ["前面聊到：做菜 做菜 做菜 做菜 跑步 跑步 编程"],
        )
        learner = PreferenceLearner(topk=5, half_life_s=100_000.0,
                                    persist_every_n_turns=0, emit_fn=em)
        kw = learner.rebuild_for_profile(persist_store=store, profile_id=pid,
                                         dialog_memory=None)
        evs = em.by_event("companion.preference_updated")
        ok_kw = bool(kw)
        ok_emit = len(evs) >= 1
        keys_required = {"topic", "delta", "new_score", "old_score", "profile_id"}
        ok_schema = all(keys_required.issubset(set(e.keys())) for e in evs)
        # profile_id 一致；new_score 为正
        ok_pid = all(e.get("profile_id") == pid for e in evs)
        ok_pos = all(float(e.get("new_score", 0.0)) > 0.0 for e in evs)
        # old_score 全是 0（首次 rebuild）
        ok_old = all(float(e.get("old_score", -1.0)) == 0.0 for e in evs)
        _record(
            "V1 emit companion.preference_updated schema (topic/delta/new_score/old_score/profile_id)",
            ok_kw and ok_emit and ok_schema and ok_pid and ok_pos and ok_old,
            f"kw={kw} n_emit={len(evs)} schema_ok={ok_schema} pid_ok={ok_pid} pos_ok={ok_pos}",
        )


# ---------------------------------------------------------------------------
# V2 去抖
# ---------------------------------------------------------------------------


def v2_dedup() -> None:
    from coco.companion.preference_learner import PreferenceLearner
    em = FakeEmitter()
    with tempfile.TemporaryDirectory() as td:
        store, pid = _make_store_with_summary(
            Path(td), "bob",
            ["前面聊到：做菜 做菜 做菜 跑步 跑步"],
        )
        learner = PreferenceLearner(topk=5, half_life_s=100_000.0,
                                    persist_every_n_turns=0, emit_fn=em)
        # 第一次：会 emit
        learner.rebuild_for_profile(persist_store=store, profile_id=pid)
        n1 = len(em.by_event("companion.preference_updated"))
        # 第二次：相同输入（store 已存 prefer_topics），prev==new → 去抖，不应再 emit
        learner.rebuild_for_profile(persist_store=store, profile_id=pid)
        n2 = len(em.by_event("companion.preference_updated"))
        ok_first = n1 >= 1
        ok_dedup = n2 == n1
        # 第三次：让 record 改名引入新 summary，分数变 → 应再 emit
        rec = store.load(pid)
        rec.dialog_summary = list(rec.dialog_summary) + ["新主题：编程 编程 编程 编程"]
        store.save(rec)
        learner.rebuild_for_profile(persist_store=store, profile_id=pid)
        n3 = len(em.by_event("companion.preference_updated"))
        ok_change = n3 > n2
        _record(
            "V2 emit 去抖（prev==new 不发；变化才发）",
            ok_first and ok_dedup and ok_change,
            f"n1={n1} n2={n2} n3={n3}",
        )


# ---------------------------------------------------------------------------
# V3 + V4 scheduler topic_seed_provider wire
# ---------------------------------------------------------------------------


def v3_v4_scheduler_provider_wire() -> None:
    """V3: provider 真被后台 maybe_trigger 调用；
       V4: candidates 注入改变 select_topic_seed 选种 + stats 计数。"""
    from coco.proactive import ProactiveScheduler, ProactiveConfig

    # 构造 scheduler，置 enabled=True、idle_threshold/cooldown 极短，
    # 不挂 power/face；llm_reply_fn / tts_say_fn 用纯 stub。
    spoken: List[str] = []

    def _llm(seed: str, **kw) -> str:
        spoken.append(seed)
        return f"LLM<{seed}>"

    def _tts(text: str, **kw) -> None:
        pass

    cfg = ProactiveConfig(
        enabled=True,
        idle_threshold_s=0.0,
        cooldown_s=0.0,
        max_topics_per_hour=1000,
        tick_s=0.05,
        topic_seed="DEFAULT_SEED",
    )
    ps = ProactiveScheduler(config=cfg, llm_reply_fn=_llm, tts_say_fn=_tts,
                            face_tracker=_FaceTracker(present=True))
    # 灌入 prefer，使 select_topic_seed 能加权
    ps.set_topic_preferences({"跑步": 1.0, "编程": 0.3})

    # 安装 provider
    provider_calls = {"n": 0}

    def _provider() -> List[str]:
        provider_calls["n"] += 1
        return ["A 聊聊跑步", "B 聊聊编程", "C 聊聊别的"]

    ps.set_topic_seed_provider(_provider)
    ok_get = ps.get_topic_seed_provider() is _provider

    # 推进 last_interaction_ts 让 idle 立即过；触发一次
    ps._last_interaction_ts = ps.clock() - 10.0
    triggered = ps.maybe_trigger()
    ok_trig = bool(triggered)
    ok_called = provider_calls["n"] >= 1
    # 后台路径走过：select_topic_seed 命中（应递增 prefer_weighted_select_count）
    ok_select = ps.stats.prefer_weighted_select_count >= 1
    # 选种结果应是"跑步"那条（按 prefer 加权胜出）→ LLM 收到的 seed 含跑步
    ok_seed = any("跑步" in s for s in spoken)

    _record(
        "V3 set_topic_seed_provider wire 到 maybe_trigger 后台路径",
        ok_get and ok_trig and ok_called,
        f"get_ok={ok_get} triggered={ok_trig} provider_called={provider_calls['n']}",
    )
    _record(
        "V4 candidates 注入按 prefer 加权挑选 + stats 计数",
        ok_select and ok_seed,
        f"prefer_select_count={ps.stats.prefer_weighted_select_count} spoken={spoken!r}",
    )


# ---------------------------------------------------------------------------
# V5 async rebuild 路径
# ---------------------------------------------------------------------------


def v5_async_rebuild() -> None:
    from coco.companion.preference_learner import (
        PreferenceLearner, async_rebuild_enabled_from_env,
    )
    # 5.1 env helper
    e0 = dict(os.environ)
    try:
        os.environ.pop("COCO_COMPANION_ASYNC_REBUILD", None)
        off = async_rebuild_enabled_from_env()
        os.environ["COCO_COMPANION_ASYNC_REBUILD"] = "1"
        on = async_rebuild_enabled_from_env()
        os.environ["COCO_COMPANION_ASYNC_REBUILD"] = "0"
        off2 = async_rebuild_enabled_from_env()
    finally:
        os.environ.clear()
        os.environ.update(e0)
    ok_env = (off is False) and (on is True) and (off2 is False)

    # 5.2 真跑 async：用慢 save 模拟 fsync 阻塞；主线程 submit 后立即返回
    em = FakeEmitter()
    with tempfile.TemporaryDirectory() as td:
        store, pid = _make_store_with_summary(
            Path(td), "carol",
            ["前面聊到：阅读 阅读 阅读 阅读 散步"],
        )
        # 包装 store.save 让它 sleep 0.3s
        orig_save = store.save
        save_started = threading.Event()
        save_done = threading.Event()

        def _slow_save(rec):
            save_started.set()
            time.sleep(0.3)
            r = orig_save(rec)
            save_done.set()
            return r
        store.save = _slow_save  # type: ignore[assignment]

        learner = PreferenceLearner(topk=5, half_life_s=100_000.0,
                                    persist_every_n_turns=0, emit_fn=em)
        t0 = time.time()
        fut = learner.rebuild_for_profile_async(persist_store=store, profile_id=pid)
        submit_dt = time.time() - t0
        ok_submit_fast = submit_dt < 0.1  # submit 自己应秒回
        # 等 future 完成
        kw = fut.result(timeout=5.0)
        ok_kw = bool(kw)
        ok_save_done = save_done.is_set()
        learner.shutdown_executor(wait=True)
    _record(
        "V5 async rebuild：env gate + submit 不阻主线程 + future 后续完成",
        ok_env and ok_submit_fast and ok_kw and ok_save_done,
        f"env_ok={ok_env} submit_dt={submit_dt:.3f}s kw={kw} save_done={ok_save_done}",
    )


# ---------------------------------------------------------------------------
# V6 default-OFF bytewise
# ---------------------------------------------------------------------------


def v6_default_off() -> None:
    """emit_fn=None → emit 不发；provider=None → maybe_trigger 不走 candidates 路径
    (companion-009 bytewise 等价：select_topic_seed 仅在外部 set_topic_preferences
    存在但 candidates=None 时无变化)。"""
    from coco.companion.preference_learner import PreferenceLearner
    from coco.proactive import ProactiveScheduler, ProactiveConfig

    em = FakeEmitter()  # 不传给 learner，确认 None 时不发
    with tempfile.TemporaryDirectory() as td:
        store, pid = _make_store_with_summary(
            Path(td), "dan",
            ["前面聊到：游泳 游泳 游泳"],
        )
        learner = PreferenceLearner(topk=5, half_life_s=100_000.0,
                                    persist_every_n_turns=0, emit_fn=None)
        learner.rebuild_for_profile(persist_store=store, profile_id=pid)
        ok_no_emit = len(em.calls) == 0  # em 没注给 learner

    # provider=None → maybe_trigger 不调 select_topic_seed(candidates=...)
    spoken: List[str] = []

    def _llm(seed: str, **kw) -> str:
        spoken.append(seed)
        return f"x<{seed}>"

    cfg = ProactiveConfig(
        enabled=True, idle_threshold_s=0.0, cooldown_s=0.0,
        max_topics_per_hour=1000, tick_s=0.05, topic_seed="DEFAULT_SEED",
    )
    ps = ProactiveScheduler(config=cfg, llm_reply_fn=_llm, tts_say_fn=lambda x: None,
                            face_tracker=_FaceTracker(present=True))
    ps.set_topic_preferences({"游泳": 1.0})
    # 不 set provider
    ps._last_interaction_ts = ps.clock() - 10.0
    triggered = ps.maybe_trigger()
    # provider=None 时 prefer_weighted_select_count 应为 0（_select_topic_seed 仅在
    # provider 路径 + select_topic_seed 才递增；后台无 candidates 路径走 default seed）
    ok_no_select = ps.stats.prefer_weighted_select_count == 0
    ok_default_seed = any("DEFAULT_SEED" in s for s in spoken)
    _record(
        "V6 default-OFF：emit_fn=None 不发 / provider=None 不走 candidates",
        ok_no_emit and triggered and ok_no_select and ok_default_seed,
        f"no_emit={ok_no_emit} triggered={triggered} "
        f"prefer_select_count={ps.stats.prefer_weighted_select_count} spoken={spoken!r}",
    )


# ---------------------------------------------------------------------------
# V7 AST/grep marker
# ---------------------------------------------------------------------------


def v7_marker() -> None:
    pl = (ROOT / "coco" / "companion" / "preference_learner.py").read_text(encoding="utf-8")
    pa = (ROOT / "coco" / "proactive.py").read_text(encoding="utf-8")
    ok_pl = (
        "_maybe_emit_preference_updated" in pl
        and "companion.preference_updated" in pl
        and "async_rebuild_enabled_from_env" in pl
        and "emit_fn" in pl
    )
    ok_pa = (
        "set_topic_seed_provider" in pa
        and "_topic_seed_provider" in pa
        and "select_topic_seed(" in pa
    )
    _record(
        "V7 source marker (preference_learner emit + proactive provider hook)",
        ok_pl and ok_pa,
        f"pl_ok={ok_pl} pa_ok={ok_pa}",
    )


# ---------------------------------------------------------------------------
# V8 AUTHORITATIVE_COMPONENTS 包含 companion
# ---------------------------------------------------------------------------


def v8_authoritative_components() -> None:
    from coco.logging_setup import AUTHORITATIVE_COMPONENTS
    ok = "companion" in AUTHORITATIVE_COMPONENTS
    _record("V8 AUTHORITATIVE_COMPONENTS 包含 'companion'", ok,
            f"companion_in={ok}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    v1_emit_schema()
    v2_dedup()
    v3_v4_scheduler_provider_wire()
    v5_async_rebuild()
    v6_default_off()
    v7_marker()
    v8_authoritative_components()

    n_pass = sum(1 for r in _results if r["ok"])
    n_total = len(_results)
    print(f"\n[verify_companion_014] summary: {n_pass}/{n_total} PASS", flush=True)

    out_dir = ROOT / "evidence" / "companion-014"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": time.time(),
        "results": _results,
        "n_pass": n_pass,
        "n_total": n_total,
    }
    (out_dir / "verify_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
