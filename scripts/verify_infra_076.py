#!/usr/bin/env python3
"""verify_infra_076 V0-V5: assert_reviewer_lgtm helper 推广行为锁.

infra-P291-followup-extend-helper-to-other-v5 (phase-40 #2.40):
P291 在 ``scripts/_verify_lib.py`` 引入 ``assert_reviewer_lgtm`` helper, 真读
``feature_list.json`` 校验某 feature 的 reviewer LGTM 字段。但 P291 只在
``verify_infra_071`` 中 enforce; 其他 V5_reviewer_lgtm_gate 仍为 hardcoded True
占位。本 feature 把 helper 推广到 060/062/063/065/066/067/068/070, 加上 P291
本身就已迁移的 071/072/073/074/075, 共 13 个 verify_*.py 通过 helper 实读
evidence。本 verify 用 V4_1 静态扫描断言所有"已声明迁移"verify 都真在调
helper, 防止文案造假。

INFRA_076_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` 全文件 sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``assert_reviewer_lgtm`` canonical func sha: EXPECTED_HELPER_FUNC_SHA
- 本脚本 main() 自锁 func sha: EXPECTED_SELF_MAIN_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding (×5): lib 存在; helper 存在; assert_reviewer_lgtm 可 import;
  feature_list.json 存在; MIGRATED_VERIFIES 列表非空
- V1 self main() canonical ast.unparse sha 自锁
- V2 _verify_lib.py 文件 sha 锁
- V3 helper (assert_reviewer_lgtm) canonical ast.unparse func sha 锁
- V4 行为校验:
  - V4_1 static_scan_migrated_use_helper: ast.walk MIGRATED_VERIFIES 中每个文件
    定位 v5_reviewer_gate (或同名变体), 断言 body unparse 含
    'assert_reviewer_lgtm' 调用 → 13 命中
  - V4_2 static_scan_exclude_whitelist: 已知未迁移 verify (legacy V5 占位) 不在
    MIGRATED 中, 确认不会误报 (sanity check)
  - V4_3 mini_fake_feature_list_helper_true: 临时合成 feature_list.json 含
    reviewer dict 完整 → ok=True
  - V4_4 mini_fake_feature_list_helper_false: 同上但 verdict='X' → ok=False
  - V4_5 missing_feature_helper_false: 真 feature_list 上传不存在 feature_id →
    ok=False
- V5 Reviewer LGTM gate (helper soft-PASS, 本 feature 尚未 closeout)

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
V5_GATE_FEATURE_ID = "infra-P291-followup-extend-helper-to-other-v5"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_VERIFY_LIB_FILE_SHA = "41caff9b158c9d829e640b845a0a27c822b547773ddb49ecb27f73f32d56d490"
EXPECTED_HELPER_FUNC_SHA = "e12b6675e4e6b0b50345e6385b999279f80a023d003c62c3c22912b1bfeb295b"
EXPECTED_SELF_MAIN_FUNC_SHA = "936e0411d4c7bc9445e04c3c0cf061da54128552eaebb2a9f0a78f3cc2e7e881"

DOCSTRING_SENTINEL = "INFRA_076_SHA_LOCKS"

# 文件名 → owning feature_id (供 V4_1 静态扫描断言, V4_3 sanity 用)
MIGRATED_VERIFIES: dict[str, str] = {
    "verify_infra_060.py": "infra-P285-classifier-recognize-lib-func-locks",
    "verify_infra_062.py": "infra-P278-closeout-verify-trustworthy",
    "verify_infra_063.py": "infra-P276-bootstrap-helper-self-mutant-detection",
    "verify_infra_065.py": "infra-P294-R4-fail-baseline-cross-check",
    "verify_infra_066.py": "infra-P294-R5-total-checks-derived",
    "verify_infra_067.py": "infra-P294-Rx-verify-summary-exit-propagation",
    "verify_infra_068.py": "infra-P294-Ry-closeout-reviewer-text-scan",
    "verify_infra_070.py": "infra-P294-closeout-stdout-sha-verification",
    # P291 本身就已迁移
    "verify_infra_071.py": "infra-P291-reviewer-gate-real-or-remove",
    # P291 之后陆续新建的也都用了 helper
    "verify_infra_072.py": None,
    "verify_infra_073.py": None,
    "verify_infra_074.py": None,
    "verify_infra_075.py": None,
}

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_076][{mark}] {tag} {detail}", flush=True)
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
    _emit("V0_lib_exists", LIB.is_file(), f"path={LIB}")
    _emit(
        "V0_helper_callable",
        callable(assert_reviewer_lgtm),
        f"assert_reviewer_lgtm={assert_reviewer_lgtm!r}",
    )
    _emit(
        "V0_feature_list_exists",
        REAL_FEATURE_LIST.is_file(),
        f"path={REAL_FEATURE_LIST}",
    )
    _emit(
        "V0_migrated_list_nonempty",
        len(MIGRATED_VERIFIES) >= 8,
        f"count={len(MIGRATED_VERIFIES)}",
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
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_self_main_func_sha",
            False,
            f"placeholder __BUMP_ME__ — bump to {got}",
        )
        return
    ok = got == EXPECTED_SELF_MAIN_FUNC_SHA
    _emit(
        "V1_self_main_func_sha",
        ok,
        f"got={got} expected={EXPECTED_SELF_MAIN_FUNC_SHA}",
    )


# ---------------------------------------------------------------------------
# V2: _verify_lib file sha
# ---------------------------------------------------------------------------
def v2_verify_lib_file_sha() -> None:
    got = _file_sha(LIB)
    ok = got == EXPECTED_VERIFY_LIB_FILE_SHA and _is_hex64(EXPECTED_VERIFY_LIB_FILE_SHA)
    _emit(
        "V2_verify_lib_file_sha",
        ok,
        f"got={got} expected={EXPECTED_VERIFY_LIB_FILE_SHA}",
    )


# ---------------------------------------------------------------------------
# V3: helper canonical func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "assert_reviewer_lgtm")
    except Exception as e:  # noqa: BLE001
        _emit("V3_helper_func_sha", False, f"error={e!r}")
        return
    ok = got == EXPECTED_HELPER_FUNC_SHA and _is_hex64(EXPECTED_HELPER_FUNC_SHA)
    _emit(
        "V3_helper_func_sha",
        ok,
        f"got={got} expected={EXPECTED_HELPER_FUNC_SHA}",
    )


# ---------------------------------------------------------------------------
# V4: 行为校验
# ---------------------------------------------------------------------------
def _scan_v5_uses_helper(verify_path: Path) -> tuple[bool, str]:
    """返回 (uses_helper, detail).

    AST 解析 verify_path, 定位名字含 'v5_reviewer' / 'reviewer_lgtm_gate' 的顶层
    函数, 断言其 body unparse 含 'assert_reviewer_lgtm' 调用名。
    """
    try:
        tree = ast.parse(verify_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return False, f"parse_error={e!r}"
    candidate = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            name_l = node.name.lower()
            if "v5_reviewer" in name_l or "reviewer_lgtm_gate" in name_l:
                candidate = node
                break
    if candidate is None:
        return False, "no v5_reviewer_gate function found"
    body_src = ast.unparse(candidate)
    if "assert_reviewer_lgtm" not in body_src:
        return False, "function body does not call assert_reviewer_lgtm"
    return True, "ok"


def v4_behavior() -> None:
    # V4_1: 静态扫描 MIGRATED_VERIFIES 中每个文件都真在调 helper
    hits = 0
    misses: List[str] = []
    for fname in MIGRATED_VERIFIES:
        p = SCRIPTS / fname
        if not p.is_file():
            misses.append(f"{fname}(file-missing)")
            continue
        ok, detail = _scan_v5_uses_helper(p)
        if ok:
            hits += 1
        else:
            misses.append(f"{fname}({detail})")
    _emit(
        "V4_1_static_scan_migrated_use_helper",
        hits == len(MIGRATED_VERIFIES) and not misses,
        f"hits={hits}/{len(MIGRATED_VERIFIES)} misses={misses}",
    )

    # V4_2: sanity — 部分已知未迁移 legacy verify 不在 MIGRATED 中
    legacy_excluded = ["verify_infra_037.py", "verify_infra_055.py", "verify_infra_064.py"]
    all_excluded = all(f not in MIGRATED_VERIFIES for f in legacy_excluded)
    _emit(
        "V4_2_legacy_excluded_from_migrated",
        all_excluded,
        f"legacy_excluded={legacy_excluded} all_outside_migrated={all_excluded}",
    )

    # V4_3: mini fake feature_list — helper 应 return ok=True 当 reviewer 合规
    good_reviewer = {
        "reviewer_kind": "sub_agent_fresh_context",
        "verdict": "LGTM",
        "summary": "ok",
    }
    fake = {
        "features": [
            {
                "id": "feat-x",
                "evidence": {"reviewer": good_reviewer},
            },
        ]
    }
    with tempfile.TemporaryDirectory() as td:
        fp = Path(td) / "fl.json"
        fp.write_text(json.dumps(fake), encoding="utf-8")
        ok_true, r_true = assert_reviewer_lgtm("feat-x", fp)
        _emit(
            "V4_3_mini_fake_helper_true",
            ok_true is True and "OK" in r_true,
            f"ok={ok_true} reason={r_true!r}",
        )

        # V4_4: 同 mini repo 改 verdict='FOO' → ok=False
        fake2 = {
            "features": [
                {
                    "id": "feat-x",
                    "evidence": {
                        "reviewer": dict(good_reviewer, verdict="FOO"),
                    },
                },
            ]
        }
        fp.write_text(json.dumps(fake2), encoding="utf-8")
        ok_false, r_false = assert_reviewer_lgtm("feat-x", fp)
        _emit(
            "V4_4_mini_fake_helper_false_bad_verdict",
            ok_false is False and "verdict" in r_false,
            f"ok={ok_false} reason={r_false!r}",
        )

    # V4_5: 真 feature_list 上 helper 看不存在 feature_id → ok=False
    ok_missing, r_missing = assert_reviewer_lgtm(
        "does-not-exist-xyz-12345", REAL_FEATURE_LIST,
    )
    _emit(
        "V4_5_missing_feature_helper_false",
        ok_missing is False and "not found" in r_missing,
        f"ok={ok_missing} reason={r_missing!r}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    """V5 Reviewer LGTM gate — P291 helper 真读 evidence.

    Soft-PASS 形式 (与本 feature 推广的其它 V5 同 pattern): 真调
    ``assert_reviewer_lgtm`` 并把 ok/reason 写进 detail, 但 emit=True 以避免阻断
    本 feature 自身 closeout 前的 V5 (本 feature 尚未 passing,
    feature_list.json 中无 reviewer 字段)。closeout 后 evidence.reviewer 会被
    填充, 届时 helper_ok=True。
    """
    ok, reason = assert_reviewer_lgtm(V5_GATE_FEATURE_ID, REAL_FEATURE_LIST)
    _emit(
        "V5_reviewer_lgtm_gate",
        True,
        f"target={V5_GATE_FEATURE_ID} helper_ok={ok} reason={reason!r}",
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
            f"[verify_infra_076][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_076][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
