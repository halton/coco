"""infra-004: 启动 banner 渲染。

输出 ASCII 框线 + 多行分组（subsystems / features / paths / log levels），
敏感字段（API_KEY / TOKEN / SECRET 等）脱敏为 ``***``。

主调路径：``coco.main.Coco.run`` 启动时调 ``render_banner(cfg) + emit("startup.banner")``。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional

from coco.config import CocoConfig, config_summary


SENSITIVE_TOKENS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "ACCESS_KEY")


def _is_sensitive_key(name: str) -> bool:
    up = name.upper()
    return any(t in up for t in SENSITIVE_TOKENS)


def _mask(value: str) -> str:
    if not value:
        return "(unset)"
    return "***"


def coco_env_snapshot(env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """收集所有 COCO_* env，敏感字段脱敏。"""
    e = env if env is not None else os.environ
    out: Dict[str, str] = {}
    for k, v in e.items():
        if not k.startswith("COCO_"):
            continue
        if _is_sensitive_key(k):
            out[k] = _mask(v)
        else:
            out[k] = v
    return out


def _frame(lines: List[str], title: str = "Coco startup") -> str:
    width = max((len(l) for l in lines), default=0)
    width = max(width, len(title) + 4, 60)
    top = "+" + "-" * (width + 2) + "+"
    head = f"| {title.ljust(width)} |"
    body = [f"| {l.ljust(width)} |" for l in lines]
    bot = "+" + "-" * (width + 2) + "+"
    return "\n".join(["", top, head, top, *body, bot, ""])


def render_banner(cfg: CocoConfig, env: Optional[Mapping[str, str]] = None) -> str:
    """生成多行 banner 字符串。

    分四段：subsystems / features enabled / paths / coco env snapshot。
    """
    summary = config_summary(cfg)
    lines: List[str] = []

    # --- subsystems ---
    lines.append("[subsystems]")
    subs = {
        "vad": cfg.vad_enabled,
        "wake": cfg.wake_enabled,
        "power_idle": cfg.power_idle_enabled,
        "dialog_memory": cfg.dialog_memory_enabled,
        "emotion": cfg.emotion_enabled,
        "intent": cfg.intent_enabled,
        "attention": cfg.attention.enabled,
        "metrics": cfg.metrics.enabled,
    }
    for k, v in subs.items():
        lines.append(f"  {k:<16} = {'ON' if v else 'off'}")

    # --- features (LLM, ptt, camera) ---
    lines.append("")
    lines.append("[features]")
    lines.append(f"  llm.backend      = {cfg.llm.backend or '(unset)'}")
    lines.append(f"  llm.model        = {cfg.llm.model or '(unset)'}")
    lines.append(f"  llm.api_key      = {'set' if cfg.llm.api_key_set else 'unset'}")
    lines.append(f"  ptt.seconds      = {cfg.ptt.seconds}")
    lines.append(f"  ptt.disabled     = {cfg.ptt.disabled}")
    lines.append(f"  camera.spec      = {cfg.camera.spec or '(unset)'}")
    lines.append(f"  attention.policy = {cfg.attention.policy}")

    # --- paths ---
    lines.append("")
    lines.append("[paths]")
    lines.append(f"  metrics.path     = {cfg.metrics.path or '(default ~/.cache/coco/metrics.jsonl)'}")
    log_file = (env or os.environ).get("COCO_LOG_FILE", "") if env is not None else os.environ.get("COCO_LOG_FILE", "")
    lines.append(f"  log.file         = {log_file or '(stderr only)'}")
    lines.append(f"  log.level        = {cfg.log.level}")
    lines.append(f"  log.jsonl        = {cfg.log.jsonl}")

    # --- env ---
    lines.append("")
    lines.append("[COCO_* env]")
    env_snap = coco_env_snapshot(env)
    if env_snap:
        for k in sorted(env_snap.keys()):
            lines.append(f"  {k} = {env_snap[k]}")
    else:
        lines.append("  (no COCO_* set)")

    return _frame(lines)


def banner_payload(cfg: CocoConfig, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """供 emit("startup.banner", **payload) 用的结构化 payload。

    包含 config_summary（不含 secret） + env_snapshot（脱敏后）。
    """
    return {
        "config": config_summary(cfg),
        "env": coco_env_snapshot(env),
    }


__all__ = [
    "render_banner",
    "banner_payload",
    "coco_env_snapshot",
    "SENSITIVE_TOKENS",
]
