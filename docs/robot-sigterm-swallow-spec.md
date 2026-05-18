# robot-022: SIGTERM swallow 行为契约 (verify-only)

**source backlog**: `robot-012-backlog-sigterm-swallow-doc`
**scope**: docs + verify-only 锁面; 0 业务源码改动
**status invariant**: default-OFF (env `COCO_ROBOT_SIGTERM_HANDLE` 未设 → 0 副作用)

---

## 1. 背景

robot-012 引入了可选的 SIGTERM/SIGINT signal handler, 用于在容器 stop /
`kill <pid>` / Ctrl-C 场景下让 `RobotSequencer` 走显式 `shutdown(wait=True, timeout=...)`
而不是被进程一刀切到死状态。

入口: `coco/robot/sequencer.py::install_signal_shutdown_handler`
(file `coco/robot/sequencer.py` 第 154-218 行, 主调用方
`coco/main.py` 第 870-935 行 `COCO_ROBOT_SIGTERM_HANDLE` gate 块)。

---

## 2. 当前行为 (锁定)

### 2.1 Default-OFF (env 未设)

`install_signal_shutdown_handler` 在 env `COCO_ROBOT_SIGTERM_HANDLE` 不在
`{"1","true","yes","on"}` 时直接 `return []`, **不注册任何 handler**, 进程对
SIGTERM 的响应与 robot-012 引入前 bytewise 等价 (Python 默认: SIGTERM → 进程终止)。

代码锚 (`coco/robot/sequencer.py` 174-178):

```python
e = env if env is not None else os.environ
if e.get("COCO_ROBOT_SIGTERM_HANDLE", "").strip().lower() not in (
    "1", "true", "yes", "on",
):
    return []
```

### 2.2 ON 模式 — SIGTERM swallow 行为

当 env 在 `{"1","true","yes","on"}` 时, 函数为每个可用 signame (默认
`("SIGTERM","SIGINT")`) 通过 `signal.signal(signum, _handler)` 注册自定义
`_handler`。`_handler` 完成 `seq.shutdown(...)` 后会尝试链 prev handler
(robot-012 设计意图: 不替用户原有 handler 做主), 但**显式跳过 `SIG_DFL` /
`SIG_IGN` / `None`**。

代码锚 (`coco/robot/sequencer.py` 197-204):

```python
prev = prev_handlers.get(signum)
try:
    sig_dfl = getattr(signal_module, "SIG_DFL", None)
    sig_ign = getattr(signal_module, "SIG_IGN", None)
    if callable(prev) and prev not in (sig_dfl, sig_ign, None):
        prev(signum, _frame)
except Exception:  # noqa: BLE001
    pass
```

**关键后果**: 在 Python 解释器中, SIGTERM 默认 prev handler 是 `signal.SIG_DFL`
(执行后进程终止)。该 case 被显式跳过, 即 `_handler` 在 `seq.shutdown()` 完成后
**自然返回**, **进程不会因 SIGTERM 自动退出**。

也就是说: **ON 模式下 SIGTERM 的默认终止行为被"吞掉" (swallow)**。同样适用于
SIGINT 默认 prev `default_int_handler` —— 该 prev 是 callable, **不会**被跳过,
会被链调用并 raise `KeyboardInterrupt`; SIGTERM 没有等价 callable prev,
因此 SIGTERM 的 swallow 是唯一行为, SIGINT 不是。

### 2.3 设计意图 (intentional, 非 bug)

该 swallow 是有意设计:

1. **Reachy Mini 真机 ON 模式**通常嵌在 `coco.main` 主循环中, 用户期望 shutdown
   后继续走 atexit / explicit cleanup, 而不是被 SIG_DFL 立刻 SIGKILL-like 终止。
2. 默认 OFF 保证未升级用户行为 bytewise 不变 (robot-012 invariant)。
3. 用户/容器可显式覆盖 prev handler (例如 `signal.signal(SIGTERM, lambda s,f: sys.exit(0))`
   在 `install_signal_shutdown_handler` **之前**注册), 那时 prev 是 callable,
   不会被跳过, 链调用即可终止进程。

### 2.4 重入与跨平台安全 (锁定)

- **重入 flag**: `in_progress["flag"]` 防 handler 在 shutdown 期间被同一 signum
  二次触发 (`coco/robot/sequencer.py` 185, 189-192)。
- **Windows / 缺信号兜底**: `getattr(signal_module, name, None)` + try/except
  `(ValueError, OSError, AttributeError)` 跳过当前平台不支持的 signame
  (`coco/robot/sequencer.py` 207-217)。
- **signal_module 注入**: 测试可注入 fake module 验 Windows / 错误路径
  (`coco/robot/sequencer.py` 158, 170, 180-181)。

---

## 3. 用户层契约 (ON 模式必读)

启用 `COCO_ROBOT_SIGTERM_HANDLE=1` (or `true`/`yes`/`on`) 后:

1. **SIGTERM 不再自动终止进程**。若用户/运维期望 SIGTERM 触发进程退出,
   必须在 `install_signal_shutdown_handler` 调用**之前**注册一个 callable prev
   handler (该 prev 会被链调用); 或在用户业务层于 shutdown 完成后显式
   `sys.exit()` / `raise SystemExit`。
2. **SIGINT (Ctrl-C) 行为保持**: Python 默认 prev (`default_int_handler`) 是
   callable, 链调用会 raise `KeyboardInterrupt`, 与未启用 robot-012 时一致。
3. **不要**期望 `kill <pid>` (默认发 SIGTERM) 在 ON 模式下立刻杀死进程; 它会先
   走完 `seq.shutdown(timeout=2.0)` (timeout 由 `install_signal_shutdown_handler`
   `timeout_s` 形参 / `coco/main.py` 默认 2s 控制) 然后回到主循环。如需硬 kill,
   使用 `kill -9 <pid>` (SIGKILL) 或在 shutdown 后显式退出。
4. **default-OFF 不变**: 未设 env 时, 上述所有行为不生效, 进程 SIGTERM 响应与
   Python 默认完全一致 (bytewise / 行为 invariant)。

---

## 4. 锁定指纹 (verify-only)

以下要点由 `scripts/verify_robot_022.py` V0-V5 锁定; 修改任一即应触发 verify FAIL:

- `coco/robot/sequencer.py::install_signal_shutdown_handler` 函数签名
- env gate token `COCO_ROBOT_SIGTERM_HANDLE` + 接受集合 `{1,true,yes,on}`
- `SIG_DFL` / `SIG_IGN` 跳过判定 (`prev not in (sig_dfl, sig_ign, None)`)
- prev callable 判定 (`callable(prev)`)
- 重入 flag `in_progress["flag"]`
- 跨平台异常兜底 `(ValueError, OSError, AttributeError)`

如需修改 SIGTERM swallow 行为本身 (例如改为强制 `os._exit` 或允许 SIG_DFL 链调用),
**必须**先开新 feature, 不能在 robot-022 verify-only 范围内修改。

---

## 5. 关联

- 上游 feature: `robot-012` (SIGTERM/SIGINT handler 引入)
- 关联 backlog (已开): `robot-012-backlog-shutdown-timeout-inf-nan-hardening`
  → upgraded `robot-014`
- 本 feature: verify-only doc 收口, 0 业务变更
