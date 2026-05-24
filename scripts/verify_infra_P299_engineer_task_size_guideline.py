#!/usr/bin/env python3
"""verify_infra_P299_engineer_task_size_guideline: AGENTS.md Engineer 任务尺寸指导段锁.

infra-P299-engineer-task-size-guideline (phase-67 backlog 入账):
来源: P294-Ry phase Engineer sub-agent 因任务 "新建 verify + cascade bump 16+ +
commit + push" 单轮过大, socket 在中段断了 2 次, 拆 A+B 才完成。教训沉淀到
AGENTS.md "Engineer 任务尺寸指导" 一段, 并机械化锁住该段的三个 sentinel 词,
避免后续无意删除或弱化。

校验层级 (V0-V5):

- V0 scaffolding: self_sha + AGENTS.md 存在 + 常量类型对
- V1 AGENTS.md sentinel lock: AGENTS.md 必须含三个 sentinel
  (``P299_ENGINEER_TASK_SIZE_SPLIT_AB``, ``Engineer-build``,
  ``Engineer-cascade-and-ship``)
- V2 section title lock: AGENTS.md 须含 ``Engineer 任务尺寸指导`` 段标题
- V3 mutant 反证: 临时 in-memory 去掉 V1 任一 sentinel, V1 应 FAIL
- V4 file sha lock: AGENTS.md 全文件 sha 锁
- V5 Reviewer LGTM gate: closeout 须 fresh-context Reviewer LGTM (走
  ``assert_reviewer_lgtm`` helper)

退出码: 0=ALL PASS, 2=任一 FAIL (走 ``verify_summary_exit``).

## Lock
- type: agents_md_sentinel + file_sha + mutant + reviewer_lgtm
- target: AGENTS.md
- locked_strings:
  - P299_ENGINEER_TASK_SIZE_SPLIT_AB
  - Engineer-build
  - Engineer-cascade-and-ship
- bump_when: AGENTS.md 改任意行 (file sha 漂)
- bump_protocol: 改完 AGENTS.md 后, 手动 recompute sha256(AGENTS.md) 并更新
  本脚本 EXPECTED_AGENTS_FILE_SHA + 同步 dump_v4_sha_graph.py _PER_FILE_LOCKS
  其他 holders (verify_infra_V24 / verify_infra_100 / verify_infra_P288 /
  verify_infra_100_backlog_agents_md_sha_cascade_fix).

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
AGENTS_MD = REPO / "AGENTS.md"
FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P299-engineer-task-size-guideline"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    verify_summary_exit,
)

# Sentinel 词清单 — V1/V3 共用
AGENTS_SENTINELS = (
    "P299_ENGINEER_TASK_SIZE_SPLIT_AB",
    "Engineer-build",
    "Engineer-cascade-and-ship",
)

SECTION_TITLE = "Engineer 任务尺寸指导"

# V4: AGENTS.md file sha 锁
EXPECTED_AGENTS_FILE_SHA = (
    "e8251b9a98f9fb7849e232b7cec632a9e1689bf53bf7f2543787d7eacc45d36f"
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P299_engineer_task_size_guideline][{mark}] {tag} {detail}",
          flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_agents_md_exists", AGENTS_MD.is_file(), f"path={AGENTS_MD}")
    _emit(
        "V0_const_agents_sha_hex64",
        _is_hex64(EXPECTED_AGENTS_FILE_SHA),
        f"val={EXPECTED_AGENTS_FILE_SHA[:16]}",
    )
    _emit(
        "V0_sentinels_nonempty",
        len(AGENTS_SENTINELS) >= 3 and all(isinstance(s, str) and s for s in AGENTS_SENTINELS),
        f"count={len(AGENTS_SENTINELS)}",
    )


# ---------------------------------------------------------------------------
# V1: AGENTS.md sentinel lock
# ---------------------------------------------------------------------------
def v1_agents_md_sentinel_lock() -> None:
    try:
        text = AGENTS_MD.read_text(encoding="utf-8")
    except Exception as e:
        _emit("V1_agents_md_loaded", False, f"err: {e!r}")
        return
    _emit("V1_agents_md_loaded", bool(text), f"len={len(text)}")
    missing = [s for s in AGENTS_SENTINELS if s not in text]
    _emit(
        "V1_agents_md_sentinels_all_present",
        not missing,
        f"required={AGENTS_SENTINELS} missing={missing}",
    )


# ---------------------------------------------------------------------------
# V2: section title lock
# ---------------------------------------------------------------------------
def v2_section_title_lock() -> None:
    try:
        text = AGENTS_MD.read_text(encoding="utf-8")
    except Exception as e:
        _emit("V2_section_title_lock", False, f"err: {e!r}")
        return
    present = SECTION_TITLE in text
    _emit(
        "V2_section_title_present",
        present,
        f"title={SECTION_TITLE!r}",
    )


# ---------------------------------------------------------------------------
# V3: mutant 反证 (in-memory 移除单 sentinel 后 V1 应 FAIL)
# ---------------------------------------------------------------------------
def v3_mutant_negative() -> None:
    try:
        text = AGENTS_MD.read_text(encoding="utf-8")
    except Exception as e:
        _emit("V3_mutant_negative", False, f"err: {e!r}")
        return
    if not text:
        _emit("V3_mutant_negative", False, "real AGENTS.md empty, cannot mutate")
        return
    all_fail_when_removed = True
    detail_parts = []
    for s in AGENTS_SENTINELS:
        if s not in text:
            all_fail_when_removed = False
            detail_parts.append(f"{s}:not_in_real_text")
            continue
        mutated = text.replace(s, "")
        still_missing = [x for x in AGENTS_SENTINELS if x not in mutated]
        ok = s in still_missing
        all_fail_when_removed = all_fail_when_removed and ok
        detail_parts.append(f"{s}:{'OK_missing' if ok else 'STILL_PRESENT'}")
    _emit(
        "V3_mutant_negative_each_sentinel_drop_detected",
        all_fail_when_removed,
        " ".join(detail_parts),
    )


# ---------------------------------------------------------------------------
# V4: AGENTS.md file sha 锁
# ---------------------------------------------------------------------------
def v4_file_sha_lock() -> None:
    got = _file_sha(AGENTS_MD)
    _emit(
        "V4_agents_md_file_sha",
        got == EXPECTED_AGENTS_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_AGENTS_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
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
    v1_agents_md_sentinel_lock()
    v2_section_title_lock()
    v3_mutant_negative()
    v4_file_sha_lock()
    v5_reviewer_lgtm_gate()
    failed = sum(1 for _, ok, _ in _results if not ok)
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
