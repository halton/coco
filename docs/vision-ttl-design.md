# Vision face_id TTL 时钟选型与 GC 触发 contract 设计记录

来源 backlog: `vision-013-backlog-ttl-and-overhead` (C1/C2)
归属 feature: vision-013 (设计落地) / vision-014 (docstring + hot path cache) / vision-014b (verify-only contract 锁定 + 本文档)
范围: `coco/perception/face_tracker.py` 中 `_face_id_meta` 持久化层 TTL + 周期 GC 触发 + `_maybe_identify` hot-path overhead

本文是 vision-013/014/014b 系列的设计 rationale 文档,用于跨进程持久化语义、NTP 异常容忍窗口、以及 default-OFF 微优化的责任划分长期可检索。

---

## 1. wall clock vs monotonic: 两个时钟在同一文件里的分工

`face_tracker.py` 同时使用 `time.time()` (wall clock / POSIX epoch) 和
`time.monotonic()` (单调时钟,进程级,不可跨进程比较),这并非疏忽,而是有
显式分工:

- **TTL 判定 (`run_gc_cycle` 内 `now - rec['last_seen'] > ttl_secs`)** —
  使用 **wall clock**。原因: `_face_id_meta[name]['last_seen']` 会随
  `data/face_id_map.json` 一起被 atomic flush 到磁盘,跨进程 / 跨重启 / 跨用户会话仍需可比较。
  `time.monotonic()` 在每个进程都从 0 开始,把它写盘后下一个进程读出来再比就是无意义的数。
  因此 last_seen 的写入侧 (`_face_id_meta[name]['last_seen'] = time.time()` 见 ~L877)
  与 TTL cutoff 比较侧 (`run_gc_cycle` 见 ~L1282) 必须同时 wall clock,这是 contract。

- **周期 GC 节流 (`_gc_last_time`, `_maybe_run_gc` 内的 `now_mono - self._gc_last_time >= self._gc_period_s`)** —
  使用 **monotonic**。原因: 这是进程内"距上次 GC 多久了"的节流计数,不持久化,不跨进程,
  正好是 monotonic 该上场的场景。这样选的一个隐性收益是:NTP 大幅回拨/前跳时,
  GC 触发节奏不会跟着乱跳 (例如 NTP 一次把 wall clock 前推数月,如果 _gc_last_time 用 wall clock,
  下一次 `_maybe_run_gc` 会立即认为 "距上次跑过去了好几个月" 触发一次额外 GC 周期,
  这没有意义且会引发不必要的 IO)。

- **TrackedFace 内部 `first_seen_ts` / `last_seen_ts`** — 使用 **monotonic**。
  这两个字段只在单次进程生命周期内对比 (用于 presence / GC stale-track 判断),
  不持久化,monotonic 完全合适。

这套分工的一个关键不变量是: **任何会落到 `data/face_id_map.json` 的时间戳必须 wall clock,
任何只在进程内消费的时间戳应优先 monotonic**。新代码扩 TTL/GC 字段时按此判断。

---

## 2. NTP 大幅调时下的已知行为窗口 (known-limit)

由于 TTL cutoff 用 wall clock,系统时钟被 NTP 大幅调整时会出现两种边界情况,
这些**不是 bug,是 wall clock TTL 选型的必然结果**,记录于此供运维参考:

**情况 A: NTP 大幅前跳 (wall clock 突然 + N 天)。** 例如设备启动时 RTC 走默认时间
(2000-01-01),NTP 同步后跳到真实当前时间,跨度可能是数年。所有 `_face_id_meta`
里 `last_seen` 还停留在 "2000 年" 的 entry 会被瞬时判过期。下次 `_maybe_run_gc` 触发
`run_gc_cycle` 会清空它们 (`dropped_ttl = N`),并 emit `vision.face_id_map_repair{reason='ttl', dropped_n=N}`。
对调用方影响: 已存的稳定 face_id 会丢失,需要重新学习。**对策**: 设备首次启动后让 NTP
同步完成、跑过一帧 identify 再正经开始填 face map;或者 ttl_days 设大 (默认 30 天)。

**情况 B: NTP 大幅后跳 (wall clock 倒退)。** 例如手动校时把 wall clock 拨慢一天。
TTL 公式 `(now - last_seen) > ttl_secs` 在 `last_seen > now` 时会得到负数,
**判断为不过期**,entry 全部保留,直到 wall clock 再次走过它们的 last_seen + ttl 才触发淘汰。
等价于"TTL 窗口被延长了 ΔNTP"。对调用方影响: 短期内 face_map 比预期大一点,
不会引发数据损坏。无对策需求。

**情况 C: 节流计时不受影响。** `_gc_last_time` 用 monotonic,情况 A/B 都不会让 GC 节奏
失常。换句话说,**TTL 命中点窗口偏移**(几条记录被多/少清),但**GC 触发频率**保持稳定。

文档对齐 vision-013 evidence 与 face_tracker.py 模块顶 docstring 第 14-26 行段落。

---

## 3. 跨进程持久化必要性 + GC 触发 contract

`face_id_map` 设计为**跨进程长期存活**: 一个用户回家、设备睡眠重启、第二天醒来再见这个人,
应当还是同一个稳定 face_id。这就要求:

1. `last_seen` 必须是绝对时间 (wall clock),所有进程都能读懂 → 排除 monotonic
2. flush 必须 atomic (write tmp + rename),避免半写文件 → 见 vision-010
3. GC 必须自洽: 启动后 hydrate → 立刻跑一次 TTL,先把陈旧 entry 删掉,再开始正常运行 →
   见 face_tracker.py L660 附近
4. TTL 边界必须明确 (本节 contract,vision-014b 锁)

**TTL 边界 contract (vision-014b 锁,V1):**

- 公式: `expired iff (now - last_seen) > ttl_secs`
- 严格大于号 (strict): `last_seen == now - ttl_secs` 边界点上的 entry **保留**,不淘汰
- `ttl_days <= 0` 视为禁用 TTL,整段 TTL 清理 short-circuit
- 单位: `ttl_secs = ttl_days * 86400.0` (整 86400 秒一天,不调闰秒)

**GC 触发 contract (vision-014b 锁,V2):**

- 双触发条件: `frame_due = (frame_counter >= period_frames)` **OR**
  `time_due = (now_mono - gc_last_time) >= period_s`
- 任一 due 即触发一轮 `run_gc_cycle`,**满足后 reset 两个计数器**(`frame_counter = 0`, `gc_last_time = now_mono`)
- `_gc_last_time` 首次为 None,启动后第一次 `_maybe_run_gc` 不会因 time-due 立即触发
  (初始化为 `now_mono`,差值 = 0,小于 period_s)
- `period_s <= 0` 视为禁用 time-due 路径,仅靠 frame-due 触发
- Default-OFF (持久化未启用 / `COCO_FACE_ID_MAP_GC` 未设) 时 `run_gc_cycle` 头部
  short-circuit 返回 `{dropped_ttl: 0, dropped_lru: 0}`,等价旧行为

**Overhead per-frame contract (vision-014b 锁,V3):**

- vision-014 引入 `_face_id_identify_wire_enabled` 实例字段缓存 (在 `__init__` 计算一次)
- Default-OFF (`COCO_FACE_ID_PERSIST` 未设) 下 `_maybe_identify` 进入 wire 块前 flag 检查,
  跳过 `get_face_id` + `record_name_confidence` 两次方法调用与各自的 lock acquire/release
- 量级目标: OFF 路径 wire 块开销 = 1 次实例属性读 + 1 次布尔短路,与 attribute access 同阶 (ns 级)
- 反向断言: OFF 路径下 `get_face_id` / `record_name_confidence` 被调用次数 **== 0** (verify V3)

以上 contract 在 `scripts/verify_vision_014b.py` 中以可执行断言形式锁定,
源码改动一旦破契即 fail 该 verify,此为长期 regression guard。
