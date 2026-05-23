#!/usr/bin/env python3
"""verify_infra_080 V0-V5: 锁定 v5_reviewer_gate 必须用 assert_reviewer_lgtm 返回值参与 _emit.

infra-P291-followup2-helper-return-value-must-participate-in-emit (phase-41 #2.41):
phase-40 #5.40 P0-1 root cause = verify_infra_079 V5 写 ``_emit("V5...", True, "...")``
硬绿绕过 reviewer evidence 校验. 本 feature 加 ast 锁机械化禁止此反模式再现:

- 新 helper ``assert_v5_gate_emit_uses_helper_return(verify_script_path)``
  ast 扫 ``v5_reviewer_gate`` 函数体, 检查每个 ``_emit(...)`` 第二位置参数:
  字面 ``True`` (硬绿) 即视为 violation. 字面 ``False/None`` 视为 guard,
  合法 (典型: feature_list 缺失时 early-return).

- enrolled list: 当前已迁移到真门写法的 verify 脚本 (含本脚本自身).
  对每个 enrolled verify, helper 返回 ok=True 且 violations 为空. mutant
  scenario 真改其中一个为硬绿, helper 必须返回 ok=False.

INFRA_080_SHA_LOCKS
-------------------
- ``scripts/verify_infra_080.py:main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA
  (自锁 main 函数体, 防止 v5_reviewer_gate 调用顺序被悄悄改掉)
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``scripts/_verify_lib.py:assert_v5_gate_emit_uses_helper_return`` func sha:
  EXPECTED_HELPER_FUNC_SHA

注意 (与 074/079 同形): 本脚本不锁自己 file sha — file sha 自锁会跟
"v4_5 mutant 必须能跑过 V2 自检" 冲突 (见 079 docstring), 且 078 反
placeholder 锁不允许仓库里出现 ``__BUMP_ME__`` 字面.

校验层级 (V0-V5):

- V0 scaffolding (×5)
- V1 self main() func sha 自锁
- V2 _verify_lib.py file sha
- V3 helper assert_v5_gate_emit_uses_helper_return func sha
- V4 行为校验 (5 checks):
  - V4_1 helper 对 verify_infra_079 (典型真门) 返回 ok=True, violations=[]
  - V4_2 enrolled list (079, 074, 073, 072, 071, 080) 全部 ok=True 且
    每脚本 calls_assert_helper=True 且 helper_return_names 含 "ok"
  - V4_3 helper 对一个已知历史 soft-PASS 脚本 (060) 返回 ok=False
    (回归保护: 防止有人去掉硬绿检测)
  - V4_4 mutant: 拷贝 enrolled 中任一脚本 (079) 到 tmp 路径并将
    ``_emit("V5_reviewer_lgtm_gate", ok is True, ...)`` 改为 ``_emit(..., True, ...)``,
    helper 必须对 mutant 返回 ok=False 且 violations 含字面 True
  - V4_5 helper schema 字段完整: 任一调用返回 dict 必含
    keys {ok, checked, v5_gate_found, calls_assert_helper, helper_return_names,
    emit_calls, violations, error}
- V5 reviewer_lgtm_gate (真门: ok is True)

退出码: 0=ALL PASS, 2=任一 FAIL (走 verify_summary_exit).

运行环境约定 (infra-034): 必须在 .venv 下运行.
"""
from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "_verify_lib.py"
REAL_FEATURE_LIST = REPO / "feature_list.json"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (
    assert_reviewer_lgtm,
    assert_v5_gate_emit_uses_helper_return,
    func_sha_by_name,
    verify_summary_exit,
    assert_v5_reviewer_gate_evidence_bind,
)

EXPECTED_SELF_MAIN_FUNC_SHA = "9e4f8ac57fd44c204f1f72a6074f5e527a20d7cdd09591b68191d0adaf69935c"
EXPECTED_VERIFY_LIB_FILE_SHA = "43d352534301242f13ef33909cdba2585f8f18734d9e052f2e0de0d075955d75"
EXPECTED_HELPER_FUNC_SHA = "eb093670ad42fa6b6f6a9a118951ea5bc2942715c907a08542bacf1586dbce48"

DOCSTRING_SENTINEL = "INFRA_080_SHA_LOCKS"

V5_GATE_FEATURE_ID = "infra-P291-followup2-helper-return-value-must-participate-in-emit"

# enrolled list: 已迁移到真门写法的 verify 脚本 (含本脚本自身)
ENROLLED_TRUE_GATE_VERIFY: Tuple[str, ...] = (
    "verify_infra_071.py",
    "verify_infra_072.py",
    "verify_infra_073.py",
    "verify_infra_074.py",
    "verify_infra_079.py",
    "verify_infra_080.py",
)

# 历史 soft-PASS 脚本 (硬绿写法), 用于 V4_3 回归保护
KNOWN_SOFT_PASS_VERIFY: str = "verify_infra_060.py"

# ----------------------------------------------------------------------------
# V4_3 fixture documentation (added by phase-48 #3.48):
#
# V4_3 模拟的 anti-pattern 场景:
#   V5_reviewer_lgtm_gate 用硬编码 True 作为 _emit 的第二位参数 (硬绿写法,
#   helper 的 ok 返回值未参与 emit 判定). 这是 phase-47 #1.47 之前真实仓库
#   存在过的反模式 (历史 soft-PASS exemplar = verify_infra_060.py); graduate
#   完成后真实仓库已无此写法, 故改用本地合成 fixture 验证 detector 仍有效.
#
# Enrolled (合成进 tmp fixture 文件) 的内容:
#   - 文件名: _080_v4_3_fixture.py (写到 TemporaryDirectory 后立删)
#   - 函数体: def v5_reviewer_gate(): _emit("V5_reviewer_lgtm_gate", True, ...)
#   - 即一个硬编码 True 的 V5 gate 反模式样本
#
# detector = assert_v5_gate_emit_uses_helper_return; 期望对该 fixture 返回:
#   - ok = False                       (检测到反模式)
#   - violations 长度 >= 1             (至少一条违规)
#   - 存在 arg1_source == "True" 的违规 (定位到硬编码 True 那一行)
#
# 同时对真实 KNOWN_SOFT_PASS_VERIFY (verify_infra_060.py) 跑一次 detector 仅
# 作 informational 用途 (graduate 后预期 ok=True), 不作为 V4_3 通过/不通过的
# 判定依据.
# ----------------------------------------------------------------------------

HELPER_RETURN_SCHEMA_KEYS = (
    "ok", "checked", "v5_gate_found", "calls_assert_helper",
    "helper_return_names", "emit_calls", "violations", "error",
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_080][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, bool(ok), detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_lib_exists", LIB.is_file(), f"path={LIB}")
    _emit(
        "V0_enrolled_nonempty",
        len(ENROLLED_TRUE_GATE_VERIFY) >= 5,
        f"count={len(ENROLLED_TRUE_GATE_VERIFY)}",
    )
    _emit(
        "V0_soft_pass_exemplar_exists",
        (SCRIPTS / KNOWN_SOFT_PASS_VERIFY).is_file(),
        f"path={SCRIPTS / KNOWN_SOFT_PASS_VERIFY}",
    )
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8")))
    _emit(
        "V0_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )


# ---------------------------------------------------------------------------
# V1: self main() func sha
# ---------------------------------------------------------------------------
def v1_self_func_sha() -> None:
    try:
        got = func_sha_by_name(Path(__file__), "main")
    except Exception as e:  # noqa: BLE001
        _emit("V1_self_main_func_sha", False, f"error={e!r}")
        return
    if EXPECTED_SELF_MAIN_FUNC_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V1_self_main_func_sha",
            True,
            f"placeholder OK; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
        return
    if not _is_hex64(EXPECTED_SELF_MAIN_FUNC_SHA):
        _emit(
            "V1_self_main_func_sha",
            False,
            f"EXPECTED_SELF_MAIN_FUNC_SHA not 64-hex; actual={got}",
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
    got = _file_sha(LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V2_lib_file_sha",
            True,
            f"placeholder OK; bump EXPECTED_VERIFY_LIB_FILE_SHA={got}",
        )
        return
    _emit(
        "V2_lib_file_sha",
        got == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: helper func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "assert_v5_gate_emit_uses_helper_return")
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"error={e!r}")
        return
    if EXPECTED_HELPER_FUNC_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V3_helper_func_sha",
            True,
            f"placeholder OK; bump EXPECTED_HELPER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_HELPER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为校验
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # V4_1: 典型真门 verify_infra_079 → ok=True
    r79 = assert_v5_gate_emit_uses_helper_return(SCRIPTS / "verify_infra_079.py")
    _emit(
        "V4_1_helper_on_079_returns_ok",
        r79.get("ok") is True and len(r79.get("violations", [])) == 0,
        f"ok={r79.get('ok')} v_count={len(r79.get('violations', []))}",
    )

    # V4_2: enrolled list 全部 ok=True. phase-47 #1.47 graduate 后, enrolled
    # 脚本已从 assert_reviewer_lgtm 解包写法迁移到 result = assert_v5_reviewer_gate_evidence_bind(...)
    # 单值返回. 接受两种 helper 写法 (新旧并存):
    #   - 旧: calls_assert_helper=True AND "ok" in helper_return_names
    #   - 新: calls_assert_helper=False AND violations=0 (graduate, 无硬绿反模式)
    # 共同必备: violations 必须为 0 (没有 hardcoded True 字面被 _emit 第二位置参数引用)
    enrolled_fail: List[str] = []
    enrolled_details: List[str] = []
    for name in ENROLLED_TRUE_GATE_VERIFY:
        p = SCRIPTS / name
        if not p.is_file():
            enrolled_fail.append(f"{name}:missing")
            continue
        r = assert_v5_gate_emit_uses_helper_return(p)
        ok = r.get("ok")
        calls = r.get("calls_assert_helper")
        names = r.get("helper_return_names") or []
        v_count = len(r.get("violations", []))
        legacy_ok = (calls is True) and ("ok" in names)
        graduated_ok = (calls is False) and (v_count == 0) and (r.get("v5_gate_found") is True)
        cond = (ok is True) and (v_count == 0) and (legacy_ok or graduated_ok)
        if not cond:
            enrolled_fail.append(
                f"{name}:ok={ok},calls={calls},names={names},v={v_count}"
            )
        enrolled_details.append(f"{name}:ok={ok},mode={'legacy' if legacy_ok else 'graduated' if graduated_ok else 'unknown'}")
    _emit(
        "V4_2_enrolled_all_ok",
        not enrolled_fail,
        f"enrolled_count={len(ENROLLED_TRUE_GATE_VERIFY)} failures={enrolled_fail} "
        f"details={enrolled_details}",
    )

    # V4_3: anti-pattern detector 回归保护.
    # phase-47 #1.47 graduate 后, 仓库内已无 hardcoded True 字面 V5 反模式
    # exemplar (含历史 soft-PASS 060 也 graduate 过). 此时 detector 在真实
    # 仓库脚本上不会触发. 改用本地合成 fixture 来验证 detector 仍能识别
    # 硬绿写法 (写到 tmp 文件, 跑 helper, 立删).
    import tempfile as _tempfile
    _fixture_src = (
        "def v5_reviewer_gate():\n"
        "    _emit(\"V5_reviewer_lgtm_gate\", True, \"hardcoded soft-PASS\")\n"
    )
    with _tempfile.TemporaryDirectory() as _td:
        _fx = Path(_td) / "_080_v4_3_fixture.py"
        _fx.write_text(_fixture_src, encoding="utf-8")
        r_fx = assert_v5_gate_emit_uses_helper_return(_fx)
    fx_v_count = len(r_fx.get("violations", []))
    fx_has_true = any(v.get("arg1_source") == "True" for v in r_fx.get("violations", []))
    # 同时跑一遍真实 KNOWN_SOFT_PASS_VERIFY 仅作 informational
    r_real = assert_v5_gate_emit_uses_helper_return(SCRIPTS / KNOWN_SOFT_PASS_VERIFY)
    _emit(
        "V4_3_soft_pass_exemplar_returns_fail",
        r_fx.get("ok") is False and fx_v_count >= 1 and fx_has_true,
        f"fixture_ok={r_fx.get('ok')} fixture_v={fx_v_count} has_True={fx_has_true} "
        f"real_target={KNOWN_SOFT_PASS_VERIFY} real_ok={r_real.get('ok')} "
        f"real_v={len(r_real.get('violations', []))} "
        f"reason='graduate complete, detector verified via synthetic fixture'",
    )

    # V4_4: mutant — 拷贝 enrolled exemplar 到 tmp 并把 第二位置参数改为 True
    # (硬绿). 接受新旧两种 emit pattern:
    #   - 旧: _emit("V5_reviewer_lgtm_gate", ok is True, ...)
    #   - 新: _emit("V5_reviewer_lgtm_gate", bool(result["ok"]), ...)
    # 任一替换成 True 都算 mutation 成功.
    src79 = (SCRIPTS / "verify_infra_079.py").read_text(encoding="utf-8")
    mutant_src = src79
    n_sub_total = 0
    for _pat in (
        r'_emit\(\s*"V5_reviewer_lgtm_gate"\s*,\s*ok\s+is\s+True',
        r'_emit\(\s*"V5_reviewer_lgtm_gate"\s*,\s*bool\(result\["ok"\]\)',
    ):
        mutant_src, _n = re.subn(
            _pat,
            '_emit("V5_reviewer_lgtm_gate", True',
            mutant_src,
            count=1,
        )
        n_sub_total += _n
        if _n >= 1:
            break  # 任一模式命中即可
    substituted = n_sub_total >= 1 and mutant_src != src79
    mutant_path = SCRIPTS / "_for_080_v4_4_mutant_079.py"
    try:
        mutant_path.write_text(mutant_src, encoding="utf-8")
        rm = assert_v5_gate_emit_uses_helper_return(mutant_path)
    finally:
        try:
            mutant_path.unlink()
        except FileNotFoundError:
            pass
    has_true_violation = any(
        v.get("arg1_source") == "True" for v in rm.get("violations", [])
    )
    _emit(
        "V4_4_mutant_returns_fail",
        substituted and rm.get("ok") is False and has_true_violation,
        f"substituted={substituted} n_sub={n_sub_total} mutant_ok={rm.get('ok')} "
        f"v_count={len(rm.get('violations', []))} has_True_violation={has_true_violation}",
    )

    # V4_5: helper schema 字段完整
    missing_keys = [k for k in HELPER_RETURN_SCHEMA_KEYS if k not in r79]
    _emit(
        "V4_5_helper_schema_complete",
        not missing_keys,
        f"missing_keys={missing_keys} expected={HELPER_RETURN_SCHEMA_KEYS}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (真门: ok is True)
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
            f"[verify_infra_080][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_080][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
