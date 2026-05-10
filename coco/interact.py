"""coco.interact — 最小语音交互闭环（interact-001）.

目标：
- push-to-talk 抽象：可由真键盘事件触发，也可由 fixture 注入（sub-agent 验证用）
- 流程：开始录音 → 停止 → ASR 转写 → 模板回应（含简单关键词路由）→ TTS + robot 动作
- 与 IdleAnimator 互斥：interact 期间 idle 暂停，结束后恢复
- 不引 LLM；回应模板可被未来 feature 替换

线程模型：
- InteractSession.handle_audio(wav_or_pcm) 是同步函数，调用方决定在哪个线程跑
- 设计上从 push-to-talk listener（终端 / 后台线程 / 单元测试）调用
- 与 IdleAnimator 共享 robot，整段 handle_audio 内 idle 被 pause()
- handle_audio 不抛（除编程错误）：所有 SDK / ASR / TTS 异常吞掉记 stats
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional, Tuple

import numpy as np

from coco.actions import look_left, look_right, nod

if TYPE_CHECKING:  # pragma: no cover
    from reachy_mini import ReachyMini
    from coco.idle import IdleAnimator


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 模板回应：基于关键词路由 + 默认 "我听到你说：…"
# ---------------------------------------------------------------------------


KEYWORD_ROUTES: List[Tuple[Tuple[str, ...], str, str]] = [
    # (关键词组, 回应模板, 动作名)
    # 顺序要点：更"具体"的主题词放前面（如 "天气" 在 "好" 之前），避免被通用词截胡
    (("你好", "嗨", "hello", "hi"), "你好呀！很高兴见到你。", "nod"),
    (("再见", "拜拜", "bye"), "好的，回头见！", "nod"),
    (("天气", "公园", "外面"), "嗯，外面挺好的呀。", "look_right"),
    (("看", "瞧", "瞅"), "我也看看。", "look_left"),
    (("好", "对", "嗯", "是的"), "好的，我听到啦。", "nod"),
]


def route_reply(text: str) -> Tuple[str, str]:
    """根据 ASR 文本返回 (reply_text, action_name)。

    匹配规则：第一个命中的关键词组生效；都未命中走默认 "我听到你说：<text>" + nod。
    """
    text = (text or "").strip()
    for kws, reply, action in KEYWORD_ROUTES:
        for kw in kws:
            if kw in text:
                return reply, action
    if not text:
        return "我没听清，可以再说一次吗？", "nod"
    return f"我听到你说：{text}", "nod"


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class InteractStats:
    sessions: int = 0
    asr_ok: int = 0
    asr_fail: int = 0
    reply_ok: int = 0
    tts_fail: int = 0
    action_fail: int = 0
    last_transcript: str = ""
    last_reply: str = ""
    last_action: str = ""
    durations_s: List[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# InteractSession
# ---------------------------------------------------------------------------


class InteractSession:
    """协调一次 push-to-talk → ASR → reply → TTS+action 闭环。

    构造时只接 robot 与可选的 idle_animator；ASR / TTS 是函数注入，
    便于单元测试和未来替换。

    asr_fn(audio_int16, sr) -> str           （由 coco.asr 包装）
    tts_say_fn(text, blocking=True) -> None   （由 coco.tts 包装）
    """

    def __init__(
        self,
        robot: "ReachyMini",
        asr_fn: Callable[[np.ndarray, int], str],
        tts_say_fn: Callable[..., None],
        idle_animator: Optional["IdleAnimator"] = None,
        llm_reply_fn: Optional[Callable[[str], str]] = None,
        on_interaction: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.robot = robot
        self.asr_fn = asr_fn
        self.tts_say_fn = tts_say_fn
        self.idle_animator = idle_animator
        # interact-002: 可选 LLM 回应函数。注入则用 LLM 决定 reply 文本，
        # 动作仍通过 KEYWORD_ROUTES 路由（基于转写文本）。
        self.llm_reply_fn = llm_reply_fn
        # companion-003 L0-2: 任何 handle_audio 入口都是一次"交互"，统一在
        # session 内挂钩。调用方传入（一般是 power_state.record_interaction），
        # 默认 None 不影响 interact-001/004/005 等历史 verify。
        self.on_interaction = on_interaction
        self.stats = InteractStats()
        # 互斥：保证同一时刻只有一个 handle_audio 跑
        self._busy = threading.Lock()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def handle_audio(
        self,
        audio_int16: np.ndarray,
        sample_rate: int,
        *,
        skip_action: bool = False,
        skip_tts_play: bool = False,
    ) -> dict:
        """处理一段录音的完整链路。

        返回 dict：{"transcript", "reply", "action", "duration_s",
                  "asr_ok", "tts_ok", "action_ok"}。
        skip_action / skip_tts_play 用于 sub-agent 验证（不真发声 / 不真动）。
        """
        if not self._busy.acquire(blocking=False):
            log.warning("InteractSession 正忙，丢弃本次音频")
            return {"transcript": "", "reply": "", "action": "", "duration_s": 0.0,
                    "asr_ok": False, "tts_ok": False, "action_ok": False, "dropped": True}
        t0 = time.monotonic()
        result = {"transcript": "", "reply": "", "action": "", "duration_s": 0.0,
                  "asr_ok": False, "tts_ok": False, "action_ok": False, "dropped": False}
        try:
            self.stats.sessions += 1
            # companion-003 L0-2: 在所有具体处理之前 fire 一次交互信号
            # （PTT / VAD / wake-bridge 都走 handle_audio，统一在这里挂钩，
            # 避免每条入口路径漏挂）。任何 callback 异常都吞掉，绝不影响主流程。
            if self.on_interaction is not None:
                try:
                    self.on_interaction("audio")
                except Exception as e:  # noqa: BLE001
                    log.warning("on_interaction callback failed: %s: %s", type(e).__name__, e)
            # 1) idle 暂停
            if self.idle_animator is not None:
                self.idle_animator.pause()
            # 2) ASR
            try:
                transcript = self.asr_fn(audio_int16, sample_rate)
                self.stats.asr_ok += 1
                result["asr_ok"] = True
            except Exception as e:  # noqa: BLE001
                log.warning("ASR failed: %s: %s", type(e).__name__, e)
                self.stats.asr_fail += 1
                transcript = ""
            transcript = (transcript or "").strip()
            self.stats.last_transcript = transcript
            result["transcript"] = transcript
            log.info("[interact] ASR -> %r", transcript)

            # 3) 路由 reply + action
            reply, action = route_reply(transcript)
            # interact-002: 如果注入了 LLM，并且转写非空，用 LLM 覆盖 reply 文本；
            # 动作仍走 KEYWORD_ROUTES（基于转写）；LLM 失败/空时已在 LLMClient 内降级。
            if self.llm_reply_fn is not None and transcript:
                try:
                    llm_text = self.llm_reply_fn(transcript)
                    if llm_text and llm_text.strip():
                        reply = llm_text.strip()
                except Exception as e:  # noqa: BLE001
                    log.warning("LLM reply failed: %s: %s; using keyword route", type(e).__name__, e)
            self.stats.last_reply = reply
            self.stats.last_action = action
            self.stats.reply_ok += 1
            result["reply"] = reply
            result["action"] = action
            log.info("[interact] reply=%r action=%s", reply, action)

            # 4) TTS（可与动作并行；这里串行简化）
            if not skip_tts_play:
                try:
                    self.tts_say_fn(reply, blocking=True)
                except Exception as e:  # noqa: BLE001
                    log.warning("TTS failed: %s: %s", type(e).__name__, e)
                    self.stats.tts_fail += 1
                else:
                    result["tts_ok"] = True
            else:
                result["tts_ok"] = True

            # 5) 动作
            if not skip_action:
                try:
                    self._do_action(action)
                except Exception as e:  # noqa: BLE001
                    log.warning("action %s failed: %s: %s", action, type(e).__name__, e)
                    self.stats.action_fail += 1
                else:
                    result["action_ok"] = True
            else:
                result["action_ok"] = True

        finally:
            # 6) 恢复 idle
            if self.idle_animator is not None:
                self.idle_animator.resume()
            dt = time.monotonic() - t0
            result["duration_s"] = dt
            self.stats.durations_s.append(dt)
            self._busy.release()
        return result

    def _do_action(self, name: str) -> None:
        if name == "nod":
            nod(self.robot, amplitude_deg=12.0, duration=0.5)
        elif name == "look_left":
            look_left(self.robot, amplitude_deg=20.0, duration=0.5, return_to_center=True)
        elif name == "look_right":
            look_right(self.robot, amplitude_deg=20.0, duration=0.5, return_to_center=True)
        else:
            # 未知动作 → nod 兜底
            nod(self.robot, amplitude_deg=10.0, duration=0.4)


# ---------------------------------------------------------------------------
# Push-to-talk 抽象
# ---------------------------------------------------------------------------


class FixtureTrigger:
    """用 fixture wav 文件代替真键盘的 push-to-talk 触发。

    用法：trigger.run(session) 阻塞跑一组（path, label）的 fixture，依次喂给
    session.handle_audio。返回 (results, dt)。
    """

    def __init__(self, fixtures: List[Tuple[str, str]]) -> None:
        # fixtures: [(label, path), ...]
        self.fixtures = list(fixtures)

    @staticmethod
    def load_wav_int16(path: str | Path) -> Tuple[np.ndarray, int]:
        from scipy.io import wavfile  # local import 避免顶层成本
        sr, data = wavfile.read(str(path))
        if data.dtype != np.int16:
            # 简单归一化到 int16
            if np.issubdtype(data.dtype, np.floating):
                data = np.clip(data, -1.0, 1.0)
                data = (data * 32767).astype(np.int16)
            else:
                data = data.astype(np.int16)
        if data.ndim > 1:
            data = data[:, 0]
        return data, int(sr)

    def run(self, session: InteractSession, *, skip_tts_play: bool = False, skip_action: bool = False) -> List[dict]:
        results = []
        for label, path in self.fixtures:
            log.info("[interact] fixture %s -> %s", label, path)
            audio, sr = self.load_wav_int16(path)
            r = session.handle_audio(audio, sr, skip_action=skip_action, skip_tts_play=skip_tts_play)
            r["fixture_label"] = label
            r["fixture_path"] = str(path)
            results.append(r)
        return results


__all__ = [
    "InteractSession",
    "InteractStats",
    "FixtureTrigger",
    "route_reply",
    "KEYWORD_ROUTES",
]
