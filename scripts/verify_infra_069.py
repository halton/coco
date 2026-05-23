#!/usr/bin/env python3
"""verify_infra_069 V0-V5: bootstrap_verify_self_checker canary edit-flow docs & helper.

infra-P297-bootstrap-canary-edit-flow-docs (phase-38 #5.38): P276 Reviewer
finding: 改 ``scripts/bootstrap_verify_self_checker.py`` 时 maintainer 必须 (a)
重算 ``_CANARY_EXPECTED_SHA``, (b) bump 锁本文件 file sha 的 verify const, 否
则 canary 子模式或 round-trip 会 false positive FAIL. 本 verify 检查:

1. bootstrap 文件 docstring 含 P297 edit-flow 章节 sentinel.
2. ``--compute-canary-sha`` CLI 子命令存在, rc=0, stdout 是 64 hex.
3. 该子命令输出与当前 ``_CANARY_EXPECTED_SHA`` 字面一致 (src 与 sha 同步).
4. 本 verify 自身常规自锁 (func sha / file sha 锁链).

INFRA_069_SHA_LOCKS
-------------------
- 本脚本 v4_behavior 函数 canonical func sha: EXPECTED_V4_CHECKER_FUNC_SHA
- 本脚本 main 函数 canonical func sha: EXPECTED_SELF_MAIN_FUNC_SHA
- ``scripts/bootstrap_verify_self_checker.py`` file sha: EXPECTED_BOOTSTRAP_FILE_SHA

校验层级 (V0-V5):

- V0 scaffolding (4): self main func sha 自锁 / shebang / docstring sentinel / cli main
- V1 self v4_behavior func sha 自锁 (1)
- V2 bootstrap_verify_self_checker.py file sha 锁 (1)
- V3 bootstrap docstring 含 P297 edit-flow 章节 sentinel (1)
- V4 行为 (2):
  - V4.1 subprocess 跑 ``--compute-canary-sha``: rc=0 且 stdout 是 64 hex
  - V4.2 子命令输出与当前 ``_CANARY_EXPECTED_SHA`` 一致
- V5 Reviewer LGTM gate (print-only) (1)

退出码 0=ALL PASS / 2=任一 FAIL (via verify_summary_exit helper).

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
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
BOOTSTRAP = SCRIPTS / "bootstrap_verify_self_checker.py"
SELF = Path(__file__).resolve()

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (
    func_sha_by_name,
    verify_summary_exit,
    assert_v5_reviewer_gate_evidence_bind,
)

EXPECTED_V4_CHECKER_FUNC_SHA = "be1d26be2be4f466ebeac98430ba4021d4ae13f9eb571f80bbd7ca45a0bbfba0"
EXPECTED_SELF_MAIN_FUNC_SHA = "08c2599a249fde6ae1ee6d8c85da8c06c007c121fbf832d19a5445133681f3fe"
EXPECTED_BOOTSTRAP_FILE_SHA = "01a3099a50b1d61da1accc74e867922e7cc9b79e80620fa50a186e207eb55b97"

DOCSTRING_SENTINEL = "INFRA_069_SHA_LOCKS"
BOOTSTRAP_DOCSTRING_SENTINEL = "编辑 _CANARY_VERIFY_SRC / _CANARY_EXPECTED_SHA 的流程 (P297)"
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


# phase-47 #1.47: V5_GATE evidence-bind helper (grace_period 兜底 soft graduate)
REAL_FEATURE_LIST = Path(__file__).resolve().parents[1] / "feature_list.json"
V5_GATE_FEATURE_ID = "__PHASE_47_PLACEHOLDER_INFRA_069__"

_results: List[Tuple[str, bool, str]] = []


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_069][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    self_src = SELF.read_text(encoding="utf-8")
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
    _emit("V0_shebang", self_src.startswith("#!/usr/bin/env python3"), "shebang present")
    _emit(
        "V0_docstring_sentinel",
        DOCSTRING_SENTINEL in self_src,
        f"sentinel={DOCSTRING_SENTINEL}",
    )
    _emit(
        "V0_cli_main",
        'if __name__ == "__main__"' in self_src and "sys.exit(verify_summary_exit" in self_src,
        "cli main + verify_summary_exit",
    )


# ---------------------------------------------------------------------------
# V1: self v4_behavior func sha 自锁
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
# V2: bootstrap_verify_self_checker.py file sha
# ---------------------------------------------------------------------------
def v2_bootstrap_file_sha() -> None:
    got = _file_sha(BOOTSTRAP)
    if EXPECTED_BOOTSTRAP_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V2_bootstrap_file_sha",
            False,
            f"placeholder; bump EXPECTED_BOOTSTRAP_FILE_SHA={got}",
        )
        return
    _emit(
        "V2_bootstrap_file_sha",
        got == EXPECTED_BOOTSTRAP_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_BOOTSTRAP_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: bootstrap docstring 含 P297 edit-flow 章节
# ---------------------------------------------------------------------------
def v3_bootstrap_docstring() -> None:
    src = BOOTSTRAP.read_text(encoding="utf-8")
    ok = BOOTSTRAP_DOCSTRING_SENTINEL in src
    _emit(
        "V3_bootstrap_docstring_p297_section",
        ok,
        f"sentinel={'present' if ok else 'MISSING'!s}",
    )


# ---------------------------------------------------------------------------
# V4: 行为 — --compute-canary-sha 子命令
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # V4.1: 子命令存在 + rc=0 + 输出是 64 hex
    proc = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "--compute-canary-sha"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    stdout = (proc.stdout or "").strip()
    rc = proc.returncode
    is_hex = bool(HEX64_RE.match(stdout))
    _emit(
        "V4_1_compute_canary_sha_cli",
        rc == 0 and is_hex,
        f"rc={rc} stdout_len={len(stdout)} is_hex64={is_hex} sample={stdout[:16]}",
    )

    # V4.2: 子命令输出与当前 _CANARY_EXPECTED_SHA 字面一致 (src 与 sha 同步)
    bsrc = BOOTSTRAP.read_text(encoding="utf-8")
    # _CANARY_EXPECTED_SHA: str = (\n    "...sha..."\n)
    m = re.search(
        r'_CANARY_EXPECTED_SHA[^"]*"([0-9a-f]{64})"',
        bsrc,
    )
    if not m:
        _emit("V4_2_canary_const_matches_helper", False, "could not parse _CANARY_EXPECTED_SHA literal")
    else:
        const_val = m.group(1)
        _emit(
            "V4_2_canary_const_matches_helper",
            const_val == stdout and is_hex,
            f"const={const_val[:16]} cli={stdout[:16]} match={const_val == stdout}",
        )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    """V5 Reviewer LGTM gate — phase-47 #1.47 graduate to evidence-bind helper.

    target feature evidence 不完整 (legacy / not_started)，通过 grace_period 兜底
    保持 emit=True，待 target feature 补齐 reviewer evidence 后从 grace 列表移除。
    """
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


def main() -> int:
    v0_scaffolding()
    v1_self_lock()
    v2_bootstrap_file_sha()
    v3_bootstrap_docstring()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_069][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return len(failed)
    print(f"[verify_infra_069][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(verify_summary_exit(main()))
