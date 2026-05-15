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

import hashlib
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


DEFAULT_TOPK = 10
DEFAULT_HALF_LIFE_S = 86400.0  # 24h
DEFAULT_MIN_TOKEN_LEN = 2
DEFAULT_PERSIST_EVERY_N_TURNS = 20


# ---------------------------------------------------------------------------
# companion-015: PreferenceLearner cross-process cache (per-profile topic→score)
# ---------------------------------------------------------------------------

# 与 vision-010 face_id_map persist 模式一致：版本化 schema + atomic write + warn-once。
# default-OFF：未设 COCO_PREFERENCE_PERSIST=1 时完全不读不写、不 emit、bytewise 等价。
_PREFERENCE_STATE_SCHEMA_VERSION = 1
_PREFERENCE_STATE_DEFAULT_PATH = "data/preference_learner_state.json"

# companion-016: emit `companion.preference_persisted` 节流参数。
# default 10s；env `COCO_PERSIST_EMIT_MIN_INTERVAL_S` 可覆盖；非法值 WARN once + fallback。
_PERSIST_EMIT_MIN_INTERVAL_S_DEFAULT = 10.0
_PERSIST_EMIT_INTERVAL_ENV = "COCO_PERSIST_EMIT_MIN_INTERVAL_S"
_PERSIST_EMIT_INTERVAL_WARN_ONCE = False  # module-level，进程内 warn once


def preference_persist_emit_min_interval_s_from_env(
    env: Optional[Mapping[str, str]] = None,
) -> float:
    """companion-016: 读 `COCO_PERSIST_EMIT_MIN_INTERVAL_S`；非法值 WARN once + fallback default。

    合法：非负 float（包含 0）。"-1" / "abc" / "" 等非法 → default 10.0 + WARN once。
    """
    global _PERSIST_EMIT_INTERVAL_WARN_ONCE
    e = env if env is not None else os.environ
    raw = e.get(_PERSIST_EMIT_INTERVAL_ENV)
    if raw is None or raw == "":
        return _PERSIST_EMIT_MIN_INTERVAL_S_DEFAULT
    try:
        v = float(raw)
        if v < 0 or math.isnan(v) or math.isinf(v):
            raise ValueError(f"out-of-range: {v!r}")
        return v
    except (TypeError, ValueError) as exc:
        if not _PERSIST_EMIT_INTERVAL_WARN_ONCE:
            log.warning(
                "[preference_learner] %s=%r invalid (%s) -> fallback default %.1fs",
                _PERSIST_EMIT_INTERVAL_ENV, raw, exc, _PERSIST_EMIT_MIN_INTERVAL_S_DEFAULT,
            )
            _PERSIST_EMIT_INTERVAL_WARN_ONCE = True
        return _PERSIST_EMIT_MIN_INTERVAL_S_DEFAULT


def _hash_preference_state(profiles: Mapping[str, Mapping[str, float]]) -> str:
    """companion-016: 稳定 hash（sorted keys + JSON），用于 content dedup。

    同一份 profiles dict（无论插入顺序）→ 同 hash。空 dict → 稳定 hash（非空字符串）。
    """
    canon = {
        pid: {t: round(float(s), 6) for t, s in sorted(topics.items())}
        for pid, topics in sorted(profiles.items())
    }
    blob = json.dumps(canon, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def preference_persist_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    """companion-015: ``COCO_PREFERENCE_PERSIST=1`` → True；default-OFF 等价。"""
    e = env if env is not None else os.environ
    return (e.get("COCO_PREFERENCE_PERSIST") or "0").strip().lower() in {"1", "true", "yes", "on"}


def preference_persist_path_from_env(env: Optional[Mapping[str, str]] = None) -> Path:
    """companion-015: ``COCO_PREFERENCE_PATH`` 覆盖默认 cache 路径。"""
    e = env if env is not None else os.environ
    return Path(e.get("COCO_PREFERENCE_PATH") or _PREFERENCE_STATE_DEFAULT_PATH)


def _load_preference_state(path: Path) -> Dict[str, Dict[str, float]]:
    """读取 preference cache；schema 不匹配 / 损坏 / 缺失 → 空 dict + warn-once.

    Returns: ``{profile_id: {topic: score, ...}, ...}``
    """
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("top-level not object")
        if data.get("version") != _PREFERENCE_STATE_SCHEMA_VERSION:
            raise ValueError(f"schema version mismatch: {data.get('version')!r}")
        profiles = data.get("profiles", {})
        if not isinstance(profiles, dict):
            raise ValueError("profiles not object")
        out: Dict[str, Dict[str, float]] = {}
        for pid, topics in profiles.items():
            if not isinstance(pid, str) or not isinstance(topics, dict):
                continue
            cleaned: Dict[str, float] = {}
            for t, s in topics.items():
                if isinstance(t, str):
                    try:
                        cleaned[t] = float(s)
                    except (TypeError, ValueError):
                        continue
            out[pid] = cleaned
        return out
    except Exception as e:  # noqa: BLE001
        log.warning(
            "PreferenceLearner state hydrate failed (path=%s): %s: %s -> empty state",
            path, type(e).__name__, e,
        )
        return {}


def _atomic_write_preference_state(
    path: Path,
    profiles: Dict[str, Dict[str, float]],
) -> None:
    """atomic write: tmp + rename，避免半写文件污染下次 hydrate。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _PREFERENCE_STATE_SCHEMA_VERSION,
        "saved_at": time.time(),
        "profiles": {
            pid: {t: float(s) for t, s in sorted(topics.items())}
            for pid, topics in sorted(profiles.items())
        },
    }
    fd, tmp_path = tempfile.mkstemp(
        prefix=".preference_state.", suffix=".json.tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

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
        emit_fn: Optional[Callable[..., None]] = None,
        # companion-015: 跨进程 state cache（默认 None；env 启用时由 main 注入）
        state_cache_path: Optional[Path] = None,
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
        # infra-009 / companion-009 L2-2：异步 rebuild executor（懒构造，单 worker）；
        # 主回调线程把 rebuild_for_profile_async 提交进来，不被 fsync 阻塞。
        self._executor: Optional[ThreadPoolExecutor] = None
        # companion-014: 真 emit `companion.preference_updated` 事件（schema 见 rebuild_for_profile）。
        # default-OFF：emit_fn 为 None 时完全 no-op，行为与 companion-009 bytewise 等价。
        self._emit_fn: Optional[Callable[..., None]] = emit_fn
        # companion-015: cross-process state cache（per-profile topic→score）。
        # default-OFF：state_cache_path 为 None 时完全不读不写、不 emit、不做任何 IO，
        # 与 companion-014 bytewise 等价。
        self._state_cache_path: Optional[Path] = state_cache_path
        self._state_cache: Dict[str, Dict[str, float]] = {}
        # warn-once 节流（emit `companion.preference_persisted` 仅 lock-once 节流见下方 _emit_persisted_once）
        self._persisted_emit_lock = threading.Lock()
        # companion-016: emit `companion.preference_persisted` 真节流状态
        # （min_interval_s + content-hash 双门；suppressed_since_last 累计被节流次数）。
        # default-OFF：state_cache_path is None 时整段逻辑不触发（_emit_persisted_once 早返回）。
        self._persist_emit_min_interval_s: float = (
            preference_persist_emit_min_interval_s_from_env()
        )
        self._persist_emit_last_ts: float = 0.0
        self._persist_emit_last_hash: str = ""
        self._persist_emit_suppressed_n: int = 0
        if self._state_cache_path is not None:
            try:
                self._state_cache = _load_preference_state(self._state_cache_path)
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "[preference_learner] state hydrate failed (path=%s): %s: %s -> empty",
                    self._state_cache_path, type(e).__name__, e,
                )
                self._state_cache = {}
            # emit 一条 load 事件（即便 cache 为空，也宣告 hydrate 已发生）
            self._emit_persisted_once(action="load")

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

        # companion-014: 计算 delta（去抖：仅当真发生变化才 emit）。
        # 旧值取自 record 既有 prefer_topics（dict 或 None）。
        try:
            prev_kw = dict(getattr(rec, "prefer_topics", None) or {})
        except Exception:  # noqa: BLE001
            prev_kw = {}

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

        # companion-014: 真 emit `companion.preference_updated` 事件（去抖）。
        # default-OFF：_emit_fn 为 None 时完全 no-op；亦在 prev==new 时跳过 emit。
        if self._emit_fn is not None:
            try:
                self._maybe_emit_preference_updated(
                    profile_id=profile_id,
                    prev_kw=prev_kw,
                    new_kw=kw,
                )
            except Exception as e:  # noqa: BLE001
                # emit 不能影响 rebuild 主路径
                log.warning("[preference_learner] emit preference_updated failed: %s: %s",
                            type(e).__name__, e)

        with self._lock:
            self.stats.updated_count += 1
            self.stats.extracted_keywords_total += len(kw)
            self.stats.last_topk = len(kw)
            self.stats.last_input_turns = n_turns
            self.stats.last_input_summaries = n_summaries
            self._pending_turns = 0
            # companion-015: 同步更新 in-memory state cache（default-OFF 时 path is None 跳过 flush）
            if self._state_cache_path is not None:
                self._state_cache[profile_id] = dict(kw)
        # flush 在 lock 外（avoid 在持锁期间 fsync）
        if self._state_cache_path is not None:
            self.flush_state()
        return kw

    # companion-014: 计算 prev/new 的 delta，按 topic 维度 emit。
    # schema：每个发生变化的 topic（包括新增/移除/分数变更）发一条
    # `companion.preference_updated`，payload 含 topic / delta / new_score / old_score / profile_id。
    def _maybe_emit_preference_updated(
        self,
        *,
        profile_id: str,
        prev_kw: Dict[str, float],
        new_kw: Dict[str, float],
    ) -> None:
        if self._emit_fn is None:
            return
        if prev_kw == new_kw:
            # 去抖：完全相同（含 key 集合 + 分数）则不 emit
            return
        keys = set(prev_kw.keys()) | set(new_kw.keys())
        for topic in sorted(keys):
            old = float(prev_kw.get(topic, 0.0))
            new = float(new_kw.get(topic, 0.0))
            if abs(new - old) < 1e-6:
                continue
            try:
                self._emit_fn(
                    "companion.preference_updated",
                    f"preference_updated topic={topic} delta={new - old:+.4f} new_score={new:.4f}",
                    topic=topic,
                    delta=round(new - old, 6),
                    new_score=round(new, 6),
                    old_score=round(old, 6),
                    profile_id=profile_id,
                )
            except Exception as e:  # noqa: BLE001
                # 单 topic emit 失败不影响其他
                log.warning("[preference_learner] emit per-topic failed: %s: %s",
                            type(e).__name__, e)

    # infra-009 / companion-009 L2-2：异步版本，main 主回调线程提交后立即返回
    # Future，不被 persist_store.save 的 fsync 阻塞。
    def rebuild_for_profile_async(
        self,
        *,
        persist_store: Any,
        profile_id: str,
        dialog_memory: Any = None,
        now: Optional[float] = None,
    ) -> "Future[Optional[Dict[str, float]]]":
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="coco-pref-learner",
                )
            ex = self._executor
        return ex.submit(
            self.rebuild_for_profile,
            persist_store=persist_store,
            profile_id=profile_id,
            dialog_memory=dialog_memory,
            now=now,
        )

    def shutdown_executor(self, wait: bool = True) -> None:
        """关闭后台 executor（main 退出时调；test 末尾用）。"""
        with self._lock:
            ex = self._executor
            self._executor = None
        if ex is not None:
            ex.shutdown(wait=wait)

    # ----------------------------------------------------- companion-015: state cache
    def _emit_persisted_once(self, *, action: str) -> None:
        """emit `companion.preference_persisted`，companion-016 真节流双门：

        - **min_interval_s**：距上次成功 emit 不足 `min_interval_s` → 抑制（计入 suppressed_n）。
        - **content-hash dedup**：当前 `_state_cache` 与上次成功 emit 的 hash 相同 → 抑制
          （即便已超 interval；状态没真变化就不噪声）。
        - 抑制时累计 `_persist_emit_suppressed_n`；下一次成功 emit 时携带
          `suppressed_since_last=N` 字段然后清零。

        例外：`action="load"` 是 hydrate 公告，强制首发（绕过双门），且 **不消耗** save 的
        interval/hash anchor，避免 load 阻塞下一次真 save emit。
        default-OFF：_emit_fn / state_cache_path 未配置时 no-op。
        """
        if self._emit_fn is None or self._state_cache_path is None:
            return
        with self._persisted_emit_lock:
            try:
                now = self.clock()
                with self._lock:
                    snapshot = {pid: dict(v) for pid, v in self._state_cache.items()}
                cur_hash = _hash_preference_state(snapshot)
                pc = len(snapshot)
                tc = sum(len(v) for v in snapshot.values())

                force = (action == "load")
                interval_ok = (now - self._persist_emit_last_ts) >= self._persist_emit_min_interval_s
                hash_changed = (cur_hash != self._persist_emit_last_hash)

                if not force and not (interval_ok and hash_changed):
                    self._persist_emit_suppressed_n += 1
                    return

                suppressed_since_last = self._persist_emit_suppressed_n
                self._emit_fn(
                    "companion.preference_persisted",
                    f"preference_persisted action={action} profiles={pc} topics={tc} "
                    f"suppressed_since_last={suppressed_since_last}",
                    action=action,
                    profile_count=pc,
                    topic_count=tc,
                    suppressed_since_last=suppressed_since_last,
                    ts=now,
                )
                if not force:
                    self._persist_emit_last_ts = now
                    self._persist_emit_last_hash = cur_hash
                    self._persist_emit_suppressed_n = 0
            except Exception as e:  # noqa: BLE001
                log.warning("[preference_learner] emit preference_persisted failed: %s: %s",
                            type(e).__name__, e)

    def get_cached_topics(self, profile_id: str) -> Dict[str, float]:
        """读取 in-memory state cache（不触发 IO）。default-OFF 时仍返回空 dict。"""
        with self._lock:
            return dict(self._state_cache.get(profile_id, {}))

    def flush_state(self) -> bool:
        """把 _state_cache atomic 写盘。default-OFF 返回 False；启用时成功 True / 失败 False。"""
        if self._state_cache_path is None:
            return False
        try:
            with self._lock:
                snapshot = {pid: dict(v) for pid, v in self._state_cache.items()}
            _atomic_write_preference_state(self._state_cache_path, snapshot)
        except Exception as e:  # noqa: BLE001
            log.warning("[preference_learner] flush state failed (path=%s): %s: %s",
                        self._state_cache_path, type(e).__name__, e)
            return False
        # emit save 事件（lock-once 节流）
        self._emit_persisted_once(action="save")
        return True

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


def async_rebuild_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    """companion-014: ``COCO_COMPANION_ASYNC_REBUILD=1`` 时
    ``_on_interaction_combined`` 主回调线程改用 ``rebuild_for_profile_async``，
    避免被 persist_store.save 的 fsync 阻塞。default-OFF：维持同步行为，
    与 companion-009 bytewise 等价。"""
    e = env if env is not None else os.environ
    return (e.get("COCO_COMPANION_ASYNC_REBUILD") or "0").strip().lower() in {"1", "true", "yes", "on"}


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
    "async_rebuild_enabled_from_env",
    "preference_persist_enabled_from_env",
    "preference_persist_path_from_env",
    "preference_persist_emit_min_interval_s_from_env",
    "tokenize",
    "LLMPreferenceBackend",
]
