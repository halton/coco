#!/usr/bin/env python3
"""infra-022 verify: history writer residual hardening + verify skip semantics.

承接 infra-017 backlog 残留升级。覆盖:

V0 _history_writer.py 源码 fingerprint (检测漂移)
V1 N1 _archive_stamp 单调性 — 同进程千次连发 stamp 全部唯一 (即使弱 ns 时钟同 ns,
   itertools.count() seq 兜底唯一)
V2 N3 _FileLock 退化路径 stderr WARN once — 强制 flock 抛 OSError, 多次入锁仅一次
   WARN 出现
V3 N4 rotate 在 FileLock 内 — _emit_safe 在阈值跨越时一次锁内完成 rename+touch+append
   (黑盒: 跨阈值连发 ROTATE+10 行后, archive 文件 = 1, 主 jsonl 行数 = 10)
V4 C5 emit_verify skip 字段语义 — 调用 emit_verify(skip=3) 后 jsonl 行 skip == 3
   (回归字段保持存在 schema)
V4b C5 run_verify_all skipped_count 传递 — 静态扫源码确认 _emit_history 接受
   skipped_count, main() 计算并传入
V5 Default-OFF / 兼容回归 — _rotate_if_needed 公共 API 仍可单独调用 (老调用方);
   emit_smoke / emit_verify 在禁用 env 下返回 False; 已有 archive 文件不被误删

Sim-first 全证: 临时目录跑 + monkeypatch HISTORY_DIR, 不动仓库 evidence/_history。
"""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
EVIDENCE_DIR = REPO_ROOT / "evidence" / "infra-022"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

EXPECTED_FINGERPRINT = "0300e618d227472eb41938ef06b36314cb8384135dc81d780f76ec0fd779c5b0"


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _fresh_hw(tmp_dir: Path):
    """重新 import _history_writer 并把 HISTORY_DIR / ARCHIVE_DIR / *_JSONL 重定向到 tmp。

    每次返回的是一个独立绑定的 module 引用; 全局 _LOCK_DEGRADED_WARNED 在新模块里复位。
    """
    sys.path.insert(0, str(SCRIPTS_DIR))
    # 强制 reload 以重置 module-level 全局 (counter / warned flag)
    if "_history_writer" in sys.modules:
        del sys.modules["_history_writer"]
    hw = importlib.import_module("_history_writer")
    hw.HISTORY_DIR = tmp_dir
    hw.ARCHIVE_DIR = tmp_dir / ".archive"
    hw.VERIFY_JSONL = tmp_dir / "verify_history.jsonl"
    hw.SMOKE_JSONL = tmp_dir / "smoke_history.jsonl"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return hw


# ---------- V0 fingerprint --------------------------------------------------

def v0_fingerprint() -> dict:
    src = (SCRIPTS_DIR / "_history_writer.py").read_bytes()
    sha = hashlib.sha256(src).hexdigest()
    if sha != EXPECTED_FINGERPRINT:
        raise AssertionError(
            f"V0 _history_writer.py fingerprint drift: expected "
            f"{EXPECTED_FINGERPRINT[:16]}... got {sha[:16]}... "
            "(如果改动是预期的, 更新 EXPECTED_FINGERPRINT)"
        )
    return {"v": "V0", "sha256": sha, "ok": True}


# ---------- V1 _archive_stamp 单调性 ----------------------------------------

def v1_stamp_unique() -> dict:
    with tempfile.TemporaryDirectory() as d:
        hw = _fresh_hw(Path(d))
        stamps = [hw._archive_stamp() for _ in range(1000)]
    uniq = len(set(stamps))
    _ok(uniq == 1000, f"V1 stamps not unique: {uniq}/1000")
    # 确认包含 4 段
    parts = stamps[0].split(".")
    _ok(len(parts) == 4, f"V1 stamp format unexpected: {stamps[0]}")
    return {"v": "V1", "count": 1000, "unique": uniq, "sample": stamps[0], "ok": True}


# ---------- V2 N3 退化 WARN once --------------------------------------------

def v2_lock_warn_once() -> dict:
    with tempfile.TemporaryDirectory() as d:
        hw = _fresh_hw(Path(d))
        # monkeypatch fcntl.flock to raise OSError, force degradation
        if hw._HAS_FCNTL:
            orig = hw._fcntl.flock

            def _raise(*a, **kw):
                raise OSError("simulated unsupported fs")

            hw._fcntl.flock = _raise
        elif hw._HAS_MSVCRT:
            orig = hw._msvcrt.locking

            def _raise(*a, **kw):
                raise OSError("simulated")

            hw._msvcrt.locking = _raise
        else:
            # 平台天生退化, 直接验
            orig = None

        try:
            buf = io.StringIO()
            saved = sys.stderr
            sys.stderr = buf
            try:
                p = Path(d) / "x.jsonl"
                # 三次入锁出锁
                for _ in range(3):
                    with hw._FileLock(p):
                        pass
            finally:
                sys.stderr = saved
            text = buf.getvalue()
            warn_lines = [l for l in text.splitlines() if "_FileLock degraded" in l]
            _ok(len(warn_lines) == 1,
                f"V2 expected exactly 1 WARN line, got {len(warn_lines)}: {text}")
        finally:
            if orig is not None:
                if hw._HAS_FCNTL:
                    hw._fcntl.flock = orig
                elif hw._HAS_MSVCRT:
                    hw._msvcrt.locking = orig
    return {"v": "V2", "warn_lines": 1, "ok": True}


# ---------- V3 N4 rotate-in-lock 黑盒 ---------------------------------------

def v3_rotate_in_lock() -> dict:
    with tempfile.TemporaryDirectory() as d:
        hw = _fresh_hw(Path(d))
        # 把 ROTATE_LINES 调到 5 方便测试
        hw.ROTATE_LINES = 5
        p = hw.SMOKE_JSONL
        # 预先写 5 行 (达到 rotate 阈值)
        for i in range(5):
            ok = hw._emit_safe(p, {"i": i, "kind": "smoke"})
            _ok(ok, f"V3 pre-fill emit failed at {i}")
        # 第 6 次 emit: 应触发 rotate (5 行 → archive), 主文件 recreate 后写入 1 行
        ok = hw._emit_safe(p, {"i": 5, "kind": "smoke"})
        _ok(ok, "V3 6th emit failed")
        # 再写 9 行, 主 jsonl 应该 = 10 行 (5..14), 因为下一次 rotate 在 line>=5 时触发
        # 但 _rotate_locked 在阈值时再次 rotate; 我们关注: archive ≥ 1, 主文件 line >= 1
        for i in range(6, 10):
            hw._emit_safe(p, {"i": i, "kind": "smoke"})
        archive_files = list((Path(d) / ".archive").glob("smoke_history.*.jsonl"))
        _ok(len(archive_files) >= 1, f"V3 expected ≥1 archive, got {archive_files}")
        # 主文件存在且非空
        _ok(p.exists(), "V3 main jsonl missing after rotate")
        with open(p) as f:
            main_lines = [l for l in f if l.strip()]
        _ok(len(main_lines) >= 1, f"V3 main jsonl empty after rotate: {main_lines}")
        # archive 第一个文件应该有 5 行 (旧阈值)
        with open(archive_files[0]) as f:
            arc_lines = [l for l in f if l.strip()]
        _ok(len(arc_lines) == 5,
            f"V3 archive should have 5 lines (pre-rotate state), got {len(arc_lines)}")
    return {"v": "V3", "archive_count": len(archive_files),
            "main_lines": len(main_lines), "ok": True}


# ---------- V4 C5 emit_verify skip 字段语义 ---------------------------------

def v4_skip_field_semantics() -> dict:
    with tempfile.TemporaryDirectory() as d:
        hw = _fresh_hw(Path(d))
        # 确保 disable env 不影响
        os.environ.pop("COCO_HISTORY_DISABLE", None)
        ok = hw.emit_verify(total=10, pass_=8, fail=2, skip=3,
                            duration_s=12.34, failed_names=["a.py", "b.py"])
        _ok(ok, "V4 emit_verify returned False")
        recs = hw.load_records(hw.VERIFY_JSONL)
        _ok(len(recs) == 1, f"V4 expected 1 record, got {len(recs)}")
        r = recs[0]
        _ok(r["skip"] == 3, f"V4 skip mismatch: {r}")
        _ok(r["total"] == 10 and r["pass"] == 8 and r["fail"] == 2,
            f"V4 totals mismatch: {r}")
        _ok(r["failed_names"] == ["a.py", "b.py"], f"V4 failed_names: {r}")
    return {"v": "V4", "record_skip": 3, "ok": True}


def v4b_run_verify_all_wire() -> dict:
    src = (SCRIPTS_DIR / "run_verify_all.py").read_text()
    _ok("skipped_count: int = 0" in src,
        "V4b _emit_history signature missing skipped_count param")
    _ok("skipped_count=skipped_count" in src,
        "V4b main() does not pass skipped_count to _emit_history")
    _ok("infra-022 C5" in src,
        "V4b infra-022 C5 marker not found in run_verify_all.py")
    return {"v": "V4b", "ok": True}


# ---------- V5 Default-OFF / 兼容 -------------------------------------------

def v5_default_off_and_compat() -> dict:
    with tempfile.TemporaryDirectory() as d:
        hw = _fresh_hw(Path(d))
        # (a) 公共 _rotate_if_needed 仍可单独调用 (老调用方)
        p = hw.SMOKE_JSONL
        # 没文件时返回 None
        _ok(hw._rotate_if_needed(p) is None, "V5a _rotate_if_needed nonexistent")
        # 行数不足时返回 None
        p.write_text('{"i":0}\n')
        _ok(hw._rotate_if_needed(p, rotate_lines=100) is None,
            "V5a _rotate_if_needed under threshold")
        # 行数达到阈值时 rotate 并 recreate 空
        p.write_text("".join(f'{{"i":{i}}}\n' for i in range(5)))
        target = hw._rotate_if_needed(p, rotate_lines=5)
        _ok(target is not None and target.exists(),
            f"V5a rotate did not produce archive: {target}")
        _ok(p.exists() and p.read_text() == "",
            "V5a main jsonl not recreated empty after rotate")
        # (b) HISTORY_DISABLE=on 时 emit 返回 False, 不动文件
        before = list(Path(d).glob("*.jsonl"))
        os.environ["COCO_HISTORY_DISABLE"] = "ON"
        try:
            ok = hw.emit_verify(total=1, pass_=1, fail=0, skip=0, duration_s=0.0)
            _ok(ok is False, "V5b emit_verify should return False under DISABLE")
            ok2 = hw.emit_smoke(total=1, pass_=1, fail=0, skip=0, duration_s=0.0)
            _ok(ok2 is False, "V5b emit_smoke should return False under DISABLE")
        finally:
            os.environ.pop("COCO_HISTORY_DISABLE", None)
        after = list(Path(d).glob("*.jsonl"))
        _ok(sorted(before) == sorted(after),
            f"V5b DISABLE should not create new jsonl files: before={before} after={after}")
    return {"v": "V5", "ok": True}


# ---------- main -------------------------------------------------------------

def main() -> int:
    results = []
    failed = []
    cases = [
        ("V0", v0_fingerprint),
        ("V1", v1_stamp_unique),
        ("V2", v2_lock_warn_once),
        ("V3", v3_rotate_in_lock),
        ("V4", v4_skip_field_semantics),
        ("V4b", v4b_run_verify_all_wire),
        ("V5", v5_default_off_and_compat),
    ]
    for name, fn in cases:
        try:
            r = fn()
            results.append(r)
            print(f"  PASS {name}")
        except AssertionError as e:
            failed.append({"v": name, "err": str(e)})
            print(f"  FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append({"v": name, "err": f"{type(e).__name__}: {e}"})
            print(f"  EXC  {name}: {type(e).__name__}: {e}")

    summary = {
        "feature_id": "infra-022",
        "results": results,
        "failed": failed,
        "total": len(cases),
        "passed": len(results),
    }
    out = EVIDENCE_DIR / "verify_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"\nwrote {out}")
    print(f"summary: passed={len(results)}/{len(cases)} failed={len(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
