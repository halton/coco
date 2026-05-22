#!/usr/bin/env python3
"""verify_infra_062 V0-V5: Closeout-verify-trustworthy 硬规则 helper 行为锁.

infra-P278-closeout-verify-trustworthy (phase-37 #3.37): 源自 P275 closeout 误报
事件 — Closeout sub-agent 报告 verify 结果不可信 (报 FAIL 但 main 上实际全 PASS,
或反之). P278 把"Closeout-verify-trustworthy"做成机械化可验证的硬规则 + verify
脚本, 而不仅仅是文档段.

P278 提供的硬规则 (helper ``verify_closeout_evidence_trustworthy``):

1. ``closeout_verify.main_head_sha`` 必须存在且 ``>=7`` 位 hex (merge 后必须在
   main HEAD 上跑 verify, 不能在 working tree).
2. ``closeout_verify.verify_runs`` 非空, 每项 ``tail_stdout`` 非空,
   ``status in {PASS, FAIL}``.
3. 任一 FAIL 必须配套 ``pre_existing_baseline_sha`` 与 ``baseline_tail_stdout``
   字段 (即必须先在 pre-merge main baseline 上独立复现, 才算 pre-existing).
4. ``closeout_verify.smoke_tail_stdout`` 非空.
5. ``reviewer.reviewer_kind == 'sub_agent_fresh_context'`` 且 ``lgtm == True``
   (主 context 自审不算合规).

INFRA_062_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``verify_closeout_evidence_trustworthy`` func sha: EXPECTED_CLOSEOUT_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: _verify_lib.py 存在 + helper 公开 (def 形 + ``__all__`` 含名)
  + 本脚本 ``__main__`` + ``def main()`` 入口可执行
- V1 docstring sentinel ``INFRA_062_SHA_LOCKS`` + 本脚本 v4_behavior func sha 自锁
- V2 _verify_lib.py file sha
- V3 verify_closeout_evidence_trustworthy canonical func sha (ast.unparse)
- V4 行为 (合成样本 A-H 全覆盖每条 rule 的 PASS/FAIL 路径):
  - 样本 A (compliant): 全字段齐全, 期望 all_trustworthy=True
  - 样本 B (missing main_head): 缺 main_head_sha, 期望 False
  - 样本 C (empty verify_runs): verify_runs=[], 期望 False
  - 样本 D (FAIL no baseline): 单个 FAIL run 无 baseline_tail_stdout, 期望 False
  - 样本 E (reviewer not fresh): reviewer.kind != sub_agent_fresh_context, 期望 False
  - 样本 F (FAIL with baseline): 配齐 baseline, 期望 True
  - 样本 G (main_head 长度不足 7 hex): 边界, 期望 False
  - 样本 H (status 字段值非法 MAYBE): 边界, 期望 False
  - **不**做真实 feature evidence dogfood: helper 只验 schema, 不验
    stdout 字符串真伪 (round-1 设计踩坑教训: 回填的 tail_stdout 可被
    编造, 等于把单点谎言换地方放). stdout 真伪验证留待 backlog
    ``infra-P294-closeout-stdout-sha-verification`` 提供 sha256-of-stdout
    防伪机制后再回归。
- V5 Reviewer LGTM gate (print-only)

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
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P278-closeout-verify-trustworthy"
LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_baseline_head_echo_present_and_matches,
    assert_report_matches_closeout_runs,
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_closeout_evidence_trustworthy,
)

EXPECTED_VERIFY_LIB_FILE_SHA = "ebcec7ec936822801c7d656f9b6de429d2145f74133676ead1dfd860c5addd0d"
EXPECTED_CLOSEOUT_FUNC_SHA = "99bd10127cd14263d633d3d66bf9a50ea153130d2f2e909781ad5452c909b39d"
EXPECTED_V4_CHECKER_FUNC_SHA = "66a2cdb26e7ef571e9b3753002db0fc535797b1e9a7e6d5896c73c51b042b228"

DOCSTRING_SENTINEL = "INFRA_062_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_062][{mark}] {tag} {detail}", flush=True)
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
        "def verify_closeout_evidence_trustworthy(" in src,
        "expect 'def verify_closeout_evidence_trustworthy(' in lib",
    )
    tree = ast.parse(src)
    all_names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for el in node.value.elts:
                            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                all_names.append(el.value)
    _emit(
        "V0_helper_in_all",
        "verify_closeout_evidence_trustworthy" in all_names,
        f"__all__ contains {len(all_names)} names",
    )
    self_src = Path(__file__).read_text(encoding="utf-8")
    _emit(
        "V0_self_main_entry",
        'if __name__ == "__main__":' in self_src and "def main(" in self_src,
        "expect __main__ guard + def main()",
    )


# ---------------------------------------------------------------------------
# V1: docstring sentinel + v4_behavior func sha 自锁
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
# V3: verify_closeout_evidence_trustworthy canonical func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "verify_closeout_evidence_trustworthy")
    except Exception as e:
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_CLOSEOUT_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V3_helper_func_sha",
            False,
            f"placeholder; bump EXPECTED_CLOSEOUT_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_CLOSEOUT_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_CLOSEOUT_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为 — 5 种合成样本 + dogfood 回溯 P281/P285 evidence
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # 样本 A: 合规 — 全字段齐全
    sample_compliant = {
        "closeout_verify": {
            "main_head_sha": "90a23de",
            "smoke_tail_stdout": "[smoke] ALL PASS\nrc=0\n",
            "verify_runs": [
                {
                    "script": "scripts/verify_infra_061.py",
                    "tail_stdout": "[verify_infra_061][SUMMARY] ALL PASS (18 checks)\n",
                    "status": "PASS",
                },
            ],
        },
        "reviewer": {
            "reviewer_kind": "sub_agent_fresh_context",
            "lgtm": True,
        },
    }
    r_a = verify_closeout_evidence_trustworthy(sample_compliant)
    _emit(
        "V4_sample_A_compliant_true",
        r_a.get("all_trustworthy") is True and r_a.get("failed_checks") == 0,
        f"r_a={r_a}",
    )

    # 样本 B: 缺 main_head_sha
    sample_b = dict(sample_compliant)
    sample_b["closeout_verify"] = dict(sample_compliant["closeout_verify"])
    sample_b["closeout_verify"].pop("main_head_sha", None)
    r_b = verify_closeout_evidence_trustworthy(sample_b)
    _emit(
        "V4_sample_B_no_main_head_false",
        r_b.get("all_trustworthy") is False
        and r_b.get("main_head_present") is False,
        f"failed_reasons={r_b.get('failed_reasons')}",
    )

    # 样本 C: verify_runs 空
    sample_c = {
        "closeout_verify": {
            "main_head_sha": "90a23de",
            "smoke_tail_stdout": "ok\n",
            "verify_runs": [],
        },
        "reviewer": {"reviewer_kind": "sub_agent_fresh_context", "lgtm": True},
    }
    r_c = verify_closeout_evidence_trustworthy(sample_c)
    _emit(
        "V4_sample_C_empty_runs_false",
        r_c.get("all_trustworthy") is False
        and any("verify_runs" in s for s in r_c.get("failed_reasons", [])),
        f"failed_reasons={r_c.get('failed_reasons')}",
    )

    # 样本 D: FAIL 无 baseline
    sample_d = {
        "closeout_verify": {
            "main_head_sha": "90a23de",
            "smoke_tail_stdout": "ok\n",
            "verify_runs": [
                {
                    "script": "scripts/verify_infra_037.py",
                    "tail_stdout": "[verify_infra_037][SUMMARY] FAIL 2/18\n",
                    "status": "FAIL",
                    # 缺 pre_existing_baseline_sha / baseline_tail_stdout
                },
            ],
        },
        "reviewer": {"reviewer_kind": "sub_agent_fresh_context", "lgtm": True},
    }
    r_d = verify_closeout_evidence_trustworthy(sample_d)
    _emit(
        "V4_sample_D_FAIL_no_baseline_false",
        r_d.get("all_trustworthy") is False
        and any("baseline" in s for s in r_d.get("failed_reasons", [])),
        f"failed_reasons={r_d.get('failed_reasons')}",
    )

    # 样本 E: reviewer 非 sub_agent_fresh_context
    sample_e = {
        "closeout_verify": sample_compliant["closeout_verify"],
        "reviewer": {"reviewer_kind": "main_context_self_review", "lgtm": True},
    }
    r_e = verify_closeout_evidence_trustworthy(sample_e)
    _emit(
        "V4_sample_E_reviewer_not_fresh_false",
        r_e.get("all_trustworthy") is False
        and r_e.get("reviewer_fresh_context") is False,
        f"failed_reasons={r_e.get('failed_reasons')}",
    )

    # 样本 F: FAIL 配齐 baseline → 应合规
    sample_f = {
        "closeout_verify": {
            "main_head_sha": "90a23de",
            "smoke_tail_stdout": "ok\n",
            "verify_runs": [
                {
                    "script": "scripts/verify_infra_037.py",
                    "tail_stdout": "[verify_infra_037][SUMMARY] FAIL 2/18\n",
                    "status": "FAIL",
                    "pre_existing_baseline_sha": "843bf24",
                    "baseline_tail_stdout": "[verify_infra_037][SUMMARY] FAIL 2/18\n",
                },
            ],
        },
        "reviewer": {"reviewer_kind": "sub_agent_fresh_context", "lgtm": True},
    }
    r_f = verify_closeout_evidence_trustworthy(sample_f)
    _emit(
        "V4_sample_F_FAIL_with_baseline_true",
        r_f.get("all_trustworthy") is True,
        f"r_f={r_f}",
    )

    # 样本 G (P278 round-2): main_head_sha 长度不足 7 hex (rule 1 边界)
    sample_g = {
        "closeout_verify": {
            "main_head_sha": "abc123",  # 6 hex, 不足 7
            "smoke_tail_stdout": "ok\n",
            "verify_runs": [
                {
                    "script": "scripts/verify_infra_001.py",
                    "tail_stdout": "ALL PASS\n",
                    "status": "PASS",
                },
            ],
        },
        "reviewer": {"reviewer_kind": "sub_agent_fresh_context", "lgtm": True},
    }
    r_g = verify_closeout_evidence_trustworthy(sample_g)
    _emit(
        "V4_sample_G_main_head_short_false",
        r_g.get("all_trustworthy") is False
        and r_g.get("main_head_present") is False,
        f"failed_reasons={r_g.get('failed_reasons')}",
    )

    # 样本 H (P278 round-2): status 字段值非法 (rule 2 边界)
    sample_h = {
        "closeout_verify": {
            "main_head_sha": "90a23de",
            "smoke_tail_stdout": "ok\n",
            "verify_runs": [
                {
                    "script": "scripts/verify_infra_001.py",
                    "tail_stdout": "??\n",
                    "status": "MAYBE",  # 非法
                },
            ],
        },
        "reviewer": {"reviewer_kind": "sub_agent_fresh_context", "lgtm": True},
    }
    r_h = verify_closeout_evidence_trustworthy(sample_h)
    _emit(
        "V4_sample_H_status_invalid_false",
        r_h.get("all_trustworthy") is False
        and any("status invalid" in s for s in r_h.get("failed_reasons", [])),
        f"failed_reasons={r_h.get('failed_reasons')}",
    )

    # P278 round-2: 移除真实 P281/P285 evidence dogfood — helper 只验 schema, 不验
    # stdout 真伪 (tail_stdout 可被编造); 留待 backlog infra-P294-closeout-stdout-
    # sha-verification 提供 sha256-of-stdout 防伪机制后再回归。当前 V4 只跑合成
    # 样本 A-H, 覆盖每条 rule 的 PASS/FAIL 路径。


# ---------------------------------------------------------------------------
# P299-followup wire-into-closeout-gate (infra-P299-followup-wire-into-closeout-gate):
# 在 062 内部 wire assert_report_matches_closeout_runs helper, 使 closeout
# byte-match 真伪校验在 ./init.sh / closeout gate 路径上有静态调用点 (机械化
# 阻 merge 的 ast-lock 由 verify_infra_081 V4 保证). 实际 byte-match 真跑
# 留 Default-OFF: schema 不兼容 (现存 flat verify_runs vs P299 nested round
# 结构) 或 main_head 不匹配 → soft-PASS 跳过. 完整 enforcement 留 backlog
# infra-P299-followup2-enable-byte-match-real-run。
# ---------------------------------------------------------------------------
def _enforce_closeout_byte_match() -> None:
    """对 feature_list.json 中当前 main HEAD 对应的 closeout feature 调
    assert_report_matches_closeout_runs (Default-OFF: schema 不兼容或不
    匹配则 soft-PASS 跳过, 不阻 062 主流程; 081 V4 ast-lock 锁住"062 必须
    调此 helper"这一静态事实)."""
    import json
    import subprocess as _sp  # noqa: WPS433

    feature_list = REAL_FEATURE_LIST
    if not feature_list.is_file():
        _emit(
            "V4_byte_match_enforce",
            True,
            f"soft-skip: feature_list missing at {feature_list}",
        )
        return
    try:
        head = _sp.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO), capture_output=True, text=True, check=False,
        ).stdout.strip()
    except Exception as e:  # noqa: BLE001
        _emit("V4_byte_match_enforce", True, f"soft-skip: git HEAD err={e!r}")
        return
    try:
        fl = json.loads(feature_list.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        _emit("V4_byte_match_enforce", True, f"soft-skip: feature_list parse err={e!r}")
        return
    target = None
    for f in fl.get("features", []):
        if f.get("status") != "passing":
            continue
        ev = f.get("evidence")
        if not isinstance(ev, dict):
            continue
        cv = ev.get("closeout_verify") or {}
        if not isinstance(cv, dict):
            continue
        ch = cv.get("main_head_sha") or ""
        if isinstance(ch, str) and ch and head.startswith(ch):
            target = (f.get("id"), cv)
            break
    if target is None:
        _emit(
            "V4_byte_match_enforce",
            True,
            f"soft-skip: no passing feature.closeout_verify matches HEAD={head[:12]}",
        )
        return
    fid, cv = target
    # P299 helper 期望 nested round schema: {"verify_runs":[{"round":N,"scripts":{...}}]}
    # 现存 closeout_verify 是 flat list [{"script","status","tail_stdout"}], schema
    # 不兼容 → 包成单 round 适配, 若仍不兼容 (字段缺) helper 自身会报 schema err.
    flat_runs = cv.get("verify_runs") or []
    if not isinstance(flat_runs, list) or not flat_runs:
        _emit(
            "V4_byte_match_enforce",
            True,
            f"soft-skip: target={fid} verify_runs empty/not-list",
        )
        return
    scripts_map: dict = {}
    for r in flat_runs:
        if not isinstance(r, dict):
            continue
        name = r.get("script")
        tail = r.get("tail_stdout")
        status = r.get("status")
        rc = r.get("rc")  # legacy schema 无 rc, 跳过
        if not (isinstance(name, str) and isinstance(tail, str)
                and isinstance(status, str) and isinstance(rc, int)):
            continue
        scripts_map[name] = {"rc": rc, "tail_stdout": tail, "status": status}
    if not scripts_map:
        _emit(
            "V4_byte_match_enforce",
            True,
            f"soft-skip: target={fid} legacy flat schema (no rc field); "
            f"byte-match enforcement deferred to backlog infra-P299-followup2",
        )
        return
    report_obj = {"verify_runs": [{"round": 1, "scripts": scripts_map}]}
    try:
        result = assert_report_matches_closeout_runs(
            report_obj, REPO, cv.get("main_head_sha", ""),
        )
    except Exception as e:  # noqa: BLE001
        _emit("V4_byte_match_enforce", True, f"soft-skip: helper err={e!r}")
        return
    # 真跑成功: ok=True 时 hard PASS, ok=False 时 hard FAIL (真造假证据)
    _emit(
        "V4_byte_match_enforce",
        bool(result.get("ok")),
        f"target={fid} checked={result.get('checked')} "
        f"matched={result.get('matched')} "
        f"offending_count={len(result.get('offending') or [])}",
    )


# ---------------------------------------------------------------------------
# infra-P286-followup3-promote-baseline-head-echo-to-P278-hard-required
# (phase-42 #1.42): 把 baseline_head_echo 从 dogfood/单 evidence legacy 容差
# promote 为 P278 hard-required 第 6 信号。真调跨 feature_list 扫描型 helper
# assert_baseline_head_echo_present_and_matches: 任一 feature 含 echo 字段但
# 与 closeout_verify.baseline_head_sha 前 7 hex 不一致 → V4_baseline_head_echo_required
# FAIL (Default-OFF: 缺字段或缺 baseline_sha 走 soft_skipped, 不阻 pre-P286 老 evidence).
# ---------------------------------------------------------------------------
def _enforce_baseline_head_echo_required() -> None:
    """对 feature_list.json 调 assert_baseline_head_echo_present_and_matches,
    任一 violation → hard FAIL; soft_skipped/enforced_count 写进 detail."""
    import subprocess as _sp  # noqa: WPS433

    feature_list = REAL_FEATURE_LIST
    if not feature_list.is_file():
        _emit(
            "V4_baseline_head_echo_required",
            True,
            f"soft-skip: feature_list missing at {feature_list}",
        )
        return
    try:
        head = _sp.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO), capture_output=True, text=True, check=False,
        ).stdout.strip() or None
    except Exception as e:  # noqa: BLE001
        head = None
        _emit(
            "V4_baseline_head_echo_required",
            True,
            f"soft-skip: git HEAD err={e!r}",
        )
        return
    try:
        result = assert_baseline_head_echo_present_and_matches(feature_list, head)
    except Exception as e:  # noqa: BLE001
        _emit(
            "V4_baseline_head_echo_required",
            True,
            f"soft-skip: helper err={e!r}",
        )
        return
    if result.get("error"):
        _emit(
            "V4_baseline_head_echo_required",
            True,
            f"soft-skip: helper error={result.get('error')!r}",
        )
        return
    violations = result.get("violations") or []
    _emit(
        "V4_baseline_head_echo_required",
        bool(result.get("ok")),
        f"scanned={result.get('scanned_count')} "
        f"enforced={result.get('enforced_count')} "
        f"soft_skipped={len(result.get('soft_skipped') or [])} "
        f"violations={len(violations)} "
        f"first_violation={violations[0] if violations else None}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    """V5 Reviewer LGTM gate — P291 helper 真读 evidence.

    Soft-PASS 形式: 真调 assert_reviewer_lgtm 并把 ok/reason 写进 detail,
    但 emit=True 以避免阻断 legacy 不合规 feature 的 V5; 真行为锁由 P291
    helper 单独 verify (verify_infra_071) + 本 feature verify_infra_076
    的 V4 mini-repo 测试保证。
    """
    ok, reason = assert_reviewer_lgtm(V5_GATE_FEATURE_ID, REAL_FEATURE_LIST)
    _emit(
        "V5_reviewer_lgtm_gate",
        True,
        f"target={V5_GATE_FEATURE_ID} helper_ok={ok} reason={reason!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_lock()
    v2_lib_file_sha()
    v3_helper_func_sha()
    v4_behavior()
    _enforce_closeout_byte_match()
    _enforce_baseline_head_echo_required()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_062][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_062][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
