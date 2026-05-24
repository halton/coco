#!/usr/bin/env python3
"""verify_infra_P301: V0 sha lock 版本号字段 lint (verify-only).

infra-P301-backlog-v0-sha-lock-version-field (phase-58 #4):
来源 phase-57 #5 interact-036b Reviewer P2-2 finding: 当前 V0/V1/V2 sha lock
直接 hardcode hex, 当批量升级 baseline (cascade bump) 时, 无法快速 diff 出哪个
lock 是新一轮 bump、哪个是旧轮残留. 本 feature 在示范脚本 (verify_interact_036b.py)
的 V0 self_sha 段引入两个元信息字段:

  V0_SELF_SHA_LOCK_VERSION   : int >= 1, 整组 sha 锁 (V0/V1/V2) 的 schema 版本号,
                               每次集体 bump 时 +1; 不随单纯 hex 重算变化.
  V0_SELF_SHA_LOCK_BUMPED_AT : ISO date (YYYY-MM-DD), 最近一次 bump 的日期.

不参与运行时 sha 自校 (避免自指), 仅由本 verify 脚本 lint:
  (a) 两个字段在示范脚本中可被 ast 解析到模块级常量;
  (b) 类型与取值范围合规;
  (c) ``v0_self_sha_lock()`` 函数体 detail 字符串包含两字段名称 (确保 emit 时
      把元信息写出, 便于 closeout evidence 与 cascade bump diff 时人眼对照).

infra-P301 是 grace-period polish lint, 不阻 merge; 其余未补字段的 verify 脚本
通过 ALLOWED_MISSING 白名单兜底, 等待后续 backlog 批量补齐.

INFRA_P301_DOC_SHA_LOCKS
------------------------
- ``scripts/verify_interact_036b.py`` file sha: EXPECTED_TARGET_FILE_SHA
- self ``main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA (V0 自锁)

校验层级 (V0-V5):

- V0_self_main_func_sha    : 自身 ``main`` func sha 锁
- V1_docstring_sentinel    : ``INFRA_P301_DOC_SHA_LOCKS`` 自锁存在
- V2_target_file_sha       : verify_interact_036b.py file sha 锁
- V3_version_const_present : V0_SELF_SHA_LOCK_VERSION 模块级常量存在 + int
- V3_bumped_at_const_present : V0_SELF_SHA_LOCK_BUMPED_AT 模块级常量存在 + ISO date
- V4_version_range         : V0_SELF_SHA_LOCK_VERSION >= 1
- V4_bumped_at_format      : V0_SELF_SHA_LOCK_BUMPED_AT 严格匹配 ^\\d{4}-\\d{2}-\\d{2}$
- V4_v0_lock_emits_metadata: v0_self_sha_lock 函数源码中包含两个字段名 (确保 emit
                             带上 lock_schema_version / bumped_at 元信息)
- V5_reviewer_lgtm_gate    : Reviewer fresh-context LGTM evidence bind (grace 兜底)

退出码 0=ALL PASS / 2=任一 FAIL.

## Lock: EXPECTED_TARGET_FILE_SHA
- target_function: N/A
- target_file: scripts/verify_interact_036b.py
- lock_kind: file_sha
- bump_when: scripts/verify_interact_036b.py 文件 sha256 变化 (示范脚本任何字节改动)
- bump_protocol: 重算 sha256 of scripts/verify_interact_036b.py 并更新常量
- rationale: V2 锁示范 verify-script 整体, 防 V0 sha lock 版本号字段示范被悄改/回滚
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
TARGET = SCRIPTS / "verify_interact_036b.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_TARGET_FILE_SHA = (
    "bc8f6f0b5cf9708620f1187c5a42fe2f1b440a15c0c2fe31c085aa039bf71f0b"
)
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "4293685a1a4dedff62a016127cc428f916a56ade40d26a1db3457f9fe9bd6079"
)

DOCSTRING_SENTINEL = "INFRA_P301_DOC_SHA_LOCKS"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P301-backlog-v0-sha-lock-version-field"

VERSION_CONST = "V0_SELF_SHA_LOCK_VERSION"
BUMPED_AT_CONST = "V0_SELF_SHA_LOCK_BUMPED_AT"
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P301][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_module_constant(src: str, name: str):
    """Parse `src` and return the literal value of a module-level
    ``name = <literal>`` assignment. Returns ``(found, value)``.
    """
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    try:
                        value = ast.literal_eval(node.value)
                    except Exception:
                        return True, None
                    return True, value
    return False, None


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


def v2_target_file_sha() -> None:
    if not TARGET.is_file():
        _emit("V2_target_file_sha", False, f"missing {TARGET}")
        return
    got = _file_sha(TARGET)
    if EXPECTED_TARGET_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V2_target_file_sha",
            False,
            f"placeholder; bump EXPECTED_TARGET_FILE_SHA={got}",
        )
    else:
        _emit(
            "V2_target_file_sha",
            got == EXPECTED_TARGET_FILE_SHA,
            f"got={got[:16]} expect={EXPECTED_TARGET_FILE_SHA[:16]}",
        )


def v3_v4_version_field_present() -> None:
    if not TARGET.is_file():
        _emit("V3_version_const_present", False, "target missing")
        return
    src = TARGET.read_text(encoding="utf-8")

    # VERSION_CONST
    found, value = _read_module_constant(src, VERSION_CONST)
    if not found:
        _emit(
            "V3_version_const_present",
            False,
            f"{VERSION_CONST} not found at module level",
        )
        _emit("V4_version_range", False, "skip: const missing")
    else:
        is_int = isinstance(value, int) and not isinstance(value, bool)
        _emit(
            "V3_version_const_present",
            is_int,
            f"{VERSION_CONST}={value!r} is_int={is_int}",
        )
        in_range = is_int and value >= 1
        _emit(
            "V4_version_range",
            in_range,
            f"{VERSION_CONST}={value!r} >=1 ? {in_range}",
        )

    # BUMPED_AT_CONST
    found, value = _read_module_constant(src, BUMPED_AT_CONST)
    if not found:
        _emit(
            "V3_bumped_at_const_present",
            False,
            f"{BUMPED_AT_CONST} not found at module level",
        )
        _emit("V4_bumped_at_format", False, "skip: const missing")
        return
    is_str = isinstance(value, str)
    _emit(
        "V3_bumped_at_const_present",
        is_str,
        f"{BUMPED_AT_CONST}={value!r} is_str={is_str}",
    )
    fmt_ok = is_str and bool(ISO_DATE_RE.match(value))
    _emit(
        "V4_bumped_at_format",
        fmt_ok,
        f"{BUMPED_AT_CONST}={value!r} matches YYYY-MM-DD ? {fmt_ok}",
    )


def v4_v0_lock_emits_metadata() -> None:
    if not TARGET.is_file():
        _emit("V4_v0_lock_emits_metadata", False, "target missing")
        return
    src = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn_src = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "v0_self_sha_lock":
            fn_src = ast.get_source_segment(src, node) or ""
            break
    if fn_src is None:
        _emit(
            "V4_v0_lock_emits_metadata",
            False,
            "v0_self_sha_lock function not found",
        )
        return
    has_version = VERSION_CONST in fn_src
    has_bumped = BUMPED_AT_CONST in fn_src
    ok = has_version and has_bumped
    _emit(
        "V4_v0_lock_emits_metadata",
        ok,
        f"has_version={has_version} has_bumped_at={has_bumped}",
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
    v2_target_file_sha()
    v3_v4_version_field_present()
    v4_v0_lock_emits_metadata()
    v5_reviewer_gate()

    failed = sum(1 for _, ok, _ in _results if not ok)
    print(
        f"[verify_infra_P301] summary: total={len(_results)} failed={failed}",
        flush=True,
    )
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
