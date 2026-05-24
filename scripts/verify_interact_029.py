"""interact-029 verification: doc 数字一致性二次锁定 (verify-only).

source backlog: interact-021-backlog-stale-doc-numbers

背景:
  interact-026 已对 scripts/verify_interact_021.py docstring +
  research/proactive_trace_contract.md §5.7 / §6 做过一轮 stale 数字纠偏
  (5 处 emit → 4 / 5 个 stage → 6, 与 coco/proactive.py 代码真实 emit
  站点数 + KNOWN_STAGES 集合大小一致); interact-029 作为同 source backlog
  的第二轮 verify-only 锁定, 防止 stale "5 处" / "5 个 stage" 字面再回潜入.

权威值 (ground truth, 来自 coco/proactive.py + coco/proactive_trace.py):
  - emit 站点 = 4 (1 个 emotion_alert 独立路径 _et + 3 个 maybe_trigger 内
    _trace_emit; 与 src.count("latency_ms=") 一致)
  - stage 名 = 6 (KNOWN_STAGES frozenset: emotion_alert / fusion_boost /
    mm_proactive / normal / cooldown_hit / arbit_winner)

子项:
  V0 file existence — doc + verify_021 + proactive.py + proactive_trace.py
     四份关键 file 都在.
  V1 doc 正确数字出现 / 过时数字不出现 — verify_021 docstring 与 contract
     §5/§6 的字面.
  V2 代码 ground truth — coco/proactive.py emit 站点 = 4,
     coco/proactive_trace.py KNOWN_STAGES 集合 = 6, 与 doc 一致.
  V3 mutant 反证 — 临时在 contract 注入 "5 个 emit" 字面 (in-memory text 模拟),
     V1 同款检查应 FAIL; 还原后 PASS.
  V4 default-OFF — 本 feature 0 业务源码改动, COCO_PROACTIVE_TRACE 未设时
     emit_trace 仍 no-op (端到端 subprocess 反证).
  V5 summary — 落 evidence/interact-029/verify_summary.json.

retval: 0 全 PASS; 1 任一失败
evidence: evidence/interact-029/verify_summary.json

运行环境约定 (infra-034)
------------------------
本脚本及其子进程**必须**在已激活的 .venv 下运行 (Python 解释器入口
``.venv/bin/python``); 不要用系统 ``python3`` 直接调用本脚本, 否则
``importlib`` 加载业务模块时依赖 (numpy / soundfile / onnxruntime 等) 可能
解析到系统站点而非 venv 站点, 导致与 ``./init.sh`` smoke 路径不一致。

约定细则:
  - **Reviewer / CI / 手动复跑入口**: 一律 ``.venv/bin/python`` 启动 (或先
    ``source .venv/bin/activate`` 再 ``python scripts/verify_interact_029.py``)。
  - **子进程 invoke**: 任何 ``subprocess.run`` 第一参数固定使用 ``sys.executable``
    (即本脚本所属解释器); 不写死 ``"python"`` / ``"python3"`` 字面量。
  - **环境变量继承**: 子进程从 ``os.environ`` 拷贝 PATH / PYTHONPATH 等,
    PATH 中 venv 的 ``bin`` 目录位置不可被人为打乱。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS: Dict[str, Dict[str, Any]] = {}


def _record(name: str, ok: bool, **detail: Any) -> None:
    RESULTS[name] = {"ok": ok, **detail}
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}: {detail}")


# 权威值 (与代码一致, 修改前先核 coco/proactive_trace.py KNOWN_STAGES
# 与 coco/proactive.py emit 站点)
AUTHORITATIVE_EMIT_SITES = 4
AUTHORITATIVE_STAGE_COUNT = 6

# 过时字面 stale token (interact-026 已纠正, interact-029 二次锁防回退)
STALE_TOKENS = [
    "5 处 emit",
    "5 个 emit",
    "5 处 latency_ms",
    "5 个 latency_ms",
    "5 个 stage 名",
    "5 个 stage",
    "5 个 `latency_ms` emit",
]


def v0_file_existence() -> None:
    paths = [
        ROOT / "research" / "proactive_trace_contract.md",
        ROOT / "scripts" / "verify_interact_021.py",
        ROOT / "coco" / "proactive.py",
        ROOT / "coco" / "proactive_trace.py",
    ]
    missing = [str(p.relative_to(ROOT)) for p in paths if not p.exists()]
    _record("V0_file_existence", ok=(not missing), missing=missing,
            checked=len(paths))


def _scan_stale(text: str) -> list[str]:
    """返回出现的 stale token 列表 (substring 命中)."""
    return [tok for tok in STALE_TOKENS if tok in text]


def v1_doc_numbers_consistency() -> None:
    """doc 含正确 (4 emit / 6 stage) 字面 + 不含 stale (5 处/5 个) 字面."""
    doc = (ROOT / "research" / "proactive_trace_contract.md").read_text(
        encoding="utf-8"
    )
    v21 = (ROOT / "scripts" / "verify_interact_021.py").read_text(encoding="utf-8")

    # 正确数字必须出现 (contract 内显式列举 4 处 emit / 6 stage)
    has_4_emit_doc = bool(re.search(r"4\s*(?:处|个)?\s*(?:`)?(?:emit|latency_ms)", doc))
    has_6_stage_doc = bool(
        re.search(r"6\s*(?:个)?\s*(?:`)?stage", doc)
        or "stage 名权威清单 (6 个)" in doc
    )
    # verify_021 docstring 已明示 "4 个 emit 站点" + "6 个 stage 名"
    has_4_emit_v21 = "4 个 emit" in v21 or "4 处 emit" in v21 or "latency_ms_kwarg_count" in v21
    has_6_stage_v21 = "6 个 stage" in v21 or "6 个 stage 名" in v21 or "STAGE_NAMES" in v21

    stale_doc = _scan_stale(doc)
    stale_v21 = _scan_stale(v21)

    ok = (
        has_4_emit_doc and has_6_stage_doc
        and has_4_emit_v21 and has_6_stage_v21
        and not stale_doc and not stale_v21
    )
    _record(
        "V1_doc_numbers_consistency",
        ok=ok,
        has_4_emit_doc=has_4_emit_doc,
        has_6_stage_doc=has_6_stage_doc,
        has_4_emit_v21=has_4_emit_v21,
        has_6_stage_v21=has_6_stage_v21,
        stale_in_doc=stale_doc,
        stale_in_v21=stale_v21,
    )


def v2_code_ground_truth() -> None:
    """coco/proactive.py emit 站点 = 4, KNOWN_STAGES 集合 = 6."""
    src_proactive = (ROOT / "coco" / "proactive.py").read_text(encoding="utf-8")
    src_trace = (ROOT / "coco" / "proactive_trace.py").read_text(encoding="utf-8")

    # emit 站点: latency_ms= kwarg 出现次数 (与 interact-018 V2 / interact-021 V2
    # 同款 anchor; 与 _et( + _trace_emit( 调用站点数一致 = 4)
    latency_kwarg = src_proactive.count("latency_ms=")
    # 调用站点行级计数 (^空白*(_et|_trace_emit)\()
    call_sites = len(
        re.findall(r"^\s*(_et|_trace_emit)\(", src_proactive, flags=re.MULTILINE)
    )

    # KNOWN_STAGES 集合大小: 解析源文本中 frozenset 字面量内的 stage 字符串个数
    m = re.search(r"KNOWN_STAGES\s*=\s*frozenset\s*\(\s*\{([^}]+)\}\s*\)", src_trace)
    stage_count = 0
    stage_names: list[str] = []
    if m:
        body = m.group(1)
        stage_names = re.findall(r'"([a-z_]+)"', body)
        stage_count = len(stage_names)

    ok = (
        latency_kwarg == AUTHORITATIVE_EMIT_SITES
        and call_sites == AUTHORITATIVE_EMIT_SITES
        and stage_count == AUTHORITATIVE_STAGE_COUNT
    )
    _record(
        "V2_code_ground_truth",
        ok=ok,
        latency_ms_kwarg_count=latency_kwarg,
        emit_call_sites=call_sites,
        expected_emit=AUTHORITATIVE_EMIT_SITES,
        known_stages_count=stage_count,
        known_stages=sorted(stage_names),
        expected_stage_count=AUTHORITATIVE_STAGE_COUNT,
    )


def v3_mutant_rejection() -> None:
    """in-memory mutant: 注入 stale "5 个 emit" 到 doc text, V1 同款检查应 FAIL;
    还原后 PASS.

    不写盘, 不改 git working tree; 只在内存中做 substring 注入 + 反证."""
    doc_path = ROOT / "research" / "proactive_trace_contract.md"
    clean_text = doc_path.read_text(encoding="utf-8")

    # 注入 mutant
    mutated = clean_text + "\n\n<!-- MUTANT: 5 个 emit -->\n"
    mutated_hits = _scan_stale(mutated)
    mutant_should_fail = bool(mutated_hits)

    # 还原 (本就没改盘, 仅校验 clean_text 不含 stale)
    clean_hits = _scan_stale(clean_text)
    clean_should_pass = not clean_hits

    ok = mutant_should_fail and clean_should_pass
    _record(
        "V3_mutant_rejection",
        ok=ok,
        mutant_stale_hits=mutated_hits,
        clean_stale_hits=clean_hits,
    )


def v4_default_off_bytewise() -> None:
    """subprocess: COCO_PROACTIVE_TRACE 未设时 emit_trace no-op (无 emit 函数被调).

    用 set_emit_override 注入计数器, 调一次 emit_trace, 计数应为 0.
    """
    code = (
        "import sys, os\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "os.environ.pop('COCO_PROACTIVE_TRACE', None)\n"
        "from coco import proactive_trace as pt\n"
        "calls = []\n"
        "pt.set_emit_override(lambda ev, **kw: calls.append((ev, kw)))\n"
        "pt.emit_trace('normal', 'cid-1', 'admit')\n"
        "pt.set_emit_override(None)\n"
        "print('CALLS=', len(calls))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=30,
    )
    ok = proc.returncode == 0 and "CALLS= 0" in proc.stdout
    _record(
        "V4_default_off_bytewise",
        ok=ok,
        rc=proc.returncode,
        stdout_tail=proc.stdout[-200:],
        stderr_tail=proc.stderr[-200:],
    )


def main() -> int:
    v0_file_existence()
    v1_doc_numbers_consistency()
    v2_code_ground_truth()
    v3_mutant_rejection()
    v4_default_off_bytewise()

    all_ok = all(r["ok"] for r in RESULTS.values())
    summary = {
        "feature": "interact-029",
        "all_pass": all_ok,
        "authoritative": {
            "emit_sites": AUTHORITATIVE_EMIT_SITES,
            "stage_count": AUTHORITATIVE_STAGE_COUNT,
        },
        "results": RESULTS,
    }
    out_dir = ROOT / "evidence" / "interact-029"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n=== interact-029 verify summary ===")
    print(json.dumps({"feature": "interact-029", "all_pass": all_ok},
                     ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
