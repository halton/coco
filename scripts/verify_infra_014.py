#!/usr/bin/env python3
"""infra-014 verification V1-V8.

按 feature_list.json infra-014 verification 字段：

  V1 --max 截断策略 stdout 含 coverage_ratio + strategy
  V2 改动权重模式（weighted）覆盖深度断言（hot verify 优先入选）
  V3 hot path 全量 fan-out 不退步（coco/main.py 仍跑全量；不被 --max 吃掉）
  V4 paths-filter 一致性 lint：.github vs evidence byte-identical
  V5 兜底段顺序：meta 段在所有 area 段之后（pyproject/tests/conftest 出现在尾段）
  V6 docs/regression-policy.md actionlint hook 列项存在
  V7 verify_impact --max 老路径 (alpha) 兼容 + 默认仍字母序但加 stdout WARN
  V8 lint_paths_filter 自检脚本被 paths-filter infra area 触发自身

回归（独立运行）：
  scripts/verify_infra_008.py / verify_infra_011.py / verify_infra_013.py 全 PASS
  ./init.sh smoke
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "precommit_impact.py"
LINT = REPO_ROOT / "scripts" / "lint_paths_filter.py"
PATHS_FILTER_GITHUB = REPO_ROOT / ".github" / "paths-filter.yml"
PATHS_FILTER_EVIDENCE = REPO_ROOT / "evidence" / "infra-008" / "paths-filter.yml"
REGRESSION_DOC = REPO_ROOT / "docs" / "regression-policy.md"

PY = sys.executable
FAILS: list[str] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    status = "OK" if ok else "FAIL"
    print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def _run(cmd: list[str], **kw) -> tuple[int, str, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return r.returncode, r.stdout, r.stderr


# -------------------- V1 --------------------
def v1_coverage_ratio_stdout() -> None:
    """触发 --run 路径（用一个返回 rc=1 的假 verify？太重）。
    退而求其次：直接读 main() 用 --files + --run + --max=1 触发截断与 stdout 痕迹。
    我们用一个低风险但确实存在的 verify_*.py 子集（perception 文件）。
    为了保证不真跑 verify，用 fixture：构造临时仓库 + 假的 verify 脚本。
    """
    # 直接调用 module 的 select_runnable 也行，但 description V1 要 stdout 痕迹。
    # 选择：构造一个 stub 调用——用 --files 触发，并劫持 SCRIPTS_DIR。
    # 简化做法：用 --files 指向 coco/perception/scene_caption.py + --max=1 +
    # --max-strategy=alpha + --run，让它真跑 1 个 verify（不阻塞太久）。
    # 但这会跑 verify_vision_*。改用 --no-skip-list + --max=0 不行（需 >=1）。
    # 使用：直接调 sample 策略 + --files=README.md（fallback 全量但无 hot-path）。
    # README 走 fallback → full_fan_out=True → 绕过 --max-strategy。
    # 最稳：用 weighted 策略，给 --files=coco/perception/scene_caption.py + --max=2，
    # 不跑 --run（用 --list）。但 --list 不打 coverage_ratio（在 --run 路径里）。
    #
    # 决策：构造 tmp git repo 镜像最小骨架，让 --run 在 fixture verify 上跑通。
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # 复制必要骨架：scripts/precommit_impact.py / scripts/run_verify_all.py
        # + 假 verify_*.py
        (tmp / "scripts").mkdir()
        (tmp / "coco").mkdir()
        for f in ["precommit_impact.py", "run_verify_all.py"]:
            shutil.copy(REPO_ROOT / "scripts" / f, tmp / "scripts" / f)
        # 假 verify：3 个全部 exit 0
        for nm in ["verify_vision_zfake_a.py", "verify_vision_zfake_b.py",
                   "verify_vision_zfake_c.py"]:
            (tmp / "scripts" / nm).write_text("import sys; sys.exit(0)\n")
        # coco/perception/x.py（让 _area_for_file 命中 vision）
        (tmp / "coco" / "perception").mkdir()
        (tmp / "coco" / "perception" / "x.py").write_text("x=1\n")
        (tmp / "coco" / "__init__.py").write_text("")
        rc, out, err = _run(
            [PY, str(tmp / "scripts/precommit_impact.py"),
             "--files", "coco/perception/x.py",
             "--run", "--max", "1", "--max-strategy", "weighted",
             "--no-skip-list"],
            cwd=str(tmp),
        )
        ok = (
            rc == 0
            and "coverage_ratio=" in out
            and "strategy=weighted" in out
            and "1/" in out  # 截断后 1
        )
        _record(
            "V1 --run stdout 含 coverage_ratio + strategy",
            ok,
            f"rc={rc} hits={'coverage_ratio=' in out},{'strategy=weighted' in out}",
        )


# -------------------- V2 --------------------
def v2_weighted_strategy_picks_hot() -> None:
    """weighted 策略下，被多 staged 文件命中的 verify 优先入选。"""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import precommit_impact as pi  # noqa: E402

    runnable = sorted([
        "verify_alpha_001.py",
        "verify_alpha_002.py",
        "verify_zhot_999.py",  # 字母序最末，但权重最高
    ])
    weights = {"verify_zhot_999.py": 5, "verify_alpha_001.py": 1}
    chosen, truncated, note = pi.select_runnable(
        runnable, max_n=1, strategy="weighted", weights=weights, sample_seed="x"
    )
    ok = truncated and chosen == ["verify_zhot_999.py"]
    _record(
        "V2 weighted 选高权重 hot verify",
        ok,
        f"chosen={chosen} truncated={truncated}",
    )


# -------------------- V3 --------------------
def v3_hot_path_fanout_no_truncate() -> None:
    """coco/main.py 触发 full_fan_out → 不被 --max 截断（绕过 strategy）。"""
    rc, out, err = _run([
        PY, str(SCRIPT), "--files", "coco/main.py",
        "--list",
    ], cwd=str(REPO_ROOT))
    has_full = "full_fan_out=True" in out
    names = [ln for ln in out.splitlines() if ln.startswith("verify_")]
    _record(
        "V3 hot-path coco/main.py 全量",
        rc == 0 and has_full and len(names) >= 30,
        f"rc={rc} full={has_full} hits={len(names)}",
    )


# -------------------- V4 --------------------
def v4_lint_byte_identical() -> None:
    rc, out, err = _run([PY, str(LINT)], cwd=str(REPO_ROOT))
    ok = rc == 0 and "L1 OK byte-identical" in out
    _record(
        "V4 lint_paths_filter 默认 PASS（byte-identical）",
        ok,
        f"rc={rc}",
    )
    # 同时构造漂移 fixture：临时改 evidence 文件 → lint 应 fail
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "a.yml"
        b = Path(td) / "b.yml"
        a.write_text("x: 1\n")
        b.write_text("x: 2\n")
        rc2, out2, err2 = _run(
            [PY, str(LINT), "--pair", str(a), str(b)],
            cwd=str(REPO_ROOT),
        )
        # 写一致 → OK
        b.write_text("x: 1\n")
        rc3, out3, err3 = _run(
            [PY, str(LINT), "--pair", str(a), str(b)],
            cwd=str(REPO_ROOT),
        )
        ok2 = rc2 != 0 and "byte-identical" in (out2 + err2) and rc3 == 0
        _record(
            "V4b 漂移 fixture lint fail / 一致 fixture lint OK",
            ok2,
            f"drift_rc={rc2} same_rc={rc3}",
        )


# -------------------- V5 --------------------
def v5_meta_tail_order() -> None:
    rc, out, err = _run([PY, str(LINT)], cwd=str(REPO_ROOT))
    ok = rc == 0 and "L5 OK meta" in out and "在所有 area 段之后" in out
    _record(
        "V5 meta 兜底段顺序在 area 之后 + 含 pyproject/tests/conftest",
        ok,
        f"rc={rc}",
    )
    # 构造 area 在 meta 之后 → fail fixture
    with tempfile.TemporaryDirectory() as td:
        tmp_yml = Path(td) / "bad.yml"
        # meta 在 vision 之前 → 顺序错
        tmp_yml.write_text(
            "meta:\n"
            "  - 'pyproject.toml'\n"
            "  - 'tests/**'\n"
            "  - 'conftest.py'\n"
            "vision:\n"
            "  - 'coco/perception/**'\n"
            "audio:\n"
            "  - 'coco/asr.py'\n"
            "companion:\n"
            "  - 'coco/companion/**'\n"
            "interact:\n"
            "  - 'coco/interact.py'\n"
            "infra:\n"
            "  - 'coco/infra/**'\n"
            "robot:\n"
            "  - 'coco/robot/**'\n"
            "publish:\n"
            "  - 'scripts/verify_publish_*.py'\n"
        )
        rc2, out2, err2 = _run(
            [PY, str(LINT), "--file", str(tmp_yml), "--evidence", str(tmp_yml)],
            cwd=str(REPO_ROOT),
        )
        ok2 = rc2 != 0 and "L5" in (out2 + err2) and "兜底段顺序" in (out2 + err2)
        _record(
            "V5b lint 检测出 meta 在 area 之前 → fail",
            ok2,
            f"rc={rc2}",
        )


# -------------------- V6 --------------------
def v6_regression_policy_actionlint_listed() -> None:
    if not REGRESSION_DOC.exists():
        _record("V6 docs/regression-policy.md 存在", False, "缺文件")
        return
    text = REGRESSION_DOC.read_text(encoding="utf-8")
    has_actionlint = "actionlint" in text.lower()
    has_dry_run = "dry-run" in text.lower() or "dry run" in text.lower()
    has_hook = "hook" in text.lower()
    has_track = "follow-up" in text.lower() or "跟踪" in text
    ok = has_actionlint and has_dry_run and has_hook and has_track
    _record(
        "V6 regression-policy 列出 actionlint dry-run hook + 跟踪",
        ok,
        f"actionlint={has_actionlint} dry-run={has_dry_run} hook={has_hook} track={has_track}",
    )


# -------------------- V7 --------------------
def v7_alpha_default_with_warn() -> None:
    """alpha 是默认策略 + stdout 含 WARN 提示。"""
    # 用 fixture repo 触发截断，验证默认 alpha + WARN
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "coco" / "perception").mkdir(parents=True)
        for f in ["precommit_impact.py", "run_verify_all.py"]:
            shutil.copy(REPO_ROOT / "scripts" / f, tmp / "scripts" / f)
        for nm in ["verify_vision_zfake_a.py", "verify_vision_zfake_b.py",
                   "verify_vision_zfake_c.py"]:
            (tmp / "scripts" / nm).write_text("import sys; sys.exit(0)\n")
        (tmp / "coco" / "perception" / "x.py").write_text("x=1\n")
        (tmp / "coco" / "__init__.py").write_text("")
        # 不传 --max-strategy → 默认 alpha
        rc, out, err = _run(
            [PY, str(tmp / "scripts/precommit_impact.py"),
             "--files", "coco/perception/x.py",
             "--run", "--max", "1", "--no-skip-list"],
            cwd=str(tmp),
        )
        ok = (
            rc == 0
            and "strategy=alpha" in out
            and "WARN alpha" in out
            and "coverage_ratio=1/" in out
        )
        _record(
            "V7 默认 alpha 截断 + WARN + coverage_ratio",
            ok,
            f"rc={rc} alpha={'strategy=alpha' in out} WARN={'WARN alpha' in out}",
        )


# -------------------- V8 --------------------
def v8_lint_script_in_paths_filter_infra() -> None:
    """改 scripts/lint_paths_filter.py 应触发 paths-filter 的 infra area
    （副作用：未来 PR 改 lint 自身时 verify-infra 矩阵会跑）。
    """
    import yaml  # type: ignore
    data = yaml.safe_load(PATHS_FILTER_GITHUB.read_text())
    infra_patterns = data.get("infra", []) or []
    has_lint = any("scripts/lint_paths_filter.py" in p for p in infra_patterns)
    # 同时验证 evidence 副本同步
    data_ev = yaml.safe_load(PATHS_FILTER_EVIDENCE.read_text())
    has_lint_ev = any(
        "scripts/lint_paths_filter.py" in p
        for p in (data_ev.get("infra", []) or [])
    )
    _record(
        "V8 lint_paths_filter.py 在 paths-filter infra area",
        has_lint and has_lint_ev,
        f"github={has_lint} evidence={has_lint_ev}",
    )


CHECKS = [
    v1_coverage_ratio_stdout,
    v2_weighted_strategy_picks_hot,
    v3_hot_path_fanout_no_truncate,
    v4_lint_byte_identical,
    v5_meta_tail_order,
    v6_regression_policy_actionlint_listed,
    v7_alpha_default_with_warn,
    v8_lint_script_in_paths_filter_infra,
]


def main() -> int:
    print("infra-014 V1-V8")
    for fn in CHECKS:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _record(fn.__name__, False, f"{type(e).__name__}: {e}")
    total = len(CHECKS) + (
        # 一些 V 内含子断言（V4b / V5b），_record 调多次。这里只统计入参。
        0
    )
    # 计 PASS：FAILS 名字唯一
    print()
    if FAILS:
        print(f"FAILED: {FAILS}")
        return 1
    print(f"PASS infra-014 V1-V8 (records: see above)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
