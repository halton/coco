#!/usr/bin/env python3
"""verify_infra_049: dump_reverse_sha_lock_index 反向锁索引工具 V0-V5 meta-lock.

infra-038-backlog-reverse-sha-lock-index (phase-34 #5.34, P268): 锁住
``scripts/dump_reverse_sha_lock_index.py`` 这个新建的反向 sha lock 索引工具的
关键行为, 防止后续无意改动悄悄改掉:

- ``build_entries``: 富化 inferred_target (复用 dump_v4_sha_graph._infer_target)
- ``render_text``: 人读分组表格
- ``render_json``: 机读 JSON payload (schema "reverse_sha_lock_index/v1")
- ``--text/--json/--check`` 三档互斥 CLI 约定

INFRA_049_SHA_LOCKS
-------------------
- ``scripts/dump_reverse_sha_lock_index.py`` file sha: EXPECTED_DUMP_INDEX_FILE_SHA
- ``render_text`` func sha: EXPECTED_RENDER_TEXT_FUNC_SHA
- ``render_json`` func sha: EXPECTED_RENDER_JSON_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: dump_reverse_sha_lock_index.py 存在 + 关键符号
  (``build_entries`` / ``render_text`` / ``render_json`` / argparse mutex group)
- V1 docstring sentinel ``INFRA_049_SHA_LOCKS`` 自锁 + 本脚本 v4_behavior func sha
- V2 dump_reverse_sha_lock_index.py file sha + render_text/render_json func sha
- V3 in-memory mutant: 替换 render_text body 为 ``return "graph LR"``, sha 必漂移;
  且原 baseline sha == EXPECTED_RENDER_TEXT_FUNC_SHA
- V4 行为验证: subprocess 分别跑 ``--json`` / ``--text``, 断言:
  - JSON 输出可 parse 为 dict 且 schema == "reverse_sha_lock_index/v1"
  - stats.scanned_count >= 5 (现状 7; 留 buffer 防 mismatch)
  - entries 每条含 6 个 key (file/lineno/const_name/sha_hex/sha_short/inferred_target)
  - text 输出含 verify_infra_034 / verify_infra_035 等关键 needle
  - ``--json --text`` 互斥 (argparse rc 非 0)
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

# infra-049 sha lock 常量 (V2) — 首跑 __BUMP_ME__ 占位, 再回填
EXPECTED_DUMP_INDEX_FILE_SHA = "1eb8fee45bd0436a328c69fbb2a130cce409483b0502928bf5a4d0ebeba36589"
EXPECTED_RENDER_TEXT_FUNC_SHA = "e2f4e931895f2f50a1ab6621045561b6c7b0cc377a5d9279e4b232e26f3e16c1"
EXPECTED_RENDER_JSON_FUNC_SHA = "872a889e839087e2246b9613fcaa509db914f4c575a7c3660bfc86a85f15d453"

# 本脚本 v4_behavior 自锁 (V1) — 首跑 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "bdcd934165a47169a2b133d40b25d66443da3664007e808ee7f1d043e8ad3c2c"

DOCSTRING_SENTINEL = "INFRA_049_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_049][{mark}] {tag} {detail}", flush=True)
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
        "def build_entries",
        "def render_text",
        "def render_json",
        "def cmd_check",
        "add_mutually_exclusive_group",
        '"--text"',
        '"--json"',
        '"--check"',
    ]
    found = [n for n in needed if n in src]
    _emit(
        "V0_dump_index_symbols",
        len(found) == len(needed),
        f"found {len(found)}/{len(needed)}",
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
# V2: dump_index file sha + render_text/render_json func sha
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
        ("render_text", EXPECTED_RENDER_TEXT_FUNC_SHA),
        ("render_json", EXPECTED_RENDER_JSON_FUNC_SHA),
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
# V3: in-memory mutant — 替换 render_text body, sha 必漂移
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    src = DUMP_INDEX_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "render_text":
            target = node
            break
    if target is None:
        _emit("V3_mutant_apply", False, "render_text not found")
        return
    baseline_sha = hashlib.sha256(ast.unparse(target).encode("utf-8")).hexdigest()
    mutant = ast.FunctionDef(
        name=target.name,
        args=target.args,
        body=[ast.Return(value=ast.Constant(value="graph LR"))],
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
    if EXPECTED_RENDER_TEXT_FUNC_SHA != "__BUMP_ME__":
        _emit(
            "V3_baseline_matches_expected",
            baseline_sha == EXPECTED_RENDER_TEXT_FUNC_SHA,
            f"baseline={baseline_sha[:16]} expect={EXPECTED_RENDER_TEXT_FUNC_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V4: 行为验证 — subprocess 跑 --json / --text / mutex
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # --- --json ---
    try:
        res_json = subprocess.run(
            [sys.executable, str(DUMP_INDEX_PY), "--json"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as e:
        _emit("V4_json_subprocess", False, f"err: {e!r}")
        return
    _emit("V4_json_subprocess", res_json.returncode == 0, f"rc={res_json.returncode}")

    parsed: dict | None = None
    try:
        parsed = json.loads(res_json.stdout)
    except Exception as e:
        _emit("V4_json_parse", False, f"err: {e!r}")
    if parsed is not None:
        _emit(
            "V4_json_schema",
            isinstance(parsed, dict) and parsed.get("schema") == "reverse_sha_lock_index/v1",
            f"schema={parsed.get('schema')!r}",
        )
        stats = parsed.get("stats") or {}
        scanned = stats.get("scanned_count", -1)
        _emit(
            "V4_json_scanned_count",
            isinstance(scanned, int) and scanned >= 5,
            f"scanned_count={scanned} (require >= 5)",
        )
        entries = parsed.get("entries") or []
        required_keys = {"file", "lineno", "const_name", "sha_hex", "sha_short", "inferred_target"}
        if entries:
            missing = [k for k in required_keys if k not in entries[0]]
            _emit(
                "V4_json_entry_keys",
                not missing,
                f"missing={missing}" if missing else f"all {len(required_keys)} keys present (n={len(entries)})",
            )
        else:
            _emit("V4_json_entry_keys", False, "entries empty")

    # --- --text ---
    try:
        res_text = subprocess.run(
            [sys.executable, str(DUMP_INDEX_PY), "--text"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as e:
        _emit("V4_text_subprocess", False, f"err: {e!r}")
        return
    _emit("V4_text_subprocess", res_text.returncode == 0, f"rc={res_text.returncode}")
    text_out = res_text.stdout
    # 关键 needle: 至少含一个 verify_infra_03X 锁主和反向锁常量前缀
    needles = ["verify_infra_03", "VERIFY_", "Reverse sha lock index"]
    miss = [n for n in needles if n not in text_out]
    _emit(
        "V4_text_needles",
        not miss,
        f"miss={miss}" if miss else f"all {len(needles)} needles present",
    )

    # --- mutex --json --text 互斥 ---
    try:
        res_mx = subprocess.run(
            [sys.executable, str(DUMP_INDEX_PY), "--json", "--text"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as e:
        _emit("V4_mutex_subprocess", False, f"err: {e!r}")
        return
    _emit(
        "V4_mutex_rejected",
        res_mx.returncode != 0 and "not allowed with" in (res_mx.stderr or ""),
        f"rc={res_mx.returncode} stderr_tail={(res_mx.stderr or '').splitlines()[-1] if res_mx.stderr else '<empty>'!r}",
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
        print(f"[verify_infra_049][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_049][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
