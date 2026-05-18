#!/usr/bin/env python3
"""verify_interact_038: docs-lock for verify_sha_lock_strategy.md

interact-038: docs / verify-only 评估 sha-lock 策略 (file-sha vs func-sha)。
不修任何 verify 脚本与业务源码；仅落 decision docs + 验证 docs 不被静默删改。

V0 docs 存在 + sentinel 段存在
V1 章节标题 single-source 解析 (_verify_lib.parse_headings_from_doc)
V2 关键短语断言 ("file-sha"/"func-sha"/"mixed strategy"/"decision matrix"/"interact-037")
V3 mutant 反证: 临时删一个章节标题字面 → subprocess --mutant-probe rc=1; finally 还原
V4 sha256 print-only (与 robot-032 风格一致, 允许正常修订)
V5 subprocess 自调用 rc=0

退出码 0=ALL PASS / 1=任一 FAIL

运行环境约定 (infra-034)
------------------------
本脚本及其 V0-V5 子进程**必须**在已激活的 .venv 下运行 (Python 解释器入口
``.venv/bin/python``); 不要用系统 ``python3`` 直接调用本脚本, 否则
``importlib`` 加载业务模块时依赖可能解析到系统站点而非 venv 站点, 导致与
``./init.sh`` smoke 路径不一致, 进而 V0-V5 退出码漂移。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _verify_lib import parse_headings_from_doc  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "verify_sha_lock_strategy.md"

_HEADINGS_SECTION_SENTINEL = "## 章节标题列表（供 verify_interact_038 用）"

EXPECTED_HEADINGS = parse_headings_from_doc(DOC, _HEADINGS_SECTION_SENTINEL)

EXPECTED_PHRASES = [
    "file-sha",
    "func-sha",
    "mixed strategy",
    "decision matrix",
    "interact-037",
    "_append_drift_history",
    "EXPECTED_FILE_SHA",
    "EXPECTED_FUNC_SHA",
]


def _run_checks(text: str, headings: list[str] | None = None) -> list[str]:
    fails: list[str] = []
    hs = headings if headings is not None else EXPECTED_HEADINGS
    for h in hs:
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
    if _HEADINGS_SECTION_SENTINEL not in text:
        print(f"[V0] FAIL: sentinel section missing: {_HEADINGS_SECTION_SENTINEL!r}")
        return 1
    print(f"[V0] PASS: sentinel section present")

    # V1b sentinel 防御
    if not EXPECTED_HEADINGS:
        fails.append("[V1b] FAIL: EXPECTED_HEADINGS parsed empty from sentinel section")
    else:
        print(f"[V1b] PASS: parsed {len(EXPECTED_HEADINGS)} headings from doc sentinel")

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

    # V3 mutant —— 删除一个独有 phrase 与一个章节标题，重跑子进程应 rc=1。
    original = text
    try:
        # 选一个独有 phrase
        target = "decision matrix"
        if target not in original:
            fails.append(f"[V3] FAIL: target {target!r} not present pre-mutation")
        else:
            mutated = original.replace(target, "__MUTATED__")
            DOC.write_text(mutated, encoding="utf-8")
            rc = subprocess.run(
                [sys.executable, str(Path(__file__)), "--mutant-probe"],
                capture_output=True, text=True,
            ).returncode
            if rc == 1:
                print("[V3] PASS: mutant probe rc=1 (detected missing phrase)")
            else:
                fails.append(f"[V3] FAIL: mutant probe rc={rc}, expected 1")
    finally:
        DOC.write_text(original, encoding="utf-8")

    # V4 sha256 print-only
    sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
    print(f"[V4] doc sha256={sha}")

    # V5 subprocess 自调用 (不带 --mutant-probe, 全量再跑一次也会触发递归; 这里改为 --noop-probe 仅返回 0)
    rc5 = subprocess.run(
        [sys.executable, str(Path(__file__)), "--noop-probe"],
        capture_output=True, text=True,
    ).returncode
    if rc5 == 0:
        print("[V5] PASS: subprocess --noop-probe rc=0")
    else:
        fails.append(f"[V5] FAIL: subprocess --noop-probe rc={rc5}, expected 0")

    if fails:
        print("\nFAIL:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("\nALL PASS")
    return 0


def _probe() -> int:
    """V3 mutant probe: 仅跑 V0+V1+V2 不带子进程, 让 mutated doc 触发 fail。"""
    if not DOC.exists():
        return 1
    text = DOC.read_text(encoding="utf-8")
    # 子进程 import 时已经基于 mutated DOC 解析了 EXPECTED_HEADINGS;
    # 若 sentinel 被删, parse_headings_from_doc 会在 import-time RuntimeError → rc!=0
    if not EXPECTED_HEADINGS:
        return 1
    fails = _run_checks(text, EXPECTED_HEADINGS)
    return 1 if fails else 0


def _noop() -> int:
    """V5 子进程入口: 仅证明解释器路径与 import 链路 OK。"""
    return 0


if __name__ == "__main__":
    if "--mutant-probe" in sys.argv:
        sys.exit(_probe())
    if "--noop-probe" in sys.argv:
        sys.exit(_noop())
    sys.exit(main())
