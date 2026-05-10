"""coco.llm — LLM 回应客户端（interact-002）.

设计原则：
- 任何 backend 失败/超时/未配置都不抛给上层；reply 一律返回字符串
- 上层调用 llm_reply(text)；底层根据环境变量挑 backend，失败时降级到 KEYWORD_ROUTES
- 不下载模型权重（>50MB 阈值）；本地 backend 假定用户已装 Ollama

环境变量：
- COCO_LLM_BACKEND: "openai" | "ollama" | "fallback" | unset
    - unset / "fallback" / 未知值 → FallbackBackend（仅 KEYWORD_ROUTES）
- COCO_LLM_BASE_URL: OpenAI 兼容 endpoint，默认 https://api.openai.com/v1
- COCO_LLM_API_KEY: API key（Ollama 不需要）
- COCO_LLM_MODEL: 模型名，OpenAI 默认 "gpt-4o-mini"，Ollama 默认 "qwen2.5:3b-instruct"
- COCO_LLM_TIMEOUT: 请求超时（秒，默认 2.0）
- COCO_LLM_MAX_CHARS: 回应字符上限（默认 60，硬截断）

使用：
    from coco.llm import build_default_client
    client = build_default_client()
    text = client.reply("你好")  # 永远返回字符串
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol


log = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "你是 Coco（可可），一个友好的桌面陪伴机器人。"
    "用一句简短的中文（不超过 60 个字）自然地回应用户的话，"
    "保持温柔好奇的语气，不要使用表情符号或英文。"
)

DEFAULT_TIMEOUT = 2.0
DEFAULT_MAX_CHARS = 60
HAN_CHAR_RE = re.compile(r"[一-鿿]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    # 去掉常见多余字符
    text = text.replace("\n", " ").replace("\r", " ").strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


def _has_chinese(text: str) -> bool:
    return bool(HAN_CHAR_RE.search(text or ""))


def _fallback_reply(user_text: str) -> str:
    """import-late 调用 coco.interact.route_reply（避免循环 import）。"""
    from coco.interact import route_reply  # local import

    reply, _action = route_reply(user_text)
    return reply


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class LLMStats:
    calls: int = 0
    backend_ok: int = 0
    backend_fail: int = 0
    fallback_used: int = 0
    durations_s: List[float] = field(default_factory=list)

    def percentile(self, p: float) -> float:
        if not self.durations_s:
            return 0.0
        xs = sorted(self.durations_s)
        idx = max(0, min(len(xs) - 1, int(round(p * (len(xs) - 1)))))
        return xs[idx]

    def summary(self) -> dict:
        return {
            "calls": self.calls,
            "backend_ok": self.backend_ok,
            "backend_fail": self.backend_fail,
            "fallback_used": self.fallback_used,
            "p50_s": round(self.percentile(0.50), 4),
            "p95_s": round(self.percentile(0.95), 4),
            "max_s": round(max(self.durations_s) if self.durations_s else 0.0, 4),
        }


# ---------------------------------------------------------------------------
# Backend Protocol
# ---------------------------------------------------------------------------


class LLMBackend(Protocol):
    name: str

    def chat(self, user_text: str, *, timeout: float) -> str:
        """返回原始 LLM 文本。失败时抛任何异常 — 由 LLMClient 兜底。"""
        ...


# ---------------------------------------------------------------------------
# OpenAI 兼容 backend
# ---------------------------------------------------------------------------


class OpenAIChatBackend:
    """OpenAI Chat Completions 兼容（GitHub Models / OpenAI / 任何兼容 endpoint）。

    用 urllib 做 POST 避免引入额外依赖。"""

    name = "openai"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def chat(self, user_text: str, *, timeout: float) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "max_tokens": 96,
            "temperature": 0.7,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        obj = json.loads(body.decode("utf-8"))
        try:
            content = obj["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"unexpected response shape: {obj!r}") from e
        return content or ""


# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------


class OllamaBackend:
    """Ollama HTTP /api/chat。本地 daemon 默认 http://localhost:11434。"""

    name = "ollama"

    def __init__(self, *, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat(self, user_text: str, *, timeout: float) -> str:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": 96},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        obj = json.loads(body.decode("utf-8"))
        try:
            content = obj["message"]["content"]
        except (KeyError, TypeError) as e:
            raise RuntimeError(f"unexpected response shape: {obj!r}") from e
        return content or ""


# ---------------------------------------------------------------------------
# Fallback (always KEYWORD_ROUTES) — 当 backend 未配置时用
# ---------------------------------------------------------------------------


class FallbackBackend:
    name = "fallback"

    def chat(self, user_text: str, *, timeout: float) -> str:
        # 直接返回 KEYWORD_ROUTES；LLMClient.reply 会再走一次截断/中文校验
        return _fallback_reply(user_text)


# ---------------------------------------------------------------------------
# LLMClient — 带超时 + 降级 + 截断
# ---------------------------------------------------------------------------


class LLMClient:
    def __init__(
        self,
        backend: LLMBackend,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        self.backend = backend
        self.timeout = timeout
        self.max_chars = max_chars
        self.stats = LLMStats()

    def reply(self, user_text: str, *, timeout: Optional[float] = None) -> str:
        """永远返回非空字符串。LLM backend 失败时降级到 KEYWORD_ROUTES。"""
        t0 = time.monotonic()
        eff_timeout = timeout if timeout is not None else self.timeout
        self.stats.calls += 1
        text = ""

        # 1) 调 backend
        try:
            raw = self.backend.chat(user_text or "", timeout=eff_timeout)
            text = _truncate(raw, self.max_chars)
            # backend 返回若不含汉字（OpenAI 偶发返回英文）→ 视为失败降级
            if text and _has_chinese(text):
                self.stats.backend_ok += 1
            else:
                log.info(
                    "[llm] backend %s returned non-Chinese or empty %r, falling back",
                    self.backend.name, text,
                )
                text = ""
                self.stats.backend_fail += 1  # 计入失败，保持 calls = ok + fail 不变
        except Exception as e:  # noqa: BLE001
            log.info(
                "[llm] backend %s failed: %s: %s; falling back",
                self.backend.name, type(e).__name__, e,
            )
            self.stats.backend_fail += 1

        # 2) 降级
        if not text:
            try:
                text = _fallback_reply(user_text)
            except Exception as e:  # noqa: BLE001
                # 终极兜底：永远不抛
                log.warning("[llm] fallback also failed: %s", e)
                text = "嗯。"
            self.stats.fallback_used += 1
            text = _truncate(text, self.max_chars)

        dt = time.monotonic() - t0
        self.stats.durations_s.append(dt)
        return text


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_default_client() -> LLMClient:
    """根据环境变量构造 LLMClient。永远返回可用 client（最差是 fallback）。"""
    backend_name = (os.environ.get("COCO_LLM_BACKEND") or "").strip().lower()
    timeout = float(os.environ.get("COCO_LLM_TIMEOUT", str(DEFAULT_TIMEOUT)))
    max_chars = int(os.environ.get("COCO_LLM_MAX_CHARS", str(DEFAULT_MAX_CHARS)))

    backend: LLMBackend
    if backend_name == "openai":
        api_key = os.environ.get("COCO_LLM_API_KEY", "").strip()
        if not api_key:
            log.info("[llm] COCO_LLM_BACKEND=openai 但 COCO_LLM_API_KEY 未设，降级到 fallback")
            backend = FallbackBackend()
        else:
            base_url = os.environ.get("COCO_LLM_BASE_URL", "https://api.openai.com/v1").strip()
            model = os.environ.get("COCO_LLM_MODEL", "gpt-4o-mini").strip()
            backend = OpenAIChatBackend(base_url=base_url, api_key=api_key, model=model)
            log.info("[llm] backend=openai base=%s model=%s", base_url, model)
    elif backend_name == "ollama":
        base_url = os.environ.get("COCO_LLM_BASE_URL", "http://localhost:11434").strip()
        model = os.environ.get("COCO_LLM_MODEL", "qwen2.5:3b-instruct").strip()
        backend = OllamaBackend(base_url=base_url, model=model)
        log.info("[llm] backend=ollama base=%s model=%s", base_url, model)
    else:
        backend = FallbackBackend()
        if backend_name and backend_name != "fallback":
            log.info("[llm] 未知 COCO_LLM_BACKEND=%r，降级到 fallback", backend_name)

    return LLMClient(backend, timeout=timeout, max_chars=max_chars)
