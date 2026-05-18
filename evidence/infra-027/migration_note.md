# infra-027 migration note

## 范围
- verify-only: 不动 .github/workflows/**, 不动 coco/**, 不动 tests/**.
- 新增: docs/ci-matrix-os-spec.md, scripts/verify_infra_027.py, evidence/infra-027/**.

## 与 infra-026 区分
- infra-026: runs-on 与 matrix.os **字段一致性**契约 (placeholder / 退化等价 / artifact 占位 / 白名单).
- infra-027: matrix.os **集合本身**的稳定性 (cardinality + 字面量 union + spec doc + negative spec + SOP).

## 默认行为
- SPEC_OS_UNION = ["ubuntu-latest"]; 与现 verify-matrix.yml 完全一致.
- 0 env flag; verify 跑法: `uv run python scripts/verify_infra_027.py`.

## 扩 OS 时 SOP (见 docs/ci-matrix-os-spec.md §4)
1. 改 spec doc §1/§2.
2. 改 SPEC_OS_UNION.
3. 改 verify-matrix.yml.
4. 跑 verify_infra_027 + verify_infra_026 双 PASS.

## 不衍生 fu chain
后续 caveat 全部入 backlog (priority=999 status=backlog phase=null).
