#!/usr/bin/env python3
"""verify_infra_101: bump_reverse_sha_lock 支持 _verify_lib.py 作为 target (verify-only).

infra-V6-bump-helper-lib-target (phase-52 #4.52):
扩展 ``scripts/bump_reverse_sha_lock.py`` 让 NNN-agnostic fallback target
(``scripts/_verify_lib.py``) 也能成为反向 sha lock 的 target。

P293/P294 cascade 中, _verify_lib.py 自身改动导致大量 ``EXPECTED_LIB_FILE_SHA`` /
``EXPECTED_VERIFY_LIB_FILE_SHA`` 反向锁需要手动 sed 刷新, helper 没覆盖这种
target — 因为原 ``_candidates_for`` 只识别 ``VERIFY_<NNN>`` ↔ ``_NNN.py``
配对, 而 ``_verify_lib.py`` 没有 NNN 后缀。

INFRA_101_SHA_LOCKS
-------------------
- ``scripts/bump_reverse_sha_lock.py`` file sha: EXPECTED_BUMP_FILE_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA

校验层级 (V0-V5):

- V0 scaffolding (×3): helper 存在 / target lib 存在 / _candidates_for 顶层符号
- V1 docstring sentinel ``INFRA_101_SHA_LOCKS`` 自锁
- V2 file sha 双锁 (helper + lib)
- V3 行为 — NNN-agnostic 分支:
  - V3_locks_nonempty: find_locks_for_target(_verify_lib.py) 至少 5 条
    (实际仓库有 40+ ``EXPECTED_*LIB_FILE_SHA`` 反向锁)
  - V3_const_name_whitelist: 所有返回 lock 的 const_name 仅限
    ``EXPECTED_LIB_FILE_SHA`` 或 ``EXPECTED_VERIFY_LIB_FILE_SHA``
    (排除 func-level sha 假阳性)
  - V3_dry_run_no_disk_change: dry-run 不改盘
  - V3_dry_run_rc_ok: rc ∈ {0, 3}
- V4 行为 — NNN-based 回归 (确认旧路径未破坏):
  - V4_nnn_lock_found: find_locks_for_target(verify_robot_025.py) 至少 1 条
    (verify_robot_027 的 VERIFY_025_EXPECTED_SHA)
- V5 Reviewer LGTM gate (grace_period 兜底)

退出码 0=ALL PASS / 2=任一 FAIL.

## Lock: EXPECTED_BUMP_FILE_SHA
- target_function: N/A
- target_file: scripts/bump_reverse_sha_lock.py
- lock_kind: file_sha
- bump_when: scripts/bump_reverse_sha_lock.py 文件 sha256 变化 (任何字节改动)
- bump_protocol: 重算 sha256 of scripts/bump_reverse_sha_lock.py 并更新常量
- rationale: 锁 helper 整体, 防 _candidates_for NNN-agnostic 分支被悄改导致 lib target 反向锁断链
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
BUMP = SCRIPTS / "bump_reverse_sha_lock.py"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    verify_summary_exit,
)

# infra-101 sha lock 常量 (V2)
EXPECTED_BUMP_FILE_SHA = "b85d7dda59abe58498bbbcfa206bace654868a112a9619c304ba563533f45b9e"
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "43d352534301242f13ef33909cdba2585f8f18734d9e052f2e0de0d075955d75"
)

DOCSTRING_SENTINEL = "INFRA_101_SHA_LOCKS"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-V6-bump-helper-lib-target"

# NNN-agnostic 分支允许的 const_name 白名单
_ALLOWED_LIB_LOCK_NAMES = ("EXPECTED_LIB_FILE_SHA", "EXPECTED_VERIFY_LIB_FILE_SHA")

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_101][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_bump_exists", BUMP.is_file(), f"path={BUMP.relative_to(REPO)}")
    _emit("V0_verify_lib_exists", VERIFY_LIB.is_file(), f"path={VERIFY_LIB.relative_to(REPO)}")
    src = BUMP.read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_funcs = {
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    _emit(
        "V0_candidates_for_top_func",
        "_candidates_for" in top_funcs,
        f"top-level has _candidates_for",
    )


# ---------------------------------------------------------------------------
# V1: docstring sentinel
# ---------------------------------------------------------------------------
def v1_docstring_sentinel() -> None:
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8")))
    _emit(
        "V1_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )


# ---------------------------------------------------------------------------
# V2: 双 file sha 锁
# ---------------------------------------------------------------------------
def v2_file_sha() -> None:
    got_bump = _file_sha(BUMP)
    _emit(
        "V2_bump_file_sha",
        got_bump == EXPECTED_BUMP_FILE_SHA,
        f"got={got_bump[:16]} expect={EXPECTED_BUMP_FILE_SHA[:16]}",
    )
    got_lib = _file_sha(VERIFY_LIB)
    _emit(
        "V2_verify_lib_file_sha",
        got_lib == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got_lib[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: NNN-agnostic 分支行为
# ---------------------------------------------------------------------------
def v3_behavior_nnn_agnostic() -> None:
    import importlib
    sys.path.insert(0, str(SCRIPTS))
    mod = importlib.import_module("bump_reverse_sha_lock")
    # 模块缓存可能拿到旧版本, 强制 reload
    mod = importlib.reload(mod)

    target = VERIFY_LIB
    locks = mod.find_locks_for_target(target)
    _emit(
        "V3_locks_nonempty",
        len(locks) >= 5,
        f"locks_count={len(locks)} (expect >= 5)",
    )

    # 白名单: 所有匹配到的 const_name 必须在白名单内 (排除 func-level 假阳性)
    bad_names = [
        lk["const_name"] for lk in locks
        if lk["const_name"] not in _ALLOWED_LIB_LOCK_NAMES
    ]
    _emit(
        "V3_const_name_whitelist",
        len(bad_names) == 0,
        f"bad={bad_names[:5] if bad_names else '[]'} "
        f"(allowed={list(_ALLOWED_LIB_LOCK_NAMES)})",
    )

    # dry-run 不改盘
    file_before = _file_sha(BUMP)
    lib_before = _file_sha(VERIFY_LIB)
    rc, _report = mod.run_bump(target, dry_run=True)
    file_after = _file_sha(BUMP)
    lib_after = _file_sha(VERIFY_LIB)
    _emit(
        "V3_dry_run_no_disk_change",
        file_before == file_after and lib_before == lib_after,
        f"bump_changed={file_before != file_after} lib_changed={lib_before != lib_after}",
    )
    _emit(
        "V3_dry_run_rc_ok",
        rc in (0, 3),
        f"rc={rc} (0=ok/no-op, 3=no locks)",
    )


# ---------------------------------------------------------------------------
# V4: NNN-based 回归 (确认旧路径未破坏)
# ---------------------------------------------------------------------------
def v4_behavior_nnn_based_regression() -> None:
    import importlib
    sys.path.insert(0, str(SCRIPTS))
    mod = importlib.import_module("bump_reverse_sha_lock")

    target = SCRIPTS / "verify_robot_025.py"
    if not target.is_file():
        _emit("V4_nnn_target_exists", False, f"target missing: {target}")
        return
    _emit("V4_nnn_target_exists", True, f"target={target.relative_to(REPO)}")

    locks = mod.find_locks_for_target(target)
    _emit(
        "V4_nnn_lock_found",
        len(locks) >= 1,
        f"locks_count={len(locks)}",
    )
    has_025 = any("VERIFY_025" in lk["const_name"] for lk in locks)
    _emit(
        "V4_nnn_lock_has_verify_025",
        has_025,
        f"const_names={[lk['const_name'] for lk in locks]}",
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


def main() -> None:
    v0_scaffolding()
    v1_docstring_sentinel()
    v2_file_sha()
    v3_behavior_nnn_agnostic()
    v4_behavior_nnn_based_regression()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(f"[verify_infra_101][SUMMARY] FAIL {failed}/{total}: {names}", flush=True)
    else:
        print(f"[verify_infra_101][SUMMARY] ALL PASS ({total} checks)", flush=True)
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
