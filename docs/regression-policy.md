# Regression Policy

本文档列出 coco 仓库的回归保护机制（CI / pre-commit / verify 矩阵），并跟踪
infra-014 引入的新 hook / lint / 影响面策略。default-OFF 项不属运行期 gate，
仅在 CI 或显式开发命令下生效。

## 1. verify 矩阵（CI 主路径）

| Layer | 触发 | 文件 | 说明 |
| --- | --- | --- | --- |
| `smoke` | 每次 push / PR | `.github/workflows/verify-matrix.yml` | `./init.sh` smoke，永远跑 |
| `verify-<area>` ×7 | paths-filter 命中 area | 同上 | dorny/paths-filter@v3 决定，meta 段触发全跑 |
| `paths-filter.yml` | infra-013 fu-2 | `.github/paths-filter.yml` + `evidence/infra-008/paths-filter.yml` | 两文件 byte-identical 由 verify_infra_011 V8 / verify_infra_013 V3 / **infra-014 lint_paths_filter** 共同保护 |

## 2. pre-commit 影响面分析（local，default-OFF）

| 项 | 文件 | gate | 说明 |
| --- | --- | --- | --- |
| `precommit_impact.py` | `scripts/precommit_impact.py` | `COCO_PRECOMMIT_HOOK=1` | infra-008 主体 |
| `--max-strategy` | 同上 | CLI | infra-014：alpha (默认/兼容) / weighted / full / sample |
| `coverage_ratio` stdout | 同上 | 总是打印 | infra-014 V1：`coverage_ratio=R/A strategy=S` |
| `last_run.json` 留痕 | `evidence/infra-008/last_run.json` | `--run` 时写 | 含 `max_strategy` / `coverage_ratio` 字段（infra-014 扩展） |
| hot-path 全量豁免 | 同上 | 总是 | `coco/main.py` 等无条件全 fan-out，绕过 `--max-strategy` |

## 3. paths-filter 自检 lint（infra-014）

| Check | 工具 | 触发 |
| --- | --- | --- |
| L1 byte-identical (.github vs evidence) | `scripts/lint_paths_filter.py` | 手动 / 可选 pre-commit |
| L2 YAML 语法 | 同上 | 同上 |
| L3 必含 7 area + meta | 同上 | 同上 |
| L4 pattern 非空 | 同上 | 同上 |
| L5 meta 兜底段顺序在 area 之后 | 同上 | 同上 |

执行：`python scripts/lint_paths_filter.py`

## 4. actionlint dry-run hook 跟踪（infra-014 V6）

`.github/workflows/*.yml` 改动建议在本地跑 actionlint dry-run 防 GHA 语法漂移。
当前状态：

- [ ] **actionlint dry-run** — 未列入 CI／pre-commit hook；仅记录跟踪。
  - 推荐工具：[actionlint](https://github.com/rhysd/actionlint)
  - 推荐触发：本地 `pre-commit` hook（`.github/workflows/**/*.yml` 改动时）+ CI
    `verify-matrix` workflow 自检 step（infra-013 fu-3 候选）
  - 现状：infra-013 EC-2 已识别但未落地；本 feature 仅做 lint_paths_filter
    （内容契约层），actionlint（语法契约层）作为后续 follow-up 留项

后续 follow-up：infra-013 fu-3 / infra-014 fu-1 候选 — 把 actionlint dry-run
落实为 CI step + pre-commit hook，并加入 verify_infra_* 自检矩阵。

## 5. 历史链路

- infra-006 — verify-matrix 骨架
- infra-008 — precommit_impact + paths-filter 生成
- infra-009 — last_run.json 留痕 + hot-path 全量豁免
- infra-010/012 — self_heal_wire / camera handle
- infra-011 — paths-filter wired into verify-matrix
- infra-013 — meta 兜底段 + workflow_dispatch
- **infra-014** — --max-strategy 三选一 + lint_paths_filter + 本文档（本 feature 引入）
