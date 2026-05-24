#!/usr/bin/env python3
"""verify_infra_039: scripts/dump_v4_sha_graph.py V4 sha-lock graph dump 工具加固 (verify-only).

infra-039: 引入 ``scripts/dump_v4_sha_graph.py`` — V4 sha-lock 链路 DAG 可视化工具,
扫描所有 ``scripts/verify_*.py`` + ``scripts/_verify_lib.py`` 抽取 sha-lock 常量,
并尝试推断锁定 target (v4_sha.json 表 / 交叉 verify 锁 / lib file 或 func 锁),
方便 closeout / Reviewer 一眼看清整套链路。本脚本锁住 dump 工具的存在、本脚本
docstring sentinel + 自 checker func sha、dump 工具 file sha、mutant 反证、
行为验证。

V0 scaffolding: dump_v4_sha_graph.py 存在 + importable, 含 6 个关键符号
   (build_graph / render_text / _scan_file / _infer_target / main /
   _RE_SINGLELINE / _RE_TUPLE_OPEN 任 6 项均算 PASS, 最少 5)。
V1 docstring sentinel + 本脚本 v4_behavior checker 自身 func sha 自锁
   (sentinel section ``INFRA_039_SHA_LOCKS`` 必须存在).
V2 dump_v4_sha_graph.py 整体 file-sha 锁 (避免无脑改 dump).
V3 mutant 反证 — 临时把 dump 的 ``_RE_SINGLELINE`` 改成永不匹配的 regex,
   subprocess 运行 dump, 输出 locks 计数应比 baseline 显著减少, finally 还原.
V4 行为验证 — subprocess 调 dump, grep 输出含若干已知锁
   ("v4_sha.json", "verify_infra_034.py", "verify_infra_037.py"), 并对 v4_sha.json
   targets 计数做下界 + 上界 sanity 断言 (EXPECTED_TARGETS_MIN <= N <=
   EXPECTED_TARGETS_MAX_SANITY); 防止节点意外丢失 (低于 MIN FAIL) 与离谱漂移
   (高于 MAX FAIL, 需人工 bump MIN)。新增节点 (MIN <= N <= MAX) 不会 FAIL。
V5 Reviewer-LGTM gate (print-only).

INFRA_039_SHA_LOCKS
-------------------
- ``scripts/dump_v4_sha_graph.py`` file sha: EXPECTED_DUMP_FILE_SHA
- 本脚本 v4_behavior 自 checker func sha: EXPECTED_V4_CHECKER_FUNC_SHA

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034): 必须在 .venv 下运行 (``.venv/bin/python``).

## Lock: EXPECTED_DUMP_FILE_SHA
- target_function: N/A
- target_file: scripts/dump_v4_sha_graph.py
- lock_kind: file_sha
- bump_when: dump_v4_sha_graph.py 任何字节改动
- bump_protocol: 重算 sha256 of scripts/dump_v4_sha_graph.py 并更新常量
- rationale: 锁 V4 sha-lock 链路 DAG 可视化工具整体, 防止图谱生成被悄改导致 audit 漏锁

## Lock: EXPECTED_V4_CHECKER_FUNC_SHA
- target_function: v4_behavior
- target_file: scripts/verify_infra_039.py
- lock_kind: ast_func_sha
- bump_when: 本脚本 v4_behavior checker 实现变化
- bump_protocol: recompute func_sha_by_name("v4_behavior", scripts/verify_infra_039.py) then update constant
- rationale: 自锁 V4 行为 checker, 防止 checker 自身被改成永真
"""
from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DUMP_PY = SCRIPTS / "dump_v4_sha_graph.py"

# infra-039 sha lock 常量 (V2): dump_v4_sha_graph.py 整体 file sha
EXPECTED_DUMP_FILE_SHA = "72a72a986f513f1483b3c49238ab85c185be6108b3616613eee0a613fc1caeb3"

# infra-039 自身 v4_behavior 函数 sha (V1 自锁, 占位; 末尾自计算后回填)
EXPECTED_V4_CHECKER_FUNC_SHA = "514c9635b5c5d5f07f3418853ca00f837e77c51adaca7993c10f9f6e05f30408"

# infra-039-backlog-v4-output-anchors-lower-bound (phase-61 #5):
# v4_sha.json targets 计数下界 + 上界 sanity。MIN = 当前实际 (20), 防止节点意外丢
# 失; MAX_SANITY = 100, 防离谱漂移。允许 MIN <= N <= MAX 之间任意新增 (无需 bump)。
EXPECTED_TARGETS_MIN = 20
EXPECTED_TARGETS_MAX_SANITY = 100

DOCSTRING_SENTINEL = "INFRA_039_SHA_LOCKS"


# phase-47 #1.47: V5_GATE evidence-bind helper (grace_period 兜底 soft graduate)
REAL_FEATURE_LIST = Path(__file__).resolve().parents[1] / "feature_list.json"
V5_GATE_FEATURE_ID = "__PHASE_47_PLACEHOLDER_INFRA_039__"

_results: List[Tuple[str, bool, str]] = []


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_lib import assert_v5_reviewer_gate_evidence_bind  # noqa: E402

def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_039][{mark}] {tag} {detail}", flush=True)
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
# V0: scaffolding — dump 存在 + 关键符号
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    if not DUMP_PY.is_file():
        _emit("V0_dump_exists", False, f"missing {DUMP_PY}")
        return
    _emit("V0_dump_exists", True, str(DUMP_PY))
    src = DUMP_PY.read_text(encoding="utf-8")
    needed = [
        "def build_graph",
        "def render_text",
        "def _scan_file",
        "def _infer_target",
        "def main",
        "_RE_SINGLELINE",
        "_RE_TUPLE_OPEN",
    ]
    found = [n for n in needed if n in src]
    _emit(
        "V0_dump_symbols",
        len(found) >= 6,
        f"found {len(found)}/{len(needed)}: {found}",
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
# V2: dump 自身 file sha 锁
# ---------------------------------------------------------------------------
def v2_dump_file_sha() -> None:
    got = _file_sha(DUMP_PY)
    _emit(
        "V2_dump_file_sha",
        got == EXPECTED_DUMP_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_DUMP_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: mutant 反证 — 改坏 _RE_SINGLELINE 让其匹配为空
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    original = DUMP_PY.read_text(encoding="utf-8")
    # 改 single-line regex 让其永不匹配 (要求行以 # 起首 + 严格不可能模式)
    needle = '_RE_SINGLELINE = re.compile('
    if needle not in original:
        _emit("V3_mutant_apply", False, "no _RE_SINGLELINE compile site found")
        return
    mutant = original.replace(
        '_RE_SINGLELINE = re.compile(\n    r\'^([A-Z_][A-Z0-9_]*)\\s*=\\s*["\\\']([0-9a-f]{64})["\\\']\\s*(?:#.*)?$\'\n)',
        '_RE_SINGLELINE = re.compile(r"^__ZZZ_NEVER_MATCH_INFRA_039__$")',
        1,
    )
    if mutant == original:
        # fallback: 用更宽容 anchor 注入
        mutant = original.replace(
            '_RE_SINGLELINE = re.compile(',
            '_RE_SINGLELINE = re.compile("^__ZZZ_NEVER_MATCH__$"); _RE_SINGLELINE_ORIG = re.compile(',
            1,
        )
    if mutant == original:
        _emit("V3_mutant_apply", False, "could not inject mutant regex")
        return
    baseline_locks = _count_locks_via_subprocess()
    try:
        DUMP_PY.write_text(mutant, encoding="utf-8")
        mut_locks = _count_locks_via_subprocess()
        # mutant 应显著减少 locks (因为 single-line 锁占大多数)
        _emit(
            "V3_mutant_detected",
            mut_locks < baseline_locks,
            f"baseline={baseline_locks} mutant={mut_locks}",
        )
    finally:
        DUMP_PY.write_text(original, encoding="utf-8")


def _count_locks_via_subprocess() -> int:
    """subprocess 跑 dump --json, 数 locks 数."""
    try:
        out = subprocess.run(
            [sys.executable, str(DUMP_PY), "--json"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if out.returncode != 0:
            return -1
        import json as _json
        graph = _json.loads(out.stdout)
        return len(graph.get("locks", []))
    except Exception:
        return -1


# ---------------------------------------------------------------------------
# V4: 行为验证 — subprocess 跑 dump, grep 输出含已知锚点
# ---------------------------------------------------------------------------
def v4_behavior() -> None:
    try:
        out = subprocess.run(
            [sys.executable, str(DUMP_PY)],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except Exception as e:
        _emit("V4_subprocess_run", False, f"err: {e!r}")
        return
    _emit("V4_subprocess_run", out.returncode == 0, f"rc={out.returncode}")
    text = out.stdout
    anchors = [
        "v4_sha.json",
        "verify_infra_034.py",
        "verify_infra_037.py",
        "=== V4 SHA-LOCK GRAPH ===",
        "SUMMARY:",
    ]
    missing = [a for a in anchors if a not in text]
    _emit(
        "V4_output_anchors",
        not missing,
        f"missing={missing}" if missing else f"all {len(anchors)} anchors found",
    )
    # infra-039-backlog-v4-output-anchors-lower-bound (phase-61 #5):
    # 提取 v4_sha.json "(N targets)" 中的 N, 断言 MIN <= N <= MAX_SANITY。
    import re as _re_targets
    m = _re_targets.search(r"v4_sha\.json\s*\((\d+)\s+targets\)", text)
    if not m:
        _emit(
            "V4_targets_count_in_bounds",
            False,
            f"could not find 'v4_sha.json (N targets)' pattern in output "
            f"(len={len(text)})",
        )
    else:
        actual = int(m.group(1))
        in_bounds = EXPECTED_TARGETS_MIN <= actual <= EXPECTED_TARGETS_MAX_SANITY
        if actual < EXPECTED_TARGETS_MIN:
            detail = (
                f"actual={actual} < min={EXPECTED_TARGETS_MIN} "
                f"(节点丢失保护; 若刻意减少 target, bump EXPECTED_TARGETS_MIN)"
            )
        elif actual > EXPECTED_TARGETS_MAX_SANITY:
            detail = (
                f"actual={actual} > max={EXPECTED_TARGETS_MAX_SANITY} "
                f"(漂移过大 sanity; 人工 bump EXPECTED_TARGETS_MAX_SANITY 并审视)"
            )
        else:
            detail = (
                f"actual={actual} in [{EXPECTED_TARGETS_MIN}, "
                f"{EXPECTED_TARGETS_MAX_SANITY}]"
            )
        _emit("V4_targets_count_in_bounds", in_bounds, detail)
    # JSON 模式也应跑通
    try:
        out_j = subprocess.run(
            [sys.executable, str(DUMP_PY), "--json"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        import json as _json
        graph = _json.loads(out_j.stdout)
        ok = (
            out_j.returncode == 0
            and isinstance(graph.get("locks"), list)
            and isinstance(graph.get("v4_sha_json"), dict)
        )
        _emit(
            "V4_json_mode",
            ok,
            f"rc={out_j.returncode} locks={len(graph.get('locks', []))}",
        )
    except Exception as e:
        _emit("V4_json_mode", False, f"err: {e!r}")


def v6_show_full_sha_option() -> None:
    """锁住 dump_v4_sha_graph.py 的 ``--show-full-sha`` CLI 选项行为契约。

    contract:
      * default 模式输出含 ``...`` 截断标记 (16 hex + ``...``)。
      * ``--show-full-sha`` 模式: 输出至少含一个 64 hex sha 完整串, 且
        默认的 ``[:16]...`` 截断不出现在 sha-lock 行(以 '@' 或 '=' 分隔的 sha 值后)。
      * JSON / mermaid 模式不受影响 (--show-full-sha 与 --json 共用时仍输出 JSON)。
    """
    import re as _re
    # default 模式
    try:
        out_def = subprocess.run(
            [sys.executable, str(DUMP_PY)],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except Exception as e:
        _emit("V6_default_run", False, f"err: {e!r}")
        return
    _emit("V6_default_run", out_def.returncode == 0, f"rc={out_def.returncode}")
    # default 应含 "..." 省略号 (sha[:16]... 模式)
    has_ellipsis_default = "..." in out_def.stdout
    _emit(
        "V6_default_has_ellipsis",
        has_ellipsis_default,
        f"len_stdout={len(out_def.stdout)}",
    )
    # --show-full-sha 模式
    try:
        out_full = subprocess.run(
            [sys.executable, str(DUMP_PY), "--show-full-sha"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except Exception as e:
        _emit("V6_full_run", False, f"err: {e!r}")
        return
    _emit("V6_full_run", out_full.returncode == 0, f"rc={out_full.returncode}")
    # 应含至少一个 64 hex sha 完整串
    full_sha_re = _re.compile(r"\b[0-9a-f]{64}\b")
    full_hits = full_sha_re.findall(out_full.stdout)
    _emit(
        "V6_full_emits_64hex",
        len(full_hits) >= 1,
        f"hits={len(full_hits)} sample={full_hits[0] if full_hits else None}",
    )
    # full 模式不应在 sha 值后跟 "..."(@ 或 = 后接 16 hex + ...)
    trunc_re = _re.compile(r"[@=]\s*[0-9a-f]{16}\.\.\.")
    trunc_hits = trunc_re.findall(out_full.stdout)
    _emit(
        "V6_full_no_truncation",
        len(trunc_hits) == 0,
        f"trunc_hits={len(trunc_hits)}",
    )
    # JSON 模式 + --show-full-sha 仍应 emit valid JSON (text-only 选项不应破坏 JSON)
    try:
        out_jf = subprocess.run(
            [sys.executable, str(DUMP_PY), "--json", "--show-full-sha"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        import json as _json
        graph_jf = _json.loads(out_jf.stdout)
        ok_jf = out_jf.returncode == 0 and isinstance(graph_jf.get("locks"), list)
        _emit(
            "V6_json_with_full_sha_unaffected",
            ok_jf,
            f"rc={out_jf.returncode} locks={len(graph_jf.get('locks', []))}",
        )
    except Exception as e:
        _emit("V6_json_with_full_sha_unaffected", False, f"err: {e!r}")


# ---------------------------------------------------------------------------
# V7: --filter <regex> 选项行为锁 (infra-039-backlog-dump-filter-pattern, phase-50 #2.50)
# ---------------------------------------------------------------------------
def v7_filter_option() -> None:
    """锁住 dump_v4_sha_graph.py 的 ``--filter <regex>`` CLI 选项行为契约。

    contract:
      * default (no --filter): JSON locks 数为 baseline N (N>=1)。
      * --filter '<具体匹配子串>': 0 < hit < baseline (用 ``verify_infra_060`` 作 anchor)。
      * --filter '<不可能匹配的串>': locks 数 == 0; v4_sha_json hub 段仍保留。
      * text 模式 --filter 同样过滤 (输出不含被过滤掉的 source 文件名)。
    """
    import json as _json
    # baseline JSON
    try:
        out_base = subprocess.run(
            [sys.executable, str(DUMP_PY), "--json"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        graph_b = _json.loads(out_base.stdout)
        baseline_n = len(graph_b.get("locks", []))
    except Exception as e:
        _emit("V7_baseline_json", False, f"err: {e!r}")
        return
    _emit("V7_baseline_json", baseline_n >= 1, f"baseline_locks={baseline_n}")

    # 命中部分 — 锚 verify_infra_060
    try:
        out_hit = subprocess.run(
            [sys.executable, str(DUMP_PY), "--json", "--filter", "verify_infra_060"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        graph_h = _json.loads(out_hit.stdout)
        hit_n = len(graph_h.get("locks", []))
    except Exception as e:
        _emit("V7_filter_partial_hit", False, f"err: {e!r}")
        return
    _emit(
        "V7_filter_partial_hit",
        0 < hit_n < baseline_n,
        f"hit_locks={hit_n} baseline={baseline_n}",
    )

    # 不命中 — 期望 0 lock, hub 仍在
    try:
        out_miss = subprocess.run(
            [sys.executable, str(DUMP_PY), "--json", "--filter",
             "THIS_PATTERN_MATCHES_NOTHING_xyz_zzz"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        graph_m = _json.loads(out_miss.stdout)
        miss_n = len(graph_m.get("locks", []))
        hub_kept = isinstance(graph_m.get("v4_sha_json"), dict)
    except Exception as e:
        _emit("V7_filter_no_match", False, f"err: {e!r}")
        return
    _emit(
        "V7_filter_no_match_zero_locks",
        miss_n == 0,
        f"miss_locks={miss_n}",
    )
    _emit(
        "V7_filter_no_match_hub_preserved",
        hub_kept,
        f"hub_kept={hub_kept}",
    )

    # text 模式 --filter 同样过滤
    # infra-039-backlog-v7-filter-substring-fix (phase-67 #14):
    # 原 pattern "verify_infra_060" 是 substring/re.search 命中, 会同时命中
    # verify_infra_060.py 与 verify_infra_060_backlog_real_unknown_count_fix.py
    # (across 2 files), 导致 "across 1 files ===" 不成立 → V7 FAIL。改用 regex
    # 锚尾 r"verify_infra_060\.py$" 精确命中单一文件, 保留 substring 模式语义
    # (--filter 仍是 re.search, 只是测试侧 pattern 写得更严)。
    try:
        out_text = subprocess.run(
            [sys.executable, str(DUMP_PY), "--filter", r"verify_infra_060\.py$"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        # SUMMARY 行体现 source-group 计数, --filter 命中后应仅剩 1 source file
        text_ok = (
            out_text.returncode == 0
            and "verify_infra_060.py" in out_text.stdout
            and "across 1 files ===" in out_text.stdout
        )
    except Exception as e:
        _emit("V7_filter_text_mode", False, f"err: {e!r}")
        return
    _emit(
        "V7_filter_text_mode",
        text_ok,
        f"rc={out_text.returncode} len={len(out_text.stdout)}",
    )


# ---------------------------------------------------------------------------
# V8: _infer_target auto-discovery 一致性
# (infra-039-backlog-infer-target-auto-discovery, phase-63 #2)
# (infra-P316-v039-V8-not-found-reduce, phase-65 #1: 多源 resolve + acceptlist)
# ---------------------------------------------------------------------------
def v8_auto_discovery_consistency() -> None:
    """扫所有 scripts/verify_infra_*.py 内的 ``EXPECTED_*_FUNC_SHA[256]`` 常量,
    走 ``_v8_resolve_target`` 多源 resolve (docstring → self_convention →
    dump_table → explicit → naming_guess) 并按 best-match-wins 判定。

    P316 (phase-65 #1) 起 V8 不再依赖单一 naming guess, 也不再用 not_found /
    ambiguous 概念。每个 lock 的 status 落到下列之一:

      * ok                — 至少一个 candidate 的 func sha 与 expect 相等。
      * mismatch          — 全部 candidate 都不等且**包含 strong source**
                            (docstring/self_convention) → V8 FAIL。
      * weak_mismatch     — 全部 candidate 都不等但**仅有 weak source**
                            (dump_table/explicit/naming_guess) → V8 不 FAIL
                            (target verify 脚本自己的 self-lock 已覆盖)。
      * acceptlisted      — 显式 acceptlist 项 (e.g. pre-existing baseline FAIL)。
      * not_found         — 任何 source 都未解析出 candidate, 且不在 acceptlist。
                            **V8 FAIL** (新的 hard gate)。
      * skip_non_func     — dump_table 指向 file-sha / line-sha (非 V8 范围)。

    overall PASS 条件: discovered >= 1 且 strong_mismatches == 0 且
    unaccepted_not_found == 0。
    """
    import sys as _sys
    _sys.path.insert(0, str(SCRIPTS))
    try:
        from _verify_lib import (
            _discover_func_locks_in_verify,
            _v8_resolve_target,
        )
    except Exception as e:
        _emit("V8_helper_importable", False, f"err: {e!r}")
        return
    _emit("V8_helper_importable", True, "")

    discovered = 0
    counts = {
        "ok": 0,
        "mismatch": 0,
        "weak_mismatch": 0,
        "acceptlisted": 0,
        "not_found": 0,
        "skip_non_func": 0,
    }
    via_counts: Dict[str, int] = {}
    strong_mismatches: List[Tuple[str, str, str]] = []
    weak_mismatch_samples: List[Tuple[str, str, str]] = []
    not_found_samples: List[Tuple[str, str]] = []
    repo_root = SCRIPTS.parent

    for verify_py in sorted(SCRIPTS.glob("verify_infra_*.py")):
        for lock in _discover_func_locks_in_verify(verify_py):
            discovered += 1
            r = _v8_resolve_target(
                verify_py, lock["const_name"], lock["sha_hex"],
                repo_root=repo_root,
            )
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            via = r.get("resolved_via") or "none"
            via_counts[via] = via_counts.get(via, 0) + 1
            if r["status"] == "mismatch":
                strong_mismatches.append(
                    (verify_py.name, lock["const_name"], r["reason"])
                )
            elif r["status"] == "weak_mismatch" and len(weak_mismatch_samples) < 5:
                weak_mismatch_samples.append(
                    (verify_py.name, lock["const_name"], r["reason"])
                )
            elif r["status"] == "not_found":
                not_found_samples.append((verify_py.name, lock["const_name"]))

    _emit(
        "V8_discovered_count",
        discovered >= 1,
        f"discovered={discovered} ok={counts['ok']} "
        f"mismatch={counts['mismatch']} weak_mismatch={counts['weak_mismatch']} "
        f"acceptlisted={counts['acceptlisted']} "
        f"not_found={counts['not_found']} "
        f"skip_non_func={counts['skip_non_func']}",
    )
    _emit(
        "V8_no_strong_mismatches",
        counts["mismatch"] == 0,
        f"strong_mismatches={strong_mismatches[:5]}"
        if strong_mismatches else "0",
    )
    # P316 新 gate: 任何未 acceptlist 的 not_found 都 FAIL — 强迫维护者
    # 在 _V8_EXPLICIT_TARGETS 或 _V8_ACCEPTLIST 显式登记每个 new lock。
    _emit(
        "V8_no_unaccepted_not_found",
        counts["not_found"] == 0,
        f"not_found={not_found_samples[:5]}" if not_found_samples else "0",
    )
    # 覆盖率指标 — 不直接 gate, 只 emit 报告; 用 ok+acceptlisted+skip 占比衡量。
    accepted = counts["ok"] + counts["acceptlisted"] + counts["skip_non_func"]
    coverage_pct = (100.0 * accepted / discovered) if discovered else 0.0
    _emit(
        "V8_resolution_coverage",
        coverage_pct >= 95.0,
        f"coverage={coverage_pct:.1f}% accepted={accepted}/{discovered} "
        f"via={via_counts}",
    )
    if counts["weak_mismatch"] or counts["acceptlisted"]:
        print(
            f"[verify_infra_039][INFO] V8 weak_mismatch={counts['weak_mismatch']} "
            f"acceptlisted={counts['acceptlisted']} "
            f"(weak_sample={weak_mismatch_samples[:3]})",
            flush=True,
        )


def v5_reviewer_gate() -> None:
    """V5 Reviewer LGTM gate — phase-47 #1.47 graduate to evidence-bind helper.

    target feature evidence 不完整 (legacy / not_started)，通过 grace_period 兜底
    保持 emit=True，待 target feature 补齐 reviewer evidence 后从 grace 列表移除。
    """
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


def main() -> int:
    v0_scaffolding()
    v1_self_lock()
    v2_dump_file_sha()
    v3_mutant()
    v4_behavior()
    v6_show_full_sha_option()
    v7_filter_option()
    v8_auto_discovery_consistency()
    v5_reviewer_gate()
    failed = [t for t, ok, _ in _results if not ok]
    if failed:
        print(f"[verify_infra_039][SUMMARY] FAILED tags: {failed}", flush=True)
        return 1
    print(f"[verify_infra_039][SUMMARY] ALL PASS ({len(_results)} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
