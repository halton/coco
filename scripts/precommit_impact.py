#!/usr/bin/env python3
"""infra-008: pre-commit verify 影响面分析

输入 staged python 文件（或 --files 显式列出），输出受影响的
`scripts/verify_*.py` 子集；可选直接 `--run` 跑这些 verify；可选生成
GitHub Actions `paths-filter` YAML 片段供 infra-006 矩阵 PR 时跳过无关 job。

用法：
    python scripts/precommit_impact.py --staged --list
    python scripts/precommit_impact.py --files coco/perception/scene_caption.py --list
    python scripts/precommit_impact.py --staged --run [--max N]
    python scripts/precommit_impact.py --paths-filter > evidence/infra-008/paths-filter.yml

映射规则（与 feature_list.json infra-008 verification 字段一致）：
  - `coco/<area>/X.py` → `scripts/verify_<area>_*.py`（area 与
    run_verify_all.py 的 AREA_RULES 对齐：infra/vision/audio/interact/
    companion/robot/publish；perception 归入 vision；llm/asr/tts 归入 audio
    或 interact 取决于文件名）。
  - `scripts/verify_*.py` 本身改动 → 自身必跑。
  - `coco/main.py` 改动 → 影响全量（保守）。
  - 无法定位的文件（非 coco/、非 scripts/verify_）：默认 fallback 跑全集；
    `--strict` 关闭 fallback 返回空列表。

import 反向图：简易静态扫描，只匹配
  `from coco.<mod> import` 与 `import coco.<mod>`，不解析动态/条件 import；
  从被改文件传播到所有引用该模块的 coco/ 与 scripts/verify_*.py。

skip-list：复用 `run_verify_all.SKIP_LIST`，`--run` 默认跳过这些（与 CI 对齐）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COCO_DIR = REPO_ROOT / "coco"
SCRIPTS_DIR = REPO_ROOT / "scripts"

# 复用 run_verify_all 的常量
sys.path.insert(0, str(SCRIPTS_DIR))
from run_verify_all import (  # noqa: E402
    AREA_RULES,
    EXCLUDED,
    SKIP_NAMES,
    classify,
    discover,
    run_one,
)

# coco 子目录 / 顶层模块 → area 映射
# 与 verify_<area>_*.py 命名约定对齐
DIR_TO_AREA: dict[str, str] = {
    "perception": "vision",  # 历史命名：perception 实现 → verify_vision_*
    "infra": "infra",
    "companion": "companion",
    "robot": "robot",
}

# 顶层 coco/*.py 模块名 → area
# 命中后该模块的改动只触发对应 area 的 verify（而非全量）
MODULE_TO_AREA: dict[str, str] = {
    "asr": "audio",
    "tts": "audio",
    "vad_trigger": "audio",
    "wake_word": "audio",
    "llm": "audio",  # 历史：LLM 集成测试在 verify_audio_* 与 interact 共担
    "interact": "interact",
    "dialog": "interact",
    "dialog_summary": "interact",
    "intent": "interact",
    "conversation": "interact",
    "gesture_dialog": "interact",
    "actions": "robot",
    "idle": "companion",
    "proactive": "companion",
    "emotion": "companion",
    "profile": "companion",
    "power_state": "companion",
    "multimodal_fusion": "companion",
    "offline_fallback": "interact",
    "banner": "infra",
    "config": "infra",
    "logging_setup": "infra",
    "metrics": "infra",
}

# 触发全量的 hot-path 文件（相对 REPO_ROOT 的 POSIX 路径）
HOT_FULL_FAN_OUT: frozenset[str] = frozenset({
    "coco/main.py",
    "coco/__init__.py",
    "coco/__main__.py",
})

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+coco(?:\.([\w\.]+))?\s+import|import\s+coco(?:\.([\w\.]+))?)",
    re.MULTILINE,
)


def _git_staged() -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=str(REPO_ROOT),
            text=True,
        )
    except subprocess.CalledProcessError:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _to_rel(path: str | Path) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.relative_to(REPO_ROOT)
        except ValueError:
            return str(p)
    return p.as_posix()


def _file_to_module(rel: str) -> str | None:
    """coco/perception/scene_caption.py → coco.perception.scene_caption"""
    if not rel.endswith(".py"):
        return None
    if not rel.startswith("coco/"):
        return None
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def _scan_imports(py_file: Path) -> set[str]:
    """返回该文件 import 的 coco.* module 名集合（dotted, no leading 'coco.')"""
    try:
        src = py_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    mods: set[str] = set()
    for m in _IMPORT_RE.finditer(src):
        sub = m.group(1) or m.group(2)
        if sub:
            # `from coco.perception.scene_caption import X` → sub='perception.scene_caption'
            # 同时把所有前缀也加进去（perception, perception.scene_caption）
            parts = sub.split(".")
            for i in range(1, len(parts) + 1):
                mods.add("coco." + ".".join(parts[:i]))
        else:
            mods.add("coco")
    return mods


def build_reverse_graph() -> dict[str, set[str]]:
    """module → set of files (rel posix) that import it (or its prefix)."""
    rev: dict[str, set[str]] = {}
    candidates: list[Path] = []
    candidates.extend(p for p in COCO_DIR.rglob("*.py") if p.is_file())
    candidates.extend(p for p in SCRIPTS_DIR.glob("verify_*.py") if p.is_file())
    for f in candidates:
        rel = _to_rel(f)
        for mod in _scan_imports(f):
            rev.setdefault(mod, set()).add(rel)
    return rev


def _verify_for_area(area: str, all_verifies: list[Path]) -> set[str]:
    """area → 命中的 verify_*.py 文件名集合（按 classify 同样规则）"""
    out: set[str] = set()
    for p in all_verifies:
        if classify(p.name) == area:
            out.add(p.name)
    return out


def _area_for_file(rel: str) -> str | None:
    """coco/<subdir>/x.py 或 coco/<top>.py → area"""
    if not rel.startswith("coco/"):
        return None
    parts = rel.split("/")
    if len(parts) >= 3:
        sub = parts[1]
        return DIR_TO_AREA.get(sub)
    if len(parts) == 2:
        # 顶层 coco/<name>.py
        name = parts[1]
        if name.endswith(".py"):
            stem = name[:-3]
            return MODULE_TO_AREA.get(stem)
    return None


def compute_impact(
    files: list[str],
    *,
    strict: bool = False,
) -> tuple[set[str], list[str], bool]:
    """返回 (affected verify 文件名集合, 注释/原因 list, full_fanout 标志)"""
    notes: list[str] = []
    rels = [_to_rel(f) for f in files]
    all_verifies = discover()  # 已排除 EXCLUDED
    verify_names = {p.name for p in all_verifies}

    # hot-path → 全量
    for r in rels:
        if r in HOT_FULL_FAN_OUT:
            notes.append(f"hot-path {r} → 全量 fan-out")
            return verify_names, notes, True

    affected: set[str] = set()
    located_any = False

    rev = build_reverse_graph()

    for rel in rels:
        # 1. verify_*.py 本身改动 → 自身
        if rel.startswith("scripts/") and rel.endswith(".py"):
            name = Path(rel).name
            if name.startswith("verify_") and name in verify_names:
                affected.add(name)
                located_any = True
                notes.append(f"{rel} → 自身命中")
                continue

        # 2. coco/<area>/X.py 或 coco/<top>.py → area verify
        if rel.startswith("coco/") and rel.endswith(".py"):
            area = _area_for_file(rel)
            if area:
                hits = _verify_for_area(area, all_verifies)
                affected |= hits
                located_any = True
                notes.append(f"{rel} → area={area} ({len(hits)} verify)")
            else:
                notes.append(f"{rel} → 无 area 映射")

            # 3. import 反向传播：找所有引用此 module 的文件，
            #    若文件是 verify_*.py 直接加，若是 coco/*.py 再按 area 加
            mod = _file_to_module(rel)
            if mod:
                callers = rev.get(mod, set())
                for caller in callers:
                    if caller.startswith("scripts/"):
                        cname = Path(caller).name
                        if cname in verify_names:
                            affected.add(cname)
                    elif caller.startswith("coco/"):
                        carea = _area_for_file(caller)
                        if carea:
                            affected |= _verify_for_area(carea, all_verifies)
                if callers:
                    notes.append(
                        f"  ↳ 反向 import 命中 {len(callers)} 个引用者"
                    )
            continue

        # 4. 其它（顶层文档、pyproject、.github 等）
        notes.append(f"{rel} → 无法定位")

    if not located_any and not affected:
        if strict:
            notes.append("strict 模式 + 全部文件未定位 → 返回空集")
            return set(), notes, False
        notes.append("fallback：未定位任何文件 → 全量 fan-out")
        return verify_names, notes, True

    # 不在 SKIP_NAMES 中的 affected 才会被 --run 实际跑；这里集合不过滤
    return affected, notes, False


def _paths_filter_yaml() -> str:
    """生成 GitHub Actions paths-filter YAML 片段（供 infra-006 矩阵参考）。

    输出形如：
      vision:
        - 'coco/perception/**'
        - 'scripts/verify_vision_*.py'
      infra:
        - 'coco/infra/**'
        - 'coco/main.py'
        - 'scripts/verify_infra_*.py'
        - 'scripts/run_verify_all.py'
    """
    areas: dict[str, list[str]] = {}
    # 从 DIR_TO_AREA / MODULE_TO_AREA 反向构建
    for subdir, area in DIR_TO_AREA.items():
        areas.setdefault(area, []).append(f"coco/{subdir}/**")
    for stem, area in MODULE_TO_AREA.items():
        areas.setdefault(area, []).append(f"coco/{stem}.py")
    # 每个 area 加 verify_<area>_*.py
    for area in list(areas.keys()) + [a for a, _ in AREA_RULES]:
        areas.setdefault(area, [])
        glob = f"scripts/verify_{area}_*.py"
        if glob not in areas[area]:
            areas[area].append(glob)
    # infra 还覆盖 run_verify_all + main + __init__
    areas.setdefault("infra", []).extend([
        "coco/main.py",
        "coco/__init__.py",
        "scripts/run_verify_all.py",
        "scripts/precommit_impact.py",
    ])
    # 去重排序
    lines: list[str] = ["# infra-008: paths-filter 建议片段（供 infra-006 verify-matrix 参考）"]
    for area in sorted(areas):
        pats = sorted(set(areas[area]))
        lines.append(f"{area}:")
        for p in pats:
            lines.append(f"  - '{p}'")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="infra-008 pre-commit impact analysis")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--staged", action="store_true",
                     help="使用 git diff --cached --name-only 作为输入")
    src.add_argument("--files", nargs="+",
                     help="显式列出文件（相对或绝对路径均可）")

    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true",
                      help="打印 affected verify 列表（默认）")
    mode.add_argument("--run", action="store_true",
                      help="依次跑 affected verify，任一失败返回非零")
    mode.add_argument("--paths-filter", action="store_true",
                      help="输出 GitHub Actions paths-filter YAML 片段")

    ap.add_argument("--strict", action="store_true",
                    help="无法定位的文件不再 fallback 全量，返回空集")
    ap.add_argument("--max", type=int, default=10,
                    help="--run 模式下最多跑多少个 verify（hook 时长护栏，default 10）")
    ap.add_argument("--timeout", type=float, default=600.0,
                    help="--run 模式下单脚本超时 (default 600s)")
    ap.add_argument("--no-skip-list", action="store_true",
                    help="--run 模式下不应用 SKIP_NAMES（默认应用，与 CI 对齐）")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.paths_filter:
        sys.stdout.write(_paths_filter_yaml())
        return 0

    if args.staged:
        files = _git_staged()
    elif args.files:
        files = args.files
    else:
        ap.error("必须指定 --staged 或 --files 或 --paths-filter")
        return 2

    if not files:
        print("[precommit_impact] no staged files; nothing to do")
        return 0

    affected, notes, full_fan_out = compute_impact(files, strict=args.strict)

    if args.verbose or args.list or not args.run:
        print(f"[precommit_impact] inputs={len(files)} affected_verify={len(affected)} "
              f"full_fan_out={full_fan_out}")
        for n in notes:
            print(f"  · {n}")

    if args.list or not args.run:
        for name in sorted(affected):
            print(name)
        return 0

    # --run：跑 affected verify
    apply_skip = not args.no_skip_list
    runnable = sorted(
        n for n in affected
        if not (apply_skip and n in SKIP_NAMES)
    )
    if len(runnable) > args.max:
        print(f"[precommit_impact] affected={len(runnable)} > --max={args.max}；"
              f"截断至前 {args.max} 个（按文件名排序）。完整列表用 --list 查看。")
        runnable = runnable[:args.max]

    if not runnable:
        print("[precommit_impact] no runnable verify after skip-list；OK")
        return 0

    print(f"[precommit_impact] running {len(runnable)} verify (apply_skip={apply_skip}):")
    fails: list[tuple[str, int, str]] = []
    for name in runnable:
        script = SCRIPTS_DIR / name
        if not script.exists():
            print(f"  [SKIP] {name} not found on disk")
            continue
        sp, rc, dt, tail = run_one(script, args.timeout)
        mark = "OK " if rc == 0 else "FAIL"
        print(f"  [{mark}] rc={rc} dt={dt:6.1f}s  {sp.name}")
        if rc != 0:
            fails.append((sp.name, rc, tail))

    if fails:
        print("\n[precommit_impact] FAILED:")
        for n, rc, tail in fails:
            print(f"\n--- {n} (rc={rc}) ---")
            print(tail)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
