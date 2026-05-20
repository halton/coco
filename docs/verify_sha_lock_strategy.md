# verify-script sha 锁策略评估 (interact-038)

> 状态: docs-only / verify-only 决策记录。本文件由 `scripts/verify_interact_038.py` 锁住章节标题与关键短语。
> 不要重命名章节标题；新增章节请同时更新 `## 章节标题列表（供 verify_interact_038 用）` sentinel 段。

## 概述

本仓库 `scripts/verify_*.py` 中存在两种针对源码/兄弟脚本内容的 hash 锁策略：

1. **file-sha 锁**：对目标文件整体 `hashlib.sha256(path.read_bytes())`，期望值 hardcode 在 verify 脚本里（或外置 `evidence/<feat>/v4_sha.json`）。
2. **func-sha 锁**：仅对目标文件中某个函数体（用 ast 或行号窗口切片）做 sha256，期望值 hardcode。

interact-037 V1/V2 同时使用了 func-sha (`_append_drift_history`) + file-sha (整 `verify_interact_024.py`) 两层锁。实际迭代中发现：file-sha 对 verify_interact_024.py 的任何微调（注释、import 顺序、helper 重排）都会破坏 V2，迭代成本显著高于 func-sha。本文件评估两种策略的适用边界并给出后续 verify 编写建议。

## 全文件 sha 锁 (file-sha) 适用场景 + 优劣

适用：

- 目标脚本/源码处于**冻结期**（feature 已 close-out、版本已 stable，预期数月内不再 touch）
- 目标文件**整体语义**都重要（不只是某个函数体，注释/常量/import 顺序均属 contract）
- 锁的对象是一份"产物"而非"被频繁迭代的工具"，比如 `bump_*_v4_sha.py` 这类一次写好就不动的脚本

优势：

- 实现极简：一行 `hashlib.sha256(p.read_bytes()).hexdigest()`，无 ast 依赖
- mutant 防御面最大：改任何一个字节都会触发 FAIL
- 锁的对象无歧义：等于"这个文件就是这个 byte 序列"

劣势：

- **耦合度过高**：注释 typo、空行、import 顺序、docstring 改写、unrelated helper 重命名 → 全部破坏 hash
- 在 verify 文件**自身**仍处于活跃迭代期（例如 verify_interact_024 在 interact-024 ~ interact-037 期间频繁 V-bump）时，下游 verify 用 file-sha 锁它 → 每次微调都要 chain 更新 EXPECTED_FILE_SHA
- 失败信息无定位价值：只能告诉你"文件变了"，不能告诉你"哪个语义元素变了"
- 多人协作时容易产生 merge-time hash drift（两个 PR 都改了同一脚本的不同段）

## 函数级 sha 锁 (func-sha) 适用场景 + 优劣

适用：

- 目标文件中只有**某个具体函数/方法**承担 verify 关心的契约（其他部分允许自由迭代）
- verify 文件本身预期会经历多轮 V-bump，外层 file-sha 锁不可承受
- 函数体较稳定但所在文件经常因为 unrelated 修改被 touch

实现：

- ast.parse → 找到目标 FunctionDef → 用 `ast.get_source_segment` 或自定义行号窗口取函数体源码 → sha256
- 现有样例：`scripts/verify_interact_037.py` V1 锁 `_append_drift_history` 函数体 (`EXPECTED_FUNC_SHA = "9440e6..."`)

优势：

- **耦合度精确**：只锁关心的函数；文件其它地方任意修改不影响
- 失败信息明确：FAIL 时能直接说"`_append_drift_history` 函数体变了"，定位到具体业务点
- 迭代友好：被锁文件可以自由演化（添加 helper、调整 import、补 docstring），只要核心函数不动
- 与 mutant 反证一致：function-level mutant（改一行函数体）必然触发 FAIL，与意图直接对齐

劣势：

- 实现复杂度高一点：依赖 ast 或精心维护的行号窗口；行号窗口在 refactor 时容易 stale
- 防御面比 file-sha 小：函数外的相关 helper 改写仍可能改变行为而不触发 hash 变化（需要补 V2 phrase 断言或额外 func-sha）
- 多个相关函数需要多个 EXPECTED_FUNC_SHA 常量，可读性下降

## 混合策略 (mixed strategy)

经验上多数 verify 不是非此即彼，而是**混合**：

- file-sha 锁**冻结产物**（baseline 文件、bump 脚本本身、evidence/*.json 外置 sha 表）
- func-sha 锁**活跃业务函数**（被验证的核心逻辑函数）
- phrase/heading 断言锁**契约级常量字符串**（环境变量名、关键 log 短语、docs 章节标题）

例如 verify_robot_027 即采用：file-sha (整 verify_robot_025.py) + line-window-sha (setter block L489-504) + phrase (常量名 EXPECTED_PREFIX) 三层叠加。

混合策略的核心问题转换为："**针对每个被锁对象，它的迭代频率与契约边界匹配哪一种 sha 锁？**" → 用决策矩阵驱动。

## 决策矩阵

> 英文别名 (decision matrix): 供 verify phrase 锁引用。

| 被锁对象类型 | 迭代频率 | 业务源码耦合度 | 模板风格一致性 | 推荐锁 |
|---|---|---|---|---|
| 已 close-out feature 的 verify 自身 | 低 | 低 | 高 | file-sha |
| 当前 milestone 仍在活跃 V-bump 的 verify | 高 | 高 | 高 | func-sha + phrase；不锁整文件 |
| 一次性 bump 脚本 (`bump_*_v4_sha.py`) | 极低 | 低 | 高 | file-sha |
| 业务源码核心函数 (e.g. `_append_drift_history`) | 中 | 高 | 中 | func-sha |
| 业务源码整文件 (proactive.py 等大模块) | 高 | 极高 | 低 | 不要 file-sha；用 line-window + phrase |
| docs/*.md 契约文档 | 中 | 低 | 高 | sentinel-section + heading 解析 + sha print-only |
| 测试 fixture / baseline json | 低 | 低 | 高 | file-sha 或 外置 json |

## 推荐：对 interact-037 等高迭代频率 verify 文件改用 func-sha；稳定 verify 保留 file-sha

**具体建议**（不在本 feature 内执行，仅记录方向，留给未来 interact-040+ 真正迁移）：

1. **interact-037 V2 EXPECTED_FILE_SHA → 拆为 func-sha + phrase**：
   - 移除整文件 file-sha (`EXPECTED_FILE_SHA = "1ff3e2..."`)
   - 改为锁 verify_interact_024.py 中的 `_compute_drift_v3` 等核心函数 func-sha
   - 补 phrase 断言保护契约级常量字符串
2. **infra-034 V4 外置 sha 表**保留 file-sha：那是冻结的 baseline + 一次性 bump 脚本，file-sha 正合适
3. **robot-027/028/030/031** 已经在用 line-window/setter-block 锁，是 func-sha 的精神延伸，保留现状
4. 新增 verify 时，先填决策矩阵那一行，再决定锁形态；不要默认 file-sha
5. 引入 `scripts/_verify_lib.py` 公共 helper `func_sha_by_name(path, func_name)` 统一切片策略（在 interact-040+ 落地）

**本 feature (interact-038) 不修任何 verify 脚本**，仅落 docs + 一个 verify-only 框架，证明决策矩阵被锁住、未来回头能引用此文件作 single-source。

## 章节标题列表（供 verify_interact_038 用）

- `概述`
- `全文件 sha 锁 (file-sha) 适用场景 + 优劣`
- `函数级 sha 锁 (func-sha) 适用场景 + 优劣`
- `混合策略 (mixed strategy)`
- `决策矩阵`
- `推荐：对 interact-037 等高迭代频率 verify 文件改用 func-sha；稳定 verify 保留 file-sha`
