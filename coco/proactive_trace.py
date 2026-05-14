"""interact-015: ProactiveScheduler 仲裁链可观察性 + mm_proactive LLM 用量监控.

提供两类观测能力，全部 default-OFF（env 未设时所有 API 为 no-op，无 IO/无 emit/
无 state 变更，与 main HEAD 字节级等价）：

(a) ``proactive.trace`` —— ProactiveScheduler 仲裁链各 stage 决策追踪。

    gate: ``COCO_PROACTIVE_TRACE=1``

    stages（与 ProactiveScheduler 既有路径对齐）::

        emotion_alert    record_emotion_alert_trigger 入口（独立路径）
        fusion_boost     maybe_trigger 检测到 _next_priority_boost
        mm_proactive     maybe_trigger 检测到 mm_llm_context
        normal           普通无 boost / 无 mm 的 idle 路径
        cooldown_hit     boost 在但 since < cooldown 抑制
        arbit_winner     仲裁胜者（最终 trigger 路径标识）

    decisions: ``admit`` / ``reject``；reject 同时附 ``reason``（与 _should_trigger
    既有 reason 字符串一致：disabled / paused / quiet_state / power / no_face /
    idle / cooldown / rate_limit / arbit_emotion_preempt）。

    候选标识 ``candidate_id`` 由 maybe_trigger 入口生成（``str(int(t*1000))``），
    同帧多 stage 共享同一 id，离线可重建一次完整决策路径。

(b) ``llm.usage`` —— mm_proactive LLM 调用用量计量。

    gate: ``COCO_LLM_USAGE_LOG=1``

    emit 字段: ``component=mm_proactive`` / ``prompt_tokens`` / ``completion_tokens``
    / ``ts``；同时滚动落盘 ``~/.coco/llm_usage_<YYYYMMDD>.jsonl``（按本地日期）。

    spec 限定：当前 LLMReply.reply 不返回精确 token，本模块按字符数估算
    （``tokens = max(1, chars // 2)``）。fixture / scripts/proactive_trace_summary.py
    可注入精确值覆盖估算（payload 透传，估算只在 estimate=True 时启用）。

两个 gate 互相独立；既不互相依赖，也不依赖 ``COCO_PROACTIVE_ARBIT``（trace 只观测，
不改决策；用量监控只对 mm 路径生效）。
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

log = logging.getLogger("coco.proactive_trace")


# 已知 stage（仅作 schema 提示；模块不强校验，未知 stage 仍 emit）
KNOWN_STAGES = frozenset(
    {
        "emotion_alert",
        "fusion_boost",
        "mm_proactive",
        "normal",
        "cooldown_hit",
        "arbit_winner",
    }
)
KNOWN_DECISIONS = frozenset({"admit", "reject"})


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    s = str(raw).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return default


def trace_enabled_from_env() -> bool:
    """interact-015: COCO_PROACTIVE_TRACE 开关；default-OFF。"""
    return _bool_env("COCO_PROACTIVE_TRACE", default=False)


def llm_usage_log_enabled_from_env() -> bool:
    """interact-015: COCO_LLM_USAGE_LOG 开关；default-OFF。"""
    return _bool_env("COCO_LLM_USAGE_LOG", default=False)


# 测试可注入的 emit 钩子；None 时延迟 import logging_setup.emit
_EMIT_OVERRIDE: Optional[Callable[..., None]] = None


def set_emit_override(fn: Optional[Callable[..., None]]) -> None:
    """测试 hook：注入自定义 emit（None 还原为 logging_setup.emit）。"""
    global _EMIT_OVERRIDE
    _EMIT_OVERRIDE = fn


def _emit(event: str, **payload: Any) -> None:
    fn = _EMIT_OVERRIDE
    if fn is None:
        try:
            from coco.logging_setup import emit as _e
            fn = _e
        except Exception as e:  # noqa: BLE001
            log.warning("[proactive_trace] emit import failed: %s: %s",
                        type(e).__name__, e)
            return
    try:
        fn(event, **payload)
    except Exception as e:  # noqa: BLE001
        log.warning("[proactive_trace] emit failed event=%s: %s: %s",
                    event, type(e).__name__, e)


# ---------------------------------------------------------------------------
# (a) trace
# ---------------------------------------------------------------------------


def emit_trace(
    stage: str,
    candidate_id: str,
    decision: str,
    *,
    reason: str = "",
    ts: Optional[float] = None,
    **extra: Any,
) -> None:
    """emit ``proactive.trace`` 一条决策路径事件。

    default-OFF：``COCO_PROACTIVE_TRACE`` 未设时立即 return，无 IO 无 emit 无 state。
    """
    if not trace_enabled_from_env():
        return
    if ts is None:
        ts = time.time()
    payload: dict[str, Any] = {
        "stage": str(stage),
        "candidate_id": str(candidate_id),
        "decision": str(decision),
        "ts": float(ts),
    }
    if reason:
        payload["reason"] = str(reason)
    # 透传 extra（如 boost_level / suppressed_path 等观测维度）
    for k, v in extra.items():
        if k in payload:
            continue
        payload[k] = v
    _emit("proactive.trace", **payload)


def make_candidate_id(ts: Optional[float] = None) -> str:
    """同帧 candidate_id 生成；只与时间戳挂钩，不引入随机源（便于 fixture 复现）。"""
    t = ts if ts is not None else time.time()
    return str(int(t * 1000))


# ---------------------------------------------------------------------------
# (b) llm.usage
# ---------------------------------------------------------------------------


def _llm_usage_log_path(now: Optional[float] = None) -> Path:
    """``~/.coco/llm_usage_<YYYYMMDD>.jsonl``。按本地日期滚动。"""
    base = Path(os.path.expanduser("~/.coco"))
    if now is None:
        now = time.time()
    date_str = _dt.datetime.fromtimestamp(now).strftime("%Y%m%d")
    return base / f"llm_usage_{date_str}.jsonl"


def _estimate_tokens_from_chars(chars: int) -> int:
    """启发式估算：~2 字符/token（中文偏粗，英文偏松）；最少 1 token。"""
    if chars <= 0:
        return 0
    return max(1, int(chars) // 2)


def record_llm_usage(
    component: str,
    *,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    prompt_chars: Optional[int] = None,
    completion_chars: Optional[int] = None,
    ts: Optional[float] = None,
    **extra: Any,
) -> None:
    """emit ``llm.usage`` 并落盘 ``~/.coco/llm_usage_<date>.jsonl``。

    优先用 ``prompt_tokens`` / ``completion_tokens``（精确）；缺失时用
    ``prompt_chars`` / ``completion_chars`` 估算（``chars // 2``）。

    default-OFF：``COCO_LLM_USAGE_LOG`` 未设时立即 return（无 emit、无文件创建）。
    """
    if not llm_usage_log_enabled_from_env():
        return
    if ts is None:
        ts = time.time()
    pt = int(prompt_tokens) if prompt_tokens is not None else _estimate_tokens_from_chars(
        int(prompt_chars or 0)
    )
    ct = int(completion_tokens) if completion_tokens is not None else _estimate_tokens_from_chars(
        int(completion_chars or 0)
    )
    payload: dict[str, Any] = {
        "component": str(component),
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "ts": float(ts),
    }
    estimated = prompt_tokens is None or completion_tokens is None
    if estimated:
        payload["estimated"] = True
    for k, v in extra.items():
        if k in payload:
            continue
        payload[k] = v

    # emit 走 llm 命名空间；component=mm_proactive 由 payload 字段表达
    _emit("llm.usage", **payload)

    # 落盘（fail-soft：disk error 不向上抛）
    try:
        path = _llm_usage_log_path(ts)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(line + "\n")
    except Exception as e:  # noqa: BLE001
        log.warning("[proactive_trace] llm_usage write failed: %s: %s",
                    type(e).__name__, e)
