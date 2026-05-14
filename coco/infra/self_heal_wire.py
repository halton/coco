"""infra-010: SelfHealRegistry reopen_fn 真实接线工厂.

把 infra-007 / infra-009 留下的占位 lambda（``lambda **kw: True``）替换成可调用的
真 reopen 路径。受 ``COCO_SELFHEAL_WIRE=1`` 默认 OFF gating；OFF 时维持
infra-007/009 行为（占位 + 一次性 WARN，消化 infra-009 caveat L1-1）。

三档回调
========

- ``audio_reopen_fn(**ctx)``：尝试关闭并重开 sounddevice 输入/输出 stream。
  当前 sim/CI 下没有真 stream 句柄，回调走 stub 路径：若 ``audio_handle`` 暴露
  ``reopen()`` / ``close()`` + ``open()`` 则真调；否则记 caveat log + 返回 True
  ("假装 reopen 成功"，与 OFF 占位等价但走真实代码路径以便观察)。
- ``asr_restart_fn(**ctx)``：与 ``OfflineDialogFallback`` 互通。在线 ASR 连续失败
  时由调用方触发 fallback；recover 时清 fallback 状态。在 sim/CI 下若 fallback
  handle 暴露 ``_enter_fallback`` / ``_exit_fallback`` 就调，否则同 stub。
- ``camera_reopen_fn(**ctx)``：尝试 ``CameraSource.release()`` 然后
  ``coco.perception.open_camera()`` 重开。sim 下 fake CameraSource 也走同路径。

设计要点
========

- **回调可调用 + 返回 bool**：任何 stub / 半接路径都要保证不抛、返回布尔，sim/CI
  必须能跑过 V4。
- **caveat 透明**：未真重启硬件的路径用 ``log.warning`` 一次 + emit
  ``self_heal.wire_stub`` 一次（component 字段 = audio/asr/camera），便于 evidence
  审计。
- **OFF 路径 WARN**：``COCO_SELFHEAL_WIRE`` 未设但 ``COCO_SELFHEAL=1`` 启用时，由
  ``main.py`` 走旧占位并打一条 WARN（infra-009 L1-1 第二半）。本模块不直接控制
  main.py 的 OFF 分支，但暴露 ``selfheal_wire_enabled_from_env()`` 给 main.py 用。

env
===

- ``COCO_SELFHEAL_WIRE=1`` 启用真实接线工厂；OFF 时 main.py 走占位 lambda 并 WARN。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from coco.logging_setup import emit as _root_emit


log = logging.getLogger(__name__)


__all__ = [
    "selfheal_wire_enabled_from_env",
    "build_real_reopen_callbacks",
    "compute_handle_status",
    "WireCallbacks",
]


# ---------------------------------------------------------------------------
# infra-012: handle status surface — 让 main.py 在 startup log 上打出
# "handles=N/3 (audio=<ok|stub>, asr=<ok|stub>, camera=<ok|stub>)"，一眼看出
# self_heal wire 接的是真句柄还是壳。
# ---------------------------------------------------------------------------


def compute_handle_status(
    *,
    audio_handle: Any = None,
    asr_handle: Any = None,
    camera_handle_ref: Any = None,
    offline_fallback: Any = None,
) -> Dict[str, str]:
    """计算三档 handle 是否真接。

    判定规则（保守）：
    - audio: ``audio_handle`` 非 None 且具备 reopen/close/open/stop_input/start_input
      任一方法 → "ok"；否则 "stub"。
    - asr: ``offline_fallback`` 非 None（拥有 _enter_fallback / _exit_fallback）
      或 ``asr_handle`` 提供 restart/reset → "ok"；否则 "stub"。
    - camera: ``camera_handle_ref`` 是 callable 或具备 ``__getitem__`` 的容器
      → "ok"；None → "stub"。

    返回 dict: {"audio": "ok|stub", "asr": "ok|stub", "camera": "ok|stub"}.
    """
    def _audio_ok() -> bool:
        if audio_handle is None:
            return False
        for attr in ("reopen", "close", "open", "stop_input", "start_input"):
            if hasattr(audio_handle, attr):
                return True
        return False

    def _asr_ok() -> bool:
        if offline_fallback is not None:
            if hasattr(offline_fallback, "_enter_fallback") or hasattr(
                offline_fallback, "_exit_fallback"
            ):
                return True
        if asr_handle is not None:
            if hasattr(asr_handle, "restart") or hasattr(asr_handle, "reset"):
                return True
        return False

    def _camera_ok() -> bool:
        if camera_handle_ref is None:
            return False
        return callable(camera_handle_ref) or hasattr(camera_handle_ref, "__getitem__")

    return {
        "audio": "ok" if _audio_ok() else "stub",
        "asr": "ok" if _asr_ok() else "stub",
        "camera": "ok" if _camera_ok() else "stub",
    }


# ---------------------------------------------------------------------------
# env
# ---------------------------------------------------------------------------


def selfheal_wire_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    e = env if env is not None else os.environ
    return (e.get("COCO_SELFHEAL_WIRE") or "0").strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _safe_emit(topic: str, **payload: Any) -> None:
    try:
        _root_emit(topic, **payload)
    except Exception as e:  # noqa: BLE001
        log.debug("[self_heal_wire] emit %s failed: %r", topic, e)


def _stub_once_logger() -> Callable[[str, str], None]:
    """每个 component 只 WARN + emit 一次 wire_stub，避免日志暴风。"""
    fired: Dict[str, bool] = {}

    def _fire(component: str, reason: str) -> None:
        if fired.get(component):
            return
        fired[component] = True
        log.warning(
            "[self_heal_wire] %s reopen stub: %s — caveat: not actually reopening hardware",
            component,
            reason,
        )
        _safe_emit("self_heal.wire_stub", component=component, reason=reason)

    return _fire


# ---------------------------------------------------------------------------
# 回调工厂
# ---------------------------------------------------------------------------


class WireCallbacks(Tuple[Callable[..., bool], Callable[..., bool], Callable[..., bool]]):
    """三档回调元组 (audio_reopen_fn, asr_restart_fn, camera_reopen_fn)。"""

    __slots__ = ()

    @property
    def audio(self) -> Callable[..., bool]:
        return self[0]

    @property
    def asr(self) -> Callable[..., bool]:
        return self[1]

    @property
    def camera(self) -> Callable[..., bool]:
        return self[2]


def build_real_reopen_callbacks(
    *,
    audio_handle: Any = None,
    asr_handle: Any = None,
    camera_handle_ref: Optional[Callable[[], Any]] = None,
    camera_spec: Optional[str] = None,
    offline_fallback: Any = None,
    emit_fn: Optional[Callable[..., None]] = None,
) -> WireCallbacks:
    """构造三档真 reopen 回调。

    Parameters
    ----------
    audio_handle:
        Audio 子系统对象。期望可选属性：
        - ``reopen()`` -> bool|None  （首选；一步到位）
        - 或 ``close()`` + ``open()``
        - 或 ``stop_input()`` + ``start_input()`` 等组合
        都没有时走 stub 路径。
    asr_handle:
        ASR 子系统对象（如 OnlineASRClient）。期望可选属性：
        - ``restart()`` -> bool|None
        - 或 ``reset()``
        都没有时走 stub。
    camera_handle_ref:
        返回当前 CameraSource 实例的 callable（lazy 引用，便于 reopen 后替换）。
        若提供，则会：当前 handle.release()；然后 ``open_camera(camera_spec)``。
        返回新 handle 不可写回（调用方需自己持有 ref；这里仅验证 read() 一次）。
    camera_spec:
        透传给 ``coco.perception.open_camera`` 的 spec；None 则用 ``COCO_CAMERA``。
    offline_fallback:
        ``OfflineDialogFallback`` 实例。期望属性：
        - ``_enter_fallback(latency_ms=)``  ASR fail → 切离线
        - ``_exit_fallback(latency_ms=)``   ASR recover → 切回
        - ``is_in_fallback()``              当前是否在 fallback
        没有时走 stub。
    emit_fn:
        emit 函数（默认 coco.logging_setup.emit）。

    Returns
    -------
    WireCallbacks
        三个可调用对象，每个签名都接受 ``**ctx`` 并返回 ``bool``。
        ctx 里若含 ``failure_kind`` 表示 self_heal dispatch 触发；
        若含 ``recover=True`` 表示尝试 recover 路径（ASR 专用）。
    """
    emit = emit_fn or _safe_emit
    stub_log = _stub_once_logger()

    def _audio_reopen(**ctx: Any) -> bool:
        component = "audio"
        h = audio_handle
        if h is None:
            stub_log(component, "audio_handle is None")
            emit("self_heal.component_attempt", component=component, path="stub_none")
            return True
        # 首选 reopen()
        try:
            if hasattr(h, "reopen"):
                r = h.reopen()
                emit("self_heal.component_attempt", component=component, path="reopen")
                return bool(r) if r is not None else True
        except Exception as e:  # noqa: BLE001
            log.debug("[self_heal_wire] audio.reopen() raised: %r", e)
            emit("self_heal.component_attempt", component=component, path="reopen_raised",
                 error=type(e).__name__)
            return False
        # close+open
        try:
            closed = False
            if hasattr(h, "close"):
                h.close()
                closed = True
            opened = False
            if hasattr(h, "open"):
                h.open()
                opened = True
            if closed or opened:
                emit("self_heal.component_attempt", component=component, path="close_open")
                return True
        except Exception as e:  # noqa: BLE001
            log.debug("[self_heal_wire] audio.close/open raised: %r", e)
            emit("self_heal.component_attempt", component=component, path="close_open_raised",
                 error=type(e).__name__)
            return False
        # stop_input/start_input
        try:
            stopped = False
            if hasattr(h, "stop_input"):
                h.stop_input()
                stopped = True
            started = False
            if hasattr(h, "start_input"):
                h.start_input()
                started = True
            if stopped or started:
                emit("self_heal.component_attempt", component=component, path="stop_start")
                return True
        except Exception as e:  # noqa: BLE001
            log.debug("[self_heal_wire] audio.stop/start raised: %r", e)
            emit("self_heal.component_attempt", component=component, path="stop_start_raised",
                 error=type(e).__name__)
            return False
        stub_log(component, "audio_handle has no reopen/close/open/stop_input/start_input")
        emit("self_heal.component_attempt", component=component, path="stub_no_method")
        return True

    def _asr_restart(**ctx: Any) -> bool:
        component = "asr"
        recover = bool(ctx.get("recover", False))
        # 1) 与 offline_fallback 互通
        fb = offline_fallback
        if fb is not None:
            try:
                if recover:
                    if hasattr(fb, "is_in_fallback") and fb.is_in_fallback():
                        if hasattr(fb, "_exit_fallback"):
                            fb._exit_fallback(latency_ms=0.0)
                            emit("self_heal.component_attempt", component=component, path="fallback_exit")
                            return True
                    emit("self_heal.component_attempt", component=component, path="fallback_no_action")
                    return True
                # 触发 fallback
                if hasattr(fb, "is_in_fallback") and not fb.is_in_fallback():
                    if hasattr(fb, "_enter_fallback"):
                        fb._enter_fallback(latency_ms=0.0)
                        emit("self_heal.component_attempt", component=component, path="fallback_enter")
                        emit("interact.offline_fallback", source="self_heal_wire")
                        return True
                emit("self_heal.component_attempt", component=component, path="fallback_already")
                return True
            except Exception as e:  # noqa: BLE001
                log.debug("[self_heal_wire] offline_fallback toggle raised: %r", e)
                emit("self_heal.component_attempt", component=component, path="fallback_raised",
                     error=type(e).__name__)
                return False
        # 2) 直接调 asr_handle.restart()/reset()
        h = asr_handle
        if h is not None:
            try:
                if hasattr(h, "restart"):
                    r = h.restart()
                    emit("self_heal.component_attempt", component=component, path="restart")
                    return bool(r) if r is not None else True
                if hasattr(h, "reset"):
                    r = h.reset()
                    emit("self_heal.component_attempt", component=component, path="reset")
                    return bool(r) if r is not None else True
            except Exception as e:  # noqa: BLE001
                log.debug("[self_heal_wire] asr_handle restart/reset raised: %r", e)
                emit("self_heal.component_attempt", component=component, path="asr_raised",
                     error=type(e).__name__)
                return False
        stub_log(component, "no offline_fallback and asr_handle has no restart/reset")
        emit("self_heal.component_attempt", component=component, path="stub")
        return True

    def _camera_reopen(**ctx: Any) -> bool:
        component = "camera"
        # infra-012: 解决 USB 独占 + ref 回写。
        # camera_handle_ref 支持两种形态：
        #   1) callable() -> CameraSource | None   （只读 ref，不能回写新 handle；
        #      这种情况下我们不能持有新 handle，必须 release 再让上游重新构造，
        #      所以走 read-probe 验活后 release 临时 handle）
        #   2) mutable container（list/dict）：传 list 时 ref[0] 是当前 handle；
        #      我们 release 老 → open 新 → 写回 ref[0]，保持 USB 独占被一个 handle
        #      持有，避免每次 reopen 都临时多开一个 (USB camera 真机会撞)。
        is_mutable_ref = (
            camera_handle_ref is not None
            and not callable(camera_handle_ref)
            and hasattr(camera_handle_ref, "__getitem__")
            and hasattr(camera_handle_ref, "__setitem__")
        )
        # 取当前 handle
        h = None
        if camera_handle_ref is not None:
            try:
                if is_mutable_ref:
                    h = camera_handle_ref[0]
                else:
                    h = camera_handle_ref()
            except Exception as e:  # noqa: BLE001
                log.debug("[self_heal_wire] camera_handle_ref raised: %r", e)
                emit("self_heal.component_attempt", component=component, path="ref_raised",
                     error=type(e).__name__)
                return False
        # 先 release 老 handle —— 否则 USB 独占下 open 新 handle 会失败
        if h is not None:
            try:
                if hasattr(h, "release"):
                    h.release()
            except Exception as e:  # noqa: BLE001
                log.debug("[self_heal_wire] camera.release raised: %r", e)
                # 释放失败不致命，继续 reopen
        # open 新
        try:
            from coco.perception import open_camera as _open_camera
            new_cam = _open_camera(camera_spec)
            # 一次 read 验活
            try:
                ok, frame = new_cam.read()
            except Exception as e:  # noqa: BLE001
                log.debug("[self_heal_wire] new camera.read raised: %r", e)
                emit("self_heal.component_attempt", component=component, path="read_raised",
                     error=type(e).__name__)
                # 验活失败也要释放，避免 USB 泄漏
                try:
                    if hasattr(new_cam, "release"):
                        new_cam.release()
                except Exception:  # noqa: BLE001
                    pass
                return False
            if is_mutable_ref:
                # 写回 ref；保留新 handle，由 ref 持有方负责后续 release。
                try:
                    camera_handle_ref[0] = new_cam
                except Exception as e:  # noqa: BLE001
                    log.debug("[self_heal_wire] camera_handle_ref writeback raised: %r", e)
                    # 写回失败 → 释放避免 USB 泄漏
                    try:
                        if hasattr(new_cam, "release"):
                            new_cam.release()
                    except Exception:  # noqa: BLE001
                        pass
                    emit("self_heal.component_attempt", component=component,
                         path="ref_writeback_raised", error=type(e).__name__)
                    return False
                emit("self_heal.component_attempt", component=component,
                     path="reopened_ref_writeback", read_ok=bool(ok))
                return bool(ok)
            # callable / None ref：临时 handle，释放避免 USB 泄漏
            emit("self_heal.component_attempt", component=component,
                 path="reopened_probe_release", read_ok=bool(ok))
            try:
                if hasattr(new_cam, "release"):
                    new_cam.release()
            except Exception:  # noqa: BLE001
                pass
            return bool(ok)
        except Exception as e:  # noqa: BLE001
            log.debug("[self_heal_wire] open_camera raised: %r", e)
            emit("self_heal.component_attempt", component=component, path="open_raised",
                 error=type(e).__name__)
            return False

    return WireCallbacks((_audio_reopen, _asr_restart, _camera_reopen))
