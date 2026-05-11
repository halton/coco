"""vision-004 verification: 多目标人脸注视切换 (AttentionSelector).

跑法：
  uv run python scripts/verify_vision_004.py

子项：
  V1  默认 OFF — COCO_ATTENTION 未设时 load_config().attention.enabled=False
  V2  COCO_ATTENTION=1 构造成功（policy/min_focus_s/cooldown 字段对齐）
  V3  单目标：focus = 那个 track
  V4  多目标 ROUND_ROBIN：依次轮换（min_focus_s 到期才切）
  V5  LARGEST_FACE：选最大 bbox（min_focus_s 到期后才切）
  V6  NEWEST：选 last_seen_ts 最新
  V7  NAMED_FIRST：有 name 的优先于无 name
  V8  min_focus_s 防抖：保持期内即便 best 已变也不切
  V9  switch_cooldown_s：连续切换之间有冷却
  V10 所有 track 消失：focus 回 None + on_change 至少一次 target=None
  V11 stop() 干净退出（主程序集成路径：tick 线程被 stop_event 终止）
  V12 emit "vision.attention_changed"（component vision 在 AUTHORITATIVE_COMPONENTS）
       + AttentionConfig env clamp（interval_ms / min_focus_s / policy 非法回退）
  V13 on_change 在 selector._lock 之外被触发：回调内反向操作 selector
       不死锁；回调抛异常后 selector 仍可继续 select()。

retval：0 全 PASS；1 任一失败
evidence 落 evidence/vision-004/verify_summary.json
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.config import (
    AttentionConfig,
    _attention_from_env,
    load_config,
)
from coco.logging_setup import AUTHORITATIVE_COMPONENTS, setup_logging
from coco.perception.attention import (
    AttentionPolicy,
    AttentionSelector,
    AttentionTarget,
)

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

errors: List[str] = []
results: dict = {}


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok   {msg}")
    else:
        errors.append(msg)
        print(f"  FAIL {msg}")


# 鸭子类型 fake track：满足 AttentionSelector 用到的属性
@dataclass
class FakeTrack:
    track_id: int
    area: int = 1000
    last_seen_ts: float = 0.0
    name: Optional[str] = None


class FakeClock:
    """可手动推进的 monotonic 时钟。"""

    def __init__(self, t0: float = 1000.0) -> None:
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# ---------------------------------------------------------------------------
# V1 默认 OFF
# ---------------------------------------------------------------------------
print("\n[V1] 默认 OFF (COCO_ATTENTION 未设)")
env = {k: v for k, v in os.environ.items() if not k.startswith("COCO_ATTENTION")}
cfg = load_config(env=env)
check(cfg.attention.enabled is False, "默认 attention.enabled = False")
check(cfg.attention.policy == "round_robin", "默认 policy = round_robin")
check(abs(cfg.attention.min_focus_s - 3.0) < 1e-6, "默认 min_focus_s = 3.0")
check(abs(cfg.attention.switch_cooldown_s - 1.0) < 1e-6, "默认 switch_cooldown_s = 1.0")
check(cfg.attention.interval_ms == 200, "默认 interval_ms = 200")

# ---------------------------------------------------------------------------
# V2 COCO_ATTENTION=1 构造成功
# ---------------------------------------------------------------------------
print("\n[V2] COCO_ATTENTION=1 构造成功")
env2 = dict(env)
env2.update({
    "COCO_ATTENTION": "1",
    "COCO_ATTENTION_POLICY": "named_first",
    "COCO_ATTENTION_MIN_FOCUS_S": "2.5",
    "COCO_ATTENTION_SWITCH_COOLDOWN_S": "0.8",
    "COCO_ATTENTION_INTERVAL_MS": "150",
})
cfg2 = load_config(env=env2)
check(cfg2.attention.enabled is True, "enabled=True")
check(cfg2.attention.policy == "named_first", "policy=named_first")
check(abs(cfg2.attention.min_focus_s - 2.5) < 1e-6, "min_focus_s=2.5")
check(abs(cfg2.attention.switch_cooldown_s - 0.8) < 1e-6, "switch_cooldown_s=0.8")
check(cfg2.attention.interval_ms == 150, "interval_ms=150")

sel2 = AttentionSelector(
    policy=AttentionPolicy(cfg2.attention.policy),
    min_focus_s=cfg2.attention.min_focus_s,
    switch_cooldown_s=cfg2.attention.switch_cooldown_s,
)
check(sel2.policy is AttentionPolicy.NAMED_FIRST, "Selector.policy = NAMED_FIRST")
check(sel2.current() is None, "Selector 初始 current()=None")

# ---------------------------------------------------------------------------
# V3 单目标
# ---------------------------------------------------------------------------
print("\n[V3] 单目标 focus")
clock = FakeClock()
sel = AttentionSelector(policy=AttentionPolicy.LARGEST_FACE, min_focus_s=1.0,
                       switch_cooldown_s=0.0, clock=clock)
t = sel.select([FakeTrack(track_id=7, area=500, last_seen_ts=clock.t)])
check(t is not None and t.track_id == 7, "single track 被选中 (track_id=7)")
check(sel.current() is not None and sel.current().track_id == 7, "current()==7")

# ---------------------------------------------------------------------------
# V4 ROUND_ROBIN
# ---------------------------------------------------------------------------
print("\n[V4] ROUND_ROBIN 多目标循环")
clock = FakeClock()
changes: List[tuple] = []
sel = AttentionSelector(
    policy=AttentionPolicy.ROUND_ROBIN,
    min_focus_s=1.0,
    switch_cooldown_s=0.0,
    clock=clock,
    on_change=lambda p, c: changes.append((p.track_id if p else None, c.track_id if c else None)),
)
tracks = [FakeTrack(track_id=1), FakeTrack(track_id=2), FakeTrack(track_id=3)]
t = sel.select(tracks)
check(t.track_id == 1, "首次 RR 取最小 id=1")
# min_focus_s 未到，反复 select 应继续保持 1
t = sel.select(tracks)
check(t.track_id == 1, "min_focus_s 未到，维持 1")
# 推进到 1.0s 后
clock.advance(1.01)
t = sel.select(tracks)
check(t.track_id == 2, "min_focus_s 到期后切到 2")
clock.advance(1.01)
t = sel.select(tracks)
check(t.track_id == 3, "再次到期切到 3")
clock.advance(1.01)
t = sel.select(tracks)
check(t.track_id == 1, "环回到 1")
expected_seq = [(None, 1), (1, 2), (2, 3), (3, 1)]
check(changes == expected_seq, f"on_change 序列正确：{changes}")

# ---------------------------------------------------------------------------
# V5 LARGEST_FACE
# ---------------------------------------------------------------------------
print("\n[V5] LARGEST_FACE 选最大 bbox")
clock = FakeClock()
sel = AttentionSelector(policy=AttentionPolicy.LARGEST_FACE, min_focus_s=1.0,
                       switch_cooldown_s=0.0, clock=clock)
tracks = [
    FakeTrack(track_id=1, area=200),
    FakeTrack(track_id=2, area=800),
    FakeTrack(track_id=3, area=500),
]
t = sel.select(tracks)
check(t.track_id == 2, "选 area 最大的 track 2")
# 改大小：把 3 变更大；min_focus_s 未到不切
tracks[2].area = 1200
t = sel.select(tracks)
check(t.track_id == 2, "min_focus_s 未到，维持 2 (即便 3 更大)")
clock.advance(1.01)
t = sel.select(tracks)
check(t.track_id == 3, "min_focus_s 到期后切到更大的 3")

# ---------------------------------------------------------------------------
# V6 NEWEST
# ---------------------------------------------------------------------------
print("\n[V6] NEWEST 选 last_seen 最新")
clock = FakeClock()
sel = AttentionSelector(policy=AttentionPolicy.NEWEST, min_focus_s=0.0,
                       switch_cooldown_s=0.0, clock=clock)
tracks = [
    FakeTrack(track_id=1, last_seen_ts=10.0),
    FakeTrack(track_id=2, last_seen_ts=30.0),
    FakeTrack(track_id=3, last_seen_ts=20.0),
]
t = sel.select(tracks)
check(t.track_id == 2, "选 last_seen_ts 最新的 track 2 (ts=30)")
tracks[2].last_seen_ts = 50.0
t = sel.select(tracks)
check(t.track_id == 3, "newest 切到 track 3 (ts=50)")

# ---------------------------------------------------------------------------
# V7 NAMED_FIRST
# ---------------------------------------------------------------------------
print("\n[V7] NAMED_FIRST 有 name 的优先")
clock = FakeClock()
sel = AttentionSelector(policy=AttentionPolicy.NAMED_FIRST, min_focus_s=0.0,
                       switch_cooldown_s=0.0, clock=clock)
tracks = [
    FakeTrack(track_id=1, area=1000, name=None),
    FakeTrack(track_id=2, area=500, name="alice"),
    FakeTrack(track_id=3, area=300, name=None),
]
t = sel.select(tracks)
check(t.track_id == 2 and t.name == "alice", "有 name 的 alice 优先于更大的 1")
# 全无 name 回退到 largest
tracks2 = [FakeTrack(track_id=1, area=1000), FakeTrack(track_id=2, area=500)]
sel2 = AttentionSelector(policy=AttentionPolicy.NAMED_FIRST, min_focus_s=0.0,
                        switch_cooldown_s=0.0, clock=FakeClock())
t = sel2.select(tracks2)
check(t.track_id == 1, "全无 name 回退到 largest=1")
# 多个 named：取面积大
tracks3 = [
    FakeTrack(track_id=1, area=500, name="alice"),
    FakeTrack(track_id=2, area=800, name="bob"),
    FakeTrack(track_id=3, area=1000, name=None),
]
sel3 = AttentionSelector(policy=AttentionPolicy.NAMED_FIRST, min_focus_s=0.0,
                        switch_cooldown_s=0.0, clock=FakeClock())
t = sel3.select(tracks3)
check(t.track_id == 2 and t.name == "bob", "多 named 取面积大的 bob")

# ---------------------------------------------------------------------------
# V8 min_focus_s 防抖
# ---------------------------------------------------------------------------
print("\n[V8] min_focus_s 防抖")
clock = FakeClock()
changes = []
sel = AttentionSelector(
    policy=AttentionPolicy.LARGEST_FACE,
    min_focus_s=3.0,
    switch_cooldown_s=0.0,
    clock=clock,
    on_change=lambda p, c: changes.append((p.track_id if p else None, c.track_id if c else None)),
)
tracks = [FakeTrack(track_id=1, area=500), FakeTrack(track_id=2, area=300)]
sel.select(tracks)  # focus=1
# 改为 2 更大；min_focus_s 内反复 select
tracks[1].area = 2000
for _ in range(5):
    clock.advance(0.5)  # 0.5..2.5s
    t = sel.select(tracks)
    check(t.track_id == 1, f"在 min_focus_s={3.0} 内维持 1 (now={clock.t})")
clock.advance(0.6)  # 总 3.1s
t = sel.select(tracks)
check(t.track_id == 2, "min_focus_s 到期后切到 2")
check(changes == [(None, 1), (1, 2)], f"on_change 只触发两次 (start + 切换)：{changes}")

# ---------------------------------------------------------------------------
# V9 switch_cooldown_s
# ---------------------------------------------------------------------------
print("\n[V9] switch_cooldown_s")
clock = FakeClock()
changes = []
sel = AttentionSelector(
    policy=AttentionPolicy.LARGEST_FACE,
    min_focus_s=0.5,
    switch_cooldown_s=2.0,
    clock=clock,
    on_change=lambda p, c: changes.append((p.track_id if p else None, c.track_id if c else None)),
)
tracks = [FakeTrack(track_id=1, area=500), FakeTrack(track_id=2, area=300),
          FakeTrack(track_id=3, area=100)]
sel.select(tracks)  # focus=1
# 把 2 改大 → 0.6s 之后会满足 min_focus_s 但 cooldown 还未到（cooldown=2s）
clock.advance(0.6)
tracks[1].area = 2000
t = sel.select(tracks)
check(t.track_id == 1, "min_focus_s 已满但 cooldown 内不切")
clock.advance(1.5)  # 总 2.1s
t = sel.select(tracks)
check(t.track_id == 2, "cooldown 到期后切到 2")
# 切完立即再变：cooldown 重置，0.1s 后不该立刻再切
clock.advance(0.6)
tracks[2].area = 3000
t = sel.select(tracks)
check(t.track_id == 2, "刚切完，cooldown 内不再切")
clock.advance(1.6)  # 总 2.2s 自上次切换
t = sel.select(tracks)
check(t.track_id == 3, "cooldown 再次到期后切到 3")

# ---------------------------------------------------------------------------
# V10 所有 track 消失
# ---------------------------------------------------------------------------
print("\n[V10] 所有 track 消失 → focus=None + 一次 None 通知")
clock = FakeClock()
changes = []
sel = AttentionSelector(
    policy=AttentionPolicy.ROUND_ROBIN,
    min_focus_s=0.5,
    switch_cooldown_s=0.0,
    clock=clock,
    on_change=lambda p, c: changes.append((p.track_id if p else None, c.track_id if c else None)),
)
tracks = [FakeTrack(track_id=1), FakeTrack(track_id=2)]
sel.select(tracks)
clock.advance(0.6)
t = sel.select([])
check(t is None, "空集 → focus=None")
check(sel.current() is None, "current() is None")
# on_change 通知里应该有一次 (?, None)
none_events = [c for c in changes if c[1] is None]
check(len(none_events) == 1, f"恰好一次 target=None 的 on_change：{changes}")
# 再次空集不重复 emit
t = sel.select([])
check(t is None and len([c for c in changes if c[1] is None]) == 1,
      "持续空集不重复 emit None")

# ---------------------------------------------------------------------------
# V11 stop() 干净退出 (主程序 tick 线程语义)
# ---------------------------------------------------------------------------
print("\n[V11] tick 线程 stop_event 干净退出 ≤2s")
sel = AttentionSelector(policy=AttentionPolicy.LARGEST_FACE, min_focus_s=0.0,
                       switch_cooldown_s=0.0)
stop_evt = threading.Event()
ticks = {"n": 0}


class _StubTracker:
    def latest(self):
        ticks["n"] += 1
        class _Snap:
            tracks = (FakeTrack(track_id=1, area=500),)
        return _Snap()


def loop():
    tracker = _StubTracker()
    while not stop_evt.is_set():
        sel.select(list(tracker.latest().tracks))
        if stop_evt.wait(timeout=0.05):
            break


th = threading.Thread(target=loop, daemon=True)
t0 = time.monotonic()
th.start()
time.sleep(0.3)
stop_evt.set()
th.join(timeout=2.0)
elapsed = time.monotonic() - t0
check(not th.is_alive(), "线程已退出")
check(elapsed < 2.5, f"退出耗时 {elapsed:.2f}s < 2.5s")
check(ticks["n"] >= 3, f"至少 tick 过若干次 ({ticks['n']})")

# ---------------------------------------------------------------------------
# V12 emit 通路 + env clamp + AUTHORITATIVE_COMPONENTS
# ---------------------------------------------------------------------------
print("\n[V12] emit/component + env clamp")
check("vision" in AUTHORITATIVE_COMPONENTS,
      "AUTHORITATIVE_COMPONENTS 含 'vision'")

# 模拟 main.py 里的 on_change → emit
emitted = []


def _fake_emit(event, **fields):
    emitted.append((event, fields))


# 直接调 selector，on_change 内部用 _fake_emit
clock = FakeClock()
sel = AttentionSelector(
    policy=AttentionPolicy.LARGEST_FACE,
    min_focus_s=0.0,
    switch_cooldown_s=0.0,
    clock=clock,
    on_change=lambda p, c: _fake_emit(
        "vision.attention_changed",
        component="vision",
        prev_track_id=p.track_id if p else None,
        target_track_id=c.track_id if c else None,
        target_name=c.name if c else None,
    ),
)
sel.select([FakeTrack(track_id=5, area=900, name="alice")])
check(len(emitted) == 1, "emit 触发一次")
ev, fields = emitted[0]
check(ev == "vision.attention_changed", "event=vision.attention_changed")
check(fields.get("component") == "vision", "component=vision")
check(fields.get("target_track_id") == 5, "target_track_id=5")
check(fields.get("target_name") == "alice", "target_name=alice")

# env clamp 测试
clamp_env = dict(env)
clamp_env.update({
    "COCO_ATTENTION": "1",
    "COCO_ATTENTION_POLICY": "bogus_policy",
    "COCO_ATTENTION_MIN_FOCUS_S": "-5.0",       # clamp 到 0.0
    "COCO_ATTENTION_SWITCH_COOLDOWN_S": "999.0",  # clamp 到 60.0
    "COCO_ATTENTION_INTERVAL_MS": "5",          # clamp 到 50
})
ccfg = _attention_from_env(clamp_env)
check(ccfg.policy == "round_robin", "非法 policy 回退 round_robin")
check(abs(ccfg.min_focus_s - 0.0) < 1e-6, "min_focus_s 负值 clamp 到 0.0")
check(abs(ccfg.switch_cooldown_s - 60.0) < 1e-6, "switch_cooldown_s 超限 clamp 到 60.0")
check(ccfg.interval_ms == 50, "interval_ms 过低 clamp 到 50")

clamp_env2 = dict(env)
clamp_env2.update({"COCO_ATTENTION": "1", "COCO_ATTENTION_INTERVAL_MS": "9999"})
ccfg2 = _attention_from_env(clamp_env2)
check(ccfg2.interval_ms == 2000, "interval_ms 过高 clamp 到 2000")

clamp_env3 = dict(env)
clamp_env3.update({"COCO_ATTENTION": "1", "COCO_ATTENTION_INTERVAL_MS": "not_int"})
ccfg3 = _attention_from_env(clamp_env3)
check(ccfg3.interval_ms == 200, "interval_ms 非整数回退 200")

# ---------------------------------------------------------------------------
# V13 on_change 在 selector._lock 之外被 fire（不死锁 + 回调可反向调 selector）
# ---------------------------------------------------------------------------
print("\n[V13] on_change 出锁触发 (无自死锁)")
clock = FakeClock()
v13_observed: List[Optional[int]] = []
v13_callback_done = threading.Event()

# 回调里反向调 selector 的方法（current()）：如果 select() 仍在锁内 fire
# 回调，且 _lock 是非可重入的会死锁；用 RLock 则同线程不死锁但持锁时间被
# 拉长。这里用线程做"另一个调用者"模拟：回调内启线程去 select(),验证
# 主线程 fire 时锁已释放（另一线程能立刻拿到锁）。
def _v13_on_change(prev, curr):
    # 在 callback 内启动后台线程，让它对 selector 再做一次同步操作。
    # 如果 fire 还在锁内，后台线程会被阻塞到 v13_done 之后才返回；
    # 我们要求后台线程在 fire 返回之前就已完成 -> 锁必须已释放。
    finished = threading.Event()

    def _worker():
        # current() 进入 selector._lock；若主线程仍在锁内则会被阻塞
        cur = v13_sel.current()
        v13_observed.append(cur.track_id if cur else None)
        finished.set()

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    # 给 worker 200ms 拿锁；fire 在锁外时这一步会很快完成
    got = finished.wait(timeout=0.5)
    if got:
        v13_callback_done.set()

v13_sel = AttentionSelector(
    policy=AttentionPolicy.LARGEST_FACE,
    min_focus_s=0.0,
    switch_cooldown_s=0.0,
    clock=clock,
    on_change=_v13_on_change,
)
v13_sel.select([FakeTrack(track_id=42, area=500)])
check(v13_callback_done.is_set(),
      "callback 内启动的后台线程能在 fire 返回前拿到 _lock（说明 fire 在锁外）")
check(v13_observed == [42],
      f"后台线程从 current() 读到刚切好的 focus=42（实测 {v13_observed}）")

# 进一步：on_change 抛异常不应破坏 selector 后续工作（既存承诺）
v13b = AttentionSelector(
    policy=AttentionPolicy.LARGEST_FACE,
    min_focus_s=0.0,
    switch_cooldown_s=0.0,
    on_change=lambda p, c: (_ for _ in ()).throw(RuntimeError("boom")),
)
v13b.select([FakeTrack(track_id=1, area=100)])
t = v13b.select([FakeTrack(track_id=1, area=100)])
check(t is not None and t.track_id == 1, "回调抛异常后 selector 仍可工作")

# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
print("\n=== summary ===")
ok = len(errors) == 0
results = {
    "feature": "vision-004",
    "status": "PASS" if ok else "FAIL",
    "errors": errors,
}
out_dir = ROOT / "evidence" / "vision-004"
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "verify_summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))

if ok:
    print("vision-004 verification: ALL PASS (V1..V13)")
    sys.exit(0)
else:
    print(f"vision-004 verification: {len(errors)} FAIL")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
