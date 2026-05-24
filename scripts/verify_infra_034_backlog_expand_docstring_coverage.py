#!/usr/bin/env python3
"""infra-034-backlog-expand-docstring-coverage verify (phase-67 #10).

来源: infra-034 Reviewer LGTM 建议; phase-67 #10 续推 batch=5.

scope: verify-only / docs-only. 把 P240 infra-034 "运行环境约定" docstring +
sys.executable 子进程模板, 推广到下一批 5 个 verify 脚本顶端 module docstring:

  - scripts/verify_robot_030.py
  - scripts/verify_robot_031.py
  - scripts/verify_interact_028.py
  - scripts/verify_interact_029.py
  - scripts/verify_interact_030.py

业务行为零变更; 5 个目标脚本函数体严格不动, 仅 module docstring 末尾追加
"运行环境约定 (infra-034)" 段; 同步把 5 个目标登记到:

  - scripts/verify_infra_034.py TARGETS 元组 (20 -> 25)
  - evidence/infra-034/v4_sha.json targets (20 -> 25)

并把 cascade sha bump 反向锁同步到 scripts/verify_infra_035.py:

  - VERIFY_034_EXPECTED_SHA  (verify_infra_034.py 文件 sha)
  - EXPECTED_SELF_FILE_SHA   (verify_infra_035.py 自体 V8 sha, pragma skip 后)

V0 self_sha — 本脚本自体 sha 锁 (pragma V0-SELF-SHA-SKIP 剔除真常量行后比对)
V1 5 个目标 docstring 全部含 5 个 KEY_PHRASES (与 verify_infra_034 同 schema)
V2 5 个目标 + 联动 file (verify_infra_034 / verify_infra_035 / v4_sha.json)
    实际 sha 等于本脚本硬锁的 EXPECTED_*_SHA (cascade 一致性)
V3 mutant — 内存中删去任一目标某关键短语, V1 同款检查应 FAIL
V4 verify_infra_034.py TARGETS 元组真值含全部 5 个新目标 (AST 解析, 非字符串扫)
V5 evidence/infra-034/v4_sha.json targets 真值含全部 5 个新目标 sha

retval: 0 全 PASS / 1 任一 FAIL

运行环境约定 (infra-034)
------------------------
本脚本及其子进程**必须**在已激活的 .venv 下运行 (Python 解释器入口
``.venv/bin/python``); 不要用系统 ``python3`` 直接调用本脚本, 否则
``importlib`` 加载业务模块时依赖 (numpy / soundfile / onnxruntime 等) 可能
解析到系统站点而非 venv 站点, 导致与 ``./init.sh`` smoke 路径不一致。

约定细则:
  - **Reviewer / CI / 手动复跑入口**: 一律 ``.venv/bin/python`` 启动 (或先
    ``source .venv/bin/activate`` 再
    ``python scripts/verify_infra_034_backlog_expand_docstring_coverage.py``)。
  - **子进程 invoke**: 任何 ``subprocess.run`` 第一参数固定使用 ``sys.executable``
    (即本脚本所属解释器); 不写死 ``"python"`` / ``"python3"`` 字面量。
  - **环境变量继承**: 子进程从 ``os.environ`` 拷贝 PATH / PYTHONPATH 等,
    PATH 中 venv 的 ``bin`` 目录位置不可被人为打乱。
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent

# phase-67 #10 续推目标
NEW_TARGETS: Tuple[str, ...] = (
    "scripts/verify_robot_030.py",
    "scripts/verify_robot_031.py",
    "scripts/verify_interact_028.py",
    "scripts/verify_interact_029.py",
    "scripts/verify_interact_030.py",
)

# 与 verify_infra_034.py V1 同 schema
KEY_PHRASES: Tuple[str, ...] = (
    ".venv",
    "sys.executable",
    "子进程",
    "运行环境约定",
    ".venv/bin/python",
)

# V2 联动文件 sha 硬锁 (cascade)
EXPECTED_TARGET_SHAS: Tuple[Tuple[str, str], ...] = (
    ("scripts/verify_robot_030.py",
     "3b9a16b6297d3044cfb178a7f9630bc547d357e1eaa6e00fca01b71902dddf80"),
    ("scripts/verify_robot_031.py",
     "3663d8dd06a2b05a347d9069f624468397db0bb1a10007d9f762f009f54e658e"),
    ("scripts/verify_interact_028.py",
     "68463a126084dac94ecb45bb6247ea3d82980f09b8cd1a2a7fe35274036f8bd1"),
    ("scripts/verify_interact_029.py",
     "931c67a421eb0a193a826af177e204d7b5dbdba6a4076b63ab28bf4b3dece7c5"),
    ("scripts/verify_interact_030.py",
     "e02f1582051b919992bcd8eb9888c7b1533aec3f99e0fe1ae81f304271a4e09d"),
    ("scripts/verify_infra_034.py",
     "ec21a8f694bb7bc132960f8e0a07c1363fc50177baf3c552a9efae2bd4640fe7"),
    ("evidence/infra-034/v4_sha.json",
     "48bb4792665ed6924719e583912d760a7b033c6095cc384dcf9b3c67d166bf1f"),
)

# V0 自体 sha 锁 (pragma V0-SELF-SHA-SKIP 行计算时剔除; 仅本行打 pragma)
EXPECTED_SELF_FILE_SHA = "9e29fcc8c379870d37b9853079723669c5dc8b77f8daa559f8e70dbb9643e0db"  # V0-SELF-SHA-SKIP


_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_034_backlog_expand][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _self_sha_skip_pragma() -> str:
    """计算本脚本自体 sha256, 剔除行尾带 ``# V0-SELF-SHA-SKIP`` 的真常量行.

    与 verify_infra_035 V8 同款收紧: 必须行尾 pragma 锚 (非宽松子串).
    """
    src = Path(__file__).resolve().read_text(encoding="utf-8")
    pragma = "# V0-SELF-SHA-SKIP"
    kept = [line for line in src.splitlines(keepends=True)
            if not line.rstrip("\n").rstrip().endswith(pragma)]
    return hashlib.sha256("".join(kept).encode("utf-8")).hexdigest()


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
# V0: self_sha (pragma skip)
# ---------------------------------------------------------------------------
def v0_self_sha() -> None:
    actual = _self_sha_skip_pragma()
    ok = actual == EXPECTED_SELF_FILE_SHA
    _emit(
        "V0_self_file_sha_lock",
        ok,
        f"actual={actual[:16]} expect={EXPECTED_SELF_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V1: 5 个目标 docstring KEY_PHRASES 全到位
# ---------------------------------------------------------------------------
def v1_targets_phrases() -> None:
    for rel in NEW_TARGETS:
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
# V2: target_file_sha (cascade)
# ---------------------------------------------------------------------------
def v2_target_file_sha() -> None:
    for rel, expect in EXPECTED_TARGET_SHAS:
        p = ROOT / rel
        if not p.is_file():
            _emit(f"V2_{rel}_sha", False, "file missing")
            continue
        got = _sha256(p)
        _emit(
            f"V2_{rel}_sha",
            got == expect,
            f"got={got[:16]} expect={expect[:16]}",
        )


# ---------------------------------------------------------------------------
# V3: mutant — 内存中删去某关键短语 → V1 FAIL
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    rel = NEW_TARGETS[0]
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
    ok = target_phrase in missing
    _emit(
        "V3_mutant",
        ok,
        f"after deletion missing={missing} (expect {target_phrase!r} in missing)",
    )


# ---------------------------------------------------------------------------
# V4: verify_infra_034.py TARGETS 元组真值 (AST 解析) 含所有 5 个新目标
# ---------------------------------------------------------------------------
def v4_verify_034_targets_registered() -> None:
    p = ROOT / "scripts" / "verify_infra_034.py"
    src = p.read_text(encoding="utf-8")
    tree = ast.parse(src)
    targets_value: Tuple[str, ...] = ()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "TARGETS":
            if isinstance(node.value, ast.Tuple):
                vals = []
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        vals.append(elt.value)
                targets_value = tuple(vals)
                break
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "TARGETS" and isinstance(node.value, ast.Tuple):
                    vals = []
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            vals.append(elt.value)
                    targets_value = tuple(vals)
                    break
    missing = [t for t in NEW_TARGETS if t not in targets_value]
    _emit(
        "V4_verify_infra_034_TARGETS_registered",
        not missing,
        f"len(TARGETS)={len(targets_value)} missing_new={missing}" if missing
        else f"len(TARGETS)={len(targets_value)}; all 5 new targets registered",
    )


# ---------------------------------------------------------------------------
# V5: v4_sha.json targets 真值含 5 个新目标 sha
# ---------------------------------------------------------------------------
def v5_v4_sha_json_has_new_targets() -> None:
    p = ROOT / "evidence" / "infra-034" / "v4_sha.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        _emit("V5_v4_sha_json", False, f"load failed: {e}")
        return
    targets = data.get("targets") or {}
    missing = [t for t in NEW_TARGETS if t not in targets]
    if missing:
        _emit("V5_v4_sha_json", False, f"missing entries: {missing}")
        return
    # 每个 entry sha 与文件实际 sha 一致
    drift = []
    for t in NEW_TARGETS:
        expect = targets.get(t)
        actual = _sha256(ROOT / t)
        if expect != actual:
            drift.append(f"{t}:expect={expect[:12]} actual={actual[:12]}")
    _emit(
        "V5_v4_sha_json",
        not drift,
        f"drift={drift}" if drift else f"all 5 new entries present + sha-coherent",
    )


def main() -> int:
    v0_self_sha()
    v1_targets_phrases()
    v2_target_file_sha()
    v3_mutant()
    v4_verify_034_targets_registered()
    v5_v4_sha_json_has_new_targets()

    failed = [t for t, ok, _ in _results if not ok]
    total = len(_results)
    print(f"[verify_infra_034_backlog_expand] summary total={total} failed={len(failed)}", flush=True)
    if failed:
        print(f"[verify_infra_034_backlog_expand] failed tags: {failed}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
