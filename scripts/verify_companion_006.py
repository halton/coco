"""scripts/verify_companion_006.py — companion-006 多用户档案切换 verification.

按 feature_list.json 中 companion-006 verification 字段实施 sim-first 验证。
所有用例都是 in-process（不起真 face camera / 真 tts），ProfileSwitcher /
MultiProfileStore 注入 fake clock + capture-list tts，避免 sleep。

V1  face_id A→B 切换（debounce 满足）
V2  短暂遮挡 A→unknown→A 不切（pending 被重置 / unknown 不主动重置 active）
V3  greet cooldown：同一 user 30min 内不重复致意
V4  default profile 时未识别脸不触发切换（observe(None) 不切到 None）
V5  切换后 ProactiveScheduler 取新兴趣（profile_store.load() 拿到新 user 的兴趣）
V6  dialog history 隔离（A 切到 B 后 DialogMemory clear）
V7  regression：companion-004 ProfileExtractor 仍能写入 active store 路径
V8  per-user 文件路径独立 + sanitize 名字 collide 检查
V9  fail-soft：tts 抛错不影响 active 切换 / 不阻塞下一次 observe
V10 force_set 绕过 debounce 但不触发 greet
V11 emit_fn 接收 component="companion" 事件 payload 字段齐全
V12 default_OFF：build_profile_switcher(config.enabled=False) → None
V13 多用户名字 unicode（中文 alice / 中文）安全 sanitize 不 crash
V14 同一 active 的 observe 是 no-op（不 emit / 不 greet）
V15 main wiring smoke：类型一致 + ProfileStore-compatible API duck-typing
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

# repo root on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coco.companion.profile_switcher import (  # noqa: E402
    DEFAULT_DEBOUNCE_S,
    DEFAULT_GREET_COOLDOWN_S,
    MultiProfileStore,
    MultiUserConfig,
    ProfileSwitcher,
    build_multi_profile_store,
    build_profile_switcher,
    multi_user_config_from_env,
)
from coco.profile import ProfileStore, UserProfile  # noqa: E402


PASS = []
FAIL = []


def _ok(name: str, msg: str = "") -> None:
    PASS.append(name)
    print(f"  PASS {name}{(' — ' + msg) if msg else ''}", flush=True)


def _bad(name: str, err: str) -> None:
    FAIL.append((name, err))
    print(f"  FAIL {name} — {err}", flush=True)


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _make_switcher(
    tmpdir: Path,
    *,
    debounce_s: float = DEFAULT_DEBOUNCE_S,
    greet_cooldown_s: float = DEFAULT_GREET_COOLDOWN_S,
    greet_enabled: bool = True,
    greet_template: str = "欢迎回来 {name}",
    initial: str | None = None,
):
    cfg = MultiUserConfig(
        enabled=True,
        debounce_s=debounce_s,
        greet_cooldown_s=greet_cooldown_s,
        greet_enabled=greet_enabled,
        greet_template=greet_template,
    )
    store = MultiProfileStore(root=tmpdir, active_user_id=initial)
    clock = FakeClock()
    tts_calls: list[str] = []
    emit_calls: list[tuple[str, dict]] = []
    on_switch_calls: list[tuple] = []

    def _tts(s: str) -> None:
        tts_calls.append(s)

    def _emit(event: str, **payload):
        emit_calls.append((event, dict(payload)))

    def _on_switch(prev, curr):
        on_switch_calls.append((prev, curr))

    sw = ProfileSwitcher(
        store=store,
        config=cfg,
        tts_say_fn=_tts,
        emit_fn=_emit,
        on_switch=_on_switch,
        clock=clock,
    )
    return sw, store, clock, tts_calls, emit_calls, on_switch_calls


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def v1_basic_switch(tmpdir: Path) -> None:
    name = "V1_face_id_A_to_B"
    try:
        sw, store, clk, tts, emits, sws = _make_switcher(tmpdir, debounce_s=5.0)
        # 先建立 active=alice：连续 observe alice，跨过 debounce
        for _ in range(2):
            sw.observe("alice")
        clk.advance(5.0)
        sw.observe("alice")
        assert store.active_user_id == "alice", f"expect alice active, got {store.active_user_id}"

        # B 出现：连续 observe bob，跨 debounce
        sw.observe("bob")  # 启动 pending
        # 立刻再 observe 不会切（debounce 0s 累积）
        sw.observe("bob")
        assert store.active_user_id == "alice", "切换不该在 debounce 满足前发生"
        clk.advance(5.0)
        sw.observe("bob")  # 满足 debounce → 切
        assert store.active_user_id == "bob", f"expect bob active, got {store.active_user_id}"

        # emit 至少一次 user_profile_switched alice→bob
        switched = [e for e in emits if e[0] == "companion.user_profile_switched"]
        assert switched, f"无 user_profile_switched 事件: {emits}"
        last = switched[-1][1]
        assert last["from_user"] == "alice" and last["to_user"] == "bob", f"payload 不对: {last}"
        assert last.get("component") == "companion", f"component 不是 companion: {last}"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def v2_brief_occlusion_no_switch(tmpdir: Path) -> None:
    name = "V2_brief_occlusion_no_switch"
    try:
        sw, store, clk, tts, emits, sws = _make_switcher(tmpdir, debounce_s=5.0)
        sw.observe("alice")
        clk.advance(5.0)
        sw.observe("alice")
        assert store.active_user_id == "alice"

        # 短暂遮挡（observe(None)）— 应不重置 active
        sw.observe(None)
        sw.observe(None)
        assert store.active_user_id == "alice", "observe(None) 不应改变 active"

        # 然后 alice 又回来 — 仍是 alice，不应触发新 emit
        prev_emit_count = len(emits)
        sw.observe("alice")
        assert store.active_user_id == "alice"
        # 不应额外 emit（同 user）
        new_switched = [e for e in emits[prev_emit_count:] if e[0] == "companion.user_profile_switched"]
        assert not new_switched, f"alice→alice 不该 emit switch: {new_switched}"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def v3_greet_cooldown(tmpdir: Path) -> None:
    name = "V3_greet_cooldown_30min"
    try:
        sw, store, clk, tts, emits, sws = _make_switcher(
            tmpdir, debounce_s=1.0, greet_cooldown_s=1800.0,
        )
        sw.observe("alice")
        clk.advance(2.0)
        sw.observe("alice")
        assert "欢迎回来 alice" in tts, f"首次切换应 greet，tts={tts}"

        # 切到 bob
        sw.observe("bob")
        clk.advance(2.0)
        sw.observe("bob")
        assert "欢迎回来 bob" in tts, f"切到 bob 应 greet，tts={tts}"

        # 立刻切回 alice — cooldown 内不应再致意
        sw.observe("alice")
        clk.advance(2.0)
        sw.observe("alice")
        # tts 列表里 alice 还是只有一次
        assert tts.count("欢迎回来 alice") == 1, (
            f"alice 在 cooldown 内应只致意 1 次，tts={tts}"
        )
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def v4_default_profile_no_face(tmpdir: Path) -> None:
    name = "V4_default_profile_unknown_no_switch"
    try:
        sw, store, clk, tts, emits, sws = _make_switcher(tmpdir, debounce_s=1.0)
        # 起始 active=None；连续 observe(None) 不应改变 active 也不 emit
        for _ in range(5):
            sw.observe(None)
            clk.advance(0.5)
        assert store.active_user_id is None, f"initial active 应保持 None: {store.active_user_id}"
        switched = [e for e in emits if e[0] == "companion.user_profile_switched"]
        assert not switched, f"observe(None) 不该 emit: {switched}"
        assert not tts, f"observe(None) 不该 greet: {tts}"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def v5_proactive_reads_new_interests(tmpdir: Path) -> None:
    name = "V5_proactive_reads_new_user_interests"
    try:
        sw, store, clk, tts, emits, sws = _make_switcher(tmpdir, debounce_s=0.0)
        # 准备 alice / bob 的 profile 文件（写到 sanitize 后的路径）
        store.set_active_user("alice")
        store.update_field(name="alice", interests=["数学", "钢琴"])
        store.set_active_user("bob")
        store.update_field(name="bob", interests=["足球"])
        store.set_active_user(None)  # 复位

        # 模拟切换到 alice
        sw.observe("alice")
        clk.advance(0.1)
        sw.observe("alice")
        p = store.load()
        assert p.name == "alice" and "数学" in p.interests, f"alice profile 不对: {p}"

        # 切换到 bob
        sw.observe("bob")
        clk.advance(0.1)
        sw.observe("bob")
        p = store.load()
        assert p.name == "bob" and "足球" in p.interests, f"bob profile 不对: {p}"
        assert "数学" not in p.interests, "bob 不应继承 alice 的 interests"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def v6_dialog_history_isolation(tmpdir: Path) -> None:
    name = "V6_dialog_history_per_profile_isolation"
    try:
        from coco.dialog import DialogMemory

        dm = DialogMemory(max_turns=8, idle_timeout_s=120.0)
        dm.append("我是 alice", "你好 alice")
        dm.append("alice 喜欢钢琴", "好的我记下了")
        assert len(dm.recent_turns()) == 2, f"alice 历史 2 轮: {dm.recent_turns()}"

        sw, store, clk, tts, emits, sws = _make_switcher(tmpdir, debounce_s=0.0)
        # 把 dm.clear 接到 on_switch
        captured_calls = []
        def _on_switch(prev, curr):
            captured_calls.append((prev, curr))
            dm.clear()
        sw._on_switch = _on_switch  # 注入

        sw.observe("bob")
        clk.advance(0.1)
        sw.observe("bob")
        assert captured_calls, "on_switch 应被触发"
        assert len(dm.recent_turns()) == 0, f"切换后 history 应清空: {dm.recent_turns()}"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def v7_regression_extractor_writes_active(tmpdir: Path) -> None:
    name = "V7_regression_extractor_writes_active_store"
    try:
        from coco.profile import extract_profile_signals

        store = MultiProfileStore(root=tmpdir, active_user_id="alice")
        # 模拟 InteractSession 调用 ProfileExtractor + add_interest
        sigs = extract_profile_signals("我喜欢钢琴")
        assert "interests" in sigs, f"extractor 没抽到: {sigs}"
        for it in sigs["interests"]:
            store.add_interest(it)

        p = store.load()
        assert "钢琴" in p.interests, f"alice store 应有钢琴: {p}"

        # 切到 bob 不该看到 alice 的兴趣
        store.set_active_user("bob")
        p2 = store.load()
        assert "钢琴" not in p2.interests, f"bob store 不该有 alice 的钢琴: {p2}"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def v8_per_user_paths(tmpdir: Path) -> None:
    name = "V8_per_user_file_paths"
    try:
        store = MultiProfileStore(root=tmpdir, active_user_id=None)
        default_path = store.active_path()
        assert default_path.name == "profile.json", f"None active 应映射 profile.json: {default_path}"

        store.set_active_user("alice")
        alice_path = store.active_path()
        assert alice_path != default_path, "alice path 应与 default 不同"
        assert alice_path.name.startswith("profile_alice_"), f"alice 文件名: {alice_path.name}"
        assert alice_path.name.endswith(".json")

        store.set_active_user("bob")
        bob_path = store.active_path()
        assert bob_path != alice_path, "bob/alice path 必须不同"

        # 确实落盘到不同文件
        store.set_active_user("alice")
        store.update_field(name="alice")
        store.set_active_user("bob")
        store.update_field(name="bob")
        assert alice_path.exists() and bob_path.exists(), "两个文件都应该存在"
        a = json.loads(alice_path.read_text(encoding="utf-8"))
        b = json.loads(bob_path.read_text(encoding="utf-8"))
        assert a["name"] == "alice" and b["name"] == "bob", f"内容串了: a={a} b={b}"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def v9_fail_soft_tts(tmpdir: Path) -> None:
    name = "V9_fail_soft_tts_failure"
    try:
        cfg = MultiUserConfig(enabled=True, debounce_s=0.0, greet_cooldown_s=1800.0)
        store = MultiProfileStore(root=tmpdir, active_user_id=None)
        clk = FakeClock()

        def _bad_tts(s: str) -> None:
            raise RuntimeError("tts boom")

        sw = ProfileSwitcher(store=store, config=cfg, tts_say_fn=_bad_tts, clock=clk)
        sw.observe("alice")
        clk.advance(0.1)
        sw.observe("alice")
        # 即使 tts 抛错，active 必须切到 alice
        assert store.active_user_id == "alice", f"tts 失败不应阻断切换: {store.active_user_id}"

        # 下一次 observe 仍能继续工作
        sw.observe("bob")
        clk.advance(0.1)
        sw.observe("bob")
        assert store.active_user_id == "bob", f"后续 observe 仍应正常: {store.active_user_id}"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def v10_force_set_no_greet(tmpdir: Path) -> None:
    name = "V10_force_set_no_greet"
    try:
        sw, store, clk, tts, emits, sws = _make_switcher(tmpdir, debounce_s=999.0)
        sw.force_set("alice")
        assert store.active_user_id == "alice"
        switched = [e for e in emits if e[0] == "companion.user_profile_switched"]
        assert switched, "force_set 也应 emit switched"
        assert switched[-1][1]["reason"] == "force", f"reason 应是 force: {switched[-1]}"
        # force 不致意
        assert not tts, f"force_set 不应 greet: {tts}"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def v11_emit_payload_complete(tmpdir: Path) -> None:
    name = "V11_emit_component_and_payload"
    try:
        sw, store, clk, tts, emits, sws = _make_switcher(
            tmpdir, debounce_s=0.0, greet_cooldown_s=10.0,
        )
        sw.observe("alice")
        clk.advance(0.1)
        sw.observe("alice")
        switch_evts = [e for e in emits if e[0] == "companion.user_profile_switched"]
        assert switch_evts, "应有 switched 事件"
        sp = switch_evts[-1][1]
        for k in ("component", "from_user", "to_user", "reason"):
            assert k in sp, f"switched payload 缺字段 {k}: {sp}"
        assert sp["component"] == "companion"

        greet_evts = [e for e in emits if e[0] == "companion.user_profile_greeted"]
        assert greet_evts, f"应有 greeted 事件: {emits}"
        gp = greet_evts[-1][1]
        for k in ("component", "user", "utterance"):
            assert k in gp, f"greeted payload 缺字段 {k}: {gp}"
        assert gp["user"] == "alice" and "alice" in gp["utterance"]
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def v12_default_off(tmpdir: Path) -> None:
    name = "V12_default_OFF"
    try:
        sw = build_profile_switcher(config=MultiUserConfig(enabled=False))
        assert sw is None, f"enabled=False 必须返回 None: {sw}"
        # env 默认值
        os.environ.pop("COCO_MULTI_USER", None)
        cfg = multi_user_config_from_env()
        assert cfg.enabled is False, f"COCO_MULTI_USER 未设时 enabled 应 False: {cfg}"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def v13_unicode_name_safe(tmpdir: Path) -> None:
    name = "V13_unicode_name_sanitize"
    try:
        store = MultiProfileStore(root=tmpdir, active_user_id="小明")
        path1 = store.active_path()
        # 不抛 + 路径安全（只含 ascii）
        assert all(c.isascii() for c in path1.name), f"sanitized 路径应是 ascii: {path1.name}"
        store.update_field(name="小明", interests=["阅读"])
        p = store.load()
        assert p.name == "小明", f"name 字段保留 unicode: {p}"

        # 另一个名字 sanitize 不冲突
        store.set_active_user("小红")
        path2 = store.active_path()
        assert path1 != path2, f"不同 unicode 名字 path 必须不同: {path1} vs {path2}"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def v14_same_user_noop(tmpdir: Path) -> None:
    name = "V14_same_active_observe_noop"
    try:
        sw, store, clk, tts, emits, sws = _make_switcher(tmpdir, debounce_s=0.5)
        sw.observe("alice")
        clk.advance(1.0)
        sw.observe("alice")
        assert store.active_user_id == "alice"
        n_emits_after_first = len(emits)
        n_tts = len(tts)

        # 重复 10 次
        for _ in range(10):
            clk.advance(0.1)
            sw.observe("alice")
        assert len(emits) == n_emits_after_first, f"重复 alice 不应额外 emit: {emits[n_emits_after_first:]}"
        assert len(tts) == n_tts, f"重复 alice 不应额外 greet: {tts[n_tts:]}"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def v15_main_wiring_smoke(tmpdir: Path) -> None:
    """与 ProfileStore duck-typing 接口对齐：InteractSession / ProactiveScheduler 不
    会因为换成 MultiProfileStore 而 attribute 缺失。"""
    name = "V15_profile_store_duck_typing"
    try:
        ms = MultiProfileStore(root=tmpdir, active_user_id="alice")
        for attr in ("load", "save", "update_field", "add_interest", "add_goal", "set_name", "reset"):
            assert hasattr(ms, attr), f"MultiProfileStore 缺 {attr}"
            assert callable(getattr(ms, attr)), f"{attr} 不可调用"
        # 行为兼容：load 返回 UserProfile
        p = ms.load()
        assert isinstance(p, UserProfile), f"load() 应返回 UserProfile: {type(p)}"
        # save / set_name 也兼容
        p2 = ms.set_name("alice")
        assert p2.name == "alice"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 70, flush=True)
    print("companion-006 verify (sim-first, no real face / no real tts)", flush=True)
    print("=" * 70, flush=True)

    cases = [
        v1_basic_switch,
        v2_brief_occlusion_no_switch,
        v3_greet_cooldown,
        v4_default_profile_no_face,
        v5_proactive_reads_new_interests,
        v6_dialog_history_isolation,
        v7_regression_extractor_writes_active,
        v8_per_user_paths,
        v9_fail_soft_tts,
        v10_force_set_no_greet,
        v11_emit_payload_complete,
        v12_default_off,
        v13_unicode_name_safe,
        v14_same_user_noop,
        v15_main_wiring_smoke,
    ]
    for case in cases:
        td = Path(tempfile.mkdtemp(prefix=f"coco_v006_{case.__name__}_"))
        try:
            case(td)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    print("-" * 70, flush=True)
    print(f"PASS: {len(PASS)}  FAIL: {len(FAIL)}", flush=True)
    if FAIL:
        print("FAILED CASES:", flush=True)
        for n, e in FAIL:
            print(f"  - {n}: {e}", flush=True)
        return 1
    print("ALL PASS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
