"""scripts/verify_interact_011.py — 离线降级回路验证 (interact-011).

V1  env=0 LLM 失败保持兼容（不进 fallback）
V2  env=1 连续 3 次失败 → 进入 fallback + emit 'interact.offline_entered'
V3  fallback 期 ProactiveScheduler 被 pause（_should_trigger 返回 "paused"）
V4  fallback 期间 LLM 任一次成功 → emit 'interact.offline_recovered' +
    主动说 '我回来了' + ProactiveScheduler resume
V5  fallback turn 在 dialog_memory 带 [fallback] 前缀
V6  interact-009 summarizer 跳过 fallback turn（断言 summary 不含 fallback utterance）
V7  companion-004 user-profile 不更新偏好（fallback 期间不抽 profile）
V8  短抖动（失败 2 次后成功）不触发 fallback
V9  fallback 引用最近 1 轮上下文片段（{recent_topic} 模板）
V10 回归 smoke：interact-002 / interact-009 / companion-005 verify 仍通过（由外部 driver 跑）

跑法：``./.venv/bin/python scripts/verify_interact_011.py``
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 全局把 OFFLINE_FALLBACK 关掉再按需开（避免 env 泄漏）
os.environ.pop("COCO_OFFLINE_FALLBACK", None)

import numpy as np

from coco.offline_fallback import (
    OfflineDialogFallback,
    OfflineFallbackConfig,
    USER_FALLBACK_TAG,
    is_fallback_user_text,
    offline_fallback_enabled_from_env,
    config_from_env,
)
from coco.dialog import DialogMemory
from coco.dialog_summary import HeuristicSummarizer, _skip_turn
from coco.proactive import ProactiveScheduler, ProactiveConfig
from coco.interact import InteractSession
from coco.profile import ProfileStore, UserProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeLLMStats:
    def __init__(self) -> None:
        self.backend_ok = 0
        self.backend_fail = 0
        self.calls = 0


class FakeLLMClient:
    """模拟 LLMClient.reply：按 outcome 队列决定本次是真成功还是 backend 失败。

    每次 reply 调用 pop 一个 outcome（True=真成功，False=失败）；
    队列空了之后默认 True。
    """

    def __init__(self, outcomes: Optional[List[bool]] = None,
                 reply_text: str = "好的呀。") -> None:
        self.stats = FakeLLMStats()
        self._outcomes: List[bool] = list(outcomes or [])
        self.reply_text = reply_text
        self.calls: List[Tuple[str, dict]] = []

    def reply(self, text: str, **kwargs) -> str:
        self.stats.calls += 1
        self.calls.append((text, kwargs))
        ok = self._outcomes.pop(0) if self._outcomes else True
        if ok:
            self.stats.backend_ok += 1
            return self.reply_text
        else:
            self.stats.backend_fail += 1
            # LLMClient 真实行为：失败时降级到 KEYWORD_ROUTES，返回非空字符串
            return "我听到你说：" + text


class FakeRobot:
    def head_to(self, *a, **k): pass
    def goto_sleep(self, *a, **k): pass
    def goto_zero(self, *a, **k): pass


class FakeTTS:
    def __init__(self) -> None:
        self.spoken: List[str] = []

    def say(self, text: str, *, blocking: bool = True, **kw) -> None:
        self.spoken.append(text)


def _fake_asr(_audio: np.ndarray, _sr: int) -> str:
    # 由 verify 用 monkey-patch 改
    return _fake_asr.transcript  # type: ignore[attr-defined]


_fake_asr.transcript = ""  # type: ignore[attr-defined]


def _silence_audio() -> Tuple[np.ndarray, int]:
    return np.zeros(160, dtype=np.int16), 16000


def _collect_events(emit_log: List[dict]):
    def _emit(event: str, **payload):
        emit_log.append({"event": event, **payload})
    return _emit


# ---------------------------------------------------------------------------
# V1
# ---------------------------------------------------------------------------


def v1_env_off_compat():
    """env=0 → wrap_llm_reply 直接转发；失败不切 fallback。"""
    print("[V1] env=0 LLM 失败保持兼容 ...")
    cfg = OfflineFallbackConfig(enabled=False, fail_threshold=3)
    fb = OfflineDialogFallback(config=cfg)
    fake = FakeLLMClient(outcomes=[False, False, False, False, False])
    wrapped = fb.wrap_llm_reply(fake)
    # wrap_llm_reply 在 enabled=False 时返回的就是 fake.reply 本身（bound method ==）
    assert wrapped == fake.reply, "env=0 时应透明转发，未透明 → 行为不兼容"
    # 多次失败：fallback 状态保持 False
    for _ in range(5):
        wrapped("hi")
    assert not fb.is_in_fallback(), "env=0 时不应进 fallback"
    print("[V1] PASS")


# ---------------------------------------------------------------------------
# V2
# ---------------------------------------------------------------------------


def v2_consec_3_fails_enter():
    print("[V2] env=1 连续 3 失败 → 进入 fallback + emit ...")
    cfg = OfflineFallbackConfig(enabled=True, fail_threshold=3, probe_interval_s=0)
    emit_log: List[dict] = []
    fb = OfflineDialogFallback(config=cfg, emit_fn=_collect_events(emit_log))
    fake = FakeLLMClient(outcomes=[False, False, False])
    wrapped = fb.wrap_llm_reply(fake)
    for _ in range(3):
        wrapped("hi")
    assert fb.is_in_fallback(), "连续 3 失败应进 fallback"
    assert fb.failure_count() == 3
    entered = [e for e in emit_log if e["event"] == "interact.offline_entered"]
    assert len(entered) == 1, f"应 emit 1 次 offline_entered，实际 {len(entered)}: {emit_log}"
    assert entered[0].get("failure_count") == 3
    print("[V2] PASS")


# ---------------------------------------------------------------------------
# V3
# ---------------------------------------------------------------------------


def v3_proactive_paused_in_fallback():
    print("[V3] fallback 期 ProactiveScheduler 被 pause ...")
    pcfg = ProactiveConfig(enabled=True, idle_threshold_s=0.01, cooldown_s=0.01,
                           max_topics_per_hour=60, tick_s=1.0)
    proactive = ProactiveScheduler(
        config=pcfg,
        power_state=None,  # 没 power_state → _should_trigger 跳过该检查
        face_tracker=type("FT", (), {"latest": staticmethod(lambda: type("S", (), {"present": True})())})(),
        llm_reply_fn=None, tts_say_fn=None, profile_store=None,
        on_interaction=None,
    )
    cfg = OfflineFallbackConfig(enabled=True, fail_threshold=2, probe_interval_s=0)
    fb = OfflineDialogFallback(config=cfg, proactive_scheduler=proactive)

    # 一开始 _should_trigger 通过（不是 paused）
    assert not proactive.is_paused()

    fake = FakeLLMClient(outcomes=[False, False])
    wrapped = fb.wrap_llm_reply(fake)
    for _ in range(2):
        wrapped("hi")
    assert fb.is_in_fallback()
    assert proactive.is_paused(), "fallback 切入后 proactive 应 paused"
    # _should_trigger 返回 "paused"
    time.sleep(0.02)  # 让 idle_threshold 超过
    reason = proactive._should_trigger()
    assert reason == "paused", f"_should_trigger 应返回 paused，实际 {reason!r}"
    print("[V3] PASS")
    return proactive, fb, fake, wrapped


# ---------------------------------------------------------------------------
# V4
# ---------------------------------------------------------------------------


def v4_recovery_on_success():
    print("[V4] fallback 期间一次成功 → 恢复 + '我回来了' + proactive resume ...")
    pcfg = ProactiveConfig(enabled=True, idle_threshold_s=0.01, cooldown_s=0.01,
                           max_topics_per_hour=60, tick_s=1.0)
    proactive = ProactiveScheduler(
        config=pcfg, power_state=None,
        face_tracker=type("FT", (), {"latest": staticmethod(lambda: type("S", (), {"present": True})())})(),
    )
    tts = FakeTTS()
    cfg = OfflineFallbackConfig(enabled=True, fail_threshold=2, probe_interval_s=0)
    emit_log: List[dict] = []
    fb = OfflineDialogFallback(
        config=cfg, proactive_scheduler=proactive,
        emit_fn=_collect_events(emit_log), tts_say_fn=tts.say,
    )
    fake = FakeLLMClient(outcomes=[False, False, True])
    wrapped = fb.wrap_llm_reply(fake)
    wrapped("a"); wrapped("b")
    assert fb.is_in_fallback()
    assert proactive.is_paused()
    # 第 3 次：真成功
    wrapped("c")
    assert not fb.is_in_fallback(), "成功后应退出 fallback"
    assert not proactive.is_paused(), "应 resume proactive"
    recovered = [e for e in emit_log if e["event"] == "interact.offline_recovered"]
    assert len(recovered) == 1, f"应 emit 1 次 offline_recovered，实际 {emit_log}"
    assert "我回来了" in tts.spoken[-1], f"应说 '我回来了'，实际 {tts.spoken}"
    print("[V4] PASS")


# ---------------------------------------------------------------------------
# V5
# ---------------------------------------------------------------------------


def v5_dialog_memory_fallback_prefix():
    print("[V5] fallback turn 在 dialog_memory 带 [fallback] 前缀 ...")
    # 直通 InteractSession 验证：env=1 + LLM 连续失败 → handle_audio 把 [fallback] 写进 dm
    cfg = OfflineFallbackConfig(enabled=True, fail_threshold=2, probe_interval_s=0)
    fb = OfflineDialogFallback(config=cfg)
    fake = FakeLLMClient(outcomes=[False, False, False])
    wrapped = fb.wrap_llm_reply(fake)
    dm = DialogMemory(max_turns=10, idle_timeout_s=300.0)
    # 给 fb 注入 dm 引用（让 compose_fallback_reply 能取最近 user 原文）
    fb._dm_ref = lambda: dm  # type: ignore[attr-defined]

    sess = InteractSession(
        robot=FakeRobot(),
        asr_fn=_fake_asr, tts_say_fn=FakeTTS().say,
        llm_reply_fn=wrapped, dialog_memory=dm,
        offline_fallback=fb,
    )
    audio, sr = _silence_audio()

    # 第一轮：失败 1，不在 fallback
    _fake_asr.transcript = "今天天气怎么样"  # type: ignore[attr-defined]
    r1 = sess.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
    assert not fb.is_in_fallback()
    # 第二轮：失败 2 → 进 fallback；本轮 reply 使用模板，user 端打 [fallback] 前缀
    _fake_asr.transcript = "你听到了吗"  # type: ignore[attr-defined]
    r2 = sess.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
    assert fb.is_in_fallback(), "第 2 次失败应进 fallback"
    assert r2.get("fallback_used") is True, f"第 2 轮应标记 fallback_used，实际 {r2}"
    turns = dm.recent_turns()
    # 找最后一轮 user
    last_u, last_a = turns[-1]
    assert last_u.startswith(USER_FALLBACK_TAG), f"最后 user 应有 [fallback] 前缀，实际 {last_u!r}"
    assert "你听到了吗" in last_u
    # 第一轮（成功失败但未切）的 user 不带前缀
    first_u, _first_a = turns[0]
    assert not first_u.startswith(USER_FALLBACK_TAG), f"首轮不应有前缀，实际 {first_u!r}"
    print("[V5] PASS")
    return dm, sess, fb


# ---------------------------------------------------------------------------
# V6
# ---------------------------------------------------------------------------


def v6_summarizer_skips_fallback():
    print("[V6] HeuristicSummarizer / _skip_turn 跳过 [fallback] turn ...")
    turns = [
        ("我喜欢公园", "好呀，公园不错。"),
        ("[fallback] 你听到了吗", "我现在有点连不上网，等一下再聊好吗？"),
        ("我想学画画", "嗯，画画很好玩。"),
    ]
    summ = HeuristicSummarizer(max_chars=200)
    out = summ.summarize(turns)
    # 不应包含 fallback utterance 或前缀
    assert "[fallback]" not in out
    assert "我现在有点连不上网" not in out, f"summary 不应含 fallback 模板：{out}"
    assert "公园" in out and "画画" in out, f"summary 应含真实话题：{out}"
    # _skip_turn 单测
    assert _skip_turn("[fallback] 你好")
    assert _skip_turn("[手势:nod]")  # 仅手势
    assert not _skip_turn("[手势:nod] 你好啊")  # 手势 + 真文本：不 skip
    assert not _skip_turn("正常文本")
    print("[V6] PASS")


# ---------------------------------------------------------------------------
# V7
# ---------------------------------------------------------------------------


def v7_profile_not_polluted(tmp_dir: Path):
    print("[V7] fallback 期间 ProfileStore 不更新 ...")
    profile_path = tmp_dir / "profile.json"
    os.environ["COCO_PROFILE_PATH"] = str(profile_path)
    os.environ.pop("COCO_PROFILE_DISABLE", None)
    store = ProfileStore(path=profile_path)
    # 初始为空
    p0 = store.load()
    assert p0.interests == []

    cfg = OfflineFallbackConfig(enabled=True, fail_threshold=2, probe_interval_s=0)
    fb = OfflineDialogFallback(config=cfg)
    fake = FakeLLMClient(outcomes=[False, False, False])
    wrapped = fb.wrap_llm_reply(fake)
    dm = DialogMemory(max_turns=10, idle_timeout_s=300.0)

    sess = InteractSession(
        robot=FakeRobot(),
        asr_fn=_fake_asr, tts_say_fn=FakeTTS().say,
        llm_reply_fn=wrapped, dialog_memory=dm,
        profile_store=store, offline_fallback=fb,
    )
    audio, sr = _silence_audio()
    # 1) 进 fallback 之前 transcript 含兴趣短语 → 抽取应正常写入
    _fake_asr.transcript = "我喜欢公园"  # type: ignore[attr-defined]
    sess.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
    p1 = store.load()
    assert "公园" in p1.interests, f"非 fallback 期应抽到兴趣，实际 {p1.interests}"

    # 2) 进入 fallback 后即使含兴趣短语也不抽
    # 触发 fail → 此时 fb.consecutive_failures 已是 1，再失败 1 次切入
    _fake_asr.transcript = "我喜欢画画"  # type: ignore[attr-defined]
    sess.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
    assert fb.is_in_fallback(), "应已进 fallback"
    # 这一轮 LLM 失败发生在 handle_audio 内部，profile.extract 此时 fallback 状态
    # 尚未切入（因为 extract 在 LLM 调用之前）—— 这是合理的（用户 actually 说了
    # "我喜欢画画"，应抽）。所以"画画"可能已经被记入。
    # 关键检查：进入 fallback 之后**下一轮**（fb.is_in_fallback() = True）说兴趣短语
    # 时 profile 不该再被更新。
    p_mid = store.load()
    snapshot_interests = list(p_mid.interests)

    # 3) fallback 已经在了；再来一轮 "我喜欢编程"，不应进 profile
    _fake_asr.transcript = "我喜欢编程"  # type: ignore[attr-defined]
    sess.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
    assert fb.is_in_fallback(), "应仍在 fallback"
    p3 = store.load()
    assert "编程" not in p3.interests, (
        f"fallback 期不应抽到 '编程'，实际 {p3.interests}"
    )
    assert p3.interests == snapshot_interests, (
        f"fallback 期 interests 不应变化，{snapshot_interests} → {p3.interests}"
    )
    print("[V7] PASS")


# ---------------------------------------------------------------------------
# V8
# ---------------------------------------------------------------------------


def v8_short_jitter_no_fallback():
    print("[V8] 短抖动（失败 2 次后成功）不切 fallback ...")
    cfg = OfflineFallbackConfig(enabled=True, fail_threshold=3, probe_interval_s=0)
    fb = OfflineDialogFallback(config=cfg)
    fake = FakeLLMClient(outcomes=[False, False, True, False, False])
    wrapped = fb.wrap_llm_reply(fake)
    for _ in range(5):
        wrapped("hi")
    assert not fb.is_in_fallback(), "失败 2-成功-失败 2，consecutive 不到 3，不切"
    assert fb.failure_count() == 2  # 最后两次连续失败
    print("[V8] PASS")


# ---------------------------------------------------------------------------
# V9
# ---------------------------------------------------------------------------


def v9_fallback_references_recent_topic():
    print("[V9] fallback 引用最近 1 轮 user 片段 ...")
    cfg = OfflineFallbackConfig(
        enabled=True, fail_threshold=2,
        templates=("我们刚才聊到 {recent_topic} 了对吧？我先记着。",),
        probe_interval_s=0,
    )
    fb = OfflineDialogFallback(config=cfg)
    dm = DialogMemory(max_turns=4, idle_timeout_s=300.0)
    fb._dm_ref = lambda: dm  # type: ignore[attr-defined]
    # 模拟一轮已记录的对话
    dm.append("公园里的花真好看", "嗯，确实漂亮。")
    text = fb.compose_fallback_reply()
    assert "公园里的花真好看" in text or "公园" in text, f"应引用最近 user 片段，实际 {text!r}"
    print("[V9] PASS")


# ---------------------------------------------------------------------------
# V10 回归
# ---------------------------------------------------------------------------


def v10_regression_imports():
    """轻量回归：interact-002 + interact-009 + companion-005 关键 import + ctor smoke。"""
    print("[V10] 回归 import / ctor smoke ...")
    from coco.llm import build_default_client, LLMClient, FallbackBackend
    from coco.dialog_summary import HeuristicSummarizer, LLMSummarizer
    from coco.companion.situational_idle import SituationalIdleModulator  # companion-005
    # build_default_client 不应抛
    c = build_default_client()
    out = c.reply("你好")
    assert isinstance(out, str) and len(out) > 0
    # HeuristicSummarizer 仍能跑
    s = HeuristicSummarizer(max_chars=100).summarize([("你好", "嗨")])
    assert "你好" in s
    print("[V10] PASS")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        try:
            v1_env_off_compat()
            v2_consec_3_fails_enter()
            v3_proactive_paused_in_fallback()
            v4_recovery_on_success()
            v5_dialog_memory_fallback_prefix()
            v6_summarizer_skips_fallback()
            v7_profile_not_polluted(td_path)
            v8_short_jitter_no_fallback()
            v9_fallback_references_recent_topic()
            v10_regression_imports()
        except AssertionError as e:
            print(f"\n[FAIL] {e}")
            return 1
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"\n[ERROR] {type(e).__name__}: {e}")
            return 2
    print("\n[OK] interact-011 V1..V10 全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
