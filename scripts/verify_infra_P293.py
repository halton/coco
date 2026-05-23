#!/usr/bin/env python3
"""verify_infra_P293: typo-guard helper docstring vs impl 对齐校验.

infra-P293-typo-guard-docstring-vs-impl-mismatch (phase-51 #5.51):
源自 infra-P281 Reviewer fresh-context finding B-P281-R1。P281 helper
``verify_expected_prefix_typo_guard`` 实际行为 比 docstring 描述更严格 —
namepart 命中 typo 拼写时, **即便后缀是规范 _SHA / _SHA256 / _SHA512 也判 typo**,
而老版 docstring 含"后者最终落到 _SHA 不算 typo"的字样, 与 impl 矛盾。

决策: 保留更严格的 impl (更安全, 不放过任何 sha-ish namepart typo);
docstring + 注释明确说明该行为, 加单测确保 impl 行为不回退到"被规范后缀豁免"。

INFRA_P293_SHA_LOCKS
--------------------
本脚本不引入新 helper / 新 file sha 锁 — 仅验证 P281 helper 的 docstring/comment
与 impl 行为一致 + namepart-typo-with-canonical-suffix 行为锁。

校验层级:
- V0: helper docstring 含明确"namepart 命中即 typo (即便后缀规范)"语义关键词
- V1: module-level 注释 (P281 typo-guard 段) 含 P293 anchor + 同样语义说明
- V2: behavior — EXPECTED_FUNC_HSA_SHA (namepart HSA + 规范后缀 _SHA) 被判 typo
- V3: behavior — EXPECTED_LIB_SHAH_FILE (namepart SHAH, 后缀非 sha) 被判 typo
- V4: behavior — EXPECTED_FUNC_DATA_SHA (无 typo namepart + 规范后缀) 不判 typo
       (negative control, 防过严回退)
- V5: behavior — EXPECTED_PALETTE (合法非 sha) 不判 typo (negative control)

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import verify_expected_prefix_typo_guard  # noqa: E402

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P293][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _make_tmp_with_consts(consts: List[str]) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="p293_typo_"))
    body = "\n".join(f'{name} = "0" * 64' for name in consts) + "\n"
    (tmp / "verify_p293_fixture.py").write_text(body, encoding="utf-8")
    return tmp


def v0_docstring_says_namepart_overrides_suffix() -> None:
    """helper docstring 应明确: namepart typo 命中 → 即便后缀规范也算 typo."""
    import inspect

    doc = inspect.getdoc(verify_expected_prefix_typo_guard) or ""
    has_namepart = "namepart" in doc.lower()
    has_override = (
        "即便后缀" in doc
        or "即便规范" in doc
        or "regardless of suffix" in doc.lower()
    )
    has_hsa_sha_example = "HSA_SHA" in doc
    ok = has_namepart and has_override and has_hsa_sha_example
    _emit(
        "V0_helper_docstring_namepart_clause",
        ok,
        f"has_namepart={has_namepart} has_override={has_override} "
        f"has_HSA_SHA_example={has_hsa_sha_example}",
    )


def v1_module_comment_has_p293_anchor() -> None:
    """_verify_lib.py P281 typo-guard 段的 module-level 注释应含 P293 anchor."""
    src = LIB.read_text(encoding="utf-8")
    # 定位 _RE_EXPECTED_NAMEPART_TYPO 上下文 ±30 行
    lines = src.splitlines()
    idx = next(
        (i for i, ln in enumerate(lines) if "_RE_EXPECTED_NAMEPART_TYPO" in ln),
        -1,
    )
    if idx < 0:
        _emit("V1_p293_anchor_in_comment", False, "namepart regex not found")
        return
    window = "\n".join(lines[max(0, idx - 20) : idx + 5])
    has_p293 = "P293" in window or "infra-P293" in window
    has_hsa_sha = "HSA_SHA" in window
    has_override_phrase = "即便后缀" in window or "即便" in window
    ok = has_p293 and has_hsa_sha and has_override_phrase
    _emit(
        "V1_p293_anchor_in_comment",
        ok,
        f"has_P293={has_p293} has_HSA_SHA={has_hsa_sha} "
        f"has_override_phrase={has_override_phrase}",
    )


def v2_namepart_with_canonical_suffix_is_typo() -> None:
    """EXPECTED_FUNC_HSA_SHA (namepart HSA + 规范后缀 _SHA) 应被判 typo."""
    tmp = _make_tmp_with_consts(["EXPECTED_FUNC_HSA_SHA"])
    res = verify_expected_prefix_typo_guard(tmp)
    samples_names = {s["name"] for s in res["typo_samples"]}
    ok = (
        res["typo_count"] == 1
        and "EXPECTED_FUNC_HSA_SHA" in samples_names
        and res["all_well_formed"] is False
    )
    _emit(
        "V2_HSA_SHA_judged_typo",
        ok,
        f"typo_count={res['typo_count']} names={sorted(samples_names)} "
        f"all_well_formed={res['all_well_formed']}",
    )


def v3_namepart_only_no_canonical_suffix_is_typo() -> None:
    """EXPECTED_LIB_SHAH_FILE (namepart SHAH, 无规范后缀) 应被判 typo."""
    tmp = _make_tmp_with_consts(["EXPECTED_LIB_SHAH_FILE"])
    res = verify_expected_prefix_typo_guard(tmp)
    samples_names = {s["name"] for s in res["typo_samples"]}
    ok = (
        res["typo_count"] == 1
        and "EXPECTED_LIB_SHAH_FILE" in samples_names
    )
    _emit(
        "V3_namepart_only_judged_typo",
        ok,
        f"typo_count={res['typo_count']} names={sorted(samples_names)}",
    )


def v4_clean_canonical_not_typo() -> None:
    """EXPECTED_FUNC_DATA_SHA (无 typo + 规范后缀) 不应判 typo (negative control)."""
    tmp = _make_tmp_with_consts(["EXPECTED_FUNC_DATA_SHA"])
    res = verify_expected_prefix_typo_guard(tmp)
    ok = (
        res["typo_count"] == 0
        and res["well_formed"] == 1
        and res["all_well_formed"] is True
    )
    _emit(
        "V4_clean_canonical_not_typo",
        ok,
        f"typo_count={res['typo_count']} well_formed={res['well_formed']} "
        f"all_well_formed={res['all_well_formed']}",
    )


def v5_legit_non_sha_not_typo() -> None:
    """EXPECTED_PALETTE (合法非 sha) 不应判 typo (negative control)."""
    tmp = _make_tmp_with_consts(["EXPECTED_PALETTE", "EXPECTED_LINES"])
    res = verify_expected_prefix_typo_guard(tmp)
    ok = (
        res["typo_count"] == 0
        and res["well_formed"] == 2
        and res["all_well_formed"] is True
    )
    _emit(
        "V5_legit_non_sha_not_typo",
        ok,
        f"typo_count={res['typo_count']} well_formed={res['well_formed']}",
    )


def main() -> int:
    v0_docstring_says_namepart_overrides_suffix()
    v1_module_comment_has_p293_anchor()
    v2_namepart_with_canonical_suffix_is_typo()
    v3_namepart_only_no_canonical_suffix_is_typo()
    v4_clean_canonical_not_typo()
    v5_legit_non_sha_not_typo()

    fails = [tag for tag, ok, _ in _results if not ok]
    total = len(_results)
    if fails:
        print(
            f"[verify_infra_P293][SUMMARY] FAIL {len(fails)}/{total}: {fails}",
            flush=True,
        )
        return 1
    print(f"[verify_infra_P293][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
