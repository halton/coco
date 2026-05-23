#!/usr/bin/env python3
"""verify_infra_070 V0-V6: closeout evidence tail_stdout sha 行为锁.

infra-P294-closeout-stdout-sha-verification (phase-X #N):
P278 verify_closeout_evidence_trustworthy 已要求 evidence.verify_runs[].tail_stdout
非空, 但不保证该 tail_stdout 在 main HEAD 实跑后能复现 — Engineer 可手贴一段
PASS 文本进 evidence, P278 字段校验过 = 实跑伪造无成本。本 feature 引入
``_verify_lib.verify_evidence_tail_stdout_sha``: 在 main_head_sha 起 git
worktree 隔离运行每个 evidence 标注的 verify 脚本, 取 stdout 末尾 N 字符
sha256, 与 ``evidence.verify_runs[i].tail_stdout_sha256`` 字段比对, 提高
closeout-verify-trustworthy 的反伪造强度。

INFRA_070_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` 全文件 sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``verify_evidence_tail_stdout_sha`` canonical func sha: EXPECTED_HELPER_FUNC_SHA
- 本脚本 main() 自锁 func sha: EXPECTED_SELF_MAIN_FUNC_SHA
- ``_make_mini_repo`` func sha + 源码 GIT_*='' 清零行 (phase-55 #5.55 P294 followup):
  EXPECTED_MAKE_MINI_REPO_FUNC_SHA

校验层级 (V0-V6):

- V0 scaffolding: 常量存在且 hex64
- V1 self func sha: 本脚本 main() canonical ast.unparse sha 自锁
- V2 _verify_lib file sha
- V3 helper func sha (verify_evidence_tail_stdout_sha) canonical ast.unparse sha
- V4 业务实跑 (用 tempfile + git init 当 mini repo, 合成 verify 脚本):
  - V4_1 ok_true: 正确 sha → ok=True checked>=1 matched==checked offending==[]
  - V4_2 mutant_sha_mismatch: 改一字符 → ok=False offending 含 run_name
  - V4_3 missing_sha_field: evidence 无 tail_stdout_sha256 → ok=False missing_fields 非空
  - V4_4 empty_runs: verify_runs=[] → ok=False error 非空 (reason: empty)
  - V4_5 invalid_main_head_sha: 假 sha "0"*40 → ok=False error 非空
- V5 Reviewer LGTM gate (closeout 须 fresh-context Reviewer LGTM)
- V6 _make_mini_repo env isolation: func sha 锁 + 源码确认 GIT_DIR/GIT_WORK_TREE/
  GIT_INDEX_FILE 三个 env var 在 helper 内被显式 pop 剥离 (非 "" 置空 — git 把
  空串当 invalid path), 防止 CI 嵌套 git worktree 串台

退出码: 0=ALL PASS, 2=任一 FAIL (走 verify_summary_exit)。

运行环境约定 (infra-034): 必须在 .venv 下运行。
"""
from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P294-closeout-stdout-sha-verification"
LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_evidence_tail_stdout_sha,
    verify_summary_exit,
    assert_v5_reviewer_gate_evidence_bind,
)

EXPECTED_VERIFY_LIB_FILE_SHA = "f789e0d870c9e6c3764b893a6bcbcca356bfc464520d9c49f3d4b17045ecd243"
EXPECTED_HELPER_FUNC_SHA = "9e69bab11675bc2c30055225614afc436362a5e6e4ea4671912ef564dfc00946"
EXPECTED_SELF_MAIN_FUNC_SHA = "71eaf71197a68abf51135fba30ad602a93ec8b2e4d03a87b7cfab3565ab752ba"
# phase-55 #5.55: lock _make_mini_repo func sha so env isolation lines can't silently drift.
EXPECTED_MAKE_MINI_REPO_FUNC_SHA = "ffa946cdcd37086be7e27905afbd56a0c6e674974be8012e1fb2ea4ebf33852b"

DOCSTRING_SENTINEL = "INFRA_070_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_070][{mark}] {tag} {detail}", flush=True)
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
    # P294-followup: helper docstring 必须明确 tail 长度单位是 chars (codepoints), not bytes.
    # 关键词锁: 'chars', 'codepoints', 'NOT raw bytes' 三者全在 helper docstring 中出现.
    try:
        lib_tree = ast.parse(LIB.read_text(encoding="utf-8"))
        helper_doc = ""
        for node in ast.walk(lib_tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "verify_evidence_tail_stdout_sha"
            ):
                helper_doc = ast.get_docstring(node) or ""
                break
    except Exception as e:
        helper_doc = ""
        _emit("V0_helper_docstring_loaded", False, f"err: {e!r}")
    else:
        _emit("V0_helper_docstring_loaded", bool(helper_doc), f"len={len(helper_doc)}")
    required_keywords = ("chars", "codepoints", "NOT raw bytes")
    missing_kw = [k for k in required_keywords if k not in helper_doc]
    _emit(
        "V0_helper_docstring_chars_vs_bytes_keywords",
        not missing_kw,
        f"required={required_keywords} missing={missing_kw}",
    )


# ---------------------------------------------------------------------------
# V1: self main() func sha lock
# ---------------------------------------------------------------------------
def v1_self_func_sha() -> None:
    try:
        got = func_sha_by_name(Path(__file__), "main")
    except Exception as e:
        _emit("V1_self_main_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        # placeholder OK during initial bring-up; emit PASS with bump hint
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
# V3: helper func sha (verify_evidence_tail_stdout_sha)
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "verify_evidence_tail_stdout_sha")
    except Exception as e:
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_HELPER_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_HELPER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 业务实跑 (合成 evidence + mini git repo + 假 verify script)
# ---------------------------------------------------------------------------
_FAKE_VERIFY_SRC = 'print("LINE1\\nLINE2\\nLINE3")\n'
# 计算 stdout = "LINE1\nLINE2\nLINE3\n" (print 末尾自带 \n)
_FAKE_STDOUT_FULL = "LINE1\nLINE2\nLINE3\n"
_FAKE_TAIL = _FAKE_STDOUT_FULL  # 取全长作 tail
_FAKE_TAIL_SHA = hashlib.sha256(_FAKE_TAIL.encode("utf-8")).hexdigest()


def _make_mini_repo(tmpd: Path) -> Tuple[Path, str]:
    """Init mini git repo with one scripts/verify_fake.py committed; return (repo_root, head_sha)."""
    repo = tmpd / "mini_repo"
    repo.mkdir()
    scripts = repo / "scripts"
    scripts.mkdir()
    (scripts / "verify_fake.py").write_text(_FAKE_VERIFY_SRC, encoding="utf-8")
    # P294 Reviewer P2 (followup #5.55): 显式剥离父进程的 GIT_DIR/GIT_WORK_TREE/
    # GIT_INDEX_FILE 等 env var, 避免 CI 嵌套 git worktree 场景串台污染 mini repo.
    # 注: 必须 pop 而非置空 "" — git 会把 GIT_DIR="" 当成 invalid path 直接 fatal.
    _parent_env = dict(__import__("os").environ)
    for _k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        _parent_env.pop(_k, None)
    env = {
        **_parent_env,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repo), check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=str(repo), check=True, env=env,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo), check=True, capture_output=True, text=True, env=env,
    ).stdout.strip()
    return repo, head


def v4_behavior() -> None:
    with tempfile.TemporaryDirectory(prefix="coco_p294_v070_") as td_str:
        tmpd = Path(td_str)
        try:
            repo, head_sha = _make_mini_repo(tmpd)
        except Exception as e:
            _emit("V4_setup_mini_repo", False, f"setup err: {e!r}")
            return
        _emit(
            "V4_setup_mini_repo",
            True,
            f"head_sha={head_sha[:12]} repo={repo}",
        )

        # V4_1: 正确 sha → ok=True
        ev_ok = {
            "verify_runs": [
                {
                    "script": "scripts/verify_fake.py",
                    "tail_stdout": _FAKE_TAIL,
                    "tail_stdout_sha256": _FAKE_TAIL_SHA,
                }
            ]
        }
        res1 = verify_evidence_tail_stdout_sha(ev_ok, repo, head_sha)
        _emit(
            "V4_1_ok_true",
            res1.get("ok") is True
            and res1.get("checked", 0) >= 1
            and res1.get("matched") == res1.get("checked")
            and res1.get("offending") == [],
            f"res={ {k: res1.get(k) for k in ('ok','checked','matched','offending','error')} }",
        )

        # V4_2: 改一字符 sha → ok=False, offending 含 script
        bad_sha = ("f" if _FAKE_TAIL_SHA[0] != "f" else "0") + _FAKE_TAIL_SHA[1:]
        ev_bad = {
            "verify_runs": [
                {
                    "script": "scripts/verify_fake.py",
                    "tail_stdout": _FAKE_TAIL,
                    "tail_stdout_sha256": bad_sha,
                }
            ]
        }
        res2 = verify_evidence_tail_stdout_sha(ev_bad, repo, head_sha)
        off_scripts = [o.get("script") for o in (res2.get("offending") or [])]
        _emit(
            "V4_2_mutant_sha_mismatch",
            res2.get("ok") is False
            and "scripts/verify_fake.py" in off_scripts
            and res2.get("checked", 0) >= 1
            and res2.get("matched", 99) == 0,
            f"ok={res2.get('ok')} off_scripts={off_scripts} matched={res2.get('matched')}",
        )

        # V4_3: 缺 tail_stdout_sha256 → missing_fields 非空, ok=False
        ev_missing = {
            "verify_runs": [
                {
                    "script": "scripts/verify_fake.py",
                    "tail_stdout": _FAKE_TAIL,
                    # tail_stdout_sha256 缺失
                }
            ]
        }
        res3 = verify_evidence_tail_stdout_sha(ev_missing, repo, head_sha)
        _emit(
            "V4_3_missing_sha_field",
            res3.get("ok") is False and bool(res3.get("missing_fields")),
            f"ok={res3.get('ok')} missing_fields={res3.get('missing_fields')}",
        )

        # V4_4: 空 verify_runs → helper 返回 ok=False + error 提示
        ev_empty = {"verify_runs": []}
        res4 = verify_evidence_tail_stdout_sha(ev_empty, repo, head_sha)
        _emit(
            "V4_4_empty_runs",
            res4.get("ok") is False
            and isinstance(res4.get("error"), str)
            and "verify_runs" in (res4.get("error") or ""),
            f"ok={res4.get('ok')} error={res4.get('error')!r}",
        )

        # V4_5: 假 sha "0"*40 不可解析 → error 非空, ok=False
        res5 = verify_evidence_tail_stdout_sha(ev_ok, repo, "0" * 40)
        _emit(
            "V4_5_invalid_main_head_sha",
            res5.get("ok") is False
            and isinstance(res5.get("error"), str)
            and bool(res5.get("error")),
            f"ok={res5.get('ok')} error={res5.get('error')!r}",
        )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (placeholder)
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    """V5 Reviewer LGTM gate — phase-47 #1.47 graduate to evidence-bind helper."""
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


# ---------------------------------------------------------------------------
# V6: _make_mini_repo env isolation lock (phase-55 #5.55, P294 followup)
# ---------------------------------------------------------------------------
def v6_make_mini_repo_env_isolation() -> None:
    """Lock _make_mini_repo func sha + assert source clears GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE."""
    try:
        got = func_sha_by_name(Path(__file__), "_make_mini_repo")
    except Exception as e:
        _emit("V6_make_mini_repo_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_MAKE_MINI_REPO_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V6_make_mini_repo_func_sha",
            True,
            f"placeholder OK; bump EXPECTED_MAKE_MINI_REPO_FUNC_SHA={got}",
        )
    else:
        _emit(
            "V6_make_mini_repo_func_sha",
            got == EXPECTED_MAKE_MINI_REPO_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_MAKE_MINI_REPO_FUNC_SHA[:16]}",
        )
    # source-text assert: all three GIT_* env clears present in the helper body.
    try:
        src = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        helper_src = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_make_mini_repo":
                helper_src = ast.get_source_segment(src, node) or ""
                break
    except Exception as e:
        _emit("V6_env_isolation_source_keys", False, f"parse err: {e!r}")
        return
    missing = [
        k for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
        if f'"{k}"' not in helper_src
    ]
    # 同时确认有 .pop( 调用 (而非 "" 置空 — git 把空串当 invalid path).
    pop_present = ".pop(" in helper_src
    _emit(
        "V6_env_isolation_source_keys",
        (not missing) and pop_present,
        f"missing={missing} pop_present={pop_present}",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_func_sha()
    v2_verify_lib_file_sha()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    v6_make_mini_repo_env_isolation()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    failed_tags = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_070][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_070][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0  # unreachable; verify_summary_exit calls sys.exit


if __name__ == "__main__":
    sys.exit(main())
