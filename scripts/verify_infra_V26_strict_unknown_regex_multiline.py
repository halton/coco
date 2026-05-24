#!/usr/bin/env python3
"""verify_infra_V26_strict_unknown_regex_multiline: bump_strict_unknown_sha
``_RE_*`` regex flag meta-lock.

infra-V26-strict-unknown-regex-multiline-meta-lock (phase-67 入账):
``scripts/bump_strict_unknown_sha.py`` 用两个模块级 ``_RE_*`` 正则匹配
verify_infra_V6_strict_area 中 ``_STRICT_UNKNOWN_EXPECTED_SHA_TUPLE`` /
``_STRICT_UNKNOWN_EXPECTED_COUNT`` 的模块级主行赋值。**两条 ``_RE_*`` 都必须
带 ``re.MULTILINE`` flag** —— 否则 ``^`` 行首锚点退化为仅匹配文件开头, ``.subn``
会扫到非主行赋值或漏匹配, 安全锁形同虚设。本 verify 机械化检查所有
``_RE_*`` 赋值都带 ``re.MULTILINE``, 避免未来重构无意丢 flag。

校验层级 (V0-V_last):

- V0 scaffolding: self_sha + target file 存在 + 常量类型对
- V1 _RE_compile_discover: AST 解析目标文件, 发现所有模块级 ``_RE_*`` 名字
  匹配 ``re.compile(...)`` 调用, ``count >= 2`` (当前实际 2 条)
- V2 all_re_have_multiline: 每个 ``_RE_*`` call 必须显式含 ``re.MULTILINE``
  flag (作为 keyword ``flags=re.MULTILINE`` 或第二个位置参数 ``re.compile(p,
  re.MULTILINE)``)
- V3 mutant: 临时把 source 中某一条 ``re.MULTILINE`` 替换成 ``0``, AST 重解,
  V2 应 FAIL (反证锁真生效)
- V4 bump_strict_unknown_file_sha: 目标文件 file sha 锁
- V_last reviewer LGTM gate

退出码: 0=ALL PASS, 2=任一 FAIL (走 ``verify_summary_exit``).

## Lock
- type: regex_flag_ast + file_sha + mutant + reviewer_lgtm
- target: scripts/bump_strict_unknown_sha.py
- locked_property: 模块级所有 ``_RE_*`` 赋值的 ``re.compile`` 调用必须含
  ``re.MULTILINE`` flag
- bump_when: bump_strict_unknown_sha.py 改任意行 (file sha 漂)
- bump_protocol: 改完 bump_strict_unknown_sha.py 后, 手动 recompute
  sha256 并更新本脚本 ``EXPECTED_BUMP_STRICT_UNKNOWN_FILE_SHA``。
  本脚本自身 sha 走 V8-SELF-SHA-SKIP 单行 pragma (用
  ``scripts/bump_self_file_sha.py --target scripts/verify_infra_V26_strict_unknown_regex_multiline.py``
  bump)。

运行环境约定 (infra-034): 必须在 .venv 下运行.
"""
from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
TARGET_FILE = SCRIPTS / "bump_strict_unknown_sha.py"
FEATURE_LIST = REPO / "feature_list.json"
V_LAST_GATE_FEATURE_ID = "infra-V26-strict-unknown-regex-multiline-meta-lock"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_reviewer_lgtm,
    verify_summary_exit,
)

# 仅 EXPECTED_SELF_FILE_SHA 真常量行打 pragma; 其余字节(含 docstring / 注释 /
# import / 函数体内字符串字面)若有 "EXPECTED_SELF_FILE_SHA = " 子串都会破规约.
EXPECTED_SELF_FILE_SHA = "4d79c352f5d184c3659da94a0b349fae2cc01c226ddec2f5a9c2f6bf92db0d9d"  # V8-SELF-SHA-SKIP

# V4: bump_strict_unknown_sha.py file sha 锁
EXPECTED_BUMP_STRICT_UNKNOWN_FILE_SHA = (
    "af11874d2d35b99b546d02cf2c09910dad7a6ac3b8b6a32c9232515f1946f919"
)

# V1/V2: _RE_* 名字前缀
RE_NAME_PREFIX = "_RE_"
MIN_RE_COUNT = 2  # 当前 _RE_SHA_TUPLE + _RE_COUNT

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_V26_strict_unknown_regex_multiline][{mark}] {tag} {detail}",
          flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{64}", s))


def _self_sha_skip_sentinel() -> str:
    """计算本 verify 自身 sha (剔除唯一一条 # V8-SELF-SHA-SKIP pragma 行后)."""
    self_path = Path(__file__)
    lines = self_path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [
        ln for ln in lines
        if not ln.rstrip("\r\n").endswith("# V8-SELF-SHA-SKIP")
    ]
    body = "".join(kept).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_repo_root_exists", REPO.is_dir(), f"path={REPO}")
    _emit("V0_target_file_exists", TARGET_FILE.is_file(), f"path={TARGET_FILE}")
    _emit(
        "V0_const_bump_sha_hex64",
        _is_hex64(EXPECTED_BUMP_STRICT_UNKNOWN_FILE_SHA),
        f"val={EXPECTED_BUMP_STRICT_UNKNOWN_FILE_SHA[:16]}",
    )
    _emit(
        "V0_min_re_count_positive",
        MIN_RE_COUNT >= 1,
        f"min={MIN_RE_COUNT}",
    )
    # V0 self-sha lock (V8-SELF-SHA-SKIP)
    got = _self_sha_skip_sentinel()
    _emit(
        "V0_self_sha",
        got == EXPECTED_SELF_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_SELF_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# 共享: 提取目标文件中所有 _RE_* = re.compile(...) 模块级赋值, 返回 ast.Call list
# ---------------------------------------------------------------------------
def _extract_re_compile_calls(src: str) -> List[Tuple[str, ast.Call]]:
    """返回 [(name, Call_node), ...] 对应模块级 ``_RE_xxx = re.compile(...)``."""
    tree = ast.parse(src)
    out: List[Tuple[str, ast.Call]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name):
            continue
        name = tgt.id
        if not name.startswith(RE_NAME_PREFIX):
            continue
        val = node.value
        if not isinstance(val, ast.Call):
            continue
        # 调用形如 re.compile(...) — func 是 Attribute 且 attr=='compile'
        fn = val.func
        if not isinstance(fn, ast.Attribute):
            continue
        if fn.attr != "compile":
            continue
        out.append((name, val))
    return out


def _call_has_multiline_flag(call: ast.Call) -> bool:
    """检查 ast.Call 是否含 ``re.MULTILINE`` flag (位置或 keyword).

    覆盖形态:
      - re.compile(p, re.MULTILINE)
      - re.compile(p, flags=re.MULTILINE)
      - re.compile(p, re.MULTILINE | re.IGNORECASE)
      - re.compile(p, flags=re.MULTILINE | re.IGNORECASE)
    """
    def _walk_has(node: ast.AST) -> bool:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and sub.attr == "MULTILINE":
                if isinstance(sub.value, ast.Name) and sub.value.id == "re":
                    return True
        return False

    # 第二个起的位置参数
    for arg in call.args[1:]:
        if _walk_has(arg):
            return True
    # keyword flags=...
    for kw in call.keywords:
        if kw.arg == "flags" and _walk_has(kw.value):
            return True
    return False


# ---------------------------------------------------------------------------
# V1: discover _RE_* count
# ---------------------------------------------------------------------------
def v1_re_compile_discover() -> None:
    try:
        src = TARGET_FILE.read_text(encoding="utf-8")
    except Exception as e:
        _emit("V1_target_loaded", False, f"err: {e!r}")
        return
    _emit("V1_target_loaded", bool(src), f"bytes={len(src)}")
    try:
        calls = _extract_re_compile_calls(src)
    except SyntaxError as e:
        _emit("V1_ast_parse_ok", False, f"err: {e!r}")
        return
    _emit("V1_ast_parse_ok", True, f"re_count={len(calls)}")
    names = [n for n, _ in calls]
    _emit(
        "V1_re_count_meets_minimum",
        len(calls) >= MIN_RE_COUNT,
        f"names={names} got={len(calls)} expect>={MIN_RE_COUNT}",
    )


# ---------------------------------------------------------------------------
# V2: all _RE_* call must contain re.MULTILINE
# ---------------------------------------------------------------------------
def _check_all_have_multiline(src: str) -> Tuple[bool, List[str], List[str]]:
    calls = _extract_re_compile_calls(src)
    names = [n for n, _ in calls]
    missing = [n for n, c in calls if not _call_has_multiline_flag(c)]
    return (not missing and len(calls) >= MIN_RE_COUNT), names, missing


def v2_all_re_have_multiline() -> None:
    try:
        src = TARGET_FILE.read_text(encoding="utf-8")
    except Exception as e:
        _emit("V2_all_re_have_multiline", False, f"err: {e!r}")
        return
    ok, names, missing = _check_all_have_multiline(src)
    _emit(
        "V2_all_re_have_multiline",
        ok,
        f"names={names} missing={missing}",
    )


# ---------------------------------------------------------------------------
# V3: mutant 反证 (in-memory 替换某条 re.MULTILINE → 0, V2 应 FAIL)
# ---------------------------------------------------------------------------
def v3_mutant_negative() -> None:
    try:
        src = TARGET_FILE.read_text(encoding="utf-8")
    except Exception as e:
        _emit("V3_mutant_negative", False, f"err: {e!r}")
        return
    # 替换第一次出现的"独占整行的 ``re.MULTILINE,`` 位置参数"
    # (锚点为 ``\n<4-space-indent>re.MULTILINE,``, 跳过注释/docstring 中裸描述);
    # 必须不破坏 syntax (替成 ``0,`` 仍是合法 flags 位置参数)
    needle = "\n    re.MULTILINE,"
    repl = "\n    0,"
    if needle not in src:
        _emit("V3_mutant_negative", False, f"{needle!r} not found in source")
        return
    mutated = src.replace(needle, repl, 1)
    try:
        ok_after, names, missing = _check_all_have_multiline(mutated)
    except SyntaxError as e:
        _emit("V3_mutant_negative", False, f"mutated source parse err: {e!r}")
        return
    # 期望 mutant 下 V2 FAIL (即 ok_after == False, 且 missing 非空)
    _emit(
        "V3_mutant_detected_missing_flag",
        (not ok_after) and bool(missing),
        f"ok_after_mutate={ok_after} missing_after={missing}",
    )


# ---------------------------------------------------------------------------
# V4: bump_strict_unknown_sha.py file sha lock
# ---------------------------------------------------------------------------
def v4_bump_strict_unknown_file_sha() -> None:
    got = _file_sha(TARGET_FILE)
    _emit(
        "V4_bump_strict_unknown_file_sha",
        got == EXPECTED_BUMP_STRICT_UNKNOWN_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_BUMP_STRICT_UNKNOWN_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V_last: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v_last_reviewer_lgtm_gate() -> None:
    try:
        ok, reason = assert_reviewer_lgtm(
            V_LAST_GATE_FEATURE_ID,
            FEATURE_LIST,
        )
    except Exception as e:
        _emit("V_last_reviewer_lgtm_gate", False, f"helper err: {e!r}")
        return
    _emit(
        "V_last_reviewer_lgtm_gate",
        ok,
        f"target={V_LAST_GATE_FEATURE_ID} reason={reason!r}",
    )


def main() -> int:
    v0_scaffolding()
    v1_re_compile_discover()
    v2_all_re_have_multiline()
    v3_mutant_negative()
    v4_bump_strict_unknown_file_sha()
    v_last_reviewer_lgtm_gate()
    failed = sum(1 for _, ok, _ in _results if not ok)
    verify_summary_exit(failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
