#!/usr/bin/env python
"""audio-002 verification 3 主验脚本：wav 路径 ASR + CER。

跑法：
    ./.venv/bin/python scripts/verify_asr_wav.py

阈值：CER < 0.15（合成音 fixture 的弹性阈值；真人录音再校准到 0.10）。
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coco.asr import transcribe_wav  # noqa: E402

WAV = ROOT / "tests" / "fixtures" / "audio" / "zh-001-walk-park.wav"
REF_TXT = ROOT / "tests" / "fixtures" / "audio" / "zh-001-walk-park.txt"
CER_THRESHOLD = 0.15


def normalize(s: str) -> str:
    """去标点 + 空格 + SenseVoice 标签前缀，仅保留中日韩汉字与英文字母数字。"""
    # 去除 SenseVoice 标签如 <|zh|><|NEUTRAL|><|Speech|><|woitn|>
    s = re.sub(r"<\|[^|]*\|>", "", s)
    return re.sub(r"[^一-鿿 A-Za-z0-9]", "", s).replace(" ", "")


def cer(hyp: str, ref: str) -> float:
    """Character Error Rate via Levenshtein distance（无外部依赖）。"""
    hyp, ref = normalize(hyp), normalize(ref)
    if not ref:
        return 0.0
    m, n = len(hyp), len(ref)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            cur = dp[j]
            if hyp[i - 1] == ref[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j - 1], dp[j])
            prev = cur
    return dp[n] / len(ref)


def wav_duration_seconds(path: Path) -> float:
    import scipy.io.wavfile as wavfile

    sr, data = wavfile.read(str(path))
    return len(data) / float(sr)


def main() -> int:
    if not WAV.exists():
        print(f"FAIL: wav fixture 不存在: {WAV}", file=sys.stderr)
        return 2
    if not REF_TXT.exists():
        print(f"FAIL: ref 标注不存在: {REF_TXT}", file=sys.stderr)
        return 2

    ref = REF_TXT.read_text(encoding="utf-8").strip()
    dur = wav_duration_seconds(WAV)

    t0 = time.time()
    hyp = transcribe_wav(WAV)
    dt = time.time() - t0

    err = cer(hyp, ref)
    rtf = dt / dur if dur > 0 else float("inf")

    print(f"WAV : {WAV.name}  ({dur:.2f}s)")
    print(f"REF : {ref}")
    print(f"HYP : {hyp}")
    print(f"NORM HYP: {normalize(hyp)}")
    print(f"NORM REF: {normalize(ref)}")
    print(f"CER : {err:.4f}  (阈值 < {CER_THRESHOLD})")
    print(f"耗时: {dt:.2f}s   RTF: {rtf:.3f}")

    if err < CER_THRESHOLD:
        print("V3 PASS")
        return 0
    print(f"V3 FAIL: CER {err:.4f} >= {CER_THRESHOLD}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
