#!/usr/bin/env python3
"""infra-034-backlog-verify-036-v4-sha-stale-sync verify (phase-67 #16).

scope
-----
infra-034 Reviewer LGTM 跟进项: 给 ``verify_interact_036.py`` V4 锚定的
``EXPECTED_VERIFY_024_SHA256`` 字节锁补"更新规程"docstring + 配套的
backlog 守卫脚本, 避免 verify_interact_024.py 升级后 verify_036 V4 默默
drift, 同时显式规范 sha 更新流程 (强制 64-char full hex, 严禁 16-char
prefix; 强制双向同步; 强制 commit 前跑 verify)。

设计决策
--------
- **不动 ``_verify_lib.py``** (P261 / 硬规则)。
- **不改 verify_interact_024.py** 业务源码 (避免 cascade)。
- 仅在 ``verify_interact_036.py`` docstring 加入显式"更新规程"段落, 配
  本守卫脚本的 V2/V3 锁面字节序列, 任何后续 docstring 漂移立即 FAIL。
- V0 self_sha 锁本脚本字节; V1 锁 verify_interact_024.py 字节 (与
  EXPECTED_VERIFY_024_SHA256 双向一致); V2 锁 verify_interact_036.py
  字节 (docstring 段落不可静默删除); V3 mutant: 拿掉 docstring 中
  "update procedure" 关键短语后 V2 应 FAIL (字节锁兜底)。

verification checks
-------------------
- V0 self_sha: 本脚本自身 sha256 (pragma ``V0-SELF-SHA-SKIP`` 行跳过)
  必须等于 ``EXPECTED_SELF_FILE_SHA``。
- V1 verify_024 file sha: ``scripts/verify_interact_024.py`` 整文件
  raw sha256 必须等于 ``EXPECTED_VERIFY_024_FILE_SHA``, 且必须等于
  ``verify_interact_036.EXPECTED_VERIFY_024_SHA256`` (双向一致)。
- V2 verify_036 file sha: ``scripts/verify_interact_036.py`` 整文件
  raw sha256 必须等于 ``EXPECTED_VERIFY_036_FILE_SHA`` (锁住 docstring
  内 update procedure 段落)。
- V3 docstring procedure anchors: verify_interact_036.py docstring 必须
  含 4 个关键短语 (update procedure / 重算实际 sha256 / 64-char full
  hex / 反模式), 任一缺失即 FAIL。
- V4 mutant proof: 把 docstring 内 "update procedure" 关键短语替换为
  随机串后, in-memory 计算 sha 应不再等于 EXPECTED_VERIFY_036_FILE_SHA
  (字节锁兜底反证)。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
VERIFY_024 = SCRIPTS / "verify_interact_024.py"
VERIFY_036 = SCRIPTS / "verify_interact_036.py"

EXPECTED_SELF_FILE_SHA = "81150185980cb51d8ce917061ac65ed4693ea2fe9c758b0a49cf0d7a0930059c"  # V0-SELF-SHA-SKIP
EXPECTED_VERIFY_024_FILE_SHA = (
    "9e5af28633783184a3b27a71e1218948e7c32aa232d1dd5551aeff2d9cb19e37"
)
EXPECTED_VERIFY_036_FILE_SHA = (
    "4f5e70b8e937c869ff48a857fe017e1edaa434da2274d0d6dbbf64edcc0ce9cd"
)

DOCSTRING_ANCHORS: Tuple[str, ...] = (
    "EXPECTED_VERIFY_024_SHA256 update procedure",
    "重算实际 sha256",
    "64-char hex",
    "反模式",
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(
        f"[verify_infra_034_backlog_verify_036_v4_sha_stale_sync][{mark}] {tag} {detail}",
        flush=True,
    )
    _results.append((tag, ok, detail))


def _self_sha_skip_pragma() -> str:
    src = Path(__file__).resolve().read_text(encoding="utf-8")
    pragma = "# V0-SELF-SHA-SKIP"
    kept = [
        line
        for line in src.splitlines(keepends=True)
        if not line.rstrip("\n").rstrip().endswith(pragma)
    ]
    return hashlib.sha256("".join(kept).encode("utf-8")).hexdigest()


def v0_self_sha() -> None:
    actual = _self_sha_skip_pragma()
    ok = actual == EXPECTED_SELF_FILE_SHA
    _emit(
        "V0_self_file_sha_lock",
        ok,
        f"actual={actual[:16]} expect={EXPECTED_SELF_FILE_SHA[:16]}",
    )


def _verify_036_expected_sha_constant() -> str:
    """Parse verify_interact_036.py for the EXPECTED_VERIFY_024_SHA256 literal."""
    import ast

    src = VERIFY_036.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "EXPECTED_VERIFY_024_SHA256":
                    v = node.value
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        return v.value
                    if isinstance(v, ast.JoinedStr):
                        # not expected; degrade gracefully
                        return ""
                    # parenthesized single-string concat: ast.Constant inside
                    # handled by ast.literal_eval fallback
                    try:
                        return ast.literal_eval(v)
                    except Exception:
                        return ""
    return ""


def v1_verify_024_file_sha() -> None:
    if not VERIFY_024.is_file():
        _emit("V1_verify_024_file_sha", False, f"missing: {VERIFY_024}")
        return
    got = hashlib.sha256(VERIFY_024.read_bytes()).hexdigest()
    embedded = _verify_036_expected_sha_constant()
    ok_file = got == EXPECTED_VERIFY_024_FILE_SHA
    ok_cross = embedded == EXPECTED_VERIFY_024_FILE_SHA
    ok = ok_file and ok_cross
    _emit(
        "V1_verify_024_file_sha",
        ok,
        f"file={got[:16]} expect={EXPECTED_VERIFY_024_FILE_SHA[:16]} "
        f"embedded_in_036={embedded[:16]} cross_ok={ok_cross}",
    )


def v2_verify_036_file_sha() -> None:
    if not VERIFY_036.is_file():
        _emit("V2_verify_036_file_sha", False, f"missing: {VERIFY_036}")
        return
    got = hashlib.sha256(VERIFY_036.read_bytes()).hexdigest()
    ok = got == EXPECTED_VERIFY_036_FILE_SHA
    _emit(
        "V2_verify_036_file_sha",
        ok,
        f"got={got[:16]} expect={EXPECTED_VERIFY_036_FILE_SHA[:16]}",
    )


def v3_docstring_procedure_anchors() -> None:
    src = VERIFY_036.read_text(encoding="utf-8")
    missing = [a for a in DOCSTRING_ANCHORS if a not in src]
    ok = not missing
    _emit(
        "V3_docstring_procedure_anchors",
        ok,
        f"anchors_required={len(DOCSTRING_ANCHORS)} missing={missing}",
    )


def v4_mutant_proof() -> None:
    """Mutant: in-memory 替换 docstring 第一个 anchor → 字节锁应 FAIL."""
    src_bytes = VERIFY_036.read_bytes()
    target_anchor = DOCSTRING_ANCHORS[0].encode("utf-8")
    if target_anchor not in src_bytes:
        _emit(
            "V4_mutant_proof",
            False,
            f"anchor not present pre-mutation: {DOCSTRING_ANCHORS[0]!r}",
        )
        return
    mutated = src_bytes.replace(target_anchor, b"MUTATED_ANCHOR_XXXX", 1)
    mutated_sha = hashlib.sha256(mutated).hexdigest()
    ok = mutated_sha != EXPECTED_VERIFY_036_FILE_SHA
    _emit(
        "V4_mutant_proof",
        ok,
        f"mutated_sha={mutated_sha[:16]} expect_lock={EXPECTED_VERIFY_036_FILE_SHA[:16]} "
        f"differs={ok}",
    )


def main() -> int:
    v0_self_sha()
    v1_verify_024_file_sha()
    v2_verify_036_file_sha()
    v3_docstring_procedure_anchors()
    v4_mutant_proof()

    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    print(
        f"[verify_infra_034_backlog_verify_036_v4_sha_stale_sync] summary "
        f"total={total} failed={len(failed)}",
        flush=True,
    )
    if failed:
        print(
            f"[verify_infra_034_backlog_verify_036_v4_sha_stale_sync] failed tags: {failed}",
            flush=True,
        )
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
