#!/usr/bin/env python3
"""Run all (or grouped) `scripts/verify_*.py` and aggregate results.

用法：
  python scripts/run_verify_all.py                # 串行跑全部
  python scripts/run_verify_all.py -j 4           # 并行 4 个 worker
  python scripts/run_verify_all.py --list         # 列出全部，不跑
  python scripts/run_verify_all.py --dry-run      # 模拟跑：列出预期任务列表与顺序，不实际跑
  python scripts/run_verify_all.py --area vision  # 仅跑 vision 组
  python scripts/run_verify_all.py --filter 006   # 仅跑文件名含 '006' 的
  python scripts/run_verify_all.py --skip-list    # 跳过 SKIP_LIST 中标注的脚本（CI 默认开）

退出码：任一脚本失败 → 1；否则 0。

infra-006：被 GitHub Actions verify-matrix workflow 调用，也可本地手跑。

## EXCLUDED 提醒（infra-009 / infra-006 L2-C）
模块级 ``EXCLUDED`` 常量列出 discover() 必须排除的 verify 脚本（避免矩阵自检递归）。
新增/重命名 verify_infra_006* 时同步更新本常量；``verify_infra_006`` 的 v5/v9
等价校验依赖与此处一致的集合。

## SKIP_LIST 维护
本仓库 sim-first：默认所有 verify_*.py 必须在 ./init.sh smoke 通过的 sim 环境内
PASS。SKIP_LIST 列出在 GitHub Actions ubuntu-latest 上**确实跑不动**的脚本，
原因仅限：
  - 真硬件（USB 麦克 / USB 摄像头 / 真扬声器 / 真 Reachy Mini 电机）
  - 真机 daemon 物理通路（仅 Control.app 拉起的 reachy-mini daemon 才有）
  - 离线包过大 / 执行时间远超 CI budget（写明 budget 数字）
每条目要求：脚本名 + 原因 + uat-* 跟踪条目（feature_list.json）。
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

# verify_infra_006.py 与 run_verify_all.py 共用此常量：discover() 必须排除
# verify_infra_006 自己（避免矩阵自检递归），verify_infra_006.v5/v9 需读同一份
# 集合做"on_disk vs --list 覆盖"等价校验。
EXCLUDED: frozenset[str] = frozenset({"verify_infra_006.py"})

# SKIP_LIST：在 CI（ubuntu-latest, COCO_CI=1）上跑不动的 verify。
# 每条 (脚本名, 原因, 跟踪项)。CI 通过 --skip-list 启用；本地默认不跳。
# 见模块 docstring 中的 SKIP_LIST 维护准则。
SKIP_LIST: tuple[tuple[str, str, str], ...] = (
    ("verify_asr_microphone.py",
     "真硬件 USB 麦克录音 + 真人开口说话，CI 无声卡",
     "uat-phase4"),
    ("verify_audio003_app_integration.py",
     "ReachyMini 客户端连 mockup-sim daemon (Zenoh 7447) + 真音频回环",
     "uat-phase4"),
    ("verify_companion001_app_integration.py",
     "ReachyMini 客户端连 mockup-sim daemon + 完整 Coco.run() 心跳协议",
     "uat-phase4"),
    ("verify_companion001_idle.py",
     "ReachyMini 客户端连 mockup-sim daemon + IdleAnimator 实时心跳",
     "uat-phase4"),
    ("verify_interact001.py",
     "ReachyMini 客户端连 mockup-sim daemon + 完整 wake/listen/think/speak 闭环",
     "uat-phase4"),
    ("verify_interact001_app_integration.py",
     "ReachyMini 客户端连 mockup-sim daemon + Coco.run() 集成",
     "uat-phase4"),
    ("verify_robot001_daemon.py",
     "spawn_daemon=False 直连 mockup-sim daemon 物理通路",
     "uat-phase4"),
    ("verify_robot002_actions.py",
     "mockup-sim daemon + 真 motion API 调用",
     "uat-phase4"),
    ("verify_publish.py",
     "reachy_mini.apps.app check . 会创建临时 venv 装包，远超 CI 60s/job budget",
     "uat-phase8"),
)
SKIP_NAMES: frozenset[str] = frozenset(name for name, *_ in SKIP_LIST)

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
    """返回 scripts/verify_*.py 按文件名稳定排序的列表。

    使用模块级 ``EXCLUDED`` 常量排除自检脚本，verify_infra_006 共用同一集合。
    """
    return sorted(
        p for p in SCRIPTS_DIR.glob("verify_*.py")
        if p.is_file() and p.name not in EXCLUDED
    )


def select(
    scripts: list[Path],
    *,
    area: str | None,
    name_filter: str | None,
    apply_skip: bool = False,
) -> list[Path]:
    out = []
    for p in scripts:
        if area and classify(p.name) != area:
            continue
        if name_filter and name_filter not in p.name:
            continue
        if apply_skip and p.name in SKIP_NAMES:
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
    p.add_argument("--skip-list", action="store_true",
                   help="启用 SKIP_LIST：跳过 CI 跑不动的脚本（CI 默认开，本地默认关）")
    p.add_argument("--restore-unrelated", metavar="FEATURE_ID", default=None,
                   help=("infra-014-fu-2: 跑完后调用 restore_unrelated_evidence "
                         "helper，自动 git restore 不在 evidence/<FEATURE_ID>/ 下"
                         "的 evidence 副作用（dev/closeout 工具）"))
    args = p.parse_args()

    scripts = select(
        discover(),
        area=args.area,
        name_filter=args.name_filter,
        apply_skip=args.skip_list,
    )
    if not scripts:
        print("[run_verify_all] no scripts matched")
        return 0

    if args.skip_list:
        skipped = [n for n, *_ in SKIP_LIST]
        print(f"[run_verify_all] --skip-list 启用，跳过 {len(skipped)} 个脚本：")
        for name, reason, track in SKIP_LIST:
            print(f"    SKIP {name}  ({reason}; 跟踪 {track})")

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
    # infra-016: append-only history jsonl（运行期零影响，写盘失败仅 warn）
    _emit_history(results, total)
    if fails:
        print("\n[run_verify_all] FAILED scripts (tail of output):")
        for sp, rc, dt, tail in fails:
            print(f"\n--- {sp.name} (rc={rc} dt={dt:.1f}s) ---")
            print(tail)
        # infra-014-fu-2: 即使 fail 也尝试 restore（dev 习惯：先看见 fail，再清干净）
        if args.restore_unrelated:
            _do_restore(args.restore_unrelated)
        return 1
    if args.restore_unrelated:
        _do_restore(args.restore_unrelated)
    return 0


def _emit_history(results: list[tuple[Path, int, float, str]], duration_s: float) -> None:
    """infra-016: 把本次 run 结果追加到 evidence/_history/verify_history.jsonl。

    运行期零影响：异常吞掉，stderr WARN 一次；不改 main() 返回 rc。
    """
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        from _history_writer import emit_verify  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[run_verify_all] WARN: history import fail: {e}\n")
        return
    fails = [r for r in results if r[1] != 0]
    passes = [r for r in results if r[1] == 0]
    emit_verify(
        total=len(results),
        pass_=len(passes),
        fail=len(fails),
        skip=0,  # run_verify_all 自身不区分 skip（SKIP_LIST 走 select 阶段过滤掉）
        duration_s=duration_s,
        failed_names=[sp.name for sp, *_ in fails],
    )


def _do_restore(target_feature_id: str) -> None:
    """infra-014-fu-2: 调用 restore_unrelated_evidence helper 清理副作用。"""
    try:
        from restore_unrelated_evidence import restore_unrelated_evidence
    except ImportError:
        # 兜底：fixed 路径 import
        sys.path.insert(0, str(SCRIPTS_DIR))
        from restore_unrelated_evidence import restore_unrelated_evidence  # type: ignore
    restored = restore_unrelated_evidence(target_feature_id, dry_run=False)
    if restored:
        print(f"\n[run_verify_all] restored {len(restored)} unrelated evidence "
              f"file(s) (target={target_feature_id}):")
        for p in restored:
            print(f"  {p}")
    else:
        print(f"\n[run_verify_all] no unrelated evidence to restore "
              f"(target={target_feature_id})")


if __name__ == "__main__":
    sys.exit(main())
