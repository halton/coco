#!/usr/bin/env python3
"""verify_infra_P301_full: V0 sha lock 版本号字段全量 lint + ALLOWED_MISSING 白名单.

phase-60 #2 infra-P301-extend-allowed-missing-allowlist-scan-all:
来源 phase-58 #4 infra-P301 Reviewer P2-1 finding. 当前 infra-P301 仅在示范脚本
verify_interact_036b.py (与本 verifier 自身参考脚本 verify_infra_P301.py) 的
V0 self_sha 段写入 ``V0_SELF_SHA_LOCK_VERSION`` + ``V0_SELF_SHA_LOCK_BUMPED_AT``
两个元信息字段, 未覆盖全部 V0 sha 锁脚本. 本 verifier 把 lint 扩展到全集:

  - 扫 ``scripts/verify_*.py`` 全集, 把"含 V0 self_sha 锁段"定义为模块级常量
    ``EXPECTED_SELF_MAIN_FUNC_SHA = <64hex>`` 存在 (canonical V0 marker).
  - 对每个 V0-locked 脚本, 必须满足以下任一:
      (a) 同时定义模块级常量 ``V0_SELF_SHA_LOCK_VERSION`` (int >= 1) 与
          ``V0_SELF_SHA_LOCK_BUMPED_AT`` (str, YYYY-MM-DD), 或
      (b) 出现在 ``ALLOWED_MISSING`` 白名单中, 并附 reason 说明.
  - 白名单是显式豁免表 (script basename -> reason), 不是黑洞: 任何不在白名单
    且缺字段的脚本都会让 V_global_coverage FAIL. 白名单条目本身也有 sha-lock
    式校验: 列出的脚本必须在仓库内存在, 且确实是 V0-locked 候选 (有
    ``EXPECTED_SELF_MAIN_FUNC_SHA`` 模块常量).

本 verifier 是 grace-period polish lint, 不阻 merge; 但 V_global_coverage 给出
具体缺字段脚本列表, 便于后续 backlog 批量补齐.

INFRA_P301_FULL_DOC_SHA_LOCKS
------------------------------
- self ``main`` func sha: ``EXPECTED_SELF_MAIN_FUNC_SHA`` (V0 自锁)
- target lib file sha: ``EXPECTED_VERIFY_LIB_FILE_SHA`` (V2, 锁 _verify_lib.py
  以确保 read_constant / func_sha_by_name 行为稳定)

校验层级 (V0-V5):

- V0_self_main_func_sha       : 本 verifier 自身 ``main`` func sha 锁
- V1_docstring_sentinel       : ``INFRA_P301_FULL_DOC_SHA_LOCKS`` 自锁存在
- V2_verify_lib_file_sha      : scripts/_verify_lib.py file sha 锁 (helper 稳定)
- V3_v0_scope_nonempty        : 扫描结果中 V0-locked 脚本数 >= 1
- V3_allowlist_targets_valid  : ALLOWED_MISSING 中每条目都对应实际存在且
                                 V0-locked 的脚本 (no stale / no typo)
- V3_allowlist_no_redundant   : ALLOWED_MISSING 不应包含已经补齐字段的脚本
                                 (allowlist 必须只豁免真正缺字段的)
- V4_global_coverage          : 全集中"缺字段且不在 ALLOWED_MISSING"的脚本数=0
- V4_version_const_typing     : 所有补齐脚本中 V0_SELF_SHA_LOCK_VERSION 必须
                                 int >= 1
- V4_bumped_at_format         : 所有补齐脚本中 V0_SELF_SHA_LOCK_BUMPED_AT 必须
                                 严格匹配 ^\\d{4}-\\d{2}-\\d{2}$
- V5_reviewer_lgtm_gate       : Reviewer fresh-context LGTM evidence bind
                                 (grace 兜底)

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

# --- self / target sha locks (V0 / V2) ------------------------------------
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "dbbe9162414d608119e8bd15a04b722427cec1c85f3dbf1dd0d23026a5ad4217"
)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "f789e0d870c9e6c3764b893a6bcbcca356bfc464520d9c49f3d4b17045ecd243"
)

# --- V0 metadata schema bump marker (供 cascade bump diff 用) ---------------
V0_SELF_SHA_LOCK_VERSION = 1
V0_SELF_SHA_LOCK_BUMPED_AT = "2026-05-23"

# --- V1 docstring sentinel -------------------------------------------------
DOCSTRING_SENTINEL = "INFRA_P301_FULL_DOC_SHA_LOCKS"

# --- V5 reviewer gate target ----------------------------------------------
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P301-extend-allowed-missing-allowlist-scan-all"

# --- 字段常量名 (与 verify_infra_P301 / verify_interact_036b 保持一致) ------
VERSION_CONST = "V0_SELF_SHA_LOCK_VERSION"
BUMPED_AT_CONST = "V0_SELF_SHA_LOCK_BUMPED_AT"
V0_MARKER_CONST = "EXPECTED_SELF_MAIN_FUNC_SHA"
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# --- ALLOWED_MISSING 白名单 (script basename -> 豁免 reason) ----------------
#
# 维护契约:
# - key 是 scripts/ 目录下 basename, 不含路径.
# - 列出的脚本必须存在且是 V0-locked (有模块级 EXPECTED_SELF_MAIN_FUNC_SHA).
# - 列出的脚本必须 *确实* 缺 V0_SELF_SHA_LOCK_VERSION 或 V0_SELF_SHA_LOCK_BUMPED_AT;
#   若后续补齐字段, 必须把对应条目从白名单移除 (否则 V3_allowlist_no_redundant FAIL).
# - reason 必须非空, 简述豁免理由 (历史脚本 / 等待批量补齐 / 设计上不需要 等).
#
# 当前列表覆盖 phase-58 及以前所有 V0-locked verify 脚本, 全部统一 reason 为
# "historical-batch-pending: 等待 infra-P301 后续 backlog 批量补齐元信息字段".
# 任何新 V0-locked verify 脚本默认要求带元信息字段; 若新增确实无法带 (例如
# 设计上自指), 必须显式加白名单条目 + 个性化 reason.
ALLOWED_MISSING: Dict[str, str] = {
    # infra-P301 示范 verifier 自身: 它的"被锁对象"是 verify_interact_036b.py,
    # 元信息字段写在 target (interact-036b) 而非 lint 脚本自身; 显式豁免.
    "verify_infra_P301.py": (
        "is-the-P301-lint-itself: 元信息字段属于其 target verify_interact_036b.py, "
        "lint 脚本自身不再重复携带"
    ),
    **{
        name: "historical-batch-pending: 等待 infra-P301 后续 backlog 批量补齐元信息字段"
        for name in (
        "verify_infra_068.py",
        "verify_infra_069.py",
        "verify_infra_070.py",
        "verify_infra_071.py",
        "verify_infra_072.py",
        "verify_infra_073.py",
        "verify_infra_074.py",
        "verify_infra_075.py",
        "verify_infra_076.py",
        "verify_infra_077.py",
        "verify_infra_078.py",
        "verify_infra_079.py",
        "verify_infra_080.py",
        "verify_infra_081.py",
        "verify_infra_082.py",
        "verify_infra_083.py",
        "verify_infra_084.py",
        "verify_infra_085.py",
        "verify_infra_086.py",
        "verify_infra_087.py",
        "verify_infra_088.py",
        "verify_infra_089.py",
        "verify_infra_090.py",
        "verify_infra_091.py",
        "verify_infra_092.py",
        "verify_infra_093.py",
        "verify_infra_094.py",
        "verify_infra_095.py",
        "verify_infra_096.py",
        "verify_infra_097.py",
        "verify_infra_098.py",
        "verify_infra_099.py",
        "verify_infra_100.py",
        "verify_infra_102.py",
        "verify_infra_104.py",
        "verify_infra_105_backlog_helper_doctest.py",
        "verify_infra_106.py",
        "verify_infra_107_backlog_dump_family_equality.py",
        "verify_infra_108.py",
        "verify_infra_109.py",
        "verify_infra_110.py",
        "verify_infra_P290.py",
        "verify_infra_P293_typo_guard_ci_integration.py",
    )
    },
}

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P301_full][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_const_lookup(src: str, name: str) -> Tuple[bool, object]:
    """同 verify_infra_P301._read_module_constant: 返回 (found, value).

    only 扫 Module.body, 严格 literal_eval; 求值失败返回 (True, None) 让上层
    报类型 FAIL.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False, None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    try:
                        return True, ast.literal_eval(node.value)
                    except Exception:
                        return True, None
    return False, None


def _scan_v0_locked_scripts() -> List[Path]:
    """返回 scripts/ 下所有 V0-locked verify 脚本 (按 basename 排序)."""
    out: List[Path] = []
    for p in sorted(SCRIPTS.glob("verify_*.py")):
        try:
            src = p.read_text(encoding="utf-8")
        except OSError:
            continue
        found, value = _module_const_lookup(src, V0_MARKER_CONST)
        # V0-locked 定义: 模块级 EXPECTED_SELF_MAIN_FUNC_SHA = <str of len>=16>
        if found and isinstance(value, str) and len(value) >= 16:
            out.append(p)
    return out


# --- V0 self sha lock ------------------------------------------------------


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
    placeholder = "0" * 64
    if EXPECTED_VERIFY_LIB_FILE_SHA == placeholder:
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


# --- 全集扫描 / 白名单一致性 -----------------------------------------------


def v3_v4_scan_and_coverage() -> None:
    scripts = _scan_v0_locked_scripts()
    scope_n = len(scripts)
    _emit(
        "V3_v0_scope_nonempty",
        scope_n >= 1,
        f"V0-locked scripts scanned={scope_n}",
    )

    # 解析每脚本 -> (has_version, has_bumped_at, version_value, bumped_at_value)
    parsed: Dict[str, Dict[str, object]] = {}
    for p in scripts:
        src = p.read_text(encoding="utf-8")
        f_v, v_val = _module_const_lookup(src, VERSION_CONST)
        f_b, b_val = _module_const_lookup(src, BUMPED_AT_CONST)
        parsed[p.name] = {
            "has_version": f_v,
            "has_bumped_at": f_b,
            "version_value": v_val,
            "bumped_at_value": b_val,
        }

    name_set = {p.name for p in scripts}

    # V3_allowlist_targets_valid: ALLOWED_MISSING 里每项必须是 V0-locked 脚本
    stale = sorted(n for n in ALLOWED_MISSING if n not in name_set)
    _emit(
        "V3_allowlist_targets_valid",
        len(stale) == 0,
        f"allowlist_size={len(ALLOWED_MISSING)} stale_entries={stale}",
    )

    # V3_allowlist_no_redundant: ALLOWED_MISSING 内的脚本必须 *确实* 缺至少一个字段
    redundant: List[str] = []
    for n, _reason in ALLOWED_MISSING.items():
        if n not in parsed:
            # 已经在 stale 里报过, 跳过避免重复
            continue
        info = parsed[n]
        if info["has_version"] and info["has_bumped_at"]:
            redundant.append(n)
    _emit(
        "V3_allowlist_no_redundant",
        len(redundant) == 0,
        f"redundant_entries={sorted(redundant)} "
        f"(已补齐字段但仍在白名单, 应移除)",
    )

    # V4_global_coverage: 缺字段且不在白名单 = 0
    violators: List[str] = []
    for n, info in parsed.items():
        missing_any = not (info["has_version"] and info["has_bumped_at"])
        if missing_any and n not in ALLOWED_MISSING:
            violators.append(n)
    _emit(
        "V4_global_coverage",
        len(violators) == 0,
        f"violators (缺字段且无白名单)={sorted(violators)}",
    )

    # V4_version_const_typing: 补齐字段的脚本中 VERSION_CONST 必须 int>=1
    bad_version: List[str] = []
    for n, info in parsed.items():
        if not info["has_version"]:
            continue
        v = info["version_value"]
        if not (isinstance(v, int) and not isinstance(v, bool) and v >= 1):
            bad_version.append(f"{n}:{v!r}")
    _emit(
        "V4_version_const_typing",
        len(bad_version) == 0,
        f"bad_version (非 int>=1)={bad_version}",
    )

    # V4_bumped_at_format: 补齐字段的脚本中 BUMPED_AT_CONST 必须 ISO date
    bad_bumped: List[str] = []
    for n, info in parsed.items():
        if not info["has_bumped_at"]:
            continue
        b = info["bumped_at_value"]
        if not (isinstance(b, str) and ISO_DATE_RE.match(b)):
            bad_bumped.append(f"{n}:{b!r}")
    _emit(
        "V4_bumped_at_format",
        len(bad_bumped) == 0,
        f"bad_bumped (非 YYYY-MM-DD)={bad_bumped}",
    )


def v5_reviewer_gate() -> None:
    if not REAL_FEATURE_LIST.is_file():
        _emit(
            "V5_reviewer_lgtm_gate",
            False,
            f"feature_list.json not found at {REAL_FEATURE_LIST}",
        )
        return
    result = assert_v5_reviewer_gate_evidence_bind(
        V5_GATE_FEATURE_ID,
        REAL_FEATURE_LIST,
        grace_period_feature_ids=(V5_GATE_FEATURE_ID,),
    )
    _emit(
        "V5_reviewer_lgtm_gate",
        bool(result["ok"]),
        f"target={V5_GATE_FEATURE_ID} helper_ok={result['ok']} "
        f"reason={result.get('reason', '')}",
    )


def main() -> None:
    v0_self_main_func_sha()
    v1_docstring_sentinel()
    v2_verify_lib_file_sha()
    v3_v4_scan_and_coverage()
    v5_reviewer_gate()

    failed = sum(1 for _, ok, _ in _results if not ok)
    print(
        f"[verify_infra_P301_full] summary: total={len(_results)} failed={failed}",
        flush=True,
    )
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
