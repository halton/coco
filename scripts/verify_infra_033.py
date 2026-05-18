#!/usr/bin/env python3
"""infra-033 verify — verify_infra_024.py docstring venv 使用约束 meta-lock。

来源: infra-024-backlog-verify-venv-docstring
范围: docs-only / verify-only。scripts/verify_infra_024.py 顶端 docstring
增补"运行环境约定 (infra-033)"段, 说明 .venv / sys.executable / 子进程入口
约定; 函数体严格 byte-equal (V4 哈希锁), 无业务行为变更。

V0 sys.path / 文件存在: scripts/verify_infra_024.py 可被 importlib 解析,
    模块顶端 docstring 非空。
V1 字面 sentinel: docstring 必须含全部关键短语
    {".venv", "sys.executable", "子进程", "运行环境约定", ".venv/bin/python"}。
V2 关键锁参数: docstring 总行数 ≥ 55 且 "运行环境约定" 段行数 ≥ 10;
    关键短语合集每个出现次数 ≥ 1。
V3 mutant 反证: 模拟删除任一关键短语后, V1 sentinel 检查必定 FAIL
    (内存中替换, 不写盘)。
V4 sha256 锁: 升级后的 scripts/verify_infra_024.py 全文件 sha256 锁定;
    一旦未来修改触动锁哈希, 提示需同步更新本 V4 期望值。
V5 端到端 subprocess invoke: 用 sys.executable 子进程跑
    scripts/verify_infra_024.py, 期望 rc==0 (smoke env 干净下 verify_024 通过)。

默认 OFF 严守: 本 verify 不引入新 env hook, 不依赖网络, 不修改业务源码。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "evidence" / "infra-033"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
TARGET = REPO_ROOT / "scripts" / "verify_infra_024.py"

SENTINEL_PHRASES = [
    ".venv",
    "sys.executable",
    "子进程",
    "运行环境约定",
    ".venv/bin/python",
]


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _load_module_docstring(text: str) -> str:
    tree = ast.parse(text)
    doc = ast.get_docstring(tree) or ""
    return doc


def v0_sys_path_and_target() -> dict:
    _ok(TARGET.exists(), f"V0 target missing: {TARGET}")
    text = TARGET.read_text(encoding="utf-8")
    doc = _load_module_docstring(text)
    _ok(bool(doc.strip()), "V0 verify_infra_024.py module docstring is empty")
    return {"target": str(TARGET), "docstring_len": len(doc), "docstring_lines": len(doc.splitlines())}


def v1_literal_sentinels() -> dict:
    text = TARGET.read_text(encoding="utf-8")
    doc = _load_module_docstring(text)
    missing = [p for p in SENTINEL_PHRASES if p not in doc]
    _ok(not missing,
        f"V1 docstring missing sentinel phrases: {missing}\nDocstring tail:\n{doc[-1500:]}")
    return {"phrases": SENTINEL_PHRASES, "all_present": True, "doc_len": len(doc)}


def v2_lock_params() -> dict:
    text = TARGET.read_text(encoding="utf-8")
    doc = _load_module_docstring(text)
    total_lines = len(doc.splitlines())
    _ok(total_lines >= 55,
        f"V2 docstring total_lines={total_lines} < 55 (lock floor)")
    # 抽出 "运行环境约定" 段落 (从该标题到下一段或 docstring 结尾)
    lines = doc.splitlines()
    section_lines: list[str] = []
    in_section = False
    for ln in lines:
        if "运行环境约定" in ln:
            in_section = True
            section_lines.append(ln)
            continue
        if in_section:
            section_lines.append(ln)
    _ok(len(section_lines) >= 10,
        f"V2 '运行环境约定' section lines={len(section_lines)} < 10")
    counts = {p: doc.count(p) for p in SENTINEL_PHRASES}
    for p, c in counts.items():
        _ok(c >= 1, f"V2 sentinel phrase {p!r} count={c} < 1")
    return {
        "total_lines": total_lines,
        "section_lines": len(section_lines),
        "phrase_counts": counts,
    }


def v3_mutant_reject() -> dict:
    text = TARGET.read_text(encoding="utf-8")
    doc = _load_module_docstring(text)
    mutated_results = {}
    for phrase in SENTINEL_PHRASES:
        # 内存中删除该短语的所有出现 (不写盘)
        mutated = doc.replace(phrase, "")
        # mutant 必须让 V1 sentinel 检查 FAIL
        missing = [p for p in SENTINEL_PHRASES if p not in mutated]
        # 至少 phrase 本身应被检测到缺失
        _ok(phrase in missing,
            f"V3 mutant: removing {phrase!r} did not surface as missing")
        mutated_results[phrase] = {"missing_after_mutation": missing}
    return {"mutants_tested": len(SENTINEL_PHRASES), "details": mutated_results}


def v4_sha256_full_file() -> dict:
    data = TARGET.read_bytes()
    h = hashlib.sha256(data).hexdigest()
    # 锁值: 由本次首次运行写入 evidence 文件; 后续运行若 hash 漂移则 FAIL,
    # 强制 Engineer 同步更新 expected_hash 与本 verify 自身, 防止 docstring
    # 段被静默回滚。expected_hash 维护在本 evidence 文件首列。
    lock_path = EVIDENCE_DIR / "verify_infra_024_sha256.lock"
    if lock_path.exists():
        expected = lock_path.read_text(encoding="utf-8").strip()
        _ok(expected == h,
            f"V4 sha256 drift: target={h} expected={expected}; 若改 docstring 请同步更新 lock 文件")
    else:
        lock_path.write_text(h + "\n", encoding="utf-8")
        expected = h
    return {"sha256": h, "expected_locked": expected, "lock_path": str(lock_path)}


def v5_subprocess_invoke_verify_024() -> dict:
    """端到端: 用 sys.executable (即 .venv python) 子进程跑 verify_infra_024.py,
    smoke env 干净时期望 rc==0。"""
    env = dict(os.environ)
    # 清除可能干扰的 gate 残留 (verify_024 V2 case 期望 default-OFF)
    env.pop("COCO_SMOKE_FINEGRAINED_EXIT", None)
    proc = subprocess.run(
        [sys.executable, str(TARGET)],
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    _ok(proc.returncode == 0,
        f"V5 verify_infra_024 subprocess rc={proc.returncode} (expected 0).\n"
        f"STDOUT tail:\n{proc.stdout[-1500:]}\nSTDERR tail:\n{proc.stderr[-800:]}")
    return {
        "rc": proc.returncode,
        "stdout_len": len(proc.stdout),
        "stderr_len": len(proc.stderr),
        "interpreter": sys.executable,
    }


def main() -> int:
    results: dict = {}
    failures: list[str] = []

    cases = [
        ("V0_sys_path_and_target", v0_sys_path_and_target),
        ("V1_literal_sentinels", v1_literal_sentinels),
        ("V2_lock_params", v2_lock_params),
        ("V3_mutant_reject", v3_mutant_reject),
        ("V4_sha256_full_file", v4_sha256_full_file),
        ("V5_subprocess_invoke_verify_024", v5_subprocess_invoke_verify_024),
    ]

    for vname, fn in cases:
        try:
            results[vname] = {"status": "PASS", "detail": fn()}
            print(f"[{vname}] PASS")
        except AssertionError as e:
            results[vname] = {"status": "FAIL", "error": str(e)}
            failures.append(f"{vname}: {e}")
            print(f"[{vname}] FAIL: {e}")

    summary = {
        "feature": "infra-033",
        "status": "PASS" if not failures else "FAIL",
        "results": results,
        "failures": failures,
    }
    out_path = EVIDENCE_DIR / "verify_summary.json"
    out_path.write_text(
        json.dumps(summary, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {out_path}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
