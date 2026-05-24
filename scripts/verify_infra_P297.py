#!/usr/bin/env python3
"""verify_infra_P297: bootstrap canary 参数化多种 mutant 形式行为锁.

infra-P297-canary-mutant-parametrize (phase-67 #19):
来源 P276 Reviewer finding: ``verify_infra_063`` V4.2 仅以一种 mutant 形式
(``return ""``) 检验 ``bootstrap_verify_self_checker.py:compute_self_checker_sha``
是否会被 canary 检出. 这无法覆盖其它常见 "helper 退化成 noop" 的绕过手段, 例如:

  (a) ``return ""``             — 原 P276 mutant (Empty string)
  (b) ``return None``           — 退化为 None (隐式 falsy)
  (c) ``raise RuntimeError(...)`` — 抛异常退化
  (d) 哈希算法被换成弱算法 (e.g. ``hashlib.md5``) — 算法降级 mutant

本 feature 不动 ``scripts/_verify_lib.py`` / ``scripts/bootstrap_verify_self_checker.py``,
而是新建独立 verify 脚本, 用 AST mutate ``compute_self_checker_sha`` 为以上 4 种
mutant, subprocess 跑 mutant ``--canary``, 期望全部 ``rc==2`` + stdout 含
``mutant_detected=True``. 对原 helper 不可见、对 mutant detect 提供更广覆盖.

INFRA_P297_DOC_SHA_LOCKS
------------------------
- ``scripts/_verify_lib.py`` file sha:                 EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/bootstrap_verify_self_checker.py`` file sha: EXPECTED_BOOTSTRAP_FILE_SHA
- self ``v4_behavior_parametrized`` func sha:           EXPECTED_V4_FUNC_SHA

校验层级 (V0-V5):

- V0_scaffolding              : bootstrap / _verify_lib 文件存在
- V1_docstring_sentinel       : INFRA_P297_DOC_SHA_LOCKS 自锁
- V1_self_v4_func_sha         : 本脚本 ``v4_behavior_parametrized`` canonical func sha 锁
- V2_lib_file_sha             : _verify_lib.py 整文件 sha
- V2_bootstrap_file_sha       : bootstrap_verify_self_checker.py 整文件 sha
- V3_clean_canary             : 不动 bootstrap 跑 --canary 期望 rc=0
- V4_mutant_*                 : 4 种 mutant 形式各跑 --canary, 期望 rc=2 +
                                mutant_detected=True
  - V4_mutant_return_empty    : compute_self_checker_sha → ``return ""``
  - V4_mutant_return_none     : compute_self_checker_sha → ``return None``
  - V4_mutant_raise           : compute_self_checker_sha → ``raise RuntimeError(...)``
  - V4_mutant_weak_hash       : compute_self_checker_sha 改算法 (替换函数体为
                                ``return hashlib.md5(verify_script.read_bytes()).hexdigest()``)
- V5_reviewer_lgtm_gate       : Reviewer fresh-context LGTM evidence bind (backloaded)

退出码 0=ALL PASS / 2=任一 FAIL.

## Lock: EXPECTED_BOOTSTRAP_FILE_SHA
- target_function: N/A
- target_file: scripts/bootstrap_verify_self_checker.py
- lock_kind: file_sha
- bump_when: bootstrap_verify_self_checker.py 文件 sha256 变化
- bump_protocol: 重算 sha256 of scripts/bootstrap_verify_self_checker.py 并更新常量
- rationale: V2 锁 bootstrap helper 整体, 防 V4 mutant 注入参考的源文件被悄改
"""
from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P297-canary-mutant-parametrize"
LIB = SCRIPTS / "_verify_lib.py"
BOOTSTRAP = SCRIPTS / "bootstrap_verify_self_checker.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_VERIFY_LIB_FILE_SHA = (
    "1990a9b61d3b14c9b827a23188a46001be2a208234352e1bc7af955285578f50"
)
EXPECTED_BOOTSTRAP_FILE_SHA = (
    "e5c85fe60ce70711c3d351da78b8d728867b5b954c022220ad53a8641fdee229"
)
EXPECTED_V4_FUNC_SHA = (
    "fb715fae39b260a1883a64a76434e15a898eecf7367eb321cf16ed5fa4652721"
)

DOCSTRING_SENTINEL = "INFRA_P297_DOC_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P297][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_verify_lib_exists", LIB.is_file(), f"path={LIB}")
    _emit("V0_bootstrap_exists", BOOTSTRAP.is_file(), f"path={BOOTSTRAP}")


# ---------------------------------------------------------------------------
# V1: docstring sentinel + self func sha lock
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
        got = func_sha_by_name(self_path, "v4_behavior_parametrized")
    except Exception as e:
        _emit("V1_self_v4_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_V4_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_self_v4_func_sha",
            False,
            f"placeholder; bump EXPECTED_V4_FUNC_SHA={got}",
        )
        return
    _emit(
        "V1_self_v4_func_sha",
        got == EXPECTED_V4_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_V4_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V2: file shas
# ---------------------------------------------------------------------------
def v2_file_shas() -> None:
    got_lib = _file_sha(LIB)
    _emit(
        "V2_lib_file_sha",
        got_lib == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got_lib[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )
    got_boot = _file_sha(BOOTSTRAP)
    _emit(
        "V2_bootstrap_file_sha",
        got_boot == EXPECTED_BOOTSTRAP_FILE_SHA,
        f"got={got_boot[:16]} expect={EXPECTED_BOOTSTRAP_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: clean canary baseline
# ---------------------------------------------------------------------------
def v3_clean_canary() -> None:
    try:
        proc = subprocess.run(
            [sys.executable, str(BOOTSTRAP), "--canary"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except Exception as e:
        _emit("V3_clean_canary", False, f"subprocess err: {e!r}")
        return
    _emit(
        "V3_clean_canary_rc0",
        proc.returncode == 0,
        f"rc={proc.returncode} stdout_tail={proc.stdout[-160:]!r}",
    )
    _emit(
        "V3_clean_canary_not_mutant",
        "mutant_detected=False" in proc.stdout,
        f"stdout_tail={proc.stdout[-200:]!r}",
    )


# ---------------------------------------------------------------------------
# V4: parametrized mutant — 4 forms, all expected rc=2 + mutant_detected=True
# ---------------------------------------------------------------------------
def _mutate_return_empty(node: ast.FunctionDef) -> None:
    """compute_self_checker_sha body 替换为 return ''"""
    node.body = [ast.Return(value=ast.Constant(value=""))]


def _mutate_return_none(node: ast.FunctionDef) -> None:
    """compute_self_checker_sha body 替换为 return None"""
    node.body = [ast.Return(value=ast.Constant(value=None))]


def _mutate_raise(node: ast.FunctionDef) -> None:
    """compute_self_checker_sha body 替换为 raise RuntimeError('mutated')"""
    node.body = [
        ast.Raise(
            exc=ast.Call(
                func=ast.Name(id="RuntimeError", ctx=ast.Load()),
                args=[ast.Constant(value="mutated")],
                keywords=[],
            ),
            cause=None,
        )
    ]


def _mutate_weak_hash(node: ast.FunctionDef) -> None:
    """compute_self_checker_sha body 替换为 weak-hash (md5) 算法.

    return hashlib.md5(verify_script.read_bytes()).hexdigest()
    """
    # import hashlib + return md5(verify_script.read_bytes()).hexdigest()
    import_stmt = ast.Import(names=[ast.alias(name="hashlib", asname=None)])
    ret_stmt = ast.Return(
        value=ast.Call(
            func=ast.Attribute(
                value=ast.Call(
                    func=ast.Attribute(
                        value=ast.Name(id="hashlib", ctx=ast.Load()),
                        attr="md5",
                        ctx=ast.Load(),
                    ),
                    args=[
                        ast.Call(
                            func=ast.Attribute(
                                value=ast.Name(id="verify_script", ctx=ast.Load()),
                                attr="read_bytes",
                                ctx=ast.Load(),
                            ),
                            args=[],
                            keywords=[],
                        )
                    ],
                    keywords=[],
                ),
                attr="hexdigest",
                ctx=ast.Load(),
            ),
            args=[],
            keywords=[],
        )
    )
    node.body = [import_stmt, ret_stmt]


_MUTANTS: List[Tuple[str, Callable[[ast.FunctionDef], None]]] = [
    ("return_empty", _mutate_return_empty),
    ("return_none", _mutate_return_none),
    ("raise", _mutate_raise),
    ("weak_hash", _mutate_weak_hash),
]


def _run_mutant(mutator: Callable[[ast.FunctionDef], None]) -> Tuple[int, str, str]:
    """复制 bootstrap → tmp, mutate compute_self_checker_sha, 跑 --canary.

    Returns: (returncode, stdout, stderr)
    """
    src = BOOTSTRAP.read_text(encoding="utf-8")
    tree = ast.parse(src)
    mutated = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "compute_self_checker_sha":
            mutator(node)
            mutated = True
            break
    if not mutated:
        return (-1, "", "could not find compute_self_checker_sha in AST")
    ast.fix_missing_locations(tree)
    mutant_src = ast.unparse(tree)
    with tempfile.TemporaryDirectory() as td:
        mutant_path = Path(td) / "bootstrap_mutant.py"
        mutant_path.write_text(mutant_src, encoding="utf-8")
        # 复制 _verify_lib.py 同目录, 让 mutant subprocess import 走得通
        (Path(td) / "_verify_lib.py").write_bytes(LIB.read_bytes())
        try:
            proc = subprocess.run(
                [sys.executable, str(mutant_path), "--canary"],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except Exception as e:
            return (-1, "", f"subprocess err: {e!r}")
        return (proc.returncode, proc.stdout, proc.stderr)


def v4_behavior_parametrized() -> None:
    """V4 核心: 4 种 mutant 全部期望 rc=2 + mutant_detected=True."""
    for name, mutator in _MUTANTS:
        rc, out, err = _run_mutant(mutator)
        _emit(
            f"V4_mutant_{name}_rc2",
            rc == 2,
            f"rc={rc} (expect 2) stderr_tail={err[-120:]!r}",
        )
        _emit(
            f"V4_mutant_{name}_detected",
            "mutant_detected=True" in out,
            f"stdout_tail={out[-200:]!r}",
        )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
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


def main() -> int:
    v0_scaffolding()
    v1_self_lock()
    v2_file_shas()
    v3_clean_canary()
    v4_behavior_parametrized()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_P297][SUMMARY] FAIL {len(failed)}/{total}: {failed}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_P297][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(len(failed))
    return 0  # unreachable; verify_summary_exit raises SystemExit


if __name__ == "__main__":
    sys.exit(main())
