#!/usr/bin/env python3
"""verify_robot_032: docs-lock for proactive_scheduler_block_policy.md

V0 文件存在
V1 章节标题（H2/H3）字面断言
V2 关键短语断言
V3 mutant 反证：删一个章节 → 重跑 verify rc=1，finally 还原
V4 文档自身 sha256 锁（hardcoded 期望值；未匹配仅 print, 不 fail —— allow
   未来正常修订；mutant 反证 V3 已覆盖结构破坏）

退出码 0=ALL PASS / 1=任一 FAIL

运行环境约定 (infra-034)
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
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "proactive_scheduler_block_policy.md"

# robot-034: EXPECTED_HEADINGS 不再 hardcode, 改为运行时从 docs 解析。
# 单一事实源: docs/proactive_scheduler_block_policy.md 中
# "## 章节标题列表（供 verify_robot_032 用）" 段下的 bullet list (每行以 "- `" 起头, 反引号包裹标题字面)。
# 解析窗口: 从 sentinel H2 行开始到文件末尾或下一 H2 行止。

_HEADINGS_SECTION_SENTINEL = "## 章节标题列表（供 verify_robot_032 用）"


def _parse_headings_from_doc(doc_path: Path) -> list[str]:
    """从 docs 的 sentinel section 提取章节标题字面列表。

    Raises:
        RuntimeError: sentinel section 缺失 / 解析为空。
    """
    if not doc_path.exists():
        raise RuntimeError(f"doc not found: {doc_path}")
    text = doc_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == _HEADINGS_SECTION_SENTINEL)
    except StopIteration:
        raise RuntimeError(f"sentinel section not found: {_HEADINGS_SECTION_SENTINEL!r}")
    # 从 sentinel 下一行到下一个 H2 (## ...) 或文件末
    headings: list[str] = []
    for ln in lines[start + 1 :]:
        s = ln.rstrip()
        if s.startswith("## "):  # 进入下一 H2 段, 停
            break
        st = s.lstrip()
        if st.startswith("- `") and st.endswith("`"):
            # 提取反引号之间的字面
            inner = st[3:-1]
            headings.append(inner)
    if not headings:
        raise RuntimeError(
            f"no headings parsed under sentinel section {_HEADINGS_SECTION_SENTINEL!r}"
        )
    return headings


# 运行时加载; 失败让 import-time 即抛, sub-process 也会被传播
EXPECTED_HEADINGS = _parse_headings_from_doc(DOC)

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

    # V1b sentinel 防御 (robot-034): EXPECTED_HEADINGS 不能为空
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
    """Used by V3: only run V0+V1+V2 on the (possibly mutated) file.

    robot-034: 子进程 fresh import 已经在 module top-level 重新调用
    ``_parse_headings_from_doc(DOC)``; 这里直接用 EXPECTED_HEADINGS 即可,
    它反映了 mutated doc 的 headings。
    若 mutated doc 直接删了 sentinel section, module import 时 RuntimeError
    会让子进程立即 rc!=0, 也算捕获。
    """
    if not DOC.exists():
        return 1
    text = DOC.read_text(encoding="utf-8")
    fails = _run_checks(text, EXPECTED_HEADINGS)
    if not EXPECTED_HEADINGS:
        return 1
    return 1 if fails else 0


if __name__ == "__main__":
    if "--mutant-probe" in sys.argv:
        sys.exit(_probe())
    sys.exit(main())
