# vision-015 future migration note (evidence-only)

scope: 未来若必须把 ``_gc_last_time`` 切回 wall clock (``time.time``) 的代价分析,
本 phase **不动手**, 仅锁面 evidence。

current owner (vision-013 后):

- ``coco/perception/face_tracker.py`` ``_maybe_run_periodic_gc`` 使用 ``time.monotonic``
  作 due check, ``_gc_last_time`` 在进程内是 monotonic value, 不持久化。
- ``run_gc_cycle(now=...)`` 内部 TTL 判定使用 ``time.time`` (wall), 与 entry
  持久化字段 ``last_seen`` 同时基, 跨进程可比较。
- 两路时钟混用为有意设计, 详见 ``docs/vision-gc-time-source-design.md`` §1-§3。

future migration (若必须切回 wall clock):

1. 源码改动:
   - ``coco/perception/face_tracker.py`` L1150 / L1153 / L1161 三处 ``time.monotonic()``
     调用切到 ``time.time()``, 删 ``now_mono`` 变量名。
   - module docstring L14-28 + ``_maybe_run_periodic_gc`` docstring L1142-1145 同步更新。
2. verify monkey-patch 同步代价:
   - ``scripts/verify_vision_013.py`` 与 ``scripts/verify_vision_014b.py`` 中所有
     mock ``time.monotonic`` 的 fixture 切到 mock ``time.time``。
   - 新增 NTP 回拨 fixture (mock ``time.time`` 突然减少), 断言 GC 不卡死,
     可能需要 jitter tolerance 或 abs() 兜底。
3. 兼容性:
   - ``_gc_last_time`` 不持久化, 切换不影响磁盘文件。
   - process restart 后 ``_gc_last_time=None`` 走 None-init 路径, 行为等价。
4. 风险与不做手术的理由:
   - 切回 wall 后 NTP step-jump (大幅回拨) 会让 ``(now - _gc_last_time)`` 瞬间为负,
     ``time_due`` 永远 false, GC 周期阻塞直到再过一个完整窗口。
   - 当前 monotonic 实现稳定通过 verify_vision_013 / 014b, 切换属于"修一个不存在的问题"。
   - vision-015 verify (V1 字面量锁面) 会主动抓 ``_gc_last_time = time.time(`` 直接赋值,
     任何破契约改动会被抓到。

vision-015 作为 verify-only 契约审计 + 设计文档锁面**到此为止**, 不衍生 fu chain,
不动源码。
