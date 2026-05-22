#!/usr/bin/env python3
"""verify_infra_065 V0-V5: baseline FAIL claim cross-check helper lock.

infra-P294-R4-fail-baseline-cross-check (phase-38 #1.38):
P278 round-1 出现 Reviewer 误报 "verify_infra_061 FAIL on baseline" 而
Engineer 实跑 PASS — 当时只能靠第三方仲裁实跑解决。本 feature 把
"baseline FAIL claim cross-check" 固化进 verify 框架 (新增
``_verify_lib.verify_baseline_fail_claims``), 并由本 verify 脚本对该 helper
做 sha 锁 + 行为校验 (5 case)。

Default-OFF: 该 helper 不在任何已有 verify 流程自动触发, 仅由调用方
显式使用; 本 verify 脚本只校验 helper 本身可用 / 锁未漂移。

INFRA_065_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:verify_baseline_fail_claims`` func sha:
  EXPECTED_BASELINE_FAIL_CROSS_CHECK_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: 文件存在 / __main__ / def main / --json
- V1 docstring sentinel ``INFRA_065_SHA_LOCKS`` + v4_behavior 自锁
- V2 _verify_lib.py file sha
- V3 helper func sha (verify_baseline_fail_claims 自锁)
- V4 行为 (5 case, 用 tmp git repo 灌入 mock verify 脚本, 不污染主仓库):
  - V4.1 reviewer_text 含 "verify_infra_999 FAIL on baseline" 但 mock 脚本
    exit 0 → contradictions=1
  - V4.2 reviewer_text 含 "verify_infra_999 FAIL" 且 mock 脚本 exit 2 →
    contradictions=0, verified=1
  - V4.3 reviewer_text 无 FAIL claim → claims_total=0
  - V4.4 baseline_ref 不存在 → error 非空 + claims_total=0
  - V4.5 多 claim 混合 (1 真 FAIL + 1 假 FAIL) → contradictions=1, verified=1
- V5 Reviewer LGTM gate (print-only)

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import func_sha_by_name, verify_baseline_fail_claims  # noqa: E402

EXPECTED_VERIFY_LIB_FILE_SHA = "79bdbb3a750f98f4ba2994cf2cabe9206fe8e9af075cd35c738a210c846e97c7"
EXPECTED_BASELINE_FAIL_CROSS_CHECK_FUNC_SHA = "4aa6b31c7b5e79b1ea8f17f33c323e68fa0c20932c090848d6e322db0cd552a7"
EXPECTED_V4_CHECKER_FUNC_SHA = "51a9db2dc9d317c2e6de21fd2be1a351430cf696313be05ee3cdf00da072b289"

DOCSTRING_SENTINEL = "INFRA_065_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_065][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=False,
    )


def _make_mock_repo(tmp: Path, mock_scripts: dict[str, str]) -> str:
    """构造 tmp git repo, 在 scripts/ 目录写入 mock_scripts (rel_name → content),
    commit 后返回 HEAD sha."""
    (tmp / "scripts").mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", cwd=tmp)
    _git("config", "user.email", "t@t", cwd=tmp)
    _git("config", "user.name", "t", cwd=tmp)
    _git("config", "commit.gpgsign", "false", cwd=tmp)
    for name, body in mock_scripts.items():
        p = tmp / "scripts" / name
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    _git("add", "-A", cwd=tmp)
    _git("commit", "-q", "-m", "init", cwd=tmp)
    rp = _git("rev-parse", "HEAD", cwd=tmp)
    return rp.stdout.strip()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_verify_lib_exists", LIB.is_file(), f"path={LIB}")
    self_path = Path(__file__)
    src = self_path.read_text(encoding="utf-8")
    _emit(
        "V0_main_guard",
        'if __name__ == "__main__":' in src and "def main(" in src,
        "expect __main__ guard + def main()",
    )
    _emit(
        "V0_json_flag_declared",
        '"--json"' in src,
        "expect --json flag",
    )


# ---------------------------------------------------------------------------
# V1: docstring sentinel + v4_behavior 自锁
# ---------------------------------------------------------------------------
def v1_self_lock() -> None:
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8")))
    _emit(
        "V1_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )
    try:
        got = func_sha_by_name(self_path, "v4_behavior")
    except Exception as e:
        _emit("V1_self_checker_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_V4_CHECKER_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_self_checker_func_sha",
            False,
            f"placeholder; bump EXPECTED_V4_CHECKER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V1_self_checker_func_sha",
        got == EXPECTED_V4_CHECKER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_V4_CHECKER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V2: _verify_lib.py file sha
# ---------------------------------------------------------------------------
def v2_file_shas() -> None:
    got_lib = _file_sha(LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit("V2_lib_file_sha", False, f"placeholder; bump={got_lib}")
    else:
        _emit(
            "V2_lib_file_sha",
            got_lib == EXPECTED_VERIFY_LIB_FILE_SHA,
            f"got={got_lib[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V3: helper func sha (verify_baseline_fail_claims)
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "verify_baseline_fail_claims")
    except Exception as e:
        _emit("V3_baseline_fail_cross_check_func_sha", False,
              f"compute err: {e!r}")
        return
    if EXPECTED_BASELINE_FAIL_CROSS_CHECK_FUNC_SHA == "__BUMP_ME__":
        _emit("V3_baseline_fail_cross_check_func_sha", False,
              f"placeholder; bump EXPECTED_BASELINE_FAIL_CROSS_CHECK_FUNC_SHA={got}")
        return
    _emit(
        "V3_baseline_fail_cross_check_func_sha",
        got == EXPECTED_BASELINE_FAIL_CROSS_CHECK_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_BASELINE_FAIL_CROSS_CHECK_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为 — 用 tmp git repo 隔离, 5 case
# ---------------------------------------------------------------------------
_MOCK_PASS = "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"
_MOCK_FAIL = "#!/usr/bin/env python3\nimport sys\nsys.exit(2)\n"


def v4_behavior() -> None:
    # V4.1: claim FAIL on baseline 但 mock 脚本 exit 0 → contradictions=1
    with tempfile.TemporaryDirectory(prefix="vi065_c1_") as td:
        tmp = Path(td)
        sha = _make_mock_repo(tmp, {"verify_infra_999.py": _MOCK_PASS})
        r = verify_baseline_fail_claims(
            "Reviewer says verify_infra_999 FAIL on baseline",
            sha, tmp, timeout_per_script=10,
        )
        _emit(
            "V4.1_false_fail_claim_detected",
            r["claims_total"] == 1 and r["claims_contradicted"] == 1
            and r["claims_verified"] == 0 and r["error"] is None,
            f"got total={r['claims_total']} verified={r['claims_verified']} "
            f"contradicted={r['claims_contradicted']} err={r['error']!r}",
        )

    # V4.2: claim FAIL + mock 脚本 exit 2 → verified=1, contradictions=0
    with tempfile.TemporaryDirectory(prefix="vi065_c2_") as td:
        tmp = Path(td)
        sha = _make_mock_repo(tmp, {"verify_infra_999.py": _MOCK_FAIL})
        r = verify_baseline_fail_claims(
            "verify_infra_999 FAIL on baseline ref",
            sha, tmp, timeout_per_script=10,
        )
        _emit(
            "V4.2_true_fail_claim_verified",
            r["claims_total"] == 1 and r["claims_verified"] == 1
            and r["claims_contradicted"] == 0 and r["error"] is None,
            f"got total={r['claims_total']} verified={r['claims_verified']} "
            f"contradicted={r['claims_contradicted']} err={r['error']!r}",
        )

    # V4.3: reviewer_text 无 FAIL claim → claims_total=0
    with tempfile.TemporaryDirectory(prefix="vi065_c3_") as td:
        tmp = Path(td)
        sha = _make_mock_repo(tmp, {"verify_infra_999.py": _MOCK_PASS})
        r = verify_baseline_fail_claims(
            "All checks PASS, no failures observed",
            sha, tmp, timeout_per_script=10,
        )
        _emit(
            "V4.3_no_claim_short_circuit",
            r["claims_total"] == 0 and r["claims_contradicted"] == 0
            and r["error"] is None,
            f"got total={r['claims_total']} err={r['error']!r}",
        )

    # V4.4: baseline_ref 不存在 → error 非空
    with tempfile.TemporaryDirectory(prefix="vi065_c4_") as td:
        tmp = Path(td)
        _make_mock_repo(tmp, {"verify_infra_999.py": _MOCK_PASS})
        r = verify_baseline_fail_claims(
            "verify_infra_999 FAIL on baseline",
            "deadbeef1234567890abcdef1234567890abcdef", tmp,
            timeout_per_script=10,
        )
        _emit(
            "V4.4_invalid_baseline_ref_errors",
            r["error"] is not None and "baseline_ref invalid" in (r["error"] or "")
            and r["claims_total"] == 0,
            f"got err={r['error']!r} total={r['claims_total']}",
        )

    # V4.5: 多 claim 混合 — 998 真 FAIL + 999 假 FAIL
    with tempfile.TemporaryDirectory(prefix="vi065_c5_") as td:
        tmp = Path(td)
        sha = _make_mock_repo(tmp, {
            "verify_infra_998.py": _MOCK_FAIL,
            "verify_infra_999.py": _MOCK_PASS,
        })
        r = verify_baseline_fail_claims(
            "Reviewer: verify_infra_998 FAIL on baseline; "
            "also verify_infra_999 FAIL on baseline",
            sha, tmp, timeout_per_script=10,
        )
        _emit(
            "V4.5_mixed_claims_partial_contradiction",
            r["claims_total"] == 2 and r["claims_verified"] == 1
            and r["claims_contradicted"] == 1 and r["error"] is None,
            f"got total={r['claims_total']} verified={r['claims_verified']} "
            f"contradicted={r['claims_contradicted']} err={r['error']!r}",
        )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    _emit(
        "V5_reviewer_lgtm_gate",
        True,
        "closeout 阶段必须有 sub-agent fresh-context Reviewer LGTM (evidence 记录)",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_lock()
    v2_file_shas()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if "--json" in sys.argv:
        print(json.dumps({"results": [(t, ok, d) for t, ok, d in _results]}, indent=2))
    if failed:
        print(f"[verify_infra_065][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_065][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
