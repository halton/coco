"""vision-007 verification: 多模态主动话题融合 (MultimodalFusion).

跑法::

    uv run python scripts/verify_vision_007.py

子项：

V1   dark_silence 规则：caption 含『暗』+ 长时间无 ASR → 触发一次
V2   motion_greet 规则：caption 含『移动』+ 长时间无交互 → 触发一次
V3   规则级 cooldown：同 rule_id 窗口内不重复触发
V4   全局 rate limit：1/min 限速生效
V5   多 rule_id 独立 cooldown：R1 触发不影响 R2 触发
V6   default-OFF：未设 COCO_MM_PROACTIVE 时 enabled=False，调入即 no-op
V7   stats 计数正确：mm_triggered_total / mm_per_rule 同步增加
V8   priority_boost：写到 proactive._next_priority_boost + stats.priority_boost_count
V9   stop+join 干净退出（fusion 无独立线程；scheduler 验证）
V10  与 vision-006 caption_proactive 共存不互相干扰

retval：0 全 PASS；1 任一失败
evidence 落 evidence/vision-007/verify_summary.json
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_vision_007] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": ok, "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}")


class FakeClock:
    def __init__(self, t0: float = 0.0) -> None:
        self.t = float(t0)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _make_fusion(
    *,
    enabled: bool = True,
    silence_window_s: float = 60.0,
    idle_window_s: float = 120.0,
    rule_cooldown_s: float = 300.0,
    rate_limit_per_min: int = 10,  # 默认放宽，单条规则 cooldown 才是默认 guard
    clock: Optional[FakeClock] = None,
    proactive: Any = None,
    emit_fn=None,
):
    from coco.multimodal_fusion import (
        MultimodalFusion,
        MultimodalFusionConfig,
    )
    cfg = MultimodalFusionConfig(
        enabled=enabled,
        silence_window_s=silence_window_s,
        idle_window_s=idle_window_s,
        rule_cooldown_s=rule_cooldown_s,
        rate_limit_per_min=rate_limit_per_min,
    )
    emits: List[Any] = []
    def default_emit(event, **kw):
        emits.append((event, kw))
    fusion = MultimodalFusion(
        config=cfg,
        proactive=proactive,
        clock=clock or FakeClock(1000.0),
        emit_fn=emit_fn or default_emit,
    )
    return fusion, emits


def _make_proactive(clock=None):
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    return ProactiveScheduler(
        config=ProactiveConfig(enabled=False),
        clock=clock,
        emit_fn=lambda *a, **k: None,
    )


# ---------------------------------------------------------------------------
# V1 dark_silence
# ---------------------------------------------------------------------------


def v1_dark_silence() -> None:
    clk = FakeClock(1000.0)
    proactive = _make_proactive(clock=clk)
    fusion, emits = _make_fusion(clock=clk, proactive=proactive, silence_window_s=60.0)

    # silence_window: 因为 _last_asr_final_ts / _last_asr_partial_ts 都为 0，
    # 函数默认 silence_for=t；> 60s 即满足。直接喂 caption。
    fusion.on_scene_caption("画面整体偏暗，看起来像夜晚")

    ok = (
        fusion.stats.triggered_total == 1
        and fusion.stats.per_rule.get("dark_silence") == 1
        and fusion.stats.last_rule_id == "dark_silence"
        and proactive.stats.mm_triggered == 1
        and proactive.stats.mm_per_rule.get("dark_silence") == 1
        and any(e[0] == "proactive.multimodal_triggered" for e in emits)
    )
    _record(
        "V1 dark_silence rule fires",
        ok,
        f"triggered={fusion.stats.triggered_total} mm_triggered={proactive.stats.mm_triggered}",
    )


# ---------------------------------------------------------------------------
# V2 motion_greet
# ---------------------------------------------------------------------------


def v2_motion_greet() -> None:
    clk = FakeClock(1000.0)
    proactive = _make_proactive(clock=clk)
    fusion, _emits = _make_fusion(clock=clk, proactive=proactive, idle_window_s=120.0)

    # 强制 last_user_activity_ts 早于 idle_window_s
    fusion.inject_user_activity(ts=clk.t - 200.0)
    fusion.on_scene_caption("移动物体在左侧出现")

    ok = (
        fusion.stats.triggered_total == 1
        and fusion.stats.per_rule.get("motion_greet") == 1
        and proactive.stats.mm_per_rule.get("motion_greet") == 1
    )
    _record(
        "V2 motion_greet rule fires",
        ok,
        f"per_rule={dict(fusion.stats.per_rule)}",
    )


# ---------------------------------------------------------------------------
# V3 规则级 cooldown
# ---------------------------------------------------------------------------


def v3_rule_cooldown() -> None:
    clk = FakeClock(1000.0)
    fusion, _ = _make_fusion(
        clock=clk,
        rule_cooldown_s=300.0,
        rate_limit_per_min=10,
        silence_window_s=10.0,
    )
    # 第一次触发
    fusion.on_scene_caption("画面很暗")
    first = fusion.stats.triggered_total
    # 同 rule_id，窗口内（300s）应被 cooldown 抑制
    clk.advance(100.0)
    fusion.on_scene_caption("画面很暗")
    after_skip = fusion.stats.triggered_total

    # 超过 300s 应可再触发
    clk.advance(300.0)
    fusion.on_scene_caption("画面很暗")
    third = fusion.stats.triggered_total

    ok = (
        first == 1
        and after_skip == 1
        and fusion.stats.cooldown_skipped >= 1
        and third == 2
    )
    _record(
        "V3 per-rule cooldown",
        ok,
        f"first={first} after_skip={after_skip} third={third} cd_skip={fusion.stats.cooldown_skipped}",
    )


# ---------------------------------------------------------------------------
# V4 全局 rate limit
# ---------------------------------------------------------------------------


def v4_rate_limit() -> None:
    clk = FakeClock(1000.0)
    fusion, _ = _make_fusion(
        clock=clk,
        rule_cooldown_s=0.0,  # 关规则 cooldown，单独检验 rate limit
        rate_limit_per_min=1,
        silence_window_s=1.0,
        idle_window_s=1.0,
    )
    # R1 第一次触发
    fusion.on_scene_caption("画面很暗")
    # 切到 R2（不同 rule_id，规则 cooldown 不挡）
    fusion.inject_user_activity(ts=clk.t - 100.0)
    clk.advance(1.0)
    fusion.on_scene_caption("移动物体经过画面")
    after_two = fusion.stats.triggered_total

    ok = (
        after_two == 1  # 第二次被 rate_limit 抑制
        and fusion.stats.rate_limit_skipped >= 1
    )
    _record(
        "V4 global rate limit 1/min",
        ok,
        f"triggered={after_two} rate_skip={fusion.stats.rate_limit_skipped}",
    )


# ---------------------------------------------------------------------------
# V5 多 rule_id 独立 cooldown
# ---------------------------------------------------------------------------


def v5_independent_cooldown() -> None:
    clk = FakeClock(1000.0)
    fusion, _ = _make_fusion(
        clock=clk,
        rule_cooldown_s=300.0,
        rate_limit_per_min=10,
        silence_window_s=1.0,
        idle_window_s=1.0,
    )
    # R1
    fusion.on_scene_caption("画面很暗")
    # 立刻 R2（不同 rule_id，独立 cooldown 互不影响）
    fusion.inject_user_activity(ts=clk.t - 100.0)
    fusion.on_scene_caption("移动物体经过画面")

    ok = (
        fusion.stats.triggered_total == 2
        and fusion.stats.per_rule.get("dark_silence") == 1
        and fusion.stats.per_rule.get("motion_greet") == 1
    )
    _record(
        "V5 independent per-rule cooldown",
        ok,
        f"per_rule={dict(fusion.stats.per_rule)}",
    )


# ---------------------------------------------------------------------------
# V6 default-OFF
# ---------------------------------------------------------------------------


def v6_default_off() -> None:
    # 确保 env 没设
    for k in (
        "COCO_MM_PROACTIVE",
        "COCO_MM_SILENCE_WINDOW_S",
        "COCO_MM_IDLE_WINDOW_S",
    ):
        os.environ.pop(k, None)
    from coco.multimodal_fusion import config_from_env
    cfg = config_from_env()
    ok_cfg = cfg.enabled is False

    # 即使 disabled，构造 + 喂 caption 也是 no-op，不抛
    fusion, emits = _make_fusion(enabled=False, silence_window_s=1.0)
    fusion.on_scene_caption("画面很暗")
    fusion.on_asr_event("final", "你好")
    fusion.on_interact_state("IDLE")
    ok_noop = fusion.stats.triggered_total == 0 and len(emits) == 0

    _record(
        "V6 default-OFF cfg + disabled no-op",
        ok_cfg and ok_noop,
        f"cfg.enabled={cfg.enabled} triggered={fusion.stats.triggered_total} emits={len(emits)}",
    )


# ---------------------------------------------------------------------------
# V7 stats counters
# ---------------------------------------------------------------------------


def v7_stats_counts() -> None:
    clk = FakeClock(1000.0)
    proactive = _make_proactive(clock=clk)
    fusion, _ = _make_fusion(
        clock=clk,
        proactive=proactive,
        rule_cooldown_s=0.0,
        rate_limit_per_min=10,
        silence_window_s=1.0,
        idle_window_s=1.0,
    )
    # 触发 R1 两次（cooldown=0，rate 放宽）
    fusion.on_scene_caption("画面很暗")
    clk.advance(2.0)
    fusion.on_scene_caption("夜色降临")
    # 触发 R2 一次
    fusion.inject_user_activity(ts=clk.t - 100.0)
    fusion.on_scene_caption("移动物体经过")

    ok = (
        fusion.stats.triggered_total == 3
        and fusion.stats.per_rule.get("dark_silence") == 2
        and fusion.stats.per_rule.get("motion_greet") == 1
        and proactive.stats.mm_triggered == 3
        and proactive.stats.mm_per_rule.get("dark_silence") == 2
        and proactive.stats.mm_per_rule.get("motion_greet") == 1
    )
    _record(
        "V7 stats counters consistent",
        ok,
        f"fusion={dict(fusion.stats.per_rule)} proactive={dict(proactive.stats.mm_per_rule)}",
    )


# ---------------------------------------------------------------------------
# V8 priority_boost
# ---------------------------------------------------------------------------


def v8_priority_boost() -> None:
    clk = FakeClock(1000.0)
    proactive = _make_proactive(clock=clk)
    # 显式把 _next_priority_boost 字段加上（vision-007 不强求 scheduler 内置；
    # 但若外部写入，MultimodalFusion 应能写穿）
    proactive._next_priority_boost = False  # noqa: SLF001
    fusion, _ = _make_fusion(
        clock=clk,
        proactive=proactive,
        rule_cooldown_s=0.0,
        silence_window_s=1.0,
    )
    fusion.on_scene_caption("画面很暗")
    ok = (
        proactive._next_priority_boost is True  # noqa: SLF001
        and fusion.stats.priority_boost_count == 1
    )
    _record(
        "V8 priority_boost written + counted",
        ok,
        f"boost_flag={proactive._next_priority_boost!r} count={fusion.stats.priority_boost_count}",  # noqa: SLF001
    )


# ---------------------------------------------------------------------------
# V9 ProactiveScheduler stop+join 干净退出（fusion 自身无线程）
# ---------------------------------------------------------------------------


def v9_stop_join_clean() -> None:
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    sched = ProactiveScheduler(
        config=ProactiveConfig(enabled=False, tick_s=0.1),
        emit_fn=lambda *a, **k: None,
    )
    stop = threading.Event()
    sched.start(stop)
    fusion, _ = _make_fusion(proactive=sched, rule_cooldown_s=0.0, silence_window_s=1.0)
    fusion.on_scene_caption("画面很暗")  # 同时写 mm_triggered
    time.sleep(0.15)
    t0 = time.monotonic()
    stop.set()
    sched.join(timeout=2.0)
    elapsed = time.monotonic() - t0
    ok = (
        sched._thread is not None  # noqa: SLF001
        and not sched._thread.is_alive()  # noqa: SLF001
        and elapsed < 2.0
        and sched.stats.mm_triggered >= 1
    )
    _record(
        "V9 stop+join clean",
        ok,
        f"elapsed={elapsed:.3f}s mm={sched.stats.mm_triggered}",
    )


# ---------------------------------------------------------------------------
# V10 与 vision-006 caption_proactive 共存不互相干扰
# ---------------------------------------------------------------------------


def v10_coexist_with_vision006() -> None:
    """同一 ProactiveScheduler 上 caption_proactive 与 mm_triggered 互不污染。"""
    clk = FakeClock(1000.0)
    proactive = _make_proactive(clock=clk)
    fusion, _ = _make_fusion(
        clock=clk,
        proactive=proactive,
        rule_cooldown_s=0.0,
        silence_window_s=1.0,
        idle_window_s=1.0,
    )
    # 模拟 vision-006 路径：SceneCaptionEmitter 直接调 record_caption_trigger
    for _ in range(3):
        proactive.record_caption_trigger("画面很暗")
    # vision-007 路径：MultimodalFusion 触发一次（不同 rule）
    fusion.on_scene_caption("画面很暗")
    fusion.inject_user_activity(ts=clk.t - 100.0)
    fusion.on_scene_caption("移动物体经过画面")

    ok = (
        proactive.stats.caption_proactive == 3
        and proactive.stats.mm_triggered == 2
        and proactive.stats.mm_per_rule.get("dark_silence") == 1
        and proactive.stats.mm_per_rule.get("motion_greet") == 1
    )
    _record(
        "V10 coexist with vision-006 caption_proactive",
        ok,
        f"caption_proactive={proactive.stats.caption_proactive} "
        f"mm={proactive.stats.mm_triggered} mm_per_rule={dict(proactive.stats.mm_per_rule)}",
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    cases = [
        v1_dark_silence,
        v2_motion_greet,
        v3_rule_cooldown,
        v4_rate_limit,
        v5_independent_cooldown,
        v6_default_off,
        v7_stats_counts,
        v8_priority_boost,
        v9_stop_join_clean,
        v10_coexist_with_vision006,
    ]
    for fn in cases:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _record(fn.__name__, False, f"raised {type(e).__name__}: {e}")

    ok_all = all(r["ok"] for r in _results)
    summary = {
        "feature": "vision-007",
        "ok": ok_all,
        "results": _results,
    }

    out_dir = ROOT / "evidence" / "vision-007"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print(
        f"[verify_vision_007] {'ALL PASS' if ok_all else 'FAILED'}"
        f" {sum(1 for r in _results if r['ok'])}/{len(_results)}"
    )
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
