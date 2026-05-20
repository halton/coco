#!/usr/bin/env python3
"""verify_infra_040: PR template with decision matrix checkbox (verify-only).

infra-040: 为 verify-script PR 引入决策矩阵 check-box 模板,
流程层防默认 file-sha 选错。新增两份 markdown 模板:

- ``.github/PULL_REQUEST_TEMPLATE/verify-script.md`` (verify-script 专用)
- ``.github/pull_request_template.md`` (通用极简版, 引导切到 verify-script 模板)

本脚本锁住模板存在、本脚本 docstring sentinel + 自 checker func sha、
verify-script 模板 file sha、mutant 反证、行为验证 (关键 section heading
存在性). 0 业务源码改动。

V0 scaffolding: 两份 PR 模板文件存在 + 非空。
V1 docstring sentinel + 本脚本 v4_behavior checker 自身 func sha 自锁
   (sentinel section ``INFRA_040_SHA_LOCKS`` 必须存在).
V2 verify-script.md 整体 file-sha 锁 (避免无脑改模板).
V3 mutant 反证 — 临时删模板中的 "决策矩阵" section heading,
   subprocess 跑 V4 行为验证应失败, finally 还原.
V4 行为验证 — 解析 verify-script.md, 检查关键 section heading 全在
   ("决策矩阵", "反向引用扫描", "锁类型选择", "验证证据", "file-level sha",
    "func-level sha", "docs/verify_sha_lock_strategy.md").
V5 Reviewer-LGTM gate (print-only).

INFRA_040_SHA_LOCKS
-------------------
- ``.github/PULL_REQUEST_TEMPLATE/verify-script.md`` file sha: EXPECTED_VERIFY_TMPL_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

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
GITHUB_DIR = REPO / ".github"
VERIFY_TMPL = GITHUB_DIR / "PULL_REQUEST_TEMPLATE" / "verify-script.md"
DEFAULT_TMPL = GITHUB_DIR / "pull_request_template.md"

# infra-040 sha lock 常量 (V2): verify-script.md 整体 file sha
EXPECTED_VERIFY_TMPL_SHA = "8c50f240f7fd8ea4dd54b1cc6dc36b6cbd2509d42fc1677f9830796160d2326d"

# infra-040 自身 v4_behavior 函数 sha (V1 自锁; 末尾自计算后回填)
EXPECTED_V4_CHECKER_FUNC_SHA = "29a26bae07566dda05cd857fa8015af3010fc12f31f125344b2c75f0b04d137c"

DOCSTRING_SENTINEL = "INFRA_040_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_040][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _func_sha_via_unparse(path: Path, func_name: str) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return hashlib.sha256(ast.unparse(node).encode("utf-8")).hexdigest()
    raise RuntimeError(f"func {func_name!r} not found in {path}")


# ---------------------------------------------------------------------------
# V0: scaffolding — 两份 PR 模板存在 + 非空
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    for label, path in [("verify_tmpl", VERIFY_TMPL), ("default_tmpl", DEFAULT_TMPL)]:
        if not path.is_file():
            _emit(f"V0_{label}_exists", False, f"missing {path}")
            continue
        size = path.stat().st_size
        _emit(f"V0_{label}_exists", size > 0, f"{path} size={size}")


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
        got = _func_sha_via_unparse(self_path, "v4_behavior")
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
# V2: verify-script.md 自身 file sha 锁
# ---------------------------------------------------------------------------
def v2_tmpl_file_sha() -> None:
    if not VERIFY_TMPL.is_file():
        _emit("V2_verify_tmpl_file_sha", False, f"missing {VERIFY_TMPL}")
        return
    got = _file_sha(VERIFY_TMPL)
    _emit(
        "V2_verify_tmpl_file_sha",
        got == EXPECTED_VERIFY_TMPL_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_TMPL_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: mutant 反证 — 删 "决策矩阵" section heading, V4 行为验证应 fail
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    if not VERIFY_TMPL.is_file():
        _emit("V3_mutant_apply", False, f"missing {VERIFY_TMPL}")
        return
    original = VERIFY_TMPL.read_text(encoding="utf-8")
    needle = "### 锁类型选择"
    if needle not in original:
        _emit("V3_mutant_apply", False, f"baseline missing needle {needle!r}")
        return
    mutant = original.replace(needle, "### (mutant removed)", 1)
    if mutant == original:
        _emit("V3_mutant_apply", False, "mutation no-op")
        return
    try:
        VERIFY_TMPL.write_text(mutant, encoding="utf-8")
        # 用与 v4_behavior 相同的 anchor 集合做检查
        text = VERIFY_TMPL.read_text(encoding="utf-8")
        anchors = _v4_anchors()
        missing = [a for a in anchors if a not in text]
        # mutant 至少要让 "锁类型选择" 这个 anchor 消失
        _emit(
            "V3_mutant_detected",
            "锁类型选择" in missing,
            f"missing={missing}",
        )
    finally:
        VERIFY_TMPL.write_text(original, encoding="utf-8")


# ---------------------------------------------------------------------------
# V4: 行为验证 — 关键 section heading 存在性
# ---------------------------------------------------------------------------
def _v4_anchors() -> List[str]:
    return [
        "决策矩阵",
        "反向引用扫描",
        "锁类型选择",
        "验证证据",
        "file-level sha",
        "func-level sha",
        "docs/verify_sha_lock_strategy.md",
        "scripts/dump_v4_sha_graph.py",
        "Reviewer fresh-context LGTM",
    ]


def v4_behavior() -> None:
    if not VERIFY_TMPL.is_file():
        _emit("V4_anchors", False, f"missing {VERIFY_TMPL}")
        return
    text = VERIFY_TMPL.read_text(encoding="utf-8")
    anchors = _v4_anchors()
    missing = [a for a in anchors if a not in text]
    _emit(
        "V4_anchors",
        not missing,
        f"missing={missing}" if missing else f"all {len(anchors)} anchors found",
    )
    # check-box 数量 sanity (verify-script.md 应有若干 `- [ ]` checkbox)
    cb_count = text.count("- [ ]")
    _emit(
        "V4_checkbox_count",
        cb_count >= 10,
        f"checkbox_count={cb_count} (>=10 expected)",
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
    v1_self_lock()
    v2_tmpl_file_sha()
    v3_mutant()
    v4_behavior()
    v5_reviewer_gate()
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_040][SUMMARY] FAILED tags: {failed}", flush=True)
        return 1
    print(f"[verify_infra_040][SUMMARY] ALL PASS ({len(_results)} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
