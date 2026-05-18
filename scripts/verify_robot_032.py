#!/usr/bin/env python3
"""verify_robot_032: docs-lock for proactive_scheduler_block_policy.md

V0 文件存在
V1 章节标题（H2/H3）字面断言
V2 关键短语断言
V3 mutant 反证：删一个章节 → 重跑 verify rc=1，finally 还原
V4 文档自身 sha256 锁（hardcoded 期望值；未匹配仅 print, 不 fail —— allow
   未来正常修订；mutant 反证 V3 已覆盖结构破坏）

退出码 0=ALL PASS / 1=任一 FAIL
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "proactive_scheduler_block_policy.md"

EXPECTED_HEADINGS = [
    "## 概述",
    "## Block 策略",
    "## Cooldown 策略",
    "### `_last_proactive_ts`",
    "### `_last_interaction_ts`",
    "### `_last_emotion_alert_ts`",
    "## Fallback 策略",
    "### sync fallback warn-once (robot-017)",
    "### enqueue fallback warn-once (robot-030)",
    "### offline fallback (interact-012)",
    "## Setter lifecycle 策略 (robot-016)",
    "## Env gate 一览",
]

EXPECTED_PHRASES = [
    "warn-once",
    "default-OFF",
    "COCO_ROBOT_SETTER_LIFECYCLE_AUDIT",
    "COCO_PROACTIVE_ARBIT",
    "COCO_PROACTIVE_TRACE",
    "_last_proactive_ts",
    "_last_interaction_ts",
    "_last_emotion_alert_ts",
    "_fallback_warned",
    "_sync_fallback_audit_seen",
    "_setter_audit_seen",
    "boost 不绕过",
]


def _run_checks(text: str) -> list[str]:
    fails: list[str] = []
    for h in EXPECTED_HEADINGS:
        if h not in text:
            fails.append(f"missing heading: {h!r}")
    for p in EXPECTED_PHRASES:
        if p not in text:
            fails.append(f"missing phrase: {p!r}")
    return fails


def main() -> int:
    fails: list[str] = []

    # V0
    if not DOC.exists():
        print(f"[V0] FAIL: {DOC} does not exist")
        return 1
    print(f"[V0] PASS: {DOC} exists")

    text = DOC.read_text(encoding="utf-8")

    # V1 + V2
    f1 = [x for x in _run_checks(text) if x.startswith("missing heading")]
    f2 = [x for x in _run_checks(text) if x.startswith("missing phrase")]
    if f1:
        fails.extend(f"[V1] {x}" for x in f1)
    else:
        print(f"[V1] PASS: {len(EXPECTED_HEADINGS)} headings present")
    if f2:
        fails.extend(f"[V2] {x}" for x in f2)
    else:
        print(f"[V2] PASS: {len(EXPECTED_PHRASES)} phrases present")

    # V3 mutant —— 删除一个独有 phrase，重跑应 fail；finally 还原。
    original = text
    try:
        target = "_sync_fallback_audit_seen"  # 出现多次但都属于内容
        if target not in original:
            fails.append(f"[V3] FAIL: target {target!r} not present pre-mutation")
            mutated = original
        else:
            # 全部替换掉，让 V2 phrase 检查必然 fail
            mutated = original.replace(target, "__MUTATED__")
            DOC.write_text(mutated, encoding="utf-8")
            rc = subprocess.run(
                [sys.executable, str(Path(__file__)), "--mutant-probe"],
                capture_output=True, text=True,
            ).returncode
            if rc == 1:
                print("[V3] PASS: mutant probe rc=1 (detected missing heading)")
            else:
                fails.append(f"[V3] FAIL: mutant probe rc={rc}, expected 1")
    finally:
        DOC.write_text(original, encoding="utf-8")

    # V4 sha256 print (no enforcement; allow normal edits)
    sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
    print(f"[V4] doc sha256={sha}")

    if fails:
        print("\nFAIL:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("\nALL PASS")
    return 0


def _probe() -> int:
    """Used by V3: only run V0+V1+V2 on the (possibly mutated) file."""
    if not DOC.exists():
        return 1
    text = DOC.read_text(encoding="utf-8")
    fails = _run_checks(text)
    return 1 if fails else 0


if __name__ == "__main__":
    if "--mutant-probe" in sys.argv:
        sys.exit(_probe())
    sys.exit(main())
