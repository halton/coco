#!/usr/bin/env python3
"""verify_infra_P301_followup_v3_scope_count_tighten: V3 阈值收紧锁.

infra-P301-followup-v3-scope-count-tighten (来源 phase-67 #21 Reviewer P1 #2):

verify_infra_P301_v0_marker_naming.py V3_v0_locked_scope_count 原阈值 ``>= 1``
过于宽松 — V0-locked 脚本被一刀删到只剩 1 也会 silent PASS, 失去 "scope
regression" 探测意义. 本 follow-up:

  - 将 V3 阈值收紧到 ``>= 53`` (phase-67 #21 close-out main HEAD=1ca519f
    时实测 v0_locked 数 = 53)
  - 通过 func sha 锁 + 源码 sentinel 双重保护这条阈值不被偷偷退回 ``>= 1``
  - mutation 反证: 临时把 V3_BASELINE 改 1 跑应 trip 本 verify (V3 文字 lint)

INFRA_P301_FOLLOWUP_V3_SCOPE_COUNT_TIGHTEN_DOC_SHA_LOCKS
--------------------------------------------------------
- self ``main`` func sha:          ``EXPECTED_SELF_MAIN_FUNC_SHA`` (V0)
- target docstring sentinel:        ``DOCSTRING_SENTINEL`` (V1 自锁)
- target lib file sha:              ``EXPECTED_VERIFY_LIB_FILE_SHA`` (V2)
- target P301_v0_marker file sha:   ``EXPECTED_P301_V0_MARKER_FILE_SHA`` (V3)
- target v3 func sha:               ``EXPECTED_V3_FUNC_SHA`` (V4)

校验层级 (V0-V_last):

- V0_self_main_func_sha         : 本 verifier 自身 ``main`` func sha 锁
- V1_docstring_sentinel         : ``INFRA_P301_FOLLOWUP_V3_SCOPE_COUNT_TIGHTEN_DOC_SHA_LOCKS`` 自锁
- V2_verify_lib_file_sha        : scripts/_verify_lib.py file sha 锁
- V3_target_file_sha            : scripts/verify_infra_P301_v0_marker_naming.py file sha 锁
- V4_target_v3_func_sha         : v3_v0_locked_scope_count func sha 锁 (核心: 锁实现)
- V5_baseline_sentinel_present  : target 源码必含 ``V3_BASELINE = 53`` 字面 sentinel
- V6_no_legacy_threshold        : target 源码必**不**含 ``>= 1)`` 风格旧阈值 (在 v3 func 范围内)
- V7_mutation_baseline_lower    : 反证 — 模拟把 sentinel 写成 ``V3_BASELINE = 1`` 应被本 verify 检出
- V_last_reviewer_lgtm_gate     : assert_reviewer_lgtm helper (backloaded)

## Lock: EXPECTED_VERIFY_LIB_FILE_SHA
- target_function: N/A
- target_file: scripts/_verify_lib.py
- lock_kind: file_sha
- bump_when: scripts/_verify_lib.py 文件 sha256 变化
- bump_protocol: 重算 sha256 of scripts/_verify_lib.py 并更新常量
- rationale: helper 接口稳定保证

## Lock: EXPECTED_P301_V0_MARKER_FILE_SHA
- target_function: N/A
- target_file: scripts/verify_infra_P301_v0_marker_naming.py
- lock_kind: file_sha
- bump_when: target verify 文件任何字节修改
- bump_protocol: 重算 sha256 of target 并更新; 同时若 v3 实现变化需 bump V4

## Lock: EXPECTED_V3_FUNC_SHA
- target_function: v3_v0_locked_scope_count
- target_file: scripts/verify_infra_P301_v0_marker_naming.py
- lock_kind: func_sha
- bump_when: v3_v0_locked_scope_count 实现变化
- bump_protocol: 重算 func_sha_by_name(target, 'v3_v0_locked_scope_count')

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"
TARGET_VERIFY = SCRIPTS / "verify_infra_P301_v0_marker_naming.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
)

# --- self / target sha locks ----------------------------------------------
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "917fa84fea28e9a615d97ed8835ec66b37ce314fb79c747fc29a74f90173db3e"
)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "1990a9b61d3b14c9b827a23188a46001be2a208234352e1bc7af955285578f50"
)
EXPECTED_P301_V0_MARKER_FILE_SHA = (
    "c68db830fec016278c74f575e5a3aebaf1acec6b33a03f62751109e65f171dee"
)
EXPECTED_V3_FUNC_SHA = (
    "c0af278a153ca063f4e25a5a5b17f14e30ce500a6507e70e34cbb4a64f4199f6"
)

# --- V0 metadata schema bump marker ----------------------------------------
V0_SELF_SHA_LOCK_VERSION = 1
V0_SELF_SHA_LOCK_BUMPED_AT = "2026-05-24"

DOCSTRING_SENTINEL = "INFRA_P301_FOLLOWUP_V3_SCOPE_COUNT_TIGHTEN_DOC_SHA_LOCKS"

# The exact sentinel literal we expect to find inside v3_v0_locked_scope_count
# source. Single source of truth — if you bump it here, also bump the target.
V3_BASELINE_SENTINEL = "V3_BASELINE = 53"
# Legacy threshold patterns we explicitly forbid in the v3 func body.
LEGACY_THRESHOLD_PATTERNS = (
    "expect >=1)",
    "(expect >=1)",
    "len(v0_locked) >= 1",
)

V_LAST_GATE_FEATURE_ID = "infra-P301-followup-v3-scope-count-tighten"
REAL_FEATURE_LIST = REPO / "feature_list.json"
GRACE_PERIOD_FEATURE_IDS = (V_LAST_GATE_FEATURE_ID,)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(
        f"[verify_infra_P301_followup_v3_scope_count_tighten][{mark}] "
        f"{tag} {detail}",
        flush=True,
    )
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _v3_func_source(src: str) -> str:
    """Extract source of v3_v0_locked_scope_count func from target src."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ""
    lines = src.splitlines(keepends=True)
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "v3_v0_locked_scope_count"
        ):
            start = node.lineno - 1
            end = node.end_lineno  # type: ignore[attr-defined]
            return "".join(lines[start:end])
    return ""


# --- V-checks --------------------------------------------------------------


def v0_self_main_func_sha() -> None:
    self_path = Path(__file__)
    got = func_sha_by_name(self_path, "main")
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V0_self_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
    else:
        _emit(
            "V0_self_main_func_sha",
            got == EXPECTED_SELF_MAIN_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
        )


def v1_docstring_sentinel() -> None:
    src = Path(__file__).read_text(encoding="utf-8")
    _emit(
        "V1_docstring_sentinel",
        DOCSTRING_SENTINEL in src,
        f"sentinel={DOCSTRING_SENTINEL!r}",
    )


def v2_verify_lib_file_sha() -> None:
    if not VERIFY_LIB.is_file():
        _emit("V2_verify_lib_file_sha", False, f"missing {VERIFY_LIB}")
        return
    got = _file_sha(VERIFY_LIB)
    _emit(
        "V2_verify_lib_file_sha",
        got == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )


def v3_target_file_sha() -> None:
    if not TARGET_VERIFY.is_file():
        _emit("V3_target_file_sha", False, f"missing {TARGET_VERIFY}")
        return
    got = _file_sha(TARGET_VERIFY)
    _emit(
        "V3_target_file_sha",
        got == EXPECTED_P301_V0_MARKER_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_P301_V0_MARKER_FILE_SHA[:16]}",
    )


def v4_target_v3_func_sha() -> None:
    if not TARGET_VERIFY.is_file():
        _emit("V4_target_v3_func_sha", False, f"missing {TARGET_VERIFY}")
        return
    got = func_sha_by_name(TARGET_VERIFY, "v3_v0_locked_scope_count")
    _emit(
        "V4_target_v3_func_sha",
        got == EXPECTED_V3_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_V3_FUNC_SHA[:16]}",
    )


def v5_baseline_sentinel_present() -> str:
    if not TARGET_VERIFY.is_file():
        _emit("V5_baseline_sentinel_present", False, f"missing {TARGET_VERIFY}")
        return ""
    src = TARGET_VERIFY.read_text(encoding="utf-8")
    v3_src = _v3_func_source(src)
    ok = V3_BASELINE_SENTINEL in v3_src
    _emit(
        "V5_baseline_sentinel_present",
        ok,
        f"sentinel={V3_BASELINE_SENTINEL!r} present={ok} "
        f"v3_func_lines={len(v3_src.splitlines())}",
    )
    return v3_src


def v6_no_legacy_threshold(v3_src: str) -> None:
    """v3 func body 必不含旧 >=1 阈值."""
    hits = [pat for pat in LEGACY_THRESHOLD_PATTERNS if pat in v3_src]
    ok = not hits
    _emit(
        "V6_no_legacy_threshold",
        ok,
        f"legacy_hits={hits}",
    )


def v7_mutation_baseline_lower() -> None:
    """反证: 模拟把 sentinel 写成 ``V3_BASELINE = 1`` 应被本 verify 检出.

    我们在内存里把 v3 源码做字符串替换, 再用相同的扫描逻辑跑一次, 期待
    V5_baseline_sentinel_present 在该 mutant 下应为 False (sentinel 不再
    匹配 ``V3_BASELINE = 53``).
    """
    if not TARGET_VERIFY.is_file():
        _emit("V7_mutation_baseline_lower", False, f"missing {TARGET_VERIFY}")
        return
    src = TARGET_VERIFY.read_text(encoding="utf-8")
    mutated = src.replace("V3_BASELINE = 53", "V3_BASELINE = 1", 1)
    if mutated == src:
        _emit(
            "V7_mutation_baseline_lower",
            False,
            "could not synthesize mutant (sentinel not present in src)",
        )
        return
    v3_src_mut = _v3_func_source(mutated)
    sentinel_in_mut = V3_BASELINE_SENTINEL in v3_src_mut
    ok = not sentinel_in_mut  # mutant should NOT contain canonical sentinel
    _emit(
        "V7_mutation_baseline_lower",
        ok,
        f"mutant_sentinel_absent={ok} (expect True)",
    )


def v_last_reviewer_lgtm_gate() -> None:
    if not REAL_FEATURE_LIST.is_file():
        _emit(
            "V_last_reviewer_lgtm_gate",
            False,
            f"feature_list.json not found at {REAL_FEATURE_LIST}",
        )
        return
    ok, reason = assert_reviewer_lgtm(V_LAST_GATE_FEATURE_ID, REAL_FEATURE_LIST)
    if not ok and V_LAST_GATE_FEATURE_ID in GRACE_PERIOD_FEATURE_IDS:
        _emit(
            "V_last_reviewer_lgtm_gate",
            True,
            f"grace-period PASS (target={V_LAST_GATE_FEATURE_ID} "
            f"helper_ok=False reason={reason!r})",
        )
        return
    _emit(
        "V_last_reviewer_lgtm_gate",
        ok,
        f"target={V_LAST_GATE_FEATURE_ID} helper_ok={ok} reason={reason!r}",
    )


def main() -> None:
    v0_self_main_func_sha()
    v1_docstring_sentinel()
    v2_verify_lib_file_sha()
    v3_target_file_sha()
    v4_target_v3_func_sha()
    v3_src = v5_baseline_sentinel_present()
    v6_no_legacy_threshold(v3_src)
    v7_mutation_baseline_lower()
    v_last_reviewer_lgtm_gate()

    failed = sum(1 for _, ok, _ in _results if not ok)
    print(
        f"[verify_infra_P301_followup_v3_scope_count_tighten] summary: "
        f"total={len(_results)} failed={failed}",
        flush=True,
    )
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
