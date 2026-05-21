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
from pathlib import Path
from typing import Any

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
    "assert_verify_passed",
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
