"""audio-002 V4 麦克 manual UAT。

用户实际开麦克说一句中文，本脚本调 ``transcribe_microphone(seconds=5)``，
打印识别段落。CER 不强卡，由用户人工核对。

跑法：
    ./.venv/bin/python scripts/verify_asr_microphone.py
"""

from __future__ import annotations

from coco.asr import transcribe_microphone


def main() -> int:
    print("=" * 60)
    print("audio-002 V4 麦克 manual UAT")
    print("=" * 60)
    print("请在 5 秒内说一句中文，例如：今天天气真好。")
    print("准备好后回车开始录音…")
    try:
        input()
    except EOFError:
        pass

    print("[info] 录音中（5 秒）…")
    segments = transcribe_microphone(seconds=5.0)
    print(f"[info] VAD 切出 {len(segments)} 段")
    for i, s in enumerate(segments):
        print(f"  [{i}] {s!r}")

    if not segments:
        print("[WARN] 未检出任何语音段（可能太轻 / 过短 / 默认输入设备未生效）")
        return 1
    print("[OK] 请人工核对识别文本是否与你说的内容一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
