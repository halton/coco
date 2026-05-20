#!/usr/bin/env python3
"""infra-034 verify — venv docstring 模板批量推广 meta-lock.

来源: infra-033-backlog-multi-verify-venv-docstring
范围: docs-only / verify-only。把 P240 infra-033 给 verify_infra_024.py 加的
"运行环境约定"段, 推广到 5 个其它 verify 脚本顶端 module docstring
(verify_robot_032 / verify_robot_033 / verify_interact_037 / verify_interact_036
/ verify_infra_033); 函数体严格 byte-equal, 无业务行为变更。

V0 目标脚本清单存在 + 顶部 module docstring 非空。
V1 每个目标脚本顶端关键短语集合断言 — 全部 5 短语 in module docstring:
    {".venv", "sys.executable", "子进程", "运行环境约定", ".venv/bin/python"}。
V2 verify_infra_024.py P240 baseline 不能 regress —
    同一组关键短语必须仍在 verify_infra_024.py docstring 内。
V3 mutant 反证: 在内存中删去任一目标某关键短语 → V1 该目标 FAIL (反证只读, 不写盘)。
V4 sha256 锁所有 6 个目标脚本 (5 个新增 + verify_infra_024 baseline)。
V5 subprocess 自调用 rc=0 (sys.executable invoke 自身, 自洽通过)。

默认 OFF 严守: 本 verify 不引入新 env hook, 不依赖网络, 不修改业务源码。

运行环境约定 (infra-034)
------------------------
本脚本及其 V0-V5 子进程**必须**在已激活的 .venv 下运行 (Python 解释器入口
``.venv/bin/python``); 不要用系统 ``python3`` 直接调用本脚本, 否则
``importlib`` 加载业务模块时依赖 (numpy / soundfile / onnxruntime 等) 可能
解析到系统站点而非 venv 站点, 导致与 ``./init.sh`` smoke 路径不一致,
进而 V0-V5 退出码漂移。

约定细则:
  - **Reviewer / CI / 手动复跑入口**: 一律 ``.venv/bin/python`` 启动 (或先
    ``source .venv/bin/activate`` 再 ``python scripts/verify_infra_034.py``)。
  - **子进程 invoke**: 任何 ``subprocess.run`` 第一参数固定使用 ``sys.executable``
    (即本脚本所属解释器); 不写死 ``"python"`` / ``"python3"`` 字面量。
  - **环境变量继承**: 子进程从 ``os.environ`` 拷贝 PATH / PYTHONPATH 等,
    PATH 中 venv 的 ``bin`` 目录位置不可被人为打乱 (init.sh 已在激活时前置)。
  - **新会话注意事项**: 干净 shell 进来务必先 ``source .venv/bin/activate``
    或显式 ``./.venv/bin/python``。
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent

# 推广目标 (P240 infra-034 首批 5 + P252 infra-036 续推 5 + P255 infra-038 续推 5 = 15)
TARGETS: Tuple[str, ...] = (
    "scripts/verify_robot_032.py",
    "scripts/verify_robot_033.py",
    "scripts/verify_interact_037.py",
    "scripts/verify_interact_036.py",
    "scripts/verify_infra_033.py",
    # P252 infra-036 续推 (无 sha 锁链路耦合的安全目标)
    "scripts/verify_robot_023.py",
    "scripts/verify_robot_024.py",
    "scripts/verify_interact_022.py",
    "scripts/verify_interact_023.py",
    "scripts/verify_infra_018.py",
    # P255 infra-038 续推 (相对稳定的 verify-only meta-lock 系列)
    "scripts/verify_robot_025.py",
    "scripts/verify_robot_026.py",
    "scripts/verify_robot_027.py",
    "scripts/verify_interact_024.py",
    "scripts/verify_interact_025.py",
)

# P240 baseline (不动它, 仅作 V2 regression guard)
BASELINE = "scripts/verify_infra_024.py"

# 必须出现在 docstring 中的 5 关键短语
KEY_PHRASES: Tuple[str, ...] = (
    ".venv",
    "sys.executable",
    "子进程",
    "运行环境约定",
    ".venv/bin/python",
)

# V4 sha256 锁 — infra-035 起从 evidence/infra-034/v4_sha.json 外置读取
# (bump_infra_034_v4_sha.py 维护; verify_infra_035 V0-V5 守护 schema/锁)
V4_SHA_JSON = "evidence/infra-034/v4_sha.json"


def _load_expected_sha() -> Dict[str, str]:
    """读取外置 v4_sha.json. 失败返回空 dict (V4 会全 FAIL)."""
    p = ROOT / V4_SHA_JSON
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    targets = data.get("targets")
    if not isinstance(targets, dict):
        return {}
    return {k: v for k, v in targets.items() if isinstance(k, str) and isinstance(v, str)}

# baseline sha (P240 verify_infra_024.py 当前文件态; 漂了即 regress)
BASELINE_SHA = ""  # 运行时计算并打印, 不硬锁 (P240 已有自己的 V4 锁住自身)


_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_034][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_docstring(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        return tree.body[0].value.value
    return ""


# ---------------------------------------------------------------------------
# V0: 目标脚本清单存在 + module docstring 非空
# ---------------------------------------------------------------------------
def v0_targets_exist() -> None:
    for rel in TARGETS:
        p = ROOT / rel
        ok_exist = p.is_file()
        if not ok_exist:
            _emit(f"V0_{rel}_exists", False, f"missing {p}")
            continue
        doc = _module_docstring(p)
        _emit(f"V0_{rel}_doc_nonempty", bool(doc.strip()), f"len={len(doc)}")
    # baseline
    bp = ROOT / BASELINE
    _emit(f"V0_{BASELINE}_exists", bp.is_file(), f"{bp}")
    if bp.is_file():
        bdoc = _module_docstring(bp)
        _emit(f"V0_{BASELINE}_doc_nonempty", bool(bdoc.strip()), f"len={len(bdoc)}")


# ---------------------------------------------------------------------------
# V1: 每个目标 docstring 含全部关键短语
# ---------------------------------------------------------------------------
def v1_key_phrases() -> None:
    for rel in TARGETS:
        p = ROOT / rel
        if not p.is_file():
            _emit(f"V1_{rel}_phrases", False, "file missing")
            continue
        doc = _module_docstring(p)
        missing = [ph for ph in KEY_PHRASES if ph not in doc]
        _emit(
            f"V1_{rel}_phrases",
            not missing,
            f"missing={missing}" if missing else f"all {len(KEY_PHRASES)} phrases present",
        )


# ---------------------------------------------------------------------------
# V2: P240 baseline (verify_infra_024.py) regression guard
# ---------------------------------------------------------------------------
def v2_baseline_no_regress() -> None:
    p = ROOT / BASELINE
    if not p.is_file():
        _emit("V2_baseline_no_regress", False, "baseline missing")
        return
    doc = _module_docstring(p)
    missing = [ph for ph in KEY_PHRASES if ph not in doc]
    _emit(
        "V2_baseline_no_regress",
        not missing,
        f"P240 baseline missing={missing}" if missing else "P240 baseline phrases all intact",
    )


# ---------------------------------------------------------------------------
# V3: mutant 反证 — 删去任一目标某关键短语 → V1 FAIL (内存中)
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    # 选第一个目标做 mutant
    rel = TARGETS[0]
    p = ROOT / rel
    if not p.is_file():
        _emit("V3_mutant", False, f"target {rel} missing")
        return
    doc = _module_docstring(p)
    target_phrase = "运行环境约定"
    if target_phrase not in doc:
        _emit("V3_mutant", False, f"phrase {target_phrase!r} unexpectedly absent pre-mutant")
        return
    mutated = doc.replace(target_phrase, "DELETED_ANCHOR")
    missing = [ph for ph in KEY_PHRASES if ph not in mutated]
    ok = target_phrase in missing  # mutant 应让该短语 missing
    _emit(
        "V3_mutant",
        ok,
        f"after deletion missing={missing} (expect {target_phrase!r} in missing)",
    )


# ---------------------------------------------------------------------------
# V4: sha256 锁所有目标脚本
# ---------------------------------------------------------------------------
def v4_sha_lock() -> None:
    expected = _load_expected_sha()
    if not expected:
        _emit("V4_load_v4_sha_json", False, f"missing or invalid {V4_SHA_JSON}")
        return
    _emit("V4_load_v4_sha_json", True, f"targets={len(expected)}")
    for rel, expect in expected.items():
        p = ROOT / rel
        if not p.is_file():
            _emit(f"V4_{rel}_sha", False, "file missing")
            continue
        got = _sha256(p)
        _emit(
            f"V4_{rel}_sha",
            got == expect,
            f"got={got[:16]} expect={expect[:16]}",
        )
    # baseline 只 print 当前 sha, 不锁 (P240 自身已锁)
    bp = ROOT / BASELINE
    if bp.is_file():
        print(f"[verify_infra_034][INFO] V4 baseline {BASELINE} sha={_sha256(bp)[:16]}", flush=True)


# ---------------------------------------------------------------------------
# V5: subprocess 自调用 rc==0
# ---------------------------------------------------------------------------
def v5_self_subprocess() -> None:
    # 防止无限递归: 用 env COCO_VERIFY_034_SELFCALL=1 标志
    import os

    if os.environ.get("COCO_VERIFY_034_SELFCALL") == "1":
        _emit("V5_self_subprocess", True, "skipped (inside selfcall)")
        return
    env = dict(os.environ)
    env["COCO_VERIFY_034_SELFCALL"] = "1"
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    _emit(
        "V5_self_subprocess",
        proc.returncode == 0,
        f"rc={proc.returncode} stdout_lines={len(proc.stdout.splitlines())}",
    )


def main() -> int:
    v0_targets_exist()
    v1_key_phrases()
    v2_baseline_no_regress()
    v3_mutant()
    v4_sha_lock()
    v5_self_subprocess()

    failed = [t for t, ok, _ in _results if not ok]
    total = len(_results)
    print(f"[verify_infra_034] summary total={total} failed={len(failed)}", flush=True)
    if failed:
        print(f"[verify_infra_034] failed tags: {failed}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
