#!/usr/bin/env python3
"""verify_infra_099 V0-V5: V6 regex 放宽时锁定 assert_unique_needle 纪律。

infra-V6-backlog-regex-unique-needle-discipline (phase-49 #3.49):
infra-V6 closeout Reviewer 建议: 未来 V6 若放宽 regex 识别更多锁式样 (例如
``r'verify_(\\w+)_(\\d+)'`` 变体或新的 EXPECTED_* 名空间), 必须同步应用
``assert_unique_needle`` 原则 — regex 抓到的需要后续 in-place mutate 的字面值
在源文本中应唯一存在; 否则 ``text.replace(needle, ...)`` 会同时改多处, V3
mutant 反证假阳性 PASS (P258 教训, 见 infra-040-backlog)。

本 verify (099) 把这条纪律锁成机器检查:

- 锁住 ``_verify_lib.assert_unique_needle`` helper def 公开 (def + __all__) +
  接受 ``(text, needle)`` 签名;
- _verify_lib.py file sha 与 helper func sha (cascade bump 时整链报警);
- 真跑 helper 正例: 唯一需要 → 无 raise;
- 真跑 helper 反例: 多次/零次出现 → ValueError, 错误文案包含 count;
- V6 regex 应用纪律演示: 把 _verify_lib 自身的 V6 反向锁 regex (singleline /
  verify_id / expected_pattern) 应用到合成源, 对每条 regex 抓到的字面值再
  call assert_unique_needle, 验证唯一性收口纪律在合成 happy path 下 PASS,
  在合成 dup 路径下被 helper 拒绝。

INFRA_099_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:assert_unique_needle`` func sha:
  EXPECTED_UNIQUE_NEEDLE_HELPER_FUNC_SHA

校验层级 (V0-V5, 共 14 checks):

- V0 scaffolding (×5):
  - V0_verify_lib_exists
  - V0_helper_def_present (def assert_unique_needle( in lib)
  - V0_helper_in_all (__all__ contains name)
  - V0_self_main_entry
  - V0_v6_regex_constants_present (lib 含 _RE_REVLOCK_SINGLELINE/
    _RE_REVLOCK_VERIFY_ID/_RE_REVLOCK_EXPECTED_PATTERN 三条 regex)
- V1 self main() func sha 自锁 (1 check)
- V2 _verify_lib file sha (1 check)
- V3 assert_unique_needle helper func sha (1 check)
- V4 行为校验 (5 checks):
  - V4_1 helper 正例: 唯一需要 → 无 raise
  - V4_2 helper 反例: 2 次出现 → ValueError, msg contains count=2
  - V4_3 helper 反例: 0 次出现 → ValueError, msg contains count=0
  - V4_4 V6 singleline regex 抓到唯一 sha 字面值 → helper PASS
  - V4_5 V6 singleline regex 抓到重复 sha 字面值 → helper raises
- V5 reviewer_lgtm_gate (真门: V5 pending 期间预期 FAIL)

退出码: 0=ALL PASS, 2=任一 FAIL (verify_summary_exit).
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
LIB = SCRIPTS / "_verify_lib.py"
REAL_FEATURE_LIST = REPO / "feature_list.json"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_unique_needle,
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_SELF_MAIN_FUNC_SHA = (
    "23b30488714538e5dc1090c8a54f1da2282815c5ffdd60489da4277b8c67eeaa"
)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "43d352534301242f13ef33909cdba2585f8f18734d9e052f2e0de0d075955d75"
)
EXPECTED_UNIQUE_NEEDLE_HELPER_FUNC_SHA = (
    "3b92e4f2a058b1bd7a26091d3adc6d7fa5efa06b0d187fad738ddd1217ed58f4"
)

DOCSTRING_SENTINEL = "INFRA_099_SHA_LOCKS"
HELPER_NAME = "assert_unique_needle"
V5_GATE_FEATURE_ID = "infra-V6-backlog-regex-unique-needle-discipline"

# V6 反向锁 singleline regex (与 _verify_lib._RE_REVLOCK_SINGLELINE 同源, 显式
# 复刻一份以便 V4 演示 "regex + assert_unique_needle" 纪律。一旦 lib 内 regex
# 被改, V2/V3 file/func sha 锁会先报警, 不会出现行为漂移而 verify 不知情。)
_RE_V6_SINGLELINE_LOCAL = re.compile(
    r'^([A-Z_][A-Z0-9_]*)\s*=\s*["\']([0-9a-f]{64})["\']\s*(?:#.*)?$'
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_099][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, bool(ok), detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_verify_lib_exists", LIB.is_file(), f"path={LIB}")
    if not LIB.is_file():
        return
    src = LIB.read_text(encoding="utf-8")
    _emit(
        "V0_helper_def_present",
        f"def {HELPER_NAME}(" in src,
        f"expect 'def {HELPER_NAME}(' in lib",
    )
    tree = ast.parse(src)
    all_names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for el in node.value.elts:
                            if isinstance(el, ast.Constant) and isinstance(
                                el.value, str
                            ):
                                all_names.append(el.value)
    _emit(
        "V0_helper_in_all",
        HELPER_NAME in all_names,
        f"__all__ contains {len(all_names)} names",
    )
    self_src = Path(__file__).read_text(encoding="utf-8")
    _emit(
        "V0_self_main_entry",
        'if __name__ == "__main__":' in self_src and "def main(" in self_src,
        "expect __main__ guard + def main()",
    )
    needed = (
        "_RE_REVLOCK_SINGLELINE",
        "_RE_REVLOCK_VERIFY_ID",
        "_RE_REVLOCK_EXPECTED_PATTERN",
    )
    missing = [n for n in needed if n not in src]
    _emit(
        "V0_v6_regex_constants_present",
        len(missing) == 0,
        f"needed={list(needed)} missing={missing}",
    )


# ---------------------------------------------------------------------------
# V1: self main() func sha
# ---------------------------------------------------------------------------
def v1_self_func_sha() -> None:
    self_path = Path(__file__)
    try:
        got = func_sha_by_name(self_path, "main")
    except Exception as e:
        _emit("V1_self_main_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_self_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
        return
    _emit(
        "V1_self_main_func_sha",
        got == EXPECTED_SELF_MAIN_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V2: _verify_lib.py file sha
# ---------------------------------------------------------------------------
def v2_verify_lib_file_sha() -> None:
    got = _file_sha(LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V2_lib_file_sha",
            False,
            f"placeholder; bump EXPECTED_VERIFY_LIB_FILE_SHA={got}",
        )
        return
    _emit(
        "V2_lib_file_sha",
        got == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: assert_unique_needle helper func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, HELPER_NAME)
    except Exception as e:
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_UNIQUE_NEEDLE_HELPER_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V3_helper_func_sha",
            False,
            f"placeholder; bump EXPECTED_UNIQUE_NEEDLE_HELPER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_UNIQUE_NEEDLE_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_UNIQUE_NEEDLE_HELPER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为正反例 + V6 regex 纪律演示
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # V4_1: 唯一 needle → 无 raise
    needle_unique = "0" * 64
    text_unique = (
        "alpha\nEXPECTED_VERIFY_LIB_FILE_SHA = \""
        + needle_unique
        + "\"\nbeta\n"
    )
    try:
        assert_unique_needle(text_unique, needle_unique)
        v4_1_ok = True
        v4_1_detail = f"count={text_unique.count(needle_unique)}"
    except Exception as e:
        v4_1_ok = False
        v4_1_detail = f"unexpected raise: {e!r}"
    _emit("V4_1_unique_needle_no_raise", v4_1_ok, v4_1_detail)

    # V4_2: 2 次出现 → ValueError, msg 含 count=2
    text_dup = text_unique + f"EXPECTED_X_FUNC_SHA = \"{needle_unique}\"\n"
    try:
        assert_unique_needle(text_dup, needle_unique)
        v4_2_ok = False
        v4_2_detail = "expected ValueError but none raised"
    except ValueError as e:
        msg = str(e)
        v4_2_ok = "count=2" in msg
        v4_2_detail = f"raised ValueError; msg={msg[:80]!r}"
    except Exception as e:
        v4_2_ok = False
        v4_2_detail = f"wrong exc type: {type(e).__name__} {e!r}"
    _emit("V4_2_duplicate_needle_raises_count2", v4_2_ok, v4_2_detail)

    # V4_3: 0 次出现 → ValueError, msg 含 count=0
    text_none = "no sha here\n"
    try:
        assert_unique_needle(text_none, needle_unique)
        v4_3_ok = False
        v4_3_detail = "expected ValueError but none raised"
    except ValueError as e:
        msg = str(e)
        v4_3_ok = "count=0" in msg
        v4_3_detail = f"raised ValueError; msg={msg[:80]!r}"
    except Exception as e:
        v4_3_ok = False
        v4_3_detail = f"wrong exc type: {type(e).__name__} {e!r}"
    _emit("V4_3_missing_needle_raises_count0", v4_3_ok, v4_3_detail)

    # V4_4: V6 singleline regex 抓到唯一 sha 字面值 → helper PASS
    synthetic_src_unique = (
        "# synthetic source for V6 unique-needle discipline test\n"
        "EXPECTED_FOO_FILE_SHA = \"" + ("1" * 64) + "\"\n"
        "OTHER = 42\n"
    )
    matches_unique = [
        m for m in (
            _RE_V6_SINGLELINE_LOCAL.match(line)
            for line in synthetic_src_unique.splitlines()
        ) if m is not None
    ]
    if len(matches_unique) != 1:
        _emit(
            "V4_4_v6_regex_unique_capture_passes",
            False,
            f"setup err: matches={len(matches_unique)} expect 1",
        )
    else:
        captured_sha = matches_unique[0].group(2)
        try:
            assert_unique_needle(synthetic_src_unique, captured_sha)
            v4_4_ok = True
            v4_4_detail = f"captured={captured_sha[:16]} unique"
        except Exception as e:
            v4_4_ok = False
            v4_4_detail = f"unexpected raise: {e!r}"
        _emit("V4_4_v6_regex_unique_capture_passes", v4_4_ok, v4_4_detail)

    # V4_5: V6 singleline regex 抓到的字面值在源中实际出现 2 次 → helper raises
    synthetic_src_dup = (
        "EXPECTED_FOO_FILE_SHA = \"" + ("2" * 64) + "\"\n"
        "EXPECTED_BAR_FILE_SHA = \"" + ("2" * 64) + "\"\n"
    )
    matches_dup = [
        m for m in (
            _RE_V6_SINGLELINE_LOCAL.match(line)
            for line in synthetic_src_dup.splitlines()
        ) if m is not None
    ]
    if len(matches_dup) != 2:
        _emit(
            "V4_5_v6_regex_duplicate_capture_raises",
            False,
            f"setup err: matches={len(matches_dup)} expect 2",
        )
    else:
        captured_sha_dup = matches_dup[0].group(2)
        try:
            assert_unique_needle(synthetic_src_dup, captured_sha_dup)
            v4_5_ok = False
            v4_5_detail = "expected ValueError but none raised"
        except ValueError as e:
            msg = str(e)
            v4_5_ok = "count=2" in msg
            v4_5_detail = f"raised ValueError; msg={msg[:80]!r}"
        except Exception as e:
            v4_5_ok = False
            v4_5_detail = f"wrong exc type: {type(e).__name__} {e!r}"
        _emit("V4_5_v6_regex_duplicate_capture_raises", v4_5_ok, v4_5_detail)


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (真门: ok is True)
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
    )
    _emit(
        "V5_reviewer_lgtm_gate",
        bool(result["ok"]),
        f"target={V5_GATE_FEATURE_ID} helper_ok={result['ok']} "
        f"verdict={result['verdict']!r} kind={result['reviewer_kind']!r} "
        f"summary_len={result['summary_len']} reason={result['reason']!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_func_sha()
    v2_verify_lib_file_sha()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    failed_tags = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_099][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_099][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
