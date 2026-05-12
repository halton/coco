"""coco.companion.emotion_renderer — companion-007.

把 ``Emotion`` 翻译成跨子系统的 ``EmotionStyle``：

- ``tts_rate`` / ``tts_pitch_semitone`` → tts.say / say_async 的 prosody 提示
- ``expr_overlay`` → ExpressionPlayer.play(...) 的短帧叠加（可选）
- ``antenna_pulse`` → 通过 PostureBaseline 的 pause/resume 协议短促打一次天线脉冲

设计要点
========

1. **同源 debounce**：``EmotionRenderer`` 不自己跑计时器；通过
   ``PostureBaselineModulator.add_listener(...)`` 订阅 baseline 的目标变更
   （已经过 5s debounce + ramp 平滑）。emotion / power_state snapshot 与 baseline
   完全同源，避免双套状态。
2. **Default-OFF**：``COCO_EMOTION_PROSODY=1`` 才装配；env=0 时 say / ExpressionPlayer
   行为与 phase-7 之前完全一致（不传 rate/pitch、不打天线脉冲、不叠加 overlay）。
3. **TTS prosody fallback**：tts backend 未必支持 pitch；
   ``say(..., rate=..., pitch_semitone=...)`` 在 backend 不支持时 fallback no-op
   并 emit 一次 ``tts.prosody_unsupported``（每进程仅 emit 一次，避免刷屏）。
4. **Antenna pulse**：与 robot-004 ``PostureBaseline.pause()/resume()`` 协议一致；
   pulse 期间 baseline 暂停天线下发，pulse 结束 resume；fail-soft 失败不抛。
5. **不抢 ExpressionPlayer**：当 player 正在 ``is_busy()`` 时跳过 expr_overlay
   叠加；同样的 baseline 也已被 player.play 自身的 pause 接管，避免双重 pause。

env
===

- ``COCO_EMOTION_PROSODY``        — 1/true/yes 启用，默认 OFF
- ``COCO_EMOTION_PROSODY_PULSE_S`` — antenna pulse 单帧时长，clamp [0.05, 1.0]，默认 0.2
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Mapping, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 安全上限（与 verification 字段对齐）
# ---------------------------------------------------------------------------

# tts_rate 视作"百分比偏移": 0.05 = +5%；clamp [-30%, +30%]
MAX_TTS_RATE_DELTA: float = 0.30
MIN_TTS_RATE_DELTA: float = -0.30
# 半音偏移上下限
MAX_PITCH_SEMITONES: float = 3.0
MIN_PITCH_SEMITONES: float = -3.0


# ---------------------------------------------------------------------------
# EmotionStyle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmotionStyle:
    """情绪 → 跨子系统风格：tts prosody + 表情叠加 + 天线脉冲。

    - tts_rate: 速率偏移（0.05 = +5%；负数 = 减速）；clamp ±30%
    - tts_pitch_semitone: 音高偏移（半音）；clamp ±3
    - expr_overlay: 可选的 expression 库内名（happy → "excited" 等短帧）；None = 不叠加
    - antenna_pulse: 是否打一次天线脉冲（happy / surprised → True）
    """

    tts_rate: float = 0.0
    tts_pitch_semitone: float = 0.0
    expr_overlay: Optional[str] = None
    antenna_pulse: bool = False

    def clamped(self) -> "EmotionStyle":
        return EmotionStyle(
            tts_rate=_clamp(self.tts_rate, MIN_TTS_RATE_DELTA, MAX_TTS_RATE_DELTA),
            tts_pitch_semitone=_clamp(
                self.tts_pitch_semitone, MIN_PITCH_SEMITONES, MAX_PITCH_SEMITONES
            ),
            expr_overlay=self.expr_overlay,
            antenna_pulse=bool(self.antenna_pulse),
        )


NEUTRAL_STYLE = EmotionStyle(0.0, 0.0, None, False)


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


# ---------------------------------------------------------------------------
# 查表：emotion.value → EmotionStyle
# ---------------------------------------------------------------------------

_STYLE_TABLE: dict = {
    "happy":     EmotionStyle(tts_rate=+0.05, tts_pitch_semitone=+1.0,
                              expr_overlay="excited", antenna_pulse=True),
    "sad":       EmotionStyle(tts_rate=-0.10, tts_pitch_semitone=-1.0,
                              expr_overlay=None, antenna_pulse=False),
    "angry":     EmotionStyle(tts_rate=0.0, tts_pitch_semitone=+0.5,
                              expr_overlay=None, antenna_pulse=True),
    "surprised": EmotionStyle(tts_rate=+0.05, tts_pitch_semitone=+2.0,
                              expr_overlay=None, antenna_pulse=True),
    "neutral":   NEUTRAL_STYLE,
    "focused":   NEUTRAL_STYLE,
}


def style_for_emotion(emotion: Any) -> EmotionStyle:
    """纯查表：emotion → EmotionStyle (clamped)。

    异常输入（None / 数字 / 未知字符串）→ NEUTRAL_STYLE。
    """
    key = _normalize_emotion(emotion)
    style = _STYLE_TABLE.get(key, NEUTRAL_STYLE)
    return style.clamped()


def _normalize_emotion(v: Any) -> str:
    if v is None:
        return "neutral"
    s = getattr(v, "value", v)
    if not isinstance(s, str):
        return "neutral"
    return s.lower()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmotionRendererConfig:
    enabled: bool = False
    pulse_s: float = 0.2


def _bool_env(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


def _float_env(env: Mapping[str, str], key: str, default: float, lo: float, hi: float) -> float:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        log.warning("[emotion_renderer] %s=%r 非数字，回退默认 %.2f", key, raw, default)
        return default
    if v < lo:
        log.warning("[emotion_renderer] %s=%.3f <%.3f，clamp", key, v, lo)
        return lo
    if v > hi:
        log.warning("[emotion_renderer] %s=%.3f >%.3f，clamp", key, v, hi)
        return hi
    return v


def emotion_prosody_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    e = env if env is not None else os.environ
    return _bool_env(e, "COCO_EMOTION_PROSODY", False)


def emotion_renderer_config_from_env(env: Optional[Mapping[str, str]] = None) -> EmotionRendererConfig:
    e = env if env is not None else os.environ
    return EmotionRendererConfig(
        enabled=_bool_env(e, "COCO_EMOTION_PROSODY", False),
        pulse_s=_float_env(e, "COCO_EMOTION_PROSODY_PULSE_S", 0.2, 0.05, 1.0),
    )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class EmotionRendererStats:
    style_changes: int = 0
    tts_styles_applied: int = 0       # 当前 style 被 apply_to_tts_kwargs 取走次数
    pulses_dispatched: int = 0
    overlays_dispatched: int = 0
    overlays_skipped_busy: int = 0
    pulses_failed: int = 0
    overlays_failed: int = 0
    last_emotion: Optional[str] = None
    last_style: Optional[EmotionStyle] = None
    history: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# EmotionRenderer
# ---------------------------------------------------------------------------


class EmotionRenderer:
    """订阅 PostureBaselineModulator 的 target 变更（同源 debounce），把
    新 emotion 翻译成 EmotionStyle 并：

    1. 缓存当前 style，供 ``apply_to_tts_kwargs(kwargs)`` 在每次 say 之前注入
    2. 若 antenna_pulse=True 且 expression_player 不忙：pause baseline → 推一帧
       天线脉冲（[+0.5, -0.5] / [-0.5, +0.5] 交替）→ 等 pulse_s → resume baseline
    3. 若 expr_overlay 命中库且 expression_player 不忙：play(overlay)（异步线程 fire-and-forget）

    用法（main.py）：
        renderer = EmotionRenderer(
            posture_baseline=_posture_baseline,
            expression_player=_expression_player,
            robot=reachy_mini,
            config=emotion_renderer_config_from_env(),
            emit_fn=emit,
        )
        renderer.start()  # 注册到 baseline.add_listener
        ...
        renderer.stop()
    """

    def __init__(
        self,
        *,
        posture_baseline: Any,
        expression_player: Any = None,
        robot: Any = None,
        config: Optional[EmotionRendererConfig] = None,
        emit_fn: Optional[Callable[..., None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.posture_baseline = posture_baseline
        self.expression_player = expression_player
        self.robot = robot
        self.config = config or EmotionRendererConfig()
        self._emit = emit_fn
        self.clock = clock or time.monotonic
        self.stats = EmotionRendererStats()

        self._lock = threading.RLock()
        self._current_style: EmotionStyle = NEUTRAL_STYLE
        self._current_emotion: str = "neutral"
        self._stopped = False
        self._listener_registered = False
        # 用于交替 antenna pulse 方向（单调累加，奇偶决定方向）
        self._pulse_seq: int = 0

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def current_style(self) -> EmotionStyle:
        if not self.config.enabled:
            return NEUTRAL_STYLE
        return self._current_style

    def current_emotion(self) -> str:
        return self._current_emotion

    def start(self) -> None:
        """注册 listener 到 PostureBaselineModulator。disabled 直接 noop。"""
        if not self.config.enabled:
            log.info("[emotion_renderer] disabled (COCO_EMOTION_PROSODY not set); start() noop")
            return
        if self._listener_registered:
            return
        if self.posture_baseline is None:
            log.warning("[emotion_renderer] posture_baseline=None；EmotionRenderer 无法订阅，noop")
            return
        add = getattr(self.posture_baseline, "add_listener", None)
        if not callable(add):
            log.warning(
                "[emotion_renderer] posture_baseline 无 add_listener；EmotionRenderer 无法启用"
            )
            return
        add(self._on_baseline_target_changed)
        self._listener_registered = True
        log.info(
            "[emotion_renderer] enabled pulse_s=%.2f, listener bound to PostureBaselineModulator",
            self.config.pulse_s,
        )

    def stop(self) -> None:
        self._stopped = True

    def apply_to_tts_kwargs(self, kwargs: dict) -> dict:
        """把当前 style 的 rate / pitch_semitone 注入 tts kwargs（in-place 也回新 dict）。

        env OFF / NEUTRAL → 不注入任何键，行为等价 phase-3 调用。
        kwargs 已包含 rate / pitch_semitone（调用方显式覆盖）→ 不覆盖。
        """
        if not self.config.enabled:
            return kwargs
        st = self._current_style
        if st.tts_rate == 0.0 and st.tts_pitch_semitone == 0.0:
            return kwargs
        if "rate" not in kwargs:
            kwargs["rate"] = st.tts_rate
        if "pitch_semitone" not in kwargs:
            kwargs["pitch_semitone"] = st.tts_pitch_semitone
        self.stats.tts_styles_applied += 1
        return kwargs

    # ------------------------------------------------------------------
    # 内部：listener 回调
    # ------------------------------------------------------------------

    def _on_baseline_target_changed(self, emotion: Any, power_state: Any) -> None:
        """PostureBaselineModulator._begin_ramp 调用此 callback。

        参数与 PostureBaseline.compute 同源（都是 modulator 同一次 snapshot）。
        """
        if self._stopped or not self.config.enabled:
            return
        try:
            ek = _normalize_emotion(emotion)
            new_style = style_for_emotion(emotion)
            with self._lock:
                if ek == self._current_emotion and new_style == self._current_style:
                    return
                old_emotion = self._current_emotion
                self._current_emotion = ek
                self._current_style = new_style
                self.stats.style_changes += 1
                self.stats.last_emotion = ek
                self.stats.last_style = new_style
                self.stats.history.append(f"{ek}@{self.clock():.2f}")
            log.info(
                "[emotion_renderer] style -> emo=%s rate=%+.2f pitch=%+.1f overlay=%s pulse=%s (was %s)",
                ek, new_style.tts_rate, new_style.tts_pitch_semitone,
                new_style.expr_overlay, new_style.antenna_pulse, old_emotion,
            )
            self._emit_event(
                "companion.emotion_render_changed",
                message=f"emotion={ek} style updated",
                emotion=ek,
                tts_rate=round(new_style.tts_rate, 4),
                tts_pitch_semitone=round(new_style.tts_pitch_semitone, 3),
                expr_overlay=new_style.expr_overlay,
                antenna_pulse=bool(new_style.antenna_pulse),
                fallback=False,
            )
            # 副作用：antenna pulse + expr overlay
            if new_style.antenna_pulse:
                self._dispatch_antenna_pulse()
            if new_style.expr_overlay:
                self._dispatch_overlay(new_style.expr_overlay)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "[emotion_renderer] _on_baseline_target_changed failed: %s: %s",
                type(exc).__name__, exc,
            )

    # ------------------------------------------------------------------
    # 副作用：antenna pulse + expr overlay
    # ------------------------------------------------------------------

    def _dispatch_antenna_pulse(self) -> None:
        """与 baseline pause/resume 协议一致：pause baseline → 一帧天线脉冲 → resume。

        若 expression_player.is_busy() → 跳过（player 已自带 baseline.pause）。
        若 robot 不支持 set_target_antenna_joint_positions → 跳过（fail-soft）。
        """
        if self.posture_baseline is None or self.robot is None:
            return
        # ExpressionPlayer 占用 baseline 时跳过（避免互踩）
        player = self.expression_player
        if player is not None:
            try:
                if hasattr(player, "is_busy") and player.is_busy():
                    return
            except Exception:  # noqa: BLE001
                pass
        if not hasattr(self.robot, "set_target_antenna_joint_positions"):
            return
        # 异步起一根 daemon thread，不阻塞 listener 回调
        t = threading.Thread(target=self._pulse_worker, name="coco-emotion-pulse", daemon=True)
        t.start()

    def _pulse_worker(self) -> None:
        try:
            self.posture_baseline.pause()
        except Exception as exc:  # noqa: BLE001
            log.warning("[emotion_renderer] baseline.pause failed: %s: %s", type(exc).__name__, exc)
        try:
            self._pulse_seq += 1
            # 交替方向：奇 → 外展，偶 → 收回，制造短促"震一下"
            if self._pulse_seq % 2 == 1:
                pos = [+0.5, -0.5]
            else:
                pos = [-0.3, +0.3]
            try:
                self.robot.set_target_antenna_joint_positions(pos)
                self.stats.pulses_dispatched += 1
            except Exception as exc:  # noqa: BLE001
                self.stats.pulses_failed += 1
                log.warning(
                    "[emotion_renderer] antenna pulse SDK failed: %s: %s",
                    type(exc).__name__, exc,
                )
            time.sleep(max(0.01, float(self.config.pulse_s)))
        finally:
            try:
                self.posture_baseline.resume()
            except Exception as exc:  # noqa: BLE001
                log.warning("[emotion_renderer] baseline.resume failed: %s: %s", type(exc).__name__, exc)

    def _dispatch_overlay(self, overlay_name: str) -> None:
        player = self.expression_player
        if player is None:
            return
        try:
            if hasattr(player, "is_busy") and player.is_busy():
                self.stats.overlays_skipped_busy += 1
                return
        except Exception:  # noqa: BLE001
            pass
        play_fn = getattr(player, "play", None)
        if not callable(play_fn):
            return

        def _worker() -> None:
            try:
                play_fn(overlay_name)
                self.stats.overlays_dispatched += 1
            except Exception as exc:  # noqa: BLE001
                self.stats.overlays_failed += 1
                log.warning(
                    "[emotion_renderer] overlay play(%r) failed: %s: %s",
                    overlay_name, type(exc).__name__, exc,
                )

        threading.Thread(target=_worker, name="coco-emotion-overlay", daemon=True).start()

    # ------------------------------------------------------------------
    # emit
    # ------------------------------------------------------------------

    def _emit_event(self, component_event: str, message: str = "", **payload: Any) -> None:
        try:
            fn = self._emit
            if fn is None:
                from coco.logging_setup import emit as _emit
                fn = _emit
            fn(component_event, message, **payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("[emotion_renderer] emit failed: %s: %s", type(exc).__name__, exc)


__all__ = [
    "EmotionStyle",
    "EmotionRenderer",
    "EmotionRendererConfig",
    "EmotionRendererStats",
    "NEUTRAL_STYLE",
    "MAX_TTS_RATE_DELTA",
    "MIN_TTS_RATE_DELTA",
    "MAX_PITCH_SEMITONES",
    "MIN_PITCH_SEMITONES",
    "style_for_emotion",
    "emotion_prosody_enabled_from_env",
    "emotion_renderer_config_from_env",
]
