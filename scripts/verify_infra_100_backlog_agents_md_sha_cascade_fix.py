#!/usr/bin/env python3
"""verify_infra-100-backlog-agents-md-sha-cascade-fix.

phase-67 backlog 入账 feature.

verify_infra_100.py 的 ``EXPECTED_AGENTS_MD_FILE_SHA`` 常量自 AGENTS.md 最近一次
修改后未同步, 导致 ``V2_agents_md_file_sha`` 长期 baseline FAIL
(got=4334067ba33761e1 expect=68cb7a3282f8172c, 与 phase-67 #11 任务说明一致).
本 verifier 锁定以下事实, 确保 cascade 修复后不再回退:

- V0: AGENTS.md 与 scripts/verify_infra_100.py 存在.
- V1: AGENTS.md 当前 file sha256 == verify_infra_100.py 中
  EXPECTED_AGENTS_MD_FILE_SHA 常量 (cascade 同步成功).
- V2: verify_infra_100.py 子进程跑全脚本 rc=0 (全 PASS), 含 V2_agents_md_file_sha
  恢复 PASS (端到端 cascade 闭环).
- V3: 自身 ``main`` func sha 自锁 (首跑 __BUMP_ME__ 占位, 再回填).
- V4: 不触碰 ``scripts/_verify_lib.py`` (file sha 锁, cascade hygiene).

INFRA_100_BACKLOG_AGENTS_MD_SHA_LOCKS
-------------------------------------
- 期望 AGENTS.md file sha: EXPECTED_AGENTS_MD_FILE_SHA
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
AGENTS_MD = REPO / "AGENTS.md"
VERIFY_INFRA_100 = SCRIPTS / "verify_infra_100.py"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    func_sha_by_name,
    verify_summary_exit,
)

# 期望值: AGENTS.md 当前实际 sha (与 verify_infra_100.py 应一致)
EXPECTED_AGENTS_MD_FILE_SHA = (
    "e8251b9a98f9fb7849e232b7cec632a9e1689bf53bf7f2543787d7eacc45d36f"
)
# _verify_lib.py 不许改 (硬规则)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "1990a9b61d3b14c9b827a23188a46001be2a208234352e1bc7af955285578f50"
)
# self main func sha 自锁
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "7486bf2a9f885c2923e6bc5b0ad3d42abad4149cb7e9ab1b2fde2208e1ec5058"
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_100_backlog_cascade][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def v0_scaffolding() -> None:
    _emit("V0_agents_md_exists", AGENTS_MD.is_file(), f"path={AGENTS_MD.name}")
    _emit(
        "V0_verify_infra_100_exists",
        VERIFY_INFRA_100.is_file(),
        f"path={VERIFY_INFRA_100.name}",
    )
    _emit("V0_verify_lib_exists", VERIFY_LIB.is_file(), f"path={VERIFY_LIB.name}")


def v1_cascade_sha_synced() -> None:
    """AGENTS.md actual sha == EXPECTED_AGENTS_MD_FILE_SHA in verify_infra_100.py."""
    if not AGENTS_MD.is_file() or not VERIFY_INFRA_100.is_file():
        _emit("V1_cascade_sha_synced", False, "scaffolding missing")
        return
    got = _file_sha(AGENTS_MD)
    src = VERIFY_INFRA_100.read_text(encoding="utf-8")
    m = re.search(
        r'EXPECTED_AGENTS_MD_FILE_SHA\s*=\s*\(\s*"([0-9a-f]{64})"\s*\)',
        src,
    )
    if not m:
        _emit(
            "V1_cascade_sha_synced",
            False,
            "EXPECTED_AGENTS_MD_FILE_SHA literal not found in verify_infra_100.py",
        )
        return
    expected_in_file = m.group(1)
    ok = got == expected_in_file == EXPECTED_AGENTS_MD_FILE_SHA
    _emit(
        "V1_cascade_sha_synced",
        ok,
        f"AGENTS.md_actual={got[:16]} verify_infra_100_const={expected_in_file[:16]} "
        f"self_const={EXPECTED_AGENTS_MD_FILE_SHA[:16]}",
    )


def v2_verify_infra_100_passes() -> None:
    """子进程跑 verify_infra_100.py, rc 必须 0."""
    proc = subprocess.run(
        [sys.executable, str(VERIFY_INFRA_100)],
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
    _emit("V2_verify_infra_100_passes", ok, detail)


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
    """_verify_lib.py file sha 锁; __BUMP_ME__ 首跑允许 FAIL + bump 提示."""
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
    v1_cascade_sha_synced()
    v2_verify_infra_100_passes()
    v3_self_main_func_sha()
    v4_verify_lib_untouched()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(
            f"[verify_infra_100_backlog_cascade][SUMMARY] FAIL {failed}/{total}: {names}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_100_backlog_cascade][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
