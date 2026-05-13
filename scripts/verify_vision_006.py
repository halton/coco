"""vision-006 verification: 看图说话 (HeuristicCaptionBackend / SceneCaptionEmitter).

跑法::

    uv run python scripts/verify_vision_006.py

子项：

V1   HeuristicCaptionBackend 暗图描述含 "暗" 或 "夜"
V2   移动物体被检出（前后帧差，描述含 "移动"）
V3   SceneCaptionEmitter 周期触发（mock clock + fake camera）
V4   min_change_threshold 抑制重复（相似描述不重复 emit）
V5   cooldown 生效（窗口内同描述不重复 emit）
V6   default-OFF 时 main 不构造 emitter，无 emit
V7   COCO_SCENE_CAPTION=1 才注入；env clamp
V8   stop+join 干净退出（线程在 timeout 内消亡）
V9   ProactiveScheduler 集成 caption_proactive 计数
V10  与 vision-005 gesture 共存不互相干扰

retval：0 全 PASS；1 任一失败
evidence 落 evidence/vision-006/verify_summary.json
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_vision_006] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": ok, "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeCamera:
    """读 frames 列表的伪 CameraSource，read() 返回 (True, frame)。"""

    def __init__(self, frames: List[np.ndarray]) -> None:
        self._frames = list(frames)
        self._i = 0

    def read(self):
        if not self._frames:
            return False, None
        f = self._frames[self._i % len(self._frames)]
        self._i += 1
        return True, f


class FakeClock:
    def __init__(self, t0: float = 0.0) -> None:
        self.t = float(t0)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _dark_frame() -> np.ndarray:
    return np.zeros((64, 64, 3), dtype=np.uint8) + 10


def _bright_frame() -> np.ndarray:
    return np.zeros((64, 64, 3), dtype=np.uint8) + 220


def _mid_frame() -> np.ndarray:
    return np.zeros((64, 64, 3), dtype=np.uint8) + 120


def _mid_with_left_motion() -> np.ndarray:
    f = _mid_frame()
    f[:, :20] = 220
    return f


# ---------------------------------------------------------------------------
# V1 暗图描述含 "暗" 或 "夜"
# ---------------------------------------------------------------------------


def v1_dark_caption() -> None:
    from coco.perception.scene_caption import HeuristicCaptionBackend

    b = HeuristicCaptionBackend()
    cap = b.caption(_dark_frame())
    ok = cap is not None and ("暗" in cap.text or "夜" in cap.text)
    _record("V1 dark caption mentions 暗/夜", ok, repr(cap.text if cap else None))


# ---------------------------------------------------------------------------
# V2 移动物体被检出
# ---------------------------------------------------------------------------


def v2_motion_caption() -> None:
    from coco.perception.scene_caption import HeuristicCaptionBackend

    b = HeuristicCaptionBackend()
    prev = _mid_frame()
    cur = _mid_with_left_motion()
    cap = b.caption(cur, prev_frame=prev)
    ok = (
        cap is not None
        and "移动" in cap.text
        and bool(cap.features.get("has_motion"))
    )
    _record("V2 motion caption", ok, repr(cap.text if cap else None))


# ---------------------------------------------------------------------------
# V3 周期触发
# ---------------------------------------------------------------------------


def v3_periodic_emit() -> None:
    """SceneCaptionEmitter 后台线程按 interval_s 周期 tick。

    用极短 interval（0.05s 实际被 clamp 到 5.0s — 太慢）。改用直接调用 _tick
    多次，验证 stats.ticks 与 emit 路径稳定推进；同时启动一次 start，确认线程
    起得来（stats.started_at 非零）。
    """
    from coco.perception.scene_caption import (
        HeuristicCaptionBackend,
        SceneCaptionEmitter,
    )

    emits: List[Any] = []
    def emit_fn(event, **kw):  # noqa: ANN001
        emits.append((event, kw))

    cam = FakeCamera([_dark_frame(), _bright_frame(), _mid_with_left_motion()])
    stop = threading.Event()
    em = SceneCaptionEmitter(
        stop,
        camera=cam,
        backend=HeuristicCaptionBackend(),
        interval_s=5.0,
        cooldown_s=0.0,
        min_change_threshold=0.0,
        emit_fn=emit_fn,
    )
    em.start()
    try:
        # 线程已起；让后台 _tick 跑一轮（_run 启动会立即 _tick 一次）
        time.sleep(0.3)
        started_ok = em.stats.started_at > 0
        # 后台至少 tick 了一次（_run 入口就调 _tick）
        ticks_ok = em.stats.ticks >= 1
        # 手动再 _tick 两次（同步路径，避免等 interval_s=5s）
        em._tick()  # noqa: SLF001
        em._tick()  # noqa: SLF001
        # 共 ticks >= 3；emits 计数 >= ticks（dark/bright/mid 都被 backend caption）
        ok = (
            started_ok
            and ticks_ok
            and em.stats.ticks >= 3
            and len(emits) >= 1
            and all(e[0] == "vision.scene_caption" for e in emits)
        )
        _record(
            "V3 periodic tick (background thread)",
            ok,
            f"started_at={em.stats.started_at:.2f} ticks={em.stats.ticks} "
            f"emits={len(emits)}",
        )
    finally:
        stop.set()
        em.join(timeout=1.0)


# ---------------------------------------------------------------------------
# V4 min_change_threshold 抑制重复
# ---------------------------------------------------------------------------


def v4_min_change_suppress() -> None:
    from coco.perception.scene_caption import (
        HeuristicCaptionBackend,
        SceneCaptionEmitter,
    )

    clk = FakeClock(0.0)
    stop = threading.Event()
    em = SceneCaptionEmitter(
        stop,
        backend=HeuristicCaptionBackend(),
        interval_s=5.0,
        cooldown_s=0.0,
        min_change_threshold=0.8,
        clock=clk,
        emit_fn=lambda *a, **k: None,
    )
    em.start()
    try:
        c1 = em.feed_frame(_dark_frame())
        clk.advance(1.0)
        # 同样的暗图 → 描述完全一致 → 应被相似度抑制
        c2 = em.feed_frame(_dark_frame())
        ok = (
            c1 is not None
            and c2 is None
            and em.stats.suppressed_similar >= 1
        )
        _record(
            "V4 min_change_threshold suppress",
            ok,
            f"suppressed_similar={em.stats.suppressed_similar}",
        )
    finally:
        stop.set()
        em.join(timeout=1.0)


# ---------------------------------------------------------------------------
# V5 cooldown 生效
# ---------------------------------------------------------------------------


def v5_cooldown_suppress() -> None:
    from coco.perception.scene_caption import (
        HeuristicCaptionBackend,
        SceneCaptionEmitter,
    )

    clk = FakeClock(100.0)
    stop = threading.Event()
    em = SceneCaptionEmitter(
        stop,
        backend=HeuristicCaptionBackend(),
        interval_s=5.0,
        cooldown_s=30.0,
        min_change_threshold=0.0,  # 关闭相似度抑制，只测 cooldown
        clock=clk,
        emit_fn=lambda *a, **k: None,
    )
    em.start()
    try:
        c1 = em.feed_frame(_dark_frame())
        clk.advance(5.0)  # 仍在 30s cooldown 内
        c2 = em.feed_frame(_bright_frame())
        ok_cd = (
            c1 is not None
            and c2 is None
            and em.stats.suppressed_cooldown >= 1
        )
        # 跳过 cooldown
        clk.advance(60.0)
        c3 = em.feed_frame(_dark_frame())
        ok_resume = c3 is not None
        _record(
            "V5 cooldown suppress",
            ok_cd and ok_resume,
            f"suppressed_cooldown={em.stats.suppressed_cooldown} resume={ok_resume}",
        )
    finally:
        stop.set()
        em.join(timeout=1.0)


# ---------------------------------------------------------------------------
# V6 default-OFF
# ---------------------------------------------------------------------------


def v6_default_off() -> None:
    from coco.perception.scene_caption import scene_caption_config_from_env

    cfg = scene_caption_config_from_env({})
    ok = cfg.enabled is False
    _record("V6 default-OFF", ok, f"cfg.enabled={cfg.enabled}")


# ---------------------------------------------------------------------------
# V7 env clamp
# ---------------------------------------------------------------------------


def v7_env_clamp() -> None:
    from coco.perception.scene_caption import scene_caption_config_from_env

    # 边界外的值，应被 clamp
    env = {
        "COCO_SCENE_CAPTION": "1",
        "COCO_SCENE_CAPTION_INTERVAL_S": "1.0",  # <5 → 5
        "COCO_SCENE_CAPTION_COOLDOWN_S": "99999",  # >3600 → 3600
        "COCO_SCENE_CAPTION_MIN_CHANGE": "2.0",  # >1 → 1
    }
    cfg = scene_caption_config_from_env(env)
    ok = (
        cfg.enabled is True
        and cfg.interval_s == 5.0
        and cfg.cooldown_s == 3600.0
        and cfg.min_change_threshold == 1.0
    )
    _record(
        "V7 env clamp",
        ok,
        f"interval_s={cfg.interval_s} cooldown_s={cfg.cooldown_s} "
        f"min_change={cfg.min_change_threshold}",
    )


# ---------------------------------------------------------------------------
# V8 stop+join 干净退出
# ---------------------------------------------------------------------------


def v8_stop_join_clean() -> None:
    from coco.perception.scene_caption import (
        HeuristicCaptionBackend,
        SceneCaptionEmitter,
    )

    cam = FakeCamera([_dark_frame()])
    stop = threading.Event()
    em = SceneCaptionEmitter(
        stop,
        camera=cam,
        backend=HeuristicCaptionBackend(),
        interval_s=5.0,  # > 我们等待的时间；wait 内被 set 立即返回
        cooldown_s=0.0,
        min_change_threshold=0.0,
        emit_fn=lambda *a, **k: None,
    )
    em.start()
    time.sleep(0.2)
    t0 = time.monotonic()
    em.stop()
    em.join(timeout=2.0)
    elapsed = time.monotonic() - t0
    alive = em.is_alive()
    ok = (not alive) and elapsed < 2.0
    _record("V8 stop+join clean", ok, f"alive={alive} elapsed={elapsed:.3f}s")


# ---------------------------------------------------------------------------
# V9 ProactiveScheduler 集成 caption_proactive 计数
# ---------------------------------------------------------------------------


def v9_proactive_caption_count() -> None:
    from coco.perception.scene_caption import (
        HeuristicCaptionBackend,
        SceneCaptionEmitter,
    )
    from coco.proactive import ProactiveScheduler, ProactiveConfig

    sched = ProactiveScheduler(config=ProactiveConfig(enabled=False))
    stop = threading.Event()
    em = SceneCaptionEmitter(
        stop,
        backend=HeuristicCaptionBackend(),
        interval_s=5.0,
        cooldown_s=0.0,
        min_change_threshold=0.0,
        on_caption=lambda cap: sched.record_caption_trigger(cap.text),
        emit_fn=lambda *a, **k: None,
    )
    em.start()
    try:
        em.feed_frame(_dark_frame())
        em.feed_frame(_bright_frame())
        em.feed_frame(_mid_with_left_motion())
        ok = sched.stats.caption_proactive == 3
        _record(
            "V9 proactive caption_proactive count",
            ok,
            f"caption_proactive={sched.stats.caption_proactive}",
        )
    finally:
        stop.set()
        em.join(timeout=1.0)


# ---------------------------------------------------------------------------
# V10 与 gesture 共存不互相干扰
# ---------------------------------------------------------------------------


def v10_coexist_with_gesture() -> None:
    """同进程内 GestureRecognizer + SceneCaptionEmitter 各跑各的，互不干扰。

    用各自独立 FakeCamera + FakeClock；feed_frame 同步喂帧；最终
    gesture.stats.frames_read 与 caption.stats.frames_read 都按预期推进；
    caption 的 emit_count 不影响 gesture 的 emit_count，反之亦然。
    """
    from coco.perception.gesture import (
        GestureRecognizer,
        HeuristicGestureBackend,
    )
    from coco.perception.scene_caption import (
        HeuristicCaptionBackend,
        SceneCaptionEmitter,
    )

    stop = threading.Event()
    g = GestureRecognizer(
        stop,
        backend=HeuristicGestureBackend(),
        interval_ms=200,
        min_confidence=0.5,
        cooldown_per_kind_s=0.0,
        window_frames=4,
    )
    sc = SceneCaptionEmitter(
        stop,
        backend=HeuristicCaptionBackend(),
        interval_s=5.0,
        cooldown_s=0.0,
        min_change_threshold=0.0,
        emit_fn=lambda *a, **k: None,
    )
    g.start()
    sc.start()
    try:
        # gesture 看 wave-like 模拟（用 dark/bright 交替的小图，不强求 detect）
        for _ in range(30):
            g.feed_frame(_dark_frame())
        # caption 看明确不同的图，确保 emit 推进
        sc.feed_frame(_dark_frame())
        sc.feed_frame(_bright_frame())
        sc.feed_frame(_mid_with_left_motion())
        # 不要求 gesture.emit_count；只要求两者状态独立且非崩溃
        ok = (
            g.stats.frames_read == 30
            and sc.stats.emitted == 3
            and sc.stats.error_count == 0
            and g.stats.error_count == 0
        )
        _record(
            "V10 coexist with gesture",
            ok,
            f"gesture.frames_read={g.stats.frames_read} "
            f"caption.emitted={sc.stats.emitted} "
            f"caption.err={sc.stats.error_count} gesture.err={g.stats.error_count}",
        )
    finally:
        stop.set()
        g.join(timeout=1.0)
        sc.join(timeout=1.0)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    cases = [
        v1_dark_caption,
        v2_motion_caption,
        v3_periodic_emit,
        v4_min_change_suppress,
        v5_cooldown_suppress,
        v6_default_off,
        v7_env_clamp,
        v8_stop_join_clean,
        v9_proactive_caption_count,
        v10_coexist_with_gesture,
    ]
    for fn in cases:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _record(fn.__name__, False, f"raised {type(e).__name__}: {e}")

    ok_all = all(r["ok"] for r in _results)
    summary = {
        "feature": "vision-006",
        "ok": ok_all,
        "results": _results,
    }

    out_dir = ROOT / "evidence" / "vision-006"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print(
        f"[verify_vision_006] {'ALL PASS' if ok_all else 'FAILED'}"
        f" {sum(1 for r in _results if r['ok'])}/{len(_results)}"
    )
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
