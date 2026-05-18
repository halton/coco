"""interact-035 verification: V1 per-stage 锚点行号漂移 sanity (warn-only) meta-lock.

source backlog: interact-033b-backlog-line-number-drift-detection
direction: verify-only - 在 scripts/verify_interact_024.py V1 中加 expected_line +
drift_tolerance 行号窗口 sanity (warn-only, 不影响 PASS/FAIL); 0 业务源码改动.

baseline (main a735a6b): admit=1056, reject_main=1025, reject_preempt=997, tolerance=20.

跑法::

    uv run python scripts/verify_interact_035.py

子项:

V0 sys.path - import 路径锁面;
V1 字面 sentinel - verify_interact_024.py 含 'expected_line=' / 'drift_tolerance=' /
   'warn-only' 短语 + 三个 EXPECTED_LINE 字面值 (1056/1025/997);
V2 关键锁参数 - drift_tolerance=20 字面值 + 三个 expected_line 数值;
V3 mutant 反证 - 临时把某 expected_line 改远 -> 期望 warn 出现且 V1 仍 PASS (drift 不 FAIL);
V4 sha256 锁 verify_interact_024.py 升级后 hash;
V5 端到端 subprocess invoke verify_interact_024.py rc==0 + drift_report 存在.

retval: 0 全 PASS; 1 任一 FAIL
evidence: evidence/interact-035/verify_summary.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERIFY_024 = ROOT / "scripts" / "verify_interact_024.py"

EXPECTED_LINES = {
    "admit": 1056,
    "reject_main": 1025,
    "reject_preempt": 997,
}
EXPECTED_TOLERANCE = 20
EXPECTED_V024_SHA256 = "9ea18966813f421ee82591de2549efe0abc20fac3b37f43abf0a1ea8316c7c97"


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_035] {tag} {msg}", flush=True)


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
# V0 sys.path
# ---------------------------------------------------------------------------


def v0_sys_path() -> None:
    if str(ROOT) not in sys.path:
        _record("V0_sys_path", False, f"{ROOT} not in sys.path")
        return
    if not VERIFY_024.exists():
        _record("V0_sys_path", False, f"missing {VERIFY_024}")
        return
    _record("V0_sys_path", True, f"ROOT in sys.path; verify_024 exists")


# ---------------------------------------------------------------------------
# V1 字面 sentinel
# ---------------------------------------------------------------------------


def v1_sentinels() -> None:
    src = VERIFY_024.read_text(encoding="utf-8")
    needed = [
        "expected_line=",
        "drift_tolerance=",
        "warn-only",
        "interact-035",
        "drift_report",
        "_v1_drift_report",
        # 三个 EXPECTED_LINE 字面值 (出现在 site_specs tuple 内):
        "1056",
        "1025",
        "997",
    ]
    missing = [s for s in needed if s not in src]
    if missing:
        _record("V1_sentinels", False, f"missing sentinels: {missing}")
        return
    _record("V1_sentinels", True, f"{len(needed)} sentinels 全部命中 (含三 EXPECTED_LINE)")


# ---------------------------------------------------------------------------
# V2 关键锁参数
# ---------------------------------------------------------------------------


def v2_lock_params() -> None:
    src = VERIFY_024.read_text(encoding="utf-8")
    # site_specs tuple 字面值精确匹配: (key, pat, win, expected_line, drift_tolerance)
    patterns = [
        (r'\(\s*"admit"\s*,[^()]+,\s*12\s*,\s*1056\s*,\s*20\s*\)', "admit_tuple"),
        (r'\(\s*"reject_main"\s*,[^()]+,\s*12\s*,\s*1025\s*,\s*20\s*\)', "reject_main_tuple"),
        (r'\(\s*"reject_preempt"\s*,[^()]+,\s*12\s*,\s*997\s*,\s*20\s*\)', "reject_preempt_tuple"),
    ]
    missing = []
    for pat, name in patterns:
        if not re.search(pat, src):
            missing.append(name)
    if missing:
        _record("V2_lock_params", False, f"site_specs tuple 缺失: {missing}")
        return
    _record(
        "V2_lock_params",
        True,
        f"site_specs 三 tuple 字面值锁面 OK (expected_line=1056/1025/997, drift_tolerance=20)",
    )


# ---------------------------------------------------------------------------
# V3 mutant 反证 - 改 expected_line, 期望 warn 出现 + V1 仍 PASS (drift warn-only)
# ---------------------------------------------------------------------------


def v3_mutant_warn_only() -> None:
    """临时把 admit expected_line 从 1056 改成 1 (远超 tolerance), 验证:
    - verify_024 整体 rc == 0 (V1 仍 PASS, drift 不 FAIL)
    - stdout 出现 WARN drift 行
    - drift_report admit.drift 大且 within_tolerance=False
    """
    src = VERIFY_024.read_text(encoding="utf-8")
    backup = src
    # 把 admit tuple 中 expected_line 1056 改成 1 (其它不动).
    mutated = re.sub(
        r'(\(\s*"admit"\s*,[^()]+?,\s*12\s*,\s*)1056(\s*,\s*20\s*\))',
        r"\g<1>1\g<2>",
        src,
        count=1,
    )
    if mutated == src:
        _record("V3_mutant_warn_only", False, "未能 mutate admit expected_line (regex 没匹配)")
        return
    try:
        VERIFY_024.write_text(mutated, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(VERIFY_024)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        rc_ok = proc.returncode == 0
        warn_seen = "WARN" in proc.stdout and "drift sanity" in proc.stdout and "stage=admit" in proc.stdout
        # 读 mutated summary
        summary_p = ROOT / "evidence" / "interact-024" / "verify_summary.json"
        admit_drift = None
        admit_within = None
        try:
            s = json.loads(summary_p.read_text(encoding="utf-8"))
            admit_entry = (s.get("v1_drift_report") or {}).get("admit") or {}
            admit_drift = admit_entry.get("drift")
            admit_within = admit_entry.get("within_tolerance")
        except Exception:  # noqa: BLE001
            pass
        bad = []
        if not rc_ok:
            bad.append(f"rc={proc.returncode} (期望 V1 仍 PASS warn-only)")
        if not warn_seen:
            bad.append(f"stdout 无 WARN drift sanity admit 行 (tail={proc.stdout[-300:]})")
        if admit_within is not False:
            bad.append(f"admit within_tolerance={admit_within!r} (期望 False)")
        if not isinstance(admit_drift, int) or admit_drift < EXPECTED_TOLERANCE:
            bad.append(f"admit drift={admit_drift!r} (期望 > {EXPECTED_TOLERANCE})")
        if bad:
            _record("V3_mutant_warn_only", False, f"issues: {bad}")
            return
        _record(
            "V3_mutant_warn_only",
            True,
            f"mutant admit expected_line=1 -> rc=0 (warn-only), drift={admit_drift}, within=False",
        )
    finally:
        # 还原 + 重跑一遍, 把 evidence/verify_summary.json 恢复成正常状态
        VERIFY_024.write_text(backup, encoding="utf-8")
        try:
            subprocess.run(
                [sys.executable, str(VERIFY_024)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=300,
            )
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# V4 sha256 锁 verify_024 升级后 hash
# ---------------------------------------------------------------------------


def v4_sha256_lock() -> None:
    actual = _sha256(VERIFY_024)
    if actual != EXPECTED_V024_SHA256:
        _record(
            "V4_sha256_lock",
            False,
            f"verify_024 sha256 mismatch: expected={EXPECTED_V024_SHA256[:16]}... actual={actual[:16]}...",
        )
        return
    _record("V4_sha256_lock", True, f"verify_024 sha256={actual[:16]}... matches lock")


# ---------------------------------------------------------------------------
# V5 端到端 subprocess invoke verify_024 rc==0 + drift_report 存在
# ---------------------------------------------------------------------------


def v5_end_to_end_invoke() -> None:
    proc = subprocess.run(
        [sys.executable, str(VERIFY_024)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        _record(
            "V5_end_to_end_invoke",
            False,
            f"verify_024 rc={proc.returncode} stderr_tail={proc.stderr[-300:]}",
        )
        return
    summary_p = ROOT / "evidence" / "interact-024" / "verify_summary.json"
    if not summary_p.exists():
        _record("V5_end_to_end_invoke", False, "verify_summary.json missing")
        return
    try:
        s = json.loads(summary_p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        _record("V5_end_to_end_invoke", False, f"json decode: {e!r}")
        return
    dr = s.get("v1_drift_report") or {}
    bad = []
    for stage, exp in EXPECTED_LINES.items():
        entry = dr.get(stage) or {}
        if entry.get("expected_line") != exp:
            bad.append(f"{stage}.expected_line={entry.get('expected_line')!r} (need {exp})")
        if entry.get("drift_tolerance") != EXPECTED_TOLERANCE:
            bad.append(f"{stage}.drift_tolerance={entry.get('drift_tolerance')!r} (need {EXPECTED_TOLERANCE})")
        if entry.get("within_tolerance") is not True:
            bad.append(f"{stage}.within_tolerance={entry.get('within_tolerance')!r} (need True)")
    if bad:
        _record("V5_end_to_end_invoke", False, f"drift_report issues: {bad}")
        return
    _record(
        "V5_end_to_end_invoke",
        True,
        f"verify_024 rc=0; drift_report 三 stage within_tolerance=True (drift=0)",
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    v0_sys_path()
    v1_sentinels()
    v2_lock_params()
    v3_mutant_warn_only()
    v4_sha256_lock()
    v5_end_to_end_invoke()

    all_ok = all(r["ok"] for r in _results)

    out_dir = ROOT / "evidence" / "interact-035"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "interact-035",
        "source_backlog": "interact-033b-backlog-line-number-drift-detection",
        "direction": "verify-only: 在 verify_interact_024.py V1 中加 expected_line + drift_tolerance 行号窗口 sanity (warn-only, 不影响 PASS/FAIL); 0 业务源码改动",
        "baseline": {
            "main_sha": "a735a6b",
            "expected_lines": EXPECTED_LINES,
            "drift_tolerance": EXPECTED_TOLERANCE,
            "verify_024_sha256": EXPECTED_V024_SHA256,
        },
        "ok": all_ok,
        "results": _results,
        "files_changed": [
            "scripts/verify_interact_024.py",
            "scripts/verify_interact_035.py",
            "evidence/interact-035/verify_summary.json",
            "feature_list.json",
            "claude-progress.md",
        ],
        "runtime_change": False,
        "default_off_invariant": True,
    }
    try:
        head_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
        summary["sha"] = head_sha
    except Exception:  # noqa: BLE001
        pass

    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _print(
        "SUMMARY",
        f"all_pass={all_ok} ({sum(1 for r in _results if r['ok'])}/{len(_results)})",
    )
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
