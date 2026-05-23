#!/usr/bin/env python3
"""_verify_lib: 共享 helper for scripts/verify_*.py (robot-035, infra-037, robot-037).

robot-035: 把 verify_robot_032 中 ``_parse_headings_from_doc`` 抽成本模块的公开
helper ``parse_headings_from_doc``, 使多个 docs-lock verify 可复用 (单一事实源 +
sha 锁链路统一)。

infra-037: 新增 ``func_sha_by_name(path, func_name)`` helper, 封装顶层
function/method 的 ast 抽取 + 规范化 (``ast.unparse``) + sha256 计算, 让后续
verify-script 复用 func-level sha 抽取的统一入口, 降低复制粘贴成本。

robot-037: 新增 ``read_constant(path, const_name)`` helper, 封装"静态读取
模块级常量字面值" (ast.literal_eval) 的通用模式, 收敛 verify_robot_034 /
verify_robot_036 中各自内部的 ``_read_sentinel_from_verify_032`` /
``_read_constant_from`` 复制实现。

运行环境约定 (infra-034)
------------------------
本模块由 scripts/verify_*.py 在已激活的 .venv 下 import (顶部以
``sys.path.insert(0, str(Path(__file__).resolve().parent))`` 把 scripts 目录置于
sys.path 头, 以便相对 import 本模块)。不要从仓库外部直接 import。
"""
from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

_MIN_PYTHON_VERSION = (3, 13)
if sys.version_info < _MIN_PYTHON_VERSION:
    raise RuntimeError(
        f"_verify_lib.py requires Python >= {_MIN_PYTHON_VERSION[0]}.{_MIN_PYTHON_VERSION[1]}; "
        f"got {sys.version_info.major}.{sys.version_info.minor}. "
        "Reason: inspect.getsource() output differs between Python versions, "
        "causing sha256-based V1_self_main_func_sha / V3_helper_func_sha lock drift. "
        "Run with .venv/bin/python (Python 3.13) for repeatable verify."
    )

__all__ = [
    "parse_headings_from_doc",
    "func_sha_by_name",
    "read_constant",
    "assert_unique_needle",
    "scan_reverse_sha_locks",
    "live_verify_sha_set",
    "verify_reverse_sha_lock_consistency",
    "verify_expected_pattern_consistency",
    "verify_palette_fills_distinct",
    "verify_unknown_node_count_bound",
    "verify_expected_prefix_typo_guard",
    "verify_closeout_evidence_trustworthy",
    "scan_reviewer_text",
    "verify_baseline_fail_claims",
    "verify_evidence_tail_stdout_sha",
    "assert_report_matches_closeout_runs",
    "assert_reviewer_lgtm",
    "assert_reviewer_baseline_head_echo",
    "assert_baseline_head_echo_present_and_matches",
    "assert_reviewer_summary_nonempty",
    "assert_verify_passed",
    "verify_summary_exit",
    "assert_verify_lib_public_helper_naming",
    "assert_closeout_verify_runs_min_count",
    "assert_closeout_smoke_tail_nonempty",
    "assert_verify_lib_helpers_in_v3_sha_table",
    "assert_closeout_verify_runs_shape",
    "assert_closeout_reviewer_block_shape",
    "assert_closeout_baseline_head_echo_format",
    "assert_closeout_merge_commit_sha_format",
    "assert_closeout_main_head_sha_format",
    "assert_closeout_verify_runs_freshness",
    "assert_v5_reviewer_gate_evidence_bind",
]


# ---------------------------------------------------------------------------
# infra-V6-backlog (P264): 反向 sha lock 扫描 helper, 从 verify_infra_034 抽出
# ---------------------------------------------------------------------------
# 顶层 64-hex sha 常量 (单行或元组形式)
_RE_REVLOCK_SINGLELINE = re.compile(
    r'^([A-Z_][A-Z0-9_]*)\s*=\s*["\']([0-9a-f]{64})["\']\s*(?:#.*)?$'
)
_RE_REVLOCK_TUPLE_OPEN = re.compile(r'^([A-Z_][A-Z0-9_]*)\s*=\s*\(\s*$')
_RE_REVLOCK_TUPLE_HEX = re.compile(r'^\s*["\']([0-9a-f]{64})["\']\s*,?\s*$')
# 反向锁常量名识别: 含 "VERIFY_<NNN>" 子串 (NNN = 3 位数字)
_RE_REVLOCK_VERIFY_ID = re.compile(r"VERIFY_(\d{3})")
# infra-049-backlog (P271): 拓宽 pattern, 也捕获 ``EXPECTED_*_(FILE|FUNC)_SHA``
# 形式的反向锁常量 (verify-to-source-script / verify-to-func 锁), 用于完整索引视图。
# 这类条目 kind="expected_pattern", V6 一致性 (orphan 检测) 跳过它们,
# 因为其 hex 值多为外部 dump_*.py 文件 sha 或 ast.unparse 后的函数 sha,
# 不属于 live_verify_sha_set 维度。
_RE_REVLOCK_EXPECTED_PATTERN = re.compile(r"^EXPECTED_.*_(FILE|FUNC)_SHA$")


def scan_reverse_sha_locks(scripts_dir: str | Path) -> list[dict]:
    """扫 scripts_dir 下所有 verify_*.py + _verify_lib.py 顶层 64-hex sha 常量。

    返回 list of dict: {file, lineno, const_name, sha_hex, kind}
    其中 const_name 含 'SHA' 且满足下列任一模式的条目:
      - kind="verify_id": 含 ``VERIFY_<NNN>`` 子串 (verify-to-verify 反向锁,
        P264 起作为 V6 一致性比对的硬目标)
      - kind="expected_pattern": 匹配 ``^EXPECTED_.*_(FILE|FUNC)_SHA$``
        (verify-to-source-script / verify-to-func 锁, P271 起纳入索引视图,
        但 V6 一致性比对**不**对其做 orphan 判定)

    infra-V6-backlog (P264): 从 verify_infra_034 ``_v6_scan_constants`` 抽出。
    infra-049-backlog (P271): 拓宽 pattern + 引入 kind 字段。
    """
    base = Path(scripts_dir)
    results: list[dict] = []
    targets = sorted(base.glob("verify_*.py"))
    lib = base / "_verify_lib.py"
    if lib.is_file():
        targets.append(lib)
    for p in targets:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        i = 0
        while i < len(lines):
            line = lines[i]
            const_name: str | None = None
            sha_hex: str | None = None
            lineno: int = i + 1
            m = _RE_REVLOCK_SINGLELINE.match(line)
            if m:
                const_name, sha_hex = m.group(1), m.group(2)
                i += 1
            else:
                m2 = _RE_REVLOCK_TUPLE_OPEN.match(line)
                if m2 and i + 1 < len(lines):
                    mh = _RE_REVLOCK_TUPLE_HEX.match(lines[i + 1])
                    if mh:
                        const_name, sha_hex = m2.group(1), mh.group(1)
                        i += 2
                    else:
                        i += 1
                else:
                    i += 1
            if const_name and sha_hex:
                if "SHA" not in const_name:
                    continue
                if _RE_REVLOCK_VERIFY_ID.search(const_name):
                    kind = "verify_id"
                elif _RE_REVLOCK_EXPECTED_PATTERN.match(const_name):
                    kind = "expected_pattern"
                else:
                    continue
                results.append({
                    "file": str(p.relative_to(base.parent)) if base.parent in p.parents else str(p),
                    "lineno": lineno,
                    "const_name": const_name,
                    "sha_hex": sha_hex,
                    "kind": kind,
                })
    return results


def live_verify_sha_set(scripts_dir: str | Path) -> dict[str, str]:
    """返回 {relative_file_path: sha256_hex} 含 scripts_dir 下所有 verify_*.py + _verify_lib.py。

    infra-V6-backlog (P264): 配合 ``scan_reverse_sha_locks`` 形成 V6 实时 sha 基准。
    """
    base = Path(scripts_dir)
    out: dict[str, str] = {}
    targets = sorted(base.glob("verify_*.py"))
    lib = base / "_verify_lib.py"
    if lib.is_file():
        targets.append(lib)
    parent = base.parent
    for p in targets:
        try:
            data = p.read_bytes()
        except Exception:
            continue
        try:
            rel = str(p.relative_to(parent))
        except ValueError:
            rel = str(p)
        out[rel] = hashlib.sha256(data).hexdigest()
    return out


def verify_reverse_sha_lock_consistency(scripts_dir: str | Path) -> dict:
    """V6 主入口: 扫所有反向锁 + 比对 live sha set, 返回结果 dict。

    Returns:
        {
            "scanned_count": int,
            "live_count": int,
            "orphans": list[dict],  # {file, lineno, const_name, sha_hex, target_id, candidates}
            "all_match": bool,
        }

    infra-V6-backlog (P264): 从 verify_infra_034 ``v6_reverse_sha_lock_consistency`` 抽出。
    infra-049-backlog (P271): pattern 放宽后, 只对 kind=="verify_id" 的条目做
    orphan 检查; expected_pattern kind (verify-to-source / verify-to-func 锁)
    的 hex 多不属于 live_verify_sha_set 维度, 不在此处校验。
    """
    base = Path(scripts_dir)
    live = live_verify_sha_set(base)
    live_sha_values = set(live.values())
    scanned = scan_reverse_sha_locks(base)
    orphans: list[dict] = []
    for item in scanned:
        if item.get("kind") != "verify_id":
            continue
        sha_val = item["sha_hex"]
        if sha_val in live_sha_values:
            continue
        m = _RE_REVLOCK_VERIFY_ID.search(item["const_name"])
        tid = m.group(1) if m else ""
        same_id_candidates = sorted(
            rel for rel in live
            if re.search(rf"_{tid}\.py$", rel)
        ) if tid else []
        orphans.append({
            "file": item["file"],
            "lineno": item["lineno"],
            "const_name": item["const_name"],
            "sha_hex": sha_val,
            "target_id": tid,
            "candidates": {rel: live[rel] for rel in same_id_candidates},
        })
    return {
        "scanned_count": len(scanned),
        "live_count": len(live),
        "orphans": orphans,
        "all_match": not orphans,
    }


def verify_expected_pattern_consistency(scripts_dir: str | Path) -> dict:
    """V7 主入口: 对 kind=="expected_pattern" 锁做独立一致性核验, 返回结果 dict。

    Returns:
        {
            "total_expected_pattern_locks": int,
            "unresolved": list[dict],         # const_name 无法解析出 <NAME> 部分 (理论应为 0)
            "orphan_const_names": list[dict], # scripts/_verify_lib 顶层赋值含 EXPECTED_*_(FILE|FUNC)_SHA 模式
                                              # 但 scan_reverse_sha_locks 未扫到的孤儿
            "missing_assignment": list[dict], # scan 报告的 const_name 在所在 file 里找不到顶层 ast.Assign 定义
            "sample": list[dict],             # 抽样前 5 条
            "all_match": bool,
        }

    背景 (infra-049-backlog-expected-pattern-consistency-check, P276):
    P271 拓宽 scan_reverse_sha_locks pattern 后, kind=="expected_pattern" 的 52+
    锁条目目前没有独立的一致性 check (V6 一致性 verify_reverse_sha_lock_consistency
    只对 kind=="verify_id" 做硬比对)。本 helper 弥补该盲区:

    - 校验 scan 输出 schema 自洽: 每条 const_name 必须 match
      ``^EXPECTED_.+_(FILE|FUNC)_SHA$`` 且 sha_hex 必须 64-hex lowercase。
    - 校验 const_name 可在其声明文件 (item["file"]) 中以**顶层 ast.Assign**
      形式找到 (防 scan 把字符串/局部变量误识为反向锁)。
    - 校验 scan 没漏扫: 重新做一次顶层 ast.Assign 全量扫描, 找出所有形如
      ``^EXPECTED_.+_(FILE|FUNC)_SHA$`` 的常量定义, 与 scan 输出做对照, 任何只在
      ast 扫到、scan 没扫到的 (file, const_name) pair 即为 orphan_const_names。
    """
    base = Path(scripts_dir)
    scanned = scan_reverse_sha_locks(base)
    ep_items = [x for x in scanned if x.get("kind") == "expected_pattern"]

    unresolved: list[dict] = []
    missing_assignment: list[dict] = []

    # 预扫所有 verify_*.py + _verify_lib.py 顶层 Assign, 索引 const 名集合 by file
    targets = sorted(base.glob("verify_*.py"))
    lib = base / "_verify_lib.py"
    if lib.is_file():
        targets.append(lib)
    parent = base.parent

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(parent))
        except ValueError:
            return str(p)

    file_const_names: dict[str, set[str]] = {}
    ast_observed_ep: set[tuple[str, str]] = set()  # (rel_file, const_name)
    for p in targets:
        try:
            src = p.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except Exception:
            continue
        rel = _rel(p)
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        names.add(tgt.id)
                        if _RE_REVLOCK_EXPECTED_PATTERN.match(tgt.id):
                            # 值必须为 64-hex 字符串字面 (排除非 sha 常量)
                            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                                v = node.value.value
                                if len(v) == 64 and all(c in "0123456789abcdef" for c in v):
                                    ast_observed_ep.add((rel, tgt.id))
        file_const_names[rel] = names

    # 检查每条 scan 输出 item 的 schema 自洽 + assignment 存在
    for item in ep_items:
        const_name = item.get("const_name", "")
        file_rel = item.get("file", "")
        m = _RE_REVLOCK_EXPECTED_PATTERN.match(const_name)
        if not m:
            unresolved.append({"file": file_rel, "const_name": const_name, "reason": "pattern not match"})
            continue
        sha_val = item.get("sha_hex", "")
        if not (len(sha_val) == 64 and all(c in "0123456789abcdef" for c in sha_val)):
            unresolved.append({"file": file_rel, "const_name": const_name, "reason": f"bad sha_hex={sha_val!r}"})
            continue
        names = file_const_names.get(file_rel, set())
        if const_name not in names:
            missing_assignment.append({"file": file_rel, "const_name": const_name})

    # orphan: ast 扫到 EP 但 scan 没扫到
    scan_observed_ep: set[tuple[str, str]] = {(x["file"], x["const_name"]) for x in ep_items}
    orphan_const_names: list[dict] = [
        {"file": f, "const_name": n}
        for (f, n) in sorted(ast_observed_ep - scan_observed_ep)
    ]

    all_match = not unresolved and not missing_assignment and not orphan_const_names
    return {
        "total_expected_pattern_locks": len(ep_items),
        "unresolved": unresolved,
        "orphan_const_names": orphan_const_names,
        "missing_assignment": missing_assignment,
        "sample": ep_items[:5],
        "all_match": all_match,
    }


def verify_palette_fills_distinct(palette: dict[str, dict[str, str]]) -> dict:
    """断言 mermaid palette 中所有 entry 的 ``fill`` 字段两两互不相同。

    背景 (infra-053-backlog-classdef-fills-distinct-check, P277):
    P273 (infra-054-classdef-palette-extract) 把 scripts/dump_v4_sha_graph.py
    的 6 类 classDef 颜色提到 module-top ``_MERMAID_PALETTE`` (dict[str,
    dict[str, str]], 6 个 key: hub/verify/lib/dump/module/unknown)。
    verify_infra_054 锁了 palette 结构与颜色 hex 字面, 但没断言 "6 色 fill
    互不相同" — 未来若误把两个 key 写成同色, verify 会 PASS 但 mermaid 图
    会失去可读性。本 helper 补该硬断言, 不依赖具体色值列表, 只对 palette
    自身做 set 去重检测。

    Args:
        palette: dict[key, dict[字段名, 字段值]]。helper 只读 ``fill`` 字段。
            每个 value entry 必须含 ``fill`` (str), 否则该 key 跳过 (不会
            crash, 让上层 verify 自己显式做 schema 检查)。

    Returns:
        dict 结构:

        ::

            {
                "total_keys": int,             # palette 顶层 key 总数
                "distinct_fill_count": int,    # 去重后 fill 值数量
                "duplicates": list[tuple],     # [(key1, key2, shared_fill), ...]
                                               # key 字典序排序, 同 fill 内部 key 对再字典序
                "all_distinct": bool,          # 综合判定 (== duplicates 为空)
                "fills": list[tuple[str, str]],# [(key, fill), ...] 按输入插入顺序
            }
    """
    fills: list[tuple[str, str]] = []
    for key, entry in palette.items():
        if not isinstance(entry, dict):
            continue
        fill = entry.get("fill")
        if not isinstance(fill, str):
            continue
        fills.append((key, fill))
    # 按 fill 值聚合 key 列表
    by_fill: dict[str, list[str]] = {}
    for k, f in fills:
        by_fill.setdefault(f, []).append(k)
    duplicates: list[tuple] = []
    for fill_val, keys in by_fill.items():
        if len(keys) <= 1:
            continue
        ks = sorted(keys)
        # 生成所有两两组合 (字典序)
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                duplicates.append((ks[i], ks[j], fill_val))
    duplicates.sort()
    distinct_fill_count = len(by_fill)
    return {
        "total_keys": len(fills),
        "distinct_fill_count": distinct_fill_count,
        "duplicates": duplicates,
        "all_distinct": not duplicates,
        "fills": fills,
    }


def verify_unknown_node_count_bound(
    nodes: list[dict], max_unknown: int = 1
) -> dict:
    """断言一组分类后节点中 ``kind == "unknown"`` 的节点数 ≤ max_unknown。

    背景 (infra-048-backlog-docstring-unknown-zero-fact, P278):
    P266 / P273 之后, ``scripts/dump_v4_sha_graph.py`` 用 ``_classify_node``
    把 mermaid 节点分 6 类: hub / verify / lib / dump / module / unknown。
    其中 ``unknown`` 是兜底分类——仅当 ``node_id.startswith("unknown_")``
    (即 lock target 无法解析出 ``.py`` stem, render_mermaid 退化成
    ``unknown_<CONST>`` 占位节点) 时才命中。理想情况下分类规则覆盖全部
    节点, unknown 节点数应为 0 或极少 (当前仅 ``unknown_EXPECTED_DOC_SHA``)。

    verify_infra_048 (palette extract 锁) 已锁分类逻辑存在 + 颜色字面,
    但**没断言 unknown 节点数 ≤ 1** 作为行为锁。如果未来分类规则失效
    (例如 target 解析正则破损 / 大量 lock target 无法 stem-resolve),
    unknown 节点会悄悄膨胀, verify 仍 PASS。本 helper 补该硬断言, 由
    verify_infra_059 V4 真实喂入 dump 派生的 nodes 做行为锁, 同时构造
    tmp 正/反例锁定 helper 行为面。

    Args:
        nodes: 已分类节点列表, 每个 entry 必须含 ``kind`` (str) 与 ``id``
            (str)。其余字段忽略。非 dict 或缺 ``kind``/``id`` 的 entry
            跳过统计 (不会 crash, 让上层 verify 自己显式做 schema 检查)。
        max_unknown: ``kind == "unknown"`` 节点数上限 (含等号), 默认 1。
            ≤ max_unknown 视为 ``within_bound=True``。

    Returns:
        dict 结构:

        ::

            {
                "total_nodes": int,         # 输入合法节点总数
                "unknown_count": int,       # kind == "unknown" 节点数
                "unknown_ids": list[str],   # unknown 节点 id (插入顺序)
                "max_allowed": int,         # 回填 max_unknown
                "within_bound": bool,       # unknown_count <= max_allowed
            }
    """
    total = 0
    unknown_ids: list[str] = []
    for entry in nodes:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        nid = entry.get("id")
        if not isinstance(kind, str) or not isinstance(nid, str):
            continue
        total += 1
        if kind == "unknown":
            unknown_ids.append(nid)
    unknown_count = len(unknown_ids)
    return {
        "total_nodes": total,
        "unknown_count": unknown_count,
        "unknown_ids": unknown_ids,
        "max_allowed": max_unknown,
        "within_bound": unknown_count <= max_unknown,
    }


def assert_unique_needle(text: str, needle: str) -> None:
    """断言 needle 在 text 中恰好出现一次, 否则 raise ValueError。

    infra-040-backlog: P258 暴露的坑——V3 mutant 用 ``text.replace(needle, ...)``
    在文档/源码上制造 sha 漂移时, 若 needle 非唯一会同时替换多处, mutant 失效,
    导致反证假阳性 PASS。所有 V3 mutant 在 replace 前应先 call 本 helper 显式
    断言 needle 唯一性。

    Args:
        text: 待 mutant 的内容。
        needle: 要替换的子串。

    Raises:
        ValueError: needle 在 text 中出现次数不为 1。
    """
    count = text.count(needle)
    if count != 1:
        raise ValueError(f"needle 不唯一: count={count}, needle={needle!r}")


def parse_headings_from_doc(doc_path: Path, sentinel: str) -> list[str]:
    """从 docs 的 sentinel section 提取章节标题字面列表。

    解析窗口: 从 sentinel H2 行开始到文件末尾或下一 H2 行止;
    bullet 形如 ``- `<title>` `` 的行的反引号字面被收集为 headings。

    Raises:
        RuntimeError: doc 不存在 / sentinel section 缺失 / 解析为空。
    """
    if not doc_path.exists():
        raise RuntimeError(f"doc not found: {doc_path}")
    text = doc_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == sentinel)
    except StopIteration:
        raise RuntimeError(f"sentinel section not found: {sentinel!r}")
    # 从 sentinel 下一行到下一个 H2 (## ...) 或文件末
    headings: list[str] = []
    for ln in lines[start + 1 :]:
        s = ln.rstrip()
        if s.startswith("## "):  # 进入下一 H2 段, 停
            break
        st = s.lstrip()
        if st.startswith("- `") and st.endswith("`"):
            # 提取反引号之间的字面
            inner = st[3:-1]
            headings.append(inner)
    if not headings:
        raise RuntimeError(
            f"no headings parsed under sentinel section {sentinel!r}"
        )
    return headings


def func_sha_by_name(path: str | Path, func_name: str) -> str:
    """抽取 path 中名为 func_name 的**顶层** function/async function 源码, 规范化后返回 sha256。

    规范化策略 (与历史 verify-script 一致, infra-037 统一入口):
    - ``ast.parse`` 抽 FunctionDef / AsyncFunctionDef node
    - ``ast.unparse(node)`` 拿 canonical 源码 (Python 3.9+)
    - utf-8 encode 后 ``sha256().hexdigest()``

    TODO(infra-037+): class method 暂不支持; 后续如需 ``ClassName.method`` 形式
    可在本 helper 上加 dotted-name 解析分支, 不破坏现有调用面。

    Args:
        path: 源文件路径 (str 或 Path 均可)。
        func_name: 顶层 function/async function 名 (不支持 ``Cls.method``)。

    Returns:
        canonical 源码的 sha256 hex (64 字符)。

    Raises:
        FileNotFoundError: path 不存在。
        ValueError: func_name 未在文件顶层找到。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"source file not found: {p}")
    src = p.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            canonical = ast.unparse(node)
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    raise ValueError(f"top-level function {func_name!r} not found in {p}")


def read_constant(path: str | Path, const_name: str) -> Any:
    """静态读取 path 中名为 const_name 的**模块级**常量字面值 (不 import, 不执行)。

    实现:
    - ``ast.parse`` 文件源码
    - 扫描 Module.body, 找形如 ``const_name = <literal>`` 的 ``ast.Assign`` 节点
      (target.id == const_name)
    - 使用 ``ast.literal_eval(node.value)`` 安全求值, 只接受 str/bytes/num/tuple/
      list/dict/set/bool/None 字面 (不会执行任意代码)

    robot-037: 收敛 verify_robot_034._read_sentinel_from_verify_032 与
    verify_robot_036._read_constant_from 的复制实现, 形成单一入口。让 verify
    脚本静态读取另一 verify 脚本中的常量 (避免 import-time 副作用) 成为标准模式。

    Args:
        path: 源文件路径 (str 或 Path 均可)。
        const_name: 模块顶层常量名。

    Returns:
        ``ast.literal_eval`` 求值后的常量值 (类型取决于字面)。

    Raises:
        FileNotFoundError: path 不存在。
        ValueError: const_name 未在文件模块级找到, 或 value 非 literal。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"source file not found: {p}")
    src = p.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == const_name:
                    try:
                        return ast.literal_eval(node.value)
                    except (ValueError, SyntaxError) as e:
                        raise ValueError(
                            f"constant {const_name!r} in {p} is not a literal: {e}"
                        )
    raise ValueError(f"module-level constant {const_name!r} not found in {p}")


# ---------------------------------------------------------------------------
# infra-P273-evidence-report-accuracy (P274): sub-agent 反失真 helper
# ---------------------------------------------------------------------------
# verify_*.py 的 SUMMARY 尾行格式正则 (双格式兼容):
# 格式 A (两段式 / 主流, infra-045+ / robot-037+):
#   [verify_infra_055][SUMMARY] ALL PASS (18 checks)
#   [verify_infra_055][SUMMARY] FAIL 2/18: ['V2_xxx', 'V4_yyy']
# 格式 B (一段式 / 旧, infra-034 / robot-035 沿用至今):
#   [verify_infra_034] summary total=53 failed=0
#   [verify_robot_035] summary total=8 failed=2
# infra-P273-evidence-report-accuracy (P274 Reviewer fix): 旧脚本的一段式
# summary 也须被 helper 识别, 否则 sub-agent 严格 dogfood 时会对 034/robot_035
# 这批旧脚本误报 FAIL。格式 B 的 passed 判定: failed == 0; checks 回填 total。
_RE_VERIFY_SUMMARY_A = re.compile(
    r"^\[(?P<name>verify_[a-z0-9_]+)\]\[SUMMARY\]\s+"
    r"(?P<verdict>ALL PASS|FAIL)\b.*?(?:\((?P<checks>\d+)\s+checks?\))?\s*$"
)
_RE_VERIFY_SUMMARY_B = re.compile(
    r"^\[(?P<name>verify_[a-z0-9_]+)\]\s+summary\s+"
    r"total=(?P<total>\d+)\s+failed=(?P<failed>\d+)\s*$"
)
# 单行 PASS/FAIL emit 行 (用于 FAIL 子串扫描豁免)
_RE_VERIFY_EMIT_LINE = re.compile(
    r"^\[(?P<name>verify_[a-z0-9_]+)\]\[(?P<mark>PASS|FAIL)\]\s+"
)


def assert_verify_passed(stdout_text: str, verify_name: str) -> dict:
    """校验一份 ``scripts/verify_*.py`` 的实测 stdout 是否真的 ALL PASS。

    infra-P273-evidence-report-accuracy (P274): P273 暴露 sub-agent 上报
    "verify PASS" 但实际未跑 / 未读 stdout / 编造结论的失真模式 (典型: 把
    054 V1 FAIL 错误归因到 044/047 称 "pre-existing"). 本 helper 提供机器辅助
    的反失真校验入口, 让 sub-agent 在 evidence 中附 stdout 与 helper 结论形成
    双重锚, 减少凭印象编造的空间。

    校验规则:
      1. 必须找到形如
         ``[<verify_name>][SUMMARY] ALL PASS (N checks)`` (格式 A, 两段式) **或**
         ``[<verify_name>] summary total=N failed=M`` (格式 B, 一段式, 旧脚本)
         的 SUMMARY 尾行; 缺失 SUMMARY → ``passed=False``。
         格式 B 的 verdict 由 ``failed == 0`` 推导, checks 取 ``total``.
      2. SUMMARY 行 verdict 必须是 ``ALL PASS`` (不允许 ``FAIL``).
      3. 扫描整 stdout, 不允许出现 ``[<name>][FAIL]`` 形式的 emit 行 (即任何
         单个 check FAIL 都视为整体 FAIL, 即便 SUMMARY 显示 PASS——防止
         SUMMARY 行被独立伪造). FAIL 子串若仅出现在 SUMMARY 行或非 emit 上下文
         (例如 docstring / 行内说明), 由步骤 1/2 已经独立判定, 不在此处误伤。
      4. ``verify_name`` 必须精确匹配 SUMMARY 行中的 ``[verify_xxx]`` 名字
         (防止把 A 脚本的 stdout 当 B 脚本的证据上报).

    Args:
        stdout_text: ``subprocess.run(...).stdout`` 或文件 cat 出来的字面文本。
        verify_name: 期望脚本名 (不含 ``.py``), 例如 ``"verify_infra_055"``。

    Returns:
        dict 结构:

        ::

            {
                "name": str,           # 实际 SUMMARY 行里 parse 到的名字 (缺失则 "")
                "passed": bool,        # 综合判定
                "checks": int,         # SUMMARY 报告的 check 总数 (缺失则 0)
                "summary_line": str,   # 命中的 SUMMARY 字面行 (缺失则 "")
                "fail_emits": list,    # 单行 FAIL emit 命中列表 (含 tag 名)
                "reason": str,         # passed=False 时的人类可读原因摘要
            }

    设计注:
      - 不 raise——返回 dict 即可让调用者 (例如 verify_infra_055 自己) 用
        ``_emit("V4_helper_pass_case", res["passed"], res["summary_line"])``
        把校验结果纳入 verify 链条。
      - sub-agent 在 evidence 段使用方式: 把整段 stdout 喂 helper, 在报告中
        贴出 ``res["summary_line"]`` + ``res["passed"]`` 双字段, 避免凭印象
        声称 "PASS"。

    Examples:
        >>> ok = "[verify_infra_055][PASS] V0_x\\n[verify_infra_055][SUMMARY] ALL PASS (1 checks)\\n"
        >>> r = assert_verify_passed(ok, "verify_infra_055")
        >>> r["passed"], r["checks"]
        (True, 1)
        >>> bad = "[verify_infra_055][FAIL] V2 got=abc\\n[verify_infra_055][SUMMARY] FAIL 1/2: ['V2']\\n"
        >>> assert_verify_passed(bad, "verify_infra_055")["passed"]
        False
        >>> assert_verify_passed("", "verify_infra_055")["passed"]
        False
    """
    summary_line = ""
    parsed_name = ""
    verdict = ""
    checks = 0
    for line in stdout_text.splitlines():
        stripped = line.strip()
        m = _RE_VERIFY_SUMMARY_A.match(stripped)
        if m:
            summary_line = line.rstrip()
            parsed_name = m.group("name") or ""
            verdict = m.group("verdict") or ""
            try:
                checks = int(m.group("checks") or 0)
            except (TypeError, ValueError):
                checks = 0
            # 只取首个 SUMMARY (一份 stdout 理应仅有一条)
            break
        mb = _RE_VERIFY_SUMMARY_B.match(stripped)
        if mb:
            summary_line = line.rstrip()
            parsed_name = mb.group("name") or ""
            try:
                total = int(mb.group("total") or 0)
                failed = int(mb.group("failed") or 0)
            except (TypeError, ValueError):
                total = 0
                failed = 1  # 保守: 解析异常视为 FAIL
            checks = total
            verdict = "ALL PASS" if failed == 0 else "FAIL"
            break

    fail_emits: list[str] = []
    for line in stdout_text.splitlines():
        m = _RE_VERIFY_EMIT_LINE.match(line.strip())
        if m and m.group("mark") == "FAIL":
            fail_emits.append(line.rstrip())

    reasons: list[str] = []
    if not summary_line:
        reasons.append("missing SUMMARY line")
    else:
        if parsed_name != verify_name:
            reasons.append(f"name mismatch: got={parsed_name!r} expect={verify_name!r}")
        if verdict != "ALL PASS":
            reasons.append(f"verdict={verdict!r} not ALL PASS")
    if fail_emits:
        reasons.append(f"fail_emits={len(fail_emits)}")

    passed = (
        bool(summary_line)
        and parsed_name == verify_name
        and verdict == "ALL PASS"
        and not fail_emits
    )

    return {
        "name": parsed_name,
        "passed": passed,
        "checks": checks,
        "summary_line": summary_line,
        "fail_emits": fail_emits,
        "reason": "; ".join(reasons) if reasons else "",
    }


# ---------------------------------------------------------------------------
# infra-P281-expected-prefix-typo-guard: 扫 EXPECTED_* 常量名 typo
# ---------------------------------------------------------------------------
# 规范 sha 锁后缀 (well-formed); 命中即视为合法 sha 反向锁。
_RE_EXPECTED_WELL_FORMED_SHA = re.compile(r"_(SHA|SHA256|SHA512)$")
# 明确的 typo 后缀族: 看起来想写 _SHA 但拼错。
# 注意: 这里只列高置信度 typo 变体, 避免误伤合法非 sha 常量
# (如 EXPECTED_PALETTE / EXPECTED_LINES / EXPECTED_FINGERPRINT)。
_RE_EXPECTED_TYPO_SUFFIX = re.compile(
    r"_(SHAH|HSA|HASH|HASHSUM|SHASUM|SH|SAH|SAH256|SHAA|SHAS|SAHA)$"
)
# 进一步: 名字中部包含 sha-ish 拼写但末尾不是规范的也算 typo
# 例: EXPECTED_LIB_SHAH_FILE, EXPECTED_FUNC_HSA_SHA (后者最终落到 _SHA 不算 typo)
_RE_EXPECTED_NAMEPART_TYPO = re.compile(
    r"(SHAH|HSA|HASHSUM|SHASUM|SAH256|SHAA|SHAS|SAHA)(?![A-Z0-9])"
)


def verify_expected_prefix_typo_guard(
    scripts_dir: "Path | None" = None,
) -> dict:
    """扫 ``scripts/verify_*.py`` 中 ``EXPECTED_*`` 前缀的模块级常量名 typo。

    规则
    ----
    - 收集每个 verify_*.py 顶层 ``Assign`` / ``AnnAssign`` 目标且名字 ``startswith("EXPECTED_")``。
    - **well-formed sha lock**: 名字末尾匹配 ``_(SHA|SHA256|SHA512)$``。
    - **non-sha legitimate**: 名字不含任何 sha-ish 后缀/中部 (e.g. ``EXPECTED_PALETTE``,
      ``EXPECTED_LINES``, ``EXPECTED_FINGERPRINT``) — 不视为 typo。
    - **typo**: 名字命中 ``_RE_EXPECTED_TYPO_SUFFIX`` (末尾 typo 变体) 或
      ``_RE_EXPECTED_NAMEPART_TYPO`` (中部 typo 拼写)。
      典型: ``EXPECTED_LIB_FILE_SHAH``, ``EXPECTED_FUNC_HSA``, ``EXPECTED_FILE_SH``。

    返回 schema::

        {
          "total_expected_consts": int,
          "well_formed": int,         # well-formed sha + legitimate non-sha 之和
          "typo_count": int,
          "typo_samples": list[{"path": str, "lineno": int,
                                "name": str, "reason": str}],  # 最多 10 条
          "all_well_formed": bool,    # typo_count == 0
        }

    P281 (infra-P281-expected-prefix-typo-guard): 源自 P276 Reviewer blind spot —
    现有锁规则只识别规范命名, typo 命名 (``_SHAH`` / ``_HSA`` / ``_SH``) 看似存在
    但实际从未参与一致性校验。本 helper 提供 ast-level 扫描器, 让 typo 在
    verify-time 直接被打出。
    """
    if scripts_dir is None:
        scripts_dir = Path(__file__).resolve().parent
    scripts_dir = Path(scripts_dir)

    total = 0
    well_formed = 0
    typos: list[dict] = []

    for py in sorted(scripts_dir.glob("verify_*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            target_names: list[tuple[int, str]] = []
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        target_names.append((t.lineno, t.id))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target_names.append((node.target.lineno, node.target.id))
            for lineno, name in target_names:
                if not name.startswith("EXPECTED_"):
                    continue
                total += 1
                # 命中 typo: 末尾或中部含 typo 拼写
                m_sfx = _RE_EXPECTED_TYPO_SUFFIX.search(name)
                m_part = _RE_EXPECTED_NAMEPART_TYPO.search(name)
                # 但若末尾仍是规范 _SHA/_SHA256/_SHA512, 视为良好
                if _RE_EXPECTED_WELL_FORMED_SHA.search(name) and not m_part:
                    well_formed += 1
                    continue
                if m_sfx or m_part:
                    bad = (m_sfx or m_part).group(0).lstrip("_")
                    # 建议最接近的合法后缀
                    suggest = "_SHA"
                    if "256" in bad:
                        suggest = "_SHA256"
                    elif "512" in bad:
                        suggest = "_SHA512"
                    reason = (
                        f"suffix/part '{bad}' looks like a typo of SHA family; "
                        f"suggest renaming to canonical '{suggest}' (well-formed = "
                        f"^EXPECTED_[A-Z0-9_]+(_(SHA|SHA256|SHA512))$)."
                    )
                    typos.append(
                        {
                            "path": str(py),
                            "lineno": lineno,
                            "name": name,
                            "reason": reason,
                        }
                    )
                else:
                    # 不含 sha-ish 拼写: 合法非 sha 常量
                    well_formed += 1

    return {
        "total_expected_consts": total,
        "well_formed": well_formed,
        "typo_count": len(typos),
        "typo_samples": typos[:10],
        "all_well_formed": len(typos) == 0,
    }


# ---------------------------------------------------------------------------
# infra-P278 (closeout-verify-trustworthy): 把"Closeout-verify-trustworthy"做成
# 机械化可验证的硬规则. 检查 feature 的 evidence dict 是否满足下列硬约束:
# 1) closeout_verify.main_head_sha 存在且 >=7 hex (合并后实测必须在 main HEAD)
# 2) closeout_verify.verify_runs 非空, 每项 tail_stdout 非空, status in {PASS,FAIL}
# 3) 任一 FAIL 必须配套 pre_existing_baseline_sha + baseline_tail_stdout 字段
#    (即必须先在 pre-merge main baseline 上独立复现, 否则不算 pre-existing)
# 4) closeout_verify.smoke_tail_stdout 非空
# 5) reviewer.reviewer_kind == 'sub_agent_fresh_context' 且 reviewer.lgtm == True
# ---------------------------------------------------------------------------
_RE_HEX7 = re.compile(r"^[0-9a-f]{7,}$")


# P294-R5: closeout-verify-trustworthy 内部 check 名表。total_checks 从此派生,
# 新增 trust 检查时往这里加一条即可, 不再需要外部同步硬编码常量。
# 注意: failed_checks 仍按 reasons 行数计 (向后兼容: 一个 check 内多个子项失败
# 会 append 多条 reason, 例如 verify_runs 多 entry 多失败)。
_CLOSEOUT_TRUSTWORTHY_CHECKS: tuple[str, ...] = (
    "main_head_sha",                  # rule 1
    "verify_runs_tail_and_baseline",  # rule 2 + 3 (合并: tail 非空 + FAIL 配套 baseline)
    "smoke_tail",                     # rule 4
    "reviewer_fresh_lgtm",            # rule 5
    "evidence_dict_shape",            # rule 0: closeout_verify + reviewer dict 存在
)


def verify_closeout_evidence_trustworthy(evidence: dict) -> dict:
    """检查 feature evidence dict 是否满足 closeout-verify-trustworthy 硬规则.

    输入 evidence schema (可选 key, 缺则记为不合规):

      evidence = {
        "closeout_verify": {
            "main_head_sha": "<7+ hex>",
            "smoke_tail_stdout": "<非空 str, >=1 行>",
            "verify_runs": [
                {
                    "script": "scripts/verify_xxx.py",
                    "tail_stdout": "<非空 str, >=1 行>",
                    "status": "PASS" | "FAIL",
                    # 若 status=="FAIL", 必须含:
                    "pre_existing_baseline_sha": "<main pre-merge HEAD>",
                    "baseline_tail_stdout": "<非空, 在 baseline 上独立复现>",
                },
                ...
            ],
        },
        "reviewer": {
            "reviewer_kind": "sub_agent_fresh_context",
            "lgtm": True,
        },
      }

    返回 dict::

        {
          "total_checks": int,
          "passed_checks": int,
          "failed_checks": int,
          "failed_reasons": list[str],
          "all_trustworthy": bool,
          "main_head_present": bool,
          "verify_runs_have_tail": bool,
          "reviewer_fresh_context": bool,
        }

    守恒律 (invariant): ``passed_checks + failed_checks == total_checks`` 恒成立,
    用于下游 V4 把"推导关系"升级为"独立字段交叉锁" (P299).
    """
    reasons: list[str] = []

    main_head_present = False
    verify_runs_have_tail = False
    reviewer_fresh_context = False

    cv = evidence.get("closeout_verify") if isinstance(evidence, dict) else None
    if not isinstance(cv, dict):
        reasons.append("missing closeout_verify dict")
    else:
        # rule 1: main_head_sha
        sha = cv.get("main_head_sha")
        if isinstance(sha, str) and _RE_HEX7.match(sha.strip().lower()):
            main_head_present = True
        else:
            reasons.append(
                f"closeout_verify.main_head_sha invalid (got {sha!r}, "
                f"expect >=7 hex chars)"
            )

        # rule 2 + 3: verify_runs non-empty, each tail_stdout non-empty, FAIL → baseline
        runs = cv.get("verify_runs")
        if not isinstance(runs, list) or len(runs) == 0:
            reasons.append("closeout_verify.verify_runs missing or empty")
        else:
            run_ok = True
            for i, r in enumerate(runs):
                if not isinstance(r, dict):
                    reasons.append(f"verify_runs[{i}] not a dict")
                    run_ok = False
                    continue
                tail = r.get("tail_stdout")
                if not isinstance(tail, str) or not tail.strip():
                    reasons.append(
                        f"verify_runs[{i}] tail_stdout empty/missing "
                        f"(script={r.get('script')!r})"
                    )
                    run_ok = False
                status = r.get("status")
                if status not in ("PASS", "FAIL"):
                    reasons.append(
                        f"verify_runs[{i}] status invalid: {status!r} "
                        f"(expect PASS or FAIL)"
                    )
                    run_ok = False
                if status == "FAIL":
                    base_sha = r.get("pre_existing_baseline_sha")
                    base_tail = r.get("baseline_tail_stdout")
                    if not (isinstance(base_sha, str)
                            and _RE_HEX7.match(base_sha.strip().lower())):
                        reasons.append(
                            f"verify_runs[{i}] FAIL lacks "
                            f"pre_existing_baseline_sha (got {base_sha!r})"
                        )
                        run_ok = False
                    if not (isinstance(base_tail, str) and base_tail.strip()):
                        reasons.append(
                            f"verify_runs[{i}] FAIL lacks baseline_tail_stdout"
                        )
                        run_ok = False
            if run_ok:
                verify_runs_have_tail = True

        # rule 4: smoke_tail_stdout
        smoke = cv.get("smoke_tail_stdout")
        if not (isinstance(smoke, str) and smoke.strip()):
            reasons.append("closeout_verify.smoke_tail_stdout empty/missing")

    # rule 5: reviewer kind + lgtm
    rv = evidence.get("reviewer") if isinstance(evidence, dict) else None
    if not isinstance(rv, dict):
        reasons.append("missing reviewer dict")
    else:
        kind = rv.get("reviewer_kind")
        lgtm = rv.get("lgtm")
        if kind == "sub_agent_fresh_context" and lgtm is True:
            reviewer_fresh_context = True
        else:
            reasons.append(
                f"reviewer not compliant: kind={kind!r} lgtm={lgtm!r} "
                f"(expect kind='sub_agent_fresh_context' and lgtm=True)"
            )

    # P294-R5: total_checks 从内部 _CHECKS 表派生, 未来新增 trust check 自动扩展,
    # 不再依赖外部硬编码常量 (旧实现: total_checks = 5)。
    total_checks = len(_CLOSEOUT_TRUSTWORTHY_CHECKS)
    failed = len(reasons)
    return {
        "total_checks": total_checks,
        "passed_checks": total_checks - failed,
        "failed_checks": failed,
        "failed_reasons": reasons,
        "all_trustworthy": failed == 0,
        "main_head_present": main_head_present,
        "verify_runs_have_tail": verify_runs_have_tail,
        "reviewer_fresh_context": reviewer_fresh_context,
    }


# ---------------------------------------------------------------------------
# infra-P294-R4 (fail-baseline-cross-check): Reviewer 报告中声称 "verify_infra_XXX
# FAIL on baseline" 的脚本, closeout 阶段须实跑确认其在 main baseline ref 上确实
# FAIL, 防止 Reviewer 凭空编造 baseline 状态 (P278 round-1 教训: Reviewer 误报
# verify_infra_061 FAIL on baseline, 而 Engineer 实跑 PASS, 当时只能靠第三方
# 仲裁实跑解决). 本 helper 把该交叉校验固化进 verify 框架, 接受 Reviewer 文本 +
# baseline ref + repo root, 解析 "verify_infra_XXX (FAIL|fail|失败)" 模式, 在
# baseline ref 上以 git worktree 隔离实跑对应脚本, 比对 claim 与实际 rc。
# Default-OFF: 不在任何已有流程自动运行; 由调用方显式触发。
# ---------------------------------------------------------------------------
_RE_BASELINE_FAIL_CLAIM = re.compile(
    r"verify_infra_(\d{3})\b[\s\S]{0,200}?\b(FAIL|fail|失败)\b"
)


def verify_baseline_fail_claims(
    reviewer_text: str,
    baseline_ref: str,
    repo_root: "Path | str",
    timeout_per_script: int = 60,
) -> dict:
    """Cross-check Reviewer baseline FAIL claims against actual baseline runs.

    输入:
      reviewer_text: Reviewer sub-agent 返回的完整文本 (中英混合 OK)
      baseline_ref: git ref (sha / branch / tag), 用 ``git rev-parse`` 校验
      repo_root: 仓库根目录 (含 ``.git``)
      timeout_per_script: 单脚本运行超时秒数 (默认 60s)

    解析规则:
      用 ``_RE_BASELINE_FAIL_CLAIM`` 抽取 "verify_infra_<NNN>" 与
      "FAIL/fail/失败" 同行/近距离 (<=80 chars) 共现的所有 claim, 去重后逐一
      在 baseline ref 上实跑。

    实跑机制:
      用 ``git worktree add --detach <tmpdir> <baseline_ref>`` 隔离, 在该
      worktree 内 ``python scripts/verify_infra_XXX.py`` (若不存在则记为
      missing); 比对 ``rc != 0`` 与 claim_fail (=True)。worktree 在 finally
      中 ``git worktree remove --force`` 清理。

    返回 dict::

        {
          "claims_total": int,
          "claims_verified": int,        # 实测 FAIL 与 claim 一致
          "claims_contradicted": int,    # 实测 PASS 但 claim 说 FAIL
          "claims_missing": int,         # baseline 上脚本不存在
          "baseline_ref": str,
          "baseline_sha": str,           # rev-parse 结果 (空字符串若无效)
          "contradictions": list[dict],  # [{script, claimed_fail, actual_fail, actual_rc, note}]
          "missing_scripts": list[str],
          "error": str | None,           # 顶层错误 (baseline_ref 无效等)
        }

    Default-OFF 约定: 本 helper 不在任何已有 verify 流程自动调用; 由 closeout
    sub-agent 或专用 verify 脚本显式触发。
    """
    import subprocess  # noqa: WPS433 — 本 helper 自包含, 避免污染模块顶层
    import tempfile

    repo_root = Path(repo_root).resolve()

    # 1) baseline_ref 校验: git rev-parse
    proc_rp = subprocess.run(
        ["git", "rev-parse", "--verify", f"{baseline_ref}^{{commit}}"],
        cwd=str(repo_root), capture_output=True, text=True, check=False,
    )
    if proc_rp.returncode != 0:
        return {
            "claims_total": 0,
            "claims_verified": 0,
            "claims_contradicted": 0,
            "claims_missing": 0,
            "baseline_ref": baseline_ref,
            "baseline_sha": "",
            "contradictions": [],
            "missing_scripts": [],
            "error": (
                f"baseline_ref invalid: {baseline_ref!r} "
                f"(git rev-parse stderr={proc_rp.stderr.strip()!r})"
            ),
        }
    baseline_sha = proc_rp.stdout.strip()

    # 2) 解析 claims (去重)
    claim_ids: list[str] = []
    seen: set[str] = set()
    for m in _RE_BASELINE_FAIL_CLAIM.finditer(reviewer_text or ""):
        nnn = m.group(1)
        if nnn not in seen:
            seen.add(nnn)
            claim_ids.append(nnn)

    if not claim_ids:
        return {
            "claims_total": 0,
            "claims_verified": 0,
            "claims_contradicted": 0,
            "claims_missing": 0,
            "baseline_ref": baseline_ref,
            "baseline_sha": baseline_sha,
            "contradictions": [],
            "missing_scripts": [],
            "error": None,
        }

    # 3) git worktree add 隔离实跑
    contradictions: list[dict] = []
    missing: list[str] = []
    verified = 0
    with tempfile.TemporaryDirectory(prefix="coco_p294r4_wt_") as tmpd:
        wt_path = Path(tmpd) / "wt"
        proc_add = subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt_path), baseline_sha],
            cwd=str(repo_root), capture_output=True, text=True, check=False,
        )
        if proc_add.returncode != 0:
            return {
                "claims_total": len(claim_ids),
                "claims_verified": 0,
                "claims_contradicted": 0,
                "claims_missing": 0,
                "baseline_ref": baseline_ref,
                "baseline_sha": baseline_sha,
                "contradictions": [],
                "missing_scripts": [],
                "error": (
                    f"git worktree add failed: "
                    f"stderr={proc_add.stderr.strip()!r}"
                ),
            }
        try:
            for nnn in claim_ids:
                script_rel = f"scripts/verify_infra_{nnn}.py"
                script_abs = wt_path / script_rel
                if not script_abs.is_file():
                    missing.append(script_rel)
                    contradictions.append({
                        "script": script_rel,
                        "claimed_fail": True,
                        "actual_fail": None,
                        "actual_rc": None,
                        "note": "script missing on baseline",
                    })
                    continue
                try:
                    proc_run = subprocess.run(
                        [sys.executable, script_rel],
                        cwd=str(wt_path),
                        capture_output=True, text=True, check=False,
                        timeout=timeout_per_script,
                    )
                    actual_rc = proc_run.returncode
                except Exception as e:  # noqa: BLE001
                    contradictions.append({
                        "script": script_rel,
                        "claimed_fail": True,
                        "actual_fail": None,
                        "actual_rc": None,
                        "note": f"run error: {e!r}",
                    })
                    continue
                actual_fail = actual_rc != 0
                if actual_fail:
                    verified += 1
                else:
                    contradictions.append({
                        "script": script_rel,
                        "claimed_fail": True,
                        "actual_fail": False,
                        "actual_rc": actual_rc,
                        "note": "Reviewer claimed FAIL but baseline run PASSED",
                    })
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=str(repo_root), capture_output=True, text=True, check=False,
            )

    # claims_contradicted 仅指 "claim FAIL 但实测 PASS" (不含 missing)
    contradicted = sum(
        1 for c in contradictions if c.get("actual_fail") is False
    )

    return {
        "claims_total": len(claim_ids),
        "claims_verified": verified,
        "claims_contradicted": contradicted,
        "claims_missing": len(missing),
        "baseline_ref": baseline_ref,
        "baseline_sha": baseline_sha,
        "contradictions": contradictions,
        "missing_scripts": missing,
        "error": None,
    }


# ---------------------------------------------------------------------------
# infra-P294-Rx (phase-38 #3.38): verify_*.py 退出码统一约定 helper
# ---------------------------------------------------------------------------
# 背景: phase-38 #2.38 round-1 Engineer 误信 shell 复合命令的 ``$?`` 判断
# verify_*.py 是否 PASS, 导致仲裁触发。机制化校验 (verify_infra_067) 实测:
# 现存 67 个 verify_infra_*.py 全部正确传播非 0 rc, 但缺统一 API,
# 后续脚本若手抄 ``if FAILURES: return 1`` 模式存在回归风险。
#
# 本 helper 提供单一入口: 调用方传入 failed_count, helper 负责
# ``sys.exit(2 if failed_count > 0 else 0)``。退出码约定:
#   - 0  : 全 PASS
#   - 2  : 至少 1 个 FAIL (非 1, 与 ``sys.exit(main())`` 的 return 1 区分,
#         便于上层 (subprocess) 判别 "verify 业务 FAIL" vs "Python 通用错误")
def verify_summary_exit(failed_count: int) -> None:
    """退出码 helper: failed_count>0 → sys.exit(2); 否则 sys.exit(0)。

    用法 (推荐):
        failed = sum(1 for _, ok, _ in _results if not ok)
        print(f"[verify_infra_NNN][SUMMARY] {'FAIL '+str(failed)+'/'+str(total) if failed else 'ALL PASS ('+str(total)+' checks)'}", flush=True)
        verify_summary_exit(failed)

    现存 67 个 verify_infra_*.py 仍可保留 ``sys.exit(main())`` + ``return 1``
    旧模式, 新脚本一律走本 helper。verify_infra_067 机制化校验所有脚本
    SUMMARY/summary FAIL 时 rc != 0 (无论走哪种实现)。
    """
    if not isinstance(failed_count, int):
        raise TypeError(f"failed_count must be int, got {type(failed_count).__name__}")
    if failed_count < 0:
        raise ValueError(f"failed_count must be >= 0, got {failed_count}")
    sys.exit(2 if failed_count > 0 else 0)


# ---------------------------------------------------------------------------
# infra-P294-Ry (reviewer text scan): Closeout 阶段 Reviewer 文本红旗检测
# ---------------------------------------------------------------------------
# 背景: P278 verify_closeout_evidence_trustworthy 只看 evidence.reviewer.reviewer_kind
# 字段是否字面 == "sub_agent_fresh_context", 若 Engineer 编造 reviewer_kind 字段但
# Reviewer 实际内容空洞 ("我刚刚跑了 ... 全 PASS" 无任何命令/sha/file:line 证据),
# P278 helper 无法识别。本 helper 扫 Reviewer 文本字符串本身, 检测 4 类硬证据信号,
# 提高 closeout-verify-trustworthy 整体信号强度, 把"编造 reviewer 内容"的成本拉高。
#
# 设计原则:
# - 纯字符串扫描, 不实跑命令, 不读真实 evidence (避免 P278 round-2 教训)。
# - 检 4 类信号: verdict 行 / 命令引用 / sha 引用 / file:line 引用。
# - signal_count >= 3 即 min_signals_met (允许 1 项缺失, 例如纯 doc-only review
#   可能无 file:line 引用)。
# - Default-OFF: 不在任何已有 verify 流程自动调用, 由 closeout sub-agent 或专用
#   verify_infra_068 显式调用。
_RE_REVIEWER_VERDICT = re.compile(r"\b(LGTM(?:\s+with\s+findings)?|REJECT)\b", re.IGNORECASE)
_RE_REVIEWER_COMMAND = re.compile(
    r"(\.venv/bin/python\b|\bgit\s+-C\b|scripts/verify_infra_\d{3}\b)"
)
_RE_REVIEWER_SHA = re.compile(r"\b[0-9a-f]{7,}\b")
_RE_REVIEWER_FILE_LINE = re.compile(r"\b[\w./\-]+\.py:\d+\b")


def scan_reviewer_text(reviewer_text: str) -> dict:
    """扫 Reviewer evidence 文本块, 检测 4 类硬证据信号.

    输入: reviewer 文本 (e.g. evidence.reviewer.summary / findings 拼接), 中英混合 OK.
    输出 dict::

        {
          "has_verdict_line": bool,      # 含 LGTM / REJECT / LGTM with findings
          "has_command_evidence": bool,  # 含 .venv/bin/python | git -C | scripts/verify_infra_NNN
          "has_sha_evidence": bool,      # 含 7+ hex sha
          "has_file_line_ref": bool,     # 含 file.py:NN 模式
          "signal_count": int,           # 上 4 项 True 计数
          "min_signals_met": bool,       # signal_count >= 3 (阈值)
          "flags": list[str],            # 红旗 (e.g. "no_verdict" / "no_command" / "no_sha" / "no_file_line")
        }

    Default-OFF: 由 closeout sub-agent 或 verify_infra_068 显式调用, 不进入任何
    自动 verify 流程。signal 阈值 3 允许 1 项缺失 (例: 纯文档 review 可能无 file:line)。
    """
    if not isinstance(reviewer_text, str):
        reviewer_text = ""

    has_verdict = bool(_RE_REVIEWER_VERDICT.search(reviewer_text))
    has_command = bool(_RE_REVIEWER_COMMAND.search(reviewer_text))
    has_sha = bool(_RE_REVIEWER_SHA.search(reviewer_text))
    has_file_line = bool(_RE_REVIEWER_FILE_LINE.search(reviewer_text))

    signal_count = sum([has_verdict, has_command, has_sha, has_file_line])
    min_signals_met = signal_count >= 3

    flags: list[str] = []
    if not has_verdict:
        flags.append("no_verdict")
    if not has_command:
        flags.append("no_command")
    if not has_sha:
        flags.append("no_sha")
    if not has_file_line:
        flags.append("no_file_line")

    return {
        "has_verdict_line": has_verdict,
        "has_command_evidence": has_command,
        "has_sha_evidence": has_sha,
        "has_file_line_ref": has_file_line,
        "signal_count": signal_count,
        "min_signals_met": min_signals_met,
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# infra-P294-closeout-stdout-sha-verification (phase-39 #1.39): closeout
# evidence 的 tail_stdout 加 sha256 防伪机制。
#
# 背景: P278 round-1 dogfood 设计踩坑 — 把 tail_stdout 文本字段塞回 evidence 仅
# 验非空即判 trustworthy, 等于让 Engineer 写任意 "ALL PASS" 文本就能蒙混 closeout
# trust gate (单点谎言换地方放)。Round-2 设计修: helper 改回只验 schema。本 helper
# 是真正的解药: evidence 加 ``tail_stdout_sha256`` 字段, 由本 helper 在 main HEAD
# (用 git worktree 隔离) 现场 re-run 对应 verify 脚本, 取 stdout 同字符长度的 tail,
# 计算 sha256 比对 evidence 中记录值, 不一致即返回 ok=False + offending list。
#
# Default-OFF: 本 helper 不在任何已有 verify 流程自动调用; 由 closeout sub-agent
# 或专用 verify (verify_infra_070) 显式触发。
# ---------------------------------------------------------------------------
def verify_evidence_tail_stdout_sha(
    evidence: dict,
    repo_root: "Path | str",
    main_head_sha: str,
    scripts_dir_name: str = "scripts",
    timeout_per_script: int = 60,
) -> dict:
    """对 evidence.verify_runs 在 main HEAD 实跑并 sha256 比对 tail_stdout.

    算法 (sha256 anti-forgery):

    1. 校验 ``main_head_sha`` 可被 ``git rev-parse`` 解析为有效 commit。
    2. 用 ``git worktree add --detach`` 把 main_head_sha 检出到临时目录, 隔离运行。
    3. 对 ``evidence['verify_runs']`` 每一项 (含 ``script``, ``tail_stdout``,
       ``tail_stdout_sha256``):
         - 在 worktree 内 ``python <script>`` 运行 (cwd = worktree root);
         - 取 captured stdout 的 tail (与 ``len(evidence_tail_stdout)`` 同长字符数,
           从末尾起取);
         - 计算 sha256(tail_bytes) (utf-8 编码), 与 ``tail_stdout_sha256`` 比对;
         - 不一致 → offending list 加一条 (含 script / expected / actual);
         - 缺失字段 (tail_stdout / tail_stdout_sha256) → reasons 加一条, ok=False。
    4. worktree 在 finally 中 ``git worktree remove --force`` 清理。

    输入:
      evidence: dict, 至少含 ``verify_runs: list[dict]`` 子结构, 每项需:
        {
          "script": "scripts/verify_infra_NNN.py",  (相对 repo_root)
          "tail_stdout": "<非空 str>",
          "tail_stdout_sha256": "<64-hex sha256 of utf-8 tail bytes>",
        }
      repo_root: 仓库根目录 (含 .git)
      main_head_sha: 期望 re-run 的 commit ref (sha / branch / tag)
      scripts_dir_name: scripts 目录名 (默认 "scripts")
      timeout_per_script: 单脚本运行超时秒数 (默认 60s)

    返回 dict::

        {
          "ok": bool,                          # 全部 sha 匹配 + 无字段缺失
          "checked": int,                      # 实跑脚本数
          "matched": int,                      # sha 匹配数
          "offending": list[dict],             # 不匹配项 [{script, expected_sha, actual_sha, note}]
          "missing_fields": list[dict],        # 缺字段项 [{index, script, missing}]
          "main_head_sha_resolved": str,       # rev-parse 解析后 sha (空字符串若无效)
          "error": str | None,                 # 顶层错误 (ref 无效 / worktree 失败等)
        }

    Default-OFF 约定: 本 helper 不在已有 verify 自动运行流程出现; 仅由 closeout
    sub-agent 或 verify_infra_070 显式触发。
    """
    import subprocess  # noqa: WPS433
    import tempfile

    repo_root = Path(repo_root).resolve()

    # 1) main_head_sha 校验
    proc_rp = subprocess.run(
        ["git", "rev-parse", "--verify", f"{main_head_sha}^{{commit}}"],
        cwd=str(repo_root), capture_output=True, text=True, check=False,
    )
    if proc_rp.returncode != 0:
        return {
            "ok": False,
            "checked": 0,
            "matched": 0,
            "offending": [],
            "missing_fields": [],
            "main_head_sha_resolved": "",
            "error": (
                f"main_head_sha invalid: {main_head_sha!r} "
                f"(git rev-parse stderr={proc_rp.stderr.strip()!r})"
            ),
        }
    resolved_sha = proc_rp.stdout.strip()

    # 2) evidence schema 基本检查
    if not isinstance(evidence, dict):
        return {
            "ok": False,
            "checked": 0,
            "matched": 0,
            "offending": [],
            "missing_fields": [],
            "main_head_sha_resolved": resolved_sha,
            "error": "evidence is not a dict",
        }
    # 支持 evidence 顶层 verify_runs 或嵌套在 closeout_verify.verify_runs
    runs = evidence.get("verify_runs")
    if runs is None:
        cv = evidence.get("closeout_verify")
        if isinstance(cv, dict):
            runs = cv.get("verify_runs")
    if not isinstance(runs, list) or not runs:
        return {
            "ok": False,
            "checked": 0,
            "matched": 0,
            "offending": [],
            "missing_fields": [],
            "main_head_sha_resolved": resolved_sha,
            "error": "evidence.verify_runs missing or empty",
        }

    offending: list[dict] = []
    missing_fields: list[dict] = []
    matched = 0
    checked = 0

    # 3) git worktree add 隔离实跑
    with tempfile.TemporaryDirectory(prefix="coco_p294_sha_wt_") as tmpd:
        wt_path = Path(tmpd) / "wt"
        proc_add = subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt_path), resolved_sha],
            cwd=str(repo_root), capture_output=True, text=True, check=False,
        )
        if proc_add.returncode != 0:
            return {
                "ok": False,
                "checked": 0,
                "matched": 0,
                "offending": [],
                "missing_fields": [],
                "main_head_sha_resolved": resolved_sha,
                "error": (
                    f"git worktree add failed: "
                    f"stderr={proc_add.stderr.strip()!r}"
                ),
            }
        try:
            for i, r in enumerate(runs):
                if not isinstance(r, dict):
                    missing_fields.append({
                        "index": i,
                        "script": None,
                        "missing": "<not a dict>",
                    })
                    continue
                script_rel = r.get("script")
                ev_tail = r.get("tail_stdout")
                ev_sha = r.get("tail_stdout_sha256")
                missing_keys: list[str] = []
                if not isinstance(script_rel, str) or not script_rel.strip():
                    missing_keys.append("script")
                if not isinstance(ev_tail, str) or not ev_tail:
                    missing_keys.append("tail_stdout")
                if (not isinstance(ev_sha, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", ev_sha or "")):
                    missing_keys.append("tail_stdout_sha256")
                if missing_keys:
                    missing_fields.append({
                        "index": i,
                        "script": script_rel,
                        "missing": ",".join(missing_keys),
                    })
                    continue

                script_abs = wt_path / script_rel
                if not script_abs.is_file():
                    offending.append({
                        "script": script_rel,
                        "expected_sha": ev_sha,
                        "actual_sha": None,
                        "note": "script missing on main HEAD worktree",
                    })
                    checked += 1
                    continue
                try:
                    proc_run = subprocess.run(
                        [sys.executable, script_rel],
                        cwd=str(wt_path),
                        capture_output=True, text=True, check=False,
                        timeout=timeout_per_script,
                    )
                except Exception as e:  # noqa: BLE001
                    offending.append({
                        "script": script_rel,
                        "expected_sha": ev_sha,
                        "actual_sha": None,
                        "note": f"run error: {e!r}",
                    })
                    checked += 1
                    continue
                actual_stdout = proc_run.stdout or ""
                # 取与 evidence tail_stdout 同字符长度的末尾子串
                n_chars = len(ev_tail)
                actual_tail = actual_stdout[-n_chars:] if n_chars > 0 else ""
                actual_sha = hashlib.sha256(
                    actual_tail.encode("utf-8")
                ).hexdigest()
                checked += 1
                if actual_sha == ev_sha:
                    matched += 1
                else:
                    offending.append({
                        "script": script_rel,
                        "expected_sha": ev_sha,
                        "actual_sha": actual_sha,
                        "note": (
                            f"sha256 mismatch (rerun rc={proc_run.returncode}, "
                            f"tail_len={n_chars})"
                        ),
                    })
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=str(repo_root), capture_output=True, text=True, check=False,
            )

    ok = (not offending) and (not missing_fields) and (checked > 0)
    return {
        "ok": ok,
        "checked": checked,
        "matched": matched,
        "offending": offending,
        "missing_fields": missing_fields,
        "main_head_sha_resolved": resolved_sha,
        "error": None,
    }


# ---------------------------------------------------------------------------
# infra-P291-reviewer-gate-real-or-remove: Reviewer LGTM gate helper
# ---------------------------------------------------------------------------
def assert_reviewer_lgtm(
    feature_id: str,
    feature_list_path: "Path | str",
) -> tuple[bool, str]:
    """实读 feature_list.json, 校验某 feature 的 Reviewer LGTM 字段是否合规。

    名实相符: 这是真 evidence-driven 的 Reviewer LGTM gate, 替代 verify_infra_*.py
    中各处 hardcoded ``True`` 的 V5_reviewer_lgtm_gate placeholder (P291)。

    校验规则 (全部满足返回 ok=True):
      1. feature_list.json 可被读取并解析为 JSON 顶层含 ``features: list`` 。
      2. 列表中存在 id == feature_id 的 feature 条目。
      3. feature ``evidence`` 子结构为 dict; 其 ``reviewer`` 字段或
         ``closeout_verify.reviewer`` 字段为 dict (优先后者, closeout 写入位置)。
      4. ``reviewer["reviewer_kind"] == "sub_agent_fresh_context"`` 。
      5. ``reviewer["verdict"]`` (大小写不敏感) 等于 "LGTM" 。

    缺任一字段或值不匹配 → ok=False, reason 指明问题。

    Default-OFF 哲学 (P291 acceptance):
      本 helper 只是工具, **不强制 cascade** 到所有现有 verify_infra_*.py。仅由
      ``scripts/verify_infra_071.py`` (本 feature 的 verify) V5 主动调用; 其它
      verify 的 V5 placeholder 保留, 后续 backlog 推进。

    Args:
      feature_id: 例如 "infra-P294-closeout-stdout-sha-verification"
      feature_list_path: 通常是 repo_root / "feature_list.json"

    Returns:
      (ok, reason) where reason is a short human-readable string.
    """
    import json as _json  # noqa: WPS433

    path = Path(feature_list_path)
    if not path.is_file():
        return False, f"feature_list.json not found: {path}"
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return False, f"feature_list.json parse error: {e!r}"
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        return False, "feature_list.json missing top-level 'features' list"
    target = None
    for f in features:
        if isinstance(f, dict) and f.get("id") == feature_id:
            target = f
            break
    if target is None:
        return False, f"feature id={feature_id!r} not found in feature_list.json"
    evidence = target.get("evidence")
    if not isinstance(evidence, dict):
        return False, (
            f"feature {feature_id!r} has no 'evidence' dict "
            f"(got type={type(evidence).__name__})"
        )
    # 优先 closeout_verify.reviewer (closeout sub-agent 写入的标准位置)
    reviewer = None
    cv = evidence.get("closeout_verify")
    if isinstance(cv, dict) and isinstance(cv.get("reviewer"), dict):
        reviewer = cv["reviewer"]
    elif isinstance(evidence.get("reviewer"), dict):
        reviewer = evidence["reviewer"]
    if not isinstance(reviewer, dict):
        return False, (
            f"feature {feature_id!r} evidence.reviewer (or "
            f"evidence.closeout_verify.reviewer) missing or not a dict"
        )
    kind = reviewer.get("reviewer_kind")
    if kind != "sub_agent_fresh_context":
        return False, (
            f"feature {feature_id!r} reviewer_kind={kind!r}, "
            f"expected 'sub_agent_fresh_context'"
        )
    verdict = reviewer.get("verdict")
    if not isinstance(verdict, str) or verdict.strip().upper() != "LGTM":
        return False, (
            f"feature {feature_id!r} reviewer.verdict={verdict!r}, "
            f"expected 'LGTM'"
        )
    return True, (
        f"feature {feature_id!r}: reviewer_kind={kind!r} verdict={verdict!r} OK"
    )


# ---------------------------------------------------------------------------
# infra-P299-engineer-report-vs-impl-trustworthy: Engineer/Reviewer report
# vs closeout 实跑 byte-match helper
# ---------------------------------------------------------------------------
# P291 真实造假事件: Engineer report 谎称 6 项 verify FAIL 是 baseline pre-existing,
# 第三方实跑全 PASS. 本 helper 在指定 main_head_sha 检出隔离 worktree, 真
# subprocess.run report 中每条 verify, 比对 actual_tail 与 claimed tail 的
# sha256 (与 P294 同口径: utf-8 编码 last 500 chars), 同时校 rc.
#
# 与 P294 verify_evidence_tail_stdout_sha 的区别:
#   - P294 输入 evidence (顶层 verify_runs 或 closeout_verify.verify_runs), 单 round
#   - P299 输入 report_obj (verify_runs: [{round, scripts: {<name>: {rc,
#     tail_stdout, status}}}, ...]), 多 round, 也校 rc
#
# Default-OFF: 本 helper 不在已有 verify 自动调用; 仅由 verify_infra_075 / 未来
# closeout enforcement 显式触发。
# ---------------------------------------------------------------------------
def assert_report_matches_closeout_runs(
    report_obj: dict,
    repo_root: "Path | str",
    main_head_sha: str,
    tail_chars: int = 500,
    timeout_per_script: int = 60,
) -> dict:
    """对 report_obj.verify_runs 在 main_head_sha 实跑并 byte-match 比对 tail+rc.

    算法:
      1. git rev-parse 校 main_head_sha 有效, 拿 resolved sha.
      2. git worktree add --detach 检出 resolved sha 到 tmpdir.
      3. 遍历 report_obj['verify_runs'] (list[dict]), 每项含
         {"round": int, "scripts": {<verify_name>: {"rc": int, "tail_stdout":
         str, "status": str}, ...}}.
         对每个 scripts 条目 subprocess.run(python <name>) (cwd=worktree),
         取 actual_stdout 的 last `tail_chars` 字符, sha256(utf-8) 比对
         sha256(claimed_tail.encode("utf-8")), 并比对 rc.
      4. 不一致 → offending 加一条 {script, round, claimed_sha, actual_sha,
         claimed_rc, actual_rc}.
      5. finally 清 worktree.

    输入:
      report_obj: dict, 至少含
        {
          "verify_runs": [
            {
              "round": int,
              "scripts": {
                "verify_infra_NNN.py": {
                  "rc": int,
                  "tail_stdout": str,        # 最近 tail_chars 字符 (utf-8)
                  "status": "PASS" | "FAIL",
                },
                ...
              },
            },
            ...
          ],
        }
      repo_root: 含 .git 的仓库根
      main_head_sha: 期望 re-run 的 commit ref
      tail_chars: 取 stdout 末尾字符数 (默认 500, 与 P294 一致)
      timeout_per_script: 单脚本超时秒数 (默认 60s)

    返回:
      {
        "ok": bool,                          # 全部 byte+rc 匹配
        "checked": int,                      # 实跑脚本-round pair 数
        "matched": int,                      # 全部匹配数
        "offending": list[dict],             # [{script, round, claimed_sha,
                                             #   actual_sha, claimed_rc, actual_rc}]
        "main_head_sha_resolved": str,
        "error": str | None,
      }

    Default-OFF: 本 helper 不在已有 verify 自动运行; 仅 verify_infra_075 / 未来
    closeout enforcement 显式触发。
    """
    import subprocess  # noqa: WPS433
    import tempfile

    repo_root = Path(repo_root).resolve()

    # 1) main_head_sha rev-parse
    proc_rp = subprocess.run(
        ["git", "rev-parse", "--verify", f"{main_head_sha}^{{commit}}"],
        cwd=str(repo_root), capture_output=True, text=True, check=False,
    )
    if proc_rp.returncode != 0:
        return {
            "ok": False,
            "checked": 0,
            "matched": 0,
            "offending": [],
            "main_head_sha_resolved": "",
            "error": (
                f"main_head_sha invalid: {main_head_sha!r} "
                f"(git rev-parse stderr={proc_rp.stderr.strip()!r})"
            ),
        }
    resolved_sha = proc_rp.stdout.strip()

    # 2) report schema 基本检查
    if not isinstance(report_obj, dict):
        return {
            "ok": False,
            "checked": 0,
            "matched": 0,
            "offending": [],
            "main_head_sha_resolved": resolved_sha,
            "error": "report_obj is not a dict",
        }
    runs = report_obj.get("verify_runs")
    if not isinstance(runs, list) or not runs:
        return {
            "ok": False,
            "checked": 0,
            "matched": 0,
            "offending": [],
            "main_head_sha_resolved": resolved_sha,
            "error": "report_obj.verify_runs missing or empty",
        }

    offending: list[dict] = []
    matched = 0
    checked = 0

    # 3) git worktree add 隔离实跑
    with tempfile.TemporaryDirectory(prefix="coco_p299_match_wt_") as tmpd:
        wt_path = Path(tmpd) / "wt"
        proc_add = subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt_path), resolved_sha],
            cwd=str(repo_root), capture_output=True, text=True, check=False,
        )
        if proc_add.returncode != 0:
            return {
                "ok": False,
                "checked": 0,
                "matched": 0,
                "offending": [],
                "main_head_sha_resolved": resolved_sha,
                "error": (
                    f"git worktree add failed: "
                    f"stderr={proc_add.stderr.strip()!r}"
                ),
            }
        try:
            for round_entry in runs:
                if not isinstance(round_entry, dict):
                    continue
                round_id = round_entry.get("round")
                scripts_map = round_entry.get("scripts")
                if not isinstance(scripts_map, dict):
                    continue
                for script_name, script_info in scripts_map.items():
                    if not isinstance(script_info, dict):
                        continue
                    claimed_rc = script_info.get("rc")
                    claimed_tail = script_info.get("tail_stdout")
                    if (not isinstance(script_name, str)
                            or not isinstance(claimed_rc, int)
                            or not isinstance(claimed_tail, str)):
                        offending.append({
                            "script": script_name,
                            "round": round_id,
                            "claimed_sha": None,
                            "actual_sha": None,
                            "claimed_rc": claimed_rc,
                            "actual_rc": None,
                            "note": "missing/invalid fields",
                        })
                        checked += 1
                        continue
                    # 支持完整路径 (scripts/verify_infra_NNN.py) 或纯文件名
                    script_rel = script_name
                    if "/" not in script_rel:
                        script_rel = f"scripts/{script_rel}"
                    script_abs = wt_path / script_rel
                    if not script_abs.is_file():
                        offending.append({
                            "script": script_name,
                            "round": round_id,
                            "claimed_sha": hashlib.sha256(
                                claimed_tail.encode("utf-8")
                            ).hexdigest(),
                            "actual_sha": None,
                            "claimed_rc": claimed_rc,
                            "actual_rc": None,
                            "note": "script missing on resolved sha worktree",
                        })
                        checked += 1
                        continue
                    try:
                        proc_run = subprocess.run(
                            [sys.executable, script_rel],
                            cwd=str(wt_path),
                            capture_output=True, text=True, check=False,
                            timeout=timeout_per_script,
                        )
                    except Exception as e:  # noqa: BLE001
                        offending.append({
                            "script": script_name,
                            "round": round_id,
                            "claimed_sha": hashlib.sha256(
                                claimed_tail.encode("utf-8")
                            ).hexdigest(),
                            "actual_sha": None,
                            "claimed_rc": claimed_rc,
                            "actual_rc": None,
                            "note": f"run error: {e!r}",
                        })
                        checked += 1
                        continue
                    actual_stdout = proc_run.stdout or ""
                    actual_tail = actual_stdout[-tail_chars:] if tail_chars > 0 else ""
                    actual_sha = hashlib.sha256(
                        actual_tail.encode("utf-8")
                    ).hexdigest()
                    claimed_sha = hashlib.sha256(
                        claimed_tail[-tail_chars:].encode("utf-8")
                    ).hexdigest()
                    actual_rc = proc_run.returncode
                    checked += 1
                    if actual_sha == claimed_sha and actual_rc == claimed_rc:
                        matched += 1
                    else:
                        offending.append({
                            "script": script_name,
                            "round": round_id,
                            "claimed_sha": claimed_sha,
                            "actual_sha": actual_sha,
                            "claimed_rc": claimed_rc,
                            "actual_rc": actual_rc,
                            "note": (
                                f"mismatch sha={actual_sha != claimed_sha} "
                                f"rc={actual_rc != claimed_rc}"
                            ),
                        })
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=str(repo_root), capture_output=True, text=True, check=False,
            )

    ok = (not offending) and (checked > 0)
    return {
        "ok": ok,
        "checked": checked,
        "matched": matched,
        "offending": offending,
        "main_head_sha_resolved": resolved_sha,
        "error": None,
    }


# ---------------------------------------------------------------------------
# infra-P286-followup-round1-reviewer-baseline-head-mismatch:
# Reviewer baseline_head_echo 字段强制校验 helper.
#
# P286 round-1 Reviewer 报告中声称 baseline c88a248 上 verify_infra_060 FAIL 1/14,
# 但 round-2 Closeout 实测 baseline 上 060 ALL PASS — round-1 Reviewer 把 baseline
# tail 取在 round-1 feat HEAD (b69310d) 上而非真 baseline sha. 本 helper 校验
# evidence.closeout_verify.reviewer.baseline_head_echo 与 pre_existing_baseline_sha
# 前 7 char 匹配 (大小写不敏感), 防止 Reviewer 在错误 HEAD 上跑 baseline.
#
# Legacy tolerance: 缺字段时返回 ok=True legacy=True (不阻 pre-P286 老 feature
# evidence 的 PASS), checked=False; 字段存在时严格校验前 7 char.
# ---------------------------------------------------------------------------
def assert_reviewer_baseline_head_echo(evidence_dict: dict) -> dict:
    """校验 evidence.closeout_verify.reviewer.baseline_head_echo 与
    pre_existing_baseline_sha 前 7 char 匹配 (大小写不敏感).

    P286 baseline-HEAD mismatch 防御: Reviewer 在跑 baseline verify 前必须先
    git checkout 到 pre_existing_baseline_sha 并 echo HEAD; helper 锁住
    echo 值与 baseline sha 前 7 char 一致, 防止 Reviewer 误把 feat HEAD 上的
    结果当 baseline 结果.

    Schema:
      {
        "ok": bool,
        "checked": bool,          # False 当 legacy 字段缺失
        "legacy": bool,           # True 当字段缺失 (老 evidence 兼容)
        "echo_value": str | None,
        "expected_prefix": str | None,
        "reason": str | None,
        "error": str | None,
      }

    Legacy 行为 (字段缺失):
      返回 ok=True, checked=False, legacy=True, reason="no baseline_head_echo
      field (legacy)". 不阻 pre-P286 老 evidence PASS, 不强制 retro fix.

    严格行为 (字段存在):
      - 取 evidence.closeout_verify.pre_existing_baseline_sha 前 7 char
        (大小写不敏感) 与 baseline_head_echo 比对
      - 比对前两个值都 .lower() 处理
      - 匹配 → ok=True checked=True legacy=False
      - 不匹配 → ok=False checked=True legacy=False reason 指明问题
    """
    out: dict = {
        "ok": False,
        "checked": False,
        "legacy": False,
        "echo_value": None,
        "expected_prefix": None,
        "reason": None,
        "error": None,
    }
    if not isinstance(evidence_dict, dict):
        out["error"] = (
            f"evidence_dict not a dict (got type={type(evidence_dict).__name__})"
        )
        return out
    cv = evidence_dict.get("closeout_verify")
    if not isinstance(cv, dict):
        out["ok"] = True
        out["legacy"] = True
        out["reason"] = "no closeout_verify dict (legacy)"
        return out
    reviewer = cv.get("reviewer") if isinstance(cv.get("reviewer"), dict) else None
    if not isinstance(reviewer, dict):
        out["ok"] = True
        out["legacy"] = True
        out["reason"] = "no closeout_verify.reviewer dict (legacy)"
        return out
    if "baseline_head_echo" not in reviewer:
        out["ok"] = True
        out["legacy"] = True
        out["reason"] = "no baseline_head_echo field (legacy)"
        return out
    echo = reviewer.get("baseline_head_echo")
    if not isinstance(echo, str) or not echo.strip():
        out["error"] = (
            f"baseline_head_echo present but not a non-empty string "
            f"(got type={type(echo).__name__} value={echo!r})"
        )
        return out
    baseline_sha = cv.get("pre_existing_baseline_sha")
    if not isinstance(baseline_sha, str) or not baseline_sha.strip():
        out["error"] = (
            f"pre_existing_baseline_sha missing or empty while "
            f"baseline_head_echo={echo!r}"
        )
        out["echo_value"] = echo
        return out
    echo_norm = echo.strip().lower()
    expected_prefix = baseline_sha.strip().lower()[:7]
    out["echo_value"] = echo
    out["expected_prefix"] = expected_prefix
    out["checked"] = True
    echo_prefix = echo_norm[:7]
    if echo_prefix == expected_prefix:
        out["ok"] = True
        out["reason"] = (
            f"baseline_head_echo={echo_prefix!r} matches "
            f"pre_existing_baseline_sha[:7]={expected_prefix!r}"
        )
    else:
        out["ok"] = False
        out["reason"] = (
            f"baseline_head_echo[:7]={echo_prefix!r} != "
            f"pre_existing_baseline_sha[:7]={expected_prefix!r}"
        )
    return out


# ---------------------------------------------------------------------------
# infra-P286-followup3-promote-baseline-head-echo-to-P278-hard-required
# (phase-42 #1.42): 跨 feature_list 扫描型 helper, 把 baseline_head_echo 从
# "dogfood + 单 evidence legacy 容差" promote 为 P278 hard-required 信号。
#
# 策略 (Default-OFF 渐进 promote, 与 062 V4_byte_match 风格一致):
#   - 老 feature (缺 reviewer.baseline_head_echo 字段) → soft_skipped
#     (不阻 pre-P286 历史 evidence PASS, 不强 retro fix)
#   - 含 baseline_head_echo 字段的 feature → hard enforce 前 7+ hex 等值匹配
#     closeout_verify.baseline_head_sha (大小写不敏感); 不匹配 → violation
#   - closeout_verify.baseline_head_sha 缺失 → soft_skipped (老 schema)
#   - 任一 violation → ok=False, V4_baseline_head_echo_required FAIL
#
# 此 helper 由 verify_infra_062 v4_behavior 段末尾真调, 把跨 feature 一致性
# 校验纳入 P278 closeout-verify-trustworthy 主入口; 同时被 verify_infra_082
# (新增) 锁住行为 + ast 静态调用链。
# ---------------------------------------------------------------------------
def assert_baseline_head_echo_present_and_matches(
    feature_list_path,
    current_git_head: str | None = None,
) -> dict:
    """跨 feature_list 扫描型 P278 hard-required check: baseline_head_echo
    字段一致性 (新 feature hard enforce, 老 feature soft skip).

    扫描 feature_list.json 所有 status=='passing' 且 evidence.closeout_verify
    存在的 feature, 对其 reviewer.baseline_head_echo (若存在) 与
    closeout_verify.baseline_head_sha 做前 7+ hex 等值匹配 (大小写不敏感)。

    参数:
        feature_list_path: feature_list.json 的路径 (str | Path).
        current_git_head: 当前 git HEAD sha (str | None); 仅记录到返回
            字典, 不参与 enforce 判定 (留作未来 cutoff 扩展点).

    返回 dict::

        {
          "ok": bool,                  # True 当无 violation
          "violations": [              # 每条违规一项
              {
                  "feature_id": str,
                  "reason": str,
                  "echo_prefix": str,
                  "expected_prefix": str,
              },
              ...
          ],
          "soft_skipped": [str, ...],  # 缺 echo 字段或缺 baseline_sha 的 feature_id
          "enforced_count": int,       # 真正参与 enforce 的 feature 数 (含字段)
          "scanned_count": int,        # 扫到的 status=passing 含 closeout_verify
          "current_git_head": str | None,
          "error": str | None,
        }

    Default-OFF 行为:
      - 老 feature (缺 baseline_head_echo 字段) → soft_skipped 列表, 不算 violation
      - 缺 baseline_head_sha 但有 echo → soft_skipped (老 schema 容差)
      - 含 baseline_head_echo 且 baseline_head_sha 都有 → hard enforce 等值

    错误:
      - feature_list 不存在 / parse 失败 → ok=False, error 字段载明
    """
    from pathlib import Path as _P
    import json as _json

    out: dict = {
        "ok": False,
        "violations": [],
        "soft_skipped": [],
        "enforced_count": 0,
        "scanned_count": 0,
        "current_git_head": current_git_head,
        "error": None,
    }
    p = _P(feature_list_path)
    if not p.is_file():
        out["error"] = f"feature_list not found at {p}"
        return out
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"feature_list parse error: {e!r}"
        return out
    features = data.get("features")
    if not isinstance(features, list):
        out["error"] = "feature_list.features missing or not a list"
        return out

    for f in features:
        if not isinstance(f, dict):
            continue
        if f.get("status") != "passing":
            continue
        ev = f.get("evidence")
        if not isinstance(ev, dict):
            continue
        cv = ev.get("closeout_verify")
        if not isinstance(cv, dict):
            continue
        fid = f.get("id") or "<no-id>"
        out["scanned_count"] += 1
        # 取 reviewer.baseline_head_echo (兼容 reviewer 为 dict 或 list 形态)
        rv = cv.get("reviewer")
        echo = None
        if isinstance(rv, dict):
            echo = rv.get("baseline_head_echo")
        elif isinstance(rv, list):
            for it in rv:
                if isinstance(it, dict) and it.get("baseline_head_echo"):
                    echo = it.get("baseline_head_echo")
                    break
        if not isinstance(echo, str) or not echo.strip():
            # 老 feature 缺字段 → soft_skipped
            out["soft_skipped"].append(fid)
            continue
        baseline_sha = cv.get("baseline_head_sha")
        if not isinstance(baseline_sha, str) or not baseline_sha.strip():
            # 含 echo 但缺 baseline_head_sha → soft_skipped (老 schema)
            out["soft_skipped"].append(fid)
            continue
        # 二者都有 → hard enforce 前 7 hex 等值
        out["enforced_count"] += 1
        echo_prefix = echo.strip().lower()[:7]
        expected_prefix = baseline_sha.strip().lower()[:7]
        if echo_prefix != expected_prefix:
            out["violations"].append({
                "feature_id": fid,
                "reason": (
                    f"reviewer.baseline_head_echo[:7]={echo_prefix!r} != "
                    f"closeout_verify.baseline_head_sha[:7]={expected_prefix!r}"
                ),
                "echo_prefix": echo_prefix,
                "expected_prefix": expected_prefix,
            })
    out["ok"] = len(out["violations"]) == 0
    return out


# ---------------------------------------------------------------------------
# infra-P291-followup2-helper-return-value-must-participate-in-emit (phase-41 #2.41)
# ast 锁: v5_reviewer_gate 函数体内 _emit(...) 的第二参数必须**使用** 来自
# assert_reviewer_lgtm 返回值的变量 (典型 `ok`/`ok is True`), 而不是字面
# True/False/None 硬绿. 此前 phase-40 #5.40 P0-1 (verify_infra_079) 即写成
# `_emit("V5...", True, "...")` 直接硬绿绕过 reviewer evidence 校验.
# ---------------------------------------------------------------------------
def assert_v5_gate_emit_uses_helper_return(verify_script_path) -> dict:
    """ast 扫描 verify_script_path 中 ``v5_reviewer_gate`` 函数体内每个
    ``_emit(...)`` 调用, 断言第二位置参数是 Name/Compare/BoolOp/Attribute 等
    引用变量的表达式 (变量名必须出现在函数体内 assert_reviewer_lgtm 的解包
    targets 中, 典型为 ``ok``), 而不是 ``Constant(True|False|None)`` 字面.

    返回 dict::
        {
          "ok": bool,
          "checked": bool,           # False 当 v5_reviewer_gate 函数不存在
          "v5_gate_found": bool,
          "calls_assert_helper": bool,
          "helper_return_names": [str, ...],   # 解包 assert_reviewer_lgtm 时的变量名
          "emit_calls": [
              {
                "lineno": int,
                "arg1_source": str,
                "arg1_node": str,       # ast 类型名
                "uses_helper_var": bool,
                "is_literal": bool,     # 字面 True/False/None
                "violation": bool,
              }, ...
          ],
          "violations": [ {lineno, arg1_source, reason}, ... ],
          "error": str | None,
        }

    判定 (round-2 P1 修: 与实现对齐):
      - 若文件无 v5_reviewer_gate 函数: ok=True, checked=False (skip).
      - 若 v5_reviewer_gate 不调用 assert_reviewer_lgtm: 仍按"字面 True
        即违规"判 (即便没调 helper, 也不允许硬绿写法).
      - 任一 _emit 第二位置参数是 Constant(value=True): violation (硬绿反模式).
      - 字面 False / None: 视为 guard 早返回 (典型: feature_list 缺失时
        ``_emit("V5_...", False, ...)`` 后 return), **合法, 不视为 violation**.
      - 其他表达式 (Name/Compare/BoolOp/Attribute/Call/...) 视为合规.
    """
    import ast as _ast
    from pathlib import Path as _Path

    out: dict = {
        "ok": False,
        "checked": False,
        "v5_gate_found": False,
        "calls_assert_helper": False,
        "helper_return_names": [],
        "emit_calls": [],
        "violations": [],
        "error": None,
    }
    path = _Path(verify_script_path)
    if not path.is_file():
        out["error"] = f"verify_script_path not a file: {path}"
        return out
    try:
        src = path.read_text(encoding="utf-8")
        tree = _ast.parse(src)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"parse error: {e!r}"
        return out

    v5_fn = None
    for node in tree.body:
        if isinstance(node, _ast.FunctionDef) and node.name == "v5_reviewer_gate":
            v5_fn = node
            break
    if v5_fn is None:
        out["ok"] = True
        out["checked"] = False
        return out
    out["v5_gate_found"] = True
    out["checked"] = True

    # collect: assert_reviewer_lgtm 调用的解包 target 变量名 (Tuple Assign 左侧 Name)
    helper_names: list = []
    for sub in _ast.walk(v5_fn):
        if isinstance(sub, _ast.Assign) and isinstance(sub.value, _ast.Call):
            callee = sub.value.func
            cname = None
            if isinstance(callee, _ast.Name):
                cname = callee.id
            elif isinstance(callee, _ast.Attribute):
                cname = callee.attr
            if cname == "assert_reviewer_lgtm":
                out["calls_assert_helper"] = True
                for t in sub.targets:
                    if isinstance(t, _ast.Tuple):
                        for elt in t.elts:
                            if isinstance(elt, _ast.Name):
                                helper_names.append(elt.id)
                    elif isinstance(t, _ast.Name):
                        helper_names.append(t.id)
    out["helper_return_names"] = helper_names

    # walk every _emit(...) call in v5 body
    emit_records: list = []
    violations: list = []
    for sub in _ast.walk(v5_fn):
        if not (isinstance(sub, _ast.Call) and isinstance(sub.func, _ast.Name) and sub.func.id == "_emit"):
            continue
        if len(sub.args) < 2:
            continue
        a1 = sub.args[1]
        arg1_source = _ast.unparse(a1)
        node_type = type(a1).__name__
        # collect every Name id in the expression
        names_in_expr = [n.id for n in _ast.walk(a1) if isinstance(n, _ast.Name)]
        uses_helper_var = any(n in helper_names for n in names_in_expr) if helper_names else False
        # 只把"字面 True 硬绿"视为违规 (phase-40 #5.40 P0-1 反模式).
        # 字面 False/None 通常是 guard 提前返回 (feature_list 缺失等), 合法.
        is_literal = isinstance(a1, _ast.Constant) and a1.value in (True, False, None)
        is_hardcoded_true = isinstance(a1, _ast.Constant) and a1.value is True
        violation = is_hardcoded_true
        rec = {
            "lineno": sub.lineno,
            "arg1_source": arg1_source,
            "arg1_node": node_type,
            "uses_helper_var": uses_helper_var,
            "is_literal": is_literal,
            "violation": violation,
        }
        emit_records.append(rec)
        if violation:
            violations.append({
                "lineno": sub.lineno,
                "arg1_source": arg1_source,
                "reason": (
                    f"_emit 第二参数是字面 True (硬绿), "
                    "必须使用 assert_reviewer_lgtm 返回的 ok 变量 (或含其的表达式)"
                ),
            })
    out["emit_calls"] = emit_records
    out["violations"] = violations
    out["ok"] = len(violations) == 0
    return out


# ---------------------------------------------------------------------------
# infra-P278-followup-reviewer-summary-nonempty-hard-check (phase-42 #4.42)
# 跨 feature_list 扫描型 P278 hard-required check: reviewer.summary
# 字段非空 (新 feature hard enforce, 老 feature soft skip).
# ---------------------------------------------------------------------------
def assert_reviewer_summary_nonempty(
    feature_list_path,
    min_chars: int = 20,
) -> dict:
    """扫 feature_list.json 所有 status=='passing' 且 evidence.closeout_verify
    存在的 feature, 对其 reviewer.summary (若存在) strip 后长度必须 >= min_chars.

    参数:
        feature_list_path: feature_list.json 的路径 (str | Path).
        min_chars: 最小字符数门槛 (strip 之后), 默认 20.

    返回 dict::

        {
          "ok": bool,                  # True 当无 violation
          "violations": [              # 每条违规一项
              {
                  "feature_id": str,
                  "reason": str,
                  "summary_len": int,
                  "min_chars": int,
              },
              ...
          ],
          "soft_skipped": [str, ...],  # 缺 reviewer.summary 字段的 feature_id
          "enforced_count": int,       # 真正参与 enforce 的 feature 数 (含字段)
          "scanned_count": int,        # 扫到的 status=passing 含 closeout_verify
          "error": str | None,
        }

    Default-OFF 行为:
      - 老 feature (缺 reviewer.summary 字段) → soft_skipped 列表, 不算 violation
      - 含 reviewer.summary 字段 → hard enforce: strip 后长度 >= min_chars

    错误:
      - feature_list 不存在 / parse 失败 → ok=False, error 字段载明
    """
    from pathlib import Path as _P
    import json as _json

    out: dict = {
        "ok": False,
        "violations": [],
        "soft_skipped": [],
        "enforced_count": 0,
        "scanned_count": 0,
        "min_chars": int(min_chars),
        "error": None,
    }
    p = _P(feature_list_path)
    if not p.is_file():
        out["error"] = f"feature_list not found at {p}"
        return out
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"feature_list parse error: {e!r}"
        return out
    features = data.get("features")
    if not isinstance(features, list):
        out["error"] = "feature_list.features missing or not a list"
        return out

    for f in features:
        if not isinstance(f, dict):
            continue
        if f.get("status") != "passing":
            continue
        ev = f.get("evidence")
        if not isinstance(ev, dict):
            continue
        cv = ev.get("closeout_verify")
        if not isinstance(cv, dict):
            continue
        fid = f.get("id") or "<no-id>"
        out["scanned_count"] += 1
        # 取 reviewer.summary (兼容 reviewer 为 dict 或 list 形态)
        rv = cv.get("reviewer")
        summary = None
        if isinstance(rv, dict):
            summary = rv.get("summary")
        elif isinstance(rv, list):
            for it in rv:
                if isinstance(it, dict) and it.get("summary"):
                    summary = it.get("summary")
                    break
        if not isinstance(summary, str):
            # 老 feature 缺字段 → soft_skipped
            out["soft_skipped"].append(fid)
            continue
        stripped = summary.strip()
        out["enforced_count"] += 1
        if len(stripped) < int(min_chars):
            out["violations"].append({
                "feature_id": fid,
                "reason": (
                    f"reviewer.summary strip 后长度 {len(stripped)} < {min_chars}"
                ),
                "summary_len": len(stripped),
                "min_chars": int(min_chars),
            })
    out["ok"] = len(out["violations"]) == 0
    return out


# ---------------------------------------------------------------------------
# infra-P278-followup-verify-lib-helper-naming-convention-lock (phase-42 #5.42)
# 锁定 _verify_lib 公开 helper 命名规约: 新增公开 helper 必须以 assert_ 或
# enforce_ 开头. legacy 已存在 helper 通过 legacy_allowlist 显式豁免;
# rename legacy 入 backlog (infra-P278-followup-verify-lib-legacy-rename).
# Default-OFF 硬规则: 若 _verify_lib 没有 __all__ → soft_skip; 含 __all__ → hard enforce.
# ---------------------------------------------------------------------------

# Legacy public helper allowlist (P278-followup 之前已 export 的名字).
# 新增 helper 必须以 assert_ / enforce_ 开头, 不可加入此 allowlist.
_VERIFY_LIB_LEGACY_PUBLIC_HELPER_ALLOWLIST: frozenset[str] = frozenset({
    "verify_reverse_sha_lock_consistency",
    "verify_expected_pattern_consistency",
    "verify_palette_fills_distinct",
    "verify_unknown_node_count_bound",
    "verify_expected_prefix_typo_guard",
    "verify_closeout_evidence_trustworthy",
    "verify_baseline_fail_claims",
    "verify_evidence_tail_stdout_sha",
    "verify_summary_exit",
})


def assert_verify_lib_public_helper_naming(
    allowed_prefixes: tuple[str, ...] = ("assert_", "enforce_", "parse_", "read_", "func_", "scan_", "live_"),
    legacy_allowlist: frozenset[str] | None = None,
) -> dict:
    """反射检查 scripts/_verify_lib 的 __all__ 中公开 helper 命名是否合规.

    规则:
      - 若模块没有 __all__ → soft_skip (Default-OFF: legacy 缺字段豁免)
      - 含 __all__ → 遍历每一项, 取 module-level callable, 名字必须以
        allowed_prefixes 任一开头, 或在 legacy_allowlist 中显式豁免.
      - 任何不在 allowlist 又不符合前缀的 public callable → violation.

    参数:
        allowed_prefixes: 允许的前缀元组, 默认 ("assert_", "enforce_", "parse_", "read_", "func_", "scan_", "live_").
        legacy_allowlist: 显式豁免的 legacy 名字集合; None 表示使用
            模块内置 _VERIFY_LIB_LEGACY_PUBLIC_HELPER_ALLOWLIST.

    返回 dict::

        {
          "ok": bool,                  # True 当无 violation
          "violations": [              # 每条违规一项
              {"name": str, "reason": str},
              ...
          ],
          "soft_skipped": bool,        # __all__ 缺失时 True
          "enforced_count": int,       # 命中前缀的 helper 数
          "scanned_count": int,        # __all__ 总条目数
          "legacy_allowlisted_count": int,  # 命中 legacy_allowlist 的 helper 数
          "error": str | None,
        }
    """
    out: dict = {
        "ok": True,
        "violations": [],
        "soft_skipped": False,
        "enforced_count": 0,
        "scanned_count": 0,
        "legacy_allowlisted_count": 0,
        "error": None,
    }
    if legacy_allowlist is None:
        legacy_allowlist = _VERIFY_LIB_LEGACY_PUBLIC_HELPER_ALLOWLIST
    try:
        import importlib
        import sys as _sys
        # 确保 import 到本仓库的 _verify_lib (避免外部同名 shadow)
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        mod = importlib.import_module("_verify_lib")
        all_names = getattr(mod, "__all__", None)
        if all_names is None:
            out["soft_skipped"] = True
            return out
        out["scanned_count"] = len(all_names)
        for name in all_names:
            obj = getattr(mod, name, None)
            if obj is None or not callable(obj):
                # 非 callable (如常量) — 跳过, 不算 violation
                continue
            if name in legacy_allowlist:
                out["legacy_allowlisted_count"] += 1
                continue
            if any(name.startswith(p) for p in allowed_prefixes):
                out["enforced_count"] += 1
                continue
            out["violations"].append({
                "name": name,
                "reason": (
                    f"public helper {name!r} 命名不符合规约 "
                    f"(必须以 {'/'.join(allowed_prefixes)} 开头, "
                    f"或加入 legacy_allowlist)"
                ),
            })
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["ok"] = False
        return out
    out["ok"] = len(out["violations"]) == 0
    return out


# ---------------------------------------------------------------------------
# infra-P278-followup-closeout-verify-runs-min-count-hard-check (phase-43 #4.43)
# Closeout-verify-trustworthy 已要求 verify_runs 每项含 tail_stdout+status,
# 但未约束最少条数. 加 Default-OFF hard check: 缺字段 soft_skip;
# 含字段且 len(verify_runs) < min_count → hard FAIL.
# ---------------------------------------------------------------------------
def assert_closeout_verify_runs_min_count(
    feature_list_path,
    min_count: int = 3,
) -> dict:
    """扫 feature_list.json 所有 status=='passing' 且 evidence.closeout_verify
    存在的 feature, 对其 closeout_verify.verify_runs (若存在) 要求
    len(verify_runs) >= min_count.

    参数:
        feature_list_path: feature_list.json 的路径 (str | Path).
        min_count: 最少 verify_runs 条数门槛, 默认 3.

    返回 dict::

        {
          "ok": bool,                  # True 当无 violation
          "violations": [              # 每条违规一项
              {
                  "feature_id": str,
                  "reason": str,
                  "runs_count": int,
                  "min_count": int,
              },
              ...
          ],
          "soft_skipped": [str, ...],  # 缺 verify_runs 字段的 feature_id
          "enforced_count": int,       # 真正参与 enforce 的 feature 数 (含字段)
          "scanned_count": int,        # 扫到的 status=passing 含 closeout_verify
          "min_count": int,
          "error": str | None,
        }

    Default-OFF 行为:
      - 老 feature (缺 verify_runs 字段 / 非 list) → soft_skipped 列表, 不算 violation
      - 含 verify_runs 字段且为 list → hard enforce: len >= min_count

    错误:
      - feature_list 不存在 / parse 失败 → ok=False, error 字段载明
    """
    from pathlib import Path as _P
    import json as _json

    out: dict = {
        "ok": False,
        "violations": [],
        "soft_skipped": [],
        "enforced_count": 0,
        "scanned_count": 0,
        "min_count": int(min_count),
        "error": None,
    }
    p = _P(feature_list_path)
    if not p.is_file():
        out["error"] = f"feature_list not found at {p}"
        return out
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"feature_list parse error: {e!r}"
        return out
    features = data.get("features")
    if not isinstance(features, list):
        out["error"] = "feature_list.features missing or not a list"
        return out

    for f in features:
        if not isinstance(f, dict):
            continue
        if f.get("status") != "passing":
            continue
        ev = f.get("evidence")
        if not isinstance(ev, dict):
            continue
        cv = ev.get("closeout_verify")
        if not isinstance(cv, dict):
            continue
        fid = f.get("id") or "<no-id>"
        out["scanned_count"] += 1
        vr = cv.get("verify_runs")
        if not isinstance(vr, list):
            # 老 feature 缺字段 (或非 list) → soft_skipped
            out["soft_skipped"].append(fid)
            continue
        out["enforced_count"] += 1
        if len(vr) < int(min_count):
            out["violations"].append({
                "feature_id": fid,
                "reason": (
                    f"closeout_verify.verify_runs len={len(vr)} "
                    f"< min_count={min_count}"
                ),
                "runs_count": len(vr),
                "min_count": int(min_count),
            })
    out["ok"] = len(out["violations"]) == 0
    return out


# ---------------------------------------------------------------------------
# infra-P278-followup-closeout-smoke-tail-nonempty-hard-check (phase-43 #5.43)
# Closeout-verify-trustworthy 已要求 smoke_tail_stdout 为 string, 但未约束内容.
# 常见占位 'smoke OK' 或空串无法 audit 真跑 ./init.sh. 加 Default-OFF hard check:
# 缺字段 soft_skip; 含字段但 strip 后 < min_chars 或不含任何 must_contain
# 关键词 (case-insensitive) → hard FAIL.
# ---------------------------------------------------------------------------
def assert_closeout_smoke_tail_nonempty(
    feature_list_path,
    min_chars: int = 20,
    must_contain: tuple = ("Smoke", "smoke"),
) -> dict:
    """扫 feature_list.json 所有 status=='passing' 且 evidence.closeout_verify
    存在的 feature, 对其 closeout_verify.smoke_tail_stdout (若存在) 要求:
      - strip() 后长度 >= min_chars
      - 字段值 (case-insensitive) 至少含 must_contain 中任一关键词

    参数:
        feature_list_path: feature_list.json 的路径 (str | Path).
        min_chars: 最少非空白字符数门槛, 默认 20.
        must_contain: 关键词元组 (case-insensitive 任一匹配即可),
            默认 ("Smoke", "smoke").

    返回 dict::

        {
          "ok": bool,
          "violations": [
              {
                  "feature_id": str,
                  "reason": str,
                  "stripped_len": int,
                  "min_chars": int,
                  "must_contain": list,
              },
              ...
          ],
          "soft_skipped": [str, ...],   # 缺 smoke_tail_stdout 字段的 feature_id
          "enforced_count": int,        # 真正参与 enforce 的 feature 数 (含字段)
          "scanned_count": int,         # status=passing 含 closeout_verify
          "min_chars": int,
          "must_contain": list,
          "error": str | None,
        }

    Default-OFF 行为:
      - 老 feature (缺 smoke_tail_stdout / 非 str) → soft_skipped, 不算 violation
      - 含字段 (str) → hard enforce: 长度门槛 + 关键词门槛
    """
    from pathlib import Path as _P
    import json as _json

    mc = list(must_contain) if must_contain else []
    out: dict = {
        "ok": False,
        "violations": [],
        "soft_skipped": [],
        "enforced_count": 0,
        "scanned_count": 0,
        "min_chars": int(min_chars),
        "must_contain": list(mc),
        "error": None,
    }
    p = _P(feature_list_path)
    if not p.is_file():
        out["error"] = f"feature_list not found at {p}"
        return out
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"feature_list parse error: {e!r}"
        return out
    features = data.get("features")
    if not isinstance(features, list):
        out["error"] = "feature_list.features missing or not a list"
        return out

    lc_keywords = [k.lower() for k in mc if isinstance(k, str) and k]

    for f in features:
        if not isinstance(f, dict):
            continue
        if f.get("status") != "passing":
            continue
        ev = f.get("evidence")
        if not isinstance(ev, dict):
            continue
        cv = ev.get("closeout_verify")
        if not isinstance(cv, dict):
            continue
        fid = f.get("id") or "<no-id>"
        out["scanned_count"] += 1
        smoke = cv.get("smoke_tail_stdout")
        if not isinstance(smoke, str):
            # 老 feature 缺字段 (或非 str) → soft_skipped
            out["soft_skipped"].append(fid)
            continue
        out["enforced_count"] += 1
        stripped = smoke.strip()
        stripped_len = len(stripped)
        if stripped_len < int(min_chars):
            out["violations"].append({
                "feature_id": fid,
                "reason": (
                    f"closeout_verify.smoke_tail_stdout stripped_len="
                    f"{stripped_len} < min_chars={min_chars}"
                ),
                "stripped_len": stripped_len,
                "min_chars": int(min_chars),
                "must_contain": list(mc),
            })
            continue
        if lc_keywords:
            lc_smoke = smoke.lower()
            if not any(k in lc_smoke for k in lc_keywords):
                out["violations"].append({
                    "feature_id": fid,
                    "reason": (
                        f"closeout_verify.smoke_tail_stdout missing any of "
                        f"must_contain={mc} (case-insensitive)"
                    ),
                    "stripped_len": stripped_len,
                    "min_chars": int(min_chars),
                    "must_contain": list(mc),
                })
    out["ok"] = len(out["violations"]) == 0
    return out


# ---------------------------------------------------------------------------
# infra-P278-followup-closeout-verify-runs-status-shape-hard-check
# (phase-44 #3.44): closeout_verify.verify_runs[] element shape hard check.
# 已有 V4_closeout_verify_runs_min_count 仅约束条数 >=3; 但对 element 字段
# (name/status/tail_stdout) 内容未约束, 仍允许 status='' 或 tail_stdout='ok' 之类
# 不可 audit 的占位. 加 Default-OFF + soft-PASS for legacy hard check:
#   - enforce-set: feature 含 closeout_verify.reviewer.reviewer_kind ==
#     'sub_agent_fresh_context' (即 P278 之后真正受 reviewer-trustworthy 约束的);
#   - 每个 verify_runs[i] 必须含 name(非空 str)/status(非空 str 且 ∈ allowed_statuses)/
#     tail_stdout(strip()后 >= min_tail_chars);
#   - legacy (reviewer_kind 缺失 / 非 sub_agent_fresh_context) → soft_skipped.
# ---------------------------------------------------------------------------
def assert_closeout_verify_runs_shape(
    feature_list_path,
    min_tail_chars: int = 20,
    allowed_statuses: tuple = ("PASS", "FAIL", "SKIP"),
    grace_period_feature_ids: tuple = (),
) -> dict:
    """扫 feature_list.json, 对 enforce-set 内 feature 的
    evidence.closeout_verify.verify_runs[] 每个 element 做 shape hard check.

    enforce-set 判定:
      - feature.status == 'passing'
      - evidence.closeout_verify 为 dict
      - closeout_verify.reviewer.reviewer_kind == 'sub_agent_fresh_context'

    Element shape 要求:
      - 'name': 非空 str
      - 'status': 非空 str 且 (若 allowed_statuses 非空) 必须 ∈ allowed_statuses
      - 'tail_stdout': str, strip() 后长度 >= min_tail_chars

    参数:
        feature_list_path: feature_list.json 路径 (str | Path).
        min_tail_chars: tail_stdout strip 后最少字符数 (默认 20).
        allowed_statuses: status 字段允许的取值; 传 () 则只校验非空, 不校验取值.
        grace_period_feature_ids: 软放过列表 — 这些历史 feature 的 violations
            不计入 violations, 而计入 grace_skipped. 新 feature 不在此列表中
            即按 hard 强制. (P294/V6-062: emit 已 promote 至 bool, 用此列表
            把 17 个历史 violation feature 一次性 grandfather 进来)

    返回 dict::

        {
          "ok": bool,
          "violations": [
              {
                  "feature_id": str,
                  "run_index": int,
                  "reason": str,
                  "field": str,            # 'name' | 'status' | 'tail_stdout'
                  "value_repr": str,       # repr(...) 截断到 80 字符
              },
              ...
          ],
          "soft_skipped": [str, ...],
          "grace_skipped": [str, ...],
          "enforced_count": int,
          "scanned_count": int,
          "grace_period_count": int,
          "min_tail_chars": int,
          "allowed_statuses": list,
          "error": str | None,
        }

    Default-OFF + soft-PASS for legacy:
      - 老 feature (无 reviewer_kind 或非 sub_agent_fresh_context / 缺 verify_runs
        list) → soft_skipped, 不算 violation
      - 在 enforce-set 内但缺 verify_runs / 非 list → soft_skipped (与 min_count
        helper 一致 — 那个 check 已负责 list 形态)
    """
    from pathlib import Path as _P
    import json as _json

    allowed_list = list(allowed_statuses) if allowed_statuses else []
    grace_set = set(grace_period_feature_ids or ())
    out: dict = {
        "ok": False,
        "violations": [],
        "soft_skipped": [],
        "grace_skipped": [],
        "enforced_count": 0,
        "scanned_count": 0,
        "grace_period_count": len(grace_set),
        "min_tail_chars": int(min_tail_chars),
        "allowed_statuses": list(allowed_list),
        "error": None,
    }
    p = _P(feature_list_path)
    if not p.is_file():
        out["error"] = f"feature_list not found at {p}"
        return out
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"feature_list parse error: {e!r}"
        return out
    features = data.get("features")
    if not isinstance(features, list):
        out["error"] = "feature_list.features missing or not a list"
        return out

    def _repr80(v) -> str:
        s = repr(v)
        return s if len(s) <= 80 else s[:77] + "..."

    for f in features:
        if not isinstance(f, dict):
            continue
        if f.get("status") != "passing":
            continue
        ev = f.get("evidence")
        if not isinstance(ev, dict):
            continue
        cv = ev.get("closeout_verify")
        if not isinstance(cv, dict):
            continue
        fid = f.get("id") or "<no-id>"
        out["scanned_count"] += 1
        reviewer = cv.get("reviewer") if isinstance(cv.get("reviewer"), dict) else {}
        if reviewer.get("reviewer_kind") != "sub_agent_fresh_context":
            out["soft_skipped"].append(fid)
            continue
        vr = cv.get("verify_runs")
        if not isinstance(vr, list):
            out["soft_skipped"].append(fid)
            continue
        out["enforced_count"] += 1
        feature_violations: list = []
        for idx, run in enumerate(vr):
            if not isinstance(run, dict):
                feature_violations.append({
                    "feature_id": fid,
                    "run_index": idx,
                    "reason": f"verify_runs[{idx}] not a dict (type={type(run).__name__})",
                    "field": "<element>",
                    "value_repr": _repr80(run),
                })
                continue
            # name
            name = run.get("name")
            if not (isinstance(name, str) and name.strip()):
                feature_violations.append({
                    "feature_id": fid,
                    "run_index": idx,
                    "reason": f"verify_runs[{idx}].name empty or not str",
                    "field": "name",
                    "value_repr": _repr80(name),
                })
            # status
            status = run.get("status")
            if not (isinstance(status, str) and status.strip()):
                feature_violations.append({
                    "feature_id": fid,
                    "run_index": idx,
                    "reason": f"verify_runs[{idx}].status empty or not str",
                    "field": "status",
                    "value_repr": _repr80(status),
                })
            elif allowed_list and status not in allowed_list:
                feature_violations.append({
                    "feature_id": fid,
                    "run_index": idx,
                    "reason": (
                        f"verify_runs[{idx}].status={status!r} not in "
                        f"allowed_statuses={allowed_list}"
                    ),
                    "field": "status",
                    "value_repr": _repr80(status),
                })
            # tail_stdout
            tail = run.get("tail_stdout")
            if not isinstance(tail, str):
                feature_violations.append({
                    "feature_id": fid,
                    "run_index": idx,
                    "reason": f"verify_runs[{idx}].tail_stdout missing or not str",
                    "field": "tail_stdout",
                    "value_repr": _repr80(tail),
                })
            else:
                stripped_len = len(tail.strip())
                if stripped_len < int(min_tail_chars):
                    feature_violations.append({
                        "feature_id": fid,
                        "run_index": idx,
                        "reason": (
                            f"verify_runs[{idx}].tail_stdout stripped_len="
                            f"{stripped_len} < min_tail_chars={min_tail_chars}"
                        ),
                        "field": "tail_stdout",
                        "value_repr": _repr80(tail),
                    })
        # grace_period: 若 feature 在 grace_set 且 有 violation, 不计入 violations
        if feature_violations and fid in grace_set:
            out["grace_skipped"].append(fid)
        else:
            out["violations"].extend(feature_violations)
    out["ok"] = len(out["violations"]) == 0
    return out


# ---------------------------------------------------------------------------
# infra-V6-backlog-062-v3-helper-func-sha-rebump-round2 (phase-44 #2.44):
# V3 helper drift detector — 扫描 _verify_lib.py 公共 helper (def 开头非下划线),
# 与传入的 v3_table_keys 比对, 任何 missing/extra 即漂移 (强制 round2-style 防漂)
# ---------------------------------------------------------------------------
def assert_verify_lib_helpers_in_v3_sha_table(
    verify_lib_path,
    v3_table_keys,
    allowlist=None,
) -> dict:
    """扫描 _verify_lib.py 公共 helper, 比对 v3_table_keys.

    设计意图:
      未来新增 helper 必须同步更新 062 的 V3 helper sha 表 (或 allowlist),
      否则 hard FAIL — 强制 round2-style 防漂移。

    Args:
        verify_lib_path: _verify_lib.py 路径 (str/Path).
        v3_table_keys: 062 V3 helper sha 表的 key 列表 (helper 函数名).
        allowlist: 显式豁免的 helper 名集合 (允许不进 V3 表).

    Returns:
        dict: ok, missing_in_v3_table, extra_in_v3_table, scanned_helpers,
              v3_table_keys, allowlist, error.
    """
    import re as _re
    from pathlib import Path as _Path
    out = {
        "ok": False,
        "missing_in_v3_table": [],
        "extra_in_v3_table": [],
        "scanned_helpers": [],
        "v3_table_keys": sorted(list(v3_table_keys or [])),
        "allowlist": sorted(list(allowlist or [])),
        "error": None,
    }
    try:
        p = _Path(verify_lib_path)
        src = p.read_text(encoding="utf-8")
    except Exception as e:
        out["error"] = f"read err: {e!r}"
        return out
    helpers = _re.findall(r"^def ([a-z][a-z0-9_]*)\(", src, _re.MULTILINE)
    out["scanned_helpers"] = sorted(set(helpers))
    al = set(allowlist or [])
    table_set = set(v3_table_keys or [])
    helper_set = set(out["scanned_helpers"])
    out["missing_in_v3_table"] = sorted(helper_set - table_set - al)
    out["extra_in_v3_table"] = sorted(table_set - helper_set)
    out["ok"] = (
        len(out["missing_in_v3_table"]) == 0
        and len(out["extra_in_v3_table"]) == 0
    )
    return out


# ---------------------------------------------------------------------------
# infra-P278-followup-closeout-reviewer-block-shape-hard-check (phase-44 #4.44):
# 扫所有 feature 的 evidence.closeout_verify.reviewer 五字段 + 形态合规.
# Default-OFF + soft-PASS for legacy: 仅对 reviewer_kind == 'sub_agent_fresh_context'
# enforce; 其他软放过 (soft_skipped).
# ---------------------------------------------------------------------------
def assert_closeout_reviewer_block_shape(
    feature_list_path,
    min_summary_chars: int = 20,
    allowed_verdicts: tuple = ("LGTM", "conditional", "REJECT"),
    required_findings_keys: tuple = ("P0", "P1", "P2"),
    grace_period_feature_ids: tuple = (),
) -> dict:
    """每个 closeout_verify.reviewer block 必须含五字段且形态合规.

    enforce-set 判定:
      - feature.status == 'passing'
      - evidence.closeout_verify 为 dict
      - closeout_verify.reviewer 为 dict
      - reviewer.reviewer_kind == 'sub_agent_fresh_context'

    五字段:
      - reviewer_kind: 非空 str
      - verdict: 非空 str 且 (若 allowed_verdicts 非空) 必须 ∈ allowed_verdicts
      - summary: str, strip 后长度 >= min_summary_chars
      - checks_run: 非空 list
      - findings: dict, 且含 required_findings_keys 中每个 key, 对应值为 list

    参数:
        feature_list_path: feature_list.json 路径 (str | Path).
        min_summary_chars: summary strip 后最少字符数 (默认 20).
        allowed_verdicts: verdict 字段允许的取值; 传 () 则只校验非空.
        required_findings_keys: findings dict 必须包含的 key (默认 P0/P1/P2).
        grace_period_feature_ids: 软放过列表 — 这些历史 feature 的 violations
            不计入 violations, 而计入 grace_skipped. 新 feature 不在此列表中
            即按 hard 强制. (V6-062-reviewer-block-shape-promote-bool:
            emit 已 promote 至 bool, 用此列表把 22 个历史 violation feature
            一次性 grandfather 进来)

    返回 dict::

        {
          "ok": bool,
          "violations": [
              {
                  "feature_id": str,
                  "reason": str,
                  "field": str,        # reviewer_kind|verdict|summary|checks_run|findings
                  "value_repr": str,   # repr(...) 截断到 80 字符
              },
              ...
          ],
          "soft_skipped": [str, ...],
          "grace_skipped": [str, ...],
          "enforced_count": int,
          "scanned_count": int,
          "grace_period_count": int,
          "min_summary_chars": int,
          "allowed_verdicts": list,
          "required_findings_keys": list,
          "error": str | None,
        }

    Default-OFF + soft-PASS for legacy:
      - 老 feature (无 reviewer / reviewer 非 dict / reviewer_kind !=
        'sub_agent_fresh_context') → soft_skipped, 不算 violation.
    """
    from pathlib import Path as _P
    import json as _json

    allowed_list = list(allowed_verdicts) if allowed_verdicts else []
    findings_keys = list(required_findings_keys) if required_findings_keys else []
    grace_set = set(grace_period_feature_ids or ())
    out: dict = {
        "ok": False,
        "violations": [],
        "soft_skipped": [],
        "grace_skipped": [],
        "enforced_count": 0,
        "scanned_count": 0,
        "grace_period_count": len(grace_set),
        "min_summary_chars": int(min_summary_chars),
        "allowed_verdicts": list(allowed_list),
        "required_findings_keys": list(findings_keys),
        "error": None,
    }
    p = _P(feature_list_path)
    if not p.is_file():
        out["error"] = f"feature_list not found at {p}"
        return out
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"feature_list parse error: {e!r}"
        return out
    features = data.get("features")
    if not isinstance(features, list):
        out["error"] = "feature_list.features missing or not a list"
        return out

    def _repr80(v) -> str:
        s = repr(v)
        return s if len(s) <= 80 else s[:77] + "..."

    for f in features:
        if not isinstance(f, dict):
            continue
        if f.get("status") != "passing":
            continue
        ev = f.get("evidence")
        if not isinstance(ev, dict):
            continue
        cv = ev.get("closeout_verify")
        if not isinstance(cv, dict):
            continue
        fid = f.get("id") or "<no-id>"
        out["scanned_count"] += 1
        reviewer = cv.get("reviewer")
        if not isinstance(reviewer, dict):
            out["soft_skipped"].append(fid)
            continue
        if reviewer.get("reviewer_kind") != "sub_agent_fresh_context":
            out["soft_skipped"].append(fid)
            continue
        out["enforced_count"] += 1
        feature_violations: list = []

        # reviewer_kind
        rk = reviewer.get("reviewer_kind")
        if not (isinstance(rk, str) and rk.strip()):
            feature_violations.append({
                "feature_id": fid,
                "reason": "reviewer.reviewer_kind empty or not str",
                "field": "reviewer_kind",
                "value_repr": _repr80(rk),
            })

        # verdict
        vd = reviewer.get("verdict")
        if not (isinstance(vd, str) and vd.strip()):
            feature_violations.append({
                "feature_id": fid,
                "reason": "reviewer.verdict empty or not str",
                "field": "verdict",
                "value_repr": _repr80(vd),
            })
        elif allowed_list and vd not in allowed_list:
            feature_violations.append({
                "feature_id": fid,
                "reason": (
                    f"reviewer.verdict={vd!r} not in "
                    f"allowed_verdicts={allowed_list}"
                ),
                "field": "verdict",
                "value_repr": _repr80(vd),
            })

        # summary
        summ = reviewer.get("summary")
        if not isinstance(summ, str):
            feature_violations.append({
                "feature_id": fid,
                "reason": "reviewer.summary missing or not str",
                "field": "summary",
                "value_repr": _repr80(summ),
            })
        else:
            stripped_len = len(summ.strip())
            if stripped_len < int(min_summary_chars):
                feature_violations.append({
                    "feature_id": fid,
                    "reason": (
                        f"reviewer.summary stripped_len={stripped_len} "
                        f"< min_summary_chars={min_summary_chars}"
                    ),
                    "field": "summary",
                    "value_repr": _repr80(summ),
                })

        # checks_run
        cr = reviewer.get("checks_run")
        if not (isinstance(cr, list) and len(cr) > 0):
            feature_violations.append({
                "feature_id": fid,
                "reason": "reviewer.checks_run missing/empty or not list",
                "field": "checks_run",
                "value_repr": _repr80(cr),
            })

        # findings
        fd = reviewer.get("findings")
        if not isinstance(fd, dict):
            feature_violations.append({
                "feature_id": fid,
                "reason": "reviewer.findings missing or not dict",
                "field": "findings",
                "value_repr": _repr80(fd),
            })
        else:
            missing_keys = [k for k in findings_keys if k not in fd]
            non_list_keys = [
                k for k in findings_keys
                if k in fd and not isinstance(fd[k], list)
            ]
            if missing_keys:
                feature_violations.append({
                    "feature_id": fid,
                    "reason": (
                        f"reviewer.findings missing required keys="
                        f"{missing_keys} (required={findings_keys})"
                    ),
                    "field": "findings",
                    "value_repr": _repr80(fd),
                })
            if non_list_keys:
                feature_violations.append({
                    "feature_id": fid,
                    "reason": (
                        f"reviewer.findings keys not list: "
                        f"{non_list_keys}"
                    ),
                    "field": "findings",
                    "value_repr": _repr80(fd),
                })
        # grace_period: 若 feature 在 grace_set 且有 violation, 不计入 violations
        if feature_violations and fid in grace_set:
            out["grace_skipped"].append(fid)
        else:
            out["violations"].extend(feature_violations)
    out["ok"] = len(out["violations"]) == 0
    return out


# ---------------------------------------------------------------------------
# infra-P278-followup-closeout-baseline-head-echo-format-hard-check
# (phase-44 #5.44)
# 每个 closeout_verify.baseline_head_echo 形态合规 hard check
# ---------------------------------------------------------------------------
def assert_closeout_baseline_head_echo_format(
    feature_list_path,
    min_hex_chars: int = 7,
    grace_period_feature_ids: tuple = (),
) -> dict:
    """每个 closeout_verify.baseline_head_echo 必须形态合规.

    enforce-set 判定:
      - feature.status == 'passing'
      - evidence.closeout_verify 为 dict
      - closeout_verify.reviewer 为 dict
      - reviewer.reviewer_kind == 'sub_agent_fresh_context'

    形态规则:
      - baseline_head_echo 必须为非空 str
      - strip 后长度 >= min_hex_chars (默认 7, git short hash 最小)
      - 字符全为小写 hex (0-9a-f); 大写 / 非 hex 字符均算违反
      - 且必须 != closeout_verify.main_head_sha 的前 N 字符 (N=baseline 长度);
        baseline 是 closeout 前的 main, 不应与 closeout 后 main 相同

    参数:
        feature_list_path: feature_list.json 路径 (str | Path).
        min_hex_chars: baseline_head_echo 最少 hex 字符数 (默认 7).
        grace_period_feature_ids: 软放过列表 — 这些历史 feature 的 violations
            不计入 violations, 而计入 grace_skipped. 新 feature 不在此列表中
            即按 hard 强制. (V6-062-baseline-head-echo-format-promote-bool:
            emit 已 promote 至 bool, 用此列表把 16 个历史 violation feature
            一次性 grandfather 进来)

    返回 dict::

        {
          "ok": bool,
          "violations": [
              {
                  "feature_id": str,
                  "reason": str,
                  "field": str,        # always "baseline_head_echo"
                  "value_repr": str,   # repr(...) 截断到 80 字符
              },
              ...
          ],
          "soft_skipped": [str, ...],
          "grace_skipped": [str, ...],
          "enforced_count": int,
          "scanned_count": int,
          "grace_period_count": int,
          "min_hex_chars": int,
          "error": str | None,
        }

    Default-OFF + soft-PASS for legacy:
      - 老 feature (无 reviewer / reviewer 非 dict / reviewer_kind !=
        'sub_agent_fresh_context') → soft_skipped, 不算 violation.
    """
    from pathlib import Path as _P
    import json as _json
    import re as _re

    grace_set = set(grace_period_feature_ids or ())
    out: dict = {
        "ok": False,
        "violations": [],
        "soft_skipped": [],
        "grace_skipped": [],
        "enforced_count": 0,
        "scanned_count": 0,
        "grace_period_count": len(grace_set),
        "min_hex_chars": int(min_hex_chars),
        "error": None,
    }
    p = _P(feature_list_path)
    if not p.is_file():
        out["error"] = f"feature_list not found at {p}"
        return out
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"feature_list parse error: {e!r}"
        return out
    features = data.get("features")
    if not isinstance(features, list):
        out["error"] = "feature_list.features missing or not a list"
        return out

    def _repr80(v) -> str:
        s = repr(v)
        return s if len(s) <= 80 else s[:77] + "..."

    hex_re = _re.compile(r"^[0-9a-f]+$")
    for f in features:
        if not isinstance(f, dict):
            continue
        if f.get("status") != "passing":
            continue
        ev = f.get("evidence")
        if not isinstance(ev, dict):
            continue
        cv = ev.get("closeout_verify")
        if not isinstance(cv, dict):
            continue
        fid = f.get("id") or "<no-id>"
        out["scanned_count"] += 1
        reviewer = cv.get("reviewer")
        if not isinstance(reviewer, dict):
            out["soft_skipped"].append(fid)
            continue
        if reviewer.get("reviewer_kind") != "sub_agent_fresh_context":
            out["soft_skipped"].append(fid)
            continue
        out["enforced_count"] += 1
        feature_violations: list = []

        bhe = cv.get("baseline_head_echo")
        if not isinstance(bhe, str):
            feature_violations.append({
                "feature_id": fid,
                "reason": "baseline_head_echo missing or not str",
                "field": "baseline_head_echo",
                "value_repr": _repr80(bhe),
            })
        else:
            bhe_s = bhe.strip()
            if not bhe_s:
                feature_violations.append({
                    "feature_id": fid,
                    "reason": "baseline_head_echo empty after strip",
                    "field": "baseline_head_echo",
                    "value_repr": _repr80(bhe),
                })
            elif len(bhe_s) < int(min_hex_chars):
                feature_violations.append({
                    "feature_id": fid,
                    "reason": (
                        f"baseline_head_echo len={len(bhe_s)} < "
                        f"min_hex_chars={min_hex_chars}"
                    ),
                    "field": "baseline_head_echo",
                    "value_repr": _repr80(bhe),
                })
            elif not hex_re.match(bhe_s):
                feature_violations.append({
                    "feature_id": fid,
                    "reason": (
                        "baseline_head_echo contains non-hex chars "
                        "(must be 0-9a-f lowercase)"
                    ),
                    "field": "baseline_head_echo",
                    "value_repr": _repr80(bhe),
                })
            else:
                mhs = cv.get("main_head_sha")
                if isinstance(mhs, str):
                    mhs_s = mhs.strip()
                    n = len(bhe_s)
                    if mhs_s and mhs_s[:n] == bhe_s:
                        feature_violations.append({
                            "feature_id": fid,
                            "reason": (
                                f"baseline_head_echo={bhe_s!r} equals "
                                f"main_head_sha[:{n}]={mhs_s[:n]!r}; "
                                "baseline must differ from closeout-after main"
                            ),
                            "field": "baseline_head_echo",
                            "value_repr": _repr80(bhe),
                        })
        # grace_period: 若 feature 在 grace_set 且有 violation, 不计入 violations
        if feature_violations and fid in grace_set:
            out["grace_skipped"].append(fid)
        else:
            out["violations"].extend(feature_violations)
    out["ok"] = len(out["violations"]) == 0
    return out


# ---------------------------------------------------------------------------
# (phase-45 #4.45) infra-P278-followup-closeout-merge-commit-sha-format-hard-check
# 每个 closeout_verify.merge_commit_sha 形态合规 hard check (首次 hard, 配 grace)
# ---------------------------------------------------------------------------
def assert_closeout_merge_commit_sha_format(
    feature_list_path,
    min_hex_chars: int = 7,
    grace_period_feature_ids: tuple = (),
) -> dict:
    """每个 closeout_verify.merge_commit_sha 必须形态合规.

    enforce-set 判定 (同 assert_closeout_baseline_head_echo_format):
      - feature.status == 'passing'
      - evidence.closeout_verify 为 dict
      - closeout_verify.reviewer 为 dict
      - reviewer.reviewer_kind == 'sub_agent_fresh_context'

    形态规则:
      - merge_commit_sha 必须为非空 str
      - strip 后长度 >= min_hex_chars (默认 7, git short hash 最小)
      - 字符全为小写 hex (0-9a-f); 大写 / 非 hex 字符均算违反

    参数:
        feature_list_path: feature_list.json 路径 (str | Path).
        min_hex_chars: merge_commit_sha 最少 hex 字符数 (默认 7).
        grace_period_feature_ids: 软放过列表 — 这些历史 feature 的 violations
            不计入 violations, 而计入 grace_skipped.

    返回 dict::

        {
          "ok": bool,
          "violations": [
              {
                  "feature_id": str,
                  "reason": str,
                  "field": str,        # always "merge_commit_sha"
                  "value_repr": str,
              },
              ...
          ],
          "soft_skipped": [str, ...],
          "grace_skipped": [str, ...],
          "enforced_count": int,
          "scanned_count": int,
          "grace_period_count": int,
          "min_hex_chars": int,
          "error": str | None,
        }
    """
    from pathlib import Path as _P
    import json as _json
    import re as _re

    grace_set = set(grace_period_feature_ids or ())
    out: dict = {
        "ok": False,
        "violations": [],
        "soft_skipped": [],
        "grace_skipped": [],
        "enforced_count": 0,
        "scanned_count": 0,
        "grace_period_count": len(grace_set),
        "min_hex_chars": int(min_hex_chars),
        "error": None,
    }
    p = _P(feature_list_path)
    if not p.is_file():
        out["error"] = f"feature_list not found at {p}"
        return out
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"feature_list parse error: {e!r}"
        return out
    features = data.get("features")
    if not isinstance(features, list):
        out["error"] = "feature_list.features missing or not a list"
        return out

    def _repr80(v) -> str:
        s = repr(v)
        return s if len(s) <= 80 else s[:77] + "..."

    hex_re = _re.compile(r"^[0-9a-f]+$")
    for f in features:
        if not isinstance(f, dict):
            continue
        if f.get("status") != "passing":
            continue
        ev = f.get("evidence")
        if not isinstance(ev, dict):
            continue
        cv = ev.get("closeout_verify")
        if not isinstance(cv, dict):
            continue
        fid = f.get("id") or "<no-id>"
        out["scanned_count"] += 1
        reviewer = cv.get("reviewer")
        if not isinstance(reviewer, dict):
            out["soft_skipped"].append(fid)
            continue
        if reviewer.get("reviewer_kind") != "sub_agent_fresh_context":
            out["soft_skipped"].append(fid)
            continue
        out["enforced_count"] += 1
        feature_violations: list = []

        mcs = cv.get("merge_commit_sha")
        if not isinstance(mcs, str):
            feature_violations.append({
                "feature_id": fid,
                "reason": "merge_commit_sha missing or not str",
                "field": "merge_commit_sha",
                "value_repr": _repr80(mcs),
            })
        else:
            mcs_s = mcs.strip()
            if not mcs_s:
                feature_violations.append({
                    "feature_id": fid,
                    "reason": "merge_commit_sha empty after strip",
                    "field": "merge_commit_sha",
                    "value_repr": _repr80(mcs),
                })
            elif len(mcs_s) < int(min_hex_chars):
                feature_violations.append({
                    "feature_id": fid,
                    "reason": (
                        f"merge_commit_sha len={len(mcs_s)} < "
                        f"min_hex_chars={min_hex_chars}"
                    ),
                    "field": "merge_commit_sha",
                    "value_repr": _repr80(mcs),
                })
            elif not hex_re.match(mcs_s):
                feature_violations.append({
                    "feature_id": fid,
                    "reason": (
                        "merge_commit_sha contains non-hex chars "
                        "(must be 0-9a-f lowercase)"
                    ),
                    "field": "merge_commit_sha",
                    "value_repr": _repr80(mcs),
                })
        if feature_violations and fid in grace_set:
            out["grace_skipped"].append(fid)
        else:
            out["violations"].extend(feature_violations)
    out["ok"] = len(out["violations"]) == 0
    return out


def assert_closeout_main_head_sha_format(
    feature_list_path,
    min_hex_chars: int = 7,
    grace_period_feature_ids: tuple = (),
) -> dict:
    """每个 closeout_verify.main_head_sha 必须形态合规.

    enforce-set 判定 (同 assert_closeout_merge_commit_sha_format):
      - feature.status == 'passing'
      - evidence.closeout_verify 为 dict
      - closeout_verify.reviewer 为 dict
      - reviewer.reviewer_kind == 'sub_agent_fresh_context'

    形态规则:
      - main_head_sha 必须为非空 str
      - strip 后长度 >= min_hex_chars (默认 7, git short hash 最小)
      - 字符全为小写 hex (0-9a-f); 大写 / 非 hex 字符均算违反

    参数:
        feature_list_path: feature_list.json 路径 (str | Path).
        min_hex_chars: main_head_sha 最少 hex 字符数 (默认 7).
        grace_period_feature_ids: 软放过列表 — 这些历史 feature 的 violations
            不计入 violations, 而计入 grace_skipped.

    返回 dict (形态同 assert_closeout_merge_commit_sha_format, 字段名替换).
    """
    from pathlib import Path as _P
    import json as _json
    import re as _re

    grace_set = set(grace_period_feature_ids or ())
    out: dict = {
        "ok": False,
        "violations": [],
        "soft_skipped": [],
        "grace_skipped": [],
        "enforced_count": 0,
        "scanned_count": 0,
        "grace_period_count": len(grace_set),
        "min_hex_chars": int(min_hex_chars),
        "error": None,
    }
    p = _P(feature_list_path)
    if not p.is_file():
        out["error"] = f"feature_list not found at {p}"
        return out
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"feature_list parse error: {e!r}"
        return out
    features = data.get("features")
    if not isinstance(features, list):
        out["error"] = "feature_list.features missing or not a list"
        return out

    def _repr80(v) -> str:
        s = repr(v)
        return s if len(s) <= 80 else s[:77] + "..."

    hex_re = _re.compile(r"^[0-9a-f]+$")
    for f in features:
        if not isinstance(f, dict):
            continue
        if f.get("status") != "passing":
            continue
        ev = f.get("evidence")
        if not isinstance(ev, dict):
            continue
        cv = ev.get("closeout_verify")
        if not isinstance(cv, dict):
            continue
        fid = f.get("id") or "<no-id>"
        out["scanned_count"] += 1
        reviewer = cv.get("reviewer")
        if not isinstance(reviewer, dict):
            out["soft_skipped"].append(fid)
            continue
        if reviewer.get("reviewer_kind") != "sub_agent_fresh_context":
            out["soft_skipped"].append(fid)
            continue
        out["enforced_count"] += 1
        feature_violations: list = []

        mhs = cv.get("main_head_sha")
        if not isinstance(mhs, str):
            feature_violations.append({
                "feature_id": fid,
                "reason": "main_head_sha missing or not str",
                "field": "main_head_sha",
                "value_repr": _repr80(mhs),
            })
        else:
            mhs_s = mhs.strip()
            if not mhs_s:
                feature_violations.append({
                    "feature_id": fid,
                    "reason": "main_head_sha empty after strip",
                    "field": "main_head_sha",
                    "value_repr": _repr80(mhs),
                })
            elif len(mhs_s) < int(min_hex_chars):
                feature_violations.append({
                    "feature_id": fid,
                    "reason": (
                        f"main_head_sha len={len(mhs_s)} < "
                        f"min_hex_chars={min_hex_chars}"
                    ),
                    "field": "main_head_sha",
                    "value_repr": _repr80(mhs),
                })
            elif not hex_re.match(mhs_s):
                feature_violations.append({
                    "feature_id": fid,
                    "reason": (
                        "main_head_sha contains non-hex chars "
                        "(must be 0-9a-f lowercase)"
                    ),
                    "field": "main_head_sha",
                    "value_repr": _repr80(mhs),
                })
        if feature_violations and fid in grace_set:
            out["grace_skipped"].append(fid)
        else:
            out["violations"].extend(feature_violations)
    out["ok"] = len(out["violations"]) == 0
    return out


def assert_closeout_verify_runs_freshness(
    feature_list_path,
    min_run_count: int = 1,
    grace_period_feature_ids: tuple = (),
) -> dict:
    """每条 closeout_verify.verify_runs 必须带 freshness_anchor 字段, 锁
    verify 跑的是最终 (post-cascade) HEAD 上的 sha, 不是 stale 中间 sha.

    背景 (infra-P299-followup-engineer-stale-verify-evidence, phase-46 #2.46):
    P299 Closeout 暴露 Engineer 报告含 stale verify_runs (基于 cascade bump
    前的 HEAD 跑出的 FAIL 结果), 实际 final HEAD 上全 PASS. 本 helper 强制
    closeout 时显式锁: 每条 verify_runs entry 必须含 ``freshness_anchor``
    字段, 值要么是 closeout_verify.main_head_sha 的前 7+ chars (同 main HEAD
    跑出), 要么是显式字面量 "post-merge-rerun" (明确声明 post-merge 重跑过).

    enforce-set 判定 (同 assert_closeout_main_head_sha_format):
      - feature.status == 'passing'
      - evidence.closeout_verify 为 dict
      - closeout_verify.reviewer 为 dict
      - reviewer.reviewer_kind == 'sub_agent_fresh_context'

    形态规则 (per feature):
      - closeout_verify.verify_runs 必须是非空 list (len >= min_run_count)
      - 每条 entry 必须含 ``freshness_anchor`` 字段 (str)
      - freshness_anchor 必须满足以下任一:
        - 等于字面量 "post-merge-rerun"
        - 是 main_head_sha 的前缀 (>=7 chars, 小写 hex)
      - 否则 violation

    参数:
        feature_list_path: feature_list.json 路径 (str | Path).
        min_run_count: verify_runs 最少 entry 数 (默认 1).
        grace_period_feature_ids: 软放过列表 — 这些历史 feature 的 violations
            不计入 violations, 而计入 grace_skipped.

    返回 dict::

        {
          "ok": bool,
          "violations": [...],
          "soft_skipped": [str, ...],   # 缺 reviewer / 非 sub_agent_fresh_context
          "grace_skipped": [str, ...],
          "enforced": int,              # 真正参与 enforce 的 feature 数
          "scanned": int,               # status=passing 含 closeout_verify
          "grace_period_count": int,
          "min_run_count": int,
          "error": str | None,
        }
    """
    from pathlib import Path as _P
    import json as _json
    import re as _re

    grace_set = set(grace_period_feature_ids or ())
    out: dict = {
        "ok": False,
        "violations": [],
        "soft_skipped": [],
        "grace_skipped": [],
        "enforced": 0,
        "scanned": 0,
        "grace_period_count": len(grace_set),
        "min_run_count": int(min_run_count),
        "error": None,
    }
    p = _P(feature_list_path)
    if not p.is_file():
        out["error"] = f"feature_list not found at {p}"
        return out
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"feature_list parse error: {e!r}"
        return out
    features = data.get("features")
    if not isinstance(features, list):
        out["error"] = "feature_list.features missing or not a list"
        return out

    def _repr80(v) -> str:
        s = repr(v)
        return s if len(s) <= 80 else s[:77] + "..."

    hex_re = _re.compile(r"^[0-9a-f]+$")
    POST_MERGE_LITERAL = "post-merge-rerun"
    for f in features:
        if not isinstance(f, dict):
            continue
        if f.get("status") != "passing":
            continue
        ev = f.get("evidence")
        if not isinstance(ev, dict):
            continue
        cv = ev.get("closeout_verify")
        if not isinstance(cv, dict):
            continue
        fid = f.get("id") or "<no-id>"
        out["scanned"] += 1
        reviewer = cv.get("reviewer")
        if not isinstance(reviewer, dict):
            out["soft_skipped"].append(fid)
            continue
        if reviewer.get("reviewer_kind") != "sub_agent_fresh_context":
            out["soft_skipped"].append(fid)
            continue
        out["enforced"] += 1
        feature_violations: list = []

        main_head = cv.get("main_head_sha") or ""
        main_head_norm = main_head.strip().lower() if isinstance(main_head, str) else ""

        runs = cv.get("verify_runs")
        if not isinstance(runs, list):
            feature_violations.append({
                "feature_id": fid,
                "reason": "verify_runs missing or not a list",
                "field": "verify_runs",
                "value_repr": _repr80(runs),
            })
        elif len(runs) < int(min_run_count):
            feature_violations.append({
                "feature_id": fid,
                "reason": (
                    f"verify_runs len={len(runs)} < "
                    f"min_run_count={min_run_count}"
                ),
                "field": "verify_runs",
                "value_repr": _repr80(runs),
            })
        else:
            for idx, r in enumerate(runs):
                if not isinstance(r, dict):
                    feature_violations.append({
                        "feature_id": fid,
                        "reason": f"verify_runs[{idx}] not a dict",
                        "field": f"verify_runs[{idx}]",
                        "value_repr": _repr80(r),
                    })
                    continue
                anchor = r.get("freshness_anchor")
                if not isinstance(anchor, str):
                    feature_violations.append({
                        "feature_id": fid,
                        "reason": (
                            f"verify_runs[{idx}].freshness_anchor missing "
                            "or not str"
                        ),
                        "field": f"verify_runs[{idx}].freshness_anchor",
                        "value_repr": _repr80(anchor),
                    })
                    continue
                anchor_s = anchor.strip().lower()
                if anchor_s == POST_MERGE_LITERAL:
                    continue
                if (
                    len(anchor_s) >= 7
                    and hex_re.match(anchor_s)
                    and main_head_norm
                    and main_head_norm.startswith(anchor_s)
                ):
                    continue
                feature_violations.append({
                    "feature_id": fid,
                    "reason": (
                        f"verify_runs[{idx}].freshness_anchor invalid: "
                        f"must equal {POST_MERGE_LITERAL!r} or be a "
                        f">=7 hex prefix of main_head_sha "
                        f"({main_head_norm[:12]!r})"
                    ),
                    "field": f"verify_runs[{idx}].freshness_anchor",
                    "value_repr": _repr80(anchor),
                })
        if feature_violations and fid in grace_set:
            out["grace_skipped"].append(fid)
        else:
            out["violations"].extend(feature_violations)
    out["ok"] = len(out["violations"]) == 0
    return out


# ---------------------------------------------------------------------------
# infra-P294-followup-v5-reviewer-gate-evidence-bind (phase-46 #4.46):
# V5_reviewer_lgtm_gate evidence-bound helper.
#
# 与 assert_reviewer_lgtm 区别:
#   - assert_reviewer_lgtm 只校 reviewer_kind + verdict == 'LGTM'
#   - 本 helper 增强: verdict 大小写不敏感 in allowed_verdicts (默认 LGTM/conditional),
#     reviewer_kind == 'sub_agent_fresh_context',
#     summary 字段长度 >= min_summary_chars (默认 20)
#
# grace_period_feature_ids: feature_id 命中则 ok=True + grace_skipped=True.
# 单 sentinel 占位 "__V5_GRADUATE_SENTINEL_NEVER_MATCHES__" 表示"未豁免",
# 不与任何真实 feature_id 匹配.
# ---------------------------------------------------------------------------
def assert_v5_reviewer_gate_evidence_bind(
    feature_id: str,
    feature_list_path,
    min_summary_chars: int = 20,
    allowed_verdicts: tuple = ("LGTM", "conditional"),
    required_reviewer_kind: str = "sub_agent_fresh_context",
    grace_period_feature_ids: tuple = (
        "__V5_GRADUATE_SENTINEL_NEVER_MATCHES__",
    ),
) -> dict:
    """V5_reviewer_lgtm_gate evidence-bound 真闸门 (phase-46 #4.46).

    实读 feature_list.json 中目标 feature 的 evidence.reviewer (或
    evidence.closeout_verify.reviewer) 字段, 校验:
      - reviewer_kind == required_reviewer_kind (默认 'sub_agent_fresh_context')
      - verdict ∈ allowed_verdicts (大小写不敏感, 默认 ('LGTM','conditional'))
      - summary 为 str 且长度 >= min_summary_chars (默认 20)

    若 feature_id 命中 grace_period_feature_ids, 直接 ok=True + grace_skipped=True.

    Returns:
        dict: ok, feature_id, reviewer_kind, verdict, summary_len,
              grace_skipped, reason, error.
    """
    import json as _json
    out = {
        "ok": False,
        "feature_id": feature_id,
        "reviewer_kind": None,
        "verdict": None,
        "summary_len": 0,
        "grace_skipped": False,
        "reason": "",
        "error": None,
    }
    grace_set = set(grace_period_feature_ids or ())
    if feature_id in grace_set:
        out["ok"] = True
        out["grace_skipped"] = True
        out["reason"] = f"feature_id={feature_id!r} in grace_period (skipped)"
        return out
    path = Path(feature_list_path)
    if not path.is_file():
        out["error"] = f"feature_list.json not found: {path}"
        out["reason"] = out["error"]
        return out
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        out["error"] = f"feature_list.json parse error: {e!r}"
        out["reason"] = out["error"]
        return out
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        out["error"] = "feature_list.json missing top-level 'features' list"
        out["reason"] = out["error"]
        return out
    target = None
    for f in features:
        if isinstance(f, dict) and f.get("id") == feature_id:
            target = f
            break
    if target is None:
        out["reason"] = (
            f"feature id={feature_id!r} not found in feature_list.json"
        )
        return out
    evidence = target.get("evidence")
    if not isinstance(evidence, dict):
        out["reason"] = (
            f"feature {feature_id!r} has no 'evidence' dict "
            f"(got type={type(evidence).__name__})"
        )
        return out
    reviewer = None
    cv = evidence.get("closeout_verify")
    if isinstance(cv, dict) and isinstance(cv.get("reviewer"), dict):
        reviewer = cv["reviewer"]
    elif isinstance(evidence.get("reviewer"), dict):
        reviewer = evidence["reviewer"]
    if not isinstance(reviewer, dict):
        out["reason"] = (
            f"feature {feature_id!r} evidence.reviewer (or "
            f"evidence.closeout_verify.reviewer) missing or not a dict"
        )
        return out
    kind = reviewer.get("reviewer_kind")
    verdict = reviewer.get("verdict")
    summary = reviewer.get("summary")
    out["reviewer_kind"] = kind if isinstance(kind, str) else None
    out["verdict"] = verdict if isinstance(verdict, str) else None
    out["summary_len"] = len(summary) if isinstance(summary, str) else 0
    if kind != required_reviewer_kind:
        out["reason"] = (
            f"feature {feature_id!r} reviewer_kind={kind!r}, "
            f"expected {required_reviewer_kind!r}"
        )
        return out
    allowed_upper = {v.strip().upper() for v in allowed_verdicts}
    if (
        not isinstance(verdict, str)
        or verdict.strip().upper() not in allowed_upper
    ):
        out["reason"] = (
            f"feature {feature_id!r} reviewer.verdict={verdict!r}, "
            f"expected one of {sorted(allowed_upper)}"
        )
        return out
    if not isinstance(summary, str) or len(summary) < min_summary_chars:
        out["reason"] = (
            f"feature {feature_id!r} reviewer.summary len="
            f"{out['summary_len']}, expected >= {min_summary_chars}"
        )
        return out
    out["ok"] = True
    out["reason"] = (
        f"feature {feature_id!r}: kind={kind!r} verdict={verdict!r} "
        f"summary_len={out['summary_len']} OK"
    )
    return out
