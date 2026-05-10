"""Coco ReachyMiniApp 入口。

双模式：
- 开发：`python -m coco.main`
- UAT/发布：通过 entry-point 被 Reachy Mini Control.app 发现并启动

audio 解耦：run() 内只用 sounddevice 采麦，不调用 reachy_mini.media。
companion-001：run() 内挂 IdleAnimator 后台线程做 idle 微动 + 偶尔环顾。
interact-001：run() 内挂 stdin push-to-talk 后台线程，按 Enter 录音 N 秒
            → ASR → 模板回应 → TTS + robot 动作；与 idle 互斥。
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path
from types import FrameType

import numpy as np
import sounddevice as sd
from reachy_mini import ReachyMini, ReachyMiniApp

from coco import asr as coco_asr
from coco import tts as coco_tts
from coco.asr import transcribe_wav
from coco.idle import IdleAnimator, IdleConfig
from coco.interact import InteractSession
from coco.vad_trigger import VADTrigger, config_from_env, vad_disabled_from_env
from coco.wake_word import (
    WakeGate,
    WakeVADBridge,
    WakeWordDetector,
    config_from_env as wake_config_from_env,
    wake_word_enabled_from_env,
)


SAMPLE_RATE = 16000
BLOCK_SECONDS = 0.5
PUSH_TO_TALK_SECONDS = float(os.environ.get("COCO_PTT_SECONDS", "4.0"))
# 设 COCO_PTT_DISABLE=1 可禁用 stdin 监听（Control.app 模式 / 无 tty 环境）
PUSH_TO_TALK_DISABLED = os.environ.get("COCO_PTT_DISABLE", "0") == "1"

# audio-002 V6：主循环启动时跑一次 fixture 转写，证明 ASR 在 ReachyMiniApp
# 主进程内可用且不阻塞心跳。后台线程保证 mic loop / stop_event 检查不被卡。
ASR_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "audio" / "zh-001-walk-park.wav"


def _run_fixture_asr_once(fixture_path: Path) -> None:
    """后台线程：跑一次 transcribe_wav，结果打到 stdout。失败只 print，不抛回主线程。

    infra-debt-sweep audio-002 M1：fixture 在 ``tests/fixtures/`` 下，wheel 不打包；
    publish/Control.app 模式下文件不存在 → 直接 log warning 并 skip，不再 raise，
    避免 publish 模式下 ASR self-check 用 FileNotFoundError 污染日志或被误判失败。
    """
    if not fixture_path.exists():
        print(
            f"[coco][asr] fixture missing (publish mode?) path={fixture_path}; "
            f"skip self-check",
            flush=True,
        )
        return
    try:
        text = transcribe_wav(fixture_path)
        print(f"[coco][asr] fixture={fixture_path.name} text={text!r}", flush=True)
    except Exception as exc:  # noqa: BLE001 — 后台线程兜底，避免炸主循环
        print(f"[coco][asr] fixture transcribe failed: {exc!r}", flush=True)


def _asr_int16_fn(audio_int16: np.ndarray, sr: int) -> str:
    """interact 用：int16 16k → SenseVoice 转写并去标签。"""
    if sr != 16000:
        raise ValueError(f"interact 仅支持 16k，sr={sr}")
    audio_f32 = audio_int16.astype(np.float32) / 32768.0
    segs = coco_asr.transcribe_segments_from_array(audio_f32, sample_rate=16000)
    return " ".join(t for t in (coco_asr.clean_sensevoice_tags(s) for s in segs) if t)


def _record_int16(seconds: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """阻塞录 ``seconds`` 秒，返回 int16 mono。"""
    n = int(seconds * sample_rate)
    rec = sd.rec(n, samplerate=sample_rate, channels=1, dtype="int16")
    sd.wait()
    return rec.reshape(-1)


def _push_to_talk_loop(session: InteractSession, stop_event: threading.Event) -> None:
    """后台线程：每次 stdin 收到 Enter，录 PUSH_TO_TALK_SECONDS 秒后跑 session。

    无 tty / EOF / 异常 → 直接结束本线程，不影响主循环。
    """
    if not sys.stdin or not sys.stdin.isatty():
        print("[coco][ptt] stdin 非 tty，push-to-talk 监听跳过", flush=True)
        return
    print(f"[coco][ptt] 按 Enter 触发录音 {PUSH_TO_TALK_SECONDS:.1f}s（Ctrl-C 退出）", flush=True)
    while not stop_event.is_set():
        try:
            line = sys.stdin.readline()
        except Exception as e:  # noqa: BLE001
            print(f"[coco][ptt] stdin error: {e!r}", flush=True)
            return
        if line == "":  # EOF
            return
        if stop_event.is_set():
            return
        try:
            print(f"[coco][ptt] 录音 {PUSH_TO_TALK_SECONDS:.1f}s ...", flush=True)
            audio = _record_int16(PUSH_TO_TALK_SECONDS)
            r = session.handle_audio(audio, SAMPLE_RATE, skip_action=False, skip_tts_play=False)
            print(f"[coco][ptt] transcript={r['transcript']!r} reply={r['reply']!r} action={r['action']} dt={r['duration_s']:.2f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[coco][ptt] handle_audio failed: {e!r}", flush=True)


class Coco(ReachyMiniApp):
    # 不需要自定义 settings 页
    custom_app_url: str | None = None
    # audio 解耦：macOS 跳过 GStreamer/camera 初始化；Coco 的 audio 走 sounddevice 直连
    # 类型注解需匹配父类 ReachyMiniApp.request_media_backend: str | None
    request_media_backend: str | None = "no_media"

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        block_frames = int(SAMPLE_RATE * BLOCK_SECONDS)

        # audio-002 V6：把 ASR 一次性 fixture 验证放后台线程，避免阻塞心跳/stop_event
        asr_thread = threading.Thread(
            target=_run_fixture_asr_once,
            args=(ASR_FIXTURE_PATH,),
            name="coco-asr-fixture",
            daemon=True,
        )
        asr_thread.start()

        # companion-001：起 idle 动画后台线程。共用 stop_event；动作经 robot-002 的安全幅度封装。
        # 失败/异常只 log，不影响 mic loop 或主退出。
        idle_animator: IdleAnimator | None = None
        try:
            try:
                reachy_mini.wake_up()
            except Exception as exc:  # noqa: BLE001
                print(f"[coco][idle] wake_up failed (continuing without): {exc!r}", flush=True)
            idle_animator = IdleAnimator(reachy_mini, stop_event, config=IdleConfig())
            idle_animator.start()
            print("[coco][idle] IdleAnimator started", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[coco][idle] start failed: {exc!r}", flush=True)

        # interact-001：起 InteractSession + push-to-talk stdin 后台线程
        # interact-002：注入 LLM client（环境变量未配则自动 fallback 到 KEYWORD_ROUTES）
        # interact-003：默认改用 VAD trigger 替代 stdin Enter；COCO_VAD_DISABLE=1 回退 PTT
        # interact-005：可选 wake-word 前置 KWS（COCO_WAKE_WORD=1 启用）；唤醒后开 6s
        #             awake 窗口，窗口外的 VAD utterance 被 awake gate 丢弃，向后兼容
        #             interact-003 默认行为（COCO_WAKE_WORD 默认关）。
        ptt_thread: threading.Thread | None = None
        vad_trigger: VADTrigger | None = None
        wake_detector: WakeWordDetector | None = None
        wake_bridge: WakeVADBridge | None = None
        try:
            from coco.llm import build_default_client as _build_llm
            _llm = _build_llm()
            if _llm.backend.name == "fallback":
                print("[coco][llm] backend=fallback (未配 COCO_LLM_BACKEND，使用关键词路由)", flush=True)
            else:
                print(f"[coco][llm] backend={_llm.backend.name} timeout={_llm.timeout}s", flush=True)
            session = InteractSession(
                robot=reachy_mini,
                asr_fn=_asr_int16_fn,
                tts_say_fn=coco_tts.say,
                idle_animator=idle_animator,
                llm_reply_fn=_llm.reply,
            )

            use_vad = (not PUSH_TO_TALK_DISABLED) and (not vad_disabled_from_env())
            if use_vad:
                # interact-003: 用 VAD 取代 stdin Enter；session.tts_say_fn 包一层 mute 防自激
                vad_cfg = config_from_env()

                def _vad_on_utterance(audio_int16: np.ndarray, sr: int) -> None:
                    r = session.handle_audio(audio_int16, sr, skip_action=False, skip_tts_play=False)
                    print(
                        f"[coco][vad] transcript={r['transcript']!r} reply={r['reply']!r} "
                        f"action={r['action']} dt={r['duration_s']:.2f}s",
                        flush=True,
                    )

                vad_trigger = VADTrigger(_vad_on_utterance, config=vad_cfg)
                # 包一层 tts_say_fn：TTS 期间 mute，避免自家声音被回采再次触发
                session.tts_say_fn = vad_trigger.wrap_tts(session.tts_say_fn)

                # interact-005: 可选 wake-word 前置 KWS。COCO_WAKE_WORD=1 启用。
                # 启用时：把 vad_trigger.on_utterance 包到 WakeVADBridge.vad_gate_callback，
                # awake 窗口外丢弃 utterance；并把 wake_detector.feed 串在 vad_trigger.feed
                # 之前（共享同一份 sounddevice 流，避免双开 InputStream 抢设备）。
                if wake_word_enabled_from_env():
                    try:
                        wake_cfg = wake_config_from_env()
                        wake_detector = WakeWordDetector(
                            on_wake=lambda t: print(
                                f"[coco][wake] hit {t!r}; awake for "
                                f"{wake_cfg.window_seconds:.1f}s",
                                flush=True,
                            ),
                            config=wake_cfg,
                        )
                        wake_gate = WakeGate(window_seconds=wake_cfg.window_seconds)
                        wake_bridge = WakeVADBridge(wake_detector, wake_gate, _vad_on_utterance)
                        wake_bridge.bind_vad(vad_trigger)
                        # 关键替换：vad_trigger 的真 callback 改为 bridge.vad_gate_callback
                        vad_trigger.on_utterance = wake_bridge.vad_gate_callback
                        # 共享流：拦截 vad_trigger.feed，让样本先喂 wake，再走 vad
                        _orig_vad_feed = vad_trigger.feed

                        def _shared_feed(samples_f32, _orig=_orig_vad_feed,
                                         _wake=wake_detector) -> None:
                            _wake.feed(samples_f32)
                            _orig(samples_f32)

                        vad_trigger.feed = _shared_feed  # type: ignore[assignment]
                        # TTS 期间也 mute KWS（与 vad_trigger.wrap_tts 同步）
                        _orig_mute = vad_trigger.mute
                        _orig_unmute = vad_trigger.unmute

                        def _mute_both(_o=_orig_mute, _w=wake_detector) -> None:
                            _o(); _w.mute()

                        def _unmute_both(_o=_orig_unmute, _w=wake_detector) -> None:
                            _o(); _w.unmute(); _w.reset_buffer()

                        vad_trigger.mute = _mute_both    # type: ignore[assignment]
                        vad_trigger.unmute = _unmute_both  # type: ignore[assignment]
                        print(
                            f"[coco][wake] wake-word enabled: keywords="
                            f"{wake_cfg.keywords} threshold={wake_cfg.threshold} "
                            f"window={wake_cfg.window_seconds}s",
                            flush=True,
                        )
                    except FileNotFoundError as exc:
                        print(
                            f"[coco][wake] init failed (model missing): {exc!r}; "
                            f"continuing without wake-word",
                            flush=True,
                        )
                        wake_detector = None
                        wake_bridge = None
                vad_trigger.start_microphone()
                print(
                    f"[coco][vad] VAD trigger started (threshold={vad_cfg.threshold} "
                    f"cooldown={vad_cfg.cooldown_seconds}s min_speech={vad_cfg.min_speech_seconds}s)",
                    flush=True,
                )
            elif not PUSH_TO_TALK_DISABLED:
                ptt_thread = threading.Thread(
                    target=_push_to_talk_loop,
                    args=(session, stop_event),
                    name="coco-push-to-talk",
                    daemon=True,
                )
                ptt_thread.start()
                print("[coco][ptt] push-to-talk listener started (COCO_VAD_DISABLE=1)", flush=True)
            else:
                print("[coco][ptt] disabled by COCO_PTT_DISABLE=1", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[coco][ptt] init failed: {exc!r}", flush=True)

        try:
            if vad_trigger is not None:
                # interact-003: VADTrigger 已经开了一路 sounddevice InputStream；
                # 主循环不再开第二路（避免设备争抢），只做 stop_event 心跳。
                while not stop_event.is_set():
                    time.sleep(0.5)
            else:
                # sounddevice 直连本机麦：与 daemon 的 audio backend / media 无耦合。
                with sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    blocksize=block_frames,
                ) as mic:
                    while not stop_event.is_set():
                        data, _overflow = mic.read(block_frames)
                        rms = float(np.sqrt(np.mean(np.square(data))))
                        print(f"[coco] rms={rms:.4f}", flush=True)
                        # 让出循环，给 stop_event 检查机会。
                        time.sleep(0.05)
        finally:
            # 确保 idle 线程退出干净；stop_event 已被外部或本循环 set
            if idle_animator is not None:
                stop_event.set()
                idle_animator.join(timeout=2.0)
                if idle_animator.is_alive():
                    print("[coco][idle] WARN: animator did not stop within 2s", flush=True)
                else:
                    print(f"[coco][idle] stopped stats={idle_animator.stats}", flush=True)
            # ptt_thread 是 daemon，stop_event 一 set 它的下一次 readline 返回前可能还在阻塞，
            # 但它是 daemon 线程，进程退出时会被回收；最多等 1s 让它响应 stop_event。
            if ptt_thread is not None:
                ptt_thread.join(timeout=1.0)
            # interact-003: 停 VAD 麦克线程（如已启动）
            if vad_trigger is not None:
                vad_trigger.stop(timeout=1.5)
            # interact-005: 停 wake detector mic（仅在独立 mic 模式才有；当前共享流模式下无）
            if wake_detector is not None and wake_detector.is_listening():
                wake_detector.stop(timeout=1.5)


def main() -> None:
    app = Coco()

    def _graceful_stop(signum: int, _frame: FrameType | None) -> None:
        # 让 wrapped_run 内的 stop_event.wait() 返回，run() 循环看到 stop_event 后退出
        app.logger.info(f"Received signal {signum}, stopping gracefully")
        app.stop()

    signal.signal(signal.SIGINT, _graceful_stop)
    signal.signal(signal.SIGTERM, _graceful_stop)

    app.wrapped_run()


if __name__ == "__main__":
    main()
