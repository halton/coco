#!/usr/bin/env python3
"""_verify_lib: 共享 helper for scripts/verify_*.py (robot-035, infra-037).

robot-035: 把 verify_robot_032 中 ``_parse_headings_from_doc`` 抽成本模块的公开
helper ``parse_headings_from_doc``, 使多个 docs-lock verify 可复用 (单一事实源 +
sha 锁链路统一)。

infra-037: 新增 ``func_sha_by_name(path, func_name)`` helper, 封装顶层
function/method 的 ast 抽取 + 规范化 (``ast.unparse``) + sha256 计算, 让后续
verify-script 复用 func-level sha 抽取的统一入口, 降低复制粘贴成本。

运行环境约定 (infra-034)
------------------------
本模块由 scripts/verify_*.py 在已激活的 .venv 下 import (顶部以
``sys.path.insert(0, str(Path(__file__).resolve().parent))`` 把 scripts 目录置于
sys.path 头, 以便相对 import 本模块)。不要从仓库外部直接 import。
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

__all__ = ["parse_headings_from_doc", "func_sha_by_name"]


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
