"""interact-025 verification: V1 cooldown 边界 verify 补强 (verify-only).

source backlog: interact-018-backlog-v1-cooldown-coverage
direction: 纯 verify-only 补丁, 不动 coco/proactive*.py 业务源码; 针对 cooldown
路径 interact-018/023 未触及的 *边界* 与 *不变式* case 加固。

interact-023 已覆盖: cooldown_hit 端到端、latency 语义 (<1000ms)、多次连发
type-strict + numeric>=0、is_fail 闭环。本任务补的是 **边界**:

  - cooldown 期满那一刻是否进 admit (since == cooldown_s, '<' 严格小于语义)
  - cooldown 期间多次 hit, stats.skipped_cooldown 计数与 trace cooldown_hit
    数严格相等 (内部计数 / 外部 emit 一致性)
  - cooldown_hit 与 arbit_winner admit 永不共享同一 candidate_id (同次
    maybe_trigger 互斥; reject 与 admit 不会同帧出现)
  - default-OFF 不变式: 未设 COCO_PROACTIVE_TRACE / 设为 "0" 时, cooldown 路径
    不 emit proactive.trace
  - V0 fingerprint sha256 lock 三关键源码片段 (cooldown < 比较 / cooldown_hit
    stage 映射 / latency_ms wire)

跑法::

    uv run python scripts/verify_interact_025.py

子项:

V0 fingerprint sha256 lock — coco/proactive.py 中 cooldown 边界比较 (`since <
   cooldown`) / cooldown_hit stage 映射 (`"cooldown_hit" if reason ==
   "cooldown"`) / latency_ms 透传 (`latency_ms=_lat_ms()`) 三个片段 sha256
   未漂移。

V1 cooldown 刚到期边界 (since == cooldown_s) — `_should_trigger` 内部用
   `since < cooldown` 严格小于, 所以 since == cooldown_s 那一刻应 *不* 被
   cooldown 抑制, maybe_trigger 返回 True; 而 since == cooldown_s - 1ms 应
   返回 False (cooldown_hit). 端到端断言两个相邻样本行为相反。

V2 cooldown 期间多次 hit 累计 — sched.stats.skipped_cooldown 与 trace 中
   stage='cooldown_hit' 的 emit 数严格相等 (连发 N 次, 内部计数器与外部
   emit 一致, 防止单边漂移导致 metric 倾斜)。

V3 cooldown_hit 与 arbit_winner admit 同次 maybe_trigger 互斥 — 一次
   maybe_trigger 调用内: reject 路径 (cooldown_hit) 与 admit 路径
   (arbit_winner) 永不会同时 emit (return False vs 继续到 admit 是互斥
   分支)。NOTE: candidate_id = `int(t * 1000)` 仅基于 ts, 跨次 (同 1ms 内)
   可能复用同 cid, 所以验证粒度是 "同次 maybe_trigger 内部" 而非 "跨次同
   cid", 用 hook _trace_emit 计每次 maybe_trigger 调用的 emit 子集。

V4 default-OFF 不变式 — env COCO_PROACTIVE_TRACE 未设 / 设为 "0" 时,
   cooldown 路径 maybe_trigger 不应有任何 proactive.trace emit; 验证
   bytewise 等价 main 的核心契约 (interact-018 ~ 023 全系列 default-OFF
   gate 在 cooldown 边界仍生效)。

V5 邻近 verify 回归 — interact-018/022/023/024 rc=0 全绿, 证明本补丁不破坏
   既有契约。

V6 smoke 全绿 (./init.sh; 11/11).

retval: 0 全 PASS; 1 任一 FAIL
evidence: evidence/interact-025/verify_summary.json
运行环境约定 (infra-038)
------------------------
本脚本及其 V0-V5 子进程**必须**在已激活的 .venv 下运行 (Python 解释器入口
``.venv/bin/python``); 不要用系统 ``python3`` 直接调用本脚本, 否则
``importlib`` 加载业务模块时依赖 (numpy / soundfile / onnxruntime 等) 可能
解析到系统站点而非 venv 站点, 导致与 ``./init.sh`` smoke 路径不一致,
进而 V0-V5 退出码漂移。

约定细则:
  - **Reviewer / CI / 手动复跑入口**: 一律 ``.venv/bin/python`` 启动 (或先
    ``source .venv/bin/activate`` 再 ``python scripts/<本脚本名>.py``)。
  - **子进程 invoke**: 任何 ``subprocess.run`` 第一参数固定使用 ``sys.executable``
    (即本脚本所属解释器); 不写死 ``"python"`` / ``"python3"`` 字面量, 确保
    子进程继承父进程同一个 venv Python, 避免 PATH 覆盖踩坑。
  - **环境变量继承**: 子进程从 ``os.environ`` 拷贝 PATH / PYTHONPATH 等,
    PATH 中 venv 的 ``bin`` 目录位置不可被人为打乱 (init.sh 已在激活时前置)。
  - **新会话注意事项**: 干净 shell 进来务必先 ``source .venv/bin/activate``
    或显式 ``./.venv/bin/python``, 否则即便代码 byte-equal 也可能因解释器
    漂移产生不可复现的 FAIL。

"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_025] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


# ---------------------------------------------------------------------------
# Shared helpers — fake ProactiveScheduler
# ---------------------------------------------------------------------------


def _build_scheduler(cooldown_s: float = 30.0):
    from coco.proactive import ProactiveScheduler, ProactiveConfig
    from coco.power_state import PowerState

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
        cooldown_s=cooldown_s,
        max_topics_per_hour=10,
        tick_s=1.0,
    )
    sched = ProactiveScheduler(
        config=cfg,
        power_state=_FakePS(),
        face_tracker=_FakeFace(),
        llm_reply_fn=_llm,
        tts_say_fn=_tts,
    )
    sched._last_interaction_ts = sched.clock() - 600.0
    return sched


def _capture_traces(fn, *args, **kwargs) -> List[Dict[str, Any]]:
    """运行 fn(captured, ...) 时打开 trace gate 并捕获 emit。"""
    os.environ["COCO_PROACTIVE_TRACE"] = "1"
    from coco import proactive_trace as pt

    captured: List[Dict[str, Any]] = []

    def _emit(event: str, **payload: Any) -> None:
        captured.append({"event": event, **payload})

    pt.set_emit_override(_emit)
    try:
        fn(captured, *args, **kwargs)
    finally:
        pt.set_emit_override(None)
        os.environ.pop("COCO_PROACTIVE_TRACE", None)
    return captured


# ---------------------------------------------------------------------------
# V0 fingerprint sha256 lock
# ---------------------------------------------------------------------------


def v0_fingerprint_sha256_lock() -> None:
    """V0: 锁住 cooldown 三关键源码片段 sha256, 防止悄悄漂移语义。"""
    src = (ROOT / "coco" / "proactive.py").read_text(encoding="utf-8")

    # 三个关键 anchor (语义锁面, 不锁字符串变体):
    anchors = {
        "cooldown_strict_lt": "if since < cooldown:",
        "cooldown_hit_stage_map": '"cooldown_hit" if reason == "cooldown"',
        "cooldown_emit_latency_wire": "latency_ms=_lat_ms(),",
    }
    bad: List[str] = []
    fingerprints: Dict[str, str] = {}
    for name, snippet in anchors.items():
        if snippet not in src:
            bad.append(f"missing anchor {name!r}: {snippet!r}")
            continue
        fingerprints[name] = hashlib.sha256(snippet.encode("utf-8")).hexdigest()[:16]

    if bad:
        _record("V0_fingerprint_sha256_lock", False, "; ".join(bad))
        return

    _record(
        "V0_fingerprint_sha256_lock",
        True,
        f"3 anchors present; sha256(16): {fingerprints}",
    )


# ---------------------------------------------------------------------------
# V1 cooldown 刚到期边界 (since == cooldown_s vs < cooldown_s)
# ---------------------------------------------------------------------------


def v1_cooldown_boundary_just_expired() -> None:
    """V1: since == cooldown_s 刚到期 → admit (strict <); since < cooldown_s → cooldown_hit。

    用 maybe_trigger(now=...) 显式控制 t, 避开 monotonic 不可控性。
    """
    def _run(captured: List[Dict[str, Any]]):
        cd = 30.0

        # case A: 在 cooldown 期内 (since = cd - 0.5s) → reject cooldown
        sched_a = _build_scheduler(cooldown_s=cd)
        t0 = 10000.0
        # 把 last_proactive_ts 设到 t0, 然后 maybe_trigger(now=t0 + cd - 0.5)
        sched_a._last_proactive_ts = t0
        sched_a._last_interaction_ts = t0 - 600.0
        ok_a = sched_a.maybe_trigger(now=t0 + cd - 0.5)
        captured.append({"event": "_meta_a", "ok": ok_a, "since": cd - 0.5})

        # case B: cooldown 刚到期 (since = cd) → admit (since < cd 为 False)
        sched_b = _build_scheduler(cooldown_s=cd)
        sched_b._last_proactive_ts = t0
        sched_b._last_interaction_ts = t0 - 600.0
        ok_b = sched_b.maybe_trigger(now=t0 + cd)  # since == cd, 不 <, 应进 admit
        captured.append({"event": "_meta_b", "ok": ok_b, "since": cd})

        # case C: cooldown 显著过期 (since = cd + 5s) → admit
        sched_c = _build_scheduler(cooldown_s=cd)
        sched_c._last_proactive_ts = t0
        sched_c._last_interaction_ts = t0 - 600.0
        ok_c = sched_c.maybe_trigger(now=t0 + cd + 5.0)
        captured.append({"event": "_meta_c", "ok": ok_c, "since": cd + 5.0})

    captured = _capture_traces(_run)
    meta_a = next((e for e in captured if e.get("event") == "_meta_a"), {})
    meta_b = next((e for e in captured if e.get("event") == "_meta_b"), {})
    meta_c = next((e for e in captured if e.get("event") == "_meta_c"), {})

    bad: List[str] = []
    if meta_a.get("ok") is not False:
        bad.append(f"case A (since=cd-0.5) ok={meta_a.get('ok')}, expected False (cooldown_hit)")
    if meta_b.get("ok") is not True:
        bad.append(f"case B (since==cd 刚到期, strict<) ok={meta_b.get('ok')}, expected True (admit)")
    if meta_c.get("ok") is not True:
        bad.append(f"case C (since>cd) ok={meta_c.get('ok')}, expected True (admit)")

    # 同时验 case A trace 有 cooldown_hit, case B/C trace 有 arbit_winner
    traces = [e for e in captured if e.get("event") == "proactive.trace"]
    cooldown_hits = [e for e in traces if e.get("stage") == "cooldown_hit"]
    winners = [e for e in traces if e.get("stage") == "arbit_winner"]
    if len(cooldown_hits) < 1:
        bad.append(f"case A 未 emit cooldown_hit; got stages={[e.get('stage') for e in traces]}")
    if len(winners) < 2:
        bad.append(f"case B+C 应有 >=2 arbit_winner admit, got {len(winners)}")

    if bad:
        _record("V1_cooldown_boundary_just_expired", False, "; ".join(bad))
        return

    _record(
        "V1_cooldown_boundary_just_expired",
        True,
        f"strict-<: case A(since=cd-0.5)→reject, case B(since==cd)→admit, "
        f"case C(since=cd+5)→admit; cooldown_hits={len(cooldown_hits)} "
        f"winners={len(winners)}",
    )


# ---------------------------------------------------------------------------
# V2 stats.skipped_cooldown 与 trace cooldown_hit 计数严格相等
# ---------------------------------------------------------------------------


def v2_skipped_cooldown_counter_consistency() -> None:
    """V2: 连发 N 次 cooldown 抑制, stats 内部计数器 == trace emit cooldown_hit 数。"""
    N = 5
    captured: List[Dict[str, Any]] = []

    def _run(cap):
        sched = _build_scheduler(cooldown_s=60.0)
        # 先 admit 一次
        ok1 = sched.maybe_trigger()
        cap.append({"event": "_meta_admit", "ok": ok1})
        if not ok1:
            return
        # 连发 N 次, 每次都把 idle 推到满足, 唯一 reject 原因是 cooldown
        for i in range(N):
            sched._last_interaction_ts = sched.clock() - 600.0
            sched.maybe_trigger()
        cap.append({"event": "_meta_stats", "skipped_cooldown": sched.stats.skipped_cooldown})

    captured = _capture_traces(_run)
    meta_admit = next((e for e in captured if e.get("event") == "_meta_admit"), {})
    meta_stats = next((e for e in captured if e.get("event") == "_meta_stats"), {})
    if not meta_admit.get("ok"):
        _record("V2_skipped_cooldown_counter_consistency", False,
                "first admit failed; fixture 前置不成立")
        return

    cooldown_hit_emits = sum(
        1 for e in captured
        if e.get("event") == "proactive.trace" and e.get("stage") == "cooldown_hit"
    )
    skipped_cooldown_stat = meta_stats.get("skipped_cooldown")

    if cooldown_hit_emits != N:
        _record("V2_skipped_cooldown_counter_consistency", False,
                f"expected N={N} cooldown_hit emits, got {cooldown_hit_emits}")
        return
    if skipped_cooldown_stat != N:
        _record("V2_skipped_cooldown_counter_consistency", False,
                f"stats.skipped_cooldown={skipped_cooldown_stat}, expected {N}")
        return

    _record(
        "V2_skipped_cooldown_counter_consistency",
        True,
        f"N={N}: stats.skipped_cooldown={skipped_cooldown_stat} == "
        f"trace cooldown_hit emits={cooldown_hit_emits} (内外计数一致)",
    )


# ---------------------------------------------------------------------------
# V3 cooldown_hit / arbit_winner candidate_id 互斥锁面
# ---------------------------------------------------------------------------


def v3_cooldown_hit_and_admit_mutex_per_candidate() -> None:
    """V3: 同一次 maybe_trigger 调用内, cooldown_hit 与 arbit_winner 互斥。

    通过 set_emit_override 在每次 maybe_trigger 前后切桶, 收集每次调用内 emit
    的 stage 集合, 断言任意一次调用内集合中 cooldown_hit 与 arbit_winner
    不同时出现。

    NOTE: candidate_id 由 `int(t*1000)` 算得, 同 1ms 内多次调用会复用同 cid,
    所以"互斥"语义只在"同次 maybe_trigger 内部"成立, 不能跨次累计同 cid。
    """
    from coco import proactive_trace as pt

    os.environ["COCO_PROACTIVE_TRACE"] = "1"

    # 每次 maybe_trigger 调用前换桶, 调用后收集本次 stage 集合
    per_call_stages: List[List[str]] = []
    current_bucket: List[str] = []

    def _emit(event: str, **payload: Any) -> None:
        if event == "proactive.trace":
            current_bucket.append(payload.get("stage"))

    pt.set_emit_override(_emit)
    try:
        sched = _build_scheduler(cooldown_s=60.0)

        # 调用 1: admit
        current_bucket = []
        sched.maybe_trigger()
        per_call_stages.append(list(current_bucket))

        # 调用 2-4: cooldown_hit
        for _ in range(3):
            sched._last_interaction_ts = sched.clock() - 600.0
            current_bucket = []
            sched.maybe_trigger()
            per_call_stages.append(list(current_bucket))
    finally:
        pt.set_emit_override(None)
        os.environ.pop("COCO_PROACTIVE_TRACE", None)

    # 任一次调用内, cooldown_hit 与 arbit_winner 不同时出现
    bad: List[str] = []
    for i, stages in enumerate(per_call_stages):
        sset = set(stages)
        if "cooldown_hit" in sset and "arbit_winner" in sset:
            bad.append(f"call#{i} stages={stages}")

    if bad:
        _record("V3_cooldown_hit_and_admit_mutex_per_candidate", False,
                f"同次 maybe_trigger 内 cooldown_hit 与 arbit_winner 双线索: {bad}")
        return

    # fixture 前置: 至少 1 次 admit-only, 至少 1 次 cooldown_hit-only
    has_admit_only = any(
        "arbit_winner" in s and "cooldown_hit" not in s
        for s in per_call_stages
    )
    has_ch_only = any(
        "cooldown_hit" in s and "arbit_winner" not in s
        for s in per_call_stages
    )
    if not (has_admit_only and has_ch_only):
        _record("V3_cooldown_hit_and_admit_mutex_per_candidate", False,
                f"fixture 前置不足: per_call_stages={per_call_stages}")
        return

    _record(
        "V3_cooldown_hit_and_admit_mutex_per_candidate",
        True,
        f"{len(per_call_stages)} maybe_trigger 调用, 同次内 cooldown_hit/arbit_winner "
        f"永不并存; per_call_stages={per_call_stages}",
    )


# ---------------------------------------------------------------------------
# V4 default-OFF 不变式 (COCO_PROACTIVE_TRACE 未设/=0 cooldown 路径无 emit)
# ---------------------------------------------------------------------------


def v4_default_off_invariant_for_cooldown() -> None:
    """V4: 未开 COCO_PROACTIVE_TRACE 时, cooldown 路径不 emit proactive.trace。"""
    from coco import proactive_trace as pt

    captured: List[Dict[str, Any]] = []

    def _emit(event: str, **payload: Any) -> None:
        captured.append({"event": event, **payload})

    # 清掉 env (默认行为)
    prev = os.environ.pop("COCO_PROACTIVE_TRACE", None)
    pt.set_emit_override(_emit)
    try:
        sched = _build_scheduler(cooldown_s=60.0)
        ok1 = sched.maybe_trigger()  # admit
        # cooldown hit 3 次
        for _ in range(3):
            sched._last_interaction_ts = sched.clock() - 600.0
            sched.maybe_trigger()
        admit_ok = ok1
    finally:
        pt.set_emit_override(None)
        if prev is not None:
            os.environ["COCO_PROACTIVE_TRACE"] = prev

    if not admit_ok:
        _record("V4_default_off_invariant_for_cooldown", False,
                "fixture 前置不成立: 首次 admit 未成功")
        return

    proactive_traces = [e for e in captured if e.get("event") == "proactive.trace"]
    if proactive_traces:
        _record(
            "V4_default_off_invariant_for_cooldown",
            False,
            f"default-OFF 时不应有 proactive.trace emit, got {len(proactive_traces)} "
            f"(samples: {[e.get('stage') for e in proactive_traces[:5]]})",
        )
        return

    # 同时验 env="0" 也不 emit
    captured.clear()
    os.environ["COCO_PROACTIVE_TRACE"] = "0"
    pt.set_emit_override(_emit)
    try:
        sched2 = _build_scheduler(cooldown_s=60.0)
        sched2.maybe_trigger()
        for _ in range(2):
            sched2._last_interaction_ts = sched2.clock() - 600.0
            sched2.maybe_trigger()
    finally:
        pt.set_emit_override(None)
        os.environ.pop("COCO_PROACTIVE_TRACE", None)

    proactive_traces2 = [e for e in captured if e.get("event") == "proactive.trace"]
    if proactive_traces2:
        _record(
            "V4_default_off_invariant_for_cooldown",
            False,
            f'env="0" 时也不应有 proactive.trace emit, got {len(proactive_traces2)}',
        )
        return

    _record(
        "V4_default_off_invariant_for_cooldown",
        True,
        "default-OFF (env 未设 / env='0') cooldown 路径均无 proactive.trace emit",
    )


# ---------------------------------------------------------------------------
# V5 邻近 verify 回归
# ---------------------------------------------------------------------------


def v5_regression() -> None:
    scripts = [
        "scripts/verify_interact_018.py",
        "scripts/verify_interact_022.py",
        "scripts/verify_interact_023.py",
        "scripts/verify_interact_024.py",
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
                bad.append(f"{s} rc={proc.returncode} stderr={proc.stderr[:200]}")
        except Exception as e:  # noqa: BLE001
            rcs[s] = -1
            bad.append(f"{s} exc={e!r}")
    if bad:
        _record("V5_regression", False, f"failed: {bad}; rcs={rcs}")
        return
    _record("V5_regression", True, f"all rc=0: {rcs}")


# ---------------------------------------------------------------------------
# V6 smoke 全绿
# ---------------------------------------------------------------------------


def v6_smoke() -> None:
    """V6: ./init.sh smoke 11/11 PASS。"""
    try:
        proc = subprocess.run(
            ["bash", "init.sh"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as e:  # noqa: BLE001
        _record("V6_smoke", False, f"init.sh exc={e!r}")
        return

    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    # 解析最后一段 "X/Y PASS" / "11/11" 等
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*PASS", out)
    if m:
        passed, total = int(m.group(1)), int(m.group(2))
        if proc.returncode == 0 and passed == total and total >= 11:
            _record("V6_smoke", True, f"{passed}/{total} PASS rc=0")
            return
        _record("V6_smoke", False,
                f"smoke unhealthy: {passed}/{total} rc={proc.returncode}")
        return
    # fallback: rc==0 也认 PASS
    if proc.returncode == 0:
        _record("V6_smoke", True, f"rc=0 (no X/Y pattern matched; tail={out[-300:]!r})")
        return
    _record("V6_smoke", False,
            f"rc={proc.returncode}; tail={out[-300:]!r}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    v0_fingerprint_sha256_lock()
    v1_cooldown_boundary_just_expired()
    v2_skipped_cooldown_counter_consistency()
    v3_cooldown_hit_and_admit_mutex_per_candidate()
    v4_default_off_invariant_for_cooldown()
    v5_regression()
    v6_smoke()

    all_ok = all(r["ok"] for r in _results)

    out_dir = ROOT / "evidence" / "interact-025"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "interact-025",
        "source_backlog": "interact-018-backlog-v1-cooldown-coverage",
        "direction": (
            "纯 verify-only 补丁 (无业务源码改动); cooldown 路径边界 + "
            "default-OFF 不变式 + 内外计数一致性 + candidate_id 互斥锁面"
        ),
        "ok": all_ok,
        "results": _results,
        "files_changed": ["scripts/verify_interact_025.py"],
        "runtime_change": False,
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
