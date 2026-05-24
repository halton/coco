#!/usr/bin/env python3
"""verify_infra_100 V0-V5: 锁定 AGENTS.md / CLAUDE.md 中 shell verify rc 读取硬规则.

infra-P299-shell-verify-rc-usage-doc (phase-49 #5.49):
P294-Rx round-1 Engineer 把真 FAIL 误判为 PASS, 根因是 shell 调用形态
``python verify_xxx.py | tail; echo $?`` 让 ``tail`` 的 rc 覆盖了真 rc.

本 verify (100) 把"shell verify rc 读取硬规则"锁成机器检查:

- AGENTS.md 含 ``INFRA_P299_SHELL_VERIFY_RC_DOC_SENTINEL`` 唯一 sentinel
  (assert_unique_needle 保证锁点不漂移);
- AGENTS.md 含三种正确形态 needle: ``rc=$?`` (不 pipe), ``set -o pipefail``
  (pipe 前置), 文件重定向 (``> /tmp/v.log 2>&1``);
- AGENTS.md 含 anti-pattern needle ``| tail; echo $?`` 作为显式禁用范例;
- CLAUDE.md 含交叉引用 sentinel (``Shell verify rc 读取硬规则 (P299``);
- 文件 sha 与 self main func sha 自锁, 文档静默漂移 → V1/V2/V3 报警;
- V5 reviewer_lgtm_gate 真门, V5 pending 期间预期 FAIL.

INFRA_100_SHA_LOCKS
-------------------
- ``AGENTS.md`` file sha: EXPECTED_AGENTS_MD_FILE_SHA
- ``CLAUDE.md`` file sha: EXPECTED_CLAUDE_MD_FILE_SHA

校验层级 (V0-V5, 共 14 checks):

- V0 scaffolding (×5):
  - V0_agents_md_exists
  - V0_claude_md_exists
  - V0_self_main_entry (有 __main__ guard + def main)
  - V0_doc_sentinel_in_agents (sentinel 字面值在 AGENTS.md)
  - V0_claude_cross_ref_in_claude_md (CLAUDE.md 含 "P299" 字样)
- V1 self main() func sha (1 check)
- V2 AGENTS.md 文件 sha (1 check)
- V3 CLAUDE.md 文件 sha (1 check)
- V4 文档实质 needle 校验 (5 checks):
  - V4_1 AGENTS.md sentinel 唯一 (assert_unique_needle V6 纪律)
  - V4_2 AGENTS.md 含三种正确形态 needle (rc=$?, set -o pipefail, 文件重定向)
  - V4_3 AGENTS.md 含 anti-pattern needle ``| tail; echo $?``
  - V4_4 CLAUDE.md 含 P299 交叉引用且回链 AGENTS.md
  - V4_5 自合成 mutant: 把 AGENTS.md sentinel 替换成 placeholder, 锁应失效
- V5 reviewer_lgtm_gate (真门: V5 pending 期间预期 FAIL)

退出码: 0=ALL PASS, 2=任一 FAIL (verify_summary_exit).

## Lock: EXPECTED_AGENTS_MD_FILE_SHA
- target_function: N/A
- target_file: AGENTS.md
- lock_kind: file_sha
- bump_when: AGENTS.md 文件 sha256 变化 (任何字节改动)
- bump_protocol: 重算 sha256 of AGENTS.md 并更新常量
- rationale: 锁 AGENTS.md 整体内容防 P299 shell rc 硬规则段被悄改/移除

## Lock: EXPECTED_CLAUDE_MD_FILE_SHA
- target_function: N/A
- target_file: CLAUDE.md
- lock_kind: file_sha
- bump_when: CLAUDE.md 文件 sha256 变化 (任何字节改动)
- bump_protocol: 重算 sha256 of CLAUDE.md 并更新常量
- rationale: 锁 CLAUDE.md 整体内容防 P299 交叉引用段被悄改/移除
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
AGENTS_MD = REPO / "AGENTS.md"
CLAUDE_MD = REPO / "CLAUDE.md"
REAL_FEATURE_LIST = REPO / "feature_list.json"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_unique_needle,
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_SELF_MAIN_FUNC_SHA = (
    "8c2d2684657a54daa792efcd09b1e0850b79ffbc5fc32bd444bb570dbb050ba2"
)
EXPECTED_AGENTS_MD_FILE_SHA = (
    "4334067ba33761e1fe1d733ccb576146b9d0d3b8460a664c427e5849cc12e042"
)
EXPECTED_CLAUDE_MD_FILE_SHA = (
    "ef8341404bebd438980afb2baa08171d3e4cf8ac22c38d07f4af6bdcd86d1b01"
)

DOC_SENTINEL = "INFRA_P299_SHELL_VERIFY_RC_DOC_SENTINEL"
V5_GATE_FEATURE_ID = "infra-P299-shell-verify-rc-usage-doc"

# 三种正确形态 needle (与 AGENTS.md 文案保持一致)
GOOD_FORM_RC_DIRECT = "rc=$?"
GOOD_FORM_PIPEFAIL = "set -o pipefail"
GOOD_FORM_REDIRECT = "> /tmp/v.log 2>&1"

# 显式禁止的 anti-pattern needle
ANTI_PATTERN = "| tail; echo $?"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_100][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, bool(ok), detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def v0_scaffolding() -> None:
    _emit("V0_agents_md_exists", AGENTS_MD.is_file(), f"path={AGENTS_MD}")
    _emit("V0_claude_md_exists", CLAUDE_MD.is_file(), f"path={CLAUDE_MD}")
    self_src = Path(__file__).read_text(encoding="utf-8")
    _emit(
        "V0_self_main_entry",
        'if __name__ == "__main__":' in self_src and "def main(" in self_src,
        "expect __main__ guard + def main()",
    )
    if AGENTS_MD.is_file():
        agents_src = AGENTS_MD.read_text(encoding="utf-8")
        _emit(
            "V0_doc_sentinel_in_agents",
            DOC_SENTINEL in agents_src,
            f"sentinel={DOC_SENTINEL!r}",
        )
    else:
        _emit("V0_doc_sentinel_in_agents", False, "AGENTS.md missing")
    if CLAUDE_MD.is_file():
        claude_src = CLAUDE_MD.read_text(encoding="utf-8")
        _emit(
            "V0_claude_cross_ref_in_claude_md",
            "P299" in claude_src,
            "expect 'P299' marker in CLAUDE.md",
        )
    else:
        _emit("V0_claude_cross_ref_in_claude_md", False, "CLAUDE.md missing")


def v1_self_func_sha() -> None:
    self_path = Path(__file__)
    try:
        got = func_sha_by_name(self_path, "main")
    except Exception as e:
        _emit("V1_self_main_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_self_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
        return
    _emit(
        "V1_self_main_func_sha",
        got == EXPECTED_SELF_MAIN_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
    )


def v2_agents_md_file_sha() -> None:
    if not AGENTS_MD.is_file():
        _emit("V2_agents_md_file_sha", False, "AGENTS.md missing")
        return
    got = _file_sha(AGENTS_MD)
    if EXPECTED_AGENTS_MD_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V2_agents_md_file_sha",
            False,
            f"placeholder; bump EXPECTED_AGENTS_MD_FILE_SHA={got}",
        )
        return
    _emit(
        "V2_agents_md_file_sha",
        got == EXPECTED_AGENTS_MD_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_AGENTS_MD_FILE_SHA[:16]}",
    )


def v3_claude_md_file_sha() -> None:
    if not CLAUDE_MD.is_file():
        _emit("V3_claude_md_file_sha", False, "CLAUDE.md missing")
        return
    got = _file_sha(CLAUDE_MD)
    if EXPECTED_CLAUDE_MD_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V3_claude_md_file_sha",
            False,
            f"placeholder; bump EXPECTED_CLAUDE_MD_FILE_SHA={got}",
        )
        return
    _emit(
        "V3_claude_md_file_sha",
        got == EXPECTED_CLAUDE_MD_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_CLAUDE_MD_FILE_SHA[:16]}",
    )


def v4_doc_needles() -> None:
    if not AGENTS_MD.is_file() or not CLAUDE_MD.is_file():
        _emit("V4_setup", False, "AGENTS.md or CLAUDE.md missing")
        return
    agents_src = AGENTS_MD.read_text(encoding="utf-8")
    claude_src = CLAUDE_MD.read_text(encoding="utf-8")

    # V4_1: AGENTS.md sentinel 唯一 (V6 unique-needle 纪律)
    try:
        assert_unique_needle(agents_src, DOC_SENTINEL)
        v4_1_ok = True
        v4_1_detail = f"sentinel unique in AGENTS.md count={agents_src.count(DOC_SENTINEL)}"
    except ValueError as e:
        v4_1_ok = False
        v4_1_detail = f"unique-needle violation: {e!r}"
    except Exception as e:
        v4_1_ok = False
        v4_1_detail = f"unexpected exc: {e!r}"
    _emit("V4_1_agents_md_sentinel_unique", v4_1_ok, v4_1_detail)

    # V4_2: 三种正确形态 needle 都在 AGENTS.md
    missing_forms = [
        name for name, needle in (
            ("rc_direct", GOOD_FORM_RC_DIRECT),
            ("pipefail", GOOD_FORM_PIPEFAIL),
            ("redirect", GOOD_FORM_REDIRECT),
        ) if needle not in agents_src
    ]
    _emit(
        "V4_2_agents_md_has_three_good_forms",
        not missing_forms,
        f"missing={missing_forms}",
    )

    # V4_3: anti-pattern needle 在 AGENTS.md (作为禁用范例)
    _emit(
        "V4_3_agents_md_has_anti_pattern_listed",
        ANTI_PATTERN in agents_src,
        f"anti_pattern={ANTI_PATTERN!r} present_for_demo",
    )

    # V4_4: CLAUDE.md 含 P299 引用且回链 AGENTS.md
    has_p299 = "P299" in claude_src
    has_link = "AGENTS.md" in claude_src
    _emit(
        "V4_4_claude_md_cross_ref",
        has_p299 and has_link,
        f"has_P299={has_p299} has_AGENTS_link={has_link}",
    )

    # V4_5 mutant: 把 sentinel 替换成 placeholder, helper 在该合成文本上应找不到 sentinel
    mutated = agents_src.replace(DOC_SENTINEL, "PLACEHOLDER_MUTANT_SENTINEL")
    try:
        assert_unique_needle(mutated, DOC_SENTINEL)
        v4_5_ok = False
        v4_5_detail = "expected ValueError on mutated text but none raised"
    except ValueError as e:
        msg = str(e)
        v4_5_ok = "count=0" in msg
        v4_5_detail = f"mutant rejected; msg={msg[:80]!r}"
    except Exception as e:
        v4_5_ok = False
        v4_5_detail = f"wrong exc: {type(e).__name__} {e!r}"
    _emit("V4_5_mutant_sentinel_removed_rejected", v4_5_ok, v4_5_detail)


def v5_reviewer_gate() -> None:
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
    v2_agents_md_file_sha()
    v3_claude_md_file_sha()
    v4_doc_needles()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    failed_tags = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_100][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_100][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
