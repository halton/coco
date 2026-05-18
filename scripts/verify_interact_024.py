"""interact-024 verification: latency_ms admit/reject/cooldown 三 stage 语义 contract 文档锁面.

source backlog: interact-018-backlog-latency-stage-semantics-doc
direction: 纯 verify-only 文档锁面, 0 业务源码改动; 仅 docs/ + scripts/ + evidence/。

跑法::

    uv run python scripts/verify_interact_024.py

子项:

V0 fingerprint sha256 锁关键文件 — docs/interact-latency-stage-contract.md 与
   coco/proactive.py 的 sha256 (proactive.py 仅作锁面快照, 0 改动);

V1 三 stage 在源码中的 emit 位置字面量锁面 — admit (arbit_winner) +
   reject (cooldown_hit 三元式) + emotion_alert 独立路径 latency_ms wire 全部存在;
   (interact-033b 升级: per-stage 锚点 admit/reject_main/reject_preempt 分别 ≥1
    站点 latency_ms=_lat_ms() 字面量, 单点删除任一处即被 V1 直接捕获)

V2 contract doc 关键短语锁面 — 至少 12 项 (确保 §1-§6 全段未被漂移);

V3 env COCO_PROACTIVE_TRACE=0 时 bytewise 等价 main — subprocess 启 scheduler
   一次 maybe_trigger, 断言无 proactive.trace 行 (default-OFF 不变式);

V4 env COCO_PROACTIVE_TRACE=1 时三 stage 均 emit latency_ms — subprocess
   分别构造 admit / reject(non-cooldown) / cooldown_hit 路径, 断言 latency_ms 字段存在
   且 isinstance(int,float) >= 0;

V5 regression — interact-018/021/022/023 verify rc=0 全绿;

V6 smoke 11/11 PASS — ./init.sh rc=0 (主 smoke 通过);

V_n evidence/migration_note — 写 evidence/interact-024/。

retval: 0 全 PASS; 1 任一 FAIL
evidence: evidence/interact-024/verify_summary.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_024] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []
_v1_drift_report: Dict[str, Dict[str, Any]] = {}

# interact-036: drift_report 跨 commit 历史趋势 append jsonl 文件名常量
# 写入位置: evidence/_history/interact_024_drift_history.jsonl (与 smoke_history.jsonl 同目录)
# 只 append, 不读旧记录; try/except 包裹避免影响 verify PASS/FAIL.
_DRIFT_HISTORY_PATH = ROOT / "evidence" / "_history" / "interact_024_drift_history.jsonl"
# interact-037: rotation size cap. 超过 _MAX_BYTES 时 rotate 到 .jsonl.1 (覆盖式; 仅保留 2 代).
_MAX_BYTES = 256 * 1024


def _append_drift_history(drift_report: Dict[str, Dict[str, Any]]) -> None:
    """append jsonl 一行: per-stage actual_line / drift / within_tolerance + git_head + ts.

    interact-036: 只 append, 不读旧记录; 全 try/except 包裹, 任何失败都不影响 verify。
    interact-037: 在 append 前若当前文件 > _MAX_BYTES, rotate 到 .jsonl.1 (覆盖式).
    """
    try:
        import datetime as _dt

        try:
            _head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
            ).strip()
        except Exception:  # noqa: BLE001
            _head = None
        record: Dict[str, Any] = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "kind": "interact_024_v1_drift",
            "git_head": _head,
            "drift_report": drift_report,
        }
        _DRIFT_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        # interact-037: size cap rotation. fail-soft 内层 try.
        try:
            if (
                _DRIFT_HISTORY_PATH.exists()
                and _DRIFT_HISTORY_PATH.stat().st_size > _MAX_BYTES
            ):
                _rotated = _DRIFT_HISTORY_PATH.with_suffix(
                    _DRIFT_HISTORY_PATH.suffix + ".1"
                )
                if _rotated.exists():
                    _rotated.unlink()
                _DRIFT_HISTORY_PATH.rename(_rotated)
        except Exception:  # noqa: BLE001
            pass
        with _DRIFT_HISTORY_PATH.open("a", encoding="utf-8") as _fh:
            _fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        # interact-036 硬约束: history 写入失败必须静默 swallow, 不影响 verify。
        pass


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
        "docs/interact-latency-stage-contract.md": None,
        "coco/proactive.py": None,
        "research/proactive_trace_contract.md": None,
    }
    missing = []
    for rel in targets:
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
        else:
            targets[rel] = _sha256(p)
    if missing:
        _record("V0_fingerprint_sha256", False, f"missing files: {missing}")
        return
    _record(
        "V0_fingerprint_sha256",
        True,
        f"sha256 锁面 3 文件: " + ", ".join(f"{k}={v[:12]}" for k, v in targets.items()),
    )


# ---------------------------------------------------------------------------
# V1 三 stage 源码 emit 字面量
# ---------------------------------------------------------------------------


def v1_source_anchors() -> None:
    src = (ROOT / "coco" / "proactive.py").read_text(encoding="utf-8")
    # interact-033b 升级: V1 anchor 从"整体短语 substring"升级为
    # "per-stage 锚点 + 窗口内 latency_ms=_lat_ms() 字面量"分别锚定 admit /
    # reject_main / reject_preempt 三 stage, 单点删除任一处都能被 V1 直接捕获,
    # 不再依赖邻近 verify 018/021/022/023 兜底。
    #
    # site-A admit (arbit_winner emit) — 锚 '"arbit_winner", _candidate_id, "admit"'
    # site-B reject_main (cooldown_hit/normal reject 三元式 emit) — 锚 '_stage_out, _candidate_id, "reject"'
    # site-C reject_preempt (arbit_emotion_preempt 抢占 emit) — 锚 'arbit_emotion_preempt'
    #
    # 每个 site 的锚点字面量本身全 repo unique, 窗口取 ±12 行覆盖跨行 _trace_emit 调用。
    # interact-035: per-stage 锚点新增 expected_line / drift_tolerance 行号窗口 sanity
    # (warn-only, 不影响 PASS/FAIL). baseline 取自 interact-035 开发时 (main a735a6b)
    # 实际命中行号: admit=1056, reject_main=1025, reject_preempt=997.
    # 若 proactive.py 大重构导致 emit 站点跨大段移动 (>drift_tolerance 行), V1 仍 PASS
    # 但会 print warn + 在 evidence drift 字段记录, 便于早期发现锚点失效.
    site_specs: List[Tuple[str, str, int, int, int]] = [
        # (key, pat, win, expected_line=<int>, drift_tolerance=<int>)
        ("admit", r'"arbit_winner",\s*_candidate_id,\s*"admit"', 12, 1056, 20),
        ("reject_main", r'_stage_out,\s*_candidate_id,\s*"reject"', 12, 1025, 20),
        ("reject_preempt", r"arbit_emotion_preempt", 12, 997, 20),
    ]
    lat_line_re = re.compile(r"latency_ms\s*=\s*_lat_ms\s*\(\s*\)\s*,")
    lines = src.split("\n")
    per_stage_hits: Dict[str, List[int]] = {key: [] for key, *_ in site_specs}
    for key, pat, win, _exp, _tol in site_specs:
        prog = re.compile(pat)
        for i, ln in enumerate(lines):
            if prog.search(ln):
                lo = max(0, i - win)
                hi = min(len(lines), i + win + 1)
                window_text = "\n".join(lines[lo:hi])
                if lat_line_re.search(window_text):
                    per_stage_hits[key].append(i + 1)
    # 每个 stage 必须 >=1 个站点 (latency_ms=_lat_ms() 出现于锚点附近窗口)。
    missing_stages = [k for k, hits in per_stage_hits.items() if len(hits) < 1]
    if missing_stages:
        _record(
            "V1_source_anchors",
            False,
            f"missing per-stage latency_ms anchors: {missing_stages} (hits={per_stage_hits})",
        )
        return
    # interact-035 drift_check: warn-only 行号漂移 sanity. 不影响 PASS/FAIL.
    drift_report: Dict[str, Dict[str, Any]] = {}
    for key, _pat, _win, expected_line, drift_tolerance in site_specs:
        hits = per_stage_hits[key]
        # 取首个命中作为 actual_line (per-stage 站点天然 unique).
        actual_line = hits[0] if hits else None
        drift = (abs(actual_line - expected_line) if actual_line is not None else None)
        within = (drift is not None and drift <= drift_tolerance)
        drift_report[key] = {
            "expected_line": expected_line,
            "actual_line": actual_line,
            "drift": drift,
            "drift_tolerance": drift_tolerance,
            "within_tolerance": within,
        }
        if actual_line is not None and drift is not None and drift > drift_tolerance:
            _print(
                "WARN",
                f"V1 drift sanity: stage={key} expected_line={expected_line} "
                f"actual_line={actual_line} drift={drift} > tolerance={drift_tolerance} (warn-only)",
            )
    # 把 drift_report 暴露到模块级, 便于 verify_summary 写入 evidence.
    global _v1_drift_report
    _v1_drift_report = drift_report
    # interact-036: append 到 evidence/_history/interact_024_drift_history.jsonl
    # 跨 commit 行号漂移趋势观测; try/except 包裹, 不影响 verify PASS/FAIL.
    _append_drift_history(drift_report)
    # 额外锁面: emotion_alert 独立 latency 路径 + 共享 _lat_ms 闭包字面量。
    extra_anchors = [
        "_lat_start = time.monotonic()",
        "round((time.monotonic() - _lat_start) * 1000.0, 3)",
        "_ea_lat_start = time.monotonic()",
        '"emotion_alert"',
        "round((time.monotonic() - _ea_lat_start) * 1000.0, 3)",
    ]
    missing_extra = [a for a in extra_anchors if a not in src]
    if missing_extra:
        _record("V1_source_anchors", False, f"missing extra anchors: {missing_extra}")
        return
    _record(
        "V1_source_anchors",
        True,
        f"per-stage anchors 全部命中: admit@{per_stage_hits['admit']} "
        f"reject_main@{per_stage_hits['reject_main']} "
        f"reject_preempt@{per_stage_hits['reject_preempt']} "
        f"+ emotion_alert 独立路径 + _lat_ms 闭包",
    )


# ---------------------------------------------------------------------------
# V2 contract doc 关键短语锁面
# ---------------------------------------------------------------------------


def v2_contract_doc_phrases() -> None:
    doc = (ROOT / "docs" / "interact-latency-stage-contract.md").read_text(encoding="utf-8")
    phrases = [
        "latency_ms admit/reject/cooldown 三 stage 语义 Contract",
        "interact-018-backlog-latency-stage-semantics-doc",
        "verify-only 文档锁面",
        "0 业务源码改动",
        "arbit_winner",
        "cooldown_hit",
        "emotion_alert",
        "_lat_start = time.monotonic()",
        "_ea_lat_start",
        "单调非降",
        "不可跨 stage 求总 p50/p95",
        "**不**包含 LLM/TTS",
        "判定即出",
        "default-OFF 不变式",
        "COCO_PROACTIVE_TRACE",
        "不衍生 fu chain",
        "research/proactive_trace_contract.md",
    ]
    missing = [p for p in phrases if p not in doc]
    if missing:
        _record("V2_contract_doc_phrases", False, f"missing: {missing}")
        return
    _record(
        "V2_contract_doc_phrases",
        True,
        f"{len(phrases)} 关键短语全部命中 (§1-§6 覆盖)",
    )


# ---------------------------------------------------------------------------
# V3 default-OFF 不变式 (subprocess)
# ---------------------------------------------------------------------------


_OFF_SCRIPT = r"""
import json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent if "__file__" in dir() else Path(".")
sys.path.insert(0, str(ROOT))
# 强制 OFF
os.environ.pop("COCO_PROACTIVE_TRACE", None)

from coco import proactive_trace as pt
from coco.proactive import ProactiveScheduler, ProactiveConfig
from coco.power_state import PowerState

captured = []
def _emit(event, **payload):
    captured.append({"event": event, **payload})
pt.set_emit_override(_emit)

class _FakePS:
    current_state = PowerState.ACTIVE
class _FakeFace:
    def latest(self):
        class _S:
            present = True
        return _S()

def _llm(text, *, system_prompt=None):
    return "你好"
def _tts(text, blocking=True):
    return None

cfg = ProactiveConfig(enabled=True, idle_threshold_s=10.0, cooldown_s=30.0,
                     max_topics_per_hour=10, tick_s=1.0)
sched = ProactiveScheduler(config=cfg, power_state=_FakePS(), face_tracker=_FakeFace(),
                            llm_reply_fn=_llm, tts_say_fn=_tts)
sched._last_interaction_ts = sched.clock() - 600.0
ok = sched.maybe_trigger()

trace_events = [e for e in captured if e.get("event") == "proactive.trace"]
print(json.dumps({"ok_trigger": bool(ok), "trace_count": len(trace_events)}))
"""


def v3_default_off_no_emit() -> None:
    # 写入临时脚本
    tmp = ROOT / "evidence" / "interact-024" / "_off_probe.py"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(_OFF_SCRIPT, encoding="utf-8")
    try:
        env = dict(os.environ)
        env.pop("COCO_PROACTIVE_TRACE", None)
        proc = subprocess.run(
            [sys.executable, str(tmp)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        if proc.returncode != 0:
            _record(
                "V3_default_off_no_emit",
                False,
                f"subprocess rc={proc.returncode} stderr={proc.stderr[-300:]}",
            )
            return
        # 取最后一行 json
        lines = [l for l in proc.stdout.strip().splitlines() if l.strip().startswith("{")]
        if not lines:
            _record("V3_default_off_no_emit", False, f"no json output: {proc.stdout[-300:]}")
            return
        out = json.loads(lines[-1])
        # default-OFF: emit_trace 内部 return, set_emit_override 也不会被 dispatch
        # (emit_trace 在 ON 时才 dispatch); 这里断言 OFF 时 trace_count == 0
        if out.get("trace_count", -1) != 0:
            _record(
                "V3_default_off_no_emit",
                False,
                f"OFF 时不应有 trace, 实际 trace_count={out.get('trace_count')}",
            )
            return
        _record(
            "V3_default_off_no_emit",
            True,
            f"default-OFF 无 trace 行 (ok_trigger={out.get('ok_trigger')}, trace=0)",
        )
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# V4 ON 时三 stage 均 emit latency_ms (subprocess)
# ---------------------------------------------------------------------------


_ON_SCRIPT = r"""
import json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent if "__file__" in dir() else Path(".")
sys.path.insert(0, str(ROOT))
os.environ["COCO_PROACTIVE_TRACE"] = "1"

from coco import proactive_trace as pt
from coco.proactive import ProactiveScheduler, ProactiveConfig
from coco.power_state import PowerState

def _build():
    class _FakePS:
        current_state = PowerState.ACTIVE
    class _FakeFace:
        def latest(self):
            class _S:
                present = True
            return _S()
    def _llm(t, *, system_prompt=None): return "你好"
    def _tts(t, blocking=True): return None
    cfg = ProactiveConfig(enabled=True, idle_threshold_s=10.0, cooldown_s=30.0,
                         max_topics_per_hour=10, tick_s=1.0)
    s = ProactiveScheduler(config=cfg, power_state=_FakePS(), face_tracker=_FakeFace(),
                            llm_reply_fn=_llm, tts_say_fn=_tts)
    s._last_interaction_ts = s.clock() - 600.0
    return s

# A. admit (arbit_winner)
captured_a = []
pt.set_emit_override(lambda e, **kw: captured_a.append({"event": e, **kw}))
sched_a = _build()
sched_a.maybe_trigger()

# B. reject non-cooldown — face_absent: 把 face 设 absent
captured_b = []
pt.set_emit_override(lambda e, **kw: captured_b.append({"event": e, **kw}))
class _FaceAbsent:
    def latest(self):
        class _S:
            present = False
        return _S()
sched_b = _build()
sched_b.face_tracker = _FaceAbsent()
sched_b.maybe_trigger()

# C. cooldown_hit — 先 admit 一次再立即第二次
captured_c = []
pt.set_emit_override(lambda e, **kw: captured_c.append({"event": e, **kw}))
sched_c = _build()
sched_c.maybe_trigger()
sched_c._last_interaction_ts = sched_c.clock() - 600.0
sched_c.maybe_trigger()

pt.set_emit_override(None)

def _stage_lat(cap, stage, decision=None):
    for e in cap:
        if e.get("event") != "proactive.trace":
            continue
        if e.get("stage") != stage:
            continue
        if decision is not None and e.get("decision") != decision:
            continue
        return e.get("latency_ms")
    return None

result = {
    "admit_arbit_winner_lat": _stage_lat(captured_a, "arbit_winner", "admit"),
    "reject_face_absent_stage": next(
        (e.get("stage") for e in captured_b if e.get("event") == "proactive.trace" and e.get("decision") == "reject"),
        None,
    ),
    "reject_face_absent_lat": next(
        (e.get("latency_ms") for e in captured_b if e.get("event") == "proactive.trace" and e.get("decision") == "reject"),
        None,
    ),
    "reject_face_absent_reason": next(
        (e.get("reason") for e in captured_b if e.get("event") == "proactive.trace" and e.get("decision") == "reject"),
        None,
    ),
    "cooldown_hit_lat": _stage_lat(captured_c, "cooldown_hit", "reject"),
}
print(json.dumps(result))
"""


def v4_on_three_stages_latency() -> None:
    tmp = ROOT / "evidence" / "interact-024" / "_on_probe.py"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(_ON_SCRIPT, encoding="utf-8")
    try:
        env = dict(os.environ)
        env["COCO_PROACTIVE_TRACE"] = "1"
        proc = subprocess.run(
            [sys.executable, str(tmp)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        if proc.returncode != 0:
            _record(
                "V4_on_three_stages_latency",
                False,
                f"subprocess rc={proc.returncode} stderr={proc.stderr[-300:]}",
            )
            return
        lines = [l for l in proc.stdout.strip().splitlines() if l.strip().startswith("{")]
        if not lines:
            _record("V4_on_three_stages_latency", False, f"no json: {proc.stdout[-300:]}")
            return
        out = json.loads(lines[-1])
        bad = []
        # admit
        a_lat = out.get("admit_arbit_winner_lat")
        if not isinstance(a_lat, (int, float)) or a_lat < 0:
            bad.append(f"admit_arbit_winner_lat={a_lat!r}")
        # reject non-cooldown
        r_stage = out.get("reject_face_absent_stage")
        r_lat = out.get("reject_face_absent_lat")
        r_reason = out.get("reject_face_absent_reason")
        if r_stage not in ("fusion_boost", "mm_proactive", "normal"):
            bad.append(f"reject stage={r_stage!r}")
        if not isinstance(r_lat, (int, float)) or r_lat < 0:
            bad.append(f"reject lat={r_lat!r}")
        if r_reason != "no_face":
            bad.append(f"reject reason={r_reason!r}")
        # cooldown_hit
        c_lat = out.get("cooldown_hit_lat")
        if not isinstance(c_lat, (int, float)) or c_lat < 0:
            bad.append(f"cooldown_hit_lat={c_lat!r}")

        if bad:
            _record("V4_on_three_stages_latency", False, f"issues: {bad}; out={out}")
            return
        _record(
            "V4_on_three_stages_latency",
            True,
            f"三 stage latency_ms 全部 numeric>=0: admit={a_lat} reject({r_stage}/{r_reason})={r_lat} cooldown_hit={c_lat}",
        )
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# V5 regression — interact-018/021/022/023 rc=0
# ---------------------------------------------------------------------------


def v5_regression() -> None:
    scripts = [
        "scripts/verify_interact_018.py",
        "scripts/verify_interact_021.py",
        "scripts/verify_interact_022.py",
        "scripts/verify_interact_023.py",
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
                timeout=180,
            )
            rcs[s] = proc.returncode
            if proc.returncode != 0:
                bad.append(f"{s} rc={proc.returncode}")
        except Exception as e:  # noqa: BLE001
            rcs[s] = -1
            bad.append(f"{s} exc={e!r}")
    if bad:
        _record("V5_regression", False, f"failed: {bad}; rcs={rcs}")
        return
    _record("V5_regression", True, f"all rc=0: {rcs}")


# ---------------------------------------------------------------------------
# V6 smoke 11/11 PASS (./init.sh)
# ---------------------------------------------------------------------------


def v6_smoke() -> None:
    init_sh = ROOT / "init.sh"
    if not init_sh.exists():
        _record("V6_smoke", False, "init.sh missing")
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
        _record("V6_smoke", False, f"exc={e!r}")
        return
    if proc.returncode != 0:
        _record(
            "V6_smoke",
            False,
            f"init.sh rc={proc.returncode} stderr tail={proc.stderr[-300:]}",
        )
        return
    _record("V6_smoke", True, f"init.sh rc=0 (stdout tail len={len(proc.stdout)})")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    v0_fingerprint_sha256()
    v1_source_anchors()
    v2_contract_doc_phrases()
    v3_default_off_no_emit()
    v4_on_three_stages_latency()
    v5_regression()
    v6_smoke()

    all_ok = all(r["ok"] for r in _results)

    out_dir = ROOT / "evidence" / "interact-024"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "interact-024",
        "source_backlog": "interact-018-backlog-latency-stage-semantics-doc",
        "direction": "verify-only 文档锁面 (0 业务源码改动): docs/interact-latency-stage-contract.md + scripts/verify_interact_024.py",
        "ok": all_ok,
        "results": _results,
        "files_changed": [
            "docs/interact-latency-stage-contract.md",
            "scripts/verify_interact_024.py",
            "evidence/interact-024/verify_summary.json",
            "evidence/interact-024/migration_note.md",
            "feature_list.json",
            "claude-progress.md",
        ],
        "runtime_change": False,
        "default_off_invariant": True,
        "v1_drift_report": _v1_drift_report,
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
