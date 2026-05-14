#!/usr/bin/env python3
"""verify_companion_011: group_mode multi-user. V1-V10 sanity tier."""
from __future__ import annotations
import os, sys

FAILURES: list[str] = []
def _check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)

def v1():
    os.environ.pop("COCO_MULTI_USER", None)
    from coco.companion import group_mode as gm
    _check("V1 GroupModeCoordinator import", hasattr(gm, "GroupModeCoordinator"))

def v2():
    from coco.logging_setup import AUTHORITATIVE_COMPONENTS
    _check("V2 AUTH contains group_mode", "group_mode" in AUTHORITATIVE_COMPONENTS)

def v3():
    from coco.companion.group_mode import GroupModeCoordinator
    _check("V3 GroupModeCoordinator class", GroupModeCoordinator is not None)

def v4():
    from coco.companion.group_mode import GroupModeCoordinator
    coord = GroupModeCoordinator()
    _check("V4 coord has entry method",
           any(hasattr(coord, m) for m in ("on_faces","update","on_face_event","update_faces","tick","observe")))

def v5():
    from coco.companion.group_mode import GroupModeCoordinator
    _check("V5 class present", GroupModeCoordinator is not None)

def v6():
    from coco.companion.group_mode import GroupModeCoordinator
    _check("V6 class present", GroupModeCoordinator is not None)

def v7():
    from coco.proactive import ProactiveStats
    s = ProactiveStats()
    _check("V7a ProactiveStats.group_mode_trigger_count", hasattr(s, "group_mode_trigger_count"))
    _check("V7b ProactiveStats.group_mode_active_total", hasattr(s, "group_mode_active_total"))

def v8():
    from coco.companion import profile_persist
    src = open(profile_persist.__file__).read()
    _check("V8 profile_persist has group_sessions", "group_sessions" in src)

def v9():
    from coco import proactive
    src = open(proactive.__file__).read()
    _check("V9 proactive.py references group_mode", "group_mode" in src)

def v10():
    from coco.logging_setup import AUTHORITATIVE_COMPONENTS
    _check("V10 emit channel group_mode whitelisted", "group_mode" in AUTHORITATIVE_COMPONENTS)

def v11():
    """V11 behavioral: 构造 coord + fake persist + resolver，喂 5 帧 known，
    断言 stats.observe_count 累计 + (enter 或 enter_candidate_since 触发)。"""
    from types import SimpleNamespace
    from coco.companion.group_mode import GroupModeCoordinator

    class _FakePersist:
        def __init__(self):
            self.saved = []
            self._recs = {}
        def load(self, pid):
            return self._recs.get(pid)
        def save(self, rec):
            self.saved.append(rec)
            self._recs[rec.profile_id] = rec

    def _resolver(name):
        # 12 hex 假 pid（与 is_valid_profile_id 兼容）
        import hashlib
        return hashlib.sha1(name.encode()).hexdigest()[:12]

    persist = _FakePersist()
    coord = GroupModeCoordinator(
        proactive_scheduler=None,
        persist_store=persist,
        profile_id_resolver=_resolver,
        enter_hold_s=0.0,  # 立即进入
        exit_hold_s=0.0,
    )

    def _snap(*names):
        return SimpleNamespace(tracks=[SimpleNamespace(name=n) for n in names])

    # 模拟 5 帧：2 known names 持续 → enter
    t = 0.0
    for i in range(5):
        coord.observe(_snap("alice", "bob"), now=t)
        t += 1.0
        coord.tick(now=t)

    _check("V11a observe_count >= 5", coord.stats.observe_count >= 5,
           f"got {coord.stats.observe_count}")
    _check("V11b coord entered group (in_group True or enter_count>=1)",
           coord.is_active() or coord.stats.enter_count >= 1,
           f"in_group={coord.is_active()} enter_count={coord.stats.enter_count}")

def v12():
    """V12 wire grep: main.py 必须含 GroupModeCoordinator 构造点。"""
    src = open("coco/main.py", encoding="utf-8").read()
    _check("V12a main.py imports GroupModeCoordinator",
           "GroupModeCoordinator" in src)
    _check("V12b main.py wires _group_mode_ref",
           "_group_mode_ref" in src and "coord.observe" not in src or "_gmc.observe" in src,
           "expect attention loop calls _gmc.observe")
    _check("V12c main.py logs 'group_mode wired'",
           "group_mode wired" in src)

def main():
    for fn in (v1,v2,v3,v4,v5,v6,v7,v8,v9,v10,v11,v12):
        try: fn()
        except Exception as e:
            _check(fn.__name__, False, f"exception: {e!r}")
    # 动态计算 total（每个 v_ 函数可能跑多个 _check）
    # 用一个简单计数：通过 = len(printed PASS lines)。这里直接报 FAIL 数 + 名称。
    print(f"\nverify_companion_011: FAIL={len(FAILURES)} failures={FAILURES}")
    return 1 if FAILURES else 0

if __name__ == "__main__":
    sys.exit(main())
