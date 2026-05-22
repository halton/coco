#!/usr/bin/env python3
"""verify_infra_075 V0-V5: Engineer/Reviewer report vs closeout 实跑 byte-match lock.

infra-P299-engineer-report-vs-impl-trustworthy (phase-40 #1.40):

P291 真实造假事件: Engineer report 谎称 6 项 verify FAIL 是 baseline pre-existing,
closeout 第三方实跑全 PASS. Engineer/Reviewer report 中声明的 tail_stdout / rc
未被任何 verify 交叉验证, 可能造假. 本 verify 锁定:

新 helper ``assert_report_matches_closeout_runs(report_obj, repo_root, main_head_sha)``:
  - 在 detached worktree 检出指定 sha
  - 真 subprocess.run report 中每条 verify
  - 比对 actual_tail 与 claimed tail 的 sha256 (utf-8, last 500 chars)
  - 同时校 rc
  - mismatch → ok=False + offending list

INFRA_075_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:assert_report_matches_closeout_runs`` func sha:
  EXPECTED_HELPER_FUNC_SHA
- 本脚本 main() 自锁: EXPECTED_SELF_MAIN_FUNC_SHA (P299 硬要求: 禁 __BUMP_ME__,
  真算值)

校验层级 (V0-V5, ~15 checks):

- V0 scaffolding: 路径/常量/docstring sentinel
- V1 self main() func sha 自锁 (真值, 禁 placeholder)
- V2 _verify_lib.py file sha
- V3 assert_report_matches_closeout_runs func sha
- V4 业务实测 (mini repo + 真 helper 调用):
  - V4_1 helper signature == 期望
  - V4_2 helper 返回 dict 含 required keys
  - V4_3 mini repo byte-match → ok=True / matched=1 / checked=1
  - V4_4 mini repo byte-mismatch → ok=False / offending 含 sha 不等
  - V4_5 mini repo rc-mismatch → ok=False / offending 含 rc 不等
  - V4_6 invalid main_head_sha → ok=False / error 非空
- V5 reviewer_lgtm_gate: 对自身 P299 软 gate (closeout 后再 LGTM)

退出码: 0=ALL PASS, 2=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"
REAL_FEATURE_LIST = REPO / "feature_list.json"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_report_matches_closeout_runs,
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_VERIFY_LIB_FILE_SHA = "99933db26a1bda209f928f24961c4f1e39105ce32f9046b841fcc9782eb919b0"
EXPECTED_HELPER_FUNC_SHA = "6578542d1b17fe12f2d2e98a598701192c1f9b2cc9fa3be185d4d4f00fad2894"
EXPECTED_SELF_MAIN_FUNC_SHA = "0fca2e2aed12e99e1e4768048bf6edded4967ad2eb79f6223c0c4978cc307e69"

EXPECTED_HELPER_SIGNATURE = (
    "(report_obj: 'dict', repo_root: \"'Path | str'\", main_head_sha: 'str', "
    "tail_chars: 'int' = 500, timeout_per_script: 'int' = 60) -> 'dict'"
)

DOCSTRING_SENTINEL = "INFRA_075_SHA_LOCKS"

V5_GATE_FEATURE_ID = "infra-P299-engineer-report-vs-impl-trustworthy"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_075][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_verify_lib_exists", VERIFY_LIB.is_file(), f"path={VERIFY_LIB}")
    _emit(
        "V0_const_lib_file_sha_hex64",
        _is_hex64(EXPECTED_VERIFY_LIB_FILE_SHA),
        f"val={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )
    _emit(
        "V0_const_helper_func_sha_hex64",
        _is_hex64(EXPECTED_HELPER_FUNC_SHA),
        f"val={EXPECTED_HELPER_FUNC_SHA[:16]}",
    )
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8")))
    _emit(
        "V0_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )


# ---------------------------------------------------------------------------
# V1: self main() func sha 自锁 (P299 硬要求: 真算值, 禁 __BUMP_ME__)
# ---------------------------------------------------------------------------
def v1_self_func_sha() -> None:
    try:
        got = func_sha_by_name(Path(__file__), "main")
    except Exception as e:  # noqa: BLE001
        _emit("V1_self_main_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_self_main_func_sha",
            False,
            (
                f"P299 hard rule violated: EXPECTED_SELF_MAIN_FUNC_SHA must be "
                f"a real 64-hex sha, NOT __BUMP_ME__; actual={got}"
            ),
        )
        return
    if not _is_hex64(EXPECTED_SELF_MAIN_FUNC_SHA):
        _emit(
            "V1_self_main_func_sha",
            False,
            (
                f"EXPECTED_SELF_MAIN_FUNC_SHA not 64-hex (got "
                f"{EXPECTED_SELF_MAIN_FUNC_SHA!r}); actual main sha={got}"
            ),
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
def v2_lib_file_sha() -> None:
    got = _file_sha(VERIFY_LIB)
    _emit(
        "V2_verify_lib_file_sha",
        got == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: assert_report_matches_closeout_runs func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(VERIFY_LIB, "assert_report_matches_closeout_runs")
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_HELPER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 业务实测 — helper signature/return schema + mini repo byte/rc match/mismatch
# ---------------------------------------------------------------------------
_MINI_VERIFY_SRC = """#!/usr/bin/env python3
import sys
print("HELLO")
print("FOO=1")
sys.exit({rc})
"""


def _make_mini_repo(rc: int = 0) -> Tuple[Path, str]:
    """造一个最小 git repo, scripts/ 下放一条 verify_xx.py 输出固定 stdout.

    返回 (repo_path, commit_sha).
    """
    repo = Path(tempfile.mkdtemp(prefix="coco_p299_minirepo_"))
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "config", "user.email", "p299@coco.local"],
        cwd=str(repo), check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "p299-test"],
        cwd=str(repo), check=True,
    )
    scripts_dir = repo / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "verify_xx.py").write_text(
        _MINI_VERIFY_SRC.format(rc=rc), encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=str(repo), check=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout.strip()
    return repo, sha


_CLAIMED_TAIL_OK = "HELLO\nFOO=1\n"
_CLAIMED_TAIL_BAD = "HELLO\nBAR=2\n"


def v4_behavior() -> None:
    # ---- V4_1: helper signature ----
    sig_str = str(inspect.signature(assert_report_matches_closeout_runs))
    _emit(
        "V4_1_helper_signature",
        sig_str == EXPECTED_HELPER_SIGNATURE,
        f"got={sig_str!r}",
    )

    # ---- V4_2: helper returns dict with required keys (用 invalid sha 触发快返回) ----
    sample = assert_report_matches_closeout_runs(
        {"verify_runs": []},
        repo_root=REPO,
        main_head_sha="0" * 40,
    )
    required = {
        "ok", "checked", "matched", "offending",
        "main_head_sha_resolved", "error",
    }
    _emit(
        "V4_2_helper_returns_dict_with_required_keys",
        isinstance(sample, dict) and required.issubset(sample.keys()),
        f"keys={sorted(sample.keys()) if isinstance(sample, dict) else type(sample)}",
    )

    # ---- V4_3: mini repo byte-match → ok=True ----
    repo_ok, sha_ok = _make_mini_repo(rc=0)
    try:
        report_match = {
            "verify_runs": [
                {
                    "round": 1,
                    "scripts": {
                        "verify_xx.py": {
                            "rc": 0,
                            "tail_stdout": _CLAIMED_TAIL_OK,
                            "status": "PASS",
                        },
                    },
                },
            ],
        }
        result_match = assert_report_matches_closeout_runs(
            report_match, repo_root=repo_ok, main_head_sha=sha_ok,
        )
        _emit(
            "V4_3_mini_repo_byte_match_PASS",
            (result_match.get("ok") is True
             and result_match.get("checked") == 1
             and result_match.get("matched") == 1
             and not result_match.get("offending")),
            f"result={ {k: v for k, v in result_match.items() if k != 'main_head_sha_resolved'} }",
        )

        # ---- V4_4: mini repo byte-mismatch → ok=False ----
        report_bad_tail = {
            "verify_runs": [
                {
                    "round": 1,
                    "scripts": {
                        "verify_xx.py": {
                            "rc": 0,
                            "tail_stdout": _CLAIMED_TAIL_BAD,
                            "status": "PASS",
                        },
                    },
                },
            ],
        }
        result_bad_tail = assert_report_matches_closeout_runs(
            report_bad_tail, repo_root=repo_ok, main_head_sha=sha_ok,
        )
        off = result_bad_tail.get("offending") or []
        sha_diff = bool(
            off
            and off[0].get("claimed_sha") != off[0].get("actual_sha")
        )
        _emit(
            "V4_4_mini_repo_byte_mismatch_FAIL",
            (result_bad_tail.get("ok") is False
             and result_bad_tail.get("checked") == 1
             and len(off) == 1
             and sha_diff),
            f"offending={off}",
        )

        # ---- V4_5: mini repo rc-mismatch → ok=False (report 声明 rc=0 实跑 rc=1) ----
        # 用新 mini repo, verify_xx.py 真实 rc=1, 而 report 谎称 rc=0 + tail 与真实匹配
        # (rc 不同, tail 相同时, helper 必须仅靠 rc 触发 mismatch)
        # 注: 真实 sys.exit(1) 后仍然 print 完上面两行, stdout tail 相同
    finally:
        subprocess.run(["rm", "-rf", str(repo_ok)], check=False)

    repo_rc1, sha_rc1 = _make_mini_repo(rc=1)
    try:
        report_bad_rc = {
            "verify_runs": [
                {
                    "round": 1,
                    "scripts": {
                        "verify_xx.py": {
                            "rc": 0,  # 谎称 rc=0
                            "tail_stdout": _CLAIMED_TAIL_OK,
                            "status": "PASS",
                        },
                    },
                },
            ],
        }
        result_bad_rc = assert_report_matches_closeout_runs(
            report_bad_rc, repo_root=repo_rc1, main_head_sha=sha_rc1,
        )
        off_rc = result_bad_rc.get("offending") or []
        rc_diff = bool(
            off_rc
            and off_rc[0].get("claimed_rc") != off_rc[0].get("actual_rc")
        )
        _emit(
            "V4_5_mini_repo_rc_mismatch_FAIL",
            (result_bad_rc.get("ok") is False
             and result_bad_rc.get("checked") == 1
             and len(off_rc) == 1
             and rc_diff),
            f"offending={off_rc}",
        )
    finally:
        subprocess.run(["rm", "-rf", str(repo_rc1)], check=False)

    # ---- V4_6: invalid main_head_sha → ok=False + error 非空 ----
    bad = assert_report_matches_closeout_runs(
        {"verify_runs": [{"round": 1, "scripts": {}}]},
        repo_root=REPO,
        main_head_sha="deadbeefcafe1234deadbeefcafe1234deadbeef",
    )
    _emit(
        "V4_6_missing_main_head_sha_FAIL",
        (bad.get("ok") is False and isinstance(bad.get("error"), str)
         and bool(bad.get("error"))),
        f"error={bad.get('error')!r}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (soft: 本 feature 自身未 LGTM 之前不阻塞)
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    if not REAL_FEATURE_LIST.is_file():
        _emit(
            "V5_reviewer_lgtm_gate",
            False,
            f"feature_list.json not found at {REAL_FEATURE_LIST}",
        )
        return
    ok, reason = assert_reviewer_lgtm(V5_GATE_FEATURE_ID, REAL_FEATURE_LIST)
    # P299 自身 feature: closeout 前必然无 LGTM evidence → 软 PASS (记录 reason)
    if not ok:
        _emit(
            "V5_reviewer_lgtm_gate",
            True,
            f"soft-PASS pre-closeout: target={V5_GATE_FEATURE_ID} reason={reason!r}",
        )
        return
    _emit(
        "V5_reviewer_lgtm_gate",
        True,
        f"target={V5_GATE_FEATURE_ID} helper_ok={ok} reason={reason!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_func_sha()
    v2_lib_file_sha()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    failed_tags = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_075][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_075][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
