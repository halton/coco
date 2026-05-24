#!/usr/bin/env python3
"""verify_infra_067 V0-V5: verify_*.py SUMMARY-FAIL exit-code propagation.

infra-P294-Rx-verify-summary-exit-propagation (phase-38 #3.38): 源自 P294-R5
round-1 Engineer 误信 shell 复合命令的 ``$?`` 判断 verify_*.py 是否 PASS
事件。机制化校验所有 ``scripts/verify_infra_*.py`` 在 SUMMARY/summary FAIL
时退出码 != 0 (典型 rc=1 或 rc=2), 防止未来"看似全 PASS 但实际 FAIL 被吞掉"
的回归; 同时新增 ``verify_summary_exit(failed_count)`` helper 给新脚本一个
统一退出码约定 (failed>0 → exit 2; 否则 exit 0), 老脚本不强制迁移
(``sys.exit(main())`` + ``return 1`` 一样正确传播)。

INFRA_067_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``verify_summary_exit`` func sha: EXPECTED_VERIFY_SUMMARY_EXIT_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: _verify_lib.py 存在 + helper 公开 (def 形 + ``__all__`` 含名)
- V1 docstring sentinel ``INFRA_067_SHA_LOCKS`` + 本脚本 v4_behavior func sha 自锁
- V2 _verify_lib.py file sha
- V3 verify_summary_exit canonical func sha (ast.unparse)
- V4 行为:
  - V4.1 helper 行为: verify_summary_exit(0) → SystemExit code=0;
         verify_summary_exit(1) → code=2; verify_summary_exit(5) → code=2;
         verify_summary_exit(-1) → ValueError; verify_summary_exit("x") → TypeError
  - V4.2 ast 扫所有 ``scripts/verify_infra_*.py``: 统计 SUMMARY/summary 类脚本
         (含 ``SUMMARY`` 字符串字面或 ``=== summary:`` 字面) 占总数比例,
         报告 split (新 / 旧 / 已迁移到 helper)
  - V4.3 行为: 真跑一个 mutant 脚本 (mutant 复制 verify_infra_062 + 破坏
         EXPECTED_VERIFY_LIB_FILE_SHA) 用 subprocess.run 验 rc != 0
  - V4.4 行为: 真跑 verify_infra_062 (未破坏) 验 rc == 0
  - V4.5 比例: 列出所有 verify_infra_*.py 中"看起来无 sys.exit 在 main 末尾"
         的脚本 (应当为 0), 否则报告它们以便后续修复
- V5 Reviewer LGTM gate (print-only)

退出码 0=ALL PASS / 1=任一 FAIL (本脚本沿用 sys.exit(main()) 旧模式,
helper 自己的 V4.1 子句即在覆盖 dogfood)。

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
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P294-Rx-verify-summary-exit-propagation"
LIB = SCRIPTS / "_verify_lib.py"
SELF = Path(__file__).resolve()
PYTHON = REPO / ".venv" / "bin" / "python"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
    assert_v5_reviewer_gate_evidence_bind,
)

EXPECTED_VERIFY_LIB_FILE_SHA = "1990a9b61d3b14c9b827a23188a46001be2a208234352e1bc7af955285578f50"
EXPECTED_VERIFY_SUMMARY_EXIT_FUNC_SHA = "09c968f6001b2f2c5249ef770fce89eb0cb4005095b3c9b32ac5f7485d106eba"
EXPECTED_V4_CHECKER_FUNC_SHA = "90115bdface39da8771ee9a48b576f657ce47e001c165be71387a78919cc90cb"

DOCSTRING_SENTINEL = "INFRA_067_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_067][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


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
        "def verify_summary_exit(" in src,
        "def verify_summary_exit(...) 在 _verify_lib.py",
    )
    _emit(
        "V0_helper_in_all",
        '"verify_summary_exit"' in src,
        "verify_summary_exit 在 __all__",
    )
    _emit(
        "V0_self_has_main",
        SELF.is_file() and "if __name__" in SELF.read_text(encoding="utf-8"),
        f"self path={SELF.name}",
    )


# ---------------------------------------------------------------------------
# V1: docstring sentinel + v4 checker func sha 自锁
# ---------------------------------------------------------------------------
def v1_self_lock() -> None:
    self_src = SELF.read_text(encoding="utf-8")
    _emit(
        "V1_docstring_sentinel",
        DOCSTRING_SENTINEL in self_src,
        f"sentinel={DOCSTRING_SENTINEL}",
    )
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
# V3: verify_summary_exit canonical func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "verify_summary_exit")
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_VERIFY_SUMMARY_EXIT_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V3_helper_func_sha",
            False,
            f"placeholder; bump EXPECTED_VERIFY_SUMMARY_EXIT_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_VERIFY_SUMMARY_EXIT_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_SUMMARY_EXIT_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为
# ---------------------------------------------------------------------------
def _helper_exits_with(failed_count) -> tuple[bool, object]:
    """子进程跑 helper, 返回 (raised_systemexit, code_or_exception)."""
    try:
        verify_summary_exit(failed_count)
        return (False, None)
    except SystemExit as e:
        return (True, e.code)
    except Exception as e:  # noqa: BLE001
        return (False, e)


def v4_behavior() -> None:
    # V4.1.a: failed=0 → exit 0
    raised, code = _helper_exits_with(0)
    _emit(
        "V4_1a_helper_zero_exits_zero",
        raised and code == 0,
        f"raised={raised} code={code!r}",
    )

    # V4.1.b: failed=1 → exit 2
    raised, code = _helper_exits_with(1)
    _emit(
        "V4_1b_helper_one_exits_two",
        raised and code == 2,
        f"raised={raised} code={code!r}",
    )

    # V4.1.c: failed=5 → exit 2
    raised, code = _helper_exits_with(5)
    _emit(
        "V4_1c_helper_many_exits_two",
        raised and code == 2,
        f"raised={raised} code={code!r}",
    )

    # V4.1.d: failed=-1 → ValueError
    raised, code = _helper_exits_with(-1)
    _emit(
        "V4_1d_helper_negative_raises",
        (not raised) and isinstance(code, ValueError),
        f"raised={raised} code={code!r}",
    )

    # V4.1.e: failed="x" → TypeError
    raised, code = _helper_exits_with("x")  # type: ignore[arg-type]
    _emit(
        "V4_1e_helper_wrong_type_raises",
        (not raised) and isinstance(code, TypeError),
        f"raised={raised} code={code!r}",
    )

    # V4.2: ast 扫所有 verify_infra_*.py 分类
    scripts = sorted(SCRIPTS.glob("verify_infra_*.py"))
    has_summary_str = []
    has_old_summary_str = []
    uses_helper = []
    no_summary = []
    for p in scripts:
        if p.name == SELF.name:
            continue
        src = p.read_text(encoding="utf-8")
        if "verify_summary_exit(" in src:
            uses_helper.append(p.name)
        if "[SUMMARY]" in src:
            has_summary_str.append(p.name)
        elif "summary:" in src.lower() or "=== summary" in src:
            has_old_summary_str.append(p.name)
        else:
            no_summary.append(p.name)
    total_scripts = len(scripts) - 1  # 不算 self
    _emit(
        "V4_2_summary_string_inventory",
        len(has_summary_str) + len(has_old_summary_str) + len(no_summary) == total_scripts,
        f"total={total_scripts} new_SUMMARY={len(has_summary_str)} old_summary={len(has_old_summary_str)} no_summary={len(no_summary)} uses_helper={len(uses_helper)}",
    )

    # V4.3: mutant 062 → rc != 0
    mutant_path = SCRIPTS / "_mutant_067.py"
    try:
        src_062 = (SCRIPTS / "verify_infra_062.py").read_text(encoding="utf-8")
        # 找一个 64-hex 锁破坏
        m = re.search(r'"([0-9a-f]{64})"', src_062)
        if not m:
            _emit("V4_3_mutant_062_fails_rc_nonzero", False, "no 64-hex lock found in 062")
        else:
            bad = "0" * 64
            mutated = src_062.replace(m.group(1), bad, 1)
            mutant_path.write_text(mutated, encoding="utf-8")
            r = subprocess.run(
                [str(PYTHON), str(mutant_path)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            summary_fail = "[SUMMARY] FAIL" in r.stdout
            ok = summary_fail and r.returncode != 0
            _emit(
                "V4_3_mutant_062_fails_rc_nonzero",
                ok,
                f"rc={r.returncode} summary_fail={summary_fail} tail={r.stdout.splitlines()[-1] if r.stdout.strip() else '<empty>'!r}",
            )
    finally:
        if mutant_path.exists():
            mutant_path.unlink()

    # V4.4: 真跑 verify_infra_062 (未破坏) → rc == 0
    try:
        r = subprocess.run(
            [str(PYTHON), str(SCRIPTS / "verify_infra_062.py")],
            capture_output=True,
            text=True,
            timeout=120,
        )
        all_pass = "[SUMMARY] ALL PASS" in r.stdout
        ok = all_pass and r.returncode == 0
        _emit(
            "V4_4_pristine_062_rc_zero",
            ok,
            f"rc={r.returncode} all_pass={all_pass}",
        )
    except Exception as e:  # noqa: BLE001
        _emit("V4_4_pristine_062_rc_zero", False, f"err={e!r}")

    # V4.5: 列出"main 末尾无 sys.exit 调用"的脚本 (应为 0)
    # 排除非典型脚本: debt_sweep 是一次性盘点脚本, 不走 verify_infra_NNN 模板
    missing_exit = []
    for p in scripts:
        if p.name == SELF.name:
            continue
        if p.name == "verify_infra_debt_sweep.py":
            continue
        src = p.read_text(encoding="utf-8")
        # 简单规则: 含 if __name__ == "__main__" 且其后 200 字符内无 sys.exit
        # 或 raise SystemExit 或 verify_summary_exit
        idx = src.find('if __name__ == "__main__"')
        if idx == -1:
            missing_exit.append(p.name + " (no __main__)")
            continue
        tail = src[idx:]
        if (
            "sys.exit(" not in tail
            and "raise SystemExit" not in tail
            and "verify_summary_exit(" not in tail
        ):
            missing_exit.append(p.name)
    _emit(
        "V4_5_all_scripts_have_exit_path",
        len(missing_exit) == 0,
        f"missing_count={len(missing_exit)} list={missing_exit[:5]}",
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
        print(f"[verify_infra_067][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_067][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
