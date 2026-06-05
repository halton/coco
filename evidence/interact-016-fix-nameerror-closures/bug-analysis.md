# interact-016-fix-nameerror-closures — bug analysis

## Bug 1: `[attention] tick failed: NameError("_group_mode_ref")`

### 旧代码结构（修复前）

```python
# coco/main.py line ~366
def _attention_loop(
    sel=_attention_selector,
    tracker=_face_tracker_shared,
    stop_evt=_attention_stop,
    outer_stop=stop_event,
    interval_s=max(0.05, _att_cfg.interval_ms / 1000.0),
):
    while not stop_evt.is_set() and not outer_stop.is_set():
        try:
            ...
            _gmc = _group_mode_ref[0]   # ← free-var lookup
            ...
        except Exception as e:
            print(f"[coco][attention] tick failed: {e!r}", flush=True)

_attention_thread = threading.Thread(target=_attention_loop, ...)
_attention_thread.start()    # 闭包立即被调用

# … 200 行之后 …
_group_mode_ref: list = [None]   # ← 定义点在 closure call 之后
```

### Root cause

`_group_mode_ref` 不是 `_attention_loop` 的 default-arg 参数，而是 closure free
variable。Python 闭包按 cell 绑定 enclosing scope 的名字。`_attention_thread.start()`
触发 `_attention_loop` 立即执行，访问 `_group_mode_ref` 时该名字在 enclosing
function scope 还**没有任何 binding**（line 580 才赋值），cell 已分配但目标对象
尚未确定，导致每个 tick 抛 `NameError("_group_mode_ref")`。

attention selector 子系统因此"启动 OK 但每 tick 异常"，日志噪音 + GroupModeCoordinator
即便配置了也永远收不到 observe/tick 调用。

### 修法

将 `_group_mode_ref: list = [None]` 初始化提前到 attention block 之前
（`coco/main.py` line 335），保留写入端（line 1477 `_group_mode_ref[0] =
_group_mode_coord`）与闭包读端（line 390 `_gmc = _group_mode_ref[0]`）不变。
两端通过同一 cell 共享同一 mutable list，default OFF 时 `[0]` 仍为 `None`，
闭包内 `_gmc is None` 分支生效 → no-op。

---

## Bug 2: `[scene_caption] on_caption cb failed: NameError: '_proactive_ref'`

### 旧代码结构（修复前）

```python
# coco/main.py line ~602
def _on_caption(cap):
    _p = _proactive_ref[0]    # ← 读取
    ...

# … 700+ 行之后 …
_proactive_ref[0] = _proactive    # ← 写入
```

### Root cause

`_proactive_ref` **完全没有 `_proactive_ref = [None]` / `_proactive_ref: list =
[None]` 这种初始化定义**。grep 显示整个 main.py 只有：
* line 603 闭包读
* line 1392 写入索引 `[0]`

写入 `_proactive_ref[0] = ...` 同样要求该名字已绑定到一个 list 对象；旧代码缺
失初始化，但写入分支被 `try/except` 包住而吃掉，闭包读端则直接报
`NameError("_proactive_ref")` 在每次 SceneCaptionEmitter 60s 回调内。

后果：scene_caption 子系统每 60s 在日志里抛 NameError，且 ProactiveScheduler
即便启用也永远收不到 `record_caption_trigger`（caption-driven 主动话题候选完全
失效）。

### 修法

在 attention block 之前与 `_group_mode_ref` 一起新增 `_proactive_ref: list =
[None]` 初始化（line 336）。写入端（line 1403）与闭包读端（line 614）不变。
default OFF 时 `[0]` 仍为 `None`，`_on_caption` 闭包内 `_p is None` no-op。

---

## 总改动

`coco/main.py`:

1. **line 326-339（+19 行）**: 在 attention block 注释前插入 interact-016 注释 +
   两条 `_group_mode_ref: list = [None]` / `_proactive_ref: list = [None]` 初始化
2. **line 578-580（-3 行 +2 行）**: 移除旧的 `_group_mode_ref: list = [None]`
   重复定义（保留 `_mm_fusion_ref` 原位），改为说明注释；避免重复 `= [None]`
   重置 cell 指向新 list

净改动 ≈ +16 行；只增加初始化点，不改任何 callsite 与写入逻辑。

## 风险

* 改动只影响 `_group_mode_ref` 和 `_proactive_ref` 的"出生时机"，不改变它们的
  生命周期与值（`[None]` → 后续 `[0] = ...`）。
* `_mm_fusion_ref` 保留原位（line 577），它的闭包读 (line 614) 在它之后定义，
  无 NameError 风险。
* 修后 attention loop tick 异常分支 `except Exception as e` 不再被触发——这恰好
  暴露了之前被 NameError 掩盖的 GroupModeCoordinator observe/tick 真实路径
  （若日后真有 observe 异常会通过现有 inner try/except 收住，影响隔离）。
