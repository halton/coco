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


# ---------------------------------------------------------------------------
# dashboard-005: runtime config hot-reload (LLM model 切换不重启 coco)
# ---------------------------------------------------------------------------
# dashboard 写 ~/.cache/coco/runtime_config.json，coco 主进程每 N 秒 check 一次，
# 发现 llm_model 变了就更新 backend.model（不重启进程，不重连 backend）。
_RUNTIME_CONFIG_PATH = os.path.expanduser("~/.cache/coco/runtime_config.json")
_LAST_CONFIG_RELOAD_INTERVAL_S = float(
    os.environ.get("COCO_CONFIG_RELOAD_INTERVAL_S", "30.0")
)
_last_config_check_ts: float = 0.0


def _load_runtime_config() -> dict:
    """读 ~/.cache/coco/runtime_config.json；文件不存在/JSON 解析失败一律返回 {}.

    dashboard-005：dashboard 后端 POST /api/config/llm_model 会原子写入此文件，
    coco 主进程通过 _maybe_reload_model 周期性读取以热切 LLM model。
    """
    try:
        if not os.path.isfile(_RUNTIME_CONFIG_PATH):
            return {}
        with open(_RUNTIME_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.loads(f.read() or "{}")
        if isinstance(data, dict):
            return data
        return {}
    except (OSError, ValueError, TypeError):
        return {}


def _maybe_reload_model(backend) -> Optional[str]:
    """周期性 check runtime_config.json，更新 backend.model；返回新 model 或 None.

    - 节流：上次 check < _LAST_CONFIG_RELOAD_INTERVAL_S 秒直接 return None
    - backend 没 ``model`` 属性（如 FallbackBackend）→ 跳过
    - config 没 ``llm_model`` 字段或与当前相同 → return None
    - 不同 → 原地改 backend.model（OpenAIChatBackend / OllamaBackend 都用
      self.model），下一次 chat() payload 自然带新 model；不重连、不重 init backend
    """
    global _last_config_check_ts
    now = time.monotonic()
    if (now - _last_config_check_ts) < _LAST_CONFIG_RELOAD_INTERVAL_S:
        return None
    _last_config_check_ts = now
    if not hasattr(backend, "model"):
        return None
    cfg = _load_runtime_config()
    new_model = cfg.get("llm_model")
    if not isinstance(new_model, str) or not new_model:
        return None
    current = getattr(backend, "model", None)
    if new_model == current:
        return None
    try:
        backend.model = new_model
    except (AttributeError, TypeError):
        return None
    log.info("llm.hot_reload model %r -> %r", current, new_model)
    return new_model


SYSTEM_PROMPT = (
    "你是 Coco（可可），一个友好的桌面陪伴机器人。"
    "用一句简短的中文（不超过 60 个字）自然地回应用户的话，"
    "保持温柔好奇的语气，不要使用表情符号或英文。"
)

DEFAULT_TIMEOUT = 2.0
DEFAULT_MAX_CHARS = 60
HAN_CHAR_RE = re.compile(r"[一-鿿]")


# ---------------------------------------------------------------------------
# interact-039 (branch feat/interact-013): LLM tool calling actions
# ACTION_TOOLS：OpenAI Chat Completions tools schema，让 LLM 把用户自然语言
# 决定的"动作"以 tool_call 形式返回。10 个候选 action 与 coco.actions 模块一一对应：
#   nod / shake / look_left / look_right / look_up / look_down /
#   tilt_left / tilt_right / goto_sleep / wake_up
# 仅 OpenAI 兼容 backend 启用；Ollama / Fallback 不传 tools。
# 调用方拿到 reply_with_action() 返回的 {text, action} dict 后，自行决定是否
# 把 action 映射到 InteractSession._do_action。失败/未返 tool_call → action=None。
# ---------------------------------------------------------------------------

ACTION_TOOL_ENUM = (
    "nod", "shake", "look_left", "look_right", "look_up",
    "look_down", "tilt_left", "tilt_right", "goto_sleep", "wake_up",
    # interact-042: antenna + body_yaw actions
    "wiggle_antennas", "perk_up", "droop_antennas",
    "turn_body_left", "turn_body_right", "turn_body_center",
)

ACTION_TOOLS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "perform_action",
            "description": (
                "Perform a physical action with the robot's head/body/antennas. "
                "Call this whenever the user requests a movement, gesture, "
                "or expressive action (look, nod, shake, tilt, sleep, wake, "
                "antenna wiggle/perk/droop, body turn)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(ACTION_TOOL_ENUM),
                        "description": (
                            "The action to perform. "
                            "nod=点头同意; shake=摇头否定; "
                            "look_left/look_right/look_up/look_down=朝该方向看; "
                            "tilt_left/tilt_right=歪头; "
                            "goto_sleep=低头睡眠; wake_up=回中位醒来; "
                            "wiggle_antennas=摇摆天线 (兴奋); "
                            "perk_up=天线竖起 (好奇/警觉); "
                            "droop_antennas=天线下垂 (失落/不开心); "
                            "turn_body_left/turn_body_right=转身向左/右 (整个上半身, 不位移); "
                            "turn_body_center=身体回正。"
                        ),
                    }
                },
                "required": ["action"],
            },
        },
    },
]


# 调用 ACTION_TOOLS 时附加的 system prompt 提示（让 LLM 同时给文本与 tool_call）
ACTION_TOOLS_SYSTEM_HINT = (
    "如果用户请求一个动作（看、点头、摇头、歪头、睡觉、醒来、"
    "天线摇摆/竖起/下垂、转身向左/向右/回正等），"
    "你**必须**调用 perform_action 工具，同时仍给一句简短的中文回应。"
    "如果只是闲聊不需要动作，直接回复文本即可。"
)



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

    def chat(
        self,
        user_text: str,
        *,
        timeout: float,
        history: Optional[List[dict]] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        """返回原始 LLM 文本。失败时抛任何异常 — 由 LLMClient 兜底。

        interact-004：``history`` 是 OpenAI/Ollama 兼容的 messages 列表（不含
        system，也不含本轮 user）。FallbackBackend 会忽略它。
        companion-004：``system_prompt`` 覆盖默认 SYSTEM_PROMPT；用于注入用户档案。
        None 时回退到 SYSTEM_PROMPT 常量（向后兼容）。FallbackBackend 也忽略。
        """
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

    def chat(
        self,
        user_text: str,
        *,
        timeout: float,
        history: Optional[List[dict]] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        url = f"{self.base_url}/chat/completions"
        sys_p = system_prompt or SYSTEM_PROMPT
        messages: List[dict] = [{"role": "system", "content": sys_p}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        payload = {
            "model": self.model,
            "messages": messages,
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

    # interact-039: OpenAI tool calling 路径。返 (text, action_or_None)。
    # 走独立方法保持旧 chat() 的 str 契约不变；调用方（LLMClient.reply_with_action）
    # 主动 hasattr/getattr 探测，不强制其它 backend 实现。
    def chat_with_tools(
        self,
        user_text: str,
        *,
        timeout: float,
        history: Optional[List[dict]] = None,
        system_prompt: Optional[str] = None,
    ) -> tuple:
        url = f"{self.base_url}/chat/completions"
        # 在 system prompt 后追加 tool 使用提示，引导 LLM 同时给 text + tool_call
        base_sys = system_prompt or SYSTEM_PROMPT
        sys_p = base_sys + "\n\n" + ACTION_TOOLS_SYSTEM_HINT
        messages: List[dict] = [{"role": "system", "content": sys_p}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 128,
            "temperature": 0.7,
            "tools": ACTION_TOOLS,
            "tool_choice": "auto",
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
            msg = obj["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"unexpected response shape: {obj!r}") from e
        content = msg.get("content") or ""
        action: Optional[str] = None
        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            # 取首个 perform_action call 的 action 字段
            for tc in tool_calls:
                fn = (tc or {}).get("function") or {}
                if fn.get("name") != "perform_action":
                    continue
                raw_args = fn.get("arguments") or ""
                try:
                    parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except (ValueError, TypeError):
                    parsed = {}
                cand = (parsed or {}).get("action")
                if isinstance(cand, str) and cand in ACTION_TOOL_ENUM:
                    action = cand
                    break
        # 若仅 tool_calls 没 content，给一个友好默认文本（让上层 TTS 仍有话可说）
        if not content and action:
            content = "好的。"
        return (content or "", action)



# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------


class OllamaBackend:
    """Ollama HTTP /api/chat。本地 daemon 默认 http://localhost:11434。"""

    name = "ollama"

    def __init__(self, *, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat(
        self,
        user_text: str,
        *,
        timeout: float,
        history: Optional[List[dict]] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        url = f"{self.base_url}/api/chat"
        sys_p = system_prompt or SYSTEM_PROMPT
        messages: List[dict] = [{"role": "system", "content": sys_p}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        payload = {
            "model": self.model,
            "messages": messages,
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

    def chat(
        self,
        user_text: str,
        *,
        timeout: float,
        history: Optional[List[dict]] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        # 直接返回 KEYWORD_ROUTES；history / system_prompt 显式忽略。
        # LLMClient.reply 会再走一次截断/中文校验。
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
        # companion-004：探测 backend.chat 是否接受 system_prompt kwarg。
        # 旧 backend / 测试 stub 不一定接受；不接受就不传，等价 phase-3 行为。
        self._backend_accepts_system_prompt = self._probe_kwarg(backend.chat, "system_prompt")

    @staticmethod
    def _probe_kwarg(fn, name: str) -> bool:
        import inspect as _ins
        try:
            sig = _ins.signature(fn)
        except (TypeError, ValueError):
            return False
        for p in sig.parameters.values():
            if p.kind is _ins.Parameter.VAR_KEYWORD:
                return True
            if p.name == name and p.kind in (
                _ins.Parameter.KEYWORD_ONLY,
                _ins.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                return True
        return False

    def reply(
        self,
        user_text: str,
        *,
        timeout: Optional[float] = None,
        history: Optional[List[dict]] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        """永远返回非空字符串。LLM backend 失败时降级到 KEYWORD_ROUTES。

        interact-004：``history`` 是 OpenAI/Ollama 兼容的 messages 列表
        （不含 system，也不含本轮 user）；只对 OpenAI/Ollama backend 生效，
        FallbackBackend 会忽略。``None`` 等价于无上下文（向后兼容）。
        companion-004：``system_prompt`` 覆盖 backend 的默认 SYSTEM_PROMPT，
        用于注入用户档案。None 时维持向后兼容。
        """
        t0 = time.monotonic()
        eff_timeout = timeout if timeout is not None else self.timeout
        self.stats.calls += 1
        text = ""

        # dashboard-005: 每 reply 入口 check 一次 runtime_config.json
        # 内部已节流（默认 30s），无需在调用方再 throttle
        try:
            _maybe_reload_model(self.backend)
        except Exception as _e:  # noqa: BLE001
            log.debug("hot_reload check skipped: %s", _e)

        # 1) 调 backend
        try:
            if self._backend_accepts_system_prompt:
                raw = self.backend.chat(
                    user_text or "",
                    timeout=eff_timeout,
                    history=history,
                    system_prompt=system_prompt,
                )
            else:
                raw = self.backend.chat(
                    user_text or "",
                    timeout=eff_timeout,
                    history=history,
                )
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

    # interact-039 (branch feat/interact-013): tool calling 路径
    def reply_text(
        self,
        user_text: str,
        *,
        timeout: Optional[float] = None,
        history: Optional[List[dict]] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        """与 reply() 等价的别名，显式表达 "只要文本不要 action" 的语义。

        向后兼容：保留旧 reply() 的 str 契约；新增此别名是用户 brief 的可选 wrapper，
        让调用方可以根据语义选 reply() vs reply_with_action()。
        """
        return self.reply(
            user_text, timeout=timeout, history=history, system_prompt=system_prompt
        )

    def reply_with_action(
        self,
        user_text: str,
        *,
        timeout: Optional[float] = None,
        history: Optional[List[dict]] = None,
        system_prompt: Optional[str] = None,
    ) -> dict:
        """tool calling 路径：返回 {"text": str, "action": Optional[str]}。

        语义：
        - backend 实现了 ``chat_with_tools`` 时（OpenAI 兼容）走 tool calling，
          解析首个 ``perform_action`` tool_call 的 action enum 作为 result['action']。
        - 其它 backend（Ollama / Fallback）退化到旧 chat()，action=None。
        - 任何异常 / 超时 → fallback 到 KEYWORD_ROUTES 文本 + action=None；
          永远返回非空 dict 与非空 text，与 reply() 的 "永不抛" 契约一致。
        """
        t0 = time.monotonic()
        eff_timeout = timeout if timeout is not None else self.timeout
        self.stats.calls += 1
        text = ""
        action: Optional[str] = None

        chat_with_tools = getattr(self.backend, "chat_with_tools", None)
        if callable(chat_with_tools):
            try:
                kwargs: dict = {"timeout": eff_timeout}
                if self._backend_accepts_system_prompt:
                    kwargs["history"] = history
                    kwargs["system_prompt"] = system_prompt
                else:
                    kwargs["history"] = history
                raw_text, raw_action = chat_with_tools(user_text or "", **kwargs)
                text = _truncate(raw_text or "", self.max_chars)
                if isinstance(raw_action, str) and raw_action in ACTION_TOOL_ENUM:
                    action = raw_action
                # 文本可以为空（只有 tool_call 的场景）：用 friendly 默认
                if not text and action:
                    text = "好的。"
                if text and _has_chinese(text):
                    self.stats.backend_ok += 1
                elif not text:
                    # 完全空 → 视为失败走 fallback
                    self.stats.backend_fail += 1
                else:
                    # 有文本但非中文 → 沿用 reply() 的判定，降级
                    log.info(
                        "[llm] backend %s tool reply non-Chinese %r, falling back",
                        self.backend.name, text,
                    )
                    text = ""
                    self.stats.backend_fail += 1
            except Exception as e:  # noqa: BLE001
                log.info(
                    "[llm] backend %s chat_with_tools failed: %s: %s; falling back",
                    self.backend.name, type(e).__name__, e,
                )
                self.stats.backend_fail += 1
                text = ""
                action = None
        else:
            # 不支持 tool calling 的 backend（Ollama / Fallback）→ 退化到普通 reply
            text = self.reply(
                user_text,
                timeout=timeout,
                history=history,
                system_prompt=system_prompt,
            )
            # reply 内部已经把 stats.calls 再加 1；这里要 rollback 一次保持 calls 正确。
            # 简化：因 reply 自身也 ++calls，本方法开头的 ++ 重复一次。这里减回去。
            self.stats.calls -= 1
            dt = time.monotonic() - t0
            self.stats.durations_s.append(dt)
            return {"text": text, "action": None}

        # 若 chat_with_tools 路径失败或空 → fallback 到 KEYWORD_ROUTES
        if not text:
            try:
                text = _fallback_reply(user_text)
            except Exception as e:  # noqa: BLE001
                log.warning("[llm] reply_with_action fallback failed: %s", e)
                text = "嗯。"
            self.stats.fallback_used += 1
            text = _truncate(text, self.max_chars)
            action = None

        dt = time.monotonic() - t0
        self.stats.durations_s.append(dt)
        return {"text": text, "action": action}



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
