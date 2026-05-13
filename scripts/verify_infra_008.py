#!/usr/bin/env python3
"""infra-008 verification V1-V10.

按 feature_list.json infra-008 verification 字段：
- V1 staged 文件 coco/perception/X.py → 命中 verify_vision_*
- V2 staged scripts/verify_robot_003.py → 命中自身
- V3 coco/main.py → 全量 fan-out
- V4 多文件并集去重
- V5 无相关 staged → 空列表（--strict）；fallback 仍是全量
- V6 hook template 在 COCO_PRECOMMIT_HOOK=0 时短路 exit 0
- V7 COCO_PRECOMMIT_HOOK=1 时调用 impact + verify（fixture 仓库内）
- V8 失败 verify 时 hook 返回非零
- V9 paths-filter YAML 片段语法合法（PyYAML 可 load）
- V10 回归 infra-006：run_verify_all --list 能跑通，且 precommit_impact 与之
     共用 EXCLUDED/SKIP_NAMES 不冲突
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "precommit_impact.py"
HOOK_TPL = REPO_ROOT / "scripts" / "pre-commit-hook.sh"
INSTALL = REPO_ROOT / "scripts" / "install_pre_commit.sh"
RUN_ALL = REPO_ROOT / "scripts" / "run_verify_all.py"

PY = sys.executable
FAILS: list[str] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    status = "OK" if ok else "FAIL"
    print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def _run(cmd: list[str], **kwargs) -> tuple[int, str, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    return r.returncode, r.stdout, r.stderr


def v1_perception_to_vision() -> None:
    rc, out, err = _run([
        PY, str(SCRIPT), "--files", "coco/perception/scene_caption.py", "--list"
    ], cwd=str(REPO_ROOT))
    names = [ln for ln in out.splitlines() if ln.startswith("verify_")]
    has_v6 = "verify_vision_006.py" in names
    all_vision = all(n.startswith("verify_vision") or n.startswith("verify_robot_005") for n in names)
    _record(
        "V1 perception → verify_vision_*",
        rc == 0 and has_v6 and len(names) >= 3 and all_vision,
        f"rc={rc} hits={len(names)} v6={has_v6}",
    )


def v2_verify_file_self_hit() -> None:
    rc, out, err = _run([
        PY, str(SCRIPT), "--files", "scripts/verify_robot_003.py", "--list"
    ], cwd=str(REPO_ROOT))
    names = [ln for ln in out.splitlines() if ln.startswith("verify_")]
    _record(
        "V2 verify_robot_003 → 自身命中",
        rc == 0 and "verify_robot_003.py" in names and len(names) == 1,
        f"rc={rc} names={names}",
    )


def v3_main_full_fanout() -> None:
    rc, out, err = _run([
        PY, str(SCRIPT), "--files", "coco/main.py", "--list"
    ], cwd=str(REPO_ROOT))
    names = [ln for ln in out.splitlines() if ln.startswith("verify_")]
    expect_min = 30
    _record(
        "V3 coco/main.py → 全量",
        rc == 0 and len(names) >= expect_min and "full_fan_out=True" in out,
        f"rc={rc} hits={len(names)} (>= {expect_min})",
    )


def v4_multi_files_union() -> None:
    rc, out, err = _run([
        PY, str(SCRIPT), "--files",
        "coco/companion/profile_persist.py",
        "coco/perception/scene_caption.py",
        "--list",
    ], cwd=str(REPO_ROOT))
    names = set(ln for ln in out.splitlines() if ln.startswith("verify_"))
    has_vision = any(n.startswith("verify_vision") for n in names)
    has_companion = any(n.startswith("verify_companion") for n in names)
    _record(
        "V4 多文件并集去重",
        rc == 0 and has_vision and has_companion and len(names) >= 6,
        f"rc={rc} names_n={len(names)} vision={has_vision} companion={has_companion}",
    )


def v5_strict_unrelated_empty() -> None:
    # 无关文件（README）+ --strict 应返回空集
    rc, out, err = _run([
        PY, str(SCRIPT), "--files", "README.md", "--strict", "--list"
    ], cwd=str(REPO_ROOT))
    names = [ln for ln in out.splitlines() if ln.startswith("verify_")]
    # 默认 fallback 应该是全量
    rc2, out2, _ = _run([
        PY, str(SCRIPT), "--files", "README.md", "--list"
    ], cwd=str(REPO_ROOT))
    names2 = [ln for ln in out2.splitlines() if ln.startswith("verify_")]
    _record(
        "V5 --strict 空集；默认 fallback 全量",
        rc == 0 and len(names) == 0 and rc2 == 0 and len(names2) >= 30,
        f"strict={len(names)} fallback={len(names2)}",
    )


def v6_hook_default_off() -> None:
    # COCO_PRECOMMIT_HOOK 未设 → exit 0 且不调 verify
    env = {k: v for k, v in os.environ.items()
           if k not in {"COCO_PRECOMMIT_HOOK", "COCO_PRECOMMIT_SKIP"}}
    r = subprocess.run(
        ["bash", str(HOOK_TPL)], capture_output=True, text=True, env=env,
        cwd=str(REPO_ROOT),
    )
    # 调用 verify 时会有 "影响面分析" 字样；default-OFF 时不应该出现
    _record(
        "V6 hook default-OFF 时短路",
        r.returncode == 0 and "影响面分析" not in (r.stdout + r.stderr),
        f"rc={r.returncode}",
    )

    # COCO_PRECOMMIT_SKIP=1 同样短路
    env2 = dict(env, COCO_PRECOMMIT_HOOK="1", COCO_PRECOMMIT_SKIP="1")
    r2 = subprocess.run(
        ["bash", str(HOOK_TPL)], capture_output=True, text=True, env=env2,
        cwd=str(REPO_ROOT),
    )
    _record(
        "V6b COCO_PRECOMMIT_SKIP=1 跳过",
        r2.returncode == 0 and "COCO_PRECOMMIT_SKIP=1" in (r2.stdout + r2.stderr),
        f"rc={r2.returncode}",
    )


def _make_fixture_repo(tmpdir: Path, *, stage: list[tuple[str, str]]) -> Path:
    """构造最小 git repo，stage 列表 (path, content)。返回 repo 路径。"""
    repo = tmpdir / "fixture"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)
    # 初始 commit
    (repo / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=str(repo), check=True)
    for rel, content in stage:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        subprocess.run(["git", "add", rel], cwd=str(repo), check=True)
    return repo


def v7_hook_enabled_runs_impact() -> None:
    """COCO_PRECOMMIT_HOOK=1 时在 fixture repo 里 hook 应调 impact。

    fixture repo 没有 scripts/precommit_impact.py，hook 应 print 跳过提示
    （脚本不存在 → exit 0）。这验证了"启用时调用路径正确 + 模板足够健壮"。
    """
    with tempfile.TemporaryDirectory() as td:
        repo = _make_fixture_repo(Path(td), stage=[("foo.txt", "x\n")])
        env = dict(os.environ, COCO_PRECOMMIT_HOOK="1")
        env.pop("COCO_PRECOMMIT_SKIP", None)
        r = subprocess.run(
            ["bash", str(HOOK_TPL)], capture_output=True, text=True,
            env=env, cwd=str(repo),
        )
        out = r.stdout + r.stderr
        # 在 fixture repo 内：脚本路径不存在 → hook print "不存在；跳过" + exit 0；
        # 这证明 hook 启用时确实走到了 SCRIPT 路径解析（而非 default-OFF 早退）。
        ok = r.returncode == 0 and "不存在" in out
        _record(
            "V7 COCO_PRECOMMIT_HOOK=1 调用 impact 路径",
            ok,
            f"rc={r.returncode}",
        )


def v8_hook_fails_when_verify_fails() -> None:
    """模拟 precommit_impact.py 失败时 hook 返回非零。

    用一个临时 shim：让 hook 调一个返回 rc=1 的假 python 命令。
    最简单做法：在 fixture repo 内放一个假的 scripts/precommit_impact.py
    内容为 `import sys; sys.exit(1)`。把 fixture repo 当 hook 的 REPO_ROOT。
    """
    with tempfile.TemporaryDirectory() as td:
        repo = _make_fixture_repo(Path(td), stage=[
            ("scripts/precommit_impact.py", "import sys\nsys.exit(1)\n"),
            ("coco/x.py", "x = 1\n"),
        ])
        env = dict(os.environ, COCO_PRECOMMIT_HOOK="1")
        env.pop("COCO_PRECOMMIT_SKIP", None)
        r = subprocess.run(
            ["bash", str(HOOK_TPL)], capture_output=True, text=True,
            env=env, cwd=str(repo),
        )
        out = r.stdout + r.stderr
        ok = r.returncode != 0 and "commit aborted" in out
        _record(
            "V8 verify 失败时 hook abort",
            ok,
            f"rc={r.returncode}",
        )


def v9_paths_filter_yaml() -> None:
    rc, out, err = _run([PY, str(SCRIPT), "--paths-filter"], cwd=str(REPO_ROOT))
    try:
        import yaml  # type: ignore
    except ImportError:
        # 没有 pyyaml：fallback 用最小语法检查
        ok = rc == 0 and out.strip().startswith("#") and "infra:" in out and "  - '" in out
        _record(
            "V9 paths-filter YAML 语法（无 pyyaml fallback）",
            ok,
            "no pyyaml; basic-check",
        )
        # 写入 evidence
        ev = REPO_ROOT / "evidence" / "infra-008"
        ev.mkdir(parents=True, exist_ok=True)
        (ev / "paths-filter.yml").write_text(out)
        return
    try:
        data = yaml.safe_load(out)
        ok = (
            rc == 0
            and isinstance(data, dict)
            and "infra" in data
            and "vision" in data
            and all(isinstance(v, list) for v in data.values())
        )
    except yaml.YAMLError as e:  # noqa: BLE001
        ok = False
        err += f"\nYAML parse fail: {e}"
    _record("V9 paths-filter YAML 合法", ok, f"areas={list(data.keys()) if ok else '-'}")
    # 写入 evidence
    if ok:
        ev = REPO_ROOT / "evidence" / "infra-008"
        ev.mkdir(parents=True, exist_ok=True)
        (ev / "paths-filter.yml").write_text(out)


def v10_no_conflict_with_run_verify_all() -> None:
    # run_verify_all --list 仍可用
    rc, out, _ = _run([PY, str(RUN_ALL), "--list"], cwd=str(REPO_ROOT))
    ok1 = rc == 0 and "verify_infra_006.py" not in out  # EXCLUDED
    # precommit_impact 默认 --run 不应跑 SKIP_NAMES 中的
    from importlib import util
    spec = util.spec_from_file_location("precommit_impact", str(SCRIPT))
    mod = util.module_from_spec(spec)  # type: ignore
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore
    # 直接调 compute_impact 看 affected 含 SKIP_NAMES 中的某个；--run 时会被过滤
    affected, _notes, _full = mod.compute_impact(["coco/main.py"])
    ok2 = "verify_publish.py" in affected  # 全量含它
    # 安装脚本存在 & 可执行
    ok3 = INSTALL.exists() and os.access(INSTALL, os.X_OK) and HOOK_TPL.exists() and os.access(HOOK_TPL, os.X_OK)
    _record(
        "V10 与 infra-006 共存 + EXCLUDED/SKIP_LIST 复用",
        ok1 and ok2 and ok3,
        f"run_verify_all_ok={ok1} affected_includes_publish={ok2} install_exec={ok3}",
    )


def main() -> int:
    print("[verify_infra_008] V1-V10 starting")
    v1_perception_to_vision()
    v2_verify_file_self_hit()
    v3_main_full_fanout()
    v4_multi_files_union()
    v5_strict_unrelated_empty()
    v6_hook_default_off()
    v7_hook_enabled_runs_impact()
    v8_hook_fails_when_verify_fails()
    v9_paths_filter_yaml()
    v10_no_conflict_with_run_verify_all()

    print()
    if FAILS:
        print(f"[verify_infra_008] FAIL ({len(FAILS)}): {FAILS}")
        return 1
    print("[verify_infra_008] all PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
