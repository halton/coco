"""scripts/verify_companion_008.py — companion-008 跨 session UserProfile 持久化 verification.

按 feature_list.json 中 companion-008 verification 字段实施 sim-first 验证。
所有用例都用 tempfile 隔离的 root，绝不动用户真实 ~/.coco/profiles/。

V1  default OFF：env COCO_PROFILE_PERSIST 未设 / =0 → profile_persist_enabled_from_env False
V2  enabled → 创建 alice profile → 文件落盘 ~/.coco/profiles/<sha1>.json
V3  hydrate：重启 PersistentProfileStore 读到 alice 的全字段
V4  nickname 合并：同 face_id 改 nickname → 新 profile_id；interests 合并去重保 LRU
V5  profile_id sha1 跨进程一致（用 subprocess 真起新解释器复算）
V6  atomic write：tmp + replace 不留半截文件；并发 save 不交错
V7  损坏 JSON → 移到 _corrupt/ + emit profile.corrupt + 启动不崩
V8  schema_version 不匹配 → _legacy_v<n>/ + emit profile.schema_mismatch
V9  路径注入 ('../etc/passwd' / 含 / \\ / unicode 字符) profile_id 拒绝 save/load
V10 回归 companion-004（ProfileStore）/ companion-006（MultiProfileStore）API 行为不变
V11 dialog_summary 截断到 keep_dialog_summary（默认 10）
V12 hydrate_all：损坏文件被隔离后，剩余正常文件全部 load 成功
V13 fail-soft：root 父目录写权限受限时 save 抛 OSError（caller 决定是否 swallow）；不破坏内存状态
V14 normalize_nickname：unicode NFKC + lower + strip — "Alice"/" alice "/"ＡＬＩＣＥ" 同 id
V15 emit payload 字段齐全（profile_id / reason / moved_to）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coco.companion.profile_persist import (  # noqa: E402
    DEFAULT_DIALOG_SUMMARY_KEEP,
    PROFILE_ID_LEN,
    SCHEMA_VERSION,
    PersistedProfile,
    PersistentProfileStore,
    compute_profile_id,
    default_persist_root,
    is_valid_profile_id,
    merge_lists_lru,
    normalize_nickname,
    profile_persist_enabled_from_env,
)


PASS = []
FAIL = []


def _ok(name: str, msg: str = "") -> None:
    PASS.append(name)
    print(f"  PASS {name}{(' — ' + msg) if msg else ''}", flush=True)


def _bad(name: str, err: str) -> None:
    FAIL.append((name, err))
    print(f"  FAIL {name} — {err}", flush=True)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _EmitCapture:
    def __init__(self) -> None:
        self.events = []
        self._lock = threading.Lock()

    def __call__(self, event: str, **payload):
        with self._lock:
            self.events.append((event, dict(payload)))

    def of(self, event: str):
        return [p for e, p in self.events if e == event]


def _new_store(root: Path, capture=None) -> PersistentProfileStore:
    return PersistentProfileStore(root=root, emit_fn=capture)


# ---------------------------------------------------------------------------
# V1 default OFF
# ---------------------------------------------------------------------------


def v1_default_off(_tmp: Path) -> None:
    name = "V1_default_off"
    try:
        # 不设 env → False
        env = {k: v for k, v in os.environ.items() if k != "COCO_PROFILE_PERSIST"}
        assert profile_persist_enabled_from_env(env) is False, "未设时应 False"
        # =0 → False
        assert profile_persist_enabled_from_env({"COCO_PROFILE_PERSIST": "0"}) is False
        # =1 → True
        assert profile_persist_enabled_from_env({"COCO_PROFILE_PERSIST": "1"}) is True
        assert profile_persist_enabled_from_env({"COCO_PROFILE_PERSIST": "true"}) is True
        # 任何非 1/true/yes/on 都 False
        assert profile_persist_enabled_from_env({"COCO_PROFILE_PERSIST": "foo"}) is False
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V2 (end-to-end) bridge persist：模拟 ProfileSwitcher.observe → bridge.on_switch
# → 实际文件落盘 <HOME>/.coco/profiles/<sha1>.json（HOME=tempdir）
# ---------------------------------------------------------------------------


def v2_endtoend_bridge_persist(tmp: Path) -> None:
    name = "V2_endtoend_bridge_persist"
    try:
        from coco.companion.profile_switcher import MultiProfileStore
        from coco.companion.profile_persist_bridge import ProfilePersistBridge

        # 用 HOME=tmp 走真实路径计算
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(tmp)
        try:
            pp_root = default_persist_root()
            assert str(pp_root).startswith(str(tmp)), f"persist root 应在 tmp 下: {pp_root}"
            pp_root.mkdir(parents=True, exist_ok=True)

            multi_root = tmp / "multi"
            multi_root.mkdir(parents=True, exist_ok=True)
            multi = MultiProfileStore(root=multi_root)
            store = PersistentProfileStore(root=pp_root)
            bridge = ProfilePersistBridge(
                persist_store=store,
                multi_store=multi,
                dialog_summary_fn=lambda: "alice 喜欢音乐和画画",
            )

            # 模拟 ProfileSwitcher.observe：切到 alice 用户、add interests
            multi.set_active_user("alice")
            multi.set_name("Alice")
            multi.add_interest("音乐")
            multi.add_interest("画画")
            multi.add_goal("学钢琴")

            # 模拟 switch out：bridge.on_switch(prev='alice', curr=None)
            bridge.on_switch("alice", None)

            # 验：文件确实落盘
            face_id = "alice"  # face_id_for_user_fn=None 时退化为 user_id
            pid = compute_profile_id(face_id, "Alice")
            disk_path = pp_root / f"{pid}.json"
            assert disk_path.exists(), f"端到端落盘失败: {disk_path}; 现有: {list(pp_root.iterdir())}"
            on_disk = json.loads(disk_path.read_text(encoding="utf-8"))
            assert on_disk["nickname"] == "Alice"
            assert "音乐" in on_disk["interests"], f"interests 漏: {on_disk['interests']}"
            assert "画画" in on_disk["interests"]
            assert "学钢琴" in on_disk["goals"]
            # dialog_summary 也被拷贝
            assert any("音乐" in s for s in on_disk.get("dialog_summary", [])), \
                f"dialog_summary 漏: {on_disk.get('dialog_summary')}"
            _ok(name, f"pid={pid} path={disk_path.name}")
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V3 (end-to-end) bridge hydrate：第二次启动 → hydrate → MultiProfileStore.load()
# 读到的 UserProfile.interests 含上次写入项
# ---------------------------------------------------------------------------


def v3_endtoend_bridge_hydrate(tmp: Path) -> None:
    name = "V3_endtoend_bridge_hydrate"
    try:
        from coco.companion.profile_switcher import MultiProfileStore
        from coco.companion.profile_persist_bridge import ProfilePersistBridge

        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(tmp)
        try:
            pp_root = default_persist_root()
            pp_root.mkdir(parents=True, exist_ok=True)

            # —— Session 1：写入 alice profile 到磁盘 ——
            multi_root_1 = tmp / "multi1"
            multi_root_1.mkdir(parents=True, exist_ok=True)
            multi1 = MultiProfileStore(root=multi_root_1)
            store1 = PersistentProfileStore(root=pp_root)
            bridge1 = ProfilePersistBridge(
                persist_store=store1, multi_store=multi1,
            )
            multi1.set_active_user("alice")
            multi1.set_name("Alice")
            multi1.add_interest("音乐")
            multi1.add_interest("画画")
            bridge1.on_switch("alice", None)
            del bridge1, store1, multi1  # 模拟进程退出

            # —— Session 2：新进程 → hydrate → 回灌 ——
            multi_root_2 = tmp / "multi2"
            multi_root_2.mkdir(parents=True, exist_ok=True)
            multi2 = MultiProfileStore(root=multi_root_2)
            store2 = PersistentProfileStore(root=pp_root)
            bridge2 = ProfilePersistBridge(
                persist_store=store2, multi_store=multi2,
            )
            n_hyd = bridge2.hydrate_into_multi_store()
            assert n_hyd >= 1, f"hydrate 数应 >=1: {n_hyd}"

            # 第二次启动后 MultiProfileStore.load() 应能读到 Alice 的 interests
            multi2.set_active_user("Alice")  # bridge 用 nickname 作 active_user
            up = multi2.load()
            assert up.name == "Alice", f"name 漏: {up.name}"
            assert "音乐" in (up.interests or []), \
                f"hydrate 后 interests 漏: {up.interests}"
            assert "画画" in (up.interests or [])
            _ok(name, f"hydrated={n_hyd} interests={up.interests}")
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V2a (unit) create + persist —— 原 V2，保留作为 PersistentProfileStore 单元层
# ---------------------------------------------------------------------------


def v2a_create_persist(tmp: Path) -> None:
    name = "V2a_create_persist_unit"
    try:
        cap = _EmitCapture()
        store = _new_store(tmp, cap)
        pid = compute_profile_id("face_alice", "Alice")
        rec = PersistedProfile(
            profile_id=pid,
            nickname="Alice",
            interests=["音乐", "画画"],
            goals=["学钢琴"],
            dialog_summary=["前面聊到：你好；你叫什么"],
        )
        path = store.save(rec)
        assert path.exists(), f"文件未落盘: {path}"
        assert path.name == f"{pid}.json", f"文件名不符: {path.name}"
        # 文件内容
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["profile_id"] == pid
        assert on_disk["nickname"] == "Alice"
        assert on_disk["interests"] == ["音乐", "画画"]
        assert on_disk["schema_version"] == SCHEMA_VERSION
        assert on_disk["created_ts"] > 0
        assert on_disk["updated_ts"] >= on_disk["created_ts"]
        # emit
        persisted = cap.of("profile.persisted")
        assert len(persisted) == 1, f"profile.persisted 未触发: {cap.events}"
        assert persisted[0]["profile_id"] == pid
        _ok(name, f"id={pid}")
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V3a (unit) hydrate —— 原 V3，保留作为 PersistentProfileStore 单元层
# ---------------------------------------------------------------------------


def v3a_hydrate(tmp: Path) -> None:
    name = "V3a_hydrate_unit"
    try:
        store1 = _new_store(tmp)
        pid = compute_profile_id("face_alice", "Alice")
        store1.save(PersistedProfile(
            profile_id=pid, nickname="Alice",
            interests=["音乐"], goals=["学钢琴"],
            dialog_summary=["s1", "s2"],
        ))
        # 新实例（模拟重启）
        cap = _EmitCapture()
        store2 = PersistentProfileStore(root=tmp, emit_fn=cap)
        all_recs = store2.hydrate_all()
        assert pid in all_recs, f"hydrate 未取到 {pid}: {list(all_recs)}"
        rec = all_recs[pid]
        assert rec.nickname == "Alice"
        assert rec.interests == ["音乐"]
        assert rec.goals == ["学钢琴"]
        assert rec.dialog_summary == ["s1", "s2"]
        # 单点 load 也行
        rec2 = store2.load(pid)
        assert rec2 is not None and rec2.profile_id == pid
        # emit profile.hydrated
        h = cap.of("profile.hydrated")
        assert len(h) == 1 and h[0]["count"] == 1
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V4 nickname 改 → 新 profile_id；interests merge LRU
# ---------------------------------------------------------------------------


def v4_nickname_change_merge(tmp: Path) -> None:
    name = "V4_nickname_change_merge"
    try:
        # 同 face_id，nickname 不同 → profile_id 必须不同（hash 输入变了）
        pid_a = compute_profile_id("face_alice", "Alice")
        pid_b = compute_profile_id("face_alice", "Alicia")
        assert pid_a != pid_b, "nickname 不同应生成不同 profile_id"
        # interests merge LRU：[a,b,c] + [b,d] → [a,c,b,d]
        merged = merge_lists_lru(["a", "b", "c"], ["b", "d"], cap=10)
        assert merged == ["a", "c", "b", "d"], f"merge 错: {merged}"
        # cap 截尾保新
        merged2 = merge_lists_lru(["a", "b", "c", "d"], ["e"], cap=3)
        assert merged2 == ["c", "d", "e"], f"cap 截尾: {merged2}"
        # 同 face_id 同 nickname → 同 profile_id（稳定）
        assert compute_profile_id("face_alice", "Alice") == pid_a
        _ok(name, f"a={pid_a} b={pid_b}")
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V5 profile_id 跨进程一致（subprocess 真起新解释器）
# ---------------------------------------------------------------------------


def v5_cross_process_stable(_tmp: Path) -> None:
    name = "V5_cross_process_stable"
    try:
        pid_inproc = compute_profile_id("face_xyz", "Bob")
        # subprocess 用 PYTHONHASHSEED=random 强制不同 hash，验证我们没用 builtin hash()
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = "random"
        code = (
            "import sys; sys.path.insert(0, %r);"
            "from coco.companion.profile_persist import compute_profile_id;"
            "print(compute_profile_id('face_xyz','Bob'))"
        ) % str(ROOT)
        out = subprocess.check_output([sys.executable, "-c", code], env=env, timeout=30).decode().strip()
        assert out == pid_inproc, f"跨进程不一致: inproc={pid_inproc} subproc={out}"
        # 再跑一次（不同 random seed），仍应相同
        out2 = subprocess.check_output([sys.executable, "-c", code], env=env, timeout=30).decode().strip()
        assert out2 == pid_inproc
        _ok(name, f"id={pid_inproc}")
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V6 atomic write + 并发
# ---------------------------------------------------------------------------


def v6_atomic_concurrent(tmp: Path) -> None:
    name = "V6_atomic_concurrent"
    try:
        store = _new_store(tmp)
        pid = compute_profile_id("face_t", "T")
        # 并发 save：每个线程写不同 interests 列表；最终文件解析 OK，是其中之一完整版本
        N = 20
        errs = []

        def worker(i: int) -> None:
            try:
                store.save(PersistedProfile(
                    profile_id=pid, nickname="T",
                    interests=[f"i{i}", f"j{i}"],
                ))
            except Exception as e:
                errs.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errs, f"并发 save 抛错: {errs}"
        # 最终文件存在且可 parse
        path = tmp / f"{pid}.json"
        assert path.exists()
        obj = json.loads(path.read_text(encoding="utf-8"))
        assert obj["profile_id"] == pid
        assert isinstance(obj["interests"], list)
        # 不应有遗留 .tmp 文件
        leftovers = list(tmp.glob("*.tmp"))
        assert not leftovers, f"遗留 tmp: {leftovers}"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V7 损坏 JSON → _corrupt
# ---------------------------------------------------------------------------


def v7_corrupt_quarantine(tmp: Path) -> None:
    name = "V7_corrupt_quarantine"
    try:
        cap = _EmitCapture()
        store = _new_store(tmp, cap)
        pid = compute_profile_id("face_c", "C")
        bad_path = tmp / f"{pid}.json"
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_path.write_text("{not valid json", encoding="utf-8")  # 故意损坏
        rec = store.load(pid)
        assert rec is None, "损坏文件应返回 None"
        # 已移到 _corrupt
        assert not bad_path.exists(), "损坏文件未移走"
        moved = tmp / "_corrupt" / f"{pid}.json.bak"
        assert moved.exists(), f"未在 _corrupt 找到: {list((tmp / '_corrupt').iterdir())}"
        # emit
        ev = cap.of("profile.corrupt")
        assert len(ev) == 1
        assert ev[0]["profile_id"] == pid
        assert "moved_to" in ev[0]
        # hydrate_all 也不崩
        all_recs = store.hydrate_all()
        assert pid not in all_recs
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V8 schema_version 不匹配 → _legacy_v<n>
# ---------------------------------------------------------------------------


def v8_schema_legacy(tmp: Path) -> None:
    name = "V8_schema_legacy"
    try:
        cap = _EmitCapture()
        store = _new_store(tmp, cap)
        pid = compute_profile_id("face_l", "L")
        legacy_obj = {
            "profile_id": pid, "nickname": "L",
            "interests": [], "goals": [],
            "schema_version": 0,  # 老版本
        }
        legacy_path = tmp / f"{pid}.json"
        legacy_path.write_text(json.dumps(legacy_obj), encoding="utf-8")
        rec = store.load(pid)
        assert rec is None, "schema 不匹配应返回 None（不覆盖）"
        assert not legacy_path.exists(), "legacy 文件未移走"
        moved = tmp / "_legacy_v0" / f"{pid}.json"
        assert moved.exists(), f"未在 _legacy_v0 找到"
        ev = cap.of("profile.schema_mismatch")
        assert len(ev) == 1
        assert ev[0]["schema_version"] == 0 and ev[0]["expected"] == SCHEMA_VERSION
        # 同样：未来高版本也走 _legacy_vN
        cap2 = _EmitCapture()
        store2 = _new_store(tmp, cap2)
        pid2 = compute_profile_id("face_l2", "L2")
        future_obj = {**legacy_obj, "profile_id": pid2, "schema_version": 99}
        future_path = tmp / f"{pid2}.json"
        future_path.write_text(json.dumps(future_obj), encoding="utf-8")
        assert store2.load(pid2) is None
        assert (tmp / "_legacy_v99" / f"{pid2}.json").exists()
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V9 路径注入拒绝
# ---------------------------------------------------------------------------


def v9_path_injection_reject(tmp: Path) -> None:
    name = "V9_path_injection_reject"
    try:
        store = _new_store(tmp)
        bad_ids = [
            "../etc/passwd",
            "../../tmp/x",
            "abc/def",
            "abc\\def",
            "ALICE",          # 大写非法
            "abcdef",         # 长度不足
            "a" * 13,         # 长度超
            "g" * PROFILE_ID_LEN,   # 含非 hex 字符
            "",
            None,
            "../../../home/user/.ssh/id_rsa",
        ]
        for bid in bad_ids:
            assert not is_valid_profile_id(bid or ""), f"应拒绝: {bid!r}"
            # save 抛 ValueError
            try:
                store.save(PersistedProfile(profile_id=bid or "", nickname="x"))
            except (ValueError, TypeError):
                pass
            else:
                raise AssertionError(f"save({bid!r}) 应抛 ValueError")
            # load 不抛，但返回 None
            rec = store.load(bid or "")
            assert rec is None, f"load({bid!r}) 应返回 None"
        # 合法 id 12 hex 通过
        good_id = "0123456789ab"
        assert is_valid_profile_id(good_id)
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V10 回归 companion-004 / companion-006 API 不变
# ---------------------------------------------------------------------------


def v10_regression_v4_v6(tmp: Path) -> None:
    name = "V10_regression_v4_v6"
    try:
        # companion-004 ProfileStore：用 tmp path，行为应完全不变
        from coco.profile import ProfileStore, UserProfile
        ps_path = tmp / "v4_profile.json"
        ps = ProfileStore(path=ps_path)
        p = ps.set_name("alice")
        assert p.name == "alice"
        ps.add_interest("music")
        ps.add_interest("art")
        loaded = ps.load()
        assert loaded.name == "alice" and "music" in loaded.interests
        # companion-006 MultiProfileStore：路径策略不变
        from coco.companion.profile_switcher import MultiProfileStore
        mps_root = tmp / "v6_root"
        mps = MultiProfileStore(root=mps_root, active_user_id="bob")
        mps.set_name("bob")
        mps.add_interest("running")
        # 文件名仍然是 profile_<sanitized>.json（companion-006 既有约定，未被本 feature 触动）
        files = sorted(mps_root.glob("profile_*.json"))
        assert files, f"MultiProfileStore 未落盘: {list(mps_root.iterdir())}"
        # API duck-typing
        for attr in ("load", "save", "set_name", "add_interest", "add_goal"):
            assert callable(getattr(mps, attr))
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V11 dialog_summary 截断
# ---------------------------------------------------------------------------


def v11_dialog_summary_cap(tmp: Path) -> None:
    name = "V11_dialog_summary_cap"
    try:
        store = PersistentProfileStore(root=tmp, keep_dialog_summary=3)
        pid = compute_profile_id("face_d", "D")
        store.save(PersistedProfile(
            profile_id=pid, nickname="D",
            dialog_summary=["s1", "s2", "s3", "s4", "s5"],
        ))
        rec = store.load(pid)
        assert rec is not None
        assert rec.dialog_summary == ["s3", "s4", "s5"], f"截断错: {rec.dialog_summary}"
        # 默认 keep=10 时不截断
        store2 = PersistentProfileStore(root=tmp / "x")
        assert store2.keep_dialog_summary == DEFAULT_DIALOG_SUMMARY_KEEP
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V12 hydrate_all 隔离损坏 + 正常仍通过
# ---------------------------------------------------------------------------


def v12_hydrate_mixed(tmp: Path) -> None:
    name = "V12_hydrate_mixed"
    try:
        cap = _EmitCapture()
        store = PersistentProfileStore(root=tmp, emit_fn=cap)
        # 一个 OK
        pid_ok = compute_profile_id("face_ok", "Ok")
        store.save(PersistedProfile(profile_id=pid_ok, nickname="Ok"))
        # 一个 corrupt
        pid_bad = compute_profile_id("face_bad", "Bad")
        (tmp / f"{pid_bad}.json").write_text("xxx", encoding="utf-8")
        # 一个 schema 不匹配
        pid_legacy = compute_profile_id("face_lg", "Lg")
        (tmp / f"{pid_legacy}.json").write_text(
            json.dumps({"profile_id": pid_legacy, "schema_version": 0}), encoding="utf-8")
        # 一个非合法文件名（应 skip 不动）
        (tmp / "not_a_profile.json").write_text("{}", encoding="utf-8")

        cap2 = _EmitCapture()
        store2 = PersistentProfileStore(root=tmp, emit_fn=cap2)
        result = store2.hydrate_all()
        assert pid_ok in result, f"OK 没 hydrate: {list(result)}"
        assert pid_bad not in result
        assert pid_legacy not in result
        # 隔离目录确认
        assert (tmp / "_corrupt" / f"{pid_bad}.json.bak").exists()
        assert (tmp / "_legacy_v0" / f"{pid_legacy}.json").exists()
        # 非 profile 文件未动
        assert (tmp / "not_a_profile.json").exists()
        # emit
        assert cap2.of("profile.hydrated")[0]["count"] == 1
        assert len(cap2.of("profile.corrupt")) == 1
        assert len(cap2.of("profile.schema_mismatch")) == 1
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V13 graceful degradation：root 创建失败时 save 抛 OSError，不污染内存
# ---------------------------------------------------------------------------


def v13_root_unwritable(tmp: Path) -> None:
    name = "V13_root_unwritable"
    try:
        # 用一个文件占位代替目录 → mkdir 会成功（已存在），写入 JSON 时 open 抛 NotADirectory
        # 这里改用：root 是某个不存在的非法路径（不可创建），save 抛 OSError
        bad_root = tmp / "file_blocking"
        bad_root.write_text("blocking file", encoding="utf-8")  # root 占位为文件
        # 期望：mkdir parents=True 在此情况下抛 FileExistsError（root 是文件不是目录）
        store = PersistentProfileStore(root=bad_root)
        pid = compute_profile_id("face_z", "Z")
        try:
            store.save(PersistedProfile(profile_id=pid, nickname="Z"))
        except (OSError, FileExistsError, NotADirectoryError):
            pass
        else:
            raise AssertionError("root 不可写时 save 应抛 OSError 系列")
        # store 对象本身仍可用（不进入坏状态）
        assert store.root == bad_root
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V14 normalize_nickname unicode
# ---------------------------------------------------------------------------


def v14_normalize_nickname(_tmp: Path) -> None:
    name = "V14_normalize_nickname"
    try:
        # 大小写 + strip + NFKC (全角→半角) → 同 id
        a = compute_profile_id("face", "Alice")
        b = compute_profile_id("face", "  alice  ")
        c = compute_profile_id("face", "ＡＬＩＣＥ")  # 全角
        assert a == b == c, f"unicode 同 id: {a} {b} {c}"
        # 中文 nickname
        zh = compute_profile_id("face", "可可")
        assert is_valid_profile_id(zh), f"zh id 非法: {zh}"
        # face_id None / "" 容忍
        e1 = compute_profile_id(None, "x")
        e2 = compute_profile_id("", "x")
        assert e1 == e2 and is_valid_profile_id(e1)
        # nickname None / "" 容忍且不撞 (None vs "x")
        n1 = compute_profile_id("face", None)
        n2 = compute_profile_id("face", "")
        assert n1 == n2
        assert n1 != a
        # normalize_nickname 单测
        assert normalize_nickname(None) == ""
        assert normalize_nickname("") == ""
        assert normalize_nickname("  Alice  ") == "alice"
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# V15 emit payload 字段齐全
# ---------------------------------------------------------------------------


def v15_emit_payload(tmp: Path) -> None:
    name = "V15_emit_payload"
    try:
        cap = _EmitCapture()
        store = PersistentProfileStore(root=tmp, emit_fn=cap)
        pid = compute_profile_id("face_p", "P")
        store.save(PersistedProfile(profile_id=pid, nickname="P"))
        ev = cap.of("profile.persisted")[0]
        assert ev["profile_id"] == pid
        assert "path" in ev and ev["path"].endswith(f"{pid}.json")
        # corrupt
        bad = compute_profile_id("face_bx", "Bx")
        (tmp / f"{bad}.json").write_text("nope", encoding="utf-8")
        store.load(bad)
        cev = cap.of("profile.corrupt")[0]
        assert cev["profile_id"] == bad
        assert "reason" in cev
        assert "moved_to" in cev
        # hydrate
        store.hydrate_all()
        hev = cap.of("profile.hydrated")[-1]
        assert "count" in hev
        assert "root" in hev
        _ok(name)
    except Exception as e:
        _bad(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 70, flush=True)
    print("companion-008 verify (sim-first, tempfile-only, no real ~/.coco)", flush=True)
    print("=" * 70, flush=True)

    cases = [
        v1_default_off,
        v2_endtoend_bridge_persist,
        v3_endtoend_bridge_hydrate,
        v2a_create_persist,
        v3a_hydrate,
        v4_nickname_change_merge,
        v5_cross_process_stable,
        v6_atomic_concurrent,
        v7_corrupt_quarantine,
        v8_schema_legacy,
        v9_path_injection_reject,
        v10_regression_v4_v6,
        v11_dialog_summary_cap,
        v12_hydrate_mixed,
        v13_root_unwritable,
        v14_normalize_nickname,
        v15_emit_payload,
    ]
    for case in cases:
        td = Path(tempfile.mkdtemp(prefix=f"coco_v008_{case.__name__}_"))
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
