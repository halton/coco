#!/usr/bin/env python3
"""_verify_lib: 共享 helper for scripts/verify_*.py (robot-035).

robot-035: 把 verify_robot_032 中 ``_parse_headings_from_doc`` 抽成本模块的公开
helper ``parse_headings_from_doc``, 使多个 docs-lock verify 可复用 (单一事实源 +
sha 锁链路统一)。

运行环境约定 (infra-034)
------------------------
本模块由 scripts/verify_*.py 在已激活的 .venv 下 import (顶部以
``sys.path.insert(0, str(Path(__file__).resolve().parent))`` 把 scripts 目录置于
sys.path 头, 以便相对 import 本模块)。不要从仓库外部直接 import。
"""
from __future__ import annotations

from pathlib import Path

__all__ = ["parse_headings_from_doc"]


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
