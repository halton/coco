#!/usr/bin/env python3
"""verify_infra_083 V0-V5: 锁定 verify_infra_062._enforce_closeout_byte_match 真 fire (非 soft-skip).

infra-P299-followup2-enable-byte-match-real-run (phase-42 #2.42):
旧版 _enforce_closeout_byte_match 依赖 closeout_verify.main_head_sha startswith
当前 git HEAD + nested rounds schema with rc 字段, 实际从未触发 (closeout commit
后 bump 又移动 HEAD; flat schema 无 rc) → 永远 soft-skip, 等于挂牌不开门。本
feature 把 enforcement 改成 "弱版 anchor byte-match":

- 扫所有 passing feature.closeout_verify.verify_runs;
- 对每条 entry, 从 name (fallback script) 提取 verify_infra_NNN 数字,
  tail_stdout 必须含子串 'verify_infra_NNN' (tail-name 一致性 anchor,
  catch copy-paste 伪造);
- name 无 verify_infra_NNN 或 tail 为空 → soft_skip (Default-OFF 渐进 promote);
- violation > 0 → hard FAIL (真开门);
- enforced >= 1 → fire=True (真触发, 不再 soft-skip overall).

本 verify (083) 锁住:
- 062 函数体必须含 ``_classify_closeout_tail_anchor`` ast 调用 (helper 真被用);
- 062.main() 可达 _enforce_closeout_byte_match 调用链;
- 真跑 _classify_closeout_tail_anchor 行为正例/反例正确;
- 真跑 _enforce_closeout_byte_match 当前必须 fire=True (enforced>=1),
  否则视为退化回 soft-skip (复刻旧 P299-followup 漏洞);
- mutant: 删 helper 调用 → ast 扫降到 0;
- 062 file sha 锁防止源被悄悄改回不调 helper 的版本.

INFRA_083_SHA_LOCKS
-------------------
- ``scripts/verify_infra_083.py:main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA
- ``scripts/verify_infra_062.py`` file sha: EXPECTED_VERIFY_062_FILE_SHA
- ``scripts/verify_infra_062.py:_classify_closeout_tail_anchor`` func sha:
  EXPECTED_VERIFY_062_CLASSIFIER_FUNC_SHA

校验层级 (V0-V5, 共 14 checks):

- V0 scaffolding (×5)
- V1 self main() func sha 自锁
- V2 062 file sha
- V3 062._classify_closeout_tail_anchor func sha (canonical helper)
- V4 行为校验 (5 checks):
  - V4_1 062 函数体 ast 扫到至少一处 _classify_closeout_tail_anchor 调用
  - V4_2 调用位于 _enforce_closeout_byte_match 真 def 函数体, 且 062.main()
    可达 (main → _enforce_closeout_byte_match → _classify_closeout_tail_anchor)
  - V4_3 真跑 _classify_closeout_tail_anchor 行为正例/反例正确
    (含 anchor → enforced_ok; 缺 anchor → violation; 空 name → soft_skip)
  - V4_4 真跑 _enforce_closeout_byte_match 必须 fire=True (enforced>=1 且
    violations==0); soft-skip overall 视为退化失败
  - V4_5 mutant: 替换 062 中 _classify_closeout_tail_anchor 调用名 → ast 扫
    必须降到 0 (detection 真有效)
- V5 reviewer_lgtm_gate (真门: ok is True)

注意 (与 074/079/080/081/082 同形): 本脚本不锁自己 file sha, 避免与 V4 mutant
对源码做 ast 替换时与自检冲突, 且与 078 placeholder 禁字面约束兼容.

退出码: 0=ALL PASS, 2=任一 FAIL (走 verify_summary_exit).

运行环境约定 (infra-034): 必须在 .venv 下运行.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_062 = SCRIPTS / "verify_infra_062.py"
REAL_FEATURE_LIST = REPO / "feature_list.json"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_SELF_MAIN_FUNC_SHA = "83fba4707db4341679ab889dd4c16bcaef4a35e5762040e3988a4d12e0f9db53"
EXPECTED_VERIFY_062_FILE_SHA = "51fb9bdf49233ccbcf8eb9c1c47b492d18a1a152f5b7cd060cfca04b19ea878f"
EXPECTED_VERIFY_062_CLASSIFIER_FUNC_SHA = "1055318f4513bbb64d31aed79a37a0d90e60107e7be519245f33b43f7ce13351"

DOCSTRING_SENTINEL = "INFRA_083_SHA_LOCKS"

CLASSIFIER_NAME = "_classify_closeout_tail_anchor"
ENFORCER_NAME = "_enforce_closeout_byte_match"
V5_GATE_FEATURE_ID = "infra-P299-followup2-enable-byte-match-real-run"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_083][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, bool(ok), detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _scan_calls_in_source(src: str, callee: str) -> List[dict]:
    """ast 扫源码中所有 Call 节点, 找 func.id==callee 或 func.attr==callee."""
    out: List[dict] = []
    try:
        tree = ast.parse(src)
    except Exception:  # noqa: BLE001
        return out

    def _walk(node, func_stack):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_stack = func_stack + [node.name]
        if isinstance(node, ast.Call):
            f = node.func
            name = None
            if isinstance(f, ast.Name):
                name = f.id
            elif isinstance(f, ast.Attribute):
                name = f.attr
            if name == callee:
                arg1 = None
                if node.args:
                    try:
                        arg1 = ast.unparse(node.args[0])
                    except Exception:  # noqa: BLE001
                        arg1 = "<unparse-err>"
                out.append({
                    "lineno": getattr(node, "lineno", -1),
                    "in_func": func_stack[-1] if func_stack else "<module>",
                    "arg1_source": arg1,
                })
        for child in ast.iter_child_nodes(node):
            _walk(child, func_stack)

    _walk(tree, [])
    return out


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_verify_062_exists", VERIFY_062.is_file(), f"path={VERIFY_062}")
    _emit(
        "V0_classifier_name_nonempty",
        bool(CLASSIFIER_NAME),
        f"helper={CLASSIFIER_NAME}",
    )
    _emit(
        "V0_enforcer_name_nonempty",
        bool(ENFORCER_NAME),
        f"enforcer={ENFORCER_NAME}",
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
# V2: 062 file sha
# ---------------------------------------------------------------------------
def v2_verify_062_file_sha() -> None:
    got = _file_sha(VERIFY_062)
    if EXPECTED_VERIFY_062_FILE_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V2_verify_062_file_sha",
            True,
            f"placeholder OK; bump EXPECTED_VERIFY_062_FILE_SHA={got}",
        )
        return
    _emit(
        "V2_verify_062_file_sha",
        got == EXPECTED_VERIFY_062_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_062_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: classifier helper func sha (canonical)
# ---------------------------------------------------------------------------
def v3_classifier_func_sha() -> None:
    try:
        got = func_sha_by_name(VERIFY_062, CLASSIFIER_NAME)
    except Exception as e:  # noqa: BLE001
        _emit("V3_classifier_func_sha", False, f"error={e!r}")
        return
    if EXPECTED_VERIFY_062_CLASSIFIER_FUNC_SHA == ("__BUMP" + "_ME__"):
        _emit(
            "V3_classifier_func_sha",
            True,
            f"placeholder OK; bump EXPECTED_VERIFY_062_CLASSIFIER_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_classifier_func_sha",
        got == EXPECTED_VERIFY_062_CLASSIFIER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_062_CLASSIFIER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为校验
# ---------------------------------------------------------------------------
def _import_verify_062_module():
    """动态 import verify_infra_062 模块, 直接拿其 _classify_closeout_tail_anchor
    和 _enforce_closeout_byte_match 函数对象 (避免重复实现)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("verify_infra_062_under_083", VERIFY_062)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def v4_behavior() -> None:
    src062 = VERIFY_062.read_text(encoding="utf-8")
    classifier_calls = _scan_calls_in_source(src062, CLASSIFIER_NAME)
    enforcer_calls = _scan_calls_in_source(src062, ENFORCER_NAME)

    # V4_1: 062 内至少一处 classifier 调用
    _emit(
        "V4_1_verify_062_calls_classifier",
        len(classifier_calls) >= 1,
        f"call_count={len(classifier_calls)} "
        f"sites={[(c['lineno'], c['in_func']) for c in classifier_calls]}",
    )

    # V4_2: classifier 调用必须在 enforcer 函数体内; main 必须可达 enforcer
    main_calls_enforcer = any(c["in_func"] == "main" for c in enforcer_calls)
    enforcer_calls_classifier = any(
        c["in_func"] == ENFORCER_NAME for c in classifier_calls
    )
    _emit(
        "V4_2_call_in_main_chain",
        main_calls_enforcer and enforcer_calls_classifier,
        f"main_calls_enforcer={main_calls_enforcer} "
        f"enforcer_calls_classifier={enforcer_calls_classifier}",
    )

    # V4_3: 真跑 _classify_closeout_tail_anchor 三类样本
    try:
        mod = _import_verify_062_module()
        classifier = getattr(mod, CLASSIFIER_NAME)
        v_ok, _ = classifier(
            "verify_infra_062.py",
            "blah\n[verify_infra_062][SUMMARY] ALL PASS (19 checks)",
        )
        v_vio, _ = classifier(
            "verify_infra_062.py",
            "blah\n[verify_infra_999][SUMMARY] ALL PASS",
        )
        v_skip, _ = classifier("./init.sh (smoke)", "==> Smoke 通过")
        v_empty, _ = classifier("verify_infra_034.py", "")
        ok_behavior = (
            v_ok == "enforced_ok"
            and v_vio == "violation"
            and v_skip == "soft_skip"
            and v_empty == "soft_skip"
        )
    except Exception as e:  # noqa: BLE001
        ok_behavior = False
        v_ok = v_vio = v_skip = v_empty = f"err={e!r}"
    _emit(
        "V4_3_classifier_behavior_correct",
        ok_behavior,
        f"enforced_ok={v_ok!r} violation={v_vio!r} skip_smoke={v_skip!r} "
        f"skip_empty={v_empty!r}",
    )

    # V4_4: 真跑 _enforce_closeout_byte_match — 必须 fire (非 soft-skip overall)
    # 用真 feature_list, 捕获其 _emit 的最后一条 V4_byte_match_enforce
    try:
        mod = _import_verify_062_module()
        mod._results.clear()  # noqa: SLF001
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod._enforce_closeout_byte_match()  # noqa: SLF001
        emits = [r for r in mod._results if r[0] == "V4_byte_match_enforce"]  # noqa: SLF001
        fired_ok = False
        emit_detail = ""
        if emits:
            tag, ok, detail = emits[-1]
            emit_detail = detail
            # fire=True 在 detail 字段中, 非 soft-skip 路径才会有
            fired_ok = ("fire=True" in detail) and ("soft-skip" not in detail) and ok
    except Exception as e:  # noqa: BLE001
        fired_ok = False
        emit_detail = f"err={e!r}"
    _emit(
        "V4_4_enforce_byte_match_fires",
        fired_ok,
        f"last_emit_detail={emit_detail!r}",
    )

    # V4_5: mutant — 替换 062 中 CLASSIFIER_NAME 调用名 → ast 扫降到 0
    mutant_src, n_sub = re.subn(
        re.escape(CLASSIFIER_NAME) + r"\b",
        f"_DELETED_{CLASSIFIER_NAME}_",
        src062,
    )
    mutant_calls = _scan_calls_in_source(mutant_src, CLASSIFIER_NAME)
    _emit(
        "V4_5_mutant_strip_call_detected_zero",
        n_sub >= 1 and len(mutant_calls) == 0,
        f"n_sub={n_sub} mutant_call_count={len(mutant_calls)} "
        f"orig_count={len(classifier_calls)}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (真门: ok is True)
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
    _emit(
        "V5_reviewer_lgtm_gate",
        ok is True,
        f"target={V5_GATE_FEATURE_ID} ok={ok} reason={reason!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_func_sha()
    v2_verify_062_file_sha()
    v3_classifier_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    failed_tags = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_083][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_083][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
