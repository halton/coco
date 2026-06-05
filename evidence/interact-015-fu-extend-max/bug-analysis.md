# interact-015-fu-extend-max — P2 修复分析

## 问题（interact-015 reviewer 标记 P2）

`coco/wake_word.py` `WakeGate.extend(seconds)` 旧实现：

```python
with self._lock:
    self._awake_until = time.monotonic() + s   # 直接覆盖
    self._was_awake = True
```

**反例**：
- 用户喊"可可"，`COCO_WAKE_WINDOW_SECONDS=30` → `trigger()` 把 `_awake_until` 设到 `now+30`
- 用户讲话约 5s → 当前剩余 ~25s
- ASR/LLM/TTS reply 完成 → `InteractSession` 调 `wake_gate.extend(15)`（`COCO_FOLLOWUP_WINDOW_S=15`）
- 旧语义：`_awake_until = now+15` → **窗口被缩到 15s**

期望：follow-up `extend` 是"至少再续 N 秒"语义，不应该**缩短**已经更长的现有窗口。

## 修法

`coco/wake_word.py` `WakeGate.extend()`：

```python
with self._lock:
    new_deadline = time.monotonic() + s
    current = self._awake_until or 0.0   # 兼容初始 None / 0
    self._awake_until = max(current, new_deadline)
    self._was_awake = True
```

只续不缩：若当前剩余窗口比 `now + N` 长，保留更长的 deadline。

## 兼容性

- **初始 `_awake_until=0`（从未 trigger 过）**：`current = 0`, `max(0, now+N) = now+N`，与旧行为一致（extend(20) 后 remaining=20）
- **0 / 负值 no-op 保留**：函数开头 `if s <= 0: return` 不变
- **`trigger()` 语义不变**：仍直接覆盖 `_awake_until = now + window_seconds`（"手动开窗"是显式动作，应该重置 deadline，不取 max）

## 旧 verify 是否受影响？

`scripts/verify_interact_015_followup_window.py` 的 V0–V7 在新语义下**全 PASS**，**无需修改**：

| V | 调用序列 | 旧 vs 新结果差异 |
|---|----------|------------------|
| V3 | env=0 → 0 次 extend 调用 | 无 extend 触发，无差异 |
| V4 | MockWakeGate.extend(20) 只记调用 | 不进真 extend 逻辑 |
| V5 | 真 WakeGate(window=1.0)，**未** trigger，handle_audio → extend(5.0) | 初始 `_awake_until=0`, `max(0, now+5)=now+5` → remaining ≈ 5.0（旧/新一致） |
| V7 | 真 WakeGate(window=0.5)，**未** trigger，extend(3.0)；reset 后 extend(0)/extend(-5) | 初始 0 + 0/负 no-op 与旧一致 |

V5/V7 都以"初始 0 起步"或"reset 后"作为前置，没有任何 V 依赖"已有更长窗口 → extend 缩短"的旧行为。因此新 max 语义对原 verify 透明。

## 新 verify (`scripts/verify_interact_015_fu_extend_max.py`)

锁定新语义的 8 个用例：

- **V0** sha256 + anchors（`max(current, new_deadline)` / `只续不缩` / `feature_tag`）
- **V1** extend(15) 后 trigger() → trigger 覆盖（默认 window_seconds=6 → ~6s）
- **V2** trigger(window=30) 后 extend(15) → remaining 仍 ~30s（**核心 P2 用例**）
- **V3** trigger(window=6) 后 extend(30) → remaining ~30s（extend 取大）
- **V4** extend(0) no-op（保留现有窗）
- **V5** extend(-5) no-op
- **V6** 未 trigger 直接 extend(20) → remaining ~20s（初始 0 兼容）
- **V7** monkey-patch `time.monotonic` 模拟 5s 后 extend(15) → remaining 仍 25s；extend(40) → 40s（避免真 sleep）

## 验证结果

- `verify_interact_015_fu_extend_max.py` 8/8 PASS, rc=0 → `verify-pass.log`
- `verify_interact_015_followup_window.py` 8/8 PASS, rc=0 → `verify-pass-original.log`（确认旧 verify 透明）
- `./init.sh` smoke PASS, rc=0 → `smoke-pass.log`
- post-smoke import: `WakeGate(window=30); trigger(); extend(15)` → remaining=30s（P2 fixed）
