#!/usr/bin/env python3
"""infra-014-fu-2: restore unrelated evidence/ files after regression verify.

Background
----------
companion-014 close-out 暴露 caveat #5：跑 verify_<other>.py 做回归会污染
非本 feature 的 evidence/<other-feature>/* 文件（覆盖 verify_summary.json
等），如果 closeout sub-agent 不手动 git restore 这些文件，会把无关副作用
带进 commit。

本脚本将该手工动作 codify 为可调用的 helper：

    from scripts.restore_unrelated_evidence import restore_unrelated_evidence
    restored = restore_unrelated_evidence("companion-014", dry_run=True)

也可作为 CLI 直接跑：

    python scripts/restore_unrelated_evidence.py --target companion-014 --dry-run
    python scripts/restore_unrelated_evidence.py --target companion-014

不引入运行期 env gate；这是 dev / closeout 工具，按需手动调用。
run_verify_all.py 也可通过 ``--restore-unrelated <feature_id>`` flag 在跑完后
立即调用本 helper。

策略
----
1. ``git status -s`` 收集当前所有 ``evidence/`` 下被改动 / 删除的文件
2. 排除 ``evidence/<target_feature_id>/`` 路径（保留本 feature 自己的产出）
3. 对其余 ``evidence/...`` 路径执行 ``git checkout -- <path>``
4. 返回被 restore 的文件列表（list[str]，仓库相对路径）

只动 evidence/，不碰任何代码 / 文档 / verify 脚本。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PREFIX = "evidence/"


def _git_status_porcelain(repo: Path) -> list[tuple[str, str]]:
    """Run ``git status -s`` in ``repo`` and return (status, path) tuples.

    porcelain v1 行格式：``XY <space> path``，rename 形如 ``R  old -> new``。
    我们对 evidence/ 下的修改只关心 path（不分 staged / unstaged，都尝试 restore）。
    """
    r = subprocess.run(
        ["git", "status", "-s", "--porcelain"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    )
    out: list[tuple[str, str]] = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        # porcelain 头两个字符是 status code，第 3 字符是空格，其余是 path
        if len(line) < 4:
            continue
        code = line[:2]
        rest = line[3:]
        # rename: "R  old -> new" → 取 new
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        # untracked file ("??") 也算 evidence/ 副作用 — 但 git checkout 无法
        # 还原它（没有 index 版本），交给用户决定。这里只记录已 tracked 的修改。
        if code == "??":
            continue
        out.append((code, rest.strip()))
    return out


def restore_unrelated_evidence(
    target_feature_id: str,
    *,
    dry_run: bool = False,
    repo_root: Path | None = None,
    extra_keep_paths: list[str] | None = None,
) -> list[str]:
    """Restore evidence/ files NOT under ``evidence/<target_feature_id>/``.

    Parameters
    ----------
    target_feature_id : str
        本 feature id（如 ``"companion-014"``）。``evidence/companion-014/``
        下的文件不会被 restore（保留本 feature 产出）。
    dry_run : bool
        True 只列出待 restore 的文件，不实际执行 ``git checkout``。
    repo_root : Path | None
        仓库根目录；默认指向 coco repo。测试时传 tmp git repo。
    extra_keep_paths : list[str] | None
        额外 protect 的仓库相对路径列表（精确字符串匹配）。用于本 feature
        需要修改的、但属于他人 evidence/ 目录的文件，例如 infra-014-fu-2
        本身需要修改 ``evidence/infra-008/paths-filter.yml``（双副本同步）。

    Returns
    -------
    list[str]
        被 restore 的文件路径列表（仓库相对，正斜杠）。dry_run 模式下返回
        "如果实际 run 会被 restore" 的列表。
    """
    if not target_feature_id or "/" in target_feature_id:
        raise ValueError(
            f"target_feature_id must be a non-empty feature id without '/': "
            f"got {target_feature_id!r}"
        )
    repo = (repo_root or REPO_ROOT).resolve()
    target_prefix = f"{EVIDENCE_PREFIX}{target_feature_id}/"
    keep_set = set(extra_keep_paths or ())

    changes = _git_status_porcelain(repo)
    candidates: list[str] = []
    for _code, path in changes:
        # 仅处理 evidence/ 下、且不在 target feature 子目录下
        if not path.startswith(EVIDENCE_PREFIX):
            continue
        if path.startswith(target_prefix):
            continue
        if path in keep_set:
            continue
        candidates.append(path)

    if not candidates or dry_run:
        return candidates

    # 实际执行 restore；逐个 checkout（容忍单个失败但记录）
    for path in candidates:
        subprocess.run(
            ["git", "checkout", "--", path],
            cwd=str(repo), check=True,
            capture_output=True, text=True,
        )
    return candidates


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Restore evidence/ files not under evidence/<target>/ "
            "after regression verify runs (infra-014-fu-2)."
        ),
    )
    p.add_argument(
        "--target", required=True,
        help="本 feature id（如 companion-014）；其 evidence/ 子目录会被保留",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="只列出待 restore 文件，不实际执行 git checkout",
    )
    p.add_argument(
        "--repo-root", type=Path, default=None,
        help="仓库根（测试用）；默认仓库自身",
    )
    p.add_argument(
        "--keep", action="append", default=[],
        metavar="PATH",
        help=("额外 protect 的仓库相对路径（可重复）。用于本 feature 需修改、"
              "但属于他人 evidence/ 目录的文件，例如 "
              "evidence/infra-008/paths-filter.yml（infra-014 系列双副本同步）"),
    )
    args = p.parse_args(argv)

    restored = restore_unrelated_evidence(
        args.target,
        dry_run=args.dry_run,
        repo_root=args.repo_root,
        extra_keep_paths=args.keep,
    )
    label = "WOULD RESTORE" if args.dry_run else "RESTORED"
    if not restored:
        print(f"[restore_unrelated_evidence] no candidates "
              f"(target={args.target}, dry_run={args.dry_run})")
        return 0
    print(f"[restore_unrelated_evidence] {label} {len(restored)} file(s) "
          f"(target={args.target}):")
    for path in restored:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
