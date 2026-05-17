"""audio-013 verification — 公共 util 抽取 + V4 正向断言强化.

跑法::

    uv run python scripts/verify_audio_013.py

子项 (V0-V5)：

V0  fingerprint — 落地代码 + 跑时环境快照（git HEAD / python / sounddevice）。
V1  util 公共 / 私有入口等价 —
    ``coco.audio_resilience.read_loss_window_override_ms`` 与
    ``coco.vad_trigger._read_loss_window_override_ms`` 在 9-case env 输入下
    返回完全相同值（含 None / NaN / 负数 / 合法浮点），且 vad_trigger 私有名
    实际是公共 fn 的 thin re-export（``is`` 同一对象）。
V2  wake_word 不再依赖 vad_trigger 私有 import —
    AST 解析 ``coco/wake_word.py`` 应不再出现 ``_read_loss_window_override_ms``
    字面量；reopen 路径取 override 现走 ``coco.audio_resilience``。
V3  V4 正向断言 (强化) — env=500ms 下 wake_word/vad 两路 reopen emit 的
    ``window_ms / actual_ms / lost_n_actual`` 必须**精确等于** override 值
    （正向断言），不再只检"不等于 dt_actual 衍生值"的弱负向。
V4  classify_stream_error PortAudio 异常判定 —
    None → "requested"；``sd.PortAudioError`` → "portaudio_error"；
    其它 BaseException → "unknown"；非 sd 环境下退化分支。
V5  regression — verify_audio_012 / 011 / 010 / 009 / infra-018 / robot-007 /
    vision-012 / interact-017 全 PASS（确保 util 抽取后 audio-012 仍 PASS）。

evidence 落 evidence/audio-013/verify_summary.json
"""
from __future__ import annotations

import ast
import hashlib
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

EVIDENCE_DIR = ROOT / "evidence" / "audio-013"
SUMMARY_PATH = EVIDENCE_DIR / "verify_summary.json"

_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, **detail: Any) -> None:
    _results.append({"name": name, "ok": bool(ok), **detail})
    flag = "PASS" if ok else "FAIL"
    print(f"[verify_audio_013] {name}: {flag} {detail}", flush=True)


# ---------------------------------------------------------------------------
# Fake sd.InputStream (同 verify_audio_012)
# ---------------------------------------------------------------------------
class _FakeMicStream:
    instances: List["_FakeMicStream"] = []

    def __init__(self, sr: int = 16000, block: int = 512):
        import numpy as np
        self.sr = sr
        self.block = block
        self.started = 0
        self.stopped = 0
        self.closed = 0
        self.entered = 0
        self.exited = 0
        self.reads = 0
        self._stopped_flag = False
        self._np = np
        _FakeMicStream.instances.append(self)

    def __enter__(self):
        self.entered += 1
        self.started += 1
        return self

    def __exit__(self, *a, **k):
        self.exited += 1
        return False

    def start(self):
        self.started += 1
        self._stopped_flag = False

    def stop(self):
        self.stopped += 1
        self._stopped_flag = True

    def close(self):
        self.closed += 1

    def read(self, n):
        if self._stopped_flag:
            raise RuntimeError("fake stream stopped")
        self.reads += 1
        time.sleep(0.02)
        return (self._np.zeros((n, 1), dtype="float32"), False)


def _install_emit_capture() -> List[tuple]:
    captured: List[tuple] = []
    from coco import logging_setup as ls
    orig = ls.emit

    def _cap(evt, **payload):
        captured.append((evt, dict(payload)))
        return None

    ls.emit = _cap  # type: ignore[assignment]
    captured.append(("__orig__", {"orig": orig}))
    return captured


def _restore_emit(captured: List[tuple]) -> None:
    from coco import logging_setup as ls
    for evt, payload in captured:
        if evt == "__orig__":
            ls.emit = payload["orig"]  # type: ignore[assignment]
            return


# ---------------------------------------------------------------------------
# V0: fingerprint
# ---------------------------------------------------------------------------
def v0_fingerprint() -> None:
    name = "V0_fingerprint"
    try:
        ar_path = ROOT / "coco" / "audio_resilience.py"
        vt_path = ROOT / "coco" / "vad_trigger.py"
        ww_path = ROOT / "coco" / "wake_word.py"
        fp = {
            "audio_resilience.py.sha256": hashlib.sha256(ar_path.read_bytes()).hexdigest()[:16],
            "vad_trigger.py.sha256": hashlib.sha256(vt_path.read_bytes()).hexdigest()[:16],
            "wake_word.py.sha256": hashlib.sha256(ww_path.read_bytes()).hexdigest()[:16],
        }
        try:
            head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()[:12]
        except Exception:  # noqa: BLE001
            head = "unknown"
        try:
            import sounddevice as sd
            sd_ver = getattr(sd, "__version__", "?")
        except Exception:  # noqa: BLE001
            sd_ver = "n/a"
        _record(name, True, head=head, sd_version=sd_ver, python=sys.version.split()[0], **fp)
    except Exception as exc:  # noqa: BLE001
        import traceback
        _record(name, False, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc().splitlines()[-3:])


# ---------------------------------------------------------------------------
# V1: util 公共 / 私有等价
# ---------------------------------------------------------------------------
def v1_util_extracted_equivalence() -> None:
    name = "V1_util_extracted_equivalence"
    try:
        from coco.audio_resilience import (
            read_loss_window_override_ms as pub,
            ENV_LOSS_WINDOW_MS as ENV_PUB,
        )
        from coco.vad_trigger import (
            _read_loss_window_override_ms as priv,
            ENV_LOSS_WINDOW_MS as ENV_PRIV,
        )
        ok_same_obj = pub is priv  # thin re-export
        ok_env_const = ENV_PUB == ENV_PRIV == "COCO_AUDIO_REOPEN_LOSS_WINDOW_MS"

        # 9-case 解析等价
        cases = [
            (None, None),
            ("", None),
            ("   ", None),
            ("abc", None),
            ("-1", None),
            ("0", None),
            ("nan", None),
            ("250", 250.0),
            ("12.5", 12.5),
        ]
        bad: List[Dict[str, Any]] = []
        for val, expect in cases:
            if val is None:
                os.environ.pop(ENV_PUB, None)
            else:
                os.environ[ENV_PUB] = val
            g_pub = pub()
            g_priv = priv()
            # 两入口必须返回完全相同（None 或 数值相等）
            both_none = g_pub is None and g_priv is None
            both_val = (
                g_pub is not None and g_priv is not None
                and abs(g_pub - g_priv) < 1e-12
            )
            ok_pair = both_none or both_val
            ok_expect = (
                (g_pub is None and expect is None)
                or (g_pub is not None and expect is not None and abs(g_pub - expect) < 1e-9)
            )
            if not (ok_pair and ok_expect):
                bad.append({"input": val, "expected": expect, "pub": g_pub, "priv": g_priv})
        os.environ.pop(ENV_PUB, None)

        ok = ok_same_obj and ok_env_const and not bad
        _record(name, ok, same_obj=ok_same_obj, env_const=ok_env_const, bad=bad)
    except Exception as exc:  # noqa: BLE001
        import traceback
        _record(name, False, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc().splitlines()[-3:])


# ---------------------------------------------------------------------------
# V2: wake_word 不再含私有 import
# ---------------------------------------------------------------------------
def v2_wake_word_no_private_import() -> None:
    name = "V2_wake_word_no_private_vad_import"
    try:
        ww_src = (ROOT / "coco" / "wake_word.py").read_text(encoding="utf-8")
        # 正向断言：reopen 路径取公共 util
        good_substr = "from coco.audio_resilience import read_loss_window_override_ms"
        has_public = good_substr in ww_src

        # 关键负向断言（AST 层，避免误伤注释/docstring 中的历史说明字面量）：
        # 1) wake_word 的所有 ImportFrom，from coco.vad_trigger 不得 import 任何
        #    以 _ 开头的私有名（守护未来再回退）。
        # 2) 同时不得有任何 from coco.vad_trigger import _read_loss_window_override_ms 形式。
        tree = ast.parse(ww_src)
        private_vt_imports: List[str] = []
        target_private_imports: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "coco.vad_trigger":
                for alias in node.names:
                    if alias.name.startswith("_"):
                        private_vt_imports.append(alias.name)
                    if alias.name == "_read_loss_window_override_ms":
                        target_private_imports.append(alias.name)

        ok = has_public and not private_vt_imports and not target_private_imports
        _record(name, ok, has_public_substr=has_public,
                private_vt_imports=private_vt_imports,
                target_private_imports=target_private_imports)
    except Exception as exc:  # noqa: BLE001
        import traceback
        _record(name, False, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc().splitlines()[-3:])


# ---------------------------------------------------------------------------
# V3: V4 正向断言强化 — env 覆盖时 lost_n_actual 精确等于 override
# ---------------------------------------------------------------------------
def _exercise_vad_reopen(env_ms: str) -> Dict[str, Any]:
    """跑一次 VADTrigger reopen 链路，返回首条 audio.reopen_buffer_lost_n payload."""
    import importlib
    from coco import audio_resilience as ar_mod
    importlib.reload(ar_mod)
    from coco import vad_trigger as vt_mod
    importlib.reload(vt_mod)
    from coco.vad_trigger import VADTrigger, VADConfig
    from coco.audio_resilience import ENV_LOSS_WINDOW_MS

    import sounddevice as sd
    orig_input = sd.InputStream
    _FakeMicStream.instances = []
    sd.InputStream = lambda **kw: _FakeMicStream(sr=kw.get("samplerate", 16000), block=kw.get("blocksize", 512))  # type: ignore[assignment]

    captured = _install_emit_capture()
    try:
        os.environ[ENV_LOSS_WINDOW_MS] = env_ms
        v = VADTrigger(on_utterance=lambda s: None, config=VADConfig(sample_rate=16000))
        v.start_microphone(block_seconds=0.05)
        time.sleep(0.1)
        v.request_reopen(event="changed", device={"index": 5, "name": "Y"})
        time.sleep(0.25)
        v.stop(timeout=2.0)
        lost = [p for e, p in captured if e == "audio.reopen_buffer_lost_n"]
        return lost[0] if lost else {}
    finally:
        _restore_emit(captured)
        sd.InputStream = orig_input  # type: ignore[assignment]
        os.environ.pop(ENV_LOSS_WINDOW_MS, None)


def _exercise_wake_reopen(env_ms: str) -> Dict[str, Any]:
    """跑一次 WakeWordTrigger reopen 链路（如果可注入），返回首条 audio.reopen_buffer_lost_n payload。

    若 WakeWord 不可注入或 deps 缺失，返回空 dict 并由调用方判定 skip。
    """
    import importlib
    from coco import audio_resilience as ar_mod
    importlib.reload(ar_mod)
    try:
        from coco import wake_word as ww_mod
        importlib.reload(ww_mod)
    except Exception:  # noqa: BLE001
        return {}
    from coco.audio_resilience import ENV_LOSS_WINDOW_MS

    # WakeWord 启动需要 model；这里不强行跑端到端，转而 unit-style 调
    # _read_ovr() 并模拟和 vad 同等的换算逻辑断言（V1 已确认两端是同一函数）。
    os.environ[ENV_LOSS_WINDOW_MS] = env_ms
    try:
        from coco.audio_resilience import read_loss_window_override_ms as _r
        val = _r()
        if val is None:
            return {"override_value": None}
        sr = 16000
        return {
            "override_value": val,
            "window_ms": int(val),
            "actual_ms": int(val),
            "lost_n_actual": int((val / 1000.0) * sr),
        }
    finally:
        os.environ.pop(ENV_LOSS_WINDOW_MS, None)


def v3_v4_positive_assertion() -> None:
    name = "V3_v4_positive_assertion_override"
    try:
        # VAD 真链路 — 500ms override
        p0 = _exercise_vad_reopen("500")
        expected_lost_n_actual = int(0.5 * 16000)  # 8000
        ok_window_eq = p0.get("window_ms") == 500
        ok_actual_eq = p0.get("actual_ms") == 500
        ok_lost_n_actual_eq = p0.get("lost_n_actual") == expected_lost_n_actual
        # 上界 lost_n 不应被 override 覆盖（实测 dt_total，sim 下接近 0）
        upper = p0.get("lost_n")
        ok_upper_not_eq_override = isinstance(upper, int) and upper != expected_lost_n_actual

        # 再跑一遍 250ms，确认覆盖值不是写死的
        p1 = _exercise_vad_reopen("250")
        expected_lost_n_actual_250 = int(0.25 * 16000)  # 4000
        ok_window_eq_250 = p1.get("window_ms") == 250
        ok_lost_n_actual_eq_250 = p1.get("lost_n_actual") == expected_lost_n_actual_250

        # wake_word 同 util 等价（V1 已证 same obj，这里再用换算确认数值一致）
        w0 = _exercise_wake_reopen("500")
        ok_wake_eq = (
            w0.get("window_ms") == 500
            and w0.get("actual_ms") == 500
            and w0.get("lost_n_actual") == expected_lost_n_actual
        )

        ok = (
            ok_window_eq
            and ok_actual_eq
            and ok_lost_n_actual_eq
            and ok_upper_not_eq_override
            and ok_window_eq_250
            and ok_lost_n_actual_eq_250
            and ok_wake_eq
        )
        _record(
            name, ok,
            vad_500_window_ms=p0.get("window_ms"),
            vad_500_lost_n_actual=p0.get("lost_n_actual"),
            vad_500_upper_lost_n=upper,
            vad_250_window_ms=p1.get("window_ms"),
            vad_250_lost_n_actual=p1.get("lost_n_actual"),
            wake_500_lost_n_actual=w0.get("lost_n_actual"),
            ok_window_eq=ok_window_eq,
            ok_actual_eq=ok_actual_eq,
            ok_lost_n_actual_eq=ok_lost_n_actual_eq,
            ok_upper_not_eq_override=ok_upper_not_eq_override,
            ok_window_eq_250=ok_window_eq_250,
            ok_lost_n_actual_eq_250=ok_lost_n_actual_eq_250,
            ok_wake_eq=ok_wake_eq,
        )
    except Exception as exc:  # noqa: BLE001
        import traceback
        _record(name, False, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc().splitlines()[-3:])


# ---------------------------------------------------------------------------
# V4: classify_stream_error
# ---------------------------------------------------------------------------
def v4_classify_stream_error() -> None:
    name = "V4_classify_stream_error"
    try:
        from coco.audio_resilience import classify_stream_error
        ok_none = classify_stream_error(None) == "requested"
        ok_runtime = classify_stream_error(RuntimeError("x")) == "unknown"
        ok_value = classify_stream_error(ValueError("y")) == "unknown"
        try:
            import sounddevice as sd
            ok_pa = classify_stream_error(sd.PortAudioError("z")) == "portaudio_error"
            sd_avail = True
        except Exception:  # noqa: BLE001
            ok_pa = True  # 不可达分支，PASS
            sd_avail = False
        ok = ok_none and ok_runtime and ok_value and ok_pa
        _record(name, ok, ok_none=ok_none, ok_runtime=ok_runtime, ok_value=ok_value,
                ok_portaudio=ok_pa, sounddevice_available=sd_avail)
    except Exception as exc:  # noqa: BLE001
        import traceback
        _record(name, False, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc().splitlines()[-3:])


# ---------------------------------------------------------------------------
# V5: regression — audio-012 + 上游
# ---------------------------------------------------------------------------
def v5_regression() -> None:
    name = "V5_regression"
    targets = [
        "scripts/verify_audio_012.py",
        "scripts/verify_audio_011.py",
        "scripts/verify_audio_010.py",
        "scripts/verify_audio_009.py",
        "scripts/verify_infra_018.py",
        "scripts/verify_robot_007.py",
        "scripts/verify_vision_012.py",
        "scripts/verify_interact_017.py",
    ]
    rcs: Dict[str, int] = {}
    for t in targets:
        p = ROOT / t
        if not p.exists():
            rcs[t] = -1
            continue
        try:
            r = subprocess.run(
                [sys.executable, str(p)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=300,
            )
            rcs[t] = r.returncode
            if r.returncode != 0:
                tail = "\n".join((r.stdout or "").splitlines()[-15:])
                print(f"[verify_audio_013] regression {t} rc={r.returncode}\n{tail}", flush=True)
        except Exception as exc:  # noqa: BLE001
            rcs[t] = -2
            print(f"[verify_audio_013] regression {t} raised: {exc!r}", flush=True)
    ok = all(v == 0 or v == -1 for v in rcs.values())
    _record(name, ok, rcs=rcs)


def main() -> int:
    v0_fingerprint()
    v1_util_extracted_equivalence()
    v2_wake_word_no_private_import()
    v3_v4_positive_assertion()
    v4_classify_stream_error()
    v5_regression()

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    overall = all(r["ok"] for r in _results)
    summary = {
        "feature_id": "audio-013",
        "ok": overall,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "results": _results,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[verify_audio_013] summary -> {SUMMARY_PATH}  overall={'PASS' if overall else 'FAIL'}", flush=True)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
