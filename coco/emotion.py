"""coco.emotion — 情绪/语气检测（interact-006）.

phase-4 简化版：基于关键词词典 + 标点/重复模式启发式的 5 类情绪分类。

5 类标签
========

- ``neutral`` — 默认；无明显情绪信号
- ``happy``   — 积极正向（开心 / 高兴 / 哈哈 ...）
- ``sad``     — 消极负向（难过 / 伤心 / 唉 ...）
- ``angry``   — 愤怒 / 厌烦（生气 / 讨厌 / 烦 ...）
- ``surprised`` — 惊讶 / 意外（真的吗 / 居然 / 哇 ...）

仲裁顺序
========

多类同分时按 ``HAPPY > SAD > ANGRY > SURPRISED > NEUTRAL`` 选择
（spec：5 类标签集合避免膨胀；happy 优先体现积极倾向）。

集成
====

- ``InteractSession`` 在 ``handle_audio`` 拿到 transcript 后调
  ``EmotionDetector.detect(text)`` → ``IdleAnimator.set_current_emotion(label)``
  并 emit ``interact.emotion_classified``。
- ``IdleConfig`` 新增 ``emotion_bias`` dict：micro_amp / glance_prob
  按当前 emotion 缩放（happy=1.3x / sad=0.7x / 其它=1.0x）。
- 衰减半衰期 60s（``COCO_EMOTION_DECAY_S``）：60s 后无新强情绪输入
  effective_emotion 回 NEUTRAL。
- backend Protocol 留接口：后续可换 LLM-based 实现。

env
===

- ``COCO_EMOTION``         — 1/true/yes 启用集成（默认 OFF；不设时
  完全等价 phase-3 行为，向后兼容）
- ``COCO_EMOTION_DECAY_S`` — 衰减半衰期秒数（默认 60，clamp [1, 3600]）

设计原则
========

- 纯 substring + count，无 ML 依赖（不引 jieba / spacy / transformers）
- 中文词典内嵌（小，~10-20 词/类）；中英混杂 fallback 单字 + 空格切分
- 异常输入（None / 数字 / 超长字符串）一律返回 neutral 不抛
- ``EmotionDetector`` 无状态；衰减由 ``EmotionTracker`` 单独维护
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, List, Mapping, Optional, Protocol

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


class Emotion(Enum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"


# 仲裁优先级：多类同分时选靠前者（数值越小越优先）。
_PRIORITY: dict = {
    Emotion.HAPPY: 0,
    Emotion.SAD: 1,
    Emotion.ANGRY: 2,
    Emotion.SURPRISED: 3,
    Emotion.NEUTRAL: 4,
}


@dataclass(frozen=True)
class EmotionLabel:
    """detect() 返回结果。

    - name: Emotion 枚举
    - score: 置信度 [0.0, 1.0]，匹配关键词数 / 文本长度归一化
    - matched_terms: 命中的关键词列表（用于 evidence / debug）
    """

    name: Emotion
    score: float = 0.0
    matched_terms: List[str] = field(default_factory=list)

    @property
    def value(self) -> str:
        """便捷读 string label，比如 'happy'。"""
        return self.name.value


# ---------------------------------------------------------------------------
# Lexicon — 中文为主，英文兜底；每类 10-20 词
# ---------------------------------------------------------------------------


DEFAULT_LEXICON: dict = {
    Emotion.HAPPY: [
        "开心", "高兴", "好棒", "棒", "太棒了", "哈哈", "嘻嘻",
        "喜欢", "爱", "好玩", "有趣", "happy", "yay", "great",
        "好耶", "笑", "幸福",
    ],
    Emotion.SAD: [
        "难过", "伤心", "哎", "唉", "委屈", "失望", "孤独",
        "想哭", "哭", "不开心", "郁闷", "sad", "心疼", "可怜",
    ],
    Emotion.ANGRY: [
        "气死", "讨厌", "烦", "生气", "愤怒", "可恶", "滚",
        "闭嘴", "走开", "angry", "hate", "气", "火大", "受不了",
    ],
    Emotion.SURPRISED: [
        "真的吗", "居然", "哇", "天哪", "不会吧", "竟然",
        "wow", "omg", "我去", "啊？", "什么？！", "真的？",
        "意外", "震惊",
    ],
}


# ---------------------------------------------------------------------------
# Backend Protocol — 留 LLM-based 替换接口
# ---------------------------------------------------------------------------


class EmotionBackend(Protocol):
    """情绪检测后端接口。phase-4 默认 keyword heuristic 实现；
    后续可注入 LLM-based 实现。"""

    def detect(self, text: str) -> EmotionLabel: ...


# ---------------------------------------------------------------------------
# EmotionDetector — keyword heuristic 实现
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmotionConfig:
    """情绪检测配置。env 由 ``config_from_env()`` 解析。"""

    decay_s: float = 60.0
    enabled: bool = False  # COCO_EMOTION


DEFAULT_DECAY_S = 60.0


class EmotionDetector:
    """关键词启发式情绪检测器。

    - 纯 substring + count（O(N*M)，N=词典词数，M=文本长度）
    - 多类同分时按 _PRIORITY 仲裁
    - confidence = matched_terms 数 / max(len(text)/4, 1.0)
      （/4 经验值：每个汉字 ~3 字节、每个 token ~2-4 字符；让一句话 1 个关键词
      约 0.25-0.5 confidence，不至于贴顶；超过 1.0 时 clamp）
    - 异常输入（None / 数字 / 超长 / 仅标点）→ NEUTRAL score=0
    """

    # 超长文本截断阈值（防 DoS / 性能）：超此长度按 NEUTRAL 处理
    MAX_TEXT_LEN = 2000

    def __init__(self, lexicon: Optional[dict] = None) -> None:
        self.lexicon = lexicon if lexicon is not None else DEFAULT_LEXICON

    def detect(self, text: Any) -> EmotionLabel:
        # 异常输入快速 return
        if text is None or not isinstance(text, str):
            return EmotionLabel(Emotion.NEUTRAL, 0.0, [])
        text = text.strip()
        if not text:
            return EmotionLabel(Emotion.NEUTRAL, 0.0, [])
        if len(text) > self.MAX_TEXT_LEN:
            log.warning("[emotion] text too long (%d > %d), neutral", len(text), self.MAX_TEXT_LEN)
            return EmotionLabel(Emotion.NEUTRAL, 0.0, [])

        # 全文小写化便于英文匹配；中文不受影响
        text_lc = text.lower()

        # 收集每类匹配项
        matches: dict = {}  # Emotion -> List[str]
        for emo, words in self.lexicon.items():
            hits: List[str] = []
            for w in words:
                # substring 匹配；空串保护
                if not w:
                    continue
                if w.lower() in text_lc:
                    hits.append(w)
            if hits:
                matches[emo] = hits

        if not matches:
            return EmotionLabel(Emotion.NEUTRAL, 0.0, [])

        # 选最大命中数；同分按 _PRIORITY 仲裁
        max_count = max(len(v) for v in matches.values())
        candidates = [emo for emo, v in matches.items() if len(v) == max_count]
        candidates.sort(key=lambda e: _PRIORITY.get(e, 99))
        winner = candidates[0]
        winner_hits = matches[winner]

        # confidence 归一化：count / (len(text)/4)，clamp [0, 1]
        denom = max(len(text) / 4.0, 1.0)
        score = float(max_count) / denom
        if score > 1.0:
            score = 1.0
        if score < 0.0:
            score = 0.0

        return EmotionLabel(winner, round(score, 4), winner_hits)


# ---------------------------------------------------------------------------
# EmotionTracker — 维护 effective_emotion 与衰减
# ---------------------------------------------------------------------------


class EmotionTracker:
    """情绪状态机：记录最新一次检测到的强情绪 + 半衰期衰减。

    - record(label, now): 记一次检测结果
    - effective(now): 返回当前生效的 emotion（衰减后回 NEUTRAL）
    - 半衰期 decay_s：从 record() 起算超过 decay_s 秒后回 NEUTRAL
    - now 可注入 fake clock；默认 time.monotonic
    """

    def __init__(
        self,
        decay_s: float = DEFAULT_DECAY_S,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.decay_s = float(decay_s)
        self._clock = clock or time.monotonic
        self._last_emotion: Emotion = Emotion.NEUTRAL
        self._last_at: float = 0.0
        self._last_score: float = 0.0

    def record(self, label: EmotionLabel, now: Optional[float] = None) -> None:
        if label is None or label.name == Emotion.NEUTRAL:
            return
        if now is None:
            now = self._clock()
        self._last_emotion = label.name
        self._last_at = float(now)
        self._last_score = float(label.score)

    def effective(self, now: Optional[float] = None) -> Emotion:
        if self._last_emotion == Emotion.NEUTRAL:
            return Emotion.NEUTRAL
        if now is None:
            now = self._clock()
        if (now - self._last_at) > self.decay_s:
            return Emotion.NEUTRAL
        return self._last_emotion

    def reset(self) -> None:
        self._last_emotion = Emotion.NEUTRAL
        self._last_at = 0.0
        self._last_score = 0.0


# ---------------------------------------------------------------------------
# env helpers
# ---------------------------------------------------------------------------


def emotion_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    """COCO_EMOTION=1 启用集成。默认 OFF（向后兼容 phase-3）。"""
    e = env if env is not None else os.environ
    raw = (e.get("COCO_EMOTION") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def config_from_env(env: Optional[Mapping[str, str]] = None) -> EmotionConfig:
    """从环境读 decay_s + enabled，越界 clamp + warn。

    - COCO_EMOTION_DECAY_S: 1..3600，默认 60
    - COCO_EMOTION:        见 emotion_enabled_from_env
    """
    e = env if env is not None else os.environ
    raw_d = (e.get("COCO_EMOTION_DECAY_S") or str(DEFAULT_DECAY_S)).strip()
    try:
        d = float(raw_d)
    except ValueError:
        log.warning("[emotion] COCO_EMOTION_DECAY_S=%r 非数字，回退默认 %.1f", raw_d, DEFAULT_DECAY_S)
        d = DEFAULT_DECAY_S
    if d < 1.0:
        log.warning("[emotion] COCO_EMOTION_DECAY_S=%.2f <1，clamp 到 1.0", d)
        d = 1.0
    if d > 3600.0:
        log.warning("[emotion] COCO_EMOTION_DECAY_S=%.2f >3600，clamp 到 3600.0", d)
        d = 3600.0
    enabled = emotion_enabled_from_env(e)
    return EmotionConfig(decay_s=d, enabled=enabled)


__all__ = [
    "Emotion",
    "EmotionLabel",
    "EmotionConfig",
    "EmotionBackend",
    "EmotionDetector",
    "EmotionTracker",
    "DEFAULT_LEXICON",
    "DEFAULT_DECAY_S",
    "emotion_enabled_from_env",
    "config_from_env",
]
