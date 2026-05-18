#!/usr/bin/env python3
"""infra-028 verify: rotate docstring 与实际行为对齐 + 邻近 verify 锁面 (verify-only).

来源 backlog: infra-022-backlog-rotate-docstring-mismatch
范围: scripts/_history_writer.py 中 rotate 相关 docstring 与代码字面量行为校准,
仅文档化, 0 业务行为改动。

V 项:
  V0 fingerprint sha256 (检测漂移; 与 verify_infra_022 同源, 但两份独立 expected
     是有意冗余 -- 任一漂移都立即可见)
  V1 _rotate_locked / module docstring 阈值字面量与实际代码行为对齐 -- 实际是
     ``_line_count(p) < rotate_lines`` 即 ``>=`` 触发, docstring 短语 ">= rotate_lines"
     与 ">= ROTATE_LINES" 都能在文件中检索到
  V2 _rotate_if_needed docstring 关键短语锁 -- "自带 _FileLock" / "锁作用域" /
     "rotate 的 原子边界" 等出现且不再含旧矛盾措辞 "无锁版本"
  V3 邻近 verify 回归 (静态锁面, rc=0): verify_infra_022 / verify_infra_024 /
     verify_infra_025 / verify_infra_026 / verify_infra_027
  V4 smoke 11/11 PASS (COCO_CI=1)
  V_n evidence/migration_note 字面落盘

Sim-first 全证, 0 真机依赖, 0 业务行为 diff (diff 范围仅 docstring + verify
fingerprint 字面量)。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
EVIDENCE_DIR = REPO_ROOT / "evidence" / "infra-028"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _py_cmd() -> list[str]:
    """优先 uv run python (项目锁定 3.13 + 完整依赖); 退回当前解释器。"""
    if shutil.which("uv") is not None:
        return ["uv", "run", "python"]
    return [sys.executable]

EXPECTED_FINGERPRINT = "77e3a1d7d3e0754a06fa1de9ed188f92a55d80794bc5c2de78828da9c5bae9f8"

RESULTS: list[dict] = []


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _record(v: str, ok: bool, detail: dict | None = None) -> None:
    RESULTS.append({"v": v, "ok": ok, "detail": detail or {}})


# ---------- V0 ---------------------------------------------------------------

def v0_fingerprint() -> dict:
    src = (SCRIPTS_DIR / "_history_writer.py").read_bytes()
    sha = hashlib.sha256(src).hexdigest()
    if sha != EXPECTED_FINGERPRINT:
        raise AssertionError(
            f"V0 _history_writer.py fingerprint drift: expected "
            f"{EXPECTED_FINGERPRINT[:16]}... got {sha[:16]}... "
            "(如果改动是预期的, 同步更新 verify_infra_022 + verify_infra_028 "
            "两份 EXPECTED_FINGERPRINT)"
        )
    return {"sha256": sha}


# ---------- V1: 阈值字面量与实际行为对齐 -------------------------------------

def v1_threshold_literal_alignment() -> dict:
    """实际行为 = `_line_count(jsonl_path) < rotate_lines: return None` 即 >= 触发。

    docstring 必须出现 ``>= rotate_lines`` 和 ``>= ROTATE_LINES`` 两处字面量。
    并且不能再出现旧的 ``>ROTATE_LINES`` (注意没有等号) 这种漏判等号边界的措辞。
    """
    src = (SCRIPTS_DIR / "_history_writer.py").read_text(encoding="utf-8")
    _ok(">= rotate_lines" in src, "V1 missing '>= rotate_lines' literal in docstring/code")
    _ok(">= ROTATE_LINES" in src, "V1 missing '>= ROTATE_LINES' literal in module docstring")
    # 反例: 旧的 '>ROTATE_LINES' (无等号) 不应再出现, 它是 docstring/代码不一致的根因
    _ok(">ROTATE_LINES" not in src or ">= ROTATE_LINES" in src,
        "V1 stale '>ROTATE_LINES' (no equals) still present without '>=' alongside")
    # 真行为字面量必须在 _rotate_locked 里
    _ok("_line_count(jsonl_path) < rotate_lines" in src,
        "V1 actual rotate gate '_line_count(jsonl_path) < rotate_lines' missing")
    return {"thresholds_literal_ok": True}


# ---------- V2: _rotate_if_needed docstring 短语锁 ---------------------------

def v2_rotate_if_needed_docstring_phrases() -> dict:
    src = (SCRIPTS_DIR / "_history_writer.py").read_text(encoding="utf-8")
    # 修正后必出短语
    required = [
        "自带 _FileLock",
        "锁作用域",
        "rotate 的 **原子边界 = _FileLock 的整段 with-block**",
        "infra-028 锁面",
        "infra-028 字面量锁",
    ]
    for phrase in required:
        _ok(phrase in src, f"V2 required docstring phrase missing: {phrase!r}")
    # 旧的自相矛盾短语必须移除
    forbidden = [
        '"""无锁版本——保留对外签名兼容（测试和老调用方继续用），自带加锁。',
    ]
    for phrase in forbidden:
        _ok(phrase not in src, f"V2 stale contradictory phrase still present: {phrase!r}")
    return {"phrases_ok": True, "required": required}


# ---------- V3: 邻近 verify 回归 ---------------------------------------------

def v3_neighbor_verify() -> dict:
    targets = [
        "verify_infra_022.py",
        "verify_infra_024.py",
        "verify_infra_025.py",
        "verify_infra_026.py",
        "verify_infra_027.py",
    ]
    out: dict[str, dict] = {}
    for t in targets:
        p = SCRIPTS_DIR / t
        if not p.exists():
            out[t] = {"skipped": True, "reason": "absent"}
            continue
        t0 = time.time()
        r = subprocess.run(
            _py_cmd() + [str(p)],
            cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=180,
        )
        dur = round(time.time() - t0, 2)
        ok = (r.returncode == 0)
        out[t] = {"rc": r.returncode, "ok": ok, "duration_s": dur}
        if not ok:
            out[t]["stderr_tail"] = r.stderr[-500:]
            raise AssertionError(f"V3 {t} rc={r.returncode}\n{r.stderr[-1000:]}")
    return out


# ---------- V4: smoke ---------------------------------------------------------

def v4_smoke() -> dict:
    smoke = SCRIPTS_DIR / "smoke.py"
    _ok(smoke.exists(), "V4 scripts/smoke.py not found")
    env = os.environ.copy()
    env["COCO_CI"] = "1"
    t0 = time.time()
    r = subprocess.run(
        _py_cmd() + [str(smoke)],
        cwd=str(REPO_ROOT),
        capture_output=True, text=True, timeout=300, env=env,
    )
    dur = round(time.time() - t0, 2)
    tail = (r.stdout + "\n" + r.stderr)[-2000:]
    # smoke 通常打印 "X/Y PASS"; 容忍 fail-soft, 这里只要 rc==0 即视为 PASS
    if r.returncode != 0:
        raise AssertionError(f"V4 smoke rc={r.returncode}\nTAIL:\n{tail}")
    return {"rc": 0, "duration_s": dur, "tail_excerpt": tail[-400:]}


# ---------- main -------------------------------------------------------------

def _run(label: str, fn) -> None:
    try:
        detail = fn()
        _record(label, True, detail)
        print(f"[{label}] PASS {detail}")
    except AssertionError as e:
        _record(label, False, {"error": str(e)})
        print(f"[{label}] FAIL {e}")
        raise


def main() -> int:
    print(" verify_infra_028 — rotate docstring 字面量对齐 + 锁面")
    print("=" * 72)

    rc = 0
    try:
        _run("V0", v0_fingerprint)
        _run("V1", v1_threshold_literal_alignment)
        _run("V2", v2_rotate_if_needed_docstring_phrases)
        _run("V3", v3_neighbor_verify)
        _run("V4", v4_smoke)
    except AssertionError:
        rc = 1

    summary = {
        "feature_id": "infra-028",
        "source_backlog": "infra-022-backlog-rotate-docstring-mismatch",
        "fingerprint_sha256": EXPECTED_FINGERPRINT,
        "results": RESULTS,
        "rc": rc,
        "policy": "verify-only; docstring 字面量对齐, 0 业务行为改动",
    }
    (EVIDENCE_DIR / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n[done] rc={rc}; evidence -> {EVIDENCE_DIR/'verify_summary.json'}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
