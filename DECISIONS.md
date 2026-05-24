# DECISIONS.md

本文件登记仓库级跨 phase 的硬性决策与历史先例, 供后续 Engineer / Reviewer /
Researcher 查阅. 不替代 `feature_list.json` / `claude-progress.md` /
phase 内 evidence; 但凡涉及 "未来再遇到该情形时应如何处理" 的可重复套路, 应在此显式
登记一节, 并保证对应机械化锁 (verify_infra_*.py) 存在.

## INFRA_V34_CLOSEOUT_EVIDENCE_FIX_PRECEDENT — closeout evidence 历史修复先例

本节登记 phase-66 #8/#9/#10 三轮连续修历史 closeout evidence 的先例, 并为未来
同类情形给出 "允许 / 禁止" 边界 + 修法规范. 字符串
``INFRA_V34_CLOSEOUT_EVIDENCE_FIX_PRECEDENT`` 是 verify_infra_V34_decisions_md_log_closeout_evidence_fix_precedent.py
V1 sentinel 锚点, 不要悄改.

### 三次先例摘要 (CLOSEOUT_EVIDENCE_FIX_V27_V29_V31)

- **phase-66 #8 V27** ── closeout 录入时把 `verify_infra_062` 当作 PASS, 但
  实际它在 main baseline 上有 3 项 pre-existing FAIL. 后续 Reviewer 发现
  evidence 里缺 `pre_existing_baseline_sha` / `baseline_tail_stdout`,
  追加补录 `verify_runs[].pre_existing_baseline_sha = <main HEAD>` +
  `baseline_tail_stdout` (保留 3 FAIL tail), 重 commit; 未修复 verify
  脚本本身, 只修 evidence.

- **phase-66 #9 V29** ── 同期 `verify_infra_033` 在 closeout 里被记成
  "PASS" 但其自身 `self-name anchor` (`__file__` 印章) 在新 commit 后
  drift. 该轮 evidence 把 V3_self_name anchor 当 FAIL 修, 改 evidence 里
  `verify_runs[].status` + tail, 并补 `reconstruction_note` 字段说明
  "evidence 重构自 main HEAD=<sha>"; 同样未改 verify 脚本.

- **phase-66 #10 V31** ── 发现 V27 / V29 两轮 evidence 仍缺 v_062 V4 要求
  的部分 schema 字段 (`closeout_verify.main_head_sha` 7+ hex,
  `reviewer.reviewer_kind == sub_agent_fresh_context`). 第 10 轮把这两条
  补齐, 同时为 V27/V29 的 evidence 各加 `reconstructed_evidence: true` +
  `reconstruction_note`, 明确这是 "路径 A 修 evidence" 而非 "重跑 verify".

三次都属 "**路径 A 修 evidence**", 都没有动 `_verify_lib.py` / `v_062`
本体, 也没回写 `feature_list.json` 中 status / phase 字段. Reviewer
fresh-context 复核 LGTM.

### 通用规范 (CLOSEOUT_EVIDENCE_FIX_GENERAL_RULES)

未来若再遇到 closeout evidence 历史污点, 必须按以下边界处理:

1. **何时允许重构 (allowed)**:
   - evidence 漏字段 / 字段命名不符 v_062 V4 schema (例如 P278 之后扩展)
   - evidence 中 tail / sha 字段是当时机械化 capture 的结果, 但格式与新版
     v_062 V4 / 新版 verify_summary_exit 不兼容
   - reviewer kind 历史值是字面 ``self`` 但实际就是 sub-agent fresh-context
     (需 git log 佐证)

2. **不允许重构 (forbidden)**:
   - 把 historical FAIL 改成 PASS (任何 verify_runs[].status 翻转)
   - 修改 `_verify_lib.py` / `scripts/v_062` / `scripts/dump_v4_sha_graph.py`
     主体以"让旧 evidence 通过" (这相当于绕过机械化锁)
   - 删 / 改 `pre_existing_baseline_sha` + `baseline_tail_stdout` 让
     FAIL 看起来像 not-pre-existing
   - 在没有 fresh-context Reviewer 评审的情况下提交 evidence 重构

3. **必填 `reconstruction_note`**:
   修法必须在 evidence 顶层 (或 `closeout_verify` 子对象) 加:
   ```json
   {
     "reconstructed_evidence": true,
     "reconstruction_note": "phase-<N> #<M> 修补 v_062 V4 schema gap, 原 evidence 见 git show <sha>:<path>"
   }
   ```
   `reconstruction_note` 必须含 (a) 修补原因, (b) 原始 commit 的 sha,
   (c) 是否动过 verify 脚本主体 (一律 No, 否则禁止).

4. **必须 Reviewer fresh-context 复核**:
   evidence 重构 PR 不能由原 Engineer 自审; 必须新开 fresh-context Reviewer
   sub-agent 评审 LGTM, 写入 evidence `reviewer.reviewer_kind = sub_agent_fresh_context`.

5. **不修改 v_062 / _verify_lib 等机械化锁**:
   若新版 schema 与历史 evidence 不兼容, 修历史 evidence (路径 A), 不修锁
   (路径 B 是绕过). 这条线 phase-66 #8/#9/#10 三轮均严格遵守.

### 机械化锁 (mechanical guard)

本节由 `scripts/verify_infra_V34_decisions_md_log_closeout_evidence_fix_precedent.py`
锁住:

- V1 docstring sentinel: 本文件必须含
  `INFRA_V34_CLOSEOUT_EVIDENCE_FIX_PRECEDENT` +
  `CLOSEOUT_EVIDENCE_FIX_V27_V29_V31` +
  `CLOSEOUT_EVIDENCE_FIX_GENERAL_RULES`
- V4 file sha lock: 本文件 (`DECISIONS.md`) 全文件 sha 锁

DECISIONS.md 改动 → 同步 bump `EXPECTED_DECISIONS_FILE_SHA`.
