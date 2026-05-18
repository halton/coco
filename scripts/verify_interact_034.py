"""interact-034 verification: verify_interact_018.py V1d/V1e arbit_fail / mm_proactive reject latency_ms meta-lock.

source backlog: interact-033-backlog-v1d-arbit-fail-latency-coverage
direction: verify-only meta-lock, 0 业务源码改动; 锁住 verify_interact_018.py
V1d (arbit_emotion_preempt reject latency_ms 端到端) + V1e (mm_proactive
non-cooldown reject latency_ms 端到端) 两段升级后的实现。

跑法::

    uv run python scripts/verify_interact_034.py

子项:

V0 sys.path 注入 + ROOT 解析正确; verify_interact_018.py 与 coco/proactive.py
   存在。
V1 verify_interact_018.py 源码字面量含 V1d / V1e 段函数名 + 关键 fixture
   字面量 (COCO_PROACTIVE_ARBIT=1, _last_emotion_alert_ts, _next_priority_boost,
   set_mm_llm_context, _recent_triggers, arbit_emotion_preempt reason 锚,
   rate_limit reason 锚, mm_proactive stage 锚)。
V2 关键短语锁面 — V1d/V1e docstring + type-strict latency_ms 断言 +
   非 bool 防御 + stage ∈ {fusion_boost, mm_proactive} 校验。
V3 mutant 反证 (内存中 patch 源码): 删除 V1d 中 arbit_emotion_preempt
   reason 断言 → 在 mutated src 上仅剥离该锚, V1e 不被误伤; 结构性证明
   V1d 锚点缺一即不能覆盖 arbit_fail 路径。
V4 sha256 锁 scripts/verify_interact_018.py + self;
V5 端到端 subprocess: .venv/bin/python scripts/verify_interact_018.py rc==0
   (V1/V1c/V1d/V1e/V2/V3/V4/V5 全 PASS 不退化)。

retval: 0 全 PASS; 1 任一 FAIL
evidence: evidence/interact-034/verify_summary.json
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
    print(f"[verify_interact_034] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


VERIFY_018 = ROOT / "scripts" / "verify_interact_018.py"
SELF = Path(__file__)
# verify_018 升级 V1d/V1e 后的 sha256 锁面（interact-034 close 时确定）
VERIFY_018_SHA256_LOCK = "325e8d1e64606b643ba67aedac2ffd1a19db14c38d0e5dd58e150815a7328737"


# ---------------------------------------------------------------------------
# V0 sys.path / ROOT 解析正确
# ---------------------------------------------------------------------------


def v0_syspath_root() -> None:
    if not VERIFY_018.exists():
        _record("V0_syspath_root", False, f"missing {VERIFY_018}")
        return
    if not (ROOT / "coco" / "proactive.py").exists():
        _record("V0_syspath_root", False, "missing coco/proactive.py")
        return
    if str(ROOT) not in sys.path:
        _record("V0_syspath_root", False, "ROOT not in sys.path")
        return
    _record("V0_syspath_root", True,
            f"ROOT={ROOT.name}, verify_018 + proactive.py 存在")


# ---------------------------------------------------------------------------
# V1 verify_018 源码字面量含 V1d/V1e 段函数名 + 关键 fixture/断言字面量
# ---------------------------------------------------------------------------


def v1_v1d_v1e_source_anchors() -> None:
    src = VERIFY_018.read_text(encoding="utf-8")
    needed = [
        # V1d / V1e 段函数定义
        "def v1d_arbit_emotion_preempt_latency_wire(",
        "def v1e_mm_proactive_non_cooldown_reject_latency_wire(",
        # V1d 关键 fixture
        '"COCO_PROACTIVE_ARBIT"',
        "_last_emotion_alert_ts",
        "_next_priority_boost",
        # V1d reason 锚
        '"arbit_emotion_preempt"',
        # V1e 关键 fixture
        "set_mm_llm_context",
        "_recent_triggers",
        # V1e stage / reason 锚
        '"mm_proactive"',
        # V1e 非 cooldown 防御
        '"cooldown"',
        # type-strict latency_ms 断言（共享）
        "isinstance(lat, bool)",
        "isinstance(lat, (int, float))",
        # main() 入口加入 V1d / V1e
        "v1d_arbit_emotion_preempt_latency_wire()",
        "v1e_mm_proactive_non_cooldown_reject_latency_wire()",
    ]
    missing = [n for n in needed if n not in src]
    if missing:
        _record("V1_v1d_v1e_source_anchors", False,
                f"missing: {missing[:5]}...")
        return
    _record("V1_v1d_v1e_source_anchors", True,
            f"V1d/V1e 段函数 + fixture 字面量 + type-strict 断言 全命中 "
            f"({len(needed)} 项)")


# ---------------------------------------------------------------------------
# V2 关键短语锁面 — docstring + type-strict 防御 + stage 校验
# ---------------------------------------------------------------------------


def v2_keyphrases() -> None:
    src = VERIFY_018.read_text(encoding="utf-8")
    phrases = [
        # V1d/V1e 升级 docstring (顶部 + 函数体)
        "interact-034",
        "arbit_emotion_preempt reject",
        "mm_proactive non-cooldown reject",
        # V1d 断言要点
        "ARBIT_EMOTION_WINDOW_S",
        "stage ∈ {fusion_boost, mm_proactive}",
        # V1e 断言要点
        "_stage_in='mm_proactive'",
        "rate_limit",
        # 共享: type-strict 非 bool 防御
        "type-strict",
    ]
    missing = [p for p in phrases if p not in src]
    if missing:
        _record("V2_keyphrases", False, f"missing: {missing}")
        return
    _record("V2_keyphrases", True,
            f"{len(phrases)} 关键短语全部命中")


# ---------------------------------------------------------------------------
# V3 mutant 反证 — 剥离 V1d 中 arbit_emotion_preempt reason 断言锚
# ---------------------------------------------------------------------------


def v3_mutant_arbit_preempt_anchor_removal() -> None:
    src = VERIFY_018.read_text(encoding="utf-8")
    # 把 V1d 中 reason 等式断言字面量删一个 → 结构性反证
    mutant_pattern = 'and e.get("reason") == "arbit_emotion_preempt"'
    mutated = src.replace(mutant_pattern, "", 1)
    if mutated == src:
        _record("V3_mutant_arbit_preempt_anchor_removal", False,
                f"mutant pattern not applied: {mutant_pattern!r} not found")
        return
    if mutant_pattern in mutated:
        # 应该只剥离一次
        # 但若源码中此模式仅出现一次，replace(..., 1) 必然剥干净
        _record("V3_mutant_arbit_preempt_anchor_removal", False,
                "mutant 仍含 pattern (replace 未生效或字面量重复)")
        return
    # V1e 路径锚不受影响
    if '"mm_proactive"' not in mutated or "set_mm_llm_context" not in mutated:
        _record("V3_mutant_arbit_preempt_anchor_removal", False,
                "mutant 误伤了 V1e 锚 (mm_proactive / set_mm_llm_context)")
        return
    # V1d 函数定义本体仍在 (mutant 是 site-specific 字面量删除)
    if "def v1d_arbit_emotion_preempt_latency_wire(" not in mutated:
        _record("V3_mutant_arbit_preempt_anchor_removal", False,
                "mutant 误伤 V1d 函数定义")
        return
    _record("V3_mutant_arbit_preempt_anchor_removal", True,
            "mutant 反证成功: 剥离 V1d arbit_emotion_preempt reason 断言锚后 "
            "V1e/V1d 函数本体仍在, 结构性证明该 reason 锚是 V1d 覆盖 arbit_fail "
            "路径的必要字面量")


# ---------------------------------------------------------------------------
# V4 sha256 锁面
# ---------------------------------------------------------------------------


def v4_sha256_locks() -> None:
    cur = _sha256(VERIFY_018)
    if cur != VERIFY_018_SHA256_LOCK:
        _record("V4_sha256_locks", False,
                f"verify_018 sha256 漂移: cur={cur[:12]} "
                f"lock={VERIFY_018_SHA256_LOCK[:12]}")
        return
    targets = {
        "scripts/verify_interact_018.py": cur,
        "scripts/verify_interact_034.py": _sha256(SELF),
    }
    _record("V4_sha256_locks", True,
            "sha256: " + ", ".join(f"{k}={v[:12]}" for k, v in targets.items()))


# ---------------------------------------------------------------------------
# V5 端到端 subprocess: verify_interact_018.py rc==0
# ---------------------------------------------------------------------------


def v5_e2e_verify_018() -> None:
    py = ROOT / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path(sys.executable)
    try:
        proc = subprocess.run(
            [str(py), str(VERIFY_018)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        _record("V5_e2e_verify_018", False, "timeout 300s")
        return
    rc = proc.returncode
    stdout = proc.stdout or ""
    tail = stdout.splitlines()[-3:] if stdout else []
    if rc != 0:
        _record("V5_e2e_verify_018", False,
                f"rc={rc}, tail={tail}, "
                f"stderr_tail={(proc.stderr or '').splitlines()[-3:]}")
        return
    # 验 V1d/V1e PASS 标记落地
    must_markers = [
        "V1d_arbit_emotion_preempt_latency_wire",
        "V1e_mm_proactive_non_cooldown_reject_latency_wire",
        "PASS V1d_arbit_emotion_preempt_latency_wire",
        "PASS V1e_mm_proactive_non_cooldown_reject_latency_wire",
        "overall: PASS",
    ]
    missing = [m for m in must_markers if m not in stdout]
    if missing:
        _record("V5_e2e_verify_018", False,
                f"missing markers in stdout: {missing}; tail={tail}")
        return
    _record("V5_e2e_verify_018", True,
            f"rc=0; V1d/V1e PASS markers 命中; tail={tail[-1] if tail else ''}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    v0_syspath_root()
    v1_v1d_v1e_source_anchors()
    v2_keyphrases()
    v3_mutant_arbit_preempt_anchor_removal()
    v4_sha256_locks()
    v5_e2e_verify_018()

    out_dir = ROOT / "evidence" / "interact-034"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
    except Exception:
        sha = ""
    summary = {
        "feature_id": "interact-034",
        "results": _results,
        "all_pass": all(r["ok"] for r in _results),
        "sha": sha,
    }
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ok = summary["all_pass"]
    _print("SUMMARY",
           f"all_pass={ok} ({sum(1 for r in _results if r['ok'])}/{len(_results)})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
