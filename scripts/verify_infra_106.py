#!/usr/bin/env python3
"""verify_infra_106: verify_infra_061 SUMMARY 用语精准化锁.

infra-P293-typo-guard-check-count-doc-reconcile (phase-54 #2.54):
源自 infra-P281 Reviewer Round-1 finding B-P281-R3 — verify_infra_061
SUMMARY 历史写 ``ALL PASS (18 checks)``, 但 "checks" 一词与
``_results`` 实际 emit 次数 / 唯一 tag 数的关系含糊。sibling
``infra-P294-R6-sample-count-doc`` (phase-53 #4.53) 已在 verify_infra_062
上落地 ``ALL PASS (N emit-paths / M unique check tags)`` 同质处理, 本
verifier 锁 verify_infra_061 已 mirror 该处理。

INFRA_106_SHA_LOCKS
-------------------
- ``scripts/verify_infra_061.py`` file sha: EXPECTED_VERIFY_INFRA_061_FILE_SHA
- 自身 ``main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: verify_infra_061.py 存在 + ``def main(`` 存在
- V1 SUMMARY 行格式: ``ALL PASS ({total} emit-paths / {unique_tags} unique check tags)``
  关键 token 必须都出现在源码里 (静态扫描)
- V2 docstring R3 段必须含 "emit-paths" + "unique check tags" +
  "infra-P293-typo-guard-check-count-doc-reconcile" 三个 anchor
- V3 verify_infra_061 file sha 锁 (任何改动均触发 cascade bump)
- V4 runtime smoke: 真跑 verify_infra_061, 末行匹配新格式正则
  ``ALL PASS \\(\\d+ emit-paths / \\d+ unique check tags\\)``
- V5 Reviewer LGTM gate (grace_period 兜底)

退出码 0=ALL PASS / 2=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).

## Lock: EXPECTED_VERIFY_INFRA_061_FILE_SHA
- target_function: N/A
- target_file: scripts/verify_infra_061.py
- lock_kind: file_sha
- bump_when: scripts/verify_infra_061.py 文件 sha256 变化 (任何字节改动)
- bump_protocol: 重算 sha256 of scripts/verify_infra_061.py 并更新常量
- rationale: 锁 verify_infra_061 整体, 任何改动均触发 cascade bump, 防 SUMMARY emit-paths/unique tags 用语漂移
"""
from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
VERIFY_061 = SCRIPTS / "verify_infra_061.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

# infra-P293 sha lock 常量 (V3)
EXPECTED_VERIFY_INFRA_061_FILE_SHA = (
    "cd47627cc40488515ff87297383234ee57bcfdb255ed3c9afabb5305c9536f25"
)
# 自身 main func sha (首跑用 __BUMP_ME__ 占位, 再回填)
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "11a767c780326f4b2c7330c6b2eddd0bc5740338f162aab8e95f461510610161"
)

DOCSTRING_SENTINEL = "INFRA_106_SHA_LOCKS"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P293-typo-guard-check-count-doc-reconcile"

# V1 静态扫描关键 token (verify_infra_061 SUMMARY 行 fmt-string 内必须含)
V1_SUMMARY_TOKENS = (
    "emit-paths",
    "unique check tags",
    "len({t for t, _, _ in _results})",  # unique tag 计算式
)

# V2 docstring R3 段 anchor
V2_DOC_ANCHORS = (
    "emit-paths",
    "unique check tags",
    "infra-P293-typo-guard-check-count-doc-reconcile",
)

# V4 runtime SUMMARY 正则
V4_SUMMARY_RE = re.compile(
    r"\[verify_infra_061\]\[SUMMARY\] ALL PASS \((\d+) emit-paths / (\d+) unique check tags\)"
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_106][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0 scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_verify_061_exists", VERIFY_061.is_file(), f"path={VERIFY_061.relative_to(REPO)}")
    if not VERIFY_061.is_file():
        return
    src = VERIFY_061.read_text(encoding="utf-8")
    has_main = "def main(" in src
    _emit("V0_def_main_present", has_main, "expect 'def main(' in verify_infra_061.py")


# ---------------------------------------------------------------------------
# V1 SUMMARY fmt-string 静态扫描
# ---------------------------------------------------------------------------
def v1_summary_fmt_tokens() -> None:
    src = VERIFY_061.read_text(encoding="utf-8")
    for tok in V1_SUMMARY_TOKENS:
        tag_suffix = re.sub(r"\W+", "_", tok)[:32]
        _emit(
            f"V1_summary_token_{tag_suffix}",
            tok in src,
            f"expect token in src: {tok!r}",
        )


# ---------------------------------------------------------------------------
# V2 docstring R3 anchor
# ---------------------------------------------------------------------------
def v2_docstring_anchors() -> None:
    src = VERIFY_061.read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc = ast.get_docstring(tree) or ""
    for anchor in V2_DOC_ANCHORS:
        tag_suffix = re.sub(r"\W+", "_", anchor)[:48]
        _emit(
            f"V2_doc_has_{tag_suffix}",
            anchor in doc,
            f"docstring anchor={anchor!r}",
        )


# ---------------------------------------------------------------------------
# V3 verify_infra_061 file sha
# ---------------------------------------------------------------------------
def v3_file_sha() -> None:
    got = _file_sha(VERIFY_061)
    if EXPECTED_VERIFY_INFRA_061_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V3_verify_infra_061_file_sha",
            False,
            f"placeholder; bump EXPECTED_VERIFY_INFRA_061_FILE_SHA={got}",
        )
    else:
        _emit(
            "V3_verify_infra_061_file_sha",
            got == EXPECTED_VERIFY_INFRA_061_FILE_SHA,
            f"got={got[:16]} expect={EXPECTED_VERIFY_INFRA_061_FILE_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V4 runtime smoke: 真跑 verify_infra_061
# ---------------------------------------------------------------------------
def v4_runtime_summary() -> None:
    venv_py = REPO / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.is_file() else sys.executable
    try:
        r = subprocess.run(
            [py, str(VERIFY_061)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as e:
        _emit("V4_runtime_summary_match", False, f"subprocess err: {e!r}")
        return
    last_line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    m = V4_SUMMARY_RE.search(last_line)
    _emit(
        "V4_runtime_summary_match",
        m is not None and r.returncode == 0,
        f"rc={r.returncode} last_line={last_line!r}",
    )
    if m is not None:
        emit_n, uniq_n = int(m.group(1)), int(m.group(2))
        _emit(
            "V4_runtime_emit_eq_unique",
            emit_n == uniq_n,
            f"emit-paths={emit_n} unique={uniq_n} (happy-path 1:1)",
        )
    else:
        _emit("V4_runtime_emit_eq_unique", False, "summary regex did not match")


# ---------------------------------------------------------------------------
# V4b self main func sha
# ---------------------------------------------------------------------------
def v4b_self_main_func_sha() -> None:
    self_path = Path(__file__)
    got = func_sha_by_name(self_path, "main")
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V4b_self_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
    else:
        _emit(
            "V4b_self_main_func_sha",
            got == EXPECTED_SELF_MAIN_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V5 Reviewer LGTM gate
# ---------------------------------------------------------------------------
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


def main() -> None:
    v0_scaffolding()
    v1_summary_fmt_tokens()
    v2_docstring_anchors()
    v3_file_sha()
    v4_runtime_summary()
    v4b_self_main_func_sha()
    v5_reviewer_gate()
    total = len(_results)
    unique_tags = len({t for t, _, _ in _results})
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(
            f"[verify_infra_106][SUMMARY] FAIL {failed}/{total} emit-paths: {names}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_106][SUMMARY] ALL PASS ({total} emit-paths / {unique_tags} unique check tags)",
            flush=True,
        )
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
