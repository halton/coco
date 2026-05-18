# CI Matrix OS Spec (infra-027)

本文档是 `verify-matrix.yml` 中 `strategy.matrix.os` 集合本身稳定性的权威 spec。
**verify-only**: 不改 CI yml, 不动业务源码; 由 `scripts/verify_infra_027.py` 锁面。

与 infra-026 的关系:
- infra-026 锁的是 `runs-on` 与 `matrix.os` 字段之间的**一致性契约** (placeholder / 退化等价 / artifact 占位 / 白名单).
- infra-027 锁的是**集合本身**: 当前选了哪几个 OS / 没选哪几个 / 为什么 / 加减时怎么改 spec.

---

## §1 当前支持 OS 集合 (closed set)

| OS image      | 状态     | 用途                              |
|---------------|----------|-----------------------------------|
| ubuntu-latest | SUPPORTED | 默认 CI runner; 所有 matrix-bearing job baseline |

**Cardinality**: 当前所有 matrix-bearing job 的 `matrix.os` 列表长度均为 1, 元素为 `ubuntu-latest`.

**集合字面量** (sorted unique union over all matrix-bearing jobs):
```
["ubuntu-latest"]
```

这是 closed set: 任何新增 OS 都必须先更新本 spec (§4 SOP), 然后才允许出现在 `verify-matrix.yml`.

---

## §2 不支持的 OS 与原因 (negative spec)

以下 image 在 GH-hosted runner 白名单 (infra-026 ALLOWED_RUNNERS) 内, 但**当前**不被本仓库 CI 使用. 加入前必须解决对应阻碍:

| OS image          | 不支持原因                                                                 |
|-------------------|----------------------------------------------------------------------------|
| ubuntu-22.04      | ubuntu-latest 已覆盖, 无需双跑; 加入需明确多版本回归收益                 |
| ubuntu-24.04      | 同上                                                                       |
| macos-latest      | TTS / sounddevice 真机门槛 (audio 子系统真机 UAT), CI 无法 sim 通; 也无 reachy-mini cp313 macOS-arm wheel CI 缓存策略 |
| macos-13          | 同 macos-latest, 且 Intel mac runner 资源更紧                              |
| macos-14          | 同 macos-latest                                                            |
| macos-15          | 同 macos-latest                                                            |
| windows-latest    | reachy-mini SDK 在 windows 上路径分隔符 / shell 行为差异未充分回归; bash 脚本 (init.sh) 不可直跑 |
| windows-2022      | 同 windows-latest                                                          |
| windows-2025      | 同 windows-latest                                                          |

**非白名单 (禁止)**: self-hosted / 任意自定义 label / `ubuntu-20.04` (GH 已 deprecate).

---

## §3 matrix.os 与 runs-on 一致性 (引用 infra-026)

集合本身合法不等于使用方式合法. `runs-on` 与 `matrix.os` 字段的一致性由 **infra-026** (`verify_infra_026.py`) 锁面:
- matrix-bearing job: runs-on 要么 `${{ matrix.os }}`, 要么硬编码且与单元素 matrix.os 字面量相等 (退化等价).
- OS-fixed job (lint / changes): runs-on 必须为白名单内的硬编码 image.
- upload-artifact name 必须含 `${{ matrix.os }}` 占位 (infra-019 落地, infra-026 V2 固化).

本 spec 不重复 infra-026 已有断言; 由 infra-027 V0 regression 跑 `verify_infra_026.py` 保证不 regress.

---

## §4 OS 集合变更 SOP

任何加减 `matrix.os` 元素都属于 spec-changing diff, 必须按以下顺序更新:

1. **先更新本 spec** (§1 表格 + cardinality 字面量列表 + §2 negative spec).
2. **再更新 `scripts/verify_infra_027.py` SPEC_OS_UNION 常量** (V1 锁面).
3. **再改 `.github/workflows/verify-matrix.yml`** (`matrix.os` 列表 + 若多元素则把 runs-on 切到 `${{ matrix.os }}`).
4. **跑 `uv run python scripts/verify_infra_027.py` + `verify_infra_026.py` 双 PASS**.
5. **再加 `scripts/verify_infra_027.py` 的 sha256 fingerprint 锁** (V0 自动检测).

顺序倒置 (先改 yml 再补 spec) 会让 V1 / V2 爆 FAIL, 这就是契约锁的意义.

---

## §5 default-OFF 不变式 (verify-only)

infra-027 是 **verify-only** feature:
- **0 业务源码改动**: 不动 `coco/**`, 不动 `.github/workflows/**`, 不动 `tests/**`, 不动 `pyproject.toml`.
- **0 CI 行为改动**: matrix.os 集合不变, runs-on 不变, smoke / verify 跑法不变.
- **0 env flag**: 不引入新环境变量, 不挂 default-OFF gate.
- 仅新增: `docs/ci-matrix-os-spec.md` (本文件) + `scripts/verify_infra_027.py` + `evidence/infra-027/**`.

bytewise 等价 main 的判据: `git diff main..feat/infra-027 --stat` 仅含 docs/ scripts/verify_infra_027.py / evidence/ / feature_list.json / claude-progress.md.

---

## §6 不衍生 fu chain (闭环原则)

本 feature 严守 phase-23 不衍生 fu chain 规则:
- 任何 verify 跑出来的 caveat / 未覆盖维度 / 将来 OS 扩展, **只入 feature_list.json backlog** (`priority=999 status=backlog phase=null`), 不在本任务里 spawn 新 fu feature.
- 若 verify_infra_027.py 自身需后续加强 (例如锁 cardinality 上界 / 锁特定 image 弃用过渡期), 同样入 backlog, 不衍生 infra-027-fu-N.

---

## Quick reference (locked phrases)

下列短语被 `verify_infra_027.py` V2 字面量锁; 改动前必须同步改 verify:

- `closed set`
- `ubuntu-latest`
- `verify-only`
- `infra-026`
- `SPEC_OS_UNION`
- `不衍生 fu chain`
