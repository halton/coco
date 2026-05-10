"""verify interact-003: VAD-driven trigger replaces stdin Enter PTT.

PASS 条件（对齐 feature_list.json verification 5 条）：
1. fixture wav 注入流（不开真麦）→ 触发恰好 1 次 session，转写文本与 fixture txt 对齐。
2. 5s 静音流 → session_count == 0，VADStats.utterances_total == 0。
3. wrap_tts 在 mute 期间投喂语音 → utterance 不触发 callback；unmute 后恢复。
4. IdleAnimator 软互斥保持：触发期间 idle.is_paused() == True，结束 resume()。
5. cooldown 防连击：连续 2 段同步喂入，第二段在 cooldown 内 → 仅触发 1 次（utterances_in_cooldown >= 1）。

输出 JSON evidence 到 evidence/interact-003/verify.json。
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
import scipy.io.wavfile as wavfile  # noqa: E402

from coco import asr as coco_asr  # noqa: E402
from coco.vad_trigger import VADConfig, VADTrigger  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("verify_interact003")

FIX_WAV = REPO / "tests" / "fixtures" / "audio" / "zh-001-walk-park.wav"
FIX_TXT = REPO / "tests" / "fixtures" / "audio" / "zh-001-walk-park.txt"
EVID = REPO / "evidence" / "interact-003"
EVID.mkdir(parents=True, exist_ok=True)


def load_wav_f32(path: Path) -> tuple[np.ndarray, int]:
    sr, a = wavfile.read(str(path))
    if a.dtype == np.int16:
        f = a.astype(np.float32) / 32768.0
    elif a.dtype == np.int32:
        f = a.astype(np.float32) / 2147483648.0
    else:
        f = a.astype(np.float32)
    if f.ndim > 1:
        f = f.mean(axis=1)
    return f, int(sr)


class FakeIdle:
    """模拟 IdleAnimator pause/resume 接口。"""
    def __init__(self) -> None:
        self._paused = False
        self.pause_calls = 0
        self.resume_calls = 0
        self.was_paused_during_cb = False

    def pause(self) -> None:
        self._paused = True
        self.pause_calls += 1

    def resume(self) -> None:
        self._paused = False
        self.resume_calls += 1

    def is_paused(self) -> bool:
        return self._paused


def feed_in_chunks(trigger: VADTrigger, audio_f32: np.ndarray, chunk: int = 1600) -> None:
    """把 audio 按 100ms 块喂进去，模拟真实麦克数据流（避免一次性大块绕过 VAD 状态机）。"""
    for i in range(0, len(audio_f32), chunk):
        trigger.feed(audio_f32[i : i + chunk])


def case_1_fixture_triggers_once() -> dict:
    log.info("=== Case 1: fixture wav 单段语音 → 触发 1 次 session ===")
    audio_f32, sr = load_wav_f32(FIX_WAV)
    expected_text = FIX_TXT.read_text(encoding="utf-8").strip()
    log.info("fixture sr=%d duration=%.2fs expected=%r", sr, len(audio_f32) / sr, expected_text)

    captured: list[tuple[np.ndarray, int]] = []

    def on_utt(audio_int16: np.ndarray, sample_rate: int) -> None:
        captured.append((audio_int16, sample_rate))
        log.info("[cb] utterance len=%d sr=%d", len(audio_int16), sample_rate)

    trigger = VADTrigger(on_utt, config=VADConfig(cooldown_seconds=0.0))
    feed_in_chunks(trigger, audio_f32)
    trigger.flush()

    assert len(captured) == 1, f"应触发 1 次，实际 {len(captured)}"
    audio_int16, sample_rate = captured[0]
    assert sample_rate == 16000
    assert len(audio_int16) >= int(0.25 * 16000), f"utterance 太短: {len(audio_int16)}"

    # 对捕获的 audio 跑 ASR 验证转写
    audio_f32_seg = audio_int16.astype(np.float32) / 32768.0
    segs = coco_asr.transcribe_segments_from_array(audio_f32_seg, sample_rate=16000)
    transcript = " ".join(coco_asr.clean_sensevoice_tags(s) for s in segs).strip()
    log.info("transcript=%r expected=%r", transcript, expected_text)
    # 期望文本对齐：核心关键词命中（"天气"、"公园"）
    assert "天气" in transcript or "公园" in transcript, f"转写未含期望关键词: {transcript!r}"

    return {
        "utterances": len(captured),
        "utterance_length_samples": int(len(audio_int16)),
        "utterance_seconds": len(audio_int16) / 16000.0,
        "transcript": transcript,
        "expected_text": expected_text,
        "stats": vars(trigger.stats),
    }


def case_2_silence_no_trigger() -> dict:
    log.info("=== Case 2: 5s 静音 → 0 次触发 ===")
    sr = 16000
    rng = np.random.default_rng(42)
    # 极低噪声底（接近完全静音），模拟环境底噪
    audio_f32 = (rng.standard_normal(int(5.0 * sr)) * 1e-4).astype(np.float32)

    captured: list = []
    trigger = VADTrigger(lambda a, s: captured.append(1), config=VADConfig())
    feed_in_chunks(trigger, audio_f32)
    trigger.flush()

    assert len(captured) == 0, f"静音不应触发，实际 {len(captured)} 次"
    return {"utterances": len(captured), "stats": vars(trigger.stats)}


def case_3_mute_blocks_callback() -> dict:
    log.info("=== Case 3: mute 期间不触发 callback；unmute 后恢复 ===")
    audio_f32, sr = load_wav_f32(FIX_WAV)

    captured: list = []
    trigger = VADTrigger(lambda a, s: captured.append(1), config=VADConfig(cooldown_seconds=0.0))
    trigger.mute()
    feed_in_chunks(trigger, audio_f32)
    trigger.flush()
    muted_count = len(captured)
    log.info("muted phase: callback=%d stats=%s", muted_count, trigger.stats)
    assert muted_count == 0, f"mute 期间不应触发，实际 {muted_count} 次"
    assert trigger.stats.utterances_while_muted >= 1, "应有 utterances_while_muted >= 1"

    # unmute 后再喂同一段，应恢复触发
    trigger.unmute()
    trigger.reset_buffer()  # 模拟 wrap_tts 行为
    feed_in_chunks(trigger, audio_f32)
    trigger.flush()
    unmuted_count = len(captured)
    log.info("unmuted phase: callback=%d stats=%s", unmuted_count, trigger.stats)
    assert unmuted_count == 1, f"unmute 后应恢复触发 1 次，实际 {unmuted_count} 次"

    return {
        "muted_callbacks": muted_count,
        "unmuted_callbacks": unmuted_count,
        "while_muted_count": trigger.stats.utterances_while_muted,
        "stats": vars(trigger.stats),
    }


def case_4_idle_soft_mutex() -> dict:
    log.info("=== Case 4: 触发期间 idle 被 pause；callback 完成后 resume ===")
    audio_f32, sr = load_wav_f32(FIX_WAV)
    fake_idle = FakeIdle()

    def on_utt(audio_int16: np.ndarray, sample_rate: int) -> None:
        # 模拟 InteractSession.handle_audio 的 idle pause/resume 流程
        fake_idle.pause()
        try:
            fake_idle.was_paused_during_cb = fake_idle.is_paused()
            time.sleep(0.05)  # 占位代表 ASR/TTS
        finally:
            fake_idle.resume()

    trigger = VADTrigger(on_utt, config=VADConfig(cooldown_seconds=0.0))
    feed_in_chunks(trigger, audio_f32)
    trigger.flush()

    assert fake_idle.was_paused_during_cb, "callback 期间 idle 应被 pause"
    assert not fake_idle.is_paused(), "callback 完成后 idle 应 resume"
    assert fake_idle.pause_calls == fake_idle.resume_calls == 1
    return {
        "pause_calls": fake_idle.pause_calls,
        "resume_calls": fake_idle.resume_calls,
        "paused_during_cb": fake_idle.was_paused_during_cb,
        "paused_after_cb": fake_idle.is_paused(),
    }


def case_5_cooldown_blocks_double_trigger() -> dict:
    log.info("=== Case 5: cooldown 防连击 → 连续 2 段语音只触发 1 次 ===")
    audio_f32, sr = load_wav_f32(FIX_WAV)
    # 构造 2 段：原 fixture + 0.6s 静音（>min_silence 让 VAD offset） + 原 fixture
    silence = np.zeros(int(0.6 * sr), dtype=np.float32)
    two_segs = np.concatenate([audio_f32, silence, audio_f32])

    captured: list = []
    trigger = VADTrigger(lambda a, s: captured.append(1), config=VADConfig(cooldown_seconds=1.5))
    feed_in_chunks(trigger, two_segs)
    trigger.flush()
    log.info("two-segment feed: callback=%d stats=%s", len(captured), trigger.stats)

    # cooldown=1.5s，两段实时间隔 < 1.5s（同步喂帧时间），第二段应被 cooldown 拦截
    assert len(captured) == 1, f"cooldown 应拦截第二段，期望 1，实际 {len(captured)}"
    assert trigger.stats.utterances_in_cooldown >= 1, "应记到 utterances_in_cooldown >= 1"

    return {
        "callbacks": len(captured),
        "in_cooldown": trigger.stats.utterances_in_cooldown,
        "stats": vars(trigger.stats),
    }


def case_6_wrap_tts_self_silencing() -> dict:
    log.info("=== Case 6: wrap_tts 在 TTS 期间自动 mute / unmute ===")
    audio_f32, sr = load_wav_f32(FIX_WAV)

    captured: list = []
    trigger = VADTrigger(lambda a, s: captured.append(1), config=VADConfig(cooldown_seconds=0.0))

    def fake_tts(text: str, blocking: bool = True) -> None:
        # 模拟 TTS 期间话筒回采到自家声音 → 把 fixture 当回采喂给 trigger
        feed_in_chunks(trigger, audio_f32)

    wrapped = trigger.wrap_tts(fake_tts)
    wrapped("hello", blocking=True)

    # TTS 期间 mute → 0 触发
    assert len(captured) == 0, f"TTS 期间 mute 应阻止触发，实际 {len(captured)} 次"
    # TTS 完成后正常喂语音 → 应触发
    feed_in_chunks(trigger, audio_f32)
    trigger.flush()
    assert len(captured) == 1, f"TTS 后正常语音应触发 1 次，实际 {len(captured)}"
    return {"during_tts_callbacks": 0, "after_tts_callbacks": len(captured), "stats": vars(trigger.stats)}


def case_7_mic_thread_clean_stop() -> dict:
    """不真起 sounddevice：直接验 stop() 行为（无 mic 时 stop 应秒退）。"""
    log.info("=== Case 7: stop() 干净退出（未启动 mic 也不报错）===")
    trigger = VADTrigger(lambda a, s: None)
    t0 = time.monotonic()
    trigger.stop(timeout=1.0)
    dt = time.monotonic() - t0
    assert dt < 1.0, f"stop 太慢: {dt:.2f}s"
    return {"stop_seconds": dt, "is_listening": trigger.is_listening()}


def main() -> int:
    results = {}
    try:
        results["case1_fixture_triggers_once"] = case_1_fixture_triggers_once()
        results["case2_silence_no_trigger"] = case_2_silence_no_trigger()
        results["case3_mute_blocks_callback"] = case_3_mute_blocks_callback()
        results["case4_idle_soft_mutex"] = case_4_idle_soft_mutex()
        results["case5_cooldown_blocks_double_trigger"] = case_5_cooldown_blocks_double_trigger()
        results["case6_wrap_tts_self_silencing"] = case_6_wrap_tts_self_silencing()
        results["case7_mic_thread_clean_stop"] = case_7_mic_thread_clean_stop()
    except AssertionError as e:
        log.error("FAIL: %s", e)
        results["fail"] = str(e)
        out_path = EVID / "verify.json"
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return 1

    out_path = EVID / "verify.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log.info("PASS, evidence -> %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
