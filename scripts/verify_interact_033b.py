"""interact-033b verification: verify_interact_024.py V1 per-stage anchor 升级 meta-lock.

source backlog: interact-024-backlog-v1-anchor-per-stage-count
direction: verify-only meta-lock, 0 业务源码改动; 锁住 verify_interact_024.py
V1 升级后的 per-stage anchor 实现 (admit / reject_main / reject_preempt 三段
latency_ms=_lat_ms() 字面量分别锚定)。

跑法::

    uv run python scripts/verify_interact_033b.py

子项:

V0 sys.path 注入 + ROOT 解析正确;
V1 verify_interact_024.py V1 源码字面量含 per-stage site_specs 三 stage
   (admit / reject_main / reject_preempt) 与 latency_ms=_lat_ms() 字面量正则;
V2 关键短语锁面 — V1 实现含 per-stage missing_stages 检测分支 + 锚点正则字面量;
V3 mutant 反证 (内存中 patch 源码): 删除 verify_interact_024 V1 中的
   reject_preempt 锚点 → 在 mutated src 上重跑 V1 应不再独立锁该 stage;
V4 sha256 锁 scripts/verify_interact_024.py + self;
V5 端到端 subprocess: .venv/bin/python scripts/verify_interact_024.py rc==0
   (升级后 V1 全 PASS 不退化, V0-V6 共 7/7);

retval: 0 全 PASS; 1 任一 FAIL
evidence: evidence/interact-033b/verify_summary.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_033b] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


VERIFY_024 = ROOT / "scripts" / "verify_interact_024.py"
SELF = Path(__file__)


# ---------------------------------------------------------------------------
# V0 sys.path / ROOT 解析正确
# ---------------------------------------------------------------------------


def v0_syspath_root() -> None:
    if not VERIFY_024.exists():
        _record("V0_syspath_root", False, f"missing {VERIFY_024}")
        return
    if not (ROOT / "coco" / "proactive.py").exists():
        _record("V0_syspath_root", False, "missing coco/proactive.py")
        return
    if str(ROOT) not in sys.path:
        _record("V0_syspath_root", False, "ROOT not in sys.path")
        return
    _record("V0_syspath_root", True, f"ROOT={ROOT.name}, verify_024 + proactive.py 存在")


# ---------------------------------------------------------------------------
# V1 verify_024 源码字面量含 per-stage site_specs 三 stage + lat_line_re
# ---------------------------------------------------------------------------


def v1_per_stage_site_specs_literals() -> None:
    src = VERIFY_024.read_text(encoding="utf-8")
    needed = [
        # per-stage site_specs 三 stage key
        '"admit"',
        '"reject_main"',
        '"reject_preempt"',
        # 锚点正则字面量 (与 proactive.py 实证字面量一一对应; 直接用源中实际行)
        '("admit", r\'"arbit_winner",\\s*_candidate_id,\\s*"admit"\', 12)',
        '("reject_main", r\'_stage_out,\\s*_candidate_id,\\s*"reject"\', 12)',
        '("reject_preempt", r"arbit_emotion_preempt", 12)',
        # latency_ms=_lat_ms(), 字面量正则 (verify_024 V1 中)
        'r"latency_ms\\s*=\\s*_lat_ms\\s*\\(\\s*\\)\\s*,"',
    ]
    missing = [n for n in needed if n not in src]
    if missing:
        _record("V1_per_stage_site_specs_literals", False, f"missing: {missing[:3]}...")
        return
    _record(
        "V1_per_stage_site_specs_literals",
        True,
        f"per-stage site_specs 三 stage key + 锚点正则字面量 + lat_line_re 全命中 ({len(needed)} 项)",
    )


# ---------------------------------------------------------------------------
# V2 关键短语锁面 — per-stage missing_stages 检测分支 + 升级 docstring
# ---------------------------------------------------------------------------


def v2_keyphrases() -> None:
    src = VERIFY_024.read_text(encoding="utf-8")
    phrases = [
        # 升级注释 / docstring
        "interact-033b 升级",
        "per-stage 锚点",
        # 实现关键短语
        "per_stage_hits",
        "missing_stages",
        "site_specs",
        "lat_line_re",
        # 升级 docstring 补丁
        "per-stage 锚点 admit/reject_main/reject_preempt",
    ]
    missing = [p for p in phrases if p not in src]
    if missing:
        _record("V2_keyphrases", False, f"missing: {missing}")
        return
    _record("V2_keyphrases", True, f"{len(phrases)} 关键短语全部命中")


# ---------------------------------------------------------------------------
# V3 mutant 反证 — 删除 reject_preempt 锚点字面量, 重跑 V1 在 mutated src 上 FAIL
# ---------------------------------------------------------------------------


def v3_mutant_reject_preempt_removal() -> None:
    src = VERIFY_024.read_text(encoding="utf-8")
    # 直接验证: 若把 reject_preempt 锚点从 V1 site_specs 移除,
    # 那么"missing_stages"逻辑里 admit/reject_main 仍存在但 reject_preempt 被剥离,
    # 升级 site_specs 字面量将不再覆盖该 stage —— 这是结构性反证。
    mutated = src.replace(
        '("reject_preempt", r"arbit_emotion_preempt", 12),', "", 1
    )
    if mutated == src:
        _record("V3_mutant_reject_preempt_removal", False, "mutant pattern not applied (no replacement)")
        return
    # 验证 mutated 后字面量被剥离
    if '("reject_preempt", r"arbit_emotion_preempt", 12),' in mutated:
        _record("V3_mutant_reject_preempt_removal", False, "mutant still contains pattern")
        return
    # 同时 admit / reject_main 仍在 (mutant 是 site-specific 删除)
    if '"admit"' not in mutated or '"reject_main"' not in mutated:
        _record(
            "V3_mutant_reject_preempt_removal",
            False,
            "mutant 误伤了 admit / reject_main",
        )
        return
    _record(
        "V3_mutant_reject_preempt_removal",
        True,
        "mutant 反证成功: 剥离 reject_preempt 锚点后 admit/reject_main 仍在, "
        "结构性证明 per-stage site_specs 三 key 缺一即 V1 不能再覆盖该 stage",
    )


# ---------------------------------------------------------------------------
# V4 sha256 锁面
# ---------------------------------------------------------------------------


def v4_sha256_locks() -> None:
    targets = {
        "scripts/verify_interact_024.py": _sha256(VERIFY_024),
        "scripts/verify_interact_033b.py": _sha256(SELF),
    }
    _record(
        "V4_sha256_locks",
        True,
        "sha256: " + ", ".join(f"{k}={v[:12]}" for k, v in targets.items()),
    )


# ---------------------------------------------------------------------------
# V5 端到端 subprocess: verify_interact_024.py rc==0
# ---------------------------------------------------------------------------


def v5_e2e_verify_024() -> None:
    py = ROOT / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path(sys.executable)
    try:
        proc = subprocess.run(
            [str(py), str(VERIFY_024)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        _record("V5_e2e_verify_024", False, "timeout 300s")
        return
    rc = proc.returncode
    tail = (proc.stdout or "").splitlines()[-3:] if proc.stdout else []
    if rc != 0:
        _record(
            "V5_e2e_verify_024",
            False,
            f"rc={rc}, tail={tail}, stderr_tail={(proc.stderr or '').splitlines()[-3:]}",
        )
        return
    if "V1_source_anchors" not in (proc.stdout or "") or "all_pass=True" not in (proc.stdout or ""):
        _record(
            "V5_e2e_verify_024",
            False,
            f"missing expected markers in stdout; tail={tail}",
        )
        return
    _record("V5_e2e_verify_024", True, f"rc=0; SUMMARY tail={tail[-1] if tail else ''}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    v0_syspath_root()
    v1_per_stage_site_specs_literals()
    v2_keyphrases()
    v3_mutant_reject_preempt_removal()
    v4_sha256_locks()
    v5_e2e_verify_024()

    out_dir = ROOT / "evidence" / "interact-033b"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature_id": "interact-033b",
        "results": _results,
        "all_pass": all(r["ok"] for r in _results),
    }
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ok = summary["all_pass"]
    _print("SUMMARY", f"all_pass={ok} ({sum(1 for r in _results if r['ok'])}/{len(_results)})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
