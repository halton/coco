#!/usr/bin/env python3
"""infra-014-fu-2 verification V1-V8.

按 description（companion-014 caveat #5 派生）：
  V1 helper 存在 + 接口签名正确（restore_unrelated_evidence(target, dry_run, repo_root)）
  V2 dry_run=True 列出待 restore 文件，不动 fake repo（基于 tmp git repo）
  V3 dry_run=False 实际执行 restore，文件回到 HEAD 状态
  V4 evidence/<target_feature_id>/ 路径不被 restore（保留本 feature 自己的产出）
  V5 不破回归：verify_infra_014 / verify_infra_014_fu_1 / verify_infra_011 / verify_infra_013 / verify_infra_008
  V6 ./init.sh COCO_CI=1 smoke 不破
  V7 AST/grep marker：scripts/restore_unrelated_evidence.py 有 ``def restore_unrelated_evidence``
     + scripts/run_verify_all.py 有 ``--restore-unrelated`` flag
  V8 docs/regression-policy.md 已加 evidence-restore policy 段
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts" / "restore_unrelated_evidence.py"
RUN_VERIFY_ALL = REPO_ROOT / "scripts" / "run_verify_all.py"
REGRESSION_DOC = REPO_ROOT / "docs" / "regression-policy.md"

PY = sys.executable
FAILS: list[str] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    status = "OK" if ok else "FAIL"
    print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(repo),
        capture_output=True, text=True, check=True,
    )


def _make_fake_repo() -> Path:
    """构造一个 tmp git repo：HEAD 含 evidence/foo/a.txt + evidence/bar/b.txt 等
    initial commit；返回 repo 路径。caller 负责删除。"""
    tmp = Path(tempfile.mkdtemp(prefix="coco-infra-014-fu-2-"))
    _git(tmp, "init", "-q", "-b", "main")
    _git(tmp, "config", "user.email", "test@example.com")
    _git(tmp, "config", "user.name", "test")
    # 三个 feature evidence 目录
    for sub in ("foo", "bar", "baz"):
        d = tmp / "evidence" / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "summary.json").write_text(f'{{"feature":"{sub}","ok":true}}\n')
    # 仓库根额外加一个非 evidence 文件，确保 helper 不会去碰它
    (tmp / "README.md").write_text("# fake repo\n")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-q", "-m", "init")
    return tmp


def _import_helper():
    """从 scripts/ 动态 import restore_unrelated_evidence 模块。"""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import restore_unrelated_evidence as mod  # type: ignore
        return mod
    finally:
        # 不污染 sys.path
        try:
            sys.path.remove(str(REPO_ROOT / "scripts"))
        except ValueError:
            pass


# -------------------- V1 --------------------
def v1_helper_exists_with_signature() -> None:
    """helper 文件存在 + 函数签名 (target_feature_id, *, dry_run=False, repo_root=None)。"""
    if not HELPER.is_file():
        _record("V1 helper 存在", False, f"{HELPER} 不存在")
        return
    src = HELPER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "restore_unrelated_evidence"),
        None,
    )
    if fn is None:
        _record("V1 helper 签名", False, "未找到 def restore_unrelated_evidence")
        return
    pos = [a.arg for a in fn.args.args]
    kwonly = [a.arg for a in fn.args.kwonlyargs]
    sig_ok = pos == ["target_feature_id"] and "dry_run" in kwonly and "repo_root" in kwonly
    _record(
        "V1 helper 存在 + 接口签名",
        sig_ok,
        f"pos={pos} kwonly={kwonly}",
    )


# -------------------- V2 --------------------
def v2_dry_run_lists_without_changing() -> None:
    """构造 fake repo：改 evidence/foo/summary.json + evidence/bar/summary.json，
    target=foo，dry_run=True 应列出 evidence/bar/summary.json 但不实际还原。"""
    mod = _import_helper()
    repo = _make_fake_repo()
    try:
        (repo / "evidence" / "foo" / "summary.json").write_text("DIRTY-FOO\n")
        (repo / "evidence" / "bar" / "summary.json").write_text("DIRTY-BAR\n")
        restored = mod.restore_unrelated_evidence(
            "foo", dry_run=True, repo_root=repo,
        )
        # 应该列出 bar 的，不列 foo 的
        listed_bar = any("bar/summary.json" in p for p in restored)
        listed_foo = any("foo/summary.json" in p for p in restored)
        # dry_run 不动文件
        bar_still_dirty = (repo / "evidence" / "bar" / "summary.json").read_text() == "DIRTY-BAR\n"
        foo_still_dirty = (repo / "evidence" / "foo" / "summary.json").read_text() == "DIRTY-FOO\n"
        ok = listed_bar and not listed_foo and bar_still_dirty and foo_still_dirty
        _record(
            "V2 dry_run=True 列出不还原",
            ok,
            f"restored={restored} bar_dirty={bar_still_dirty} foo_dirty={foo_still_dirty}",
        )
    finally:
        shutil.rmtree(repo, ignore_errors=True)


# -------------------- V3 --------------------
def v3_real_run_restores() -> None:
    """dry_run=False 实际 restore：bar/summary.json 回到 HEAD 内容。"""
    mod = _import_helper()
    repo = _make_fake_repo()
    try:
        (repo / "evidence" / "bar" / "summary.json").write_text("DIRTY-BAR\n")
        original_bar = '{"feature":"bar","ok":true}\n'
        restored = mod.restore_unrelated_evidence(
            "foo", dry_run=False, repo_root=repo,
        )
        bar_content = (repo / "evidence" / "bar" / "summary.json").read_text()
        ok = (
            any("bar/summary.json" in p for p in restored)
            and bar_content == original_bar
        )
        _record(
            "V3 dry_run=False 实际 restore",
            ok,
            f"restored={restored} bar_restored={bar_content==original_bar}",
        )
    finally:
        shutil.rmtree(repo, ignore_errors=True)


# -------------------- V4 --------------------
def v4_target_feature_protected() -> None:
    """target 自己的 evidence 不被 restore — 即使脏也保留。"""
    mod = _import_helper()
    repo = _make_fake_repo()
    try:
        (repo / "evidence" / "foo" / "summary.json").write_text("FOO-NEW\n")
        (repo / "evidence" / "bar" / "summary.json").write_text("BAR-DIRTY\n")
        restored = mod.restore_unrelated_evidence(
            "foo", dry_run=False, repo_root=repo,
        )
        foo_kept = (repo / "evidence" / "foo" / "summary.json").read_text() == "FOO-NEW\n"
        bar_restored = (
            (repo / "evidence" / "bar" / "summary.json").read_text()
            == '{"feature":"bar","ok":true}\n'
        )
        no_foo_in_restored = not any("foo/" in p for p in restored)
        ok = foo_kept and bar_restored and no_foo_in_restored
        _record(
            "V4 target evidence 不被 restore",
            ok,
            f"foo_kept={foo_kept} bar_restored={bar_restored} restored={restored}",
        )
    finally:
        shutil.rmtree(repo, ignore_errors=True)


# -------------------- V5 --------------------
def v5_regression_no_break() -> None:
    """跑 verify_infra_014 / fu_1 / 011 / 013 / 008 全 PASS。

    注意：verify_infra_008 自身会 regenerate evidence/infra-008/paths-filter.yml，
    可能与本 feature 的双副本 sync 冲突。跑完后用本 feature 自己的 helper
    （extra_keep_paths）保护 paths-filter.yml，证明 helper eats its own dogfood。
    """
    targets = [
        "scripts/verify_infra_014.py",
        "scripts/verify_infra_014_fu_1.py",
        "scripts/verify_infra_011.py",
        "scripts/verify_infra_013.py",
        "scripts/verify_infra_008.py",
    ]
    # 跑前先 sync 双副本（infra-014-fu-2 改动，必须保持 byte-identical
    # 才能让 verify_infra_014 V4 / verify_infra_011 V8 PASS）
    src_text = (REPO_ROOT / ".github" / "paths-filter.yml").read_text(encoding="utf-8")
    (REPO_ROOT / "evidence" / "infra-008" / "paths-filter.yml").write_text(
        src_text, encoding="utf-8",
    )
    failed = []
    for rel in targets:
        p = REPO_ROOT / rel
        if not p.is_file():
            failed.append(f"{rel}=missing")
            continue
        # 跑每个 verify 前 re-sync（前一个 verify 可能 dirty 它）
        (REPO_ROOT / "evidence" / "infra-008" / "paths-filter.yml").write_text(
            src_text, encoding="utf-8",
        )
        r = subprocess.run(
            [PY, str(p)], cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            failed.append(f"{rel}=rc{r.returncode}")
    # 跑完后 self-clean evidence/infra-008/paths-filter.yml（infra-008 verify
    # 会重写它），用本 feature 自己的 helper —— 证明 dogfood。我们把它纳入
    # extra_keep_paths 防止被 restore（本 feature commit 需要它）。
    try:
        mod = _import_helper()
        mod.restore_unrelated_evidence(
            "infra-014-fu-2",
            dry_run=False,
            repo_root=REPO_ROOT,
            extra_keep_paths=["evidence/infra-008/paths-filter.yml"],
        )
        # 然后把 .github 的 paths-filter 内容回写到 evidence/infra-008/，
        # 维持双副本 byte-identical（infra-014-fu-2 commit 需要这一致性）。
        src = (REPO_ROOT / ".github" / "paths-filter.yml").read_text(encoding="utf-8")
        (REPO_ROOT / "evidence" / "infra-008" / "paths-filter.yml").write_text(
            src, encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        # 本身 V5 验回归 PASS 即可，cleanup 失败不算 V5 fail（V1-V4 已验 helper 行为）
        print(f"    [V5 note] cleanup raised: {type(e).__name__}: {e}")
    ok = not failed
    _record(
        "V5 回归 verify_infra_014 / fu_1 / 011 / 013 / 008 全 PASS",
        ok,
        f"failed={failed}" if failed else f"ran {len(targets)}",
    )


# -------------------- V6 --------------------
def v6_smoke_no_break() -> None:
    init = REPO_ROOT / "init.sh"
    if not init.is_file():
        _record("V6 ./init.sh smoke", False, "init.sh 不存在")
        return
    env = os.environ.copy()
    env["COCO_CI"] = "1"
    r = subprocess.run(
        ["bash", str(init)], capture_output=True, text=True,
        env=env, cwd=str(REPO_ROOT), timeout=300,
    )
    ok = r.returncode == 0
    _record("V6 ./init.sh COCO_CI=1 smoke PASS", ok, f"rc={r.returncode}")
    if not ok:
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-30:])
        print(f"    --- init.sh tail ---\n{tail}\n    --- end ---")


# -------------------- V7 --------------------
def v7_markers() -> None:
    """AST/grep marker：helper 函数名 + run_verify_all.py 的 --restore-unrelated flag。"""
    helper_src = HELPER.read_text(encoding="utf-8")
    rva_src = RUN_VERIFY_ALL.read_text(encoding="utf-8")
    has_helper_def = "def restore_unrelated_evidence" in helper_src
    has_flag = "--restore-unrelated" in rva_src
    has_call = "restore_unrelated_evidence(" in rva_src
    ok = has_helper_def and has_flag and has_call
    _record(
        "V7 AST/grep marker (helper def + run_verify_all flag + 调用)",
        ok,
        f"helper_def={has_helper_def} flag={has_flag} call={has_call}",
    )


# -------------------- V8 --------------------
def v8_docs_updated() -> None:
    """docs/regression-policy.md 已加 evidence-restore policy 段。"""
    text = REGRESSION_DOC.read_text(encoding="utf-8")
    has_section = "infra-014-fu-2" in text or "restore_unrelated_evidence" in text
    has_helper_ref = "restore_unrelated_evidence" in text
    has_flag_ref = "--restore-unrelated" in text or "restore-unrelated" in text
    ok = has_section and has_helper_ref and has_flag_ref
    _record(
        "V8 docs/regression-policy.md 已加 evidence-restore 段",
        ok,
        f"section={has_section} helper_ref={has_helper_ref} flag_ref={has_flag_ref}",
    )


CHECKS = [
    v1_helper_exists_with_signature,
    v2_dry_run_lists_without_changing,
    v3_real_run_restores,
    v4_target_feature_protected,
    v5_regression_no_break,
    v6_smoke_no_break,
    v7_markers,
    v8_docs_updated,
]


def main() -> int:
    print("infra-014-fu-2 V1-V8")
    for fn in CHECKS:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _record(fn.__name__, False, f"{type(e).__name__}: {e}")
    print()
    if FAILS:
        print(f"FAILED: {FAILS}")
        return 1
    print(f"PASS infra-014-fu-2 V1-V8 ({len(CHECKS)}/{len(CHECKS)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
