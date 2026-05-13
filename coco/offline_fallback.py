"""coco.offline_fallback — 离线降级回路 (interact-011).

设计原则
========

- **默认 OFF**（``COCO_OFFLINE_FALLBACK=1`` 才启用），保守降级，避免误触发污染对话上下文
- **失败计数语义**：连续失败 ``threshold`` 次（默认 3）→ 切 fallback；任一次真成功
  → 立即清零并退出 fallback。"短抖动"（失败 1-2 次后又成功）不切。
- **判定"真成功"**：通过包装 ``LLMClient.reply``，对比 ``stats.backend_ok`` 增量。
  注意 LLMClient.reply 内部已经 try/except 不抛了，所以"backend 失败但 keyword 兜底成功"
  仍算失败（不是真 LLM 成功）。
- **fallback utterance**：模板池轮换（中文），可引用 ``dialog_memory`` 最近 1 轮 user
  原文做"刚才"引用；不调 LLM。
- **dialog_memory 标记**：user_text 加 ``[fallback]`` 前缀（与 interact-010 ``[手势:xxx]``
  统一约定）。下游 summarizer / profile extractor 见到前缀跳过该轮。
- **ProactiveScheduler 暂停**：fallback 切入时 ``pause()``，恢复时 ``resume()``。
- **fail-soft**：模板渲染 / 钩子异常一律吞掉，不 crash session。

线程模型
========

- 失败计数 / 模式切换在 ``InteractSession.handle_audio`` 同一线程里读写（串行）
- 内置 ``RLock`` 保护 enter/exit 与计数器，允许 ProactiveScheduler 线程并发读
- 探活：fallback 期间下一次用户开口仍走 LLM 一次，成功即退；不另起后台线程

公开 API
========

- ``OfflineFallbackConfig`` dataclass
- ``offline_fallback_enabled_from_env() / config_from_env()``
- ``OfflineDialogFallback`` 主类
    * ``wrap_llm_reply(llm_client) -> callable``: 把 ``LLMClient.reply`` 包成
      InteractSession 期望的 ``llm_reply_fn(text, **kwargs) -> str``
    * ``compose_fallback_reply(transcript) -> str``: 渲染一句 fallback 文本
    * ``is_in_fallback() -> bool``
    * ``failure_count() -> int``
- ``USER_FALLBACK_TAG = "[fallback]"``: 前缀常量，dialog_memory.append 时用
- ``is_fallback_user_text(text) -> bool``: summarizer / profile extractor 判定 helper
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Mapping, Optional, Tuple


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tag 约定（与 interact-010 [手势:xxx] 风格统一）
# ---------------------------------------------------------------------------

USER_FALLBACK_TAG = "[fallback]"
"""dialog_memory.append 时给 user_text 加的前缀，summarizer / profile 据此跳过。"""


def is_fallback_user_text(text: Optional[str]) -> bool:
    """判断一段 user_text 是否是 fallback 模式产出。

    与 ``[手势:xxx]`` 不同，``[fallback]`` 不带参数，直接前缀匹配（空格 / 无空格都识别）。
    """
    if not text:
        return False
    t = text.lstrip()
    return t.startswith(USER_FALLBACK_TAG)


# ---------------------------------------------------------------------------
# 模板池
# ---------------------------------------------------------------------------

DEFAULT_TEMPLATES: Tuple[str, ...] = (
    "我现在有点连不上网，等一下再聊好吗？",
    "嗯，我先听你说，外面信号好像不太顺。",
    "网络好像不太顺，我先记下来，等会儿再细聊。",
    "我有点反应不过来，先停一停好不好？",
    "我们刚才聊到 {recent_topic} 了对吧？我先记着。",
    "我先听着，等连上了我们继续。",
)
"""默认 fallback 模板池（中文）。其中 ``{recent_topic}`` 会被替换为最近 1 轮 user 摘要片段。

注意：含 ``{recent_topic}`` 的模板在没有 recent context 时会跳过（避免渲染 "聊到 了"）。
"""

RECOVERY_UTTERANCE = "我回来了。"
"""退出 fallback 时机器人主动说的话。"""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class OfflineFallbackConfig:
    enabled: bool = False
    fail_threshold: int = 3
    templates: Tuple[str, ...] = DEFAULT_TEMPLATES
    recovery_utterance: str = RECOVERY_UTTERANCE
    # 引用最近 1 轮 user 的最大字符数（避免冗长 transcript 拼进模板）
    recent_topic_max_chars: int = 12
    # fallback 模式下探活间隔（秒）：避免每轮都吃 LLM 超时
    probe_interval_s: float = 20.0


def offline_fallback_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    """``COCO_OFFLINE_FALLBACK=1`` 启用。默认 OFF。"""
    e = env if env is not None else os.environ
    return (e.get("COCO_OFFLINE_FALLBACK") or "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


def config_from_env(env: Optional[Mapping[str, str]] = None) -> OfflineFallbackConfig:
    e = env if env is not None else os.environ
    enabled = offline_fallback_enabled_from_env(e)
    raw_th = (e.get("COCO_OFFLINE_FALLBACK_THRESHOLD") or "3").strip()
    try:
        th = int(raw_th)
    except ValueError:
        log.warning(
            "[offline_fallback] COCO_OFFLINE_FALLBACK_THRESHOLD=%r 非整数，回退 3",
            raw_th,
        )
        th = 3
    if th < 1:
        log.warning("[offline_fallback] threshold=%d <1，clamp 到 1", th)
        th = 1
    if th > 20:
        log.warning("[offline_fallback] threshold=%d >20，clamp 到 20", th)
        th = 20
    return OfflineFallbackConfig(enabled=enabled, fail_threshold=th)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class OfflineFallbackStats:
    llm_calls: int = 0
    llm_real_ok: int = 0
    llm_failures: int = 0
    consecutive_failures: int = 0
    entries: int = 0      # 共进入 fallback 几次
    recoveries: int = 0   # 共恢复几次
    utterances: int = 0   # fallback utterance 渲染次数
    template_index: int = 0  # 模板轮换游标
    last_failure_latency_ms: float = 0.0
    last_recovery_latency_ms: float = 0.0


# ---------------------------------------------------------------------------
# OfflineDialogFallback
# ---------------------------------------------------------------------------


class OfflineDialogFallback:
    """连续 LLM 失败计数 + fallback 模板 + ProactiveScheduler 暂停协调。

    生命周期：
        f = OfflineDialogFallback(cfg, proactive_scheduler=p, emit_fn=emit,
                                  tts_say_fn=tts.say, dialog_memory_ref=lambda: dm)
        wrapped = f.wrap_llm_reply(llm_client)
        # 把 wrapped 传给 InteractSession(llm_reply_fn=wrapped)
        # 把 f 自身也传给 InteractSession(offline_fallback=f) 让其感知 fallback 模式
    """

    def __init__(
        self,
        config: Optional[OfflineFallbackConfig] = None,
        *,
        proactive_scheduler: Any = None,
        emit_fn: Optional[Callable[..., None]] = None,
        tts_say_fn: Optional[Callable[..., None]] = None,
        dialog_memory_ref: Optional[Callable[[], Any]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.config = config or OfflineFallbackConfig()
        self.proactive_scheduler = proactive_scheduler
        self._emit = emit_fn
        self.tts_say_fn = tts_say_fn
        # dialog_memory_ref：lazy 引用，避免循环依赖；返回 DialogMemory 或 None
        self._dm_ref = dialog_memory_ref
        self.clock = clock or time.monotonic

        self.stats = OfflineFallbackStats()
        self._lock = threading.RLock()
        self._in_fallback: bool = False
        self._last_probe_ts: float = 0.0

    # ------------------------------------------------------------------
    # 内省
    # ------------------------------------------------------------------

    def is_in_fallback(self) -> bool:
        with self._lock:
            return self._in_fallback

    def failure_count(self) -> int:
        with self._lock:
            return self.stats.consecutive_failures

    def is_enabled(self) -> bool:
        return bool(self.config.enabled)

    # ------------------------------------------------------------------
    # 状态切换（内部调）
    # ------------------------------------------------------------------

    def _emit_safe(self, event: str, **payload: Any) -> None:
        try:
            emit_fn = self._emit
            if emit_fn is None:
                from coco.logging_setup import emit as _emit  # local import
                emit_fn = _emit
            emit_fn(event, **payload)
        except Exception as e:  # noqa: BLE001
            log.warning("[offline_fallback] emit %s failed: %s: %s",
                        event, type(e).__name__, e)

    def _enter_fallback(self, *, latency_ms: float = 0.0) -> None:
        """切入 fallback 模式：pause proactive + emit。"""
        with self._lock:
            if self._in_fallback:
                return
            self._in_fallback = True
            self.stats.entries += 1
            fail_count = self.stats.consecutive_failures
        # 调 proactive.pause（锁外，避免与 proactive 自己的 lock 嵌套）
        if self.proactive_scheduler is not None:
            try:
                if hasattr(self.proactive_scheduler, "pause"):
                    self.proactive_scheduler.pause(source="offline_fallback")
            except Exception as e:  # noqa: BLE001
                log.warning("[offline_fallback] proactive.pause failed: %s: %s",
                            type(e).__name__, e)
        self._emit_safe(
            "interact.offline_entered",
            failure_count=fail_count,
            latency_ms=round(latency_ms, 2),
        )
        log.info("[offline_fallback] entered fallback (consec_fail=%d)", fail_count)

    def _exit_fallback(self, *, latency_ms: float = 0.0) -> None:
        """退出 fallback：resume proactive + emit + 主动说 '我回来了'。"""
        with self._lock:
            if not self._in_fallback:
                return
            self._in_fallback = False
            self.stats.recoveries += 1
            self.stats.last_recovery_latency_ms = float(latency_ms)
        # proactive.resume（锁外）
        if self.proactive_scheduler is not None:
            try:
                if hasattr(self.proactive_scheduler, "resume"):
                    self.proactive_scheduler.resume(source="offline_fallback")
            except Exception as e:  # noqa: BLE001
                log.warning("[offline_fallback] proactive.resume failed: %s: %s",
                            type(e).__name__, e)
        self._emit_safe(
            "interact.offline_recovered",
            latency_ms=round(latency_ms, 2),
        )
        log.info("[offline_fallback] recovered after fallback")
        # 主动说 "我回来了"（fail-soft）
        if self.tts_say_fn is not None and self.config.recovery_utterance:
            try:
                self.tts_say_fn(self.config.recovery_utterance, blocking=True)
            except Exception as e:  # noqa: BLE001
                log.warning("[offline_fallback] recovery TTS failed: %s: %s",
                            type(e).__name__, e)

    # ------------------------------------------------------------------
    # 包装 LLMClient.reply
    # ------------------------------------------------------------------

    def wrap_llm_reply(self, llm_client: Any) -> Callable[..., str]:
        """把 ``llm_client.reply`` 包成可注入 InteractSession 的函数。

        语义：
        - enabled=False → 直接转发 llm_client.reply（透明）
        - enabled=True →
            * 每次调用前记录 ``llm_client.stats.backend_ok``
            * 调用 ``llm_client.reply(...)`` 拿 reply（永不抛 —— LLMClient 内部已 try/except）
            * 调用后看 backend_ok 是否 +1：
                + +1 → 真成功：清零 consecutive_failures；若在 fallback → exit
                + 未涨 → 真失败：consecutive_failures += 1；若 >= threshold 且未在 fallback → enter
            * 在 fallback 期间不阻断 LLM 调用（每次都试，让恢复有机会发生）；
              这避免 "fallback 黑洞"——一旦切入就再也回不去。
        """
        if not self.config.enabled:
            # 透明：env=0 时与今天一致
            return llm_client.reply  # type: ignore[no-any-return]

        # closure 捕获 self / llm_client
        def _wrapped(text: str, **kwargs: Any) -> str:
            t0 = self.clock()
            # fallback 模式下做探活节流：距上次真调 < probe_interval_s 时跳过实际调用
            with self._lock:
                in_fb = self._in_fallback
                last_probe = self._last_probe_ts
                probe_int = self.config.probe_interval_s
            if in_fb and probe_int > 0 and (t0 - last_probe) < probe_int:
                # 不消耗 LLM 超时；返回空让 InteractSession 用模板
                # 失败计数不增（不算"一次失败"——只是没调）
                return ""
            try:
                prev_ok = int(getattr(llm_client.stats, "backend_ok", 0))
            except Exception:  # noqa: BLE001
                prev_ok = 0
            # 真调
            try:
                reply = llm_client.reply(text, **kwargs)
            except Exception as e:  # noqa: BLE001
                # LLMClient.reply 不该抛，但保险起见 catch
                log.warning("[offline_fallback] llm_client.reply 异常: %s: %s",
                            type(e).__name__, e)
                reply = ""
            dt_ms = (self.clock() - t0) * 1000.0
            with self._lock:
                self._last_probe_ts = self.clock()

            try:
                cur_ok = int(getattr(llm_client.stats, "backend_ok", 0))
            except Exception:  # noqa: BLE001
                cur_ok = prev_ok

            real_ok = cur_ok > prev_ok

            with self._lock:
                self.stats.llm_calls += 1
                if real_ok:
                    self.stats.llm_real_ok += 1
                    self.stats.consecutive_failures = 0
                    should_exit = self._in_fallback
                    should_enter = False
                else:
                    self.stats.llm_failures += 1
                    self.stats.consecutive_failures += 1
                    self.stats.last_failure_latency_ms = float(dt_ms)
                    should_enter = (
                        not self._in_fallback
                        and self.stats.consecutive_failures >= self.config.fail_threshold
                    )
                    should_exit = False

            if should_exit:
                self._exit_fallback(latency_ms=dt_ms)
            elif should_enter:
                self._enter_fallback(latency_ms=dt_ms)

            return reply

        # 转发签名探测属性（让 InteractSession 的 _probe_kwarg 看到 **kwargs）
        # 用 **kwargs 通配，所以 _probe_kwarg 走 VAR_KEYWORD 分支返回 True
        return _wrapped

    # ------------------------------------------------------------------
    # 渲染 fallback utterance
    # ------------------------------------------------------------------

    def _recent_user_topic(self) -> str:
        """从 dialog_memory 取最近 1 轮 user 原文，截断到 N 字符。

        跳过 ``[fallback]`` / ``[手势:xxx]`` 前缀的轮（找上一条"正常"的 user）。
        """
        if self._dm_ref is None:
            return ""
        try:
            dm = self._dm_ref()
        except Exception:  # noqa: BLE001
            return ""
        if dm is None or not hasattr(dm, "recent_turns"):
            return ""
        try:
            turns = list(dm.recent_turns())
        except Exception:  # noqa: BLE001
            return ""
        for u, _a in reversed(turns):
            if not u:
                continue
            if is_fallback_user_text(u):
                continue
            if u.lstrip().startswith("[手势:"):
                # 找 "] " 之后的真实内容
                rest = u.split("] ", 1)
                u = rest[1] if len(rest) == 2 else u
            u = u.strip()
            if not u:
                continue
            if len(u) > self.config.recent_topic_max_chars:
                u = u[: self.config.recent_topic_max_chars] + "…"
            return u
        return ""

    def compose_fallback_reply(self, _transcript: str = "") -> str:
        """从模板池选一句 fallback utterance。

        - 模板池轮换（按 ``stats.template_index``），避免连续重复扰民
        - 含 ``{recent_topic}`` 的模板：若 recent_user_topic 为空，跳到下一个模板
        - 永远返回非空字符串（最差 "嗯。"）
        """
        templates = self.config.templates or DEFAULT_TEMPLATES
        if not templates:
            return "嗯。"
        recent = self._recent_user_topic()

        # 至多遍历一圈
        n = len(templates)
        with self._lock:
            start = self.stats.template_index % n
        chosen = ""
        for offset in range(n):
            idx = (start + offset) % n
            tpl = templates[idx]
            if "{recent_topic}" in tpl:
                if not recent:
                    continue
                try:
                    chosen = tpl.format(recent_topic=recent)
                except Exception:  # noqa: BLE001
                    continue
            else:
                chosen = tpl
            if chosen:
                with self._lock:
                    self.stats.template_index = (idx + 1) % n
                    self.stats.utterances += 1
                self._emit_safe(
                    "interact.fallback_uttered",
                    template_id=idx,
                    text=chosen[:120],
                    has_recent_ref=("{recent_topic}" in tpl),
                )
                return chosen

        # 全部模板都因 recent_topic 缺失被跳过 → 退化到第一个不带占位符的
        for idx, tpl in enumerate(templates):
            if "{recent_topic}" not in tpl:
                with self._lock:
                    self.stats.template_index = (idx + 1) % n
                    self.stats.utterances += 1
                self._emit_safe(
                    "interact.fallback_uttered",
                    template_id=idx,
                    text=tpl[:120],
                    has_recent_ref=False,
                )
                return tpl
        return "嗯。"


__all__ = [
    "OfflineFallbackConfig",
    "OfflineFallbackStats",
    "OfflineDialogFallback",
    "offline_fallback_enabled_from_env",
    "config_from_env",
    "DEFAULT_TEMPLATES",
    "RECOVERY_UTTERANCE",
    "USER_FALLBACK_TAG",
    "is_fallback_user_text",
]
