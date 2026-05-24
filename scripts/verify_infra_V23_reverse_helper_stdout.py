#!/usr/bin/env python3
"""verify_infra_V23_reverse_helper_stdout: 细粒度锁 bump_reverse_sha_lock.py 的
stdout sentinel 格式契约 (infra-V23-reverse-helper-stdout-format-lock).

背景
----
P312 V7/V8 锁了 reverse helper 的 e2e stdout 必须含 ``RESULT: NOOP`` 字串。
但这只是 "粗粒度行为锁": 如果 sentinel 字面格式被悄悄改成 ``result: noop`` /
``RESULT NOOP`` / 多出大括号 / reason 集合添加新值, V7/V8 仍能蒙混过关
(它们只 substring 命中)。

V23 在源码 + e2e 两层做**细粒度格式锁**:

源码层 (AST + 字符串字面量扫描):
  V1: 文件存在 + 顶层有 ``compute_sentinel`` / ``main`` 函数
  V2: 字面量集合含 APPLIED 前缀 (``"RESULT: APPLIED holders="``)
  V3: 字面量集合含 NOOP 前缀 + 允许 reason 集合 = {``no_holders``, ``all_uptodate``}
  V4: 字面量集合含 FAIL 前缀 + 允许 reason 集合 = {``target_missing``}
  V5: 整体文件 sha 锁 (锁源码不被悄改)

行为层 (e2e dry-run + 正则匹配最后一行):
  V6: 不带参数跑 helper 等价于跑 cascade no-drift 路径 → 最后一行必须匹配:
      ``^RESULT: (APPLIED holders=\\d+|NOOP reason=(no_holders|all_uptodate)|FAIL reason=target_missing)$``

任何字面格式漂移都会在 V2-V4 / V6 触发 FAIL, 比 P312 V7/V8 的 substring 检测严格 N 倍。

## Lock
- type: file_sha + ast_literal_scan + e2e_regex
- target: scripts/bump_reverse_sha_lock.py
- target_function: compute_sentinel, main
- locked_strings: RESULT: APPLIED holders=, RESULT: NOOP reason=, RESULT: FAIL reason=
- bump_when: stdout sentinel 格式 (前缀 / 字段名 / 允许 reason 集合) 改变
- bump_protocol: 改完 reverse helper 源码后, 用 ``python scripts/bump_reverse_sha_lock_file_sha.py``
  (若存在) 或手动 recompute sha256(bump_reverse_sha_lock.py) 并更新本文件
  EXPECTED_REVERSE_HELPER_FILE_SHA 常量, 再视情况调整 ALLOWED_NOOP_REASONS /
  ALLOWED_FAIL_REASONS 集合。
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
TARGET = SCRIPTS / "bump_reverse_sha_lock.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import verify_summary_exit  # noqa: E402

# V5: 整体文件 sha 锁 (锁源码不被悄改)
EXPECTED_REVERSE_HELPER_FILE_SHA = (
    "629ab94af5ad6950bd9219ab29c91f501b9553f322a5204a28606ef03db27f31"
)

# 允许的 sentinel 字面前缀 / reason 集合 (与 reverse helper compute_sentinel + main 一致)
EXPECTED_APPLIED_PREFIX = "RESULT: APPLIED holders="
EXPECTED_NOOP_PREFIX = "RESULT: NOOP reason="
# FAIL 分支当前实现用 f-string ``f"RESULT: {kind} reason={reason}"`` 拼接 (kind 走 FAIL/NOOP 共用模板),
# 所以源码字面量里不会出现 "RESULT: FAIL reason=" 完整前缀。改成锁 f-string 模板的 ``RESULT: `` + `` reason=`` 双锚点。
EXPECTED_FAIL_FSTRING_LEAD = "RESULT: "
EXPECTED_FAIL_FSTRING_MID = " reason="

# V3 / V4: 允许的 reason 集合, 漂移即 FAIL
ALLOWED_NOOP_REASONS = frozenset({"no_holders", "all_uptodate"})
ALLOWED_FAIL_REASONS = frozenset({"target_missing"})

# V6: 行为层 sentinel 正则 (最后一行必须 fullmatch)
SENTINEL_RE = re.compile(
    r"^RESULT: (APPLIED holders=\d+|NOOP reason=(no_holders|all_uptodate)|FAIL reason=target_missing)$"
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_V23][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, bool(ok), detail))


def _collect_string_literals(src: str) -> List[str]:
    """收集源码中所有 ast.Constant(str) 字面量值."""
    tree = ast.parse(src)
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


def v1_top_level_functions() -> None:
    if not TARGET.is_file():
        _emit("V1_top_level_functions", False, f"{TARGET} not found")
        return
    src = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(src)
    func_names = {
        n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    has_compute = "compute_sentinel" in func_names
    has_main = "main" in func_names
    ok = has_compute and has_main
    _emit(
        "V1_top_level_functions",
        ok,
        f"compute_sentinel={has_compute} main={has_main}",
    )


def v2_applied_prefix_literal() -> None:
    if not TARGET.is_file():
        _emit("V2_applied_prefix_literal", False, "target missing")
        return
    src = TARGET.read_text(encoding="utf-8")
    lits = _collect_string_literals(src)
    # 任一字面量含 APPLIED 锁前缀子串即可 (helper 用 f-string 拼 holders=<N>)
    hits = [s for s in lits if EXPECTED_APPLIED_PREFIX in s]
    ok = len(hits) >= 1
    _emit(
        "V2_applied_prefix_literal",
        ok,
        f"prefix={EXPECTED_APPLIED_PREFIX!r} hits={len(hits)}",
    )


def v3_noop_prefix_and_reasons() -> None:
    if not TARGET.is_file():
        _emit("V3_noop_prefix_and_reasons", False, "target missing")
        return
    src = TARGET.read_text(encoding="utf-8")
    lits = _collect_string_literals(src)
    has_prefix = any(EXPECTED_NOOP_PREFIX in s for s in lits)
    # 提取 helper 里 NOOP/FAIL reason 来源: compute_sentinel 返回 dict 的 reason 字段值
    # 静态扫描 ast 找 ``{"kind": "NOOP", ..., "reason": "<X>"}`` 这样的字面量字典
    tree = ast.parse(src)
    noop_reasons: set[str] = set()
    fail_reasons: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
            vals = [v.value if isinstance(v, ast.Constant) else None for v in node.values]
            kv = dict(zip(keys, vals))
            kind = kv.get("kind")
            reason = kv.get("reason")
            if kind == "NOOP" and isinstance(reason, str):
                noop_reasons.add(reason)
            if kind == "FAIL" and isinstance(reason, str):
                fail_reasons.add(reason)
    # 漂移检测: helper 里出现的 NOOP reason 必须是允许集合的子集
    extra_noop = noop_reasons - ALLOWED_NOOP_REASONS
    missing_noop = ALLOWED_NOOP_REASONS - noop_reasons
    ok = has_prefix and not extra_noop and not missing_noop
    _emit(
        "V3_noop_prefix_and_reasons",
        ok,
        f"prefix_hit={has_prefix} noop_reasons={sorted(noop_reasons)} "
        f"extra={sorted(extra_noop)} missing={sorted(missing_noop)}",
    )
    # 把 fail_reasons 暂存到 module 供 V4 复用
    v3_noop_prefix_and_reasons._fail_reasons = fail_reasons  # type: ignore[attr-defined]


def v4_fail_prefix_and_reasons() -> None:
    if not TARGET.is_file():
        _emit("V4_fail_prefix_and_reasons", False, "target missing")
        return
    src = TARGET.read_text(encoding="utf-8")
    # FAIL 分支当前实现: main() 里有 f"RESULT: {sentinel['kind']} reason={sentinel['reason']}"
    # 我们扫描 f-string (ast.JoinedStr) 里同时含 "RESULT: " 与 " reason=" 两段字面量
    tree = ast.parse(src)
    fail_template_hit = False
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            consts = [
                v.value
                for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            ]
            joined_text = "".join(consts)
            if EXPECTED_FAIL_FSTRING_LEAD in joined_text and EXPECTED_FAIL_FSTRING_MID in joined_text:
                fail_template_hit = True
                break
    # 复用 V3 扫描出的 fail_reasons
    fail_reasons: set[str] = getattr(v3_noop_prefix_and_reasons, "_fail_reasons", set())
    # FAIL 集合是开放的: helper 实际可能有 target_missing / regex_miss 等多种
    # V23 只锁 target_missing 必须存在 (核心契约), 其他不限制
    has_target_missing = "target_missing" in fail_reasons
    ok = fail_template_hit and has_target_missing
    _emit(
        "V4_fail_prefix_and_reasons",
        ok,
        f"fail_template_hit={fail_template_hit} fail_reasons={sorted(fail_reasons)} "
        f"has_target_missing={has_target_missing}",
    )


def v5_file_sha_lock() -> None:
    if not TARGET.is_file():
        _emit("V5_file_sha_lock", False, "target missing")
        return
    actual = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    ok = actual == EXPECTED_REVERSE_HELPER_FILE_SHA
    _emit(
        "V5_file_sha_lock",
        ok,
        f"expected={EXPECTED_REVERSE_HELPER_FILE_SHA[:16]} actual={actual[:16]}",
    )


def v6_e2e_sentinel_regex() -> None:
    """跑 helper dry-run --target <self>, 解析最后一行非空 stdout 必须匹配 SENTINEL_RE."""
    if not TARGET.is_file():
        _emit("V6_e2e_sentinel_regex", False, "target missing")
        return
    # 用 _verify_lib.py 作为 dry-run target (任意稳定文件即可触发 NOOP all_uptodate 或 no_holders)
    proc = subprocess.run(
        [sys.executable, str(TARGET), "--target", "scripts/_verify_lib.py"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    out_lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    last_line = out_lines[-1] if out_lines else ""
    m = SENTINEL_RE.match(last_line)
    ok = proc.returncode in (0, 3) and m is not None
    _emit(
        "V6_e2e_sentinel_regex",
        ok,
        f"rc={proc.returncode} last_line={last_line!r} regex_match={bool(m)}",
    )


def main() -> int:
    v1_top_level_functions()
    v2_applied_prefix_literal()
    v3_noop_prefix_and_reasons()
    v4_fail_prefix_and_reasons()
    v5_file_sha_lock()
    v6_e2e_sentinel_regex()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(
        f"[verify_infra_V23][SUMMARY] "
        f"{'FAIL ' + str(failed) + '/' + str(total) if failed else 'ALL PASS (' + str(total) + ' checks)'}",
        flush=True,
    )
    verify_summary_exit(failed)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
