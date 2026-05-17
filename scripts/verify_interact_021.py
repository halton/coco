"""interact-021 verification: latency_ms 各 stage 语义文档化.

承接 interact-018 Reviewer caveat (interact-018-backlog-latency-stage-semantics-doc):
interact-018 把 latency_ms 在 5 个 emit 站点都接好后, Reviewer 指出 — 同一
字段名在不同 stage 下语义粒度不同 (admit 端到端 vs reject 判定即出 vs
arbit_winner 锁内预占即出 vs emotion_alert 独立路径自测量), 直接 p50/p95
混合统计会误导。本 feature 把语义按 stage 分类文档化, **纯文档**, 不改
运行时行为。

产物:
- research/proactive_trace_contract.md §5 (单源真理)
- coco/proactive.py `_lat_start` 处 docstring/注释增强 (5 个 stage 名清单)

子项::

V0 fingerprint: 文档/源码含 5 个 stage 名 + 关键术语 (monotonic, cumulative,
   ms, latency_ms, _lat_start, _lat_ms)。

V1 contract 文档段含 5 个 stage 名 + monotonic + cumulative + ms 关键词
   (research/proactive_trace_contract.md §5)。

V2 coco/proactive.py 仍含 5 处 latency_ms emit (与 interact-018 V2 锁同步,
   防止文档化过程误删 emit)。

V3 latency_ms 单调非降 (cumulative): 在 fixture 跑 maybe_trigger, 收集所有
   emit 的 latency_ms (按 emit 顺序), 断言后发 emit >= 前发 emit。

V4 docstring / 注释与代码 stage 名一致: 文档 §5.3 列的 5 个 stage 名
   {emotion_alert, fusion_boost, mm_proactive, cooldown_hit, arbit_winner,
   normal} 都能在 coco/proactive.py 找到 emit 站点字面量。

V5 regression: verify_interact_018.py + verify_interact_019.py +
   verify_interact_020.py 子进程 rc=0。

retval: 0 全 PASS; 1 任一失败
evidence: evidence/interact-021/verify_summary.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


RESULTS: Dict[str, Dict[str, Any]] = {}


def _record(name: str, ok: bool, **detail: Any) -> None:
    RESULTS[name] = {"ok": ok, **detail}
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}: {detail}")


# 5 个 stage 名权威清单 (文档 §5.3 同步)
STAGE_NAMES = {
    "emotion_alert",
    "fusion_boost",
    "mm_proactive",
    "cooldown_hit",
    "arbit_winner",
    "normal",
}


def v0_fingerprint() -> None:
    """V0: 文档/源码含 5 个 stage 名 + latency_ms 语义关键词."""
    doc_path = ROOT / "research" / "proactive_trace_contract.md"
    proactive_src_path = ROOT / "coco" / "proactive.py"
    doc_text = doc_path.read_text(encoding="utf-8") if doc_path.exists() else ""
    src_text = proactive_src_path.read_text(encoding="utf-8")
    combined = (doc_text + "\n" + src_text).lower()

    anchors = [
        "latency_ms",
        "_lat_start",
        "_lat_ms",
        "monotonic",
        "cumulative",
        "interact-021",
    ]
    missing_anchors = [a for a in anchors if a not in combined]

    missing_stages = [s for s in STAGE_NAMES if s not in combined]

    _record(
        "V0_fingerprint",
        ok=(not missing_anchors and not missing_stages),
        missing_anchors=missing_anchors,
        missing_stages=missing_stages,
        doc_size=len(doc_text),
    )


def v1_contract_doc_stage_section() -> None:
    """V1: contract 文档 §5 段含 5 个 stage 名 + monotonic + cumulative + ms 关键词."""
    doc_path = ROOT / "research" / "proactive_trace_contract.md"
    if not doc_path.exists():
        _record("V1_contract_doc_stage_section", False, reason="doc missing")
        return
    text = doc_path.read_text(encoding="utf-8")
    lower = text.lower()

    # 必须含 §5 标题锚点
    section_marker = "latency_ms" in lower and "stage" in lower
    needed_tokens = ["monotonic", "cumulative", " ms", "latency_ms", "round(", "0.001"]
    missing_tokens = [t for t in needed_tokens if t not in lower]
    missing_stages = [s for s in STAGE_NAMES if s not in lower]

    # 必须有"判定即出"和"端到端"的对比
    has_judge_only = "判定即出" in text or "judge_only" in lower
    has_end_to_end = "端到端" in text or "end-to-end" in lower or "end_to_end" in lower

    ok = (
        section_marker
        and not missing_tokens
        and not missing_stages
        and has_judge_only
        and has_end_to_end
    )
    _record(
        "V1_contract_doc_stage_section",
        ok=ok,
        missing_tokens=missing_tokens,
        missing_stages=missing_stages,
        has_judge_only=has_judge_only,
        has_end_to_end=has_end_to_end,
    )


def v2_proactive_py_emit_sites() -> None:
    """V2: coco/proactive.py 仍含 4 处 latency_ms emit kwarg (与 interact-018 wire 同步).

    注: 实际 emit 站点 4 个 (1 个 emit_emotion_alert + 3 个 maybe_trigger 内
    的 _trace_emit), 与 interact-018 V2 一致。文档 §5 仍登记 5 个 stage 名
    清单 (含 normal — 是 fusion_boost / mm_proactive / normal 入口快照的
    default 分支, 不额外加 emit 站点)。
    """
    src_path = ROOT / "coco" / "proactive.py"
    src = src_path.read_text(encoding="utf-8")
    count_kwarg = src.count("latency_ms=")
    # 至少 4 处 (1 emit_emotion_alert + 3 _trace_emit 站点); 注释中也含
    # "latency_ms" 但不带 "=" 后缀, 不被 count_kwarg 计入。
    ok = count_kwarg >= 4
    _record(
        "V2_proactive_py_emit_sites",
        ok=ok,
        latency_ms_kwarg_count=count_kwarg,
        expected_at_least=4,
    )


def v3_latency_monotonic_in_fixture() -> None:
    """V3: fixture 跑 maybe_trigger, 收集 emit latency_ms 序列, 断言单调非降.

    用 interact-018 同款 fake fixture 跑一次 maybe_trigger 成功路径, 捕获
    所有 proactive.trace emit; 按 emit 顺序看 latency_ms 是否单调非降。
    """
    os.environ["COCO_PROACTIVE_TRACE"] = "1"

    from coco import proactive_trace as pt
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    from coco.power_state import PowerState

    captured: List[Dict[str, Any]] = []

    def _emit(event: str, **payload: Any) -> None:
        captured.append({"event": event, **payload})

    pt.set_emit_override(_emit)
    try:
        class _FakePS:
            current_state = PowerState.ACTIVE

        class _FakeFace:
            def latest(self):
                class _S:
                    present = True
                return _S()

        def _llm(text, *, system_prompt=None):
            return "你好呀"

        def _tts(text, blocking=True):
            return None

        cfg = ProactiveConfig(
            enabled=True,
            idle_threshold_s=10.0,
            cooldown_s=10.0,
            max_topics_per_hour=10,
            tick_s=1.0,
        )
        sched = ProactiveScheduler(
            config=cfg,
            power_state=_FakePS(),
            face_tracker=_FakeFace(),
            llm_reply_fn=_llm,
            tts_say_fn=_tts,
            emit_fn=_emit,
        )
        sched._last_interaction_ts = sched.clock() - 60.0
        ok_trigger = sched.maybe_trigger()
        if not ok_trigger:
            _record("V3_latency_monotonic_in_fixture", False,
                    reason="maybe_trigger returned False",
                    captured_events=len(captured))
            return

        # 仅看 proactive.trace 含 latency_ms
        traces = [
            e for e in captured
            if e.get("event") == "proactive.trace" and "latency_ms" in e
        ]
        if not traces:
            _record("V3_latency_monotonic_in_fixture", False,
                    reason="no trace events with latency_ms",
                    captured_events=len(captured))
            return

        # 单调非降检查 (cumulative)
        lats = [float(e["latency_ms"]) for e in traces]
        stages_seq = [e.get("stage") for e in traces]
        violations = []
        for i in range(1, len(lats)):
            if lats[i] < lats[i - 1] - 1e-6:  # 允许浮点抖动 1us
                violations.append({
                    "idx": i,
                    "prev": lats[i - 1],
                    "curr": lats[i],
                    "stage_prev": stages_seq[i - 1],
                    "stage_curr": stages_seq[i],
                })

        _record(
            "V3_latency_monotonic_in_fixture",
            ok=(not violations),
            traces_count=len(traces),
            latency_seq=lats,
            stages_seq=stages_seq,
            violations=violations,
        )
    finally:
        pt.set_emit_override(None)
        os.environ.pop("COCO_PROACTIVE_TRACE", None)


def v4_doc_stage_names_in_source() -> None:
    """V4: 文档 §5.3 列的 5 个 stage 名都能在 coco/proactive.py 找到 emit 站点字面量."""
    src = (ROOT / "coco" / "proactive.py").read_text(encoding="utf-8")
    missing = []
    for stage in STAGE_NAMES:
        # stage 名作为字符串字面出现在 _trace_emit / _et 调用附近
        # 用引号包裹防止匹配到注释中的孤立单词 (但 normal/fusion_boost 等
        # 多处出现, 这里只要存在 "stage" 字面量即可)
        needle1 = f'"{stage}"'
        needle2 = f"'{stage}'"
        if needle1 not in src and needle2 not in src:
            missing.append(stage)
    _record(
        "V4_doc_stage_names_in_source",
        ok=(not missing),
        missing_stage_names_in_source=missing,
        checked_stages=sorted(STAGE_NAMES),
    )


def v5_regression() -> None:
    """V5: verify_interact_018 / 019 / 020 子进程 rc=0."""
    targets = [
        "verify_interact_018.py",
        "verify_interact_019.py",
        "verify_interact_020.py",
    ]
    bad: List[Dict[str, Any]] = []
    rcs: Dict[str, int] = {}
    for t in targets:
        p = ROOT / "scripts" / t
        proc = subprocess.run(
            [sys.executable, str(p)],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=180,
        )
        rcs[t] = proc.returncode
        if proc.returncode != 0:
            bad.append({
                "target": t,
                "rc": proc.returncode,
                "stderr_tail": proc.stderr[-200:] if proc.stderr else "",
            })
    _record("V5_regression", ok=(not bad), rcs=rcs, bad=bad)


def main() -> int:
    v0_fingerprint()
    v1_contract_doc_stage_section()
    v2_proactive_py_emit_sites()
    v3_latency_monotonic_in_fixture()
    v4_doc_stage_names_in_source()
    v5_regression()

    all_ok = all(r["ok"] for r in RESULTS.values())
    summary = {
        "feature": "interact-021",
        "all_pass": all_ok,
        "results": RESULTS,
    }
    out_dir = ROOT / "evidence" / "interact-021"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n=== interact-021 verify summary ===")
    print(json.dumps({"feature": "interact-021", "all_pass": all_ok}, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
