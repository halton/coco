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

import os
import threading
import time
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
    """订阅回调是否走 daemon 线程异步投递；True 时订阅慢不阻塞 sequencer。"""


def sequencer_config_from_env(env: Optional[dict] = None) -> SequencerConfig:
    """从环境变量读 SequencerConfig；缺省全默认。default-OFF: COCO_ROBOT_SEQ 未设 → enabled=False。"""
    env = env if env is not None else os.environ
    enabled = env.get("COCO_ROBOT_SEQ", "").strip() in ("1", "true", "yes", "on")
    poll = env.get("COCO_ROBOT_SEQ_POLL_S", "").strip()
    try:
        poll_v = float(poll) if poll else 0.02
    except ValueError:
        poll_v = 0.02
    sub_async = env.get("COCO_ROBOT_SEQ_SUB_ASYNC", "1").strip() not in ("0", "false", "no", "off")
    return SequencerConfig(enabled=enabled, cancel_poll_interval_s=poll_v, subscribe_async=sub_async)


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

    # ------------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------------
    def subscribe(self, callback: Callable[[str, dict], None]) -> None:
        """注册订阅回调；签名 callback(event_name: str, payload: dict)。"""
        with self._lock:
            self._subs.append(callback)

    def _dispatch(self, event: str, payload: dict) -> None:
        """投递给订阅者；subscribe_async=True 时丢 daemon 线程，慢回调不阻塞 sequencer。"""
        with self._lock:
            subs = list(self._subs)
        if not subs:
            return
        for cb in subs:
            if self.config.subscribe_async:
                t = threading.Thread(
                    target=self._safe_invoke, args=(cb, event, payload), daemon=True
                )
                t.start()
            else:
                self._safe_invoke(cb, event, payload)

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
]
