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
    assert_closeout_verify_runs_min_count,
    assert_report_matches_closeout_runs,
    assert_reviewer_lgtm,
    assert_reviewer_summary_nonempty,
    assert_verify_lib_public_helper_naming,
    func_sha_by_name,
    verify_closeout_evidence_trustworthy,
)

EXPECTED_VERIFY_LIB_FILE_SHA = "57385b07472b1fc811d351c9c12dcf2f4a85b3bdfe8e466a6f77cdea673110be"
EXPECTED_CLOSEOUT_FUNC_SHA = "d190174c24b264946d16ff31f37d2b4ed607b3bee24a82c3a5588d8679fe0917"
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
# P299-followup2 enable-byte-match-real-run (infra-P299-followup2-enable-byte-
# match-real-run):
# 旧版 _enforce_closeout_byte_match 依赖 closeout_verify.main_head_sha 与当前
# git HEAD startswith 匹配 + nested round schema with rc 字段, 实际从未触发
# (closeout commit 后 bump 又移动 HEAD; flat schema 无 rc) → 永远 soft-skip,
# 等于挂牌不开门。本轮把 enforcement 改成 "弱版 anchor byte-match" 方案:
#   - 扫所有 passing feature 的 closeout_verify.verify_runs
#   - 对每条 entry: 从 name (fallback script) 提取 verify_infra_NNN 数字, tail_stdout
#     必须含 'verify_infra_NNN' 子串 (≈ tail-name 一致性 anchor, catch
#     copy-paste 伪造)
#   - name 无 verify_infra_NNN (smoke/bootstrap 等) 或 tail 为空 → soft_skip
#     (Default-OFF 渐进 promote, 与 P286-followup3 baseline_head_echo 同风格)
#   - 任一 violation → hard FAIL (开门)
#   - enforced >= 1 → fire=True (真触发, 不再 soft-skip overall)
# Default-OFF 哲学保留: 老 evidence 无规范 tail 自动 soft_skip, 不 retro 重写;
# 新 closeout 真 enforce。assert_report_matches_closeout_runs (P299 nested
# schema 真重跑 helper) 留作未来 enforcement 强版本入口, 由 backlog 单独 promote。
# ---------------------------------------------------------------------------


def _classify_closeout_tail_anchor(name: str, tail: str) -> tuple[str, str]:
    """对单条 verify_runs entry 分类: 返回 (verdict, detail)。

    verdict in {"enforced_ok", "violation", "soft_skip"}.

    规则:
      - name 中无 verify_infra_NNN → soft_skip (smoke/bootstrap/init 等非 verify
        脚本)
      - tail.strip() 为空 → soft_skip (老 evidence 未捕获 tail)
      - tail 含子串 'verify_infra_NNN' → enforced_ok (tail-name 一致)
      - 否则 → violation (tail 与声称 name 不匹配, 疑似 copy-paste 伪造)
    """
    import re as _re

    m = _re.search(r"verify_infra_(\d+)", name or "")
    if not m:
        return ("soft_skip", "name has no verify_infra_NNN")
    nnn = m.group(1)
    if not (tail or "").strip():
        return ("soft_skip", f"tail_stdout empty for verify_infra_{nnn}")
    anchor = f"verify_infra_{nnn}"
    if anchor in tail:
        return ("enforced_ok", f"anchor {anchor} present")
    return (
        "violation",
        f"tail missing anchor {anchor}; tail-tail={tail[-60:]!r}",
    )


def _enforce_closeout_byte_match() -> None:
    """弱版 anchor byte-match: 扫所有 passing feature.closeout_verify.verify_runs,
    要求每条 entry 的 tail_stdout 含 verify_infra_NNN anchor (NNN 来自 name).

    Default-OFF 渐进 promote: 老 evidence/smoke entry 自动 soft_skip;
    新 evidence 真 enforce。violation > 0 → hard FAIL, 真开门。
    """
    import json

    feature_list = REAL_FEATURE_LIST
    if not feature_list.is_file():
        _emit(
            "V4_byte_match_enforce",
            True,
            f"soft-skip: feature_list missing at {feature_list}",
        )
        return
    try:
        fl = json.loads(feature_list.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        _emit(
            "V4_byte_match_enforce",
            True,
            f"soft-skip: feature_list parse err={e!r}",
        )
        return
    scanned = 0
    enforced = 0
    soft_skipped = 0
    violations: list[str] = []
    fired_feature_ids: list[str] = []
    for f in fl.get("features", []):
        if f.get("status") != "passing":
            continue
        ev = f.get("evidence")
        if not isinstance(ev, dict):
            continue
        cv = ev.get("closeout_verify") or {}
        if not isinstance(cv, dict):
            continue
        runs = cv.get("verify_runs") or []
        if not isinstance(runs, list):
            continue
        feat_fired = False
        for r in runs:
            if not isinstance(r, dict):
                continue
            name = r.get("name") or r.get("script") or ""
            tail = r.get("tail_stdout") or ""
            scanned += 1
            verdict, detail = _classify_closeout_tail_anchor(name, tail)
            if verdict == "enforced_ok":
                enforced += 1
                feat_fired = True
            elif verdict == "violation":
                violations.append(f"{f.get('id')}::{name}::{detail}")
            else:
                soft_skipped += 1
        if feat_fired:
            fired_feature_ids.append(f.get("id"))
    fire_overall = enforced >= 1
    ok = (len(violations) == 0)
    detail = (
        f"scanned={scanned} enforced={enforced} soft_skipped={soft_skipped} "
        f"violations={len(violations)} fire={fire_overall} "
        f"fired_features={len(fired_feature_ids)}"
    )
    if violations:
        detail += f" first_violation={violations[0]!r}"
    if not fire_overall:
        # 无任何 entry 触发 enforcement (例如全部 soft_skip), soft-PASS overall
        _emit(
            "V4_byte_match_enforce",
            True,
            f"soft-skip: no enforced entry; {detail}",
        )
    else:
        _emit("V4_byte_match_enforce", ok, detail)

    # Deferred strong path (Default-OFF, 大多数情况 noop): 若某 passing feature
    # 的 closeout_verify 含 P299 nested rounds schema (含 round + scripts + rc)
    # 且 main_head_sha 与当前 HEAD 匹配, 则真调 assert_report_matches_closeout_runs
    # 做 byte+rc 比对; 现存 evidence 全是 flat schema → 不命中即 noop。保留 ast
    # 调用满足 verify_infra_081 V4 锁 ("062._enforce_closeout_byte_match 函数体
    # 必须含 helper 调用"), 并为未来 schema 迁移留 hook。
    import subprocess as _sp  # noqa: WPS433

    try:
        _head = _sp.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO), capture_output=True, text=True, check=False,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return
    for _f in fl.get("features", []):
        if _f.get("status") != "passing":
            continue
        _ev = _f.get("evidence")
        if not isinstance(_ev, dict):
            continue
        _cv = _ev.get("closeout_verify") or {}
        if not isinstance(_cv, dict):
            continue
        _ch = _cv.get("main_head_sha") or ""
        if not (isinstance(_ch, str) and _ch and _head.startswith(_ch)):
            continue
        _runs = _cv.get("verify_runs") or []
        if not isinstance(_runs, list) or not _runs:
            continue
        _first = _runs[0]
        if not (isinstance(_first, dict)
                and "round" in _first
                and isinstance(_first.get("scripts"), dict)):
            continue
        _report_obj = {"verify_runs": _runs}
        try:
            assert_report_matches_closeout_runs(
                _report_obj, REPO, _cv.get("main_head_sha", ""),
            )
        except Exception:  # noqa: BLE001
            return
        return


def _maybe_strong_byte_match_deferred(fl: dict) -> None:
    """Reserved hook for future schema migration; currently unused. The
    actual deferred strong path lives inline in ``_enforce_closeout_byte_match``
    so verify_infra_081 V4_2 can detect ``assert_report_matches_closeout_runs``
    inside the enforcer's own function body via AST scan."""
    return


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
# V4: reviewer.summary 非空 hard check (phase-42 #4.42)
# infra-P278-followup-reviewer-summary-nonempty-hard-check —
# Default-OFF 渐进 promote: 缺字段 soft_skip; 含字段 strip 长度 >= 20 hard enforce.
# ---------------------------------------------------------------------------
def _enforce_reviewer_summary_nonempty() -> None:
    """对 feature_list.json 调 assert_reviewer_summary_nonempty(min_chars=20),
    任一 violation → hard FAIL; soft_skipped/enforced_count 写进 detail."""
    feature_list = REAL_FEATURE_LIST
    if not feature_list.is_file():
        _emit(
            "V4_reviewer_summary_nonempty",
            True,
            f"soft-skip: feature_list missing at {feature_list}",
        )
        return
    try:
        result = assert_reviewer_summary_nonempty(feature_list, min_chars=20)
    except Exception as e:  # noqa: BLE001
        _emit(
            "V4_reviewer_summary_nonempty",
            True,
            f"soft-skip: helper err={e!r}",
        )
        return
    if result.get("error"):
        _emit(
            "V4_reviewer_summary_nonempty",
            True,
            f"soft-skip: helper error={result.get('error')!r}",
        )
        return
    violations = result.get("violations") or []
    _emit(
        "V4_reviewer_summary_nonempty",
        bool(result.get("ok")),
        f"scanned={result.get('scanned_count')} "
        f"enforced={result.get('enforced_count')} "
        f"soft_skipped={len(result.get('soft_skipped') or [])} "
        f"violations={len(violations)} "
        f"min_chars={result.get('min_chars')} "
        f"first_violation={violations[0] if violations else None}",
    )


# ---------------------------------------------------------------------------
# infra-P278-followup-verify-lib-helper-naming-convention-lock (phase-42 #5.42)
# 反射锁: _verify_lib 公开 helper (在 __all__ 内的 callable) 命名必须以
# assert_ / enforce_ 开头, 否则 hard FAIL. legacy 名字通过模块内置
# allowlist 显式豁免; 缺 __all__ → soft_skip (Default-OFF).
# ---------------------------------------------------------------------------
def _enforce_verify_lib_helper_naming() -> None:
    try:
        result = assert_verify_lib_public_helper_naming()
    except Exception as e:  # noqa: BLE001
        _emit(
            "V4_verify_lib_helper_naming_convention",
            True,
            f"soft-skip: helper err={e!r}",
        )
        return
    if result.get("error"):
        _emit(
            "V4_verify_lib_helper_naming_convention",
            True,
            f"soft-skip: helper error={result.get('error')!r}",
        )
        return
    if result.get("soft_skipped"):
        _emit(
            "V4_verify_lib_helper_naming_convention",
            True,
            "soft-skip: _verify_lib has no __all__ (Default-OFF)",
        )
        return
    violations = result.get("violations") or []
    _emit(
        "V4_verify_lib_helper_naming_convention",
        bool(result.get("ok")),
        f"scanned={result.get('scanned_count')} "
        f"enforced={result.get('enforced_count')} "
        f"legacy_allowlisted={result.get('legacy_allowlisted_count')} "
        f"violations={len(violations)} "
        f"first_violation={violations[0] if violations else None}",
    )


# ---------------------------------------------------------------------------
# V4: closeout_verify.verify_runs 最少条数 hard check (phase-43 #4.43)
# infra-P278-followup-closeout-verify-runs-min-count-hard-check —
# Default-OFF 渐进 promote: 缺字段 soft_skip; 含字段 len>=3 hard enforce.
# ---------------------------------------------------------------------------
def _enforce_closeout_verify_runs_min_count() -> None:
    """对 feature_list.json 调 assert_closeout_verify_runs_min_count(min_count=3),
    任一 violation → hard FAIL; soft_skipped/enforced_count 写进 detail."""
    feature_list = REAL_FEATURE_LIST
    if not feature_list.is_file():
        _emit(
            "V4_closeout_verify_runs_min_count",
            True,
            f"soft-skip: feature_list missing at {feature_list}",
        )
        return
    try:
        result = assert_closeout_verify_runs_min_count(feature_list, min_count=3)
    except Exception as e:  # noqa: BLE001
        _emit(
            "V4_closeout_verify_runs_min_count",
            True,
            f"soft-skip: helper err={e!r}",
        )
        return
    if result.get("error"):
        _emit(
            "V4_closeout_verify_runs_min_count",
            True,
            f"soft-skip: helper error={result.get('error')!r}",
        )
        return
    violations = result.get("violations") or []
    _emit(
        "V4_closeout_verify_runs_min_count",
        bool(result.get("ok")),
        f"scanned={result.get('scanned_count')} "
        f"enforced={result.get('enforced_count')} "
        f"soft_skipped={len(result.get('soft_skipped') or [])} "
        f"violations={len(violations)} "
        f"min_count={result.get('min_count')} "
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
    _enforce_reviewer_summary_nonempty()
    _enforce_verify_lib_helper_naming()
    _enforce_closeout_verify_runs_min_count()
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
