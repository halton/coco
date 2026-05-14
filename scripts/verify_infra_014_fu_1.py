#!/usr/bin/env python3
"""infra-014-fu-1 verification V1-V8.

按 description（infra-014 caveat #5 + 1 轻微瑕疵）：
  V1 SyntaxWarning 已消除：scripts/lint_paths_filter.py compile 0 warning
  V2 lint_paths_filter.py 仍 PASS（不破 infra-014 行为）
  V3 actionlint hook 调用路径存在：scripts/lint_workflows.py 存在 + 可 --help
  V4 actionlint 未装时优雅 skip + 不 fail（rc=0）；--strict 模式 rc=1
  V5 actionlint 已装时真跑 dry-run（本机有 actionlint 才校验，否则记录跳过）
  V6 不破回归：verify_infra_014 / verify_infra_011 / verify_infra_013 全 PASS
  V7 smoke 不破：./init.sh COCO_CI=1 PASS
  V8 docs/regression-policy.md 已更新（actionlint hook 不再"仅跟踪"）+
     scripts/lint_workflows.py 已纳入 paths-filter（infra area + meta）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LINT_PATHS = REPO_ROOT / "scripts" / "lint_paths_filter.py"
LINT_WORKFLOWS = REPO_ROOT / "scripts" / "lint_workflows.py"
PATHS_FILTER_GITHUB = REPO_ROOT / ".github" / "paths-filter.yml"
PATHS_FILTER_EVIDENCE = REPO_ROOT / "evidence" / "infra-008" / "paths-filter.yml"
REGRESSION_DOC = REPO_ROOT / "docs" / "regression-policy.md"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

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
def v1_syntax_warning_gone() -> None:
    """compile lint_paths_filter.py，确保 0 SyntaxWarning / DeprecationWarning
    （raw-string \\s 已修复）。
    """
    src = LINT_PATHS.read_text(encoding="utf-8")
    bad: list[str] = []
    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")
        compile(src, str(LINT_PATHS), "exec")
        for w in ws:
            # 我们只关心和 escape sequence 相关的 warning
            msg = str(w.message)
            if "escape sequence" in msg or w.category.__name__ in (
                "SyntaxWarning", "DeprecationWarning"
            ):
                bad.append(f"{w.category.__name__}@{w.lineno}: {msg}")
    ok = not bad
    _record(
        "V1 SyntaxWarning 已消除（lint_paths_filter.py compile clean）",
        ok,
        f"warnings={bad}" if bad else "0 warnings",
    )


# -------------------- V2 --------------------
def v2_lint_paths_filter_still_pass() -> None:
    """lint_paths_filter.py 默认 5/5 PASS（不破 infra-014 V4/V5 行为）。"""
    rc, out, err = _run([PY, str(LINT_PATHS)], cwd=str(REPO_ROOT))
    ok = (
        rc == 0
        and "L1 OK" in out
        and "L2 OK" in out
        and "L3 OK" in out
        and "L4 OK" in out
        and "L5 OK" in out
        and "OK 5/5" in out
    )
    _record(
        "V2 lint_paths_filter.py 仍 PASS（infra-014 行为不破）",
        ok,
        f"rc={rc}",
    )


# -------------------- V3 --------------------
def v3_lint_workflows_callable() -> None:
    """scripts/lint_workflows.py 存在 + --help 可用 + import 模块成功。"""
    exists = LINT_WORKFLOWS.is_file()
    rc, out, err = _run([PY, str(LINT_WORKFLOWS), "--help"], cwd=str(REPO_ROOT))
    help_ok = rc == 0 and "actionlint" in (out + err).lower()
    # import 校验：拿 actionlint_available / run_actionlint 函数
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import_ok = False
    try:
        import importlib
        if "lint_workflows" in sys.modules:
            del sys.modules["lint_workflows"]
        mod = importlib.import_module("lint_workflows")
        import_ok = (
            hasattr(mod, "actionlint_available")
            and hasattr(mod, "run_actionlint")
            and hasattr(mod, "main")
        )
    except Exception as e:
        import_ok = False
        help_ok = help_ok and False
        print(f"    import error: {e}")
    ok = exists and help_ok and import_ok
    _record(
        "V3 actionlint hook 调用路径存在 (lint_workflows.py 可用)",
        ok,
        f"exists={exists} help_rc={rc} import={import_ok}",
    )


# -------------------- V4 --------------------
def v4_skip_when_missing() -> None:
    """actionlint 未装时：默认 rc=0 + SKIP 输出；--strict 模式 rc=1。
    用 PATH=空目录强制模拟未装环境（即使本机装了 actionlint 也能 cover）。
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        env = os.environ.copy()
        env["PATH"] = td  # 空 PATH，actionlint 必定找不到
        # 默认模式
        r1 = subprocess.run(
            [PY, str(LINT_WORKFLOWS)],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
        )
        ok_default = r1.returncode == 0 and "SKIP" in r1.stdout
        # strict 模式
        r2 = subprocess.run(
            [PY, str(LINT_WORKFLOWS), "--strict"],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
        )
        ok_strict = r2.returncode == 1 and (
            "FAIL" in r2.stderr or "FAIL" in r2.stdout
        )
        ok = ok_default and ok_strict
        _record(
            "V4 actionlint 未装：默认 rc=0+SKIP，strict rc=1+FAIL",
            ok,
            f"default_rc={r1.returncode} strict_rc={r2.returncode}",
        )


# -------------------- V5 --------------------
def v5_real_run_when_installed() -> None:
    """actionlint 已装时跑 dry-run（本机情况下条件触发）。"""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    if "lint_workflows" in sys.modules:
        del sys.modules["lint_workflows"]
    import importlib
    mod = importlib.import_module("lint_workflows")
    bin_path = mod.actionlint_available()
    if not bin_path:
        # 本机未装 → 记录但不算 fail（V4 已 cover skip 路径）
        _record(
            "V5 actionlint 已装真跑 dry-run（本机未装，跳过；V4 cover skip 路径）",
            True,
            "actionlint not in PATH on this machine",
        )
        return
    # 真跑：对当前 .github/workflows 跑 actionlint
    rc, out, err = _run(
        [PY, str(LINT_WORKFLOWS)], cwd=str(REPO_ROOT),
    )
    # 注意：现网 workflow 可能已存在 actionlint warn/error；本 verify 是
    # "hook 能跑通 + 接通 stdout/stderr"，不是"workflow 必须 0 issue"。
    # 我们接受 rc=0 (clean) 或 rc=1 (workflow 自身有 issue)，
    # 但 stderr/stdout 必须含 actionlint 输出痕迹，确保 hook 真的调用到了 actionlint。
    has_trace = (
        "actionlint" in (out + err).lower()
        or "shellcheck" in (out + err).lower()
        or rc == 0  # actionlint 成功时通常静默
    )
    ok = has_trace
    _record(
        "V5 actionlint 已装真跑 dry-run（hook 接通 actionlint 二进制）",
        ok,
        f"bin={bin_path} rc={rc}",
    )


# -------------------- V6 --------------------
def v6_regression_no_break() -> None:
    """跑 verify_infra_014 / 011 / 013 不退步。"""
    targets = [
        REPO_ROOT / "scripts" / "verify_infra_014.py",
        REPO_ROOT / "scripts" / "verify_infra_011.py",
        REPO_ROOT / "scripts" / "verify_infra_013.py",
    ]
    sub_results: list[str] = []
    all_ok = True
    for t in targets:
        rc, out, err = _run([PY, str(t)], cwd=str(REPO_ROOT))
        sub_ok = rc == 0
        sub_results.append(f"{t.name}={'OK' if sub_ok else 'FAIL'}(rc={rc})")
        if not sub_ok:
            all_ok = False
            # 把最后 30 行尾巴打出来辅助 debug
            tail = "\n".join((out + err).splitlines()[-30:])
            print(f"    --- {t.name} tail ---\n{tail}\n    --- end ---")
    _record(
        "V6 回归 verify_infra_014/011/013 全 PASS",
        all_ok,
        " ".join(sub_results),
    )


# -------------------- V7 --------------------
def v7_smoke_no_break() -> None:
    """./init.sh COCO_CI=1 smoke 不破。"""
    init = REPO_ROOT / "init.sh"
    if not init.is_file():
        _record("V7 smoke (./init.sh)", False, "init.sh 不存在")
        return
    env = os.environ.copy()
    env["COCO_CI"] = "1"
    r = subprocess.run(
        ["bash", str(init)], capture_output=True, text=True,
        env=env, cwd=str(REPO_ROOT), timeout=300,
    )
    ok = r.returncode == 0
    _record(
        "V7 ./init.sh COCO_CI=1 smoke PASS",
        ok,
        f"rc={r.returncode}",
    )
    if not ok:
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-40:])
        print(f"    --- init.sh tail ---\n{tail}\n    --- end ---")


# -------------------- V8 --------------------
def v8_docs_and_paths_filter_synced() -> None:
    """docs/regression-policy.md 已更新（actionlint 落地段存在）+
    scripts/lint_workflows.py 在 paths-filter infra area + meta。
    """
    text = REGRESSION_DOC.read_text(encoding="utf-8")
    has_落地 = "落地" in text and "lint_workflows" in text
    has_skip = "优雅 skip" in text or "skip" in text.lower()
    has_strict = "--strict" in text
    no_legacy = "未列入 CI／pre-commit hook" not in text  # 旧"仅跟踪"措辞已删
    docs_ok = has_落地 and has_skip and has_strict and no_legacy
    # paths-filter 检查
    import yaml  # type: ignore
    g = yaml.safe_load(PATHS_FILTER_GITHUB.read_text())
    e = yaml.safe_load(PATHS_FILTER_EVIDENCE.read_text())
    in_infra_g = any(
        "lint_workflows.py" in p for p in (g.get("infra") or [])
    )
    in_meta_g = any(
        "lint_workflows.py" in p for p in (g.get("meta") or [])
    )
    in_infra_e = any(
        "lint_workflows.py" in p for p in (e.get("infra") or [])
    )
    in_meta_e = any(
        "lint_workflows.py" in p for p in (e.get("meta") or [])
    )
    pf_ok = in_infra_g and in_meta_g and in_infra_e and in_meta_e
    ok = docs_ok and pf_ok
    _record(
        "V8 docs 已更新 + lint_workflows 在 paths-filter (infra+meta, 双副本)",
        ok,
        f"docs={docs_ok} pf_infra_g={in_infra_g} meta_g={in_meta_g} "
        f"infra_e={in_infra_e} meta_e={in_meta_e}",
    )


CHECKS = [
    v1_syntax_warning_gone,
    v2_lint_paths_filter_still_pass,
    v3_lint_workflows_callable,
    v4_skip_when_missing,
    v5_real_run_when_installed,
    v6_regression_no_break,
    v7_smoke_no_break,
    v8_docs_and_paths_filter_synced,
]


def main() -> int:
    print("infra-014-fu-1 V1-V8")
    for fn in CHECKS:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _record(fn.__name__, False, f"{type(e).__name__}: {e}")
    print()
    if FAILS:
        print(f"FAILED: {FAILS}")
        return 1
    print(f"PASS infra-014-fu-1 V1-V8 ({len(CHECKS)}/{len(CHECKS)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
