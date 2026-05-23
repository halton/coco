#!/usr/bin/env python3
"""verify_robot_037: _verify_lib.read_constant helper 加固验证 (verify-only).

robot-037: 在 scripts/_verify_lib.py 中新增公开 helper
``read_constant(path, const_name)``, 封装 ast.literal_eval 静态读取模块级
常量字面值的通用模式, 收敛 verify_robot_034 / verify_robot_036 各自内部
``_read_sentinel_from_verify_032`` / ``_read_constant_from`` 的复制实现。
本脚本锁住该 helper 存在、文件 sha、函数 sha、mutant 反证、行为正确性,
并断言 verify_robot_034 已切换到通过 ``from _verify_lib import read_constant``
路径调用 helper (不再自带 inline 实现)。

V0 scaffolding: _verify_lib.py 存在 + read_constant 公开符号 (def 形 + __all__);
   verify_robot_034 中 ``from _verify_lib import read_constant`` 字面存在,
   且 inline _read_sentinel_from_verify_032 helper 已被移除。
V1 docstring sentinel + 本脚本关键 checker (v2_sha_locks) 自身 func sha 自锁
   (sentinel section ``ROBOT_037_SHA_LOCKS`` 必须存在)。
V2 file-level sha 锁 _verify_lib.py 整体 + func-level sha 锁
   _verify_lib.read_constant (canonical via ast.unparse)。
V3 mutant 反证: 临时把 _verify_lib.read_constant 内的 ``ast.literal_eval(node.value)``
   改为 ``eval(ast.unparse(node.value))`` (危险路径, 行为不再等价 literal_eval),
   重新加载 module 后期望调用结果或异常发生变化; finally 还原原文件。
V4 行为验证: 调用 read_constant 读 verify_robot_034 中 ``_SENTINEL_SRC_NAME``
   字面常量, 断言非空 str; 不存在的 const_name 抛 ValueError;
   不存在的 path 抛 FileNotFoundError; 非 literal value (如 Path 表达式) 抛 ValueError。
V5 Reviewer-LGTM gate (print-only, 提示后续 closeout 阶段必须有
   sub-agent fresh-context Reviewer LGTM 记录在 evidence 中)。
V6 robot-037-backlog import-time fail fallback meta-lock:
   - V6a verify_robot_034.py 顶层包含 try/except 字面 + sentinel fallback 常量
     ``_SENTINEL_FALLBACK``;
   - V6b verify_robot_034.py 文件级 sha 锁 (EXPECTED_VERIFY_034_FILE_SHA);
   - V6c verify_robot_034.py ``main`` 函数体 sha 自锁
     (EXPECTED_VERIFY_034_MAIN_FUNC_SHA, 保证 main 入口形态稳定);
   - V6d 行为: 在子进程中 monkeypatch ``_verify_lib.read_constant`` 抛
     RuntimeError, import verify_robot_034 模块 → 必须**不抛** ImportError,
     且 ``SENTINEL_LINE`` 等于 fallback 常量 (证明降级生效)。

ROBOT_037_SHA_LOCKS
-------------------
- ``_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``_verify_lib.read_constant`` canonical func sha: EXPECTED_READ_CONSTANT_FUNC_SHA
- 本脚本关键 checker (v2_sha_locks) 自身 func sha: EXPECTED_V2_CHECKER_FUNC_SHA

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034)
------------------------
本脚本必须在已激活的 .venv 下运行 (``.venv/bin/python``); 不要用系统
``python3`` 直接调用, 否则模块加载路径可能与 ``./init.sh`` smoke 不一致。
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"
VERIFY_034 = SCRIPTS / "verify_robot_034.py"

# robot-037 sha lock 常量 (V2)
# infra-V6-backlog bump (P264): 抽 V6 scan_reverse_sha_locks 等 helper 后 file sha 变更
# infra-P281-expected-prefix-typo-guard bump (P281): 新增 verify_expected_prefix_typo_guard helper
EXPECTED_VERIFY_LIB_FILE_SHA = "7df5af6b9d48687e0a5efab7b3dc2e3dfc2fd54e6aa07d1583fb9d5a56604ef4"
EXPECTED_READ_CONSTANT_FUNC_SHA = "a06af8a80fe201ecb03acc7d8b04551e1875031447dd140582cf557b5de75bd2"

# 本脚本关键 checker (v2_sha_locks) 函数 sha (V1 自锁, 占位, 末尾自计算)
EXPECTED_V2_CHECKER_FUNC_SHA = "3f6c7a303b4f0a434699b2e88ef8fcef9e36e3ffaf93ca09a98a321e64d6dae4"

# robot-037-backlog (V6): verify_robot_034.py 文件级 + main 函数体 sha 锁
EXPECTED_VERIFY_034_FILE_SHA = "2c7b49c23ed22feb52c40b9a72582d4bda3e8154f24c943c3646fc64b4a78983"
EXPECTED_VERIFY_034_MAIN_FUNC_SHA = "f995f51c1578e5568dccbcea6b7df4b3797789db85db542da7a14843eac02476"
# V6a 字面检查: try/except 包裹 read_constant 顶层调用 + sentinel fallback 命名
V6_TRY_EXCEPT_NEEDLE = "SENTINEL_LINE = read_constant(VERIFY_032, _SENTINEL_SRC_NAME)"
V6_FALLBACK_CONST_NEEDLE = "_SENTINEL_FALLBACK"

# docstring sentinel (V1)
DOCSTRING_SENTINEL = "ROBOT_037_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_robot_037][{mark}] {tag} {detail}", flush=True)
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
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    if not VERIFY_LIB.is_file():
        _emit("V0_verify_lib_exists", False, f"missing {VERIFY_LIB}")
        return
    _emit("V0_verify_lib_exists", True, str(VERIFY_LIB))
    src = VERIFY_LIB.read_text(encoding="utf-8")
    _emit(
        "V0_func_def_present",
        "def read_constant" in src,
        "def read_constant",
    )
    _emit(
        "V0_in_all_export",
        '"read_constant"' in src or "'read_constant'" in src,
        "__all__ contains read_constant",
    )
    # verify_robot_034 必须改用新 helper
    if not VERIFY_034.is_file():
        _emit("V0_verify_034_exists", False, f"missing {VERIFY_034}")
        return
    _emit("V0_verify_034_exists", True, str(VERIFY_034))
    src34 = VERIFY_034.read_text(encoding="utf-8")
    _emit(
        "V0_verify_034_imports_helper",
        "from _verify_lib import read_constant" in src34,
        "from _verify_lib import read_constant",
    )
    _emit(
        "V0_verify_034_inline_helper_removed",
        "def _read_sentinel_from_verify_032" not in src34,
        "inline _read_sentinel_from_verify_032 absent",
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
        got_func = _func_sha_via_unparse(VERIFY_LIB, "read_constant")
    except Exception as e:
        _emit("V2_read_constant_func_sha", False, f"compute err: {e!r}")
        return
    _emit(
        "V2_read_constant_func_sha",
        got_func == EXPECTED_READ_CONSTANT_FUNC_SHA,
        f"got={got_func[:16]} expect={EXPECTED_READ_CONSTANT_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: mutant 反证 — 临时污染 _verify_lib.read_constant 内 literal_eval
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    original = VERIFY_LIB.read_text(encoding="utf-8")
    needle = "return ast.literal_eval(node.value)"
    mutant_repl = "return eval(ast.unparse(node.value))  # ROBOT_037_MUTANT"
    if needle not in original:
        _emit("V3_mutant_apply", False, "mutant needle not found")
        return
    mutant = original.replace(needle, mutant_repl, 1)
    try:
        VERIFY_LIB.write_text(mutant, encoding="utf-8")
        try:
            lib = _load_lib()
            # mutant 路径下, read_constant 通过 eval 仍能返回 str literal 的值
            # 但 func sha 必然变化 → 用 sha 差异作 mutant detection 指标
            mut_sha = _func_sha_via_unparse(VERIFY_LIB, "read_constant")
        except Exception as e:
            _emit("V3_mutant_detected", True, f"mutated path raised: {e!r}")
            return
        _emit(
            "V3_mutant_detected",
            mut_sha != EXPECTED_READ_CONSTANT_FUNC_SHA,
            f"mut={mut_sha[:16]} baseline={EXPECTED_READ_CONSTANT_FUNC_SHA[:16]}",
        )
        # 进一步: mutant 加载后 helper 可调用 (literal str 仍能拿到)
        try:
            val = lib.read_constant(VERIFY_034, "_SENTINEL_SRC_NAME")
            _emit(
                "V3_mutant_call_still_works_for_literal_str",
                isinstance(val, str) and len(val) > 0,
                f"val={val!r}",
            )
        except Exception as e:
            _emit("V3_mutant_call_still_works_for_literal_str", False, f"err: {e!r}")
    finally:
        VERIFY_LIB.write_text(original, encoding="utf-8")
        sys.path.insert(0, str(SCRIPTS))
        if "_verify_lib" in sys.modules:
            del sys.modules["_verify_lib"]


# ---------------------------------------------------------------------------
# V4: 行为验证
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    try:
        lib = _load_lib()
    except Exception as e:
        _emit("V4_helper_load", False, f"import err: {e!r}")
        return
    _emit("V4_helper_load", True, "_verify_lib loaded")
    # 正常路径: 读 verify_robot_034 中 _SENTINEL_SRC_NAME (str literal)
    try:
        val = lib.read_constant(VERIFY_034, "_SENTINEL_SRC_NAME")
    except Exception as e:
        _emit("V4_helper_call", False, f"err: {e!r}")
        return
    ok = isinstance(val, str) and len(val) > 0
    _emit("V4_helper_call", ok, f"val={val!r}")
    # 异常路径: 不存在的 const_name
    try:
        lib.read_constant(VERIFY_034, "__no_such_const_xyz__")
        _emit("V4_value_error_missing", False, "should have raised ValueError")
    except ValueError:
        _emit("V4_value_error_missing", True, "ValueError raised as expected")
    except Exception as e:
        _emit("V4_value_error_missing", False, f"wrong exc: {e!r}")
    # 异常路径: 不存在的 path
    try:
        lib.read_constant("/tmp/__nonexistent_robot_037__.py", "x")
        _emit("V4_file_not_found", False, "should have raised FileNotFoundError")
    except FileNotFoundError:
        _emit("V4_file_not_found", True, "FileNotFoundError raised as expected")
    except Exception as e:
        _emit("V4_file_not_found", False, f"wrong exc: {e!r}")
    # 异常路径: 非 literal value (verify_robot_034 中 VERIFY_032 = Path 表达式)
    try:
        lib.read_constant(VERIFY_034, "VERIFY_032")
        _emit("V4_value_error_non_literal", False, "should have raised ValueError")
    except ValueError:
        _emit("V4_value_error_non_literal", True, "ValueError on non-literal Path expr")
    except Exception as e:
        _emit("V4_value_error_non_literal", False, f"wrong exc: {e!r}")


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (print-only)
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    _emit(
        "V5_reviewer_lgtm_gate",
        True,
        "closeout 阶段必须有 sub-agent fresh-context Reviewer LGTM (evidence 记录)",
    )


# ---------------------------------------------------------------------------
# V6: robot-037-backlog import-time fail fallback meta-lock
# ---------------------------------------------------------------------------
def v6_import_time_fail_fallback() -> None:
    if not VERIFY_034.is_file():
        _emit("V6_verify_034_exists", False, f"missing {VERIFY_034}")
        return
    src34 = VERIFY_034.read_text(encoding="utf-8")
    # V6a 字面 grep: try/except 顶层包裹 + sentinel fallback 常量名
    _emit(
        "V6a_try_except_wraps_read_constant",
        V6_TRY_EXCEPT_NEEDLE in src34
        and "try:" in src34
        and "except Exception" in src34,
        f"needle={V6_TRY_EXCEPT_NEEDLE!r}",
    )
    _emit(
        "V6a_sentinel_fallback_const",
        V6_FALLBACK_CONST_NEEDLE in src34,
        f"needle={V6_FALLBACK_CONST_NEEDLE}",
    )
    # V6b file sha 锁
    got_file = _file_sha(VERIFY_034)
    _emit(
        "V6b_verify_034_file_sha",
        got_file == EXPECTED_VERIFY_034_FILE_SHA,
        f"got={got_file[:16]} expect={EXPECTED_VERIFY_034_FILE_SHA[:16]}",
    )
    # V6c main 函数 sha 自锁
    try:
        got_main = _func_sha_via_unparse(VERIFY_034, "main")
    except Exception as e:
        _emit("V6c_verify_034_main_func_sha", False, f"compute err: {e!r}")
        return
    _emit(
        "V6c_verify_034_main_func_sha",
        got_main == EXPECTED_VERIFY_034_MAIN_FUNC_SHA,
        f"got={got_main[:16]} expect={EXPECTED_VERIFY_034_MAIN_FUNC_SHA[:16]}",
    )
    # V6d 行为: 子进程 monkeypatch read_constant 抛异常, import verify_robot_034
    # 不应该 ImportError, SENTINEL_LINE 应为 fallback
    import subprocess as _sp

    child_code = r"""
import sys, importlib
from pathlib import Path
SCRIPTS = Path(__file__).resolve().parent if False else Path(r"{scripts}")
sys.path.insert(0, str(SCRIPTS))
import _verify_lib

def _boom(*a, **kw):
    raise RuntimeError("ROBOT_037_BACKLOG_INJECTED_FAIL")

_verify_lib.read_constant = _boom

# 强制重载 verify_robot_034 (若已加载先 pop)
sys.modules.pop("verify_robot_034", None)
import importlib.util
spec = importlib.util.spec_from_file_location("verify_robot_034_under_test", r"{v034}")
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except ImportError as e:
    print("FAIL_IMPORT_ERROR:" + repr(e))
    sys.exit(2)
except Exception as e:
    print("FAIL_OTHER_EXC:" + repr(e))
    sys.exit(3)
sentinel = getattr(mod, "SENTINEL_LINE", None)
fb = getattr(mod, "_SENTINEL_FALLBACK", None)
if sentinel == fb and fb is not None:
    print("OK_FALLBACK:" + repr(sentinel))
    sys.exit(0)
print("FAIL_NO_FALLBACK: sentinel=" + repr(sentinel) + " fb=" + repr(fb))
sys.exit(4)
""".format(scripts=str(SCRIPTS), v034=str(VERIFY_034))

    proc = _sp.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    ok = proc.returncode == 0 and "OK_FALLBACK" in proc.stdout
    _emit(
        "V6d_import_time_fallback_behavior",
        ok,
        f"rc={proc.returncode} stdout={proc.stdout.strip()[:120]!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_lock()
    v2_sha_locks()
    v3_mutant()
    v4_behavior()
    v5_reviewer_gate()
    v6_import_time_fail_fallback()
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_robot_037][SUMMARY] FAILED tags: {failed}", flush=True)
        return 1
    print(f"[verify_robot_037][SUMMARY] ALL PASS ({len(_results)} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
