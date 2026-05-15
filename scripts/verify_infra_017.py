#!/usr/bin/env python3
"""infra-017 verify: history jsonl 加固 + verify_vision_010 evidence 幂等.

Acceptance (与 feature_list.json infra-017 一致)
-----------------------------------------------
V1 并发 append 文件锁：两 worker 各写 100 行 → 200 行无撕裂。
V2 rotate 后立即 recreate 空 jsonl。
V3 .archive/ 文件名 PID + nanos 同秒不碰撞。
V4 archive retention 保 N=20 / 超量按 mtime 删旧。
V5 COCO_HISTORY_DISABLE 容忍大小写 / on/yes/true/1。
V6 evidence/infra-016 archived_path stamp 剔除 → bytewise 稳定。
V7 verify_vision_010 两次跑 evidence/vision-010/verify_summary.json 字节级等价。
V8 infra-016 / vision-010 回归 PASS。

运行期零影响（独立 V）：HISTORY_DISABLE=on 时 emit 返回 False 不创建文件。
"""
from __future__ import annotations

import filecmp
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
EVIDENCE_DIR = REPO_ROOT / "evidence" / "infra-017"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# ---------- V1: 并发 append 文件锁 ------------------------------------------

def _worker_append(args: tuple[str, str, int, int]) -> int:
    """子进程：往 jsonl_path 追加 N 行带 worker_id 的 record。"""
    jsonl_path_s, worker_id, n_lines, _seed = args
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _history_writer as hw
    p = Path(jsonl_path_s)
    written = 0
    for i in range(n_lines):
        rec = {
            "kind": "verify",
            "worker": worker_id,
            "i": i,
            # padding 撑大单行字节数，更容易暴露撕裂
            "padding": "x" * 200,
        }
        # 直接调底层 _append_line（绕过 env disable / git_head 副作用）
        try:
            hw._append_line(p, rec)
            written += 1
        except Exception:
            pass
    return written


def v1_concurrent_append_lock(tmpdir: Path) -> dict:
    name = "V1_concurrent_append_lock"
    jpath = tmpdir / "v_concurrent.jsonl"
    n_per_worker = 100
    workers = 2
    with ProcessPoolExecutor(max_workers=workers) as ex:
        args_list = [
            (str(jpath), f"w{i}", n_per_worker, i)
            for i in range(workers)
        ]
        written = list(ex.map(_worker_append, args_list))
    total_written = sum(written)
    _ok(total_written == workers * n_per_worker,
        f"workers should write {workers * n_per_worker}, got {total_written}")
    # 每行必须是合法 json + 字段 'worker' 存在
    bad = 0
    total_lines = 0
    counts: dict[str, int] = {}
    with open(jpath, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            total_lines += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            w = rec.get("worker")
            if w is None:
                bad += 1
                continue
            counts[w] = counts.get(w, 0) + 1
    _ok(bad == 0, f"撕裂行数={bad} (应为 0)")
    _ok(total_lines == workers * n_per_worker,
        f"总行数={total_lines}, 期望={workers * n_per_worker}")
    for i in range(workers):
        wid = f"w{i}"
        _ok(counts.get(wid) == n_per_worker,
            f"worker {wid} 写了 {counts.get(wid)} 行，期望 {n_per_worker}")
    return {
        "name": name, "passed": True,
        "total_lines": total_lines, "bad_lines": bad,
        "per_worker_counts": counts,
    }


# ---------- V2: rotate 后立即 recreate 空 jsonl -----------------------------

def v2_rotate_recreates_empty(tmpdir: Path) -> dict:
    name = "V2_rotate_recreates_empty"
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _history_writer as hw
    jpath = tmpdir / "v_rot.jsonl"
    with open(jpath, "w") as f:
        for i in range(5):
            f.write(json.dumps({"i": i}) + "\n")
    orig_arch = hw.ARCHIVE_DIR
    hw.ARCHIVE_DIR = tmpdir / ".archive"
    try:
        archived = hw._rotate_if_needed(jpath, rotate_lines=3)
        _ok(archived is not None and archived.exists(), "rotate 未生成 archive")
        _ok(jpath.exists(), "rotate 后主 jsonl 应立即被 recreate")
        _ok(jpath.stat().st_size == 0, f"recreate 后应为空文件，size={jpath.stat().st_size}")
        # 下次 append 行数应 +1（不是 +2）
        hw._append_line(jpath, {"after": True})
        with open(jpath) as f:
            lines = [ln for ln in f.read().splitlines() if ln]
        _ok(len(lines) == 1, f"recreate 后 append 应只有 1 行，实际 {len(lines)}")
    finally:
        hw.ARCHIVE_DIR = orig_arch
    return {"name": name, "passed": True,
            "archive_exists": True, "main_recreated_empty": True}


# ---------- V3: .archive PID+nanos 同秒不碰撞 -------------------------------

def v3_archive_stamp_unique(tmpdir: Path) -> dict:
    name = "V3_archive_stamp_unique"
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _history_writer as hw
    # 同一进程同秒内多次取 stamp，必须互不相同
    stamps = [hw._archive_stamp() for _ in range(50)]
    _ok(len(set(stamps)) == 50, f"50 个 stamp 仅 {len(set(stamps))} 个唯一")
    # 模拟 rotate：连续 rotate 3 个文件，文件名应互不相同
    arch_dir = tmpdir / ".archive"
    orig_arch = hw.ARCHIVE_DIR
    hw.ARCHIVE_DIR = arch_dir
    try:
        names: list[str] = []
        for i in range(3):
            jp = tmpdir / f"v_a{i}.jsonl"
            with open(jp, "w") as f:
                for k in range(3):
                    f.write(json.dumps({"k": k}) + "\n")
            target = hw._rotate_if_needed(jp, rotate_lines=3)
            _ok(target is not None, f"第 {i} 次 rotate 未触发")
            names.append(target.name)
        _ok(len(set(names)) == 3, f"3 次 rotate 文件名重复: {names}")
        # 名字中必须含 pid
        pid_str = str(os.getpid())
        for nm in names:
            _ok(pid_str in nm, f"archive 名 {nm} 应含 pid {pid_str}")
    finally:
        hw.ARCHIVE_DIR = orig_arch
    return {"name": name, "passed": True,
            "unique_stamps": len(set(stamps)),
            "rotate_unique_names": len(set(names))}


# ---------- V4: archive retention 保 N 删旧 ---------------------------------

def v4_archive_retention(tmpdir: Path) -> dict:
    name = "V4_archive_retention"
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _history_writer as hw
    arch_dir = tmpdir / ".archive"
    arch_dir.mkdir()
    stem = "verify_history"
    # 造 25 个 archive；mtime 递增（最早 = i=0）
    now = time.time()
    for i in range(25):
        p = arch_dir / f"{stem}.fake_{i:02d}.jsonl"
        p.write_text(f"{i}\n")
        os.utime(p, (now + i, now + i))
    deleted = hw._enforce_retention(stem, archive_dir=arch_dir, keep=20)
    remaining = sorted(arch_dir.glob(f"{stem}.*.jsonl"))
    _ok(len(deleted) == 5, f"应删 5 个，实删 {len(deleted)}")
    _ok(len(remaining) == 20, f"应剩 20 个，实剩 {len(remaining)}")
    # 删的必须是最旧的 i=0..4
    deleted_names = {p.name for p in deleted}
    for i in range(5):
        _ok(f"{stem}.fake_{i:02d}.jsonl" in deleted_names,
            f"应删除 fake_{i:02d}，实删 {deleted_names}")
    # env override：keep=10
    os.environ["COCO_HISTORY_ARCHIVE_KEEP"] = "10"
    try:
        deleted2 = hw._enforce_retention(stem, archive_dir=arch_dir)
        _ok(len(deleted2) == 10, f"env keep=10 应删 10，实删 {len(deleted2)}")
    finally:
        os.environ.pop("COCO_HISTORY_ARCHIVE_KEEP", None)
    return {"name": name, "passed": True,
            "first_pass_deleted": 5, "env_override_deleted": 10}


# ---------- V5: HISTORY_DISABLE 大小写不敏感 --------------------------------

def v5_disable_case_insensitive(tmpdir: Path) -> dict:
    name = "V5_disable_case_insensitive"
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _history_writer as hw
    jpath = tmpdir / "v_dis.jsonl"
    orig = hw.VERIFY_JSONL
    hw.VERIFY_JSONL = jpath
    orig_env = os.environ.get("COCO_HISTORY_DISABLE")
    try:
        for val in ("1", "true", "TRUE", "True",
                    "yes", "YES", "Yes",
                    "on", "ON", "On"):
            os.environ["COCO_HISTORY_DISABLE"] = val
            if jpath.exists():
                jpath.unlink()
            ok = hw.emit_verify(total=1, pass_=1, fail=0, skip=0, duration_s=0.1)
            _ok(ok is False, f"DISABLE={val!r} 应返回 False，实际 {ok}")
            _ok(not jpath.exists(), f"DISABLE={val!r} 不应创建 jsonl")
        # negative：未设 / 空 / off / 0 / random → 允许写
        for val in ("", "0", "false", "no", "off", "FOO"):
            os.environ["COCO_HISTORY_DISABLE"] = val
            if jpath.exists():
                jpath.unlink()
            ok = hw.emit_verify(total=1, pass_=1, fail=0, skip=0, duration_s=0.1)
            _ok(ok is True, f"DISABLE={val!r} 应允许写，实际 {ok}")
            _ok(jpath.exists(), f"DISABLE={val!r} 应创建 jsonl")
    finally:
        hw.VERIFY_JSONL = orig
        if orig_env is None:
            os.environ.pop("COCO_HISTORY_DISABLE", None)
        else:
            os.environ["COCO_HISTORY_DISABLE"] = orig_env
    return {"name": name, "passed": True,
            "true_values_tested": ["1", "true", "TRUE", "True", "yes", "YES",
                                   "Yes", "on", "ON", "On"],
            "negative_values_tested": ["", "0", "false", "no", "off", "FOO"]}


# ---------- V6: infra-016 evidence archived_path stamp 剔除 -----------------

def v6_infra016_evidence_stable() -> dict:
    """运行 verify_infra_016 两次（隔离 evidence），diff 应为 0。

    顺带断言 V4 结果不再含 'archived_path' 完整时间戳字符串。
    """
    name = "V6_infra016_evidence_stable_bytewise"
    out1 = EVIDENCE_DIR / "_tmp_016_run1.json"
    out2 = EVIDENCE_DIR / "_tmp_016_run2.json"
    # 直接读现有 evidence 内容；如果不存在，跑一次填充
    target = REPO_ROOT / "evidence" / "infra-016" / "verify_summary.json"
    if not target.exists():
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verify_infra_016.py")],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
        )
        _ok(proc.returncode == 0,
            f"prep run verify_infra_016 失败 rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    # 拍 snapshot 1
    shutil.copy(target, out1)
    # 重跑
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verify_infra_016.py")],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
    )
    _ok(proc.returncode == 0,
        f"second run verify_infra_016 失败 rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    shutil.copy(target, out2)
    same = filecmp.cmp(out1, out2, shallow=False)
    _ok(same, "verify_infra_016 evidence 两次不一致（V4 仍含动态 stamp？）")
    # 内容侧断言：当前 evidence 不含 'archived_path' 完整路径字段
    body = target.read_text(encoding="utf-8")
    _ok('"archived_path"' not in body,
        "evidence/infra-016 仍含 archived_path（C7 剔除失败）")
    # 清 tmp
    out1.unlink()
    out2.unlink()
    return {"name": name, "passed": True, "bytewise_equal": True}


# ---------- V7: verify_vision_010 evidence 字节级幂等 -----------------------

def v7_vision010_idempotent() -> dict:
    name = "V7_vision010_idempotent"
    target = REPO_ROOT / "evidence" / "vision-010" / "verify_summary.json"
    out1 = EVIDENCE_DIR / "_tmp_v010_run1.json"
    out2 = EVIDENCE_DIR / "_tmp_v010_run2.json"
    out3 = EVIDENCE_DIR / "_tmp_v010_run3.json"
    # run 1
    proc1 = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verify_vision_010.py")],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600,
    )
    _ok(proc1.returncode == 0,
        f"vision-010 run1 失败 rc={proc1.returncode}\n{proc1.stdout[-2000:]}\n{proc1.stderr[-2000:]}")
    shutil.copy(target, out1)
    # run 2
    proc2 = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verify_vision_010.py")],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600,
    )
    _ok(proc2.returncode == 0, f"vision-010 run2 失败 rc={proc2.returncode}")
    shutil.copy(target, out2)
    # run 3 (三次确认稳定)
    proc3 = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verify_vision_010.py")],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600,
    )
    _ok(proc3.returncode == 0, f"vision-010 run3 失败 rc={proc3.returncode}")
    shutil.copy(target, out3)
    h1 = hashlib.sha256(out1.read_bytes()).hexdigest()
    h2 = hashlib.sha256(out2.read_bytes()).hexdigest()
    h3 = hashlib.sha256(out3.read_bytes()).hexdigest()
    _ok(h1 == h2 == h3,
        f"vision-010 evidence 三次不字节等价: {h1} {h2} {h3}")
    out1.unlink()
    out2.unlink()
    out3.unlink()
    return {"name": name, "passed": True,
            "sha256": h1, "runs": 3, "bytewise_equal": True}


# ---------- V8: 回归 infra-016 / vision-010 PASS ---------------------------

def v8_regression() -> dict:
    name = "V8_regression_infra016_vision010"
    results: dict[str, dict] = {}
    for script in ("verify_infra_016.py", "verify_vision_010.py"):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / script)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600,
        )
        results[script] = {"rc": proc.returncode}
        _ok(proc.returncode == 0,
            f"{script} 回归 FAIL rc={proc.returncode}\n"
            f"--- stdout tail ---\n{proc.stdout[-1500:]}\n"
            f"--- stderr tail ---\n{proc.stderr[-1500:]}")
    return {"name": name, "passed": True, "results": results}


# ---------- 运行期零影响 ----------------------------------------------------

def v_zero_runtime_impact(tmpdir: Path) -> dict:
    """运行期零影响独立 V：HISTORY_DISABLE=on 时 emit 返回 False，文件不创建。"""
    name = "V_zero_runtime_impact_disable_on"
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _history_writer as hw
    jpath = tmpdir / "v_zri.jsonl"
    orig = hw.VERIFY_JSONL
    hw.VERIFY_JSONL = jpath
    orig_env = os.environ.get("COCO_HISTORY_DISABLE")
    os.environ["COCO_HISTORY_DISABLE"] = "on"
    try:
        ok = hw.emit_verify(total=1, pass_=1, fail=0, skip=0, duration_s=0.1)
        _ok(ok is False, "DISABLE=on 时 emit_verify 应返回 False")
        _ok(not jpath.exists(), "DISABLE=on 时 jsonl 不应被创建")
    finally:
        hw.VERIFY_JSONL = orig
        if orig_env is None:
            os.environ.pop("COCO_HISTORY_DISABLE", None)
        else:
            os.environ["COCO_HISTORY_DISABLE"] = orig_env
    return {"name": name, "passed": True}


# ---------- main ----------------------------------------------------------

def main() -> int:
    failed: list[str] = []
    results: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        checks: list[tuple[str, callable, tuple]] = [
            ("v1", v1_concurrent_append_lock, (tmpdir / "v1",)),
            ("v2", v2_rotate_recreates_empty, (tmpdir / "v2",)),
            ("v3", v3_archive_stamp_unique, (tmpdir / "v3",)),
            ("v4", v4_archive_retention, (tmpdir / "v4",)),
            ("v5", v5_disable_case_insensitive, (tmpdir / "v5",)),
            ("v6", v6_infra016_evidence_stable, ()),
            ("v7", v7_vision010_idempotent, ()),
            ("v8", v8_regression, ()),
            ("vZ", v_zero_runtime_impact, (tmpdir / "vZ",)),
        ]
        for tag, fn, args in checks:
            sub = args[0] if args else None
            if sub is not None:
                sub.mkdir(parents=True, exist_ok=True)
            print(f"\n--- {tag} {fn.__name__} ---", flush=True)
            try:
                r = fn(*args)
                print(f"  PASS: {r}")
                results.append({**r, "tag": tag})
            except AssertionError as e:
                print(f"  FAIL: {e}", file=sys.stderr)
                failed.append(fn.__name__)
                results.append({"name": fn.__name__, "tag": tag,
                                "passed": False, "error": str(e)})
            except Exception as e:  # pragma: no cover
                print(f"  ERROR: {fn.__name__}: {e!r}", file=sys.stderr)
                failed.append(fn.__name__)
                results.append({"name": fn.__name__, "tag": tag,
                                "passed": False, "error": repr(e)})

    summary_path = EVIDENCE_DIR / "verify_summary.json"
    summary = {
        "feature_id": "infra-017",
        "title": "history jsonl 加固 + verify_vision_010 evidence 幂等",
        "phase": 14,
        "branch": "feat/infra-017",
        "verify_script": "scripts/verify_infra_017.py",
        "results": results,
        "totals": f"{len(results) - len(failed)}/{len(results)} PASS",
        "default_off": "N/A — CI/工具加固。运行期零影响：HISTORY_DISABLE=1/on/yes/true "
                       "(case-insensitive) 完全停 IO（V_zero_runtime_impact 守护）；写盘失败"
                       "仅 stderr WARN 不阻塞 main flow。",
        "real_machine_uat": "N/A — pure CI/observability tool",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"\n[verify_infra_017] summary -> {summary_path}")
    if failed:
        print(f"\ninfra-017 verify FAIL: {failed}", file=sys.stderr)
        return 1
    print(f"\ninfra-017 verify PASS ({len(results)}/{len(results)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
