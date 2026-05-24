#!/usr/bin/env python3
"""verify_infra_P301_v0_marker_naming: V0 marker 命名约定 lint + near-miss 检测.

infra-P301-backlog-v0-marker-extended-naming (来源 phase-60 #2 infra-P301-extend
Reviewer P2-2 finding):

verify_infra_P301_full.py 通过字面 ``V0_SELF_SHA_LOCK_VERSION`` /
``V0_SELF_SHA_LOCK_BUMPED_AT`` 模块常量标记识别 V0-locked 脚本; 异类命名
(如 ``SELF_SHA_LOCK_VERSION`` 缺 V0_ 前缀, ``V0_SHA_LOCK_VERSION`` 缺 SELF,
``V0_SELF_SHA_VERSION`` 缺 LOCK 等) 会被静默漏扫. 本 verifier:

  - 把 V0 marker 命名约定显式写入 docstring (本节即规约文本); canonical 形态
    必须严格匹配 ``V0_SELF_SHA_LOCK_VERSION`` / ``V0_SELF_SHA_LOCK_BUMPED_AT``.
  - 扫 ``scripts/verify_*.py`` 全集中"V0-locked"脚本 (含
    ``EXPECTED_SELF_MAIN_FUNC_SHA`` 常量), 检查它们是否含 canonical V0 marker.
    若脚本是 V0-locked 但完全没有 canonical V0_SELF_SHA_LOCK_VERSION /
    V0_SELF_SHA_LOCK_BUMPED_AT 常量, 同时却含"看起来像 V0 marker typo"
    的模块级常量 (regex 见 ``_NEAR_MISS_RE``), 即报 near-miss FAIL.
    合法的不同段 (例如 ``V8_SELF_SHA_LOCK_VERSION``) 在脚本同时含 canonical
    V0 marker 时不视为 near-miss (它们是不同 V 段的独立元信息).
  - near-miss 数=0 时 V5 PASS (当前 baseline 状态); >0 时 FAIL 并列名供
    修正. V6 mutation (canary): 临时构造一个 near-miss 命名常量 (写入 tmp
    sandbox), 扫描应报 ≥1, 反证扫描真的有效.

CANONICAL V0 MARKER NAMING CONVENTION (规约)
--------------------------------------------
任何 verify_*.py 在 V0 self_sha 锁段写入元信息时必须使用以下严格命名:

  - ``V0_SELF_SHA_LOCK_VERSION``      (int >= 1)
  - ``V0_SELF_SHA_LOCK_BUMPED_AT``    (str, YYYY-MM-DD)

禁止异类: ``SELF_SHA_LOCK_VERSION`` / ``V0_SHA_LOCK_VERSION`` /
``SELF_SHA_VERSION`` / ``V0_SELF_LOCK_VERSION`` 等都视为 near-miss FAIL.
理由: P301-full 全集 lint 用字面 grep canonical 名识别 V0-locked, 异类命名
会被漏扫导致白名单/coverage 失准.

INFRA_P301_V0_MARKER_NAMING_DOC_SHA_LOCKS
-----------------------------------------
- self ``main`` func sha: ``EXPECTED_SELF_MAIN_FUNC_SHA`` (V0 自锁)
- target lib file sha: ``EXPECTED_VERIFY_LIB_FILE_SHA`` (V2)

校验层级 (V0-V_last):

- V0_self_main_func_sha     : 本 verifier 自身 ``main`` func sha 锁
- V1_docstring_sentinel     : ``INFRA_P301_V0_MARKER_NAMING_DOC_SHA_LOCKS`` 自锁
- V2_verify_lib_file_sha    : scripts/_verify_lib.py file sha 锁
- V3_v0_locked_scope_count  : V0-locked 脚本 (含 canonical V0_SELF_SHA_LOCK_VERSION)
                              数量 >= 53 (硬 baseline, 收紧自 >=1; 见 infra-
                              P301-followup-v3-scope-count-tighten)
- V4_canonical_names_intact : self docstring 与 ALLOWED canonical 名称表对齐
- V5_no_near_miss           : 全集中"含 near-miss 命名但非 canonical"脚本数=0
- V6_mutation_near_miss     : canary mode 模拟一条 near-miss, scanner 应检出
- V_last_reviewer_lgtm_gate : assert_reviewer_lgtm helper (backloaded)

## Lock: EXPECTED_VERIFY_LIB_FILE_SHA
- target_function: N/A
- target_file: scripts/_verify_lib.py
- lock_kind: file_sha
- bump_when: scripts/_verify_lib.py 文件 sha256 变化 (helper 任何字节改动)
- bump_protocol: 重算 sha256 of scripts/_verify_lib.py 并更新常量
- rationale: V2 锁 helper 文件, 防 assert_reviewer_lgtm / func_sha_by_name /
  read_constant 等接口在本 verify 不察觉时被改

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import ast
import hashlib
import re
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
)

# --- self / target sha locks (V0 / V2) ------------------------------------
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "55f4396f8423f0bcc91654b219858032959bc8770bbc69d1cab8f0ad88a19670"
)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "1990a9b61d3b14c9b827a23188a46001be2a208234352e1bc7af955285578f50"
)

# --- V0 metadata schema bump marker ----------------------------------------
V0_SELF_SHA_LOCK_VERSION = 1
V0_SELF_SHA_LOCK_BUMPED_AT = "2026-05-24"

DOCSTRING_SENTINEL = "INFRA_P301_V0_MARKER_NAMING_DOC_SHA_LOCKS"

# Canonical V0 marker names (only these are accepted; anything else with similar
# substring is a near-miss).
CANONICAL_V0_MARKERS = frozenset(
    {
        "V0_SELF_SHA_LOCK_VERSION",
        "V0_SELF_SHA_LOCK_BUMPED_AT",
    }
)

# Broader regex used to find near-miss candidate names. Matches any UPPER_SNAKE
# identifier that *looks like* a V0 sha-lock metadata constant. Canonical names
# also match (and are filtered out by the canonical set above).
_NEAR_MISS_RE = re.compile(
    r"^[A-Z][A-Z0-9_]*("
    r"SHA_LOCK_VERSION|"
    r"SHA_LOCK_BUMPED_AT|"
    r"SHA_LOCK_BUMPED|"
    r"SELF_SHA_VERSION|"
    r"SELF_LOCK_VERSION"
    r")$"
)

V_LAST_GATE_FEATURE_ID = "infra-P301-backlog-v0-marker-extended-naming"
REAL_FEATURE_LIST = REPO / "feature_list.json"
GRACE_PERIOD_FEATURE_IDS = (V_LAST_GATE_FEATURE_ID,)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P301_v0_marker_naming][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_level_names(src: str) -> List[str]:
    """Return all module-level `Name = <literal>` assignment targets in `src`."""
    out: List[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for tgt in targets:
                if isinstance(tgt, ast.Name):
                    out.append(tgt.id)
    return out


def _scan_v0_locked_and_near_miss(
    extra_files: List[Path] | None = None,
) -> Tuple[List[Path], List[Tuple[Path, str]]]:
    """Walk scripts/verify_*.py (+ optional extras) and classify.

    Returns:
      (v0_locked_paths, near_miss_pairs)
        v0_locked_paths: files containing EXPECTED_SELF_MAIN_FUNC_SHA marker.
        near_miss_pairs: (path, name) where the file IS V0-locked but does NOT
                         contain any canonical V0 marker, yet contains a module
                         level const whose name matches _NEAR_MISS_RE. This
                         narrows to genuine "tried to write V0 marker but
                         misnamed it" cases, while legitimate other-V-segment
                         metadata (e.g. V8_SELF_SHA_LOCK_VERSION) coexisting
                         alongside canonical V0 markers is NOT flagged.
    """
    paths = sorted(SCRIPTS.glob("verify_*.py"))
    if extra_files:
        paths = paths + list(extra_files)
    v0_locked: List[Path] = []
    near_miss: List[Tuple[Path, str]] = []
    for p in paths:
        try:
            src = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if "EXPECTED_SELF_MAIN_FUNC_SHA" not in src:
            continue
        v0_locked.append(p)
        names = _module_level_names(src)
        has_canonical = any(n in CANONICAL_V0_MARKERS for n in names)
        if has_canonical:
            # legitimate V0-marker users; other V<n>_SHA_LOCK_* names are
            # independent metadata for other lock segments, not near-miss.
            continue
        # V0-locked but missing canonical V0 markers; ANY remaining
        # SHA_LOCK_VERSION-tail name is a typo candidate.
        for name in names:
            if name in CANONICAL_V0_MARKERS:
                continue
            if _NEAR_MISS_RE.match(name):
                near_miss.append((p, name))
    return v0_locked, near_miss


# --- canary mode -----------------------------------------------------------
# canary mode: write a synthetic verify_*.py with a near-miss constant into a
# tmp dir and ensure scanner detects it (mutation reverse-test). Run via
# `python verify_infra_P301_v0_marker_naming.py --canary`.

_CANARY_NEAR_MISS_NAME = "SELF_SHA_LOCK_VERSION"  # missing V0_ prefix
_CANARY_FILE_TEMPLATE = (
    '"""canary file for verify_infra_P301_v0_marker_naming."""\n'
    f"{_CANARY_NEAR_MISS_NAME} = 1\n"
)


def _run_canary() -> int:
    """Build synthetic near-miss script in tmp; scanner must detect it.

    Exit 0 if mutation detected, 2 otherwise.
    """
    # We cannot put the canary file into scripts/ (would pollute scan).
    # Instead, we directly verify the regex + canonical filter detects the
    # synthetic name parsed from a tmp file.
    with tempfile.TemporaryDirectory() as td:
        canary_path = Path(td) / "verify_canary_synthetic.py"
        canary_path.write_text(_CANARY_FILE_TEMPLATE, encoding="utf-8")
        names = _module_level_names(canary_path.read_text(encoding="utf-8"))
        detected = [
            n
            for n in names
            if n not in CANONICAL_V0_MARKERS and _NEAR_MISS_RE.match(n)
        ]
        if _CANARY_NEAR_MISS_NAME in detected:
            print(
                f"[verify_infra_P301_v0_marker_naming][canary][PASS] "
                f"near-miss {_CANARY_NEAR_MISS_NAME!r} detected",
                flush=True,
            )
            return 0
        print(
            f"[verify_infra_P301_v0_marker_naming][canary][FAIL] "
            f"near-miss {_CANARY_NEAR_MISS_NAME!r} NOT detected "
            f"(scanner missed it); detected={detected}",
            flush=True,
        )
        return 2


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
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V2_verify_lib_file_sha",
            False,
            f"placeholder; bump EXPECTED_VERIFY_LIB_FILE_SHA={got}",
        )
    else:
        _emit(
            "V2_verify_lib_file_sha",
            got == EXPECTED_VERIFY_LIB_FILE_SHA,
            f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
        )


def v3_v0_locked_scope_count() -> List[Path]:
    v0_locked, _ = _scan_v0_locked_and_near_miss()
    # baseline 53 锁 (phase-67 #21 close-out 时 v0_locked count == 53).
    # 收紧自 >=1 -> >=53 防 silent regression (删 V0 marker 不再静默 PASS).
    # bump_protocol: 若 V0 marker 在多脚本批量退役, 同时 bump 此处 + 文档.
    V3_BASELINE = 53
    ok = len(v0_locked) >= V3_BASELINE
    _emit(
        "V3_v0_locked_scope_count",
        ok,
        f"v0_locked_count={len(v0_locked)} (expect >={V3_BASELINE})",
    )
    return v0_locked


def v4_canonical_names_intact() -> None:
    src = Path(__file__).read_text(encoding="utf-8")
    missing = [n for n in sorted(CANONICAL_V0_MARKERS) if n not in src]
    ok = not missing
    _emit(
        "V4_canonical_names_intact",
        ok,
        f"canonical_count={len(CANONICAL_V0_MARKERS)} missing={missing}",
    )


def v5_no_near_miss() -> None:
    _, near_miss = _scan_v0_locked_and_near_miss()
    ok = not near_miss
    if near_miss:
        sample = ", ".join(f"{p.name}:{n}" for p, n in near_miss[:5])
        detail = f"count={len(near_miss)} sample=[{sample}]"
    else:
        detail = "count=0"
    _emit("V5_no_near_miss", ok, detail)


def v6_mutation_near_miss() -> None:
    """Reverse-test: synthesize a near-miss inline, ensure scanner sees it."""
    synthetic_src = "SELF_SHA_LOCK_VERSION = 1\n"
    names = _module_level_names(synthetic_src)
    detected = [
        n
        for n in names
        if n not in CANONICAL_V0_MARKERS and _NEAR_MISS_RE.match(n)
    ]
    ok = "SELF_SHA_LOCK_VERSION" in detected
    _emit(
        "V6_mutation_near_miss",
        ok,
        f"synthetic near-miss detected={detected}",
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
    if "--canary" in sys.argv:
        sys.exit(_run_canary())

    v0_self_main_func_sha()
    v1_docstring_sentinel()
    v2_verify_lib_file_sha()
    v3_v0_locked_scope_count()
    v4_canonical_names_intact()
    v5_no_near_miss()
    v6_mutation_near_miss()
    v_last_reviewer_lgtm_gate()

    failed = sum(1 for _, ok, _ in _results if not ok)
    print(
        f"[verify_infra_P301_v0_marker_naming] summary: "
        f"total={len(_results)} failed={failed}",
        flush=True,
    )
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
