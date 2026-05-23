#!/usr/bin/env python3
"""verify_infra_P290: verifier tag 命名 docstring↔_emit 一致性 lint (verify-only).

infra-P290-backlog-verifier-tag-naming-doc (phase-58 #1):
来源 phase-53 #3.53 Reviewer P2 finding: brief 文档与 verify_infra_102.py 实际 emit
的 verifier tag 命名口径不一致。本 verify 把 docstring (V0-V5 段) 里出现的所有
``V[0-9][A-Za-z_0-9]+`` tag 抽取出来, 与 verify_infra_102.py 中所有 ``_emit("Vx_..", ...)``
第一参数集合做对照, 任一方向不一致即 FAIL, 锁住未来 docstring drift / emit drift。

INFRA_P290_DOC_SHA_LOCKS
------------------------
- ``scripts/verify_infra_102.py`` ``main`` func sha: EXPECTED_TARGET_FUNC_SHA
- self ``main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA (V0 自锁)

校验层级 (V0-V5):

- V0_self_main_func_sha: 自身 ``main`` func sha 锁
- V1_docstring_sentinel: ``INFRA_P290_DOC_SHA_LOCKS`` 自锁存在
- V2_target_func_sha: verify_infra_102.py ``main`` func sha 锁 (func-level
  lock, 相比 file sha 对 docstring / 空行 / 无关编辑弹性)
- V3_docstring_tags_nonempty: docstring 中可抽 >= 6 个 Vx_ tag
- V3_emit_tags_nonempty: target 中可抽 >= 6 个 Vx_ tag 字面常量
- V4_doc_subset_of_emit: docstring 中出现的 Vx_ tag 全部能在 target 中找到
- V4_emit_subset_of_doc: target 的 Vx_ tag 全部能在 docstring 中找到
- V4_no_stale_aliases: docstring 不含已知历史别名 (V4_uses_in_keyword /
  V1_family_sets_are_frozenset / V_classify_func_sha)
- V5_reviewer_lgtm_gate (grace_period 兜底)

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import List, Set, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
TARGET = SCRIPTS / "verify_infra_102.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

EXPECTED_TARGET_FUNC_SHA = (
    "072652ad44846671abec9b9b38aa5d229bbe71d21e9b90d7bee3f356cdacbc2f"
)
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "02e54a1fb197287f185cd2df7441f161fbd9cb86521c7196a8c674e53eccdc68"
)

DOCSTRING_SENTINEL = "INFRA_P290_DOC_SHA_LOCKS"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P290-backlog-verifier-tag-naming-doc"

# 已知历史别名 (brief 用过, 实际从未被 _emit), V4_no_stale_aliases 防回潮
STALE_ALIASES = (
    "V4_uses_in_keyword",
    "V1_family_sets_are_frozenset",
    "V_classify_func_sha",
)

# docstring 内对 family contents 描述时, 用 V3_dump_family_contents 等"虚 tag"
# 描述集合内容, 并非真 _emit 的 tag. 允许它们出现在 docstring 而不在 emit 集合中.
DOC_ONLY_DESCRIPTIVE = frozenset({
    "V3_dump_family_contents",
    "V3_hub_family_contents",
    "V3_lib_family_contents",
})

TAG_RE = re.compile(r"\bV[0-9][A-Za-z0-9_]*[A-Za-z][A-Za-z0-9_]*\b")

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P290][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _extract_docstring_tags(src: str) -> Set[str]:
    tree = ast.parse(src)
    doc = ast.get_docstring(tree) or ""
    return set(TAG_RE.findall(doc))


def _extract_emit_tags(src: str) -> Set[str]:
    """Extract emit-able verifier tag strings from target source.

    Strategy: walk AST and collect every ``ast.Constant`` string literal that
    fully matches ``TAG_RE``. This covers both literal ``_emit("Vx_..", ...)``
    first-args and dynamic dispatch via ``cases = [(.., "Vx_..")]`` lists.
    """
    tree = ast.parse(src)
    out: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if TAG_RE.fullmatch(val):
                out.add(val)
    return out


def v0_self_main_func_sha() -> None:
    self_path = Path(__file__)
    got = func_sha_by_name(self_path, "main")
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V0_self_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
    else:
        _emit(
            "V0_self_main_func_sha",
            got == EXPECTED_SELF_MAIN_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
        )


def v1_docstring_sentinel() -> None:
    src = Path(__file__).read_text(encoding="utf-8")
    _emit(
        "V1_docstring_sentinel",
        DOCSTRING_SENTINEL in src,
        f"sentinel={DOCSTRING_SENTINEL!r}",
    )


def v2_target_func_sha() -> None:
    if not TARGET.is_file():
        _emit("V2_target_func_sha", False, f"missing {TARGET}")
        return
    got = func_sha_by_name(TARGET, "main")
    _emit(
        "V2_target_func_sha",
        got == EXPECTED_TARGET_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_TARGET_FUNC_SHA[:16]}",
    )


def v3_v4_tag_consistency() -> None:
    if not TARGET.is_file():
        _emit("V3_docstring_tags_nonempty", False, "target missing")
        return
    src = TARGET.read_text(encoding="utf-8")
    doc_tags = _extract_docstring_tags(src)
    emit_tags = _extract_emit_tags(src)
    _emit(
        "V3_docstring_tags_nonempty",
        len(doc_tags) >= 6,
        f"count={len(doc_tags)} sample={sorted(doc_tags)[:5]}",
    )
    _emit(
        "V3_emit_tags_nonempty",
        len(emit_tags) >= 6,
        f"count={len(emit_tags)} sample={sorted(emit_tags)[:5]}",
    )

    # 允许 doc-only 描述性 tag 与 family-prefix 别名(如 V3_hub_is_frozenset
    # 在 docstring 写成 "V3_hub_is_frozenset / V3_lib_is_frozenset / ..." 时
    # TAG_RE 已能识别).
    doc_for_check = doc_tags - DOC_ONLY_DESCRIPTIVE
    missing_in_emit = doc_for_check - emit_tags
    _emit(
        "V4_doc_subset_of_emit",
        not missing_in_emit,
        f"missing_in_emit={sorted(missing_in_emit)}",
    )

    missing_in_doc = emit_tags - doc_tags
    _emit(
        "V4_emit_subset_of_doc",
        not missing_in_doc,
        f"missing_in_doc={sorted(missing_in_doc)}",
    )

    stale_hit = [a for a in STALE_ALIASES if a in src]
    _emit(
        "V4_no_stale_aliases",
        not stale_hit,
        f"stale_hit={stale_hit}",
    )


def v5_reviewer_gate() -> None:
    if not REAL_FEATURE_LIST.is_file():
        _emit(
            "V5_reviewer_lgtm_gate",
            False,
            f"feature_list.json not found at {REAL_FEATURE_LIST}",
        )
        return
    result = assert_v5_reviewer_gate_evidence_bind(
        V5_GATE_FEATURE_ID,
        REAL_FEATURE_LIST,
        grace_period_feature_ids=(V5_GATE_FEATURE_ID,),
    )
    _emit(
        "V5_reviewer_lgtm_gate",
        bool(result["ok"]),
        f"target={V5_GATE_FEATURE_ID} helper_ok={result['ok']} "
        f"reason={result.get('reason', '')}",
    )


def main() -> None:
    v0_self_main_func_sha()
    v1_docstring_sentinel()
    v2_target_func_sha()
    v3_v4_tag_consistency()
    v5_reviewer_gate()

    failed = sum(1 for _, ok, _ in _results if not ok)
    print(
        f"[verify_infra_P290] summary: total={len(_results)} failed={failed}",
        flush=True,
    )
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
