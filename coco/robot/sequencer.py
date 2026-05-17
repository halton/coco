"""robot-006: RobotSequencer — mockup-sim 多动作序列编排 + emit + cancel.

设计：
- 接受 list[Action] (head_turn / nod / look_at / sleep / wakeup) 串行执行。
- 每个 action 完成 emit `robot.action_done{action_id, type, duration_ms, ts}`。
- cancel(): 立即停当前 action + 跳 pending，emit `robot.sequence_cancelled{cancelled_n}`。
- 业务订阅回压：通过 subscribe(callback) 注册订阅；emit 走 try/except + (可选)
  ThreadPoolExecutor 投递，订阅方处理慢不阻塞 sequencer 主线程。
- mockup-sim 模式下 zero-hardware：action.execute(robot) 中走 SDK 调用；
  当 robot is None（fixture）或 robot 是 MagicMock，行为完全等价于"调用日志"。

class 始终可构造（always-on）；main wire 仅在 COCO_ROBOT_SEQ=1 时启用，
default-OFF bytewise 与基线等价。

followed_from: robot-005
"""

from __future__ import annotations

import math
import os
import queue as _queue
import sys as _sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence

# emit fallback：模块顶 import，避免 hot path lazy import 抖动（robot-005 教训）
try:
    from coco.logging_setup import emit as _DEFAULT_EMIT
except Exception:  # pragma: no cover
    def _DEFAULT_EMIT(component_event: str, message: str = "", **payload: Any) -> None:
        return None


ActionType = str  # "head_turn" | "nod" | "look_at" | "sleep" | "wakeup"

_VALID_TYPES = frozenset({"head_turn", "nod", "look_at", "sleep", "wakeup"})


@dataclass
class Action:
    """单个动作描述。

    Attributes:
        action_id: 业务侧分配的唯一 id，用于关联 emit 事件。
        type: 动作类型；必须在 _VALID_TYPES 中。
        params: 该动作的参数（如 yaw_deg / amplitude_deg / duration_s 等）。
        duration_s: 期望耗时（秒）；mockup-sim 内 sleep 实际等待这个时长。
    """

    action_id: str
    type: ActionType
    params: dict = field(default_factory=dict)
    duration_s: float = 0.3

    def __post_init__(self) -> None:
        if self.type not in _VALID_TYPES:
            raise ValueError(f"unknown action type: {self.type!r}; valid={sorted(_VALID_TYPES)}")
        if not isinstance(self.duration_s, (int, float)) or self.duration_s < 0:
            raise ValueError(f"duration_s must be non-negative: {self.duration_s!r}")


@dataclass
class SequencerConfig:
    enabled: bool = False
    """env gate；class 始终可构造，但 main 只在 enabled=True 时 wire。"""

    cancel_poll_interval_s: float = 0.02
    """cancel 轮询粒度：等待动作"完成"时每隔多久检查一次 _cancel_flag。"""

    subscribe_async: bool = True
    """订阅回调是否走有界 ThreadPoolExecutor 异步投递；True 时订阅慢不阻塞 sequencer。"""

    pool_size: int = 4
    """robot-007: subscribe dispatch ThreadPoolExecutor 工作线程数。"""

    queue_max: int = 64
    """robot-007: dispatch 任务队列上限。"""

    overflow_policy: str = "drop_oldest"
    """robot-007: 满队列回压策略 — 'drop_oldest' | 'drop_new' | 'block'。"""

    shutdown_timeout_s: float = 2.0
    """robot-012: shutdown(wait=True, timeout=…) 默认值；signal handler / atexit 共用。
    env: COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S (float seconds)，默认 2.0。
    robot-014: 非有限值 (inf/-inf/nan) 或 <=0 → 降级到 2.0, 防 thread.join(timeout=inf) 永久阻塞。"""

    def __post_init__(self) -> None:
        # robot-014: shutdown_timeout_s 硬化 — inf/nan/<=0 一律降级到 2.0
        try:
            v = float(self.shutdown_timeout_s)
        except (TypeError, ValueError):
            v = 2.0
        if not math.isfinite(v) or v <= 0:
            v = 2.0
        # 通过 object.__setattr__ 兼容 frozen dataclass / 普通 dataclass 都安全
        object.__setattr__(self, "shutdown_timeout_s", v)


_VALID_OVERFLOW = frozenset({"drop_oldest", "drop_new", "block"})


def _parse_pos_int(raw: str, default: int) -> int:
    """robot-007: 解析正整数 env，非法/空 → default。"""
    if not raw:
        return default
    try:
        v = int(raw.strip())
        if v < 1:
            return default
        return v
    except (ValueError, AttributeError):
        return default


def sequencer_config_from_env(env: Optional[dict] = None) -> SequencerConfig:
    """从环境变量读 SequencerConfig；缺省全默认。default-OFF: COCO_ROBOT_SEQ 未设 → enabled=False."""
    env = env if env is not None else os.environ
    enabled = env.get("COCO_ROBOT_SEQ", "").strip() in ("1", "true", "yes", "on")
    poll = env.get("COCO_ROBOT_SEQ_POLL_S", "").strip()
    try:
        poll_v = float(poll) if poll else 0.02
    except ValueError:
        poll_v = 0.02
    sub_async = env.get("COCO_ROBOT_SEQ_SUB_ASYNC", "1").strip() not in ("0", "false", "no", "off")
    pool_size = _parse_pos_int(env.get("COCO_ROBOT_SEQ_POOL_SIZE", ""), 4)
    queue_max = _parse_pos_int(env.get("COCO_ROBOT_SEQ_QUEUE_MAX", ""), 64)
    overflow = env.get("COCO_ROBOT_SEQ_OVERFLOW", "").strip().lower() or "drop_oldest"
    if overflow not in _VALID_OVERFLOW:
        overflow = "drop_oldest"
    # robot-012: shutdown timeout 配置化（signal handler + atexit 共用）
    # robot-014: 加 math.isfinite() 拦 inf/-inf/nan, 防 thread.join(timeout=inf) 永久阻塞
    raw_st = env.get("COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S", "").strip()
    try:
        shutdown_to = float(raw_st) if raw_st else 2.0
        if not math.isfinite(shutdown_to) or shutdown_to <= 0:
            shutdown_to = 2.0
    except ValueError:
        shutdown_to = 2.0
    return SequencerConfig(
        enabled=enabled,
        cancel_poll_interval_s=poll_v,
        subscribe_async=sub_async,
        pool_size=pool_size,
        queue_max=queue_max,
        overflow_policy=overflow,
        shutdown_timeout_s=shutdown_to,
    )


def install_signal_shutdown_handler(
    sequencer: "RobotSequencer",
    timeout_s: float = 2.0,
    env: Optional[dict] = None,
    signal_module: Optional[Any] = None,
    signames: Sequence[str] = ("SIGTERM", "SIGINT"),
) -> List[str]:
    """robot-012: 注册 SIGTERM/SIGINT signal handler 调 sequencer.shutdown(timeout).

    Default-OFF: env COCO_ROBOT_SIGTERM_HANDLE 未设 → 不注册任何 handler, 返回 []
    (bytewise 等价基线).

    设计:
    - 重入安全: 内部 flag 防 handler 触发后再次进入.
    - chain prev handler: 每个 signum 单独保存 prev (避免 closure 绑错).
    - Windows / 缺信号兼容: getattr 兜底, signal.signal 异常 (ValueError/OSError) skip.
    - signal_module 参数允许测试注入 fake module (验 Windows path / 错误兜底).

    返回: 成功注册的 signame 列表 (env OFF 或失败 → []).
    """
    e = env if env is not None else os.environ
    if e.get("COCO_ROBOT_SIGTERM_HANDLE", "").strip().lower() not in (
        "1", "true", "yes", "on",
    ):
        return []

    if signal_module is None:
        import signal as signal_module  # type: ignore

    seq_ref = sequencer
    seq_to = float(timeout_s)
    in_progress = {"flag": False}
    prev_handlers: dict = {}

    def _handler(signum, _frame):  # noqa: ANN001
        # 重入: 第二次进入直接 return, 不再调 shutdown
        if in_progress["flag"]:
            return
        in_progress["flag"] = True
        try:
            seq_ref.shutdown(wait=True, timeout=seq_to)
        except Exception:  # noqa: BLE001
            pass
        prev = prev_handlers.get(signum)
        try:
            sig_dfl = getattr(signal_module, "SIG_DFL", None)
            sig_ign = getattr(signal_module, "SIG_IGN", None)
            if callable(prev) and prev not in (sig_dfl, sig_ign, None):
                prev(signum, _frame)
        except Exception:  # noqa: BLE001
            pass

    registered: List[str] = []
    for name in signames:
        signum = getattr(signal_module, name, None)
        if signum is None:
            continue
        try:
            prev_handlers[signum] = signal_module.getsignal(signum)
            signal_module.signal(signum, _handler)
            registered.append(name)
        except (ValueError, OSError, AttributeError):
            # ValueError: not in main thread; OSError: not supported on platform
            continue
    return registered


class RobotSequencer:
    """多动作序列编排器。

    用法：
        seq = RobotSequencer(robot=reachy_mini)
        seq.subscribe(lambda ev, payload: ...)
        seq.run([Action("a1","head_turn",{"yaw_deg":20}, 0.3), ...])
        # 在另一个线程 / 回调里调 seq.cancel() 中断

    线程模型：
        - run() 在调用线程同步执行；调用方可在 worker 线程跑。
        - cancel() 可在任意线程调用。
        - subscribe 回调默认在 daemon 线程异步派发（subscribe_async=True）；
          关闭后退化为 sequencer 主线程同步派发（仅测试用）。
    """

    def __init__(
        self,
        robot: Optional[Any] = None,
        config: Optional[SequencerConfig] = None,
        emit_fn: Optional[Callable[..., None]] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.robot = robot
        self.config = config or SequencerConfig()
        self._emit = emit_fn if emit_fn is not None else _DEFAULT_EMIT
        self.clock = clock

        self._cancel_flag = threading.Event()
        self._running = False
        self._lock = threading.Lock()
        self._subs: List[Callable[[str, dict], None]] = []
        self._last_seq_id = 0

        # robot-007: 有界 ThreadPoolExecutor + bounded queue。
        # 仅在 subscribe_async=True 时构造；否则同步派发不需要 pool。
        # 设计：N 个 worker 线程各自从同一个 bounded queue.get() 阻塞拉取任务。
        # queue 真实反映 backlog（不像 executor 内部任务队列那样无界）。
        self._dispatch_executor: Optional[ThreadPoolExecutor] = None
        self._dispatch_queue: Optional[_queue.Queue] = None
        self._dispatch_workers: List[threading.Thread] = []
        self._dispatch_stop = threading.Event()
        self._dropped_n = 0  # 累计 drop 计数（滚动）
        self._drop_lock = threading.Lock()
        if self.config.subscribe_async:
            self._init_dispatch_pool()

        # robot-009: action enqueue worker。所有触发源（proactive / future
        # GroupModeCoord / Gesture）通过 enqueue(action) 走同一道口，由单一
        # action_worker 串行消费（复用 run([action]) 内部串行/cancel/emit 语义）。
        # 单 worker 保证 action 之间不并发——多个 trigger 同时来会按 FIFO 排队，
        # 不会出现两个动作同时驱动 robot SDK 的竞争。
        # bounded queue（容量默认 = config.queue_max）+ overflow_policy 与 dispatch
        # 同套策略；满时 emit `robot.enqueue_dropped`。
        # shutdown() 时一并清空 + 注入 sentinel + join worker。
        self._action_queue: Optional[_queue.Queue] = None
        self._action_worker: Optional[threading.Thread] = None
        self._action_stop = threading.Event()
        self._enqueue_dropped_n = 0
        self._enqueue_drop_lock = threading.Lock()
        # robot-013: busy_count — cumulative busy-hit 计数 (queue_full 类 drop
        # 的累计, monotonic 自启动)。仅在 COCO_ROBOT_BUSY_METRIC=ON 时附加到
        # robot.enqueue_dropped emit; default-OFF bytewise 与 main 等价 (policy
        # 字段 additive 常开)。
        self._busy_count = 0
        self._busy_count_lock = threading.Lock()
        self._is_shutdown = False
        self._init_action_worker()

    def _init_action_worker(self) -> None:
        """robot-009: 启动单一 action worker 消费 enqueue 投递的 action。"""
        self._action_queue = _queue.Queue(maxsize=max(1, int(self.config.queue_max)))
        self._action_stop.clear()
        th = threading.Thread(
            target=self._action_worker_loop,
            daemon=True,
            name="coco-robot-seq-action",
        )
        th.start()
        self._action_worker = th

    def _action_worker_loop(self) -> None:
        """Worker：阻塞 get → 串行 run([action]) → 再 get 下一条。"""
        q = self._action_queue
        assert q is not None
        while not self._action_stop.is_set():
            try:
                item = q.get(timeout=0.05)
            except _queue.Empty:
                continue
            if item is None:
                # sentinel
                break
            action = item
            try:
                # 串行调用现有 run() 主循环（emit + dispatch + cancel 一致）。
                # 若已 _running（罕见：外部正调 run），等下一轮再消费。
                if self._running:
                    # put back to tail and yield
                    try:
                        q.put_nowait(action)
                    except _queue.Full:
                        self._on_enqueue_drop(reason="busy_requeue_full")
                    time.sleep(self.config.cancel_poll_interval_s)
                    continue
                self.run([action])
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[robot_seq] action worker run failed: {type(exc).__name__}: {exc!r}",
                    file=_sys.stderr,
                )

    def enqueue(self, action: "Action") -> bool:
        """robot-009: 非阻塞投递一个 action 给 sequencer 内部 worker 执行。

        返回 True 表示已入队（worker 将异步执行），False 表示被 drop（队列满
        或 sequencer 已 shutdown）。drop 时 emit `robot.enqueue_dropped`。

        所有外部触发源（ProactiveScheduler / GroupModeCoord / Gesture）一律走
        本入口，统一调度，消除外部 daemon thread + queue/executor 双 fan-out。
        """
        if not isinstance(action, Action):
            raise TypeError(f"enqueue expects Action, got {type(action).__name__}")
        if self._is_shutdown or self._action_queue is None:
            # 已 shutdown：best-effort no-op + emit dropped
            self._on_enqueue_drop(reason="shutdown")
            return False
        q = self._action_queue
        policy = self.config.overflow_policy
        if policy == "block":
            try:
                q.put(action, timeout=1.0)
                return True
            except _queue.Full:
                self._on_enqueue_drop(reason="block_timeout")
                return False
        try:
            q.put_nowait(action)
            return True
        except _queue.Full:
            if policy == "drop_new":
                self._on_enqueue_drop(reason="drop_new")
                return False
            # drop_oldest
            try:
                q.get_nowait()
                self._on_enqueue_drop(reason="drop_oldest")
            except _queue.Empty:
                pass
            try:
                q.put_nowait(action)
                return True
            except _queue.Full:
                self._on_enqueue_drop(reason="drop_oldest_retry_full")
                return False

    def _on_enqueue_drop(self, reason: str) -> None:
        """robot-009: emit robot.enqueue_dropped；enqueue_dropped_n 累计。

        robot-013: additive 字段:
        - policy: 当前 overflow_policy (常开, additive)
        - busy_count: cumulative busy-hit 计数 (env-gated
          COCO_ROBOT_BUSY_METRIC, default-OFF; 仅 queue_full 类 reason 计入).
        """
        with self._enqueue_drop_lock:
            self._enqueue_dropped_n += 1
            n = self._enqueue_dropped_n
        # robot-013: busy_count — queue_full 类 reason 才计入
        # (shutdown 类不算 busy)
        _busy_reasons = (
            "drop_oldest",
            "drop_new",
            "block_timeout",
            "busy_requeue_full",
            "drop_oldest_retry_full",
        )
        busy_n: Optional[int] = None
        if reason in _busy_reasons:
            with self._busy_count_lock:
                self._busy_count += 1
                busy_n = self._busy_count
        # env gate: default-OFF; 未设 → 不附 busy_count, bytewise 等价
        _env_on = os.environ.get("COCO_ROBOT_BUSY_METRIC", "").strip().lower() in (
            "1", "true", "yes", "on",
        )
        payload: dict = dict(
            reason=reason,
            queue_max=self.config.queue_max,
            dropped_n=n,
            policy=self.config.overflow_policy,  # additive 常开
        )
        if _env_on and busy_n is not None:
            payload["busy_count"] = busy_n
        try:
            self._emit("robot.enqueue_dropped", **payload)
        except Exception as exc:  # noqa: BLE001
            print(f"[robot_seq] emit enqueue_dropped failed: {exc!r}", file=_sys.stderr)

    def is_shutdown(self) -> bool:
        """robot-009: 是否已 shutdown。"""
        return self._is_shutdown

    def _init_dispatch_pool(self) -> None:
        """robot-007: bounded queue + N worker threads pulling from it."""
        self._dispatch_queue = _queue.Queue(maxsize=max(1, int(self.config.queue_max)))
        self._dispatch_stop.clear()
        n = max(1, int(self.config.pool_size))
        # ThreadPoolExecutor 用于记账/shutdown 一致性；worker 主体是自管线程，
        # 这样能精确控制：worker 必须先消费完一条才能拉下一条 → queue=真实 backlog。
        self._dispatch_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="coco-robot-seq-disp-meta"
        )
        for i in range(n):
            th = threading.Thread(
                target=self._dispatch_worker_loop,
                daemon=True,
                name=f"coco-robot-seq-w{i}",
            )
            th.start()
            self._dispatch_workers.append(th)

    def _dispatch_worker_loop(self) -> None:
        """Worker 主循环：阻塞 get → invoke → 完成后再 get 下一条。"""
        q = self._dispatch_queue
        assert q is not None
        while not self._dispatch_stop.is_set():
            try:
                item = q.get(timeout=0.05)
            except _queue.Empty:
                continue
            if item is None:
                # sentinel: 推回去让其他 worker 也能退出
                try:
                    q.put_nowait(None)
                except _queue.Full:
                    pass
                break
            cb, event, payload = item
            self._safe_invoke(cb, event, payload)

    def shutdown(self, wait: bool = True, timeout: float = 2.0) -> None:
        """robot-007: 优雅停 dispatch pool。robot-009: 同时停 action worker。"""
        # robot-009: 标记已 shutdown，之后 enqueue 全部 no-op
        self._is_shutdown = True

        # robot-009: 停 action worker
        self._action_stop.set()
        aq = self._action_queue
        if aq is not None:
            try:
                while True:
                    aq.get_nowait()
            except _queue.Empty:
                pass
            try:
                aq.put_nowait(None)
            except _queue.Full:
                pass
        aw = self._action_worker
        if aw is not None and aw.is_alive():
            aw.join(timeout=timeout)
        self._action_worker = None
        self._action_queue = None

        self._dispatch_stop.set()
        q = self._dispatch_queue
        if q is not None:
            # 排空队列 + 注入 sentinel
            try:
                while True:
                    q.get_nowait()
            except _queue.Empty:
                pass
            for _ in range(len(self._dispatch_workers) or 1):
                try:
                    q.put_nowait(None)
                except _queue.Full:
                    break
        for th in self._dispatch_workers:
            if th.is_alive():
                th.join(timeout=timeout)
        self._dispatch_workers = []
        ex = self._dispatch_executor
        if ex is not None:
            try:
                ex.shutdown(wait=wait, cancel_futures=True)  # type: ignore[call-arg]
            except TypeError:
                ex.shutdown(wait=wait)
        self._dispatch_executor = None
        self._dispatch_queue = None

    # ------------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------------
    def subscribe(self, callback: Callable[[str, dict], None]) -> None:
        """注册订阅回调；签名 callback(event_name: str, payload: dict)。"""
        with self._lock:
            self._subs.append(callback)

    def _dispatch(self, event: str, payload: dict) -> None:
        """投递给订阅者；subscribe_async=True 时走有界 ThreadPoolExecutor，慢回调不阻塞 sequencer。

        robot-007: 有界 queue + 三种 overflow 策略 (drop_oldest / drop_new / block)。
        满 drop 时 emit `robot.subscribe_dropped`，dropped_n 单调累计。
        """
        with self._lock:
            subs = list(self._subs)
        if not subs:
            return
        if not self.config.subscribe_async:
            for cb in subs:
                self._safe_invoke(cb, event, payload)
            return

        # robot-007: enqueue 到 bounded queue
        q = self._dispatch_queue
        if q is None:
            # 退化路径：pool 没初始化（理论不会到这里）
            for cb in subs:
                self._safe_invoke(cb, event, payload)
            return

        policy = self.config.overflow_policy
        for cb in subs:
            item = (cb, event, payload)
            if policy == "block":
                q.put(item)
                continue
            try:
                q.put_nowait(item)
            except _queue.Full:
                if policy == "drop_new":
                    self._on_drop(event, reason="drop_new")
                else:
                    # drop_oldest: 丢一个最老的，腾位置；记录被丢的那个
                    try:
                        old = q.get_nowait()
                        old_event = old[1] if old is not None else event
                        self._on_drop(old_event, reason="drop_oldest")
                    except _queue.Empty:
                        # 极少：刚好被 consumer 取走，再塞应该成功
                        pass
                    try:
                        q.put_nowait(item)
                    except _queue.Full:
                        # 实在塞不下，记为本身被 drop
                        self._on_drop(event, reason="drop_oldest")

    def _on_drop(self, event: str, reason: str) -> None:
        """robot-007: emit robot.subscribe_dropped；dropped_n 累计。"""
        with self._drop_lock:
            self._dropped_n += 1
            n = self._dropped_n
        qmax = self.config.queue_max
        try:
            self._emit(
                "robot.subscribe_dropped",
                event=event,
                reason=reason,
                queue_max=qmax,
                dropped_n=n,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[robot_seq] emit subscribe_dropped failed: {exc!r}", file=_sys.stderr)

    @staticmethod
    def _safe_invoke(cb: Callable[[str, dict], None], event: str, payload: dict) -> None:
        try:
            cb(event, payload)
        except Exception as exc:  # noqa: BLE001
            # 订阅方异常不能影响 sequencer；仅记一行 stderr（避免 evidence 噪音）
            import sys as _sys
            print(
                f"[robot_seq] subscriber raised {type(exc).__name__}: {exc!r} on event={event!r}",
                file=_sys.stderr,
            )

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def cancel(self) -> None:
        """立刻请求中止当前 / pending action。可在任意线程调用。"""
        self._cancel_flag.set()

    def is_running(self) -> bool:
        return self._running

    def run(self, actions: Sequence[Action]) -> dict:
        """串行执行 actions。返回 summary {executed, cancelled, cancelled_n, action_dones}.

        cancelled 为 True 时，cancelled_n = len(actions) - executed。
        """
        if self._running:
            raise RuntimeError("RobotSequencer.run() called while already running")
        self._running = True
        self._cancel_flag.clear()
        self._last_seq_id += 1
        seq_id = self._last_seq_id

        executed = 0
        action_dones: List[dict] = []
        cancelled = False

        try:
            for i, a in enumerate(actions):
                if self._cancel_flag.is_set():
                    cancelled = True
                    break

                t_start = self.clock()
                # 执行动作主体：sleep 模拟耗时，期间轮询 _cancel_flag
                self._execute_action(a)

                # 等待 duration（轮询 cancel）
                remaining = a.duration_s - (self.clock() - t_start)
                while remaining > 0:
                    if self._cancel_flag.is_set():
                        cancelled = True
                        break
                    step = min(self.config.cancel_poll_interval_s, remaining)
                    time.sleep(step)
                    remaining = a.duration_s - (self.clock() - t_start)

                if cancelled:
                    break

                t_done = self.clock()
                duration_ms = int((t_done - t_start) * 1000)
                payload = {
                    "action_id": a.action_id,
                    "type": a.type,
                    "duration_ms": duration_ms,
                    "ts": t_done,
                    "seq_id": seq_id,
                }
                action_dones.append(payload)
                executed += 1

                # emit + 业务订阅派发
                try:
                    self._emit("robot.action_done", **payload)
                except Exception as exc:  # noqa: BLE001
                    import sys as _sys
                    print(f"[robot_seq] emit failed: {exc!r}", file=_sys.stderr)
                self._dispatch("robot.action_done", payload)

            if cancelled:
                cancelled_n = len(actions) - executed
                payload = {
                    "seq_id": seq_id,
                    "executed_n": executed,
                    "cancelled_n": cancelled_n,
                    "ts": self.clock(),
                }
                try:
                    self._emit("robot.sequence_cancelled", **payload)
                except Exception as exc:  # noqa: BLE001
                    import sys as _sys
                    print(f"[robot_seq] emit failed: {exc!r}", file=_sys.stderr)
                self._dispatch("robot.sequence_cancelled", payload)
        finally:
            self._running = False

        return {
            "executed": executed,
            "cancelled": cancelled,
            "cancelled_n": len(actions) - executed if cancelled else 0,
            "action_dones": action_dones,
            "seq_id": seq_id,
        }

    # ------------------------------------------------------------------
    # 动作执行（mockup-sim zero-hardware）
    # ------------------------------------------------------------------
    def _execute_action(self, a: Action) -> None:
        """对底层 robot 触发动作。robot=None 时 no-op（fixture / unit test）。

        mockup-sim daemon 下 self.robot 是 ReachyMini client；调用其 SDK 方法
        即可——daemon 自身实现"假"硬件，不接触真扭矩；此处不做额外保护。
        """
        if self.robot is None:
            return  # fixture-level zero-hardware

        try:
            if a.type == "head_turn":
                yaw = float(a.params.get("yaw_deg", 0.0))
                from coco.actions import euler_pose
                self.robot.goto_target(head=euler_pose(yaw_deg=yaw), duration=max(a.duration_s, 0.1))
            elif a.type == "nod":
                amp = float(a.params.get("amplitude_deg", 10.0))
                from coco.actions import euler_pose
                self.robot.goto_target(head=euler_pose(pitch_deg=amp), duration=max(a.duration_s, 0.1))
            elif a.type == "look_at":
                yaw = float(a.params.get("yaw_deg", 0.0))
                pitch = float(a.params.get("pitch_deg", 0.0))
                from coco.actions import euler_pose
                self.robot.goto_target(
                    head=euler_pose(yaw_deg=yaw, pitch_deg=pitch),
                    duration=max(a.duration_s, 0.1),
                )
            elif a.type == "sleep":
                # goto_sleep 是 reachy_mini API；mockup-sim 下也是 no-op
                fn = getattr(self.robot, "goto_sleep", None)
                if callable(fn):
                    fn()
            elif a.type == "wakeup":
                fn = getattr(self.robot, "wake_up", None)
                if callable(fn):
                    fn()
        except Exception as exc:  # noqa: BLE001
            # action 执行异常不应中断整个序列；记录后继续等 duration
            import sys as _sys
            print(f"[robot_seq] action {a.action_id!r} execute failed: {exc!r}", file=_sys.stderr)


__all__ = [
    "Action",
    "SequencerConfig",
    "RobotSequencer",
    "sequencer_config_from_env",
    "install_signal_shutdown_handler",
]
