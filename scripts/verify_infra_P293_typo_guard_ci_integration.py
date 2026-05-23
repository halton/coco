#!/usr/bin/env python3
"""verify_infra_P293_typo_guard_ci_integration: 锁 P281 typo-guard helper 已挂到 ./init.sh smoke.

infra-P293-typo-guard-ci-integration (phase-55 #1.55):
源自 infra-P281 Reviewer Round-4 finding B-P281-R4 — ``scan_expected_prefix_typos`` /
``verify_expected_prefix_typo_guard`` 当前只被 ``verify_infra_061`` 主动调用,
开发者只跑 ``./init.sh`` smoke 时不会触发 typo 扫描, 护栏价值依赖开发者记得手跑
verify_infra_061。本 feature 在 ``scripts/smoke.py`` 增加 ``smoke_typo_guard`` 子检查,
init.sh smoke (秒级) 每次都触发 EXPECTED_* 前缀 typo 扫描, 提升触发频率即提升护栏价值。

INFRA_P293_CI_SHA_LOCKS
-----------------------
- ``scripts/smoke.py`` file sha: EXPECTED_SMOKE_PY_FILE_SHA
- 自身 ``main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: scripts/smoke.py 存在 + ``def smoke_typo_guard(`` 存在
- V1 smoke 循环列表必须含 ``("typo_guard", smoke_typo_guard)`` 元组
  (静态扫描源码, 防止注册被移除)
- V2 smoke.py 必须 import ``verify_expected_prefix_typo_guard``
  (helper 链接锚点, 防止函数体被改成空 stub)
- V3 scripts/smoke.py file sha 锁 (任何改动均触发 cascade bump)
- V4 runtime smoke: 真跑 scripts/smoke.py, stdout 必须出现
  ``==> Smoke: typo-guard (EXPECTED_* 前缀拼写扫描)`` 行 + ``typo_count=0`` 行
- V4b 自身 main func sha
- V5 Reviewer LGTM gate (grace_period 兜底)

退出码 0=ALL PASS / 2=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
SMOKE_PY = SCRIPTS / "smoke.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

# infra-P293 sha lock 常量 (V3)
EXPECTED_SMOKE_PY_FILE_SHA = (
    "50f166a3287e5648ac59a0dd3ddb415cdb87b4a5c53e5aaa337d8ba792d2e944"
)
# 自身 main func sha (首跑用 __BUMP_ME__ 占位, 再回填)
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "2ac1a8498b102f8551a38006e271f00202d8fe7a333852b7f29297e4ed8ac99e"
)

DOCSTRING_SENTINEL = "INFRA_P293_CI_SHA_LOCKS"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P293-typo-guard-ci-integration"

# V1 smoke 循环列表中必须出现的元组字面量 (静态扫描)
V1_LOOP_ENTRY_TOKEN = '("typo_guard", smoke_typo_guard)'

# V2 helper import 必须出现在 smoke.py
V2_IMPORT_TOKEN = "verify_expected_prefix_typo_guard"

# V4 runtime stdout 关键 token (smoke_typo_guard 子检查 print 出来的标识)
V4_STDOUT_TOKENS = (
    "==> Smoke: typo-guard",
    "typo_count=0",
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P293_typo_guard_ci_integration][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0 scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_smoke_py_exists", SMOKE_PY.is_file(), f"path={SMOKE_PY.relative_to(REPO)}")
    if not SMOKE_PY.is_file():
        return
    src = SMOKE_PY.read_text(encoding="utf-8")
    has_fn = "def smoke_typo_guard(" in src
    _emit("V0_smoke_typo_guard_present", has_fn, "expect 'def smoke_typo_guard(' in smoke.py")


# ---------------------------------------------------------------------------
# V1 smoke loop list 注册
# ---------------------------------------------------------------------------
def v1_smoke_loop_registered() -> None:
    src = SMOKE_PY.read_text(encoding="utf-8")
    _emit(
        "V1_loop_entry_present",
        V1_LOOP_ENTRY_TOKEN in src,
        f"expect literal {V1_LOOP_ENTRY_TOKEN!r} in smoke.py",
    )


# ---------------------------------------------------------------------------
# V2 helper import 锚点
# ---------------------------------------------------------------------------
def v2_helper_import() -> None:
    src = SMOKE_PY.read_text(encoding="utf-8")
    _emit(
        "V2_helper_import_present",
        V2_IMPORT_TOKEN in src,
        f"expect token {V2_IMPORT_TOKEN!r} in smoke.py",
    )


# ---------------------------------------------------------------------------
# V3 smoke.py file sha
# ---------------------------------------------------------------------------
def v3_file_sha() -> None:
    got = _file_sha(SMOKE_PY)
    if EXPECTED_SMOKE_PY_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V3_smoke_py_file_sha",
            False,
            f"placeholder; bump EXPECTED_SMOKE_PY_FILE_SHA={got}",
        )
    else:
        _emit(
            "V3_smoke_py_file_sha",
            got == EXPECTED_SMOKE_PY_FILE_SHA,
            f"got={got[:16]} expect={EXPECTED_SMOKE_PY_FILE_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V4 runtime smoke: 真跑 scripts/smoke.py
# ---------------------------------------------------------------------------
def v4_runtime_smoke() -> None:
    venv_py = REPO / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.is_file() else sys.executable
    try:
        r = subprocess.run(
            [py, str(SMOKE_PY)],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except Exception as e:
        _emit("V4_runtime_smoke_rc", False, f"subprocess err: {e!r}")
        return
    _emit(
        "V4_runtime_smoke_rc",
        r.returncode == 0,
        f"rc={r.returncode}",
    )
    for tok in V4_STDOUT_TOKENS:
        tag_suffix = re.sub(r"\W+", "_", tok)[:48]
        _emit(
            f"V4_stdout_token_{tag_suffix}",
            tok in r.stdout,
            f"expect token in stdout: {tok!r}",
        )


# ---------------------------------------------------------------------------
# V4b self main func sha
# ---------------------------------------------------------------------------
def v4b_self_main_func_sha() -> None:
    self_path = Path(__file__)
    got = func_sha_by_name(self_path, "main")
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V4b_self_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
    else:
        _emit(
            "V4b_self_main_func_sha",
            got == EXPECTED_SELF_MAIN_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V5 Reviewer LGTM gate (grace_period 兜底)
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
        grace_period_feature_ids=(V5_GATE_FEATURE_ID,),
    )
    _emit(
        "V5_reviewer_lgtm_gate",
        bool(result["ok"]),
        f"target={V5_GATE_FEATURE_ID} helper_ok={result['ok']} "
        f"grace_skipped={result['grace_skipped']} "
        f"verdict={result['verdict']!r} kind={result['reviewer_kind']!r} "
        f"summary_len={result['summary_len']} reason={result['reason']!r}",
    )


def main() -> None:
    v0_scaffolding()
    v1_smoke_loop_registered()
    v2_helper_import()
    v3_file_sha()
    v4_runtime_smoke()
    v4b_self_main_func_sha()
    v5_reviewer_gate()
    total = len(_results)
    unique_tags = len({t for t, _, _ in _results})
    failed = sum(1 for _, ok, _ in _results if not ok)
    tag = "verify_infra_P293_typo_guard_ci_integration"
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(
            f"[{tag}][SUMMARY] FAIL {failed}/{total} emit-paths: {names}",
            flush=True,
        )
    else:
        print(
            f"[{tag}][SUMMARY] ALL PASS ({total} emit-paths / {unique_tags} unique check tags)",
            flush=True,
        )
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
