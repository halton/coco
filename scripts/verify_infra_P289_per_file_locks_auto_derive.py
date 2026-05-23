#!/usr/bin/env python3
"""verify_infra_P289: _PER_FILE_LOCKS docstring-based auto-derive audit (verify-only).

## Lock: EXPECTED_PARSE_LOCK_DOCSTRING_FUNC_SHA
- target_function: _parse_lock_docstring
- target_file: scripts/_verify_lib.py
- lock_kind: ast_func_sha
- bump_when: _parse_lock_docstring implementation changes
- bump_protocol: recompute func_sha_by_name("_parse_lock_docstring", target_file) then update constant
- rationale: lock auto-derive parser; mutation would silently mis-populate audit table

## Lock: EXPECTED_AUTO_DERIVE_FUNC_SHA
- target_function: scan_per_file_locks_from_docstrings
- target_file: scripts/_verify_lib.py
- lock_kind: ast_func_sha
- bump_when: scan_per_file_locks_from_docstrings implementation changes
- bump_protocol: recompute func_sha_by_name("scan_per_file_locks_from_docstrings", target_file) then update constant
- rationale: lock outer scanner; mutation could drop verify files / mis-key result

infra-P289-per-file-locks-auto-derive (phase-63 #3): 把 ``_PER_FILE_LOCKS``
硬编码表 (dump_v4_sha_graph) 改造为可从 verify 脚本 module docstring 中
``## Lock: <CONST>`` 小节自动派生的 audit 通道。本 verify 只做 audit, **不**
替换 dump_v4_sha_graph 的硬编码表 (audit-only 阶段)。

INFRA_P289_SHA_LOCKS
--------------------
- self ``main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA
- ``_parse_lock_docstring`` func sha: EXPECTED_PARSE_LOCK_DOCSTRING_FUNC_SHA
- ``scan_per_file_locks_from_docstrings`` func sha: EXPECTED_AUTO_DERIVE_FUNC_SHA

校验层级:

- V1 self main func sha (V8 sentinel pragma 沿用)
- V2 scan_per_file_locks_from_docstrings 返回 non-empty dict
- V3 至少一个 docstring ``## Lock:`` 小节存在 (phase-62 #2 verify_infra_110)
- V4 至少 1 个 entry 的 6 个字段 (_LOCK_FIELDS) 全部命中
- V5 helper func sha 自锁 (_parse_lock_docstring + scan_per_file_locks_from_docstrings)
- V6 mutant: 临时 fixture 缺 target_function → returns partial (5/6);
  完整 6 字段 → returns 6/6

退出码 0=ALL PASS / 1=任一 FAIL.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    _parse_lock_docstring,
    scan_per_file_locks_from_docstrings,
    func_sha_by_name,
    verify_summary_exit,
)

# 自锁: V1 self main + V5 helper func sha
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "8280ccaf1f3edaa764473372614135f3f2949860b8b0a847a0727a16a8e22ade"
)
EXPECTED_PARSE_LOCK_DOCSTRING_FUNC_SHA = (
    "ec07222ded11d2738b96ea06b4fa68862425d21219d66947d741e5b74615cb77"
)
EXPECTED_AUTO_DERIVE_FUNC_SHA = (
    "eac29f82dc6d2e786fa707363a17160b271df548307dcf84b06f1cc28c064137"
)

_LOCK_FIELDS_EXPECTED = (
    "target_function",
    "target_file",
    "lock_kind",
    "bump_when",
    "bump_protocol",
    "rationale",
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P289][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _check_sha(tag: str, got: str, expected: str, label: str) -> None:
    if expected == "__BUMP_ME__":
        _emit(tag, False, f"placeholder; bump {label}={got}")
        return
    _emit(
        tag,
        got == expected,
        f"got={got[:16]} expect={expected[:16]}",
    )


# ---------------------------------------------------------------------------
# V1: self main func sha (sentinel pragma)
# ---------------------------------------------------------------------------
def v1_self_main_func_sha() -> None:
    got = func_sha_by_name(Path(__file__), "main")
    _check_sha("V1_self_main_func_sha", got, EXPECTED_SELF_MAIN_FUNC_SHA,
               "EXPECTED_SELF_MAIN_FUNC_SHA")


# ---------------------------------------------------------------------------
# V2: scan_per_file_locks_from_docstrings returns non-empty dict
# ---------------------------------------------------------------------------
def v2_auto_derive_returns_dict() -> dict:
    d = scan_per_file_locks_from_docstrings(SCRIPTS)
    _emit(
        "V2_auto_derive_returns_dict",
        isinstance(d, dict) and len(d) >= 1,
        f"type={type(d).__name__} n_entries={len(d) if isinstance(d, dict) else -1}",
    )
    return d if isinstance(d, dict) else {}


# ---------------------------------------------------------------------------
# V3: at least one Lock block present (phase-62 #2 verify_infra_110 baseline)
# ---------------------------------------------------------------------------
def v3_at_least_one_lock_block_present(derived: dict) -> None:
    # 任意 source_file 包含至少 1 个 Lock 小节即 PASS
    sources = {src for (src, _const) in derived.keys()}
    has_any = len(derived) >= 1
    _emit(
        "V3_at_least_one_lock_block_present",
        has_any,
        f"n_entries={len(derived)} sources={sorted(sources)}",
    )


# ---------------------------------------------------------------------------
# V4: parsed fields complete — 至少 1 个 entry 含全部 6 字段
# ---------------------------------------------------------------------------
def v4_parsed_fields_complete(derived: dict) -> None:
    expected = set(_LOCK_FIELDS_EXPECTED)
    n_complete = 0
    examples: list[str] = []
    for (src, const), meta in derived.items():
        if set(meta.keys()) >= expected:
            n_complete += 1
            if len(examples) < 2:
                examples.append(f"{src}::{const}")
    _emit(
        "V4_parsed_fields_complete",
        n_complete >= 1,
        f"n_complete={n_complete}/{len(derived)} examples={examples} "
        f"expected_fields={sorted(expected)}",
    )


# ---------------------------------------------------------------------------
# V5: helper func sha 自锁 (_parse_lock_docstring + scan_per_file_locks_from_docstrings)
# ---------------------------------------------------------------------------
def v5_helper_func_sha_lock() -> None:
    lib_path = SCRIPTS / "_verify_lib.py"
    got_parse = func_sha_by_name(lib_path, "_parse_lock_docstring")
    _check_sha(
        "V5_parse_lock_docstring_func_sha",
        got_parse,
        EXPECTED_PARSE_LOCK_DOCSTRING_FUNC_SHA,
        "EXPECTED_PARSE_LOCK_DOCSTRING_FUNC_SHA",
    )
    got_auto = func_sha_by_name(lib_path, "scan_per_file_locks_from_docstrings")
    _check_sha(
        "V5_auto_derive_func_sha",
        got_auto,
        EXPECTED_AUTO_DERIVE_FUNC_SHA,
        "EXPECTED_AUTO_DERIVE_FUNC_SHA",
    )


# ---------------------------------------------------------------------------
# V6: mutant — fixture 缺 target_function → partial; 完整 6 字段 → full
# ---------------------------------------------------------------------------
_FIXTURE_PARTIAL = '''"""fixture partial: missing target_function.

## Lock: EXPECTED_FOO_FUNC_SHA
- target_file: scripts/foo.py
- lock_kind: ast_func_sha
- bump_when: foo changes
- bump_protocol: recompute then update
- rationale: partial-fixture for mutant V6
"""
'''

_FIXTURE_FULL = '''"""fixture full: all 6 fields.

## Lock: EXPECTED_BAR_FUNC_SHA
- target_function: bar
- target_file: scripts/bar.py
- lock_kind: ast_func_sha
- bump_when: bar changes
- bump_protocol: recompute then update
- rationale: full-fixture for mutant V6
"""
'''


def v6_mutant() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        partial = td_path / "fixture_partial.py"
        partial.write_text(_FIXTURE_PARTIAL, encoding="utf-8")
        full = td_path / "fixture_full.py"
        full.write_text(_FIXTURE_FULL, encoding="utf-8")
        p_res = _parse_lock_docstring(partial)
        f_res = _parse_lock_docstring(full)
        # partial: 仍返回 dict (含 1 个 entry) 但 entry fields 数 < 6
        partial_ok = (
            isinstance(p_res, dict)
            and "EXPECTED_FOO_FUNC_SHA" in p_res
            and "target_function" not in p_res["EXPECTED_FOO_FUNC_SHA"]
            and len(p_res["EXPECTED_FOO_FUNC_SHA"]) == 5
        )
        _emit(
            "V6_mutant_partial_5fields",
            partial_ok,
            f"partial_fields_n={len(p_res['EXPECTED_FOO_FUNC_SHA']) if isinstance(p_res, dict) and 'EXPECTED_FOO_FUNC_SHA' in p_res else -1}",
        )
        # full: 6 字段全到位
        full_ok = (
            isinstance(f_res, dict)
            and "EXPECTED_BAR_FUNC_SHA" in f_res
            and set(f_res["EXPECTED_BAR_FUNC_SHA"].keys())
            == set(_LOCK_FIELDS_EXPECTED)
        )
        _emit(
            "V6_mutant_full_6fields",
            full_ok,
            f"full_fields={sorted(f_res['EXPECTED_BAR_FUNC_SHA'].keys()) if isinstance(f_res, dict) and 'EXPECTED_BAR_FUNC_SHA' in f_res else None}",
        )


def main() -> None:
    v1_self_main_func_sha()
    derived = v2_auto_derive_returns_dict()
    v3_at_least_one_lock_block_present(derived)
    v4_parsed_fields_complete(derived)
    v5_helper_func_sha_lock()
    v6_mutant()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(f"[verify_infra_P289][SUMMARY] FAIL {failed}/{total}: {names}", flush=True)
    else:
        print(f"[verify_infra_P289][SUMMARY] ALL PASS ({total} checks)", flush=True)
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
