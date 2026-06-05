"""scripts/verify_interact_015_followup_window.py — follow-up window 验证.

V0  fingerprint sha256 锁面 coco/interact.py + coco/wake_word.py + coco/main.py
V1  InteractSession.__init__ 接 ``wake_gate`` kwarg + ``set_wake_gate`` setter
V2  env ``COCO_FOLLOWUP_WINDOW_S`` 默认 15.0；int/float/error 兜底
V3  env=0 → 禁用 follow-up (handle_audio reply 完不调 trigger/extend)
V4  env=20 → reply 完调 ``wake_gate.extend(20.0)``
V5  WakeGate 实例集成：handle_audio 一轮后 ``extend`` 被调；窗口被续到
V6  ``wake_gate=None`` → handle_audio 不抛（向后兼容）
V7  coco/main.py 含 ``set_wake_gate`` wire；WakeGate 含 ``extend`` method

跑法：``./.venv/bin/python scripts/verify_interact_015_followup_window.py``
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 清掉 env 避免泄漏
os.environ.pop("COCO_FOLLOWUP_WINDOW_S", None)

import numpy as np  # noqa: E402


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class FakeRobot:
    def head_to(self, *a, **k): pass
    def goto_sleep(self, *a, **k): pass
    def goto_zero(self, *a, **k): pass


class FakeTTS:
    def __init__(self) -> None:
        self.spoken: List[str] = []

    def say(self, text: str, *, blocking: bool = True, **kw) -> None:
        self.spoken.append(text)


class MockWakeGate:
    """模拟 WakeGate；记录 extend/trigger 调用次数 + 参数。"""

    def __init__(self, *, supports_extend: bool = True) -> None:
        self.extend_calls: List[float] = []
        self.trigger_calls: int = 0
        self._supports_extend = supports_extend

    def trigger(self) -> None:
        self.trigger_calls += 1

    if True:  # 默认有 extend
        def extend(self, seconds: float) -> None:
            self.extend_calls.append(float(seconds))


class MockWakeGateNoExtend:
    """模拟无 extend 的旧 WakeGate；只支持 trigger。"""

    def __init__(self) -> None:
        self.trigger_calls: int = 0

    def trigger(self) -> None:
        self.trigger_calls += 1


def _fake_asr(_audio: np.ndarray, _sr: int) -> str:
    return "你好"


def _silence_audio() -> tuple[np.ndarray, int]:
    return np.zeros(16000, dtype=np.int16), 16000


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}: {detail}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _make_session(*, wake_gate: Any = None):
    """构造最小可跑的 InteractSession（已注入 wake_gate）。"""
    from coco.interact import InteractSession
    return InteractSession(
        robot=FakeRobot(),
        asr_fn=_fake_asr,
        tts_say_fn=FakeTTS().say,
        wake_gate=wake_gate,
    )


# ---------------------------------------------------------------------------
# V0 fingerprint sha256
# ---------------------------------------------------------------------------


def v0_fingerprint() -> None:
    targets = [
        ROOT / "coco" / "interact.py",
        ROOT / "coco" / "wake_word.py",
        ROOT / "coco" / "main.py",
    ]
    parts: List[str] = []
    all_exist = True
    for p in targets:
        if not p.exists():
            _record("V0_fingerprint_sha256", False, f"missing {p}")
            all_exist = False
            return
        parts.append(f"{p.name}={_sha256(p)[:10]}")
    # 关键锚点
    interact_src = (ROOT / "coco" / "interact.py").read_text(encoding="utf-8")
    wake_src = (ROOT / "coco" / "wake_word.py").read_text(encoding="utf-8")
    main_src = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    anchors = {
        "interact.py:wake_gate kwarg": "wake_gate: Optional[Any] = None" in interact_src,
        "interact.py:_followup_window_s": "_followup_window_s" in interact_src,
        "interact.py:_resolve_followup_window_s": "_resolve_followup_window_s" in interact_src,
        "interact.py:set_wake_gate": "def set_wake_gate" in interact_src,
        "interact.py:env name": "COCO_FOLLOWUP_WINDOW_S" in interact_src,
        "interact.py:extend hook": (
            'getattr(self._wake_gate, "extend"' in interact_src
            and "_followup_window_s" in interact_src
        ),
        "wake_word.py:def extend": "def extend(" in wake_src,
        "main.py:set_wake_gate wire": "set_wake_gate(wake_gate)" in main_src,
    }
    missing = [k for k, v in anchors.items() if not v]
    ok = all_exist and not missing
    _record("V0_fingerprint_sha256", ok, f"{', '.join(parts)} anchors_missing={missing}")


# ---------------------------------------------------------------------------
# V1 kwarg + setter
# ---------------------------------------------------------------------------


def v1_kwarg_and_setter() -> None:
    try:
        import inspect
        from coco.interact import InteractSession
        sig = inspect.signature(InteractSession.__init__)
        has_kwarg = "wake_gate" in sig.parameters
        param = sig.parameters.get("wake_gate")
        default_ok = param is not None and param.default is None
        has_setter = hasattr(InteractSession, "set_wake_gate") and callable(InteractSession.set_wake_gate)
        # 实例化验证
        gate = MockWakeGate()
        s = _make_session(wake_gate=gate)
        attr_wired = getattr(s, "_wake_gate", None) is gate
        # setter
        s2 = _make_session(wake_gate=None)
        s2.set_wake_gate(gate)
        setter_wired = getattr(s2, "_wake_gate", None) is gate
        ok = has_kwarg and default_ok and has_setter and attr_wired and setter_wired
        _record(
            "V1_kwarg_and_setter", ok,
            f"kwarg={has_kwarg} default_none={default_ok} setter={has_setter} "
            f"attr_wired={attr_wired} setter_wired={setter_wired}",
        )
    except Exception as e:  # noqa: BLE001
        _record("V1_kwarg_and_setter", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V2 env parsing
# ---------------------------------------------------------------------------


def v2_env_parsing() -> None:
    from coco.interact import InteractSession
    cases = [
        ("", 15.0),         # 默认
        ("0", 0.0),
        ("0.0", 0.0),
        ("20", 20.0),
        ("3.5", 3.5),
        ("60", 60.0),
        ("90", 60.0),        # clamp 上界
        ("-5", 0.0),         # clamp 下界
        ("abc", 15.0),       # 解析失败兜底
        ("  ", 15.0),        # 空字符串
    ]
    failures: List[str] = []
    for raw, expected in cases:
        if raw == "":
            os.environ.pop("COCO_FOLLOWUP_WINDOW_S", None)
        else:
            os.environ["COCO_FOLLOWUP_WINDOW_S"] = raw
        got = InteractSession._resolve_followup_window_s()
        if abs(got - expected) > 1e-6:
            failures.append(f"raw={raw!r} expected={expected} got={got}")
    os.environ.pop("COCO_FOLLOWUP_WINDOW_S", None)
    ok = not failures
    _record("V2_env_parsing", ok, f"failures={failures}" if failures else f"{len(cases)} cases ok")


# ---------------------------------------------------------------------------
# V3 env=0 → no extend call
# ---------------------------------------------------------------------------


def v3_env_zero_disables() -> None:
    os.environ["COCO_FOLLOWUP_WINDOW_S"] = "0"
    try:
        gate = MockWakeGate()
        s = _make_session(wake_gate=gate)
        # session 已在 __init__ 里读 env；确认是 0
        if s._followup_window_s != 0.0:
            _record("V3_env_zero_disables", False, f"_followup_window_s={s._followup_window_s} (expected 0)")
            return
        audio, sr = _silence_audio()
        res = s.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
        # res["reply"] 可能非空（fake_asr 返回 "你好" → KEYWORD_ROUTES 命中）
        # 但因 _followup_window_s == 0 → finally 不调 extend
        ok = len(gate.extend_calls) == 0 and gate.trigger_calls == 0
        _record(
            "V3_env_zero_disables", ok,
            f"extend_calls={gate.extend_calls} trigger_calls={gate.trigger_calls} "
            f"reply={res.get('reply')!r}",
        )
    finally:
        os.environ.pop("COCO_FOLLOWUP_WINDOW_S", None)


# ---------------------------------------------------------------------------
# V4 env=20 → extend(20.0) called
# ---------------------------------------------------------------------------


def v4_env_20_extends() -> None:
    os.environ["COCO_FOLLOWUP_WINDOW_S"] = "20"
    try:
        gate = MockWakeGate()
        s = _make_session(wake_gate=gate)
        if s._followup_window_s != 20.0:
            _record("V4_env_20_extends", False, f"_followup_window_s={s._followup_window_s} (expected 20)")
            return
        audio, sr = _silence_audio()
        res = s.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
        if not res.get("reply"):
            _record("V4_env_20_extends", False, f"reply empty: {res}")
            return
        ok = (len(gate.extend_calls) == 1 and gate.extend_calls[0] == 20.0
              and gate.trigger_calls == 0)
        _record(
            "V4_env_20_extends", ok,
            f"extend_calls={gate.extend_calls} trigger_calls={gate.trigger_calls}",
        )
    finally:
        os.environ.pop("COCO_FOLLOWUP_WINDOW_S", None)


# ---------------------------------------------------------------------------
# V5 真实 WakeGate 集成 — handle_audio 一轮后 window 被续到
# ---------------------------------------------------------------------------


def v5_real_wakegate_integration() -> None:
    os.environ["COCO_FOLLOWUP_WINDOW_S"] = "5"
    try:
        from coco.wake_word import WakeGate
        gate = WakeGate(window_seconds=1.0)  # 默认窗 1s（短）
        s = _make_session(wake_gate=gate)
        # extend 存在
        has_extend = hasattr(gate, "extend") and callable(gate.extend)
        if not has_extend:
            _record("V5_real_wakegate_integration", False, "WakeGate.extend missing")
            return
        # 初始：sleeping
        if gate.is_awake():
            _record("V5_real_wakegate_integration", False, "WakeGate 应初始 sleeping 但 is_awake=True")
            return
        # 跑一轮 handle_audio
        audio, sr = _silence_audio()
        res = s.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
        if not res.get("reply"):
            _record("V5_real_wakegate_integration", False, f"reply empty: {res}")
            return
        # finally 应已 extend(5.0) → 现在窗口剩余 ~5s（> 默认 window_seconds=1.0）
        rem = gate.remaining_seconds()
        ok = gate.is_awake() and 3.0 < rem <= 5.0  # 给点 slack（asr/tts 实际耗时也 < 1s）
        _record(
            "V5_real_wakegate_integration", ok,
            f"is_awake={gate.is_awake()} remaining={rem:.2f}s (expect ~5s)",
        )
    finally:
        os.environ.pop("COCO_FOLLOWUP_WINDOW_S", None)


# ---------------------------------------------------------------------------
# V6 wake_gate=None backward compat
# ---------------------------------------------------------------------------


def v6_none_backward_compat() -> None:
    s = _make_session(wake_gate=None)
    audio, sr = _silence_audio()
    try:
        res = s.handle_audio(audio, sr, skip_action=True, skip_tts_play=True)
        ok = bool(res.get("reply"))
        _record("V6_none_backward_compat", ok, f"handle_audio ok, reply={res.get('reply')!r}")
    except Exception as e:  # noqa: BLE001
        _record("V6_none_backward_compat", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V7 main.py wire + WakeGate.extend
# ---------------------------------------------------------------------------


def v7_main_wire_and_wakegate_extend() -> None:
    main_src = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    wake_src = (ROOT / "coco" / "wake_word.py").read_text(encoding="utf-8")
    main_wire = "set_wake_gate(wake_gate)" in main_src
    wake_extend = "def extend(" in wake_src and "self._awake_until" in wake_src
    # 运行时校验 WakeGate.extend 行为
    try:
        from coco.wake_word import WakeGate
        g = WakeGate(window_seconds=0.5)
        g.extend(3.0)
        rem_after = g.remaining_seconds()
        runtime_ok = g.is_awake() and 2.0 < rem_after <= 3.0
        # extend(0) no-op
        g.reset()
        g.extend(0)
        zero_noop = not g.is_awake()
        # extend(-5) no-op
        g.extend(-5)
        neg_noop = not g.is_awake()
    except Exception as e:  # noqa: BLE001
        _record("V7_main_wire_and_wakegate_extend", False, f"WakeGate.extend runtime err: {e}")
        return
    ok = main_wire and wake_extend and runtime_ok and zero_noop and neg_noop
    _record(
        "V7_main_wire_and_wakegate_extend", ok,
        f"main_wire={main_wire} wake_extend_def={wake_extend} "
        f"runtime_ok={runtime_ok} zero_noop={zero_noop} neg_noop={neg_noop}",
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    v0_fingerprint()
    v1_kwarg_and_setter()
    v2_env_parsing()
    v3_env_zero_disables()
    v4_env_20_extends()
    v5_real_wakegate_integration()
    v6_none_backward_compat()
    v7_main_wire_and_wakegate_extend()
    print("\n" + "=" * 60)
    fail = [r for r in _results if not r["ok"]]
    print(f"Summary: {len(_results) - len(fail)}/{len(_results)} PASS")
    for r in fail:
        print(f"  - FAIL {r['name']}: {r['detail']}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
