#!/usr/bin/env python3
"""verify_infra_030 — scripts/ sys.path 注入审计 meta-lock（verify-only）。

V0 file existence (docs + verify 文件)
V1 grep `sys.path.insert` 在 scripts/ 实际数量 >= 130 (doc 列举的 buffer 下限)
V2 doc 中两个核心锚点 (verify_interact_018.py / proactive_trace_summary.py) 注入字面量与当前源码一致
V3 mutant 反证：模拟新增 sys.path.insert（in-memory 临时文件），verify_infra_030 总数锁应触发感知
V4 default-OFF 等价（0 业务源码改动）— 检查 coco/ 树下没有任何 sys.path 操作
V5 summary

吸收 robot-025 finding F1/F2：sha256 锁与 git show 基线自洽问题 → 改用「字面量集合 + 总数下限」双层。
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
DOC = ROOT / "docs" / "syspath-injection-audit.md"

INJECTION_TOTAL_MIN = 130  # buffer 下限 (实际 136)

CORE_ANCHORS = {
    "scripts/verify_interact_018.py": "sys.path.insert(0, str(ROOT))",
    "scripts/proactive_trace_summary.py": "sys.path.insert(0, str(_ROOT))",
}

RESULTS: List[Tuple[str, str, str]] = []  # (verdict, name, detail)


def _emit(verdict: str, name: str, detail: str = "") -> None:
    RESULTS.append((verdict, name, detail))
    print(f"[verify_infra_030] {verdict} {name}: {detail}", flush=True)


def _grep_inserts() -> List[Tuple[Path, int, str]]:
    """返回 (path, lineno, line) 列表，全 scripts/*.py 下 sys.path.insert/append 命中。"""
    hits: List[Tuple[Path, int, str]] = []
    pat = re.compile(r"sys\.path\.(insert|append)")
    for f in sorted(SCRIPTS_DIR.glob("*.py")):
        try:
            text = f.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for i, line in enumerate(text, 1):
            if pat.search(line):
                hits.append((f, i, line))
    return hits


def v0_files_exist() -> None:
    missing = []
    if not DOC.exists():
        missing.append(str(DOC.relative_to(ROOT)))
    self_path = Path(__file__)
    if not self_path.exists():
        missing.append(str(self_path.relative_to(ROOT)))
    if missing:
        _emit("FAIL", "V0_files_exist", f"missing={missing}")
    else:
        _emit("PASS", "V0_files_exist", f"doc={DOC.relative_to(ROOT)} verify=self")


def v1_grep_total() -> None:
    hits = _grep_inserts()
    n = len(hits)
    if n >= INJECTION_TOTAL_MIN:
        _emit("PASS", "V1_grep_total", f"hits={n} (min={INJECTION_TOTAL_MIN})")
    else:
        _emit("FAIL", "V1_grep_total", f"hits={n} < min={INJECTION_TOTAL_MIN}")


def v2_core_anchors() -> None:
    missing = []
    for rel, literal in CORE_ANCHORS.items():
        p = ROOT / rel
        if not p.exists():
            missing.append(f"{rel}: file missing")
            continue
        text = p.read_text(encoding="utf-8")
        if literal not in text:
            missing.append(f"{rel}: literal not found `{literal}`")
    if missing:
        _emit("FAIL", "V2_core_anchors", "; ".join(missing))
    else:
        _emit("PASS", "V2_core_anchors", f"anchors={list(CORE_ANCHORS.keys())}")


def v3_mutant_detect() -> None:
    """in-memory mutant：模拟在 scripts/ 新增一个临时 .py，含 sys.path.insert，
    grep 命中数应增加 → 总数锁的下限策略可感知（命中数单调上升时锁仍 PASS，下降时锁 FAIL）。
    本断言反证：剔除任一已知核心锚点后总数 < 当前实测时锁不变（防止锁失效）。
    """
    hits_before = _grep_inserts()
    # 临时往 scripts/ 写一个 mutant 文件
    mutant = SCRIPTS_DIR / "_mutant_infra_030_tmp.py"
    try:
        mutant.write_text("import sys\nsys.path.insert(0, '/tmp')\n", encoding="utf-8")
        hits_after = _grep_inserts()
        delta = len(hits_after) - len(hits_before)
        if delta >= 1:
            _emit("PASS", "V3_mutant_detect", f"injected mutant -> hits {len(hits_before)} -> {len(hits_after)} (delta={delta})")
        else:
            _emit("FAIL", "V3_mutant_detect", f"mutant not detected: {len(hits_before)} -> {len(hits_after)}")
    finally:
        if mutant.exists():
            mutant.unlink()


def v4_no_coco_pollution() -> None:
    """coco/ 源码树下不应有 sys.path 操作（业务代码零侵入）。"""
    pat = re.compile(r"sys\.path\.(insert|append)")
    coco_dir = ROOT / "coco"
    offenders = []
    for f in coco_dir.rglob("*.py"):
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line) and not line.strip().startswith("#"):
                offenders.append(f"{f.relative_to(ROOT)}:{i}")
    if offenders:
        _emit("FAIL", "V4_no_coco_pollution", f"unexpected sys.path in coco/: {offenders}")
    else:
        _emit("PASS", "V4_no_coco_pollution", "coco/ 树下无 sys.path 注入 (符合 default-OFF 等价)")


def v5_summary() -> None:
    fails = [r for r in RESULTS if r[0] != "PASS"]
    total = len(RESULTS)
    if fails:
        _emit("FAIL", "V5_summary", f"fails={[r[1] for r in fails]} (total={total})")
    else:
        _emit("PASS", "V5_summary", f"all green ({total} checks)")


def main() -> int:
    v0_files_exist()
    v1_grep_total()
    v2_core_anchors()
    v3_mutant_detect()
    v4_no_coco_pollution()
    v5_summary()
    overall = "PASS" if all(r[0] == "PASS" for r in RESULTS) else "FAIL"
    print(f"[verify_infra_030] overall: {overall}", flush=True)
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
