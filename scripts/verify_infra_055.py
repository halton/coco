#!/usr/bin/env python3
"""verify_infra_055: assert_verify_passed helper + Sub-agent Evidence Report Accuracy (P273 防御).

infra-P273-evidence-report-accuracy (phase-36 #1.36 P274): P273 暴露 sub-agent
上报 "verify PASS" 但实际未跑 / 未读 stdout / 编造结论的失真模式 (典型: 把
054 V1 FAIL 错误归因到 044/047 称 "pre-existing"). 本 feature 建立两层防御:

1. ``scripts/_verify_lib.assert_verify_passed(stdout, name)`` 机器辅助校验 stdout
   是否真的 ALL PASS (parse SUMMARY 尾行 + 扫描 FAIL emit 行 + 校验脚本名);
2. AGENTS.md 加 "Sub-agent Evidence Report Accuracy" 硬规则段, 要求 sub-agent
   上报 verify 结果时必须附实测 stdout 尾行, 不允许凭印象编造或随意归因
   "pre-existing".

本脚本验证:
- V0 scaffolding: _verify_lib.py 存在 + assert_verify_passed 在 __all__
- V1 docstring sentinel ``INFRA_055_SHA_LOCKS`` 自锁 + 本脚本 v4_behavior func sha
- V2 _verify_lib.py file sha + ``assert_verify_passed`` func sha
- V3 mutant 反证:
  - mutant-A: 把 helper 内 verdict 校验改成总返回 passed=True, FAIL stdout 喂入
    应仍报 passed=True (反向证明: helper 真的在校验 verdict)
  - mutant-B: 删除 helper 内 fail_emits 计入 passed 的判定, FAIL emit stdout
    仍判 passed=True (反向证明: helper 真的扫了 FAIL emit)
- V4 行为验证 (round-trip):
  - 正例: 构造 ALL PASS stdout → assert_verify_passed 返回 passed=True / checks 正确
  - 反例1: 构造 FAIL emit + FAIL SUMMARY → passed=False / fail_emits>0
  - 反例2: 构造缺 SUMMARY 行的 stdout → passed=False / reason 含 "missing SUMMARY"
  - 反例3: name mismatch → passed=False / reason 含 "name mismatch"
  - 反例4: 空字符串 → passed=False
  - AGENTS.md 关键字断言: 含 "Sub-agent Evidence Report Accuracy" + "stdout 尾行"
    + "assert_verify_passed" + "ALL PASS"
- V5 Reviewer LGTM gate (print-only)

INFRA_055_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_LIB_FILE_SHA
- ``assert_verify_passed`` func sha: EXPECTED_ASSERT_VERIFY_PASSED_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"
AGENTS_MD = REPO / "AGENTS.md"

# 让本脚本能 import 同目录的 _verify_lib
sys.path.insert(0, str(SCRIPTS))

# infra-055 sha lock 常量 (V2)
EXPECTED_LIB_FILE_SHA = "e583aed3fd27d6dcc1f55b7f326b2cd6296876d3bdb0c5b1a111994f3f4f99c1"
EXPECTED_ASSERT_VERIFY_PASSED_FUNC_SHA = "918a4e5ab40aad3b49e635081902ddc41e0ab47e394610c8f63a9df0d59febb5"

# 本脚本 v4_behavior 自锁 (V1) — 首跑用 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "83cee8d276bec9c1f596ea83bb96283627a4654fdf38627ebf61f1cf166f7414"

DOCSTRING_SENTINEL = "INFRA_055_SHA_LOCKS"


# phase-47 #1.47: V5_GATE evidence-bind helper (grace_period 兜底 soft graduate)
REAL_FEATURE_LIST = Path(__file__).resolve().parents[1] / "feature_list.json"
V5_GATE_FEATURE_ID = "__PHASE_47_PLACEHOLDER_INFRA_055__"

_results: List[Tuple[str, bool, str]] = []


import sys as _sys_v5
_sys_v5.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_055][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _func_sha(path: Path, func_name: str) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return hashlib.sha256(ast.unparse(node).encode("utf-8")).hexdigest()
    raise RuntimeError(f"func {func_name!r} not found in {path}")


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_verify_lib_exists", VERIFY_LIB.is_file(), f"path={VERIFY_LIB}")
    _emit("V0_agents_md_exists", AGENTS_MD.is_file(), f"path={AGENTS_MD}")
    src = VERIFY_LIB.read_text(encoding="utf-8")
    _emit(
        "V0_assert_verify_passed_in_all",
        '"assert_verify_passed"' in src,
        "expect 'assert_verify_passed' in __all__",
    )
    _emit(
        "V0_assert_verify_passed_def",
        "def assert_verify_passed(" in src,
        "expect 'def assert_verify_passed(' in source",
    )


# ---------------------------------------------------------------------------
# V1: docstring sentinel + 本脚本 v4_behavior func sha 自锁
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
        got = _func_sha(self_path, "v4_behavior")
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
# V2: _verify_lib.py file sha + assert_verify_passed func sha
# ---------------------------------------------------------------------------
def v2_lib_locks() -> None:
    got_file = _file_sha(VERIFY_LIB)
    if EXPECTED_LIB_FILE_SHA == "__BUMP_ME__":
        _emit("V2_lib_file_sha", False, f"placeholder; bump EXPECTED_LIB_FILE_SHA={got_file}")
    else:
        _emit(
            "V2_lib_file_sha",
            got_file == EXPECTED_LIB_FILE_SHA,
            f"got={got_file[:16]} expect={EXPECTED_LIB_FILE_SHA[:16]}",
        )
    try:
        got_func = _func_sha(VERIFY_LIB, "assert_verify_passed")
    except Exception as e:
        _emit("V2_assert_verify_passed_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_ASSERT_VERIFY_PASSED_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V2_assert_verify_passed_func_sha",
            False,
            f"placeholder; bump EXPECTED_ASSERT_VERIFY_PASSED_FUNC_SHA={got_func}",
        )
    else:
        _emit(
            "V2_assert_verify_passed_func_sha",
            got_func == EXPECTED_ASSERT_VERIFY_PASSED_FUNC_SHA,
            f"got={got_func[:16]} expect={EXPECTED_ASSERT_VERIFY_PASSED_FUNC_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V3: mutant 反证
# ---------------------------------------------------------------------------
def _load_helper_with_mutated_src(mutated_src: str):
    """编译 mutated _verify_lib.py 源码, 返回独立 namespace 的 assert_verify_passed."""
    ns: dict = {"__name__": "_infra055_mutant", "__file__": str(VERIFY_LIB)}
    exec(compile(mutated_src, str(VERIFY_LIB) + ".mutant", "exec"), ns)
    return ns["assert_verify_passed"]


def v3_mutant() -> None:
    src = VERIFY_LIB.read_text(encoding="utf-8")

    # mutant-A: 把 verdict 校验短路掉 — 改 ``and verdict == "ALL PASS"`` 为
    # ``and verdict != "__never__"`` (恒真), FAIL stdout 喂入应被错判 passed=True
    needle_a = 'and verdict == "ALL PASS"'
    if src.count(needle_a) != 1:
        _emit("V3a_mutant_apply", False, f"needle not unique: count={src.count(needle_a)}")
    else:
        mutated_a = src.replace(needle_a, 'and verdict != "__never__"', 1)
        try:
            mutant_fn = _load_helper_with_mutated_src(mutated_a)
            fail_stdout = (
                "[verify_infra_055][PASS] V0_x ok\n"
                "[verify_infra_055][SUMMARY] FAIL 1/2: ['V2']\n"
            )
            res = mutant_fn(fail_stdout, "verify_infra_055")
            # mutant 把 verdict 校验失活, 但 fail_emits 仍为空 (上面 stdout 无 [FAIL] emit),
            # name 匹配, summary_line 存在 → mutant 下应错判 True
            _emit(
                "V3a_mutant_verdict_short_circuit",
                res["passed"] is True,
                f"mutant passed={res['passed']} reason={res['reason']!r}",
            )
        except Exception as e:
            _emit("V3a_mutant_verdict_short_circuit", False, f"exec err: {e!r}")

    # mutant-B: 把 fail_emits 校验短路掉 — 改 ``and not fail_emits`` 为
    # ``and not False`` (恒真), FAIL emit 但 SUMMARY 行被伪造为 ALL PASS 的 stdout
    # 应被错判 passed=True
    needle_b = "and not fail_emits"
    if src.count(needle_b) != 1:
        _emit("V3b_mutant_apply", False, f"needle not unique: count={src.count(needle_b)}")
    else:
        mutated_b = src.replace(needle_b, "and not False", 1)
        try:
            mutant_fn = _load_helper_with_mutated_src(mutated_b)
            sus_stdout = (
                "[verify_infra_055][FAIL] V2_xxx bad\n"
                "[verify_infra_055][SUMMARY] ALL PASS (2 checks)\n"
            )
            res = mutant_fn(sus_stdout, "verify_infra_055")
            _emit(
                "V3b_mutant_fail_emit_short_circuit",
                res["passed"] is True,
                f"mutant passed={res['passed']} reason={res['reason']!r}",
            )
        except Exception as e:
            _emit("V3b_mutant_fail_emit_short_circuit", False, f"exec err: {e!r}")


# ---------------------------------------------------------------------------
# V4: 行为验证 — round-trip helper + AGENTS.md 关键字
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # 直接 import 真 helper (sys.path 已含 scripts)
    try:
        from _verify_lib import assert_verify_passed
    except Exception as e:
        _emit("V4_import_helper", False, f"import err: {e!r}")
        return
    _emit("V4_import_helper", True, "imported assert_verify_passed")

    # 正例: ALL PASS stdout → passed=True
    good = (
        "[verify_infra_055][PASS] V0_x ok\n"
        "[verify_infra_055][PASS] V1_y ok\n"
        "[verify_infra_055][SUMMARY] ALL PASS (2 checks)\n"
    )
    r1 = assert_verify_passed(good, "verify_infra_055")
    _emit(
        "V4_pass_case_passed_true",
        r1["passed"] is True,
        f"r={r1}",
    )
    _emit(
        "V4_pass_case_checks_count",
        r1["checks"] == 2,
        f"checks={r1['checks']} expect=2",
    )
    _emit(
        "V4_pass_case_summary_line_nonempty",
        bool(r1["summary_line"]) and "ALL PASS" in r1["summary_line"],
        f"summary_line={r1['summary_line']!r}",
    )

    # 反例 1: FAIL emit + FAIL SUMMARY → passed=False
    bad_fail = (
        "[verify_infra_055][FAIL] V2_xxx got=abc\n"
        "[verify_infra_055][SUMMARY] FAIL 1/2: ['V2_xxx']\n"
    )
    r2 = assert_verify_passed(bad_fail, "verify_infra_055")
    _emit("V4_fail_case_passed_false", r2["passed"] is False, f"r={r2}")
    _emit("V4_fail_case_fail_emits_count", len(r2["fail_emits"]) == 1, f"fail_emits={r2['fail_emits']}")

    # 反例 2: 缺 SUMMARY 行 → passed=False
    no_summary = "[verify_infra_055][PASS] V0_x ok\n"
    r3 = assert_verify_passed(no_summary, "verify_infra_055")
    _emit(
        "V4_no_summary_passed_false",
        r3["passed"] is False and "missing SUMMARY" in r3["reason"],
        f"r={r3}",
    )

    # 反例 3: name mismatch → passed=False
    wrong_name = (
        "[verify_infra_099][PASS] V0_x ok\n"
        "[verify_infra_099][SUMMARY] ALL PASS (1 checks)\n"
    )
    r4 = assert_verify_passed(wrong_name, "verify_infra_055")
    _emit(
        "V4_name_mismatch_passed_false",
        r4["passed"] is False and "name mismatch" in r4["reason"],
        f"r={r4}",
    )

    # 反例 4: 空字符串 → passed=False
    r5 = assert_verify_passed("", "verify_infra_055")
    _emit("V4_empty_stdin_passed_false", r5["passed"] is False, f"r={r5}")

    # 正例 B (P274 Reviewer fix): 一段式 SUMMARY (旧脚本 infra_034 / robot_035 格式)
    #   ``[verify_infra_034] summary total=N failed=0`` → passed=True / checks=N
    good_b = (
        "[verify_infra_034][PASS] V0_x ok\n"
        "[verify_infra_034] summary total=53 failed=0\n"
    )
    r6 = assert_verify_passed(good_b, "verify_infra_034")
    _emit("V4_oneline_summary_pass_case", r6["passed"] is True, f"r={r6}")
    _emit("V4_oneline_summary_checks_count", r6["checks"] == 53, f"checks={r6['checks']} expect=53")

    good_b2 = "[verify_robot_035] summary total=8 failed=0\n"
    r7 = assert_verify_passed(good_b2, "verify_robot_035")
    _emit("V4_oneline_summary_robot_pass", r7["passed"] is True, f"r={r7}")

    # 反例 B: 一段式 failed=N>0 → passed=False
    bad_b = (
        "[verify_infra_034][FAIL] V2_xxx err\n"
        "[verify_infra_034] summary total=10 failed=2\n"
    )
    r8 = assert_verify_passed(bad_b, "verify_infra_034")
    _emit(
        "V4_oneline_summary_fail_case",
        r8["passed"] is False and len(r8["fail_emits"]) >= 1,
        f"r={r8}",
    )

    # 反例 B2: 一段式 failed>0 但无 FAIL emit (verdict 单独把关)
    bad_b2 = "[verify_robot_035] summary total=5 failed=3\n"
    r9 = assert_verify_passed(bad_b2, "verify_robot_035")
    _emit(
        "V4_oneline_summary_fail_no_emit",
        r9["passed"] is False and "not ALL PASS" in r9["reason"],
        f"r={r9}",
    )

    # AGENTS.md 关键字断言
    if not AGENTS_MD.is_file():
        _emit("V4_agents_md_keywords", False, "AGENTS.md missing")
    else:
        md = AGENTS_MD.read_text(encoding="utf-8")
        keywords = [
            "Sub-agent Evidence Report Accuracy",
            "stdout 尾行",
            "assert_verify_passed",
            "ALL PASS",
        ]
        missing = [k for k in keywords if k not in md]
        _emit(
            "V4_agents_md_keywords",
            not missing,
            f"missing={missing}" if missing else f"hit {len(keywords)}/{len(keywords)}",
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
    v2_lib_locks()
    v3_mutant()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_055][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_055][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
