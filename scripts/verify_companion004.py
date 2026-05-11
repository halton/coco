#!/usr/bin/env python3
"""verify_companion004.py — UserProfile 跨 session 长期记忆验证（companion-004）.

覆盖（spec 5 子项 + 集成 + 隐私 reset）：
  V1 ProfileStore round-trip：save → load 字段完全一致；atomic write（无中间半文件）
  V2 文件不存在 / disable / 损坏 → load 返回空 profile（不抛）
  V3 add_interest LRU：重复 → 移到末位；超容（≥6）丢最旧；goals 同
  V4 ProfileExtractor：12 条用例（5 名字 + 4 兴趣 + 3 目标，含负面）准确率 ≥85%
  V5 build_system_prompt：全空 → base 透传；部分字段 → 含「用户昵称：…」等格式
  V6 InteractSession 集成（向后兼容）：profile_store=None 时行为完全等价 phase-3
  V7 InteractSession + ProfileStore 端到端：
     - session1 注入 "我叫小明" → set_name 写盘 → load 返回 name='小明'
     - session2 重启用同一 path 新建 store → load 返回 name='小明'
     - 调 LLM 时 backend.chat 收到 system_prompt 含 "用户昵称：小明"
  V8 disable kill switch：COCO_PROFILE_DISABLE=1 → load/save no-op；reset 也 no-op
  V9 schema_version 不匹配 → fail-soft 返回空 profile，原文件保留
  V10 reset_profile.py：删除后 fresh start 不抛、load 返回空

evidence/companion-004/verify_summary.json 记录通过情况。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# 隔离 env：测试期临时挪走 COCO_PROFILE_PATH / DISABLE，结束恢复
_PRESERVED_ENV = ("COCO_PROFILE_PATH", "COCO_PROFILE_DISABLE")
_saved_env: Dict[str, Optional[str]] = {k: os.environ.get(k) for k in _PRESERVED_ENV}
for k in _PRESERVED_ENV:
    os.environ.pop(k, None)


def _restore_env() -> None:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@contextmanager
def temp_profile_path():
    """yield 一个临时 profile.json 路径（父目录会被建出）。结束后清干净 + 重置 disable。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "profile" / "profile.json"
        old_path = os.environ.get("COCO_PROFILE_PATH")
        old_dis = os.environ.get("COCO_PROFILE_DISABLE")
        os.environ["COCO_PROFILE_PATH"] = str(p)
        os.environ.pop("COCO_PROFILE_DISABLE", None)
        try:
            yield p
        finally:
            if old_path is None:
                os.environ.pop("COCO_PROFILE_PATH", None)
            else:
                os.environ["COCO_PROFILE_PATH"] = old_path
            if old_dis is None:
                os.environ.pop("COCO_PROFILE_DISABLE", None)
            else:
                os.environ["COCO_PROFILE_DISABLE"] = old_dis


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# 延迟 import 让 env 注入先生效
def _imports():
    from importlib import reload
    import coco.profile as _p
    reload(_p)
    return _p


EVIDENCE_DIR = ROOT / "evidence" / "companion-004"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# V1 — round-trip
# ---------------------------------------------------------------------------


def v1_round_trip() -> dict:
    print("=" * 60)
    print("V1 — ProfileStore round-trip + atomic write")
    print("=" * 60)
    with temp_profile_path() as p:
        prof_mod = _imports()
        store = prof_mod.ProfileStore(path=p)
        prof = prof_mod.UserProfile(
            name="小明",
            interests=["恐龙", "太空"],
            goals=["学加法"],
            last_updated=12345.0,
        )
        store.save(prof)
        _ok(p.exists(), "save 后文件应存在")
        # 中间 .tmp 不应残留
        tmp = p.with_suffix(p.suffix + ".tmp")
        _ok(not tmp.exists(), f"atomic write 后 .tmp 不应存在：{tmp}")
        # round-trip
        loaded = store.load()
        _ok(loaded.name == "小明", f"name 错：{loaded.name!r}")
        _ok(loaded.interests == ["恐龙", "太空"], f"interests 错：{loaded.interests}")
        _ok(loaded.goals == ["学加法"], f"goals 错：{loaded.goals}")
        _ok(loaded.last_updated == 12345.0, f"last_updated 错：{loaded.last_updated}")
        _ok(loaded.schema_version == prof_mod.SCHEMA_VERSION,
            f"schema_version 错：{loaded.schema_version}")
        # 文件本身解析合法 JSON
        raw = json.loads(p.read_text(encoding="utf-8"))
        _ok(raw["name"] == "小明", f"raw json name 错：{raw}")
    print("V1 PASS")
    return {"name": "V1 round-trip + atomic", "passed": True}


# ---------------------------------------------------------------------------
# V2 — missing / disable / corrupt
# ---------------------------------------------------------------------------


def v2_missing_and_corrupt() -> dict:
    print("=" * 60)
    print("V2 — missing / corrupt → 空 profile（不抛）")
    print("=" * 60)
    with temp_profile_path() as p:
        prof_mod = _imports()
        store = prof_mod.ProfileStore(path=p)
        # 文件不存在
        _ok(not p.exists(), "起始应不存在")
        prof = store.load()
        _ok(prof.name is None and prof.interests == [] and prof.goals == [],
            f"missing 应返空 profile：{prof}")
        # 损坏 JSON
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
        prof2 = store.load()
        _ok(prof2.name is None and prof2.interests == [],
            f"corrupt 应返空 profile：{prof2}")
        # root 不是 dict
        p.write_text("[1,2,3]", encoding="utf-8")
        prof3 = store.load()
        _ok(prof3.name is None, f"非 dict root 应返空 profile：{prof3}")
    print("V2 PASS")
    return {"name": "V2 missing/corrupt soft-fail", "passed": True}


# ---------------------------------------------------------------------------
# V3 — LRU truncation
# ---------------------------------------------------------------------------


def v3_lru_truncation() -> dict:
    print("=" * 60)
    print("V3 — add_interest 去重 + LRU 截断")
    print("=" * 60)
    with temp_profile_path() as p:
        prof_mod = _imports()
        store = prof_mod.ProfileStore(path=p)
        # 加 5 个 — 不应丢
        for x in ["恐龙", "太空", "机器人", "音乐", "画画"]:
            store.add_interest(x)
        loaded = store.load()
        _ok(loaded.interests == ["恐龙", "太空", "机器人", "音乐", "画画"],
            f"5 个 LRU 错：{loaded.interests}")
        # 第 6 个应挤掉最旧（恐龙）
        store.add_interest("游泳")
        loaded = store.load()
        _ok(loaded.interests == ["太空", "机器人", "音乐", "画画", "游泳"],
            f"超容截断错：{loaded.interests}")
        # 重复 → 移到末位
        store.add_interest("机器人")
        loaded = store.load()
        _ok(loaded.interests == ["太空", "音乐", "画画", "游泳", "机器人"],
            f"重复移末错：{loaded.interests}")

        # goals 容量 3
        for x in ["学加法", "认字", "学英语"]:
            store.add_goal(x)
        loaded = store.load()
        _ok(loaded.goals == ["学加法", "认字", "学英语"], f"goals 3 错：{loaded.goals}")
        store.add_goal("学画画")
        loaded = store.load()
        _ok(loaded.goals == ["认字", "学英语", "学画画"], f"goals 截断错：{loaded.goals}")
    print("V3 PASS")
    return {"name": "V3 LRU truncation", "passed": True}


# ---------------------------------------------------------------------------
# V4 — Extractor 12 cases ≥ 85%
# ---------------------------------------------------------------------------


# (text, expected_field, expected_value)
# expected_field ∈ {"name", "interest", "goal", "none"}; value 字符串或 None
EXTRACT_CASES = [
    # 名字 5 条
    ("我叫小明", "name", "小明"),
    ("我的名字是李雷", "name", "李雷"),
    ("我是韩梅梅", "name", "韩梅梅"),
    ("你好，我叫小红，今年五岁", "name", "小红"),
    ("我不叫小刚", "none", None),  # 负面前缀
    # 兴趣 4 条
    ("我喜欢恐龙", "interest", "恐龙"),
    ("我对太空感兴趣", "interest", "太空"),
    ("我喜欢机器人和画画", "interest", "机器人和画画"),  # 不强求切分
    ("我不喜欢香菜", "none", None),
    # 目标 3 条
    ("我想学加法", "goal", "加法"),
    ("我的目标是认字", "goal", "认字"),
    ("这周我想学游泳", "goal", "游泳"),
]


def v4_extractor_accuracy() -> dict:
    print("=" * 60)
    print("V4 — ProfileExtractor 12 用例 ≥85%")
    print("=" * 60)
    prof_mod = _imports()
    ex = prof_mod.ProfileExtractor()
    n_total = len(EXTRACT_CASES)
    n_pass = 0
    failures: List[dict] = []
    for text, fld, val in EXTRACT_CASES:
        sig = ex.extract(text)
        ok = False
        if fld == "name":
            ok = sig.get("name") == val
        elif fld == "interest":
            ok = val in (sig.get("interests") or [])
        elif fld == "goal":
            ok = val in (sig.get("goals") or [])
        elif fld == "none":
            # 负面 case：name 为空 + interests/goals 为空（或不存在）
            ok = not sig.get("name") and not sig.get("interests") and not sig.get("goals")
        if ok:
            n_pass += 1
        else:
            failures.append({"text": text, "expected": (fld, val), "actual": sig})
    acc = n_pass / n_total
    print(f"准确率 {n_pass}/{n_total} = {acc:.0%}")
    if failures:
        print("FAIL details:")
        for f in failures:
            print(" ", f)
    _ok(acc >= 0.85, f"准确率 {acc:.0%} <85%；失败 {failures}")
    print("V4 PASS")
    return {"name": "V4 extractor accuracy", "passed": True,
            "accuracy": round(acc, 3), "n_pass": n_pass, "n_total": n_total,
            "failures": failures}


# ---------------------------------------------------------------------------
# V5 — build_system_prompt
# ---------------------------------------------------------------------------


def v5_build_system_prompt() -> dict:
    print("=" * 60)
    print("V5 — build_system_prompt 格式")
    print("=" * 60)
    prof_mod = _imports()
    base = "你是 Coco（可可），一个友好的桌面陪伴机器人。"
    # 全空 / None
    _ok(prof_mod.build_system_prompt(None, base=base) == base,
        "None profile 应返回 base")
    empty = prof_mod.UserProfile()
    _ok(prof_mod.build_system_prompt(empty, base=base) == base,
        "空 profile 应返回 base")
    # base=None + 空 profile → None
    _ok(prof_mod.build_system_prompt(empty, base=None) is None,
        "空 profile + base=None 应返回 None")
    # 部分字段
    p = prof_mod.UserProfile(name="小明", interests=["恐龙"], goals=["学加法"])
    out = prof_mod.build_system_prompt(p, base=base)
    _ok(base in out and "用户昵称：小明" in out and "兴趣：恐龙" in out and "学习目标：学加法" in out,
        f"部分字段格式错：{out!r}")
    # 仅 name
    p2 = prof_mod.UserProfile(name="小红")
    out2 = prof_mod.build_system_prompt(p2, base=base)
    _ok("用户昵称：小红" in out2 and "兴趣：" not in out2 and "学习目标：" not in out2,
        f"仅 name 不应包含其它行：{out2!r}")
    print("V5 PASS")
    return {"name": "V5 build_system_prompt", "passed": True}


# ---------------------------------------------------------------------------
# V6/V7 — InteractSession integration
# ---------------------------------------------------------------------------


class RecordingBackend:
    """记录 chat() 收到的 history / user_text / system_prompt，每次返回固定中文。"""

    name = "recording"

    def __init__(self, reply: str = "好的呀") -> None:
        self.reply = reply
        self.calls: List[dict] = []

    def chat(self, user_text, *, timeout, history=None, system_prompt=None):
        self.calls.append({
            "user_text": user_text,
            "history": None if history is None else [dict(m) for m in history],
            "system_prompt": system_prompt,
        })
        return self.reply


def _dummy_audio() -> np.ndarray:
    return np.zeros(1600, dtype=np.int16)


def _make_session(asr_text_seq, backend, dialog_memory, profile_store):
    from coco.llm import LLMClient
    from coco.interact import InteractSession
    seq_iter = iter(asr_text_seq)

    def _asr(_a, _sr):
        return next(seq_iter)

    def _tts(_t, **_kw):
        return None

    client = LLMClient(backend=backend, timeout=2.0)

    def _llm(text, *, history=None, system_prompt=None):
        return client.reply(text, history=history, system_prompt=system_prompt)

    return InteractSession(
        robot=None,
        asr_fn=_asr,
        tts_say_fn=_tts,
        llm_reply_fn=_llm,
        dialog_memory=dialog_memory,
        profile_store=profile_store,
    )


def v6_session_backward_compat() -> dict:
    print("=" * 60)
    print("V6 — InteractSession profile_store=None 向后兼容")
    print("=" * 60)
    rec = RecordingBackend(reply="嗯嗯")
    sess = _make_session(["你好"], rec, dialog_memory=None, profile_store=None)
    r = sess.handle_audio(_dummy_audio(), 16000, skip_action=True, skip_tts_play=True)
    _ok(r["transcript"] == "你好", f"transcript 错：{r}")
    _ok(len(rec.calls) == 1, f"应 1 次 LLM：{rec.calls}")
    # 没 profile_store → system_prompt 应为 None（fall back to backend default）
    _ok(rec.calls[0]["system_prompt"] is None,
        f"无 profile_store system_prompt 应 None：{rec.calls[0]['system_prompt']!r}")
    _ok("profile_extracted" not in r, f"无 store 不应抽取：{r}")
    print("V6 PASS")
    return {"name": "V6 session backward-compat", "passed": True}


def v7_end_to_end_two_sessions() -> dict:
    print("=" * 60)
    print("V7 — 端到端：session1 写 → session2 读 + LLM system_prompt 注入")
    print("=" * 60)
    with temp_profile_path() as p:
        prof_mod = _imports()
        # session 1
        store1 = prof_mod.ProfileStore(path=p)
        rec1 = RecordingBackend(reply="你好小明")
        sess1 = _make_session(["我叫小明"], rec1, dialog_memory=None, profile_store=store1)
        r1 = sess1.handle_audio(_dummy_audio(), 16000, skip_action=True, skip_tts_play=True)
        _ok(r1.get("profile_extracted", {}).get("name") == "小明",
            f"session1 应抽取 name=小明：{r1.get('profile_extracted')}")
        # 写盘验证
        loaded = store1.load()
        _ok(loaded.name == "小明", f"session1 写盘后 name 错：{loaded.name}")
        _ok(p.exists(), "profile.json 应已落盘")

        # session 2 — 新进程语义：新建 store 复用同 path
        store2 = prof_mod.ProfileStore(path=p)
        loaded2 = store2.load()
        _ok(loaded2.name == "小明", f"session2 重启 load 应得 小明：{loaded2.name}")

        # 注入兴趣
        rec2 = RecordingBackend(reply="好的小明")
        sess2 = _make_session(["我喜欢恐龙"], rec2, dialog_memory=None, profile_store=store2)
        sess2.handle_audio(_dummy_audio(), 16000, skip_action=True, skip_tts_play=True)
        sp = rec2.calls[0]["system_prompt"]
        _ok(sp is not None and "用户昵称：小明" in sp,
            f"system_prompt 应含 '用户昵称：小明'：{sp!r}")
        # interest 应已写盘并出现在下一次 prompt
        rec3 = RecordingBackend(reply="好")
        sess3 = _make_session(["再来一句"], rec3, dialog_memory=None, profile_store=store2)
        sess3.handle_audio(_dummy_audio(), 16000, skip_action=True, skip_tts_play=True)
        sp3 = rec3.calls[0]["system_prompt"]
        _ok(sp3 is not None and "兴趣：恐龙" in sp3 and "用户昵称：小明" in sp3,
            f"sp3 应含兴趣+name：{sp3!r}")
    print("V7 PASS")
    return {"name": "V7 end-to-end 2 sessions", "passed": True}


# ---------------------------------------------------------------------------
# V8 — disable kill switch
# ---------------------------------------------------------------------------


def v8_disable_kill_switch() -> dict:
    print("=" * 60)
    print("V8 — COCO_PROFILE_DISABLE=1 → load/save no-op")
    print("=" * 60)
    with temp_profile_path() as p:
        os.environ["COCO_PROFILE_DISABLE"] = "1"
        try:
            prof_mod = _imports()
            store = prof_mod.ProfileStore(path=p)
            # save 不应落盘
            store.save(prof_mod.UserProfile(name="小红"))
            _ok(not p.exists(), f"disable 时 save 不应落盘，但文件存在：{p}")
            # load 应空
            loaded = store.load()
            _ok(loaded.name is None, f"disable 时 load 应空：{loaded}")
            # add_interest 也不落盘
            store.add_interest("恐龙")
            _ok(not p.exists(), "disable 时 add_interest 也不应落盘")
            # 生造一个文件 → reset 不删（因为 disable）
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"name": "X", "schema_version": 1}), encoding="utf-8")
            store2 = prof_mod.ProfileStore(path=p)
            store2.reset()
            _ok(p.exists(), "disable 时 reset 应 no-op，文件不应被删")
            # load 仍应空（disable）
            loaded2 = store2.load()
            _ok(loaded2.name is None, f"disable 时 load 应空：{loaded2}")
        finally:
            os.environ.pop("COCO_PROFILE_DISABLE", None)
    print("V8 PASS")
    return {"name": "V8 disable kill switch", "passed": True}


# ---------------------------------------------------------------------------
# V9 — schema_version mismatch
# ---------------------------------------------------------------------------


def v9_schema_version_mismatch() -> dict:
    print("=" * 60)
    print("V9 — schema_version 不匹配 fail-soft")
    print("=" * 60)
    with temp_profile_path() as p:
        prof_mod = _imports()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"name": "小明", "interests": ["恐龙"], "schema_version": 999}),
            encoding="utf-8",
        )
        store = prof_mod.ProfileStore(path=p)
        loaded = store.load()
        _ok(loaded.name is None, f"v999 应 fail-soft 返空：{loaded}")
        # 原文件应保留待人工迁移
        _ok(p.exists(), "原文件应保留")
        raw = json.loads(p.read_text(encoding="utf-8"))
        _ok(raw["name"] == "小明", "原文件内容应保持")
    print("V9 PASS")
    return {"name": "V9 schema_version mismatch", "passed": True}


# ---------------------------------------------------------------------------
# V10 — reset_profile.py
# ---------------------------------------------------------------------------


def v10_reset_script() -> dict:
    print("=" * 60)
    print("V10 — scripts/reset_profile.py 删除后 fresh start 不抛")
    print("=" * 60)
    with temp_profile_path() as p:
        prof_mod = _imports()
        store = prof_mod.ProfileStore(path=p)
        store.update_field(name="小明", interests=["恐龙"], goals=["学加法"])
        _ok(p.exists(), "前置：文件应已存在")

        # 跑 reset 脚本（COCO_PROFILE_PATH 已注入到 env）
        env = dict(os.environ)
        env["COCO_PROFILE_PATH"] = str(p)
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "reset_profile.py")],
            env=env, capture_output=True, text=True, timeout=30,
        )
        _ok(r.returncode == 0, f"reset 脚本返回非 0：{r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}")
        _ok(not p.exists(), f"reset 后文件应删除：{p}")

        # fresh start 仍可 load（空 profile）
        store2 = prof_mod.ProfileStore(path=p)
        loaded = store2.load()
        _ok(loaded.name is None and loaded.interests == [],
            f"删后 load 应空：{loaded}")
    print("V10 PASS")
    return {"name": "V10 reset script", "passed": True}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    results = []
    failures = []
    for fn in (
        v1_round_trip,
        v2_missing_and_corrupt,
        v3_lru_truncation,
        v4_extractor_accuracy,
        v5_build_system_prompt,
        v6_session_backward_compat,
        v7_end_to_end_two_sessions,
        v8_disable_kill_switch,
        v9_schema_version_mismatch,
        v10_reset_script,
    ):
        try:
            results.append(fn())
        except Exception as e:
            import traceback
            traceback.print_exc()
            results.append({"name": fn.__name__, "passed": False,
                            "error": f"{type(e).__name__}: {e}"})
            failures.append(fn.__name__)

    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r.get("passed")),
        "failed": len(failures),
        "failures": failures,
        "results": results,
    }
    out = EVIDENCE_DIR / "verify_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 60)
    print(f"Summary: {summary['passed']}/{summary['total']} PASS  -> {out}")
    _restore_env()
    if failures:
        print("FAIL:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
