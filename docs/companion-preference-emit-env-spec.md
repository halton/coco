# companion preference emit env 命名权威 spec (companion-018 锁面)

> 本文档是 `companion.preference_persisted` emit 节流相关 env 变量与模块级状态的
> **唯一权威**。spec / 代码 / docstring 出现分歧时以本文档为准；本文档与代码冲突时
> 以代码为准 (verify 脚本会强制锁面)。
>
> scope: 吸收 backlog `companion-016-backlog-polish` 的 **C1 (env 命名口径) + C4
> (warn-once 多进程语义)** 子项,把 companion-017 已在 `preference_learner.py`
> 内联注释里建立的口径外移到独立 spec 文档,便于跨子系统引用。
>
> verify-only / 0 源码改动 / default-OFF / sim-first。

---

## §1 权威 env 名 (单一来源)

| 项 | 权威值 |
| --- | --- |
| 环境变量名 | `COCO_PERSIST_EMIT_MIN_INTERVAL_S` |
| 默认值 | `10.0` 秒 |
| 合法取值 | 非负 float (包含 `0`) |
| 非法取值 | 负数 / `nan` / `inf` / 非数字字符串 / 空串 → fallback default + WARN once |
| 代码站点 | `coco/companion/preference_learner.py` `_PERSIST_EMIT_INTERVAL_ENV` 常量 |
| 解析函数 | `preference_persist_emit_min_interval_s_from_env()` |
| 历史误称 (**不使用**) | `COCO_PREFERENCE_EMIT_INTERVAL_S` (默认 30s) — 仅出现在 companion-016 规划期 brief / backlog 文案, 实现从未读这个名字 |

### §1.1 不为旧名提供 alias 的理由

1. 旧名 `COCO_PREFERENCE_EMIT_INTERVAL_S` 从未在任何代码路径 (`coco/`) 被 `os.environ.get` 读出
2. 外部 (`scripts/` / `tests/` / `evidence/` / `docs/` / 用户配置) 没有任何引用
3. 加 alias 会引入两个心智负担:
   - "为什么 spec 有两个名字"
   - "alias 失效时哪个胜出 (新名 vs 旧名 vs default)"
4. 历史误称只活在 backlog 文案中,锁定到本文档即可,无需 alias

未来若**确实需要**改名 (例如更通用的 `COCO_EMIT_THROTTLE_MIN_INTERVAL_S`),
应:

- 新增一层 `_resolve_env(...)` 同时读新旧名,新名优先
- spec 文档显式标 deprecation timeline
- verify 脚本同步收紧/放宽

当前 (phase-22 companion-018) 不进行这层抽象,**保持单名权威**。

---

## §2 模块级 WARN-once 多进程语义 (C4 锁面)

### §2.1 实现现状

```python
# coco/companion/preference_learner.py L92
_PERSIST_EMIT_INTERVAL_WARN_ONCE = False  # module-level，进程内 warn once
```

`preference_persist_emit_min_interval_s_from_env()` 在第一次解析到非法 env 值时:

1. `log.warning(...)` 输出一行
2. 置 `_PERSIST_EMIT_INTERVAL_WARN_ONCE = True`
3. 同一进程内后续调用直接 fallback default,**不再 warn**

### §2.2 多进程行为 (**已知预期**)

- module-level flag 在 **每个新解释器进程** (subprocess / multiprocessing / pytest-xdist worker / verify 子进程) **重置为 False**
- 每个进程因此**各 warn 一次**
- 与 companion-015 `_PREFERENCE_STATE_WARN_ONCE` 行为**一致**

### §2.3 不视为 regression 的场景

- sim/verify 跑子进程时单次 warn → 预期
- pytest-xdist worker 数 N 时 N 次 warn → 预期
- container / k8s pod 重启后再 warn → 预期 (新进程)

### §2.4 若未来需要 *true* "全局 warn once"

实现成本 (仅记录,不做):

- 持久化 flag 到磁盘文件 (例如 `~/.coco/.warn_once.json`)
- atomic write + schema version + corruption fallback
- 跨用户 / 跨机器维度需要额外考虑
- 与 companion-015 hydrate 一致的版本化 schema

收益极低 (warn 行本身是 dev-info,不影响功能),**不进行此抽象**。

---

## §3 spec ↔ code ↔ docstring 对照表

| 项 | spec (本文档) | code (`preference_learner.py`) | docstring (module-level / function) | 历史 brief (backlog) |
| --- | --- | --- | --- | --- |
| env 名 | `COCO_PERSIST_EMIT_MIN_INTERVAL_S` | `_PERSIST_EMIT_INTERVAL_ENV` (L91) | L77 / L91 / L98 注释一致 | brief 曾写 `COCO_PREFERENCE_EMIT_INTERVAL_S` (误,**不采纳**) |
| 默认值 | `10.0` 秒 | `_PERSIST_EMIT_MIN_INTERVAL_S_DEFAULT = 10.0` (L90) | L77 / L90 一致 | brief 曾写 `30s` (误,**不采纳**) |
| 非法值行为 | WARN once + fallback default | `preference_persist_emit_min_interval_s_from_env` L113-119 | function docstring L98-101 一致 | n/a |
| WARN once 范围 | 进程级 | module-level flag (L92) | L86-89 docstring 明示 | n/a |
| 多进程语义 | 各 warn 一次 (预期) | 同上 | L88-89 注释明示 "sub-process 隔离" | n/a |

---

## §4 不一致点 doc-only 修复清单

| 不一致点 | 位置 | 修复方式 |
| --- | --- | --- |
| brief / backlog 写 `COCO_PREFERENCE_EMIT_INTERVAL_S` 默认 30s | `feature_list.json` companion-016 / companion-016-backlog-polish description | **不改 description** (历史只读); 通过本 spec 文档显式 "以代码为准" 锁面 |
| 文档单点 | n/a (此前**无独立 spec 文档**) | 本次 companion-018 新建 `docs/companion-preference-emit-env-spec.md` 作权威 |
| 模块级 WARN once 多进程语义 | code L86-89 注释 | 外移到本文档 §2,code 注释保留作交叉引用 |

修复后 (本次 companion-018):

- 单一权威文档: 本文件
- code 注释指向本文档 (后续若需更精细可加链接,本次不动 code)
- verify_companion_018.py 强制锁面 env 名 + 关键短语

---

## §5 verify-only 边界

companion-018 严格 **0 源码改动**:

- 不改 `coco/companion/preference_learner.py`
- 不改 `scripts/verify_companion_015.py` / `verify_companion_016.py` / `verify_companion_017.py`
- 不改 `feature_list.json` 既有 description 字段 (仅追加 companion-018 自身条目状态)

允许改动 (本次):

- 新建本文档
- 新建 `scripts/verify_companion_018.py`
- 新建 `evidence/companion-018/` 目录及内容
- 更新 `feature_list.json` companion-018 status + `claude-progress.md` Session 条目

---

## §6 future migration alias 成本评估 (evidence)

若**未来**接到需求 (例如外部用户已经在用旧名 `COCO_PREFERENCE_EMIT_INTERVAL_S`,
要兼容):

1. 改动面 (估算):
   - `preference_persist_emit_min_interval_s_from_env`: +5~10 行,优先读新名,fallback 读旧名,旧名命中加 deprecation WARN
   - 本 spec 文档: 新增 §1.x deprecation table + timeline
   - verify_companion_018: V1 锁面放宽允许旧名出现 (但仅在 alias 站点)
   - verify_companion_017: 同步
2. 风险:
   - 用户混用两个名时优先级歧义
   - deprecation WARN 噪声
   - test fixture 维护成本翻倍
3. 当前结论: **不做 alias** (§1.1)。本节仅作 evidence,锁 backlog 不衍生 fu chain。

---

## §7 引用

- 代码: `coco/companion/preference_learner.py`
- 上游 verify: `scripts/verify_companion_015.py` / `verify_companion_016.py` / `verify_companion_017.py`
- 本 phase verify: `scripts/verify_companion_018.py`
- backlog source: `companion-016-backlog-polish` (status=upgraded → companion-017 + companion-018)
- 历史踩坑: companion-016 brief / backlog 文案曾使用旧名,companion-017 已通过 code 注释锁面,companion-018 外移到独立 spec

锁面到此为止,**不衍生 follow-up chain**。
