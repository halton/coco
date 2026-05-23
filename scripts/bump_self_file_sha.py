#!/usr/bin/env python3
"""bump_self_file_sha: 通用 self-file-sha (V8-SELF-SHA-SKIP pragma) bump helper.

infra-P306-bump-helper-script-rollout (phase-64 #4):
风格对齐 `bump_reverse_sha_lock.py` / `bump_infra_034_v4_sha.py`. 此前
`verify_infra_035.py` / `verify_infra_033_lock_doc_rollout.py` 都用
``EXPECTED_SELF_FILE_SHA = "<64hex>"  # V8-SELF-SHA-SKIP`` 模式自锁本文件
整体 sha (剔除唯一一条 pragma 行后), 但 *bump* 路径只有人工 sed / 跑一次看
actual 再手抄回去, 与 V2/V4 外置常量已有 bump 助手的风格不一致, 容易在
closeout 漏 bump 触发 V8 红线.

本通用 helper:

1. 扫 ``scripts/verify_*.py`` 找到所有持 ``# V8-SELF-SHA-SKIP`` pragma 的
   ``EXPECTED_SELF_FILE_SHA = "<64hex>"`` 单行字面常量;
2. 按等价算法 (splitlines(keepends=True) + 剔除 rstrip endswith pragma 的
   行 + sha256) 重算每个 verify 脚本的自体 sha;
3. dry-run 报告差异; ``--apply`` 实际写回 (single-line literal 替换, 保留
   pragma 注释).

设计要点
--------
- **默认 dry-run, 不写盘**. 落盘需显式 ``--apply``.
- 支持 ``--target <path>`` 限定单个 verify 脚本; 默认扫所有 scripts/verify_*.py.
- 写回后 read-back 校验, 失败回滚 (写盘前保留原内容副本).
- 与 ``bump_reverse_sha_lock.py`` 互补: 那个负责"指向 *别的* verify 的反向
  sha 锁"; 本 helper 负责"指向 *自己* 的 V8 自体 sha 锁". 边界互不重叠.

退出码
------
0  无需更新 / 成功 dry-run / 成功 apply
2  指定 --target 不存在或无 pragma 锚
3  扫描后无任何 V8 self-sha 锁可处理 (空操作)
4  字面替换失败 (regex 未命中真常量行, 表示文件偏离规范)
5  写回后 read-back 校验不一致

CLI
---
::

    # dry-run 全扫
    python scripts/bump_self_file_sha.py

    # 仅看 verify_infra_035
    python scripts/bump_self_file_sha.py --target scripts/verify_infra_035.py

    # 实际落盘 (全部)
    python scripts/bump_self_file_sha.py --apply
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

PRAGMA = "# V8-SELF-SHA-SKIP"

# 真常量行严格匹配 (与 verify_infra_035.py V10 正则风格对齐, 允许多种引号):
#   EXPECTED_SELF_FILE_SHA = "<64hex>"  # V8-SELF-SHA-SKIP
_RE_SELF_LINE = re.compile(
    r'^(?P<lead>EXPECTED_SELF_FILE_SHA\s*=\s*")(?P<hex>[0-9a-fA-F]{64})(?P<tail>"\s*#\s*V8-SELF-SHA-SKIP\s*)$',
)


def _self_sha_excluding_pragma(path: Path) -> str:
    """与 verify_infra_035._self_sha_skip_sentinel() 等价的算法.

    splitlines(keepends=True) + 剔除 rstrip endswith pragma 的行 + sha256.
    """
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines(keepends=True)
    kept = [ln for ln in lines if not ln.rstrip().endswith(PRAGMA)]
    return hashlib.sha256("".join(kept).encode("utf-8")).hexdigest()


def _find_self_lock_line(src: str) -> Tuple[int, "re.Match[str]"] | Tuple[None, None]:
    """在源码里找唯一一条匹配 EXPECTED_SELF_FILE_SHA 真常量 + pragma 的行."""
    for i, ln in enumerate(src.splitlines(keepends=False)):
        m = _RE_SELF_LINE.match(ln)
        if m:
            return i, m
    return None, None


def scan_candidates(target: Path | None) -> List[Path]:
    """返回所有持 V8 self-sha 锁的 verify 脚本路径.

    target 非 None 时, 只返回该 target (必须存在且有锚); 否则扫 scripts/verify_*.py.
    """
    if target is not None:
        return [target]
    out: List[Path] = []
    for p in sorted(SCRIPTS.glob("verify_*.py")):
        src = p.read_text(encoding="utf-8")
        if PRAGMA not in src:
            continue
        lineno, _m = _find_self_lock_line(src)
        if lineno is None:
            continue
        out.append(p)
    return out


def bump_one(path: Path, dry_run: bool) -> Tuple[int, List[str]]:
    """Bump 单个 verify 脚本的 EXPECTED_SELF_FILE_SHA 到当前自体 sha.

    Returns (rc, report_lines).
    rc:
      0  成功 (含 no-op / dry-run / apply)
      4  regex 未命中真常量行
      5  read-back 校验不一致
    """
    report: List[str] = []
    rel = path.relative_to(REPO) if REPO in path.parents else path
    src = path.read_text(encoding="utf-8")
    lineno, m = _find_self_lock_line(src)
    if lineno is None:
        report.append(f"  - FAIL {rel}: 找不到 EXPECTED_SELF_FILE_SHA pragma 行")
        return 4, report
    old_sha = m.group("hex")
    new_sha = _self_sha_excluding_pragma(path)
    if old_sha == new_sha:
        report.append(f"  - {rel}:{lineno+1} no-op ({old_sha[:16]}...)")
        return 0, report
    if dry_run:
        report.append(
            f"  - DRY-RUN {rel}:{lineno+1} {old_sha[:16]} -> {new_sha[:16]}"
        )
        return 0, report
    # apply: 重建该行
    lines = src.splitlines(keepends=True)
    raw_line = lines[lineno]
    nl = "\n" if raw_line.endswith("\n") else ""
    new_line = (
        m.group("lead") + new_sha + m.group("tail").rstrip() + nl
    )
    lines[lineno] = new_line
    path.write_text("".join(lines), encoding="utf-8")
    # read-back 校验: 既要新字面命中, 又要新 sha 与 _self_sha_excluding_pragma 一致
    after_src = path.read_text(encoding="utf-8")
    _, m2 = _find_self_lock_line(after_src)
    if m2 is None or m2.group("hex") != new_sha:
        # 回滚
        path.write_text(src, encoding="utf-8")
        report.append(f"  - FAIL {rel}: read-back literal 不一致, 已回滚")
        return 5, report
    after_actual = _self_sha_excluding_pragma(path)
    if after_actual != new_sha:
        # 回滚
        path.write_text(src, encoding="utf-8")
        report.append(
            f"  - FAIL {rel}: read-back actual={after_actual[:16]} != literal={new_sha[:16]}, 已回滚"
        )
        return 5, report
    report.append(
        f"  - APPLIED {rel}:{lineno+1} {old_sha[:16]} -> {new_sha[:16]}"
    )
    return 0, report


def run(target: Path | None, dry_run: bool) -> Tuple[int, List[str]]:
    report: List[str] = []
    if target is not None:
        if not target.is_file():
            report.append(f"FAIL: target {target} not found")
            return 2, report
        src = target.read_text(encoding="utf-8")
        if PRAGMA not in src:
            report.append(f"FAIL: target {target} 不含 {PRAGMA} pragma")
            return 2, report
        lineno, _m = _find_self_lock_line(src)
        if lineno is None:
            report.append(f"FAIL: target {target} 无 EXPECTED_SELF_FILE_SHA pragma 行")
            return 2, report
    candidates = scan_candidates(target)
    if not candidates:
        report.append("WARN: 没有发现持 V8 self-sha 锁的 verify 脚本")
        return 3, report
    report.append(f"candidates={len(candidates)} dry_run={dry_run}")
    worst = 0
    any_changed = False
    for p in candidates:
        rc, sub_report = bump_one(p, dry_run=dry_run)
        report.extend(sub_report)
        if rc != 0:
            worst = max(worst, rc)
        else:
            # 区分 no-op vs 实际改动
            for ln in sub_report:
                if "DRY-RUN" in ln or "APPLIED" in ln:
                    any_changed = True
                    break
    if worst != 0:
        return worst, report
    if not any_changed:
        report.append("OK: 所有 V8 self-sha 锁已是最新, 无需 bump")
    return 0, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument(
        "--target",
        default=None,
        help="单个 verify 脚本路径 (相对仓库根或绝对); 不传则全扫 scripts/verify_*.py",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="实际写盘 (默认 dry-run)",
    )
    args = ap.parse_args(argv)
    target: Path | None = None
    if args.target is not None:
        target = Path(args.target)
        if not target.is_absolute():
            target = (REPO / target).resolve()
    rc, report = run(target, dry_run=not args.apply)
    for line in report:
        print(line, flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
