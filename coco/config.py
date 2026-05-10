"""infra-002: CocoConfig — 全仓 env 聚合层 + 单点 from_env。

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
from typing import Any, Dict, Mapping, Optional

log = logging.getLogger(__name__)


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

    # 各业务子 config 用 Any 占位以避免 import 时循环（运行期 from_env 注入）。
    vad: Any = None  # coco.vad_trigger.VADConfig
    vad_enabled: bool = True  # COCO_VAD_DISABLE 反义；默认 True
    wake: Any = None  # coco.wake_word.WakeConfig
    wake_enabled: bool = False  # COCO_WAKE_WORD；默认 False
    power: Any = None  # coco.power_state.PowerConfig
    power_idle_enabled: bool = False  # COCO_POWER_IDLE；默认 False
    dialog: Any = None  # coco.dialog.DialogConfig
    dialog_memory_enabled: bool = False  # COCO_DIALOG_MEMORY；默认 False


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


def load_config(env: Optional[Mapping[str, str]] = None) -> CocoConfig:
    """单点入口：env=None 时用 os.environ。

    为了**完全向后兼容**，业务子 config 直接调各模块原 helper（不复制 clamp 逻辑）。
    任一子模块 from_env 异常被吞，用该模块 dataclass 默认值兜底。
    """
    env = env if env is not None else os.environ

    # 业务子 config（按需 import 防循环）
    from coco import vad_trigger as _vad
    from coco import wake_word as _wake
    from coco import power_state as _power
    from coco import dialog as _dialog

    vad_cfg = _safe_call("vad", lambda: _vad.config_from_env()) or _vad.VADConfig()
    wake_cfg = _safe_call("wake", lambda: _wake.config_from_env()) or _wake.WakeConfig()
    # power.config_from_env 不接受 env 参数；它内部读 os.environ。本 helper 在
    # env != os.environ 时（test 注入）会读不到；这是已知限制（仅 verify 用 test
    # env，业务路径都是 os.environ）。
    power_cfg = _safe_call("power", lambda: _power.config_from_env()) or _power.PowerConfig()
    dialog_cfg = _safe_call("dialog", lambda: _dialog.config_from_env()) or _dialog.DialogConfig()

    vad_enabled = not _vad.vad_disabled_from_env() if env is os.environ else not _bool_env(env, "COCO_VAD_DISABLE", False)
    wake_enabled = _wake.wake_word_enabled_from_env() if env is os.environ else _bool_env(env, "COCO_WAKE_WORD", False)
    power_idle_enabled = _power.power_idle_enabled_from_env() if env is os.environ else _bool_env(env, "COCO_POWER_IDLE", False)
    dialog_memory_enabled = _dialog.dialog_memory_enabled_from_env() if env is os.environ else _bool_env(env, "COCO_DIALOG_MEMORY", False)

    return CocoConfig(
        log=_log_from_env(env),
        ptt=_ptt_from_env(env),
        camera=CameraConfig(spec=_str_env(env, "COCO_CAMERA")),
        llm=_llm_from_env(env),
        vad=vad_cfg,
        vad_enabled=vad_enabled,
        wake=wake_cfg,
        wake_enabled=wake_enabled,
        power=power_cfg,
        power_idle_enabled=power_idle_enabled,
        dialog=dialog_cfg,
        dialog_memory_enabled=dialog_memory_enabled,
    )


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
        "vad": {"enabled": cfg.vad_enabled, "config": _sub(cfg.vad)},
        "wake": {"enabled": cfg.wake_enabled, "config": _sub(cfg.wake)},
        "power": {"idle_enabled": cfg.power_idle_enabled, "config": _sub(cfg.power)},
        "dialog": {"memory_enabled": cfg.dialog_memory_enabled, "config": _sub(cfg.dialog)},
    }


__all__ = [
    "CocoConfig",
    "LogConfig",
    "PTTConfig",
    "CameraConfig",
    "LLMConfig",
    "load_config",
    "config_summary",
]
