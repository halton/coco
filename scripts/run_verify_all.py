#!/usr/bin/env python3
"""Run all (or grouped) `scripts/verify_*.py` and aggregate results.

用法：
  python scripts/run_verify_all.py                # 串行跑全部
  python scripts/run_verify_all.py -j 4           # 并行 4 个 worker
  python scripts/run_verify_all.py --list         # 列出全部，不跑
  python scripts/run_verify_all.py --dry-run      # 模拟跑：列出预期任务列表与顺序，不实际跑
  python scripts/run_verify_all.py --area vision  # 仅跑 vision 组
  python scripts/run_verify_all.py --filter 006   # 仅跑文件名含 '006' 的

退出码：任一脚本失败 → 1；否则 0。

infra-006：被 GitHub Actions verify-matrix workflow 调用，也可本地手跑。
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"

# area 分组规则：按文件名前缀映射
AREA_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("infra", re.compile(r"verify_infra(_|\d)")),
    ("vision", re.compile(r"verify_vision")),
    ("audio", re.compile(r"verify_(asr|audio|tts)")),
    ("interact", re.compile(r"verify_interact")),
    ("companion", re.compile(r"verify_companion")),
    ("robot", re.compile(r"verify_robot")),
    ("publish", re.compile(r"verify_publish")),
]


def classify(name: str) -> str:
    for area, pat in AREA_RULES:
        if pat.search(name):
            return area
    return "other"


def discover() -> list[Path]:
    """返回 scripts/verify_*.py 按文件名稳定排序的列表。"""
    return sorted(
        p for p in SCRIPTS_DIR.glob("verify_*.py")
        if p.is_file() and p.name != "verify_infra_006.py"  # 不让矩阵 verify 自己跑自己
    )


def select(
    scripts: list[Path],
    *,
    area: str | None,
    name_filter: str | None,
) -> list[Path]:
    out = []
    for p in scripts:
        if area and classify(p.name) != area:
            continue
        if name_filter and name_filter not in p.name:
            continue
        out.append(p)
    return out


def run_one(script: Path, timeout: float) -> tuple[Path, int, float, str]:
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        dt = time.time() - t0
        # truncate huge output
        tail = (proc.stdout + proc.stderr)[-2000:]
        return script, proc.returncode, dt, tail
    except subprocess.TimeoutExpired:
        dt = time.time() - t0
        return script, 124, dt, f"TIMEOUT after {timeout}s"
    except Exception as e:  # noqa: BLE001
        dt = time.time() - t0
        return script, 1, dt, f"EXC: {type(e).__name__}: {e}"


def main() -> int:
    p = argparse.ArgumentParser(description="Run all verify_*.py and summarize")
    p.add_argument("-j", "--jobs", type=int, default=1,
                   help="并行 worker 数 (default 1 串行)")
    p.add_argument("--area", choices=[a for a, _ in AREA_RULES] + ["other"],
                   help="只跑指定 area")
    p.add_argument("--filter", dest="name_filter",
                   help="文件名子串过滤")
    p.add_argument("--timeout", type=float, default=600.0,
                   help="单个脚本超时秒 (default 600)")
    p.add_argument("--list", action="store_true",
                   help="仅列出选中脚本，不跑")
    p.add_argument("--dry-run", action="store_true",
                   help="模拟运行：按预期顺序列出任务但不实际执行")
    args = p.parse_args()

    scripts = select(discover(), area=args.area, name_filter=args.name_filter)
    if not scripts:
        print("[run_verify_all] no scripts matched")
        return 0

    if args.list:
        print(f"[run_verify_all] {len(scripts)} script(s) matched:")
        for s in scripts:
            print(f"  {classify(s.name):>10s}  {s.name}")
        return 0

    if args.dry_run:
        print(f"[run_verify_all] DRY-RUN: would run {len(scripts)} script(s) "
              f"(jobs={args.jobs}, timeout={args.timeout}s):")
        for i, s in enumerate(scripts, 1):
            print(f"  [{i:>2d}/{len(scripts)}] {classify(s.name):>10s}  {s.name}")
        print("[run_verify_all] DRY-RUN OK — no scripts executed")
        return 0

    print(f"[run_verify_all] running {len(scripts)} script(s), jobs={args.jobs}, timeout={args.timeout}s")
    results: list[tuple[Path, int, float, str]] = []
    t_start = time.time()
    if args.jobs <= 1:
        for s in scripts:
            r = run_one(s, args.timeout)
            results.append(r)
            sp, rc, dt, _ = r
            print(f"  [{('OK ' if rc == 0 else 'FAIL')}] rc={rc} dt={dt:6.1f}s  {sp.name}")
    else:
        with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futs = {ex.submit(run_one, s, args.timeout): s for s in scripts}
            for fut in cf.as_completed(futs):
                r = fut.result()
                results.append(r)
                sp, rc, dt, _ = r
                print(f"  [{('OK ' if rc == 0 else 'FAIL')}] rc={rc} dt={dt:6.1f}s  {sp.name}")

    total = time.time() - t_start
    fails = [r for r in results if r[1] != 0]
    print()
    print(f"[run_verify_all] total={total:.1f}s passed={len(results)-len(fails)} failed={len(fails)}")
    if fails:
        print("\n[run_verify_all] FAILED scripts (tail of output):")
        for sp, rc, dt, tail in fails:
            print(f"\n--- {sp.name} (rc={rc} dt={dt:.1f}s) ---")
            print(tail)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
