#!/usr/bin/env python3
"""verify_infra_033_lock_doc_rollout: ``## Lock: <CONST>`` docstring 小节推广验证 (verify-only).

infra-033-backlog-multi-verify-venv-docstring (phase-63 #5):
phase-63 #3 引入 ``_parse_lock_docstring`` / ``scan_per_file_locks_from_docstrings``
作为 per-file Lock 元信息的 canonical 解析器. 本 feature 把这一模式推广到 5 个
高价值 verify 脚本 (037 / 039 / 049 / 062 / P290), 给它们的顶部 docstring 补上
``## Lock: <CONST>`` 6-field 小节 (target_function / target_file / lock_kind /
bump_when / bump_protocol / rationale), 让机械化 audit 可以从一处统一抽取所有
sha-lock 的元信息. 本 verify 锁住这一推广的最低基线.

校验层级 (V1-V5):
- V1_self_sha (V8 pragma 模式, 自身 file sha 自锁)
- V2_min_entries: scan_per_file_locks_from_docstrings 总 entries >= EXPECTED_MIN_ENTRIES
- V3_required_files_have_lock_block: 必须含 Lock 小节的 verify 文件清单都被 scan 命中
- V4_field_completeness: 所有 entries 都 6/6 字段完备
- V5_target_files_resolvable: Lock 小节 target_file 字段指的文件应存在

V6 mutant: 临时把 scripts/verify_infra_039.py 顶部 docstring 的 Lock 小节抹掉 →
   subprocess 跑 V3 应判定 039 缺失 → finally 还原.

## Lock: EXPECTED_SELF_FILE_SHA
- target_function: N/A
- target_file: scripts/verify_infra_033_lock_doc_rollout.py
- lock_kind: file_sha
- bump_when: 本脚本任何字节改动 (V8 pragma 行除外)
- bump_protocol: 重算 self file sha (剔除 V8-SELF-SHA-SKIP 那一行) 并更新 EXPECTED_SELF_FILE_SHA
- rationale: 自锁本 verify 整体, 防止 V1-V5 被悄改成永真

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
SELF = Path(__file__).resolve()

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    scan_per_file_locks_from_docstrings,
    verify_summary_exit,
)

# V1: 自身 file sha 自锁 (V8 pragma 模式, 计算时剔除带 pragma 的那一行)
EXPECTED_SELF_FILE_SHA = "76a2a0b733f6f25918a5bb67bdd4ad688a1ac104ad865d4e54abd533a4285f40"  # V8-SELF-SHA-SKIP

# V2: scan 输出 entries 数下界 (phase-63 #5 推广后 17 个, 留少量余量;
# 未来若再推广更多文件可上调; 若有意删则需主动 bump 此常量)
EXPECTED_MIN_ENTRIES = 14

# V3: 这些 verify 文件必须含 ## Lock: 小节 (phase-63 #5 推广目标 + 已有的)
REQUIRED_LOCK_FILES = [
    "verify_infra_037.py",
    "verify_infra_039.py",
    "verify_infra_049.py",
    "verify_infra_062.py",
    "verify_infra_110.py",
    "verify_infra_P290.py",
]

# V4: 每个 entry 必须 6/6 字段完备
EXPECTED_FIELDS = {
    "target_function",
    "target_file",
    "lock_kind",
    "bump_when",
    "bump_protocol",
    "rationale",
}

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    _results.append((tag, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[verify_infra_033] {status} {tag}: {detail}", flush=True)


def _self_file_sha_excluding_pragma_line() -> str:
    """计算自身 file sha, 剔除唯一一行行尾 pragma ``# V8-SELF-SHA-SKIP``."""
    lines = SELF.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not ln.rstrip("\n").rstrip().endswith("# V8-SELF-SHA-SKIP")]
    return hashlib.sha256("".join(kept).encode("utf-8")).hexdigest()


def v1_self_sha() -> None:
    got = _self_file_sha_excluding_pragma_line()
    if EXPECTED_SELF_FILE_SHA == "__BUMP_ME__":
        _emit("V1_self_sha", False, f"placeholder; bump EXPECTED_SELF_FILE_SHA={got}")
    else:
        ok = got == EXPECTED_SELF_FILE_SHA
        _emit(
            "V1_self_sha",
            ok,
            f"got={got[:16]} expect={EXPECTED_SELF_FILE_SHA[:16]}",
        )


def v2_min_entries(scan: dict) -> None:
    n = len(scan)
    ok = n >= EXPECTED_MIN_ENTRIES
    _emit(
        "V2_min_entries",
        ok,
        f"entries={n} >= EXPECTED_MIN_ENTRIES={EXPECTED_MIN_ENTRIES}",
    )


def v3_required_files_have_lock_block(scan: dict) -> None:
    seen_files = {k[0] for k in scan.keys()}
    missing = [f for f in REQUIRED_LOCK_FILES if f not in seen_files]
    ok = len(missing) == 0
    _emit(
        "V3_required_files_have_lock_block",
        ok,
        f"required={len(REQUIRED_LOCK_FILES)} seen_subset={len(REQUIRED_LOCK_FILES) - len(missing)} missing={missing}",
    )


def v4_field_completeness(scan: dict) -> None:
    incomplete: List[str] = []
    for (fname, const), meta in scan.items():
        have = set(meta.keys())
        if have != EXPECTED_FIELDS:
            missing = EXPECTED_FIELDS - have
            extra = have - EXPECTED_FIELDS
            incomplete.append(f"{fname}::{const} missing={sorted(missing)} extra={sorted(extra)}")
    ok = len(incomplete) == 0
    _emit(
        "V4_field_completeness",
        ok,
        f"checked={len(scan)} incomplete={len(incomplete)} samples={incomplete[:3]}",
    )


def v5_target_files_resolvable(scan: dict) -> None:
    unresolved: List[str] = []
    checked = 0
    for (fname, const), meta in scan.items():
        tgt = meta.get("target_file", "")
        if not tgt or tgt == "N/A":
            continue
        checked += 1
        p = REPO / tgt
        if not p.exists():
            unresolved.append(f"{fname}::{const} target_file={tgt!r} not found")
    ok = len(unresolved) == 0
    _emit(
        "V5_target_files_resolvable",
        ok,
        f"checked={checked} unresolved={len(unresolved)} samples={unresolved[:3]}",
    )


def v6_mutant_drop_lock_block_makes_v3_fail() -> None:
    """临时把 verify_infra_039.py 中 ``## Lock:`` 小节抹掉, 期 V3 判定 039 缺失."""
    target = SCRIPTS / "verify_infra_039.py"
    if not target.exists():
        _emit("V6_mutant_drop_lock_block", False, f"target missing: {target}")
        return
    orig = target.read_text(encoding="utf-8")
    # 简化 mutant: 把所有 "## Lock:" 行替换为非匹配 header
    mutated = orig.replace("## Lock: ", "## NotLock: ")
    if mutated == orig:
        _emit("V6_mutant_drop_lock_block", False, "no ## Lock: header in 039 to mutate")
        return
    try:
        target.write_text(mutated, encoding="utf-8")
        scan_after = scan_per_file_locks_from_docstrings(str(SCRIPTS))
        seen = {k[0] for k in scan_after.keys()}
        # 期 verify_infra_039.py 不再在 seen
        if "verify_infra_039.py" in seen:
            _emit(
                "V6_mutant_drop_lock_block",
                False,
                "mutated 039 still has Lock entries (parser too lenient?)",
            )
        else:
            _emit(
                "V6_mutant_drop_lock_block",
                True,
                "after mutating 039 header, scan no longer reports it (V3 would fail)",
            )
    finally:
        target.write_text(orig, encoding="utf-8")


def main() -> int:
    v1_self_sha()
    scan = scan_per_file_locks_from_docstrings(str(SCRIPTS))
    v2_min_entries(scan)
    v3_required_files_have_lock_block(scan)
    v4_field_completeness(scan)
    v5_target_files_resolvable(scan)
    v6_mutant_drop_lock_block_makes_v3_fail()
    fails = sum(1 for _, ok, _ in _results if not ok)
    verify_summary_exit(fails)
    return 0


if __name__ == "__main__":
    sys.exit(main())
