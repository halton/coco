#!/usr/bin/env python3
"""verify_infra_P322_pr_template_decision_matrix_lock: PR 模板决策矩阵
checkbox 流程锁.

infra-P322-pr-template-decision-matrix-lock (phase-67 #26):
来源: interact-038-backlog-pr-template-decision-matrix-checkbox.
``.github/PULL_REQUEST_TEMPLATE/verify-script.md`` 已在 infra-040 落地 PR 模板
决策矩阵 checkbox (锁类型选择 + 决策依据 + 反向引用扫描 + 验证证据 4 段). 但
之前**没有机械化锁**该模板的 sentinel checkbox 字符串和 file sha — 模板内容
被静默改写或 checkbox 段被误删, 没有任何自动检测; 本 verify 给该模板加流程
层硬锁, 防漂.

校验层级 (V0-V_last):

- V0 scaffolding: self_sha (V8-SELF-SHA-SKIP) + 模板文件存在 + 常量 hex64
- V1 sentinel_checkboxes_present: 模板内容必须含 6 个 sentinel 字符串
  (决策矩阵段标题 + 3 个锁类型 checkbox + 反向引用扫描 checkbox + Reviewer
  fresh-context LGTM checkbox)
- V2 mutant_negative: in-memory 移除任一 sentinel 字符串, V1 应 FAIL
- V3 pr_template_file_sha: 模板文件 file sha 整文件锁
- V_last reviewer_lgtm_gate: closeout 须 fresh-context Reviewer LGTM

退出码: 0=ALL PASS, 2=任一 FAIL (走 ``verify_summary_exit``).

## Lock
- type: sentinel_string + file_sha + mutant + reviewer_lgtm
- target: .github/PULL_REQUEST_TEMPLATE/verify-script.md
- locked_strings:
  - "决策矩阵 (必须勾选"
  - "**file-level sha**"
  - "**func-level sha**"
  - "**混合**"
  - "反向引用扫描 (cascade 影响)"
  - "Reviewer fresh-context LGTM (sub-agent"
- bump_when: .github/PULL_REQUEST_TEMPLATE/verify-script.md 改任意行 (file
  sha 漂)
- bump_protocol: 改完 PR 模板后, 手动 recompute sha256 并更新本脚本
  ``EXPECTED_PR_TEMPLATE_FILE_SHA``. 本脚本自身 sha 走 V8-SELF-SHA-SKIP 单行
  pragma (用 ``scripts/bump_self_file_sha.py --target
  scripts/verify_infra_P322_pr_template_decision_matrix_lock.py`` bump).

运行环境约定 (infra-034): 必须在 .venv 下运行.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
PR_TEMPLATE = REPO / ".github" / "PULL_REQUEST_TEMPLATE" / "verify-script.md"
FEATURE_LIST = REPO / "feature_list.json"
V_LAST_GATE_FEATURE_ID = "infra-P322-pr-template-decision-matrix-lock"

sys.path.insert(0, str(REPO / "scripts"))
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    verify_summary_exit,
)

# 仅 EXPECTED_SELF_FILE_SHA 真常量行打 pragma; 其余字节(含 docstring / 注释 /
# import / 函数体内字符串字面)若有 "EXPECTED_SELF_FILE_SHA = " 子串都会破规约.
EXPECTED_SELF_FILE_SHA = "e9900980f664cdd63465fbe1ea8a275f578a85f114d7dbdfe266f1eaa4f2ecdf"  # V8-SELF-SHA-SKIP

# V3: PR 模板 file sha 锁
EXPECTED_PR_TEMPLATE_FILE_SHA = (
    "8d0cb524ff580ce3fba6a9209fadd0095129186c4799341c30e23a22f64e6ebd"
)

# V1/V2: sentinel 字符串集合 (必须全部出现在模板内容中)
SENTINEL_STRINGS: Tuple[str, ...] = (
    "决策矩阵 (必须勾选",
    "**file-level sha**",
    "**func-level sha**",
    "**混合**",
    "反向引用扫描 (cascade 影响)",
    "Reviewer fresh-context LGTM (sub-agent",
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P322][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _self_sha_skip_sentinel() -> str:
    """计算本 verify 自身 sha (剔除唯一一条 # V8-SELF-SHA-SKIP pragma 行后)."""
    self_path = Path(__file__)
    lines = self_path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [
        ln for ln in lines
        if not ln.rstrip("\r\n").endswith("# V8-SELF-SHA-SKIP")
    ]
    body = "".join(kept).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_pr_template_exists", PR_TEMPLATE.is_file(), f"path={PR_TEMPLATE}")
    _emit(
        "V0_const_pr_template_sha_hex64",
        _is_hex64(EXPECTED_PR_TEMPLATE_FILE_SHA),
        f"val={EXPECTED_PR_TEMPLATE_FILE_SHA[:16]}",
    )
    _emit(
        "V0_sentinel_count_positive",
        len(SENTINEL_STRINGS) >= 6,
        f"count={len(SENTINEL_STRINGS)}",
    )
    # V0 self-sha lock (V8-SELF-SHA-SKIP)
    got = _self_sha_skip_sentinel()
    _emit(
        "V0_self_sha",
        got == EXPECTED_SELF_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_SELF_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V1: sentinel checkbox/字符串全部存在
# ---------------------------------------------------------------------------
def _load_template() -> str:
    try:
        return PR_TEMPLATE.read_text(encoding="utf-8")
    except Exception:
        return ""


def v1_sentinel_checkboxes_present() -> None:
    text = _load_template()
    _emit("V1_template_loaded", bool(text), f"len={len(text)}")
    missing = [s for s in SENTINEL_STRINGS if s not in text]
    _emit(
        "V1_sentinel_checkboxes_all_present",
        not missing,
        f"required_count={len(SENTINEL_STRINGS)} missing={missing}",
    )


# ---------------------------------------------------------------------------
# V2: mutant 反证 (in-memory 移除单 sentinel 后 V1 应 FAIL)
# ---------------------------------------------------------------------------
def v2_mutant_negative() -> None:
    text = _load_template()
    if not text:
        _emit("V2_mutant_negative", False, "real template empty, cannot mutate")
        return
    all_fail_when_removed = True
    detail_parts = []
    for s in SENTINEL_STRINGS:
        if s not in text:
            all_fail_when_removed = False
            detail_parts.append(f"{s!r}:not_in_real_template")
            continue
        mutated = text.replace(s, "")  # 仅在内存里移除
        still_missing = [x for x in SENTINEL_STRINGS if x not in mutated]
        ok = s in still_missing
        all_fail_when_removed = all_fail_when_removed and ok
        detail_parts.append(f"{s!r}:{'OK_missing' if ok else 'STILL_PRESENT'}")
    _emit(
        "V2_mutant_negative_each_sentinel_drop_detected",
        all_fail_when_removed,
        " | ".join(detail_parts),
    )


# ---------------------------------------------------------------------------
# V3: file sha 锁
# ---------------------------------------------------------------------------
def v3_pr_template_file_sha() -> None:
    got = _file_sha(PR_TEMPLATE)
    _emit(
        "V3_pr_template_file_sha",
        got == EXPECTED_PR_TEMPLATE_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_PR_TEMPLATE_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V_last: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v_last_reviewer_lgtm_gate() -> None:
    try:
        ok, reason = assert_reviewer_lgtm(
            V_LAST_GATE_FEATURE_ID,
            FEATURE_LIST,
        )
    except Exception as e:
        _emit("V_last_reviewer_lgtm_gate", False, f"helper err: {e!r}")
        return
    _emit(
        "V_last_reviewer_lgtm_gate",
        ok,
        f"target={V_LAST_GATE_FEATURE_ID} reason={reason!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_sentinel_checkboxes_present()
    v2_mutant_negative()
    v3_pr_template_file_sha()
    v_last_reviewer_lgtm_gate()
    failed = sum(1 for _, ok, _ in _results if not ok)
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
