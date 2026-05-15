#!/usr/bin/env python3
"""infra-016 verify: verify/smoke history jsonl + health_summary CLI + restore protect.

Phase-13 infra-016：把 verify / smoke 跑完后的关键指标（总数 / pass / fail /
duration / git_head / failed_names / per-area smoke status）append-only 写到
evidence/_history/{verify,smoke}_history.jsonl，配 scripts/health_summary.py CLI
出趋势报告。runtime 零影响（默认 enabled，无 env gate；可用
``COCO_HISTORY_DISABLE=1`` 关掉）。

Checks
------
V1  scripts/_history_writer.py 存在且 import OK，公共符号齐全
V2  emit_verify 写一行 → load_records 读出，字段集稳定
V3  emit_smoke 写一行 → load_records 读出，字段集稳定 + areas dict 保真
V4  rotate：当 jsonl 行数 ≥ ROTATE_LINES → 自动归档 .archive/，主文件清空
V5  run_verify_all.py wire-in：源码含 ``_emit_history(`` 调用且 import 路径正确
V6  smoke.py wire-in：源码含 ``_emit_smoke_history(`` 调用
V7  health_summary.py CLI：fake jsonl → 输出含 pass_rate / avg_duration_s /
    top_failing / areas 关键字段；--json 模式 json.loads 通
V8  restore_unrelated_evidence dogfood：evidence/_history/foo 永远不被 restore
V9  运行期零影响 — COCO_HISTORY_DISABLE=1 时 emit_* 返回 False 不写文件
V10 jsonl 字段稳定性：emit_verify 二次调用 line 数 +1，schema 一致
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
EVIDENCE_DIR = REPO_ROOT / "evidence" / "infra-016"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def v1_history_writer_imports() -> dict:
    """_history_writer.py 模块存在 + 公共符号齐全。"""
    name = "V1_history_writer_imports"
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _history_writer as hw
    needed = {"emit_verify", "emit_smoke", "load_records",
              "VERIFY_JSONL", "SMOKE_JSONL", "HISTORY_DIR",
              "ARCHIVE_DIR", "ROTATE_LINES"}
    missing = needed - set(dir(hw))
    _ok(not missing, f"_history_writer 缺符号: {missing}")
    return {"name": name, "passed": True, "exports": sorted(needed)}


def v2_emit_verify_roundtrip(tmpdir: Path) -> dict:
    name = "V2_emit_verify_roundtrip"
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _history_writer as hw
    jpath = tmpdir / "v_verify.jsonl"
    # monkey-patch
    orig = hw.VERIFY_JSONL
    hw.VERIFY_JSONL = jpath
    try:
        ok = hw.emit_verify(total=10, pass_=8, fail=2, skip=0,
                            duration_s=12.345,
                            failed_names=["verify_a.py", "verify_b.py"])
        _ok(ok, "emit_verify 返回 False")
        recs = hw.load_records(jpath)
        _ok(len(recs) == 1, f"expected 1 record, got {len(recs)}")
        r = recs[0]
        for k in ("ts", "kind", "git_head", "total", "pass", "fail", "skip",
                  "duration_s", "failed_names"):
            _ok(k in r, f"verify record 缺字段 {k}: {r}")
        _ok(r["kind"] == "verify", f"kind 应 'verify'，实际 {r['kind']!r}")
        _ok(r["total"] == 10 and r["pass"] == 8 and r["fail"] == 2,
            f"counts mismatch: {r}")
        _ok(r["failed_names"] == ["verify_a.py", "verify_b.py"],
            f"failed_names mismatch: {r['failed_names']}")
        _ok(r["duration_s"] == 12.35, f"duration round err: {r['duration_s']}")
    finally:
        hw.VERIFY_JSONL = orig
    return {"name": name, "passed": True, "record_keys": sorted(r.keys())}


def v3_emit_smoke_roundtrip(tmpdir: Path) -> dict:
    name = "V3_emit_smoke_roundtrip"
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _history_writer as hw
    jpath = tmpdir / "v_smoke.jsonl"
    orig = hw.SMOKE_JSONL
    hw.SMOKE_JSONL = jpath
    try:
        areas = {"audio": "PASS", "vision": "PASS", "tts": "FAIL"}
        ok = hw.emit_smoke(total=3, pass_=2, fail=1, skip=0,
                           duration_s=5.0, areas=areas)
        _ok(ok, "emit_smoke 返回 False")
        recs = hw.load_records(jpath)
        _ok(len(recs) == 1, f"expected 1 smoke record, got {len(recs)}")
        r = recs[0]
        _ok(r["kind"] == "smoke", f"kind={r['kind']}")
        _ok(r["areas"] == areas, f"areas mismatch: {r['areas']}")
        _ok(r["fail"] == 1, f"fail count: {r['fail']}")
    finally:
        hw.SMOKE_JSONL = orig
    return {"name": name, "passed": True, "areas_preserved": True}


def v4_rotate(tmpdir: Path) -> dict:
    name = "V4_rotate_when_over_threshold"
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _history_writer as hw
    jpath = tmpdir / "v_rot.jsonl"
    # write rotate_lines lines first
    with open(jpath, "w") as f:
        for i in range(3):
            f.write(json.dumps({"i": i}) + "\n")
    archive_dir = tmpdir / ".archive"
    # monkey-patch ARCHIVE_DIR
    orig_arch = hw.ARCHIVE_DIR
    hw.ARCHIVE_DIR = archive_dir
    try:
        # 用低 threshold rotate
        archived = hw._rotate_if_needed(jpath, rotate_lines=3)
        _ok(archived is not None, "rotate 未触发")
        _ok(archived.exists(), f"归档文件不存在: {archived}")
        _ok(not jpath.exists(), "rotate 后主 jsonl 应消失，等下一次 append 重建")
        # rotate threshold 不到不触发
        with open(jpath, "w") as f:
            f.write(json.dumps({"i": 0}) + "\n")
        archived2 = hw._rotate_if_needed(jpath, rotate_lines=3)
        _ok(archived2 is None, "未到 threshold 不应 rotate")
    finally:
        hw.ARCHIVE_DIR = orig_arch
    return {"name": name, "passed": True,
            "archived_path": str(archived.relative_to(tmpdir))}


def v5_run_verify_all_wire_in() -> dict:
    name = "V5_run_verify_all_wire_in"
    text = (SCRIPTS_DIR / "run_verify_all.py").read_text()
    _ok("_emit_history(" in text, "run_verify_all.py 缺 _emit_history 调用")
    _ok("from _history_writer import emit_verify" in text,
        "run_verify_all.py 未 import emit_verify")
    return {"name": name, "passed": True}


def v6_smoke_wire_in() -> dict:
    name = "V6_smoke_wire_in"
    text = (SCRIPTS_DIR / "smoke.py").read_text()
    _ok("_emit_smoke_history(" in text, "smoke.py 缺 _emit_smoke_history 调用")
    _ok("from _history_writer import emit_smoke" in text,
        "smoke.py 未 import emit_smoke")
    return {"name": name, "passed": True}


def v7_health_summary_cli(tmpdir: Path) -> dict:
    name = "V7_health_summary_cli"
    # 准备 fake jsonl
    vpath = tmpdir / "v.jsonl"
    spath = tmpdir / "s.jsonl"
    with open(vpath, "w") as f:
        f.write(json.dumps({"ts": "2026-05-15T00:00:00+00:00", "kind": "verify",
                            "total": 10, "pass": 8, "fail": 2, "skip": 0,
                            "duration_s": 12.0,
                            "failed_names": ["verify_x.py", "verify_y.py"]}) + "\n")
        f.write(json.dumps({"ts": "2026-05-15T01:00:00+00:00", "kind": "verify",
                            "total": 10, "pass": 9, "fail": 1, "skip": 0,
                            "duration_s": 11.0,
                            "failed_names": ["verify_x.py"]}) + "\n")
    with open(spath, "w") as f:
        f.write(json.dumps({"ts": "2026-05-15T00:00:00+00:00", "kind": "smoke",
                            "total": 3, "pass": 3, "fail": 0, "skip": 0,
                            "duration_s": 5.0,
                            "areas": {"audio": "PASS", "vision": "PASS",
                                      "tts": "PASS"}}) + "\n")
    # JSON mode
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "health_summary.py"),
         "--verify-jsonl", str(vpath), "--smoke-jsonl", str(spath),
         "--json"],
        capture_output=True, text=True, check=True,
    )
    obj = json.loads(r.stdout)
    _ok("verify" in obj and "smoke" in obj, f"--json 缺 verify/smoke 段: {obj}")
    v = obj["verify"]
    _ok(v["count"] == 2, f"verify count={v['count']}")
    _ok(v["pass_rate"] == round(17/20, 4), f"pass_rate={v['pass_rate']}")
    top = {e["name"] for e in v["top_failing"]}
    _ok("verify_x.py" in top, f"top_failing 应含 verify_x.py: {top}")
    s = obj["smoke"]
    _ok(s["count"] == 1 and s["areas"]["audio"]["pass_rate"] == 1.0,
        f"smoke areas: {s.get('areas')}")
    # table mode
    r2 = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "health_summary.py"),
         "--verify-jsonl", str(vpath), "--smoke-jsonl", str(spath)],
        capture_output=True, text=True, check=True,
    )
    _ok("verify history" in r2.stdout, "table 缺 verify history 段")
    _ok("top_failing" in r2.stdout, "table 缺 top_failing")
    _ok("areas" in r2.stdout, "table 缺 areas")
    return {"name": name, "passed": True,
            "verify_summary": v, "smoke_summary": s}


def v8_restore_protects_history(tmpdir: Path) -> dict:
    """dogfood: restore_unrelated_evidence 永远不动 evidence/_history/*。"""
    name = "V8_restore_protects_history"
    # 建临时 git repo，模拟 evidence/_history 改动 + evidence/<other>/x 改动
    repo = tmpdir / "fakerepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    # baseline files
    (repo / "evidence" / "_history").mkdir(parents=True)
    (repo / "evidence" / "_history" / "verify_history.jsonl").write_text("base\n")
    (repo / "evidence" / "other").mkdir()
    (repo / "evidence" / "other" / "f.json").write_text("base\n")
    (repo / "evidence" / "myfeat").mkdir()
    (repo / "evidence" / "myfeat" / "f.json").write_text("base\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
    # modify all three
    (repo / "evidence" / "_history" / "verify_history.jsonl").write_text("CHANGED\n")
    (repo / "evidence" / "other" / "f.json").write_text("CHANGED\n")
    (repo / "evidence" / "myfeat" / "f.json").write_text("CHANGED\n")
    # call restore with target=myfeat
    sys.path.insert(0, str(SCRIPTS_DIR))
    from restore_unrelated_evidence import restore_unrelated_evidence
    restored = restore_unrelated_evidence("myfeat", repo_root=repo, dry_run=False)
    _ok("evidence/other/f.json" in restored,
        f"应 restore evidence/other/f.json, got {restored}")
    _ok("evidence/_history/verify_history.jsonl" not in restored,
        f"_history/* 不该被 restore，got {restored}")
    # 实际文件验证
    _ok((repo / "evidence" / "other" / "f.json").read_text() == "base\n",
        "evidence/other/f.json 应被 restore 回 base")
    _ok((repo / "evidence" / "_history" / "verify_history.jsonl").read_text() == "CHANGED\n",
        "_history/* 内容不应回退")
    _ok((repo / "evidence" / "myfeat" / "f.json").read_text() == "CHANGED\n",
        "本 feature evidence/myfeat/f.json 不应回退")
    return {"name": name, "passed": True, "restored_count": len(restored)}


def v9_runtime_zero_impact_via_env(tmpdir: Path) -> dict:
    """COCO_HISTORY_DISABLE=1 时 emit_* 立即 return False，不创建文件。"""
    name = "V9_runtime_zero_impact_env_disable"
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _history_writer as hw
    jpath = tmpdir / "v_disabled.jsonl"
    orig = hw.VERIFY_JSONL
    hw.VERIFY_JSONL = jpath
    orig_env = os.environ.get("COCO_HISTORY_DISABLE")
    os.environ["COCO_HISTORY_DISABLE"] = "1"
    try:
        ok = hw.emit_verify(total=1, pass_=1, fail=0, skip=0, duration_s=0.1)
        _ok(ok is False, "DISABLE=1 时 emit_verify 应返回 False")
        _ok(not jpath.exists(), f"DISABLE=1 时 jsonl 不应被创建: {jpath}")
    finally:
        hw.VERIFY_JSONL = orig
        if orig_env is None:
            os.environ.pop("COCO_HISTORY_DISABLE", None)
        else:
            os.environ["COCO_HISTORY_DISABLE"] = orig_env
    return {"name": name, "passed": True}


def v10_schema_stable_across_appends(tmpdir: Path) -> dict:
    name = "V10_schema_stable_across_appends"
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _history_writer as hw
    jpath = tmpdir / "v_stab.jsonl"
    orig = hw.VERIFY_JSONL
    hw.VERIFY_JSONL = jpath
    try:
        hw.emit_verify(total=1, pass_=1, fail=0, skip=0, duration_s=0.5)
        hw.emit_verify(total=2, pass_=1, fail=1, skip=0, duration_s=0.7,
                       failed_names=["x.py"])
        recs = hw.load_records(jpath)
        _ok(len(recs) == 2, f"expected 2 records, got {len(recs)}")
        k0 = set(recs[0].keys())
        k1 = set(recs[1].keys())
        _ok(k0 == k1, f"schema drift: {k0 ^ k1}")
    finally:
        hw.VERIFY_JSONL = orig
    return {"name": name, "passed": True, "schema_keys": sorted(k0)}


def main() -> int:
    failed: list[str] = []
    results: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        checks = [
            ("v1", v1_history_writer_imports, ()),
            ("v2", v2_emit_verify_roundtrip, (tmpdir / "v2",)),
            ("v3", v3_emit_smoke_roundtrip, (tmpdir / "v3",)),
            ("v4", v4_rotate, (tmpdir / "v4",)),
            ("v5", v5_run_verify_all_wire_in, ()),
            ("v6", v6_smoke_wire_in, ()),
            ("v7", v7_health_summary_cli, (tmpdir / "v7",)),
            ("v8", v8_restore_protects_history, (tmpdir / "v8",)),
            ("v9", v9_runtime_zero_impact_via_env, (tmpdir / "v9",)),
            ("v10", v10_schema_stable_across_appends, (tmpdir / "v10",)),
        ]
        for tag, fn, args in checks:
            sub_tmp = args[0] if args else None
            if sub_tmp is not None:
                sub_tmp.mkdir(parents=True, exist_ok=True)
            print(f"\n--- {tag} {fn.__name__} ---")
            try:
                r = fn(*args)
                print(f"  PASS: {r}")
                results.append({**r, "tag": tag})
            except AssertionError as e:
                print(f"  FAIL: {e}", file=sys.stderr)
                failed.append(fn.__name__)
                results.append({"name": fn.__name__, "tag": tag, "passed": False, "error": str(e)})
            except Exception as e:  # pragma: no cover
                print(f"  ERROR: {fn.__name__}: {e!r}", file=sys.stderr)
                failed.append(fn.__name__)
                results.append({"name": fn.__name__, "tag": tag, "passed": False, "error": repr(e)})

    summary_path = EVIDENCE_DIR / "verify_summary.json"
    summary = {
        "feature_id": "infra-016",
        "title": "verify/smoke history jsonl + health_summary CLI + restore protect",
        "phase": 13,
        "branch": "feat/infra-016",
        "verify_script": "scripts/verify_infra_016.py",
        "results": results,
        "totals": f"{len(results) - len(failed)}/{len(results)} PASS",
        "default_off": "N/A — CI/dev tool。jsonl append 默认 always-on，但运行期"
                       "零影响：写盘失败仅 stderr WARN；COCO_HISTORY_DISABLE=1 完"
                       "全关闭（V9 守护）。无 stdout/rc 语义改变。",
        "real_machine_uat": "N/A — pure CI/observability tool",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"\n[verify_infra_016] summary -> {summary_path}")
    if failed:
        print(f"\ninfra-016 verify FAIL: {failed}", file=sys.stderr)
        return 1
    print(f"\ninfra-016 verify PASS ({len(results)}/{len(results)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
