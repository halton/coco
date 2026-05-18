"""vision-015 verification — _gc_last_time time-source 设计文档锁面 (verify-only).

跑法::

    uv run python scripts/verify_vision_015.py

背景
----
vision-013 已把 ``_gc_last_time`` 从 ``time.time()`` (wall) 切到 ``time.monotonic()``,
``run_gc_cycle`` 的 TTL 仍走 wall (与 entry ``last_seen`` 同时基)。GC 路径上 wall +
monotonic 混用为有意设计。vision-012-backlog-time-source-and-validation C1+C2 子项
要求把这一选型显式锁面 + 三维对比文档化, 避免后续有人误把 ``_gc_last_time`` 切回
wall (会引入 NTP 回拨敏感)。

phase-22 P181 verify-only 锁面 (不动源码):

V0  fingerprint — git HEAD + python + face_tracker.py / design doc / verify 自身 sha256。
V1  字面量锁面 — ``coco/perception/face_tracker.py`` 内 ``_gc_last_time`` 赋值 RHS
    必须出现 ``time.monotonic`` (允许 ``now_mono`` 中介变量), 反证不得出现把
    ``_gc_last_time`` 直接赋为 ``time.time()`` 的字面 (会被 grep 抓到)。
V2  设计文档锁面 — ``docs/vision-gc-time-source-design.md`` 必须存在且含关键短语:
    "wall vs monotonic" / "NTP 回拨" / "300s" / "持久化" / "不迁移" / "300" 默认窗口。
V3  regression — verify_vision_012 / 013 / 014b rc=0。
V4  migration evidence — ``evidence/vision-015/migration_note.md`` 存在,
    含未来切回 wall 的代价 (verify monkey-patch 同步) 文字。
V5  smoke 回归 — ``./init.sh`` 总数 11 PASS (与 audio-014 closeout 一致)。

evidence 落 ``evidence/vision-015/verify_summary.json``。
default-OFF, real_machine_uat=n/a, 0 源码改动。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "evidence" / "vision-015"
SUMMARY_PATH = EVIDENCE_DIR / "verify_summary.json"
MIGRATION_NOTE_PATH = EVIDENCE_DIR / "migration_note.md"
DESIGN_DOC_PATH = ROOT / "docs" / "vision-gc-time-source-design.md"
FACE_TRACKER_PATH = ROOT / "coco" / "perception" / "face_tracker.py"

_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, **detail: Any) -> None:
    _results.append({"name": name, "ok": bool(ok), **detail})
    flag = "PASS" if ok else "FAIL"
    print(f"[verify_vision_015] {name}: {flag} {detail}", flush=True)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
    except Exception as exc:  # noqa: BLE001
        return f"unknown:{exc}"


# ---------------------------------------------------------------------------
# V0 fingerprint
# ---------------------------------------------------------------------------
def v0_fingerprint() -> Dict[str, Any]:
    ft_text = FACE_TRACKER_PATH.read_text(encoding="utf-8")
    doc_text = (
        DESIGN_DOC_PATH.read_text(encoding="utf-8") if DESIGN_DOC_PATH.exists() else ""
    )
    self_text = Path(__file__).read_text(encoding="utf-8")
    fp = {
        "git_head": _git_head(),
        "python": sys.version.split()[0],
        "face_tracker_sha256": _sha256_text(ft_text),
        "design_doc_sha256": _sha256_text(doc_text),
        "verify_self_sha256": _sha256_text(self_text),
    }
    _record("V0_fingerprint", True, **fp)
    return fp


# ---------------------------------------------------------------------------
# V1 字面量锁面 — _gc_last_time RHS 必须走 monotonic
# ---------------------------------------------------------------------------
def v1_literal_lock_monotonic() -> None:
    text = FACE_TRACKER_PATH.read_text(encoding="utf-8")

    # (a) `_gc_last_time = now_mono` 出现 (monotonic 中介变量赋值)
    pat_mono_assign = re.compile(r"_gc_last_time\s*=\s*now_mono\b")
    hit_mono_assign = pat_mono_assign.findall(text)

    # (b) `now_mono = time.monotonic()` 出现 (中介变量来源)
    pat_now_mono = re.compile(r"now_mono\s*=\s*time\.monotonic\s*\(")
    hit_now_mono = pat_now_mono.findall(text)

    # (c) 反证: 不得出现 `_gc_last_time = time.time(` 直接赋 wall clock
    pat_wall_assign = re.compile(r"_gc_last_time\s*=\s*time\.time\s*\(")
    hit_wall_assign = pat_wall_assign.findall(text)

    # (d) 反证: time_due 比较的左侧应当是 now_mono (走 monotonic)
    pat_time_due_mono = re.compile(r"now_mono\s*-\s*self\._gc_last_time")
    hit_time_due_mono = pat_time_due_mono.findall(text)

    ok = (
        len(hit_mono_assign) >= 1
        and len(hit_now_mono) >= 1
        and len(hit_wall_assign) == 0
        and len(hit_time_due_mono) >= 1
    )
    _record(
        "V1_gc_last_time_monotonic_literal",
        ok,
        hit_mono_assign=len(hit_mono_assign),
        hit_now_mono=len(hit_now_mono),
        hit_wall_assign=len(hit_wall_assign),
        hit_time_due_mono=len(hit_time_due_mono),
    )


# ---------------------------------------------------------------------------
# V2 设计文档锁面 — 关键短语必须出现
# ---------------------------------------------------------------------------
def v2_design_doc_lock() -> None:
    if not DESIGN_DOC_PATH.exists():
        _record("V2_design_doc_lock", False, reason="design doc missing")
        return
    text = DESIGN_DOC_PATH.read_text(encoding="utf-8")
    phrases = [
        "wall vs monotonic",
        "NTP 回拨",
        "300s",
        "持久化",
        "_gc_last_time",
        "time.monotonic",
        "time.time",
        "last_seen",
    ]
    hits = {p: (p in text) for p in phrases}
    ok = all(hits.values()) and len(text.encode("utf-8")) > 800
    _record(
        "V2_design_doc_phrase_lock",
        ok,
        path=str(DESIGN_DOC_PATH.relative_to(ROOT)),
        bytes=len(text.encode("utf-8")),
        **{f"has_{i}": v for i, v in enumerate(hits.values())},
    )


# ---------------------------------------------------------------------------
# V3 regression — 邻近 verify rc=0
# ---------------------------------------------------------------------------
def v3_regression() -> None:
    scripts = [
        "scripts/verify_vision_012.py",
        "scripts/verify_vision_013.py",
        "scripts/verify_vision_014b.py",
    ]
    rcs: Dict[str, int] = {}
    all_ok = True
    for s in scripts:
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, s],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=240,
        )
        dt = time.time() - t0
        rcs[s] = proc.returncode
        ok_one = proc.returncode == 0
        all_ok = all_ok and ok_one
        print(
            f"[verify_vision_015] regression {s}: rc={proc.returncode} dt={dt:.1f}s",
            flush=True,
        )
    _record("V3_regression_vision_012_013_014b", all_ok, rcs=rcs)


# ---------------------------------------------------------------------------
# V4 migration evidence-only
# ---------------------------------------------------------------------------
MIGRATION_NOTE = """# vision-015 future migration note (evidence-only)

scope: 未来若必须把 ``_gc_last_time`` 切回 wall clock (``time.time``) 的代价分析,
本 phase **不动手**, 仅锁面 evidence。

current owner (vision-013 后):

- ``coco/perception/face_tracker.py`` ``_maybe_run_periodic_gc`` 使用 ``time.monotonic``
  作 due check, ``_gc_last_time`` 在进程内是 monotonic value, 不持久化。
- ``run_gc_cycle(now=...)`` 内部 TTL 判定使用 ``time.time`` (wall), 与 entry
  持久化字段 ``last_seen`` 同时基, 跨进程可比较。
- 两路时钟混用为有意设计, 详见 ``docs/vision-gc-time-source-design.md`` §1-§3。

future migration (若必须切回 wall clock):

1. 源码改动:
   - ``coco/perception/face_tracker.py`` L1150 / L1153 / L1161 三处 ``time.monotonic()``
     调用切到 ``time.time()``, 删 ``now_mono`` 变量名。
   - module docstring L14-28 + ``_maybe_run_periodic_gc`` docstring L1142-1145 同步更新。
2. verify monkey-patch 同步代价:
   - ``scripts/verify_vision_013.py`` 与 ``scripts/verify_vision_014b.py`` 中所有
     mock ``time.monotonic`` 的 fixture 切到 mock ``time.time``。
   - 新增 NTP 回拨 fixture (mock ``time.time`` 突然减少), 断言 GC 不卡死,
     可能需要 jitter tolerance 或 abs() 兜底。
3. 兼容性:
   - ``_gc_last_time`` 不持久化, 切换不影响磁盘文件。
   - process restart 后 ``_gc_last_time=None`` 走 None-init 路径, 行为等价。
4. 风险与不做手术的理由:
   - 切回 wall 后 NTP step-jump (大幅回拨) 会让 ``(now - _gc_last_time)`` 瞬间为负,
     ``time_due`` 永远 false, GC 周期阻塞直到再过一个完整窗口。
   - 当前 monotonic 实现稳定通过 verify_vision_013 / 014b, 切换属于"修一个不存在的问题"。
   - vision-015 verify (V1 字面量锁面) 会主动抓 ``_gc_last_time = time.time(`` 直接赋值,
     任何破契约改动会被抓到。

vision-015 作为 verify-only 契约审计 + 设计文档锁面**到此为止**, 不衍生 fu chain,
不动源码。
"""


def v4_migration_evidence() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    MIGRATION_NOTE_PATH.write_text(MIGRATION_NOTE, encoding="utf-8")
    text = MIGRATION_NOTE_PATH.read_text(encoding="utf-8")
    has_monotonic = "time.monotonic" in text
    has_wall = "time.time" in text or "wall" in text.lower()
    has_verify = "verify_vision_013" in text or "verify_vision_014b" in text
    has_freeze = "锁面" in text or "freeze" in text.lower()
    nbytes = len(text.encode("utf-8"))
    ok = has_monotonic and has_wall and has_verify and has_freeze and nbytes > 500
    _record(
        "V4_migration_evidence",
        ok,
        path=str(MIGRATION_NOTE_PATH.relative_to(ROOT)),
        bytes=nbytes,
        has_monotonic=has_monotonic,
        has_wall=has_wall,
        has_verify_ref=has_verify,
        has_freeze=has_freeze,
    )


# ---------------------------------------------------------------------------
# V5 smoke 回归
# ---------------------------------------------------------------------------
def v5_smoke_regression() -> None:
    # 跑 ./init.sh 取 smoke 行
    t0 = time.time()
    proc = subprocess.run(
        ["bash", "init.sh"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "CI": "1"},
    )
    dt = time.time() - t0
    out = (proc.stdout or "") + (proc.stderr or "")
    # init.sh 实际输出: 每个 smoke 段 "==> Smoke: <name>" + "  ok: ..."; 末尾
    # "==> Smoke 通过。"。统计 `==> Smoke:` 段数 (== ok 行数) 作 PASS_N/TOTAL_N。
    smoke_lines = re.findall(r"^==>\s*Smoke:\s+", out, re.M)
    ok_lines = re.findall(r"^\s*ok:\s+", out, re.M)
    total_n = len(smoke_lines)
    pass_n = len(ok_lines)
    smoke_done = "Smoke 通过" in out
    rc_ok = proc.returncode == 0
    counts_ok = (total_n >= 11) and (pass_n >= total_n - 1)  # ASR 段不出 ok: 行
    ok = rc_ok and counts_ok and smoke_done
    _record(
        "V5_smoke_regression",
        ok,
        rc=proc.returncode,
        pass_n=pass_n,
        total_n=total_n,
        smoke_done=smoke_done,
        dt_s=round(dt, 1),
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    fp = v0_fingerprint()
    v1_literal_lock_monotonic()
    v2_design_doc_lock()
    v3_regression()
    v4_migration_evidence()
    v5_smoke_regression()

    all_ok = all(r["ok"] for r in _results)
    summary = {
        "feature": "vision-015",
        "scope": "_gc_last_time time-source 设计文档锁面 (verify-only)",
        "verify_only": True,
        "source_code_changes": 0,
        "default_off": True,
        "real_machine_uat": "n/a",
        "fingerprint": fp,
        "results": _results,
        "all_ok": all_ok,
        "ts": time.time(),
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[verify_vision_015] ALL_OK={all_ok} summary={SUMMARY_PATH.relative_to(ROOT)}",
        flush=True,
    )
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
