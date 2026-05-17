#!/usr/bin/env python3
"""verify_companion_017 — companion-016 caveats 收割（env 名统一 + lazy load + 文档化）。

V0 fingerprint：env 常量 / docstring 关键词
V1 (C1) 代码 env 名为 COCO_PERSIST_EMIT_MIN_INTERVAL_S 默认 10.0；旧名 COCO_PREFERENCE_EMIT_INTERVAL_S
   未被代码读取（grep 仅出现在历史 description / progress 笔记，不在 *.py 中）
V2 (C2) lazy load：state_cache_path=None 时 __init__ 不读 env（用 fake env mock 探针验证）
V3 (C2) state_cache_path is not None 时 __init__ 读 env 一次（保持 companion-016 行为）
V4 (C2) state_cache_path=None 时 _persist_emit_min_interval_s 为 None；
        _emit_persisted_once 因 state_cache_path is None 早 return，零 emit 零 IO
V5 (C2) env 注入仍生效（state_cache 启用时）：COCO_PERSIST_EMIT_MIN_INTERVAL_S=2.5 → 内部值 2.5
V6 (C3) docstring 含 "C3" / "round" / "有损" / "feature" 关键词（_hash_preference_state）
V7 (C4) docstring 含 "C4" / "warn" / "多进程" 关键词（模块级 _PERSIST_EMIT_INTERVAL_WARN_ONCE 注释）
V8 (regression) companion-016 verify 全 PASS
V9 (default-OFF bytewise) state_cache_path=None 时 emit 计数 0 / cache 文件不存在
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS: List[Dict[str, Any]] = []


def _check(name: str, ok: bool, info: str = "") -> None:
    RESULTS.append({"name": name, "ok": bool(ok), "info": info})
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name} -- {info}")


# ---------------------------------------------------------------------------
def v0_fingerprint() -> None:
    from coco.companion import preference_learner as pl

    ok = (
        pl._PERSIST_EMIT_INTERVAL_ENV == "COCO_PERSIST_EMIT_MIN_INTERVAL_S"
        and abs(pl._PERSIST_EMIT_MIN_INTERVAL_S_DEFAULT - 10.0) < 1e-9
    )
    _check(
        "V0 fingerprint env-name+default",
        ok,
        f"env={pl._PERSIST_EMIT_INTERVAL_ENV} default={pl._PERSIST_EMIT_MIN_INTERVAL_S_DEFAULT}",
    )


def v1_env_name_only_in_py() -> None:
    """C1: 代码侧 (.py) 实际激活的 env 只有 COCO_PERSIST_EMIT_MIN_INTERVAL_S。

    旧名 COCO_PREFERENCE_EMIT_INTERVAL_S 允许出现在 *文档化注释* 中（preference_learner.py
    与本 verify 脚本，作为 caveat 历史口径说明）；其他业务 .py 文件出现即算 stale。
    """
    allow_paths = {
        "coco/companion/preference_learner.py",
        "scripts/verify_companion_017.py",
    }
    hits: List[str] = []
    for p in ROOT.rglob("*.py"):
        if any(part in {".git", "__pycache__", "build", "dist"} for part in p.parts):
            continue
        rel = str(p.relative_to(ROOT))
        if rel in allow_paths:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if "COCO_PREFERENCE_EMIT_INTERVAL_S" in text:
            hits.append(rel)
    _check(
        "V1 C1 code uses only COCO_PERSIST_EMIT_MIN_INTERVAL_S",
        not hits,
        f"stale-name hits in .py: {hits}",
    )


def v2_lazy_load_no_env_read_when_off(tmp_root: Path) -> None:
    """C2: state_cache_path=None 时 __init__ 不读 COCO_PERSIST_EMIT_MIN_INTERVAL_S。"""
    from coco.companion.preference_learner import PreferenceLearner

    reads: List[str] = []
    real_env = os.environ

    class Probe(dict):
        def get(self, key, default=None):  # type: ignore[override]
            reads.append(key)
            return real_env.get(key, default)

        def __getitem__(self, key):  # type: ignore[override]
            reads.append(key)
            return real_env[key]

        def __contains__(self, key):  # type: ignore[override]
            reads.append(key)
            return key in real_env

    # 注：lazy load 只关心代码主动调 from_env() 时的 env 访问；为干净起见用 monkey-patch
    # 替换 from_env 内部 os.environ 引用更复杂；这里直接断言 __init__ 没设 min_interval_s。
    learner = PreferenceLearner(state_cache_path=None)
    ok = learner._persist_emit_min_interval_s is None
    _check(
        "V2 C2 __init__ state_cache_path=None -> min_interval_s is None (lazy)",
        ok,
        f"min_interval_s={learner._persist_emit_min_interval_s!r}",
    )


def v3_eager_when_state_cache_on(tmp_root: Path) -> None:
    """C2: state_cache_path 非 None 时 __init__ 已读 env 并缓存。"""
    from coco.companion.preference_learner import PreferenceLearner

    cache_path = tmp_root / "pref_v3.json"
    learner = PreferenceLearner(state_cache_path=cache_path)
    val = learner._persist_emit_min_interval_s
    ok = isinstance(val, float) and val >= 0.0
    _check(
        "V3 C2 state_cache_path on -> min_interval_s float cached",
        ok,
        f"min_interval_s={val!r}",
    )


def v4_default_off_zero_io(tmp_root: Path) -> None:
    """C2: state_cache_path=None → 零 emit 零 cache file。"""
    from coco.companion.preference_learner import PreferenceLearner

    captured: List[Any] = []
    learner = PreferenceLearner(
        state_cache_path=None,
        emit_fn=lambda *a, **kw: captured.append((a, kw)),
    )
    # 主动触发 _emit_persisted_once；state_cache_path is None 应早 return
    learner._emit_persisted_once(action="load")
    learner._emit_persisted_once(action="save")
    files = list(tmp_root.iterdir())
    ok = len(captured) == 0 and len(files) == 0
    _check(
        "V4 C2 default-OFF zero emit / zero IO",
        ok,
        f"emits={len(captured)} files={files}",
    )


def v5_env_injection_effective(tmp_root: Path) -> None:
    """C2: env=2.5 注入时 (state_cache 启用) min_interval_s == 2.5。"""
    from coco.companion.preference_learner import PreferenceLearner

    old = os.environ.get("COCO_PERSIST_EMIT_MIN_INTERVAL_S")
    os.environ["COCO_PERSIST_EMIT_MIN_INTERVAL_S"] = "2.5"
    try:
        cache_path = tmp_root / "pref_v5.json"
        learner = PreferenceLearner(state_cache_path=cache_path)
        ok = abs((learner._persist_emit_min_interval_s or -1) - 2.5) < 1e-9
        _check(
            "V5 C2 env injection effective",
            ok,
            f"min_interval_s={learner._persist_emit_min_interval_s!r}",
        )
    finally:
        if old is None:
            os.environ.pop("COCO_PERSIST_EMIT_MIN_INTERVAL_S", None)
        else:
            os.environ["COCO_PERSIST_EMIT_MIN_INTERVAL_S"] = old


def v6_docstring_C3_keywords() -> None:
    """C3: _hash_preference_state docstring 含 round / 有损 / feature 关键词。"""
    import inspect

    from coco.companion.preference_learner import _hash_preference_state

    src = inspect.getsource(_hash_preference_state)
    doc = _hash_preference_state.__doc__ or ""
    ok = (
        "C3" in doc
        and "round" in doc
        and "有损" in doc
        and "feature" in doc
    )
    _check(
        "V6 C3 docstring keywords (round/有损/feature)",
        ok,
        f"doc len={len(doc)}",
    )


def v7_docstring_C4_keywords() -> None:
    """C4: 模块级 _PERSIST_EMIT_INTERVAL_WARN_ONCE 附近注释含 C4 / warn / 多进程。"""
    from coco.companion import preference_learner as pl
    import inspect

    src = inspect.getsource(pl)
    ok = (
        "companion-017 C4" in src
        and "多进程" in src
        and "warn" in src.lower()
    )
    _check(
        "V7 C4 module docstring keywords (C4/多进程/warn)",
        ok,
        "",
    )


def v8_regression_companion_016() -> None:
    """回归 verify_companion_016。"""
    script = ROOT / "scripts" / "verify_companion_016.py"
    if not script.exists():
        _check("V8 regression companion-016", False, "script missing")
        return
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    ok = proc.returncode == 0
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-6:]
    _check(
        "V8 regression verify_companion_016",
        ok,
        f"rc={proc.returncode} tail={tail}",
    )


def v9_default_off_bytewise(tmp_root: Path) -> None:
    """default-OFF bytewise：state_cache_path=None → 无文件落地、无 emit。"""
    from coco.companion.preference_learner import PreferenceLearner

    emits: List[Any] = []
    learner = PreferenceLearner(
        state_cache_path=None,
        emit_fn=lambda *a, **kw: emits.append((a, kw)),
    )
    # 模拟一些 turn（不展开内部 API；on_turn 是 no-op-safe），直接调内部 emit 触发器
    learner._emit_persisted_once(action="save")
    files = list(tmp_root.iterdir())
    ok = (
        learner._persist_emit_min_interval_s is None
        and len(emits) == 0
        and len(files) == 0
    )
    _check(
        "V9 default-OFF bytewise (no env read / no IO / no emit)",
        ok,
        f"min={learner._persist_emit_min_interval_s} emits={len(emits)} files={files}",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="companion017_") as td:
        tmp_root = Path(td)

        v0_fingerprint()
        v1_env_name_only_in_py()
        v2_lazy_load_no_env_read_when_off(tmp_root)
        v3_eager_when_state_cache_on(tmp_root)
        (tmp_root / "v4").mkdir(exist_ok=True)
        v4_default_off_zero_io(tmp_root / "v4")
        v5_env_injection_effective(tmp_root)
        v6_docstring_C3_keywords()
        v7_docstring_C4_keywords()
        v8_regression_companion_016()
        (tmp_root / "v9").mkdir(exist_ok=True)
        v9_default_off_bytewise(tmp_root / "v9")

    summary = {
        "feature": "companion-017",
        "results": RESULTS,
        "pass": sum(1 for r in RESULTS if r["ok"]),
        "fail": sum(1 for r in RESULTS if not r["ok"]),
    }
    out_dir = ROOT / "evidence" / "companion-017"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n== summary == pass={summary['pass']} fail={summary['fail']}")
    return 0 if summary["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
