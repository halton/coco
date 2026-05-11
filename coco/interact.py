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

import inspect
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional, Tuple

import numpy as np

from coco.actions import look_left, look_right, nod
from coco.dialog import DialogMemory

if TYPE_CHECKING:  # pragma: no cover
    from reachy_mini import ReachyMini
    from coco.idle import IdleAnimator
    from coco.emotion import EmotionDetector, EmotionTracker
    from coco.profile import ProfileStore


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
        llm_reply_fn: Optional[Callable[..., str]] = None,
        on_interaction: Optional[Callable[[str], None]] = None,
        dialog_memory: Optional[DialogMemory] = None,
        emotion_detector: Optional["EmotionDetector"] = None,
        emotion_tracker: Optional["EmotionTracker"] = None,
        profile_store: Optional["ProfileStore"] = None,
    ) -> None:
        self.robot = robot
        self.asr_fn = asr_fn
        self.tts_say_fn = tts_say_fn
        self.idle_animator = idle_animator
        # interact-002: 可选 LLM 回应函数。注入则用 LLM 决定 reply 文本，
        # 动作仍通过 KEYWORD_ROUTES 路由（基于转写文本）。
        # interact-004: llm_reply_fn 现支持可选 history kwarg —— 优先用
        # ``llm_reply_fn(text, history=...)``；不接受 history 的旧签名也兼容。
        # closeout L1: 用 inspect 在构造期一次性探测签名，缓存 bool；
        # 避免 try/except TypeError 把 fn 内部的 TypeError 误判为"签名不接受
        # history"导致重复调用 / 二次副作用。
        self.llm_reply_fn = llm_reply_fn
        self._llm_accepts_history = self._probe_accepts_history(llm_reply_fn)
        # companion-003 L0-2: 任何 handle_audio 入口都是一次"交互"，统一在
        # session 内挂钩。调用方传入（一般是 power_state.record_interaction），
        # 默认 None 不影响 interact-001/004/005 等历史 verify。
        self.on_interaction = on_interaction
        # interact-004: 多轮对话 ring buffer。None 时退化为单轮模式（向后兼容）。
        # 即使 LLM 走 fallback，append 仍记录（保持 KEYWORD_ROUTES 路径"看似"多轮，
        # 但 history 仅在调用 llm_reply_fn 时实际注入）。
        self.dialog_memory = dialog_memory
        # interact-006: 情绪检测。emotion_detector=None 时整段路径不走，
        # 完全等价 phase-3 行为（向后兼容）。注入后：transcript 拿到 → detect →
        # tracker.record → idle_animator.set_current_emotion(effective) → emit。
        self.emotion_detector = emotion_detector
        self.emotion_tracker = emotion_tracker
        # companion-004: 可选 ProfileStore。注入后：
        #   - handle_audio 抽取 transcript 中的 profile 信号写盘（set_name /
        #     add_interest / add_goal），并 emit interact.profile_extracted。
        #   - LLM 调用前 build_system_prompt(profile) 注入 system_prompt。
        # None 时整段路径不走，完全等价 phase-3/interact-006 行为（向后兼容）。
        self.profile_store = profile_store
        # 探测 llm_reply_fn 是否接受 system_prompt kwarg（与 history 类似）。
        self._llm_accepts_system_prompt = self._probe_kwarg(llm_reply_fn, "system_prompt")
        self.stats = InteractStats()
        # 互斥：保证同一时刻只有一个 handle_audio 跑
        self._busy = threading.Lock()

    @staticmethod
    def _probe_accepts_history(fn: Optional[Callable[..., str]]) -> bool:
        """探测 llm_reply_fn 是否接受 ``history`` 关键字参数。

        - None → False（不会被调用）
        - 显式声明 ``history`` 参数 → True
        - 含 ``**kwargs`` → True
        - 否则 → False
        - inspect 失败（C 函数等）→ False（保守：不传 history，等价旧行为）
        """
        return InteractSession._probe_kwarg(fn, "history")

    @staticmethod
    def _probe_kwarg(fn: Optional[Callable[..., str]], name: str) -> bool:
        """通用 kwarg 探测；fn=None 或 inspect 失败 → False。"""
        if fn is None:
            return False
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return False
        for p in sig.parameters.values():
            if p.kind is inspect.Parameter.VAR_KEYWORD:
                return True
            if p.name == name and p.kind in (
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                return True
        return False

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

            # interact-006: 情绪检测。检测到非 NEUTRAL 才 record + emit；
            # NEUTRAL 走"无信号"分支不污染 tracker 的最近一次强情绪。
            # 异常一律吞掉（emotion 是非关键路径，绝不阻塞主对话流）。
            if self.emotion_detector is not None and transcript:
                try:
                    label = self.emotion_detector.detect(transcript)
                    if self.emotion_tracker is not None:
                        self.emotion_tracker.record(label)
                        effective = self.emotion_tracker.effective()
                    else:
                        effective = label.name
                    if self.idle_animator is not None and hasattr(self.idle_animator, "set_current_emotion"):
                        self.idle_animator.set_current_emotion(effective)
                    # 局部 import 避免顶层依赖（emotion 模块在 phase-4 才上）
                    try:
                        from coco.logging_setup import emit as _emit
                        _emit(
                            "interact.emotion_classified",
                            emotion=label.value,
                            score=label.score,
                            matched_terms=list(label.matched_terms),
                            effective=getattr(effective, "value", str(effective)),
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    result["emotion"] = label.value
                    result["emotion_score"] = label.score
                except Exception as e:  # noqa: BLE001
                    log.warning("emotion detect failed: %s: %s", type(e).__name__, e)

            # companion-004: profile 抽取（仅当 profile_store 注入且 transcript 非空）。
            # 抽取在 emotion 之后、route_reply 之前；写盘失败一律吞掉不阻塞主流程。
            if self.profile_store is not None and transcript:
                try:
                    from coco.profile import extract_profile_signals
                    sig = extract_profile_signals(transcript)
                    if sig:
                        if "name" in sig:
                            self.profile_store.set_name(sig["name"])
                        for it in sig.get("interests", []) or []:
                            self.profile_store.add_interest(it)
                        for g in sig.get("goals", []) or []:
                            self.profile_store.add_goal(g)
                        try:
                            from coco.logging_setup import emit as _emit
                            _emit(
                                "interact.profile_extracted",
                                name=sig.get("name"),
                                interests=list(sig.get("interests", []) or []),
                                goals=list(sig.get("goals", []) or []),
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        result["profile_extracted"] = sig
                except Exception as e:  # noqa: BLE001
                    log.warning("profile extract failed: %s: %s", type(e).__name__, e)

            # 3) 路由 reply + action
            reply, action = route_reply(transcript)
            # interact-002: 如果注入了 LLM，并且转写非空，用 LLM 覆盖 reply 文本；
            # 动作仍走 KEYWORD_ROUTES（基于转写）；LLM 失败/空时已在 LLMClient 内降级。
            # interact-004: 若有 dialog_memory 且 llm_reply_fn 支持 history kwarg
            # （构造时已用 inspect 探测，缓存于 self._llm_accepts_history），
            # 把最近 N 轮拼成 messages 注入；否则按旧签名调用。
            # 注意：不再用 try/except TypeError 探测——避免 fn 内部抛 TypeError
            # 被误判为签名不匹配导致重复调用。
            if self.llm_reply_fn is not None and transcript:
                history_msgs: Optional[List[dict]] = None
                if self.dialog_memory is not None and self._llm_accepts_history:
                    history_msgs = []
                    for u, a in self.dialog_memory.recent_turns():
                        if u:
                            history_msgs.append({"role": "user", "content": u})
                        if a:
                            history_msgs.append({"role": "assistant", "content": a})
                # companion-004: 组装 system_prompt（含 profile 块）若 fn 接受
                profile_sys_prompt: Optional[str] = None
                if self.profile_store is not None and self._llm_accepts_system_prompt:
                    try:
                        from coco.profile import build_system_prompt
                        from coco.llm import SYSTEM_PROMPT as _BASE_SYS
                        prof = self.profile_store.load()
                        profile_sys_prompt = build_system_prompt(prof, base=_BASE_SYS)
                    except Exception as e:  # noqa: BLE001
                        log.warning("profile system_prompt build failed: %s: %s",
                                    type(e).__name__, e)
                try:
                    kwargs: dict = {}
                    if history_msgs is not None:
                        kwargs["history"] = history_msgs
                    if profile_sys_prompt is not None:
                        kwargs["system_prompt"] = profile_sys_prompt
                    if kwargs:
                        llm_text = self.llm_reply_fn(transcript, **kwargs)
                    else:
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

            # interact-004: 记一轮到 ring buffer。即使走 KEYWORD_ROUTES（无 LLM）
            # 也 append —— 保持"对话发生过"的事实，下一次若 LLM 上线可立即接续。
            # transcript 为空（ASR 失败/静音）时不记，避免污染上下文。
            if self.dialog_memory is not None and transcript:
                try:
                    self.dialog_memory.append(transcript, reply)
                except Exception as e:  # noqa: BLE001
                    log.warning("dialog_memory.append failed: %s: %s", type(e).__name__, e)

            # 4) TTS（可与动作并行；这里串行简化）
            if not skip_tts_play:
                try:
                    # interact-006: 若 emotion 已检测且 tts_say_fn 接受 emotion kwarg，传入；
                    # 用 inspect 在调用点轻量探测，失败回退到不带 emotion 的旧调用。
                    em = result.get("emotion")
                    if em:
                        try:
                            sig = inspect.signature(self.tts_say_fn)
                            accepts_emotion = any(
                                p.kind is inspect.Parameter.VAR_KEYWORD
                                or (p.name == "emotion" and p.kind in (
                                    inspect.Parameter.KEYWORD_ONLY,
                                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                ))
                                for p in sig.parameters.values()
                            )
                        except (TypeError, ValueError):
                            accepts_emotion = False
                        if accepts_emotion:
                            self.tts_say_fn(reply, blocking=True, emotion=em)
                        else:
                            self.tts_say_fn(reply, blocking=True)
                    else:
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
