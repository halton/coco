#!/usr/bin/env python3
"""verify_infra_033_lock_doc_rollout: ``## Lock: <CONST>`` docstring 小节推广验证 (verify-only).

infra-033-backlog-multi-verify-venv-docstring (phase-63 #5):
phase-63 #3 引入 ``_parse_lock_docstring`` / ``scan_per_file_locks_from_docstrings``
作为 per-file Lock 元信息的 canonical 解析器. 本 feature 把这一模式推广到 5 个
高价值 verify 脚本 (037 / 039 / 049 / 062 / P290), 给它们的顶部 docstring 补上
``## Lock: <CONST>`` 6-field 小节 (target_function / target_file / lock_kind /
bump_when / bump_protocol / rationale), 让机械化 audit 可以从一处统一抽取所有
sha-lock 的元信息. 本 verify 锁住这一推广的最低基线.

校验层级 (V1-V7):
- V1_self_sha (V8 pragma 模式, 自身 file sha 自锁)
- V2_min_entries: scan_per_file_locks_from_docstrings 总 entries >= EXPECTED_MIN_ENTRIES
- V3_required_files_have_lock_block: 必须含 Lock 小节的 verify 文件清单都被 scan 命中
- V4_field_completeness: 所有 entries 都 6/6 字段完备
- V5_target_files_resolvable: Lock 小节 target_file 字段指的文件应存在
- V7_doc_value_consistency (infra-V11-doc-value-lock-rollout, phase-64 #2):
  把 verify_infra_110 的 V11 (6-field 值精确匹配) 模式机械化推广到全量 18+ entries.
  对每个 (file, const) Lock entry 校验:
    (a) const_name 命名一致: lock_kind=file_sha → const_name endswith _FILE_SHA;
        lock_kind=ast_func_sha → const_name endswith _FUNC_SHA
    (b) lock_kind=file_sha → target_function 必须为 "N/A"
    (c) lock_kind=ast_func_sha → target_function 必须能在 target_file 的顶层
        function 集合中找到 (调 func_sha_by_name 不抛异常即可, 返回 hex sha 表示
        AST 顶层定义存在)
  任一字段值偏离即 FAIL, 锁住 docstring lock 元信息的语义正确性, 不仅锁存在性.

V6 mutant: 临时把 scripts/verify_infra_039.py 顶部 docstring 的 Lock 小节抹掉 →
   subprocess 跑 V3 应判定 039 缺失 → finally 还原.

V8 mutant (V11 rollout): 在内存中构造一个 fake entry, 把 target_function 改成不存在
   的函数名, 走 V7 期 FAIL; 反证 V7 不是永真.

## Lock: EXPECTED_SELF_FILE_SHA
- target_function: N/A
- target_file: scripts/verify_infra_033_lock_doc_rollout.py
- lock_kind: file_sha
- bump_when: 本脚本任何字节改动 (V8 pragma 行除外)
- bump_protocol: 重算 self file sha (剔除 V8-SELF-SHA-SKIP 那一行) 并更新 EXPECTED_SELF_FILE_SHA
- rationale: 自锁本 verify 整体, 防止 V1-V7 被悄改成永真

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
    func_sha_by_name,
    scan_per_file_locks_from_docstrings,
    verify_summary_exit,
)

# V1: 自身 file sha 自锁 (V8 pragma 模式, 计算时剔除带 pragma 的那一行)
EXPECTED_SELF_FILE_SHA = "792eace9e1e448c248a1cdf5ed77779e035e803cf5a4238f30bf248d240e6f94"  # V8-SELF-SHA-SKIP

# V2: scan 输出 entries 数下界 (phase-63 #5 推广后 17 个, 留少量余量;
#     phase-64 #2 V11-doc-value-lock-rollout 把基线上拔至实测 18.
#     未来若再推广更多文件可上调; 若有意删则需主动 bump 此常量)
EXPECTED_MIN_ENTRIES = 18

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


def v7_doc_value_consistency(scan: dict) -> None:
    """V7 (infra-V11-doc-value-lock-rollout, phase-64 #2): 把 V11 模式
    (110.py: classify_node lock 6 字段值精确锁) 机械化推广到 scan 出的全量
    entries.

    对每个 (file, const) Lock entry 做三类一致性校验:

    (a) 命名一致 ``const ⇄ lock_kind``:
        - ``lock_kind == "file_sha"`` → ``const`` 须 endswith ``_FILE_SHA``
        - ``lock_kind == "ast_func_sha"`` → ``const`` 须 endswith ``_FUNC_SHA``
    (b) ``lock_kind == "file_sha"`` → ``target_function`` 必须为 ``"N/A"``
        (file 级锁不应挂目标函数, 防止读者误解为 func 锁)
    (c) ``lock_kind == "ast_func_sha"`` → ``target_function`` 必须是
        ``target_file`` 的顶层 def/async-def 之一 (调 ``func_sha_by_name``
        不抛 ValueError, 返回 hex sha 即视为存在)。任意一类违反即 FAIL。

    与 V4_field_completeness 互补 — V4 只看 6 字段都"在", 不看值的语义;
    V7 锁字段值的语义正确性, 防止保留字段名但值被悄改成无关内容
    (110.py V11 已对自身做这件事, 这里把它机械化扩到全量)。
    """
    violations: List[str] = []
    checked = 0
    for (fname, const), meta in scan.items():
        kind = meta.get("lock_kind", "")
        tgt_fn = meta.get("target_function", "")
        tgt_file = meta.get("target_file", "")
        if kind == "file_sha":
            checked += 1
            # (a) 命名后缀
            if not const.endswith("_FILE_SHA"):
                violations.append(
                    f"{fname}::{const} kind=file_sha 但 const 不以 _FILE_SHA 结尾"
                )
            # (b) target_function 必须 N/A
            if tgt_fn != "N/A":
                violations.append(
                    f"{fname}::{const} kind=file_sha 但 target_function={tgt_fn!r} (期望 'N/A')"
                )
        elif kind == "ast_func_sha":
            checked += 1
            # (a) 命名后缀
            if not const.endswith("_FUNC_SHA"):
                violations.append(
                    f"{fname}::{const} kind=ast_func_sha 但 const 不以 _FUNC_SHA 结尾"
                )
            # (c) 顶层 func 可解析
            tgt_path = REPO / tgt_file if tgt_file and tgt_file != "N/A" else None
            if tgt_path is None or not tgt_path.exists():
                violations.append(
                    f"{fname}::{const} kind=ast_func_sha 但 target_file={tgt_file!r} 不可定位"
                )
            else:
                try:
                    sha = func_sha_by_name(tgt_path, tgt_fn)
                    if not (isinstance(sha, str) and len(sha) == 64):
                        violations.append(
                            f"{fname}::{const} func_sha_by_name 返回异常 sha={sha!r}"
                        )
                except Exception as exc:
                    violations.append(
                        f"{fname}::{const} func_sha_by_name({tgt_fn!r}, {tgt_file!r}) raised: {type(exc).__name__}: {exc}"
                    )
        else:
            # 未知 lock_kind 不强制, 但记到 checked 之外, 让 V4 (field_completeness)
            # 仍能覆盖. 此分支不算 violation, 因为未来可能加新 kind。
            continue
    ok = len(violations) == 0
    _emit(
        "V7_doc_value_consistency",
        ok,
        f"checked={checked} violations={len(violations)} samples={violations[:3]}",
    )


def v8_mutant_break_func_name_makes_v7_fail() -> None:
    """V8 mutant: 在内存中构造一个 entry 把 target_function 改成不存在的函数名,
    走 V7 check 期 FAIL; 反证 V7 不是永真."""
    import contextlib
    import io

    fake_scan = {
        ("verify_infra_037.py", "EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA"): {
            "target_function": "this_function_does_not_exist_xyz_v11_rollout",
            "target_file": "scripts/_verify_lib.py",
            "lock_kind": "ast_func_sha",
            "bump_when": "x",
            "bump_protocol": "x",
            "rationale": "x",
        }
    }
    # 直接复用 V7 逻辑 (但不污染主 _results, 也不污染主 stdout); 临时 swap
    saved = list(_results)
    sub: list = []
    try:
        _results.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            v7_doc_value_consistency(fake_scan)
        sub = list(_results)
    finally:
        _results.clear()
        _results.extend(saved)
    if not sub:
        _emit("V8_mutant_break_func_name", False, "V7 sub-run produced no result")
        return
    _, sub_ok, sub_detail = sub[0]
    if sub_ok:
        _emit(
            "V8_mutant_break_func_name",
            False,
            f"mutant 应让 V7 FAIL 但仍 PASS: {sub_detail}",
        )
    else:
        _emit(
            "V8_mutant_break_func_name",
            True,
            f"mutant 成功让 V7 FAIL: violations>=1",
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
    v7_doc_value_consistency(scan)
    v8_mutant_break_func_name_makes_v7_fail()
    fails = sum(1 for _, ok, _ in _results if not ok)
    verify_summary_exit(fails)
    return 0


if __name__ == "__main__":
    sys.exit(main())
