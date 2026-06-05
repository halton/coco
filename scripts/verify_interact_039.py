"""interact-039 verification — LLM tool calling actions + 6 new robot actions.

(分支名 feat/interact-013-llm-tool-calling-actions 保留 user brief 命名；
内部 feature ID 已重派为 interact-039 以避免与既有 interact-013 (MM proactive prompt) 冲突。)

跑法::

    /Users/halton/work/coco/.venv/bin/python scripts/verify_interact_039.py

子项::

    V0   hash lock on coco/actions.py + coco/llm.py + coco/interact.py
         （只记录 sha 到结果，便于后续 closeout 比对；本 verify 不阻塞）
    V1   import 新动作 (shake / tilt_left / tilt_right / look_up / look_down /
         goto_sleep / wake_up) + ACTION_TOOLS 不抛
    V2   mock OpenAI chat_with_tools → tool_calls 路径 → reply_with_action 返
         {text: str, action='look_left'}
    V3   mock OpenAI chat_with_tools → 仅 content 没 tool_calls → action=None
    V4   Ollama backend → 不实现 chat_with_tools → reply_with_action 退化到
         reply 返 {text, action=None}
    V5   Fallback backend → reply_with_action 不崩，返 {text, action=None}
    V6   route_reply("随便", llm_result={'action':'shake'}) → action == 'shake'
         （覆盖 keyword 默认）
    V7   route_reply("向左看") (llm_result=None) → action == 'look_left' (keyword fallback)
    V8   ACTION_TOOLS schema: 1 个 perform_action function, 10 个 enum, name 正确
    V9   main.py 调用 reply_with_action + interact.py 引用 llm_action_fn
         (wire 调用点存在性检查，证明 LLM action 真的接到 InteractSession)

rc=0 全 PASS。
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_results: List[Dict[str, Any]] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(f"[verify_interact_039] {tag} {name}: {detail}", flush=True)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# ---------- V0: hash lock ----------
def v0_hash_lock() -> None:
    target_files = [
        ROOT / "coco" / "actions.py",
        ROOT / "coco" / "llm.py",
        ROOT / "coco" / "interact.py",
    ]
    sha_map: Dict[str, str] = {}
    try:
        for p in target_files:
            if not p.exists():
                _check("V0_hash_lock", False, f"missing {p}")
                return
            sha_map[p.relative_to(ROOT).as_posix()] = _sha(p)
        _check(
            "V0_hash_lock",
            True,
            "+".join(f"{k}={v}" for k, v in sha_map.items()),
        )
    except Exception as e:  # noqa: BLE001
        _check("V0_hash_lock", False, f"{type(e).__name__}: {e}")


# ---------- V1: import 新动作 + ACTION_TOOLS ----------
def v1_imports() -> None:
    try:
        from coco.actions import (
            shake, tilt_left, tilt_right, look_up, look_down,
            goto_sleep, wake_up,
        )
        from coco.llm import ACTION_TOOLS, ACTION_TOOL_ENUM
        callables_ok = all(callable(f) for f in (
            shake, tilt_left, tilt_right, look_up, look_down,
            goto_sleep, wake_up,
        ))
        tools_ok = (
            isinstance(ACTION_TOOLS, list)
            and len(ACTION_TOOLS) == 1
            and isinstance(ACTION_TOOL_ENUM, tuple)
            and len(ACTION_TOOL_ENUM) == 10
        )
        _check(
            "V1_imports",
            callables_ok and tools_ok,
            f"callables={callables_ok} tools_len={len(ACTION_TOOLS)} enum_len={len(ACTION_TOOL_ENUM)}",
        )
    except Exception as e:  # noqa: BLE001
        _check("V1_imports", False, f"{type(e).__name__}: {e}")


# ---------- Mock backends ----------
class _MockOpenAIWithToolCall:
    """模拟 OpenAI backend，chat_with_tools 返 (text, action='look_left')。"""
    name = "openai"

    def chat(self, user_text: str, *, timeout: float,
             history: Optional[List[dict]] = None,
             system_prompt: Optional[str] = None) -> str:
        return "中文兜底回应。"

    def chat_with_tools(self, user_text: str, *, timeout: float,
                        history: Optional[List[dict]] = None,
                        system_prompt: Optional[str] = None) -> Tuple[str, Optional[str]]:
        return ("好的，我看左边。", "look_left")


class _MockOpenAIContentOnly:
    """模拟 OpenAI 返 content 但 tool_calls 为空。"""
    name = "openai"

    def chat(self, user_text: str, *, timeout: float,
             history: Optional[List[dict]] = None,
             system_prompt: Optional[str] = None) -> str:
        return "今天天气很好。"

    def chat_with_tools(self, user_text: str, *, timeout: float,
                        history: Optional[List[dict]] = None,
                        system_prompt: Optional[str] = None) -> Tuple[str, Optional[str]]:
        return ("今天天气很好。", None)


class _MockOllama:
    """Ollama backend 风格：实现 chat 但不实现 chat_with_tools。"""
    name = "ollama"

    def chat(self, user_text: str, *, timeout: float,
             history: Optional[List[dict]] = None,
             system_prompt: Optional[str] = None) -> str:
        return "本地回复。"


# ---------- V2: tool_calls 路径返 action ----------
def v2_openai_tool_call() -> None:
    try:
        from coco.llm import LLMClient
        client = LLMClient(_MockOpenAIWithToolCall(), timeout=1.0, max_chars=60)
        result = client.reply_with_action("看左边")
        ok = (
            isinstance(result, dict)
            and isinstance(result.get("text"), str)
            and result.get("text").strip()
            and result.get("action") == "look_left"
        )
        _check("V2_openai_tool_call", ok, f"result={result}")
    except Exception as e:  # noqa: BLE001
        _check("V2_openai_tool_call", False, f"{type(e).__name__}: {e}")


# ---------- V3: 仅 content 没 tool_calls ----------
def v3_openai_content_only() -> None:
    try:
        from coco.llm import LLMClient
        client = LLMClient(_MockOpenAIContentOnly(), timeout=1.0, max_chars=60)
        result = client.reply_with_action("今天怎么样")
        ok = (
            isinstance(result, dict)
            and isinstance(result.get("text"), str)
            and result.get("text").strip()
            and result.get("action") is None
        )
        _check("V3_openai_content_only", ok, f"result={result}")
    except Exception as e:  # noqa: BLE001
        _check("V3_openai_content_only", False, f"{type(e).__name__}: {e}")


# ---------- V4: Ollama 退化路径 ----------
def v4_ollama_no_tools() -> None:
    try:
        from coco.llm import LLMClient
        client = LLMClient(_MockOllama(), timeout=1.0, max_chars=60)
        result = client.reply_with_action("聊天")
        ok = (
            isinstance(result, dict)
            and isinstance(result.get("text"), str)
            and result.get("text").strip()
            and result.get("action") is None
        )
        _check("V4_ollama_no_tools", ok, f"result={result}")
    except Exception as e:  # noqa: BLE001
        _check("V4_ollama_no_tools", False, f"{type(e).__name__}: {e}")


# ---------- V5: Fallback backend 不崩 ----------
def v5_fallback_backend() -> None:
    try:
        from coco.llm import LLMClient, FallbackBackend
        client = LLMClient(FallbackBackend(), timeout=1.0, max_chars=60)
        result = client.reply_with_action("你好")
        ok = (
            isinstance(result, dict)
            and isinstance(result.get("text"), str)
            and result.get("text").strip()
            and result.get("action") is None
        )
        _check("V5_fallback_backend", ok, f"result={result}")
    except Exception as e:  # noqa: BLE001
        _check("V5_fallback_backend", False, f"{type(e).__name__}: {e}")


# ---------- V6: route_reply 用 LLM action 覆盖 keyword ----------
def v6_route_reply_llm_overrides() -> None:
    try:
        from coco.interact import route_reply
        # 文本 "随便" 不命中任何 keyword → 默认 nod；llm_result 给 shake
        reply, action = route_reply("随便说点什么", llm_result={"action": "shake"})
        ok_action = action == "shake"
        ok_reply = isinstance(reply, str) and reply
        # 再验：同时提供 text 时也用 LLM text
        reply2, action2 = route_reply(
            "随便", llm_result={"action": "wake_up", "text": "我醒啦！"}
        )
        ok_text2 = reply2 == "我醒啦！" and action2 == "wake_up"
        _check(
            "V6_route_reply_llm_overrides",
            ok_action and ok_reply and ok_text2,
            f"first=({reply!r},{action!r}) second=({reply2!r},{action2!r})",
        )
    except Exception as e:  # noqa: BLE001
        _check("V6_route_reply_llm_overrides", False, f"{type(e).__name__}: {e}")


# ---------- V7: route_reply 无 LLM 走 keyword fallback ----------
def v7_route_reply_keyword_fallback() -> None:
    try:
        from coco.interact import route_reply
        # 关键词新增了 "向左/左看/左边" → look_left；同时 "睡觉" → goto_sleep
        reply_left, action_left = route_reply("我想向左看")
        reply_sleep, action_sleep = route_reply("我要睡觉")
        reply_shake, action_shake = route_reply("摇头")
        ok = (
            action_left == "look_left"
            and action_sleep == "goto_sleep"
            and action_shake == "shake"
        )
        _check(
            "V7_route_reply_keyword_fallback",
            ok,
            f"left={action_left} sleep={action_sleep} shake={action_shake}",
        )
    except Exception as e:  # noqa: BLE001
        _check("V7_route_reply_keyword_fallback", False, f"{type(e).__name__}: {e}")


# ---------- V8: ACTION_TOOLS schema ----------
def v8_action_tools_schema() -> None:
    try:
        from coco.llm import ACTION_TOOLS, ACTION_TOOL_ENUM
        expected_enum = {
            "nod", "shake", "look_left", "look_right", "look_up",
            "look_down", "tilt_left", "tilt_right", "goto_sleep", "wake_up",
        }
        tool = ACTION_TOOLS[0]
        ok_type = tool.get("type") == "function"
        fn = tool.get("function", {})
        ok_name = fn.get("name") == "perform_action"
        params = fn.get("parameters", {})
        props = (params.get("properties") or {}).get("action", {})
        ok_enum = set(props.get("enum") or []) == expected_enum
        ok_required = params.get("required") == ["action"]
        ok_enum_const = set(ACTION_TOOL_ENUM) == expected_enum
        ok = ok_type and ok_name and ok_enum and ok_required and ok_enum_const
        _check(
            "V8_action_tools_schema",
            ok,
            f"type={ok_type} name={ok_name} enum={ok_enum} required={ok_required} const={ok_enum_const}",
        )
    except Exception as e:  # noqa: BLE001
        _check("V8_action_tools_schema", False, f"{type(e).__name__}: {e}")


# ---------- V9: main.py / interact.py 调用 reply_with_action ----------
def v9_wire_call_site() -> None:
    """V9: 证明 main.py 真的 wire 了 reply_with_action，不是只暴露不调。

    检查：(a) coco/main.py 含 ``reply_with_action`` 字面引用（构造 InteractSession
    时传 llm_action_fn=_llm.reply_with_action）；(b) coco/interact.py 引用 llm_action_fn
    （handle_audio LLM 块用 action 函数拿 {text, action}）。
    """
    try:
        main_src = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
        interact_src = (ROOT / "coco" / "interact.py").read_text(encoding="utf-8")
        ok_main = "reply_with_action" in main_src
        ok_interact = "llm_action_fn" in interact_src
        ok = ok_main and ok_interact
        _check(
            "V9_wire_call_site",
            ok,
            f"main.reply_with_action={ok_main} interact.llm_action_fn={ok_interact}",
        )
    except Exception as e:  # noqa: BLE001
        _check("V9_wire_call_site", False, f"{type(e).__name__}: {e}")


# ---------- Driver ----------
def main() -> int:
    t0 = time.monotonic()
    v0_hash_lock()
    v1_imports()
    v2_openai_tool_call()
    v3_openai_content_only()
    v4_ollama_no_tools()
    v5_fallback_backend()
    v6_route_reply_llm_overrides()
    v7_route_reply_keyword_fallback()
    v8_action_tools_schema()
    v9_wire_call_site()
    dt = time.monotonic() - t0

    failed = [r for r in _results if not r["ok"]]
    summary = {
        "verify": "interact-039",
        "branch": "feat/interact-013-llm-tool-calling-actions",
        "results": _results,
        "failed_count": len(failed),
        "duration_s": round(dt, 4),
    }
    out_dir = ROOT / "evidence" / "interact-039"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"[verify_interact_039] DONE total={len(_results)} failed={len(failed)} "
        f"dt={dt:.3f}s",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
