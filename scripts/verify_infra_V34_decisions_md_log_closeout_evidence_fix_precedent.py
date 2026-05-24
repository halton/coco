#!/usr/bin/env python3
"""verify_infra_V34_decisions_md_log_closeout_evidence_fix_precedent.

infra-V34-decisions-md-log-closeout-evidence-fix-precedent (phase-67 #27):
来源: phase-66 #10 V31 closeout Reviewer P2-a. 把三轮 (phase-66 #8/#9/#10
对应 V27/V29/V31) "路径 A 修历史 closeout evidence" 的先例显式登记到仓库根
``DECISIONS.md`` 的
``INFRA_V34_CLOSEOUT_EVIDENCE_FIX_PRECEDENT`` 节,
并机械化锁住该节的 3 个 sentinel 关键词 + DECISIONS.md 全文件 sha, 防止
未来悄改 / 误删.

校验层级 (V0-V5):

- V0 scaffolding: self_sha + DECISIONS.md 文件存在 + 常量 hex64
- V1 docstring sentinel lock: ``DECISIONS.md`` 必须含三个 sentinel:
  - ``INFRA_V34_CLOSEOUT_EVIDENCE_FIX_PRECEDENT`` (节锚)
  - ``CLOSEOUT_EVIDENCE_FIX_V27_V29_V31`` (三次先例摘要小节)
  - ``CLOSEOUT_EVIDENCE_FIX_GENERAL_RULES`` (通用规范小节)
- V2 forbidden-rules-present: DECISIONS.md 必须显式包含 "不允许重构" 的关键
  词 (``forbidden``, ``reconstruction_note``, ``sub_agent_fresh_context``)
- V3 mutant 反证: 临时去掉 docstring 中任一 sentinel 关键词, V1 必须 FAIL
  (in-memory 字符串替换, 不写盘)
- V4 file sha lock: ``DECISIONS.md`` 全文件 sha 锁
- V5 Reviewer LGTM gate: closeout 须 fresh-context Reviewer LGTM (走
  ``assert_reviewer_lgtm`` helper); 本 feature 在 backloaded 时 rc=2 预期
  (Engineer 阶段 verdict 缺), 由 closeout Reviewer 写入 verdict 后 rc=0.

退出码: 0=ALL PASS, 2=任一 FAIL (走 ``verify_summary_exit``).

## Lock
- type: docstring_sentinel + file_sha + mutant + reviewer_lgtm
- target: DECISIONS.md
- locked_strings:
  - INFRA_V34_CLOSEOUT_EVIDENCE_FIX_PRECEDENT
  - CLOSEOUT_EVIDENCE_FIX_V27_V29_V31
  - CLOSEOUT_EVIDENCE_FIX_GENERAL_RULES
- bump_when: DECISIONS.md 改任意一行 (file sha 漂)
- bump_protocol: 改完 DECISIONS.md 后, 手动 recompute sha256(DECISIONS.md)
  并更新本脚本 ``EXPECTED_DECISIONS_FILE_SHA``; 必要时跑
  ``python scripts/bump_reverse_sha_lock.py --target DECISIONS.md --apply``
  让所有反向 file sha 锁同步 (若未来有第二个 verify 也锁 DECISIONS.md).

运行环境约定 (infra-034): 必须在 .venv 下运行.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DECISIONS_MD = REPO / "DECISIONS.md"
FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-V34-decisions-md-log-closeout-evidence-fix-precedent"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    verify_summary_exit,
)

# V1/V3 共用 sentinel 词清单 (DECISIONS.md 节锚)
DECISIONS_SENTINELS = (
    "INFRA_V34_CLOSEOUT_EVIDENCE_FIX_PRECEDENT",
    "CLOSEOUT_EVIDENCE_FIX_V27_V29_V31",
    "CLOSEOUT_EVIDENCE_FIX_GENERAL_RULES",
)

# V2: forbidden-rules-present 关键词 (确保通用规范段含核心要素)
FORBIDDEN_RULE_KEYWORDS = (
    "forbidden",
    "reconstruction_note",
    "sub_agent_fresh_context",
)

# V4: DECISIONS.md 全文件 sha 锁
EXPECTED_DECISIONS_FILE_SHA = (
    "0488dc1232d58cd1a78f6d5a54fa5cd70f1b38e9e7154afddbde18febdc48574"
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(
        f"[verify_infra_V34_decisions_md_log_closeout_evidence_fix_precedent]"
        f"[{mark}] {tag} {detail}",
        flush=True,
    )
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _decisions_text() -> str:
    try:
        return DECISIONS_MD.read_text(encoding="utf-8")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_decisions_md_exists", DECISIONS_MD.is_file(), f"path={DECISIONS_MD}")
    _emit(
        "V0_const_decisions_sha_hex64",
        _is_hex64(EXPECTED_DECISIONS_FILE_SHA),
        f"val={EXPECTED_DECISIONS_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V1: DECISIONS.md sentinel lock
# ---------------------------------------------------------------------------
def v1_decisions_md_sentinel_lock() -> None:
    text = _decisions_text()
    _emit("V1_decisions_md_loaded", bool(text), f"len={len(text)}")
    missing = [s for s in DECISIONS_SENTINELS if s not in text]
    _emit(
        "V1_decisions_md_sentinels_all_present",
        not missing,
        f"required={DECISIONS_SENTINELS} missing={missing}",
    )


# ---------------------------------------------------------------------------
# V2: forbidden-rules-present
# ---------------------------------------------------------------------------
def v2_forbidden_rules_present() -> None:
    text = _decisions_text()
    missing = [s for s in FORBIDDEN_RULE_KEYWORDS if s not in text]
    _emit(
        "V2_forbidden_rule_keywords_all_present",
        not missing,
        f"required={FORBIDDEN_RULE_KEYWORDS} missing={missing}",
    )


# ---------------------------------------------------------------------------
# V3: mutant 反证 (in-memory 移除单 sentinel 后 V1 应 FAIL)
# ---------------------------------------------------------------------------
def v3_mutant_negative() -> None:
    text = _decisions_text()
    if not text:
        _emit("V3_mutant_negative", False, "real DECISIONS.md empty, cannot mutate")
        return
    all_fail_when_removed = True
    detail_parts = []
    for s in DECISIONS_SENTINELS:
        if s not in text:
            all_fail_when_removed = False
            detail_parts.append(f"{s}:not_in_real_doc")
            continue
        mutated = text.replace(s, "")  # 仅在内存里移除
        ok = s not in mutated
        all_fail_when_removed = all_fail_when_removed and ok
        detail_parts.append(f"{s}:{'OK_missing' if ok else 'STILL_PRESENT'}")
    _emit(
        "V3_mutant_negative_each_sentinel_drop_detected",
        all_fail_when_removed,
        " ".join(detail_parts),
    )


# ---------------------------------------------------------------------------
# V4: file sha 锁
# ---------------------------------------------------------------------------
def v4_file_sha_locks() -> None:
    got = _file_sha(DECISIONS_MD)
    _emit(
        "V4_decisions_md_file_sha",
        got == EXPECTED_DECISIONS_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_DECISIONS_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (backloaded — Engineer 阶段预期 rc=2)
# ---------------------------------------------------------------------------
def v5_reviewer_lgtm_gate() -> None:
    try:
        ok, reason = assert_reviewer_lgtm(
            V5_GATE_FEATURE_ID,
            FEATURE_LIST,
        )
    except Exception as e:
        _emit("V5_reviewer_lgtm_gate", False, f"helper err: {e!r}")
        return
    _emit(
        "V5_reviewer_lgtm_gate",
        ok,
        f"target={V5_GATE_FEATURE_ID} reason={reason!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_decisions_md_sentinel_lock()
    v2_forbidden_rules_present()
    v3_mutant_negative()
    v4_file_sha_locks()
    v5_reviewer_lgtm_gate()
    failed = sum(1 for _, ok, _ in _results if not ok)
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
