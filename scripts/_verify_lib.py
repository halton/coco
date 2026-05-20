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


def scan_reverse_sha_locks(scripts_dir: str | Path) -> list[dict]:
    """扫 scripts_dir 下所有 verify_*.py + _verify_lib.py 顶层 64-hex sha 常量。

    返回 list of dict: {file, lineno, const_name, sha_hex}
    其中只保留 const_name 含 'SHA' 且匹配 ``VERIFY_<NNN>`` 模式 (反向锁候选)
    的条目, 用于 V6 类型一致性比对。

    infra-V6-backlog (P264): 从 verify_infra_034 ``_v6_scan_constants`` 抽出。
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
                if not _RE_REVLOCK_VERIFY_ID.search(const_name):
                    continue
                results.append({
                    "file": str(p.relative_to(base.parent)) if base.parent in p.parents else str(p),
                    "lineno": lineno,
                    "const_name": const_name,
                    "sha_hex": sha_hex,
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
    """
    base = Path(scripts_dir)
    live = live_verify_sha_set(base)
    live_sha_values = set(live.values())
    scanned = scan_reverse_sha_locks(base)
    orphans: list[dict] = []
    for item in scanned:
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
