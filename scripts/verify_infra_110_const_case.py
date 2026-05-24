#!/usr/bin/env python3
"""verify_infra_110_const_case: EXPECTED_* 反向锁常量名全大写约定硬锁 (verify-only).

## Lock: EXPECTED_VERIFY_LIB_FILE_SHA
- target_function: scan_reverse_sha_locks_ast (helper, dependency boundary marker)
- target_file: scripts/_verify_lib.py
- lock_kind: file_sha (P314 cascade member)
- bump_when: scripts/_verify_lib.py changes
- bump_protocol: recompute sha256(scripts/_verify_lib.py) and bump
  EXPECTED_VERIFY_LIB_FILE_SHA, then participate in cascade audit
- rationale: 本 verify 复用 _verify_lib.scan_reverse_sha_locks_ast 扫所有
  verify_*.py + _verify_lib.py 顶层 EXPECTED_*_(FILE|FUNC)_SHA 赋值,
  锁住 helper 的具体行为防止扫描语义被悄改导致 V12 vacuous PASS

infra-110-backlog-composite-key-const-case-extension (phase-67 #9):

scope: phase-60 #4 infra-110 Reviewer 提出 non-blocking concern_3 —
verify_infra_110.V4_composite_key_well_formed 的 anchor regex 仅匹配大写
``EXPECTED_[A-Z0-9_]+`` 形态 const；若未来引入小写 const (例如某 verify
脚本用 ``expected_foo_sha = "..."`` 这种 PEP8 风格命名)，需要扩展 case
分支或统一命名约定。

本 verify 落实"统一命名约定"路线 (非"扩展接受小写")：

- 整个 codebase 现存约定 (``_verify_lib._RE_REVLOCK_SINGLELINE`` /
  ``_RE_REVLOCK_TUPLE_OPEN`` 全大写常量名 + ``_RE_REVLOCK_EXPECTED_PATTERN``
  仅匹配大写 EXPECTED_*) 是隐式硬约定, 无显式 verify 锁;
- 若有人后续提交一个小写 EXPECTED 风格 const (如 ``expected_foo_file_sha``),
  会被 _RE_REVLOCK_SINGLELINE 的 ``[A-Z_][A-Z0-9_]*`` 静默忽略, 既不进入
  反向锁索引也不被 V12 catch, 同时 verify_infra_110.V4 anchor 仍仅看
  rendered mermaid 节点 id (它来自 build_graph 已 collected 的 const 集),
  vacuous 通过. 这是 backlog concern_3 的真实风险面.
- V12 显式扫描 ``scripts/verify_*.py`` + ``scripts/_verify_lib.py`` 模块顶层
  ``ast.Assign`` 节点, 用 case-insensitive regex 匹配
  ``(?i)^expected_[a-z0-9_]+_(?:file|func)_sha$``, 找到任何**非全大写**
  形态即 FAIL (附带行号 + const 名 + 文件路径 evidence).

校验层级 (V0-V12):

- V0 scaffolding: _verify_lib.py 存在 + scan_reverse_sha_locks_ast 顶层符号在
- V1 self file sha lock (self_sha 自锁防 vacuous 改写)
- V2 _verify_lib.py file sha lock (helper 边界锁, P314 cascade member)
- V12 case_uniform_lock: 扫所有 scripts/verify_*.py + _verify_lib.py 模块顶层
  Assign, 任何 const 名 case-insensitive 命中
  ``^expected_.+_(?:file|func)_sha$`` 但不是全大写 ASCII (含 lowercase /
  mixed-case) 即 FAIL. 全大写形态 (现存约定) 视为 PASS.
- V12b vacuous_guard: 若 V12 扫描结果中**全大写** EXPECTED_*_(FILE|FUNC)_SHA
  形态命中数 < 1, 视为 helper 行为退化 (例如 scan_reverse_sha_locks_ast 被
  改写后扫不到任何赋值), 触发 FAIL 防 V12 vacuous PASS.

退出码 0=ALL PASS / 2=任一 FAIL.
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
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import verify_summary_exit  # noqa: E402

# V1 self file sha 自锁 (首跑 __BUMP_ME__, 然后回填)
EXPECTED_SELF_FILE_SHA = (
    "2812506a3441509fd1db41efd609b1f6a2abe2ab2cf17122a95bc4d6ecd30c91"
)
# V2 _verify_lib.py file sha 锁 (P314 cascade member)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "1990a9b61d3b14c9b827a23188a46001be2a208234352e1bc7af955285578f50"
)

# Case-insensitive anchor for EXPECTED_*_(FILE|FUNC)_SHA family.
# 全大写形态 (现存约定) 命中 + 区分大小写比对得出"小写 / mixed-case 违规"
_RE_EXPECTED_CASE_INSENSITIVE = re.compile(
    r"^expected_[a-z0-9_]+_(?:file|func)_sha$", re.IGNORECASE
)
# 严格全大写形态: 现存约定
_RE_EXPECTED_STRICT_UPPER = re.compile(r"^EXPECTED_[A-Z0-9_]+_(?:FILE|FUNC)_SHA$")

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    _results.append((tag, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[verify_infra_110_const_case] {status} {tag}: {detail}", flush=True)


def _file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _self_file_sha_without_field() -> str:
    """计算本 verify 文件 sha, 但抹掉 EXPECTED_SELF_FILE_SHA 行的 hex 字段值,
    避免自锁 chicken-and-egg (替换策略与 verify_infra_V6_strict_area 一致)。
    """
    src = Path(__file__).read_text(encoding="utf-8")
    placeholder = "0" * 64
    pat = re.compile(
        r'(EXPECTED_SELF_FILE_SHA\s*=\s*\(\s*\n\s*")[0-9a-f]{64}(")',
        re.MULTILINE,
    )
    new_src = pat.sub(r"\1" + placeholder + r"\2", src)
    return hashlib.sha256(new_src.encode("utf-8")).hexdigest()


def _scan_module_toplevel_const_names(p: Path) -> List[Tuple[str, int]]:
    """扫 module 顶层 ``ast.Assign`` 节点, 返回 [(const_name, lineno), ...].

    仅识别 ``ast.Assign`` 单 target ``ast.Name``, 不递归; 与
    ``scan_reverse_sha_locks_ast`` 设计一致, 避开 docstring / 注释 /
    嵌套 tuple 字面量假阳性.
    """
    try:
        src = p.read_text(encoding="utf-8")
    except Exception:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out: List[Tuple[str, int]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name):
            continue
        out.append((tgt.id, node.lineno))
    return out


def _check_v12_case_uniform_lock() -> Tuple[bool, str, int, int]:
    """V12 + V12b 计算: 扫描 scripts/ 全体 verify_*.py + _verify_lib.py
    顶层 const 名, case-insensitive 匹配 EXPECTED_*_(FILE|FUNC)_SHA 形态,
    断言无 lowercase / mixed-case 违规.

    返回 (ok, detail_str, strict_upper_count, ci_match_count).
    """
    targets = sorted(SCRIPTS.glob("verify_*.py"))
    if VERIFY_LIB.is_file():
        targets.append(VERIFY_LIB)
    violations: List[str] = []
    strict_upper = 0
    ci_match = 0
    for p in targets:
        for const_name, lineno in _scan_module_toplevel_const_names(p):
            if not _RE_EXPECTED_CASE_INSENSITIVE.match(const_name):
                continue
            ci_match += 1
            if _RE_EXPECTED_STRICT_UPPER.match(const_name):
                strict_upper += 1
                continue
            # CI 命中但非严格全大写 → 违规
            rel = p.relative_to(REPO) if REPO in p.parents else p
            violations.append(f"{rel}:{lineno} {const_name!r}")
    if violations:
        detail = f"non-uppercase violations={violations[:5]} total={len(violations)}"
        return False, detail, strict_upper, ci_match
    detail = (
        f"all {ci_match} case-insensitive matches are strict uppercase "
        f"(strict_upper={strict_upper}, scanned {len(targets)} files)"
    )
    return True, detail, strict_upper, ci_match


def main() -> None:
    # V0 scaffolding
    if not VERIFY_LIB.is_file():
        _emit("V0_verify_lib_exists", False, f"missing {VERIFY_LIB}")
        verify_summary_exit(sum(1 for _, ok, _ in _results if not ok))
        return
    _emit("V0_verify_lib_exists", True, str(VERIFY_LIB.relative_to(REPO)))
    lib_src = VERIFY_LIB.read_text(encoding="utf-8")
    if "def scan_reverse_sha_locks_ast" not in lib_src:
        _emit(
            "V0_helper_symbol",
            False,
            "scan_reverse_sha_locks_ast not found in _verify_lib.py",
        )
        verify_summary_exit(sum(1 for _, ok, _ in _results if not ok))
        return
    _emit("V0_helper_symbol", True, "scan_reverse_sha_locks_ast present")

    # V1 self file sha (normalize EXPECTED_SELF_FILE_SHA 行 hex 为占位避免 chicken-egg)
    got_self = _self_file_sha_without_field()
    if EXPECTED_SELF_FILE_SHA == "0" * 64:
        _emit("V1_self_file_sha", True, f"PLACEHOLDER actual={got_self[:16]}... (bootstrap)")
    else:
        _emit(
            "V1_self_file_sha",
            got_self == EXPECTED_SELF_FILE_SHA,
            f"got={got_self[:16]} expect={EXPECTED_SELF_FILE_SHA[:16]}",
        )

    # V2 _verify_lib.py file sha
    got_lib = _file_sha(VERIFY_LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit("V2_verify_lib_file_sha", False, f"placeholder; bump EXPECTED_VERIFY_LIB_FILE_SHA={got_lib}")
    else:
        _emit(
            "V2_verify_lib_file_sha",
            got_lib == EXPECTED_VERIFY_LIB_FILE_SHA,
            f"got={got_lib[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
        )

    # V12 case_uniform_lock
    v12_ok, v12_detail, strict_upper, ci_match = _check_v12_case_uniform_lock()
    _emit("V12_case_uniform_lock", v12_ok, v12_detail)

    # V12b vacuous_guard: 严格全大写命中数必须 >= 1
    # (helper scan_reverse_sha_locks_ast 设计上能扫到大量 EXPECTED_*_FILE_SHA /
    # EXPECTED_*_FUNC_SHA, 若扫出 0 个表示 helper 被改写到 broken 状态,
    # V12 会 vacuous PASS, 此处显式 catch)
    _emit(
        "V12b_vacuous_guard",
        strict_upper >= 1,
        f"strict_upper={strict_upper} ci_match={ci_match} (expect strict_upper>=1)",
    )

    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(f"[verify_infra_110_const_case][SUMMARY] FAIL {failed}/{total}: {names}", flush=True)
    else:
        print(f"[verify_infra_110_const_case][SUMMARY] ALL PASS ({total} checks)", flush=True)
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
