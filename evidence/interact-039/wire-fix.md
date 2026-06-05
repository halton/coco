# interact-039 wire-fix patch

## Context

Engineer 上轮 commit 7225c3a 在 `feat/interact-013-llm-tool-calling-actions` 引入
`LLMClient.reply_with_action()` + 10-enum action tool schema + 6 个新动作。
但 **main.py 没把 `reply_with_action` wire 到 `InteractSession`**，导致即使
LLM backend 返回 tool_call 决定的 action，对话主循环里 `handle_audio` 仍走
`route_reply(transcript)` 的关键词路径 — 功能未真正生效。

本次补丁补这个洞，让 LLM tool calling 决定的动作真正接到 push-to-talk 主循环。

## Changes

### rebase

- feat 分支基于旧 main `e15e4fa`；rebase 到当前 main `aba69b7`（含 interact-014
  watchdog `4ac0b81` + closeout `9b5449c`）。
- 1 个 commit (7225c3a → e8138cb)；无冲突。

### `coco/interact.py`

1. `InteractSession.__init__` 新增可选 kwarg
   `llm_action_fn: Optional[Callable[..., dict]] = None`（紧跟 `llm_reply_fn`）。
   - 构造时用 `_probe_kwarg` 探测 `history` / `system_prompt` 接受性，
     缓存到 `self._llm_action_accepts_history` / `_llm_action_accepts_system_prompt`。
2. `handle_audio` LLM 块（原 line 593+）：
   - LLM 调用前优先尝试 `self.llm_action_fn(transcript, **action_kwargs)` 拿
     `{"text": ..., "action": ...}` dict。
   - dict.action 为非空字符串 → 记入 `llm_action_override`，待后置覆盖 keyword base action。
   - `llm_action_fn` 未注入 / 返回空 text / 抛异常 → 退化到原 `self.llm_reply_fn(...)`
     路径（保持 interact-002/004/008/011 行为完全等价，向后兼容）。
3. fallback / llm_text 落盘段后新增：
   ```python
   if not _use_fallback_now and llm_action_override:
       action = llm_action_override
   ```
   语义：fallback 模式（OfflineDialogFallback 激活）不被 LLM action 覆盖；
   否则 LLM 决定的 action 优先于 keyword route 的 base action。

### `coco/main.py`

L1908 `InteractSession(...)` 构造里在 `llm_reply_fn=_wrapped_llm_reply,` 之后
新增一行：
```python
llm_action_fn=_llm.reply_with_action,
```
说明 (comment)：`_offline_fallback` 只 wrap `_llm.reply` 计 fail/probe；
`reply_with_action` 走自己的 try/except + fallback，不计入 OfflineDialogFallback
失败计数，与现有 fallback 语义解耦（向后兼容 interact-011）。

### `scripts/verify_interact_039.py`

新增 V9_wire_call_site：检查 `coco/main.py` 含字面 `reply_with_action` +
`coco/interact.py` 含字面 `llm_action_fn`，证明 LLM action 真接到 main 入口
不是只暴露方法不调。

## Verification (post-fix)

| Check | rc/result |
|---|---|
| `verify_interact_039.py` V0-V9 | total=10 failed=0 |
| `./init.sh` smoke | rc=0 |
| `from coco.interact import InteractSession; sig 含 'llm_action_fn'` | True |
| `LLMClient.reply_with_action` 存在 | True |

V0 hash_lock 是 echo-style（仅回显当前 sha 不锁 expected），interact.py
新 sha `e0bdf49b17ec591a`；actions.py / llm.py sha 未变。

## Behavior summary

| 情景 | 行为 |
|---|---|
| OpenAI backend + tool_call 命中 perform_action(action="look_left") | reply=tool text 或 "好的。"; action="look_left" 覆盖 keyword |
| OpenAI backend + 只 content 无 tool_call | reply=content; action=keyword route 结果（向后兼容 interact-002） |
| Ollama backend（无 chat_with_tools） | reply_with_action 退化到 reply()，action=None → keyword route |
| Fallback backend | dict.action=None → keyword route |
| OfflineDialogFallback 激活 | 不覆盖 action（fallback 模板回复 + keyword action） |
| `llm_action_fn=None`（旧调用方） | 完全等价 interact-002 行为，handle_audio 走 llm_reply_fn(str) |

## Out of scope (intentional)

- Reviewer fresh-context review — Engineer 阶段只做开发，stop 在 feat 分支
- merge 回 main / push — 待 Reviewer LGTM 后由 closeout sub-agent 做
- 真机 UAT — uat-script.py 已存（user_pending），sim-first 原则不阻 merge
