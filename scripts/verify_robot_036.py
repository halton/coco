#!/usr/bin/env python3
"""verify_robot_036: SENTINEL_LINE single-source from verify_robot_032.

robot-036: 移除 verify_robot_034.py 中 hardcode 的 SENTINEL_LINE 字面, 改为
通过 ast.literal_eval 静态读取 verify_robot_032.py 中的 ``_HEADINGS_SECTION_SENTINEL``
常量, 形成 verify_robot_032 → verify_robot_034 的 single source 链路。

设计选择
--------
- 采用 ast.literal_eval 静态读取而非 ``from verify_robot_032 import ...``,
  避免 import-time 副作用 (verify_robot_032 module-level 会立即解析 docs 生成
  EXPECTED_HEADINGS); 让 verify_robot_034 顶层加载与 docs 文件状态完全解耦。
- 后续引用名 ``SENTINEL_LINE`` 不变, V0/V1/V3 调用面零迁移。

锁
--
V0 verify_robot_034.py 中无 hardcode ``SENTINEL_LINE = "..."`` 字面 (仅允许通过
   ast 读取赋值); verify_robot_032 中 ``_HEADINGS_SECTION_SENTINEL`` 常量仍存在。
V1 静态读取后 SENTINEL_LINE 与 verify_robot_032 中常量字面值 byte-equal。
V2 sha256 锁 verify_robot_032 中 ``_HEADINGS_SECTION_SENTINEL`` 行 (hardcoded)。
V3 mutant: 临时改 verify_robot_032 sentinel 字面 → verify_robot_034 SENTINEL_LINE
   也跟着变 (证 single-source); 同时复跑 verify_robot_032 应 rc=1 (V2 sha 锁失效),
   finally git restore。
V4 sha256 锁 verify_robot_034.py 整体 (hardcoded)。
V5 subprocess 自调用 rc=0。

退出码 0=ALL PASS / 1=任一 FAIL。

运行环境约定 (与 infra-034 一致): 必须在 ``.venv/bin/python`` 下运行。
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
VERIFY_032 = REPO / "scripts" / "verify_robot_032.py"
VERIFY_034 = REPO / "scripts" / "verify_robot_034.py"

SENTINEL_CONST_NAME = "_HEADINGS_SECTION_SENTINEL"

# V2: sha256 锁 verify_robot_032 中 sentinel 行 (含尾换行)
EXPECTED_SENTINEL_LINE_SHA = (
    "212b71ff2e6af6048258b68088f57f9b7eb5703767978d7bc58ee75e8821f185"
)

# V4: sha256 锁 verify_robot_034.py 整体
EXPECTED_VERIFY_034_SHA = (
    "0e390ca10577016e24dc3f531a7a9aefa30a3305614cb609db4150d7db36eca2"
)

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_robot_036][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _read_constant_from(path: Path, name: str) -> str:
    """静态读取 path 中名为 name 的顶层常量字面值 (str)。"""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return ast.literal_eval(node.value)
    raise RuntimeError(f"constant {name!r} not found in {path}")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sentinel_line_bytes() -> bytes:
    """提取 verify_robot_032 中 sentinel 常量赋值行 (含 \n)。"""
    text = VERIFY_032.read_text(encoding="utf-8")
    for line in text.splitlines(keepends=True):
        if SENTINEL_CONST_NAME in line and "=" in line:
            return line.encode("utf-8")
    raise RuntimeError(f"sentinel line not found in {VERIFY_032}")


# ---------------------------------------------------------------------------
# V0: no hardcode literal of SENTINEL_LINE in verify_robot_034
# ---------------------------------------------------------------------------
def v0_no_hardcode() -> None:
    if not VERIFY_034.is_file():
        _emit("V0_verify_034_exists", False, f"missing {VERIFY_034}")
        return
    _emit("V0_verify_034_exists", True, str(VERIFY_034))

    src = VERIFY_034.read_text(encoding="utf-8")
    # 禁止形如 `SENTINEL_LINE = "..."` 或 `SENTINEL_LINE = '...'` 的直接字面赋值。
    # 允许 `SENTINEL_LINE = <call/name expr>`, 如 `SENTINEL_LINE = _read_sentinel_from_verify_032()`。
    bad = re.search(r"^SENTINEL_LINE\s*=\s*['\"]", src, re.MULTILINE)
    _emit(
        "V0_no_hardcode_literal",
        bad is None,
        f"match={bad.group(0)!r}" if bad else "no literal assignment",
    )
    # 同时确认 SENTINEL_LINE 名字仍存在 (避免误删导致后续 V0/V1/V3 调用面崩)。
    has_name = re.search(r"^SENTINEL_LINE\s*=", src, re.MULTILINE) is not None
    _emit("V0_sentinel_name_present", has_name, "SENTINEL_LINE assignment exists")

    # verify_robot_032 中 _HEADINGS_SECTION_SENTINEL 仍存在
    if not VERIFY_032.is_file():
        _emit("V0_verify_032_exists", False, f"missing {VERIFY_032}")
        return
    src32 = VERIFY_032.read_text(encoding="utf-8")
    _emit(
        "V0_sentinel_const_in_verify_032",
        f"{SENTINEL_CONST_NAME} =" in src32 or f"{SENTINEL_CONST_NAME}=" in src32,
        f"const={SENTINEL_CONST_NAME}",
    )


# ---------------------------------------------------------------------------
# V1: byte-equal between verify_robot_034.SENTINEL_LINE and source-of-truth
# ---------------------------------------------------------------------------
def v1_byte_equal() -> None:
    try:
        truth = _read_constant_from(VERIFY_032, SENTINEL_CONST_NAME)
    except Exception as e:
        _emit("V1_read_truth", False, f"err={e!r}")
        return
    _emit("V1_read_truth", True, f"value={truth!r}")

    # 通过 importlib 加载 verify_robot_034 取其 SENTINEL_LINE (避免污染当前 sys.modules)
    try:
        spec = importlib.util.spec_from_file_location(
            "verify_robot_034_inproc_036", VERIFY_034
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception as e:
        _emit("V1_load_verify_034", False, f"import err={e!r}")
        return
    got = getattr(mod, "SENTINEL_LINE", None)
    if got is None:
        _emit("V1_sentinel_attr", False, "SENTINEL_LINE missing on module")
        return
    _emit(
        "V1_byte_equal",
        got == truth,
        f"got={got!r} truth={truth!r}",
    )


# ---------------------------------------------------------------------------
# V2: sha256 lock of sentinel line in verify_robot_032
# ---------------------------------------------------------------------------
def v2_sentinel_line_sha() -> None:
    try:
        b = _sentinel_line_bytes()
    except Exception as e:
        _emit("V2_sentinel_line_sha", False, f"extract err={e!r}")
        return
    got = _sha256_bytes(b)
    _emit(
        "V2_sentinel_line_sha",
        got == EXPECTED_SENTINEL_LINE_SHA,
        f"got={got[:16]} expect={EXPECTED_SENTINEL_LINE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: mutant — temporarily edit verify_robot_032 sentinel literal; assert
#     verify_robot_034 SENTINEL_LINE follows; verify_robot_032 V2 sha rc=1.
# ---------------------------------------------------------------------------
def v3_mutant_single_source() -> None:
    original_bytes = VERIFY_032.read_bytes()
    original_text = original_bytes.decode("utf-8")
    mutated_value = "## SENTINEL_MUTATED_BY_robot_036"
    # 替换常量字面: 找到 `_HEADINGS_SECTION_SENTINEL = "..."` 这一行整行替换
    pattern = re.compile(
        rf"^{re.escape(SENTINEL_CONST_NAME)}\s*=\s*['\"][^'\"]*['\"]",
        re.MULTILINE,
    )
    if not pattern.search(original_text):
        _emit("V3_mutant_setup", False, "could not locate sentinel assignment line")
        return
    mutated_text = pattern.sub(
        f'{SENTINEL_CONST_NAME} = "{mutated_value}"', original_text, count=1
    )
    try:
        VERIFY_032.write_text(mutated_text, encoding="utf-8")

        # (a) verify_robot_034 SENTINEL_LINE 应跟着变
        try:
            spec = importlib.util.spec_from_file_location(
                "verify_robot_034_mutant_036", VERIFY_034
            )
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            got = getattr(mod, "SENTINEL_LINE", None)
        except Exception as e:
            _emit("V3_mutant_propagates", False, f"load err={e!r}")
            got = None
        _emit(
            "V3_mutant_propagates",
            got == mutated_value,
            f"got={got!r} expected={mutated_value!r}",
        )

        # (b) verify_robot_032 自身 rc=1 (V2 sha 锁失效, 因 V2 锁了 sentinel
        #     line 的 sha; 改了字面后 sha 必然变)
        proc = subprocess.run(
            [sys.executable, str(VERIFY_032)],
            capture_output=True,
            text=True,
            cwd=str(REPO),
        )
        _emit(
            "V3_mutant_verify_032_fails",
            proc.returncode == 1,
            f"rc={proc.returncode}",
        )
    finally:
        # git restore 是更稳的恢复 (即使写入失败也能回到 HEAD), 但这里 mutated
        # 是文件内容覆盖, 我们直接写回 original_bytes 也足够。
        VERIFY_032.write_bytes(original_bytes)
        # double-check 恢复成功
        restored_ok = VERIFY_032.read_bytes() == original_bytes
        if not restored_ok:
            # 兜底
            subprocess.run(
                ["git", "restore", "--source=HEAD", "--", str(VERIFY_032)],
                cwd=str(REPO),
                check=False,
            )


# ---------------------------------------------------------------------------
# V4: sha256 lock of verify_robot_034.py whole file
# ---------------------------------------------------------------------------
def v4_verify_034_sha() -> None:
    if not VERIFY_034.is_file():
        _emit("V4_verify_034_sha", False, f"missing {VERIFY_034}")
        return
    got = hashlib.sha256(VERIFY_034.read_bytes()).hexdigest()
    _emit(
        "V4_verify_034_sha",
        got == EXPECTED_VERIFY_034_SHA,
        f"got={got[:16]} expect={EXPECTED_VERIFY_034_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V5: subprocess self-invoke rc=0
# ---------------------------------------------------------------------------
def v5_self_subprocess() -> None:
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--inner"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    _emit(
        "V5_self_subprocess",
        proc.returncode == 0,
        f"rc={proc.returncode} stdout_lines={len(proc.stdout.splitlines())}",
    )


def _run_all_checks() -> int:
    v0_no_hardcode()
    v1_byte_equal()
    v2_sentinel_line_sha()
    v3_mutant_single_source()
    v4_verify_034_sha()
    return 0


def main() -> int:
    # --inner: skip V5 (avoid recursion)
    inner = "--inner" in sys.argv[1:]
    _run_all_checks()
    if not inner:
        v5_self_subprocess()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"[verify_robot_036] summary total={total} failed={failed}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
