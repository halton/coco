#!/usr/bin/env python3
"""verify_infra_064 V0-V5: smoke_history.jsonl 不再 tracked 政策锁.

infra-P284-smoke-history-jsonl-policy (phase-37 #5.37):
``evidence/_history/smoke_history.jsonl`` 是 ``./init.sh`` smoke 运行的本机
append-only evidence。先前 git tracked, 导致每次 closeout / Reviewer 都要先
``git stash`` 才能 merge, 流程噪音。本 feature 选方向 A —— 把该文件加进
``.gitignore`` 并 ``git rm --cached``, 之后磁盘文件仍可用作本机调试, 但 git
永远不会再 dirty。

INFRA_064_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: .gitignore 与目标 jsonl 路径变量存在 / __main__ / --json
- V1 docstring sentinel ``INFRA_064_SHA_LOCKS`` + 本脚本 v4_behavior 自锁
- V2 _verify_lib.py file sha
- V3 helper func sha (本 verify 自身的 _gitignore_has_target / _file_is_ignored
  两个核心 helper func sha 自锁)
- V4 行为:
  - V4.1: 读 ``.gitignore`` 内容, 断言含 ``evidence/_history/smoke_history.jsonl``
    (精确字符串匹配 — 防有人改成更宽通配又改其他文件)
  - V4.2: ``git ls-files -- evidence/_history/smoke_history.jsonl`` 输出空
    (即非 tracked)
  - V4.3: ``git check-ignore evidence/_history/smoke_history.jsonl`` exit 0
    (被 ignored 命中)
  - V4.4: 模拟 append 一行到 jsonl, 跑 ``git status --porcelain -- <jsonl>``,
    断言输出为空 (即 ignored 生效, append 不会让 git 变 dirty); 然后恢复文件
  - V4.5: 若 jsonl 存在且非空, 解析每行 JSON 必须合法 dict 含 ``ts`` 与
    ``kind`` 两个 key (schema 不退化)
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
LIB = SCRIPTS / "_verify_lib.py"
GITIGNORE = REPO / ".gitignore"
JSONL_REL = "evidence/_history/smoke_history.jsonl"
JSONL = REPO / JSONL_REL

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import func_sha_by_name  # noqa: E402

EXPECTED_VERIFY_LIB_FILE_SHA = "4f168152cb1c4def4a6b5559bfea8699633df0b79fc6cac9805460d34408a6bd"
EXPECTED_GITIGNORE_HELPER_FUNC_SHA = "b6bbd8e96e3078a335db3f535ecd67f62cef1dc1db6585ce73f6295b7744fb08"
EXPECTED_FILE_IGNORED_HELPER_FUNC_SHA = "042ac55f55ee97de823a8547e7728fef3e63b11d34c392312a2215c10b8621b3"
EXPECTED_V4_CHECKER_FUNC_SHA = "7919b8c04e99db2efc2667fefbc760f0d85da35705aa09314b12f6f15db06054"

DOCSTRING_SENTINEL = "INFRA_064_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_064][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gitignore_has_target(gitignore_path: Path, target: str) -> bool:
    """断言 .gitignore 含 target 精确字符串 (单行 strip 后 == target)."""
    if not gitignore_path.is_file():
        return False
    for raw in gitignore_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == target:
            return True
    return False


def _file_is_ignored(repo: Path, rel: str) -> bool:
    """``git check-ignore`` exit 0 即返回 True."""
    proc = subprocess.run(
        ["git", "check-ignore", "--", rel],
        cwd=str(repo), capture_output=True, text=True, check=False,
    )
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_verify_lib_exists", LIB.is_file(), f"path={LIB}")
    _emit("V0_gitignore_exists", GITIGNORE.is_file(), f"path={GITIGNORE}")
    self_path = Path(__file__)
    src = self_path.read_text(encoding="utf-8")
    _emit(
        "V0_main_guard",
        'if __name__ == "__main__":' in src and "def main(" in src,
        "expect __main__ guard + def main()",
    )
    _emit(
        "V0_json_flag_declared",
        '"--json"' in src,
        "expect --json flag",
    )


# ---------------------------------------------------------------------------
# V1: docstring sentinel + v4_behavior 自锁
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
        got = func_sha_by_name(self_path, "v4_behavior")
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
# V2: file shas
# ---------------------------------------------------------------------------
def v2_file_shas() -> None:
    got_lib = _file_sha(LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit("V2_lib_file_sha", False, f"placeholder; bump={got_lib}")
    else:
        _emit(
            "V2_lib_file_sha",
            got_lib == EXPECTED_VERIFY_LIB_FILE_SHA,
            f"got={got_lib[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V3: helper func sha (self-checks for the two core helpers)
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    self_path = Path(__file__)
    try:
        got_gi = func_sha_by_name(self_path, "_gitignore_has_target")
    except Exception as e:
        _emit("V3_gitignore_helper_func_sha", False, f"compute err: {e!r}")
        got_gi = None
    if got_gi is not None:
        if EXPECTED_GITIGNORE_HELPER_FUNC_SHA == "__BUMP_ME__":
            _emit("V3_gitignore_helper_func_sha", False,
                  f"placeholder; bump EXPECTED_GITIGNORE_HELPER_FUNC_SHA={got_gi}")
        else:
            _emit(
                "V3_gitignore_helper_func_sha",
                got_gi == EXPECTED_GITIGNORE_HELPER_FUNC_SHA,
                f"got={got_gi[:16]} expect={EXPECTED_GITIGNORE_HELPER_FUNC_SHA[:16]}",
            )
    try:
        got_fi = func_sha_by_name(self_path, "_file_is_ignored")
    except Exception as e:
        _emit("V3_file_ignored_helper_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_FILE_IGNORED_HELPER_FUNC_SHA == "__BUMP_ME__":
        _emit("V3_file_ignored_helper_func_sha", False,
              f"placeholder; bump EXPECTED_FILE_IGNORED_HELPER_FUNC_SHA={got_fi}")
    else:
        _emit(
            "V3_file_ignored_helper_func_sha",
            got_fi == EXPECTED_FILE_IGNORED_HELPER_FUNC_SHA,
            f"got={got_fi[:16]} expect={EXPECTED_FILE_IGNORED_HELPER_FUNC_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V4: 行为
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # V4.1: .gitignore 精确含 jsonl 路径
    _emit(
        "V4.1_gitignore_has_jsonl",
        _gitignore_has_target(GITIGNORE, JSONL_REL),
        f"expect line '{JSONL_REL}' in .gitignore",
    )

    # V4.2: jsonl 非 tracked
    proc_ls = subprocess.run(
        ["git", "ls-files", "--", JSONL_REL],
        cwd=str(REPO), capture_output=True, text=True, check=False,
    )
    _emit(
        "V4.2_jsonl_not_tracked",
        proc_ls.returncode == 0 and proc_ls.stdout.strip() == "",
        f"ls-files stdout={proc_ls.stdout.strip()!r}",
    )

    # V4.3: check-ignore 命中
    _emit(
        "V4.3_jsonl_check_ignore_hit",
        _file_is_ignored(REPO, JSONL_REL),
        f"git check-ignore -- {JSONL_REL}",
    )

    # V4.4: append 一行后 git status 仍空 (ignored 生效)
    if JSONL.is_file():
        original = JSONL.read_bytes()
    else:
        original = None
        # 父目录存在性保证
        JSONL.parent.mkdir(parents=True, exist_ok=True)
        JSONL.write_text("", encoding="utf-8")
    try:
        sentinel_line = json.dumps({
            "ts": "verify_infra_064_probe", "kind": "smoke",
            "areas": {}, "pass": 0, "fail": 0, "skip": 0, "total": 0,
            "duration_s": 0.0, "git_head": "probe",
        }) + "\n"
        with JSONL.open("a", encoding="utf-8") as f:
            f.write(sentinel_line)
        proc_st = subprocess.run(
            ["git", "status", "--porcelain", "--", JSONL_REL],
            cwd=str(REPO), capture_output=True, text=True, check=False,
        )
        _emit(
            "V4.4_jsonl_append_no_dirty",
            proc_st.returncode == 0 and proc_st.stdout.strip() == "",
            f"status stdout={proc_st.stdout.strip()!r}",
        )
    finally:
        if original is None:
            try:
                JSONL.unlink()
            except FileNotFoundError:
                pass
        else:
            JSONL.write_bytes(original)

    # V4.5: 现存 jsonl schema 不退化
    if JSONL.is_file() and JSONL.stat().st_size > 0:
        bad = []
        n = 0
        with JSONL.open("r", encoding="utf-8") as f:
            for i, raw in enumerate(f, 1):
                raw = raw.strip()
                if not raw:
                    continue
                n += 1
                try:
                    obj = json.loads(raw)
                except Exception as e:
                    bad.append(f"line {i}: {e!r}")
                    continue
                if not isinstance(obj, dict) or "ts" not in obj or "kind" not in obj:
                    bad.append(f"line {i}: missing ts/kind")
        _emit(
            "V4.5_jsonl_schema_ok",
            not bad,
            f"parsed={n} bad={bad[:3]}",
        )
    else:
        _emit(
            "V4.5_jsonl_schema_ok",
            True,
            "jsonl absent or empty (skip schema check)",
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
    v2_file_shas()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if "--json" in sys.argv:
        print(json.dumps({"results": [(t, ok, d) for t, ok, d in _results]}, indent=2))
    if failed:
        print(f"[verify_infra_064][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_064][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
