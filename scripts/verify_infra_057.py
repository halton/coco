#!/usr/bin/env python3
"""verify_infra_057: expected_pattern 锁独立一致性核验 V0-V5.

infra-049-backlog-expected-pattern-consistency-check (phase-36 #3.36, P276):
P271 (infra-052-pattern-expand) 把 scan_reverse_sha_locks 的 pattern 放宽到
匹配 ``VERIFY_<NNN>`` OR ``EXPECTED_.*_(FILE|FUNC)_SHA``, 返回 schema 加
``kind`` 字段 (``verify_id`` / ``expected_pattern``)。V6 一致性核验
(verify_reverse_sha_lock_consistency) 只对 ``kind=verify_id`` 做硬比对,
60+ 个 ``kind=expected_pattern`` 锁此前**没有独立的一致性 check** — 这是
反向锁体系的真盲区: scan 解析错误 / 顶层 Assign 与字符串字面错配 / scan 漏扫
某文件等问题都不会被现有 verify 抓到。

本 feature 加 helper ``_verify_lib.verify_expected_pattern_consistency``
弥补该盲区, 并由 verify_infra_057 V0-V5 完整锁该 helper。

INFRA_057_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_LIB_FILE_SHA
- ``verify_expected_pattern_consistency`` func sha:
  EXPECTED_VERIFY_EP_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: _verify_lib.py 存在 + verify_expected_pattern_consistency
  公开 (def 形 + ``__all__`` 含入口名)
- V1 docstring sentinel ``INFRA_057_SHA_LOCKS`` + 本脚本 v4_behavior func sha 自锁
- V2 _verify_lib.py file sha (本 feature 共享同源 EXPECTED_LIB_FILE_SHA)
- V3 verify_expected_pattern_consistency canonical func sha
- V4 行为: 直接调用 helper 断言 all_match=True + total>=30 + sample schema 正确;
  in-memory mutant 反证 (临时 monkey-patch scan 返回非法 sha) 必须破坏 all_match;
  正例: 实建 tmp scripts 目录构造 1 个 verify + 1 个 lib (含 EP 锁) helper 必须扫到。
- V5 Reviewer LGTM gate (print-only)

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).
"""
from __future__ import annotations

import ast
import hashlib
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    func_sha_by_name,
    scan_reverse_sha_locks,
    verify_expected_pattern_consistency,
)

# infra-057 sha lock 常量 (V2 / V3)
EXPECTED_LIB_FILE_SHA = "0398ae0cb5ab1b8cfd30588a635eefced7d9fa2f0cb610fbfba4f9ea4184f85b"
EXPECTED_VERIFY_EP_FUNC_SHA = "092041818bd1bcd3232b250d064aa52ff7d125112389629b1e3aecbab3d5754c"

# 本脚本 v4_behavior 自锁 (V1) — 首跑 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "3d44c4e07cb404a69e70eb91a4419938e84927be33f1fd41128b4c56f12e5eee"

DOCSTRING_SENTINEL = "INFRA_057_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_057][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_verify_lib_exists", LIB.is_file(), f"path={LIB}")
    if not LIB.is_file():
        return
    src = LIB.read_text(encoding="utf-8")
    _emit(
        "V0_helper_def_present",
        "def verify_expected_pattern_consistency(" in src,
        "expect 'def verify_expected_pattern_consistency(' in lib",
    )
    # __all__ 必须含入口名
    tree = ast.parse(src)
    all_names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for el in node.value.elts:
                            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                all_names.append(el.value)
    _emit(
        "V0_helper_in_all",
        "verify_expected_pattern_consistency" in all_names,
        f"__all__ contains {len(all_names)} names",
    )


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
        got = func_sha_by_name(self_path, "v4_behavior")
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
# V2: _verify_lib.py file sha
# ---------------------------------------------------------------------------
def v2_lib_file_sha() -> None:
    got = _file_sha(LIB)
    if EXPECTED_LIB_FILE_SHA == "__BUMP_ME__":
        _emit("V2_lib_file_sha", False, f"placeholder; bump EXPECTED_LIB_FILE_SHA={got}")
        return
    _emit(
        "V2_lib_file_sha",
        got == EXPECTED_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: verify_expected_pattern_consistency canonical func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "verify_expected_pattern_consistency")
    except Exception as e:
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_VERIFY_EP_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V3_helper_func_sha",
            False,
            f"placeholder; bump EXPECTED_VERIFY_EP_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_VERIFY_EP_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_EP_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为 — 真目录 + tmp 正例 + mutant 反证
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # 1) 真 scripts 目录: all_match=True, total>=30 (现网基线 60+)
    real = verify_expected_pattern_consistency(SCRIPTS)
    _emit(
        "V4_real_all_match",
        real.get("all_match") is True,
        f"all_match={real.get('all_match')} unresolved={len(real.get('unresolved', []))} "
        f"orphans={len(real.get('orphan_const_names', []))} missing={len(real.get('missing_assignment', []))}",
    )
    total = real.get("total_expected_pattern_locks", 0)
    _emit("V4_real_total_ge_30", total >= 30, f"total={total}")

    # sample schema: 5 条, 每条必有 file/const_name/sha_hex/kind=expected_pattern
    sample = real.get("sample", [])
    ok_schema = (
        isinstance(sample, list)
        and len(sample) >= 1
        and all(
            isinstance(s, dict)
            and "file" in s and "const_name" in s and "sha_hex" in s
            and s.get("kind") == "expected_pattern"
            for s in sample
        )
    )
    _emit("V4_sample_schema", ok_schema, f"sample_len={len(sample)}")

    # 2) tmp 正例: 自建一个含 EP 锁的 verify 脚本 + lib, helper 必须扫到该锁
    with tempfile.TemporaryDirectory() as td:
        tmp_scripts = Path(td) / "scripts"
        tmp_scripts.mkdir()
        # 必须有 _verify_lib.py 才有 scan 入口? 不, scan 只看 verify_*.py + _verify_lib.py.
        # 这里建一个 verify_xxx.py 即可。
        (tmp_scripts / "verify_demo_999.py").write_text(
            'EXPECTED_DEMO_FILE_SHA = "'+ "a"*64 +'"\n', encoding="utf-8"
        )
        r2 = verify_expected_pattern_consistency(tmp_scripts)
        _emit(
            "V4_tmp_positive_scan",
            r2.get("total_expected_pattern_locks") == 1 and r2.get("all_match") is True,
            f"total={r2.get('total_expected_pattern_locks')} all_match={r2.get('all_match')}",
        )

    # 3) mutant 反证: 在 tmp 目录里建一个**正确的 ast Assign EP**, 同时 scan 拿不到它
    #    (通过把它写成不被 scan_reverse_sha_locks 单行 regex 接受的形式: 如包在条件块里 / 多行赋值)
    #    构造 orphan: 用 tuple 形式但 hex 在第二行 — scan 应该能扫到 (tuple-open regex), 排除
    #    更可靠的 mutant 是: 在 tmp 里建一个被 _RE_REVLOCK_SINGLELINE 匹配但 SHA 大写
    #    -> 不会被 scan 接受 (regex 只允许 [0-9a-f]), 但 ast 走 isinstance Constant + 我们 helper 内
    #    也要求 lowercase 0-9a-f, 所以 helper 也不会 ast_observed_ep 加入它 -> 不构成 orphan.
    #    改用更直接路径: monkey-patch scan_reverse_sha_locks 返回空列表, 同时该 lib 在 ast
    #    扫描中能看到 verify_expected_pattern_consistency 自身定义内部不含 EP. 真正可靠的反证:
    #    在 tmp 里建一个含 EP 的 verify_*.py, 然后**临时 monkey-patch** scan_reverse_sha_locks
    #    使返回空 -> ast_observed_ep 仍有 1 项, scan_observed_ep 为空 -> orphan 出现 -> all_match=False
    import _verify_lib as lib_mod
    original_scan = lib_mod.scan_reverse_sha_locks
    with tempfile.TemporaryDirectory() as td2:
        tmp_scripts2 = Path(td2) / "scripts"
        tmp_scripts2.mkdir()
        (tmp_scripts2 / "verify_demo_998.py").write_text(
            'EXPECTED_MUTANT_FILE_SHA = "'+ "b"*64 +'"\n', encoding="utf-8"
        )
        try:
            lib_mod.scan_reverse_sha_locks = lambda _d: []  # type: ignore
            r3 = verify_expected_pattern_consistency(tmp_scripts2)
        finally:
            lib_mod.scan_reverse_sha_locks = original_scan  # type: ignore
    orphans3 = r3.get("orphan_const_names", [])
    _emit(
        "V4_mutant_scan_returns_empty",
        r3.get("all_match") is False and len(orphans3) == 1 and orphans3[0].get("const_name") == "EXPECTED_MUTANT_FILE_SHA",
        f"all_match={r3.get('all_match')} orphans={orphans3}",
    )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
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
    v2_lib_file_sha()
    v3_helper_func_sha()
    v4_behavior()
    v5_reviewer_gate()
    total = len(_results)
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_057][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_057][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
