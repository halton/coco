"""audio-002 V4 离线验证：把 V3 wav 喂给 VAD → SenseVoice，验证 VAD 链路。

不开麦克。证明 VoiceActivityDetector + OfflineRecognizer 协同可用。
真机麦克实测走 scripts/verify_asr_microphone.py（manual UAT）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile

from coco.asr import transcribe_segments_from_array

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "audio" / "zh-001-walk-park.wav"
REF = "今天天气真好，我们一起去公园散步。"


def _cer(hyp: str, ref: str) -> float:
    """字符错误率，与 V3 主验脚本一致的简化实现。"""
    import unicodedata

    def _norm(s: str) -> str:
        s = unicodedata.normalize("NFKC", s)
        return "".join(c for c in s if not c.isspace() and c not in "，。！？,.!?")

    h, r = _norm(hyp), _norm(ref)
    n, m = len(h), len(r)
    if m == 0:
        return 0.0 if n == 0 else 1.0
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, m + 1):
            cur = dp[j]
            if h[i - 1] == r[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = cur
    return dp[m] / m


def main() -> int:
    sr, audio = wavfile.read(str(FIXTURE))
    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    else:
        audio = audio.astype(np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    assert sr == 16000, f"fixture sr={sr}"

    print(f"[info] fixture: {FIXTURE.name}, samples={len(audio)}, dur={len(audio)/sr:.2f}s")
    segments = transcribe_segments_from_array(audio, sample_rate=16000)
    print(f"[info] VAD segments: {len(segments)}")
    for i, s in enumerate(segments):
        print(f"  [{i}] {s!r}")

    if not segments:
        print("[FAIL] VAD 未切出任何段")
        return 1

    joined = "".join(segments)
    cer = _cer(joined, REF)
    print(f"[info] joined hyp: {joined!r}")
    print(f"[info] ref       : {REF!r}")
    print(f"[info] CER       : {cer:.4f}")

    if cer < 0.15:
        print(f"[PASS] VAD→ASR 链路可用，segments={len(segments)}, CER={cer:.4f}")
        return 0
    print(f"[FAIL] CER={cer:.4f} >= 0.15")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
