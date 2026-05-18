"""interact-028 verification: interact docs polish + verify 锁面 (verify-only).

source backlog: interact-016-backlog-doc-polish
direction: doc polish + 同源校验. 锁定 emit_trace 注释与 _RESERVED_TRACE_KEYS
集合与 logging_setup.JsonlFormatter._RESERVED 同源, 修复 N-1 (缺 taskName) +
N-2 (Python 3.13 KeyError 描述过时, 改为 "所有受支持 CPython 版本一致").

跑法::

    uv run python scripts/verify_interact_028.py

子项:

V0 fingerprint sha256 锁 coco/proactive_trace.py / coco/logging_setup.py / self;
V1 doc polish 短语锁 — emit_trace 注释含 "所有受支持 CPython 版本" 与
   "双向同步" 与 "interact-028" 关键短语;
V2 _RESERVED_TRACE_KEYS ⊇ logging_setup.JsonlFormatter._RESERVED 同源校验
   (含 taskName, Python 3.12+ asyncio LogRecord 字段);
V3 cross-ref 完整性 — proactive_trace.py 引用的 logging_setup._RESERVED 实体
   存在且为 set/frozenset 且含相同最小公共子集;
V4 邻近 verify 回归 — interact-018/021..027 rc=0 静态锁面;
V5 smoke 11/11 PASS — ./init.sh rc=0;
V_n evidence/migration_note — 写 evidence/interact-028/.

retval: 0 全 PASS; 1 任一 FAIL
evidence: evidence/interact-028/verify_summary.json
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_028] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# V0 fingerprint sha256
# ---------------------------------------------------------------------------


def v0_fingerprint_sha256() -> None:
    targets = {
        "coco/proactive_trace.py": None,
        "coco/logging_setup.py": None,
        "scripts/verify_interact_028.py": None,
    }
    missing = []
    for rel in targets:
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
        else:
            targets[rel] = _sha256(p)
    if missing:
        _record("V0_fingerprint_sha256", False, f"missing: {missing}")
        return
    _record(
        "V0_fingerprint_sha256",
        True,
        "sha256 锁面: " + ", ".join(f"{k}={v[:12]}" for k, v in targets.items()),
    )


# ---------------------------------------------------------------------------
# V1 doc polish 短语锁
# ---------------------------------------------------------------------------

_REQUIRED_PHRASES = [
    # N-2: "Python 3.13 KeyError" 描述过时 → 改为"所有受支持 CPython 版本一致"
    "所有受支持 CPython 版本",
    # 同源精神
    "双向同步",
    # interact-028 标识
    "interact-028",
    # N-1: taskName 备注
    "Python 3.12+ asyncio task",
    # 保留 interact-016 C-4 历史引用
    "interact-016 C-4",
]


def v1_doc_polish_phrases() -> None:
    src = (ROOT / "coco" / "proactive_trace.py").read_text(encoding="utf-8")
    missing = [p for p in _REQUIRED_PHRASES if p not in src]
    if missing:
        _record(
            "V1_doc_polish_phrases",
            False,
            f"缺关键短语: {missing}",
        )
        return
    _record(
        "V1_doc_polish_phrases",
        True,
        f"全部 {len(_REQUIRED_PHRASES)} 个 polish 短语 present",
    )


# ---------------------------------------------------------------------------
# V2 _RESERVED_TRACE_KEYS ⊇ logging_setup._RESERVED 同源校验
# ---------------------------------------------------------------------------


def v2_reserved_keys_superset() -> None:
    # 直接 import 两个集合做集合差校验
    try:
        from coco.proactive_trace import _RESERVED_TRACE_KEYS
        from coco.logging_setup import JsonlFormatter
    except Exception as e:  # noqa: BLE001
        _record("V2_reserved_keys_superset", False, f"import 失败: {e!r}")
        return

    logging_reserved = set(JsonlFormatter._RESERVED)
    trace_reserved = set(_RESERVED_TRACE_KEYS)
    missing_from_trace = logging_reserved - trace_reserved
    if missing_from_trace:
        _record(
            "V2_reserved_keys_superset",
            False,
            f"_RESERVED_TRACE_KEYS 缺 (相对 logging_setup._RESERVED): "
            f"{sorted(missing_from_trace)}",
        )
        return
    # 也 sanity check N-1 显式: taskName 在两个集合都在
    if "taskName" not in trace_reserved or "taskName" not in logging_reserved:
        _record(
            "V2_reserved_keys_superset",
            False,
            f"taskName 必须双向同步; trace={'taskName' in trace_reserved} "
            f"logging={'taskName' in logging_reserved}",
        )
        return
    _record(
        "V2_reserved_keys_superset",
        True,
        f"_RESERVED_TRACE_KEYS ⊇ logging_setup._RESERVED "
        f"(trace|={len(trace_reserved)} ⊇ logging|={len(logging_reserved)}, "
        f"含 taskName)",
    )


# ---------------------------------------------------------------------------
# V3 cross-ref 完整性
# ---------------------------------------------------------------------------


def v3_cross_ref_integrity() -> None:
    # proactive_trace.py 注释引用 logging_setup.JsonlFormatter._RESERVED
    # 该实体必须存在且是 set/frozenset
    try:
        from coco.logging_setup import JsonlFormatter
    except Exception as e:  # noqa: BLE001
        _record("V3_cross_ref_integrity", False, f"import logging_setup 失败: {e!r}")
        return
    if not hasattr(JsonlFormatter, "_RESERVED"):
        _record(
            "V3_cross_ref_integrity",
            False,
            "JsonlFormatter._RESERVED 不存在 (proactive_trace 注释 cross-ref dangling)",
        )
        return
    obj = JsonlFormatter._RESERVED
    if not isinstance(obj, (set, frozenset)):
        _record(
            "V3_cross_ref_integrity",
            False,
            f"JsonlFormatter._RESERVED 类型应为 set/frozenset, got {type(obj).__name__}",
        )
        return
    # 检 LogRecord 内置字段的最小公共子集
    minimal_common = {"name", "msg", "levelname", "created"}
    missing = minimal_common - set(obj)
    if missing:
        _record(
            "V3_cross_ref_integrity",
            False,
            f"JsonlFormatter._RESERVED 缺 LogRecord 最小子集: {sorted(missing)}",
        )
        return
    _record(
        "V3_cross_ref_integrity",
        True,
        f"cross-ref OK: JsonlFormatter._RESERVED 存在 (|={len(obj)}, "
        f"type={type(obj).__name__}, 含 LogRecord 最小子集)",
    )


# ---------------------------------------------------------------------------
# V4 邻近 verify 回归
# ---------------------------------------------------------------------------


def v4_regression() -> None:
    scripts = [
        "scripts/verify_interact_018.py",
        "scripts/verify_interact_021.py",
        "scripts/verify_interact_022.py",
        "scripts/verify_interact_023.py",
        "scripts/verify_interact_024.py",
        "scripts/verify_interact_025.py",
        "scripts/verify_interact_026.py",
        "scripts/verify_interact_027.py",
    ]
    rcs: Dict[str, int] = {}
    bad: List[str] = []
    for s in scripts:
        try:
            proc = subprocess.run(
                [sys.executable, s],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=240,
            )
            rcs[s] = proc.returncode
            if proc.returncode != 0:
                bad.append(f"{s} rc={proc.returncode}")
        except Exception as e:  # noqa: BLE001
            rcs[s] = -1
            bad.append(f"{s} exc={e!r}")
    if bad:
        _record("V4_regression", False, f"failed: {bad}; rcs={rcs}")
        return
    _record("V4_regression", True, f"all rc=0: {rcs}")


# ---------------------------------------------------------------------------
# V5 smoke 11/11 PASS
# ---------------------------------------------------------------------------


def v5_smoke() -> None:
    init_sh = ROOT / "init.sh"
    if not init_sh.exists():
        _record("V5_smoke", False, "init.sh missing")
        return
    try:
        proc = subprocess.run(
            ["bash", str(init_sh)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=240,
        )
    except Exception as e:  # noqa: BLE001
        _record("V5_smoke", False, f"exc={e!r}")
        return
    if proc.returncode != 0:
        _record(
            "V5_smoke",
            False,
            f"init.sh rc={proc.returncode} stderr tail={proc.stderr[-300:]}",
        )
        return
    _record("V5_smoke", True, f"init.sh rc=0 (stdout len={len(proc.stdout)})")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    v0_fingerprint_sha256()
    v1_doc_polish_phrases()
    v2_reserved_keys_superset()
    v3_cross_ref_integrity()
    v4_regression()
    v5_smoke()

    all_ok = all(r["ok"] for r in _results)

    out_dir = ROOT / "evidence" / "interact-028"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "interact-028",
        "source_backlog": "interact-016-backlog-doc-polish",
        "direction": (
            "doc polish + 同源校验: emit_trace 注释精炼 (N-2 KeyError 版本描述) "
            "+ _RESERVED_TRACE_KEYS 加 taskName 双向同步 logging_setup._RESERVED "
            "(N-1)"
        ),
        "ok": all_ok,
        "results": _results,
        "files_changed": [
            "coco/proactive_trace.py",
            "scripts/verify_interact_028.py",
            "evidence/interact-028/verify_summary.json",
            "evidence/interact-028/migration_note.md",
            "feature_list.json",
            "claude-progress.md",
        ],
        "runtime_change": False,
        "default_off_invariant": True,
        "n1_addressed": "taskName 入 _RESERVED_TRACE_KEYS, 与 logging_setup 双向同步",
        "n2_addressed": "注释中 'Python 3.13 KeyError' 改为 '所有受支持 CPython 版本一致'",
        "c1_c5_status": "doc-only marker; 真 LLM backend hook 接入时再处理",
    }
    summary_path = out_dir / "verify_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _print("INFO", f"evidence written: {summary_path}")

    print()
    print("=" * 60)
    if all_ok:
        print(f"[verify_interact_028] ALL PASS ({len(_results)} checks)")
    else:
        failed = [r["name"] for r in _results if not r["ok"]]
        print(f"[verify_interact_028] FAIL: {failed}")
    print("=" * 60)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
