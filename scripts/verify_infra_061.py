#!/usr/bin/env python3
"""verify_infra_061 V0-V5: EXPECTED_* 前缀 typo guard 行为锁.

infra-P281-expected-prefix-typo-guard (phase-37 #2.37): 源自 P276 Reviewer
blind spot。现有 V6 一致性规则 (verify_reverse_sha_lock_consistency /
verify_expected_pattern_consistency) 只识别**规范命名** ``EXPECTED_*_(SHA|
SHA256|SHA512)$``。若有人写成 ``EXPECTED_LIB_FILE_SHAH`` / ``EXPECTED_FUNC_HSA``
/ ``EXPECTED_FILE_SH``, 这些 typo 常量看似存在但**实际从未参与一致性校验**
（被 V6 静默放过，反向锁形同虚设）。

P281 修复路径:
1. ``_verify_lib.py`` 新增 helper ``verify_expected_prefix_typo_guard``,
   ast 扫描 ``scripts/verify_*.py`` 顶层 ``EXPECTED_*`` 常量名, 命中 typo
   后缀 (``_SHAH/_HSA/_SH/...``) 或中部 typo 拼写即 fail-flag。
2. 本脚本 verify_infra_061 V0-V5 完整锁该 helper + dogfood 当前仓库
   ``all_well_formed=True``。

INFRA_061_SHA_LOCKS
-------------------
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``verify_expected_prefix_typo_guard`` func sha: EXPECTED_TYPO_GUARD_FUNC_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

校验层级 (V0-V5):

- V0 scaffolding: _verify_lib.py 存在 + verify_expected_prefix_typo_guard
  公开 (def 形 + ``__all__`` 含入口名) + 本脚本 ``__main__`` 入口可执行
- V1 docstring sentinel ``INFRA_061_SHA_LOCKS`` + 本脚本 v4_behavior func
  sha 自锁
- V2 _verify_lib.py file sha
- V3 verify_expected_prefix_typo_guard canonical func sha
- V4 行为 (dogfood + tmp 正/反例):
  - 真实 helper(scripts/) → all_well_formed=True, typo_count=0
    (若 fail 说明仓库新引入 typo, 打出 typo_samples 供修复)
  - tmp 反例: helper 应能识别 typo 后缀
    (验证规则覆盖 ``_SHAH`` / ``_HSA`` / ``_SH``)
  - tmp 正例: helper 应放过合法 sha 常量 (``_SHA`` / ``_SHA256``)
    与合法非 sha 常量 (``EXPECTED_PALETTE`` 类)
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
    verify_expected_prefix_typo_guard,
)

# infra-061 sha lock 常量 (V2 / V3)
EXPECTED_VERIFY_LIB_FILE_SHA = "a80af0088b53116bb10b672540a129f26d9b9fdf4dfeab862ddffb9170578525"
EXPECTED_TYPO_GUARD_FUNC_SHA = "41d9744902cd0caea4973245cf91597c577d6200f11650a12fe29105338eb17c"

# 本脚本 v4_behavior 自锁 (V1) — 首跑 __BUMP_ME__ 占位, 再回填
EXPECTED_V4_CHECKER_FUNC_SHA = "fd11a5eebf37a2722c39267939a10dc6d989486d0710e6b54979c520cb230e26"

DOCSTRING_SENTINEL = "INFRA_061_SHA_LOCKS"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_061][{mark}] {tag} {detail}", flush=True)
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
        "def verify_expected_prefix_typo_guard(" in src,
        "expect 'def verify_expected_prefix_typo_guard(' in lib",
    )
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
        "verify_expected_prefix_typo_guard" in all_names,
        f"__all__ contains {len(all_names)} names",
    )
    self_src = Path(__file__).read_text(encoding="utf-8")
    _emit(
        "V0_self_main_entry",
        'if __name__ == "__main__":' in self_src and "def main(" in self_src,
        "expect __main__ guard + def main()",
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
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V2_lib_file_sha",
            False,
            f"placeholder; bump EXPECTED_VERIFY_LIB_FILE_SHA={got}",
        )
        return
    _emit(
        "V2_lib_file_sha",
        got == EXPECTED_VERIFY_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: verify_expected_prefix_typo_guard canonical func sha
# ---------------------------------------------------------------------------
def v3_helper_func_sha() -> None:
    try:
        got = func_sha_by_name(LIB, "verify_expected_prefix_typo_guard")
    except Exception as e:
        _emit("V3_helper_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_TYPO_GUARD_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V3_helper_func_sha",
            False,
            f"placeholder; bump EXPECTED_TYPO_GUARD_FUNC_SHA={got}",
        )
        return
    _emit(
        "V3_helper_func_sha",
        got == EXPECTED_TYPO_GUARD_FUNC_SHA,
        f"got={got[:16]} expect={EXPECTED_TYPO_GUARD_FUNC_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V4: 行为 — dogfood 仓库 + tmp 正反例
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    # 1) dogfood: 扫真实 scripts/, 应 all_well_formed=True
    r_real = verify_expected_prefix_typo_guard(SCRIPTS)
    total = r_real.get("total_expected_consts", 0)
    wf = r_real.get("well_formed", 0)
    tc = r_real.get("typo_count", 0)
    samples = r_real.get("typo_samples", [])
    _emit(
        "V4_dogfood_schema_keys",
        all(
            k in r_real
            for k in (
                "total_expected_consts",
                "well_formed",
                "typo_count",
                "typo_samples",
                "all_well_formed",
            )
        ),
        f"keys={sorted(r_real.keys())}",
    )
    _emit(
        "V4_dogfood_total_positive",
        isinstance(total, int) and total > 0,
        f"total_expected_consts={total}",
    )
    _emit(
        "V4_dogfood_well_formed_eq_total",
        wf == total,
        f"well_formed={wf} total={total}",
    )
    _emit(
        "V4_dogfood_all_well_formed",
        r_real.get("all_well_formed") is True and tc == 0,
        f"all_well_formed={r_real.get('all_well_formed')} "
        f"typo_count={tc} samples={samples[:3]}",
    )

    # 2) tmp 反例: 写入临时 verify_*.py, 含三种 typo + 一个合法 sha + 一个合法非 sha
    tmp_root = Path(tempfile.mkdtemp(prefix="p281_typo_"))
    bad_py = tmp_root / "verify_xxx_999.py"
    bad_py.write_text(
        "EXPECTED_LIB_FILE_SHAH = '0' * 64\n"
        "EXPECTED_FUNC_HSA = '1' * 64\n"
        "EXPECTED_FILE_SH = '2' * 64\n"
        "EXPECTED_GOOD_FILE_SHA = '3' * 64\n"
        "EXPECTED_GOOD_PALETTE = ('a', 'b')\n",
        encoding="utf-8",
    )
    r_tmp = verify_expected_prefix_typo_guard(tmp_root)
    typo_names = sorted(s["name"] for s in r_tmp.get("typo_samples", []))
    expected_typos = sorted(
        ["EXPECTED_LIB_FILE_SHAH", "EXPECTED_FUNC_HSA", "EXPECTED_FILE_SH"]
    )
    _emit(
        "V4_tmp_typo_detected",
        r_tmp.get("typo_count") == 3
        and typo_names == expected_typos
        and r_tmp.get("all_well_formed") is False,
        f"typo_count={r_tmp.get('typo_count')} names={typo_names}",
    )
    _emit(
        "V4_tmp_well_formed_count",
        r_tmp.get("well_formed") == 2,
        f"well_formed={r_tmp.get('well_formed')} (expect 2: 1 good sha + 1 palette)",
    )
    _emit(
        "V4_tmp_typo_samples_have_path_lineno",
        all(
            isinstance(s.get("path"), str)
            and isinstance(s.get("lineno"), int)
            and isinstance(s.get("name"), str)
            and isinstance(s.get("reason"), str)
            for s in r_tmp.get("typo_samples", [])
        ),
        f"samples={r_tmp.get('typo_samples')}",
    )

    # 3) tmp 边界: 空 dir
    empty_dir = Path(tempfile.mkdtemp(prefix="p281_empty_"))
    r_empty = verify_expected_prefix_typo_guard(empty_dir)
    _emit(
        "V4_tmp_empty_dir",
        r_empty.get("total_expected_consts") == 0
        and r_empty.get("typo_count") == 0
        and r_empty.get("all_well_formed") is True,
        f"r_empty={r_empty}",
    )

    # 4) tmp 上限: 11 typo → 仅返回 10 samples
    many = tmp_root / "verify_yyy_999.py"
    many.write_text(
        "\n".join(f"EXPECTED_T{i}_SHAH = '0' * 64" for i in range(11)) + "\n",
        encoding="utf-8",
    )
    r_many = verify_expected_prefix_typo_guard(tmp_root)
    _emit(
        "V4_tmp_samples_capped_at_10",
        r_many.get("typo_count") == 3 + 11
        and len(r_many.get("typo_samples", [])) == 10,
        f"typo_count={r_many.get('typo_count')} "
        f"samples_len={len(r_many.get('typo_samples', []))}",
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
        print(f"[verify_infra_061][SUMMARY] FAIL {len(failed)}/{total}: {failed}", flush=True)
        return 1
    print(f"[verify_infra_061][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
