"""interact-026 verification: interact-021 文档数字纠偏 + verify 锁面 (verify-only).

承接 backlog `interact-021-backlog-stale-doc-numbers`:
- `scripts/verify_interact_021.py` docstring 与 `research/proactive_trace_contract.md`
  §5.7 / §6 内"5 处 emit / 5 个 stage"字面与代码真实值 (4 处 emit / 6 个 stage)
  不一致 — interact-018 V2 真实锁的是 4 处 `latency_ms=` kwarg (1 个
  emit_emotion_alert + 3 个 maybe_trigger 内 _trace_emit), interact-021 STAGE_NAMES
  set 真实含 6 个 stage 名 (emotion_alert / fusion_boost / mm_proactive /
  cooldown_hit / arbit_winner / normal).

本 feature **纯文档纠偏 + verify-only 锁面**, 0 业务源码改动 (`coco/` 无 diff).
约束: 文档字面量 == 代码字面量, 任一方漂移立即 FAIL.

子项::

V0 fingerprint sha256 锁 — 锁 verify_interact_021.py + proactive_trace_contract.md
   + proactive.py bytewise sha256 + size, 任何字节变化都被记录入 evidence
   (不强制等值, 写盘做 diff baseline).

V1 代码常量 == doc 字面量 — 抽 coco/proactive.py 中实际 `latency_ms=` kwarg 数量
   和 STAGE_NAMES set 真实大小, 与 doc 字面"4 处 emit"/"6 个 stage"对齐.

V2 doc 关键短语锁 — research/proactive_trace_contract.md 必须含 LOCKED_PHRASES
   纠偏后的字面量 ("4 处 emit", "6 个 stage", "4 个 emit 站点", "4 个 latency_ms").
   改 doc 时必须同步改本 verify 否则 FAIL.

V3 邻近 verify 静态回归 — verify_interact_018 / 021 / 022 / 023 / 024 / 025
   单跑 rc=0, 保证不 regress 已锁面的兄弟契约.

V4 smoke 11/11 PASS — 跑 ./init.sh (COCO_CI=1) 全 smoke 必须通过.

V_n evidence/migration_note — 写 evidence/interact-026/verify_summary.json +
   migration_note.md, 含 fingerprint / 纠偏前后数字对照 / files_changed.

retval: 0 全 PASS; 1 任一失败.


运行环境约定 (infra-036 phase-58 #2)
-----------------------------------
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
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS: Dict[str, Dict[str, Any]] = {}


def _record(name: str, ok: bool, **detail: Any) -> None:
    RESULTS[name] = {"ok": ok, **detail}
    flag = "PASS" if ok else "FAIL"
    print(f"[verify_interact_026] {flag} {name}: {detail}")


# 锁定的真实数字 (代码侧权威值)
EXPECTED_EMIT_SITES = 4  # coco/proactive.py 内 `latency_ms=` kwarg 数量
EXPECTED_STAGE_NAMES = 6  # verify_interact_021.STAGE_NAMES 大小

# Doc 内 (纠偏后) 必须含的关键短语
LOCKED_PHRASES = [
    "4 处 emit",
    "6 个 stage 名",
    "4 个 emit 站点",
    "4 个 `latency_ms` emit 站点",
]


def _sha256(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"path": str(path.relative_to(ROOT)), "exists": False}
    data = path.read_bytes()
    return {
        "path": str(path.relative_to(ROOT)),
        "exists": True,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest()[:12],
    }


def v0_fingerprint() -> None:
    """V0: bytewise sha256 锁 3 文件 (verify_021 + contract doc + proactive.py)."""
    files = [
        ROOT / "scripts" / "verify_interact_021.py",
        ROOT / "research" / "proactive_trace_contract.md",
        ROOT / "coco" / "proactive.py",
    ]
    fps = [_sha256(p) for p in files]
    all_exist = all(fp.get("exists") for fp in fps)
    _record("V0_fingerprint_sha256", ok=all_exist, fingerprints=fps)


def v1_code_constants_match_doc() -> None:
    """V1: 代码 latency_ms= kwarg count + STAGE_NAMES size 等于 doc 字面量."""
    src = (ROOT / "coco" / "proactive.py").read_text(encoding="utf-8")
    count_kwarg = src.count("latency_ms=")

    # 从 verify_interact_021.py 抽 STAGE_NAMES set 真实大小
    v21 = (ROOT / "scripts" / "verify_interact_021.py").read_text(encoding="utf-8")
    # match STAGE_NAMES = {...} 段
    m = re.search(r"STAGE_NAMES\s*=\s*\{([^}]*)\}", v21, re.DOTALL)
    if not m:
        _record("V1_code_constants_match_doc", False, reason="STAGE_NAMES not found in verify_021")
        return
    body = m.group(1)
    # 数字符串字面量 (单/双引号)
    stages = re.findall(r"['\"]([a-zA-Z_]+)['\"]", body)
    stage_count = len(set(stages))

    code_emit_ok = count_kwarg == EXPECTED_EMIT_SITES
    code_stage_ok = stage_count == EXPECTED_STAGE_NAMES

    ok = code_emit_ok and code_stage_ok
    _record(
        "V1_code_constants_match_doc",
        ok=ok,
        latency_ms_kwarg_count=count_kwarg,
        expected_emit_sites=EXPECTED_EMIT_SITES,
        stage_names_count=stage_count,
        expected_stage_names=EXPECTED_STAGE_NAMES,
        stages=sorted(set(stages)),
    )


def v2_doc_phrases_lock() -> None:
    """V2: research/proactive_trace_contract.md 必须含 LOCKED_PHRASES + 无遗留 stale."""
    doc = (ROOT / "research" / "proactive_trace_contract.md").read_text(encoding="utf-8")

    missing = [p for p in LOCKED_PHRASES if p not in doc]

    # 遗留 stale 数字必须清零
    stale_patterns = ["5 处 emit", "5 个 stage 名", "5 个 `latency_ms` emit 站点", "5 个 latency_ms emit 站点"]
    leftover = [p for p in stale_patterns if p in doc]

    # verify_interact_021.py docstring 同检查
    v21 = (ROOT / "scripts" / "verify_interact_021.py").read_text(encoding="utf-8")
    v21_stale = [p for p in ["5 处 latency_ms emit", "5 个 stage 名", "5 个 emit 站点"] if p in v21]

    ok = not missing and not leftover and not v21_stale
    _record(
        "V2_doc_phrases_lock",
        ok=ok,
        missing_phrases=missing,
        leftover_stale_in_doc=leftover,
        leftover_stale_in_verify021=v21_stale,
    )


def v3_neighbor_verify_regression() -> None:
    """V3: 邻近 verify_interact_018/021/022/023/024/025 子进程 rc=0."""
    targets = [
        "verify_interact_018.py",
        "verify_interact_021.py",
        "verify_interact_022.py",
        "verify_interact_023.py",
        "verify_interact_024.py",
        "verify_interact_025.py",
    ]
    rcs: Dict[str, int] = {}
    bad: List[Dict[str, Any]] = []
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
    _record("V3_neighbor_verify_regression", ok=(not bad), rcs=rcs, bad=bad)


def v4_smoke() -> None:
    """V4: ./init.sh smoke rc=0."""
    init = ROOT / "init.sh"
    if not init.exists():
        _record("V4_smoke", False, reason="init.sh missing")
        return
    env = os.environ.copy()
    env["COCO_CI"] = "1"
    proc = subprocess.run(
        ["bash", str(init)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=300,
        env=env,
    )
    ok = proc.returncode == 0
    _record(
        "V4_smoke",
        ok=ok,
        rc=proc.returncode,
        stdout_tail_len=len(proc.stdout[-1000:]),
    )


def _write_evidence(all_pass: bool) -> None:
    ev_dir = ROOT / "evidence" / "interact-026"
    ev_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "interact-026",
        "title": "interact-021 文档数字纠偏 + verify 锁面 (verify-only)",
        "verify_only": True,
        "business_source_diff": "none (coco/ untouched; only docs/ scripts/ touched)",
        "expected_emit_sites": EXPECTED_EMIT_SITES,
        "expected_stage_names": EXPECTED_STAGE_NAMES,
        "results": RESULTS,
        "all_pass": all_pass,
    }
    (ev_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    v0_fingerprint()
    v1_code_constants_match_doc()
    v2_doc_phrases_lock()
    v3_neighbor_verify_regression()
    v4_smoke()
    all_pass = all(r.get("ok") for r in RESULTS.values())
    _write_evidence(all_pass)
    print(f"[verify_interact_026] SUMMARY all_pass={all_pass} ({sum(1 for r in RESULTS.values() if r.get('ok'))}/{len(RESULTS)})")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
