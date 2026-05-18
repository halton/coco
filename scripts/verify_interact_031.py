"""interact-031 verification: verify_interact_021 V3 多 emit 同 tick 覆盖 meta lock.

承接 interact-021 Reviewer caveat (interact-021-backlog-v3-multi-emit-coverage):
verify_interact_021.py V3 当前 fixture 仅捕 1 条 trace (arbit_winner admit 路径),
未覆盖同 tick 多 emit 场景 (_preempt_boost=True 触发站点 #2+#4 双 emit)。本 feature
在 verify_interact_021.py 新增 V3b 子模块, 构造 fusion_boost preempt + cooldown_hit
双 emit fixture, 0 业务源码改动, 纯 verify-only。

子项::

V0 file_existence: scripts/verify_interact_021.py + verify_interact_031.py 存在。

V1 v3b_anchor_in_verify_021: verify_interact_021.py 含 V3b 多 emit 锚点字面量
   ({V3B_FUNC_NAME, "fusion_boost", "cooldown_hit", "_preempt_boost",
   "COCO_PROACTIVE_ARBIT", "_last_emotion_alert_ts", "V3b_multi_emit_same_tick"})
   且 V3b 函数定义出现次数 >= 1 (行号锚定 def v3b_multi_emit_same_tick)。

V2 mutant_inverse: 临时把 V3b fixture 中关键设置 "_next_priority_boost = True"
   字面量统计应 >= 1 (mutant 反证: 若该行被删, 多 emit 路径不再触发)。

V3 subprocess_v021: 子进程跑 verify_interact_021.py rc=0, summary V3b 必须存在
   且 ok=True (锁 V3b 不被回退)。

V4 zero_business_diff: coco/proactive.py vs main 53413ac bytewise 等价 (sha256)。
   default-OFF / V3b fixture 显式 set COCO_PROACTIVE_ARBIT=1 仅在 verify 子进程内,
   不污染 main 进程。

V5 summary: 汇总 + evidence/interact-031/verify_summary.json。

retval: 0 全 PASS; 1 任一失败
evidence: evidence/interact-031/verify_summary.json
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


RESULTS: Dict[str, Dict[str, Any]] = {}


def _record(name: str, ok: bool, **detail: Any) -> None:
    RESULTS[name] = {"ok": ok, **detail}
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}: {detail}")


# V3b 函数名 (verify_interact_021.py 中的 def 行锚点)
V3B_FUNC_NAME = "v3b_multi_emit_same_tick"

# V3b fixture 必须出现的锚点字面量 (interact-031 多 emit 双站点锁)
V3B_ANCHORS = [
    "fusion_boost",
    "cooldown_hit",
    "_preempt_boost",
    "COCO_PROACTIVE_ARBIT",
    "_last_emotion_alert_ts",
    "V3b_multi_emit_same_tick",
]

# 已知 main 53413ac coco/proactive.py 的 sha256 (吸收 robot-025 Reviewer
# findings F1/F2 教训: hardcoded prefix 常量, 不在 EXPECTED 中同表达式自洽)
PROACTIVE_PY_SHA256_PREFIX_MAIN_53413AC = "EXPECTED_PROACTIVE_PY_SHA256_PREFIX_PLACEHOLDER"


def v0_file_existence() -> None:
    v021 = ROOT / "scripts" / "verify_interact_021.py"
    v031 = ROOT / "scripts" / "verify_interact_031.py"
    missing = [str(p.relative_to(ROOT)) for p in (v021, v031) if not p.exists()]
    _record(
        "V0_file_existence",
        ok=(not missing),
        missing=missing,
        v021_size=v021.stat().st_size if v021.exists() else 0,
        v031_size=v031.stat().st_size if v031.exists() else 0,
    )


def v1_v3b_anchor_in_verify_021() -> None:
    """V1: verify_interact_021.py 含 V3b 多 emit 锚点字面量 + V3b 函数定义."""
    v021 = ROOT / "scripts" / "verify_interact_021.py"
    text = v021.read_text(encoding="utf-8")

    missing_anchors = [a for a in V3B_ANCHORS if a not in text]

    # 行号锚定: def v3b_multi_emit_same_tick 出现次数应 >= 1
    def_marker = f"def {V3B_FUNC_NAME}"
    def_count = text.count(def_marker)

    # main() 必须调用 v3b
    call_marker = f"{V3B_FUNC_NAME}()"
    call_count = text.count(call_marker)

    ok = (not missing_anchors) and (def_count >= 1) and (call_count >= 1)
    _record(
        "V1_v3b_anchor_in_verify_021",
        ok=ok,
        missing_anchors=missing_anchors,
        def_marker=def_marker,
        def_count=def_count,
        call_count=call_count,
        v3b_anchors_total=len(V3B_ANCHORS),
    )


def v2_mutant_inverse() -> None:
    """V2: mutant 反证 — 关键 fixture 设置行字面量出现次数 >= 1.

    锁 _next_priority_boost = True / _last_emotion_alert_ts = now_t - 0.3
    两条触发多 emit 路径的关键设置不被静默删除。若任一被删, V3b 多 emit 不再
    成立 (要么走单 emit arbit_winner, 要么走 normal 单 emit)。
    """
    v021 = ROOT / "scripts" / "verify_interact_021.py"
    text = v021.read_text(encoding="utf-8")

    # 关键 fixture 设置 (interact-031 多 emit 触发条件)
    boost_line = "sched._next_priority_boost = True"
    emo_line = "sched._last_emotion_alert_ts"
    cooldown_setup = "sched._last_proactive_ts"

    counts = {
        "boost_line": text.count(boost_line),
        "emo_line": text.count(emo_line),
        "cooldown_setup": text.count(cooldown_setup),
    }
    bad = [k for k, v in counts.items() if v < 1]
    _record(
        "V2_mutant_inverse",
        ok=(not bad),
        counts=counts,
        bad=bad,
    )


def v3_subprocess_v021() -> None:
    """V3: 子进程跑 verify_interact_021.py rc=0 + V3b 子项 ok=True."""
    v021 = ROOT / "scripts" / "verify_interact_021.py"
    proc = subprocess.run(
        [sys.executable, str(v021)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=180,
    )
    rc = proc.returncode

    # 读 evidence/interact-021/verify_summary.json 验 V3b 结果
    summary_path = ROOT / "evidence" / "interact-021" / "verify_summary.json"
    v3b_ok = False
    v3b_traces = -1
    v3b_stages: List[str] = []
    if summary_path.exists():
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            results = data.get("results", {})
            v3b = results.get("V3b_multi_emit_same_tick", {})
            v3b_ok = bool(v3b.get("ok"))
            v3b_traces = int(v3b.get("traces_count", -1))
            v3b_stages = list(v3b.get("stages_seq", []))
        except Exception as e:  # noqa: BLE001
            _record("V3_subprocess_v021", False,
                    reason=f"summary parse: {e!r}", rc=rc)
            return

    ok = (rc == 0) and v3b_ok and v3b_traces >= 2
    _record(
        "V3_subprocess_v021",
        ok=ok,
        rc=rc,
        v3b_ok=v3b_ok,
        v3b_traces=v3b_traces,
        v3b_stages=v3b_stages,
        stderr_tail=proc.stderr[-200:] if proc.stderr else "",
    )


def v4_zero_business_diff() -> None:
    """V4: coco/proactive.py 与 main HEAD 53413ac bytewise 等价.

    interact-031 是 verify-only feature, coco/proactive.py 必须 0 改动。
    用 git show 取 main 53413ac 时 coco/proactive.py 的 blob, 比 sha256。
    EXPECTED 用 git show 实测 (不是同表达式自洽永真) — main 基线 53413ac
    在 task brief 中明确指定, 当前 working tree 应与该基线一致。
    """
    cur_path = ROOT / "coco" / "proactive.py"
    cur_sha = hashlib.sha256(cur_path.read_bytes()).hexdigest()

    # 用 git show 53413ac:coco/proactive.py 取基线
    try:
        proc = subprocess.run(
            ["git", "show", "53413ac:coco/proactive.py"],
            capture_output=True,
            cwd=str(ROOT),
            timeout=30,
        )
        if proc.returncode != 0:
            _record("V4_zero_business_diff", False,
                    reason="git show failed",
                    rc=proc.returncode,
                    stderr=proc.stderr.decode("utf-8", errors="replace")[-200:])
            return
        base_sha = hashlib.sha256(proc.stdout).hexdigest()
    except Exception as e:  # noqa: BLE001
        _record("V4_zero_business_diff", False, reason=f"git show exception: {e!r}")
        return

    ok = (cur_sha == base_sha)
    _record(
        "V4_zero_business_diff",
        ok=ok,
        cur_sha=cur_sha,
        base_sha=base_sha,
        baseline_commit="53413ac",
    )


def v5_summary() -> None:
    all_ok = all(r["ok"] for r in RESULTS.values())
    _record("V5_summary", ok=all_ok,
            total=len(RESULTS),
            pass_count=sum(1 for r in RESULTS.values() if r["ok"]))


def main() -> int:
    v0_file_existence()
    v1_v3b_anchor_in_verify_021()
    v2_mutant_inverse()
    v3_subprocess_v021()
    v4_zero_business_diff()
    v5_summary()

    all_ok = all(r["ok"] for r in RESULTS.values())
    summary = {
        "feature": "interact-031",
        "all_pass": all_ok,
        "results": RESULTS,
    }
    out_dir = ROOT / "evidence" / "interact-031"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n=== interact-031 verify summary ===")
    print(json.dumps({"feature": "interact-031", "all_pass": all_ok}, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
