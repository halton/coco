#!/usr/bin/env python3
"""infra-034-backlog-typo-guard-expected-target-shas-fix verify (phase-67 #15).

scope
-----
修复 typo-guard 在 main HEAD=64add10 baseline 的单点 FAIL —— P281
``verify_expected_prefix_typo_guard`` 把
``scripts/verify_infra_034_backlog_expand_docstring_coverage.py:81``
的 ``EXPECTED_TARGET_SHAS`` (合法 sha tuple plural) 误判为 typo
(``_RE_EXPECTED_TYPO_SUFFIX`` 末尾 ``_SHAS`` 命中)。

设计决策: **不动 ``_verify_lib.py``** (P261/硬规则); 改 verify 脚本侧
变量名 ``EXPECTED_TARGET_SHAS`` → ``EXPECTED_TARGET_SHA_LIST``。
``_LIST`` 是非 sha 合法后缀 (与 ``EXPECTED_LINES`` / ``EXPECTED_PALETTE`` /
``EXPECTED_FINGERPRINT`` 同类 well-formed legitimate non-sha 命名)。
``EXPECTED_TARGET_SHA_LIST`` 命名清晰表达"sha 元组的列表", 不与 sha 单数
锁后缀 ``_SHA`` 冲突 (后者要求字段值是一个 sha 字符串)。

副作用: rename 触发自体 ``EXPECTED_SELF_FILE_SHA`` (line 99 ``# V0-SELF-SHA-SKIP``
pragma 跳过自身那行) 需重算 → ``eb8b4fb2038efe57210f395995b2a478c43045a233789befd6f03da1590d6daa``。

运行环境约定 (infra-034)
------------------------
本脚本必须用 ``.venv/bin/python`` 跑 (而非 system ``python3``)。所有 helper
import 走 ``sys.executable`` 子进程, 避免跨解释器路径污染。
典型: ``./.venv/bin/python scripts/verify_infra_034_backlog_typo_guard_expected_target_shas_fix.py``。

verification checks
-------------------
- V0 self_sha: 本脚本自身 sha256 (pragma V0-SELF-SHA-SKIP 行跳过) 必须等于
  ``EXPECTED_SELF_FILE_SHA``。
- V1 target file sha: ``scripts/verify_infra_034_backlog_expand_docstring_coverage.py``
  raw sha256 必须等于 ``EXPECTED_TARGET_FILE_SHA`` (patched 后的字节级锁)。
- V2 typo-guard clean: ``verify_expected_prefix_typo_guard()`` 跑过后
  ``typo_count == 0`` 且 ``all_well_formed == True``。
- V3 old name absent: 修改后的目标文件 AST 顶层不得再含 ``EXPECTED_TARGET_SHAS``
  这个名字 (防 rebase 时把老名字带回来)。
- V4 new name present: 同一目标文件 AST 顶层必须存在 ``EXPECTED_TARGET_SHA_LIST``
  且其值是 tuple of tuple of (str, str).
- V5 target file still runs PASS: 子进程跑 verify_infra_034_backlog_expand_docstring_coverage.py
  必须 rc=0 且最后一行 ``failed=0``。
"""

from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
TARGET_REL = "scripts/verify_infra_034_backlog_expand_docstring_coverage.py"
TARGET = ROOT / TARGET_REL

EXPECTED_SELF_FILE_SHA = "4e45b0f93b62af9a31cd774db619ba39d1387471fa1d2a8984c6ac2c43e9cdba"  # V0-SELF-SHA-SKIP
EXPECTED_TARGET_FILE_SHA = (
    "bbaed314262e9271131d2f465f20269c2ae5d894a23d5949a4c97abdefccddb9"
)

OLD_NAME = "EXPECTED_TARGET_SHAS"
NEW_NAME = "EXPECTED_TARGET_SHA_LIST"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_034_backlog_typo_guard_fix][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _self_sha_skip_pragma() -> str:
    src = Path(__file__).resolve().read_text(encoding="utf-8")
    pragma = "# V0-SELF-SHA-SKIP"
    kept = [
        line
        for line in src.splitlines(keepends=True)
        if not line.rstrip("\n").rstrip().endswith(pragma)
    ]
    return hashlib.sha256("".join(kept).encode("utf-8")).hexdigest()


def v0_self_sha() -> None:
    actual = _self_sha_skip_pragma()
    ok = actual == EXPECTED_SELF_FILE_SHA
    _emit(
        "V0_self_file_sha_lock",
        ok,
        f"actual={actual[:16]} expect={EXPECTED_SELF_FILE_SHA[:16]}",
    )


def v1_target_file_sha() -> None:
    if not TARGET.is_file():
        _emit("V1_target_file_sha", False, f"missing: {TARGET_REL}")
        return
    got = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    ok = got == EXPECTED_TARGET_FILE_SHA
    _emit(
        "V1_target_file_sha",
        ok,
        f"got={got[:16]} expect={EXPECTED_TARGET_FILE_SHA[:16]}",
    )


def v2_typo_guard_clean() -> None:
    sys.path.insert(0, str(SCRIPTS))
    try:
        from _verify_lib import verify_expected_prefix_typo_guard
    except Exception as exc:
        _emit("V2_typo_guard_clean", False, f"import failure: {exc!r}")
        return
    r = verify_expected_prefix_typo_guard()
    ok = (
        r.get("typo_count") == 0
        and r.get("all_well_formed") is True
    )
    detail = (
        f"total={r.get('total_expected_consts')} "
        f"well_formed={r.get('well_formed')} "
        f"typo_count={r.get('typo_count')}"
    )
    if not ok:
        detail += f" samples={r.get('typo_samples')}"
    _emit("V2_typo_guard_clean", ok, detail)


def _collect_top_level_assign_names(p: Path) -> List[str]:
    tree = ast.parse(p.read_text(encoding="utf-8"))
    names: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.append(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


def v3_old_name_absent() -> None:
    if not TARGET.is_file():
        _emit("V3_old_name_absent", False, "target missing")
        return
    names = _collect_top_level_assign_names(TARGET)
    ok = OLD_NAME not in names
    _emit(
        "V3_old_name_absent",
        ok,
        f"OLD={OLD_NAME!r} present={OLD_NAME in names}",
    )


def v4_new_name_present() -> None:
    if not TARGET.is_file():
        _emit("V4_new_name_present", False, "target missing")
        return
    src = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    is_tuple_of_tuples = False
    for node in tree.body:
        target_name = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
            value = node.value
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == NEW_NAME:
                    target_name = t.id
                    value = node.value
                    break
        if target_name == NEW_NAME:
            found = True
            if isinstance(value, ast.Tuple) and value.elts:
                first = value.elts[0]
                if isinstance(first, ast.Tuple) and len(first.elts) == 2:
                    is_tuple_of_tuples = True
            break
    ok = found and is_tuple_of_tuples
    _emit(
        "V4_new_name_present",
        ok,
        f"NEW={NEW_NAME!r} found={found} tuple_of_tuples={is_tuple_of_tuples}",
    )


def v5_target_still_passes() -> None:
    proc = subprocess.run(
        [sys.executable, str(TARGET)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    rc = proc.returncode
    tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    last = tail[0]
    ok = rc == 0 and "failed=0" in last
    _emit(
        "V5_target_still_passes",
        ok,
        f"rc={rc} last={last!r}",
    )


def main() -> int:
    v0_self_sha()
    v1_target_file_sha()
    v2_typo_guard_clean()
    v3_old_name_absent()
    v4_new_name_present()
    v5_target_still_passes()

    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    print(
        f"[verify_infra_034_backlog_typo_guard_fix] summary "
        f"total={total} failed={len(failed)}",
        flush=True,
    )
    if failed:
        print(
            f"[verify_infra_034_backlog_typo_guard_fix] failed tags: {failed}",
            flush=True,
        )
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
