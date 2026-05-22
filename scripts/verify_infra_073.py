#!/usr/bin/env python3
"""verify_infra_073 V0-V5: baseline FAIL claim regex 支持跨行 claim.

infra-P299-baseline-fail-claim-regex-multiline (phase-39 #4.39):

P294-R4 Reviewer info finding: ``_verify_lib._RE_BASELINE_FAIL_CLAIM`` 旧 regex
``verify_infra_(\\d{3})\\b[^\\n]{0,80}?\\b(FAIL|fail|失败)\\b`` 只匹配同行,
若 Reviewer markdown 把 "verify_infra_XXX" 与 "FAIL" 写跨行 (折行 bullet)
会漏报 false-fail 主张 (false negative). 本 feature 把 regex 改为
``[\\s\\S]{0,200}`` (跨行 + 200 char 跨度) 支持跨行 claim,
并新增本 verify 锁定 helper 行为 + cross-check 真跑.

INFRA_073_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` 全文件 sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``verify_baseline_fail_claims`` canonical func sha: EXPECTED_HELPER_FUNC_SHA
- 本脚本 main() 自锁 func sha: EXPECTED_SELF_MAIN_FUNC_SHA (placeholder __BUMP_ME__)

校验层级 (V0-V5):

- V0 scaffolding: 路径存在 + 常量 hex64 + docstring sentinel
- V1 self main() func sha 自锁 (placeholder OK)
- V2 _verify_lib file sha
- V3 helper func sha (verify_baseline_fail_claims)
- V4 regex 真匹配:
  - V4_1 单行 claim "verify_infra_001 FAIL" → 抓
  - V4_2 跨行 claim "verify_infra_002\\n    FAIL: ..." → 抓 (新 regex 必须捕获)
  - V4_3 多 verify claim 文本 → 抓全部 verify id 去重
  - V4_4 伪阳性: "Note: FAIL is used" 无 verify_infra_NNN → 不抓
  - V4_5 整段 baseline cross-check 真跑: 合成 reviewer text + tempfile mini
    git repo + 假 baseline_ref → helper 返回正确 claims/missing/contradictions
- V5_reviewer_lgtm_gate: 用 ``assert_reviewer_lgtm`` 真校 P291 (已 passing)

退出码: 0=ALL PASS, 2=任一 FAIL (走 verify_summary_exit)。

运行环境约定 (infra-034): 必须在 .venv 下运行。
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "_verify_lib.py"
REAL_FEATURE_LIST = REPO / "feature_list.json"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    _RE_BASELINE_FAIL_CLAIM,
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_baseline_fail_claims,
    verify_summary_exit,
)

EXPECTED_VERIFY_LIB_FILE_SHA = "99933db26a1bda209f928f24961c4f1e39105ce32f9046b841fcc9782eb919b0"
EXPECTED_HELPER_FUNC_SHA = "4aa6b31c7b5e79b1ea8f17f33c323e68fa0c20932c090848d6e322db0cd552a7"
EXPECTED_SELF_MAIN_FUNC_SHA = "8b0ac9b322ffaee7b9d1331edeadfef044353d46ae58b3935d95618d99e474e5"

DOCSTRING_SENTINEL = "INFRA_073_SHA_LOCKS"

# V5 gate target: P291 已 passing
V5_GATE_FEATURE_ID = "infra-P291-reviewer-gate-real-or-remove"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_073][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _ids_from_text(text: str) -> List[str]:
    ids: List[str] = []
    seen = set()
    for m in _RE_BASELINE_FAIL_CLAIM.finditer(text):
        nnn = m.group(1)
        if nnn not in seen:
            seen.add(nnn)
            ids.append(nnn)
    return ids


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_verify_lib_exists", LIB.is_file(), f"path={LIB}")
    _emit(
        "V0_const_file_sha_hex64",
        _is_hex64(EXPECTED_VERIFY_LIB_FILE_SHA),
        f"val={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )
    _emit(
        "V0_const_helper_sha_hex64",
        _is_hex64(EXPECTED_HELPER_FUNC_SHA),
        f"val={EXPECTED_HELPER_FUNC_SHA[:16]}",
    )
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8")))
    _emit(
        "V0_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )


# ---------------------------------------------------------------------------
# V1: self main() func sha lock (placeholder __BUMP_ME__ 风格)
# ---------------------------------------------------------------------------
def v1_self_func_sha() -> None:
    try:
        got = func_sha_by_name(Path(__file__), "main")
    except Exception as e:  # noqa: BLE001
        _emit("V1_self_main_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_self_main_func_sha",
            True,
            f"placeholder OK; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
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
def v2_verify_lib_file_sha() -> None:
    got = _file_sha(LIB)
    _emit(
        "V2_verify_lib_file_sha",
        got == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: helper func sha (verify_baseline_fail_claims)
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "verify_baseline_fail_claims")
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_HELPER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: regex 真匹配
# ---------------------------------------------------------------------------
def v4_regex_behavior() -> None:
    # V4_1 单行 claim
    text1 = "Reviewer note: verify_infra_001 FAIL on baseline d69a594"
    ids1 = _ids_from_text(text1)
    _emit(
        "V4_1_single_line_claim_captured",
        ids1 == ["001"],
        f"ids={ids1}",
    )

    # V4_2 跨行 claim (新 regex 必须抓; 旧 [^\n] regex 漏)
    text2 = (
        "- verify_infra_002\n"
        "    FAIL: helper sha drift on baseline d69a594\n"
    )
    ids2 = _ids_from_text(text2)
    _emit(
        "V4_2_multiline_claim_captured",
        ids2 == ["002"],
        f"ids={ids2}",
    )

    # V4_3 多 verify claim 文本 → 全部 verify id 去重
    text3 = (
        "Reviewer findings:\n"
        "1) verify_infra_010 FAIL on baseline\n"
        "2) verify_infra_011\n"
        "   FAIL\n"
        "3) verify_infra_012 fail\n"
        "4) verify_infra_010 FAIL again (duplicate)\n"
    )
    ids3 = _ids_from_text(text3)
    _emit(
        "V4_3_multi_claims_dedup",
        ids3 == ["010", "011", "012"],
        f"ids={ids3}",
    )

    # V4_4 伪阳性: 无 verify_infra_NNN, 仅出现 FAIL 字面
    text4 = "Note: FAIL is sometimes used in unrelated contexts."
    ids4 = _ids_from_text(text4)
    _emit(
        "V4_4_no_verify_id_no_match",
        ids4 == [],
        f"ids={ids4}",
    )

    # V4_5 整段 baseline cross-check 真跑: 合成 mini git repo + 假 baseline
    ok = False
    detail = ""
    try:
        with tempfile.TemporaryDirectory(prefix="coco_p299_073_") as tmpd:
            mini = Path(tmpd) / "mini"
            mini.mkdir()
            scripts_dir = mini / "scripts"
            scripts_dir.mkdir()
            # Mini repo: 创建一个会 FAIL 的 verify_infra_801.py (rc=1)
            (scripts_dir / "verify_infra_801.py").write_text(
                "import sys\nprint('synthetic 801 FAIL')\nsys.exit(1)\n"
            )
            # 不创建 verify_infra_802.py → claims_missing 路径
            env = os.environ.copy()
            subprocess.run(["git", "init", "-q", str(mini)], check=True, env=env)
            subprocess.run(
                ["git", "-C", str(mini), "config", "user.email", "t@x"],
                check=True, env=env,
            )
            subprocess.run(
                ["git", "-C", str(mini), "config", "user.name", "t"],
                check=True, env=env,
            )
            subprocess.run(
                ["git", "-C", str(mini), "add", "-A"], check=True, env=env,
            )
            subprocess.run(
                ["git", "-C", str(mini), "commit", "-q", "-m", "init"],
                check=True, env=env,
            )
            head = subprocess.check_output(
                ["git", "-C", str(mini), "rev-parse", "HEAD"], env=env,
            ).decode().strip()

            # Reviewer text: 一个跨行 claim (801) + 一个单行 claim (802 missing)
            reviewer_text = (
                "Reviewer findings on baseline cross-check:\n"
                "- verify_infra_801\n"
                "    FAIL on baseline (real fail)\n"
                "- verify_infra_802 FAIL on baseline (missing script)\n"
            )
            r = verify_baseline_fail_claims(
                reviewer_text=reviewer_text,
                baseline_ref=head,
                repo_root=mini,
                timeout_per_script=30,
            )
            ok = (
                r.get("error") is None
                and r.get("claims_total") == 2
                and r.get("claims_verified") == 1
                and r.get("claims_missing") == 1
                and r.get("claims_contradicted") == 0
                and "scripts/verify_infra_802.py" in r.get("missing_scripts", [])
            )
            detail = (
                f"total={r.get('claims_total')} verified={r.get('claims_verified')} "
                f"missing={r.get('claims_missing')} "
                f"contradicted={r.get('claims_contradicted')} "
                f"err={r.get('error')!r}"
            )
    except Exception as e:  # noqa: BLE001
        detail = f"exception: {e!r}"
    _emit("V4_5_baseline_cross_check_real_run", ok, detail)


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate — 复用 P291 helper, 锁 P291 自身 passing evidence
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
        f"target={V5_GATE_FEATURE_ID} helper_ok={ok} reason={reason!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_func_sha()
    v2_verify_lib_file_sha()
    v3_helper_func_sha()
    v4_regex_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    failed_tags = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_073][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_073][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
