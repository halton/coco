#!/usr/bin/env python3
"""verify_infra_071 V0-V5: Reviewer LGTM gate "real or remove" 行为锁.

infra-P291-reviewer-gate-real-or-remove (phase-39 #2.39):

现状: verify_infra_034 / 060 / 062 / 063 / 065 / 066 / 067 / 068 / 069 / 070
等脚本的 ``V5_reviewer_lgtm_gate`` 全是 hardcoded ``True`` placeholder, 与 tag
名字 "Reviewer LGTM gate" 名实不符 (实际不读任何 evidence)。本 feature 选
"option A: 实读 evidence", 在 ``_verify_lib.assert_reviewer_lgtm(feature_id,
feature_list_path)`` 加 helper, 真读 feature_list.json 校验
``evidence.closeout_verify.reviewer.reviewer_kind == 'sub_agent_fresh_context'``
且 ``verdict == 'LGTM'``。

**Default-OFF 哲学** (P291 acceptance): helper 是工具, **不强制 cascade** 改所有
V5。本 feature 只锁 ``scripts/verify_infra_071.py`` 自身 V5 用 helper, 其它
verify 的 V5 placeholder 保留 (入 backlog 后续推进)。

INFRA_071_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` 全文件 sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``assert_reviewer_lgtm`` canonical func sha: EXPECTED_HELPER_FUNC_SHA
- 本脚本 main() 自锁 func sha: EXPECTED_SELF_MAIN_FUNC_SHA (placeholder __BUMP_ME__)

校验层级 (V0-V5):

- V0 scaffolding: 常量存在且 hex64
- V1 self main() func sha 自锁 (placeholder OK 与 070 风格一致)
- V2 _verify_lib file sha
- V3 helper func sha (assert_reviewer_lgtm) canonical ast.unparse sha
- V4 业务实测 (在 tempfile 写假 feature_list.json):
  - V4_1: 含正确 reviewer dict → ok=True
  - V4_2 mutant: reviewer_kind='main_context' → ok=False, reason 含 'reviewer_kind'
  - V4_3: verdict='REJECT' → ok=False
  - V4_4: 缺 reviewer key → ok=False
  - V4_5: feature_id 不存在 → ok=False, reason 含 'not found'
- V5_reviewer_lgtm_gate: 用 helper 真读 repo 的 feature_list.json 校验上一
  passing feature ``infra-P294-closeout-stdout-sha-verification`` 的 reviewer
  字段 → 期望 ok=True

退出码: 0=ALL PASS, 2=任一 FAIL (走 verify_summary_exit)。

运行环境约定 (infra-034): 必须在 .venv 下运行。
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
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
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_VERIFY_LIB_FILE_SHA = "2d6d4118c664412efe2d2c2e5c988adba47fe682e1ae939f13c2a5fdd4ca09c3"
EXPECTED_HELPER_FUNC_SHA = "e12b6675e4e6b0b50345e6385b999279f80a023d003c62c3c22912b1bfeb295b"
EXPECTED_SELF_MAIN_FUNC_SHA = "4e7a5205b4c611d28f8c0788ff8d9b74725bb2481b893ec53b0882fb4e7c69ba"

DOCSTRING_SENTINEL = "INFRA_071_SHA_LOCKS"

# V5 gate target: 上一 passing feature, evidence.closeout_verify.reviewer 已含 LGTM
V5_GATE_FEATURE_ID = "infra-P294-closeout-stdout-sha-verification"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_071][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


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
# V1: self main() func sha lock
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
# V3: helper func sha (assert_reviewer_lgtm)
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "assert_reviewer_lgtm")
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_HELPER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 业务实测 (tempfile 合成 feature_list.json)
# ---------------------------------------------------------------------------
def _make_fl(tmpd: Path, name: str, content: dict) -> Path:
    p = tmpd / name
    p.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def v4_behavior() -> None:
    with tempfile.TemporaryDirectory(prefix="coco_p291_v071_") as td_str:
        tmpd = Path(td_str)

        good_reviewer = {
            "reviewer_kind": "sub_agent_fresh_context",
            "verdict": "LGTM",
            "summary": "all good",
        }

        # V4_1: 正确 reviewer (放在 closeout_verify.reviewer) → ok=True
        fl_ok = _make_fl(tmpd, "fl_ok.json", {
            "features": [{
                "id": "feat-x",
                "status": "passing",
                "evidence": {"closeout_verify": {"reviewer": good_reviewer}},
            }],
        })
        ok1, reason1 = assert_reviewer_lgtm("feat-x", fl_ok)
        _emit("V4_1_ok_true", ok1 is True, f"reason={reason1!r}")

        # V4_2 mutant: reviewer_kind='main_context' → ok=False, reason 含 'reviewer_kind'
        bad_kind = dict(good_reviewer, reviewer_kind="main_context")
        fl_bad_kind = _make_fl(tmpd, "fl_bad_kind.json", {
            "features": [{
                "id": "feat-x",
                "evidence": {"closeout_verify": {"reviewer": bad_kind}},
            }],
        })
        ok2, reason2 = assert_reviewer_lgtm("feat-x", fl_bad_kind)
        _emit(
            "V4_2_mutant_reviewer_kind",
            ok2 is False and "reviewer_kind" in reason2,
            f"ok={ok2} reason={reason2!r}",
        )

        # V4_3: verdict='REJECT' → ok=False
        bad_verdict = dict(good_reviewer, verdict="REJECT")
        fl_bad_v = _make_fl(tmpd, "fl_bad_verdict.json", {
            "features": [{
                "id": "feat-x",
                "evidence": {"closeout_verify": {"reviewer": bad_verdict}},
            }],
        })
        ok3, reason3 = assert_reviewer_lgtm("feat-x", fl_bad_v)
        _emit(
            "V4_3_bad_verdict",
            ok3 is False and "verdict" in reason3.lower(),
            f"ok={ok3} reason={reason3!r}",
        )

        # V4_4: 缺 reviewer key → ok=False
        fl_no_rev = _make_fl(tmpd, "fl_no_rev.json", {
            "features": [{
                "id": "feat-x",
                "evidence": {"closeout_verify": {"verify_runs": []}},
            }],
        })
        ok4, reason4 = assert_reviewer_lgtm("feat-x", fl_no_rev)
        _emit(
            "V4_4_missing_reviewer",
            ok4 is False and "reviewer" in reason4.lower(),
            f"ok={ok4} reason={reason4!r}",
        )

        # V4_5: feature_id 不存在 → ok=False, reason 含 'not found'
        ok5, reason5 = assert_reviewer_lgtm("does-not-exist", fl_ok)
        _emit(
            "V4_5_feature_not_found",
            ok5 is False and "not found" in reason5.lower(),
            f"ok={ok5} reason={reason5!r}",
        )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate — 真读 repo feature_list.json
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
    v2_verify_lib_file_sha()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    failed_tags = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_071][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_071][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
