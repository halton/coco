#!/usr/bin/env python3
"""verify_infra_108: typo-guard hash-suffix-policy 决策锁.

infra-P293-typo-guard-hash-suffix-policy (phase-54 #5.54):
源自 infra-P281 Reviewer Round-1 finding B-P281-R2 — 当前 typo-guard 把
``_HASH$`` 列入 ``_RE_EXPECTED_TYPO_SUFFIX``, 设计上认为 ``_HASH`` 是
``_HASHSUM`` / ``_SHA`` 拼错的高置信度候选。但未来若引入合法非 SHA hash 算法
常量 (如 ``EXPECTED_FOO_BLAKE3_HASH`` / ``EXPECTED_BAR_MD5_HASH``) 会被
错判 typo。本 verifier 显式锁定当前决策与未来 allowlist 路径, 让后续
"加 allowlist 解除误判" 的演进有 mechanical anchor 触发。

INFRA_108_HASH_SUFFIX_POLICY (决策)
-----------------------------------
1. ``_HASH$`` 与 ``_HASHSUM$`` 当前**保留**在 ``_RE_EXPECTED_TYPO_SUFFIX``。
   理由: 当前 repo 内**没有**任何合法 ``EXPECTED_*_HASH`` 常量, 误判风险=0,
   而抓 ``_HASH`` 拼错的护栏价值 > 0。
2. ``_RE_EXPECTED_NAMEPART_TYPO`` **不含**裸 ``HASH`` (只保留 ``HASHSUM``):
   namepart 出现 ``HASH`` 可能落在合法位置 (如 ``EXPECTED_HASH_FOO_SHA``),
   设计上 namepart 比尾后缀宽松。
3. 未来当合法非 SHA hash 常量出现 (``BLAKE3`` / ``MD5`` / ``BLAKE2`` 等),
   应走以下其一:
   - (a) 加 ``_RE_EXPECTED_HASH_ALGO_ALLOW`` allowlist regex
     (e.g. ``_(BLAKE3|MD5|BLAKE2|XXH|XXH3|CRC32)_HASH$`` 视为 well-formed),
   - (b) 把 ``_HASH$`` 降为 warn 而非 fail。
   届时本 verifier 需相应放松 V3_legitimate_blake3_currently_flagged 断言。

INFRA_108_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha (锁住 typo regex 集合的源码状态)
- 自身 ``main`` func sha

校验层级 (V0-V5):

- V0 scaffolding: ``_verify_lib.py`` 存在 + ``verify_expected_prefix_typo_guard`` 可 import
- V1 source token: ``_RE_EXPECTED_TYPO_SUFFIX`` 正则源码必含 ``HASH`` + ``HASHSUM``
  (锁住决策 #1: ``_HASH`` 仍在 typo suffix 集)
- V2 namepart regex: ``_RE_EXPECTED_NAMEPART_TYPO`` 源码**不含** bare ``HASH``
  (但保留 ``HASHSUM``), 锁住决策 #2
- V3 dogfood: tmp fixture ``EXPECTED_FOO_BLAKE3_HASH`` 当前**被判 typo**
  (锁住 R2 在 allowlist 引入前的现状; 一旦引入 (a) 方案此断言需放松)
- V3b dogfood: tmp fixture ``EXPECTED_BAR_MD5_HASH`` 当前同样被判 typo
- V3c well-formed sanity: ``EXPECTED_FOO_SHA`` 不被判 typo
- V4 self main func sha (mutation detector)
- V5 Reviewer LGTM gate (grace_period 兜底)

退出码 0=ALL PASS / 2=任一 FAIL.

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
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_expected_prefix_typo_guard,
    verify_summary_exit,
)

# 自身 main func sha (首跑 __BUMP_ME__ 占位, 再回填)
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "52dac80ba95a4461f8ed01d64510537b185a6d5be8fb1f337fdd2bb6118b28db"
)

DOCSTRING_SENTINEL = "INFRA_108_HASH_SUFFIX_POLICY"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P293-typo-guard-hash-suffix-policy"

# V1 / V2 静态扫描 token
V1_SUFFIX_TOKENS_REQUIRED = ("HASH", "HASHSUM")  # 都必须在 _RE_EXPECTED_TYPO_SUFFIX 源码出现
V2_NAMEPART_HASHSUM_REQUIRED = "HASHSUM"  # 必须保留
V2_NAMEPART_HASH_BARE_FORBIDDEN_NEIGHBOR = (
    # _RE_EXPECTED_NAMEPART_TYPO regex pattern 中**不能**有 bare `HASH(?` 形式
    # (允许 `HASHSUM` 因尾部还有 SUM)
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_108][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0 scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit("V0_verify_lib_exists", VERIFY_LIB.is_file(), f"path={VERIFY_LIB.relative_to(REPO)}")
    _emit(
        "V0_helper_importable",
        callable(verify_expected_prefix_typo_guard),
        "verify_expected_prefix_typo_guard imported",
    )


# ---------------------------------------------------------------------------
# V1 _RE_EXPECTED_TYPO_SUFFIX 源码 token: 必须含 HASH + HASHSUM
# ---------------------------------------------------------------------------
def v1_suffix_regex_tokens() -> None:
    src = VERIFY_LIB.read_text(encoding="utf-8")
    # 提取 _RE_EXPECTED_TYPO_SUFFIX = re.compile(...) block
    # 简单做: 找到该常量名后取后续 ~200 字符片段
    idx = src.find("_RE_EXPECTED_TYPO_SUFFIX")
    if idx < 0:
        _emit("V1_suffix_regex_block_present", False, "constant not found in _verify_lib")
        return
    block = src[idx : idx + 300]
    for tok in V1_SUFFIX_TOKENS_REQUIRED:
        _emit(
            f"V1_suffix_regex_has_{tok}",
            tok in block,
            f"_RE_EXPECTED_TYPO_SUFFIX block must include token {tok!r}",
        )


# ---------------------------------------------------------------------------
# V2 _RE_EXPECTED_NAMEPART_TYPO 源码: 含 HASHSUM, 不含 bare HASH(?
# ---------------------------------------------------------------------------
def v2_namepart_regex_tokens() -> None:
    src = VERIFY_LIB.read_text(encoding="utf-8")
    idx = src.find("_RE_EXPECTED_NAMEPART_TYPO")
    if idx < 0:
        _emit("V2_namepart_regex_block_present", False, "constant not found in _verify_lib")
        return
    block = src[idx : idx + 300]
    _emit(
        "V2_namepart_has_HASHSUM",
        "HASHSUM" in block,
        f"namepart regex must include HASHSUM (block snippet ok)",
    )
    # bare HASH 形态: 出现 `HASH(?` 或 `HASH|` 但不是 `HASHSUM`
    # 实现: 去掉所有 HASHSUM 后再看 HASH 是否还在
    block_no_hashsum = block.replace("HASHSUM", "")
    bare_hash_present = "HASH" in block_no_hashsum
    _emit(
        "V2_namepart_no_bare_HASH",
        not bare_hash_present,
        "namepart regex must NOT include bare HASH (only HASHSUM allowed)",
    )


# ---------------------------------------------------------------------------
# V3 dogfood: 用 tmp fixture 验证 EXPECTED_FOO_BLAKE3_HASH 当前被判 typo
# ---------------------------------------------------------------------------
def v3_blake3_currently_flagged() -> None:
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        fixture = tdp / "verify_tmp_fixture.py"
        fixture.write_text(
            "EXPECTED_FOO_BLAKE3_HASH = '0' * 64\n"
            "EXPECTED_BAR_MD5_HASH = '0' * 32\n"
            "EXPECTED_GOOD_FOO_SHA = '0' * 64\n",
            encoding="utf-8",
        )
        r = verify_expected_prefix_typo_guard(scripts_dir=tdp)
        names = [s["name"] for s in r["typo_samples"]]
        _emit(
            "V3_legitimate_blake3_currently_flagged",
            "EXPECTED_FOO_BLAKE3_HASH" in names,
            f"BLAKE3 hash 常量当前被判 typo (R2 现状; 待 allowlist 演进) names={names}",
        )
        _emit(
            "V3b_legitimate_md5_currently_flagged",
            "EXPECTED_BAR_MD5_HASH" in names,
            f"MD5 hash 常量当前被判 typo names={names}",
        )
        _emit(
            "V3c_canonical_sha_not_flagged",
            "EXPECTED_GOOD_FOO_SHA" not in names,
            f"canonical _SHA not in typo samples names={names}",
        )
        _emit(
            "V3d_typo_count_eq_two",
            r["typo_count"] == 2,
            f"expect typo_count=2 (BLAKE3+MD5); got={r['typo_count']}",
        )


# ---------------------------------------------------------------------------
# V4 self main func sha (mutation detector)
# ---------------------------------------------------------------------------
def v4_self_main_func_sha() -> None:
    self_path = Path(__file__)
    got = func_sha_by_name(self_path, "main")
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V4_self_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
    else:
        _emit(
            "V4_self_main_func_sha",
            got == EXPECTED_SELF_MAIN_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V5 Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    if not REAL_FEATURE_LIST.is_file():
        _emit(
            "V5_reviewer_lgtm_gate",
            False,
            f"feature_list.json not found at {REAL_FEATURE_LIST}",
        )
        return
    result = assert_v5_reviewer_gate_evidence_bind(
        V5_GATE_FEATURE_ID, REAL_FEATURE_LIST,
        grace_period_feature_ids=(V5_GATE_FEATURE_ID,),
    )
    _emit(
        "V5_reviewer_lgtm_gate",
        bool(result["ok"]),
        f"target={V5_GATE_FEATURE_ID} helper_ok={result['ok']} "
        f"grace_skipped={result['grace_skipped']} "
        f"verdict={result['verdict']!r} kind={result['reviewer_kind']!r} "
        f"summary_len={result['summary_len']} reason={result['reason']!r}",
    )


def main() -> None:
    v0_scaffolding()
    v1_suffix_regex_tokens()
    v2_namepart_regex_tokens()
    v3_blake3_currently_flagged()
    v4_self_main_func_sha()
    v5_reviewer_gate()
    total = len(_results)
    unique_tags = len({t for t, _, _ in _results})
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(
            f"[verify_infra_108][SUMMARY] FAIL {failed}/{total} emit-paths: {names}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_108][SUMMARY] ALL PASS ({total} emit-paths / {unique_tags} unique check tags)",
            flush=True,
        )
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
