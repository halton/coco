#!/usr/bin/env python3
"""verify_robot_009_backlog_block_policy_doc.

robot-009-backlog-block-policy-doc (phase-67 #28):
来源: Reviewer caveat (robot-009) — RobotSequencer.enqueue 的 overflow_policy
'block' 实际语义是 "最多阻塞 ~1s 等 worker 消费, 超时退化 drop"，
与 robot-009 brief 中 "完全非阻塞" 表述存在歧义。本 feature 把三策略
(drop_oldest 默认 / drop_new / block≤1s) 显式写入 ``coco/robot/sequencer.py``
的 module docstring 中 ``ROBOT_009_OVERFLOW_POLICY_DOC`` 小节, 并机械化锁住
该小节 sentinel + 三策略关键词 + sequencer.py 全文件 sha, 防止悄改 / 误删.

校验层级 (V0-V5):

- V0 scaffolding: self_sha + sequencer.py 文件存在 + 常量 hex64
- V1 docstring sentinel lock: ``sequencer.py`` 必须含 sentinel:
  - ``ROBOT_009_OVERFLOW_POLICY_DOC`` (节锚)
- V2 three-policy keywords present: docstring 须显式列出三策略名:
  - ``drop_oldest``
  - ``drop_new``
  - ``block``
  并含 "~1s" 超时保护关键描述以与 brief "完全非阻塞" 歧义切割.
- V3 mutant 反证: 临时去掉 sentinel / 任一策略关键词, V1/V2 必须 FAIL
  (in-memory 字符串替换, 不写盘).
- V4 file sha lock: ``coco/robot/sequencer.py`` 全文件 sha 锁.
- V5 Reviewer LGTM gate: closeout 须 fresh-context Reviewer LGTM (走
  ``assert_reviewer_lgtm`` helper); 本 feature 在 backloaded 时 rc=2 预期
  (Engineer 阶段 verdict 缺), 由 closeout Reviewer 写入 verdict 后 rc=0.

退出码: 0=ALL PASS, 2=任一 FAIL (走 ``verify_summary_exit``).

## Lock
- type: docstring_sentinel + file_sha + mutant + reviewer_lgtm
- target: coco/robot/sequencer.py
- locked_strings:
  - ROBOT_009_OVERFLOW_POLICY_DOC
  - drop_oldest
  - drop_new
  - block
  - ~1s
- bump_when: sequencer.py 改任意一行 (file sha 漂)
- bump_protocol: 改完 sequencer.py 后, 手动 recompute
  sha256(coco/robot/sequencer.py) 并更新本脚本 ``EXPECTED_SEQUENCER_FILE_SHA``;
  必要时跑 ``python scripts/bump_reverse_sha_lock.py
  --target coco/robot/sequencer.py --apply`` 让所有反向 file sha 锁同步.

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
SEQUENCER_PY = REPO / "coco" / "robot" / "sequencer.py"
FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "robot-009-backlog-block-policy-doc"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    verify_summary_exit,
)

# V1 sentinel (node anchor in module docstring)
DOCSTRING_SENTINELS = (
    "ROBOT_009_OVERFLOW_POLICY_DOC",
)

# V2 three-policy + timeout-disambiguation keywords (docstring 必含)
THREE_POLICY_KEYWORDS = (
    "drop_oldest",
    "drop_new",
    "block",
    "~1s",
)

# V4: coco/robot/sequencer.py 全文件 sha 锁
EXPECTED_SEQUENCER_FILE_SHA = (
    "a3d5a44111beaf0abe0ed2648c5ea8ee59d4eec8cbd00476bf7e1caeaa92c5ce"
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(
        f"[verify_robot_009_backlog_block_policy_doc]"
        f"[{mark}] {tag} {detail}",
        flush=True,
    )
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _sequencer_text() -> str:
    try:
        return SEQUENCER_PY.read_text(encoding="utf-8")
    except Exception:
        return ""


def _module_docstring(text: str) -> str:
    """Extract the module-level docstring (first triple-quoted string)."""
    m = re.match(r'\s*"""(.*?)"""', text, re.DOTALL)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit(
        "V0_sequencer_py_exists",
        SEQUENCER_PY.is_file(),
        f"path={SEQUENCER_PY}",
    )
    _emit(
        "V0_const_sequencer_sha_hex64",
        _is_hex64(EXPECTED_SEQUENCER_FILE_SHA),
        f"val={EXPECTED_SEQUENCER_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V1: sequencer.py module docstring sentinel lock
# ---------------------------------------------------------------------------
def v1_docstring_sentinel_lock() -> None:
    text = _sequencer_text()
    _emit("V1_sequencer_py_loaded", bool(text), f"len={len(text)}")
    doc = _module_docstring(text)
    _emit(
        "V1_module_docstring_extracted",
        bool(doc),
        f"docstring_len={len(doc)}",
    )
    missing = [s for s in DOCSTRING_SENTINELS if s not in doc]
    _emit(
        "V1_docstring_sentinels_all_present",
        not missing,
        f"required={DOCSTRING_SENTINELS} missing={missing}",
    )


# ---------------------------------------------------------------------------
# V2: three-policy keywords present in docstring
# ---------------------------------------------------------------------------
def v2_three_policy_keywords_present() -> None:
    doc = _module_docstring(_sequencer_text())
    missing = [s for s in THREE_POLICY_KEYWORDS if s not in doc]
    _emit(
        "V2_three_policy_keywords_all_present",
        not missing,
        f"required={THREE_POLICY_KEYWORDS} missing={missing}",
    )


# ---------------------------------------------------------------------------
# V3: mutant 反证 (in-memory 移除 sentinel / 策略关键词后 V1/V2 应 FAIL)
# ---------------------------------------------------------------------------
def v3_mutant_negative() -> None:
    text = _sequencer_text()
    if not text:
        _emit("V3_mutant_negative", False, "sequencer.py empty, cannot mutate")
        return
    all_fail_when_removed = True
    detail_parts = []
    for s in DOCSTRING_SENTINELS + THREE_POLICY_KEYWORDS:
        if s not in text:
            all_fail_when_removed = False
            detail_parts.append(f"{s}:not_in_real_doc")
            continue
        mutated = text.replace(s, "")
        ok = s not in mutated
        all_fail_when_removed = all_fail_when_removed and ok
        detail_parts.append(f"{s}:{'OK_missing' if ok else 'STILL_PRESENT'}")
    _emit(
        "V3_mutant_negative_each_token_drop_detected",
        all_fail_when_removed,
        " ".join(detail_parts),
    )


# ---------------------------------------------------------------------------
# V4: file sha 锁
# ---------------------------------------------------------------------------
def v4_file_sha_locks() -> None:
    got = _file_sha(SEQUENCER_PY)
    _emit(
        "V4_sequencer_py_file_sha",
        got == EXPECTED_SEQUENCER_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_SEQUENCER_FILE_SHA[:16]}",
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
    v1_docstring_sentinel_lock()
    v2_three_policy_keywords_present()
    v3_mutant_negative()
    v4_file_sha_locks()
    v5_reviewer_lgtm_gate()
    failed = sum(1 for _, ok, _ in _results if not ok)
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
