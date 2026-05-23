#!/usr/bin/env python3
"""verify_infra_P306: bump_self_file_sha helper script rollout 验证 (sim-only).

infra-P306-bump-helper-script-rollout (phase-64 #4):
风格对齐 V2/V4 已有的独立 bump 助手, 给 V8 self-file-sha 锁配通用 bump 助手
``scripts/bump_self_file_sha.py``. 本 verify 锁住:

V1  bump_self_file_sha.py 存在
V2  bump_self_file_sha.py 顶层导入了正确算法记号 (PRAGMA 常量 + 等价计算)
V3  dry-run 全扫 rc==0 且 stdout 含 candidates>=2 (verify_infra_033 + verify_infra_035)
V4  dry-run 全扫 stdout 不含 'FAIL' 字符且不含 'APPLIED' (默认不写盘)
V5  --target 指向不存在文件时 rc==2
V6  --target 指向不含 pragma 的脚本时 rc==2 (用 verify_infra_034.py 反证)
V7  helper 的 _self_sha_excluding_pragma 算法与 verify_infra_035._self_sha_skip_sentinel 同结果
V8  helper 的 _self_sha_excluding_pragma 算法与 verify_infra_033._self_file_sha_excluding_pragma_line 同结果
V9  helper 字面常量 EXPECTED_SELF_FILE_SHA 真常量行 regex 与 verify_infra_035 V10 风格兼容
V10 EXPECTED_BUMP_SELF_SHA_HELPER_FILE_SHA 字面常量 sha256 锁 bump_self_file_sha.py 整体内容 (drift 抓手)

verify-only / default-OFF 友好 / 无业务源码改动.
"""
from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SELF = Path(__file__).resolve()
BUMP_HELPER = SCRIPTS / "bump_self_file_sha.py"
VERIFY_033 = SCRIPTS / "verify_infra_033_lock_doc_rollout.py"
VERIFY_034 = SCRIPTS / "verify_infra_034.py"
VERIFY_035 = SCRIPTS / "verify_infra_035.py"

# V10: 整体 sha256 锁 bump_self_file_sha.py. 任何漂移需同步 bump 本常量.
EXPECTED_BUMP_SELF_SHA_HELPER_FILE_SHA = (
    "707225403911bff7cec7973c25a5d23b383edce074652f7f6b5efe26775ab2ba"
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    _results.append((tag, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P306] {status} {tag}: {detail}", flush=True)


def _load_helper_module():
    """动态 import scripts/bump_self_file_sha.py 拿到其符号."""
    spec = importlib.util.spec_from_file_location(
        "bump_self_file_sha", str(BUMP_HELPER)
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"[verify_infra_P306] FAIL: helper import 失败: {e}", file=sys.stderr)
        return None
    return mod


# ---------------------------------------------------------------------------
# V1: helper 存在
# ---------------------------------------------------------------------------
def v1_helper_exists() -> None:
    _emit("V1_helper_exists", BUMP_HELPER.is_file(), str(BUMP_HELPER.relative_to(ROOT)))


# ---------------------------------------------------------------------------
# V2: helper 顶层有 PRAGMA + 等价算法函数
# ---------------------------------------------------------------------------
def v2_helper_has_pragma_and_algo() -> None:
    if not BUMP_HELPER.is_file():
        _emit("V2_helper_has_pragma_and_algo", False, "helper missing")
        return
    src = BUMP_HELPER.read_text(encoding="utf-8")
    has_pragma_const = 'PRAGMA = "# V8-SELF-SHA-SKIP"' in src
    has_algo_fn = "def _self_sha_excluding_pragma" in src
    ok = has_pragma_const and has_algo_fn
    _emit(
        "V2_helper_has_pragma_and_algo",
        ok,
        f"pragma_const={has_pragma_const} algo_fn={has_algo_fn}",
    )


# ---------------------------------------------------------------------------
# V3: dry-run 全扫 rc==0 且 candidates>=2
# ---------------------------------------------------------------------------
def v3_dryrun_all() -> None:
    if not BUMP_HELPER.is_file():
        _emit("V3_dryrun_all", False, "helper missing")
        return
    proc = subprocess.run(
        [sys.executable, str(BUMP_HELPER)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = proc.stdout + proc.stderr
    rc_ok = proc.returncode == 0
    cand_line = next((ln for ln in out.splitlines() if ln.startswith("candidates=")), "")
    cand_n = 0
    if cand_line:
        try:
            cand_n = int(cand_line.split()[0].split("=")[1])
        except Exception:
            pass
    ok = rc_ok and cand_n >= 2
    _emit(
        "V3_dryrun_all",
        ok,
        f"rc={proc.returncode} candidates={cand_n}",
    )


# ---------------------------------------------------------------------------
# V4: dry-run 全扫 stdout 不含 FAIL / APPLIED
# ---------------------------------------------------------------------------
def v4_dryrun_safe() -> None:
    if not BUMP_HELPER.is_file():
        _emit("V4_dryrun_safe", False, "helper missing")
        return
    proc = subprocess.run(
        [sys.executable, str(BUMP_HELPER)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = proc.stdout
    has_fail = "FAIL" in out
    has_applied = "APPLIED" in out
    ok = (not has_fail) and (not has_applied)
    _emit("V4_dryrun_safe", ok, f"has_FAIL={has_fail} has_APPLIED={has_applied}")


# ---------------------------------------------------------------------------
# V5: --target 不存在 → rc==2
# ---------------------------------------------------------------------------
def v5_target_missing() -> None:
    if not BUMP_HELPER.is_file():
        _emit("V5_target_missing", False, "helper missing")
        return
    proc = subprocess.run(
        [
            sys.executable,
            str(BUMP_HELPER),
            "--target",
            "scripts/__no_such_verify_xyz__.py",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    _emit("V5_target_missing", proc.returncode == 2, f"rc={proc.returncode}")


# ---------------------------------------------------------------------------
# V6: --target 指向不含 pragma 的脚本 → rc==2 (反证)
# ---------------------------------------------------------------------------
def v6_target_no_pragma() -> None:
    if not BUMP_HELPER.is_file() or not VERIFY_034.is_file():
        _emit("V6_target_no_pragma", False, "helper or verify_034 missing")
        return
    # 先确认 verify_034 不含 pragma
    src = VERIFY_034.read_text(encoding="utf-8")
    if "# V8-SELF-SHA-SKIP" in src:
        _emit(
            "V6_target_no_pragma",
            False,
            "前置失败: verify_infra_034 居然含 pragma, 该 V6 反证基准失效",
        )
        return
    proc = subprocess.run(
        [
            sys.executable,
            str(BUMP_HELPER),
            "--target",
            str(VERIFY_034.relative_to(ROOT)),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    _emit("V6_target_no_pragma", proc.returncode == 2, f"rc={proc.returncode}")


# ---------------------------------------------------------------------------
# V7/V8: helper 算法与 verify_infra_035 / verify_infra_033 同结果
# ---------------------------------------------------------------------------
def _algo_035(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines(keepends=True)
    _PRAGMA = "# V8-SELF-SHA-SKIP"
    kept = [ln for ln in lines if not ln.rstrip().endswith(_PRAGMA)]
    return hashlib.sha256("".join(kept).encode("utf-8")).hexdigest()


def _algo_033(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not ln.rstrip("\n").rstrip().endswith("# V8-SELF-SHA-SKIP")]
    return hashlib.sha256("".join(kept).encode("utf-8")).hexdigest()


def v7_algo_equiv_035() -> None:
    mod = _load_helper_module()
    if mod is None or not VERIFY_035.is_file():
        _emit("V7_algo_equiv_035", False, "helper or verify_035 missing")
        return
    a = mod._self_sha_excluding_pragma(VERIFY_035)
    b = _algo_035(VERIFY_035)
    _emit("V7_algo_equiv_035", a == b, f"helper={a[:16]} ref={b[:16]}")


def v8_algo_equiv_033() -> None:
    mod = _load_helper_module()
    if mod is None or not VERIFY_033.is_file():
        _emit("V8_algo_equiv_033", False, "helper or verify_033 missing")
        return
    a = mod._self_sha_excluding_pragma(VERIFY_033)
    b = _algo_033(VERIFY_033)
    _emit("V8_algo_equiv_033", a == b, f"helper={a[:16]} ref={b[:16]}")


# ---------------------------------------------------------------------------
# V9: helper 的 _RE_SELF_LINE 能命中 verify_infra_035 真常量行
# ---------------------------------------------------------------------------
def v9_regex_compat() -> None:
    mod = _load_helper_module()
    if mod is None or not VERIFY_035.is_file():
        _emit("V9_regex_compat", False, "helper or verify_035 missing")
        return
    src = VERIFY_035.read_text(encoding="utf-8")
    hits = [
        i
        for i, ln in enumerate(src.splitlines(keepends=False))
        if mod._RE_SELF_LINE.match(ln)
    ]
    ok = len(hits) == 1
    _emit("V9_regex_compat", ok, f"matches={len(hits)} at_lines={hits[:3]}")


# ---------------------------------------------------------------------------
# V10: 整体 sha256 锁 bump_self_file_sha.py drift 抓手
# ---------------------------------------------------------------------------
def v10_helper_sha_lock() -> None:
    if not BUMP_HELPER.is_file():
        _emit("V10_helper_sha_lock", False, "helper missing")
        return
    actual = hashlib.sha256(BUMP_HELPER.read_bytes()).hexdigest()
    if EXPECTED_BUMP_SELF_SHA_HELPER_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V10_helper_sha_lock",
            False,
            f"placeholder; bump EXPECTED_BUMP_SELF_SHA_HELPER_FILE_SHA={actual}",
        )
        return
    ok = actual == EXPECTED_BUMP_SELF_SHA_HELPER_FILE_SHA
    _emit(
        "V10_helper_sha_lock",
        ok,
        f"actual={actual[:16]} expect={EXPECTED_BUMP_SELF_SHA_HELPER_FILE_SHA[:16]}",
    )


def main() -> int:
    v1_helper_exists()
    v2_helper_has_pragma_and_algo()
    v3_dryrun_all()
    v4_dryrun_safe()
    v5_target_missing()
    v6_target_no_pragma()
    v7_algo_equiv_035()
    v8_algo_equiv_033()
    v9_regex_compat()
    v10_helper_sha_lock()
    fail = [t for t, ok, _ in _results if not ok]
    if fail:
        print(
            f"[verify_infra_P306] OVERALL FAIL: {len(fail)}/{len(_results)} failed: {fail}",
            flush=True,
        )
        return 1
    print(
        f"[verify_infra_P306] OVERALL PASS: {len(_results)}/{len(_results)} checks",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
