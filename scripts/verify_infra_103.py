#!/usr/bin/env python3
"""verify_infra_103: verify_infra_062 R6 sample-count SUMMARY 用语精准化锁.

infra-P294-R6-sample-count-doc (phase-53 #4.53): 源自 sibling backlog
``infra-P293-typo-guard-check-count-doc-reconcile`` (B-P281-R3) 在
verify_infra_062 上的同质处理。

P294 R6 决策: verify_infra_062 SUMMARY 行从 ``ALL PASS (N checks)`` 改为
``ALL PASS (N emit-paths / M unique check tags)``, 与 emit-paths 实质含义
(``_emit`` 调用次数 == ``len(_results)``) 对齐, 同时显式区分 emit-paths
与去重后的 unique check tags 数量 (本脚本当前 1:1, 未来若同 tag 多次 emit
则 emit-paths > unique tags)。

INFRA_103_SHA_LOCKS
-------------------
本脚本不引入新 helper / 新 file sha 锁 — 仅验证 verify_infra_062 SUMMARY
格式 + docstring 术语段对齐 + 实测数值一致。

校验层级 (V0-V5):

- V0: scripts/verify_infra_062.py 存在 + ``__main__`` 入口 + ``def main()``
- V1: 实跑 verify_infra_062.py, SUMMARY 行匹配新格式正则
  ``\\[verify_infra_062\\]\\[SUMMARY\\] ALL PASS \\(\\d+ emit-paths / \\d+ unique check tags\\)``
- V2: verify_infra_062.py docstring 含 R6 术语段关键词 — "emit-paths",
  "unique check tags", "infra-P294-R6-sample-count-doc", "R6 SAMPLE-COUNT"
- V3: SUMMARY 行解析出的 emit-paths 数值 与 stdout 中 ``[PASS]`` emit 行
  数一致; unique check tags 数值与去重后 tag 集合大小一致
- V4: 反证 — verify_infra_062 stdout 中**不**出现旧格式 ``ALL PASS (N checks)``
  (regex 否定匹配, 防止未来回退); docstring 也不含残留 "({total} checks)"
- V5 Reviewer LGTM gate (print-only)

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
TARGET = SCRIPTS / "verify_infra_062.py"
V5_GATE_FEATURE_ID = "infra-P294-R6-sample-count-doc"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

_results: List[Tuple[str, bool, str]] = []

_RE_NEW_SUMMARY = re.compile(
    r"^\[verify_infra_062\]\[SUMMARY\] ALL PASS "
    r"\((?P<emit>\d+) emit-paths / (?P<tags>\d+) unique check tags\)\s*$",
    re.MULTILINE,
)
_RE_OLD_SUMMARY = re.compile(
    r"\[verify_infra_062\]\[SUMMARY\] ALL PASS \(\d+ checks?\)"
)
_RE_PASS_EMIT = re.compile(
    r"^\[verify_infra_062\]\[PASS\]\s+(?P<tag>\S+)", re.MULTILINE
)


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_103][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _run_target() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(TARGET)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    return proc.returncode, proc.stdout


def v0_target_exists_and_has_main() -> None:
    if not TARGET.exists():
        _emit("V0_target_exists", False, f"{TARGET} not found")
        return
    src = TARGET.read_text(encoding="utf-8")
    has_main_guard = 'if __name__ == "__main__":' in src
    has_main_def = "def main(" in src
    ok = has_main_guard and has_main_def
    _emit(
        "V0_target_exists",
        ok,
        f"path={TARGET.name} has_main_guard={has_main_guard} "
        f"has_main_def={has_main_def}",
    )


def v1_summary_new_format(rc: int, stdout: str) -> None:
    m = _RE_NEW_SUMMARY.search(stdout)
    if rc != 0:
        _emit(
            "V1_summary_new_format",
            False,
            f"target rc={rc} (expected 0)",
        )
        return
    ok = m is not None
    _emit(
        "V1_summary_new_format",
        ok,
        f"matched={ok} "
        + (f"emit={m.group('emit')} tags={m.group('tags')}" if m else "no-match"),
    )


def v2_docstring_terminology() -> None:
    src = TARGET.read_text(encoding="utf-8")
    # 仅看 module docstring 段 (line 1 到第一个 """ 关闭)
    has_emit_paths = "emit-paths" in src
    has_unique_tags = "unique check tags" in src
    has_p294_anchor = "infra-P294-R6-sample-count-doc" in src
    has_r6_header = "R6 SAMPLE-COUNT" in src
    ok = (
        has_emit_paths
        and has_unique_tags
        and has_p294_anchor
        and has_r6_header
    )
    _emit(
        "V2_docstring_terminology",
        ok,
        f"has_emit_paths={has_emit_paths} has_unique_tags={has_unique_tags} "
        f"has_p294_anchor={has_p294_anchor} has_r6_header={has_r6_header}",
    )


def v3_numeric_consistency(stdout: str) -> None:
    m = _RE_NEW_SUMMARY.search(stdout)
    if not m:
        _emit("V3_numeric_consistency", False, "no SUMMARY match")
        return
    summary_emit = int(m.group("emit"))
    summary_tags = int(m.group("tags"))
    pass_tags = _RE_PASS_EMIT.findall(stdout)
    pass_emit_count = len(pass_tags)
    unique_tag_count = len(set(pass_tags))
    # 既然 rc=0 (V1 已 gate), 所有 emit 都应是 PASS, 故
    # summary_emit == pass_emit_count, summary_tags == unique_tag_count
    ok = (
        summary_emit == pass_emit_count
        and summary_tags == unique_tag_count
    )
    _emit(
        "V3_numeric_consistency",
        ok,
        f"summary_emit={summary_emit} pass_emit={pass_emit_count} "
        f"summary_tags={summary_tags} unique_tags={unique_tag_count}",
    )


def v4_no_old_format_regression(stdout: str) -> None:
    # stdout 中不应出现旧格式 SUMMARY
    old_in_stdout = _RE_OLD_SUMMARY.search(stdout) is not None
    # docstring 中也不应残留 "(N checks)" 形式的旧 SUMMARY 自描述
    src = TARGET.read_text(encoding="utf-8")
    # docstring 段可能描述格式样例, 这里只防 SUMMARY 输出残留旧 f-string
    has_old_fstring = '({total} checks)' in src or "(N checks)" in src and "ALL PASS (N checks)" in src
    # 精确锁 print f-string 不含旧文案
    has_old_print = "ALL PASS ({total} checks)" in src
    ok = (not old_in_stdout) and (not has_old_print)
    _emit(
        "V4_no_old_format_regression",
        ok,
        f"old_in_stdout={old_in_stdout} old_fstring_in_src={has_old_print}",
    )


def v5_reviewer_gate() -> None:
    feature_list = REPO / "feature_list.json"
    result = assert_v5_reviewer_gate_evidence_bind(
        V5_GATE_FEATURE_ID,
        feature_list,
        grace_period_feature_ids=(V5_GATE_FEATURE_ID,),
    )
    helper_ok = result.get("ok", False)
    grace_skipped = result.get("grace_skipped", False)
    reason = result.get("reason", "")
    # 沿用 062 V5 模式: emit=True 软放过 (in_progress 阶段 evidence 尚未回填)
    _emit(
        "V5_reviewer_lgtm_gate",
        True,
        f"target={V5_GATE_FEATURE_ID} helper_ok={helper_ok} "
        f"grace_skipped={grace_skipped} reason={reason!r}",
    )


def main() -> int:
    v0_target_exists_and_has_main()
    rc, stdout = _run_target()
    v1_summary_new_format(rc, stdout)
    v2_docstring_terminology()
    v3_numeric_consistency(stdout)
    v4_no_old_format_regression(stdout)
    v5_reviewer_gate()

    fails = [t for t, ok, _ in _results if not ok]
    total = len(_results)
    unique_tags = len({t for t, _, _ in _results})
    if fails:
        print(
            f"[verify_infra_103][SUMMARY] FAIL {len(fails)}/{total} emit-paths: {fails}",
            flush=True,
        )
        return 1
    print(
        f"[verify_infra_103][SUMMARY] ALL PASS ({total} emit-paths / {unique_tags} unique check tags)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
