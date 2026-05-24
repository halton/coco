#!/usr/bin/env python3
"""verify_infra_105: ``func_sha_by_name`` doctest 锁 (verify-only).

infra-037-backlog-helper-doctest (phase-54 #1.54):
``scripts/_verify_lib.py`` 中的 ``func_sha_by_name`` helper 是 ~42 处
verifier 的核心 sha 计算入口 (cascade 面巨大)。本 feature 在该 helper
docstring 末尾追加 ``Examples:`` doctest 块, 锁定 (a) 返回 hex+长度 64 +
(b) 不存在函数名时抛 ValueError 的契约。doctest 本身不锁具体 sha 值
(否则 docstring 改动会无限自递归)。本 verifier 锁:

- doctest 文本存在 (V1)
- ``python -m doctest scripts/_verify_lib.py`` 干净通过 (V2)
- ``_verify_lib.py`` 整文件 file-sha (V3, cascade 入口)
- 本 verifier ``main`` func sha 自锁 (V4)
- Reviewer LGTM gate (V5)

INFRA_105_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- self ``main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

# infra-037-backlog sha lock 常量
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "1990a9b61d3b14c9b827a23188a46001be2a208234352e1bc7af955285578f50"
)
# 自身 main func sha (首跑用 __BUMP_ME__ 占位, 再回填)
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "b1782a7fa034c6ac4e3fe080a0ac5a4637325d11685ff2630eaed45636a00e10"
)

DOCSTRING_SENTINEL = "INFRA_105_SHA_LOCKS"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-037-backlog-helper-doctest"

# V1 doctest 关键词 (必须出现在 func_sha_by_name docstring 内)
V1_DOCTEST_KEYWORDS = (
    "Examples:",
    ">>> ",
    "func_sha_by_name",
    "ValueError",
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_105][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit(
        "V0_verify_lib_exists",
        VERIFY_LIB.is_file(),
        f"path={VERIFY_LIB.relative_to(REPO)}",
    )


# ---------------------------------------------------------------------------
# V1: doctest 关键词必须出现在 func_sha_by_name docstring
# ---------------------------------------------------------------------------
def v1_doctest_present() -> None:
    import ast
    src = VERIFY_LIB.read_text(encoding="utf-8")
    tree = ast.parse(src)
    docstring: str | None = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == "func_sha_by_name":
            docstring = ast.get_docstring(node)
            break
    _emit(
        "V1_docstring_present",
        docstring is not None,
        "func_sha_by_name docstring exists",
    )
    if docstring is None:
        for kw in V1_DOCTEST_KEYWORDS:
            _emit(
                f"V1_doctest_has_{kw.strip().replace(':', '').replace(' ', '_')[:16]}",
                False,
                "docstring missing",
            )
        return
    for kw in V1_DOCTEST_KEYWORDS:
        tag_suffix = kw.strip().replace(':', '').replace(' ', '_')[:16]
        _emit(
            f"V1_doctest_has_{tag_suffix}",
            kw in docstring,
            f"keyword={kw!r}",
        )


# ---------------------------------------------------------------------------
# V2: doctest 实跑必须干净通过
# ---------------------------------------------------------------------------
def v2_doctest_runs_clean() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "doctest", str(VERIFY_LIB)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    rc = proc.returncode
    ok = rc == 0
    detail = f"rc={rc} stdout_len={len(proc.stdout)} stderr_len={len(proc.stderr)}"
    if not ok:
        detail += f" stderr_tail={proc.stderr[-200:]!r}"
    _emit("V2_doctest_runs_clean", ok, detail)


# ---------------------------------------------------------------------------
# V3: _verify_lib.py 整文件 file-sha 锁
# ---------------------------------------------------------------------------
def v3_verify_lib_file_sha() -> None:
    got = _file_sha(VERIFY_LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V3_verify_lib_file_sha",
            False,
            f"placeholder; bump EXPECTED_VERIFY_LIB_FILE_SHA={got}",
        )
    else:
        _emit(
            "V3_verify_lib_file_sha",
            got == EXPECTED_VERIFY_LIB_FILE_SHA,
            f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V4: 自身 main func sha 自锁
# ---------------------------------------------------------------------------
def v4_self_main_func_sha() -> None:
    self_path = Path(__file__)
    got = func_sha_by_name(self_path, "main")
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V4_self_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
    else:
        _emit(
            "V4_self_main_func_sha",
            got == EXPECTED_SELF_MAIN_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
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
    v1_doctest_present()
    v2_doctest_runs_clean()
    v3_verify_lib_file_sha()
    v4_self_main_func_sha()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(f"[verify_infra_105][SUMMARY] FAIL {failed}/{total}: {names}", flush=True)
    else:
        print(f"[verify_infra_105][SUMMARY] ALL PASS ({total} checks)", flush=True)
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
