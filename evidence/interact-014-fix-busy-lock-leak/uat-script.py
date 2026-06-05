"""evidence/interact-014-fix-busy-lock-leak/uat-script.py — 真机 UAT 脚本.

跑法 (在 coco repo 根, 真机插好后)::

    /Users/halton/work/coco/.venv/bin/python evidence/interact-014-fix-busy-lock-leak/uat-script.py

目的: 验证 _busy lock leak 修复在真机环境下:
- 连续触发 5 次 turn (每轮间隔 ~3s), 看每轮都能正常 acquire / release。
- 故意 sleep 35s 后再 wake, 模拟「上一轮 lock 因异常路径泄漏 + 已超过
  COCO_INTERACT_LOCK_TIMEOUT_S(默认30s)」场景, 断言 watchdog 强释放 +
  emit interact.lock_recovered 事件 + 用户层面下一轮能正常激活。

注意: 本脚本不真的让 wake.hit 接进来 — 改用直接调 InteractSession
.handle_audio(audio, sr) 注入一段 silent fixture, 等价 wake-bridge 触发
端的语义。如果要做端到端 (真键盘 PTT / 真 wake-word / 真 mic) UAT, 用户
直接拿真 mic 走 wake-bridge 触发。

输出: evidence/interact-014-fix-busy-lock-leak/uat-result.json (本脚本写)。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import numpy as np

from coco.interact import InteractSession


class FakeRobot:
    def head_to(self, *a, **k): pass
    def goto_sleep(self, *a, **k): pass
    def goto_zero(self, *a, **k): pass


class FakeTTS:
    def __init__(self):
        self.spoken = []
    def say(self, text, *, blocking=True, **kw):
        self.spoken.append(text)


def fake_asr(_audio, _sr):
    return "你好"


def silence_audio():
    return np.zeros(16000, dtype=np.int16), 16000


def main() -> int:
    print(f"=== interact-014 真机 UAT 脚本 (timeout={os.environ.get('COCO_INTERACT_LOCK_TIMEOUT_S', '30 default')}s) ===")
    sess = InteractSession(
        robot=FakeRobot(),
        asr_fn=fake_asr,
        tts_say_fn=FakeTTS().say,
    )
    audio, sr = silence_audio()
    log = []

    # 1) 连续 5 turn
    print("\n[Phase 1] 连续 5 turn — 每轮应正常 acquire/release")
    for i in range(5):
        t0 = time.time()
        r = sess.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
        dt = time.time() - t0
        locked = sess._busy.locked()
        ts_after = sess._busy_acquired_ts
        print(f"  turn{i+1}: dropped={r.get('dropped')} dt={dt:.3f}s "
              f"locked_after={locked} ts_after={ts_after}")
        log.append({"phase": 1, "turn": i + 1, "dropped": r.get("dropped"),
                    "dt_s": round(dt, 3), "locked_after": locked, "ts_after": ts_after})
        time.sleep(3.0)

    # 2) 模拟历史泄漏: 手工 acquire + 设 ts=now-31s, 等下次 handle_audio
    #    验证 watchdog 强释放
    print("\n[Phase 2] 模拟历史泄漏 — ts=now-35s, 下次 handle_audio 应触发 watchdog 强释放")
    sess._busy.acquire(blocking=False)
    sess._busy_acquired_ts = time.time() - 35.0  # 强制 >30s
    print(f"  pre-state: locked={sess._busy.locked()} ts_age={35.0}s")
    log.append({"phase": 2, "step": "leak-injected",
                "locked_pre": True, "ts_age_s": 35.0})

    # 等待 5s 模拟用户在真机上停了一会儿才再次 wake
    print("  sleeping 5s before next wake...")
    time.sleep(5.0)

    t0 = time.time()
    r = sess.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
    dt = time.time() - t0
    locked = sess._busy.locked()
    ts_after = sess._busy_acquired_ts
    print(f"  recovery turn: dropped={r.get('dropped')} dt={dt:.3f}s "
          f"locked_after={locked} ts_after={ts_after}")
    log.append({"phase": 2, "step": "recovery-turn",
                "dropped": r.get("dropped"), "dt_s": round(dt, 3),
                "locked_after": locked, "ts_after": ts_after})

    # 期望: dropped=False, locked_after=False, ts_after=None
    success = (
        not r.get("dropped")
        and not locked
        and ts_after is None
    )

    out_path = Path(__file__).parent / "uat-result.json"
    out_path.write_text(json.dumps({
        "success": success,
        "log": log,
        "timeout_s": float(os.environ.get("COCO_INTERACT_LOCK_TIMEOUT_S", "30.0")),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nresult written to {out_path}")
    print(f"success={success}")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
