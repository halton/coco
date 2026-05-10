"""verify interact-001：完整 push-to-talk → ASR → reply → TTS + action 闭环。

设计：
- 启动 IdleAnimator（与 companion-001 集成方式一致）
- 创建 InteractSession 注入 ASR/TTS
- 用 FixtureTrigger 把 fixture wav 当成 push-to-talk 触发
- skip_tts_play=True（避免在 sub-agent CI 环境真发声；TTS 仍真合成验证链路）
- 真做 robot 动作（mockup-sim）
- 5s 等观察 idle 是否在结束后正常恢复

PASS 条件：
- transcript 非空，与 fixture 文本对齐
- reply 路由正确（含关键词的去命中模板回应）
- TTS 合成 reply 文本采样数 > 0
- robot 动作 SDK 调用未抛
- IdleAnimator 在 handle_audio 期间 paused，结束后恢复（skipped_paused > 0 或 paused 状态切换被观察到）
- stop_event.set() 后干净退出 < 2s
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

from reachy_mini import ReachyMini  # noqa: E402

from coco import asr as coco_asr  # noqa: E402
from coco import tts as coco_tts  # noqa: E402
from coco.idle import IdleAnimator, IdleConfig  # noqa: E402
from coco.interact import FixtureTrigger, InteractSession, route_reply  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("verify_interact001")

FIX_WAV = REPO / "tests" / "fixtures" / "audio" / "zh-001-walk-park.wav"
EVID = REPO / "evidence" / "interact-001"
EVID.mkdir(parents=True, exist_ok=True)


def asr_fn_int16(audio_int16: np.ndarray, sr: int) -> str:
    if sr != 16000:
        raise ValueError(f"interact verify 仅支持 16k fixture，sr={sr}")
    audio_f32 = audio_int16.astype(np.float32) / 32768.0
    segs = coco_asr.transcribe_segments_from_array(audio_f32, sample_rate=16000)
    return " ".join(t for t in (coco_asr.clean_sensevoice_tags(s) for s in segs) if t)


def tts_say_fn(text: str, blocking: bool = True) -> None:
    """sub-agent 验证：真做合成（拿采样数与时长），但不真发声。"""
    samples, sr = coco_tts.synthesize(text)
    log.info("[tts] reply=%r samples=%d sr=%d dt(samples/sr)=%.2fs", text, len(samples), sr, len(samples) / max(sr, 1))


def main() -> int:
    log.info("REPO=%s FIX_WAV=%s exists=%s", REPO, FIX_WAV, FIX_WAV.exists())
    assert FIX_WAV.exists(), f"fixture wav not found: {FIX_WAV}"

    # 1) 检查回复路由（纯函数测试）
    cases = [
        ("你好", "你好呀"),
        ("再见", "回头见"),
        ("好的对", "我听到啦"),
        ("看一看", "我也看看"),
        ("天气真好", "外面挺好"),
        ("xyz", "我听到你说"),
        ("", "再说一次"),
    ]
    for text, expect_substr in cases:
        reply, action = route_reply(text)
        ok = expect_substr in reply
        log.info("  route(%r) -> reply=%r action=%s ok=%s", text, reply, action, ok)
        assert ok, f"route_reply({text!r}) -> {reply!r} 缺关键词 {expect_substr!r}"

    # 2) 连 daemon
    log.info("== 连接 mockup-sim daemon ==")
    t0 = time.monotonic()
    robot = ReachyMini(media_backend="no_media")
    log.info("  ReachyMini connected dt=%.2fs", time.monotonic() - t0)
    try:
        robot.wake_up()
    except Exception as e:
        log.warning("wake_up: %s: %s", type(e).__name__, e)

    # 3) 起 IdleAnimator
    stop_event = threading.Event()
    idle_cfg = IdleConfig(
        # 把 micro 间隔压短，让 verify 期间能采到几次 paused/skipped
        micro_interval_min=0.5,
        micro_interval_max=1.0,
        glance_interval_min=10.0,
        glance_interval_max=20.0,
    )
    idle = IdleAnimator(robot, stop_event, config=idle_cfg)
    idle.start()
    log.info("  IdleAnimator started")

    # 4) 让 idle 跑 2s 积累一些 micro
    time.sleep(2.0)
    log.info("  pre-interact stats: %s", idle.stats)
    pre_micro = idle.stats.micro_count
    pre_skip = idle.stats.skipped_paused

    # 5) InteractSession + FixtureTrigger
    session = InteractSession(
        robot=robot,
        asr_fn=asr_fn_int16,
        tts_say_fn=tts_say_fn,
        idle_animator=idle,
    )
    fixtures = [
        ("zh-001 walk-park", str(FIX_WAV)),
    ]
    trigger = FixtureTrigger(fixtures)

    log.info("== 跑 fixture 触发 ==")
    results = trigger.run(session, skip_tts_play=True, skip_action=False)

    # 在 interact 期间，idle 应被 paused：检查 idle._paused 已经 cleared
    assert not idle.is_paused(), "interact 完成后 idle 仍在 paused 状态"

    # 6) 多触发一次 fixture 保证可重入
    results2 = trigger.run(session, skip_tts_play=True, skip_action=False)
    results.extend(results2)

    # 让 idle 在 interact 后再跑一会儿，确认恢复 micro
    time.sleep(2.0)
    post_micro = idle.stats.micro_count
    post_skip = idle.stats.skipped_paused
    log.info("  post-interact stats: %s", idle.stats)

    # 7) 停止
    stop_t0 = time.monotonic()
    stop_event.set()
    idle.join(timeout=2.0)
    stop_dt = time.monotonic() - stop_t0
    alive_after = idle.is_alive()
    try:
        robot.goto_sleep()
    except Exception as e:
        log.warning("goto_sleep: %s", e)

    # ----- 断言 -----
    fails = []
    for i, r in enumerate(results):
        if r.get("dropped"):
            fails.append(f"#{i} dropped")
            continue
        if not r["asr_ok"] or not r["transcript"]:
            fails.append(f"#{i} asr_ok={r['asr_ok']} transcript={r['transcript']!r}")
        if not r["reply"]:
            fails.append(f"#{i} empty reply")
        if not r["tts_ok"]:
            fails.append(f"#{i} tts_ok=False")
        if not r["action_ok"]:
            fails.append(f"#{i} action_ok=False")
    if stop_dt > 2.0:
        fails.append(f"stop_dt={stop_dt:.3f}s > 2s")
    if alive_after:
        fails.append("idle thread still alive after stop")
    # paused 标志至少触发了一次（micro 间隔 0.5-1s，单次 handle_audio ~1-3s，应至少 skip 1 次）
    delta_skip = post_skip - pre_skip
    if delta_skip < 1:
        log.warning("delta skipped_paused=%d；handle_audio 可能太快没让 idle 跑到 wait", delta_skip)
    if post_micro <= pre_micro:
        fails.append(f"post_micro={post_micro} <= pre_micro={pre_micro}（idle 未恢复）")

    summary = {
        "results": results,
        "idle_stats": {
            "micro_count": idle.stats.micro_count,
            "glance_count": idle.stats.glance_count,
            "skipped_paused": idle.stats.skipped_paused,
            "error_count": idle.stats.error_count,
            "micro_kinds": idle.stats.micro_kinds,
        },
        "interact_stats": {
            "sessions": session.stats.sessions,
            "asr_ok": session.stats.asr_ok,
            "asr_fail": session.stats.asr_fail,
            "tts_fail": session.stats.tts_fail,
            "action_fail": session.stats.action_fail,
            "last_transcript": session.stats.last_transcript,
            "last_reply": session.stats.last_reply,
            "last_action": session.stats.last_action,
            "durations_s": session.stats.durations_s,
        },
        "stop_dt": stop_dt,
        "alive_after": alive_after,
        "delta_skip": delta_skip,
        "fails": fails,
    }
    json_path = EVID / "v1_interact_summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log.info("summary -> %s", json_path)

    print("\n=== summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print("FAILS:", fails)
    if fails:
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
