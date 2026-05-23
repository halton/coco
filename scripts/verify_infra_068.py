#!/usr/bin/env python3
"""verify_infra_068 V0-V5: closeout Reviewer evidence text-signal scan.

infra-P294-Ry-closeout-reviewer-text-scan (phase-38 #4.38): 给 closeout
sub-agent 一个机制化工具去扫 Reviewer evidence 文本块, 检测 4 类硬证据信号
(verdict / command / sha / file:line); 阈值 >=3 → min_signals_met=True,
允许 1 项缺失 (e.g. 纯文档 review 可能无 file:line)。default-OFF: 不进入
任何自动 verify 流程, 仅由 closeout 或本脚本显式调用。

INFRA_068_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scan_reviewer_text`` func sha: EXPECTED_REVIEWER_TEXT_SCAN_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA
- 本脚本 file sha: EXPECTED_SELF_FILE_SHA

校验层级 (V0-V5):

- V0 scaffolding (4): file sha 自锁 / shebang / docstring sentinel / cli main
- V1 self func sha (1): 本脚本 v4_behavior canonical func sha 自锁
- V2 lib file sha (1): scripts/_verify_lib.py file sha
- V3 helper func sha (1): scan_reviewer_text canonical func sha
- V4 行为 (5):
  - V4.1 全 4 信号: verdict + command + sha + file:line → signal_count==4 min_signals_met=True
  - V4.2 空文本: → signal_count==0 min_signals_met=False flags 含 4 项
  - V4.3 仅 verdict: → signal_count==1 min_signals_met=False
  - V4.4 verdict + command: → signal_count==2 min_signals_met=False
  - V4.5 verdict + command + sha + file:line (合成): signal_count==4 min_signals_met=True
- V5 Reviewer LGTM gate (print-only)

退出码 0=ALL PASS / 2=任一 FAIL (via verify_summary_exit helper)。

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P294-Ry-closeout-reviewer-text-scan"
LIB = SCRIPTS / "_verify_lib.py"
SELF = Path(__file__).resolve()

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (
    assert_reviewer_lgtm,
    func_sha_by_name,
    scan_reviewer_text,
    verify_summary_exit,
    assert_v5_reviewer_gate_evidence_bind,
)

EXPECTED_VERIFY_LIB_FILE_SHA = "43d352534301242f13ef33909cdba2585f8f18734d9e052f2e0de0d075955d75"
EXPECTED_REVIEWER_TEXT_SCAN_FUNC_SHA = "87d2fb49229d5128d3463cc8b4bb47c7e662eed6da2c87309afbf9901f9a21f9"
EXPECTED_V4_CHECKER_FUNC_SHA = "ab6e753438c746c0addcd0b73acf7ba8c26542b400eaab5e1e60fa73f5152ef7"
EXPECTED_SELF_MAIN_FUNC_SHA = "b4a1e550327efd7e2840a6fee77ecb07de4921d634dc0f99aa3c37efb19587ed"

DOCSTRING_SENTINEL = "INFRA_068_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_068][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    self_src = SELF.read_text(encoding="utf-8")
    # V0.1: self main func sha 自锁 (file sha 自锁不动点问题, 改用 canonical func sha)
    try:
        got = func_sha_by_name(SELF, "main")
    except Exception as e:  # noqa: BLE001
        _emit("V0_self_main_func_sha", False, f"compute err: {e!r}")
    else:
        if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
            _emit(
                "V0_self_main_func_sha",
                False,
                f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
            )
        else:
            _emit(
                "V0_self_main_func_sha",
                got == EXPECTED_SELF_MAIN_FUNC_SHA,
                f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
            )
    # V0.2: shebang
    _emit(
        "V0_shebang",
        self_src.startswith("#!/usr/bin/env python3"),
        "shebang present",
    )
    # V0.3: docstring sentinel
    _emit(
        "V0_docstring_sentinel",
        DOCSTRING_SENTINEL in self_src,
        f"sentinel={DOCSTRING_SENTINEL}",
    )
    # V0.4: cli main
    _emit(
        "V0_cli_main",
        'if __name__ == "__main__"' in self_src and "sys.exit(verify_summary_exit" in self_src,
        "cli main + verify_summary_exit",
    )


# ---------------------------------------------------------------------------
# V1: self func sha 自锁
# ---------------------------------------------------------------------------
def v1_self_lock() -> None:
    try:
        got = func_sha_by_name(SELF, "v4_behavior")
    except Exception as e:  # noqa: BLE001
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
def v2_lib_file_sha() -> None:
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
# V3: scan_reviewer_text canonical func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "scan_reviewer_text")
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_REVIEWER_TEXT_SCAN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V3_helper_func_sha",
            False,
            f"placeholder; bump EXPECTED_REVIEWER_TEXT_SCAN_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_REVIEWER_TEXT_SCAN_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_REVIEWER_TEXT_SCAN_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为 (合成样本, 不引真实 evidence)
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # V4.1: 全 4 信号
    text_full = (
        "verdict: LGTM\n"
        ".venv/bin/python scripts/verify_infra_068.py\n"
        "sha=abc1234\n"
        "file_lib.py:1254"
    )
    r = scan_reviewer_text(text_full)
    _emit(
        "V4_1_all_signals_present",
        r["signal_count"] == 4 and r["min_signals_met"] is True and r["flags"] == [],
        f"signal_count={r['signal_count']} min_met={r['min_signals_met']} flags={r['flags']}",
    )

    # V4.2: 空文本
    r = scan_reviewer_text("")
    _emit(
        "V4_2_empty_text",
        r["signal_count"] == 0
        and r["min_signals_met"] is False
        and set(r["flags"]) == {"no_verdict", "no_command", "no_sha", "no_file_line"},
        f"signal_count={r['signal_count']} flags={r['flags']}",
    )

    # V4.3: 仅 verdict
    r = scan_reviewer_text("verdict: LGTM")
    _emit(
        "V4_3_only_verdict",
        r["signal_count"] == 1
        and r["min_signals_met"] is False
        and r["has_verdict_line"] is True,
        f"signal_count={r['signal_count']} has_verdict={r['has_verdict_line']}",
    )

    # V4.4: verdict + command
    r = scan_reviewer_text("verdict: LGTM\n.venv/bin/python scripts/x.py")
    _emit(
        "V4_4_verdict_plus_command",
        r["signal_count"] == 2
        and r["min_signals_met"] is False
        and r["has_verdict_line"] is True
        and r["has_command_evidence"] is True,
        f"signal_count={r['signal_count']} has_verdict={r['has_verdict_line']} has_cmd={r['has_command_evidence']}",
    )

    # V4.5: verdict + command + sha + file:line (另一组合成)
    text_compound = (
        "Reviewer verdict: LGTM with findings\n"
        "git -C /Users/halton/work/coco log --oneline -3\n"
        "main HEAD=1234567abcd\n"
        "scripts/_verify_lib.py:1280"
    )
    r = scan_reviewer_text(text_compound)
    _emit(
        "V4_5_compound_all_signals",
        r["signal_count"] == 4 and r["min_signals_met"] is True,
        f"signal_count={r['signal_count']} min_met={r['min_signals_met']} flags={r['flags']}",
    )


# ---------------------------------------------------------------------------
# V5: reviewer gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    """V5 Reviewer LGTM gate — phase-47 #1.47 graduate to evidence-bind helper."""
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
    v1_self_lock()
    v2_lib_file_sha()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_068][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return len(failed)
    print(f"[verify_infra_068][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(verify_summary_exit(main()))
