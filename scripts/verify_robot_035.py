#!/usr/bin/env python3
"""verify_robot_035: shared helper lib for docs-lock verify scripts.

robot-035: 把 verify_robot_032 中的 ``_parse_headings_from_doc`` helper 抽到
``scripts/_verify_lib.py`` 中作公开 helper ``parse_headings_from_doc``, 让多个
docs-lock verify (当前为 verify_robot_032/034, 后续可扩展) 复用同一份解析逻辑。
本 verify 锁住该共享 lib 的存在、可调用性、函数体 sha 与文件 sha。

V0 _verify_lib.py 存在 + 公开 ``parse_headings_from_doc`` 符号 (def 形)。
V1 subprocess import 路径有效: 通过 sys.executable 起子进程
   ``import _verify_lib; _verify_lib.parse_headings_from_doc(DOC, SENTINEL)``
   返回 list 非空 (>=10 章节)。
V2 sha256 锁 _verify_lib.parse_headings_from_doc 函数体 (hardcoded, 与
   verify_robot_034 V2 同一字面, 证两处 sha 链路一致)。
V3 mutant: 在内存中 ast 替换 helper body 中 sentinel 检测逻辑 → 通过
   importlib.util.module_from_spec 加载 mutated module 调用 helper, 与正常调用
   diff 反证 (mutated 版本对正常 doc 返回不同结果或抛错)。
V4 sha256 锁 _verify_lib.py 整体文件 (hardcoded)。
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
    ``source .venv/bin/activate`` 再 ``python scripts/verify_robot_035.py``)。
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
SCRIPTS = REPO / "scripts"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"
DOC = REPO / "docs" / "proactive_scheduler_block_policy.md"

LIB_HELPER_NAME = "parse_headings_from_doc"
SENTINEL_LINE = "## 章节标题列表（供 verify_robot_032 用）"
MIN_HEADINGS = 10

# V2: hardcoded sha256 of _verify_lib.parse_headings_from_doc function body
# (与 verify_robot_034 V2 同一字面, 证两处 sha 链路一致)
EXPECTED_HELPER_SHA = "98890e28f76d095217aa3edf70f229ea68cf535fac284747257e62a810916405"

# V4: hardcoded sha256 of _verify_lib.py file
# infra-037 bump: helper 扩展 (新增 func_sha_by_name) 后文件 sha 变更
# infra-040-backlog bump: 新增 assert_unique_needle helper 后文件 sha 再变更
# infra-V6-backlog bump (P264): 抽 V6 scan_reverse_sha_locks 等 helper 后文件 sha 再变更
# infra-V6-backlog bump (#1.48): _verify_lib.py 再次扩展后 sha 再变更
EXPECTED_LIB_FILE_SHA = "6098f8c1b0a70331a12407d0e184b7d30c6a014e5a7b37090ff4981cc357a93b"


_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_robot_035][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _helper_source_segment(src: str) -> str:
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == LIB_HELPER_NAME:
            seg = ast.get_source_segment(src, n)
            if seg is None:
                raise RuntimeError("ast.get_source_segment returned None")
            return seg
    raise RuntimeError(f"helper {LIB_HELPER_NAME!r} not found")


# ---------------------------------------------------------------------------
# V0: lib file + public symbol presence
# ---------------------------------------------------------------------------
def v0_presence() -> None:
    if not VERIFY_LIB.is_file():
        _emit("V0_lib_file_exists", False, f"missing {VERIFY_LIB}")
        return
    _emit("V0_lib_file_exists", True, str(VERIFY_LIB))
    src = VERIFY_LIB.read_text(encoding="utf-8")
    _emit(
        "V0_lib_helper_def",
        f"def {LIB_HELPER_NAME}" in src,
        f"helper={LIB_HELPER_NAME}",
    )
    _emit(
        "V0_lib_all_export",
        f'"{LIB_HELPER_NAME}"' in src and "__all__" in src,
        f"__all__ contains {LIB_HELPER_NAME!r}",
    )


# ---------------------------------------------------------------------------
# V1: subprocess import + call yields >=MIN_HEADINGS
# ---------------------------------------------------------------------------
def v1_subprocess_import_call() -> None:
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
        "import _verify_lib\n"
        f"h = _verify_lib.{LIB_HELPER_NAME}({str(DOC)!r}.__class__ and __import__('pathlib').Path({str(DOC)!r}), {SENTINEL_LINE!r})\n"
        "print(json.dumps({'len': len(h), 'first': h[0] if h else None}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        _emit("V1_subprocess_import", False, f"rc={proc.returncode} stderr={proc.stderr[:120]!r}")
        return
    import json as _json

    try:
        data = _json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as e:
        _emit("V1_subprocess_import", False, f"json parse error: {e!r} out={proc.stdout[:120]!r}")
        return
    _emit(
        "V1_subprocess_import",
        isinstance(data.get("len"), int) and data["len"] >= MIN_HEADINGS,
        f"len={data.get('len')} first={data.get('first')!r}",
    )


# ---------------------------------------------------------------------------
# V2: sha256 lock of helper function body
# ---------------------------------------------------------------------------
def v2_helper_sha_lock() -> None:
    try:
        src = VERIFY_LIB.read_text(encoding="utf-8")
        seg = _helper_source_segment(src)
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
# V3: in-memory mutant — patch helper body via ast → diff vs normal call
# ---------------------------------------------------------------------------
def v3_mutant_helper_body() -> None:
    try:
        src = VERIFY_LIB.read_text(encoding="utf-8")
    except Exception as e:
        _emit("V3_mutant_setup", False, f"read error: {e!r}")
        return
    # 不写盘, 用一个字符串替换在 src 上构造 mutated 源 (替换 ``if not doc_path.exists():`` 块整体逻辑,
    # 改为直接 return [] —— 任意正常 doc 调用都会得到空列表, 与真实 helper 输出不同, 证 sha 锁的是真实 body)。
    needle = '    if not doc_path.exists():\n        raise RuntimeError(f"doc not found: {doc_path}")\n'
    if needle not in src:
        _emit("V3_mutant_setup", False, "mutant needle not found in lib src")
        return
    mutated_src = src.replace(
        needle,
        '    if not doc_path.exists():\n        raise RuntimeError(f"doc not found: {doc_path}")\n    return []  # ROBOT_035_MUTANT_INJECTED\n',
        1,
    )
    # 通过 importlib spec 从字符串加载 mutated module
    import types

    mutant_mod = types.ModuleType("_verify_lib_mutant_035")
    try:
        exec(compile(mutated_src, "<mutant>", "exec"), mutant_mod.__dict__)
    except Exception as e:
        _emit("V3_mutant_setup", False, f"compile/exec error: {e!r}")
        return
    helper = getattr(mutant_mod, LIB_HELPER_NAME, None)
    if helper is None:
        _emit("V3_mutant_setup", False, "helper missing in mutant module")
        return
    # 真实 lib 调用结果
    sys.path.insert(0, str(SCRIPTS))
    try:
        import _verify_lib as real_lib  # type: ignore
    finally:
        # 不污染 sys.path 永久
        pass
    real_result = getattr(real_lib, LIB_HELPER_NAME)(DOC, SENTINEL_LINE)
    mutant_result = helper(DOC, SENTINEL_LINE)
    # 反证: mutant 返回空 list, real 返回 >=10 项 → diff 非空且语义不同
    diff_ok = (
        isinstance(real_result, list)
        and isinstance(mutant_result, list)
        and len(real_result) >= MIN_HEADINGS
        and len(mutant_result) == 0
    )
    _emit(
        "V3_mutant_diff",
        diff_ok,
        f"real_len={len(real_result)} mutant_len={len(mutant_result)}",
    )


# ---------------------------------------------------------------------------
# V4: sha256 lock of _verify_lib.py file
# ---------------------------------------------------------------------------
def v4_lib_file_sha() -> None:
    if not VERIFY_LIB.is_file():
        _emit("V4_lib_file_sha", False, "lib file missing")
        return
    got = hashlib.sha256(VERIFY_LIB.read_bytes()).hexdigest()
    _emit(
        "V4_lib_file_sha",
        got == EXPECTED_LIB_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_LIB_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V5: subprocess self-call rc=0
# ---------------------------------------------------------------------------
def v5_self_subprocess() -> None:
    if os.environ.get("COCO_VERIFY_035_ROBOT_SELFCALL") == "1":
        _emit("V5_self_subprocess", True, "skipped (inside selfcall)")
        return
    env = dict(os.environ)
    env["COCO_VERIFY_035_ROBOT_SELFCALL"] = "1"
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
    v1_subprocess_import_call()
    v2_helper_sha_lock()
    v3_mutant_helper_body()
    v4_lib_file_sha()
    v5_self_subprocess()
    failed = [t for t, ok, _ in _results if not ok]
    total = len(_results)
    print(f"[verify_robot_035] summary total={total} failed={len(failed)}", flush=True)
    if failed:
        print(f"[verify_robot_035] failed tags: {failed}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
