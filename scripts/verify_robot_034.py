#!/usr/bin/env python3
"""verify_robot_034: docs single-source for verify_robot_032 headings.

robot-034: 把 verify_robot_032 的 EXPECTED_HEADINGS 从 hardcoded list 改为
运行时从 docs/proactive_scheduler_block_policy.md 的 sentinel section 解析,
形成 docs → verify 单一事实源。本 verify 负责锁死这个约定本身。

robot-035: helper 函数体迁移到 scripts/_verify_lib.py 中 ``parse_headings_from_doc``
公开 API。本 verify 的 V0/V1/V2 改为锁该共享 lib 函数, verify_robot_032 中保留
``_parse_headings_from_doc`` 本地别名 (import as) 以维持调用面与 V0 函数名串检测。

V0 docs sentinel section 存在; verify_robot_032 中存在名为
   ``_parse_headings_from_doc`` 的本地 symbol; ``_verify_lib`` 模块存在且
   暴露 ``parse_headings_from_doc``。
V1 helper 返回非空且 >=10 章节 (经 verify_robot_032 import 后通过 attribute 调用)。
V2 sha256 锁 _verify_lib.parse_headings_from_doc 函数体源代码 (hardcoded)。
V3 mutant 反证 (临时文件): 把 sentinel 行替换为别的字面 → helper RuntimeError。
   写到 /tmp 临时 doc, finally 清理, 不动真实 docs。
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
# robot-035: 共享 lib
VERIFY_LIB = REPO / "scripts" / "_verify_lib.py"

# robot-037: 复用 _verify_lib.read_constant 静态读 verify_032 中常量
sys.path.insert(0, str(REPO / "scripts"))
from _verify_lib import read_constant  # noqa: E402

HELPER_NAME = "_parse_headings_from_doc"  # verify_robot_032 中的本地别名 symbol
LIB_HELPER_NAME = "parse_headings_from_doc"  # _verify_lib 中的公开 helper


# robot-036: SENTINEL_LINE 不再 hardcode 重复, 改为从 verify_robot_032.py 中的
# ``_HEADINGS_SECTION_SENTINEL`` 常量静态读取 (ast.literal_eval), 形成 single source。
# 选择 ast 而非 ``from verify_robot_032 import ...`` 是为了避免 module-level
# 副作用 (verify_robot_032 import 时会立即解析 docs 生成 EXPECTED_HEADINGS),
# 让 verify_robot_034 顶层加载与 docs 文件状态完全解耦; V1/V3 仍走原有 importlib 路径。
# robot-037: 内部 _read_sentinel_from_verify_032 helper 提升到 _verify_lib.read_constant,
# 形成通用入口; 此处仅保留常量名 + 一次调用。
_SENTINEL_SRC_NAME = "_HEADINGS_SECTION_SENTINEL"

SENTINEL_LINE = read_constant(VERIFY_032, _SENTINEL_SRC_NAME)

# V2: hardcoded sha256 of _verify_lib.parse_headings_from_doc function body (ast source segment)
# robot-035: 改锁共享 lib helper (helper 函数体新增 sentinel 形参 + 文档与原版有差异,
# 因此 sha 不同于 robot-034 时锁的 verify_robot_032 中函数体)。
EXPECTED_HELPER_SHA = "98890e28f76d095217aa3edf70f229ea68cf535fac284747257e62a810916405"

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
    """robot-035: 从 _verify_lib.py 中提取 parse_headings_from_doc 函数体 ast 源段。"""
    src = VERIFY_LIB.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == LIB_HELPER_NAME:
            seg = ast.get_source_segment(src, n)
            if seg is None:
                raise RuntimeError("ast.get_source_segment returned None")
            return seg
    raise RuntimeError(f"helper {LIB_HELPER_NAME!r} not found in {VERIFY_LIB}")


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
    # robot-035: helper 现以 ``from _verify_lib import ... as _parse_headings_from_doc``
    # 形式存在; V0 串检测仍要求该本地别名 symbol 出现, 以维持 verify_robot_032
    # 调用面稳定 (EXPECTED_HEADINGS = _parse_headings_from_doc(DOC, ...))。
    _emit(
        "V0_helper_alias_in_verify_032",
        f"as {HELPER_NAME}" in src or f"def {HELPER_NAME}" in src,
        f"alias={HELPER_NAME}",
    )
    # robot-035: 新增检测 _verify_lib 文件与公开 helper
    if not VERIFY_LIB.is_file():
        _emit("V0_verify_lib_exists", False, f"missing {VERIFY_LIB}")
        return
    _emit("V0_verify_lib_exists", True, str(VERIFY_LIB))
    lib_src = VERIFY_LIB.read_text(encoding="utf-8")
    _emit(
        "V0_lib_helper_def",
        f"def {LIB_HELPER_NAME}" in lib_src,
        f"lib_helper={LIB_HELPER_NAME}",
    )


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
        # robot-035: helper 新签名 (doc_path, sentinel); 通过 verify_robot_032 中的
        # 本地别名 _parse_headings_from_doc 调用, 等价于直接调 _verify_lib.parse_headings_from_doc。
        headings = getattr(mod, HELPER_NAME)(DOC, SENTINEL_LINE)
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
            helper(tmp_path, SENTINEL_LINE)
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
