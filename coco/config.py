"""infra-002: CocoConfig — 全仓 env 聚合层 + 单点 from_env。

TODO (phase-4 known-debt L2-1)：当前实现是"聚合不替代" —— 各子模块仍保留
``config_from_env()`` helper，业务代码可绕过 CocoConfig 直接调它们。后续
feature 接入时应逐步把读取路径归口到 CocoConfig，让本文件成为唯一入口。

设计原则
========

1. **聚合不替代**：保留各模块原有 ``config_from_env()`` / ``*_enabled_from_env()``
   helper（dialog / power / vad / wake / llm 等），CocoConfig.from_env() 只是
   把它们调一遍打包成一份 frozen dataclass。这样：

   - 现有 verify_*.py / smoke 不动一行就过；
   - 旧 helper 仍是 import 路径；
   - 默认值 / clamp 区间唯一来源仍在各模块（不重复维护）。

2. **frozen dataclass**：CocoConfig 不可变，防业务代码乱改。

3. **每个字段独立 try**：一个子模块 from_env() 抛异常不污染其他子模块（fail-soft；
   异常被吞 + log.warning + 用该模块默认值兜底）。

4. **config_summary()**：给启动 banner 用，输出 dict（不含 secret，例如
   COCO_LLM_API_KEY 仅以 ``set/unset`` 表示）。

5. **logging_setup 配套**：``LogConfig`` 单字段（jsonl + level + path），由
   coco/logging_setup.setup_logging 消费。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

log = logging.getLogger(__name__)


class ConfigValidationError(ValueError):
    """infra-004: validate_config 检出 error 级别问题时抛出。

    携带完整 issues list（含 error / warning / info），便于上层日志。
    """

    def __init__(self, issues):  # type: ignore[no-untyped-def]
        self.issues = list(issues)
        errs = [m for sev, m in self.issues if sev == "error"]
        super().__init__("config validation failed: " + "; ".join(errs))


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LogConfig:
    """COCO_LOG_JSONL=1 启用 jsonl 模式；COCO_LOG_LEVEL 默认 INFO。"""

    jsonl: bool = False
    level: str = "INFO"


@dataclass(frozen=True)
class PTTConfig:
    seconds: float = 4.0
    disabled: bool = False


@dataclass(frozen=True)
class CameraConfig:
    """COCO_CAMERA spec；为空表示 'usb:0' 默认。"""

    spec: str = ""


@dataclass(frozen=True)
class LLMConfig:
    backend: str = ""
    timeout: float = 2.0
    max_chars: int = 60
    base_url: str = ""
    model: str = ""
    api_key_set: bool = False  # 仅记是否已设，不存 secret


@dataclass(frozen=True)
class MetricsConfig:
    """COCO_METRICS=1 启用；COCO_METRICS_INTERVAL 秒（clamp [1,300]）；
    COCO_METRICS_PATH 输出 jsonl 路径（默认 ~/.cache/coco/metrics.jsonl）。"""

    enabled: bool = False
    interval_s: float = 5.0
    path: str = ""


@dataclass(frozen=True)
class AttentionConfig:
    """vision-004：多目标人脸注视切换配置。

    - COCO_ATTENTION=1 启用（默认 OFF）
    - COCO_ATTENTION_POLICY ∈ round_robin / largest_face / newest / named_first（默认 round_robin）
    - COCO_ATTENTION_MIN_FOCUS_S clamp [0.0, 60.0]，默认 3.0
    - COCO_ATTENTION_SWITCH_COOLDOWN_S clamp [0.0, 60.0]，默认 1.0
    - COCO_ATTENTION_INTERVAL_MS clamp [50, 2000]，默认 200（~5Hz）
    """

    enabled: bool = False
    policy: str = "round_robin"
    min_focus_s: float = 3.0
    switch_cooldown_s: float = 1.0
    interval_ms: int = 200


# 业务子 config（来自各模块 dataclass，避免重复定义；这里只引类型）
# 在 __init__ 时按需 import，防循环。


@dataclass(frozen=True)
class CocoConfig:
    """全仓配置聚合。

    字段命名空间与 phase-3 模块 config 一一对应；新字段（log / ptt / camera /
    llm）首次落地于 infra-002。
    """

    log: LogConfig = field(default_factory=LogConfig)
    ptt: PTTConfig = field(default_factory=PTTConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    attention: AttentionConfig = field(default_factory=AttentionConfig)

    # 各业务子 config 用 Any 占位以避免 import 时循环（运行期 from_env 注入）。
    vad: Any = None  # coco.vad_trigger.VADConfig
    vad_enabled: bool = True  # COCO_VAD_DISABLE 反义；默认 True
    wake: Any = None  # coco.wake_word.WakeConfig
    wake_enabled: bool = False  # COCO_WAKE_WORD；默认 False
    power: Any = None  # coco.power_state.PowerConfig
    power_idle_enabled: bool = False  # COCO_POWER_IDLE；默认 False
    dialog: Any = None  # coco.dialog.DialogConfig
    dialog_memory_enabled: bool = False  # COCO_DIALOG_MEMORY；默认 False
    dialog_summary: Any = None  # coco.dialog_summary.DialogSummaryConfig (interact-009)
    emotion: Any = None  # coco.emotion.EmotionConfig
    emotion_enabled: bool = False  # COCO_EMOTION；默认 False
    intent: Any = None  # coco.intent.IntentConfig
    intent_enabled: bool = False  # COCO_INTENT；默认 False
    conversation: Any = None  # coco.conversation.ConversationConfig
    # robot-003: ExpressionsConfig（默认 OFF）
    expressions: Any = None  # coco.robot.expressions.ExpressionsConfig
    # vision-005: GestureConfig（默认 OFF）
    gesture: Any = None  # coco.perception.gesture.GestureConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        log.warning("[config] %s=%r 非数字，回退默认 %.2f", key, raw, default)
        return default
    if v < lo:
        log.warning("[config] %s=%.2f <%.2f，clamp 到 %.2f", key, v, lo, lo)
        return lo
    if v > hi:
        log.warning("[config] %s=%.2f >%.2f，clamp 到 %.2f", key, v, hi, hi)
        return hi
    return v


def _str_env(env: Mapping[str, str], key: str, default: str = "") -> str:
    raw = env.get(key)
    if raw is None:
        return default
    return raw.strip()


# ---------------------------------------------------------------------------
# from_env — 单点入口
# ---------------------------------------------------------------------------


def _safe_call(label: str, fn):  # type: ignore[no-untyped-def]
    """各子模块 from_env 出错时 fail-soft，返回 None。"""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log.warning("[config] %s from_env failed: %s: %s — 用模块默认", label, type(e).__name__, e)
        return None


def _llm_from_env(env: Mapping[str, str]) -> LLMConfig:
    backend = _str_env(env, "COCO_LLM_BACKEND").lower()
    timeout = _float_env(env, "COCO_LLM_TIMEOUT", default=2.0, lo=0.1, hi=120.0)
    raw_max = env.get("COCO_LLM_MAX_CHARS")
    max_chars = 60
    if raw_max is not None and raw_max != "":
        try:
            max_chars = int(raw_max)
            if max_chars < 1:
                log.warning("[config] COCO_LLM_MAX_CHARS=%d <1，clamp 1", max_chars)
                max_chars = 1
            elif max_chars > 4096:
                log.warning("[config] COCO_LLM_MAX_CHARS=%d >4096，clamp 4096", max_chars)
                max_chars = 4096
        except ValueError:
            log.warning("[config] COCO_LLM_MAX_CHARS=%r 非整数，回退 60", raw_max)
            max_chars = 60
    base_url = _str_env(env, "COCO_LLM_BASE_URL")
    model = _str_env(env, "COCO_LLM_MODEL")
    api_key_set = bool(_str_env(env, "COCO_LLM_API_KEY"))
    return LLMConfig(
        backend=backend,
        timeout=timeout,
        max_chars=max_chars,
        base_url=base_url,
        model=model,
        api_key_set=api_key_set,
    )


def _ptt_from_env(env: Mapping[str, str]) -> PTTConfig:
    seconds = _float_env(env, "COCO_PTT_SECONDS", default=4.0, lo=0.1, hi=60.0)
    disabled = (env.get("COCO_PTT_DISABLE") or "0").strip() == "1"
    return PTTConfig(seconds=seconds, disabled=disabled)


def _log_from_env(env: Mapping[str, str]) -> LogConfig:
    jsonl = _bool_env(env, "COCO_LOG_JSONL", default=False)
    level = _str_env(env, "COCO_LOG_LEVEL", "INFO").upper() or "INFO"
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        log.warning("[config] COCO_LOG_LEVEL=%r 非法，回退 INFO", level)
        level = "INFO"
    return LogConfig(jsonl=jsonl, level=level)


def _metrics_from_env(env: Mapping[str, str]) -> MetricsConfig:
    enabled = _bool_env(env, "COCO_METRICS", default=False)
    interval_s = _float_env(env, "COCO_METRICS_INTERVAL", default=5.0, lo=1.0, hi=300.0)
    path = _str_env(env, "COCO_METRICS_PATH")
    return MetricsConfig(enabled=enabled, interval_s=interval_s, path=path)


_ATTENTION_VALID_POLICIES = {"round_robin", "largest_face", "newest", "named_first"}


def _attention_from_env(env: Mapping[str, str]) -> AttentionConfig:
    enabled = _bool_env(env, "COCO_ATTENTION", False)
    policy = _str_env(env, "COCO_ATTENTION_POLICY", "round_robin").lower() or "round_robin"
    if policy not in _ATTENTION_VALID_POLICIES:
        log.warning("[config] COCO_ATTENTION_POLICY=%r 非法，回退 round_robin", policy)
        policy = "round_robin"
    min_focus_s = _float_env(env, "COCO_ATTENTION_MIN_FOCUS_S", default=3.0, lo=0.0, hi=60.0)
    switch_cooldown_s = _float_env(env, "COCO_ATTENTION_SWITCH_COOLDOWN_S",
                                   default=1.0, lo=0.0, hi=60.0)
    raw_interval = env.get("COCO_ATTENTION_INTERVAL_MS")
    interval_ms = 200
    if raw_interval is not None and raw_interval != "":
        try:
            interval_ms = int(raw_interval)
        except ValueError:
            log.warning("[config] COCO_ATTENTION_INTERVAL_MS=%r 非整数，回退 200", raw_interval)
            interval_ms = 200
    if interval_ms < 50:
        log.warning("[config] COCO_ATTENTION_INTERVAL_MS=%d <50，clamp 50", interval_ms)
        interval_ms = 50
    elif interval_ms > 2000:
        log.warning("[config] COCO_ATTENTION_INTERVAL_MS=%d >2000，clamp 2000", interval_ms)
        interval_ms = 2000
    return AttentionConfig(
        enabled=enabled,
        policy=policy,
        min_focus_s=min_focus_s,
        switch_cooldown_s=switch_cooldown_s,
        interval_ms=interval_ms,
    )


def load_config(env: Optional[Mapping[str, str]] = None) -> CocoConfig:
    """单点入口：env=None 时用 os.environ。

    为了**完全向后兼容**，业务子 config 直接调各模块原 helper（不复制 clamp 逻辑）。
    任一子模块 from_env 异常被吞，用该模块 dataclass 默认值兜底。

    **已知限制（phase-4 known-debt L2-2）**：``env=`` 注入仅覆盖 *本文件直管* 的字段
    （``log`` / ``ptt`` / ``camera`` / ``llm`` / 各子系统 enabled 标志位）。子模块
    dataclass 字段（如 ``DialogConfig.max_turns`` 通过 ``COCO_DIALOG_MAX_TURNS``、
    ``PowerConfig.*`` 通过 ``COCO_POWER_*``、``WakeConfig.*``、``VADConfig.*``）
    走各自模块的 ``config_from_env()``，那里直接读 ``os.environ``，不接受注入 env。
    所以 ``load_config(env={"COCO_DIALOG_MAX_TURNS": "7"})`` 不会改 ``cfg.dialog.max_turns``，
    后者仍是 ``DialogConfig`` 默认或 ``os.environ`` 的值。verify V11 锁住此行为；
    要改请重写各子模块 ``config_from_env(env=...)`` 签名（独立 feature）。
    """
    env = env if env is not None else os.environ

    # 业务子 config（按需 import 防循环）
    from coco import vad_trigger as _vad
    from coco import wake_word as _wake
    from coco import power_state as _power
    from coco import dialog as _dialog
    from coco import emotion as _emotion
    from coco import intent as _intent
    from coco import conversation as _conversation

    vad_cfg = _safe_call("vad", lambda: _vad.config_from_env()) or _vad.VADConfig()
    wake_cfg = _safe_call("wake", lambda: _wake.config_from_env()) or _wake.WakeConfig()
    # power.config_from_env 不接受 env 参数；它内部读 os.environ。本 helper 在
    # env != os.environ 时（test 注入）会读不到；这是已知限制（仅 verify 用 test
    # env，业务路径都是 os.environ）。
    power_cfg = _safe_call("power", lambda: _power.config_from_env()) or _power.PowerConfig()
    dialog_cfg = _safe_call("dialog", lambda: _dialog.config_from_env()) or _dialog.DialogConfig()
    # interact-009: dialog_summary（默认 enabled=False）
    try:
        from coco import dialog_summary as _dialog_summary
        dialog_summary_cfg = _safe_call(
            "dialog_summary", lambda: _dialog_summary.config_from_env(env)
        ) or _dialog_summary.DialogSummaryConfig()
    except Exception as ex:  # noqa: BLE001
        log.warning("[config] dialog_summary import failed: %s: %s", type(ex).__name__, ex)
        dialog_summary_cfg = None
    # emotion.config_from_env 接受 env 参数；env 注入即可生效（与 phase-4 known-debt L2-2 不同）
    emotion_cfg = _safe_call("emotion", lambda: _emotion.config_from_env(env)) or _emotion.EmotionConfig()

    vad_enabled = not _vad.vad_disabled_from_env() if env is os.environ else not _bool_env(env, "COCO_VAD_DISABLE", False)
    wake_enabled = _wake.wake_word_enabled_from_env() if env is os.environ else _bool_env(env, "COCO_WAKE_WORD", False)
    power_idle_enabled = _power.power_idle_enabled_from_env() if env is os.environ else _bool_env(env, "COCO_POWER_IDLE", False)
    dialog_memory_enabled = _dialog.dialog_memory_enabled_from_env() if env is os.environ else _bool_env(env, "COCO_DIALOG_MEMORY", False)
    emotion_enabled = _emotion.emotion_enabled_from_env(env)
    intent_cfg = _safe_call("intent", lambda: _intent.config_from_env(env)) or _intent.IntentConfig()
    intent_enabled = _intent.intent_enabled_from_env(env)
    conversation_cfg = _safe_call("conversation", lambda: _conversation.config_from_env(env)) or _conversation.ConversationConfig()

    # robot-003: ExpressionsConfig
    try:
        from coco.robot.expressions import (
            expressions_config_from_env as _expr_from_env,
            ExpressionsConfig as _ExpressionsConfig,
        )
        expr_cfg = _safe_call("expressions", lambda: _expr_from_env(env)) or _ExpressionsConfig()
    except Exception as e:  # noqa: BLE001
        log.warning("[config] expressions module import failed: %s: %s", type(e).__name__, e)
        expr_cfg = None

    # vision-005: GestureConfig（默认 OFF）
    try:
        from coco.perception.gesture import (
            gesture_config_from_env as _gesture_from_env,
            GestureConfig as _GestureConfig,
        )
        gesture_cfg = _safe_call("gesture", lambda: _gesture_from_env(env)) or _GestureConfig()
    except Exception as e:  # noqa: BLE001
        log.warning("[config] gesture module import failed: %s: %s", type(e).__name__, e)
        gesture_cfg = None

    cfg = CocoConfig(
        log=_log_from_env(env),
        ptt=_ptt_from_env(env),
        camera=CameraConfig(spec=_str_env(env, "COCO_CAMERA")),
        llm=_llm_from_env(env),
        metrics=_metrics_from_env(env),
        attention=_attention_from_env(env),
        vad=vad_cfg,
        vad_enabled=vad_enabled,
        wake=wake_cfg,
        wake_enabled=wake_enabled,
        power=power_cfg,
        power_idle_enabled=power_idle_enabled,
        dialog=dialog_cfg,
        dialog_memory_enabled=dialog_memory_enabled,
        dialog_summary=dialog_summary_cfg,
        emotion=emotion_cfg,
        emotion_enabled=emotion_enabled,
        intent=intent_cfg,
        intent_enabled=intent_enabled,
        conversation=conversation_cfg,
        expressions=expr_cfg,
        gesture=gesture_cfg,
    )
    # infra-004: 跨字段 / 路径 / 不兼容组合校验。error 抛 ConfigValidationError；
    # warning / info 只写日志。
    issues = validate_config(cfg, env=env)
    for sev, msg in issues:
        if sev == "error":
            log.error("[config] %s", msg)
        elif sev == "warning":
            log.warning("[config] %s", msg)
        else:
            log.info("[config] %s", msg)
    if any(sev == "error" for sev, _ in issues):
        raise ConfigValidationError(issues)
    return cfg


# ---------------------------------------------------------------------------
# validate_config — infra-004 schema 校验
# ---------------------------------------------------------------------------


def validate_config(cfg: CocoConfig, env: Optional[Mapping[str, str]] = None) -> list:
    """检出 cfg 中的跨字段不一致 / 路径不可写 / 不兼容组合。

    返回 list[(severity, message)]：severity ∈ {"error", "warning", "info"}。
    主调用方（load_config）负责落日志 + 决定是否抛 ConfigValidationError。

    设计原则：
    - error：必然导致运行时崩溃或语义错乱（如 drowsy>=sleep，metrics.path 不可写）
    - warning：可运行但用户可能不希望（如 proactive 开但 intent 关，导致 QUIET 失效）
    - info：纯提示（如 jsonl=False 时 rotate 配置被忽略）

    所有检查 try/except 包住，单个检查异常不会让 validate_config 自己崩。
    """
    issues: list = []
    e = env if env is not None else os.environ

    # 1. cross-field: power drowsy < sleep（双保险——PowerConfig 自身已检查）
    try:
        if cfg.power is not None:
            d = float(getattr(cfg.power, "drowsy_after", 0.0))
            s = float(getattr(cfg.power, "sleep_after", 0.0))
            if d >= s:
                issues.append(("error",
                    f"power.drowsy_after={d} 必须 < power.sleep_after={s}"))
    except Exception as ex:  # noqa: BLE001
        issues.append(("warning", f"power cross-check skipped: {ex!r}"))

    # 2. metrics.path 父目录可创建/可写
    try:
        if cfg.metrics.enabled:
            p_raw = cfg.metrics.path or ""
            if p_raw:
                p = Path(os.path.expanduser(p_raw))
            else:
                p = Path.home() / ".cache" / "coco" / "metrics.jsonl"
            parent = p.parent
            try:
                parent.mkdir(parents=True, exist_ok=True)
                if not os.access(parent, os.W_OK):
                    issues.append(("error",
                        f"metrics.path 父目录不可写 path={p} parent={parent}"))
            except (OSError, PermissionError) as ex:
                issues.append(("error",
                    f"metrics.path 父目录无法创建 path={p}: {ex!r}"))
    except Exception as ex:  # noqa: BLE001
        issues.append(("warning", f"metrics.path check skipped: {ex!r}"))

    # 3. proactive=1 + intent=0：QUIET 期间 proactive 不会被静音，可能扰民
    try:
        proactive_enabled = (e.get("COCO_PROACTIVE") or "0").strip().lower() in {"1", "true", "yes", "on"}
        if proactive_enabled and not cfg.intent_enabled:
            issues.append(("warning",
                "COCO_PROACTIVE=1 但 COCO_INTENT=0：QUIET 期间主动话题不会被静音"))
    except Exception as ex:  # noqa: BLE001
        issues.append(("warning", f"proactive/intent combo check skipped: {ex!r}"))

    # 4. attention=1 但 face_track=0：AttentionSelector 启动时会被 main 跳过
    try:
        att_on = cfg.attention.enabled
        face_track = (e.get("COCO_FACE_TRACK") or "0").strip().lower() in {"1", "true", "yes", "on"}
        if att_on and not face_track:
            issues.append(("warning",
                "COCO_ATTENTION=1 但 COCO_FACE_TRACK=0：AttentionSelector 不会启动"))
    except Exception as ex:  # noqa: BLE001
        issues.append(("warning", f"attention/face_track combo check skipped: {ex!r}"))

    # 5. wake_word=1 但 vad disabled：wake-word 需要 VAD 才能产生 utterance
    try:
        if cfg.wake_enabled and not cfg.vad_enabled:
            issues.append(("warning",
                "COCO_WAKE_WORD=1 但 COCO_VAD_DISABLE=1：wake-word 没有 VAD 兜底"))
    except Exception as ex:  # noqa: BLE001
        issues.append(("warning", f"wake/vad combo check skipped: {ex!r}"))

    # 6. info: jsonl=False 时 rotate 配置不生效
    try:
        if not cfg.log.jsonl and (e.get("COCO_LOG_MAX_MB") or "").strip():
            issues.append(("info",
                "COCO_LOG_MAX_MB 设置了但 COCO_LOG_JSONL=0：rotate 仅对 jsonl 文件输出生效"))
    except Exception as ex:  # noqa: BLE001
        issues.append(("info", f"rotate config check skipped: {ex!r}"))

    return issues


# ---------------------------------------------------------------------------
# config_summary — 启动 banner 用
# ---------------------------------------------------------------------------


def config_summary(cfg: CocoConfig) -> Dict[str, Any]:
    """返回不含 secret 的 dict（COCO_LLM_API_KEY 只以 set/unset 表示）。

    用于启动 banner / verify 检查。字段稳定；新增字段需追加而不是 rename。
    """

    def _sub(obj: Any) -> Any:
        if obj is None:
            return None
        if hasattr(obj, "__dataclass_fields__"):
            return asdict(obj)
        return repr(obj)

    return {
        "log": asdict(cfg.log),
        "ptt": asdict(cfg.ptt),
        "camera": asdict(cfg.camera),
        "llm": {
            "backend": cfg.llm.backend or "(unset)",
            "timeout": cfg.llm.timeout,
            "max_chars": cfg.llm.max_chars,
            "base_url": cfg.llm.base_url or "(unset)",
            "model": cfg.llm.model or "(unset)",
            "api_key": "set" if cfg.llm.api_key_set else "unset",
        },
        "metrics": asdict(cfg.metrics),
        "attention": asdict(cfg.attention),
        "vad": {"enabled": cfg.vad_enabled, "config": _sub(cfg.vad)},
        "wake": {"enabled": cfg.wake_enabled, "config": _sub(cfg.wake)},
        "power": {"idle_enabled": cfg.power_idle_enabled, "config": _sub(cfg.power)},
        "dialog": {"memory_enabled": cfg.dialog_memory_enabled, "config": _sub(cfg.dialog)},
        "dialog_summary": {"config": _sub(cfg.dialog_summary)},
        "emotion": {"enabled": cfg.emotion_enabled, "config": _sub(cfg.emotion)},
        "intent": {"enabled": cfg.intent_enabled, "config": _sub(cfg.intent)},
        "conversation": {"config": _sub(cfg.conversation)},
        "expressions": {"config": _sub(cfg.expressions)},
        "gesture": {"config": _sub(cfg.gesture)},
    }


__all__ = [
    "CocoConfig",
    "ConfigValidationError",
    "LogConfig",
    "PTTConfig",
    "CameraConfig",
    "LLMConfig",
    "MetricsConfig",
    "AttentionConfig",
    "load_config",
    "config_summary",
    "validate_config",
]
