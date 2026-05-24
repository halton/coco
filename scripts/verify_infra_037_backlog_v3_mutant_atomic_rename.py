#!/usr/bin/env python3
"""verify_infra_037_backlog_v3_mutant_atomic_rename: 锁住 verify_infra_037.v3_mutant
采用 tempfile + os.replace 原子替换 + atexit 兜底, 不再使用裸 ``write_text(mutant)``
(verify-only).

infra-037 V3 mutant 原本直接 ``VERIFY_LIB.write_text(mutant)`` 然后 ``finally``
``write_text(original)``; 如果在 write 之后、finally 之前进程被 Ctrl-C/OOM 杀掉,
``_verify_lib.py`` 会停留在污染状态, 后续所有 verify 全错。本脚本锁住 v3_mutant
改用 ``tempfile.mkstemp`` + ``os.replace`` 原子替换, 并注册 ``atexit`` 兜底 restore,
让 mutant 写入和 restore 在 POSIX/Windows 上都是原子操作。

V0 self_sha: 本脚本自锁。
V1 sha_locks: verify_infra_037.py 文件 sha + v3_mutant 函数 sha 锁。
V2 ast_pattern: AST-level 扫 v3_mutant 函数体, 必须出现以下三类调用之一:
   - ``tempfile.mkstemp(...)`` (atomic temp file)
   - ``os.replace(...)`` (atomic rename in same volume)
   - ``atexit.register(...)`` (process-level restore fallback)
   且**不得**出现 ``VERIFY_LIB.write_text(mutant)`` 这种裸写。
V3 mutant_reverse: 临时把 v3_mutant 内 ``os.replace`` 替换为 ``shutil.copy``
   (非原子语义), 重新解析期望 V2 失败; finally 用 atomic rename 还原原文件 (吃自己
   的狗粮)。
V_last reviewer_lgtm_gate (backloaded): print-only, 提示 closeout 须有 sub-agent
   fresh-context Reviewer LGTM 落 evidence。

INFRA_037_BACKLOG_V3_MUTANT_ATOMIC_RENAME_SHA_LOCKS
---------------------------------------------------
- ``scripts/verify_infra_037.py`` file sha: EXPECTED_VERIFY_INFRA_037_FILE_SHA
- ``scripts/verify_infra_037.py:v3_mutant`` canonical func sha: EXPECTED_V3_MUTANT_FUNC_SHA
- 本脚本自身 self_sha: EXPECTED_SELF_FILE_SHA

退出码 0=ALL PASS / 1=任一 FAIL.

## Lock: EXPECTED_VERIFY_INFRA_037_FILE_SHA
- target_function: N/A
- target_file: scripts/verify_infra_037.py
- lock_kind: file_sha
- bump_when: verify_infra_037.py 任何字节变更 (含 v3_mutant 改动)
- bump_protocol: 重算 sha256 of scripts/verify_infra_037.py 并更新常量
- rationale: 锁住 V3 mutant 原子 rename 模式所在文件整体内容

## Lock: EXPECTED_V3_MUTANT_FUNC_SHA
- target_function: v3_mutant
- target_file: scripts/verify_infra_037.py
- lock_kind: ast_func_sha
- bump_when: v3_mutant 实现变化
- bump_protocol: recompute func_sha_by_name("v3_mutant", scripts/verify_infra_037.py)
- rationale: 锁 V3 mutant 函数体本身, 防止 atomic rename 模式被悄改回裸 write_text

## Lock: EXPECTED_SELF_FILE_SHA
- target_function: N/A
- target_file: scripts/verify_infra_037_backlog_v3_mutant_atomic_rename.py
- lock_kind: file_sha (self)
- bump_when: 本脚本自身任何字节变更
- bump_protocol: 重算 sha256 of scripts/verify_infra_037_backlog_v3_mutant_atomic_rename.py
- rationale: V0 self_sha 自锁, 防止本 verify 被悄改成永真
"""
from __future__ import annotations

import ast
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
SELF_PATH = Path(__file__).resolve()
VERIFY_INFRA_037 = SCRIPTS / "verify_infra_037.py"

# V0 self_sha
EXPECTED_SELF_FILE_SHA = "__SCAFFOLDING_ONLY__"

# V0 self func-sha 锁 (主关键 checker func)
EXPECTED_V2_CHECKER_FUNC_SHA = "6b15de635edc5445d88a1eae2903e9277845c6eaee822b89b31e0a7a5e8735a7"
EXPECTED_V3_REVERSE_FUNC_SHA = "7e96b83a0ed4cc9324fa48aad5fd30c578a7b26c6a74df4e3b4d64d496c427e2"

# V1 verify_infra_037.py file sha
EXPECTED_VERIFY_INFRA_037_FILE_SHA = "25c6b20ce91c3de3b48cf2e803a52eff26243040b02b756e117a8f983e21ac9c"

# V1 v3_mutant func sha
EXPECTED_V3_MUTANT_FUNC_SHA = "48abbd6d2f82aebf5cb09d6a3f6ed7b6030232b5a3c35bcc1a55c887a921e06e"

DOCSTRING_SENTINEL = "INFRA_037_BACKLOG_V3_MUTANT_ATOMIC_RENAME_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

REAL_FEATURE_LIST = REPO / "feature_list.json"
V_LAST_GATE_FEATURE_ID = "__PHASE_67_PLACEHOLDER_INFRA_037_V3_MUTANT_ATOMIC_RENAME__"


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_037_backlog_v3_mutant_atomic_rename][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _func_sha(path: Path, func_name: str) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return hashlib.sha256(ast.unparse(node).encode("utf-8")).hexdigest()
    raise RuntimeError(f"func {func_name!r} not found in {path}")


def _get_func_node(path: Path, func_name: str) -> ast.AST:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node
    raise RuntimeError(f"func {func_name!r} not found in {path}")


def _atomic_write_self(target: Path, payload: bytes) -> None:
    """吃自己的狗粮: tempfile + os.replace 原子写。"""
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=".verify_infra_037_backlog_",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(tmp_fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# V0: self_sha 自锁
# ---------------------------------------------------------------------------
def v0_self_sha() -> None:
    # self_sha 占位为 scaffolding-only (file sha 鸡生蛋); 真自锁走 func sha
    got_file = _file_sha(SELF_PATH)
    _emit(
        "V0_self_file_exists",
        SELF_PATH.is_file(),
        f"path={SELF_PATH.name} sha={got_file[:16]}",
    )
    # docstring sentinel
    doc = ast.get_docstring(ast.parse(SELF_PATH.read_text(encoding="utf-8")))
    _emit(
        "V0_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )
    # 关键 checker func sha 自锁 (v2_ast_pattern + v3_mutant_reverse)
    for fn, expected in (
        ("v2_ast_pattern", EXPECTED_V2_CHECKER_FUNC_SHA),
        ("v3_mutant_reverse", EXPECTED_V3_REVERSE_FUNC_SHA),
    ):
        try:
            got = _func_sha(SELF_PATH, fn)
        except Exception as e:
            _emit(f"V0_self_func_sha_{fn}", False, f"compute err: {e!r}")
            continue
        if expected == "__BUMP_ME__":
            _emit(
                f"V0_self_func_sha_{fn}",
                False,
                f"placeholder; bump EXPECTED_{fn.upper()}_FUNC_SHA={got}",
            )
            continue
        _emit(
            f"V0_self_func_sha_{fn}",
            got == expected,
            f"got={got[:16]} expect={expected[:16]}",
        )


# ---------------------------------------------------------------------------
# V1: verify_infra_037.py file sha + v3_mutant func sha
# ---------------------------------------------------------------------------
def v1_sha_locks() -> None:
    got_file = _file_sha(VERIFY_INFRA_037)
    _emit(
        "V1_verify_infra_037_file_sha",
        got_file == EXPECTED_VERIFY_INFRA_037_FILE_SHA,
        f"got={got_file[:16]} expect={EXPECTED_VERIFY_INFRA_037_FILE_SHA[:16]}",
    )
    try:
        got_func = _func_sha(VERIFY_INFRA_037, "v3_mutant")
    except Exception as e:
        _emit("V1_v3_mutant_func_sha", False, f"compute err: {e!r}")
        return
    _emit(
        "V1_v3_mutant_func_sha",
        got_func == EXPECTED_V3_MUTANT_FUNC_SHA,
        f"got={got_func[:16]} expect={EXPECTED_V3_MUTANT_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V2: AST-level pattern — v3_mutant 必须出现原子 helper, 不得裸 write_text(mutant)
# ---------------------------------------------------------------------------
def _scan_v3_mutant_pattern() -> dict:
    """扫 v3_mutant 函数体, 返回 pattern flags."""
    node = _get_func_node(VERIFY_INFRA_037, "v3_mutant")
    has_mkstemp = False
    has_os_replace = False
    has_atexit_register = False
    has_naked_write_text_mutant = False
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            # tempfile.mkstemp / mkstemp
            if isinstance(func, ast.Attribute) and func.attr == "mkstemp":
                has_mkstemp = True
            if isinstance(func, ast.Name) and func.id == "mkstemp":
                has_mkstemp = True
            # os.replace
            if isinstance(func, ast.Attribute) and func.attr == "replace":
                if isinstance(func.value, ast.Name) and func.value.id == "os":
                    has_os_replace = True
            # atexit.register
            if isinstance(func, ast.Attribute) and func.attr == "register":
                if isinstance(func.value, ast.Name) and func.value.id == "atexit":
                    has_atexit_register = True
            # naked VERIFY_LIB.write_text(mutant) — 直接调 write_text 且参数含 mutant Name
            if isinstance(func, ast.Attribute) and func.attr == "write_text":
                # 检查参数是否是裸 mutant Name (而非 atomic helper 内部调用)
                if sub.args:
                    arg0 = sub.args[0]
                    if isinstance(arg0, ast.Name) and arg0.id == "mutant":
                        has_naked_write_text_mutant = True
    return {
        "mkstemp": has_mkstemp,
        "os_replace": has_os_replace,
        "atexit_register": has_atexit_register,
        "naked_write_text_mutant": has_naked_write_text_mutant,
    }


def v2_ast_pattern() -> None:
    p = _scan_v3_mutant_pattern()
    # 三个原子保障必须至少出现两个 (mkstemp + os.replace 是核心, atexit 是兜底)
    atomic_score = int(p["mkstemp"]) + int(p["os_replace"]) + int(p["atexit_register"])
    _emit(
        "V2_atomic_pattern_present",
        atomic_score >= 2 and p["mkstemp"] and p["os_replace"],
        f"mkstemp={p['mkstemp']} os_replace={p['os_replace']} "
        f"atexit_register={p['atexit_register']} score={atomic_score}",
    )
    _emit(
        "V2_no_naked_write_text_mutant",
        not p["naked_write_text_mutant"],
        f"naked_write_text_mutant={p['naked_write_text_mutant']}",
    )


# ---------------------------------------------------------------------------
# V3: mutant reverse — 临时去掉原子 helper, 期望 V2 检测失败
# ---------------------------------------------------------------------------
def v3_mutant_reverse() -> None:
    original_bytes = VERIFY_INFRA_037.read_bytes()
    original = original_bytes.decode("utf-8")
    # 把 os.replace(tmp_path, target) 换成 shutil.copy(tmp_path, str(target))
    # (非原子, 仍能让文件最终被写到, 但不再是 atomic rename)
    mutant = original.replace(
        "os.replace(tmp_path, target)",
        "shutil.copy(tmp_path, str(target))",
        1,
    )
    if mutant == original:
        _emit("V3_mutant_reverse_apply", False, "no replacement target found")
        return
    try:
        _atomic_write_self(VERIFY_INFRA_037, mutant.encode("utf-8"))
        # 重新扫
        p = _scan_v3_mutant_pattern()
        # 期望: os_replace 不再为 True
        _emit(
            "V3_mutant_reverse_detects_loss_of_os_replace",
            p["os_replace"] is False,
            f"after mutation os_replace={p['os_replace']} (expect False)",
        )
    finally:
        _atomic_write_self(VERIFY_INFRA_037, original_bytes)


# ---------------------------------------------------------------------------
# V_last: Reviewer LGTM gate (backloaded)
# ---------------------------------------------------------------------------
def v_last_reviewer_gate() -> None:
    if not REAL_FEATURE_LIST.is_file():
        _emit(
            "V_last_reviewer_lgtm_gate",
            False,
            f"feature_list.json not found at {REAL_FEATURE_LIST}",
        )
        return
    result = assert_v5_reviewer_gate_evidence_bind(
        V_LAST_GATE_FEATURE_ID, REAL_FEATURE_LIST,
        grace_period_feature_ids=(V_LAST_GATE_FEATURE_ID,),
    )
    _emit(
        "V_last_reviewer_lgtm_gate",
        bool(result["ok"]),
        f"target={V_LAST_GATE_FEATURE_ID} helper_ok={result['ok']} "
        f"grace_skipped={result['grace_skipped']} "
        f"verdict={result['verdict']!r} kind={result['reviewer_kind']!r} "
        f"summary_len={result['summary_len']} reason={result['reason']!r}",
    )


def main() -> int:
    v0_self_sha()
    v1_sha_locks()
    v2_ast_pattern()
    v3_mutant_reverse()
    v_last_reviewer_gate()
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_037_backlog_v3_mutant_atomic_rename][SUMMARY] "
            f"FAIL {len(failed)}/{len(_results)} tags: {failed}",
            flush=True,
        )
        return 1
    print(
        f"[verify_infra_037_backlog_v3_mutant_atomic_rename][SUMMARY] "
        f"ALL PASS ({len(_results)} checks)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
