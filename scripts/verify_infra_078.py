#!/usr/bin/env python3
"""verify_infra_078 V0-V5: 防止 verify_infra_*.py 出现 EXPECTED_*_SHA 占位.

infra-P286-followup-074-self-main-func-sha-bump (phase-40 #4.40):
此前 verify_infra_070 与 verify_infra_074 的 EXPECTED_SELF_MAIN_FUNC_SHA 长期
保持为占位字符串 (placeholder), 导致 V1_self_main_func_sha check 实际被绕过
(走 placeholder 分支即 emit PASS, 而非真值校验). P286 followup 把这两个常量
收口为真算值; 本 verify 用 V4_1..V4_5 行为校验长期保证: 任何
verify_infra_*.py 中 EXPECTED_*_SHA 名字的常量都不得回退为占位字符串。

INFRA_078_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` 全文件 sha: EXPECTED_VERIFY_LIB_FILE_SHA
- 本脚本 main() 自锁 func sha: EXPECTED_SELF_MAIN_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding (×5): scripts/ 存在; _verify_lib 存在; 070 存在; 074 存在;
  scan 函数可调用
- V1 self main() canonical ast.unparse sha 自锁
- V2 _verify_lib.py 文件 sha 锁
- V3 (omitted, 本 feature 不引入 lib helper)
- V4 行为校验:
  - V4_1 scan_all_verify_for_placeholder: ast 扫所有 scripts/verify_infra_*.py,
    遍历顶层 Assign / AnnAssign, 名字匹配 EXPECTED_.*_SHA, value 为字符串
    占位 (PLACEHOLDER_SENTINEL) → 任一命中即 FAIL (offending 列表)
  - V4_2 070_self_main_func_sha_real: 真读 verify_infra_070.py, 取出
    EXPECTED_SELF_MAIN_FUNC_SHA, 断言 == 当下 ast.unparse(main).sha256
  - V4_3 074_self_main_func_sha_real: 同 V4_2, 对 verify_infra_074.py
  - V4_4 git_grep_no_placeholder_assignment: subprocess git grep 占位 字面 在
    所有 verify_infra_*.py 中, 解析每行, 断言不存在 `EXPECTED_*_SHA =
    "<PLACEHOLDER>"` 形式赋值 (允许 docstring / 条件比较等其他出现)
  - V4_5 synthetic_placeholder_hit: tmp 写一个 fake verify_infra_999.py 含
    EXPECTED_FOO_SHA = PLACEHOLDER, 调 V4_1 同款扫描函数应命中
- V5 Reviewer LGTM gate (helper soft-PASS, closeout 前 reviewer 字段未填)

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
from typing import Iterable, List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "_verify_lib.py"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P286-followup-074-self-main-func-sha-bump"

# 占位 sentinel —— 本仓库历史 placeholder 字符串。
#
# # noqa: PLACEHOLDER_SELF_EXEMPT
# ----------------------------------
# 这里**故意**用 `"__BUMP" + "_ME__"` split 拼接, 让 V4_1 扫描自身常量声明时
# value 是 BinOp (不是 ast.Constant(str)), 从而 V4_1 不会把本文件自身误判为
# placeholder 回归; 同时 V4_4 git grep 占位字面 `__BUMP_ME__` 也不会在本源码
# 行命中. 后续维护者: **严禁**把这一行改写成字面 `"__BUMP_ME__"`, 否则
# verify_infra_078 自身会立刻把自己扫成 placeholder 命中 (V4_1 FAIL) 并/或
# V4_4 git grep 命中赋值. 这是 sentinel self-exempt 的硬约束。
PLACEHOLDER_SENTINEL = "__BUMP" + "_ME__"  # noqa: PLACEHOLDER_SELF_EXEMPT — 严禁字面化为 "__BUMP_ME__"
EXPECTED_CONST_RE = re.compile(r"^EXPECTED_.*_SHA$")

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (
    assert_reviewer_lgtm,
    func_sha_by_name,
    verify_summary_exit,
    assert_v5_reviewer_gate_evidence_bind,
)

EXPECTED_VERIFY_LIB_FILE_SHA = "f7248f548eab36eab74ff678aa84e567928b5fc02e9a085f9289539aaef1ee99"
EXPECTED_SELF_MAIN_FUNC_SHA = "a762cfb89365f04b56d48f8be0af18d15ed667a77e78284e0c6536bf04ad743d"

DOCSTRING_SENTINEL = "INFRA_078_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_078][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


# ---------------------------------------------------------------------------
# Core scan helper (shared by V4_1, V4_5)
# ---------------------------------------------------------------------------
def scan_file_for_placeholder_assignments(path: Path) -> List[Tuple[str, int]]:
    """Return [(const_name, lineno), ...] 列表, 表示 path 中名字匹配
    EXPECTED_*_SHA 的顶层常量赋值, 且其 value 为字面 PLACEHOLDER_SENTINEL.

    仅看顶层 Assign / AnnAssign 节点 (排除函数 / 类内部), 仅看 value 是
    ``ast.Constant(str)`` 的情况; if/比较 / docstring 中出现的字符串不计入。

    **Sentinel self-exempt 硬规则 (P286 followup4)**:
    本仓库内任何使用 PLACEHOLDER_SENTINEL 的源文件 (尤其是本 verify 自身),
    **必须**保持 sentinel 在源码中以拼接形式存在 (例如 ``"__BUMP" + "_ME__"``),
    **严禁**字面化为单个字符串常量 ``"__BUMP_ME__"``. 字面化将导致:

    1. 本扫描函数把该文件自身的 sentinel 常量识别为 placeholder 命中
       (V4_1 FAIL, offending 包含本文件);
    2. V4_4 git grep 占位字面 `__BUMP_ME__` 会在源码里命中赋值正则,
       立刻 FAIL.

    含 sentinel 拼接的位置请加 ``# noqa: PLACEHOLDER_SELF_EXEMPT`` 行内注释
    显式声明 self-exempt 意图, 防止后续维护误改。
    """
    out: List[Tuple[str, int]] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return out
    for node in tree.body:
        targets: List[str] = []
        value = None
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    targets.append(t.id)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets.append(node.target.id)
            value = node.value
        else:
            continue
        if value is None:
            continue
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        if value.value != PLACEHOLDER_SENTINEL:
            continue
        for name in targets:
            if EXPECTED_CONST_RE.match(name):
                out.append((name, node.lineno))
    return out


def scan_paths_for_placeholder_assignments(paths: Iterable[Path]) -> List[Tuple[str, str, int]]:
    """返回 (rel_path, const, lineno) 三元组列表."""
    out: List[Tuple[str, str, int]] = []
    for p in paths:
        for const, lineno in scan_file_for_placeholder_assignments(p):
            out.append((str(p.relative_to(REPO)), const, lineno))
    return out


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_scripts_dir_exists", SCRIPTS.is_dir(), f"path={SCRIPTS}")
    _emit("V0_lib_exists", LIB.is_file(), f"path={LIB}")
    _emit(
        "V0_verify_070_exists",
        (SCRIPTS / "verify_infra_070.py").is_file(),
        "scripts/verify_infra_070.py",
    )
    _emit(
        "V0_verify_074_exists",
        (SCRIPTS / "verify_infra_074.py").is_file(),
        "scripts/verify_infra_074.py",
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
    if EXPECTED_SELF_MAIN_FUNC_SHA == PLACEHOLDER_SENTINEL:
        _emit(
            "V1_self_main_func_sha",
            False,
            f"placeholder sentinel — bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
        return
    if not _is_hex64(EXPECTED_SELF_MAIN_FUNC_SHA):
        _emit(
            "V1_self_main_func_sha",
            False,
            f"EXPECTED_SELF_MAIN_FUNC_SHA not 64-hex (got "
            f"{EXPECTED_SELF_MAIN_FUNC_SHA!r}); actual main sha={got}",
        )
        return
    _emit(
        "V1_self_main_func_sha",
        got == EXPECTED_SELF_MAIN_FUNC_SHA,
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
# V4: 行为校验
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    all_verifies = sorted(SCRIPTS.glob("verify_infra_*.py"))

    # V4_1: 扫所有 verify_infra_*.py, 任何 EXPECTED_*_SHA = PLACEHOLDER 都视作回归
    offending = scan_paths_for_placeholder_assignments(all_verifies)
    _emit(
        "V4_1_scan_all_verify_for_placeholder",
        not offending,
        f"scanned={len(all_verifies)} offending={offending}",
    )

    # V4_2: 070 EXPECTED_SELF_MAIN_FUNC_SHA == ast.unparse(main).sha256
    p070 = SCRIPTS / "verify_infra_070.py"
    try:
        const_val_070 = _extract_const_value(p070, "EXPECTED_SELF_MAIN_FUNC_SHA")
        main_sha_070 = func_sha_by_name(p070, "main")
    except Exception as e:  # noqa: BLE001
        _emit("V4_2_070_self_main_func_sha_real", False, f"error={e!r}")
    else:
        ok = (
            isinstance(const_val_070, str)
            and _is_hex64(const_val_070)
            and const_val_070 == main_sha_070
        )
        _emit(
            "V4_2_070_self_main_func_sha_real",
            ok,
            f"const={const_val_070!r} computed={main_sha_070}",
        )

    # V4_3: 074 同上
    p074 = SCRIPTS / "verify_infra_074.py"
    try:
        const_val_074 = _extract_const_value(p074, "EXPECTED_SELF_MAIN_FUNC_SHA")
        main_sha_074 = func_sha_by_name(p074, "main")
    except Exception as e:  # noqa: BLE001
        _emit("V4_3_074_self_main_func_sha_real", False, f"error={e!r}")
    else:
        ok = (
            isinstance(const_val_074, str)
            and _is_hex64(const_val_074)
            and const_val_074 == main_sha_074
        )
        _emit(
            "V4_3_074_self_main_func_sha_real",
            ok,
            f"const={const_val_074!r} computed={main_sha_074}",
        )

    # V4_4: git grep 占位字面, 解析行, 断言不存在 `EXPECTED_*_SHA = "<PLACEHOLDER>"`
    # 形式的赋值. 比较 / docstring 中的占位字符串允许出现 (这里只 ban 真赋值).
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO), "grep", "-n", PLACEHOLDER_SENTINEL,
             "--", "scripts/verify_infra_*.py"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:  # noqa: BLE001
        _emit("V4_4_git_grep_no_placeholder_assignment", False, f"error={e!r}")
    else:
        bad_lines: List[str] = []
        assign_re = re.compile(
            r":(?P<line>\d+):\s*EXPECTED_[A-Z0-9_]*_SHA\s*(?::\s*\w+\s*)?=\s*"
            r"['\"]" + re.escape(PLACEHOLDER_SENTINEL) + r"['\"]"
        )
        for raw in proc.stdout.splitlines():
            # 格式: <path>:<lineno>:<text>
            if assign_re.search(raw):
                bad_lines.append(raw)
        _emit(
            "V4_4_git_grep_no_placeholder_assignment",
            not bad_lines,
            f"bad_lines={bad_lines}",
        )

    # V4_5: 合成 fake verify 文件含占位赋值 → 扫描应命中
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        fake_repo_scripts = tmpdir / "scripts"
        fake_repo_scripts.mkdir(parents=True)
        fake = fake_repo_scripts / "verify_infra_999.py"
        # noqa: PLACEHOLDER_SELF_EXEMPT — fake 文件内容**故意**用运行时拼接构造
        # placeholder 赋值字面, 源码不出现 __BUMP_ME__ 字面 (避开 V4_4 git grep
        # 与本扫描函数的 self 命中); fake 文件本身写到 tmpdir 且不在 scripts/,
        # 不会被 V4_1 真正扫描序列纳入, 安全。
        fake.write_text(
            "EXPECTED_FOO_SHA = \"" + PLACEHOLDER_SENTINEL + "\"\n"
            "EXPECTED_BAR_SHA: str = \"" + PLACEHOLDER_SENTINEL + "\"\n",
            encoding="utf-8",
        )
        hits = scan_file_for_placeholder_assignments(fake)
        names = sorted(name for name, _ln in hits)
        _emit(
            "V4_5_synthetic_placeholder_hit",
            names == ["EXPECTED_BAR_SHA", "EXPECTED_FOO_SHA"],
            f"hits={hits}",
        )


def _extract_const_value(path: Path, name: str):
    """从 path 顶层取 const 字面值 (str/int). 找不到 → 抛 KeyError."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets: List[str] = []
        value = None
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    targets.append(t.id)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets.append(node.target.id)
            value = node.value
        if name in targets and isinstance(value, ast.Constant):
            return value.value
    raise KeyError(name)


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
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


def main() -> int:
    v0_scaffolding()
    v1_self_func_sha()
    v2_verify_lib_file_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    failed_tags = [t for t, ok, _ in _results if not ok]
    if failed:
        print(
            f"[verify_infra_078][SUMMARY] FAIL {failed}/{total}: {failed_tags}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_078][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
