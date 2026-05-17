"""coco.proactive — 主动话题发起 (interact-007).

设计目标
========

ACTIVE 状态下，机器人看到人脸 + 一段时间没人说话时，自动发起一个轻量话题，
用 UserProfile 注入 system_prompt 偏向用户兴趣/学习目标，让陪伴感不只是被动应答。

触发条件（必须 ALL 满足才发）
----------------------------

1. ``COCO_PROACTIVE=1`` 启用（默认 OFF，向后兼容 phase-3/4 全部测试不变）
2. ``power_state.current_state == ACTIVE``（DROWSY/SLEEP 不主动）
3. ``face_tracker.latest().present`` 为 True（视野里有人）
4. 距上次 ``InteractSession`` 交互（``on_interaction``）已超过 ``idle_threshold_s``
5. 距上次主动话题已超过 ``cooldown_s``
6. 最近 1h 内主动话题次数 < ``max_topics_per_hour``

环境变量
--------

- ``COCO_PROACTIVE``: 主开关，默认 OFF
- ``COCO_PROACTIVE_IDLE_S``: 触发阈值，默认 60.0，clamp [10, 3600]
- ``COCO_PROACTIVE_COOLDOWN_S``: 主动话题间冷却，默认 180.0，clamp [10, 7200]
- ``COCO_PROACTIVE_MAX_PER_HOUR``: 限流，默认 10，clamp [1, 60]
- ``COCO_PROACTIVE_TICK_S``: scheduler 心跳，默认 1.0

线程模型
--------

- ``ProactiveScheduler.start(stop_event)``: 起一个 daemon 线程，``tick_s`` 周期检查
- 触发后调用 ``llm_client.reply(prompt, system_prompt=...)`` + ``tts_say_fn(text)``
- LLM/TTS 异常一律吞掉（fail-soft），不让线程崩；写 stats.errors
- 触发后调 ``on_proactive(reply)`` 钩子（默认指向 ``power_state.record_interaction``）
  避免主动话题刚发完又被自己当 idle 立即重发

不破坏 default-OFF
------------------

- ``COCO_PROACTIVE=0``（默认）→ ``main.py`` 不构造 scheduler，零开销
- 构造后即使没注入 face_tracker / power_state，``_should_trigger`` 也会因约束不满足
  返回 False，不会乱发
"""

from __future__ import annotations

import collections
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Sequence


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults / config
# ---------------------------------------------------------------------------


DEFAULT_IDLE_S = 60.0
DEFAULT_COOLDOWN_S = 180.0
DEFAULT_MAX_PER_HOUR = 10
DEFAULT_TICK_S = 1.0

# 用于 LLM 提示的"prompt"种子；真正人格/兴趣由 system_prompt 注入
DEFAULT_TOPIC_SEED = "用一句温柔好奇的话主动开个话题，可以问对方在做什么或聊一个轻松的小事。"


@dataclass(frozen=True)
class ProactiveConfig:
    enabled: bool = False
    idle_threshold_s: float = DEFAULT_IDLE_S
    cooldown_s: float = DEFAULT_COOLDOWN_S
    max_topics_per_hour: int = DEFAULT_MAX_PER_HOUR
    tick_s: float = DEFAULT_TICK_S
    topic_seed: str = DEFAULT_TOPIC_SEED


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class ProactiveStats:
    ticks: int = 0
    triggered: int = 0
    skipped_disabled: int = 0
    skipped_power: int = 0
    skipped_no_face: int = 0
    skipped_idle: int = 0
    skipped_cooldown: int = 0
    skipped_rate_limit: int = 0
    skipped_quiet_state: int = 0
    skipped_paused: int = 0
    llm_errors: int = 0
    tts_errors: int = 0
    # vision-006: scene caption 作为外部触发源时的累计
    caption_proactive: int = 0
    # vision-007: multimodal fusion 触发记账
    mm_triggered: int = 0
    mm_per_rule: dict = field(default_factory=dict)
    # interact-012: MM proactive LLM 化（COCO_MM_PROACTIVE_LLM=1）
    # - mm_llm_proactive_count: ON 且 LLM 成功生成后递增
    # - mm_llm_errors: ON 但 LLM 失败 / 异常的次数
    # - mm_llm_fallback_offline: 离线 fallback 期间退化模板的次数
    mm_llm_proactive_count: int = 0
    mm_llm_errors: int = 0
    mm_llm_fallback_offline: int = 0
    # companion-009: 偏好加权选 topic 命中计数
    prefer_weighted_select_count: int = 0
    # companion-010: 情绪记忆触发的 alert 计数 + 按 kind 拆分
    emotion_alert_triggered: int = 0
    emotion_alert_per_kind: dict = field(default_factory=dict)
    # vision-007 / infra-009: priority_boost 被 _should_trigger 消费的次数
    priority_boost_consumed: int = 0
    # interact-014: ProactiveScheduler 真消费 vision-007 priority_boost（仲裁层）
    # - priority_boost_level_consumed: 按 boost level（rule_id，如 dark_silence /
    #   motion_greet / curious_idle）拆分的消费计数
    # - arbit_skipped_for_emotion: 同帧 emotion_alert 最近发生 → fusion/mm 被抑制的次数
    # - arbit_cooldown_with_boost: boost 有效但仍因全局 cooldown 抑制的次数
    #   （证明 boost 不绕过 cooldown）
    priority_boost_level_consumed: dict = field(default_factory=dict)
    arbit_skipped_for_emotion: int = 0
    arbit_cooldown_with_boost: int = 0
    # companion-011: group_mode 状态统计
    # - group_mode_trigger_count: 累计进入 group_mode 的次数（False→True 边沿）
    # - group_mode_active_total: 累计在 group_mode 内的 tick / observe 次数（驻留时长代理）
    group_mode_trigger_count: int = 0
    group_mode_active_total: int = 0
    last_topic: str = ""
    last_topic_ts: float = 0.0
    # interact-007 L2: history 用 deque(maxlen=200)，避免长跑会话内存无界增长
    history: Deque[str] = field(default_factory=lambda: collections.deque(maxlen=200))


# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------


def _bool_env(key: str, default: bool = False) -> bool:
    raw = (os.environ.get(key) or "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


def _float_env(key: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        log.warning("[proactive] %s=%r 非数字，回退默认 %.2f", key, raw, default)
        return default
    if v < lo:
        log.warning("[proactive] %s=%.2f <%.2f，clamp 到 %.2f", key, v, lo, lo)
        return lo
    if v > hi:
        log.warning("[proactive] %s=%.2f >%.2f，clamp 到 %.2f", key, v, hi, hi)
        return hi
    return v


def _int_env(key: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        log.warning("[proactive] %s=%r 非整数，回退默认 %d", key, raw, default)
        return default
    if v < lo:
        log.warning("[proactive] %s=%d <%d，clamp 到 %d", key, v, lo, lo)
        return lo
    if v > hi:
        log.warning("[proactive] %s=%d >%d，clamp 到 %d", key, v, hi, hi)
        return hi
    return v


def proactive_enabled_from_env() -> bool:
    return _bool_env("COCO_PROACTIVE", default=False)


def mm_proactive_llm_enabled_from_env() -> bool:
    """interact-012: MM proactive LLM 化（default-OFF）。

    需同时设 ``COCO_MM_PROACTIVE_LLM=1``（本 feature 开关）；
    上层 main.py 仍要求 ``COCO_MM_PROACTIVE=1`` 让 MultimodalFusion 本身可构造。
    """
    return _bool_env("COCO_MM_PROACTIVE_LLM", default=False)


def proactive_arbitration_enabled_from_env() -> bool:
    """interact-014: ProactiveScheduler 真消费 vision-007 priority_boost 仲裁层。

    default-OFF（``COCO_PROACTIVE_ARBIT=1`` 启用）。OFF 时与 vision-007 现状
    bytewise 等价：cooldown 缩放 0.5 全规则，无 boost_level emit，emotion_alert
    不抑制同帧 fusion/mm。ON 时引入：
    - 按 boost level（dark_silence=0.3 / motion_greet=0.5 / curious_idle=0.7 /
      其他=0.5）缩放 cooldown，但 boost 不绕过全局 cooldown（最小裁剪后仍执行
      `since < cooldown` 检查）
    - 最近 ``ARBIT_EMOTION_WINDOW_S`` 内若 record_emotion_alert_trigger 发生，
      本帧 fusion_boost / mm_proactive 路径被抑制（emotion_alert > fusion >
      mm > 普通）
    - trigger 成功时 emit `interact.proactive_topic` 附 ``boost_level`` 字段
    """
    return _bool_env("COCO_PROACTIVE_ARBIT", default=False)


# interact-014: boost level → cooldown 缩放因子。
# arbit OFF 时统一用 0.5（与 vision-007 原 _should_trigger 行为等价）。
# arbit ON 时按 rule_id 区分：dark_silence 最强（紧急感）；curious_idle 最弱。
_ARBIT_BOOST_COOLDOWN_SCALE = {
    "dark_silence": 0.3,
    "motion_greet": 0.5,
    "curious_idle": 0.7,
}
_ARBIT_BOOST_DEFAULT_SCALE = 0.5
# 同帧 emotion_alert 抢占的时间窗（秒）。emotion_alert 由独立路径触发，不走
# maybe_trigger；仲裁层把 _last_emotion_alert_ts 与当前帧 t 比较，落在窗内
# 则抑制 fusion/mm 一次。
ARBIT_EMOTION_WINDOW_S = 1.0


def config_from_env() -> ProactiveConfig:
    return ProactiveConfig(
        enabled=proactive_enabled_from_env(),
        idle_threshold_s=_float_env("COCO_PROACTIVE_IDLE_S", DEFAULT_IDLE_S, 10.0, 3600.0),
        cooldown_s=_float_env("COCO_PROACTIVE_COOLDOWN_S", DEFAULT_COOLDOWN_S, 10.0, 7200.0),
        max_topics_per_hour=_int_env("COCO_PROACTIVE_MAX_PER_HOUR", DEFAULT_MAX_PER_HOUR, 1, 60),
        tick_s=_float_env("COCO_PROACTIVE_TICK_S", DEFAULT_TICK_S, 0.1, 30.0),
    )


# ---------------------------------------------------------------------------
# interact-013: MM prompt snapshot（锁内 collect + 锁外 render 拆分）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MmPromptSnapshot:
    """interact-013: 锁内抓取的 mm prompt 构建所需 self.* 与 ctx 快照（不可变）。

    render 阶段在锁外纯函数运行，调 profile_store.load() 做 IO（如有）。
    """
    # self.* 快照
    profile_store: Any  # 引用即可（load() 锁外调）
    topic_preferences: Mapping[str, int]
    group_template_override: Optional[Sequence[str]]
    current_emotion_label: str
    # ctx 拷贝
    rule_id: str
    caption: str
    hint: str
    face_ids: Sequence[str]
    ctx_emotion_label: str


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class ProactiveScheduler:
    """主动话题调度器。

    依赖（全部 optional，缺一即 _should_trigger=False）：

    - power_state: PowerStateMachine（current_state==ACTIVE 才触发）
    - face_tracker: 提供 ``.latest()`` 返回有 ``.present`` 字段的对象
    - llm_reply_fn: ``(text, *, system_prompt=None) -> str`` —— 通常是
      ``llm_client.reply``；不接受 system_prompt 时本类自动退化
    - tts_say_fn: ``(text, blocking=True) -> None``
    - profile_store: 可选；有则 ``build_system_prompt(profile)`` 注入
    - on_interaction: 触发后调用，统一记账（默认指向 power_state.record_interaction）
    - clock: 时间源，便于 fake clock 测试
    """

    def __init__(
        self,
        *,
        config: Optional[ProactiveConfig] = None,
        power_state: Any = None,
        face_tracker: Any = None,
        llm_reply_fn: Optional[Callable[..., str]] = None,
        tts_say_fn: Optional[Callable[..., None]] = None,
        profile_store: Any = None,
        on_interaction: Optional[Callable[[str], None]] = None,
        clock: Optional[Callable[[], float]] = None,
        emit_fn: Optional[Callable[..., None]] = None,
        conv_state_machine: Any = None,
    ) -> None:
        self.config = config or ProactiveConfig()
        self.power_state = power_state
        self.face_tracker = face_tracker
        self.llm_reply_fn = llm_reply_fn
        self.tts_say_fn = tts_say_fn
        self.profile_store = profile_store
        self.on_interaction = on_interaction
        self.clock = clock or time.monotonic
        self._emit = emit_fn  # 由测试注入；None 时延迟 import logging_setup.emit
        # interact-008 L1-1: ConversationStateMachine（可选）。
        # 注入后 _should_trigger 在 QUIET 状态返回 "quiet_state"，
        # 避免后台 ProactiveScheduler 在用户要求"安静"期间还自顾自地开口。
        self.conv_state_machine = conv_state_machine
        self._lock = threading.RLock()
        self.stats = ProactiveStats()

        # last_interaction_ts：从 InteractSession 钩进来；初始化为"刚启动"，
        # 让 idle_threshold 从 start 时刻起算（避免一上来就秒发）。
        self._last_interaction_ts: float = self.clock()
        self._last_proactive_ts: float = 0.0
        # 最近 1h 触发时间戳队列（用于 max_per_hour 限流）
        self._recent_triggers: Deque[float] = collections.deque()

        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        # interact-011: 离线降级时 pause()，恢复时 resume()。
        # _paused=True 时 _should_trigger 返回 "paused"。pause/resume 是幂等的。
        self._paused: bool = False

        # companion-009: 偏好关键词 {keyword: weight}（归一化）。default-OFF 时
        # 维持空 dict —— _select_topic_seed / _build_system_prompt 行为不变。
        self._topic_preferences: dict = {}

        # companion-014: 后台 _do_trigger_unlocked 路径上的 candidates 注入 hook。
        # provider() 返回 Sequence[str]（topic 候选池）；非空时由 select_topic_seed
        # 按 prefer overlap 加权挑选；None / 空 / 异常 → 维持 config.topic_seed 现行为。
        # default-OFF：未 set 即 None；与 companion-009 select_topic_seed 仅 system_prompt
        # 间接注入路径 bytewise 等价。
        self._topic_seed_provider: Optional[Callable[[], Sequence[str]]] = None

        # vision-007 / infra-009: multimodal_fusion 命中规则后写 True；
        # 下一次 _should_trigger 命中时 idle_threshold 减半 + cooldown 减半
        # 视作"优先调度一次"，consume 后立即清回 False。MultimodalFusion 通过
        # hasattr 检测后写本字段。
        self._next_priority_boost: bool = False
        # interact-014: boost level（rule_id，如 dark_silence/motion_greet/curious_idle）；
        # MultimodalFusion 真消费仲裁路径上 fire 时由 fusion 设置。OFF 时无人写也无人读，
        # 与 vision-007 现状 bytewise 等价。
        self._next_priority_boost_level: Optional[str] = None
        # interact-014: 最近一次 record_emotion_alert_trigger 的时间戳（用于
        # 仲裁层"emotion_alert 抢占同帧 fusion/mm"判定，default-OFF 时无效）
        self._last_emotion_alert_ts: float = 0.0
        # companion-010: 可选 EmotionAlertCoordinator 注入；scheduler tick 时
        # 顺带调一次 coord.tick() 让到期 prefer 自动还原（不再依赖新 emotion 事件触发）。
        self._emotion_alert_coord: Any = None

        # companion-011: group_mode 句式 override；GroupModeCoordinator 进入
        # group_mode 时 set_group_template_override((...))，退出时清 None。
        # 非空时 _build_system_prompt 追加 group 句式指令，_do_trigger_unlocked
        # 在 LLM 兜底前缀用 group_phrases[0]。
        self._group_template_override: Optional[tuple] = None

        # robot-008: 可选 RobotSequencer 注入（default=None）。
        # 注入后 _do_trigger_unlocked emit 之后会异步 run 一个简单的 nod 序列，
        # 让主动开口附带轻量肢体反馈。None 时整段 no-op，bytewise 与基线等价。
        self._robot_sequencer: Any = None

        # interact-012: MM proactive LLM 化（default-OFF）。MultimodalFusion 命中
        # 规则后通过 set_mm_llm_context({rule_id, hint, caption, emotion_label,
        # face_ids, ts}) 把上下文塞过来；下一次 maybe_trigger 命中时 _build_mm_system_prompt
        # 注入专用 prompt（含场景词 + 当前情绪 + prefer TopK）。一次性消费——consume
        # 后立即清回 None，避免污染后续普通 tick。仅在 COCO_MM_PROACTIVE_LLM=1 时
        # 真把它喂给 LLM；OFF 时即使 fusion 调过 setter，本字段也维持 None。
        self._mm_llm_context: Optional[Dict[str, Any]] = None
        # interact-012: 当前情绪 label（可选）；EmotionAlertCoordinator / 外部 emotion 模块
        # 通过 set_current_emotion_label() 灌入；_build_mm_system_prompt 注入。
        self._current_emotion_label: str = ""
        # interact-012: 离线 fallback 标志位（OfflineDialogFallback 在离线降级期间 set True）。
        # MM 路径若 _offline_fallback_active=True 则退化为离线模板，不调 LLM。
        self._offline_fallback_active: bool = False

        # 探测 llm_reply_fn 是否接受 system_prompt
        self._llm_accepts_system_prompt = self._probe_kwarg(llm_reply_fn, "system_prompt")

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    @staticmethod
    def _probe_kwarg(fn: Optional[Callable[..., Any]], name: str) -> bool:
        if fn is None:
            return False
        import inspect
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

    def record_interaction(self, source: str = "interact") -> None:
        """挂到 InteractSession.on_interaction：每次交互重置 idle 计时。"""
        with self._lock:
            self._last_interaction_ts = self.clock()

    def set_robot_sequencer(self, sequencer: Any) -> None:
        """robot-008: 注入 RobotSequencer 实例。

        注入后 _do_trigger_unlocked emit 之后会 best-effort 触发一个简单 nod
        序列，让主动开口附带轻量肢体反馈。sequencer=None 时清除注入（OFF 等价）。

        线程 (robot-015 锁定): _do_trigger_unlocked 优先调 seq.enqueue(action) 非阻塞投递,
        由 RobotSequencer 内部 _action_worker daemon 线程串行消费, proactive _loop 不再
        起新线程; 仅当注入对象无 enqueue 方法时, 走 seq.run([action]) 同步 fallback (legacy /
        mock 兜底, 也不起新线程)。lifecycle 串行: set_robot_sequencer 应在 sequencer 注入
        路径就绪后、proactive 启动 _loop 前调用 (main.py 当前布局已满足)。

        robot-010: lifecycle 校验
        - (a) 注入已 shutdown 的 sequencer → logger.warning 后 **拒绝**（不写入 _robot_sequencer）；
        - (b) 已存在 _robot_sequencer 时再次注入 → logger.warning 记重复注入后覆盖；
        - (c) None 入参 → 清除（与 OFF 等价）；
        - 探测 is_shutdown 用 best-effort：sequencer 没有该方法 / 抛异常时按"未 shutdown"处理，
          避免对老/mock sequencer 产生反向破坏（与 default-OFF 不冲突）。
        """
        # robot-010 (a): is_shutdown 探针 —— 拒绝注入已 shutdown 的 sequencer。
        # 注：仅当 is_shutdown() 返回**严格 bool True** 才视为 shutdown；返回非 bool
        # （如 MagicMock 默认 stub 出的 MagicMock 实例）按"未 shutdown"处理，
        # 保护既有 mock 测试不被反向破坏。
        if sequencer is not None:
            try:
                is_fn = getattr(sequencer, "is_shutdown", None)
                if callable(is_fn):
                    rv = is_fn()
                    if isinstance(rv, bool) and rv is True:
                        log.warning(
                            "[proactive] set_robot_sequencer: refuse to inject "
                            "already-shutdown sequencer (%r); keeping existing=%r",
                            sequencer, self._robot_sequencer is not None,
                        )
                        return
            except Exception as e:  # noqa: BLE001
                # 探针自身异常 fail-soft —— 按"未 shutdown"继续注入
                log.warning("[proactive] set_robot_sequencer: is_shutdown probe failed: %s: %s",
                            type(e).__name__, e)
        with self._lock:
            # robot-010 (b): 重复注入 WARNING
            if self._robot_sequencer is not None and sequencer is not None:
                log.warning(
                    "[proactive] set_robot_sequencer: overwriting existing sequencer "
                    "(prev=%r, new=%r) — double-injection detected",
                    self._robot_sequencer, sequencer,
                )
            self._robot_sequencer = sequencer

    # interact-010: 共享 cooldown API
    # ------------------------------------------------------------------
    # 让外部 trigger 源（如 GestureDialogBridge）能把"我刚开口了"写穿
    # _last_proactive_ts，下一轮 _should_trigger 自动遵守同一 cooldown 窗口。
    # 反向：is_in_cooldown 让外部源在自己开口前先检查 proactive 是否刚发过。
    def record_trigger(self, source: str = "external") -> None:
        """外部触发源（如 gesture bridge）用来注册一次 trigger 进 cooldown 窗口。

        与 maybe_trigger 内的预占逻辑一致：写 _last_proactive_ts +
        _last_interaction_ts + recent_triggers，下一轮 _should_trigger 自动
        识别为 "cooldown" / "rate_limit"。
        """
        with self._lock:
            t = self.clock()
            self._last_proactive_ts = t
            self._last_interaction_ts = t
            self._recent_triggers.append(t)
            # 借 stats.history 留个调试痕迹（不动 triggered，避免污染 metrics）
            self.stats.history.append(f"@{t:.2f}: <external:{source}>")

    def is_in_cooldown(self, now: Optional[float] = None) -> bool:
        """是否仍在 cooldown_s 窗口内（自上次 _last_proactive_ts 起算）。"""
        with self._lock:
            if self._last_proactive_ts <= 0:
                return False
            t = now if now is not None else self.clock()
            return (t - self._last_proactive_ts) < self.config.cooldown_s

    # vision-006: scene caption 作为外部触发源
    # ------------------------------------------------------------------
    # SceneCaptionEmitter 在 caption 命中时调本方法。把 caption 当作"主动开口
    # 候选信号"记账：自增 stats.caption_proactive；同时复用 record_trigger 写
    # _last_proactive_ts，让常规 cooldown 窗口对 caption 也生效，避免与 idle 路径
    # 双发。后续若需要让 caption 真正驱动一次 LLM/TTS（"你今天好像换了头发"），
    # 在这里把 caption.text 作为 seed 直接调 _do_trigger_unlocked 即可；本期先
    # 只记账，保持最小改动。
    def record_caption_trigger(self, caption_text: str = "") -> None:
        with self._lock:
            self.stats.caption_proactive += 1
            t = self.clock()
            # 借 history 留个调试痕迹（不动 triggered，避免污染 metrics）
            try:
                self.stats.history.append(
                    f"@{t:.2f}: <caption:{(caption_text or '')[:60]}>"
                )
            except Exception:  # noqa: BLE001
                pass

    # vision-007: multimodal fusion 作为外部触发源
    # ------------------------------------------------------------------
    # MultimodalFusion 命中规则后调本方法。仅记账（mm_triggered + mm_per_rule
    # + history）并 emit ``proactive.multimodal_triggered``；不立即驱动 LLM/TTS，
    # 保持单一调度入口（与 record_caption_trigger 同模式）。priority boost
    # 由 MultimodalFusion 自己写 _next_priority_boost 标志位，本方法不动 cooldown，
    # 让 ProactiveScheduler 的 idle / cooldown 规则继续生效。
    def record_multimodal_trigger(self, rule_id: str, hint: str = "") -> None:
        with self._lock:
            self.stats.mm_triggered += 1
            self.stats.mm_per_rule[rule_id] = self.stats.mm_per_rule.get(rule_id, 0) + 1
            t = self.clock()
            try:
                self.stats.history.append(
                    f"@{t:.2f}: <mm:{rule_id}:{(hint or '')[:40]}>"
                )
            except Exception:  # noqa: BLE001
                pass
        try:
            emit_fn = self._emit
            if emit_fn is None:
                from coco.logging_setup import emit as _emit
                emit_fn = _emit
            emit_fn(
                "proactive.multimodal_triggered",
                rule_id=rule_id,
                hint=(hint or "")[:200],
                subtype=f"mm_{rule_id}",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[proactive] mm emit failed: %s: %s", type(e).__name__, e)

    # companion-010: 情绪记忆告警作为外部触发源
    # ------------------------------------------------------------------
    # EmotionAlertCoordinator 在连续 sad 比例阈值触发后调本方法。记账（
    # emotion_alert_triggered + per_kind + history）+ emit
    # ``proactive.emotion_alert``，同时把告警视为一次主动开口契机：
    # - 不动 cooldown：alert 由 EmotionMemoryWindow 自带 30 分钟 cooldown，
    #   不再叠加 ProactiveScheduler 的 cooldown；
    # - 安慰类 prefer 由调用方（Coordinator）通过 set_topic_preferences 注入
    #   并在 cooldown 过后还原。
    def record_emotion_alert_trigger(self, kind: str, ratio: float = 0.0,
                                     window_size: int = 0) -> None:
        with self._lock:
            self.stats.emotion_alert_triggered += 1
            self.stats.emotion_alert_per_kind[kind] = (
                self.stats.emotion_alert_per_kind.get(kind, 0) + 1
            )
            t = self.clock()
            # interact-014: 写最近 alert 时戳，仲裁层用以在 maybe_trigger 同帧抢占 fusion/mm
            self._last_emotion_alert_ts = t
            try:
                self.stats.history.append(
                    f"@{t:.2f}: <emotion_alert:{kind}:r={ratio:.2f}>"
                )
            except Exception:  # noqa: BLE001
                pass
        try:
            emit_fn = self._emit
            if emit_fn is None:
                from coco.logging_setup import emit as _emit
                emit_fn = _emit
            emit_fn(
                "proactive.emotion_alert",
                kind=str(kind),
                ratio=float(ratio),
                window_size=int(window_size),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[proactive] emotion_alert emit failed: %s: %s",
                        type(e).__name__, e)
        # interact-015: 仲裁链 trace —— emotion_alert 始终视为 admit（独立路径，已发告警）
        # default-OFF 时 emit_trace 立即 return，不引入 IO/state 变化
        # interact-018: 独立路径的 latency_ms 自测量（emit_trace 内部到外部无锁，取 monotonic 单点 0.0）
        _ea_lat_start = time.monotonic()
        try:
            from coco.proactive_trace import emit_trace as _et, make_candidate_id as _mci
            _et(
                "emotion_alert",
                _mci(t),
                "admit",
                ts=t,
                kind=str(kind),
                ratio=float(ratio),
                latency_ms=round((time.monotonic() - _ea_lat_start) * 1000.0, 3),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[proactive] trace emotion_alert failed: %s: %s",
                        type(e).__name__, e)

    # interact-011: pause / resume —— 让 OfflineDialogFallback 在离线降级期间静默
    # ProactiveScheduler，避免雪上加霜。pause/resume 幂等；线程 loop 不停（继续 tick
    # 但 _should_trigger 因 paused 返回 "paused"），便于恢复后立即可用。
    def pause(self, source: str = "external") -> None:
        with self._lock:
            if self._paused:
                return
            self._paused = True
        log.info("[proactive] paused by %s", source)

    def resume(self, source: str = "external") -> None:
        with self._lock:
            if not self._paused:
                return
            self._paused = False
            # 防止 resume 后秒发：刷新 last_interaction_ts，让 idle_threshold 重新计时
            self._last_interaction_ts = self.clock()
        log.info("[proactive] resumed by %s", source)

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    # companion-009: 偏好加权 API
    # ------------------------------------------------------------------
    # 由 PreferenceLearner / main.py 在每次 rebuild 完成后调一次。
    # prefer 是归一化的 {keyword: weight in [0,1]}；空 dict 视为"未学过" → 维持原 seed 行为。
    def set_topic_preferences(self, prefer: Optional[Mapping[str, float]]) -> None:
        with self._lock:
            if not prefer:
                self._topic_preferences = {}
                return
            cleaned: Dict[str, float] = {}
            for k, v in dict(prefer).items():
                try:
                    if not k:
                        continue
                    w = float(v)
                    if w <= 0:
                        continue
                    cleaned[str(k)] = w
                except (TypeError, ValueError):
                    continue
            self._topic_preferences = cleaned

    def get_topic_preferences(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._topic_preferences)

    # companion-014: 公开 select_topic_seed candidates 注入 hook。
    # provider 为零参 callable，返回 Sequence[str] 候选话题；scheduler 后台
    # _do_trigger_unlocked 路径上若 provider 非 None，会调用并将结果作为
    # select_topic_seed(candidates=...) 的 candidates。default-OFF（None）。
    def set_topic_seed_provider(
        self,
        provider: Optional[Callable[[], Sequence[str]]],
    ) -> None:
        with self._lock:
            self._topic_seed_provider = provider

    def get_topic_seed_provider(self) -> Optional[Callable[[], Sequence[str]]]:
        with self._lock:
            return self._topic_seed_provider

    # companion-011: GroupModeCoordinator 注入 group 句式 override。
    # ``phrases=None`` 清除（退出 group_mode）；非空 tuple/list 注入 group 句式。
    def set_group_template_override(self, phrases: Optional[Sequence[str]]) -> None:
        with self._lock:
            if phrases is None:
                self._group_template_override = None
            else:
                cleaned = tuple(str(p).strip() for p in phrases if p and str(p).strip())
                self._group_template_override = cleaned or None

    def get_group_template_override(self) -> Optional[tuple]:
        with self._lock:
            return self._group_template_override

    # interact-012: MM proactive LLM 化 API
    # ------------------------------------------------------------------
    # MultimodalFusion 触发规则时（在 record_multimodal_trigger 路径之后）调本方法，
    # 把场景上下文塞进 scheduler，下一次 maybe_trigger 命中时由 _build_mm_system_prompt
    # 注入专用 prompt。一次性消费。OFF 时调入也无害（_build_mm_system_prompt 只在 ON 时读）。
    def set_mm_llm_context(self, ctx: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            if ctx is None:
                self._mm_llm_context = None
                return
            # 防御性拷贝，避免上游 mutate 后续 prompt 渲染时跳值
            try:
                self._mm_llm_context = dict(ctx)
            except (TypeError, ValueError):
                self._mm_llm_context = None

    def get_mm_llm_context(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._mm_llm_context) if self._mm_llm_context else None

    def set_current_emotion_label(self, label: str) -> None:
        """interact-012: 外部情绪源（companion-010 / EmotionAlertCoordinator）灌入当前情绪 label。"""
        with self._lock:
            self._current_emotion_label = str(label or "").strip()

    def set_offline_fallback_active(self, active: bool) -> None:
        """interact-012: interact-011 OfflineDialogFallback 在离线降级期间 set True。

        ON 时 MM 路径退化为离线模板（不调 LLM）；OFF 时维持普通 LLM 路径。
        与 pause()/resume() 解耦：pause 是整段 proactive 静默；本字段只影响 MM-LLM 路径行为。
        """
        with self._lock:
            self._offline_fallback_active = bool(active)

    def is_offline_fallback_active(self) -> bool:
        with self._lock:
            return self._offline_fallback_active

    # companion-010 / infra-009: 让 scheduler tick 顺手调一次 coord.tick()，
    # 这样 alert 过期 prefer 还原不再依赖"再来一个 emotion 事件触发 on_emotion 的内部 tick"。
    def set_emotion_alert_coord(self, coord: Any) -> None:
        self._emotion_alert_coord = coord

    def select_topic_seed(
        self,
        candidates: Optional[Sequence[str]] = None,
        *,
        default: Optional[str] = None,
    ) -> str:
        """从 candidates 里按 prefer overlap 加权选一个 topic seed。

        - 没 prefer：返回 default 或 config.topic_seed。
        - 有 prefer 但 candidates 空：返回 default 或 config.topic_seed。
        - candidates 非空 + prefer 非空：每个 candidate 算 ``sum(prefer[k]
          for k in prefer if k in candidate)`` 作为分数；分数最高者胜（同分取首位）；
          所有分数 0 则回退 default / config.topic_seed。

        命中（即真的按 prefer 选了一条而非回退默认）时 stats.prefer_weighted_select_count++。
        """
        fallback = default if default is not None else self.config.topic_seed
        with self._lock:
            prefer = dict(self._topic_preferences)
        if not candidates or not prefer:
            return fallback
        best_score = 0.0
        best_idx = -1
        for i, c in enumerate(candidates):
            if not c:
                continue
            score = 0.0
            for k, w in prefer.items():
                if k and k in c:
                    score += w
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx < 0 or best_score <= 0:
            return fallback
        with self._lock:
            self.stats.prefer_weighted_select_count += 1
        return candidates[best_idx]

    def start(self, stop_event: threading.Event) -> None:
        if self._thread is not None and self._thread.is_alive():
            log.warning("[proactive] scheduler already running")
            return
        self._stop_event = stop_event
        self._thread = threading.Thread(
            target=self._loop,
            name="coco-proactive",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "[proactive] scheduler started cfg=idle=%.1fs cooldown=%.1fs max/h=%d tick=%.1fs",
            self.config.idle_threshold_s, self.config.cooldown_s,
            self.config.max_topics_per_hour, self.config.tick_s,
        )

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # 触发逻辑（同步，便于 verify 直调）
    # ------------------------------------------------------------------

    def _should_trigger(self, now: Optional[float] = None) -> Optional[str]:
        """检查是否该触发；返回 None 表示触发，否则返回 skip 原因字符串。"""
        if not self.config.enabled:
            return "disabled"
        # interact-011: 被 OfflineDialogFallback 暂停 → "paused"
        if self._paused:
            return "paused"
        t = now if now is not None else self.clock()
        # interact-008 L1-1: 如果对话状态机在 QUIET，跳过（用户明确要求安静）
        if self.conv_state_machine is not None:
            try:
                if self.conv_state_machine.is_quiet_now():
                    return "quiet_state"
            except Exception as e:  # noqa: BLE001
                log.warning("[proactive] conv_state_machine read failed: %s: %s", type(e).__name__, e)
        # 1) power state must be ACTIVE
        if self.power_state is not None:
            try:
                from coco.power_state import PowerState as _PS
                if self.power_state.current_state != _PS.ACTIVE:
                    return "power"
            except Exception as e:  # noqa: BLE001
                log.warning("[proactive] power_state read failed: %s: %s", type(e).__name__, e)
                return "power"
        # 2) face presence
        if self.face_tracker is not None:
            try:
                snap = self.face_tracker.latest()
                if not bool(getattr(snap, "present", False)):
                    return "no_face"
            except Exception as e:  # noqa: BLE001
                log.warning("[proactive] face_tracker read failed: %s: %s", type(e).__name__, e)
                return "no_face"
        else:
            # 没注入 face_tracker → 必然不发（保护性默认）
            return "no_face"
        # 3) idle threshold
        idle_for = max(0.0, t - self._last_interaction_ts)
        # vision-007 / infra-009: priority_boost 减半 idle threshold
        # interact-014: arbit ON 时按 boost level 缩放；OFF 维持 0.5（bytewise 等价）
        idle_threshold = self.config.idle_threshold_s
        cooldown_scale = 1.0  # 1.0 = 不缩放
        if self._next_priority_boost:
            arbit_on = proactive_arbitration_enabled_from_env()
            if arbit_on:
                lvl = self._next_priority_boost_level or ""
                cooldown_scale = _ARBIT_BOOST_COOLDOWN_SCALE.get(
                    lvl, _ARBIT_BOOST_DEFAULT_SCALE
                )
                # idle 缩放：保持 vision-007 原减半行为（避免改变 V1 既有断言）
                idle_threshold = max(0.0, idle_threshold * 0.5)
            else:
                cooldown_scale = 0.5
                idle_threshold = max(0.0, idle_threshold * 0.5)
        if idle_for < idle_threshold:
            return "idle"
        # 4) cooldown since last proactive
        # interact-014: boost 不绕过全局 cooldown —— 仅缩放，若仍 < cooldown 则 skip
        if self._last_proactive_ts > 0:
            since = max(0.0, t - self._last_proactive_ts)
            cooldown = self.config.cooldown_s
            if self._next_priority_boost:
                cooldown = max(0.0, cooldown * cooldown_scale)
            if since < cooldown:
                # interact-014: boost 在但被 cooldown 抑制 → 单独记账（V5 断言用）
                if self._next_priority_boost and proactive_arbitration_enabled_from_env():
                    self.stats.arbit_cooldown_with_boost += 1
                return "cooldown"
        # 5) rate limit
        # 清理 1h 之外的旧条目
        cutoff = t - 3600.0
        while self._recent_triggers and self._recent_triggers[0] < cutoff:
            self._recent_triggers.popleft()
        if len(self._recent_triggers) >= self.config.max_topics_per_hour:
            return "rate_limit"
        return None

    def maybe_trigger(self, now: Optional[float] = None) -> bool:
        """同步检查并触发；返回是否触发了一次主动话题。

        verify 路径直接调；scheduler 线程也调它（共享路径，避免行为漂移）。

        interact-007 L1-2: 锁的作用域从"全程"收缩到"判定 + 抢占式预占"，
        实际 LLM/TTS（耗时数秒）在锁外执行，避免阻塞 InteractSession.record_interaction
        刷新 _last_interaction_ts。
        """
        # ---- 锁内：判定 + 抢占式预占（fail-soft：不回滚，宁少发也不连发）----
        # interact-018: latency_ms 测量起点 —— 锁外 monotonic，覆盖整段 maybe_trigger
        # （判定 + 预占 + LLM/TTS + emit）。t_start 后续每次 _trace_emit 时计算
        # `round((monotonic() - t_start) * 1000, 3)` 作为 latency_ms extra kwarg。
        #
        # interact-021: latency_ms 各 stage 语义文档化（单源真理：
        # research/proactive_trace_contract.md §5）。同一字段名在不同 emit 站点下
        # 语义不同, 下游聚合 (scripts/proactive_trace_summary.py latency_by_stage)
        # 不可跨 stage 求总 p50/p95。
        #
        # 6 个 stage 名权威清单（与 §5.3 同步, 下游必须假设全集）:
        #   emotion_alert  (站点 #1, 独立路径自测量, decision=admit)
        #   fusion_boost   (站点 #2/#3/#4, decision=reject 或 admit)
        #   mm_proactive   (站点 #2/#3/#4, decision=reject 或 admit)
        #   cooldown_hit   (站点 #3 reason=cooldown 特化, decision=reject)
        #   arbit_winner   (站点 #4 锁内预占成功, decision=admit, 不含锁外 LLM/TTS)
        #   normal         (站点 #3 default 入口快照, decision=reject)
        #
        # 单调性 (cumulative): 同一 maybe_trigger 调用内多个 emit 共享 _lat_start,
        # 后发 emit 的 latency_ms >= 前发 emit (单调非降, 不是严格递增)。
        # 时钟单位: ms float, 精度 0.001 (`round(..., 3)`); monotonic 不受墙钟回拨影响。
        _lat_start = time.monotonic()

        def _lat_ms() -> float:
            return round((time.monotonic() - _lat_start) * 1000.0, 3)

        with self._lock:
            self.stats.ticks += 1
            t = now if now is not None else self.clock()

            # interact-015: 同帧 candidate_id（trace 用于把同一次决策路径的多 stage
            # 关联起来；trace OFF 时 emit_trace 立即 return，无副作用）
            try:
                from coco.proactive_trace import (
                    emit_trace as _trace_emit,
                    make_candidate_id as _trace_mci,
                )
                _candidate_id = _trace_mci(t)
            except Exception:  # noqa: BLE001
                def _trace_emit(*a, **kw):  # type: ignore[no-redef]
                    return None
                _candidate_id = str(int(t * 1000))

            # interact-014: 仲裁层（default-OFF）—— emotion_alert > fusion_boost > mm_proactive > 普通
            # ON 时若最近 ARBIT_EMOTION_WINDOW_S 内发生过 emotion_alert，则抑制本帧
            # fusion/mm 路径（清 boost flag + mm_ctx），记 arbit_skipped_for_emotion 后
            # 让 _should_trigger 走普通路径（多半会因 cooldown skip）。emotion_alert
            # 已通过独立 emit 路径输出告警，仲裁层不再二次驱动 LLM/TTS。
            arbit_on = proactive_arbitration_enabled_from_env()
            if arbit_on and self._last_emotion_alert_ts > 0:
                if (t - self._last_emotion_alert_ts) < ARBIT_EMOTION_WINDOW_S:
                    # interact-016 C-6 fix: 在改 state 之前先快照原始路径，避免
                    # 抑制后再用 _next_priority_boost 推断 stage 名（之前 bug:
                    # boost True 抑制路径被错标成 mm_proactive；mm-only 路径被
                    # 错标成 fusion_boost）。fusion 路径优先于 mm（与仲裁链
                    # emotion_alert > fusion_boost > mm_proactive 同序）。
                    _preempt_boost = bool(self._next_priority_boost)
                    _preempt_mm = self._mm_llm_context is not None
                    suppressed_any = False
                    if self._next_priority_boost:
                        self._next_priority_boost = False
                        self._next_priority_boost_level = None
                        suppressed_any = True
                    if self._mm_llm_context is not None:
                        self._mm_llm_context = None
                        suppressed_any = True
                    if suppressed_any:
                        self.stats.arbit_skipped_for_emotion += 1
                        # interact-015 trace: emotion_alert 窗口内抢占 fusion/mm
                        # interact-016 C-6: stage 名按快照决定（fusion 优先）
                        try:
                            _preempt_stage = (
                                "fusion_boost" if _preempt_boost
                                else ("mm_proactive" if _preempt_mm else "fusion_boost")
                            )
                            _trace_emit(
                                _preempt_stage,
                                _candidate_id, "reject",
                                reason="arbit_emotion_preempt", ts=t,
                                latency_ms=_lat_ms(),
                            )
                        except Exception:  # noqa: BLE001
                            pass

            # interact-015 trace: stage 入口侦测（emit 顺序：fusion_boost / mm_proactive / normal）
            # 仅观测，不改 state。OFF 时 _trace_emit no-op。
            try:
                _stage_in: Optional[str] = None
                if self._next_priority_boost:
                    _stage_in = "fusion_boost"
                elif self._mm_llm_context is not None:
                    _stage_in = "mm_proactive"
                else:
                    _stage_in = "normal"
            except Exception:  # noqa: BLE001
                _stage_in = "normal"

            reason = self._should_trigger(now=t)
            if reason is not None:
                key = f"skipped_{reason}"
                if hasattr(self.stats, key):
                    setattr(self.stats, key, getattr(self.stats, key) + 1)
                # interact-015 trace: reject —— stage 与入口 stage 一致；cooldown 单独标 cooldown_hit
                try:
                    _stage_out = "cooldown_hit" if reason == "cooldown" else (_stage_in or "normal")
                    _trace_emit(
                        _stage_out, _candidate_id, "reject",
                        reason=str(reason), ts=t,
                        latency_ms=_lat_ms(),
                    )
                except Exception:  # noqa: BLE001
                    pass
                return False
            # 抢占式预占：先把 last_proactive_ts / last_interaction_ts / recent_triggers
            # 写好，再放锁；这样 LLM/TTS 期间外部线程读到的"已发"，不会被同 tick 重复触发。
            self._last_proactive_ts = t
            self._last_interaction_ts = t
            self._recent_triggers.append(t)
            self.stats.triggered += 1
            # vision-007 / infra-009: consume priority_boost（无论本次是否真因
            # boost 命中——只要 boost 标志为 True 且本次成功 trigger，就视为已消耗）
            # interact-014: 同时按 level 拆账；本次 emit 也带上 boost_level 字段
            consumed_boost_level: Optional[str] = None
            if self._next_priority_boost:
                self.stats.priority_boost_consumed += 1
                consumed_boost_level = self._next_priority_boost_level or ""
                if proactive_arbitration_enabled_from_env():
                    key = consumed_boost_level or "_unknown"
                    self.stats.priority_boost_level_consumed[key] = (
                        self.stats.priority_boost_level_consumed.get(key, 0) + 1
                    )
                self._next_priority_boost = False
                self._next_priority_boost_level = None

            # interact-015 trace: arbit_winner —— 本次 trigger 已锁内预占成功
            try:
                _trace_emit(
                    "arbit_winner", _candidate_id, "admit",
                    ts=t,
                    stage_in=str(_stage_in or "normal"),
                    boost_level=(consumed_boost_level if consumed_boost_level else ""),
                    latency_ms=_lat_ms(),
                )
            except Exception:  # noqa: BLE001
                pass

            # interact-012: MM proactive LLM 化（default-OFF via COCO_MM_PROACTIVE_LLM=1）
            # - mm_llm_on=True 且 mm_ctx 非空：用 MM 专用 prompt（含场景词 + 当前 emotion + prefer TopK）；
            #   一次性消费（无论 LLM 成败都清 ctx，避免污染下一个普通 tick）
            # - 离线 fallback 激活：退化为模板，不调 LLM，仅记 mm_llm_fallback_offline
            # - OFF：维持普通 _build_system_prompt 路径（vision-007 record_trigger only 行为）
            mm_llm_on = mm_proactive_llm_enabled_from_env()
            mm_ctx = self._mm_llm_context
            mm_offline_fallback = False
            seed = self.config.topic_seed
            # companion-014: 后台 _do_trigger_unlocked 路径上调 select_topic_seed(candidates=...)
            # 显式注入候选池（公开 API wire）。provider 为 None / 异常 / 空候选 →
            # 维持 config.topic_seed 现行为（与 companion-009 bytewise 等价）。
            # 注：mm_ctx.hint 优先级仍最高（下方 if mm_llm_on 分支覆盖 seed），
            # 这里只影响普通后台路径。
            _seed_provider = self._topic_seed_provider
            if _seed_provider is not None:
                try:
                    _candidates = _seed_provider()
                except Exception as e:  # noqa: BLE001
                    log.warning("[proactive] topic_seed_provider failed: %s: %s",
                                type(e).__name__, e)
                    _candidates = None
                if _candidates:
                    try:
                        seed = self.select_topic_seed(
                            candidates=_candidates,
                            default=seed,
                        )
                    except Exception as e:  # noqa: BLE001
                        log.warning("[proactive] select_topic_seed(candidates=...) failed: %s: %s",
                                    type(e).__name__, e)
            # interact-013: 锁内只 snapshot；不在锁内调 profile_store.load / _build_system_prompt
            mm_snapshot: Optional[MmPromptSnapshot] = None
            base_prompt_needed = False
            if mm_llm_on and mm_ctx:
                if self._offline_fallback_active:
                    mm_offline_fallback = True
                    self.stats.mm_llm_fallback_offline += 1
                    base_prompt_needed = True  # 锁外构造普通 base prompt
                    seed_hint = str(mm_ctx.get("hint") or "").strip()
                    if seed_hint:
                        seed = seed_hint
                else:
                    try:
                        mm_snapshot = self._collect_mm_prompt_snapshot_locked(mm_ctx)
                    except Exception as e:  # noqa: BLE001
                        # snapshot 抓取异常 → 退化为普通 prompt 路径（V7 断言）
                        log.warning("[proactive] mm snapshot collect failed: %s: %s",
                                    type(e).__name__, e)
                        mm_snapshot = None
                        base_prompt_needed = True
                    seed_hint = str(mm_ctx.get("hint") or "").strip()
                    if seed_hint:
                        seed = seed_hint
                self._mm_llm_context = None  # 一次性消费
            else:
                base_prompt_needed = True
        # ---- 锁外：渲染 prompt（含 profile_store IO）+ LLM + TTS + emit ----
        if mm_snapshot is not None:
            try:
                system_prompt = self._render_mm_prompt_from_snapshot(mm_snapshot)
            except Exception as e:  # noqa: BLE001
                log.warning("[proactive] mm prompt render failed: %s: %s",
                            type(e).__name__, e)
                system_prompt = self._build_system_prompt()
        elif base_prompt_needed:
            system_prompt = self._build_system_prompt()
        else:
            system_prompt = None
        # interact-012: mm_offline_fallback=True → 走 _do_trigger_unlocked 但跳过 LLM（template-only）
        llm_errors_before = self.stats.llm_errors
        self._do_trigger_unlocked(
            t,
            system_prompt=system_prompt,
            seed=seed,
            skip_llm=mm_offline_fallback,
            boost_level=consumed_boost_level,
        )
        # interact-012: ON 且 mm_ctx 真触发了 LLM（非 fallback），且 LLM 没新增 error → 算成功
        if mm_llm_on and mm_ctx is not None and not mm_offline_fallback:
            if self.stats.llm_errors > llm_errors_before:
                with self._lock:
                    self.stats.mm_llm_errors += 1
            else:
                with self._lock:
                    self.stats.mm_llm_proactive_count += 1
                # interact-015: 记录 mm_proactive LLM 用量（estimate from chars; default-OFF）
                try:
                    from coco.proactive_trace import record_llm_usage as _rlu
                    _prompt_chars = len(system_prompt or "") + len(seed or "")
                    _completion_chars = len(self.stats.last_topic or "")
                    _rlu(
                        "mm_proactive",
                        prompt_chars=_prompt_chars,
                        completion_chars=_completion_chars,
                        ts=t,
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("[proactive] llm_usage record failed: %s: %s",
                                type(e).__name__, e)
        return True

    def _do_trigger_unlocked(self, t: float, *, system_prompt: Optional[str], seed: str,
                              skip_llm: bool = False,
                              boost_level: Optional[str] = None) -> None:
        """实际触发的耗时段：LLM → TTS → emit → on_interaction。锁外执行。

        失败时**不回滚**预占（fail-soft）：即使 LLM/TTS 都失败，也宁可少一次主动话题，
        也不冒"重新放锁后立即重发"的风险。仅 emit 一个 proactive_topic_failed 事件
        让上层可观测。

        interact-012: ``skip_llm=True`` 时（MM 路径离线 fallback）整段跳过 LLM 调用，
        直接用 seed 作为模板播报。
        """
        topic_text = ""
        # 1) LLM
        if self.llm_reply_fn is not None and not skip_llm:
            try:
                if self._llm_accepts_system_prompt and system_prompt is not None:
                    topic_text = self.llm_reply_fn(seed, system_prompt=system_prompt)
                else:
                    topic_text = self.llm_reply_fn(seed)
                topic_text = (topic_text or "").strip()
            except Exception as e:  # noqa: BLE001
                self.stats.llm_errors += 1
                log.warning("[proactive] llm_reply_fn failed: %s: %s", type(e).__name__, e)
                topic_text = ""
        if not topic_text:
            # fail-soft：用一句兜底，仍走 TTS（保证"主动开口"这件事 happen）
            # interact-012: skip_llm（MM 离线 fallback）走 seed（mm hint 模板），不再用通用兜底
            if skip_llm and seed:
                topic_text = seed
            else:
                topic_text = "我们聊点什么吧？"
            # companion-011: group_mode override → 兜底句子前缀拼 group lead，
            # 即便 LLM 失败也保持群体场景口吻。
            with self._lock:
                _gov = self._group_template_override
            if _gov:
                try:
                    _lead = str(_gov[0]).strip()
                except Exception:  # noqa: BLE001
                    _lead = ""
                if _lead:
                    topic_text = f"{_lead}，{topic_text}"

        # 2) TTS
        tts_ok = True
        if self.tts_say_fn is not None:
            try:
                self.tts_say_fn(topic_text, blocking=True)
            except Exception as e:  # noqa: BLE001
                tts_ok = False
                self.stats.tts_errors += 1
                log.warning("[proactive] tts_say_fn failed: %s: %s", type(e).__name__, e)

        # 3) 记 last_topic / history（锁内已经预占了 ts/triggered/recent_triggers）
        self.stats.last_topic = topic_text
        self.stats.last_topic_ts = t
        self.stats.history.append(f"@{t:.2f}: {topic_text[:60]}")

        # 4) on_interaction 钩子（默认走 power_state.record_interaction）
        if self.on_interaction is not None:
            try:
                self.on_interaction("proactive")
            except Exception as e:  # noqa: BLE001
                log.warning("[proactive] on_interaction failed: %s: %s", type(e).__name__, e)

        # 5) emit event
        try:
            emit_fn = self._emit
            if emit_fn is None:
                from coco.logging_setup import emit as _emit
                emit_fn = _emit
            # interact-007 L2: 去掉 informational 但语义无效的 idle_for 字段
            # interact-014: arbit ON 且本次 trigger 消费了 boost 时附 boost_level 字段
            _emit_kwargs = {"topic": topic_text[:200], "source": "scheduler"}
            if boost_level is not None and proactive_arbitration_enabled_from_env():
                _emit_kwargs["boost_level"] = boost_level or "_unknown"
            emit_fn(
                "interact.proactive_topic",
                **_emit_kwargs,
            )
            if not tts_ok or self.stats.llm_errors > 0:
                # 仅记一次失败事件（不影响计数已经预占的 triggered）
                # interact-018: emit-end 标准 fail 三口同发（additive，不破坏既有字段）
                #   - ok=False（强类型 bool；is_fail 第一口）
                #   - error="<llm|tts>:<class>"（is_fail 第二口）
                #   - failure_reason="llm_or_tts"（is_fail 第三口）
                _err_parts = []
                if self.stats.llm_errors > 0:
                    _err_parts.append(f"llm_errors={int(self.stats.llm_errors)}")
                if not tts_ok:
                    _err_parts.append(f"tts_errors={int(self.stats.tts_errors)}")
                _err_str = ";".join(_err_parts) or "unknown"
                emit_fn(
                    "interact.proactive_topic_failed",
                    topic=topic_text[:200],
                    llm_errors=int(self.stats.llm_errors),
                    tts_errors=int(self.stats.tts_errors),
                    ok=False,
                    error=_err_str,
                    failure_reason="llm_or_tts",
                )
        except Exception as e:  # noqa: BLE001
            log.warning("[proactive] emit failed: %s: %s", type(e).__name__, e)

        # robot-008: best-effort 触发轻量 nod 序列（注入后才生效）。
        # robot-009: 改造 — 不再起 daemon thread + seq.run(), 改为 sequencer.enqueue(action)
        # 非阻塞投递；由 sequencer 内部 action worker 串行消费，统一调度入口。
        # 任何异常吃掉——主动话题 happen 不依赖于 robot 动作成功。
        # robot-010 (c): 触发前用 is_shutdown 探针检查；若注入后 sequencer 已 shutdown
        # → 自动清空 _robot_sequencer 引用 + 跳过本次 enqueue，避免悬垂引用。
        with self._lock:
            _seq = self._robot_sequencer
        if _seq is not None:
            # robot-010: shutdown 自检 —— 已 shutdown → 清引用 + skip
            # 严格 bool True 才视为 shutdown（保护 MagicMock 默认 stub 场景）
            try:
                _is_fn = getattr(_seq, "is_shutdown", None)
                _shutdown_detected = False
                if callable(_is_fn):
                    _rv = _is_fn()
                    if isinstance(_rv, bool) and _rv is True:
                        _shutdown_detected = True
                if _shutdown_detected:
                    log.warning(
                        "[proactive] _do_trigger_unlocked: detected shutdown "
                        "sequencer; clearing _robot_sequencer and skipping enqueue"
                    )
                    with self._lock:
                        # 仅当仍是同一个对象时清空，避免覆盖他人新注入
                        if self._robot_sequencer is _seq:
                            self._robot_sequencer = None
                    _seq = None
            except Exception as e:  # noqa: BLE001
                log.warning("[proactive] is_shutdown probe (trigger) failed: %s: %s",
                            type(e).__name__, e)
        if _seq is not None:
            try:
                from coco.robot.sequencer import Action as _SeqAction
                _nod = _SeqAction(
                    action_id=f"proactive-nod-{int(t * 1000)}",
                    type="nod",
                    params={"amplitude_deg": 8.0},
                    duration_s=0.25,
                )
                # robot-009: 优先调 enqueue（新非阻塞 API），若 sequencer 是旧/mock
                # 没有 enqueue 方法则退化（不再起新线程，调用方负责）。
                _enqueue_fn = getattr(_seq, "enqueue", None)
                if callable(_enqueue_fn):
                    try:
                        _enqueue_fn(_nod)
                    except Exception as _e:  # noqa: BLE001
                        log.warning("[proactive] robot_sequencer.enqueue failed: %s: %s",
                                    type(_e).__name__, _e)
                else:
                    # 兼容路径：sequencer 上没有 enqueue（不应出现，留兜底）。
                    # 同步调用 run，不再起 daemon thread。
                    try:
                        _seq.run([_nod])
                    except Exception as _e:  # noqa: BLE001
                        log.warning("[proactive] robot_sequencer.run fallback failed: %s: %s",
                                    type(_e).__name__, _e)
            except Exception as e:  # noqa: BLE001
                log.warning("[proactive] robot_sequencer dispatch failed: %s: %s",
                            type(e).__name__, e)

        log.info("[proactive] triggered topic=%r", topic_text[:80])

    def _collect_mm_prompt_snapshot_locked(self, ctx: Mapping[str, Any]) -> MmPromptSnapshot:
        """interact-013: 锁内 snapshot —— 仅拷贝 self.* 可变字段与 ctx，不做 IO。

        必须在持有 self._lock 状态下调用（调用方负责）。返回 frozen dataclass，
        后续 render 阶段在锁外纯函数运行。
        """
        try:
            face_ids_raw = ctx.get("face_ids") or []
            face_ids = tuple(str(x) for x in face_ids_raw if str(x).strip())
        except Exception:  # noqa: BLE001
            face_ids = tuple()
        return MmPromptSnapshot(
            profile_store=self.profile_store,
            topic_preferences=dict(self._topic_preferences),
            group_template_override=(
                tuple(self._group_template_override)
                if self._group_template_override else None
            ),
            current_emotion_label=str(self._current_emotion_label or "").strip(),
            rule_id=str(ctx.get("rule_id") or "").strip(),
            caption=str(ctx.get("caption") or "").strip(),
            hint=str(ctx.get("hint") or "").strip(),
            face_ids=face_ids,
            ctx_emotion_label=str(ctx.get("emotion_label") or "").strip(),
        )

    @staticmethod
    def _render_mm_prompt_from_snapshot(snapshot: MmPromptSnapshot) -> Optional[str]:
        """interact-013: 锁外纯渲染 —— 不取 self._lock，profile_store.load IO 在此发生。

        与原 _build_mm_system_prompt_unlocked 输出等价（含 base prompt + scene/emotion/prefer/face）。
        异常 fail-soft：profile_store IO 失败时退化为不含 profile 段的 prompt。
        """
        # --- base prompt（含 profile + prefer + group_override）---
        prefer = dict(snapshot.topic_preferences)
        prefer_hint_base = ""
        if prefer:
            top = sorted(prefer.items(), key=lambda kv: kv[1], reverse=True)[:5]
            keys = "、".join(k for k, _ in top)
            prefer_hint_base = f"用户感兴趣的话题包括：{keys}。优先围绕这些聊。"
        group_hint = ""
        if snapshot.group_template_override:
            try:
                lead = str(snapshot.group_template_override[0]).strip()
            except Exception:  # noqa: BLE001
                lead = ""
            if lead:
                group_hint = (
                    f"群体场景偏好：现在有多位用户在场，用 \"{lead}\" 这类群体口吻开口，"
                    "不要单独称呼某个 profile。"
                )

        def _join(*parts: str) -> Optional[str]:
            xs = [p for p in parts if p]
            return "\n".join(xs) if xs else None

        base: Optional[str]
        if snapshot.profile_store is None:
            base = _join(prefer_hint_base, group_hint)
        else:
            try:
                from coco.profile import build_system_prompt as _bsp
                from coco.llm import SYSTEM_PROMPT as _BASE
                prof = snapshot.profile_store.load()
                sp = _bsp(prof, base=_BASE)
                base = _join(sp or "", prefer_hint_base, group_hint)
            except Exception as e:  # noqa: BLE001
                log.warning("[proactive] render: build_system_prompt failed: %s: %s",
                            type(e).__name__, e)
                base = _join(prefer_hint_base, group_hint)
        base = base or ""

        # --- MM 专属段 ---
        rule_lookup = {
            "dark_silence": "环境偏暗且用户已经一段时间没有说话，可能需要灯光提示或轻松开口。",
            "motion_greet": "检测到用户在视野内有移动/经过，但已经一段时间没有交互，是个打招呼契机。",
        }
        scene_parts = []
        if snapshot.rule_id:
            scene_parts.append(
                f"触发规则：{snapshot.rule_id}（"
                f"{rule_lookup.get(snapshot.rule_id, '多模态触发')}）"
            )
        if snapshot.caption:
            scene_parts.append(f"当前场景描述：{snapshot.caption}")
        if snapshot.hint:
            scene_parts.append(f"建议参考开口意图：{snapshot.hint}")
        scene_desc = "\n".join(scene_parts) if scene_parts else ""

        emotion_label = (snapshot.ctx_emotion_label or snapshot.current_emotion_label or "").strip()
        emotion_hint = f"当前用户情绪：{emotion_label}。语气与之匹配。" if emotion_label else ""

        prefer_hint = ""
        if prefer:
            top = sorted(prefer.items(), key=lambda kv: kv[1], reverse=True)[:5]
            keys = "、".join(k for k, _ in top)
            prefer_hint = f"用户偏好话题（TopK）：{keys}。围绕这些展开。"

        face_hint = ""
        if snapshot.face_ids:
            ids = list(snapshot.face_ids)[:5]
            if ids:
                face_hint = f"在场用户 face_id：{', '.join(ids)}。"

        mm_section_parts = [
            "[MM 主动话题上下文]",
            scene_desc,
            emotion_hint,
            prefer_hint,
            face_hint,
            "请基于上述场景说一句自然、简短（<=30 字）的主动话题。",
        ]
        mm_section = "\n".join(p for p in mm_section_parts if p)

        if base:
            return f"{base}\n\n{mm_section}"
        return mm_section

    def _build_mm_system_prompt_unlocked(self, ctx: Dict[str, Any]) -> Optional[str]:
        """interact-012/013: MM proactive system_prompt 便利包装。

        interact-013 后已拆为 _collect_mm_prompt_snapshot_locked + _render_mm_prompt_from_snapshot
        两步：本包装锁内 collect 锁外 render，保持原对外语义与 verify_interact_012 V4 兼容。
        """
        with self._lock:
            snapshot = self._collect_mm_prompt_snapshot_locked(ctx)
        return self._render_mm_prompt_from_snapshot(snapshot)

    def _build_system_prompt(self) -> Optional[str]:
        # companion-009: 即使没 profile_store，只要 set_topic_preferences 被调过，
        # 也把 prefer_topics 拼进一个轻量 system_prompt 让 LLM 偏向用户兴趣。
        with self._lock:
            prefer = dict(self._topic_preferences)
            group_override = self._group_template_override
        prefer_hint = ""
        if prefer:
            top = sorted(prefer.items(), key=lambda kv: kv[1], reverse=True)[:5]
            keys = "、".join(k for k, _ in top)
            prefer_hint = f"用户感兴趣的话题包括：{keys}。优先围绕这些聊。"

        # companion-011: group_mode override → 注入群体场景偏好（用第一个 phrase 作为开场暗示）
        group_hint = ""
        if group_override:
            try:
                lead = str(group_override[0]).strip()
            except Exception:  # noqa: BLE001
                lead = ""
            if lead:
                group_hint = f"群体场景偏好：现在有多位用户在场，用 \"{lead}\" 这类群体口吻开口，不要单独称呼某个 profile。"

        def _join(*parts: str) -> Optional[str]:
            xs = [p for p in parts if p]
            return "\n".join(xs) if xs else None

        if self.profile_store is None:
            return _join(prefer_hint, group_hint)
        try:
            from coco.profile import build_system_prompt
            from coco.llm import SYSTEM_PROMPT as _BASE
            prof = self.profile_store.load()
            sp = build_system_prompt(prof, base=_BASE)
            return _join(sp or "", prefer_hint, group_hint)
        except Exception as e:  # noqa: BLE001
            log.warning("[proactive] build_system_prompt failed: %s: %s", type(e).__name__, e)
            return _join(prefer_hint, group_hint)

    # ------------------------------------------------------------------
    # 后台线程
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        assert self._stop_event is not None
        ev = self._stop_event
        try:
            while not ev.wait(timeout=self.config.tick_s):
                try:
                    # companion-010 / infra-009: 先 tick coordinator（到期还原 prefer），
                    # 再 maybe_trigger，让本轮选 seed 看到最新的 prefer。
                    coord = self._emotion_alert_coord
                    if coord is not None:
                        try:
                            tick_fn = getattr(coord, "tick", None)
                            if callable(tick_fn):
                                tick_fn(now=self.clock())
                        except Exception as e:  # noqa: BLE001
                            log.warning("[proactive] emotion_alert_coord.tick failed: %s: %s",
                                        type(e).__name__, e)
                    self.maybe_trigger()
                except Exception as e:  # noqa: BLE001
                    log.warning("[proactive] tick error: %s", e)
        finally:
            log.info("[proactive] scheduler stopped stats=%s", self.stats)


__all__ = [
    "ProactiveConfig",
    "ProactiveScheduler",
    "ProactiveStats",
    "config_from_env",
    "proactive_enabled_from_env",
    "proactive_arbitration_enabled_from_env",
    "DEFAULT_IDLE_S",
    "DEFAULT_COOLDOWN_S",
    "DEFAULT_MAX_PER_HOUR",
    "DEFAULT_TICK_S",
    "DEFAULT_TOPIC_SEED",
]
