#!/usr/bin/env python3
"""verify_interact_036b.py — drift trend regression alert 验证.

V0 docstring + 环境约定 + self sha256 lock (本文件全文 sha)
V1 静态 sha256 锁: scripts/drift_trend_alert.py 全文件
V2 静态 sha256 锁: 关键函数源码 (analyze_drift_history / _ols_slope /
   _is_monotonic_up / format_alert_line)
V3 行为: 合成 monotonic-up 序列 -> any_triggered=True; 合成 flat 序列 ->
   any_triggered=False; 合成 high-slope 序列 -> slope_exceeded=True
V4 mutant: 把 _is_monotonic_up 输入改成 strictly decreasing -> 触发应为 False
   (反证), 同时 _ols_slope 在常数序列上必为 0.0
V5 subprocess: 用 sys.executable 跑 scripts/drift_trend_alert.py 临时 fixture,
   rc==0 (alert-only 不阻 merge), stdout 至少含一行 JSON payload 含
   kind=interact_036b_drift_trend_alert

interact-036b sim-first; verify-only; 不阻 merge gate.

运行环境约定 (infra-034)
------------------------
本脚本及其 V0-V5 子进程**必须**在已激活的 .venv 下运行
(``.venv/bin/python``); 不要用系统 ``python3`` 直接调用本脚本, 否则
``importlib`` 加载业务模块时依赖可能解析到系统站点而非 venv 站点,
进而 V0-V5 退出码漂移。

约定细则:
  - Reviewer / CI 入口: ``.venv/bin/python`` 或先 ``source .venv/bin/activate``
  - subprocess.run 第一参数固定 ``sys.executable``
  - 环境变量继承 PATH / PYTHONPATH, venv bin 前置不可被打乱
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
TARGET = ROOT / "scripts" / "drift_trend_alert.py"
SELF = Path(__file__).resolve()

# ---- V0 lock schema metadata (infra-P301) --------------------------------
# 元信息字段, 用于在批量升级 baseline (cascade bump) 时快速 diff 出哪个 lock
# 是新一轮 bump、哪个是旧轮残留. 字段语义:
#   V0_SELF_SHA_LOCK_VERSION : int, 整个 sha 锁组 (V0/V1/V2) 的 schema 版本号,
#                              每次集体 bump 时 +1; 不随单纯的 hex 重算变化.
#   V0_SELF_SHA_LOCK_BUMPED_AT: ISO date, 最近一次 bump 的日期.
# 不参与运行时校验 (避免自指), 仅由 verify_infra_P301.py lint 锁定字段存在 + 类型.
V0_SELF_SHA_LOCK_VERSION = 1
V0_SELF_SHA_LOCK_BUMPED_AT = "2026-05-23"

# ---- 静态 sha 锁常量 (interact-036b baseline) ----------------------------
EXPECTED_FILE_SHA = "8874f864f1702a43c4b452a646dab17a0508eea697d350c94226ccb8e796b3e0"
EXPECTED_FUNC_SHA = {
    "analyze_drift_history": "33da2f00a6f75e3cbde3b81e426d153d11e17ad1922b26b0c851f1769fbd12a8",
    "_ols_slope": "1d8af4de879b1c8d8666e2fe7506e415a902c25f5cc9459b5907bc2a8396a205",
    "_is_monotonic_up": "cfc20cf966c7ba7526cc494c9752ea490b5f57d26624e29f78d5f13ab46b22da",
    "format_alert_line": "a05c15b4024bd38c999de01ff0666db3719694f0339e324bbfdded1a6011671a",
}

_results: List[Dict[str, Any]] = []


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_036b] {tag} {msg}", flush=True)


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    _print("PASS" if ok else "FAIL", f"{name}: {detail}" if detail else name)


def _load_target():
    spec = importlib.util.spec_from_file_location("dta_mod", str(TARGET))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["dta_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---- V0 ------------------------------------------------------------------
def v0_self_sha_lock() -> None:
    """记录本 verify 脚本自身 sha 与目标存在; sha 仅记录, 不强校验自身 (避免自指)。"""
    if not TARGET.exists():
        _record("V0_target_exists", False, "drift_trend_alert.py missing")
        return
    self_sha = hashlib.sha256(SELF.read_bytes()).hexdigest()
    _record(
        "V0_self_sha_recorded",
        True,
        (
            f"self_sha={self_sha[:12]} "
            f"lock_schema_version={V0_SELF_SHA_LOCK_VERSION} "
            f"bumped_at={V0_SELF_SHA_LOCK_BUMPED_AT}"
        ),
    )


# ---- V1 ------------------------------------------------------------------
def v1_file_sha_lock() -> None:
    actual = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    ok = actual == EXPECTED_FILE_SHA
    _record(
        "V1_file_sha_lock",
        ok,
        f"actual={actual[:12]} expected={EXPECTED_FILE_SHA[:12]}",
    )


# ---- V2 ------------------------------------------------------------------
def v2_func_sha_lock() -> None:
    try:
        mod = _load_target()
    except Exception as exc:
        _record("V2_func_sha_lock", False, f"load fail: {exc}")
        return
    all_ok = True
    detail_parts = []
    for name, expected in EXPECTED_FUNC_SHA.items():
        fn = getattr(mod, name, None)
        if fn is None:
            all_ok = False
            detail_parts.append(f"{name}=MISSING")
            continue
        src = inspect.getsource(fn)
        actual = hashlib.sha256(src.encode()).hexdigest()
        ok = actual == expected
        if not ok:
            all_ok = False
        detail_parts.append(f"{name}={'ok' if ok else 'DRIFT('+actual[:8]+')'}")
    _record("V2_func_sha_lock", all_ok, " ".join(detail_parts))


# ---- V3 ------------------------------------------------------------------
def v3_behavior() -> None:
    try:
        mod = _load_target()
    except Exception as exc:
        _record("V3_behavior", False, f"load fail: {exc}")
        return
    # monotonic-up across all subjects -> any_triggered True
    mono_records = [
        {"drift_report": {s: {"drift": float(i)} for s in ("admit", "reject_main", "reject_preempt")}}
        for i in range(1, 6)
    ]
    r_mono = mod.analyze_drift_history(mono_records, window=5, slope_threshold=0.5)
    mono_ok = r_mono.any_triggered() and all(s.monotonic_up for s in r_mono.subjects)

    # flat -> not triggered
    flat_records = [
        {"drift_report": {s: {"drift": 5.0} for s in ("admit", "reject_main", "reject_preempt")}}
        for _ in range(5)
    ]
    r_flat = mod.analyze_drift_history(flat_records, window=5, slope_threshold=0.5)
    flat_ok = (not r_flat.any_triggered())

    # high-slope but with one equal step (still triggers slope path even if not strictly mono)
    slope_records = [
        {"drift_report": {s: {"drift": float(i * 2)} for s in ("admit", "reject_main", "reject_preempt")}}
        for i in range(5)
    ]
    r_slope = mod.analyze_drift_history(slope_records, window=5, slope_threshold=1.5)
    slope_ok = all(s.slope_exceeded for s in r_slope.subjects)

    ok = mono_ok and flat_ok and slope_ok
    _record(
        "V3_behavior",
        ok,
        f"mono={mono_ok} flat={flat_ok} slope={slope_ok} slopes={[round(s.slope,3) for s in r_slope.subjects]}",
    )


# ---- V4 ------------------------------------------------------------------
def v4_mutant_negative() -> None:
    try:
        mod = _load_target()
    except Exception as exc:
        _record("V4_mutant_negative", False, f"load fail: {exc}")
        return
    # strictly decreasing -> _is_monotonic_up False
    dec = mod._is_monotonic_up([5.0, 4.0, 3.0, 2.0])
    # constant -> _is_monotonic_up False
    const = mod._is_monotonic_up([3.0, 3.0, 3.0])
    # slope of constant -> 0.0
    s = mod._ols_slope([7.0, 7.0, 7.0, 7.0])
    # single point -> slope 0.0, monotonic False
    one = mod._is_monotonic_up([1.0])
    s1 = mod._ols_slope([1.0])
    ok = (not dec) and (not const) and s == 0.0 and (not one) and s1 == 0.0
    _record(
        "V4_mutant_negative",
        ok,
        f"dec={dec} const={const} slope_const={s} one={one} slope_one={s1}",
    )


# ---- V5 ------------------------------------------------------------------
def v5_subprocess_invoke() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "drift.jsonl"
        lines = []
        for i in range(5):
            lines.append(
                json.dumps(
                    {
                        "ts": f"2026-05-23T00:00:0{i}Z",
                        "drift_report": {
                            "admit": {"drift": float(i)},
                            "reject_main": {"drift": float(i)},
                            "reject_preempt": {"drift": float(i)},
                        },
                    }
                )
            )
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(TARGET), str(tmp), "5", "0.5"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        rc_ok = proc.returncode == 0
        # find the JSON payload line
        payload_ok = False
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("kind") == "interact_036b_drift_trend_alert":
                payload_ok = obj.get("any_triggered") is True
                break
        ok = rc_ok and payload_ok
        _record(
            "V5_subprocess_invoke",
            ok,
            f"rc={proc.returncode} payload_triggered={payload_ok} stdout_tail={proc.stdout.splitlines()[-1][:80] if proc.stdout else ''}",
        )


def main() -> int:
    v0_self_sha_lock()
    v1_file_sha_lock()
    v2_func_sha_lock()
    v3_behavior()
    v4_mutant_negative()
    v5_subprocess_invoke()
    failed = [r for r in _results if not r["ok"]]
    print(
        f"\n[verify_interact_036b] SUMMARY total={len(_results)} pass={len(_results)-len(failed)} fail={len(failed)}",
        flush=True,
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
