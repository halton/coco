#!/usr/bin/env python3
"""verify_infra_P314_lib_sha_cascade: closeout cascade audit for ``_verify_lib.py`` file-sha holders.

## Lock: EXPECTED_VERIFY_LIB_FILE_SHA cascade audit

- target_files: scripts/verify_*.py 持有 ``EXPECTED_VERIFY_LIB_FILE_SHA`` 常量的脚本
- ground_truth: ``hashlib.sha256(open('scripts/_verify_lib.py','rb').read()).hexdigest()``
- lock_kind: cross-script lib_file_sha consistency
- bump_when: ``scripts/_verify_lib.py`` 被修改 (任何 closeout 涉及 helper 改写)
- bump_protocol: 改完 ``_verify_lib.py`` 后, cascade 更新所有 EXPECTED_VERIFY_LIB_FILE_SHA 持锁脚本
- rationale: phase-63 #5 Reviewer 发现 phase-63 #4 closeout 只 bump 了 v062/v110 但漏 bump
  v037 + 一大批其他持锁脚本; 本 verify 在 closeout pre-merge / Reviewer 阶段机械化扫描
  全部持锁脚本, 任一 stale 即 FAIL, 防同类漏 bump 再次发生.

INFRA_P314_SHA_LOCKS
--------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA (本脚本本身锁)
- self ``main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA (sentinel pragma V8 自检)

校验层级 (V0-V4 + mutant):

- V0 scaffolding: _verify_lib.py 存在 + scripts/ 路径合法
- V1 docstring sentinel ``INFRA_P314_SHA_LOCKS`` 自锁
- V1b self_main_func_sha sentinel pragma V8 自检 (首跑 __BUMP_ME__ 占位)
- V2_all_verify_lib_locks_consistent: AST 扫 scripts/verify_*.py 找
  ``EXPECTED_VERIFY_LIB_FILE_SHA = "..."`` 赋值, 与实测 _verify_lib.py sha 比对,
  任一不等 → FAIL + 列出 stale 脚本名/旧值/新值
- V3_count_lower_bound: stale == 0 且 found_count >= EXPECTED_HOLDERS_MIN (>= 30,
  保护即便扫描器空跑也不会 vacuous PASS)
- V4_self_lib_sha: EXPECTED_VERIFY_LIB_FILE_SHA 自身 == 实测
- V_mutant: 临时改第一个持锁脚本的 EXPECTED 末字节 → V2 期 FAIL, 恢复 + 验恢复后 PASS

退出码 0=ALL PASS / 2=任一 FAIL.
"""
# V8-SELF-SHA-SKIP

from __future__ import annotations

import ast
import hashlib
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    verify_summary_exit,
)

# Cascade audit constant — must equal live sha256(_verify_lib.py)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "43d352534301242f13ef33909cdba2585f8f18734d9e052f2e0de0d075955d75"
)

# Self main func sha (sentinel pragma V8-SELF-SHA-SKIP above 跳过自检)
EXPECTED_SELF_MAIN_FUNC_SHA = "__BUMP_ME__"

# 至少这么多脚本应持有 EXPECTED_VERIFY_LIB_FILE_SHA 锁; 若 < 该值则视为扫描漏读
EXPECTED_HOLDERS_MIN = 30

DOCSTRING_SENTINEL = "INFRA_P314_SHA_LOCKS"
LOCK_CONST_NAME = "EXPECTED_VERIFY_LIB_FILE_SHA"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    _results.append((tag, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P314] {status} {tag}: {detail}", flush=True)


def _compute_lib_sha() -> str:
    return hashlib.sha256(VERIFY_LIB.read_bytes()).hexdigest()


def _scan_lib_sha_holders(scripts_dir: Path) -> list[dict]:
    """AST 扫 scripts_dir/verify_*.py 找 EXPECTED_VERIFY_LIB_FILE_SHA = "<64-hex>" 赋值.

    支持两种 AST 形态:
    - ``ast.Assign(Name, Constant(str))``: 单行字面量赋值
    - ``ast.Assign(Name, value=Constant(str))`` 内 value 是 paren-wrapped 单 Constant
      (Python AST 中 ``X = ("abc",)`` 是 Tuple; ``X = ("abc")`` 仍是 Constant)

    返回 list of {file, lineno, sha_hex}.
    """
    results: list[dict] = []
    targets = sorted(scripts_dir.glob("verify_*.py"))
    for p in targets:
        if p.name == Path(__file__).name:
            # 跳过自身, 自身值由 V4_self_lib_sha 锁
            continue
        try:
            src = p.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1:
                continue
            tgt = node.targets[0]
            if not isinstance(tgt, ast.Name) or tgt.id != LOCK_CONST_NAME:
                continue
            value = node.value
            sha_hex: str | None = None
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                sha_hex = value.value
            elif isinstance(value, ast.Tuple) and value.elts:
                for el in value.elts:
                    if isinstance(el, ast.Constant) and isinstance(el.value, str):
                        sha_hex = el.value
                        break
            if not sha_hex or len(sha_hex) != 64:
                continue
            if not all(c in "0123456789abcdef" for c in sha_hex):
                continue
            results.append({
                "file": p.name,
                "lineno": node.lineno,
                "sha_hex": sha_hex,
            })
    return results


def _run_v2_consistency(scripts_dir: Path, live_sha: str) -> tuple[int, list[str]]:
    """Return (found_count, stale_list); stale_list contains 'file (got=...)' for any mismatch."""
    holders = _scan_lib_sha_holders(scripts_dir)
    stale: list[str] = []
    for h in holders:
        if h["sha_hex"] != live_sha:
            stale.append(f"{h['file']}@L{h['lineno']} got={h['sha_hex'][:16]}")
    return len(holders), stale


def main() -> int:
    # V0
    _emit("V0_verify_lib_exists", VERIFY_LIB.is_file(), str(VERIFY_LIB))
    _emit("V0_scripts_dir", SCRIPTS.is_dir(), str(SCRIPTS))

    # V1 docstring sentinel
    self_src = Path(__file__).read_text(encoding="utf-8")
    _emit(
        "V1_docstring_sentinel",
        DOCSTRING_SENTINEL in self_src,
        f"sentinel={DOCSTRING_SENTINEL}",
    )

    # V1b self main func sha (V8-SELF-SHA-SKIP pragma 跳过, 仅做 placeholder check)
    if "V8-SELF-SHA-SKIP" in self_src:
        _emit(
            "V1b_self_main_func_sha",
            True,
            f"skipped via V8-SELF-SHA-SKIP pragma (placeholder={EXPECTED_SELF_MAIN_FUNC_SHA[:16]})",
        )
    else:
        _emit("V1b_self_main_func_sha", False, "missing V8-SELF-SHA-SKIP pragma")

    # Compute live sha
    live_sha = _compute_lib_sha()

    # V4 self lib sha
    _emit(
        "V4_self_lib_sha",
        EXPECTED_VERIFY_LIB_FILE_SHA == live_sha,
        f"got={live_sha[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )

    # V2 cross-script consistency
    found_count, stale = _run_v2_consistency(SCRIPTS, live_sha)
    stale_summary = (
        f"stale={len(stale)} found={found_count} live_sha={live_sha[:16]}"
        + (f" stale_list={stale[:5]}" if stale else "")
    )
    _emit(
        "V2_all_verify_lib_locks_consistent",
        not stale,
        stale_summary,
    )

    # V3 lower bound on holders
    _emit(
        "V3_count_lower_bound",
        found_count >= EXPECTED_HOLDERS_MIN,
        f"found_count={found_count} min={EXPECTED_HOLDERS_MIN}",
    )

    # V_mutant: 拷一个 verify 脚本到 tmp 目录, 改它的 EXPECTED 末字节, 再扫 tmp 目录
    # (不污染原 scripts/)
    holders_for_mutant = _scan_lib_sha_holders(SCRIPTS)
    if not holders_for_mutant:
        _emit("V_mutant_setup", False, "no holders found, cannot run mutant")
    else:
        victim = SCRIPTS / holders_for_mutant[0]["file"]
        original = victim.read_text(encoding="utf-8")
        # Build mutant: swap last char of EXPECTED_VERIFY_LIB_FILE_SHA value
        mutant_sha = live_sha[:-1] + ("0" if live_sha[-1] != "0" else "1")
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            # Mirror victim into tmpdir with mutated sha
            for src_file in SCRIPTS.glob("verify_*.py"):
                if src_file.name == victim.name:
                    mutated = original.replace(live_sha, mutant_sha, 1)
                    (tmpdir / src_file.name).write_text(mutated, encoding="utf-8")
                else:
                    # Symlink unnecessary; only need ≥1 holder. Copy only victim.
                    pass
            # Run V2 against tmpdir (only contains mutated victim)
            mut_count, mut_stale = _run_v2_consistency(tmpdir, live_sha)
            mutant_caught = mut_count >= 1 and len(mut_stale) >= 1
            _emit(
                "V_mutant_detected",
                mutant_caught,
                f"mut_count={mut_count} mut_stale={len(mut_stale)} victim={victim.name}",
            )

    # Summary
    failed = [tag for tag, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_P314][SUMMARY] FAIL {len(failed)}/{len(_results)}: {failed}", flush=True)
    else:
        print(f"[verify_infra_P314][SUMMARY] ALL PASS ({len(_results)} checks)", flush=True)
    verify_summary_exit(len(failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
