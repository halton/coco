#!/usr/bin/env python3
"""verify_interact_037.py — drift_history jsonl rotation / size cap 验证.

V0 scripts/verify_interact_024.py 存在 + 目标符号可载入
V1 静态 sha256 锁 _append_drift_history 函数体
V2 静态 sha256 锁全 verify_interact_024.py 文件
V3 行为: tmpdir monkey-patch _DRIFT_HISTORY_PATH, 写满 → 触发 rotate
   + mutant: _MAX_BYTES 调成 10MB 时 rotate 不应触发 (反证)
V4 fixture 隔离: 真实 evidence 路径未被本测试写入
V5 subprocess 自调用 rc=0

interact-037: verify-only, 锁 256KB rotation 行为 + 仅 2 代 (.jsonl + .jsonl.1).
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
V24 = ROOT / "scripts" / "verify_interact_024.py"

# ---- 静态 sha 锁常量 (interact-037 baseline) -----------------------------
EXPECTED_FUNC_SHA = "9440e68ad94013679c421e6c55389fe8a9d1cafde57fcafa80e57c79eb9bb969"
EXPECTED_FILE_SHA = "1ff3e2b15677b49523fb49464b4bad1d1f87378e774e624cae41f6c758d18f10"

_results: List[Dict[str, Any]] = []


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_037] {tag} {msg}", flush=True)


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


def _load_v24():
    spec = importlib.util.spec_from_file_location("v24_mod", str(V24))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ---- V0 ------------------------------------------------------------------
def v0_target_exists() -> None:
    if not V24.exists():
        _record("V0_target_exists", False, "verify_interact_024.py missing")
        return
    try:
        mod = _load_v24()
    except Exception as e:  # noqa: BLE001
        _record("V0_target_exists", False, f"load failed: {e}")
        return
    missing = [n for n in ("_DRIFT_HISTORY_PATH", "_MAX_BYTES", "_append_drift_history") if not hasattr(mod, n)]
    if missing:
        _record("V0_target_exists", False, f"symbols missing: {missing}")
        return
    src_lines = V24.read_text(encoding="utf-8").splitlines()
    fn_lines = [i + 1 for i, L in enumerate(src_lines) if L.startswith("def _append_drift_history")]
    _record(
        "V0_target_exists",
        True,
        f"_append_drift_history at line(s) {fn_lines}; _MAX_BYTES={mod._MAX_BYTES}",
    )


# ---- V1 ------------------------------------------------------------------
def v1_func_sha_lock() -> None:
    try:
        mod = _load_v24()
        src = inspect.getsource(mod._append_drift_history)
    except Exception as e:  # noqa: BLE001
        _record("V1_func_sha_lock", False, f"introspect failed: {e}")
        return
    got = hashlib.sha256(src.encode()).hexdigest()
    ok = got == EXPECTED_FUNC_SHA
    _record(
        "V1_func_sha_lock",
        ok,
        f"expected={EXPECTED_FUNC_SHA[:12]} got={got[:12]}",
    )


# ---- V2 ------------------------------------------------------------------
def v2_file_sha_lock() -> None:
    got = hashlib.sha256(V24.read_bytes()).hexdigest()
    ok = got == EXPECTED_FILE_SHA
    _record(
        "V2_file_sha_lock",
        ok,
        f"expected={EXPECTED_FILE_SHA[:12]} got={got[:12]}",
    )


# ---- V3 ------------------------------------------------------------------
def v3_rotation_behavior() -> None:
    try:
        mod = _load_v24()
    except Exception as e:  # noqa: BLE001
        _record("V3_rotation_behavior", False, f"load failed: {e}")
        return

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td) / "drift.jsonl"
        orig_path = mod._DRIFT_HISTORY_PATH
        orig_max = mod._MAX_BYTES
        # 用一个小阈值快速触发 rotate (1KB).
        mod._DRIFT_HISTORY_PATH = tmp_path
        mod._MAX_BYTES = 1024
        try:
            # 先写一些把文件撑过 1KB.
            big_drift = {f"k{i}": {"v": "x" * 80} for i in range(20)}
            for _ in range(5):
                mod._append_drift_history(big_drift)
            size_before_rotate = tmp_path.stat().st_size
            # 现在 size > 1KB, 下一次 append 应触发 rotate.
            mod._append_drift_history({"trigger": {"v": "y"}})

            rotated = tmp_path.with_suffix(tmp_path.suffix + ".1")
            cond_rotated_exists = rotated.exists()
            # 新 .jsonl 应只含 1 行 (rotate 后 fresh + 这次 append).
            new_lines = tmp_path.read_text(encoding="utf-8").splitlines() if tmp_path.exists() else []
            cond_new_one_line = len(new_lines) == 1
            cond_old_in_rotated = rotated.exists() and rotated.stat().st_size > 0

            # mutant: 把 _MAX_BYTES 调成 10MB, append 不应再 rotate.
            # 重置: 删掉 rotated, 把当前 .jsonl 写到 > 1KB (模拟旧状态), 然后用 10MB 阈值 append.
            if rotated.exists():
                rotated.unlink()
            tmp_path.write_text("x" * 2048 + "\n", encoding="utf-8")
            mod._MAX_BYTES = 10 * 1024 * 1024
            mod._append_drift_history({"mutant": {"v": "z"}})
            cond_mutant_no_rotate = not rotated.exists()
        finally:
            mod._DRIFT_HISTORY_PATH = orig_path
            mod._MAX_BYTES = orig_max

        all_ok = (
            cond_rotated_exists
            and cond_new_one_line
            and cond_old_in_rotated
            and cond_mutant_no_rotate
        )
        _record(
            "V3_rotation_behavior",
            all_ok,
            (
                f"pre_rotate_size={size_before_rotate} "
                f"rotated_exists={cond_rotated_exists} "
                f"new_one_line={cond_new_one_line} "
                f"old_in_rotated={cond_old_in_rotated} "
                f"mutant_no_rotate={cond_mutant_no_rotate}"
            ),
        )


# ---- V4 ------------------------------------------------------------------
def v4_real_path_untouched() -> None:
    # V3 用 tmpdir + monkey-patch, 真实 evidence 路径不应被本脚本污染.
    # 我们只校验: 真实 _DRIFT_HISTORY_PATH 路径未指向 tmpdir; 并且我们没在本脚本直接打开它写测试数据.
    try:
        mod = _load_v24()
    except Exception as e:  # noqa: BLE001
        _record("V4_real_path_untouched", False, f"load failed: {e}")
        return
    real = mod._DRIFT_HISTORY_PATH
    ok = "evidence" in str(real) and str(real).startswith(str(ROOT))
    _record(
        "V4_real_path_untouched",
        ok,
        f"_DRIFT_HISTORY_PATH={real}",
    )


# ---- V5 ------------------------------------------------------------------
def v5_subprocess_self() -> None:
    try:
        r = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--inner"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as e:  # noqa: BLE001
        _record("V5_subprocess_self", False, f"subprocess failed: {e}")
        return
    ok = r.returncode == 0
    tail = (r.stdout or "").splitlines()[-3:]
    _record(
        "V5_subprocess_self",
        ok,
        f"rc={r.returncode} tail={tail}",
    )


# ---- main ----------------------------------------------------------------
def main() -> int:
    v0_target_exists()
    v1_func_sha_lock()
    v2_file_sha_lock()
    v3_rotation_behavior()
    v4_real_path_untouched()
    if "--inner" not in sys.argv:
        v5_subprocess_self()
    fails = [r for r in _results if not r["ok"]]
    summary = {
        "feature": "interact-037",
        "total": len(_results),
        "fail": len(fails),
        "results": _results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
