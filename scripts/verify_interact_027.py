"""interact-027 verification: V1 anchor 按 stage 字面量分别 anchor + 站点数 ==3 严格断言.

source backlog: interact-024-backlog-v1-anchor-per-stage-count
direction: 纯 verify-only, 0 业务源码改动; additive (不改 scripts/verify_interact_024.py).

interact-024 V1 anchor `"latency_ms=_lat_ms()"` 只用 substring presence:
只要 >=1 处出现即 PASS, 单点删除 (3 处删 1) 不能被 V1 抓住, 靠 V5 邻近回归兜住。

本任务升级为按 stage 上下文字面量分别 anchor + 严格计数 ==3:
- site-A admit  (arbit_winner emit, decision="admit")
- site-B reject (cooldown_hit / normal reject 三元式 emit, decision="reject", reason 传入)
- site-C reject (arbit_emotion_preempt emit, decision="reject", reason="arbit_emotion_preempt")

单点删除任一处 `latency_ms=_lat_ms(),` 都能被独立 V 项 FAIL 抓住。

跑法::

    uv run python scripts/verify_interact_027.py

子项:

V0 fingerprint sha256 锁 coco/proactive.py + self;
V1 admit stage (arbit_winner) latency_ms= 站点数 == 1;
V2 reject stage (主路径 cooldown_hit/normal) latency_ms= 站点数 == 1;
V3 reject stage (arbit_emotion_preempt 抢占) latency_ms= 站点数 == 1;
V4 全局 `latency_ms=_lat_ms(),` 总站点数 == 3 (严格);
V5 单点删除模拟 — 内存中 patch 源码删除任一 _lat_ms() emit,
   V1/V2/V3 分组计数应 FAIL (per-stage 断点能感知);
V6 env COCO_PROACTIVE_TRACE=0 时 bytewise 等价 — subprocess 启 scheduler
   一次 maybe_trigger, 断言无 proactive.trace 行 (default-OFF 不变式);
V7 邻近 verify 回归 — interact-018/021/022/023/024/025/026 rc=0;
V8 smoke 11/11 PASS — ./init.sh rc=0;
V_n evidence/migration_note — 写 evidence/interact-027/.

retval: 0 全 PASS; 1 任一 FAIL
evidence: evidence/interact-027/verify_summary.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_027] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


def _sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()


def _sha256(p: Path) -> str:
    return _sha256_bytes(p.read_bytes())


# ---------------------------------------------------------------------------
# 共享 helper: 按 stage 上下文字面量识别 3 个 latency_ms=_lat_ms() emit-site
# ---------------------------------------------------------------------------


# 每个 site 用 (stage_anchor_regex, decision_anchor_regex) 双锚定位前置上下文,
# 然后向下 ~12 行内必须出现 `latency_ms=_lat_ms(),`。
#
# - site-A admit:   `"arbit_winner", _candidate_id, "admit"` -> latency_ms=_lat_ms(),
# - site-B reject:  `_stage_out, _candidate_id, "reject"`    -> latency_ms=_lat_ms(),
# - site-C reject:  `_preempt_stage, _candidate_id, "reject"` 且 reason="arbit_emotion_preempt"
#                                                              -> latency_ms=_lat_ms(),


# 用"锚点字面量 (单行 grep) + 窗口内出现 latency_ms=_lat_ms()"识别 3 个 emit site.
# 锚点选择标准: 锚点本身在 proactive.py 中 globally unique, 不依赖跨行表达式。
SITE_SPECS: List[Tuple[str, str, int]] = [
    # site-A: admit (arbit_winner) — 锚: '"arbit_winner", _candidate_id, "admit"'
    #   该字面量整行存在 (proactive.py 行 1053 附近)。
    ("admit", r'"arbit_winner",\s*_candidate_id,\s*"admit"', 12),
    # site-B: reject 主路径 (cooldown_hit/normal) — 锚: '_stage_out, _candidate_id, "reject"'
    #   该字面量整行存在 (行 1022 附近)。
    ("reject_main", r'_stage_out,\s*_candidate_id,\s*"reject"', 12),
    # site-C: reject 抢占 — 锚: 'arbit_emotion_preempt'
    #   该字面量在全 repo 只出现在抢占 emit 的 reason= 参数中 (单点 unique)。
    #   窗口取上 12 行 / 下 6 行覆盖 _trace_emit 多行调用。
    ("reject_preempt", r"arbit_emotion_preempt", 12),
]

LAT_LINE_RE = re.compile(r"latency_ms\s*=\s*_lat_ms\s*\(\s*\)\s*,")


def _find_site_lat_lines(src: str) -> Dict[str, List[int]]:
    """对每个 site spec, 返回锚点行号: 锚点附近窗口内必须出现 `latency_ms=_lat_ms(),`.

    窗口: 上 spec[2] 行 / 下 spec[2] 行 (双向, 覆盖跨行 _trace_emit 调用)。
    返回锚点行号 (1-indexed) 列表。
    """
    lines = src.split("\n")
    out: Dict[str, List[int]] = {key: [] for key, _, _ in SITE_SPECS}
    for key, pat, win in SITE_SPECS:
        prog = re.compile(pat)
        for i, ln in enumerate(lines):
            if prog.search(ln):
                lo = max(0, i - win)
                hi = min(len(lines), i + win + 1)
                window_text = "\n".join(lines[lo:hi])
                if not LAT_LINE_RE.search(window_text):
                    continue
                out[key].append(i + 1)  # 1-indexed
    return out


# ---------------------------------------------------------------------------
# V0 fingerprint sha256
# ---------------------------------------------------------------------------


def v0_fingerprint_sha256() -> None:
    targets = {
        "coco/proactive.py": None,
        "scripts/verify_interact_027.py": None,
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
        "sha256 锁面: " + ", ".join(f"{k}={v[:12]}" for k, v in targets.items()),
    )


# ---------------------------------------------------------------------------
# V1 admit stage (arbit_winner) latency_ms= 站点数 == 1
# ---------------------------------------------------------------------------


def _src() -> str:
    return (ROOT / "coco" / "proactive.py").read_text(encoding="utf-8")


def v1_admit_arbit_winner_count() -> None:
    sites = _find_site_lat_lines(_src())
    found = sites["admit"]
    if len(found) != 1:
        _record(
            "V1_admit_arbit_winner_count",
            False,
            f"admit (arbit_winner) latency_ms=_lat_ms() emit-site count={len(found)} expected==1, lines={found}",
        )
        return
    _record(
        "V1_admit_arbit_winner_count",
        True,
        f"admit (arbit_winner) latency_ms=_lat_ms() emit-site == 1 at line {found[0]}",
    )


# ---------------------------------------------------------------------------
# V2 reject 主路径 (cooldown_hit / normal) latency_ms= 站点数 == 1
# ---------------------------------------------------------------------------


def v2_reject_main_count() -> None:
    sites = _find_site_lat_lines(_src())
    found = sites["reject_main"]
    if len(found) != 1:
        _record(
            "V2_reject_main_count",
            False,
            f"reject main (_stage_out) latency_ms=_lat_ms() emit-site count={len(found)} expected==1, lines={found}",
        )
        return
    _record(
        "V2_reject_main_count",
        True,
        f"reject main (cooldown_hit/normal 三元式) latency_ms=_lat_ms() emit-site == 1 at line {found[0]}",
    )


# ---------------------------------------------------------------------------
# V3 reject 抢占 (arbit_emotion_preempt) latency_ms= 站点数 == 1
# ---------------------------------------------------------------------------


def v3_reject_preempt_count() -> None:
    sites = _find_site_lat_lines(_src())
    found = sites["reject_preempt"]
    if len(found) != 1:
        _record(
            "V3_reject_preempt_count",
            False,
            f"reject preempt (arbit_emotion_preempt) latency_ms=_lat_ms() emit-site count={len(found)} expected==1, lines={found}",
        )
        return
    _record(
        "V3_reject_preempt_count",
        True,
        f"reject preempt (arbit_emotion_preempt) latency_ms=_lat_ms() emit-site == 1 at line {found[0]}",
    )


# ---------------------------------------------------------------------------
# V4 全局 latency_ms=_lat_ms() 总站点数 == 3 严格
# ---------------------------------------------------------------------------


def v4_global_total_count_strict() -> None:
    src = _src()
    total = len(LAT_LINE_RE.findall(src))
    if total != 3:
        _record(
            "V4_global_total_count_strict",
            False,
            f"global latency_ms=_lat_ms() count={total} expected==3 (strict)",
        )
        return
    # 三 site 都各占 1, 加起来 ==3, 应等于全局 total
    sites = _find_site_lat_lines(src)
    per_site_sum = sum(len(v) for v in sites.values())
    if per_site_sum != 3:
        _record(
            "V4_global_total_count_strict",
            False,
            f"per-site sum={per_site_sum} != global=3, sites={sites}",
        )
        return
    _record(
        "V4_global_total_count_strict",
        True,
        f"全局 latency_ms=_lat_ms() == 3 严格; per-site={sites}",
    )


# ---------------------------------------------------------------------------
# V5 单点删除模拟 — 内存 patch, 任一 emit 删除后 V1/V2/V3/V4 应感知
# ---------------------------------------------------------------------------


def _count_sites_on_text(src: str) -> Tuple[Dict[str, int], int]:
    sites = _find_site_lat_lines(src)
    return ({k: len(v) for k, v in sites.items()}, len(LAT_LINE_RE.findall(src)))


def v5_single_delete_simulation() -> None:
    src = _src()
    lines = src.split("\n")
    # 找所有 latency_ms=_lat_ms() 实体行号
    hit_lines = [i for i, ln in enumerate(lines) if LAT_LINE_RE.search(ln)]
    if len(hit_lines) != 3:
        _record(
            "V5_single_delete_simulation",
            False,
            f"baseline hit_lines!=3 (got {len(hit_lines)}); 与 V4 前置不一致",
        )
        return
    failures: List[str] = []
    for idx, ln_no in enumerate(hit_lines):
        # 内存中删除该行
        patched_lines = list(lines)
        patched_lines[ln_no] = ""  # 删除 latency_ms=_lat_ms(), 不动其它行
        patched_src = "\n".join(patched_lines)
        sites_counts, total = _count_sites_on_text(patched_src)
        # 至少有一个 site count 应 != 1, 或 total != 3
        per_site_ok_count_each_1 = all(c == 1 for c in sites_counts.values())
        if total == 3 or per_site_ok_count_each_1:
            failures.append(
                f"delete#{idx}@line{ln_no+1}: 删除后未被感知 (total={total}, per_site={sites_counts})"
            )
    if failures:
        _record("V5_single_delete_simulation", False, "; ".join(failures))
        return
    _record(
        "V5_single_delete_simulation",
        True,
        f"3 单点删除模拟均被 per-stage/total 严格断言感知 (3/3 detected)",
    )


# ---------------------------------------------------------------------------
# V6 default-OFF 不变式 (subprocess)
# ---------------------------------------------------------------------------


_OFF_SCRIPT = r"""
import json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent if "__file__" in dir() else Path(".")
sys.path.insert(0, str(ROOT))
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


def v6_default_off_no_emit() -> None:
    tmp = ROOT / "evidence" / "interact-027" / "_off_probe.py"
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
                "V6_default_off_no_emit",
                False,
                f"subprocess rc={proc.returncode} stderr={proc.stderr[-300:]}",
            )
            return
        lines = [l for l in proc.stdout.strip().splitlines() if l.strip().startswith("{")]
        if not lines:
            _record("V6_default_off_no_emit", False, f"no json output: {proc.stdout[-300:]}")
            return
        out = json.loads(lines[-1])
        if out.get("trace_count", -1) != 0:
            _record(
                "V6_default_off_no_emit",
                False,
                f"OFF 时不应有 trace, trace_count={out.get('trace_count')}",
            )
            return
        _record(
            "V6_default_off_no_emit",
            True,
            f"default-OFF 无 trace 行 (ok_trigger={out.get('ok_trigger')}, trace=0)",
        )
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# V7 邻近 verify 回归 — interact-018/021/022/023/024/025/026 rc=0
# ---------------------------------------------------------------------------


def v7_regression() -> None:
    scripts = [
        "scripts/verify_interact_018.py",
        "scripts/verify_interact_021.py",
        "scripts/verify_interact_022.py",
        "scripts/verify_interact_023.py",
        "scripts/verify_interact_024.py",
        "scripts/verify_interact_025.py",
        "scripts/verify_interact_026.py",
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
        _record("V7_regression", False, f"failed: {bad}; rcs={rcs}")
        return
    _record("V7_regression", True, f"all rc=0: {rcs}")


# ---------------------------------------------------------------------------
# V8 smoke 11/11 PASS (./init.sh)
# ---------------------------------------------------------------------------


def v8_smoke() -> None:
    init_sh = ROOT / "init.sh"
    if not init_sh.exists():
        _record("V8_smoke", False, "init.sh missing")
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
        _record("V8_smoke", False, f"exc={e!r}")
        return
    if proc.returncode != 0:
        _record(
            "V8_smoke",
            False,
            f"init.sh rc={proc.returncode} stderr tail={proc.stderr[-300:]}",
        )
        return
    _record("V8_smoke", True, f"init.sh rc=0 (stdout len={len(proc.stdout)})")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    v0_fingerprint_sha256()
    v1_admit_arbit_winner_count()
    v2_reject_main_count()
    v3_reject_preempt_count()
    v4_global_total_count_strict()
    v5_single_delete_simulation()
    v6_default_off_no_emit()
    v7_regression()
    v8_smoke()

    all_ok = all(r["ok"] for r in _results)

    out_dir = ROOT / "evidence" / "interact-027"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "interact-027",
        "source_backlog": "interact-024-backlog-v1-anchor-per-stage-count",
        "direction": "verify-only, additive (不改 scripts/verify_interact_024.py); V1 升级为 per-stage 严格站点数==3 + 单点删除可感知",
        "ok": all_ok,
        "results": _results,
        "files_changed": [
            "scripts/verify_interact_027.py",
            "evidence/interact-027/verify_summary.json",
            "evidence/interact-027/migration_note.md",
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
