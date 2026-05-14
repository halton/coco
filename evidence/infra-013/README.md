# infra-013 — paths-filter cross-area trade-off + workflow_dispatch 行为

infra-013 吸收 infra-011 的 fu-1（workflow_dispatch 行为实证 + cross-area
regression mitigation 文档化）与 fu-2（paths-filter 补 meta 兜底段）。

## fu-1：workflow_dispatch 行为实证

### 现状（截至 main HEAD=ebe2b95）

`.github/workflows/verify-matrix.yml` 顶部 trigger 已包含 `workflow_dispatch:`
（line 27），allow 用户在 GitHub Actions UI 手动触发任意 ref 上的 verify-matrix。

### 行为推导

verify-matrix.yml 的 dataflow：

1. `changes` job 永远跑（无 `if:` gate），调用 `dorny/paths-filter@v3`
   读取 `.github/paths-filter.yml`，输出 7 area boolean + meta boolean。
   - 在 `workflow_dispatch` 事件下，paths-filter 默认行为是 base ref 不存在 →
     不输出 changed area（boolean = 'false'）。**但这不影响 verify-XXX**，因为
     下游 if 条件第一支 `github.event_name != 'pull_request'` 已经为 true。
2. 7 个 `verify-XXX` job `needs: [smoke, changes]`，if 条件结构：

   ```yaml
   if: github.event_name != 'pull_request'
       || needs.changes.outputs.<area> == 'true'
       || needs.changes.outputs.meta == 'true'
   ```

   `workflow_dispatch` 事件下 `github.event_name == 'workflow_dispatch'`，所以
   `!= 'pull_request'` 为 true → 短路 OR → 跑全量。

3. `smoke` job 无 gate，永远跑。

### 结论

**workflow_dispatch 不阻塞 verify-XXX 跑全量。** 不需要为 changes job 加
`if: github.event_name == 'pull_request'`，现有 `!=` 兜底已经足够。手动触发
verify-matrix 的工作流（任意 ref）能拿到 smoke + 全 7 个 verify 的 full coverage。

## fu-2：paths-filter cross-area regression mitigation

### 问题

infra-011 的 paths-filter 按 7 area 切分 verify-XXX。如果 PR diff 落在
**多个 area 共享的基础文件**（pyproject.toml / tests/ 公共 fixture /
conftest.py / .github/ workflow 自身 / scripts/run_verify_all.py 等），但
paths-filter 仅匹配单 area，CI 可能漏报 cross-area regression。

具体场景：

- 改 `pyproject.toml` 升一个公共依赖（如 `numpy`） → vision / audio / robot
  都可能受影响，但 paths-filter 不匹配任何 area → PR CI 只跑 smoke，漏过 7
  个 verify。
- 改 `tests/fixtures/` 下的公共 fixture → 多 verify 脚本受影响。
- 改 `.github/workflows/verify-matrix.yml` 自身 → CI 应自检全量。
- 改 `scripts/run_verify_all.py` 的 SKIP_LIST / runner 逻辑 → 影响所有 area。

### Mitigation：meta 兜底段

`.github/paths-filter.yml` 新增 `meta` area：

```yaml
meta:
  - 'pyproject.toml'
  - 'uv.lock'
  - 'conftest.py'
  - 'tests/**'
  - '.github/**'
  - 'scripts/run_verify_all.py'
  - 'scripts/precommit_impact.py'
```

`verify-matrix.yml` 7 个 verify-XXX 的 if 条件均追加：

```yaml
|| needs.changes.outputs.meta == 'true'
```

PR 命中 meta 任一 pattern → 全 7 个 verify-XXX 跑全量，相当于 paths-filter
被旁路。

### Trade-off 权衡

| 维度 | 选择 | 理由 |
|---|---|---|
| **粒度** | 7 area + 1 meta 兜底 | 兼顾速度（典型单 area PR）与正确性（基础设施改动）|
| **保守倾向** | 偏保守 | meta 段命中即全跑。漏报代价（main 红 / 真机回归）远大于多跑 ~10 min CI |
| **main push 兜底** | 仍跑全量 | `push: branches: [main, 'feat/**']` 下 `!= 'pull_request'` 命中 → 全 7 个无条件跑，paths-filter 完全旁路 |
| **workflow_dispatch 兜底** | 仍跑全量 | 同上 |
| **维护成本** | 单点维护 paths-filter.yml | 不需要在每个 verify-XXX 重复列 fallback 路径 |

### 未覆盖的 cross-area 场景（已知 caveat）

paths-filter meta 兜底**只覆盖文件路径**。以下情况仍可能漏：

1. **运行时跨 area 调用**：`coco/companion/X.py` 改动调到 `coco/robot/Y.py`
   的内部 API → 只触发 companion verify，不触发 robot verify。
   mitigation：靠 verify 脚本自身集成测试 + main push 全量兜底。
2. **隐式依赖**：fixture 文件名不在 `tests/**` 下（如根目录 `*.json` data
   file）→ 不命中 meta。
   mitigation：rare case，发现后追加到 meta area。
3. **环境变量行为**：`COCO_*` 环境变量在多 area 共享 → paths-filter 无法
   追踪。
   mitigation：靠 main push 全量兜底。

**最终防线**：main / feat/** push event 永远跑全 7 verify（line 92/120/148/
176/206/240/268 的 if 第一支 `!= 'pull_request'` 命中）。即使 PR CI 漏，
合并到 main 后会立即被全量捕获。

## 相关文件

- `.github/paths-filter.yml`（7 area + meta）
- `.github/workflows/verify-matrix.yml`（changes outputs + 7 verify-XXX if）
- `evidence/infra-008/paths-filter.yml`（与 .github/ byte-identical 同步源）
- `scripts/verify_infra_013.py`（V1-V8 自动化校验）
