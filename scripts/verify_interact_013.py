"""interact-013 verification — MM proactive prompt 锁内 IO 拆分.

跑法::

    .venv/bin/python scripts/verify_interact_013.py

子项::

    V1  _collect_mm_prompt_snapshot_locked 方法存在
    V2  _render_mm_prompt_from_snapshot 方法存在 + 是纯函数（@staticmethod，
        源码不出现 self._lock / profile_store.load 之外的 IO）
    V3  MmPromptSnapshot 是 frozen dataclass（不可变）
    V4  maybe_trigger 锁内 collect、锁外 render：源码静态检查
        with self._lock: ... 块结束前不调 _render_mm_prompt_from_snapshot
    V5  渲染结果含 rule_id + caption + emotion + prefer + face_id 关键字段（行为）
    V6  回归：verify_interact_012 V1-V10 仍 PASS（python 调用 + exit code 0）
    V7  锁外 render 阶段不再触发 profile_store.load（mock 计数）：
        collect 阶段 ==0，render 阶段 ==1（profile_store 非 None 时）
    V8  AUTHORITATIVE_COMPONENTS 含 'mm_proactive_llm'（sanity）

evidence 落 evidence/interact-013/verify_summary.json
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import is_dataclass, fields
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_results: List[Dict[str, Any]] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(f"[verify_interact_013] {tag} {name}: {detail}", flush=True)


# ---------------------------------------------------------------------------

def _fresh_sched():
    from coco.proactive import ProactiveScheduler
    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._lock = threading.RLock()
    sched.profile_store = None
    sched._topic_preferences = {}
    sched._group_template_override = None
    sched._current_emotion_label = ""
    return sched


def v1_collect_exists():
    from coco.proactive import ProactiveScheduler
    fn = getattr(ProactiveScheduler, "_collect_mm_prompt_snapshot_locked", None)
    _check("V1 _collect_mm_prompt_snapshot_locked exists", callable(fn),
           detail=f"type={type(fn).__name__}")


def v2_render_pure():
    from coco.proactive import ProactiveScheduler
    fn = getattr(ProactiveScheduler, "_render_mm_prompt_from_snapshot", None)
    if not callable(fn):
        _check("V2 _render_mm_prompt_from_snapshot pure", False, "missing")
        return
    # 静态检查：取源码看是否有真正的 self._lock 调用（不计注释/docstring）
    src = inspect.getsource(fn)
    # 简单方式：去掉 # ... 行尾注释 与 docstring，再 grep self._lock
    import re as _re
    src_no_comments = "\n".join(
        _re.sub(r"#.*$", "", line) for line in src.splitlines()
    )
    # 去 docstring（第一段 """..."""）
    src_no_doc = _re.sub(r'"""[\s\S]*?"""', "", src_no_comments, count=1)
    no_self_lock = "self._lock" not in src_no_doc
    # 是否 @staticmethod —— 通过 __func__/__self__ 检查
    raw = inspect.getattr_static(ProactiveScheduler, "_render_mm_prompt_from_snapshot")
    is_static = isinstance(raw, staticmethod)
    _check("V2 _render_mm_prompt_from_snapshot pure",
           no_self_lock and is_static,
           detail=f"is_static={is_static} no_self_lock={no_self_lock}")


def v3_snapshot_frozen():
    from coco.proactive import MmPromptSnapshot
    ok_dc = is_dataclass(MmPromptSnapshot)
    frozen = bool(getattr(MmPromptSnapshot, "__dataclass_params__", None)
                  and MmPromptSnapshot.__dataclass_params__.frozen)
    names = {f.name for f in fields(MmPromptSnapshot)} if ok_dc else set()
    expected = {"profile_store", "topic_preferences", "group_template_override",
                "current_emotion_label", "rule_id", "caption", "hint",
                "face_ids", "ctx_emotion_label"}
    has_keys = expected.issubset(names)
    _check("V3 MmPromptSnapshot frozen dataclass",
           ok_dc and frozen and has_keys,
           detail=f"frozen={frozen} fields={sorted(names)}")


def v4_maybe_trigger_split():
    """源码静态：with self._lock: 块内不调 _render_mm_prompt_from_snapshot；
    必须调 _collect_mm_prompt_snapshot_locked。"""
    src = (ROOT / "coco" / "proactive.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # 找 maybe_trigger 函数体
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "ProactiveScheduler")
    fn = next(n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name == "maybe_trigger")

    # 收集所有 With 节点（lock 块），并在其内查找调用
    collect_in_lock = False
    render_in_lock = False
    render_outside = False
    collect_outside = False

    class V(ast.NodeVisitor):
        def __init__(self):
            self.depth = 0
        def visit_With(self, node):  # noqa: N802
            # 判断是 self._lock
            is_lock = False
            for item in node.items:
                ce = item.context_expr
                if isinstance(ce, ast.Attribute) and ce.attr == "_lock":
                    is_lock = True
            if is_lock:
                self.depth += 1
                for s in node.body:
                    self.visit(s)
                self.depth -= 1
            else:
                self.generic_visit(node)
        def visit_Call(self, node):  # noqa: N802
            nonlocal collect_in_lock, render_in_lock, render_outside, collect_outside
            name = None
            f = node.func
            if isinstance(f, ast.Attribute):
                name = f.attr
            if name == "_collect_mm_prompt_snapshot_locked":
                if self.depth > 0:
                    collect_in_lock = True
                else:
                    collect_outside = True
            if name == "_render_mm_prompt_from_snapshot":
                if self.depth > 0:
                    render_in_lock = True
                else:
                    render_outside = True
            self.generic_visit(node)

    V().visit(fn)
    ok = collect_in_lock and render_outside and (not render_in_lock)
    _check("V4 maybe_trigger collect-in-lock / render-out-of-lock", ok,
           detail=f"collect_in_lock={collect_in_lock} render_outside={render_outside} "
                  f"render_in_lock={render_in_lock} collect_outside={collect_outside}")


def v5_render_equivalence():
    from coco.proactive import ProactiveScheduler
    sched = _fresh_sched()
    sched._topic_preferences = {"music": 5, "football": 3}
    sched._current_emotion_label = "happy"
    ctx = {
        "rule_id": "dark_silence",
        "caption": "有人在窗边",
        "hint": "问问他在读什么书",
        "face_ids": ["u1"],
        "emotion_label": "sad",  # 应优先 ctx
    }
    with sched._lock:
        snap = sched._collect_mm_prompt_snapshot_locked(ctx)
    out = ProactiveScheduler._render_mm_prompt_from_snapshot(snap) or ""
    must_have = ["dark_silence", "有人在窗边", "sad", "music", "u1",
                 "[MM 主动话题上下文]"]
    missing = [m for m in must_have if m not in out]
    _check("V5 render contains rule/caption/emotion/prefer/face_id",
           not missing, detail=f"missing={missing} len={len(out)}")


def v6_regression_verify_012():
    """跑 verify_interact_012 子进程；exit 0 = PASS。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    res = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "python"),
         str(ROOT / "scripts" / "verify_interact_012.py")],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    ok = res.returncode == 0
    tail = (res.stdout or "").splitlines()[-3:]
    _check("V6 verify_interact_012 regression",
           ok, detail=f"rc={res.returncode} tail={tail}")


def v7_render_no_profile_load_count():
    """mock profile_store；collect 阶段 load 调用计数应为 0，render 阶段应 ==1。"""
    from coco.proactive import ProactiveScheduler

    class FakeProfileStore:
        def __init__(self):
            self.load_calls = 0
        def load(self):
            self.load_calls += 1
            from coco.profile import UserProfile
            return UserProfile()

    sched = _fresh_sched()
    fps = FakeProfileStore()
    sched.profile_store = fps

    ctx = {"rule_id": "motion_greet", "caption": "走动", "hint": "打招呼",
           "face_ids": ["u2"], "emotion_label": "calm"}
    with sched._lock:
        snap = sched._collect_mm_prompt_snapshot_locked(ctx)
    collect_calls = fps.load_calls
    _ = ProactiveScheduler._render_mm_prompt_from_snapshot(snap)
    render_delta = fps.load_calls - collect_calls
    ok = (collect_calls == 0) and (render_delta == 1)
    _check("V7 profile_store.load only at render",
           ok, detail=f"collect_calls={collect_calls} render_delta={render_delta}")


def v8_authoritative_components():
    """AUTHORITATIVE_COMPONENTS 定义在 coco.logging_setup。"""
    try:
        from coco.logging_setup import AUTHORITATIVE_COMPONENTS as comps
        ok = "mm_proactive_llm" in comps
        _check("V8 AUTHORITATIVE_COMPONENTS mm_proactive_llm",
               ok, detail=f"present={ok} total={len(comps)}")
    except Exception as e:  # noqa: BLE001
        _check("V8 AUTHORITATIVE_COMPONENTS mm_proactive_llm",
               False, detail=f"import failed: {e}")


# ---------------------------------------------------------------------------

def main():
    funcs = [
        v1_collect_exists, v2_render_pure, v3_snapshot_frozen,
        v4_maybe_trigger_split, v5_render_equivalence,
        v6_regression_verify_012, v7_render_no_profile_load_count,
        v8_authoritative_components,
    ]
    for f in funcs:
        try:
            f()
        except Exception as e:  # noqa: BLE001
            _check(f.__name__, False, f"EXCEPTION {type(e).__name__}: {e}")

    total = len(_results)
    passed = sum(1 for r in _results if r["ok"])
    print(f"\n[verify_interact_013] SUMMARY {passed}/{total} PASS", flush=True)

    out_dir = ROOT / "evidence" / "interact-013"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "ts": time.time(),
        "total": total,
        "passed": passed,
        "items": _results,
    }
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
