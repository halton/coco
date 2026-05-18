"""interact-033 verification: verify_interact_018 V1c cooldown_hit latency_ms 覆盖 meta-lock.

承接 interact-018 Reviewer caveat (interact-018-backlog-v1-cooldown-coverage):
verify_interact_018.py V1 admit/reject 路径 latency_ms wire 已覆盖, cooldown_hit
路径 latency_ms 端到端断言此前缺失。本 feature 升级 verify_interact_018.py 加
V1c_cooldown_hit_latency_wire 档 (verify-only, 0 业务源码改动)。本脚本是
meta-lock: 锁住升级落地 + 不漂移。

子项::

V0 sys.path / 文件存在: scripts/verify_interact_018.py + verify_interact_033.py
   存在, 业务依赖 coco/proactive.py + coco/proactive_trace.py 存在。

V1 字面 sentinel: verify_interact_018.py 含 V1c 升级关键字面量集合
   (函数名 / record label / 关键断言短语 / docstring V1c 段)。

V2 关键锁参数: V1c 关键断言 (stage=='cooldown_hit', decision=='reject',
   reason=='cooldown', type-strict isinstance(lat, (int, float)) + bool 排除,
   lat >= 0) 全部以字面 anchor 形式存在; cooldown_s=30.0 fixture 参数锁定。

V3 mutant 反证: 静态扫已知 anti-anchor 不应出现于 verify_interact_018.py
   (例如旧错位字面 'cooldown_miss' / 'rate_limit_hit' 之类)。同时 V1c 新增
   anchor 数量 >= 下限 (避免被静默削弱)。

V4 verify_interact_018.py working-tree sha256 锁: 锁住升级后的全文件指纹,
   未来无意修改会被立即捕获 (吸收 robot-027 verify-self-hash bump 模式)。

V5 端到端 subprocess: 子进程跑 verify_interact_018.py rc==0, 且
   evidence/interact-018/verify_summary.json 中 V1c_cooldown_hit_latency_wire
   ok=True。

retval: 0 全 PASS; 1 任一失败
evidence: evidence/interact-033/verify_summary.json
"""
from __future__ import annotations

import hashlib
import json
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


# ---------------------------------------------------------------------------
# V0 file_existence
# ---------------------------------------------------------------------------

V018 = ROOT / "scripts" / "verify_interact_018.py"
V033 = ROOT / "scripts" / "verify_interact_033.py"
SRC_PROACTIVE = ROOT / "coco" / "proactive.py"
SRC_PROACTIVE_TRACE = ROOT / "coco" / "proactive_trace.py"


def v0_file_existence() -> None:
    files = {
        "verify_interact_018.py": V018,
        "verify_interact_033.py": V033,
        "coco/proactive.py": SRC_PROACTIVE,
        "coco/proactive_trace.py": SRC_PROACTIVE_TRACE,
    }
    missing = [k for k, p in files.items() if not p.exists()]
    sizes = {k: (p.stat().st_size if p.exists() else 0) for k, p in files.items()}
    _record(
        "V0_file_existence",
        ok=(not missing),
        missing=missing,
        sizes=sizes,
        syspath_root=str(ROOT),
    )


# ---------------------------------------------------------------------------
# V1 字面 sentinel
# ---------------------------------------------------------------------------

# V1c 升级关键字面量集合 (函数名 / record label / 关键断言短语 / docstring)
V1C_ANCHORS = [
    "def v1c_cooldown_hit_latency_wire(",
    "V1c_cooldown_hit_latency_wire",
    "V1c (interact-033 升级)",
    "v1c_cooldown_hit_latency_wire()",  # main 调用点
    "cooldown_hit emit (decision=reject, reason=cooldown) 含 latency_ms wire",
]


def v1_literal_sentinel() -> None:
    text = V018.read_text(encoding="utf-8")
    missing = [a for a in V1C_ANCHORS if a not in text]
    counts = {a: text.count(a) for a in V1C_ANCHORS}
    _record(
        "V1_literal_sentinel",
        ok=(not missing),
        missing=missing,
        counts=counts,
        total_anchors=len(V1C_ANCHORS),
    )


# ---------------------------------------------------------------------------
# V2 关键锁参数 (硬编码 anchor)
# ---------------------------------------------------------------------------

V1C_ASSERTION_ANCHORS = [
    'e.get("stage") == "cooldown_hit"',
    'e.get("decision") == "reject"',
    'sample.get("reason") != "cooldown"',
    "isinstance(lat, bool) or not isinstance(lat, (int, float))",
    "if lat < 0:",
    "cooldown_s=30.0,",  # fixture 参数锁定 (cooldown 窗口足够长保证抑制)
    "sched._last_interaction_ts = sched.clock() - 600.0",  # idle 满足
]


def v2_assertion_lock() -> None:
    text = V018.read_text(encoding="utf-8")
    missing = [a for a in V1C_ASSERTION_ANCHORS if a not in text]
    _record(
        "V2_assertion_lock",
        ok=(not missing),
        missing=missing,
        total=len(V1C_ASSERTION_ANCHORS),
    )


# ---------------------------------------------------------------------------
# V3 mutant 反证: anti-anchor 应缺席, 新 anchor 计数下限
# ---------------------------------------------------------------------------

# 这些字面量是 V1c 升级"应该不存在"的错位 (mutant signals)
ANTI_ANCHORS = [
    "cooldown_miss",       # 错位 stage 名
    "rate_limit_hit",      # 错位 stage 名 (与 cooldown 路径混淆)
    "stage == 'cooldown'",  # 错位 (源码 stage 名是 cooldown_hit, 不是 cooldown)
    'stage == "cooldown")',  # 同上 (双引号变体)
]

# V1c 新增锚点最小计数下限 (避免静默被删/弱化)
NEW_ANCHOR_MIN_COUNTS = {
    "V1c_cooldown_hit_latency_wire": 4,  # docstring + def 名 + 多次 _record + main
    "cooldown_hit": 4,                     # docstring + assertion + detail msg + comment
    "_lat_start": 0,                       # 源码概念 (本脚本不必出现, 仅占位)
}


def v3_mutant_anti_anchors() -> None:
    text = V018.read_text(encoding="utf-8")
    bad_present = [a for a in ANTI_ANCHORS if a in text]
    bad_counts: List[str] = []
    counts: Dict[str, int] = {}
    for a, lower in NEW_ANCHOR_MIN_COUNTS.items():
        c = text.count(a)
        counts[a] = c
        if c < lower:
            bad_counts.append(f"{a}: {c} < {lower}")
    ok = (not bad_present) and (not bad_counts)
    _record(
        "V3_mutant_anti_anchors",
        ok=ok,
        bad_present=bad_present,
        counts=counts,
        bad_counts=bad_counts,
    )


# ---------------------------------------------------------------------------
# V4 verify_interact_018.py working-tree sha256 锁
# ---------------------------------------------------------------------------

# 升级后的 sha256 指纹 (working tree)
EXPECTED_V018_SHA256 = "775d5b09c6e4093c40d17a9f863c2f23cbdbdf1984b45d741ecf1e4e960fa18b"


def v4_v018_sha256_lock() -> None:
    data = V018.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    ok = (got == EXPECTED_V018_SHA256)
    _record(
        "V4_v018_sha256_lock",
        ok=ok,
        expected=EXPECTED_V018_SHA256,
        got=got,
        file_size=len(data),
        hint=("verify_interact_018.py 内容漂移; 若是预期改动, "
              "请同步 bump EXPECTED_V018_SHA256") if not ok else "",
    )


# ---------------------------------------------------------------------------
# V5 端到端 subprocess
# ---------------------------------------------------------------------------


def v5_subprocess_v018() -> None:
    proc = subprocess.run(
        [sys.executable, str(V018)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=180,
    )
    rc = proc.returncode

    summary_path = ROOT / "evidence" / "interact-018" / "verify_summary.json"
    v1c_ok = False
    v1c_detail: Dict[str, Any] = {}
    parse_err = ""
    if summary_path.exists():
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            results = data.get("results", [])
            if isinstance(results, list):
                for entry in results:
                    if entry.get("name") == "V1c_cooldown_hit_latency_wire":
                        v1c_ok = bool(entry.get("ok"))
                        v1c_detail = dict(entry)
                        break
        except Exception as e:  # noqa: BLE001
            parse_err = repr(e)

    ok = (rc == 0) and v1c_ok
    _record(
        "V5_subprocess_v018",
        ok=ok,
        rc=rc,
        v1c_ok=v1c_ok,
        v1c_detail=v1c_detail,
        parse_err=parse_err,
        stderr_tail=proc.stderr[-200:] if proc.stderr else "",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    v0_file_existence()
    v1_literal_sentinel()
    v2_assertion_lock()
    v3_mutant_anti_anchors()
    v4_v018_sha256_lock()
    v5_subprocess_v018()

    all_ok = all(r["ok"] for r in RESULTS.values())
    summary = {
        "feature": "interact-033",
        "all_pass": all_ok,
        "results": RESULTS,
    }
    out_dir = ROOT / "evidence" / "interact-033"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n=== interact-033 verify summary ===")
    print(json.dumps({"feature": "interact-033", "all_pass": all_ok},
                     ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
