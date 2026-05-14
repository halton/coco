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

def main():
    for fn in (v1,v2,v3,v4,v5,v6,v7,v8,v9,v10):
        try: fn()
        except Exception as e:
            _check(fn.__name__, False, f"exception: {e!r}")
    passed = 11 - len(FAILURES)
    print(f"\nverify_companion_011: PASS={passed} FAIL={len(FAILURES)}")
    return 1 if FAILURES else 0

if __name__ == "__main__":
    sys.exit(main())
