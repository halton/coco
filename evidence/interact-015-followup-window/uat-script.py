"""interact-015 follow-up window — 真机 UAT 脚本.

跑法 (用户在真机环境执行)::

    /Users/halton/work/coco/.venv/bin/python evidence/interact-015-followup-window/uat-script.py

预期行为：
  1) 喊一次 "可可" 唤醒
  2) 等待 coco 回应 (TTS 说一句)
  3) reply 完后 15s 内说一句话 (不再喊 "可可")
     → 应正常触发 ASR + LLM + reply (说明 follow-up window 续上了)
  4) 再 reply 完后 15s 内再说一句话 (仍不喊 "可可")
     → 应继续触发 reply (说明每次 reply 都续窗)
  5) 沉默 ≥20s → 下次说话应不再触发 (回 sleeping)
     → 必须再喊一次 "可可" 才行

通过判据：
  - 步骤 (3)(4) 用户 utterance 都触发 reply（无需再 wake）
  - 步骤 (5) 沉默到时，下次 utterance 不触发，必须重新 wake
  - dashboard 上 timeline 显示 utterance 之间 awake 状态持续

可选环境变量：
  COCO_FOLLOWUP_WINDOW_S=20  # 调长 follow-up 窗口 (默认 15.0, 0=禁用, clamp [0,60])

本脚本只 print 提示, 不真跑链路 (链路在已运行的 coco 进程内)。
"""

from __future__ import annotations

import os
import sys


STEPS = [
    "[1] 喊一次 '可可' 唤醒",
    "[2] 等待 coco 回应 (TTS 应该说一句; 例如 '你好呀！很高兴见到你。')",
    "[3] reply 完后 ≤15s 内说话（不喊 wake）",
    "    预期: ASR+LLM+reply 触发，dashboard 显示 utterance",
    "[4] reply 完后 ≤15s 内再说一次（仍不喊 wake）",
    "    预期: 继续触发 reply（说明每次 reply 都续窗）",
    "[5] 沉默 ≥20s，然后再说话",
    "    预期: 不触发 reply（回 sleeping），需重新喊 '可可'",
]


PASS_CRITERIA = """
通过判据 (record_evidence_yourself):
  - 步骤 (3)(4): utterance 触发 reply 无需 wake → PASS
  - 步骤 (5): 沉默后 utterance 不触发，需重新 wake → PASS
  - dashboard timeline 显示 awake 持续到沉默到时

回填位置:
  evidence/interact-015-followup-window/uat-real-machine.md
  (执行结果 + 截屏 + dashboard 摘要)
"""


def main() -> int:
    print("=" * 60)
    print("interact-015 follow-up window — 真机 UAT 步骤")
    print("=" * 60)
    print(f"COCO_FOLLOWUP_WINDOW_S = {os.environ.get('COCO_FOLLOWUP_WINDOW_S', '15.0 (默认)')}")
    print()
    for s in STEPS:
        print(s)
    print()
    print(PASS_CRITERIA)
    print("注: 本脚本仅提示步骤; 真链路在已运行的 coco 进程内。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
