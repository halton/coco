#!/usr/bin/env python
"""interact-039 UAT: 让用户用自然语言直接试 LLM tool calling → action 映射.

跑法::

    /Users/halton/work/coco/.venv/bin/python evidence/interact-039/uat-script.py

前置::

    - copilot-api 跑在本机（38079 端口或环境配置的 base url）
    - COCO_LLM_BACKEND=openai
    - COCO_LLM_API_KEY 已配
    - COCO_LLM_BASE_URL / COCO_LLM_MODEL 按需

预期::

    每个 prompt 打 result = {'text': ..., 'action': <enum or None>}。
    自然语言 → 动作的映射应贴近预期（见下方注释）。

注意：本脚本只调 LLM 决策，不真的驱动 robot；要看真机动作请走 InteractSession
（或在自己脚本里把 result['action'] 喂给 coco.actions 模块对应函数）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from coco.llm import build_default_client

    client = build_default_client()
    print(f"[uat] backend={client.backend.name}")
    print(f"[uat] has chat_with_tools = {hasattr(client.backend, 'chat_with_tools')}")
    if not hasattr(client.backend, "chat_with_tools"):
        print("[uat] WARNING: backend 不支持 tool calling；reply_with_action 将退化到普通 reply。")
        print("[uat]          要启用 LLM action 决策请设 COCO_LLM_BACKEND=openai + COCO_LLM_API_KEY。")

    prompts = [
        # (用户说的话, 预期 action)
        ("低头睡觉", "goto_sleep"),
        ("醒醒", "wake_up"),
        ("向左看一下", "look_left"),
        ("向右看", "look_right"),
        ("抬起头", "look_up"),
        ("低头", "look_down"),
        ("摇头表示不同意", "shake"),
        ("点头同意", "nod"),
        ("把头歪向左边", "tilt_left"),
        ("今天天气真好啊", None),  # 闲聊不必触发动作
    ]

    print()
    print(f"{'prompt':<24} | {'expected':<14} | {'actual_action':<14} | text")
    print("-" * 90)
    correct = 0
    for prompt, expected in prompts:
        try:
            result = client.reply_with_action(prompt, timeout=8.0)
        except Exception as e:  # noqa: BLE001
            print(f"{prompt:<24} | {expected!s:<14} | ERROR          | {type(e).__name__}: {e}")
            continue
        actual = result.get("action")
        text = result.get("text", "")
        match = "OK " if actual == expected else "DIFF"
        if actual == expected:
            correct += 1
        print(f"{prompt:<24} | {expected!s:<14} | {actual!s:<14} | {match} {text}")

    print()
    print(f"[uat] {correct}/{len(prompts)} 命中预期 action")
    print("[uat] 注意：LLM 输出有随机性，DIFF 不一定算 bug；连续多次跑可看稳定度。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
