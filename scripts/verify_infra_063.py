#!/usr/bin/env python3
"""verify_infra_063 V0-V5: Bootstrap helper self-mutant canary 行为锁.

infra-P276-bootstrap-helper-self-mutant-detection (phase-37 #4.37):
P275 引入 ``scripts/bootstrap_verify_self_checker.py`` (V4 sha 自助回填工具),
但缺 mutant 检测 — helper 实现被改坏 (例如 ``compute_self_checker_sha`` 改
``return ""``) 后用户不会立刻发现, 同质 P291 (helper 退化成 noop). 本 feature
给 bootstrap 加 self-mutant canary:

- ``_CANARY_VERIFY_SRC``: 内嵌一段最小合法 verify 脚本字符串
- ``_CANARY_EXPECTED_SHA``: 该 canary v4_behavior 函数 ast.unparse canonical sha
- ``run_canary_self_check()``: 写入 tmp + 算 sha + 与期望比对
- ``--canary`` 子模式: ok=False 时 exit 2

V4 中通过 subprocess 跑一份**真实 mutant 副本** (用 ast 改坏 helper 的 V4-sha
解析函数 → return ""), 期望 mutant_detected=True 且 exit=2; 不是纯合成样本.

INFRA_063_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/bootstrap_verify_self_checker.py`` file sha: EXPECTED_BOOTSTRAP_FILE_SHA
- ``run_canary_self_check`` func sha: EXPECTED_CANARY_FUNC_SHA
- ``_CANARY_EXPECTED_SHA`` 字面值锁: EXPECTED_CANARY_CONST_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: file 存在 / __main__ / --json / --text / --canary 子模式可执行
- V1 docstring sentinel ``INFRA_063_SHA_LOCKS`` + 本脚本 v4_behavior 自锁
- V2 _verify_lib.py file sha + bootstrap helper 整文件 sha
- V3 ``run_canary_self_check`` canonical func sha + ``_CANARY_EXPECTED_SHA`` const lock
- V4 行为 (真实 mutant dogfood):
  - V4.1: 不动 bootstrap 跑 ``--canary``, 期望 mutant_detected=False, exit 0
  - V4.2 (核心 mutant): 复制 bootstrap 到 /tmp, ast 改 ``compute_self_checker_sha``
    body 为 ``return ""``, subprocess 跑 mutant ``--canary``, 期望 exit 2 +
    mutant_detected=True
  - V4.3: ``_CANARY_VERIFY_SRC`` 字符串含 ``def `` 与 ``if __name__`` 子串
  - V4.4: ``_CANARY_EXPECTED_SHA`` 长度==64 且全 hex
- V5 Reviewer LGTM gate (print-only)

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "_verify_lib.py"
BOOTSTRAP = SCRIPTS / "bootstrap_verify_self_checker.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import func_sha_by_name  # noqa: E402

EXPECTED_VERIFY_LIB_FILE_SHA = "5647e74194aff139a45de09e24a62a7ac1417d35905c9bebd668352370708381"
EXPECTED_BOOTSTRAP_FILE_SHA = "01a3099a50b1d61da1accc74e867922e7cc9b79e80620fa50a186e207eb55b97"
EXPECTED_CANARY_FUNC_SHA = "12cef322c3671b196d39781c9ce6568f7f3f224e17a876f282dbd77f40c38c90"
EXPECTED_CANARY_CONST_SHA = "5d611a08c7d9a7df9e79b6da1e1a5aabfb88c9e5fc373917f612f69f3580d249"
EXPECTED_V4_CHECKER_FUNC_SHA = "0e25c9a3a5f2d60378424a80accab4e8955dfacbf495090f38686888183a5d1a"

DOCSTRING_SENTINEL = "INFRA_063_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_063][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_module_const(path: Path, name: str):
    """读取 path 中名为 name 的 module-level 常量字面值 (支持 Assign / AnnAssign).

    无 / 解析失败返回 None.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name and node.value is not None:
                    try:
                        return ast.literal_eval(node.value)
                    except Exception:
                        return None
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == name
                and node.value is not None
            ):
                try:
                    return ast.literal_eval(node.value)
                except Exception:
                    return None
    return None


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_verify_lib_exists", LIB.is_file(), f"path={LIB}")
    _emit("V0_bootstrap_exists", BOOTSTRAP.is_file(), f"path={BOOTSTRAP}")
    if not BOOTSTRAP.is_file():
        return
    src = BOOTSTRAP.read_text(encoding="utf-8")
    _emit(
        "V0_bootstrap_main_guard",
        'if __name__ == "__main__":' in src and "def main(" in src,
        "expect __main__ guard + def main()",
    )
    # 子模式 sanity: 不实跑, 仅检 argparse 接受 --canary / --json
    _emit(
        "V0_bootstrap_canary_flag_declared",
        '"--canary"' in src,
        "expect --canary flag in argparse",
    )
    _emit(
        "V0_bootstrap_json_flag_declared",
        '"--json"' in src,
        "expect --json flag in argparse",
    )
    # text 模式 = 默认 (无 --text 显式; 只要 argparse 仍 print 即可); 检 text 输出
    # 路径关键打印行残留:
    _emit(
        "V0_bootstrap_text_print_path",
        "paste this line back into the script" in src,
        "expect text-mode hint string in helper",
    )


# ---------------------------------------------------------------------------
# V1: docstring sentinel + v4_behavior 自锁
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
        got = func_sha_by_name(self_path, "v4_behavior")
    except Exception as e:
        _emit("V1_self_checker_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_V4_CHECKER_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_self_checker_func_sha",
            False,
            f"placeholder; bump EXPECTED_V4_CHECKER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V1_self_checker_func_sha",
        got == EXPECTED_V4_CHECKER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_V4_CHECKER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V2: file shas
# ---------------------------------------------------------------------------
def v2_file_shas() -> None:
    got_lib = _file_sha(LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit("V2_lib_file_sha", False, f"placeholder; bump={got_lib}")
    else:
        _emit(
            "V2_lib_file_sha",
            got_lib == EXPECTED_VERIFY_LIB_FILE_SHA,
            f"got={got_lib[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
        )
    got_boot = _file_sha(BOOTSTRAP)
    if EXPECTED_BOOTSTRAP_FILE_SHA == "__BUMP_ME__":
        _emit("V2_bootstrap_file_sha", False, f"placeholder; bump={got_boot}")
    else:
        _emit(
            "V2_bootstrap_file_sha",
            got_boot == EXPECTED_BOOTSTRAP_FILE_SHA,
            f"got={got_boot[:16]} expect={EXPECTED_BOOTSTRAP_FILE_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V3: helper func sha + canary const lock
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(BOOTSTRAP, "run_canary_self_check")
    except Exception as e:
        _emit("V3_canary_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_CANARY_FUNC_SHA == "__BUMP_ME__":
        _emit("V3_canary_func_sha", False, f"placeholder; bump={got}")
    else:
        _emit(
            "V3_canary_func_sha",
            got == EXPECTED_CANARY_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_CANARY_FUNC_SHA[:16]}",
        )
    # _CANARY_EXPECTED_SHA 字面值锁: 解析 bootstrap AST 取该常量值
    canary_const_val = _read_module_const(BOOTSTRAP, "_CANARY_EXPECTED_SHA")
    _emit(
        "V3_canary_const_lock",
        canary_const_val == EXPECTED_CANARY_CONST_SHA,
        f"got={str(canary_const_val)[:16]} expect={EXPECTED_CANARY_CONST_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为 (真实 mutant dogfood)
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # V4.1: clean canary
    try:
        proc = subprocess.run(
            [sys.executable, str(BOOTSTRAP), "--canary"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except Exception as e:
        _emit("V4.1_clean_canary", False, f"subprocess err: {e!r}")
        return
    _emit(
        "V4.1_clean_canary_exit0",
        proc.returncode == 0 and "mutant_detected=False" in proc.stdout,
        f"rc={proc.returncode} stdout_tail={proc.stdout[-160:]!r}",
    )

    # V4.2: real mutant — copy bootstrap, ast-rewrite compute_self_checker_sha
    # body to `return ""`, then run --canary expecting exit 2 + mutant_detected=True.
    src = BOOTSTRAP.read_text(encoding="utf-8")
    tree = ast.parse(src)
    mutated = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "compute_self_checker_sha":
            node.body = [ast.Return(value=ast.Constant(value=""))]
            mutated = True
            break
    if not mutated:
        _emit("V4.2_mutant_canary", False, "could not find compute_self_checker_sha in AST")
        return
    mutant_src = ast.unparse(tree)
    with tempfile.TemporaryDirectory() as td:
        mutant_path = Path(td) / "bootstrap_mutant.py"
        mutant_path.write_text(mutant_src, encoding="utf-8")
        # 复制 _verify_lib 同目录 (mutant 文件 import _verify_lib 走相对路径
        # `sys.path.insert(Path(__file__).resolve().parent)`)
        # 简单方案: 直接把 _verify_lib.py 复制到 td
        (Path(td) / "_verify_lib.py").write_bytes(LIB.read_bytes())
        try:
            proc_m = subprocess.run(
                [sys.executable, str(mutant_path), "--canary"],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except Exception as e:
            _emit("V4.2_mutant_canary", False, f"subprocess err: {e!r}")
            return
        _emit(
            "V4.2_mutant_exit2",
            proc_m.returncode == 2,
            f"rc={proc_m.returncode} (expect 2) stderr_tail={proc_m.stderr[-120:]!r}",
        )
        _emit(
            "V4.2_mutant_detected_true",
            "mutant_detected=True" in proc_m.stdout,
            f"stdout_tail={proc_m.stdout[-200:]!r}",
        )

    # V4.3: _CANARY_VERIFY_SRC 字符串内嵌完整性
    canary_src_val = _read_module_const(BOOTSTRAP, "_CANARY_VERIFY_SRC")
    _emit(
        "V4.3_canary_src_has_def",
        isinstance(canary_src_val, str) and "def " in canary_src_val,
        f"canary_src_val starts with={(canary_src_val or '')[:40]!r}",
    )
    _emit(
        "V4.3_canary_src_has_main_guard",
        isinstance(canary_src_val, str) and "if __name__" in canary_src_val,
        "expect 'if __name__' substring in _CANARY_VERIFY_SRC",
    )

    # V4.4: _CANARY_EXPECTED_SHA 长度 + hex
    canary_const_val = _read_module_const(BOOTSTRAP, "_CANARY_EXPECTED_SHA")
    is_hex = isinstance(canary_const_val, str) and bool(re.fullmatch(r"[0-9a-f]+", canary_const_val))
    _emit(
        "V4.4_canary_const_len64_hex",
        isinstance(canary_const_val, str) and len(canary_const_val) == 64 and is_hex,
        f"len={len(canary_const_val) if isinstance(canary_const_val, str) else 'NA'} hex={is_hex}",
    )

    # V4.5: 验证 --canary stdout JSON-like 字段存在 (parse expected/actual line)
    # 与 V4.1 共享一次 stdout, 确认 expected= / actual= 行格式
    _emit(
        "V4.5_canary_stdout_has_expected_actual",
        "expected=" in proc.stdout and "actual" in proc.stdout,
        "expect 'expected=' and 'actual' substrings in canary stdout",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    _emit(
        "V5_reviewer_lgtm_gate",
        True,
        "closeout 阶段必须有 sub-agent fresh-context Reviewer LGTM (evidence 记录)",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_lock()
    v2_file_shas()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if "--json" in sys.argv:
        print(json.dumps({"results": [(t, ok, d) for t, ok, d in _results]}, indent=2))
    if failed:
        print(f"[verify_infra_063][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_063][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
