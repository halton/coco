#!/usr/bin/env python3
"""verify_infra_P313 V1-V4: 锁 ``parse_area_from_verify_path`` regex 策略.

infra-P313-parse-area-regex-policy (phase-66 #13):

背景
----
``parse_area_from_verify_path`` 当前 regex 限定 area 段为**纯字母** (无数字):

    ^verify_([a-z][a-z]*)_(\\d{3})(?:[._].*)?$

phase-63 #4 Reviewer P2 提出: 若未来引入 l10n / i18n 风格带数字的 area
(e.g. ``verify_i18n_002.py`` 中 ``i18n``), 需明确策略。

P313 策略 (POLICY: REJECT-DIGIT-AREA)
-------------------------------------
**显式拒绝数字 area**。Coco 项目当前所有 area 仅含字母 (``infra``, ``audio``,
``robot``, ``companion``, ``interact``, ``vision``, ``perception``, ``uat``)。
若日后必须引入带数字 area, 应另立 feature 改 regex + 升 V6 EXPECTED 锁,
而不是默默放宽。

本 verify 通过四档锁固化策略:

INFRA_P313_LOCKS
----------------
- V1 regex pattern contract (字面值锁): EXPECTED_RE_VERIFY_AREA_PATTERN
- V2 func body sha 自锁: EXPECTED_PARSE_AREA_FUNC_SHA
- V3 behavior matrix (正反样本 oracle): parse_area_from_verify_path 在
  代表性样本上的输入→输出对必须稳定:
    * 字母 area / 含 NNN: returns area
    * 数字 area / 含 NNN: returns None (策略要求拒绝)
    * 非 verify_*.py 前缀 / 无 NNN: returns None
- V4 docstring policy marker: docstring 必须含 "REJECT-DIGIT-AREA" 字面
  (作为人类可读的策略标记, 防被悄改成 ACCEPT 后这里也跟着改, 因为 V1/V2
  会同步漂移并要求 bump, bump 时必须重读 docstring)

## Lock: EXPECTED_RE_VERIFY_AREA_PATTERN
- target_function: N/A
- target_file: scripts/_verify_lib.py
- lock_kind: literal_string
- bump_when: ``_RE_VERIFY_AREA`` regex pattern 字面值变化
- bump_protocol: 重新评估 P313 策略 → 若放宽数字 area, 立新 feature 改本
  EXPECTED + 文档化新策略; 不允许在本 verify 内悄悄跟改字面
- rationale: 锁 regex 字面, 防数字 area 被悄悄放进 area 段

## Lock: EXPECTED_PARSE_AREA_FUNC_SHA
- target_function: parse_area_from_verify_path
- target_file: scripts/_verify_lib.py
- lock_kind: ast_func_sha
- bump_when: parse_area_from_verify_path 实现变化
- bump_protocol: ``func_sha_by_name("parse_area_from_verify_path",
  "scripts/_verify_lib.py")`` 重算后填入
- rationale: 锁函数体, 防 regex 字面没动但函数逻辑被旁路 (e.g. 加
  ``str.isdigit`` 后处理 silent-accept 数字 area)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    func_sha_by_name,
    parse_area_from_verify_path,
    _RE_VERIFY_AREA,
    verify_summary_exit,
)

# V1: regex pattern literal lock (REJECT-DIGIT-AREA 策略)
EXPECTED_RE_VERIFY_AREA_PATTERN = r"^verify_([a-z][a-z]*)_(\d{3})(?:[._].*)?$"

# V2: parse_area_from_verify_path AST func sha lock
EXPECTED_PARSE_AREA_FUNC_SHA = (
    "1d9cc4ec9fbc8aa0b1ba3de9965da58142e6edf3784f4c38b1fded9fe223080a"
)

# V3 behavior matrix: (path, expected_area_or_None)
_BEHAVIOR_MATRIX: List[Tuple[str, str | None]] = [
    # 正样本: 纯字母 area + NNN
    ("scripts/verify_infra_079.py", "infra"),
    ("scripts/verify_audio_003.py", "audio"),
    ("scripts/verify_robot_001_daemon.py", "robot"),
    ("verify_companion_042.py", "companion"),
    ("scripts/verify_perception_005.something.py", "perception"),
    # 反样本: 数字 area (P313 REJECT 策略)
    ("scripts/verify_i18n_002.py", None),
    ("scripts/verify_l10n_001.py", None),
    ("scripts/verify_2dgfx_001.py", None),
    ("scripts/verify_p99_001.py", None),
    # 反样本: 非 verify_*.py / 无 NNN
    ("scripts/_verify_lib.py", None),
    ("scripts/verify_publish.py", None),
    ("scripts/verify_infra.py", None),
    ("scripts/verify_infra_12.py", None),  # NNN 必须 3 位
    ("scripts/verify_infra_1234.py", None),
    ("verify_infra_079.txt", None),
    ("", None),
]

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P313][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, bool(ok), detail))


def v1_regex_pattern_literal() -> None:
    actual = _RE_VERIFY_AREA.pattern
    ok = actual == EXPECTED_RE_VERIFY_AREA_PATTERN
    _emit(
        "V1_regex_pattern_literal",
        ok,
        f"expected={EXPECTED_RE_VERIFY_AREA_PATTERN!r} actual={actual!r}",
    )


def v2_parse_area_func_sha() -> None:
    actual = func_sha_by_name(LIB, "parse_area_from_verify_path")
    ok = actual == EXPECTED_PARSE_AREA_FUNC_SHA
    _emit(
        "V2_parse_area_func_sha",
        ok,
        f"expected={EXPECTED_PARSE_AREA_FUNC_SHA[:16]} actual={actual[:16]}",
    )


def v3_behavior_matrix() -> None:
    mismatches: List[str] = []
    for path, want in _BEHAVIOR_MATRIX:
        got = parse_area_from_verify_path(path)
        if got != want:
            mismatches.append(f"{path!r} got={got!r} want={want!r}")
    ok = not mismatches
    detail = (
        f"checked={len(_BEHAVIOR_MATRIX)} mismatches={len(mismatches)}"
        if ok
        else "; ".join(mismatches[:5])
    )
    _emit("V3_behavior_matrix", ok, detail)


def v4_docstring_policy_marker() -> None:
    """锁本 verify 自身 docstring 含 REJECT-DIGIT-AREA 字面。

    防策略被悄悄翻转 (e.g. 改成 accept-digit) 而 verify 还在叫 P313:
    bump V1/V2 时必须同步翻 docstring 文字, 这里给出最后一道人类可读的
    sanity check。
    """
    src = Path(__file__).read_text(encoding="utf-8")
    has_marker = "REJECT-DIGIT-AREA" in src
    _emit(
        "V4_docstring_policy_marker",
        has_marker,
        f"has_marker={has_marker}",
    )


def main() -> int:
    v1_regex_pattern_literal()
    v2_parse_area_func_sha()
    v3_behavior_matrix()
    v4_docstring_policy_marker()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(
        f"[verify_infra_P313][SUMMARY] "
        f"{'FAIL ' + str(failed) + '/' + str(total) if failed else 'ALL PASS (' + str(total) + ' checks)'}",
        flush=True,
    )
    verify_summary_exit(failed)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
