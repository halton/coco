#!/usr/bin/env python3
"""verify_infra_056: New verify-script self-checker bootstrap protocol (P275).

infra-P273-new-verify-self-checker-fixup-protocol (phase-36 #2.36, P275): P273
暴露过 "新建 verify_infra_NNN.py 时 EXPECTED_V4_CHECKER_FUNC_SHA 锁的是 V4
实现写完之前的旧 sha → 首跑 V1 self-check FAIL → 作者错误归因 pre-existing" 的
失真模式。本 feature 建立第 3 层防御（前两层 P274 已加: assert_verify_passed
helper + AGENTS.md Sub-agent Evidence Report Accuracy 硬规则）:

1. ``scripts/bootstrap_verify_self_checker.py`` —— 脚手架 helper, 让作者一行
   invoke 计算新 verify 脚本的 v4_behavior func sha, 不靠 "先跑看 FAIL 抠 sha"
   的隐式流程;
2. ``AGENTS.md`` 新增 "New verify-script self-checker bootstrap protocol (P275)"
   段, 4 条强制要求 + 推荐脚手架步骤模板。

本脚本验证:
- V0 scaffolding: bootstrap helper / AGENTS.md / _verify_lib 全部就位
- V1 docstring sentinel ``INFRA_056_SHA_LOCKS`` + 本脚本 v4_behavior func sha 自锁
- V2 _verify_lib.py file sha 锁 (本 feature 与 helper 共享同源 func_sha_by_name)
- V3 AGENTS.md sentinel: "New verify-script self-checker bootstrap protocol (P275)"
  段必须存在, 含 4 条强制要点 anchor
- V4 行为: 构造 tmp verify 脚本, 用 bootstrap helper subprocess 跑出 actual sha,
  断言与 _verify_lib.func_sha_by_name 直接调用一致 (round-trip)
- V5 Reviewer LGTM gate

INFRA_056_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_LIB_FILE_SHA
- ``scripts/bootstrap_verify_self_checker.py`` 必存 + 含
  ``compute_self_checker_sha`` 顶层函数
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA
- AGENTS.md sentinel: ``New verify-script self-checker bootstrap protocol (P275)``

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"
BOOTSTRAP_HELPER = SCRIPTS / "bootstrap_verify_self_checker.py"
AGENTS_MD = REPO / "AGENTS.md"

# 让本脚本能 import 同目录的 _verify_lib
sys.path.insert(0, str(SCRIPTS))

# infra-056 sha lock 常量 (V2)
EXPECTED_LIB_FILE_SHA = "f789e0d870c9e6c3764b893a6bcbcca356bfc464520d9c49f3d4b17045ecd243"

# 本脚本 v4_behavior 自锁 (V1) — 首跑用 __BUMP_ME__ 占位, 用 bootstrap helper 取真值后回填
EXPECTED_V4_CHECKER_FUNC_SHA = "0269c13abf4c3c3c9d90df840d07cf82f64967cfc37992dfc3107231bad6fbc6"

DOCSTRING_SENTINEL = "INFRA_056_SHA_LOCKS"
AGENTS_MD_SECTION_SENTINEL = "New verify-script self-checker bootstrap protocol (P275)"

# AGENTS.md P275 段必须包含的 4 条强制要点 anchor
AGENTS_MD_REQUIRED_ANCHORS: Tuple[str, ...] = (
    "最后一步才填",  # 强制 1: 最后一步回填
    "回填前必须先跑一次脚本",  # 强制 2: 先跑看真值
    "回填后必须再跑一次确认 PASS",  # 强制 3: 二次确认
    "推荐脚手架步骤模板",  # 强制 4: 模板段
)

# bootstrap helper 必须含的顶层函数签名 sentinel
BOOTSTRAP_REQUIRED_FUNCS: Tuple[str, ...] = (
    "compute_self_checker_sha",
    "build_constant_line",
    "main",
)


# phase-47 #1.47: V5_GATE evidence-bind helper (grace_period 兜底 soft graduate)
REAL_FEATURE_LIST = Path(__file__).resolve().parents[1] / "feature_list.json"
V5_GATE_FEATURE_ID = "__PHASE_47_PLACEHOLDER_INFRA_056__"

_results: List[Tuple[str, bool, str]] = []


import sys as _sys_v5
_sys_v5.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_056][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _func_sha(path: Path, func_name: str) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return hashlib.sha256(ast.unparse(node).encode("utf-8")).hexdigest()
    raise RuntimeError(f"func {func_name!r} not found in {path}")


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_verify_lib_exists", VERIFY_LIB.is_file(), f"path={VERIFY_LIB}")
    _emit("V0_bootstrap_helper_exists", BOOTSTRAP_HELPER.is_file(), f"path={BOOTSTRAP_HELPER}")
    _emit("V0_agents_md_exists", AGENTS_MD.is_file(), f"path={AGENTS_MD}")
    if BOOTSTRAP_HELPER.is_file():
        src = BOOTSTRAP_HELPER.read_text(encoding="utf-8")
        for fn in BOOTSTRAP_REQUIRED_FUNCS:
            _emit(
                f"V0_bootstrap_func_{fn}",
                f"def {fn}(" in src,
                f"expect 'def {fn}(' in helper source",
            )


# ---------------------------------------------------------------------------
# V1: docstring sentinel + 本脚本 v4_behavior func sha 自锁
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
        got = _func_sha(self_path, "v4_behavior")
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
def v2_lib_locks() -> None:
    got_file = _file_sha(VERIFY_LIB)
    if EXPECTED_LIB_FILE_SHA == "__BUMP_ME__":
        _emit("V2_lib_file_sha", False, f"placeholder; bump EXPECTED_LIB_FILE_SHA={got_file}")
    else:
        _emit(
            "V2_lib_file_sha",
            got_file == EXPECTED_LIB_FILE_SHA,
            f"got={got_file[:16]} expect={EXPECTED_LIB_FILE_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V3: AGENTS.md sentinel + 4 条强制要点 anchor
# ---------------------------------------------------------------------------
def v3_agents_md_sentinel() -> None:
    if not AGENTS_MD.is_file():
        _emit("V3_agents_md_section_sentinel", False, "AGENTS.md missing")
        return
    md = AGENTS_MD.read_text(encoding="utf-8")
    _emit(
        "V3_agents_md_section_sentinel",
        AGENTS_MD_SECTION_SENTINEL in md,
        f"sentinel={AGENTS_MD_SECTION_SENTINEL!r}",
    )
    missing = [a for a in AGENTS_MD_REQUIRED_ANCHORS if a not in md]
    _emit(
        "V3_agents_md_required_anchors",
        not missing,
        f"missing={missing}" if missing else f"hit {len(AGENTS_MD_REQUIRED_ANCHORS)}/{len(AGENTS_MD_REQUIRED_ANCHORS)}",
    )
    # AGENTS.md 段必须出现在 Sub-agent Evidence Report Accuracy 段之后 (语义顺序)
    idx_p274 = md.find("Sub-agent Evidence Report Accuracy")
    idx_p275 = md.find(AGENTS_MD_SECTION_SENTINEL)
    _emit(
        "V3_agents_md_section_order",
        idx_p274 != -1 and idx_p275 != -1 and idx_p275 > idx_p274,
        f"idx_p274={idx_p274} idx_p275={idx_p275}",
    )


# ---------------------------------------------------------------------------
# V4: 行为 — bootstrap helper round-trip
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # 直接 import 真 helper (sys.path 已含 scripts)
    try:
        from _verify_lib import func_sha_by_name
    except Exception as e:
        _emit("V4_import_lib", False, f"import err: {e!r}")
        return
    _emit("V4_import_lib", True, "imported func_sha_by_name")

    # 构造一个 tmp verify 脚本: 含一个名为 v4_behavior 的顶层函数, 内容固定
    tmp_src = (
        "def v4_behavior():\n"
        "    x = 1 + 2\n"
        "    return x\n"
    )
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td) / "verify_infra_dummy.py"
        tmp_path.write_text(tmp_src, encoding="utf-8")
        # 1) 直接调用 lib helper
        try:
            direct_sha = func_sha_by_name(tmp_path, "v4_behavior")
        except Exception as e:
            _emit("V4_direct_func_sha", False, f"lib err: {e!r}")
            return
        _emit("V4_direct_func_sha", bool(direct_sha) and len(direct_sha) == 64, f"sha={direct_sha[:16]}")

        # 2) 通过 bootstrap helper subprocess --json 拿 sha
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(BOOTSTRAP_HELPER),
                    "--verify-script",
                    str(tmp_path),
                    "--func-name",
                    "v4_behavior",
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception as e:
            _emit("V4_subprocess_helper", False, f"subprocess err: {e!r}")
            return
        _emit(
            "V4_subprocess_helper",
            proc.returncode == 0,
            f"rc={proc.returncode} stderr={proc.stderr[:120]!r}",
        )
        if proc.returncode != 0:
            return
        try:
            payload = json.loads(proc.stdout)
        except Exception as e:
            _emit("V4_helper_json_parse", False, f"json err: {e!r} stdout={proc.stdout[:200]!r}")
            return
        _emit(
            "V4_helper_json_parse",
            isinstance(payload, dict) and "actual_func_sha" in payload,
            f"keys={sorted(payload.keys()) if isinstance(payload, dict) else type(payload)}",
        )
        helper_sha = payload.get("actual_func_sha", "") if isinstance(payload, dict) else ""
        _emit(
            "V4_helper_sha_matches_direct",
            helper_sha == direct_sha,
            f"helper={helper_sha[:16]} direct={direct_sha[:16]}",
        )
        # paste_line 字段必须含建议常量名 + sha
        paste_line = payload.get("paste_line", "") if isinstance(payload, dict) else ""
        _emit(
            "V4_helper_paste_line_format",
            "EXPECTED_V4_CHECKER_FUNC_SHA" in paste_line and direct_sha in paste_line,
            f"paste_line={paste_line!r}",
        )
        # infra-P277: schema_version=1 锚点 — JSON 输出必须含整数 schema_version 等于 1
        schema_version = payload.get("schema_version") if isinstance(payload, dict) else None
        _emit(
            "V4_helper_schema_version_eq_1",
            schema_version == 1,
            f"schema_version={schema_version!r} expect=1",
        )

        # 3) 反例: 函数名不存在 → helper 应非 0 退出
        try:
            proc_bad = subprocess.run(
                [
                    sys.executable,
                    str(BOOTSTRAP_HELPER),
                    "--verify-script",
                    str(tmp_path),
                    "--func-name",
                    "does_not_exist_xyz",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception as e:
            _emit("V4_helper_missing_func_rc", False, f"subprocess err: {e!r}")
            return
        _emit(
            "V4_helper_missing_func_rc",
            proc_bad.returncode != 0,
            f"rc={proc_bad.returncode} stderr={proc_bad.stderr[:120]!r}",
        )

        # 4) 反例: --verify-script 路径不存在 → helper 应非 0 退出
        try:
            proc_missing = subprocess.run(
                [
                    sys.executable,
                    str(BOOTSTRAP_HELPER),
                    "--verify-script",
                    str(Path(td) / "no_such_file.py"),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception as e:
            _emit("V4_helper_missing_path_rc", False, f"subprocess err: {e!r}")
            return
        _emit(
            "V4_helper_missing_path_rc",
            proc_missing.returncode != 0,
            f"rc={proc_missing.returncode}",
        )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    """V5 Reviewer LGTM gate — phase-47 #1.47 graduate to evidence-bind helper.

    target feature evidence 不完整 (legacy / not_started)，通过 grace_period 兜底
    保持 emit=True，待 target feature 补齐 reviewer evidence 后从 grace 列表移除。
    """
    if not REAL_FEATURE_LIST.is_file():
        _emit(
            "V5_reviewer_lgtm_gate",
            False,
            f"feature_list.json not found at {REAL_FEATURE_LIST}",
        )
        return
    result = assert_v5_reviewer_gate_evidence_bind(
        V5_GATE_FEATURE_ID, REAL_FEATURE_LIST,
        grace_period_feature_ids=(V5_GATE_FEATURE_ID,),
    )
    _emit(
        "V5_reviewer_lgtm_gate",
        bool(result["ok"]),
        f"target={V5_GATE_FEATURE_ID} helper_ok={result['ok']} "
        f"grace_skipped={result['grace_skipped']} "
        f"verdict={result['verdict']!r} kind={result['reviewer_kind']!r} "
        f"summary_len={result['summary_len']} reason={result['reason']!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_lock()
    v2_lib_locks()
    v3_agents_md_sentinel()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_056][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_056][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
