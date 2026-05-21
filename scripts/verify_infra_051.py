#!/usr/bin/env python3
"""verify_infra_051: dump_reverse_sha_lock_index --check --json coalesce V0-V5.

infra-049-backlog-check-coalesce-json (phase-35 #2.35, P270): 锁住
``scripts/dump_reverse_sha_lock_index.py`` 在 P270 引入的 ``--check --json`` 协同
模式 + argparse 拓扑调整, 防止后续无意改动悄悄撤回:

- argparse 由原 ``--text/--json/--check`` 三档互斥拓扑改为:
  ``--text/--json`` 互斥 (输出格式), ``--check`` 独立 flag (行为开关)
- ``render_check_json``: 新增 consistency report JSON 渲染函数
  schema = ``reverse_sha_lock_consistency/v1``
- ``cmd_check(as_json: bool)``: 接受 as_json 旗标; True 走 render_check_json,
  False 维持原 stdout 文本 summary

INFRA_051_SHA_LOCKS
-------------------
- ``scripts/dump_reverse_sha_lock_index.py`` file sha: EXPECTED_DUMP_INDEX_FILE_SHA
- ``render_check_json`` func sha: EXPECTED_RENDER_CHECK_JSON_FUNC_SHA
- ``cmd_check`` func sha: EXPECTED_CMD_CHECK_FUNC_SHA
- ``build_arg_parser`` func sha: EXPECTED_BUILD_ARG_PARSER_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: dump_reverse_sha_lock_index.py 存在 + 关键符号
  (``render_check_json`` / ``cmd_check`` 带 as_json 形参 / ``reverse_sha_lock_consistency/v1``)
- V1 docstring sentinel ``INFRA_051_SHA_LOCKS`` 自锁 + 本脚本 v4_behavior func sha
- V2 dump_index file sha + render_check_json/cmd_check/build_arg_parser func sha
- V3 in-memory mutant: render_check_json 替换 schema 常量, sha 必漂移
- V4 行为验证 subprocess:
  - ``--check``                   rc=0, stdout 含 "all_match=True"
  - ``--check --json``            rc=0, stdout JSON parse 含 schema reverse_sha_lock_consistency/v1
                                        + all_match: true + scanned_count/live_count int
  - ``--check --text``            rc=0, 与 ``--check`` 文本输出等价
  - ``--text --json``             rc=2 (argparse mutex 保留)
- V5 Reviewer LGTM gate (print-only)

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DUMP_INDEX_PY = SCRIPTS / "dump_reverse_sha_lock_index.py"

# infra-051 sha lock 常量 (V2) — 首跑 __BUMP_ME__ 占位, 再回填
EXPECTED_DUMP_INDEX_FILE_SHA = "f3d93d4f79e6580a1aabaa419281ed8ba2ee4aeaf9e899ff425531429c9bddc0"
EXPECTED_RENDER_CHECK_JSON_FUNC_SHA = "310d1b6bda8ecebf01abed9064d895de33d351f9bac7405310f5ae75795b2011"
EXPECTED_CMD_CHECK_FUNC_SHA = "5cc2173fe5eb11920f1fe92d5660bc8a02eb102cb03da3ddcfcd69c2d5d0b285"
EXPECTED_BUILD_ARG_PARSER_FUNC_SHA = "9fd746f61a1705f69107a072736fe1272fca3260ed0c5567b7d59ef33d58092f"

# 本脚本 v4_behavior 自锁 (V1) — 首跑 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "3c986d4bdeeee98812cd937f03f271b1871df861e7f91014891ab8fecd3cf0e4"

DOCSTRING_SENTINEL = "INFRA_051_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_051][{mark}] {tag} {detail}", flush=True)
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
    _emit("V0_dump_index_exists", DUMP_INDEX_PY.is_file(), f"path={DUMP_INDEX_PY}")
    src = DUMP_INDEX_PY.read_text(encoding="utf-8")
    needed = [
        "def render_check_json",
        "def cmd_check(as_json",
        '"reverse_sha_lock_consistency/v1"',
        "as_json=bool(args.json)",
    ]
    found = [n for n in needed if n in src]
    _emit(
        "V0_dump_index_symbols",
        len(found) == len(needed),
        f"found {len(found)}/{len(needed)} missing={[n for n in needed if n not in src]}",
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
# V2: dump_index file sha + render_check_json/cmd_check/build_arg_parser func sha
# ---------------------------------------------------------------------------
def v2_dump_locks() -> None:
    got_file = _file_sha(DUMP_INDEX_PY)
    if EXPECTED_DUMP_INDEX_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V2_dump_index_file_sha",
            False,
            f"placeholder; bump EXPECTED_DUMP_INDEX_FILE_SHA={got_file}",
        )
    else:
        _emit(
            "V2_dump_index_file_sha",
            got_file == EXPECTED_DUMP_INDEX_FILE_SHA,
            f"got={got_file[:16]} expect={EXPECTED_DUMP_INDEX_FILE_SHA[:16]}",
        )

    for fn, expected in (
        ("render_check_json", EXPECTED_RENDER_CHECK_JSON_FUNC_SHA),
        ("cmd_check", EXPECTED_CMD_CHECK_FUNC_SHA),
        ("build_arg_parser", EXPECTED_BUILD_ARG_PARSER_FUNC_SHA),
    ):
        try:
            got = _func_sha(DUMP_INDEX_PY, fn)
        except Exception as e:
            _emit(f"V2_{fn}_func_sha", False, f"compute err: {e!r}")
            continue
        if expected == "__BUMP_ME__":
            _emit(
                f"V2_{fn}_func_sha",
                False,
                f"placeholder; bump EXPECTED_{fn.upper()}_FUNC_SHA={got}",
            )
            continue
        _emit(
            f"V2_{fn}_func_sha",
            got == expected,
            f"got={got[:16]} expect={expected[:16]}",
        )


# ---------------------------------------------------------------------------
# V3: in-memory mutant — render_check_json schema 常量篡改, sha 必漂移
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    src = DUMP_INDEX_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "render_check_json":
            target = node
            break
    if target is None:
        _emit("V3_mutant_apply", False, "render_check_json not found")
        return
    baseline_sha = hashlib.sha256(ast.unparse(target).encode("utf-8")).hexdigest()
    mutant = ast.FunctionDef(
        name=target.name,
        args=target.args,
        body=[ast.Return(value=ast.Constant(value="MUTANT"))],
        decorator_list=[],
        returns=target.returns,
    )
    ast.fix_missing_locations(mutant)
    mutant_sha = hashlib.sha256(ast.unparse(mutant).encode("utf-8")).hexdigest()
    _emit(
        "V3_mutant_sha_drift",
        baseline_sha != mutant_sha,
        f"baseline={baseline_sha[:16]} mutant={mutant_sha[:16]}",
    )
    if EXPECTED_RENDER_CHECK_JSON_FUNC_SHA != "__BUMP_ME__":
        _emit(
            "V3_baseline_matches_expected",
            baseline_sha == EXPECTED_RENDER_CHECK_JSON_FUNC_SHA,
            f"baseline={baseline_sha[:16]} expect={EXPECTED_RENDER_CHECK_JSON_FUNC_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V4: 行为验证 — subprocess 跑 --check / --check --json / --check --text / mutex
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    def _run(args: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(DUMP_INDEX_PY), *args],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=30,
        )

    # --- --check (默认文本) ---
    try:
        r_check = _run(["--check"])
    except Exception as e:
        _emit("V4_check_subprocess", False, f"err: {e!r}")
        return
    _emit(
        "V4_check_rc_and_text",
        r_check.returncode == 0 and "all_match=True" in r_check.stdout,
        f"rc={r_check.returncode} stdout_head={r_check.stdout.splitlines()[0] if r_check.stdout else '<empty>'!r}",
    )

    # --- --check --json (协同) ---
    try:
        r_cj = _run(["--check", "--json"])
    except Exception as e:
        _emit("V4_check_json_subprocess", False, f"err: {e!r}")
        return
    _emit("V4_check_json_rc", r_cj.returncode == 0, f"rc={r_cj.returncode}")
    parsed: dict | None = None
    try:
        parsed = json.loads(r_cj.stdout)
    except Exception as e:
        _emit("V4_check_json_parse", False, f"err: {e!r}")
    if parsed is not None:
        _emit(
            "V4_check_json_schema",
            isinstance(parsed, dict) and parsed.get("schema") == "reverse_sha_lock_consistency/v1",
            f"schema={parsed.get('schema')!r}",
        )
        _emit(
            "V4_check_json_all_match",
            parsed.get("all_match") is True,
            f"all_match={parsed.get('all_match')!r}",
        )
        sc, lc = parsed.get("scanned_count"), parsed.get("live_count")
        _emit(
            "V4_check_json_counts",
            isinstance(sc, int) and isinstance(lc, int) and sc >= 1 and lc >= 1,
            f"scanned_count={sc} live_count={lc}",
        )
        _emit(
            "V4_check_json_orphans_list",
            isinstance(parsed.get("orphans"), list),
            f"orphans_type={type(parsed.get('orphans')).__name__}",
        )

    # --- --check --text (等价 --check) ---
    try:
        r_ct = _run(["--check", "--text"])
    except Exception as e:
        _emit("V4_check_text_subprocess", False, f"err: {e!r}")
        return
    _emit(
        "V4_check_text_equiv",
        r_ct.returncode == 0 and r_ct.stdout == r_check.stdout,
        f"rc={r_ct.returncode} equiv={r_ct.stdout == r_check.stdout}",
    )

    # --- --text --json 互斥保留 ---
    try:
        r_mx = _run(["--text", "--json"])
    except Exception as e:
        _emit("V4_mutex_subprocess", False, f"err: {e!r}")
        return
    _emit(
        "V4_mutex_rejected",
        r_mx.returncode == 2 and "not allowed with" in (r_mx.stderr or ""),
        f"rc={r_mx.returncode} stderr_tail={(r_mx.stderr or '').splitlines()[-1] if r_mx.stderr else '<empty>'!r}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    _emit(
        "V5_reviewer_lgtm_gate",
        True,
        "closeout 阶段必须有 sub-agent fresh-context Reviewer LGTM (evidence 记录)",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_lock()
    v2_dump_locks()
    v3_mutant()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_051][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_051][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
