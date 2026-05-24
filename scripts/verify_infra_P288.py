#!/usr/bin/env python3
"""verify_infra_P288: AGENTS.md 中 "Sub-agent fact-vs-blame checklist" 段落必须存在且含三项举证要求。

INFRA_P288_SHA_LOCKS sentinel.

P275/P278/P285 复现过同一类失真: sub-agent 把自身回填错误 / 中间脏快照 / 占位
未更新误归因为 "pre-existing FAIL"。本 feature 在 AGENTS.md "硬规则" 区追加
fact-vs-blame checklist 段, 把 "pre-existing 归因" 门槛抬高到必须同时附:

  1) 当前 main HEAD sha (7+ hex)
  2) 在该 main HEAD 上的 verify 实测 rc + stdout 尾行
  3) 涉及 EXPECTED_*_FUNC_SHA 锁值类 FAIL 时, 附 ast.unparse / file-sha 实测快照

V0-V5 锁:

- V0: scaffolding — AGENTS.md & 本脚本 self 存在
- V1: docstring sentinel + 本脚本 main() 自锁
- V5a: AGENTS.md 中段落标题字面存在 (锚点 marker)
- V5b: 三项举证要求字面 grep (main HEAD sha / verify 实测 rc + stdout / EXPECTED_*_FUNC_SHA 锁值)
- V5c: AGENTS.md 整体 file sha 锁 (任何对该段的删改都必 bump)

不锁 _verify_lib.py / 任何 V4 func sha — 本 feature 纯文档级硬规则, 避免无关 cascade。

Run::

    .venv/bin/python scripts/verify_infra_P288.py

## Lock: EXPECTED_AGENTS_MD_FILE_SHA
- target_file: AGENTS.md
- lock_kind: file_sha
- bump_when: 本 P288 段或 AGENTS.md 其它部分被合法修改
- bump_protocol: sha256(AGENTS.md.read_bytes()) → 回填常量, 再跑一次 ALL PASS
- rationale: 任何对 fact-vs-blame checklist 段的删改都强制 bump, 避免静默回退
"""

from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
AGENTS_MD = REPO / "AGENTS.md"
SELF_PATH = Path(__file__).resolve()

# V5c: AGENTS.md 整体 file sha — 首跑 placeholder, 跑出实测再回填
EXPECTED_AGENTS_MD_FILE_SHA = "31c305491b73dbeec16efb99cba306c7c261c0fd373c659fd8e63d68a8e631dd"

# V1: 本脚本 main() 自锁 — 首跑 placeholder, 跑出实测再回填
EXPECTED_MAIN_FUNC_SHA = "3858f81a86392437a7342662e874b38006a059ca0976d7905137878cd7abc64e"

DOCSTRING_SENTINEL = "INFRA_P288_SHA_LOCKS"

# V5a: AGENTS.md 中本段标题 marker
SECTION_HEADING = "### Sub-agent fact-vs-blame checklist (P288 防御，硬规则)"

# V5b: 三项举证要求关键字面 — 缺一即视为段落被偷偷改瘦, FAIL
REQUIRED_LITERALS = [
    "当前 main HEAD sha",
    "verify 实测 rc + 完整 stdout 尾行",
    "ast.unparse",
    "pre_existing_baseline_sha",
    "attributed_to: sub_agent_self_error_suspected",
]

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_P288][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _func_sha(path: Path, func_name: str) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return hashlib.sha256(ast.unparse(node).encode("utf-8")).hexdigest()
    raise RuntimeError(f"func {func_name!r} not found in {path}")


def v0_scaffolding() -> None:
    _emit("V0_agents_md_exists", AGENTS_MD.is_file(), f"path={AGENTS_MD}")
    _emit("V0_self_exists", SELF_PATH.is_file(), f"path={SELF_PATH}")


def v1_sentinel_and_main_sha() -> None:
    src = SELF_PATH.read_text(encoding="utf-8")
    _emit(
        "V1_docstring_sentinel",
        DOCSTRING_SENTINEL in src,
        f"expect '{DOCSTRING_SENTINEL}' in self source",
    )
    try:
        got = _func_sha(SELF_PATH, "main")
    except Exception as e:
        _emit("V1_main_func_sha", False, f"compute err: {e!r}")
        return
    if EXPECTED_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V1_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_MAIN_FUNC_SHA={got}",
        )
    else:
        _emit(
            "V1_main_func_sha",
            got == EXPECTED_MAIN_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_MAIN_FUNC_SHA[:16]}",
        )


def v5a_section_heading() -> None:
    src = AGENTS_MD.read_text(encoding="utf-8")
    _emit(
        "V5a_section_heading_present",
        SECTION_HEADING in src,
        f"expect heading {SECTION_HEADING!r} in AGENTS.md",
    )


def v5b_required_literals() -> None:
    src = AGENTS_MD.read_text(encoding="utf-8")
    # 定位段落范围: heading 到下一个 ### 之间
    start = src.find(SECTION_HEADING)
    if start < 0:
        _emit("V5b_section_locatable", False, "section heading not found, cannot scope")
        return
    next_h = src.find("\n### ", start + len(SECTION_HEADING))
    section = src[start : next_h if next_h > 0 else len(src)]
    for lit in REQUIRED_LITERALS:
        _emit(
            f"V5b_literal_{lit[:24].replace(' ', '_').replace(':', '')}",
            lit in section,
            f"expect literal {lit!r} inside P288 section",
        )


def v5c_agents_md_file_sha() -> None:
    got = _file_sha(AGENTS_MD)
    if EXPECTED_AGENTS_MD_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V5c_agents_md_file_sha",
            False,
            f"placeholder; bump EXPECTED_AGENTS_MD_FILE_SHA={got}",
        )
    else:
        _emit(
            "V5c_agents_md_file_sha",
            got == EXPECTED_AGENTS_MD_FILE_SHA,
            f"got={got[:16]} expect={EXPECTED_AGENTS_MD_FILE_SHA[:16]}",
        )


def main() -> int:
    v0_scaffolding()
    v1_sentinel_and_main_sha()
    v5a_section_heading()
    v5b_required_literals()
    v5c_agents_md_file_sha()

    fails = [t for (t, ok, _d) in _results if not ok]
    total = len(_results)
    if fails:
        print(
            f"[verify_infra_P288][SUMMARY] FAIL {len(fails)}/{total}: {fails}",
            flush=True,
        )
        return 1
    print(f"[verify_infra_P288][SUMMARY] ALL PASS ({total} checks)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
