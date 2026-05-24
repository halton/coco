#!/usr/bin/env python3
"""verify_infra_P301_followup_typed_enum: P301_full ALLOWED_MISSING reason
字段 typed-enum lint.

phase-67 #1 infra-P301-backlog-allowlist-reason-typed-enum:
来源 phase-60 #2 infra-P301-extend-allowed-missing-allowlist-scan-all backlog
finding: ``verify_infra_P301_full.py`` 的 ``ALLOWED_MISSING`` 白名单 reason
字段当前是自由文本, 44 条 historical-batch-pending + 1 条 P301-self 已天然形
成 ``<prefix>:<detail>`` 形式. 但缺少机械化校验, 后续新增条目易写出未归类
prefix (typo / 自由发挥 / 漏写 prefix), 难分类与统计.

本 verifier 把 ALLOWED_MISSING 中每条 value 拆 ``<prefix>:<detail>``, 强制
``prefix`` ∈ ``ALLOWED_REASON_PREFIXES`` (typed enum set). 不阻 merge, 也不
改 ``verify_infra_P301_full.py`` 自身的 main() func sha (新增 lint 走独立脚本,
零 cascade 风险).

INFRA_P301_FOLLOWUP_TYPED_ENUM_DOC_SHA_LOCKS
--------------------------------------------
- self ``main`` func sha: ``EXPECTED_SELF_MAIN_FUNC_SHA`` (V0 自锁)
- target P301_full file sha: ``EXPECTED_P301_FULL_FILE_SHA`` (V2 锁, 防 target
  飘移导致 lint 解析失配)

校验层级 (V0-V5):

- V0_self_main_func_sha       : 本 verifier 自身 ``main`` func sha 锁
- V1_docstring_sentinel       : ``INFRA_P301_FOLLOWUP_TYPED_ENUM_DOC_SHA_LOCKS``
                                 自锁存在
- V2_p301_full_file_sha       : verify_infra_P301_full.py file sha 锁
- V3_allowlist_nonempty       : 从 target 解析出的 ALLOWED_MISSING 非空
- V3_allowlist_reason_format  : 每条 reason 必须含 ":" 分隔 (即 prefix:detail)
- V3_allowlist_reason_typed   : 每条 reason 的 prefix ∈ ALLOWED_REASON_PREFIXES
- V4_enum_nonempty            : ALLOWED_REASON_PREFIXES 至少 1 项
- V5_reviewer_lgtm_gate       : Reviewer fresh-context LGTM evidence bind

V0 metadata schema fields:
- V0_SELF_SHA_LOCK_VERSION (int >= 1)
- V0_SELF_SHA_LOCK_BUMPED_AT (str, YYYY-MM-DD)

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
P301_FULL = SCRIPTS / "verify_infra_P301_full.py"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

# --- self / target sha locks (V0 / V2) ------------------------------------
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "e291af057e5dfc7ecb97a7d95e3d35a90d278fc1de13da9627fe2941c2c15359"
)
EXPECTED_P301_FULL_FILE_SHA = (
    "3fc72a0fea0f46a572eb417436ace97f60a4abf0cf1cfea0d99d08fd8ceabf7d"
)

# --- V0 metadata schema bump marker ---------------------------------------
V0_SELF_SHA_LOCK_VERSION = 1
V0_SELF_SHA_LOCK_BUMPED_AT = "2026-05-24"

# --- V1 docstring sentinel ------------------------------------------------
DOCSTRING_SENTINEL = "INFRA_P301_FOLLOWUP_TYPED_ENUM_DOC_SHA_LOCKS"

# --- V5 reviewer gate target ----------------------------------------------
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P301-backlog-allowlist-reason-typed-enum"

# --- typed enum: 允许的 reason prefix ---------------------------------------
# 维护契约:
# - 新增条目到 ALLOWED_MISSING 时, reason 必须以下列 prefix 之一开头, 后接
#   ":" + 自由文本细节.
# - 新增 prefix 需同时改本 enum + 在 P301_full ALLOWED_MISSING 中使用; 改本
#   enum 会触发本 verifier 的 self_sha bump.
# - prefix 含义:
#     historical-batch-pending : phase-58 及以前历史 V0-locked 脚本, 等待批量补齐
#                                 元信息字段, 暂豁免缺字段;
#     is-the-P301-lint-itself  : P301 lint 脚本自身, 元信息字段属其 target, 不
#                                 在 lint 本身重复携带 (设计豁免).
ALLOWED_REASON_PREFIXES: Tuple[str, ...] = (
    "historical-batch-pending",
    "is-the-P301-lint-itself",
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P301_followup_typed_enum][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_allowed_missing(src: str) -> Dict[str, str]:
    """从 P301_full 源码中 ast.literal_eval-ish 提取 ALLOWED_MISSING dict.

    P301_full 用 ``ALLOWED_MISSING: Dict[str, str] = {... **{name: reason for ...}}``
    形式, 不是纯字面量, 故无法直接 ast.literal_eval. 改为:
      1. import target 模块拿运行时 dict (target 已是 module-level constant,
         无副作用 import).
    """
    spec_dir = str(P301_FULL.parent)
    if spec_dir not in sys.path:
        sys.path.insert(0, spec_dir)
    # 直接 import target module 拿 ALLOWED_MISSING (模块级常量, 纯字典构造无 IO 副作用)
    import importlib

    mod_name = P301_FULL.stem
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    mod = importlib.import_module(mod_name)
    am = getattr(mod, "ALLOWED_MISSING", None)
    if not isinstance(am, dict):
        return {}
    return dict(am)


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


def v2_p301_full_file_sha() -> None:
    if not P301_FULL.is_file():
        _emit("V2_p301_full_file_sha", False, f"missing {P301_FULL}")
        return
    got = _file_sha(P301_FULL)
    if EXPECTED_P301_FULL_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V2_p301_full_file_sha",
            False,
            f"placeholder; bump EXPECTED_P301_FULL_FILE_SHA={got}",
        )
    else:
        _emit(
            "V2_p301_full_file_sha",
            got == EXPECTED_P301_FULL_FILE_SHA,
            f"got={got[:16]} expect={EXPECTED_P301_FULL_FILE_SHA[:16]}",
        )


# --- V3 / V4 allowlist reason typed-enum ----------------------------------


def v3_v4_allowlist_typed() -> None:
    try:
        src = P301_FULL.read_text(encoding="utf-8")
    except OSError as e:
        _emit("V3_allowlist_nonempty", False, f"read err: {e!r}")
        return
    try:
        allowed_missing = _parse_allowed_missing(src)
    except Exception as e:
        _emit("V3_allowlist_nonempty", False, f"parse err: {e!r}")
        return

    _emit(
        "V3_allowlist_nonempty",
        len(allowed_missing) >= 1,
        f"size={len(allowed_missing)}",
    )

    # V3_allowlist_reason_format: 每条 reason 含 ':'
    bad_format = sorted(k for k, v in allowed_missing.items() if ":" not in (v or ""))
    _emit(
        "V3_allowlist_reason_format",
        len(bad_format) == 0,
        f"no_prefix_colon={bad_format}",
    )

    # V3_allowlist_reason_typed: prefix ∈ enum
    enum_set = set(ALLOWED_REASON_PREFIXES)
    bad_typed: List[Tuple[str, str]] = []
    for name, reason in allowed_missing.items():
        if not reason or ":" not in reason:
            continue
        prefix = reason.split(":", 1)[0].strip()
        if prefix not in enum_set:
            bad_typed.append((name, prefix))
    _emit(
        "V3_allowlist_reason_typed",
        len(bad_typed) == 0,
        f"unknown_prefixes={bad_typed[:5]} (showing<=5) total={len(bad_typed)}",
    )

    # V4_enum_nonempty
    _emit(
        "V4_enum_nonempty",
        len(ALLOWED_REASON_PREFIXES) >= 1,
        f"enum_size={len(ALLOWED_REASON_PREFIXES)} values={list(ALLOWED_REASON_PREFIXES)}",
    )


# --- V5 reviewer LGTM gate -------------------------------------------------


def v5_reviewer_gate() -> None:
    try:
        result = assert_v5_reviewer_gate_evidence_bind(
            feature_list_path=REAL_FEATURE_LIST,
            feature_id=V5_GATE_FEATURE_ID,
        )
    except Exception as e:
        _emit("V5_reviewer_lgtm_gate", False, f"helper err: {e!r}")
        return
    _emit(
        "V5_reviewer_lgtm_gate",
        bool(result.get("ok")),
        (
            f"target={V5_GATE_FEATURE_ID} helper_ok={result.get('ok')} "
            f"verdict={result.get('verdict')!r} kind={result.get('kind')!r} "
            f"summary_len={result.get('summary_len')} reason={result.get('reason')!r}"
        ),
    )


def main() -> None:
    v0_self_main_func_sha()
    v1_docstring_sentinel()
    v2_p301_full_file_sha()
    v3_v4_allowlist_typed()
    v5_reviewer_gate()

    failed = sum(1 for _, ok, _ in _results if not ok)
    print(
        f"[verify_infra_P301_followup_typed_enum] summary: total={len(_results)} failed={failed}",
        flush=True,
    )
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
