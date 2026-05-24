#!/usr/bin/env python3
"""verify_infra-039-backlog-v7-filter-substring-fix.

phase-67 #14 backlog 入账 feature.

verify_infra_039.py:v7_filter_behavior 中 ``V7_filter_text_mode`` check 原本用
substring pattern ``"verify_infra_060"`` 跑 ``dump_v4_sha_graph.py --filter``,
但该 pattern 是 ``re.search`` 命中, 会同时命中 ``scripts/verify_infra_060.py`` 与
``scripts/verify_infra_060_backlog_real_unknown_count_fix.py`` 两个文件
(``across 2 files``), 导致期望 ``across 1 files ===`` 不成立, 长期 baseline FAIL
(已在 phase-67 #13 closeout evidence 中登记为 pre-existing baseline)。

本 feat 把 pattern 改为 regex 锚尾 ``r"verify_infra_060\\.py$"``, 精确命中单一
源文件, V7_filter_text_mode 恢复 PASS。**不动** ``scripts/dump_v4_sha_graph.py``
的 ``filter_locks`` 实现 (它本来就是 ``re.search`` 通用 regex, 行为正确; 修复
落在测试侧 pattern 的严格度)。

本 verifier 锁定以下事实:

- V0: scripts/verify_infra_039.py 与 scripts/dump_v4_sha_graph.py 存在.
- V1: verify_infra_039.py 中 V7_filter_text_mode 使用的 pattern 字面量含
  ``\\.py$`` 锚尾 (regex 严格匹配, 否决 substring 双命中).
- V2: dump_v4_sha_graph.py --filter r'verify_infra_060\\.py$' 输出 SUMMARY
  ``across 1 files ===`` (端到端验证 pattern 行为).
- V3: dump_v4_sha_graph.py --filter 'verify_infra_060' (旧 substring pattern)
  输出 SUMMARY ``across 2 files ===`` (反证: 旧 pattern 确实双命中, 解释了
  baseline FAIL 的根因).
- V4: 子进程跑 verify_infra_039.py rc=0 且 V7_filter_text_mode PASS
  (端到端 baseline FAIL 已被消除).
- V5: 自身 main func sha 自锁.
- V6: 不触碰 scripts/_verify_lib.py (file sha 锁, cascade hygiene).

INFRA_039_BACKLOG_V7_FILTER_SUBSTRING_FIX_LOCKS
-----------------------------------------------
- 期望 _verify_lib.py file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- self main func sha: EXPECTED_SELF_MAIN_FUNC_SHA

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_INFRA_039 = SCRIPTS / "verify_infra_039.py"
DUMP_PY = SCRIPTS / "dump_v4_sha_graph.py"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    func_sha_by_name,
    verify_summary_exit,
)

# _verify_lib.py 不许改 (硬规则)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "1990a9b61d3b14c9b827a23188a46001be2a208234352e1bc7af955285578f50"
)
# self main func sha 自锁 (首跑 __BUMP_ME__, 自动 bump 后回填)
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "5cf6346a398fa7b5a4c220d2f77df26a118db0373e9d6ec85736e502bea454ff"
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(
        f"[verify_infra_039_backlog_v7_filter_substring_fix][{mark}] {tag} {detail}",
        flush=True,
    )
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def v0_scaffolding() -> None:
    _emit(
        "V0_verify_infra_039_exists",
        VERIFY_INFRA_039.is_file(),
        f"path={VERIFY_INFRA_039.name}",
    )
    _emit("V0_dump_py_exists", DUMP_PY.is_file(), f"path={DUMP_PY.name}")
    _emit("V0_verify_lib_exists", VERIFY_LIB.is_file(), f"path={VERIFY_LIB.name}")


def v1_pattern_literal_is_strict_regex() -> None:
    """verify_infra_039.py 中 V7_filter_text_mode 的 pattern literal 必须含
    ``\\.py$`` 锚尾 (regex 形态), 防止回退到 substring 双命中。"""
    if not VERIFY_INFRA_039.is_file():
        _emit("V1_pattern_literal_is_strict_regex", False, "scaffolding missing")
        return
    src = VERIFY_INFRA_039.read_text(encoding="utf-8")
    # 抽取 v7 函数体附近的 "--filter" 调用参数; 简化策略: 全文检索
    # 期望: 出现 r"verify_infra_060\.py$" 字面量 (raw string + 锚尾).
    # 同时不应再有裸 "verify_infra_060" (无 .py$ 锚) 出现在 v7_filter_behavior
    # 中作为 --filter 实参 (V7_filter_partial_hit 用的是 "verify_infra_060" 字面量,
    # 那是 JSON 模式 partial_hit 测试, 不在本检查范围)。
    # 这里只锁 text 模式的严格 pattern 存在。
    has_strict = bool(
        re.search(
            r'"--filter"\s*,\s*r?"verify_infra_060\\\.py\$"',
            src,
        )
    )
    _emit(
        "V1_pattern_literal_is_strict_regex",
        has_strict,
        "found r'verify_infra_060\\.py$' --filter literal" if has_strict
        else "MISSING strict regex literal in verify_infra_039.py",
    )


def v2_dump_strict_pattern_hits_one_file() -> None:
    """端到端: dump_v4_sha_graph.py --filter 严格 regex → across 1 files."""
    proc = subprocess.run(
        [sys.executable, str(DUMP_PY), "--filter", r"verify_infra_060\.py$"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=30,
    )
    rc = proc.returncode
    has_one_file = "across 1 files ===" in proc.stdout
    has_target = "verify_infra_060.py" in proc.stdout
    ok = rc == 0 and has_one_file and has_target
    _emit(
        "V2_dump_strict_pattern_hits_one_file",
        ok,
        f"rc={rc} across_1_files={has_one_file} has_target={has_target}",
    )


def v3_dump_substring_pattern_hits_two_files() -> None:
    """反证: 旧 substring pattern 'verify_infra_060' 命中 across 2 files,
    这就是 baseline FAIL 的根因。本 check 锁住此现象, 若 dump 实现日后改变
    导致 substring pattern 也只命中 1 file, 我们能立刻知道。"""
    proc = subprocess.run(
        [sys.executable, str(DUMP_PY), "--filter", "verify_infra_060"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=30,
    )
    rc = proc.returncode
    # 注意: 这里期望 "across 2 files" (旧 pattern 双命中)。
    has_two_files = "across 2 files ===" in proc.stdout
    ok = rc == 0 and has_two_files
    _emit(
        "V3_dump_substring_pattern_hits_two_files",
        ok,
        f"rc={rc} across_2_files={has_two_files}",
    )


def v4_verify_infra_039_v7_filter_text_passes() -> None:
    """子进程跑 verify_infra_039.py, 整体 rc=0 且 V7_filter_text_mode PASS."""
    proc = subprocess.run(
        [sys.executable, str(VERIFY_INFRA_039)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=120,
    )
    rc = proc.returncode
    v7_pass = "[verify_infra_039][PASS] V7_filter_text_mode" in proc.stdout
    v7_fail = "[verify_infra_039][FAIL] V7_filter_text_mode" in proc.stdout
    ok = rc == 0 and v7_pass and not v7_fail
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    _emit(
        "V4_verify_infra_039_v7_filter_text_passes",
        ok,
        f"rc={rc} v7_pass={v7_pass} v7_fail={v7_fail} tail={tail!r}",
    )


def v5_self_main_func_sha() -> None:
    self_path = Path(__file__)
    got = func_sha_by_name(self_path, "main")
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V5_self_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
    else:
        _emit(
            "V5_self_main_func_sha",
            got == EXPECTED_SELF_MAIN_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
        )


def v6_verify_lib_untouched() -> None:
    got = _file_sha(VERIFY_LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V6_verify_lib_untouched",
            False,
            f"placeholder; bump EXPECTED_VERIFY_LIB_FILE_SHA={got}",
        )
    else:
        _emit(
            "V6_verify_lib_untouched",
            got == EXPECTED_VERIFY_LIB_FILE_SHA,
            f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
        )


def main() -> None:
    v0_scaffolding()
    v1_pattern_literal_is_strict_regex()
    v2_dump_strict_pattern_hits_one_file()
    v3_dump_substring_pattern_hits_two_files()
    v4_verify_infra_039_v7_filter_text_passes()
    v5_self_main_func_sha()
    v6_verify_lib_untouched()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(
            f"[verify_infra_039_backlog_v7_filter_substring_fix][SUMMARY] "
            f"FAIL {failed}/{total}: {names}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_039_backlog_v7_filter_substring_fix][SUMMARY] "
            f"ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
