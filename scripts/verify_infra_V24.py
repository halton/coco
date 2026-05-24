#!/usr/bin/env python3
"""verify_infra_V24: cascade RESULT sentinel 大小写敏感 + argparse 失败路径文档化.

infra-V24-cascade-result-sentinel-docs (phase-67 #17):
来源: phase-66 #5 Reviewer P1. 把两条 cascade 解析端容易踩坑的约定写进
``scripts/bump_reverse_sha_lock.py`` 的 module docstring 与 ``AGENTS.md`` 同一
段, 并机械化锁住这两个文档锚点不被悄改.

校验层级 (V0-V5):

- V0 scaffolding: self_sha + 关键文件存在
- V1 docstring sentinel lock: ``scripts/bump_reverse_sha_lock.py`` 模块 docstring
  必须含三个 sentinel 关键词 (``INFRA_V24_CASCADE_RESULT_SENTINEL_CONVENTIONS``,
  ``SENTINEL_CASE_SENSITIVE``, ``ARGPARSE_FAILURE_SKIPS_RESULT``)
- V2 AGENTS.md docs lock: AGENTS.md 必须含 V24 标题段 +
  ``SENTINEL_CASE_SENSITIVE`` + ``ARGPARSE_FAILURE_SKIPS_RESULT`` 关键词
- V3 mutant 反证: 临时去掉 docstring 中任一 sentinel 关键词, V1 必须 FAIL
  (使用 in-memory 字符串替换, 不写盘)
- V4 file sha lock: ``scripts/bump_reverse_sha_lock.py`` 全文件 sha 锁
  + AGENTS.md 全文件 sha 锁 (锚定本 V24 段也在 AGENTS.md 内)
- V5 Reviewer LGTM gate: closeout 须 fresh-context Reviewer LGTM (走
  ``assert_reviewer_lgtm`` helper)

退出码: 0=ALL PASS, 2=任一 FAIL (走 ``verify_summary_exit``).

## Lock
- type: docstring_sentinel + file_sha + mutant + reviewer_lgtm
- target: scripts/bump_reverse_sha_lock.py, AGENTS.md
- locked_strings:
  - INFRA_V24_CASCADE_RESULT_SENTINEL_CONVENTIONS
  - SENTINEL_CASE_SENSITIVE
  - ARGPARSE_FAILURE_SKIPS_RESULT
- bump_when: bump_reverse_sha_lock.py 或 AGENTS.md 改任意行 (file sha 漂)
- bump_protocol: 改完 target 后, 运行
  ``python scripts/bump_reverse_sha_lock.py --target scripts/bump_reverse_sha_lock.py --apply``
  让所有反向 file sha 锁 (含本脚本 EXPECTED_BUMP_REVERSE_FILE_SHA) 同步;
  AGENTS.md sha 需手动 recompute sha256(AGENTS.md) 并更新
  ``EXPECTED_AGENTS_FILE_SHA``.

运行环境约定 (infra-034): 必须在 .venv 下运行.
"""
from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
BUMP_REVERSE = SCRIPTS / "bump_reverse_sha_lock.py"
AGENTS_MD = REPO / "AGENTS.md"
FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-V24-cascade-result-sentinel-docs"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    verify_summary_exit,
)

# Sentinel 词清单 — V1/V3 共用
DOCSTRING_SENTINELS = (
    "INFRA_V24_CASCADE_RESULT_SENTINEL_CONVENTIONS",
    "SENTINEL_CASE_SENSITIVE",
    "ARGPARSE_FAILURE_SKIPS_RESULT",
)
AGENTS_SENTINELS = (
    "Cascade RESULT sentinel 约定",
    "SENTINEL_CASE_SENSITIVE",
    "ARGPARSE_FAILURE_SKIPS_RESULT",
)

# V4: 反向 file sha 锁
EXPECTED_BUMP_REVERSE_FILE_SHA = (
    "db81f0c5e7afd4bb75cd03ecc2f96c28916e972e0046e2e551718fe4358fca9c"
)
EXPECTED_AGENTS_FILE_SHA = (
    "31c305491b73dbeec16efb99cba306c7c261c0fd373c659fd8e63d68a8e631dd"
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_V24][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _bump_reverse_docstring() -> str:
    try:
        src = BUMP_REVERSE.read_text(encoding="utf-8")
        tree = ast.parse(src)
        return ast.get_docstring(tree) or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_bump_reverse_exists", BUMP_REVERSE.is_file(), f"path={BUMP_REVERSE}")
    _emit("V0_agents_md_exists", AGENTS_MD.is_file(), f"path={AGENTS_MD}")
    _emit(
        "V0_const_bump_reverse_sha_hex64",
        _is_hex64(EXPECTED_BUMP_REVERSE_FILE_SHA),
        f"val={EXPECTED_BUMP_REVERSE_FILE_SHA[:16]}",
    )
    _emit(
        "V0_const_agents_sha_hex64",
        _is_hex64(EXPECTED_AGENTS_FILE_SHA),
        f"val={EXPECTED_AGENTS_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V1: docstring sentinel lock
# ---------------------------------------------------------------------------
def v1_docstring_sentinel_lock() -> None:
    doc = _bump_reverse_docstring()
    _emit("V1_docstring_loaded", bool(doc), f"len={len(doc)}")
    missing = [s for s in DOCSTRING_SENTINELS if s not in doc]
    _emit(
        "V1_docstring_sentinels_all_present",
        not missing,
        f"required={DOCSTRING_SENTINELS} missing={missing}",
    )


# ---------------------------------------------------------------------------
# V2: AGENTS.md docs lock
# ---------------------------------------------------------------------------
def v2_agents_md_docs_lock() -> None:
    try:
        text = AGENTS_MD.read_text(encoding="utf-8")
    except Exception as e:
        _emit("V2_agents_md_loaded", False, f"err: {e!r}")
        return
    _emit("V2_agents_md_loaded", bool(text), f"len={len(text)}")
    missing = [s for s in AGENTS_SENTINELS if s not in text]
    _emit(
        "V2_agents_md_sentinels_all_present",
        not missing,
        f"required={AGENTS_SENTINELS} missing={missing}",
    )


# ---------------------------------------------------------------------------
# V3: mutant 反证 (in-memory 移除单 sentinel 后 V1 应 FAIL)
# ---------------------------------------------------------------------------
def v3_mutant_negative() -> None:
    doc = _bump_reverse_docstring()
    if not doc:
        _emit("V3_mutant_negative", False, "real docstring empty, cannot mutate")
        return
    all_fail_when_removed = True
    detail_parts = []
    for s in DOCSTRING_SENTINELS:
        if s not in doc:
            all_fail_when_removed = False
            detail_parts.append(f"{s}:not_in_real_doc")
            continue
        mutated = doc.replace(s, "")  # 仅在内存里移除
        # 在 mutated 里 grep 三个 sentinel — 至少一个应 missing
        still_missing = [x for x in DOCSTRING_SENTINELS if x not in mutated]
        # mutant 至少应让被移除的 s missing (排除子串重叠误判)
        ok = s in still_missing
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
    got_bump = _file_sha(BUMP_REVERSE)
    _emit(
        "V4_bump_reverse_file_sha",
        got_bump == EXPECTED_BUMP_REVERSE_FILE_SHA,
        f"got={got_bump[:16]} expect={EXPECTED_BUMP_REVERSE_FILE_SHA[:16]}",
    )
    got_agents = _file_sha(AGENTS_MD)
    _emit(
        "V4_agents_md_file_sha",
        got_agents == EXPECTED_AGENTS_FILE_SHA,
        f"got={got_agents[:16]} expect={EXPECTED_AGENTS_FILE_SHA[:16]}",
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
    v1_docstring_sentinel_lock()
    v2_agents_md_docs_lock()
    v3_mutant_negative()
    v4_file_sha_locks()
    v5_reviewer_lgtm_gate()
    failed = sum(1 for _, ok, _ in _results if not ok)
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
