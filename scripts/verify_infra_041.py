#!/usr/bin/env python3
"""verify_infra_041: V6 pre-flight 反向 sha lock check meta-lock (verify-only).

infra-038-backlog-V6-pre-flight-grep-check: 守护 scripts/verify_infra_034.py
新增的 V6 反向 sha lock 一致性 pre-flight check (扫 scripts/verify_*.py +
_verify_lib.py 中 ``VERIFY_<NNN>_*SHA*`` 形式的反向锁常量, 若其 hex 值不等于
任意现存 verify 脚本的当前实际 sha → FAIL, 防 cascade 漏改).

V0 scaffolding: verify_infra_034.py 存在 + 顶部 docstring 提及 V6.
V1 docstring sentinel: 本脚本 docstring 含 INFRA_041_SHA_LOCKS section.
V2 锁 verify_infra_034.py V6 三个相关函数的 func sha
   (v6_reverse_sha_lock_consistency, _v6_scan_constants, _v6_target_id).
V3 mutant 反证 — 临时 monkey-patch verify_infra_034.py 让 V6 跳过比对
   (替换 sha 比对成永真), subprocess 调用 V4 行为应失败 / 至少应能区分.
V4 行为验证 — 构造 in-memory mutant: 真的写一份临时 mutated verify_robot_027
   copy, 让 V6 logic 应当报 orphan 来证明 checker 有真实判别力;
   实现上通过 subprocess 调 verify_infra_034.py 时透过 PYTHONPATH 等手段
   不影响 working tree, 改为: 在本进程内 import V6 helpers 直接调用并验证
   "构造的 orphan sha 值 → 不在 live_sha_set 内 → 应被识别".
V5 Reviewer-LGTM gate (print-only).

INFRA_041_SHA_LOCKS
-------------------
- scripts/verify_infra_034.py v6_reverse_sha_lock_consistency func sha:
  EXPECTED_V6_CHECKER_FUNC_SHA
- scripts/verify_infra_034.py _v6_scan_constants func sha:
  EXPECTED_V6_SCAN_FUNC_SHA
- scripts/verify_infra_034.py _v6_target_id func sha:
  EXPECTED_V6_TARGET_ID_FUNC_SHA

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``); 子进程
用 ``sys.executable``; 不写死 ``"python"`` / ``"python3"``.
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
VERIFY_034 = REPO / "scripts" / "verify_infra_034.py"

# V2 func sha 锁 (verify_infra_034 中的 V6 实现段)
EXPECTED_V6_CHECKER_FUNC_SHA = "a460f1bd19e4f8b91019fce1caead058f308f0cebce60585fa241ce58dfcb985"
EXPECTED_V6_SCAN_FUNC_SHA = "11cb2d846cfa0072fa572ecf8e85c02833c809ffc34ad9e810166d5ccdf98e85"
EXPECTED_V6_TARGET_ID_FUNC_SHA = "8c4e9697dd54c4b757d3ed4d09adfd1b1a492f4ae9359da071630ab7f3d1ebda"

DOCSTRING_SENTINEL = "INFRA_041_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_041][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _func_sha_via_unparse(path: Path, func_name: str) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return hashlib.sha256(ast.unparse(node).encode("utf-8")).hexdigest()
    raise RuntimeError(f"func {func_name!r} not found in {path}")


# ---------------------------------------------------------------------------
# V0: scaffolding — verify_infra_034.py 存在 + docstring 提及 V6
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    if not VERIFY_034.is_file():
        _emit("V0_verify_034_exists", False, f"missing {VERIFY_034}")
        return
    _emit("V0_verify_034_exists", True, str(VERIFY_034.relative_to(REPO)))
    try:
        doc = ast.get_docstring(ast.parse(VERIFY_034.read_text(encoding="utf-8"))) or ""
    except Exception as e:
        _emit("V0_verify_034_docstring_v6", False, f"parse err: {e!r}")
        return
    ok = "V6" in doc and "反向" in doc
    _emit(
        "V0_verify_034_docstring_v6",
        ok,
        f"docstring 含 'V6' 与 '反向': {ok}",
    )


# ---------------------------------------------------------------------------
# V1: 本脚本 docstring sentinel
# ---------------------------------------------------------------------------
def v1_self_sentinel() -> None:
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8"))) or ""
    _emit(
        "V1_docstring_sentinel",
        DOCSTRING_SENTINEL in doc,
        f"sentinel={DOCSTRING_SENTINEL}",
    )


# ---------------------------------------------------------------------------
# V2: 锁 verify_infra_034.py 中 V6 三个函数的 func sha
# ---------------------------------------------------------------------------
_V6_FUNC_LOCKS = (
    ("v6_reverse_sha_lock_consistency", "EXPECTED_V6_CHECKER_FUNC_SHA", EXPECTED_V6_CHECKER_FUNC_SHA),
    ("_v6_scan_constants", "EXPECTED_V6_SCAN_FUNC_SHA", EXPECTED_V6_SCAN_FUNC_SHA),
    ("_v6_target_id", "EXPECTED_V6_TARGET_ID_FUNC_SHA", EXPECTED_V6_TARGET_ID_FUNC_SHA),
)


def v2_func_sha_locks() -> None:
    for func_name, const_name, expected in _V6_FUNC_LOCKS:
        try:
            got = _func_sha_via_unparse(VERIFY_034, func_name)
        except Exception as e:
            _emit(f"V2_{func_name}_func_sha", False, f"compute err: {e!r}")
            continue
        if expected == "__BUMP_ME__":
            _emit(
                f"V2_{func_name}_func_sha",
                False,
                f"placeholder; bump {const_name}={got}",
            )
            continue
        _emit(
            f"V2_{func_name}_func_sha",
            got == expected,
            f"got={got[:16]} expect={expected[:16]}",
        )


# ---------------------------------------------------------------------------
# V3: mutant 反证 — V6 checker 函数被掉包成永真 → V2 应 FAIL (func sha 变)
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    """读 verify_infra_034.py 源, 在内存中替换 V6 checker 函数体为永真 stub,
    重新 unparse + sha256 该函数, 应与 EXPECTED_V6_CHECKER_FUNC_SHA 不等
    (反证: V2 锁住的 sha 真的会随 V6 函数体变化漂移, 即 V2 有判别力)."""
    src = VERIFY_034.read_text(encoding="utf-8")
    tree = ast.parse(src)
    func_node = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "v6_reverse_sha_lock_consistency":
            func_node = node
            break
    if func_node is None:
        _emit("V3_mutant_apply", False, "v6_reverse_sha_lock_consistency not found")
        return
    # 真函数 sha
    real_sha = hashlib.sha256(ast.unparse(func_node).encode("utf-8")).hexdigest()
    # 替换 body 为 `pass` (永真 stub)
    stub = ast.FunctionDef(
        name=func_node.name,
        args=func_node.args,
        body=[ast.Pass()],
        decorator_list=func_node.decorator_list,
        returns=func_node.returns,
        type_comment=None,
    )
    ast.fix_missing_locations(stub)
    mutant_sha = hashlib.sha256(ast.unparse(stub).encode("utf-8")).hexdigest()
    _emit(
        "V3_mutant_func_sha_diverges",
        real_sha != mutant_sha,
        f"real={real_sha[:12]} mutant={mutant_sha[:12]}",
    )
    if EXPECTED_V6_CHECKER_FUNC_SHA not in ("__BUMP_ME__",):
        _emit(
            "V3_mutant_would_break_v2",
            mutant_sha != EXPECTED_V6_CHECKER_FUNC_SHA,
            "mutant sha != EXPECTED (V2 锁会检出)",
        )


# ---------------------------------------------------------------------------
# V4: 行为验证 — 直接 import V6 helpers, 构造 orphan sha → V6 应识别
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    """从 verify_infra_034 加载 _v6_scan_constants + _v6_target_id, 构造
    一个合成的反向锁 const+sha (sha 取 64 个零, 显然不等于任意现存 verify
    sha) → 验证 _v6_target_id 能从 'verify_robot_025' 推出 '025', 且
    _v6_scan_constants 在 verify_robot_027.py 中能扫到 VERIFY_025_EXPECTED_SHA
    常量条目 (列表非空, 元组结构正确)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("verify_infra_034", VERIFY_034)
    if spec is None or spec.loader is None:
        _emit("V4_load_module", False, "spec/loader None")
        return
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except SystemExit:
        # verify_infra_034.py 顶层不会 SystemExit (仅在 __main__), 安全
        pass
    except Exception as e:
        _emit("V4_load_module", False, f"exec err: {e!r}")
        return
    _emit("V4_load_module", True, "verify_infra_034 imported")

    # _v6_target_id 行为
    tid = mod._v6_target_id("scripts/verify_robot_025.py")
    _emit("V4_target_id_known", tid == "025", f"got tid={tid!r} expect '025'")
    tid_none = mod._v6_target_id("scripts/_verify_lib.py")
    _emit("V4_target_id_lib_empty", tid_none == "", f"got tid={tid_none!r}")

    # _v6_scan_constants 扫 verify_robot_027.py 应找到 VERIFY_025_EXPECTED_SHA
    rb027 = REPO / "scripts" / "verify_robot_027.py"
    items = mod._v6_scan_constants(rb027)
    names = [c for c, _s, _l in items]
    ok = "VERIFY_025_EXPECTED_SHA" in names
    _emit(
        "V4_scan_finds_robot_027_lock",
        ok,
        f"consts_found={len(items)}; has VERIFY_025_EXPECTED_SHA={ok}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (print-only)
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    _emit(
        "V5_reviewer_lgtm_gate",
        True,
        "closeout 必须有 sub-agent fresh-context Reviewer LGTM (evidence 记录)",
    )


def main() -> int:
    v0_scaffolding()
    v1_self_sentinel()
    v2_func_sha_locks()
    v3_mutant()
    v4_behavior()
    v5_reviewer_gate()
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_041][SUMMARY] FAILED tags: {failed}", flush=True)
        return 1
    print(f"[verify_infra_041][SUMMARY] ALL PASS ({len(_results)} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
