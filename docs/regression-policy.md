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

## 4. actionlint dry-run hook（infra-014-fu-1 落地）

`.github/workflows/*.yml` 改动需在本地 / CI 跑 actionlint dry-run 防 GHA 语法漂移。
infra-014-fu-1 已把 hook 从"仅跟踪"升级为"可调用脚本 + verify 自检"：

| 项 | 文件 | 触发 | 行为 |
| --- | --- | --- | --- |
| `lint_workflows.py` | `scripts/lint_workflows.py` | 手动 / CI / verify_infra_014_fu_1 | 对 `.github/workflows/*.yml` 跑 actionlint |
| 优雅 skip | 同上 | actionlint 未装 | 打印安装提示 + rc=0（不阻断 verify） |
| `--strict` | 同上 | CI 显式开启 | actionlint 未装即 rc=1 |

执行：
- 本地：`python scripts/lint_workflows.py`（未装则 skip + 提示）
- CI/strict：`python scripts/lint_workflows.py --strict`
- verify 自检：`scripts/verify_infra_014_fu_1.py` V3-V5 校验调用路径 / 优雅 skip / 实跑

安装 actionlint：
- `brew install actionlint`（macOS）
- `bash <(curl https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash)`
- `go install github.com/rhysd/actionlint/cmd/actionlint@latest`

后续 follow-up：把 `lint_workflows.py --strict` 接入 `.github/workflows/verify-matrix.yml`
的某个 lint job（独立轻量 job，不进 verify-infra），与 pre-commit hook 二选一或并存。

## 5. evidence 副作用清理（infra-014-fu-2）

跑某个 feature 的 verify 时常需要捎带跑相关回归 verify（例如 companion-014
close-out 跑 verify_companion_009 / -010 / -011 / -012 / -013 + interact-013/-14），
这些回归会重写 `evidence/<other-feature>/verify_summary.json` 等文件。如果直
接 `git add -A`，无关副作用就会进入本 feature commit。

`scripts/restore_unrelated_evidence.py` 把"close-out 时手动 git restore 非本
feature evidence"这一动作 codify 为可复用 helper：

| 项 | 文件 | 触发 | 行为 |
| --- | --- | --- | --- |
| `restore_unrelated_evidence.py` | `scripts/restore_unrelated_evidence.py` | 手动 / closeout / `run_verify_all --restore-unrelated` | 收集 `git status -s` 中 `evidence/` 下的 tracked 修改，排除 `evidence/<target_feature_id>/`，对其余 `git checkout -- <path>` |
| `--restore-unrelated FEATURE_ID` | `scripts/run_verify_all.py` | CLI flag | 跑完后立即调用 helper（fail 也清，方便 closeout 干净复跑） |
| Python API | 同 helper | `from restore_unrelated_evidence import restore_unrelated_evidence` | `restore_unrelated_evidence(target, *, dry_run=False, repo_root=None) -> list[str]` |

不引入运行期 env gate；这是 dev / closeout 工具，按需调用。`untracked` 文件
（`??`）不会被自动还原（git checkout 还原不了），由 closeout sub-agent 自行处理。

执行：
- 干跑：`python scripts/restore_unrelated_evidence.py --target companion-014 --dry-run`
- 实跑：`python scripts/restore_unrelated_evidence.py --target companion-014`
- 跑完 verify 自动清：`python scripts/run_verify_all.py --filter companion --restore-unrelated companion-014`

verify 自检：`scripts/verify_infra_014_fu_2.py` V1-V8（fake tmp git repo 验
helper 行为；V5 跑回归验脚本 + V8 校验本段文档同步）。

## 6. 历史链路


- infra-006 — verify-matrix 骨架
- infra-008 — precommit_impact + paths-filter 生成
- infra-009 — last_run.json 留痕 + hot-path 全量豁免
- infra-010/012 — self_heal_wire / camera handle
- infra-011 — paths-filter wired into verify-matrix
- infra-013 — meta 兜底段 + workflow_dispatch
- **infra-014** — --max-strategy 三选一 + lint_paths_filter + 本文档（本 feature 引入）
- **infra-014-fu-1** — actionlint dry-run hook 落地 (`scripts/lint_workflows.py`) +
  `lint_paths_filter.py:127` docstring raw-string 修复（消除 SyntaxWarning）
- **infra-014-fu-2** — `scripts/restore_unrelated_evidence.py` helper +
  `run_verify_all --restore-unrelated` flag（codify "close-out git restore 无关 evidence"）
