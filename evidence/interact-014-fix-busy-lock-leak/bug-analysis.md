# interact-014-fix-busy-lock-leak — bug analysis & 修法

## Root cause

`coco/interact.py` 中 `InteractSession.handle_audio` 用 `self._busy = threading.Lock()`
做 push-to-talk 互斥（同一时刻只允许一个 turn 跑）：

```python
# 修前 (interact-014 之前)
if not self._busy.acquire(blocking=False):
    log.warning("InteractSession 正忙，丢弃本次音频")
    return {...}
# QUIET 早返回 — manual release
if self.conv_state_machine is not None:
    try:
        if self.conv_state_machine.is_quiet_now():
            self._busy.release()
            return {..., "quiet": True}
    except Exception:
        pass
t0 = time.monotonic()
result = {...}
try:
    # ASR / LLM / TTS streaming await ... ~360 行
finally:
    # idle.resume / state_machine.on_tts_done / ...
    self._busy.release()  # ← 唯一兜底 release
```

理论上 `try/finally` 已经包了主体，`finally` 一定跑。但在以下三类路径下 lock 仍可能永泄漏：

1. **double release 引起异常**：QUIET 早返回路径 release 一次，若上下游有任何一处又
   调一次 `self._busy.release()` → 第二次 release 抛 `RuntimeError("release unlocked lock")`
   → handle_audio 抛出去 → 调用方层面看不到 lock 状态，但 `_busy` 已 unlock；下次
   acquire 反而 OK。这条具体不会 leak，但 propagate 异常本身污染上层。
2. **finally 块内自身抛异常**：例如 `idle_animator.resume()` 抛异常（idle_animator 为
   None 时被 short-circuit 跳过；非 None 时若 resume 内部抛 → finally 块内 `_busy.release()`
   在它后面，**Python finally 块会停在第一个未捕获异常处** → release 跳过 → lock 永泄漏。
3. **edge-tts streaming 的后台 worker 抛 / asyncio CancelledError**：tts_say_fn 是注入的，
   audio-015 之后改用 edge-tts streaming（ffmpeg subprocess + reader thread）。如果 reader
   thread 抛异常被 propagate 到主 turn 的 await 边界 → 落到 try 的 `except Exception`
   被吞，finally 还是会跑。但若 reader thread 抛 BaseException（KeyboardInterrupt /
   SystemExit / asyncio.CancelledError）→ 主 turn 直接被打断，finally 才跑也没事 —
   除非中断本身命中 finally 块内某行（罕见但可能）。

最关键的是 **(2)**：现实中观察到的「再也激活不了」表现 — 用户连续多个 wake.hit 都被
`if not self._busy.acquire(blocking=False): return` 驳回 — 与 lock 永持有 + 没有自
恢复机制完全吻合。代码层虽然 try/finally 看起来对，但**没有任何兜底机制**能保证
lock 在「极端 finally 自身崩」的情况下恢复。

## 修法 (interact-014)

三件事:

### (i) 严格 try/finally + 统一 _release_busy_safe 入口

- 新增 `_release_busy_safe()` 实例方法：try release / except RuntimeError pass / 清 ts。
- handle_audio 主 finally + QUIET 早返回路径全部走 `_release_busy_safe`。
- double release / 未持锁一律静默忽略，**不再向上 propagate `RuntimeError`**。

### (ii) Watchdog: 持锁 >30s 自动 break

- 新增 `_busy_acquired_ts: Optional[float]` 实例字段 — acquire 成功时记 `time.time()`。
- 新增 `_check_lock_watchdog(now=None)` helper：
  - 读 `COCO_INTERACT_LOCK_TIMEOUT_S` (默认 30.0)。
  - 若 `_busy.locked() and (now - _busy_acquired_ts) > timeout` → 强释放 + 清 ts。
  - emit `interact.lock_recovered` 事件（含 `elapsed_s` / `timeout_s` / `prev_acquired_ts`）。
  - log warning。
- **关键**：`_check_lock_watchdog()` 调用放在 `acquire(blocking=False)` 之前 →
  即使发生历史泄漏，下一次 wake.hit 进 handle_audio 时立刻自动重置，用户层面
  「再也激活不了」的体感被自愈。

### (iii) edge-tts per-turn cleanup — 留 backlog

audio-015 V0 hash 锁了 `coco/tts.py _synthesize_and_play_edge_streaming`，本 feature
不动它（避免 scope 蔓延）。`_release_busy_safe` + watchdog 已能保证 lock 不泄漏，
ffmpeg subprocess / reader thread 的 per-turn cleanup 单独立 backlog
`interact-014-fu-edge-tts-turn-cleanup` 跟踪。

## Env 配置

- `COCO_INTERACT_LOCK_TIMEOUT_S` (float, 默认 `30.0`): watchdog 触发阈值。
  - `<= 0` 或解析失败 → 回默认 30.0。
  - 单元测试用 10.0 缩短验证时间。

## Files changed

- `coco/interact.py` (+~85 行新代码，删 1 行旧 manual release)
- `scripts/verify_interact_014_fix_busy_lock_leak.py` (新, 9 子项 V0-V8)
- `feature_list.json` (+2 节点: interact-014-fix-busy-lock-leak in_progress + interact-014-fu-edge-tts-turn-cleanup backlog)
- `evidence/interact-014-fix-busy-lock-leak/`: verify-pass.log / smoke-pass.log / uat-script.py / bug-analysis.md

## Future work

- `interact-014-fu-edge-tts-turn-cleanup`: edge-tts streaming finally 块加 ffmpeg.kill +
  reader.join(timeout)，handle_audio finally 调一次 tts.cleanup_streaming() 入口。
