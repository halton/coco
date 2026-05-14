#!/usr/bin/env python3
"""infra-014: paths-filter.yml 自检 lint（消化 infra-013 EC-2）

检查项（CI/dev 工具，default-OFF 不属运行期 gate）：
  L1 .github/paths-filter.yml 与 evidence/infra-008/paths-filter.yml byte-identical
     （infra-011 V8 / infra-013 V3 契约）
  L2 YAML 语法合法（PyYAML safe_load 不抛）
  L3 必含 area keys: vision/audio/companion/interact/infra/robot/publish + meta
     （与 verify_infra_011 AREAS + infra-013 meta 兜底对齐）
  L4 各 area pattern 非空（list 长度 >= 1，每条非空字符串）
  L5 兜底段顺序：meta 段所在行号必须在所有 7 个 area 段之后
     （pyproject.toml / tests/ / conftest.py 等跨 area 兜底必须放尾段，
      避免被 area pattern 抢先匹配；description V5 要求）

退出码：
  0 全 PASS
  1 任意 fail（带详细说明）

用法：
  python scripts/lint_paths_filter.py
  python scripts/lint_paths_filter.py --file <alt.yml>   # 指定文件（fixture 测试用）
  python scripts/lint_paths_filter.py --pair A.yml B.yml # 仅做 byte-identical 对比

infra-014 V2/V3 fixture 测试通过 --file/--pair 注入临时文件触发 fail 路径。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GITHUB = REPO_ROOT / ".github" / "paths-filter.yml"
DEFAULT_EVIDENCE = REPO_ROOT / "evidence" / "infra-008" / "paths-filter.yml"

REQUIRED_AREAS: tuple[str, ...] = (
    "vision",
    "audio",
    "companion",
    "interact",
    "infra",
    "robot",
    "publish",
)
META_KEY = "meta"
META_TAIL_HINTS: tuple[str, ...] = (
    "pyproject.toml",
    "tests/",
    "conftest.py",
)


class LintFail(Exception):
    pass


def _load_yaml(path: Path):
    import yaml  # type: ignore

    with open(path) as f:
        return yaml.safe_load(f)


def check_byte_identical(a: Path, b: Path) -> str:
    if not a.exists():
        raise LintFail(f"文件不存在: {a}")
    if not b.exists():
        raise LintFail(f"文件不存在: {b}")
    ba = a.read_bytes()
    bb = b.read_bytes()
    if ba != bb:
        raise LintFail(
            f"L1 byte-identical 失败：{a} ({len(ba)}B) != {b} ({len(bb)}B)；"
            "infra-011/013 契约要求两文件完全一致"
        )
    return f"L1 OK byte-identical {len(ba)}B"


def check_yaml_syntax(path: Path):
    try:
        data = _load_yaml(path)
    except Exception as e:
        raise LintFail(f"L2 YAML 语法非法 {path}: {type(e).__name__}: {e}") from None
    if not isinstance(data, dict):
        raise LintFail(f"L2 YAML 顶层非 dict: type={type(data).__name__}")
    return data, f"L2 OK YAML 合法（顶层 {len(data)} keys）"


def check_required_areas(data: dict) -> str:
    missing = [a for a in REQUIRED_AREAS if a not in data]
    if missing:
        raise LintFail(
            f"L3 缺 area keys: {missing}；现有 keys={sorted(data.keys())}"
        )
    if META_KEY not in data:
        raise LintFail(
            f"L3 缺 meta 兜底 key；现有 keys={sorted(data.keys())}"
        )
    # 警告：未知 key（不 fail）
    expected = set(REQUIRED_AREAS) | {META_KEY}
    unknown = sorted(k for k in data if k not in expected)
    msg = f"L3 OK areas {len(REQUIRED_AREAS)} + meta 齐全"
    if unknown:
        msg += f"  WARN unknown keys (non-fatal): {unknown}"
    return msg


def check_pattern_nonempty(data: dict) -> str:
    bad: list[str] = []
    for k, v in data.items():
        if not isinstance(v, list):
            bad.append(f"{k}: 非 list (type={type(v).__name__})")
            continue
        if len(v) == 0:
            bad.append(f"{k}: 空 list")
            continue
        for i, p in enumerate(v):
            if not isinstance(p, str) or not p.strip():
                bad.append(f"{k}[{i}]: 非字符串或空 ({p!r})")
    if bad:
        raise LintFail("L4 pattern 非空检查失败:\n  - " + "\n  - ".join(bad))
    total = sum(len(v) for v in data.values() if isinstance(v, list))
    return f"L4 OK 共 {total} pattern 非空"


def check_meta_tail_order(path: Path) -> str:
    r"""L5：meta 段在所有 area 段之后（行号比较）。

    用文本扫描而非 yaml.safe_load（后者顺序无意义）。识别顶层 key 行：
    `^[a-z_]+:\s*$`（注意 yaml 顶层 key，无缩进）。
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    top_key_lines: dict[str, int] = {}
    for i, ln in enumerate(lines):
        stripped = ln.rstrip()
        if not stripped or stripped.startswith("#"):
            continue
        # 顶层 key：无缩进 + 以 ':' 结尾（可能有空格或注释）
        if not ln[:1].isalpha():
            continue
        # `meta:` 或 `vision:` 等
        head = stripped.split(":", 1)[0].strip()
        if not head or " " in head:
            continue
        # 只关心我们已知的 key，避免误命中
        if head in REQUIRED_AREAS or head == META_KEY:
            top_key_lines.setdefault(head, i)

    if META_KEY not in top_key_lines:
        raise LintFail(
            f"L5 meta 段不存在（行号扫描未命中）；known top keys: {top_key_lines}"
        )

    meta_line = top_key_lines[META_KEY]
    area_lines = {
        k: top_key_lines[k] for k in REQUIRED_AREAS if k in top_key_lines
    }
    bad = [k for k, ln in area_lines.items() if ln >= meta_line]
    if bad:
        raise LintFail(
            f"L5 兜底段顺序异常：以下 area 段出现在 meta 段之后或同行 "
            f"(meta line={meta_line}): {[(k, area_lines[k]) for k in bad]}"
        )

    # 同时验证 meta 段确实承载兜底 hint（pyproject/tests/conftest 至少一个出现）
    # 用 yaml load 拿 meta list
    data = _load_yaml(path)
    meta_list = data.get(META_KEY, []) or []
    flat = "\n".join(meta_list) if isinstance(meta_list, list) else ""
    hits = [h for h in META_TAIL_HINTS if h in flat]
    if not hits:
        raise LintFail(
            f"L5 meta 段未承载 pyproject/tests/conftest 任一兜底 pattern；"
            f"meta={meta_list!r}"
        )

    return (
        f"L5 OK meta(line={meta_line}) 在所有 area 段之后；"
        f"兜底 hint hits={hits}"
    )


def run_full_lint(github: Path, evidence: Path) -> list[str]:
    results: list[str] = []
    # L1
    results.append(check_byte_identical(github, evidence))
    # L2
    data, msg = check_yaml_syntax(github)
    results.append(msg)
    # L3
    results.append(check_required_areas(data))
    # L4
    results.append(check_pattern_nonempty(data))
    # L5
    results.append(check_meta_tail_order(github))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="infra-014 paths-filter.yml 自检 lint")
    ap.add_argument("--file", type=Path, default=DEFAULT_GITHUB,
                    help="主 paths-filter.yml（默认 .github/paths-filter.yml）")
    ap.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE,
                    help="evidence 副本（默认 evidence/infra-008/paths-filter.yml）")
    ap.add_argument("--pair", nargs=2, type=Path, metavar=("A", "B"),
                    help="仅做 byte-identical 对比，跳过其它检查")
    args = ap.parse_args()

    try:
        if args.pair:
            msg = check_byte_identical(args.pair[0], args.pair[1])
            print(msg)
            print("[lint_paths_filter] OK (pair-only)")
            return 0
        results = run_full_lint(args.file, args.evidence)
        for r in results:
            print(r)
        print(f"[lint_paths_filter] OK {len(results)}/5")
        return 0
    except LintFail as e:
        print(f"[lint_paths_filter] FAIL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
