#!/usr/bin/env python3
"""verify_robot_034: docs single-source for verify_robot_032 headings.

robot-034: 把 verify_robot_032 的 EXPECTED_HEADINGS 从 hardcoded list 改为
运行时从 docs/proactive_scheduler_block_policy.md 的 sentinel section 解析,
形成 docs → verify 单一事实源。本 verify 负责锁死这个约定本身。

V0 docs sentinel section 存在; verify_robot_032 中存在 helper
   ``_parse_headings_from_doc``。
V1 helper 返回非空且 >=10 章节。
V2 sha256 锁 verify_robot_032 中 helper 函数体源代码 (hardcoded)。
V3 mutant 反证 (内存中): 把 sentinel 行替换为别的字面 → helper RuntimeError。
   不写盘, finally 无需还原。
V4 sha256 print docs 文件 (与 robot-032 V4 风格一致, print-only, 不 enforce)。
V5 subprocess 自调用 rc=0 (sys.executable invoke 自身, 自洽通过)。

退出码 0=ALL PASS / 1=任一 FAIL.

运行环境约定 (infra-034)
------------------------
本脚本及其 V0-V5 子进程**必须**在已激活的 .venv 下运行 (Python 解释器入口
``.venv/bin/python``); 不要用系统 ``python3`` 直接调用本脚本, 否则
``importlib`` 加载业务模块时依赖 (numpy / soundfile / onnxruntime 等) 可能
解析到系统站点而非 venv 站点, 导致与 ``./init.sh`` smoke 路径不一致,
进而 V0-V5 退出码漂移。

约定细则:
  - **Reviewer / CI / 手动复跑入口**: 一律 ``.venv/bin/python`` 启动 (或先
    ``source .venv/bin/activate`` 再 ``python scripts/verify_robot_034.py``)。
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
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "proactive_scheduler_block_policy.md"
VERIFY_032 = REPO / "scripts" / "verify_robot_032.py"

HELPER_NAME = "_parse_headings_from_doc"
SENTINEL_LINE = "## 章节标题列表（供 verify_robot_032 用）"

# V2: hardcoded sha256 of helper function body (ast source segment)
EXPECTED_HELPER_SHA = "33b728b8f5a07c7e652ebf66adca38552858814041ef3951b37110ca81997dc3"

# V4: doc sha print (informational, not enforced)
EXPECTED_DOC_SHA = "33f4484cc7108a3aba68e3ecef9d8419a2fca02ddb3665f754e8dc7667aa5f5a"

MIN_HEADINGS = 10

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_robot_034][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _load_verify_032_module():
    spec = importlib.util.spec_from_file_location("verify_robot_032_inproc", VERIFY_032)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _helper_source_segment() -> str:
    src = VERIFY_032.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == HELPER_NAME:
            seg = ast.get_source_segment(src, n)
            if seg is None:
                raise RuntimeError("ast.get_source_segment returned None")
            return seg
    raise RuntimeError(f"helper {HELPER_NAME!r} not found in {VERIFY_032}")


# ---------------------------------------------------------------------------
# V0: sentinel + helper presence
# ---------------------------------------------------------------------------
def v0_presence() -> None:
    if not DOC.is_file():
        _emit("V0_doc_exists", False, f"missing {DOC}")
        return
    _emit("V0_doc_exists", True, str(DOC))
    text = DOC.read_text(encoding="utf-8")
    _emit("V0_sentinel_in_doc", SENTINEL_LINE in text, f"sentinel={SENTINEL_LINE!r}")

    if not VERIFY_032.is_file():
        _emit("V0_verify_032_exists", False, f"missing {VERIFY_032}")
        return
    _emit("V0_verify_032_exists", True, str(VERIFY_032))
    src = VERIFY_032.read_text(encoding="utf-8")
    _emit("V0_helper_def_in_verify_032", f"def {HELPER_NAME}" in src, f"helper={HELPER_NAME}")


# ---------------------------------------------------------------------------
# V1: helper returns >=MIN_HEADINGS items
# ---------------------------------------------------------------------------
def v1_helper_nonempty() -> None:
    try:
        mod = _load_verify_032_module()
    except Exception as e:
        _emit("V1_helper_loads", False, f"import error: {e!r}")
        return
    if not hasattr(mod, HELPER_NAME):
        _emit("V1_helper_loads", False, f"attr {HELPER_NAME} missing")
        return
    try:
        headings = getattr(mod, HELPER_NAME)(DOC)
    except Exception as e:
        _emit("V1_helper_call", False, f"helper raised: {e!r}")
        return
    _emit(
        "V1_helper_count",
        isinstance(headings, list) and len(headings) >= MIN_HEADINGS,
        f"len={len(headings) if hasattr(headings,'__len__') else 'n/a'} (>= {MIN_HEADINGS})",
    )


# ---------------------------------------------------------------------------
# V2: sha256 lock of helper source segment
# ---------------------------------------------------------------------------
def v2_helper_sha_lock() -> None:
    try:
        seg = _helper_source_segment()
    except Exception as e:
        _emit("V2_helper_sha", False, f"extract error: {e!r}")
        return
    got = hashlib.sha256(seg.encode("utf-8")).hexdigest()
    _emit(
        "V2_helper_sha",
        got == EXPECTED_HELPER_SHA,
        f"got={got[:16]} expect={EXPECTED_HELPER_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: mutant — replace sentinel line in memory → helper RuntimeError
# ---------------------------------------------------------------------------
def v3_mutant_sentinel() -> None:
    try:
        mod = _load_verify_032_module()
    except Exception as e:
        _emit("V3_mutant_setup", False, f"import error: {e!r}")
        return
    helper = getattr(mod, HELPER_NAME, None)
    if helper is None:
        _emit("V3_mutant_setup", False, "helper missing")
        return
    # 写到 /tmp 临时 mutated doc, 不动 DOC 文件
    import tempfile

    text = DOC.read_text(encoding="utf-8")
    if SENTINEL_LINE not in text:
        _emit("V3_mutant_sentinel", False, "sentinel unexpectedly absent pre-mutant")
        return
    mutated = text.replace(SENTINEL_LINE, "## REMOVED_SENTINEL_FOR_MUTANT")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(mutated)
        tmp_path = Path(f.name)
    try:
        raised = False
        err = ""
        try:
            helper(tmp_path)
        except RuntimeError as e:
            raised = True
            err = str(e)
        _emit("V3_mutant_sentinel", raised, f"raised={raised} err={err[:80]!r}")
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# V4: doc sha256 print (print-only, not enforced)
# ---------------------------------------------------------------------------
def v4_doc_sha_print() -> None:
    if not DOC.is_file():
        _emit("V4_doc_sha", False, "doc missing")
        return
    got = hashlib.sha256(DOC.read_bytes()).hexdigest()
    matches = got == EXPECTED_DOC_SHA
    print(
        f"[verify_robot_034][INFO] V4 doc sha={got} expect={EXPECTED_DOC_SHA} match={matches}",
        flush=True,
    )
    # 不 enforce, 始终 PASS
    _emit("V4_doc_sha_print", True, f"sha={got[:16]} match={matches}")


# ---------------------------------------------------------------------------
# V5: subprocess self-call rc=0
# ---------------------------------------------------------------------------
def v5_self_subprocess() -> None:
    if os.environ.get("COCO_VERIFY_034_ROBOT_SELFCALL") == "1":
        _emit("V5_self_subprocess", True, "skipped (inside selfcall)")
        return
    env = dict(os.environ)
    env["COCO_VERIFY_034_ROBOT_SELFCALL"] = "1"
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
    v0_presence()
    v1_helper_nonempty()
    v2_helper_sha_lock()
    v3_mutant_sentinel()
    v4_doc_sha_print()
    v5_self_subprocess()
    failed = [t for t, ok, _ in _results if not ok]
    total = len(_results)
    print(f"[verify_robot_034] summary total={total} failed={len(failed)}", flush=True)
    if failed:
        print(f"[verify_robot_034] failed tags: {failed}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
