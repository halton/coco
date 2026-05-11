"""vision-004b-wire: 把 MultiFaceAttention 状态机接到运行时.

设计目标
========

vision-004b 提供 ``MultiFaceAttention`` 纯状态机（不依赖任何运行时组件）；
本模块负责"接线层"：

1. 从 ``AttentionSelector`` 拿当前 primary；
2. 从 ``FaceTracker.latest()`` 拿全部活跃 tracks；
3. 从 ``ConversationStateMachine`` 拿 conv_state（用 ``is_quiet_now()`` 兼容 QUIET）；
4. 从 ``ProactiveScheduler`` 拿 ``proactive_recent`` flag（last_proactive_ts 距今
   是否在 ``proactive_block_window_s`` 内）；
5. ~3Hz 后台 daemon 线程跑 ``mfa.tick(...)``；返回 ``GreetAction`` 时调
   ``ExpressionPlayer.play('greet')`` + ``tts_say_fn(action.utterance)``。

primary 闪烁 race
-----------------

Reviewer 指出：``MultiFaceAttention`` 内 ``_last_primary_id`` 切换会重置 silence
计时。在真摄像头/真 tracker 下，primary 可能因为 1-2 帧短暂遮挡或 track_id
重排在 A↔B 之间闪烁，导致 silence 永远凑不齐，greet 永远不触发。

wire 层加 "primary stable >=N 秒" 防抖：
- 维护 ``_last_primary_id`` / ``_last_primary_change_ts``；
- 只在 primary 稳定 ``primary_stable_s`` 秒后才把它透传给 ``mfa.tick``；
- 抖动期透传上一次"已稳定 primary"，silence 不被无意义重置。

emit 钩子
---------

每次 ``mfa.tick`` 返回的 state change 实际由 mfa 内部 ``emit_fn`` 触发
``companion.multi_face_attention_state``（vision-004b 已存在）；为了在 wire
层也有 vision 子系统视角的事件，本模块额外 emit
``vision.multi_face_state_changed``（component="vision"）。

零开销
------

``GreetSecondaryConfig.enabled=False`` 时 ``build_greet_secondary_wire`` 直接
返回 None，main.py 不构造任何对象、不起线程。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from coco.companion.multi_face_attention import (
    GreetAction,
    MFAConfig,
    MFAState,
    MultiFaceAttention,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GreetSecondaryConfig:
    """vision-004b-wire 接线配置.

    ``enabled=False`` 时 main.py 不构造 wire，零开销。所有阈值在 ``__post_init__``
    内 clamp。
    """

    enabled: bool = False
    tick_hz: float = 3.0                    # 后台 tick 频率（clamp [0.5, 30]）
    silence_threshold_s: float = 8.0        # 透传给 MFAConfig
    secondary_visible_s: float = 3.0
    cooldown_s: float = 30.0                # 透传 greet_cooldown_s
    greet_duration_s: float = 3.0
    return_duration_s: float = 2.0
    proactive_block_window_s: float = 10.0
    require_named_secondary: bool = True
    primary_stable_s: float = 2.0           # primary 防抖窗口（wire 独有，clamp [0, 30]）
    utterance_template: str = "你好"

    def __post_init__(self) -> None:
        def _clamp(name: str, lo: float, hi: float) -> None:
            v = float(getattr(self, name))
            if v < lo:
                log.warning("[greet_wire] %s=%.2f <%.2f, clamp", name, v, lo)
                object.__setattr__(self, name, lo)
            elif v > hi:
                log.warning("[greet_wire] %s=%.2f >%.2f, clamp", name, v, hi)
                object.__setattr__(self, name, hi)

        _clamp("tick_hz", 0.5, 30.0)
        _clamp("silence_threshold_s", 1.0, 600.0)
        _clamp("secondary_visible_s", 0.5, 60.0)
        _clamp("cooldown_s", 5.0, 3600.0)
        _clamp("greet_duration_s", 0.1, 10.0)
        _clamp("return_duration_s", 0.1, 10.0)
        _clamp("proactive_block_window_s", 0.0, 60.0)
        _clamp("primary_stable_s", 0.0, 30.0)


def _bool_env(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _float_env(env: Mapping[str, str], key: str, default: float) -> float:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("[greet_wire] %s=%r 非数字，回退 %.2f", key, raw, default)
        return default


def greet_secondary_config_from_env(env: Optional[Mapping[str, str]] = None) -> GreetSecondaryConfig:
    """从环境变量构造配置。COCO_GREET_SECONDARY=1 启用。"""
    e = env if env is not None else os.environ
    return GreetSecondaryConfig(
        enabled=_bool_env(e, "COCO_GREET_SECONDARY", False),
        tick_hz=_float_env(e, "COCO_GREET_SECONDARY_TICK_HZ", 3.0),
        silence_threshold_s=_float_env(e, "COCO_GREET_SILENCE_S", 8.0),
        secondary_visible_s=_float_env(e, "COCO_GREET_SECONDARY_VIS_S", 3.0),
        cooldown_s=_float_env(e, "COCO_GREET_COOLDOWN_S", 30.0),
        greet_duration_s=_float_env(e, "COCO_GREET_DUR_S", 3.0),
        return_duration_s=_float_env(e, "COCO_GREET_RETURN_S", 2.0),
        proactive_block_window_s=_float_env(e, "COCO_GREET_PROACTIVE_BLOCK_S", 10.0),
        require_named_secondary=_bool_env(e, "COCO_GREET_REQUIRE_NAMED", True),
        primary_stable_s=_float_env(e, "COCO_GREET_PRIMARY_STABLE_S", 2.0),
        utterance_template=(e.get("COCO_GREET_UTTERANCE") or "你好"),
    )


# ---------------------------------------------------------------------------
# Wire
# ---------------------------------------------------------------------------


EmitFn = Callable[..., None]


class GreetSecondaryWire:
    """运行时接线 + primary 防抖 + 后台 tick 线程.

    用法（见 main.py）::

        wire = build_greet_secondary_wire(
            attention_selector=_attention_selector,
            face_tracker=_face_tracker_shared,
            tts_say_fn=coco_tts.say,
            expression_player=_expression_player,
            conv_state_machine=_conv_sm,
            proactive_scheduler=_proactive,
            emit_fn=emit,
        )
        if wire is not None:
            wire.start(stop_event)
            try:
                ...
            finally:
                wire.stop(timeout=2.0)
    """

    def __init__(
        self,
        *,
        config: GreetSecondaryConfig,
        mfa: MultiFaceAttention,
        attention_selector: Any,
        face_tracker: Any,
        tts_say_fn: Optional[Callable[[str], Any]] = None,
        expression_player: Any = None,
        conv_state_machine: Any = None,
        proactive_scheduler: Any = None,
        emit_fn: Optional[EmitFn] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.config = config
        self.mfa = mfa
        self.attention_selector = attention_selector
        self.face_tracker = face_tracker
        self.tts_say_fn = tts_say_fn
        self.expression_player = expression_player
        self.conv_state_machine = conv_state_machine
        self.proactive_scheduler = proactive_scheduler
        self._emit_fn = emit_fn
        self._clock = clock or time.monotonic

        # primary 防抖
        self._last_primary_id: Optional[int] = None
        self._last_primary_change_ts: float = self._clock()
        self._stable_primary_id: Optional[int] = None  # 透传给 mfa 的稳定 primary

        # state-change wire-level emit
        self._last_mfa_state: MFAState = mfa.state

        # 后台线程
        self._stop_event: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None

        # mfa 的 on_action 钩到我们这里，触发 tts + expression
        if mfa._on_action is None:
            mfa._on_action = self._on_greet_action  # type: ignore[assignment]
        # mfa 的 on_state_change 钩到 wire emit
        if mfa._on_state_change is None:
            mfa._on_state_change = self._on_mfa_state_change  # type: ignore[assignment]

    # ---------------- lifecycle ----------------
    def start(self, stop_event: threading.Event) -> None:
        self._stop_event = stop_event
        self._thread = threading.Thread(
            target=self._loop,
            name="coco-greet-secondary",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                log.warning("[greet_wire] thread did not stop within %.2fs", timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---------------- internal: snapshot resolvers ----------------
    def _resolve_primary(self) -> Optional[Any]:
        sel = self.attention_selector
        if sel is None:
            return None
        try:
            return sel.current()
        except Exception as e:  # noqa: BLE001
            log.warning("[greet_wire] attention.current failed: %s: %s", type(e).__name__, e)
            return None

    def _resolve_tracks(self) -> list:
        tracker = self.face_tracker
        if tracker is None:
            return []
        try:
            snap = tracker.latest()
        except Exception as e:  # noqa: BLE001
            log.warning("[greet_wire] face_tracker.latest failed: %s: %s", type(e).__name__, e)
            return []
        try:
            return list(getattr(snap, "tracks", ()) or ())
        except Exception:  # noqa: BLE001
            return []

    def _resolve_conv_state(self) -> Any:
        sm = self.conv_state_machine
        if sm is None:
            return "idle"
        # is_quiet_now() 触发 QUIET 自动过期检查；之后再读 current_state
        try:
            sm.is_quiet_now()
        except Exception:  # noqa: BLE001
            pass
        try:
            return sm.current_state
        except Exception as e:  # noqa: BLE001
            log.warning("[greet_wire] conv.current_state failed: %s: %s", type(e).__name__, e)
            return "idle"

    def _resolve_proactive_recent(self) -> bool:
        pa = self.proactive_scheduler
        if pa is None:
            return False
        try:
            last = float(getattr(pa, "_last_proactive_ts", 0.0))
        except (TypeError, ValueError):
            return False
        if last <= 0:
            return False
        # 使用墙钟（proactive 用 time.time）；wire 自身 clock 仅用于防抖
        now_wall = time.time()
        return (now_wall - last) < float(self.config.proactive_block_window_s)

    # ---------------- internal: primary stability ----------------
    def _debounced_primary(self, raw_primary: Optional[Any], now: float) -> Optional[Any]:
        """primary 防抖：只在 primary 稳定 >= primary_stable_s 才透传.

        策略：
        - 输入侧 raw primary id 与上次不同 → 记录 change_ts，但**不立即透传新 id**；
        - 若 raw primary id 持续 >= primary_stable_s，则把它作为 stable primary 透传；
        - 抖动期内透传上一次 stable primary（若仍存在）；
        - 完全没有 stable primary 时透传 None。

        防抖窗口 = 0 时退化为直通（每次输入即透传）。
        """
        raw_id = None
        if raw_primary is not None:
            try:
                raw_id = int(raw_primary.track_id)
            except (AttributeError, TypeError, ValueError):
                raw_id = None

        if raw_id != self._last_primary_id:
            self._last_primary_change_ts = now
            self._last_primary_id = raw_id

        if self.config.primary_stable_s <= 0.0:
            self._stable_primary_id = raw_id
            return raw_primary

        stable_elapsed = now - self._last_primary_change_ts
        if stable_elapsed >= self.config.primary_stable_s and raw_id is not None:
            # raw 已稳定，提升为 stable
            self._stable_primary_id = raw_id
            return raw_primary

        # 抖动期：返回先前 stable primary 对象（若可在 tracks 中找到）
        # 这里只能返回 raw_primary 或 None；为了让 mfa 的 _last_primary_id
        # 不在抖动期被反复重置，我们透传一个伪 primary 持有 stable id。
        if self._stable_primary_id is not None:
            return _SyntheticPrimary(self._stable_primary_id, raw_primary)
        return None

    # ---------------- internal: tick ----------------
    def _tick_once(self) -> Optional[GreetAction]:
        now = self._clock()
        raw_primary = self._resolve_primary()
        tracks = self._resolve_tracks()
        conv_state = self._resolve_conv_state()
        proactive_recent = self._resolve_proactive_recent()

        primary = self._debounced_primary(raw_primary, now)

        try:
            return self.mfa.tick(
                tracks=tracks,
                primary=primary,
                conv_state=conv_state,
                proactive_recent=proactive_recent,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[greet_wire] mfa.tick failed: %s: %s", type(e).__name__, e)
            return None

    def _loop(self) -> None:
        stop_event = self._stop_event
        assert stop_event is not None
        period = 1.0 / max(0.5, float(self.config.tick_hz))
        while not stop_event.is_set():
            try:
                self._tick_once()
            except Exception as e:  # noqa: BLE001
                log.warning("[greet_wire] loop tick exception: %s: %s", type(e).__name__, e)
            if stop_event.wait(timeout=period):
                break

    # ---------------- internal: callbacks ----------------
    def _on_greet_action(self, action: GreetAction) -> None:
        """触发 expression + tts."""
        # ExpressionPlayer 优先（短动作 + 表情），失败不阻断 tts
        if self.expression_player is not None:
            try:
                self.expression_player.play("greet")
            except Exception as e:  # noqa: BLE001
                log.warning("[greet_wire] expression.play failed: %s: %s", type(e).__name__, e)
        if self.tts_say_fn is not None:
            try:
                self.tts_say_fn(action.utterance)
            except Exception as e:  # noqa: BLE001
                log.warning("[greet_wire] tts.say failed: %s: %s", type(e).__name__, e)

    def _on_mfa_state_change(self, prev: MFAState, curr: MFAState) -> None:
        """状态机变更时 emit vision.multi_face_state_changed."""
        self._last_mfa_state = curr
        if self._emit_fn is None:
            return
        try:
            self._emit_fn(
                "vision.multi_face_state_changed",
                component="vision",
                prev=prev.value,
                curr=curr.value,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[greet_wire] emit state failed: %s: %s", type(e).__name__, e)


class _SyntheticPrimary:
    """抖动期透传的伪 primary：只暴露 track_id，与 mfa.tick 内 ``int(primary.track_id)``
    访问兼容；其他属性透明转发给原始 primary（可能为 None）。"""

    __slots__ = ("track_id", "_real")

    def __init__(self, track_id: int, real: Any) -> None:
        self.track_id = track_id
        self._real = real

    def __getattr__(self, name: str) -> Any:
        # __init__ 已设置 track_id 和 _real；其余从 _real 取
        real = object.__getattribute__(self, "_real")
        if real is None:
            raise AttributeError(name)
        return getattr(real, name)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_greet_secondary_wire(
    *,
    config: Optional[GreetSecondaryConfig] = None,
    attention_selector: Any = None,
    face_tracker: Any = None,
    tts_say_fn: Optional[Callable[[str], Any]] = None,
    expression_player: Any = None,
    conv_state_machine: Any = None,
    proactive_scheduler: Any = None,
    emit_fn: Optional[EmitFn] = None,
    clock: Optional[Callable[[], float]] = None,
) -> Optional[GreetSecondaryWire]:
    """主入口。``config.enabled=False`` 返回 None（零开销）。

    必要依赖（``attention_selector`` / ``face_tracker`` 任一为 None）时也返回
    None 并在日志中说明，避免在不完整运行时下空跑 daemon。
    """
    cfg = config if config is not None else greet_secondary_config_from_env()
    if not cfg.enabled:
        return None
    if attention_selector is None or face_tracker is None:
        log.warning(
            "[greet_wire] disabled: requires attention_selector and face_tracker "
            "(got %r / %r); set COCO_ATTENTION=1 + COCO_FACE_TRACK=1",
            attention_selector,
            face_tracker,
        )
        return None

    mfa_cfg = MFAConfig(
        enabled=True,
        silence_threshold_s=cfg.silence_threshold_s,
        secondary_visible_s=cfg.secondary_visible_s,
        greet_cooldown_s=cfg.cooldown_s,
        greet_duration_s=cfg.greet_duration_s,
        return_duration_s=cfg.return_duration_s,
        proactive_block_window_s=cfg.proactive_block_window_s,
        require_named_secondary=cfg.require_named_secondary,
    )
    mfa = MultiFaceAttention(
        config=mfa_cfg,
        clock=clock,
        emit_fn=emit_fn,
        utterance_template=cfg.utterance_template,
    )
    return GreetSecondaryWire(
        config=cfg,
        mfa=mfa,
        attention_selector=attention_selector,
        face_tracker=face_tracker,
        tts_say_fn=tts_say_fn,
        expression_player=expression_player,
        conv_state_machine=conv_state_machine,
        proactive_scheduler=proactive_scheduler,
        emit_fn=emit_fn,
        clock=clock,
    )


__all__ = [
    "GreetSecondaryConfig",
    "GreetSecondaryWire",
    "build_greet_secondary_wire",
    "greet_secondary_config_from_env",
]
