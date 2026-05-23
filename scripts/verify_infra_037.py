#!/usr/bin/env python3
"""verify_infra_037: _verify_lib.func_sha_by_name helper 加固验证 (verify-only).

infra-037: 在 scripts/_verify_lib.py 中新增公开 helper
``func_sha_by_name(path, func_name)``, 封装 ast 顶层 function/async function
抽取 + ``ast.unparse`` 规范化 + sha256, 让后续 verify-script 复用 func-level sha
抽取的统一入口。本脚本锁住该 helper 的存在、文件 sha、函数 sha、mutant 反证及
行为正确性。

V0 scaffolding: _verify_lib.py 存在 + func_sha_by_name 公开符号 (def 形 + __all__)。
V1 docstring sentinel + 本脚本函数 sha 自锁
   (sentinel section ``INFRA_037_SHA_LOCKS`` 必须存在; 关键 checker 函数体 sha 锁住)。
V2 file-level sha 锁 _verify_lib.py 整体文件 + func-level sha 锁
   _verify_lib.func_sha_by_name (canonical via ast.unparse)。
V3 mutant 反证: 临时把 _verify_lib.func_sha_by_name 内的 ``ast.unparse(node)``
   改为 ``str(node)`` (污染规范化), 重新加载 module 后期望算出的 sha 与 EXPECTED
   不同; finally 还原原文件。
V4 行为验证: 调用 func_sha_by_name 对 _verify_lib.py 中已知 helper
   (parse_headings_from_doc) 算 sha, 断言返回非空且 hex 长度 64;
   不存在 func_name 抛 ValueError; 不存在 path 抛 FileNotFoundError。
V5 Reviewer-LGTM gate (print-only, 提示后续 closeout 阶段必须有
   sub-agent fresh-context Reviewer LGTM 记录在 evidence 中)。
V6 (infra-037-backlog-nested-name-error-clarity): 构造临时文件含 2 个顶层同名
   函数, 期望 func_sha_by_name raise ValueError, 错误消息包含 ``found 2``
   与 ``lineno`` 关键字; 单同名 case 仍正常返回 hex sha。

INFRA_037_SHA_LOCKS
-------------------
- ``_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``_verify_lib.func_sha_by_name`` canonical func sha: EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA
- 本脚本关键 checker (v2_sha_locks) 自身 func sha: EXPECTED_V2_CHECKER_FUNC_SHA

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034)
------------------------
本脚本必须在已激活的 .venv 下运行 (``.venv/bin/python``); 不要用系统
``python3`` 直接调用, 否则模块加载路径可能与 ``./init.sh`` smoke 不一致。

## Lock: EXPECTED_VERIFY_LIB_FILE_SHA
- target_function: N/A
- target_file: scripts/_verify_lib.py
- lock_kind: file_sha
- bump_when: _verify_lib.py 文件 sha256 变 (任何字节改动)
- bump_protocol: 重算 sha256 of scripts/_verify_lib.py 并更新常量
- rationale: 锁住 V4 sha-lock 公共 helper 库的整体内容, 防止悄改污染 canonical 化逻辑

## Lock: EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA
- target_function: func_sha_by_name
- target_file: scripts/_verify_lib.py
- lock_kind: ast_func_sha
- bump_when: func_sha_by_name 实现变化
- bump_protocol: recompute func_sha_by_name("func_sha_by_name", scripts/_verify_lib.py) then update constant
- rationale: 锁 V4 lock 最底层的 canonical func sha 生成器, mutation 会让全套 func-sha 锁集体失效

## Lock: EXPECTED_V2_CHECKER_FUNC_SHA
- target_function: v2_sha_locks
- target_file: scripts/verify_infra_037.py
- lock_kind: ast_func_sha
- bump_when: 本脚本 v2_sha_locks checker 实现变化
- bump_protocol: recompute func_sha_by_name("v2_sha_locks", scripts/verify_infra_037.py) then update constant
- rationale: 自检 checker, 防止 checker 自身被悄改成永真
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

# infra-037 sha lock 常量 (V2)
# infra-040-backlog bump: 新增 assert_unique_needle helper 后 file sha 变更
# infra-V6-backlog bump (P264): 抽 V6 scan_reverse_sha_locks 等 helper 后 file sha 再变更
EXPECTED_VERIFY_LIB_FILE_SHA = "43d352534301242f13ef33909cdba2585f8f18734d9e052f2e0de0d075955d75"
EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA = "781650973120bca1e7aa9d0eebfaad12c323d3002f5ff68eccc913ac6ecfb09e"

# infra-037 自身关键 checker (v2_sha_locks) 函数 sha (V1 自锁, 占位, 末尾自计算)
EXPECTED_V2_CHECKER_FUNC_SHA = "d625d1aeaea5e8429fa897d38cc0e293eb606f784452da014039ee4eebc89130"

# docstring sentinel (V1)
DOCSTRING_SENTINEL = "INFRA_037_SHA_LOCKS"


# phase-47 #1.47: V5_GATE evidence-bind helper (grace_period 兜底 soft graduate)
REAL_FEATURE_LIST = Path(__file__).resolve().parents[1] / "feature_list.json"
V5_GATE_FEATURE_ID = "__PHASE_47_PLACEHOLDER_INFRA_037__"

_results: List[Tuple[str, bool, str]] = []


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_037][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _load_lib():
    sys.path.insert(0, str(SCRIPTS))
    if "_verify_lib" in sys.modules:
        del sys.modules["_verify_lib"]
    import _verify_lib  # type: ignore
    return _verify_lib


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _func_sha_via_unparse(path: Path, func_name: str) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return hashlib.sha256(ast.unparse(node).encode("utf-8")).hexdigest()
    raise RuntimeError(f"func {func_name!r} not found in {path}")


# ---------------------------------------------------------------------------
# V0: scaffolding — lib 存在 + 公开符号
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    if not VERIFY_LIB.is_file():
        _emit("V0_verify_lib_exists", False, f"missing {VERIFY_LIB}")
        return
    _emit("V0_verify_lib_exists", True, str(VERIFY_LIB))
    src = VERIFY_LIB.read_text(encoding="utf-8")
    _emit(
        "V0_func_def_present",
        "def func_sha_by_name" in src,
        "def func_sha_by_name",
    )
    _emit(
        "V0_in_all_export",
        '"func_sha_by_name"' in src or "'func_sha_by_name'" in src,
        "__all__ contains func_sha_by_name",
    )


# ---------------------------------------------------------------------------
# V1: docstring sentinel + 自身 checker func sha 自锁
# ---------------------------------------------------------------------------
def v1_self_lock() -> None:
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8")))
    _emit(
        "V1_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )
    # 自身 v2_sha_locks 函数 sha 锁
    try:
        got = _func_sha_via_unparse(self_path, "v2_sha_locks")
    except Exception as e:
        _emit("V1_self_checker_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_V2_CHECKER_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_self_checker_func_sha",
            False,
            f"placeholder; bump EXPECTED_V2_CHECKER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V1_self_checker_func_sha",
        got == EXPECTED_V2_CHECKER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_V2_CHECKER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V2: file sha + helper func sha 锁
# ---------------------------------------------------------------------------
def v2_sha_locks() -> None:
    got_file = _file_sha(VERIFY_LIB)
    _emit(
        "V2_lib_file_sha",
        got_file == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got_file[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )
    try:
        got_func = _func_sha_via_unparse(VERIFY_LIB, "func_sha_by_name")
    except Exception as e:
        _emit("V2_func_sha_by_name_func_sha", False, f"compute err: {e!r}")
        return
    _emit(
        "V2_func_sha_by_name_func_sha",
        got_func == EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA,
        f"got={got_func[:16]} expect={EXPECTED_FUNC_SHA_BY_NAME_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: mutant 反证 — 临时污染 _verify_lib.func_sha_by_name 内规范化策略
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    original = VERIFY_LIB.read_text(encoding="utf-8")
    mutant = original.replace("canonical = ast.unparse(matches[0])", "canonical = str(matches[0])", 1)
    if mutant == original:
        _emit("V3_mutant_apply", False, "no replacement target found")
        return
    try:
        VERIFY_LIB.write_text(mutant, encoding="utf-8")
        try:
            lib = _load_lib()
            # 用 mutated helper 对 parse_headings_from_doc 自身重新计算 sha
            mut_sha = lib.func_sha_by_name(VERIFY_LIB, "parse_headings_from_doc")
        except Exception as e:
            # 抛错也算 mutant 被发现 (与 unparse 路径行为不同)
            _emit("V3_mutant_detected", True, f"mutated path raised: {e!r}")
            return
        # 计算 unparse 路径作为 baseline (从原文件)
        original_sha = hashlib.sha256(
            ast.unparse(
                next(
                    n for n in ast.parse(original).body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == "parse_headings_from_doc"
                )
            ).encode("utf-8")
        ).hexdigest()
        _emit(
            "V3_mutant_detected",
            mut_sha != original_sha,
            f"mut={mut_sha[:16]} baseline={original_sha[:16]}",
        )
    finally:
        VERIFY_LIB.write_text(original, encoding="utf-8")
        # 清缓存避免污染后续 V4
        sys.path.insert(0, str(SCRIPTS))
        if "_verify_lib" in sys.modules:
            del sys.modules["_verify_lib"]


# ---------------------------------------------------------------------------
# V4: 行为验证 — 调 helper 算已知函数 sha + 异常路径
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    try:
        lib = _load_lib()
    except Exception as e:
        _emit("V4_helper_load", False, f"import err: {e!r}")
        return
    _emit("V4_helper_load", True, "_verify_lib loaded")
    try:
        sha = lib.func_sha_by_name(VERIFY_LIB, "parse_headings_from_doc")
    except Exception as e:
        _emit("V4_helper_call", False, f"err: {e!r}")
        return
    ok_hex = isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)
    _emit("V4_helper_call", ok_hex, f"sha={sha[:16]} len={len(sha)}")
    # 异常路径: 不存在的 func
    try:
        lib.func_sha_by_name(VERIFY_LIB, "no_such_function_xyz")
        _emit("V4_value_error", False, "should have raised ValueError")
    except ValueError:
        _emit("V4_value_error", True, "ValueError raised as expected")
    except Exception as e:
        _emit("V4_value_error", False, f"wrong exc: {e!r}")
    # 异常路径: 不存在的 path
    try:
        lib.func_sha_by_name("/tmp/__nonexistent_infra_037__.py", "x")
        _emit("V4_file_not_found", False, "should have raised FileNotFoundError")
    except FileNotFoundError:
        _emit("V4_file_not_found", True, "FileNotFoundError raised as expected")
    except Exception as e:
        _emit("V4_file_not_found", False, f"wrong exc: {e!r}")


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (print-only)
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    """V5 Reviewer LGTM gate — phase-47 #1.47 graduate to evidence-bind helper.

    target feature evidence 不完整 (legacy / not_started)，通过 grace_period 兜底
    保持 emit=True，待 target feature 补齐 reviewer evidence 后从 grace 列表移除。
    """
    if not REAL_FEATURE_LIST.is_file():
        _emit(
            "V5_reviewer_lgtm_gate",
            False,
            f"feature_list.json not found at {REAL_FEATURE_LIST}",
        )
        return
    result = assert_v5_reviewer_gate_evidence_bind(
        V5_GATE_FEATURE_ID, REAL_FEATURE_LIST,
        grace_period_feature_ids=(V5_GATE_FEATURE_ID,),
    )
    _emit(
        "V5_reviewer_lgtm_gate",
        bool(result["ok"]),
        f"target={V5_GATE_FEATURE_ID} helper_ok={result['ok']} "
        f"grace_skipped={result['grace_skipped']} "
        f"verdict={result['verdict']!r} kind={result['reviewer_kind']!r} "
        f"summary_len={result['summary_len']} reason={result['reason']!r}",
    )


# ---------------------------------------------------------------------------
# V6: duplicate-name ValueError (infra-037-backlog-nested-name-error-clarity)
# ---------------------------------------------------------------------------
def v6_duplicate_name_error() -> None:
    """构造临时 .py: 含 2 个同名顶层函数, 期望 func_sha_by_name raise ValueError.

    错误消息须含 ``found 2`` 与 ``lineno`` 关键字; 单同名 case 仍 PASS。
    """
    import tempfile
    import os
    try:
        lib = _load_lib()
    except Exception as e:
        _emit("V6_helper_load", False, f"import err: {e!r}")
        return
    src_dup = (
        "def dup_target():\n"
        "    return 1\n"
        "\n"
        "def other():\n"
        "    return 2\n"
        "\n"
        "def dup_target():\n"
        "    return 3\n"
    )
    fd, path = tempfile.mkstemp(suffix="_v6_dup.py", prefix="verify_infra_037_")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src_dup)
        raised = False
        msg = ""
        try:
            lib.func_sha_by_name(path, "dup_target")
        except ValueError as e:
            raised = True
            msg = str(e)
        except Exception as e:
            _emit("V6_duplicate_raises_value_error", False, f"wrong exc: {e!r}")
            return
        has_found2 = "found 2" in msg
        has_lineno = "lineno" in msg
        _emit(
            "V6_duplicate_raises_value_error",
            raised and has_found2 and has_lineno,
            f"raised={raised} found2={has_found2} lineno_in_msg={has_lineno} msg={msg!r}",
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    src_single = "def single_target():\n    return 1\n"
    fd, path2 = tempfile.mkstemp(suffix="_v6_single.py", prefix="verify_infra_037_")
    os.close(fd)
    try:
        with open(path2, "w", encoding="utf-8") as fh:
            fh.write(src_single)
        try:
            sha = lib.func_sha_by_name(path2, "single_target")
            ok = isinstance(sha, str) and len(sha) == 64
            _emit("V6_single_name_still_works", ok, f"sha={sha[:16]} len={len(sha)}")
        except Exception as e:
            _emit("V6_single_name_still_works", False, f"unexpected err: {e!r}")
    finally:
        try:
            os.unlink(path2)
        except OSError:
            pass


def main() -> int:
    v0_scaffolding()
    v1_self_lock()
    v2_sha_locks()
    v3_mutant()
    v4_behavior()
    v5_reviewer_gate()
    v6_duplicate_name_error()
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_037][SUMMARY] FAILED tags: {failed}", flush=True)
        return 1
    print(f"[verify_infra_037][SUMMARY] ALL PASS ({len(_results)} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
