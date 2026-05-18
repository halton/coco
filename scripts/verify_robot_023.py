#!/usr/bin/env python3
"""robot-023 verify: verify_robot_015.py V3 标题文案 rename meta-验证 (verify-only doc fix).

来源 backlog: robot-015-backlog-v3-warn-once-rename
    "verify_robot_015 V3 标题/注释/contract 字符串使用 'warn-once', 实际源码
     (coco/proactive.py:1311-1317) 是 try/except + log.warning + 继续, 没有去抖
     语义。'warn-once' 命名误导, 应 rename 为 'warn-and-continue'。纯文案。"

判定记录: 源码 coco/proactive.py:1313-1317 行 V3 路径每次 enqueue 异常都会
log.warning, 没有 dedup / seen-set / once-flag, 即每次异常都 warn 一次。
故源 backlog 判定正确, 执行 rename: warn-once → warn-and-continue。

Acceptance
----------
V0 file existence: scripts/verify_robot_015.py 存在且可读。
V1 anchor: 文件含新 phrase ``warn-and-continue`` (rename 命中目标名)。
V2 no-stale: 文件不含旧 phrase ``warn-once`` (case-sensitive, 任一残留 → FAIL)。
V3 subprocess: ``python scripts/verify_robot_015.py`` rc==0 (rename 不破断言)。
V4 mutant: 在内存中把新 phrase 改回旧 phrase, 重跑 V1/V2 锁应 FAIL
    (反证 V1/V2 真的能在旧名上炸, 不是空锁)。
V5 summary: 写 evidence/robot-023/verify_summary.json。

Sim-first: 纯文件 + subprocess, 不依赖真硬件。
default-OFF 严守: 0 业务源码改动, 仅 scripts/verify_robot_015.py 文案 rename。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET = REPO_ROOT / "scripts" / "verify_robot_015.py"
EVIDENCE_DIR = REPO_ROOT / "evidence" / "robot-023"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

NEW_PHRASE = "warn-and-continue"
OLD_PHRASE = "warn-once"


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def v0_file_existence() -> dict:
    _ok(TARGET.exists(), f"V0 target missing: {TARGET}")
    text = TARGET.read_text(encoding="utf-8")
    _ok(len(text) > 0, "V0 target file is empty")
    return {"path": str(TARGET), "bytes": len(text.encode("utf-8"))}


def v1_anchor_new_phrase() -> dict:
    text = TARGET.read_text(encoding="utf-8")
    _ok(NEW_PHRASE in text,
        f"V1 anchor missing: expected literal {NEW_PHRASE!r} in {TARGET.name}")
    count = text.count(NEW_PHRASE)
    # 至少应在 docstring + V3 注释 + contract 字符串各出现一次 → ≥3
    _ok(count >= 3,
        f"V1 anchor count too low: got {count}, expected ≥3 occurrences "
        f"(docstring + V3 comment + contract summary)")
    return {"new_phrase_count": count}


def v2_no_stale_old_phrase() -> dict:
    text = TARGET.read_text(encoding="utf-8")
    _ok(OLD_PHRASE not in text,
        f"V2 stale old phrase leak: {OLD_PHRASE!r} still in {TARGET.name}")
    return {"old_phrase_count": 0}


def v3_subprocess_passes() -> dict:
    proc = subprocess.run(
        [sys.executable, str(TARGET)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=900,
    )
    _ok(proc.returncode == 0,
        f"V3 verify_robot_015 rc={proc.returncode} (expected 0)\n"
        f"STDOUT tail:\n{proc.stdout[-1000:]}\nSTDERR:\n{proc.stderr[-500:]}")
    return {"rc": proc.returncode, "stdout_len": len(proc.stdout)}


def v4_mutant_old_phrase_should_fail() -> dict:
    """反证 V1/V2 真能炸: 把目标文件 text 在内存中改回旧 phrase, 重跑 V1/V2 锁,
    必须 FAIL。不写盘, 不污染真文件。
    """
    text = TARGET.read_text(encoding="utf-8")
    mutant = text.replace(NEW_PHRASE, OLD_PHRASE)
    _ok(mutant != text, "V4 mutant: replace did nothing — anchor literal not present?")
    _ok(OLD_PHRASE in mutant, "V4 mutant: old phrase not re-injected")
    _ok(NEW_PHRASE not in mutant, "V4 mutant: new phrase still present (replace incomplete)")

    # 模拟 V1 锁在 mutant 上跑 → 应当 fail (新 phrase 缺失)
    v1_would_fail = NEW_PHRASE not in mutant
    # 模拟 V2 锁在 mutant 上跑 → 应当 fail (旧 phrase 残留)
    v2_would_fail = OLD_PHRASE in mutant
    _ok(v1_would_fail and v2_would_fail,
        f"V4 mutant did not flip both locks: v1_fail={v1_would_fail}, v2_fail={v2_would_fail}")
    return {"v1_would_fail_on_mutant": v1_would_fail,
            "v2_would_fail_on_mutant": v2_would_fail}


def main() -> int:
    results: dict = {}
    failures: list[str] = []

    cases = [
        ("V0_file_existence", v0_file_existence),
        ("V1_anchor_new_phrase", v1_anchor_new_phrase),
        ("V2_no_stale_old_phrase", v2_no_stale_old_phrase),
        ("V3_subprocess_passes", v3_subprocess_passes),
        ("V4_mutant_old_phrase_should_fail", v4_mutant_old_phrase_should_fail),
    ]

    for vname, fn in cases:
        try:
            results[vname] = {"status": "PASS", "detail": fn()}
            print(f"[{vname}] PASS")
        except AssertionError as e:
            results[vname] = {"status": "FAIL", "error": str(e)}
            failures.append(f"{vname}: {e}")
            print(f"[{vname}] FAIL: {e}")

    summary = {
        "feature": "robot-023",
        "status": "PASS" if not failures else "FAIL",
        "source_backlog": "robot-015-backlog-v3-warn-once-rename",
        "rename": {"from": OLD_PHRASE, "to": NEW_PHRASE,
                   "target_file": str(TARGET.relative_to(REPO_ROOT))},
        "source_semantics_note": (
            "coco/proactive.py:1313-1317 V3 enqueue-exception path 是 try/except "
            "+ log.warning + 继续, 无 dedup/once-flag, 故 'warn-once' 命名误导, "
            "rename → 'warn-and-continue' 与实际语义对齐。"
        ),
        "results": results,
        "failures": failures,
    }
    out_path = EVIDENCE_DIR / "verify_summary.json"
    out_path.write_text(
        json.dumps(summary, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {out_path}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
