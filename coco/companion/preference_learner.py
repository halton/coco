"""coco.companion.preference_learner — companion-009 用户偏好学习.

设计目标
========

把跨会话已经持久化 / in-session 仍鲜活的对话数据转成用户偏好关键词
（TopK + 时间衰减），写入 ``PersistentProfileStore`` 的 ``prefer_topics`` 字段；
``ProactiveScheduler`` 选话题时可据此加权候选 topic，让主动开口更贴近用户兴趣。

输入来源（duck-typed，三者均可缺省，缺即跳过该来源）：

- ``DialogMemory.recent_turns()`` → list[(user_text, assistant_text)]
  每轮记一次"现在"时刻（learner 持有 clock，append 时盖一次 ts）
- ``DialogMemory.summary`` → 摘要文本（interact-009 触发后存在）
- ``PersistedProfile.dialog_summary`` → 跨会话摘要列表（companion-008 落盘的）

抽取算法（启发式，不引入新依赖；保留 LLMPreferenceBackend stub docstring）：

1. 把 user_text + assistant_text 拼成纯文本；正则保留中文 / 英文 / 数字。
2. 中文段切成 bigram（连续 2 字），英文段按空白切成 token（>=2 chars）。
3. 用内置停用词表过滤；过短 token / 纯数字 token 丢弃。
4. 时间衰减：weight = exp(-Δt / half_life_s)，Δt 为该 turn 与 ``now`` 的差；
   旧 turn 衰减更狠。摘要文本统一按"摘要时刻"（learner 维护单一 ts）计权。
5. 累加 → 取 TopK 词 → 归一化（最大 weight 缩放到 1.0），输出 dict。
6. 同义合并（简单版）：若 keyword A 是 keyword B 的子串且 |A| < |B|，
   把 A 的 weight 累加到 B，A 丢弃。仅做一轮，避免链式。

线程模型：
- ``PreferenceLearner`` 内部 RLock；``on_turn`` 任意线程可调；
- ``rebuild_for_profile(...)`` 是同步耗时函数，调用方自行节流（main.py 接线层
  每 N 轮 / on_profile_switch 才触发）。

env：``COCO_PREFER_LEARN=1`` 才在 main.py 装配；本模块自身不读 env，便于复用 +
verify 直构造。
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


DEFAULT_TOPK = 10
DEFAULT_HALF_LIFE_S = 86400.0  # 24h
DEFAULT_MIN_TOKEN_LEN = 2
DEFAULT_PERSIST_EVERY_N_TURNS = 20

# 极简中文停用词表（高频虚词、代词、量词、口语助词），覆盖最常见对话噪声。
# 不追求齐全；目标是把"我们""然后""可以"这种盖过实词的高频词压下。
_ZH_STOP = frozenset([
    # 人称 / 指代
    "我", "你", "他", "她", "它", "们", "我们", "你们", "他们", "她们",
    "这", "那", "这个", "那个", "这样", "那样", "这里", "那里",
    "自己", "别人",
    # 虚词 / 副词 / 助词
    "的", "了", "在", "和", "与", "也", "都", "就", "还", "再", "又",
    "很", "更", "最", "好", "不", "没", "没有", "是", "不是", "有", "没",
    "可以", "可能", "应该", "需要", "要", "会", "能", "想", "觉得",
    "因为", "所以", "但是", "不过", "如果", "或者", "而且", "还是",
    "什么", "怎么", "怎样", "为什么", "为啥", "哪里", "哪个", "几",
    "一下", "一点", "一些", "一直", "一起", "一样", "一个",
    "然后", "现在", "今天", "明天", "昨天", "刚才", "刚刚", "马上",
    "吧", "啊", "呀", "哦", "嗯", "哈", "呢", "嘛", "啦", "诶",
    "上", "下", "里", "外", "中", "前", "后", "左", "右",
    "对", "错",
    # 通用泛词（聊天里被大量复读，盖过实词）
    "事情", "东西", "时候", "地方",
])

# 极简英文停用词表（小写匹配）。
_EN_STOP = frozenset([
    "the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "on", "at",
    "for", "with", "as", "by", "is", "am", "are", "was", "were", "be", "been",
    "being", "do", "does", "did", "doing", "have", "has", "had", "having",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their", "this", "that", "these", "those",
    "not", "no", "yes", "so", "very", "just", "really", "also", "too",
    "can", "could", "would", "should", "will", "shall", "may", "might",
    "what", "when", "where", "why", "how", "which", "who", "whom",
])


# 中文 + 英文 + 数字 + 空白 + 基本标点 -> 仅保留前三类（拆词时用）
_TEXT_KEEP_RE = re.compile(r"[一-鿿]+|[A-Za-z]+|\d+")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TurnEntry:
    """单条 turn 文本 + ts（learner 内部）。"""
    text: str
    ts: float


@dataclass
class PreferenceLearnerStats:
    updated_count: int = 0  # rebuild_for_profile 完整跑过的次数
    extracted_keywords_total: int = 0  # 累计写入 profile 的 keyword 总数（去重前）
    on_turn_count: int = 0
    persist_skipped_count: int = 0  # 未到 N / 缺 profile 等被跳过
    last_topk: int = 0
    last_input_turns: int = 0
    last_input_summaries: int = 0


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------


def _is_all_digit(s: str) -> bool:
    return bool(s) and all(c.isdigit() for c in s)


def _tokenize_chunk(chunk: str, *, min_len: int) -> List[str]:
    """对单个 ascii / chinese / digit 块切词：
    - 中文：连续切 bigram（i, i+1）。
    - 英文：整块作为 token；lower。
    - 数字：丢弃（纯数字一般不是兴趣关键词）。
    返回原始 token 列表（含 lower 后的英文，未过滤停用词）。
    """
    if not chunk:
        return []
    if _is_all_digit(chunk):
        return []
    # 中文
    if "一" <= chunk[0] <= "鿿":
        if len(chunk) < 2:
            return []
        out: List[str] = []
        for i in range(len(chunk) - 1):
            bg = chunk[i: i + 2]
            out.append(bg)
        return out
    # 英文
    if chunk[0].isalpha():
        tok = chunk.lower()
        if len(tok) < min_len:
            return []
        return [tok]
    return []


def tokenize(text: str, *, min_len: int = DEFAULT_MIN_TOKEN_LEN) -> List[str]:
    """把一段任意文本切成 token 列表（粗启发式，不去重）."""
    if not text:
        return []
    toks: List[str] = []
    for m in _TEXT_KEEP_RE.finditer(text):
        chunk = m.group(0)
        toks.extend(_tokenize_chunk(chunk, min_len=min_len))
    return toks


def _is_stop(tok: str) -> bool:
    if not tok:
        return True
    if tok in _ZH_STOP:
        return True
    if tok in _EN_STOP:
        return True
    return False


# ---------------------------------------------------------------------------
# Core learner
# ---------------------------------------------------------------------------


class PreferenceLearner:
    """从 dialog_memory + dialog_summary 抽取 TopK 偏好关键词。

    Parameters
    ----------
    topk
        输出 keyword 数上限（默认 10）。
    half_life_s
        时间衰减半衰期；默认 86400s = 24h。
        权重 ``w(Δt) = exp(-Δt * ln2 / half_life_s)``；半衰期处 w=0.5。
    min_token_len
        英文 token 最小长度（中文 bigram 固定 2）。默认 2。
    persist_every_n_turns
        on_turn 计数到该阈值时再写盘（节流）；0 关闭节流由 caller 自决。
    clock
        时间源（默认 time.time，便于 fake clock 测试）。
    """

    def __init__(
        self,
        *,
        topk: int = DEFAULT_TOPK,
        half_life_s: float = DEFAULT_HALF_LIFE_S,
        min_token_len: int = DEFAULT_MIN_TOKEN_LEN,
        persist_every_n_turns: int = DEFAULT_PERSIST_EVERY_N_TURNS,
        clock: Callable[[], float] = time.time,
        extra_stopwords: Optional[Iterable[str]] = None,
    ) -> None:
        if topk < 1:
            raise ValueError(f"topk 必须 ≥1，got {topk}")
        if half_life_s <= 0:
            raise ValueError(f"half_life_s 必须 >0，got {half_life_s}")
        self.topk = int(topk)
        self.half_life_s = float(half_life_s)
        self.min_token_len = max(1, int(min_token_len))
        self.persist_every_n_turns = max(0, int(persist_every_n_turns))
        self.clock = clock
        self._lock = threading.RLock()
        self.stats = PreferenceLearnerStats()
        self._extra_stop = frozenset(s.strip().lower() for s in (extra_stopwords or []) if s)
        # 自上次 rebuild 后累计的 on_turn 计数（用于 persist_every_n_turns 节流）
        self._pending_turns = 0

    # ---------------------------------------------------------------- helpers
    def _decay_weight(self, ts: float, *, now: float) -> float:
        if self.half_life_s <= 0:
            return 1.0
        dt = max(0.0, now - ts)
        # exp(-dt * ln2 / half_life)
        return math.exp(-dt * math.log(2.0) / self.half_life_s)

    def _filter_token(self, tok: str) -> bool:
        if _is_stop(tok):
            return False
        if tok in self._extra_stop:
            return False
        return True

    # ------------------------------------------------------------------ core
    def extract_keywords(
        self,
        entries: Sequence[TurnEntry],
        *,
        now: Optional[float] = None,
    ) -> Dict[str, float]:
        """从 entries 抽 TopK 关键词 → {keyword: normalized_weight}.

        - entries 每条带 ``ts``，weight 用 _decay_weight。
        - 同 token 多 turn 出现 → 累加 decayed weight。
        - 取 TopK 后做一次"子串合并"：短词被长词吸收。
        - 归一化：最大 weight 缩放到 1.0；其余按比例缩。
        - 输出按 weight 降序，dict 在 Python 3.7+ 保插入序。
        """
        if not entries:
            return {}
        if now is None:
            now = self.clock()

        scores: Dict[str, float] = {}
        for ent in entries:
            tokens = tokenize(ent.text, min_len=self.min_token_len)
            if not tokens:
                continue
            w = self._decay_weight(ent.ts, now=now)
            if w <= 0:
                continue
            for tok in tokens:
                if not self._filter_token(tok):
                    continue
                scores[tok] = scores.get(tok, 0.0) + w

        if not scores:
            return {}

        # 先取 TopK*2（留余量给子串合并），合并后再截 TopK
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        candidates = ranked[: max(self.topk * 2, self.topk)]
        merged = self._merge_substrings(dict(candidates))
        # 重新排序 + 截 TopK
        ranked2 = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[: self.topk]
        if not ranked2:
            return {}
        max_w = ranked2[0][1] or 1.0
        out: Dict[str, float] = {}
        for k, v in ranked2:
            out[k] = round(v / max_w, 6)
        return out

    @staticmethod
    def _merge_substrings(scores: Dict[str, float]) -> Dict[str, float]:
        """简单同义合并：若 A 是 B 的子串且 len(A) < len(B)，
        A 的权重加给 B，A 丢弃。仅做一轮，避免链式。"""
        if len(scores) < 2:
            return dict(scores)
        keys = list(scores.keys())
        # 按长度降序：长词优先吸收短词
        keys.sort(key=lambda k: len(k), reverse=True)
        absorbed: set = set()
        out: Dict[str, float] = dict(scores)
        for long in keys:
            if long in absorbed:
                continue
            for short in keys:
                if short == long or short in absorbed:
                    continue
                if len(short) >= len(long):
                    continue
                if short in long:
                    out[long] = out.get(long, 0.0) + out.get(short, 0.0)
                    out.pop(short, None)
                    absorbed.add(short)
        return out

    # ------------------------------------------------------ entries builders
    def build_entries_from_dialog_memory(
        self,
        dialog_memory: Any,
        *,
        now: Optional[float] = None,
    ) -> List[TurnEntry]:
        """从 DialogMemory 抽 recent_turns + summary → TurnEntry 列表。

        约定：recent_turns 全部按"现在"打 ts（无单 turn 时间戳源）；summary
        亦视作"现在"。这是有意的——衰减主要打 *跨会话* 旧 summary，而非 in-session。
        """
        if dialog_memory is None:
            return []
        if now is None:
            now = self.clock()
        out: List[TurnEntry] = []
        try:
            turns = dialog_memory.recent_turns()
        except Exception as e:  # noqa: BLE001
            log.warning("[preference_learner] dialog_memory.recent_turns failed: %s: %s",
                        type(e).__name__, e)
            turns = []
        for (u, a) in turns or []:
            # 跳过 [fallback] / 仅 [手势:xxx]（与 dialog_summary._skip_turn 同语义但本模块不依赖它）
            ut = (u or "").lstrip()
            if ut.startswith("[fallback]"):
                continue
            if ut.startswith("[手势:"):
                idx = ut.find("]")
                if idx >= 0 and not ut[idx + 1:].strip():
                    continue
            text = " ".join(filter(None, [u or "", a or ""])).strip()
            if text:
                out.append(TurnEntry(text=text, ts=now))
        # in-session summary（如果 interact-009 触发后有）
        try:
            s = getattr(dialog_memory, "summary", None)
            if s:
                out.append(TurnEntry(text=str(s), ts=now))
        except Exception:  # noqa: BLE001
            pass
        return out

    def build_entries_from_persisted(
        self,
        record: Any,
        *,
        now: Optional[float] = None,
    ) -> List[TurnEntry]:
        """从 PersistedProfile.dialog_summary 抽 TurnEntry。

        ``record.updated_ts`` 作为这些 summary 的统一 ts；缺失则用 now。
        """
        if record is None:
            return []
        if now is None:
            now = self.clock()
        ts = float(getattr(record, "updated_ts", 0.0) or now)
        if ts <= 0:
            ts = now
        out: List[TurnEntry] = []
        for s in (getattr(record, "dialog_summary", None) or []):
            if s:
                out.append(TurnEntry(text=str(s), ts=ts))
        return out

    # ---------------------------------------------- top-level rebuild + write
    def rebuild_for_profile(
        self,
        *,
        persist_store: Any,
        profile_id: str,
        dialog_memory: Any = None,
        now: Optional[float] = None,
    ) -> Optional[Dict[str, float]]:
        """从所有来源聚合 entries → extract → 写回 ``persist_store`` 的 record.prefer_topics。

        Returns
        -------
        新的 prefer_topics dict；若 record 不存在 / 失败返回 None。
        """
        if persist_store is None or not profile_id:
            with self._lock:
                self.stats.persist_skipped_count += 1
            return None
        if now is None:
            now = self.clock()
        try:
            rec = persist_store.load(profile_id)
        except Exception as e:  # noqa: BLE001
            log.warning("[preference_learner] persist.load(%s) failed: %s: %s",
                        profile_id, type(e).__name__, e)
            return None
        if rec is None:
            with self._lock:
                self.stats.persist_skipped_count += 1
            return None

        entries = list(self.build_entries_from_persisted(rec, now=now))
        n_summaries = len(entries)
        if dialog_memory is not None:
            entries.extend(self.build_entries_from_dialog_memory(dialog_memory, now=now))
        n_turns = len(entries) - n_summaries

        kw = self.extract_keywords(entries, now=now)

        # 把 dict 写回 record.prefer_topics（schema 已新增字段）
        try:
            setattr(rec, "prefer_topics", dict(kw))
        except Exception as e:  # noqa: BLE001
            log.warning("[preference_learner] set prefer_topics failed: %s: %s",
                        type(e).__name__, e)
            return None
        try:
            persist_store.save(rec)
        except Exception as e:  # noqa: BLE001
            log.warning("[preference_learner] persist.save(%s) failed: %s: %s",
                        profile_id, type(e).__name__, e)
            return None

        with self._lock:
            self.stats.updated_count += 1
            self.stats.extracted_keywords_total += len(kw)
            self.stats.last_topk = len(kw)
            self.stats.last_input_turns = n_turns
            self.stats.last_input_summaries = n_summaries
            self._pending_turns = 0
        return kw

    # ----------------------------------------------------- on_turn (lightweight)
    def on_turn(
        self,
        *,
        user_text: str = "",
        assistant_text: str = "",
    ) -> bool:
        """每轮对话末尾调一次（cheap），仅累计计数；返回 True 表示
        ``persist_every_n_turns`` 达到，**调用方该自行 trigger rebuild**。

        本方法故意不直接读 persist_store / dialog_memory（避免 hot-path 抓 lock + I/O）。
        """
        with self._lock:
            self.stats.on_turn_count += 1
            self._pending_turns += 1
            n = self.persist_every_n_turns
            if n <= 0:
                return False
            should = self._pending_turns >= n
            if should:
                # 不在这里重置 _pending_turns —— 由调用方 rebuild_for_profile 完成后重置
                pass
            return should

    def force_due(self) -> None:
        """让下一次 on_turn 直接返回 True（profile_switch 等场景）。"""
        with self._lock:
            self._pending_turns = max(self._pending_turns, self.persist_every_n_turns or 1)

    # --------------------------------------------------------------- helpers
    def reset_pending(self) -> None:
        """无 rebuild 走捷径完结一次窗口（用于 caller 决定不触发但要清计数）。"""
        with self._lock:
            self._pending_turns = 0


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def preference_learn_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    """``COCO_PREFER_LEARN=1`` 启用。默认 OFF（向后兼容 companion-008）。"""
    e = env if env is not None else os.environ
    return (e.get("COCO_PREFER_LEARN") or "0").strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Stub: LLM-backed preference backend (future work)
# ---------------------------------------------------------------------------


class LLMPreferenceBackend:
    """[FUTURE] LLM-based preference extraction backend.

    当前 companion-009 用启发式（bigram + 停用词 + 衰减），无新依赖。后续若需要
    更准的偏好抽取，可在这里接 LLM：让 LLM 读 dialog_summary，输出
    JSON ``{"prefer_topics": {"cooking": 0.9, "running": 0.4}}``，PreferenceLearner
    把 LLM 输出 merge 进启发式结果（保留 fail-soft）。

    本类目前为占位文档；未实现。
    """

    def extract(self, entries: Sequence[TurnEntry]) -> Dict[str, float]:  # pragma: no cover
        raise NotImplementedError("LLMPreferenceBackend 未实现；目前仅启发式")


__all__ = [
    "PreferenceLearner",
    "PreferenceLearnerStats",
    "TurnEntry",
    "DEFAULT_TOPK",
    "DEFAULT_HALF_LIFE_S",
    "DEFAULT_MIN_TOKEN_LEN",
    "DEFAULT_PERSIST_EVERY_N_TURNS",
    "preference_learn_enabled_from_env",
    "tokenize",
    "LLMPreferenceBackend",
]
