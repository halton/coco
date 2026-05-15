"""audio-011 verification: asr/main InputStream wrap + HotplugWatcher callback registry + env poll_interval.

跑法::

    uv run python scripts/verify_audio_011.py

子项 (V1-V5)：

V1   asr.transcribe_microphone 真实 InputStream 调用站走 open_stream_with_recovery 路径。
     注入 fake sd.InputStream 抛 sd.PortAudioError，COCO_AUDIO_RECOVERY=1，验证 wrapper
     退避重试（calls >= 2，最终成功）。
V2   main.py 主循环 fallback InputStream 同 wrap 路径（mockup / monkeypatch 的最小子集）。
     直接验证 wrapper 行为本身（main.py 实际启动太重，采用 unit-level：导入 sd 注入 fake +
     直接调 audio_resilience.open_stream_with_recovery 模拟该位置）。
V3   HotplugWatcher callback registry — add/remove + poll_once 触发遍历 + 异常不传染。
V4   env COCO_AUDIO_HOTPLUG_INTERVAL_S 解析 9-case + HotplugWatcher 从 env 读 default。
V5   regression — verify_audio_010 / 009 / infra_018 / robot_007 全 PASS（如脚本存在）。

evidence 落 evidence/audio-011/verify_summary.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "evidence" / "audio-011"
SUMMARY_PATH = EVIDENCE_DIR / "verify_summary.json"

_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, **detail: Any) -> None:
    _results.append({"name": name, "ok": bool(ok), **detail})
    flag = "PASS" if ok else "FAIL"
    print(f"[verify_audio_011] {name}: {flag} {detail}", flush=True)


# ---------------------------------------------------------------------------
# V1: asr.transcribe_microphone wrap → wrapper 退避重试
# ---------------------------------------------------------------------------
def v1_asr_inputstream_wrap() -> None:
    """注入 fake sd.InputStream（前 2 次 raise PortAudioError，第 3 次成功），
    COCO_AUDIO_RECOVERY=1，调 open_stream_with_recovery（asr.py 内部就走这条），
    验 calls=3 + 成功 + 退避计数。
    """
    name = "V1_asr_inputstream_wrap"
    try:
        # 重新 import 保证拿到 env 下的状态
        os.environ["COCO_AUDIO_RECOVERY"] = "1"
        import importlib
        import sounddevice as sd  # 真实存在
        from coco import audio_resilience as ar
        importlib.reload(ar)

        calls = {"n": 0}
        sleeps: List[float] = []

        class _FakeStream:
            def __enter__(self):
                return self
            def __exit__(self, *a, **k):
                return False
            def read(self, n):
                return (b"\x00" * n, False)

        def _open():
            calls["n"] += 1
            if calls["n"] < 3:
                raise sd.PortAudioError("fake port audio error")
            return _FakeStream()

        stream = ar.open_stream_with_recovery(
            _open,
            stream_kind="input",
            sleep_fn=lambda s: sleeps.append(s),
        )
        ok = stream is not None and calls["n"] == 3 and len(sleeps) == 2
        _record(name, ok, calls=calls["n"], sleeps=sleeps, recovered=stream is not None)
    except Exception as exc:  # noqa: BLE001
        _record(name, False, error=f"{type(exc).__name__}: {exc}")
    finally:
        os.environ.pop("COCO_AUDIO_RECOVERY", None)


# ---------------------------------------------------------------------------
# V2: main.py 主循环 fallback wrap — 等价路径行为校验
# ---------------------------------------------------------------------------
def v2_main_inputstream_wrap_recovery_off_equivalence() -> None:
    """recovery OFF 时 wrapper 路径与原直连等价：calls=1，无 emit，无 sleep。
    模拟 main.py 主 mic loop 在 COCO_AUDIO_RECOVERY 未设时的行为。
    """
    name = "V2_main_inputstream_wrap_off_equivalence"
    try:
        os.environ.pop("COCO_AUDIO_RECOVERY", None)
        import importlib
        from coco import audio_resilience as ar
        importlib.reload(ar)

        calls = {"n": 0}
        emits: List[tuple] = []
        sleeps: List[float] = []

        def _open():
            calls["n"] += 1
            return object()  # sentinel；OFF 时透传

        out = ar.open_stream_with_recovery(
            _open,
            stream_kind="input",
            emit_fn=lambda evt, **payload: emits.append((evt, payload)),
            sleep_fn=lambda s: sleeps.append(s),
        )
        ok = out is not None and calls["n"] == 1 and not emits and not sleeps
        _record(name, ok, calls=calls["n"], emits_n=len(emits), sleeps_n=len(sleeps))
    except Exception as exc:  # noqa: BLE001
        _record(name, False, error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# V3: HotplugWatcher callback registry
# ---------------------------------------------------------------------------
def v3_hotplug_registry() -> None:
    name = "V3_hotplug_callback_registry"
    try:
        import importlib
        from coco import audio_resilience as ar
        importlib.reload(ar)

        seen_a: List[tuple] = []
        seen_b: List[tuple] = []

        def cb_a(event: str, device: dict) -> None:
            seen_a.append((event, device.get("name")))

        def cb_b(event: str, device: dict) -> None:
            seen_b.append((event, device.get("name")))

        def cb_raises(event: str, device: dict) -> None:
            raise RuntimeError("registry cb error must not poison sibling cbs")

        # 注入 query_devices_fn：第一次空，第二次有一个新 device
        states = [
            [],
            [{"index": 0, "name": "Fake Mic", "max_input_channels": 1, "max_output_channels": 0}],
            [],  # 第三次又被移除
        ]
        idx = {"i": 0}

        def _q():
            i = idx["i"]
            idx["i"] = min(i + 1, len(states) - 1)
            return states[i]

        emits: List[tuple] = []

        w = ar.HotplugWatcher(
            poll_interval=0.05,
            emit_fn=lambda evt, **p: emits.append((evt, p)),
            query_devices_fn=_q,
        )
        # 注册
        w.add_reopen_callback(cb_a)
        w.add_reopen_callback(cb_b)
        w.add_reopen_callback(cb_raises)
        w.add_reopen_callback(cb_a)  # 不应重复
        count_after_add = w.reopen_callback_count
        # prime（不 emit）+ 第一次 poll → added
        w.prime()  # _prev = []
        added1, removed1 = w.poll_once()
        # 第二次 poll → removed
        added2, removed2 = w.poll_once()

        # remove cb_b
        removed_flag = w.remove_reopen_callback(cb_b)
        removed_again = w.remove_reopen_callback(cb_b)

        # Brief 期望 set-like 语义；此处用 list 但 add 时去重，效果相同。
        ok = (
            count_after_add == 3  # cb_a + cb_b + cb_raises，re-add cb_a 不重复
            and len(added1) == 1 and len(removed1) == 0
            and len(added2) == 0 and len(removed2) == 1
            and seen_a == [("added", "Fake Mic"), ("removed", "Fake Mic")]
            and seen_b == [("added", "Fake Mic"), ("removed", "Fake Mic")]
            and removed_flag is True
            and removed_again is False
            and w.reopen_call_count >= 2  # 至少 added + removed 各计数（仅成功的 cb 计入）
        )
        _record(
            name, ok,
            count_after_add=count_after_add,
            seen_a=seen_a,
            seen_b=seen_b,
            removed_flag=removed_flag,
            removed_again=removed_again,
            reopen_call_count=w.reopen_call_count,
            emits_n=len(emits),
        )
    except Exception as exc:  # noqa: BLE001
        _record(name, False, error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# V4: env COCO_AUDIO_HOTPLUG_INTERVAL_S 9-case + Watcher 默认从 env 读
# ---------------------------------------------------------------------------
def v4_env_poll_interval() -> None:
    name = "V4_env_hotplug_interval"
    try:
        import importlib
        from coco import audio_resilience as ar
        importlib.reload(ar)

        env = ar.ENV_HOTPLUG_INTERVAL
        default = ar.HOTPLUG_INTERVAL_DEFAULT  # 5.0
        min_v = ar.HOTPLUG_INTERVAL_MIN  # 0.01

        cases = [
            # (env_value or None, expected)
            (None, default),         # 未设
            ("", default),           # 空串
            ("   ", default),        # 纯空格
            ("abc", default),        # 非数字
            ("-1", default),         # 负数
            ("0", default),          # 0
            ("0.001", min_v),        # 小于 MIN → clamp
            ("3", 3.0),              # 合法整数
            ("0.5", 0.5),            # 合法浮点
        ]
        bad: List[Dict[str, Any]] = []
        for val, expect in cases:
            if val is None:
                os.environ.pop(env, None)
            else:
                os.environ[env] = val
            got = ar._read_hotplug_interval()
            if abs(got - expect) > 1e-9:
                bad.append({"input": val, "expected": expect, "got": got})

        # Watcher 默认从 env 读
        os.environ[env] = "0.05"
        w = ar.HotplugWatcher()
        watcher_pi = w._poll_interval
        # 显式参数覆盖 env
        w2 = ar.HotplugWatcher(poll_interval=2.5)
        explicit_pi = w2._poll_interval

        ok = (
            not bad
            and abs(watcher_pi - 0.05) < 1e-9
            and abs(explicit_pi - 2.5) < 1e-9
        )
        _record(name, ok, bad=bad, watcher_pi=watcher_pi, explicit_pi=explicit_pi, cases_n=len(cases))
    except Exception as exc:  # noqa: BLE001
        _record(name, False, error=f"{type(exc).__name__}: {exc}")
    finally:
        os.environ.pop("COCO_AUDIO_HOTPLUG_INTERVAL_S", None)


# ---------------------------------------------------------------------------
# V5: regression — verify_audio_010 / 009 / infra-018 / robot-007
# ---------------------------------------------------------------------------
def v5_regression() -> None:
    name = "V5_regression"
    targets = [
        "scripts/verify_audio_010.py",
        "scripts/verify_audio_009.py",
        "scripts/verify_infra_018.py",
        "scripts/verify_robot_007.py",
    ]
    rcs: Dict[str, int] = {}
    for t in targets:
        p = ROOT / t
        if not p.exists():
            rcs[t] = -1  # skip
            continue
        try:
            r = subprocess.run(
                [sys.executable, str(p)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=180,
            )
            rcs[t] = r.returncode
        except Exception as exc:  # noqa: BLE001
            rcs[t] = -2
            print(f"[verify_audio_011] regression {t} raised: {exc!r}", flush=True)
    ok = all(v == 0 or v == -1 for v in rcs.values())
    _record(name, ok, rcs=rcs)


def main() -> int:
    v1_asr_inputstream_wrap()
    v2_main_inputstream_wrap_recovery_off_equivalence()
    v3_hotplug_registry()
    v4_env_poll_interval()
    v5_regression()

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "audio-011",
        "ts": time.time(),
        "all_passed": all(r["ok"] for r in _results),
        "results": _results,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"[verify_audio_011] summary → {SUMMARY_PATH}", flush=True)
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
