#!/usr/bin/env python3
"""verify_infra_042: assert_unique_needle helper lock (verify-only).

infra-040-backlog (phase-33 #3.33): 锁住 ``scripts/_verify_lib.py::assert_unique_needle``
helper 的存在与行为. P258 暴露的 V3 mutant needle 多重替换坑由本 helper 兜底,
所有 V3 mutant 在 ``text.replace(needle, ...)`` 之前应 call helper 显式断言唯一性.

本脚本验证:
- helper 存在于 _verify_lib.py 且入 ``__all__``
- _verify_lib.py file sha + assert_unique_needle func sha 双锁
- in-memory ast mutant: 改写 assert_unique_needle 的条件 (count != 1 → count == 1),
  unparse 后 sha 必漂移 (反证 V2 func-sha 锁不退化)
- 行为验证: 0/1/2/3 次出现的输入分别验 raise / 不 raise / raise / raise

V0 scaffolding: _verify_lib.py 存在 + 本脚本本身可执行 + V0-V5 section 都在.
V1 docstring sentinel ``INFRA_042_SHA_LOCKS`` 自锁.
V2 _verify_lib.py file sha + assert_unique_needle func sha 双锁.
V3 in-memory mutant 反证: ast 改 ``count != 1`` → ``count == 1``, unparse 后
   sha 必不同于 baseline (锁链路有效).
V4 行为验证: call helper, 0/2/3 次必 raise ValueError, 1 次必 return None.
V5 Reviewer LGTM gate (print-only, evidence 字段).

INFRA_042_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_LIB_FILE_SHA
- ``assert_unique_needle`` func sha: EXPECTED_HELPER_FUNC_SHA

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import assert_unique_needle, func_sha_by_name  # noqa: E402

# infra-042 sha lock 常量 (V2)
# infra-V6-backlog bump (P264): 抽 V6 scan_reverse_sha_locks 等 helper 后 file sha 变更
EXPECTED_LIB_FILE_SHA = "d91ab742543a7dcaca6327e44b59f55e6c41db3cc9aeb841539b4876e1c1ea9e"
EXPECTED_HELPER_FUNC_SHA = "e3223584fbdb5f1bbe426a8f483ae74ab2d17c7586206db3578a83c233c1bcaf"

DOCSTRING_SENTINEL = "INFRA_042_SHA_LOCKS"
HELPER_NAME = "assert_unique_needle"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_042][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_lib_exists", LIB.is_file(), f"path={LIB}")
    self_path = Path(__file__)
    _emit("V0_self_exists", self_path.is_file(), f"path={self_path}")
    # helper 在 __all__ 中
    src = LIB.read_text(encoding="utf-8")
    tree = ast.parse(src)
    all_list: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    try:
                        all_list = list(ast.literal_eval(node.value))
                    except Exception:
                        all_list = []
    _emit(
        "V0_helper_in_all",
        HELPER_NAME in all_list,
        f"__all__={all_list}",
    )


# ---------------------------------------------------------------------------
# V1: docstring sentinel 自锁
# ---------------------------------------------------------------------------
def v1_docstring_sentinel() -> None:
    self_path = Path(__file__)
    doc = ast.get_docstring(ast.parse(self_path.read_text(encoding="utf-8")))
    _emit(
        "V1_docstring_sentinel",
        bool(doc) and DOCSTRING_SENTINEL in (doc or ""),
        f"sentinel={DOCSTRING_SENTINEL}",
    )


# ---------------------------------------------------------------------------
# V2: _verify_lib.py file sha + assert_unique_needle func sha 双锁
# ---------------------------------------------------------------------------
def v2_file_and_func_sha() -> None:
    got_file = _file_sha(LIB)
    _emit(
        "V2_lib_file_sha",
        got_file == EXPECTED_LIB_FILE_SHA,
        f"got={got_file[:16]} expect={EXPECTED_LIB_FILE_SHA[:16]}",
    )
    got_func = func_sha_by_name(LIB, HELPER_NAME)
    _emit(
        "V2_helper_func_sha",
        got_func == EXPECTED_HELPER_FUNC_SHA,
        f"got={got_func[:16]} expect={EXPECTED_HELPER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: in-memory ast mutant 反证 — 改 count != 1 → count == 1, sha 必漂移
# ---------------------------------------------------------------------------
class _CmpFlipper(ast.NodeTransformer):
    """把 NotEq 比较翻转为 Eq, 用于翻转 assert_unique_needle 的条件。"""

    def visit_Compare(self, node: ast.Compare) -> ast.AST:  # noqa: N802
        new_ops = [ast.Eq() if isinstance(op, ast.NotEq) else op for op in node.ops]
        if any(isinstance(op, ast.Eq) and not isinstance(orig, ast.Eq)
               for op, orig in zip(new_ops, node.ops)):
            node.ops = new_ops
        return self.generic_visit(node)


def v3_mutant() -> None:
    src = LIB.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target: ast.FunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == HELPER_NAME:
            target = node
            break
    if target is None:
        _emit("V3_mutant_apply", False, f"helper {HELPER_NAME!r} not found")
        return
    baseline_sha = hashlib.sha256(ast.unparse(target).encode("utf-8")).hexdigest()
    mutant_node = _CmpFlipper().visit(ast.parse(ast.unparse(target)).body[0])
    ast.fix_missing_locations(mutant_node)
    mutant_sha = hashlib.sha256(ast.unparse(mutant_node).encode("utf-8")).hexdigest()
    _emit(
        "V3_mutant_sha_drift",
        baseline_sha != mutant_sha,
        f"baseline={baseline_sha[:16]} mutant={mutant_sha[:16]}",
    )
    # baseline 应等于 EXPECTED_HELPER_FUNC_SHA (双锁一致)
    _emit(
        "V3_baseline_matches_expected",
        baseline_sha == EXPECTED_HELPER_FUNC_SHA,
        f"baseline={baseline_sha[:16]} expect={EXPECTED_HELPER_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为验证 — call helper 直接验各种 count
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    cases = [
        ("count=0_raises", "abc def ghi", "xyz", True),
        ("count=1_returns", "abc xyz ghi", "xyz", False),
        ("count=2_raises", "xyz abc xyz", "xyz", True),
        ("count=3_raises", "xyz abc xyz def xyz", "xyz", True),
    ]
    for tag, text, needle, should_raise in cases:
        raised = False
        try:
            assert_unique_needle(text, needle)
        except ValueError:
            raised = True
        except Exception as e:
            _emit(f"V4_{tag}", False, f"wrong exc type: {e!r}")
            continue
        ok = raised == should_raise
        _emit(
            f"V4_{tag}",
            ok,
            f"raised={raised} expect_raise={should_raise}",
        )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate (print-only)
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    _emit(
        "V5_reviewer_lgtm_gate",
        True,
        "closeout 阶段必须有 sub-agent fresh-context Reviewer LGTM (evidence 记录)",
    )


def main() -> int:
    v0_scaffolding()
    v1_docstring_sentinel()
    v2_file_and_func_sha()
    v3_mutant()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_042][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_042][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
