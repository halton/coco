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
from coco.config import load_config, config_summary
from coco.idle import IdleAnimator, IdleConfig
from coco.interact import InteractSession
from coco.logging_setup import setup_logging, emit
from coco.power_state import (
    PowerState,
    PowerStateMachine,
    config_from_env as power_config_from_env,
    power_idle_enabled_from_env,
)
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


def _face_presence_watcher(
    face_tracker,
    power_state: "PowerStateMachine",
    stop_event: threading.Event,
    *,
    period: float = 0.5,
) -> None:
    """companion-003 L0-1: 监听 face presence 边沿（False→True），rising-edge 调
    ``power_state.record_interaction(source="face")``。

    独立于 IdleAnimator —— 后者在 SLEEP 下早早 ``continue``，永远观察不到
    face 出现，spec verification 第 2 条的 "face 唤醒 SLEEP" 就靠不住。
    本 watcher 是独立 daemon thread，无视 power_state 当前态，每 ``period``
    秒读一次 ``face_tracker.latest().present``，捕到 False→True 立刻 fire。

    任何异常都吞掉只 log，绝不让线程崩溃；stop_event set 后下一轮 wait 退出。
    """
    if face_tracker is None or power_state is None:
        return
    last_present = False
    while not stop_event.wait(timeout=period):
        try:
            snap = face_tracker.latest()
            present = bool(getattr(snap, "present", False))
        except Exception as exc:  # noqa: BLE001
            print(f"[coco][power] face watcher read failed: {exc!r}", flush=True)
            continue
        if present and not last_present:
            try:
                power_state.record_interaction(source="face")
                print("[coco][power] face rising-edge -> wake", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[coco][power] face record_interaction failed: {exc!r}", flush=True)
        last_present = present


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
        # infra-002: 单点 load_config + setup_logging。banner 写 config_summary（无 secret）。
        # 默认 jsonl=False、INFO；不改任何 phase-3 默认行为。COCO_LOG_JSONL=1 启用 jsonl。
        # infra-004: load_config 内置 validate_config；error → ConfigValidationError；
        # 启动 banner 改用 coco.banner.render_banner + emit("startup.banner")。
        try:
            _coco_cfg = load_config()
            setup_logging(jsonl=_coco_cfg.log.jsonl, level=_coco_cfg.log.level)
            # L1-3：把 cfg.ptt.* 写回模块级变量，避免"两套 PTT 真值源"。
            global PUSH_TO_TALK_SECONDS, PUSH_TO_TALK_DISABLED
            PUSH_TO_TALK_SECONDS = float(_coco_cfg.ptt.seconds)
            PUSH_TO_TALK_DISABLED = bool(_coco_cfg.ptt.disabled)
            try:
                from coco.banner import render_banner, banner_payload
                _banner_text = render_banner(_coco_cfg)
                print(_banner_text, flush=True)
                try:
                    emit("startup.banner", component="startup", **banner_payload(_coco_cfg))
                except Exception:  # noqa: BLE001
                    pass
            except Exception as _be:  # noqa: BLE001
                # banner 失败不阻断；回落到旧 config_summary 单行 print
                import json as _json
                print(
                    f"[coco][config] " + _json.dumps(config_summary(_coco_cfg), ensure_ascii=False),
                    flush=True,
                )
                print(f"[coco][banner] render failed: {_be!r}", flush=True)
        except Exception as _e:  # noqa: BLE001
            # infra-004 L2: ConfigValidationError 必须干净退出，不能"continuing" — 否则
            # setup_logging 没跑、cfg 未定义，下游 import / 读 _coco_cfg 会 NameError 半启动。
            from coco.config import ConfigValidationError as _CVE
            if isinstance(_e, _CVE):
                print(
                    f"[coco][config] FATAL: config validation failed: {_e}",
                    file=sys.stderr,
                    flush=True,
                )
                sys.exit(2)
            print(f"[coco][config] load_config/setup_logging failed (continuing): {_e!r}", flush=True)

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
        # companion-003：可选挂 PowerStateMachine（COCO_POWER_IDLE=1 启用）。drowsy 时
        #             idle interval 自动放大；sleep 时 idle 跳过 micro/glance + 调
        #             robot.goto_sleep()；wake 事件（wake-word/face/interact）调
        #             robot.wake_up() 并回 active。默认 OFF 保持 companion-002 行为不变。
        idle_animator: IdleAnimator | None = None
        power_state: PowerStateMachine | None = None

        # interact-007 L1-1: 集中构造 face_tracker（COCO_FACE_TRACK=1 启用），
        # 同一实例供 power presence watcher 与 ProactiveScheduler 共用。
        # 默认 OFF：不构造 → power watcher 直接 skip，proactive 因 face_tracker=None
        # 在 _should_trigger 内被判 "no_face"（保护性默认），三方行为完全向后兼容。
        _face_tracker_shared = None
        try:
            if os.environ.get("COCO_FACE_TRACK", "0") == "1":
                from coco.perception.face_tracker import FaceTracker as _FaceTracker
                _spec = os.environ.get("COCO_CAMERA")
                if _spec:
                    _face_tracker_shared = _FaceTracker(stop_event, camera_spec=_spec)
                    _face_tracker_shared.start()
                    print(
                        f"[coco][face] FaceTracker started camera={_spec!r}",
                        flush=True,
                    )
                else:
                    print(
                        "[coco][face] COCO_FACE_TRACK=1 但 COCO_CAMERA 未设；FaceTracker 跳过构造",
                        flush=True,
                    )
        except Exception as exc:  # noqa: BLE001
            print(f"[coco][face] FaceTracker init failed: {exc!r}", flush=True)
            _face_tracker_shared = None

        # vision-004: AttentionSelector — 多目标人脸注视切换。
        # 默认 OFF；仅在 COCO_ATTENTION=1 且 FaceTracker 已构造时启动。
        # focus 变化时 emit "vision.attention_changed"（component "vision"）。
        _attention_selector = None
        _attention_thread: threading.Thread | None = None
        _attention_stop = threading.Event()
        try:
            if (
                os.environ.get("COCO_ATTENTION", "0") == "1"
                and _face_tracker_shared is not None
            ):
                from coco.config import _attention_from_env  # type: ignore
                from coco.perception.attention import (
                    AttentionPolicy,
                    AttentionSelector,
                )

                _att_cfg = _attention_from_env(os.environ)

                def _on_attention_change(prev, curr):  # noqa: ANN001
                    try:
                        emit(
                            "vision.attention_changed",
                            component="vision",
                            prev_track_id=(prev.track_id if prev else None),
                            prev_name=(prev.name if prev else None),
                            target_track_id=(curr.track_id if curr else None),
                            target_name=(curr.name if curr else None),
                            policy=_att_cfg.policy,
                        )
                    except Exception:  # noqa: BLE001
                        pass

                _attention_selector = AttentionSelector(
                    policy=AttentionPolicy(_att_cfg.policy),
                    min_focus_s=_att_cfg.min_focus_s,
                    switch_cooldown_s=_att_cfg.switch_cooldown_s,
                    on_change=_on_attention_change,
                )

                def _attention_loop(
                    sel=_attention_selector,
                    tracker=_face_tracker_shared,
                    stop_evt=_attention_stop,
                    outer_stop=stop_event,
                    interval_s=max(0.05, _att_cfg.interval_ms / 1000.0),
                ):
                    while not stop_evt.is_set() and not outer_stop.is_set():
                        try:
                            snap = tracker.latest()
                            sel.select(list(snap.tracks))
                            # companion-006: 把当前 focus 的 name 喂给 switcher
                            cur = sel.current()
                            cur_name = (cur.name if cur else None)
                            # 通过 attribute on selector 上挂 switcher 引用（main 段
                            # 装配后会 set），避免 closure 早绑定问题。
                            pw = getattr(sel, "_coco_profile_switcher", None)
                            if pw is not None:
                                try:
                                    pw.observe(cur_name)
                                except Exception as _e:  # noqa: BLE001
                                    print(
                                        f"[coco][attention] switcher.observe failed: {_e!r}",
                                        flush=True,
                                    )
                        except Exception as e:  # noqa: BLE001
                            print(f"[coco][attention] tick failed: {e!r}", flush=True)
                        if stop_evt.wait(timeout=interval_s):
                            break

                _attention_thread = threading.Thread(
                    target=_attention_loop,
                    name="coco-attention",
                    daemon=True,
                )
                _attention_thread.start()
                print(
                    f"[coco][attention] AttentionSelector started policy={_att_cfg.policy} "
                    f"min_focus_s={_att_cfg.min_focus_s} cooldown_s={_att_cfg.switch_cooldown_s} "
                    f"interval_ms={_att_cfg.interval_ms}",
                    flush=True,
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[coco][attention] init failed: {exc!r}", flush=True)
            _attention_selector = None
            _attention_thread = None

        # vision-005: GestureRecognizer — 简易手势识别（sim-only）。
        # 默认 OFF；仅在 COCO_GESTURE=1 且 COCO_CAMERA 已设时启动。
        # 命中（含 cooldown / min_confidence 过滤）时 emit "vision.gesture_detected"
        # （component "vision"）；同时由本会话内的"行为侧 handler"按 kind 分发：
        #   - WAVE       → look_left 一下（glance） + tts.say_async("你好")
        #   - THUMBS_UP  → ExpressionPlayer.play("excited")（'praise' 的近义；库内无 praise）
        #   - NOD/SHAKE/HEART → 仅记录，不主动发声/动作（避免误判扰民）
        # 行为侧再加 per-kind 30s cooldown（与 backend 的 detect cooldown 解耦）。
        # 先把 _expression_player 占位为 None；下方 robot-003 段落可能赋真实实例。
        # 闭包按名字延迟解析（cellvar），handler 在运行时读最新值，因此这里仅
        # 需保证名字存在，避免 NameError。
        _expression_player = None
        _gesture_recognizer = None
        _gesture_behavior_last_ts: dict[str, float] = {}
        _GESTURE_BEHAVIOR_COOLDOWN_S = 30.0
        try:
            if os.environ.get("COCO_GESTURE", "0") == "1":
                from coco.perception.camera_source import open_camera as _open_cam
                from coco.perception.gesture import (
                    GestureRecognizer,
                    HeuristicGestureBackend,
                    gesture_config_from_env,
                )

                _gesture_cfg = gesture_config_from_env(os.environ)
                _gesture_spec = os.environ.get("COCO_CAMERA")
                if not _gesture_spec:
                    print(
                        "[coco][gesture] COCO_GESTURE=1 但 COCO_CAMERA 未设；GestureRecognizer 跳过构造",
                        flush=True,
                    )
                else:
                    _gesture_cam = _open_cam(_gesture_spec)

                    def _gesture_behavior_handler(lbl, _r=reachy_mini):  # noqa: ANN001
                        """vision-005 闭环：根据 kind 触发 tts/glance/expression。

                        额外一层 30s/kind 行为冷却：backend cooldown 控"再次检出"，
                        本 cooldown 控"再次发声/动头"，两者分离，避免 backend 调小时
                        闭环行为被刷屏。
                        """
                        try:
                            kind = lbl.kind.value
                        except Exception:  # noqa: BLE001
                            kind = "unknown"
                        now = time.monotonic()
                        last = _gesture_behavior_last_ts.get(kind)
                        if last is not None and (now - last) < _GESTURE_BEHAVIOR_COOLDOWN_S:
                            print(
                                f"[coco][gesture] behavior suppressed (cooldown) kind={kind}",
                                flush=True,
                            )
                            return
                        _gesture_behavior_last_ts[kind] = now
                        try:
                            if kind == "wave":
                                # 看一下 + 打招呼。glance 用 look_left（短促 0.4s 回中），
                                # tts 用 say_async 不阻塞 main loop / event 线程。
                                try:
                                    from coco.actions import look_left as _look_left
                                    _look_left(_r, duration=0.4, return_to_center=True)
                                except Exception as e:  # noqa: BLE001
                                    print(f"[coco][gesture] glance failed: {e!r}", flush=True)
                                try:
                                    coco_tts.say_async("你好")
                                except Exception as e:  # noqa: BLE001
                                    print(f"[coco][gesture] tts say_async failed: {e!r}", flush=True)
                                print("[coco][gesture] WAVE → glance + 你好", flush=True)
                            elif kind == "thumbs_up":
                                # 库内无 'praise' expression（见 robot/expressions.py
                                # EXPRESSION_LIBRARY），用语义近似的 'excited' 替代。
                                if _expression_player is not None:
                                    try:
                                        _expression_player.play("excited")
                                    except Exception as e:  # noqa: BLE001
                                        print(
                                            f"[coco][gesture] expression play(excited) failed: {e!r}",
                                            flush=True,
                                        )
                                else:
                                    print(
                                        "[coco][gesture] THUMBS_UP detected but ExpressionPlayer 未启用 (COCO_EXPRESSIONS=1?)",
                                        flush=True,
                                    )
                                print("[coco][gesture] THUMBS_UP → expression(excited)", flush=True)
                            else:
                                # NOD/SHAKE/HEART：仅记录
                                print(f"[coco][gesture] {kind} detected (no behavior wired)", flush=True)
                        except Exception as e:  # noqa: BLE001
                            print(f"[coco][gesture] behavior handler crashed: {e!r}", flush=True)

                    def _on_gesture(lbl):  # noqa: ANN001
                        try:
                            emit(
                                "vision.gesture_detected",
                                component="vision",
                                kind=lbl.kind.value,
                                confidence=float(lbl.confidence),
                                bbox=list(lbl.bbox) if lbl.bbox is not None else None,
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        # 闭环：emit 之后再触发行为，确保 evidence/事件先落
                        _gesture_behavior_handler(lbl)

                    _gesture_recognizer = GestureRecognizer(
                        stop_event,
                        camera=_gesture_cam,
                        backend=HeuristicGestureBackend(),
                        interval_ms=_gesture_cfg.interval_ms,
                        min_confidence=_gesture_cfg.min_confidence,
                        cooldown_per_kind_s=_gesture_cfg.cooldown_per_kind_s,
                        window_frames=_gesture_cfg.window_frames,
                        on_gesture=_on_gesture,
                    )
                    _gesture_recognizer.start()
                    print(
                        f"[coco][gesture] GestureRecognizer started camera={_gesture_spec!r} "
                        f"interval_ms={_gesture_cfg.interval_ms} min_conf={_gesture_cfg.min_confidence} "
                        f"cooldown_s={_gesture_cfg.cooldown_per_kind_s} window={_gesture_cfg.window_frames}",
                        flush=True,
                    )
        except Exception as exc:  # noqa: BLE001
            print(f"[coco][gesture] init failed: {exc!r}", flush=True)
            _gesture_recognizer = None

        try:
            try:
                reachy_mini.wake_up()
            except Exception as exc:  # noqa: BLE001
                print(f"[coco][idle] wake_up failed (continuing without): {exc!r}", flush=True)
            if power_idle_enabled_from_env():
                try:
                    pcfg = power_config_from_env()
                    power_state = PowerStateMachine(config=pcfg)

                    def _on_sleep(_psm: PowerStateMachine, _r=reachy_mini) -> None:
                        print(f"[coco][power] -> sleep, calling goto_sleep()", flush=True)
                        try:
                            emit("power.transition", from_state="drowsy", to_state="sleep", source="tick")
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            _r.goto_sleep()
                        except Exception as e:  # noqa: BLE001
                            print(f"[coco][power] goto_sleep failed: {e!r}", flush=True)

                    def _on_active(_psm: PowerStateMachine, prev: PowerState, _r=reachy_mini) -> None:
                        try:
                            emit("power.transition", from_state=prev.value, to_state="active", source="interaction")
                        except Exception:  # noqa: BLE001
                            pass
                        if prev == PowerState.SLEEP:
                            print(f"[coco][power] sleep -> active, calling wake_up()", flush=True)
                            try:
                                _r.wake_up()
                            except Exception as e:  # noqa: BLE001
                                print(f"[coco][power] wake_up failed: {e!r}", flush=True)
                        else:
                            print(f"[coco][power] {prev.value} -> active", flush=True)

                    def _on_drowsy(_psm: PowerStateMachine) -> None:
                        print(f"[coco][power] -> drowsy (interval x{pcfg.drowsy_micro_scale})", flush=True)

                    power_state.on_enter_sleep = _on_sleep
                    power_state.on_enter_active = _on_active
                    power_state.on_enter_drowsy = _on_drowsy
                    power_state.start_driver(stop_event)
                    print(
                        f"[coco][power] enabled drowsy_after={pcfg.drowsy_after}s "
                        f"sleep_after={pcfg.sleep_after}s scale={pcfg.drowsy_micro_scale}x",
                        flush=True,
                    )
                    # companion-003 L0-1: 起 face presence watcher（rising-edge → wake）。
                    # interact-007 L1-1: face_tracker 构造前移到此（_init_face_tracker），
                    # 同一实例同时供 power watcher 和 ProactiveScheduler 使用，
                    # 避免 Reviewer 指出的 "scheduler 拿到 face_tracker=None" 死锁。
                    # 未来 face tracker 注入由 _init_face_tracker_for_app() 集中决定。
                    if _face_tracker_shared is not None:
                        threading.Thread(
                            target=_face_presence_watcher,
                            args=(_face_tracker_shared, power_state, stop_event),
                            name="coco-power-face-watcher",
                            daemon=True,
                        ).start()
                    else:
                        print(
                            "[coco][power] face watcher skipped (face_tracker not constructed; "
                            "set COCO_FACE_TRACK=1 to enable)",
                            flush=True,
                        )
                except Exception as exc:  # noqa: BLE001
                    print(f"[coco][power] init failed: {exc!r}", flush=True)
                    power_state = None
            # companion-005: 可选 situational idle modulator（默认 OFF）
            _sit_modulator = None
            try:
                from coco.companion.situational_idle import (
                    situational_idle_enabled_from_env as _sit_enabled,
                    situational_idle_config_from_env as _sit_cfg_from_env,
                    SituationalIdleModulator as _SitModulator,
                )
                from coco.logging_setup import emit as _emit
                if _sit_enabled():
                    _scfg = _sit_cfg_from_env()
                    def _sit_emit_cb(prev, curr, sit, _e=_emit):
                        try:
                            _e(
                                "companion.idle_situation_changed",
                                micro_amp_scale=curr.micro_amp_scale,
                                glance_prob_scale=curr.glance_prob_scale,
                                glance_amp_scale=curr.glance_amp_scale,
                                face_present=sit.face_present,
                                focus_stable_s=sit.focus_stable_s,
                                time_since_interaction_s=sit.time_since_interaction_s,
                                power_state=sit.power_state,
                                emotion=sit.emotion,
                                profile_has_interests=sit.profile_has_interests,
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    _sit_modulator = _SitModulator(
                        config=_scfg,
                        power_state=power_state,
                        face_tracker=_face_tracker_shared,
                        attention_selector=_attention_selector,
                        emotion_tracker=None,
                        profile_store=None,
                        emit_cb=_sit_emit_cb,
                    )
                    print(
                        f"[coco][sit_idle] enabled focus_stable={_scfg.focus_stable_threshold_s}s "
                        f"recent={_scfg.interaction_recent_s}s stale={_scfg.interaction_stale_s}s",
                        flush=True,
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"[coco][sit_idle] init failed: {exc!r}", flush=True)
                _sit_modulator = None
            idle_animator = IdleAnimator(
                reachy_mini, stop_event, config=IdleConfig(), power_state=power_state,
                situational_modulator=_sit_modulator,
            )
            idle_animator.start()
            print("[coco][idle] IdleAnimator started", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[coco][idle] start failed: {exc!r}", flush=True)

        # robot-003: 可选 ExpressionPlayer（COCO_EXPRESSIONS=1 启用，默认 OFF）。
        # 注入 tts 模块（say(expression=...) 自动触发）+ 后续 ProactiveScheduler。
        _expression_player = None
        try:
            from coco.robot.expressions import (
                ExpressionPlayer as _ExpressionPlayer,
                expressions_config_from_env as _expr_from_env,
            )
            _ecfg = _expr_from_env()
            if _ecfg.enabled and reachy_mini is not None:
                _expression_player = _ExpressionPlayer(
                    reachy_mini,
                    idle_animator=idle_animator,
                    config=_ecfg,
                )
                coco_tts.set_expression_player(_expression_player)
                print(
                    f"[coco][expr] ExpressionPlayer enabled speed={_ecfg.global_speed_scale} "
                    f"cooldown_default={_ecfg.cooldown_default_s}s",
                    flush=True,
                )
            else:
                print("[coco][expr] disabled (COCO_EXPRESSIONS not set)", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[coco][expr] init failed: {exc!r}", flush=True)
            _expression_player = None

        # robot-004: 可选 PostureBaselineModulator（COCO_POSTURE_BASELINE=1 启用，默认 OFF）。
        # 必须在 IdleAnimator + ExpressionPlayer 构造之后；通过 setattr 反向注入引用。
        # 需要一个 EmotionTracker 实例 — 若 COCO_EMOTION 启用且尚未构造则共享一个，
        # 同时把同一 tracker 也传给后面的 InteractSession（见下方 _shared_emotion_tracker）。
        _posture_baseline = None
        _shared_emotion_tracker = None
        try:
            from coco.robot.posture_baseline import (
                PostureBaselineModulator as _PostureBM,
                posture_baseline_config_from_env as _pb_cfg_from_env,
            )
            from coco.emotion import (
                EmotionTracker as _EmotionTracker,
                emotion_enabled_from_env as _emo_enabled,
                config_from_env as _emo_cfg_from_env,
            )
            _pb_cfg = _pb_cfg_from_env()
            if _pb_cfg.enabled and reachy_mini is not None:
                # baseline 启用 → 必须有 EmotionTracker（即使 COCO_EMOTION 未设也构造一个）
                _emo_cfg = _emo_cfg_from_env()
                _shared_emotion_tracker = _EmotionTracker(decay_s=_emo_cfg.decay_s)
                _posture_baseline = _PostureBM(
                    robot=reachy_mini,
                    emotion_tracker=_shared_emotion_tracker,
                    power_state=power_state,
                    config=_pb_cfg,
                    emit_fn=emit,
                )
                # 反向注入：让 IdleAnimator 在 _micro_head/_breathe 中叠加 baseline，
                # 让 ExpressionPlayer 在 play 期间 pause baseline 天线下发。
                if idle_animator is not None:
                    idle_animator.posture_baseline = _posture_baseline
                if _expression_player is not None:
                    _expression_player.posture_baseline = _posture_baseline
                _posture_baseline.start(stop_event)
                print(
                    f"[coco][posture] PostureBaselineModulator enabled "
                    f"ramp={_pb_cfg.ramp_s:.1f}s tick={_pb_cfg.tick_interval_s:.2f}s "
                    f"debounce={_pb_cfg.debounce_s:.1f}s",
                    flush=True,
                )
            else:
                print("[coco][posture] disabled (COCO_POSTURE_BASELINE not set)", flush=True)
                # 若 baseline 关但 COCO_EMOTION 启用，仍构造 tracker 给 InteractSession
                if _emo_enabled():
                    _emo_cfg = _emo_cfg_from_env()
                    _shared_emotion_tracker = _EmotionTracker(decay_s=_emo_cfg.decay_s)
        except Exception as exc:  # noqa: BLE001
            print(f"[coco][posture] init failed: {exc!r}", flush=True)
            _posture_baseline = None

        # robot-004 helper: 共享 emotion tracker 时也按需构造 detector（feed transcript）。
        def _build_emotion_detector_for_session():  # noqa: ANN202
            try:
                from coco.emotion import EmotionDetector as _ED
                return _ED()
            except Exception as exc:  # noqa: BLE001
                print(f"[coco][emotion] detector build failed: {exc!r}", flush=True)
                return None

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
            from coco.dialog import (
                DialogMemory,
                config_from_env as dialog_config_from_env,
                dialog_memory_enabled_from_env,
            )
            _llm = _build_llm()
            if _llm.backend.name == "fallback":
                print("[coco][llm] backend=fallback (未配 COCO_LLM_BACKEND，使用关键词路由)", flush=True)
            else:
                print(f"[coco][llm] backend={_llm.backend.name} timeout={_llm.timeout}s", flush=True)

            # interact-004: 可选 multi-turn dialog memory（默认 OFF，向后兼容）
            _dialog_memory: DialogMemory | None = None
            if dialog_memory_enabled_from_env():
                _dcfg = dialog_config_from_env()
                _dialog_memory = DialogMemory(
                    max_turns=_dcfg.max_turns,
                    idle_timeout_s=_dcfg.idle_timeout_s,
                )
                print(
                    f"[coco][dialog] memory enabled max_turns={_dcfg.max_turns} "
                    f"idle_timeout={_dcfg.idle_timeout_s:.0f}s",
                    flush=True,
                )

            # companion-004: 可选 ProfileStore（默认 OFF，向后兼容）。
            # COCO_PROFILE_DISABLE=1 即使代码侧构造了 store，store 内部 load/save 也会 no-op。
            # companion-006: 若 COCO_MULTI_USER=1，把 _profile_store 替换成 MultiProfileStore，
            # 下游 InteractSession / ProactiveScheduler 接口不变（duck-typing：load/save/...）。
            _profile_store = None
            _profile_switcher = None  # companion-006
            try:
                from coco.profile import (
                    ProfileStore as _ProfileStore,
                    profile_store_disabled_from_env as _profile_disabled,
                    default_profile_path as _default_profile_path,
                )
                from coco.companion.profile_switcher import (
                    MultiProfileStore as _MultiProfileStore,
                    multi_user_config_from_env as _mu_cfg_from_env,
                )
                if not _profile_disabled():
                    _mu_cfg = _mu_cfg_from_env()
                    if _mu_cfg.enabled:
                        # companion-006: per-user profile 路由
                        _profile_store = _MultiProfileStore(
                            root=_default_profile_path().parent,
                            active_user_id=None,
                        )
                        _p = _profile_store.load()
                        print(
                            f"[coco][profile] enabled multi-user root="
                            f"{_default_profile_path().parent} "
                            f"active=None debounce_s={_mu_cfg.debounce_s:.1f} "
                            f"greet_cooldown_s={_mu_cfg.greet_cooldown_s:.0f}",
                            flush=True,
                        )
                    else:
                        _profile_store = _ProfileStore()
                        _p = _profile_store.load()
                        print(
                            f"[coco][profile] enabled path={_default_profile_path()} "
                            f"name={_p.name!r} interests={_p.interests} goals={_p.goals}",
                            flush=True,
                        )
                    try:
                        emit(
                            "interact.profile_loaded",
                            name=_p.name,
                            interests=list(_p.interests),
                            goals=list(_p.goals),
                            schema_version=_p.schema_version,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    print("[coco][profile] disabled (COCO_PROFILE_DISABLE=1)", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[coco][profile] init failed: {type(e).__name__}: {e}", flush=True)
                _profile_store = None

            # vision-003: 可选 face-id 识别（默认 OFF）。
            # 当前 main 不构造 FaceTracker（business 决策同 face_tracker_for_power）；
            # 这里只在 COCO_FACE_ID=1 时初始化 classifier 并 emit backend_selected event，
            # 留作未来 vision 子系统启用时的注入点。
            _face_id_classifier = None
            try:
                from coco.perception.face_id import (
                    FaceIDClassifier as _FaceIDClassifier,
                    FaceIDStore as _FaceIDStore,
                    config_from_env as _face_id_config_from_env,
                )
                _fid_cfg = _face_id_config_from_env()
                if _fid_cfg.enabled:
                    _store_root = Path(_fid_cfg.path) if _fid_cfg.path else None
                    _face_id_classifier = _FaceIDClassifier(
                        store=_FaceIDStore(_store_root),
                        threshold=_fid_cfg.confidence_threshold,
                        backend_pref=_fid_cfg.backend,
                    )
                    print(
                        f"[coco][face_id] enabled backend={_face_id_classifier.backend_name} "
                        f"threshold={_face_id_classifier.threshold:.2f} "
                        f"records={len(_face_id_classifier.store.all_records())}",
                        flush=True,
                    )
                    try:
                        emit(
                            "face.id_backend_selected",
                            backend=_face_id_classifier.backend_name,
                            threshold=_face_id_classifier.threshold,
                            records=len(_face_id_classifier.store.all_records()),
                        )
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    print("[coco][face_id] disabled (COCO_FACE_ID not set)", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[coco][face_id] init failed: {type(e).__name__}: {e}", flush=True)
                _face_id_classifier = None

            # companion-006: 多用户 ProfileSwitcher（COCO_MULTI_USER=1）。
            # 必须在 _profile_store(MultiProfileStore) 与 _dialog_memory 都构造完之后。
            # on_switch 回调里 clear DialogMemory，确保 per-profile 隔离（V6）。
            try:
                from coco.companion.profile_switcher import (
                    build_profile_switcher as _build_pw,
                    multi_user_config_from_env as _mu_cfg2,
                )
                _mu_cfg_now = _mu_cfg2()
                if (
                    _mu_cfg_now.enabled
                    and _profile_store is not None
                    and type(_profile_store).__name__ == "MultiProfileStore"
                ):
                    def _on_profile_switch(prev, curr, _dm=_dialog_memory):
                        if _dm is not None:
                            try:
                                _dm.clear()
                            except Exception as _e:  # noqa: BLE001
                                print(
                                    f"[coco][profile_switcher] dialog clear failed: "
                                    f"{type(_e).__name__}: {_e}",
                                    flush=True,
                                )
                    _profile_switcher = _build_pw(
                        store=_profile_store,
                        config=_mu_cfg_now,
                        # L1-1 fix: say_async 不阻塞 attention tick 线程
                        # （observe() 由 attention loop 调用，blocking say 会卡 2-5s）
                        tts_say_fn=coco_tts.say_async,
                        emit_fn=emit,
                        on_switch=_on_profile_switch,
                    )
                    if _profile_switcher is not None:
                        # late-binding wire 到 attention loop（loop 通过 selector 上的
                        # _coco_profile_switcher attribute 取 switcher，避免 closure
                        # 早绑定 None）。
                        if _attention_selector is not None:
                            try:
                                setattr(_attention_selector, "_coco_profile_switcher", _profile_switcher)
                            except Exception:  # noqa: BLE001
                                pass
                        print(
                            f"[coco][profile_switcher] enabled debounce_s={_mu_cfg_now.debounce_s:.1f} "
                            f"greet_cooldown_s={_mu_cfg_now.greet_cooldown_s:.0f} "
                            f"greet_enabled={_mu_cfg_now.greet_enabled}",
                            flush=True,
                        )
                else:
                    if _mu_cfg_now.enabled:
                        print(
                            "[coco][profile_switcher] disabled: requires "
                            "MultiProfileStore (set COCO_MULTI_USER=1 上面已生效)",
                            flush=True,
                        )
            except Exception as e:  # noqa: BLE001
                print(
                    f"[coco][profile_switcher] init failed: {type(e).__name__}: {e}",
                    flush=True,
                )
                _profile_switcher = None

            # interact-008: 可选 IntentClassifier + ConversationStateMachine（默认 OFF）。
            # COCO_INTENT=1 启用：handle_audio 内做 intent 分类 + state 机；
            # COMMAND="安静"/"重复"/TEACH 都按 ConvState 走特殊路径。
            # 注意：放在 ProactiveScheduler 构造之前，便于把 _conv_sm 注入 proactive，
            # QUIET 期间后台主动话题也会跳过（interact-008 L1-1）。
            _intent_classifier = None
            _conv_sm = None
            try:
                from coco.intent import (
                    IntentClassifier as _IntentClassifier,
                    config_from_env as _intent_cfg_from_env,
                    intent_enabled_from_env as _intent_enabled,
                )
                from coco.conversation import (
                    ConversationStateMachine as _ConvSM,
                    config_from_env as _conv_cfg_from_env,
                )
                if _intent_enabled():
                    _icfg = _intent_cfg_from_env()
                    # interact-008 L2: COCO_INTENT_LLM=1 时把 _llm.reply 作为 llm_fn 注入；
                    # IntentClassifier 内仅在 config.llm_fallback=True 才真的调，仍 fail-soft。
                    _intent_llm_fn = _llm.reply if _icfg.llm_fallback else None
                    _intent_classifier = _IntentClassifier(config=_icfg, llm_fn=_intent_llm_fn)
                    _conv_sm = _ConvSM(config=_conv_cfg_from_env())
                    print(
                        f"[coco][intent] enabled llm_fallback={_icfg.llm_fallback} "
                        f"quiet_s={_conv_sm.config.quiet_seconds:.0f} "
                        f"teaching_max_s={_conv_sm.config.teaching_max_seconds:.0f}",
                        flush=True,
                    )
                else:
                    print("[coco][intent] disabled (COCO_INTENT not set)", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[coco][intent] init failed: {type(e).__name__}: {e}", flush=True)
                _intent_classifier = None
                _conv_sm = None

            # interact-007: 可选 ProactiveScheduler（默认 OFF）。
            # 构造放在 InteractSession 之前以便把 record_interaction 钩进 session.on_interaction。
            _proactive = None
            try:
                from coco.proactive import (
                    ProactiveScheduler as _ProactiveScheduler,
                    config_from_env as _proactive_config_from_env,
                )
                _pcfg = _proactive_config_from_env()
                if _pcfg.enabled:
                    _proactive = _ProactiveScheduler(
                        config=_pcfg,
                        power_state=power_state,
                        face_tracker=_face_tracker_shared,  # interact-007 L1-1: 复用 power watcher 同一实例
                        llm_reply_fn=_llm.reply,
                        tts_say_fn=coco_tts.say,
                        profile_store=_profile_store,
                        on_interaction=(
                            (lambda src, _ps=power_state: _ps.record_interaction(source=src))
                            if power_state is not None else None
                        ),
                        # interact-008 L1-1: QUIET 期间后台主动话题也跳过
                        conv_state_machine=_conv_sm,
                    )
                    print(
                        f"[coco][proactive] enabled idle={_pcfg.idle_threshold_s:.0f}s "
                        f"cooldown={_pcfg.cooldown_s:.0f}s max/h={_pcfg.max_topics_per_hour}",
                        flush=True,
                    )
                else:
                    print("[coco][proactive] disabled (COCO_PROACTIVE not set)", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[coco][proactive] init failed: {type(e).__name__}: {e}", flush=True)
                _proactive = None

            def _on_interaction_combined(src: str, _ps=power_state, _pa=_proactive) -> None:
                if _ps is not None:
                    try:
                        _ps.record_interaction(source=src)
                    except Exception as e:  # noqa: BLE001
                        print(f"[coco][power] record_interaction failed: {e!r}", flush=True)
                if _pa is not None:
                    try:
                        _pa.record_interaction(source=src)
                    except Exception as e:  # noqa: BLE001
                        print(f"[coco][proactive] record_interaction failed: {e!r}", flush=True)


            # interact-009: 可选对话历史压缩（默认 OFF）。
            # 启用时若 dialog_memory 未启用，summarizer 也不构造（无 history 可压缩）。
            _dialog_summarizer = None
            _dialog_summary_threshold = 10
            _dialog_summary_keep_recent = 4
            try:
                from coco.dialog_summary import (
                    config_from_env as _ds_config_from_env,
                    build_summarizer as _ds_build,
                )
                _ds_cfg = _ds_config_from_env()
                if _ds_cfg.enabled and _dialog_memory is not None:
                    _dialog_summarizer = _ds_build(_ds_cfg, llm_reply_fn=_llm.reply)
                    _dialog_summary_threshold = _ds_cfg.threshold_turns
                    _dialog_summary_keep_recent = _ds_cfg.keep_recent
                    # interact-009 L1-2: auto-bump dialog max_turns >= threshold + keep_recent
                    # （deque 必须能容纳触发压缩所需的 turns，否则永远跑不到 threshold）。
                    # deque maxlen 不可改 → 重建 DialogMemory 实例。
                    _required_max = _ds_cfg.threshold_turns + _ds_cfg.keep_recent
                    if _dialog_memory.max_turns < _required_max:
                        _orig_max = _dialog_memory.max_turns
                        try:
                            from coco.dialog import DialogMemory as _DM
                            _dialog_memory = _DM(
                                max_turns=_required_max,
                                idle_timeout_s=_dialog_memory.idle_timeout_s,
                            )
                            print(
                                f"[coco][dialog_summary] auto-bumped dialog max_turns "
                                f"{_orig_max} -> {_required_max} "
                                f"(threshold={_ds_cfg.threshold_turns} + keep={_ds_cfg.keep_recent})",
                                flush=True,
                            )
                        except Exception as _e:  # noqa: BLE001
                            print(
                                f"[coco][dialog_summary] WARN auto-bump failed "
                                f"({type(_e).__name__}: {_e}); 压缩可能不会触发",
                                flush=True,
                            )
                    print(
                        f"[coco][dialog_summary] enabled kind={_ds_cfg.summarizer_kind} "
                        f"threshold={_ds_cfg.threshold_turns} keep={_ds_cfg.keep_recent} "
                        f"max_chars={_ds_cfg.summary_max_chars}",
                        flush=True,
                    )
            except Exception as e:  # noqa: BLE001
                print(f"[coco][dialog_summary] init failed: {type(e).__name__}: {e}", flush=True)

            session = InteractSession(
                robot=reachy_mini,
                asr_fn=_asr_int16_fn,
                tts_say_fn=coco_tts.say,
                idle_animator=idle_animator,
                llm_reply_fn=_llm.reply,
                # companion-003 L0-2 + interact-007: 统一交互钩子，同时通知 power_state
                # 与 ProactiveScheduler，避免 phase-3 双计数 / phase-4 主动话题误发。
                on_interaction=(
                    _on_interaction_combined
                    if (power_state is not None or _proactive is not None) else None
                ),
                dialog_memory=_dialog_memory,
                profile_store=_profile_store,
                intent_classifier=_intent_classifier,
                conv_state_machine=_conv_sm,
                dialog_summarizer=_dialog_summarizer,
                dialog_summary_threshold=_dialog_summary_threshold,
                dialog_summary_keep_recent=_dialog_summary_keep_recent,
                # robot-004: 共享 EmotionTracker（若 robot-004 / interact-006 启用之一构造了它）
                emotion_detector=(
                    _build_emotion_detector_for_session()
                    if _shared_emotion_tracker is not None else None
                ),
                emotion_tracker=_shared_emotion_tracker,
            )

            # interact-007: 启动 scheduler（必须在 session 构造之后，因为 InteractSession
            # 把 on_interaction 钩到 _proactive.record_interaction，避免一启动就秒发）。
            if _proactive is not None:
                try:
                    _proactive.start(stop_event)
                except Exception as e:  # noqa: BLE001
                    print(f"[coco][proactive] start failed: {type(e).__name__}: {e}", flush=True)

            # infra-003: 可选 MetricsCollector（默认 OFF；COCO_METRICS=1 启用）。
            # 把已构造的 power/dialog/proactive/face 注入；缺谁就 skip 谁的 source。
            # L1-5: 真正用 cfg.metrics 驱动（path / interval_s / enabled），不再走
            # path_from_env 的次级路径——env 解析仍由 config.py 完成。
            _metrics = None
            try:
                from coco.metrics import (
                    metrics_enabled_from_env as _metrics_enabled,
                    build_default_collector as _build_metrics,
                    default_metrics_path as _default_metrics_path,
                )
                _mcfg = getattr(_coco_cfg, "metrics", None)
                _enabled = bool(_mcfg.enabled) if _mcfg is not None else _metrics_enabled()
                if _enabled:
                    _m_path = Path(_mcfg.path) if (_mcfg and _mcfg.path) else _default_metrics_path()
                    _m_interval = float(_mcfg.interval_s) if _mcfg is not None else None
                    _metrics = _build_metrics(
                        power_state=power_state,
                        dialog_memory=_dialog_memory,
                        proactive=_proactive,
                        face_tracker=_face_tracker_shared,
                        path=_m_path,
                        interval_s=_m_interval,
                    )
                    _metrics.start(stop_event)
                    print(
                        f"[coco][metrics] enabled path={_metrics.path} "
                        f"interval={_metrics.interval_s:.1f}s sources={len(_metrics.sources)}",
                        flush=True,
                    )
                else:
                    print("[coco][metrics] disabled (cfg.metrics.enabled=False)", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[coco][metrics] init failed: {type(e).__name__}: {e}", flush=True)
                _metrics = None

            # vision-004b-wire: 可选 MultiFaceAttention 接线（COCO_GREET_SECONDARY=1 启用，默认 OFF）。
            # 把 AttentionSelector / FaceTracker / ConvSM / Proactive 喂进状态机；
            # 触发 GreetAction 时调 ExpressionPlayer.play("greet") + tts.say(utterance)。
            _greet_wire = None
            try:
                from coco.companion.greet_secondary_wire import (
                    build_greet_secondary_wire as _build_greet_wire,
                    greet_secondary_config_from_env as _greet_cfg_from_env,
                )
                _gwcfg = _greet_cfg_from_env()
                if _gwcfg.enabled:
                    _greet_wire = _build_greet_wire(
                        config=_gwcfg,
                        attention_selector=_attention_selector,
                        face_tracker=_face_tracker_shared,
                        tts_say_fn=coco_tts.say,
                        expression_player=_expression_player,
                        conv_state_machine=_conv_sm,
                        proactive_scheduler=_proactive,
                        emit_fn=emit,
                    )
                    if _greet_wire is not None:
                        _greet_wire.start(stop_event)
                        print(
                            f"[coco][greet_wire] enabled tick_hz={_gwcfg.tick_hz} "
                            f"silence={_gwcfg.silence_threshold_s}s "
                            f"cooldown={_gwcfg.cooldown_s}s "
                            f"primary_stable={_gwcfg.primary_stable_s}s",
                            flush=True,
                        )
                    else:
                        print(
                            "[coco][greet_wire] disabled (missing attention_selector "
                            "or face_tracker; need COCO_ATTENTION=1 + COCO_FACE_TRACK=1)",
                            flush=True,
                        )
                else:
                    print("[coco][greet_wire] disabled (COCO_GREET_SECONDARY not set)", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[coco][greet_wire] init failed: {type(e).__name__}: {e}", flush=True)
                _greet_wire = None

            use_vad = (not PUSH_TO_TALK_DISABLED) and (not vad_disabled_from_env())
            if use_vad:
                # interact-003: 用 VAD 取代 stdin Enter；session.tts_say_fn 包一层 mute 防自激
                vad_cfg = config_from_env()

                def _vad_on_utterance(audio_int16: np.ndarray, sr: int) -> None:
                    # companion-003 L0-2: record_interaction 统一在 InteractSession.handle_audio
                    # 内通过 on_interaction 钩子触发，不在这里重复（避免双计数）。
                    try:
                        emit("vad.utterance", samples=int(audio_int16.shape[0]), sr=sr)
                    except Exception:  # noqa: BLE001
                        pass
                    r = session.handle_audio(audio_int16, sr, skip_action=False, skip_tts_play=False)
                    try:
                        emit(
                            "asr.transcribe",
                            text=str(r.get("transcript", ""))[:200],
                            ok=bool(r.get("asr_ok", False)),
                        )
                        emit(
                            "llm.reply",
                            text=str(r.get("reply", ""))[:200],
                            action=str(r.get("action", "")),
                            duration_s=float(r.get("duration_s", 0.0)),
                        )
                    except Exception:  # noqa: BLE001
                        pass
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
                            on_wake=lambda t: (
                                power_state.record_interaction(source="wake_word")
                                if power_state is not None else None,
                                emit("wake.hit", word=str(t), window_s=wake_cfg.window_seconds),
                                print(
                                    f"[coco][wake] hit {t!r}; awake for "
                                    f"{wake_cfg.window_seconds:.1f}s",
                                    flush=True,
                                ),
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

                        # NOTE: 用 _shared_feed 代替 bridge.feed()，等价但保留 KWS→VAD 顺序在主流程显式可见
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
            # robot-003: 停 ExpressionPlayer（解绑 tts 注入并 stop()）
            if _expression_player is not None:
                try:
                    _expression_player.stop()
                except Exception as e:  # noqa: BLE001
                    print(f"[coco][expr] stop failed: {e!r}", flush=True)
                try:
                    coco_tts.set_expression_player(None)
                except Exception:  # noqa: BLE001
                    pass
            # robot-004: 停 PostureBaselineModulator
            if _posture_baseline is not None:
                try:
                    _posture_baseline.join(timeout=2.0)
                    if _posture_baseline.is_alive():
                        print("[coco][posture] WARN: modulator did not stop within 2s", flush=True)
                    else:
                        print(f"[coco][posture] stopped stats={_posture_baseline.stats}", flush=True)
                except Exception as e:  # noqa: BLE001
                    print(f"[coco][posture] stop failed: {e!r}", flush=True)
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
            # companion-003: 停 power_state driver
            if power_state is not None:
                power_state.join_driver(timeout=2.0)
            # interact-007: 停 ProactiveScheduler
            try:
                if "_proactive" in locals() and _proactive is not None:
                    _proactive.join(timeout=2.0)
            except Exception as e:  # noqa: BLE001
                print(f"[coco][proactive] join failed: {e!r}", flush=True)
            # interact-007 L1-1: 停 FaceTracker（如已构造）
            try:
                if _face_tracker_shared is not None:
                    _face_tracker_shared.join(timeout=2.0)
            except Exception as e:  # noqa: BLE001
                print(f"[coco][face] join failed: {e!r}", flush=True)
            # vision-004: 停 AttentionSelector tick 线程
            try:
                _attention_stop.set()
                if _attention_thread is not None:
                    _attention_thread.join(timeout=2.0)
            except Exception as e:  # noqa: BLE001
                print(f"[coco][attention] stop failed: {e!r}", flush=True)
            # companion-006: 解绑 selector 上的 switcher 引用（避免残留状态）
            try:
                if _attention_selector is not None:
                    if hasattr(_attention_selector, "_coco_profile_switcher"):
                        delattr(_attention_selector, "_coco_profile_switcher")
            except Exception:  # noqa: BLE001
                pass
            # infra-003: 停 MetricsCollector
            try:
                if "_metrics" in locals() and _metrics is not None:
                    _metrics.stop(timeout=2.0)
            except Exception as e:  # noqa: BLE001
                print(f"[coco][metrics] stop failed: {e!r}", flush=True)
            # vision-004b-wire: 停 GreetSecondaryWire
            try:
                if "_greet_wire" in locals() and _greet_wire is not None:
                    _greet_wire.stop(timeout=2.0)
            except Exception as e:  # noqa: BLE001
                print(f"[coco][greet_wire] stop failed: {e!r}", flush=True)
            # vision-005: 停 GestureRecognizer（与其他后台组件清理风格一致）
            try:
                if "_gesture_recognizer" in locals() and _gesture_recognizer is not None:
                    _gesture_recognizer.stop()
                    _gesture_recognizer.join(timeout=2.0)
                    if _gesture_recognizer.is_alive():
                        print("[coco][gesture] WARN: recognizer did not stop within 2s", flush=True)
                    else:
                        print(f"[coco][gesture] stopped stats={_gesture_recognizer.stats}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[coco][gesture] stop failed: {e!r}", flush=True)


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
