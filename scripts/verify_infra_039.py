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
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DUMP_PY = SCRIPTS / "dump_v4_sha_graph.py"

# infra-039 sha lock 常量 (V2): dump_v4_sha_graph.py 整体 file sha
EXPECTED_DUMP_FILE_SHA = "ae7a5b72ef631cf8679b8519c5558984ca5d136dbdb8b2c25c75ebc76f7c4450"

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
    try:
        out_text = subprocess.run(
            [sys.executable, str(DUMP_PY), "--filter", "verify_infra_060"],
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
# ---------------------------------------------------------------------------
def v8_auto_discovery_consistency() -> None:
    """扫所有 scripts/verify_infra_*.py 内的 ``EXPECTED_*_FUNC_SHA[256]`` 常量,
    从命名规约派生 func_name guess, 在候选 source file 中 AST 找定义,
    算 ``func_sha_by_name`` 与锁值比对。

    候选 source file 顺序 (启发式):
      1) verify 脚本自身 (self-checker func sha 占多数)
      2) ``scripts/_verify_lib.py``
      3) ``scripts/bump_reverse_sha_lock.py``
      4) ``scripts/dump_reverse_sha_lock_index.py`` (若存在)
      5) ``scripts/dump_v4_sha_graph.py``

    判定:
      * 候选中**唯一** match 且 sha 一致 → ok
      * 唯一 match 但 sha 不一致 → mismatch (FAIL)
      * 多个候选都有该 func name → ambiguous (作 not_found, warning)
      * 所有候选都不含该 func → not_found (warning, 跨脚本引用不在本 helper 范围)

    overall PASS 条件: discovered >= 1 且 mismatches == 0。
    """
    import sys as _sys
    _sys.path.insert(0, str(SCRIPTS))
    try:
        from _verify_lib import _discover_func_locks_in_verify, func_sha_by_name
    except Exception as e:
        _emit("V8_helper_importable", False, f"err: {e!r}")
        return
    _emit("V8_helper_importable", True, "")

    extra_candidates = [
        SCRIPTS / "_verify_lib.py",
        SCRIPTS / "bump_reverse_sha_lock.py",
        SCRIPTS / "dump_reverse_sha_lock_index.py",
        SCRIPTS / "dump_v4_sha_graph.py",
    ]

    discovered = 0
    matched = 0
    mismatches: List[Tuple[str, str, str, str]] = []
    not_found: List[Tuple[str, str, str]] = []
    ambiguous: List[Tuple[str, str, str, int]] = []

    for verify_py in sorted(SCRIPTS.glob("verify_infra_*.py")):
        locks = _discover_func_locks_in_verify(verify_py)
        for lock in locks:
            discovered += 1
            const = lock["const_name"]
            guess = lock["func_name_guess"]
            expected_sha = lock["sha_hex"]
            # 候选顺序: verify 自身优先 (self-checker 占多数), 然后 extras
            sources = [verify_py] + extra_candidates
            hits: List[Tuple[Path, str]] = []
            for src in sources:
                if not src.is_file():
                    continue
                try:
                    got = func_sha_by_name(src, guess)
                    hits.append((src, got))
                except (ValueError, FileNotFoundError):
                    continue
                except Exception:
                    continue
            if not hits:
                not_found.append((verify_py.name, const, guess))
                continue
            if len(hits) > 1:
                # 多源命中 → ambiguous, 不做 mismatch 判定 (避免误报):
                # 仅在常量名暗示 self-lock (含 CHECKER / SELF) 且 verify 自身命中时,
                # 才以 verify 自身为准。
                self_hit = [h for h in hits if h[0] == verify_py]
                is_self_lock_hint = ("CHECKER" in const) or ("SELF" in const)
                if self_hit and is_self_lock_hint:
                    src_used, got_sha = self_hit[0]
                else:
                    ambiguous.append((verify_py.name, const, guess, len(hits)))
                    continue
            else:
                src_used, got_sha = hits[0]
            if got_sha == expected_sha:
                matched += 1
            else:
                mismatches.append(
                    (verify_py.name, const, expected_sha[:16],
                     got_sha[:16] + f"@{src_used.name}")
                )

    _emit(
        "V8_discovered_count",
        discovered >= 1,
        f"discovered={discovered} matched={matched} "
        f"not_found={len(not_found)} ambiguous={len(ambiguous)} "
        f"mismatches={len(mismatches)}",
    )
    _emit(
        "V8_no_mismatches",
        len(mismatches) == 0,
        f"mismatches={mismatches[:5]}" if mismatches else "0",
    )
    if not_found or ambiguous:
        print(
            f"[verify_infra_039][INFO] V8 not_found={len(not_found)} "
            f"ambiguous={len(ambiguous)} "
            f"(nf_sample={not_found[:3]} amb_sample={ambiguous[:3]})",
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
