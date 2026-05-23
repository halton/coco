#!/usr/bin/env python3
"""verify_infra_V6_strict_area V1-V6: 锁 V6 NNN→area opt-in 严格匹配模式.

infra-V6-backlog-strict-area-match-mode (phase-63 #4):

V6 反向 sha lock scan 当前用 ``_<NNN>.py$`` 宽匹配填充候选, 同 NNN 跨 area
(infra / robot / audio / companion / ...) 候选会一并列出, 造成假阳性。本
verify 锁 ``_verify_lib`` 中新引入的三个 helper:

- ``parse_area_from_verify_path(path)``: 从 ``verify_<area>_<NNN>(...).py`` 推断 area
- ``scan_unknown_area_nnns(scripts_dir, known_area_nnns=None)``: 列出未登记
  NNN, 用于 strict 模式 EXPECTED 基准
- ``scan_reverse_sha_lock_consistency_strict(scripts_dir, strict_area_match=False)``:
  V6 主入口 (loose 默认, strict 可选过滤跨 area 候选)

INFRA_V6_STRICT_AREA_SHA_LOCKS
------------------------------
- ``scripts/_verify_lib.py`` ``parse_area_from_verify_path`` func sha:
  EXPECTED_PARSE_AREA_FUNC_SHA
- ``scripts/_verify_lib.py`` ``scan_unknown_area_nnns`` func sha:
  EXPECTED_SCAN_UNKNOWN_FUNC_SHA
- ``scripts/_verify_lib.py`` ``scan_reverse_sha_lock_consistency_strict``
  func sha: EXPECTED_SCAN_STRICT_FUNC_SHA

校验层级 (V1-V6, 共 6 checks):

- V1 self file sha (本 verify 文件自锁, V8 pragma 兼容)
- V2 loose 模式基准 (scan_reverse_sha_lock_consistency_strict 默认 strict=False
  返回结果, 与 verify_reverse_sha_lock_consistency loose 版 orphans 个数一致)
- V3 strict 模式当前 repo unknown 集合 sha 锁 (空 known 表 → 当前所有可推断 area
  NNN 都计入 unknown; 期 sha == EXPECTED_STRICT_UNKNOWN_SHA256)
- V4 strict 模式 mutant: 临时新增 verify_infra_999.py 不登记 area → unknown
  集合 += "999" → sha 应不等; 清理后恢复
- V5 strict 模式 mutant: 临时把 unknown 中某 NNN 加入 known 表 → unknown 集合
  应减少 1 项 → sha 应不等; 恢复
- V6 helper func sha 锁 (三个 helper, 任一漂移 → FAIL)

退出码: 0=ALL PASS, 2=任一 FAIL.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    parse_area_from_verify_path,
    func_sha_by_name,
    scan_unknown_area_nnns,
    verify_reverse_sha_lock_consistency,
    scan_reverse_sha_lock_consistency_strict,
    verify_summary_exit,
)

# V1: self file sha (V8 pragma 兼容; 静态读取 + 抹去本字段)
EXPECTED_SELF_FILE_SHA = (
    "9b0898bfbefba171cf357455621de57d2562311f88f6ac38ce600cdc57299465"
)
# V3: strict 当前 repo unknown 集合 sha (空 known 表)
# 由 hashlib.sha256(",".join(sorted_unknown_nnns).encode()).hexdigest() 得到
EXPECTED_STRICT_UNKNOWN_SHA256 = (
    "ce0f5023e7976e8aa6000fc74560f7b31fdb5d9199dadc2c642fb98f92484d95"
)
EXPECTED_STRICT_UNKNOWN_COUNT = 109

# V6: helper func sha 锁
EXPECTED_PARSE_AREA_FUNC_SHA = (
    "1d9cc4ec9fbc8aa0b1ba3de9965da58142e6edf3784f4c38b1fded9fe223080a"
)
EXPECTED_SCAN_UNKNOWN_FUNC_SHA = (
    "da47158955667f677b20edec9b19b0036076f52731eb6c531c904b22baa49622"
)
EXPECTED_SCAN_STRICT_FUNC_SHA = (
    "58d3758321af2a54ad4287b776b5453f191ebbdce1991265a8477c9276bb07d0"
)

LIB_PY = SCRIPTS / "_verify_lib.py"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_V6_strict_area][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, bool(ok), detail))


def _unknown_sha(scripts_dir: Path, known: frozenset[str] | None = None) -> str:
    unk = scan_unknown_area_nnns(scripts_dir, known_area_nnns=known)
    return hashlib.sha256(",".join(unk).encode()).hexdigest()


def _self_file_sha_without_field() -> str:
    """计算本 verify 文件的 sha, 但抹掉 EXPECTED_SELF_FILE_SHA 行的 hex 字段。

    避免自锁 chicken-and-egg: 锁字段本身参与 sha 会无解。
    替换策略: 把该常量字面 hex 值替换为 64 个 '0' 后计算。
    """
    src = Path(__file__).read_text(encoding="utf-8")
    placeholder = "0" * 64
    # Normalize: 把字面 EXPECTED_SELF_FILE_SHA = "..."(64-hex) 的 hex 替换为 placeholder
    import re
    pat = re.compile(
        r'(EXPECTED_SELF_FILE_SHA\s*=\s*\(\s*\n\s*")[0-9a-f]{64}(")',
        re.MULTILINE,
    )
    new_src = pat.sub(r"\1" + placeholder + r"\2", src)
    return hashlib.sha256(new_src.encode("utf-8")).hexdigest()


def v1_self_file_sha() -> None:
    actual = _self_file_sha_without_field()
    ok = (actual == EXPECTED_SELF_FILE_SHA) or (EXPECTED_SELF_FILE_SHA == "0" * 64)
    # 首次写入时 EXPECTED 占位 (全 0), 此时报 PASS + 提示 actual 供锁链回填
    if EXPECTED_SELF_FILE_SHA == "0" * 64:
        _emit("V1_self_file_sha", True, f"PLACEHOLDER actual={actual[:16]}... (bootstrap)")
    else:
        _emit("V1_self_file_sha", ok, f"expected={EXPECTED_SELF_FILE_SHA[:16]} actual={actual[:16]}")


def v2_loose_baseline() -> None:
    loose_strict_off = scan_reverse_sha_lock_consistency_strict(SCRIPTS, strict_area_match=False)
    loose_legacy = verify_reverse_sha_lock_consistency(SCRIPTS)
    ok = (
        loose_strict_off["scanned_count"] == loose_legacy["scanned_count"]
        and len(loose_strict_off["orphans"]) == len(loose_legacy["orphans"])
        and loose_strict_off["strict_area_match"] is False
    )
    _emit(
        "V2_loose_mode_baseline",
        ok,
        f"strict_off_orphans={len(loose_strict_off['orphans'])} legacy_orphans={len(loose_legacy['orphans'])}",
    )


def v3_strict_unknown_baseline() -> None:
    actual_sha = _unknown_sha(SCRIPTS, known=None)
    unk = scan_unknown_area_nnns(SCRIPTS, known_area_nnns=None)
    ok = (actual_sha == EXPECTED_STRICT_UNKNOWN_SHA256) and (len(unk) == EXPECTED_STRICT_UNKNOWN_COUNT)
    _emit(
        "V3_strict_mode_current_repo",
        ok,
        f"count={len(unk)} (expected {EXPECTED_STRICT_UNKNOWN_COUNT}) sha={actual_sha[:16]}",
    )


def v4_mutant_unknown_added() -> None:
    """临时新增 verify_infra_999.py 不登记 area, strict unknown 应增 1 项 → sha 不等。"""
    mutant = SCRIPTS / "verify_infra_999.py"
    if mutant.exists():
        _emit("V4_strict_mode_mutant_unknown_added", False, "verify_infra_999.py 已存在, 拒绝覆盖")
        return
    try:
        mutant.write_text(
            '"""verify_infra_999 mutant: 临时文件, V4 测试用, 不应入库。"""\n',
            encoding="utf-8",
        )
        new_sha = _unknown_sha(SCRIPTS, known=None)
        unk = scan_unknown_area_nnns(SCRIPTS, known_area_nnns=None)
        # 期望: sha 与 EXPECTED 不等 (因为多了 "999"), 且 "999" 在 unknown 列表中
        ok = (new_sha != EXPECTED_STRICT_UNKNOWN_SHA256) and ("999" in unk)
        _emit(
            "V4_strict_mode_mutant_unknown_added",
            ok,
            f"new_count={len(unk)} new_sha={new_sha[:16]} '999_in_unk'={'999' in unk}",
        )
    finally:
        try:
            mutant.unlink()
        except FileNotFoundError:
            pass
    # 清理后再做一次 baseline 比对, 确保 unknown 集合还原
    restored_sha = _unknown_sha(SCRIPTS, known=None)
    if restored_sha != EXPECTED_STRICT_UNKNOWN_SHA256:
        _emit(
            "V4_cleanup_baseline_restored",
            False,
            f"after cleanup sha={restored_sha[:16]} != expected {EXPECTED_STRICT_UNKNOWN_SHA256[:16]}",
        )


def v5_mutant_unknown_resolved() -> None:
    """临时把 unknown 中第一个 NNN 加入 known 表, unknown 减少 1 项 → sha 不等。"""
    base_unk = scan_unknown_area_nnns(SCRIPTS, known_area_nnns=None)
    if not base_unk:
        _emit("V5_strict_mode_mutant_unknown_resolved", False, "baseline unknown 为空, 无法 mutant")
        return
    pick = base_unk[0]
    known = frozenset({pick})
    new_unk = scan_unknown_area_nnns(SCRIPTS, known_area_nnns=known)
    new_sha = hashlib.sha256(",".join(new_unk).encode()).hexdigest()
    ok = (
        new_sha != EXPECTED_STRICT_UNKNOWN_SHA256
        and len(new_unk) == EXPECTED_STRICT_UNKNOWN_COUNT - 1
        and pick not in new_unk
    )
    _emit(
        "V5_strict_mode_mutant_unknown_resolved",
        ok,
        f"pick={pick} new_count={len(new_unk)} new_sha={new_sha[:16]}",
    )


def v6_helper_func_sha() -> None:
    cases = [
        ("parse_area_from_verify_path", EXPECTED_PARSE_AREA_FUNC_SHA),
        ("scan_unknown_area_nnns", EXPECTED_SCAN_UNKNOWN_FUNC_SHA),
        ("scan_reverse_sha_lock_consistency_strict", EXPECTED_SCAN_STRICT_FUNC_SHA),
    ]
    all_ok = True
    details: List[str] = []
    for func_name, expected in cases:
        actual = func_sha_by_name(LIB_PY, func_name)
        ok = actual == expected
        if not ok:
            all_ok = False
        details.append(f"{func_name}={'PASS' if ok else f'FAIL(expected={expected[:8]} actual={actual[:8]})'}")
    _emit("V6_helper_func_sha_lock", all_ok, "; ".join(details))


def main() -> int:
    v1_self_file_sha()
    v2_loose_baseline()
    v3_strict_unknown_baseline()
    v4_mutant_unknown_added()
    v5_mutant_unknown_resolved()
    v6_helper_func_sha()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(
        f"[verify_infra_V6_strict_area][SUMMARY] "
        f"{'FAIL ' + str(failed) + '/' + str(total) if failed else 'ALL PASS (' + str(total) + ' checks)'}",
        flush=True,
    )
    verify_summary_exit(failed)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
