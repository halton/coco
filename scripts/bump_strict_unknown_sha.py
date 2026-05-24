#!/usr/bin/env python3
"""bump_strict_unknown_sha: 自动 bump V3 EXPECTED_STRICT_UNKNOWN_SHA256/COUNT (dry-run 友好).

infra-P312-strict-unknown-sha-auto-bump (phase-66 #3):

V6 strict-area-match (verify_infra_V6_strict_area.py) V3 锁锁定当前 repo
``scan_unknown_area_nnns(scripts, known_area_nnns=None)`` 返回结果的
canonical sha256 + 集合 size。每次新增 ``verify_<area>_<NNN>.py`` (NNN 不在
已登记 area 表) → 集合大小 +1 → sha 漂移 → V3 FAIL, 需要手动 bump
``EXPECTED_STRICT_UNKNOWN_SHA256`` 与 ``EXPECTED_STRICT_UNKNOWN_COUNT``。

之前在 phase-66 #2 Reviewer 提出 P2 痛点: 每个新加 verify 脚本的 feature 都
要单独跑一次 "scan + 手抄 sha + 手抄 count" 三步, 易漏。本 helper 把这三步
机械化, 供 closeout sub-agent (或 Engineer 加完新 verify 脚本后) 一键调用。

设计要点
--------
- **默认 dry-run 不写盘**, 显式 ``--apply`` 才落盘。
- 自动算 ``scan_unknown_area_nnns(SCRIPTS, known_area_nnns=None)`` 当前结果,
  得到 new_sha + new_count。
- 在 ``scripts/verify_infra_V6_strict_area.py`` 中正则替换:
  - ``EXPECTED_STRICT_UNKNOWN_SHA256 = (...)`` 元组 hex 行
  - ``EXPECTED_STRICT_UNKNOWN_COUNT = <N>`` 数字行
- ``--verify`` bump 完跑 ``scripts/verify_infra_V6_strict_area.py`` 自检 V3 PASS。

退出码
------
0  无需更新 / 成功 dry-run / 成功 apply / 成功 verify
2  verify_infra_V6_strict_area.py 不存在
4  字面替换失败 (regex 未命中)
5  --verify 模式下 verify_infra_V6_strict_area.py 非 0 退出
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import scan_unknown_area_nnns  # noqa: E402

V6_FILE = SCRIPTS / "verify_infra_V6_strict_area.py"

# 元组形式: EXPECTED_STRICT_UNKNOWN_SHA256 = (\n    "<hex>"\n)
_RE_SHA_TUPLE = re.compile(
    r'(EXPECTED_STRICT_UNKNOWN_SHA256\s*=\s*\(\s*\n\s*")(?P<hex>[0-9a-f]{64})("\s*\n\s*\))',
    re.MULTILINE,
)
# COUNT 单行: EXPECTED_STRICT_UNKNOWN_COUNT = <N>
# 注意: 只匹配数字本身, 不吞行末空白/换行, 避免替换时丢失邻接空行触发 V1 self_file_sha 漂移
_RE_COUNT = re.compile(
    r'(?P<lead>EXPECTED_STRICT_UNKNOWN_COUNT\s*=\s*)(?P<n>\d+)',
)


def compute_current() -> tuple[str, int]:
    """算当前 repo scan_unknown_area_nnns 的 sha256 + count."""
    unk = scan_unknown_area_nnns(SCRIPTS, known_area_nnns=None)
    sha = hashlib.sha256(",".join(unk).encode()).hexdigest()
    return sha, len(unk)


def read_expected_from_v6() -> tuple[str | None, int | None]:
    """读 V6 文件中现有的 EXPECTED_STRICT_UNKNOWN_SHA256 与 COUNT."""
    if not V6_FILE.is_file():
        return None, None
    src = V6_FILE.read_text(encoding="utf-8")
    m_sha = _RE_SHA_TUPLE.search(src)
    m_cnt = _RE_COUNT.search(src)
    sha = m_sha.group("hex") if m_sha else None
    cnt = int(m_cnt.group("n")) if m_cnt else None
    return sha, cnt


def run_bump(dry_run: bool) -> tuple[int, list[str]]:
    """主流程: 读现状 → 算新值 → 比对 → 替换 (或 dry-run)."""
    report: list[str] = []
    if not V6_FILE.is_file():
        report.append(f"FAIL: {V6_FILE} not found")
        return 2, report
    new_sha, new_count = compute_current()
    old_sha, old_count = read_expected_from_v6()
    report.append(f"target={V6_FILE.relative_to(REPO)}")
    report.append(f"old_sha={old_sha[:16] if old_sha else 'None'}... old_count={old_count}")
    report.append(f"new_sha={new_sha[:16]}... new_count={new_count}")
    if old_sha == new_sha and old_count == new_count:
        report.append("OK: 已是最新, 无需 bump")
        return 0, report
    src = V6_FILE.read_text(encoding="utf-8")
    src_new, n_sha = _RE_SHA_TUPLE.subn(
        lambda m: m.group(1) + new_sha + m.group(3), src
    )
    src_new, n_cnt = _RE_COUNT.subn(
        lambda m: m.group("lead") + str(new_count), src_new
    )
    if n_sha != 1 or n_cnt != 1:
        report.append(f"FAIL: regex 命中次数异常 (sha={n_sha}, count={n_cnt}; 期望均为 1)")
        return 4, report
    if dry_run:
        report.append(
            f"DRY-RUN: would bump SHA {old_sha[:16] if old_sha else 'None'} -> {new_sha[:16]}"
            f", COUNT {old_count} -> {new_count}"
        )
        return 0, report
    V6_FILE.write_text(src_new, encoding="utf-8")
    report.append(
        f"APPLIED: bumped SHA {old_sha[:16] if old_sha else 'None'} -> {new_sha[:16]}"
        f", COUNT {old_count} -> {new_count}"
    )
    return 0, report


def run_verify() -> int:
    """跑 V6 verify 自检 V3 PASS."""
    if not V6_FILE.is_file():
        print(f"WARN: {V6_FILE} 不存在, 跳过 --verify", flush=True)
        return 0
    py = sys.executable
    proc = subprocess.run(
        [py, str(V6_FILE)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )
    print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--apply", action="store_true", help="实际写盘 (默认 dry-run)")
    ap.add_argument("--verify", action="store_true", help="bump 后跑 verify_infra_V6_strict_area.py")
    args = ap.parse_args(argv)
    rc, report = run_bump(dry_run=not args.apply)
    for line in report:
        print(line, flush=True)
    if rc != 0:
        return rc
    if args.verify:
        vrc = run_verify()
        if vrc != 0:
            print(f"FAIL: verify_infra_V6_strict_area.py rc={vrc}", flush=True)
            return 5
        print("OK: verify_infra_V6_strict_area.py PASS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
