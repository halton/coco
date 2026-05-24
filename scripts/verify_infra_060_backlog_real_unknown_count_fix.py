#!/usr/bin/env python3
"""verify_infra-060-backlog-real-unknown-count-fix.

phase-67 #12 backlog 入账 feature.

verify_infra_060.py 的 ``V4_real_unknown_count_eq_one`` 长期 baseline FAIL
(unknown_count=7 expect=4), 因为 phase-67 累积新增 3 个 .md / template target
锁 (verify_infra_040 PR template / verify_infra_100 + cascade-fix +
verify_infra_P288 AGENTS.md / verify_infra_P301_followup_typed_enum P301 doc
/ verify_robot_034 docs/.md / verify_infra_100 CLAUDE.md), 全部为非 .py
target, dump_v4_sha_graph mermaid stem-resolve 失败留 unknown, 与原
EXPECTED_DOC_SHA 设计意图一致 (合理 unknown). 本 feature bump
EXPECTED_CURRENT_UNKNOWN_COUNT 4 → 7 并扩展模块注释列出 7 项, 让
verify_infra_060 重新 ALL PASS.

本 verifier 锁定 cascade 修复后下列事实:

- V0: scripts/verify_infra_060.py / scripts/dump_v4_sha_graph.py /
  scripts/_verify_lib.py 存在.
- V1: verify_infra_060.py 中 EXPECTED_CURRENT_UNKNOWN_COUNT 字面值 == 7
  (regex 抓常量, 防回退).
- V2: verify_infra_060.py 整脚本 rc=0 (含 V4_real_unknown_count_eq_one
  恢复 PASS, 端到端 cascade 闭环).
- V3: 自身 ``main`` func sha 自锁.
- V4: 不触碰 ``scripts/_verify_lib.py`` (file sha 锁, cascade hygiene).

INFRA_060_BACKLOG_REAL_UNKNOWN_COUNT_LOCKS
------------------------------------------
- 期望 EXPECTED_CURRENT_UNKNOWN_COUNT (verify_infra_060.py 字面值): 7
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
VERIFY_INFRA_060 = SCRIPTS / "verify_infra_060.py"
DUMP_V4 = SCRIPTS / "dump_v4_sha_graph.py"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    func_sha_by_name,
    verify_summary_exit,
)

# 期望 EXPECTED_CURRENT_UNKNOWN_COUNT 字面值
EXPECTED_UNKNOWN_COUNT_LITERAL = 7

# _verify_lib.py 不许改 (硬规则)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "1990a9b61d3b14c9b827a23188a46001be2a208234352e1bc7af955285578f50"
)
# self main func sha 自锁 (首跑 __BUMP_ME__, 再回填)
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "dfb097f02295a7f0b53b09dcd70efb67cff762bd2db2ea12f337a4ce0b42e454"
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_060_backlog_real_unknown][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def v0_scaffolding() -> None:
    _emit("V0_verify_infra_060_exists", VERIFY_INFRA_060.is_file(), f"path={VERIFY_INFRA_060.name}")
    _emit("V0_dump_v4_exists", DUMP_V4.is_file(), f"path={DUMP_V4.name}")
    _emit("V0_verify_lib_exists", VERIFY_LIB.is_file(), f"path={VERIFY_LIB.name}")


def v1_expected_count_literal() -> None:
    """verify_infra_060.py 中 EXPECTED_CURRENT_UNKNOWN_COUNT == 7 (regex 抓字面值)."""
    if not VERIFY_INFRA_060.is_file():
        _emit("V1_expected_count_literal", False, "verify_infra_060.py missing")
        return
    src = VERIFY_INFRA_060.read_text(encoding="utf-8")
    m = re.search(
        r"^EXPECTED_CURRENT_UNKNOWN_COUNT\s*=\s*(\d+)\s*$",
        src,
        re.MULTILINE,
    )
    if not m:
        _emit(
            "V1_expected_count_literal",
            False,
            "EXPECTED_CURRENT_UNKNOWN_COUNT module-level literal not found",
        )
        return
    got = int(m.group(1))
    _emit(
        "V1_expected_count_literal",
        got == EXPECTED_UNKNOWN_COUNT_LITERAL,
        f"got={got} expect={EXPECTED_UNKNOWN_COUNT_LITERAL}",
    )


def v2_verify_infra_060_passes() -> None:
    """子进程跑 verify_infra_060.py, rc 必须 0."""
    proc = subprocess.run(
        [sys.executable, str(VERIFY_INFRA_060)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    rc = proc.returncode
    ok = rc == 0
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    detail = f"rc={rc} stdout_tail={tail!r}"
    if not ok:
        detail += f" stderr_tail={proc.stderr[-200:]!r}"
    _emit("V2_verify_infra_060_passes", ok, detail)


def v3_self_main_func_sha() -> None:
    self_path = Path(__file__)
    got = func_sha_by_name(self_path, "main")
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V3_self_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
    else:
        _emit(
            "V3_self_main_func_sha",
            got == EXPECTED_SELF_MAIN_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
        )


def v4_verify_lib_untouched() -> None:
    got = _file_sha(VERIFY_LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V4_verify_lib_untouched",
            False,
            f"placeholder; bump EXPECTED_VERIFY_LIB_FILE_SHA={got}",
        )
    else:
        _emit(
            "V4_verify_lib_untouched",
            got == EXPECTED_VERIFY_LIB_FILE_SHA,
            f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
        )


def main() -> None:
    v0_scaffolding()
    v1_expected_count_literal()
    v2_verify_infra_060_passes()
    v3_self_main_func_sha()
    v4_verify_lib_untouched()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(
            f"[verify_infra_060_backlog_real_unknown][SUMMARY] FAIL {failed}/{total}: {names}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_060_backlog_real_unknown][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
