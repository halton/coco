#!/usr/bin/env python3
"""verify_infra_V6_scan_ast: scan_reverse_sha_locks AST 路径 vs regex 一致性 + 字面假阳性免疫.

INFRA_V6_SCAN_AST_SHA_LOCKS  # sentinel pragma: noqa: E501

infra-V6-backlog-scan-ast-based (phase-63 #1):
``scripts/_verify_lib.py`` 中 ``scan_reverse_sha_locks`` 使用 regex
(``_RE_REVLOCK_SINGLELINE`` / ``_RE_REVLOCK_TUPLE_OPEN`` 系列) line-anchored
匹配 ``EXPECTED_*_SHA256 = "..."`` 顶层字面常量。该实现存在以下字面假阳性盲区:
docstring 内逐行内容若**行首即形如**赋值字面 (``^EXPECTED_X_SHA256 = "<hex>"$``,
缩进 0), regex 会误把它识别为反向锁条目, 把不属于真实 module-toplevel 赋值的
hex 加入扫描结果。

新增 ``scan_reverse_sha_locks_ast`` 使用 ``ast.parse`` 遍历 module body, 仅识别
真正的 ``ast.Assign`` + ``Name`` target + ``Constant(str)`` value, docstring /
comment / 嵌套 tuple 内字面值天然不会被 AST 解析为赋值, 因此完全免疫该假阳性。

校验层级:
- V1 self file sha 自锁 (sentinel pragma 行尾锚 + SHA256 自检, P305 模式)
- V2 真实 scripts/ 目录上 AST vs regex 输出**完全一致** (set 等值)
- V3 docstring mutant: AST ⊊ regex 且 EXPECTED_FAKE_SHA256 不在 AST 结果
- V4 comment mutant: # 注释起首 (regex anchored 不会匹配, 此 check 验证不退化为漏报)
- V5 nested-tuple mutant: AST 与 regex 都应忽略嵌套 tuple 内的字面 hex,
  断言 EXPECTED_NESTED_TUPLE_SHA256 不在两者结果

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import hashlib
import re
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
SELF = Path(__file__).resolve()

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    scan_reverse_sha_locks,
    scan_reverse_sha_locks_ast,
    verify_summary_exit,
)

SENTINEL_PRAGMA = "INFRA_V6_SCAN_AST_SHA_LOCKS"
# 首跑用 __BUMP_ME__ 占位; V1 会回填 self file sha
EXPECTED_SELF_FILE_SHA256 = "__BUMP_ME__"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    _results.append((tag, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[verify_infra_V6_scan_ast] {status} {tag}: {detail}", flush=True)


def _check_v1_self_sha() -> None:
    """V1: sentinel pragma 行尾锚 + self file sha 自检 (P305 模式).

    要求 SELF 文件中含 ``INFRA_V6_SCAN_AST_SHA_LOCKS`` 文字 sentinel
    (行尾锚以 ``  # sentinel pragma: noqa: E501`` 形式), 且如果
    EXPECTED_SELF_FILE_SHA256 非 ``__BUMP_ME__``, 则它必须等于 SELF 当前 sha256.
    """
    src = SELF.read_text(encoding="utf-8")
    pragma_pattern = re.compile(rf"^{SENTINEL_PRAGMA}\s+# sentinel pragma:", re.MULTILINE)
    if not pragma_pattern.search(src):
        _emit("V1_self_sha", False, f"sentinel pragma '{SENTINEL_PRAGMA}' (line-anchored, '# sentinel pragma:' suffix) not found in SELF")
        return
    if EXPECTED_SELF_FILE_SHA256 == "__BUMP_ME__":
        _emit("V1_self_sha", True, f"sentinel present; EXPECTED_SELF_FILE_SHA256 is __BUMP_ME__ placeholder (first-run bootstrap)")
        return
    actual = hashlib.sha256(SELF.read_bytes()).hexdigest()
    ok = actual == EXPECTED_SELF_FILE_SHA256
    _emit(
        "V1_self_sha",
        ok,
        f"expected={EXPECTED_SELF_FILE_SHA256[:12]} actual={actual[:12]}",
    )


def _check_v2_ast_vs_regex_clean() -> None:
    """V2: 真实 scripts/ 目录下 AST 与 regex 输出 set 完全一致."""
    ast_res = scan_reverse_sha_locks_ast(SCRIPTS)
    re_res = scan_reverse_sha_locks(SCRIPTS)
    ast_keys = {(r["file"], r["const_name"], r["sha_hex"], r["kind"]) for r in ast_res}
    re_keys = {(r["file"], r["const_name"], r["sha_hex"], r["kind"]) for r in re_res}
    diff_only_ast = ast_keys - re_keys
    diff_only_re = re_keys - ast_keys
    ok = not diff_only_ast and not diff_only_re and len(ast_keys) > 0
    _emit(
        "V2_ast_vs_regex_clean",
        ok,
        f"ast={len(ast_keys)} regex={len(re_keys)} ast-only={len(diff_only_ast)} regex-only={len(diff_only_re)}",
    )


def _write_fixture(tmp: Path, name: str, content: str) -> Path:
    """fixture 文件名须形如 verify_*.py 才能被 scan_reverse_sha_locks 拾取."""
    p = tmp / name
    p.write_text(content, encoding="utf-8")
    return p


def _check_v3_docstring_mutant() -> None:
    """V3 mutant: docstring 内字面 EXPECTED_FAKE_SHA256 = "<hex>" 行首.

    regex 路径 line-anchored 应误把它识别为反向锁; AST 路径应忽略.
    断言 AST 结果 ⊊ regex 结果 且 EXPECTED_FAKE_SHA256 不在 AST.
    """
    real_hex_good = "a" * 64  # 真实 toplevel 赋值用
    fake_hex = "b" * 64       # docstring 内字面 (regex 应误报)
    content = f'''"""docstring header.

EXPECTED_FAKE_VERIFY_888_SHA256 = "{fake_hex}"

end docstring."""

EXPECTED_REAL_VERIFY_999_SHA256 = "{real_hex_good}"
'''
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_fixture(tmp, "verify_mutant_docstring.py", content)
        ast_res = scan_reverse_sha_locks_ast(tmp)
        re_res = scan_reverse_sha_locks(tmp)
        ast_names = {r["const_name"] for r in ast_res}
        re_names = {r["const_name"] for r in re_res}
        fake_in_ast = "EXPECTED_FAKE_VERIFY_888_SHA256" in ast_names
        fake_in_regex = "EXPECTED_FAKE_VERIFY_888_SHA256" in re_names
        real_in_ast = "EXPECTED_REAL_VERIFY_999_SHA256" in ast_names
        # AST 应只含 real, regex 应含 real + fake (即 ast ⊊ regex)
        ok = (
            not fake_in_ast            # AST 忽略 docstring 字面
            and real_in_ast             # AST 收录真实 toplevel 赋值
            and fake_in_regex           # regex 误报 docstring 字面 (证明 AST 之必要)
            and ast_names < re_names    # AST 严格子集 regex
        )
        _emit(
            "V3_ast_ignores_docstring_literal",
            ok,
            f"ast_names={sorted(ast_names)} re_names={sorted(re_names)}",
        )


def _check_v4_comment_mutant() -> None:
    """V4 mutant: 注释 `# EXPECTED_X_SHA256 = "..."` 起首.

    regex 路径要求 ``^([A-Z_]...`` 行首字母, ``#`` 起首不匹配; AST 自然忽略.
    断言两者**都不含** EXPECTED_COMMENT_VERIFY_777_SHA256, 真实赋值收录.
    """
    real_hex_good = "c" * 64
    fake_hex = "d" * 64
    content = f'''"""mutant docstring."""

# EXPECTED_COMMENT_VERIFY_777_SHA256 = "{fake_hex}"

EXPECTED_REAL_VERIFY_998_SHA256 = "{real_hex_good}"
'''
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_fixture(tmp, "verify_mutant_comment.py", content)
        ast_res = scan_reverse_sha_locks_ast(tmp)
        re_res = scan_reverse_sha_locks(tmp)
        ast_names = {r["const_name"] for r in ast_res}
        re_names = {r["const_name"] for r in re_res}
        ok = (
            "EXPECTED_COMMENT_VERIFY_777_SHA256" not in ast_names
            and "EXPECTED_COMMENT_VERIFY_777_SHA256" not in re_names
            and "EXPECTED_REAL_VERIFY_998_SHA256" in ast_names
            and "EXPECTED_REAL_VERIFY_998_SHA256" in re_names
        )
        _emit(
            "V4_ast_ignores_comment_literal",
            ok,
            f"ast_names={sorted(ast_names)} re_names={sorted(re_names)}",
        )


def _check_v5_nested_tuple_mutant() -> None:
    """V5 mutant: 嵌套 tuple 内字面 hex.

    例如 ``OTHER_VAR_VERIFY = ("EXPECTED_NESTED_SHA256", "<hex>")`` 这种把假
    const-name 字符串与 hex 字符串塞进 tuple value 里。
    regex 路径行首是 ``OTHER_`` 不会匹配 EXPECTED_*, AST 路径只看 toplevel
    Assign name + Constant(str) value, 嵌套 tuple 元素中的字面不会被识别为新 const.
    断言两者**都不含** EXPECTED_NESTED_VERIFY_777_SHA256.
    """
    real_hex_good = "e" * 64
    fake_hex = "f" * 64
    content = f'''"""mutant docstring."""

OTHER_VAR_VERIFY = ("EXPECTED_NESTED_VERIFY_777_SHA256", "{fake_hex}")

EXPECTED_REAL_VERIFY_997_SHA256 = "{real_hex_good}"
'''
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_fixture(tmp, "verify_mutant_nested.py", content)
        ast_res = scan_reverse_sha_locks_ast(tmp)
        re_res = scan_reverse_sha_locks(tmp)
        ast_names = {r["const_name"] for r in ast_res}
        re_names = {r["const_name"] for r in re_res}
        ok = (
            "EXPECTED_NESTED_VERIFY_777_SHA256" not in ast_names
            and "EXPECTED_NESTED_VERIFY_777_SHA256" not in re_names
            and "EXPECTED_REAL_VERIFY_997_SHA256" in ast_names
            and "EXPECTED_REAL_VERIFY_997_SHA256" in re_names
        )
        _emit(
            "V5_ast_ignores_nested_tuple_literal",
            ok,
            f"ast_names={sorted(ast_names)} re_names={sorted(re_names)}",
        )


def main() -> int:
    _check_v1_self_sha()
    _check_v2_ast_vs_regex_clean()
    _check_v3_docstring_mutant()
    _check_v4_comment_mutant()
    _check_v5_nested_tuple_mutant()
    failed = sum(1 for _, ok, _ in _results if not ok)
    total = len(_results)
    if failed:
        print(f"[verify_infra_V6_scan_ast][SUMMARY] FAIL {failed}/{total}", flush=True)
    else:
        print(f"[verify_infra_V6_scan_ast][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return failed


if __name__ == "__main__":
    failed = main()
    verify_summary_exit(failed)
