#!/usr/bin/env python3
"""dump_v4_sha_graph: V4 sha-lock 链路 DAG 可视化工具 (infra-039, verify-only).

扫描 ``scripts/verify_*.py`` 与 ``scripts/_verify_lib.py``, 抽取 ``*_SHA = "<64hex>"``
或 ``*_SHA = ( "<64hex>" )`` 形式的 sha 锁常量, 并尝试推断锁定 target (常见模式:
verify_<id> 交叉锁 / _verify_lib file 锁 / v4_sha.json 表), 以文本或 JSON 形式输出
当前仓库的整套 V4 sha-lock graph, 便于 closeout / Reviewer 一眼把握链路。

用法:
    python scripts/dump_v4_sha_graph.py            # 文本 graph -> stdout
    python scripts/dump_v4_sha_graph.py --json     # JSON dump
    python scripts/dump_v4_sha_graph.py --mermaid  # mermaid graph LR 语法
    python scripts/dump_v4_sha_graph.py --out F    # 写入文件 F
    python scripts/dump_v4_sha_graph.py --show-full-sha  # 文本模式展示完整 64 hex sha
    python scripts/dump_v4_sha_graph.py --filter <regex>  # 按 source/const/target 子串过滤 locks

本脚本是 *只读* 工具, 不修改任何文件, 不依赖 reachy-mini SDK。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
V4_SHA_JSON = REPO / "evidence" / "infra-034" / "v4_sha.json"

# infra-048-backlog-mermaid-palette-extract: 6 类 classDef 色值常量化
# render_mermaid 按固定顺序 (hub/verify/lib/dump/module/unknown) 迭代生成 classDef 行；
# 输出必须与提取前 P272 状态 bytewise 完全一致 (6 个 verify 依赖此输出)。
_MERMAID_PALETTE: Dict[str, Dict[str, str]] = {
    "hub":     {"fill": "#fc6", "stroke": "#b85", "color": "#000"},
    "verify":  {"fill": "#9cf", "stroke": "#069", "color": "#000"},
    "lib":     {"fill": "#9f9", "stroke": "#090", "color": "#000"},
    "dump":    {"fill": "#ff9", "stroke": "#990", "color": "#000"},
    "module":  {"fill": "#c9f", "stroke": "#609", "color": "#000"},
    "unknown": {"fill": "#f99", "stroke": "#900", "color": "#000"},
}

# 形如  CONST = "abc...64..."  或  CONST = (\n    "abc...64..."\n)
_RE_SINGLELINE = re.compile(
    r'^([A-Z_][A-Z0-9_]*)\s*=\s*["\']([0-9a-f]{64})["\']\s*(?:#.*)?$'
)
_RE_TUPLE_OPEN = re.compile(r'^([A-Z_][A-Z0-9_]*)\s*=\s*\(\s*$')
_RE_TUPLE_HEX = re.compile(r'^\s*["\']([0-9a-f]{64})["\']\s*,?\s*$')

# 用于推断 target: 例如 EXPECTED_VERIFY_024_SHA256 -> verify_interact_024 / verify_robot_024 / verify_infra_024 等
#
# infra-039-backlog-bump-regex-normalize (phase-51 #1.51):
# - 位数策略统一: 所有 *_NUM_HINT / *_HINT 命名空间内, 形如 "前缀_<digits>_" 的捕获组,
#   一律使用宽松匹配 \d{2,4} (而非裸 \d+ 或固定 \d{3}); 既覆盖历史 2 位 (NN) 与新生 3 位 (NNN)
#   feature id 范畴, 也避免 \d+ 误吞过长数字串。
# - docstring 语义: 每条 _RE_* 注释中标明 (a) 期望匹配的常量名样例,
#   (b) 捕获组语义 (NN-NNN feature 序号字符串), (c) 调用方在 _infer_target 中的用途。
_RE_VERIFY_HINT = re.compile(r'VERIFY_(\d{2,4})_')  # 例: VERIFY_024 / VERIFY_1024; group1 = 数字串
_RE_LIB_HINT = re.compile(r'(LIB|VERIFY_LIB|HELPER)')  # 关键字命中即视为 _verify_lib 相关
# 形如 V018_EXPECTED_SHA / EXPECTED_V024_SHA256 / V010_EXPECTED_SHA; group1 = 2-4 位 feature 序号
_RE_V_NUM_HINT = re.compile(r'V(\d{2,4})_')
# 形如 BUMP_028_EXPECTED_SHA / BUMP_1024_*; group1 = 2-4 位 feature 序号 (与 V_NUM 对齐, 避免 \d+ 误吞)
_RE_BUMP_HINT = re.compile(r'BUMP_(\d{2,4})_')

# infra-039-backlog-target-inference: 非数字常量名 → 文件路径 查表 (fingerprint / bump-only / dump 自锁)
# 用于覆盖 _RE_VERIFY_HINT 无法识别的复合 sha 锁; 含通用文件 sha 与 func sha
_KNOWN_NON_NUMERIC_TARGETS: Dict[str, str] = {
    # _verify_lib 反向锁
    "EXPECTED_LIB_FILE_SHA": "scripts/_verify_lib.py (file-sha)",
    "EXPECTED_VERIFY_LIB_FILE_SHA": "scripts/_verify_lib.py (file-sha)",
    "EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA": "scripts/_verify_lib.py:func_sha_by_name (func-sha)",
    "EXPECTED_V6_SCAN_FUNC_SHA": "scripts/_verify_lib.py:_v6_scan_constants (func-sha)",
    "EXPECTED_V6_TARGET_ID_FUNC_SHA": "scripts/_verify_lib.py:_v6_target_id (func-sha)",
    "EXPECTED_READ_CONSTANT_FUNC_SHA": "scripts/_verify_lib.py:read_constant (func-sha)",
    # dump_v4_sha_graph 自锁 (infra-039 / 043 / 044)
    "EXPECTED_DUMP_FILE_SHA": "scripts/dump_v4_sha_graph.py (file-sha)",
    "EXPECTED_RENDER_MERMAID_FUNC_SHA": "scripts/dump_v4_sha_graph.py:render_mermaid (func-sha)",
    "EXPECTED_INFER_TARGET_FUNC_SHA": "scripts/dump_v4_sha_graph.py:_infer_target (func-sha)",
    # verify_template 锁 (infra-040)
    "EXPECTED_VERIFY_TMPL_SHA": "scripts/_verify_template.py (file-sha; if exists)",
}

# infra-039-backlog-source-file-aware: (source_file_basename, const_name) → target 二级查表
# 用于跨 verify 文件同名常量歧义场景 (如 EXPECTED_FILE_SHA / EXPECTED_FUNC_SHA / SETTER_BLOCK_EXPECTED_SHA),
# 单纯按 const 名无法判定; 必须结合 source_file 才能锁出唯一 target。
_PER_FILE_LOCKS: Dict[Tuple[str, str], str] = {
    # verify_infra_045 — 锁 _verify_lib.py 的具体 func sha
    ("verify_infra_045.py", "EXPECTED_SCAN_FUNC_SHA"):
        "scripts/_verify_lib.py:scan_reverse_sha_locks (func-sha)",
    ("verify_infra_045.py", "EXPECTED_LIVE_FUNC_SHA"):
        "scripts/_verify_lib.py:live_verify_sha_set (func-sha)",
    ("verify_infra_045.py", "EXPECTED_CHECK_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_reverse_sha_lock_consistency (func-sha)",
    # verify_infra_046 — 锁 bump_reverse_sha_lock.py 的 file / func sha
    ("verify_infra_046.py", "EXPECTED_BUMP_FILE_SHA"):
        "scripts/bump_reverse_sha_lock.py (file-sha)",
    ("verify_infra_046.py", "EXPECTED_RUN_BUMP_FUNC_SHA"):
        "scripts/bump_reverse_sha_lock.py:run_bump (func-sha)",
    ("verify_infra_046.py", "EXPECTED_FIND_LOCKS_FUNC_SHA"):
        "scripts/bump_reverse_sha_lock.py:find_locks_for_target (func-sha)",
    ("verify_infra_046.py", "EXPECTED_BUMP_IN_FILE_FUNC_SHA"):
        "scripts/bump_reverse_sha_lock.py:_bump_in_file (func-sha)",
    ("verify_infra_046.py", "EXPECTED_MAIN_FUNC_SHA"):
        "scripts/bump_reverse_sha_lock.py:main (func-sha)",
    # verify_interact_037 — 锁 verify_interact_024.py 的 func / file sha
    ("verify_interact_037.py", "EXPECTED_FUNC_SHA"):
        "scripts/verify_interact_024.py:_append_drift_history (func-sha)",
    ("verify_interact_037.py", "EXPECTED_FILE_SHA"):
        "scripts/verify_interact_024.py (file-sha)",
    # verify_robot_025 / 027 — coco/proactive.py setter block
    ("verify_robot_025.py", "SETTER_BLOCK_EXPECTED_SHA"):
        "coco/proactive.py (setter block sha)",
    ("verify_robot_027.py", "SETTER_BLOCK_EXPECTED_SHA"):
        "coco/proactive.py (setter block sha)",
    ("verify_robot_028.py", "BUMP_EXPECTED_SHA"):
        "coco/proactive.py (bump-only block sha)",
    ("verify_robot_029.py", "SETTER_BASELINE_EXPECTED_SHA"):
        "coco/proactive.py (setter baseline sha)",
    ("verify_robot_030.py", "BLOCK_BASELINE_EXPECTED_SHA"):
        "coco/proactive.py (block baseline sha)",
    # verify_robot_033 — coco/proactive.py 多锚点
    ("verify_robot_033.py", "INIT_LINE_SHA"):
        "coco/proactive.py (init line sha)",
    ("verify_robot_033.py", "EXCEPT_BLOCK_SHA"):
        "coco/proactive.py (except block sha)",
    ("verify_robot_033.py", "FILE_SHA"):
        "coco/proactive.py (file-sha)",
    # verify_robot_034 — docs file
    ("verify_robot_034.py", "EXPECTED_DOC_SHA"):
        "docs (robot-032 headings doc) (file-sha)",
    # verify_robot_036 — verify_robot_032.py sentinel line
    ("verify_robot_036.py", "EXPECTED_SENTINEL_LINE_SHA"):
        "scripts/verify_robot_032.py:_HEADINGS_SECTION_SENTINEL (line-sha)",
    # ── P285 phase-37 #1.37 ──
    # infra-P285-classifier-recognize-lib-func-locks: 补全 13 个 unknown 中可消除项的 (source, const) → target
    # 真实 target 取自各 verify 脚本 docstring 头部 ``sha 锁清单`` 字段; 落地后这些 const
    # 在 render_mermaid 中可解析到正确文件 stem (而非 unknown_<CONST> 占位), 并按 _classify_node
    # 归 lib (_verify_lib helper) / dump (dump_reverse_sha_lock_index helper) 两类。
    # verify_infra_049 / 050 / 051 — 锁 dump_reverse_sha_lock_index.py file / func sha
    ("verify_infra_049.py", "EXPECTED_DUMP_INDEX_FILE_SHA"):
        "scripts/dump_reverse_sha_lock_index.py (file-sha)",
    ("verify_infra_049.py", "EXPECTED_RENDER_TEXT_FUNC_SHA"):
        "scripts/dump_reverse_sha_lock_index.py:render_text (func-sha)",
    ("verify_infra_049.py", "EXPECTED_RENDER_JSON_FUNC_SHA"):
        "scripts/dump_reverse_sha_lock_index.py:render_json (func-sha)",
    ("verify_infra_050.py", "EXPECTED_DUMP_INDEX_FILE_SHA"):
        "scripts/dump_reverse_sha_lock_index.py (file-sha)",
    ("verify_infra_050.py", "EXPECTED_RENDER_JSON_FUNC_SHA"):
        "scripts/dump_reverse_sha_lock_index.py:render_json (func-sha)",
    ("verify_infra_051.py", "EXPECTED_DUMP_INDEX_FILE_SHA"):
        "scripts/dump_reverse_sha_lock_index.py (file-sha)",
    ("verify_infra_051.py", "EXPECTED_RENDER_CHECK_JSON_FUNC_SHA"):
        "scripts/dump_reverse_sha_lock_index.py:render_check_json (func-sha)",
    ("verify_infra_051.py", "EXPECTED_CMD_CHECK_FUNC_SHA"):
        "scripts/dump_reverse_sha_lock_index.py:cmd_check (func-sha)",
    ("verify_infra_051.py", "EXPECTED_BUILD_ARG_PARSER_FUNC_SHA"):
        "scripts/dump_reverse_sha_lock_index.py:build_arg_parser (func-sha)",
    # verify_infra_052 — _verify_lib helper sha (与 045 同名 const, 同 lib helper)
    ("verify_infra_052.py", "EXPECTED_SCAN_FUNC_SHA"):
        "scripts/_verify_lib.py:scan_reverse_sha_locks (func-sha)",
    ("verify_infra_052.py", "EXPECTED_CHECK_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_reverse_sha_lock_consistency (func-sha)",
    # verify_infra_055 / 057 / 058 / 059 — _verify_lib helper sha (各 1 个)
    ("verify_infra_055.py", "EXPECTED_ASSERT_VERIFY_PASSED_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_verify_passed (func-sha)",
    ("verify_infra_057.py", "EXPECTED_VERIFY_EP_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_expected_pattern_consistency (func-sha)",
    ("verify_infra_058.py", "EXPECTED_PALETTE_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_palette_fills_distinct (func-sha)",
    ("verify_infra_059.py", "EXPECTED_UNKNOWN_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_unknown_node_count_bound (func-sha)",
    # verify_infra_060 — 锁 dump_v4_sha_graph.py 自身 _classify_node func sha
    ("verify_infra_060.py", "EXPECTED_CLASSIFY_FUNC_SHA"):
        "scripts/dump_v4_sha_graph.py:_classify_node (func-sha)",
    # verify_infra_061 (P281) — 锁 verify_expected_prefix_typo_guard func sha
    ("verify_infra_061.py", "EXPECTED_TYPO_GUARD_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_expected_prefix_typo_guard (func-sha)",
    # verify_infra_062 (P278) — 锁 verify_closeout_evidence_trustworthy func sha
    ("verify_infra_062.py", "EXPECTED_CLOSEOUT_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_closeout_evidence_trustworthy (func-sha)",
    # verify_infra_063 (P276) — 锁 bootstrap_verify_self_checker.py file / func / const sha
    ("verify_infra_063.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_063.py", "EXPECTED_BOOTSTRAP_FILE_SHA"):
        "scripts/bootstrap_verify_self_checker.py (file-sha)",
    ("verify_infra_063.py", "EXPECTED_CANARY_FUNC_SHA"):
        "scripts/bootstrap_verify_self_checker.py:run_canary_self_check (func-sha)",
    ("verify_infra_063.py", "EXPECTED_CANARY_CONST_SHA"):
        "scripts/bootstrap_verify_self_checker.py:_CANARY_EXPECTED_SHA (const-lock)",
    # verify_infra_064 (P284) — 锁 smoke_history.jsonl 不再 tracked 政策
    ("verify_infra_064.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    # verify_infra_064 (P298) — V3 helper full coverage
    ("verify_infra_064.py", "EXPECTED_V3_FILE_SHA_FUNC_SHA"):
        "scripts/verify_infra_064.py:_file_sha (func-sha)",
    ("verify_infra_064.py", "EXPECTED_V3_EMIT_FUNC_SHA"):
        "scripts/verify_infra_064.py:_emit (func-sha)",
    # verify_infra_065 (P294-R4) — 锁 verify_baseline_fail_claims helper
    ("verify_infra_065.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_065.py", "EXPECTED_BASELINE_FAIL_CROSS_CHECK_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_baseline_fail_claims (func-sha)",
    # verify_infra_066 (P294-R5) — 锁 verify_closeout_evidence_trustworthy (派生 total_checks)
    ("verify_infra_066.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_066.py", "EXPECTED_CLOSEOUT_TRUSTWORTHY_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_closeout_evidence_trustworthy (func-sha)",
    # verify_infra_067 (P294-Rx) — 锁 verify_summary_exit helper
    ("verify_infra_067.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_067.py", "EXPECTED_VERIFY_SUMMARY_EXIT_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_summary_exit (func-sha)",
    # verify_infra_068 (P294-Ry) — 锁 scan_reviewer_text helper
    ("verify_infra_068.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_068.py", "EXPECTED_REVIEWER_TEXT_SCAN_FUNC_SHA"):
        "scripts/_verify_lib.py:scan_reviewer_text (func-sha)",
    # verify_infra_069 (P297) — 锁 bootstrap canary edit-flow docs / helper
    ("verify_infra_069.py", "EXPECTED_BOOTSTRAP_FILE_SHA"):
        "scripts/bootstrap_verify_self_checker.py (file-sha)",
    # verify_infra_070 (P294-closeout-stdout-sha-verification) — 锁 verify_evidence_tail_stdout_sha helper
    ("verify_infra_070.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_070.py", "EXPECTED_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_evidence_tail_stdout_sha (func-sha)",
    # verify_infra_071 (P291-reviewer-gate-real-or-remove) — 锁 assert_reviewer_lgtm helper
    ("verify_infra_071.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_071.py", "EXPECTED_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_reviewer_lgtm (func-sha)",
    # verify_infra_072 (P299-closeout-verify-trustworthy-helper-passed-checks-field) —
    # 锁 verify_closeout_evidence_trustworthy helper, 校 passed_checks 字段 + 守恒律
    ("verify_infra_072.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_072.py", "EXPECTED_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_closeout_evidence_trustworthy (func-sha)",
    # verify_infra_073 (P299-baseline-fail-claim-regex-multiline) — 锁 baseline FAIL claim
    # regex 跨行支持 + verify_baseline_fail_claims helper
    ("verify_infra_073.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_073.py", "EXPECTED_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:verify_baseline_fail_claims (func-sha)",
    # verify_infra_074 (P286-total-nodes-lock) — 锁 verify_infra_059 EXPECTED_CURRENT_TOTAL_NODES 检查
    ("verify_infra_074.py", "EXPECTED_VERIFY_059_FILE_SHA"):
        "scripts/verify_infra_059.py (file-sha)",
    ("verify_infra_074.py", "EXPECTED_059_MAIN_FUNC_SHA"):
        "scripts/verify_infra_059.py:main (func-sha)",
    # verify_infra_075 (P299-engineer-report-vs-impl-trustworthy) — 锁 assert_report_matches_closeout_runs helper
    ("verify_infra_075.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_075.py", "EXPECTED_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_report_matches_closeout_runs (func-sha)",
    # verify_infra_076 (P291-followup-extend-helper-to-other-v5) — 锁 assert_reviewer_lgtm helper
    ("verify_infra_076.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_076.py", "EXPECTED_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_reviewer_lgtm (func-sha)",
    # verify_infra_077 (P286-followup-round1-reviewer-baseline-head-mismatch) —
    # 锁 assert_reviewer_baseline_head_echo helper
    ("verify_infra_077.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_077.py", "EXPECTED_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_reviewer_baseline_head_echo (func-sha)",
    # verify_infra_080 (P291-followup2-helper-return-value-must-participate-in-emit) —
    # 锁 assert_v5_gate_emit_uses_helper_return helper + 自 main + lib file
    # round-2 P0-1 修: 两个 func-sha 常量名移除 VERIFY_080 中缀, 避免被 V6
    # 反向锁 regex 误判为 verify_id orphan.
    ("verify_infra_080.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_080.py", "EXPECTED_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_v5_gate_emit_uses_helper_return (func-sha)",
    ("verify_infra_080.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_080.py:main (func-sha)",
    # verify_infra_081 (P299-followup-wire-into-closeout-gate) — 锁
    # assert_report_matches_closeout_runs helper + lib file + 062 file (wire
    # target) + 自 main; 不锁 self file sha (与 074/079/080 同形)
    ("verify_infra_081.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_081.py", "EXPECTED_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_report_matches_closeout_runs (func-sha)",
    ("verify_infra_081.py", "EXPECTED_VERIFY_062_FILE_SHA"):
        "scripts/verify_infra_062.py (file-sha)",
    ("verify_infra_081.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_081.py:main (func-sha)",
    # verify_infra_082 (P286-followup3-promote-baseline-head-echo-to-P278-hard-required) —
    # phase-42 #1.42: 锁 assert_baseline_head_echo_present_and_matches helper + lib
    # file + 062 file (wire target) + 自 main; 不锁 self file sha (同 074/079/080/081 形)
    ("verify_infra_082.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_082.py", "EXPECTED_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_baseline_head_echo_present_and_matches (func-sha)",
    ("verify_infra_082.py", "EXPECTED_VERIFY_062_FILE_SHA"):
        "scripts/verify_infra_062.py (file-sha)",
    ("verify_infra_082.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_082.py:main (func-sha)",
    # verify_infra_084 (P278-followup-reviewer-summary-nonempty-hard-check) —
    # phase-42 #4.42: 锁 assert_reviewer_summary_nonempty helper + lib file +
    # 自 main; 不锁 self file sha (同 074/079/080/081/082/083 形)
    ("verify_infra_084.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_084.py", "EXPECTED_REVIEWER_SUMMARY_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_reviewer_summary_nonempty (func-sha)",
    ("verify_infra_084.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_084.py:main (func-sha)",
    # verify_infra_085 (P278-followup-verify-lib-helper-naming-convention-lock) —
    # phase-42 #5.42: 锁 assert_verify_lib_public_helper_naming helper + lib file +
    # 自 main; 不锁 self file sha (同 074/079/080/081/082/083/084 形)
    ("verify_infra_085.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_085.py", "EXPECTED_NAMING_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_verify_lib_public_helper_naming (func-sha)",
    ("verify_infra_085.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_085.py:main (func-sha)",
    # verify_infra_086 (P278-followup-closeout-verify-runs-min-count-hard-check) —
    # phase-43 #4.43: 锁 assert_closeout_verify_runs_min_count helper + lib file +
    # 自 main; 不锁 self file sha (同 074/079/080/081/082/083/084/085 形)
    ("verify_infra_086.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_086.py", "EXPECTED_RUNS_MIN_COUNT_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_closeout_verify_runs_min_count (func-sha)",
    ("verify_infra_086.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_086.py:main (func-sha)",
    # verify_infra_087 (P278-followup-closeout-smoke-tail-nonempty-hard-check) —
    # phase-43 #5.43: 锁 assert_closeout_smoke_tail_nonempty helper + lib file +
    # 自 main; 不锁 self file sha (同 074/079/080/081/082/083/084/085/086 形)
    ("verify_infra_087.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_087.py", "EXPECTED_SMOKE_TAIL_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_closeout_smoke_tail_nonempty (func-sha)",
    ("verify_infra_087.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_087.py:main (func-sha)",
    # verify_infra_088 (V6-backlog-062-v3-helper-func-sha-rebump-round2) —
    # phase-44 #2.44: 锁 assert_verify_lib_helpers_in_v3_sha_table helper +
    # lib file + 自 main; 不锁 self file sha (同 074/079-087 形)
    ("verify_infra_088.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_088.py", "EXPECTED_DRIFT_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_verify_lib_helpers_in_v3_sha_table (func-sha)",
    ("verify_infra_088.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_088.py:main (func-sha)",
    # verify_infra_089 (P278-followup-closeout-verify-runs-status-shape-hard-check) —
    # phase-44 #3.44: 锁 assert_closeout_verify_runs_shape helper +
    # lib file + 自 main; 不锁 self file sha (同 074/079-088 形)
    ("verify_infra_089.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_089.py", "EXPECTED_SHAPE_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_closeout_verify_runs_shape (func-sha)",
    ("verify_infra_089.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_089.py:main (func-sha)",
    # verify_infra_090 (P278-followup-closeout-reviewer-block-shape-hard-check) —
    # phase-44 #4.44: 锁 assert_closeout_reviewer_block_shape helper +
    # lib file + 自 main; 不锁 self file sha (同 074/079-089 形)
    ("verify_infra_090.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_090.py", "EXPECTED_REVIEWER_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_closeout_reviewer_block_shape (func-sha)",
    ("verify_infra_090.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_090.py:main (func-sha)",
    # verify_infra_091 (P278-followup-closeout-baseline-head-echo-format-hard-check) —
    # phase-44 #5.44: 锁 assert_closeout_baseline_head_echo_format helper +
    # lib file + 自 main; 不锁 self file sha (同 074/079-090 形)
    ("verify_infra_091.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_091.py", "EXPECTED_BASELINE_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_closeout_baseline_head_echo_format (func-sha)",
    ("verify_infra_091.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_091.py:main (func-sha)",
    # verify_infra_092 (infra-V6-backlog-062-v4-closeout-verify-runs-shape-promote-bool) —
    # phase-45 #1.45: 锁 062 _enforce_closeout_verify_runs_shape 已 promote 真硬 +
    # 配套 V4_VERIFY_RUNS_SHAPE_GRACE_PERIOD_FEATURE_IDS 17 historic grace_set 行为
    ("verify_infra_092.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_092.py", "EXPECTED_SHAPE_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_closeout_verify_runs_shape (func-sha)",
    ("verify_infra_092.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_092.py:main (func-sha)",
    # verify_infra_093 (infra-V6-backlog-062-v4-closeout-reviewer-block-shape-promote-bool) —
    # phase-45 #2.45: 锁 062 _enforce_closeout_reviewer_block_shape 已 promote 真硬 +
    # 配套 V4_REVIEWER_BLOCK_SHAPE_GRACE_PERIOD_FEATURE_IDS 22 historic grace_set 行为
    ("verify_infra_093.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_093.py", "EXPECTED_SHAPE_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_closeout_reviewer_block_shape (func-sha)",
    ("verify_infra_093.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_093.py:main (func-sha)",
    # verify_infra_094 (infra-V6-backlog-062-v4-closeout-baseline-head-echo-format-promote-bool) —
    # phase-45 #3.45: 锁 062 _enforce_closeout_baseline_head_echo_format 已 promote 真硬 +
    # 配套 V4_BASELINE_HEAD_ECHO_FORMAT_GRACE_PERIOD_FEATURE_IDS 16 historic grace_set 行为
    ("verify_infra_094.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_094.py", "EXPECTED_BASELINE_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_closeout_baseline_head_echo_format (func-sha)",
    ("verify_infra_094.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_094.py:main (func-sha)",
    # verify_infra_095 (infra-P278-followup-closeout-merge-commit-sha-format-hard-check) —
    # phase-45 #4.45: 新增 V4_closeout_merge_commit_sha_format hard check +
    # 配套 V4_MERGE_COMMIT_SHA_FORMAT_GRACE_PERIOD_FEATURE_IDS 14 historic grace_set 行为
    ("verify_infra_095.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_095.py", "EXPECTED_MERGE_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_closeout_merge_commit_sha_format (func-sha)",
    ("verify_infra_095.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_095.py:main (func-sha)",
    # verify_infra_096 (infra-P278-followup-closeout-main-head-sha-format-hard-check) —
    # phase-45 #5.45: 新增 V4_closeout_main_head_sha_format hard check +
    # 配套 V4_MAIN_HEAD_SHA_FORMAT_GRACE_PERIOD_FEATURE_IDS sentinel grace_set 行为
    ("verify_infra_096.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_096.py", "EXPECTED_MAIN_HEAD_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_closeout_main_head_sha_format (func-sha)",
    ("verify_infra_096.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_096.py:main (func-sha)",
    # verify_infra_097 (infra-P299-followup-engineer-stale-verify-evidence) —
    # phase-46 #2.46: 新增 V4_closeout_verify_runs_freshness hard check +
    # 配套 V4_VERIFY_RUNS_FRESHNESS_GRACE_PERIOD_FEATURE_IDS (34 historic) grace_set 行为
    ("verify_infra_097.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_097.py", "EXPECTED_FRESHNESS_HELPER_FUNC_SHA"):
        "scripts/_verify_lib.py:assert_closeout_verify_runs_freshness (func-sha)",
    ("verify_infra_097.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_097.py:main (func-sha)",
    # verify_infra_102 (infra-P290-classify-node-family-sets) —
    # phase-53 #3.53: 锁 _classify_node family frozenset 改写 + dump file sha;
    # _classify_node func sha 已由 verify_infra_060 (EXPECTED_CLASSIFY_FUNC_SHA) 主锁,
    # 这里仅锁 dump_v4_sha_graph.py file sha + 自 main + lib file sha (用于 V5 helper)。
    ("verify_infra_102.py", "EXPECTED_DUMP_FILE_SHA"):
        "scripts/dump_v4_sha_graph.py (file-sha)",
    ("verify_infra_102.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_102.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_102.py:main (func-sha)",
    # verify_infra_104 (infra-047-backlog-per-file-self-locks-comment) —
    # phase-53 #5.53: 锁 dump_v4_sha_graph.py file sha + _verify_lib.py file sha + 自 main func sha。
    ("verify_infra_104.py", "EXPECTED_DUMP_FILE_SHA"):
        "scripts/dump_v4_sha_graph.py (file-sha)",
    ("verify_infra_104.py", "EXPECTED_VERIFY_LIB_FILE_SHA"):
        "scripts/_verify_lib.py (file-sha)",
    ("verify_infra_104.py", "EXPECTED_SELF_MAIN_FUNC_SHA"):
        "scripts/verify_infra_104.py:main (func-sha)",
}

# infra-039-backlog-source-file-aware: 真自锁 const 名 (target = source_file 自身)
# 当 (source_file, const_name) 未命中 _PER_FILE_LOCKS 时, 这里命中则返回 source_file 自锁标记。
#
# V1_SELF_LOCKS_COMMENT_BEGIN  (infra-104-backlog-v1-window-hardening sentinel — DO NOT REMOVE)
# infra-047-backlog-per-file-self-locks-comment (phase-53 #5.53) — 集合边界说明:
# 本集合 _PER_FILE_SELF_LOCKS 只装"target = source_file 自身的 file-sha 自锁" (整文件 sha 锁)。
# 形似自锁但实际指向 source_file 内某段 func-sha / block-sha 的常量
# (如 verify_robot_025.py 内的 SETTER_BLOCK_EXPECTED_SHA 锁 _apply_setter func-sha,
# verify_robot_033.py 内的 EXCEPT_BLOCK_SHA 锁 except block func-sha)
# 一律走上面的 _PER_FILE_LOCKS 二级查表, 不进本集合。
# 维护规则: 新增常量若 target 不是 source_file 整文件 sha, 一律归 _PER_FILE_LOCKS。
# V1_SELF_LOCKS_COMMENT_END  (infra-104-backlog sentinel — DO NOT REMOVE)
_PER_FILE_SELF_LOCKS: set = {
    "EXPECTED_FINGERPRINT",  # verify_infra_022 / verify_infra_028 自我 fingerprint
}


def _scan_file(path: Path) -> List[Dict[str, str]]:
    """返回 [{const, sha, line}] 列表."""
    out: List[Dict[str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return out
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _RE_SINGLELINE.match(line)
        if m:
            out.append({"const": m.group(1), "sha": m.group(2), "line": str(i + 1)})
            i += 1
            continue
        m2 = _RE_TUPLE_OPEN.match(line)
        if m2 and i + 1 < len(lines):
            mh = _RE_TUPLE_HEX.match(lines[i + 1])
            if mh:
                out.append({"const": m2.group(1), "sha": mh.group(1), "line": str(i + 1)})
                i += 2
                continue
        i += 1
    return out


def _infer_target_via_ast(const: str, source_file: str) -> Optional[str]:
    """infra-V12-AST-based-infer-target-hardening (phase-64 #5).

    用 AST + _verify_lib helper 推断 target, 不依赖正则字符串拼接。优先级:

      (a) 若 source_file 在 module docstring 中有 ``## Lock: <const>`` 小节,
          (经 _verify_lib._parse_lock_docstring 解析), 直接返回
          ``"<target_file>:<target_function> (<lock_kind>)"``。这是 truth source。

      (b) 对形如 ``EXPECTED_<NAME>_FUNC_SHA[256]`` 的常量, 取 ``name = NAME.lower()``
          (e.g. ``EXPECTED_PARSE_AREA_FUNC_SHA`` → ``parse_area``), 在 source_file 自身
          先后尝试 ``name`` / ``_<name>`` (private convention), 用 AST FunctionDef
          的 ``ast.unparse`` 与 _verify_lib.func_sha_by_name 计算 sha; 若与该常量绑定
          的 sha 字面值完全一致, 返回 ``"<source_file>:<func_name> (ast func-sha)"``。

    任一步未命中或 helper import 失败均返回 None, 让旧逻辑兜底。本函数纯只读,
    无副作用; 失败时静默 fallback。
    """
    if not source_file:
        return None
    # 仅对 verify_*.py / dump_v4_sha_graph.py / _verify_lib.py 启用 AST 推断
    src_base = source_file.rsplit("/", 1)[-1]
    if not (src_base.startswith("verify_") or src_base in ("dump_v4_sha_graph.py", "_verify_lib.py")):
        return None
    try:
        # lazy import 避免 import 阶段循环
        sys.path.insert(0, str(SCRIPTS))
        import _verify_lib as _L  # type: ignore
    except Exception:
        return None
    # (a) docstring lock 优先
    try:
        doc_locks = _L._parse_lock_docstring(REPO / source_file)
    except Exception:
        doc_locks = None
    if doc_locks and const in doc_locks:
        meta = doc_locks[const]
        tf = meta.get("target_function") or ""
        tfile = meta.get("target_file") or ""
        kind = meta.get("lock_kind") or "ast_func_sha"
        if tfile and tf:
            return f"{tfile}:{tf} ({kind})"
        if tfile:
            return f"{tfile} ({kind})"
        if tf:
            return f"{source_file}:{tf} ({kind})"
    # (b) AST 同文件 FUNC_SHA 推断
    m = re.match(r'^EXPECTED_(.+?)_FUNC_SHA(?:256)?$', const)
    if not m:
        return None
    guess = m.group(1).lower()
    if not guess:
        return None
    candidates = [guess, f"_{guess}"]
    src_path = REPO / source_file
    if not src_path.is_file():
        return None
    # 取该 const 在源文件中的 sha 字面值 (与 _scan_file 同语义)
    try:
        text = src_path.read_text(encoding="utf-8")
    except Exception:
        return None
    bound_sha: Optional[str] = None
    for entry in _scan_file(src_path):
        if entry["const"] == const:
            bound_sha = entry["sha"]
            break
    if not bound_sha:
        return None
    for cand in candidates:
        try:
            got = _L.func_sha_by_name(src_path, cand)
        except Exception:
            got = ""
        if got and got == bound_sha:
            return f"{source_file}:{cand} (ast func-sha)"
    return None


def _infer_target(const: str, source_file: str) -> str:
    """从常量名推断锁定 target. 无把握时返回 '<unknown>'.

    infra-039-backlog: 先查 _KNOWN_NON_NUMERIC_TARGETS, 再走 _RE_VERIFY_HINT,
    再尝试 V<NNN>_ / BUMP_<NNN>_ 数字提取, 最后是 lib / self / unknown 兜底。

    infra-V12-AST-based-infer-target-hardening (phase-64 #5): 在 step 0.7 注入
    _infer_target_via_ast, 用 docstring lock + AST 同文件 FUNC_SHA 推断替代
    字符串模式拼接, 把 not_found 数从 22 降到 <20 (acceptance 阈值)。
    """
    # 0) source-file-aware 查表 (infra-039-backlog)
    # 同一 const 名在不同 verify 脚本中锁不同 target 的歧义场景, 必须结合 source_file 锁定
    src_base = source_file.rsplit("/", 1)[-1] if source_file else ""
    if src_base and (src_base, const) in _PER_FILE_LOCKS:
        return _PER_FILE_LOCKS[(src_base, const)]
    # 0.5) source-file self-lock (target = source_file 自身)
    if src_base and const in _PER_FILE_SELF_LOCKS:
        return f"{source_file} (self file-sha)"
    # 0.7) AST + docstring lock 推断 (infra-V12, phase-64 #5)
    ast_target = _infer_target_via_ast(const, source_file)
    if ast_target is not None:
        return ast_target
    # 1) 非数字常量名查表 (fingerprint / bump-only / dump 自锁)
    if const in _KNOWN_NON_NUMERIC_TARGETS:
        return _KNOWN_NON_NUMERIC_TARGETS[const]
    # 2) 优先 _verify_lib 锁
    if _RE_LIB_HINT.search(const):
        if "FILE" in const:
            return "scripts/_verify_lib.py (file-sha)"
        if "FUNC" in const:
            # 形如 EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA → func_sha_by_name
            tail = const.replace("EXPECTED_", "").replace("_FUNC_SHA", "")
            return f"scripts/_verify_lib.py:{tail.lower()} (func-sha)"
        if "HELPER" in const:
            return "scripts/_verify_lib.py (helper sha)"
        return "scripts/_verify_lib.py"
    # 3) VERIFY_<num>_ 交叉锁
    m = _RE_VERIFY_HINT.search(const)
    if m:
        num = m.group(1)
        candidates = sorted(SCRIPTS.glob(f"verify_*_{num}.py"))
        if candidates:
            rels = [str(c.relative_to(REPO)) for c in candidates]
            return f"{', '.join(rels)} (file-sha)"
        return f"scripts/verify_*_{num}.py (file-sha; not found)"
    # 4) V<NNN>_ / BUMP_<NNN>_ 数字提取 (infra-039-backlog)
    mv = _RE_V_NUM_HINT.search(const) or _RE_BUMP_HINT.search(const)
    if mv:
        num = mv.group(1).lstrip("0") or "0"
        # 3 位 V018 -> 18; 直接补齐 3 位查找
        num3 = num.zfill(3)
        candidates = sorted(SCRIPTS.glob(f"verify_*_{num3}.py"))
        if candidates:
            rels = [str(c.relative_to(REPO)) for c in candidates]
            return f"{', '.join(rels)} (file-sha)"
        return f"scripts/verify_*_{num3}.py (file-sha; not found)"
    # 5) 自锁 — checker 自身函数 sha
    if "CHECKER" in const or "SELF" in const:
        return f"{source_file} self-checker (func-sha)"
    return "<unknown target>"


def _scan_v4_sha_json() -> Optional[Dict[str, str]]:
    if not V4_SHA_JSON.is_file():
        return None
    try:
        data = json.loads(V4_SHA_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data.get("targets") or {}


def build_graph() -> Dict:
    """返回结构:
    {
      "v4_sha_json": {"path": str, "targets": {path: sha, ...}, "count": int} | None,
      "locks": [
        {"source": "scripts/verify_xxx.py", "const": "...", "sha": "...", "target": "..."},
        ...
      ],
    }
    """
    graph: Dict = {"v4_sha_json": None, "locks": []}
    v4 = _scan_v4_sha_json()
    if v4 is not None:
        graph["v4_sha_json"] = {
            "path": str(V4_SHA_JSON.relative_to(REPO)),
            "count": len(v4),
            "targets": v4,
        }

    files: List[Path] = []
    files.extend(sorted(SCRIPTS.glob("verify_*.py")))
    lib = SCRIPTS / "_verify_lib.py"
    if lib.is_file():
        files.append(lib)
    for path in files:
        rel = str(path.relative_to(REPO))
        for entry in _scan_file(path):
            target = _infer_target(entry["const"], rel)
            graph["locks"].append({
                "source": rel,
                "const": entry["const"],
                "sha": entry["sha"],
                "line": entry["line"],
                "target": target,
            })
    return graph


def filter_locks(graph: Dict, pattern: str) -> Dict:
    """infra-039-backlog-dump-filter-pattern: 按 regex 过滤 ``graph['locks']``。

    匹配 source / const / target 任一字段 ``re.search`` 命中即保留。仅过滤 ``locks``
    数组; ``v4_sha_json`` hub 段原样保留 (它是 V4 hub 元信息, 不是 lock 行)。
    返回**新 dict**, 不原地修改入参。

    pattern 为 None / 空串时原样返回 (caller 应自己判 short-circuit, 此处也兜底)。
    """
    if not pattern:
        return graph
    rx = re.compile(pattern)
    new_locks = [
        lk for lk in graph.get("locks", [])
        if rx.search(lk.get("source", ""))
        or rx.search(lk.get("const", ""))
        or rx.search(lk.get("target", ""))
    ]
    new_graph = dict(graph)
    new_graph["locks"] = new_locks
    return new_graph


def render_text(graph: Dict, show_full_sha: bool = False) -> str:
    """渲染文本 graph。

    infra-039-backlog-dump-show-full-sha: ``show_full_sha=True`` 时输出完整 64 hex
    sha (不带省略号), 默认行为 ``[:16]...`` 保持兼容。
    """
    def _fmt(sha: str) -> str:
        return sha if show_full_sha else f"{sha[:16]}..."

    out: List[str] = []
    out.append("=== V4 SHA-LOCK GRAPH ===")
    out.append("")
    v4 = graph.get("v4_sha_json")
    if v4:
        out.append(f"{v4['path']} ({v4['count']} targets):")
        for tgt, sha in sorted(v4["targets"].items()):
            out.append(f"  ├─ {tgt:<40s} @ {_fmt(sha)}")
        out.append("")
        out.append("scripts/verify_infra_034.py V4")
        out.append(f"  └─→ reads {v4['path']} (sort_keys canonical)")
        out.append(f"      └─→ locks {v4['count']} verify scripts file-sha")
        out.append("")
    # 按 source 文件分组
    locks = graph.get("locks", [])
    by_src: Dict[str, List[Dict]] = {}
    for lock in locks:
        by_src.setdefault(lock["source"], []).append(lock)
    for src in sorted(by_src.keys()):
        items = by_src[src]
        out.append(f"{src}")
        for it in items:
            out.append(f"  └─→ {it['const']} = {_fmt(it['sha'])}  (L{it['line']})")
            out.append(f"      └─→ locks {it['target']}")
        out.append("")
    out.append(f"=== SUMMARY: {len(locks)} sha-lock constants across {len(by_src)} files ===")
    return "\n".join(out)


# infra-P290-classify-node-family-sets (phase-53 #3.53): family 集合显式化。
# 用 frozenset 替代 OR 链 / 单值 ==, 让 hub / lib / dump 家族扩展边界一目了然,
# 新增 dump 工具或 lib helper 只需向集合追加 node_id 即可, 不再改 if 分支。
_HUB_FAMILY: frozenset = frozenset({"v4_sha_json"})
_LIB_FAMILY: frozenset = frozenset({"_verify_lib"})
_DUMP_FAMILY: frozenset = frozenset({"dump_v4_sha_graph", "dump_reverse_sha_lock_index"})


def _classify_node(node_id: str) -> str:
    """根据 node_id 判定 classDef 类别 (infra-039-backlog-mermaid-classDef-styling).

    返回 className: hub / verify / lib / dump / module / unknown

    infra-P290-classify-node-family-sets: hub / lib / dump 三类用模块级 frozenset
    (_HUB_FAMILY / _LIB_FAMILY / _DUMP_FAMILY) 做成员判断, 替代 OR 链 / 单值 ==,
    家族扩展边界显式可见。
    """
    if node_id in _HUB_FAMILY:
        return "hub"
    if node_id.startswith("unknown_"):
        return "unknown"
    if node_id in _LIB_FAMILY:
        return "lib"
    if node_id in _DUMP_FAMILY:
        return "dump"
    if node_id.startswith("verify_"):
        return "verify"
    return "module"


def render_mermaid(graph: Dict) -> str:
    """渲染 mermaid graph LR 语法, 可直接 paste 到 mermaid.live.

    infra-039-backlog-mermaid-classDef-styling: 在末尾 emit 6 类 classDef 与
    每个节点的 class 关联, 视觉上分层 hub / verify / lib / dump / module / unknown。
    """
    out: List[str] = []
    out.append("graph LR")
    nodes: set = set()

    def _node_id(label: str) -> str:
        # sanitize: 只留字母数字下划线
        nid = re.sub(r"[^A-Za-z0-9_]", "_", label)
        if nid and nid[0].isdigit():
            nid = "n_" + nid
        return nid or "anon"

    # v4_sha.json hub 节点
    v4 = graph.get("v4_sha_json")
    if v4:
        hub = "v4_sha_json"
        if hub not in nodes:
            out.append(f'    {hub}["v4_sha.json ({v4["count"]} targets)"]')
            nodes.add(hub)
        for tgt in sorted(v4["targets"].keys()):
            # tgt 形如 scripts/verify_xxx.py
            stem = Path(tgt).stem
            nid = _node_id(stem)
            if nid not in nodes:
                out.append(f'    {nid}["{stem}"]')
                nodes.add(nid)
            out.append(f"    {hub} -->|file-sha| {nid}")

    # 反向 sha lock 边: source --|const|--> target
    #
    # infra-039-backlog-mermaid-tuple-fanout (phase-57 #1):
    # target 字段可能是 "scripts/verify_a.py, scripts/verify_b.py (file-sha)" 形态
    # (来自 _RE_VERIFY_HINT / _RE_V_NUM_HINT / _RE_BUMP_HINT 分支对多 candidate 的 join),
    # 表示**一个 source 反向锁同时绑定 N 个 target file-sha**。早期实现 ``re.search``
    # 只取第一个 .py stem, 把 1→N 耦合压缩成 1→1 single edge, 视觉上丢失 fanout 拓扑。
    # 这里改成: 用 ``re.findall`` 抓出所有 ``<stem>.py`` token, sorted 去重后逐个 emit
    # 独立 ``src --|const| tgt`` 边, 保持 deterministic (sorted by stem)。
    for lock in graph.get("locks", []):
        src_stem = Path(lock["source"]).stem
        src_id = _node_id(src_stem)
        if src_id not in nodes:
            out.append(f'    {src_id}["{src_stem}"]')
            nodes.add(src_id)
        target = lock["target"]
        stems = re.findall(r"([A-Za-z0-9_]+)\.py", target)
        if stems:
            # deterministic fanout: sorted 去重 (保留原序去重亦可, sorted 更稳)
            seen: set = set()
            ordered_stems: List[str] = []
            for s in sorted(stems):
                if s not in seen:
                    seen.add(s)
                    ordered_stems.append(s)
            for tgt_stem in ordered_stems:
                tgt_id = _node_id(tgt_stem)
                if tgt_id not in nodes:
                    out.append(f'    {tgt_id}["{tgt_stem}"]')
                    nodes.add(tgt_id)
                out.append(f"    {src_id} -->|{lock['const']}| {tgt_id}")
        else:
            # unknown target — 用 (source, const) 复合 key 的占位节点
            # infra-039-backlog-mermaid-unknown-target-id-collision (phase-59 #4):
            # 早期实现 ``tgt_id = _node_id("unknown_" + lock["const"])`` 仅用 const 名
            # 作为 unknown 节点 id, 跨 source 同名 const (如 ``EXPECTED_TARGET_FILE_SHA``
            # 在 verify_infra_P290 与 verify_infra_P301 中都出现) 会被合并到同一个
            # ``unknown_<CONST>`` 节点, 视觉上把多 source 共享一个 unknown 目标的拓扑
            # 错误地呈现为一个 unknown 节点接收多条入边, 实际上它们是两个互不相干的
            # 未识别 target。改为 (source_stem, const) 复合 key, 保证每个 source 的
            # 未知 target 独立成节点, label 仍是 ``?<CONST>`` 保持可读性。
            tgt_id = _node_id(f"unknown_{src_stem}_{lock['const']}")
            if tgt_id not in nodes:
                out.append(f'    {tgt_id}["?{lock["const"]}"]')
                nodes.add(tgt_id)
            out.append(f"    {src_id} -->|{lock['const']}| {tgt_id}")

    # infra-039-backlog-mermaid-classDef-styling: classDef 声明 + 每节点 class 关联
    # infra-048-backlog-mermaid-palette-extract: 色值来自 _MERMAID_PALETTE, 固定顺序
    for _cls in ("hub", "verify", "lib", "dump", "module", "unknown"):
        _p = _MERMAID_PALETTE[_cls]
        out.append(
            f"    classDef {_cls} fill:{_p['fill']},stroke:{_p['stroke']},color:{_p['color']};"
        )
    for nid in sorted(nodes):
        out.append(f"    class {nid} {_classify_node(nid)};")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="V4 sha-lock graph dump")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--mermaid", action="store_true", help="emit mermaid graph LR syntax")
    ap.add_argument("--out", type=str, default=None, help="write output to file")
    ap.add_argument(
        "--show-full-sha",
        action="store_true",
        help="text mode emits full 64 hex sha (no truncation); JSON/mermaid unaffected (infra-039-backlog-dump-show-full-sha)",
    )
    ap.add_argument(
        "--filter",
        dest="filter_pattern",
        type=str,
        default=None,
        help="regex 过滤 locks (按 source/const/target 字段 re.search), JSON/text 路径都过滤 (infra-039-backlog-dump-filter-pattern)",
    )
    args = ap.parse_args()
    if args.json and args.mermaid:
        print("[dump_v4_sha_graph] --json and --mermaid are mutually exclusive", file=sys.stderr)
        return 2
    graph = build_graph()
    if args.filter_pattern:
        graph = filter_locks(graph, args.filter_pattern)
    if args.json:
        output = json.dumps(graph, indent=2, sort_keys=True)
    elif args.mermaid:
        output = render_mermaid(graph)
    else:
        output = render_text(graph, show_full_sha=args.show_full_sha)
    if args.out:
        Path(args.out).write_text(output + ("\n" if not output.endswith("\n") else ""), encoding="utf-8")
        print(f"[dump_v4_sha_graph] wrote {args.out} ({len(output)} bytes)", flush=True)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
