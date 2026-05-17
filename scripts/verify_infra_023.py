#!/usr/bin/env python3
"""verify_infra_023: smoke._classify_stdout 升级为 list-prefix 规则表 + markdown 列表项支持.

V0 fingerprint   — _CLASSIFIER_RULES 列表存在, 含 skip/warn 两条规则且顺序锁住
                   (FAIL 不在表内, 由 SystemExit 捕获).
V1 bytewise main — 50+ 不含 markdown 列表标记的 stdout 样本, infra-019 实现
                   (内嵌副本) vs infra-023 新实现 逐字节相等.
V2 list-prefix   — monkey-patch _CLASSIFIER_RULES 追加 ('err:', 'ERR'), 验证
                   扩展只需 append, _classify_stdout 主体未改即可识别新 state.
V3 priority      — 同一段 stdout 多行不同 prefix 命中, 返回顺序按 RULES list
                   顺序 (SKIP > WARN). 含 markdown 列表混入样本.
V4 regression    — scripts/verify_infra_019.py / 020 / 021 / 022 子进程 rc=0.
V5 smoke         — ./init.sh smoke 整体 rc=0.

来源 backlog: infra-019-backlog-classifier-list-prefix.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import scripts.smoke as smoke  # noqa: E402


# --- main 版本 (infra-019) 行为副本, 仅作 V1 bytewise oracle 用 -----------------
def _classify_stdout_main_oracle(text: str) -> str:
    """infra-019 main HEAD=cb6dacb 时 _classify_stdout 行为的精确副本."""
    has_skip = False
    has_warn = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        head_lo = line.lower()
        if head_lo.startswith("skip:"):
            has_skip = True
        elif head_lo.startswith("warn:"):
            has_warn = True
    if has_skip:
        return "SKIP"
    if has_warn:
        return "WARN"
    return "PASS"


def _ok(v: int, msg: str) -> None:
    print(f"V{v} PASS: {msg}")


def _fail(v: int, msg: str) -> None:
    print(f"V{v} FAIL: {msg}")
    sys.exit(1)


# --- V0 fingerprint -----------------------------------------------------------
def v0_fingerprint() -> dict:
    rules = getattr(smoke, "_CLASSIFIER_RULES", None)
    if rules is None:
        _fail(0, "smoke._CLASSIFIER_RULES 不存在")
    if not isinstance(rules, list):
        _fail(0, f"_CLASSIFIER_RULES 应为 list, got {type(rules).__name__}")
    if len(rules) < 2:
        _fail(0, f"_CLASSIFIER_RULES 至少 2 条规则 (skip/warn), got {len(rules)}")
    # 顺序锁: 第一条必须 skip:, 第二条必须 warn: (SKIP 优先于 WARN 与 main 等价)
    if rules[0] != ("skip:", "SKIP"):
        _fail(0, f"_CLASSIFIER_RULES[0] 应 == ('skip:', 'SKIP'), got {rules[0]!r}")
    if rules[1] != ("warn:", "WARN"):
        _fail(0, f"_CLASSIFIER_RULES[1] 应 == ('warn:', 'WARN'), got {rules[1]!r}")
    # FAIL 不应在表内 (由 SystemExit 捕获, 见 _classify_stdout docstring)
    for prefix, state in rules:
        if state == "FAIL":
            _fail(0, "_CLASSIFIER_RULES 不应含 FAIL (FAIL 由 SystemExit 捕获)")
    # markdown 列表项剥离常量存在
    bullets = getattr(smoke, "_LIST_BULLETS", None)
    if bullets is None:
        _fail(0, "smoke._LIST_BULLETS 不存在")
    for ch in "-*+":
        if ch not in bullets:
            _fail(0, f"_LIST_BULLETS 应含 {ch!r}, got {bullets!r}")
    _ok(0, f"_CLASSIFIER_RULES={rules!r} 顺序锁 SKIP>WARN; _LIST_BULLETS={bullets!r}")
    return {"rules": rules, "bullets": bullets}


# --- V1 bytewise vs main ------------------------------------------------------
def v1_bytewise_main() -> int:
    samples = [
        # PASS 样本 (无 marker)
        "ok",
        "",
        "hello world",
        "all good",
        "running...\ndone",
        "...",
        "abc\ndef\nghi",
        "log: nothing important",
        "test passed",
        "完成",
        # SKIP 样本 (行级前缀)
        "SKIP: model not downloaded",
        "skip: foo",
        "Skip: bar",
        "  SKIP:   indented",
        "before\nSKIP: middle\nafter",
        "SKIP: a\nSKIP: b",
        "SKIP:no-space",
        "SKIP: x\nok",
        "noise\nSKIP: x",
        "SKIP: 末尾\n",
        # WARN 样本
        "WARN: deprecated",
        "warn: foo",
        "Warn: bar",
        "  WARN: indented",
        "WARN: a\nWARN: b",
        "ok\nWARN: x",
        "WARN: x\nok",
        "WARN: model not downloaded, skipped",  # infra-019 修复点: 不应误判为 SKIP
        "WARN: skipped install",
        "WARN:no-space",
        # SKIP + WARN 共存 (SKIP 优先)
        "SKIP: a\nWARN: b",
        "WARN: a\nSKIP: b",
        "SKIP: x\nWARN: y\nWARN: z",
        "ok\nSKIP: a\nWARN: b\nok",
        # 含 "skip"/"warn" 子串但非前缀 (不应命中, infra-019 关键 fix)
        "task skipped",
        "no warning",
        "skipping ahead",
        "warned about it",
        "the test was skipped due to env",
        # 空白 / 边角
        "   ",
        "\n\n\n",
        "\nSKIP: x\n",
        "WARN:\n",
        "SKIP:",
        "WARN:",
        # 多行混合
        "line1\nline2\nWARN: w1\nline4\nSKIP: s1\nline6",
        "a\nb\nc\nd\ne",
        # 大小写边界
        "skip:lower",
        "SKIP:upper",
        "Skip:mixed",
        "WARN:upper",
        "warn:lower",
        "Warn:mixed",
    ]
    assert len(samples) >= 50, f"V1 至少 50 样本, got {len(samples)}"
    mismatches = []
    for i, s in enumerate(samples):
        expect = _classify_stdout_main_oracle(s)
        actual = smoke._classify_stdout(s)
        if expect != actual:
            mismatches.append((i, expect, actual, s[:60]))
    if mismatches:
        for m in mismatches[:5]:
            print(f"  mismatch[{m[0]}]: main={m[1]} new={m[2]} sample={m[3]!r}")
        _fail(1, f"{len(mismatches)}/{len(samples)} 样本与 main 不等价")
    _ok(1, f"{len(samples)} 样本与 main _classify_stdout bytewise 等价")
    return len(samples)


# --- V2 list-prefix 扩展性 -----------------------------------------------------
def v2_list_prefix_extensibility() -> None:
    # 1. 默认 RULES 下, 含 'ERR:' 行只能落 PASS (不在表内)
    text = "ERR: something broken"
    rv = smoke._classify_stdout(text)
    if rv != "PASS":
        _fail(2, f"默认 RULES 下 'ERR:' 行应 PASS, got {rv}")
    # 2. monkey-patch RULES 追加 ('err:', 'ERR'), 不改 _classify_stdout 主体即可识别
    orig = list(smoke._CLASSIFIER_RULES)
    try:
        smoke._CLASSIFIER_RULES.append(("err:", "ERR"))
        rv2 = smoke._classify_stdout(text)
        if rv2 != "ERR":
            _fail(2, f"扩展 RULES 后 'ERR:' 应分类为 ERR, got {rv2}")
        # 验证扩展后, 原有规则不破坏
        if smoke._classify_stdout("SKIP: x") != "SKIP":
            _fail(2, "扩展后 SKIP 规则破坏")
        if smoke._classify_stdout("WARN: x") != "WARN":
            _fail(2, "扩展后 WARN 规则破坏")
        # 优先级: SKIP > WARN > ERR (ERR 加在末尾)
        rv3 = smoke._classify_stdout("ERR: a\nWARN: b\nSKIP: c")
        if rv3 != "SKIP":
            _fail(2, f"优先级 SKIP>WARN>ERR 失败, got {rv3}")
        # 列表项前缀对新规则同样生效 (lstrip 是统一前置步骤)
        if smoke._classify_stdout("- ERR: x") != "ERR":
            _fail(2, "扩展规则不享受 list-prefix 剥离")
    finally:
        smoke._CLASSIFIER_RULES[:] = orig
    # 3. 恢复后再次确认 'ERR:' 不命中
    rv4 = smoke._classify_stdout(text)
    if rv4 != "PASS":
        _fail(2, f"恢复 RULES 后 'ERR:' 应回到 PASS, got {rv4}")
    _ok(2, "RULES append 一项 ('err:','ERR') 即扩展, _classify_stdout 主体未改")


# --- V3 priority + markdown 列表项 ---------------------------------------------
def v3_priority_and_list() -> None:
    # 单行多 marker (startswith 互斥, 只命中首个 prefix 按 list 顺序)
    # "SKIP: WARN: ..." 单行 → SKIP (RULES[0] 首个命中)
    if smoke._classify_stdout("SKIP: WARN: both") != "SKIP":
        _fail(3, "单行 'SKIP: WARN:' 应命中 SKIP")
    # 多行共存
    if smoke._classify_stdout("WARN: a\nSKIP: b") != "SKIP":
        _fail(3, "多行 WARN+SKIP 应 SKIP 优先")
    # markdown 列表项前缀剥离 (backlog 原意)
    cases = [
        ("- SKIP: foo", "SKIP"),
        ("* SKIP: foo", "SKIP"),
        ("+ SKIP: foo", "SKIP"),
        ("- WARN: foo", "WARN"),
        ("* WARN: foo", "WARN"),
        ("  - SKIP: indented bullet", "SKIP"),
        ("- WARN: a\n- SKIP: b", "SKIP"),
        ("* SKIP: a\n* WARN: b\n* WARN: c", "SKIP"),
        ("- normal text", "PASS"),
        ("-SKIP: no-space-after-bullet", "SKIP"),  # lstrip '-*+ \t' 后剩 'SKIP:...'
    ]
    for text, expect in cases:
        rv = smoke._classify_stdout(text)
        if rv != expect:
            _fail(3, f"list-prefix case {text!r} 期望 {expect}, got {rv}")
    _ok(3, f"优先级 SKIP>WARN + markdown 列表项剥离 ({len(cases)} cases) 正确")


# --- V4 regression: 前序 verify 子进程 rc=0 -----------------------------------
def v4_regression() -> list[dict]:
    targets = ["verify_infra_019.py", "verify_infra_020.py",
               "verify_infra_021.py", "verify_infra_022.py"]
    results = []
    for t in targets:
        p = REPO / "scripts" / t
        if not p.exists():
            _fail(4, f"{t} 不存在")
        t0 = time.time()
        rv = subprocess.run([sys.executable, str(p)], cwd=str(REPO),
                            capture_output=True, text=True, timeout=600)
        dt = time.time() - t0
        results.append({"script": t, "rc": rv.returncode, "elapsed_s": round(dt, 2)})
        if rv.returncode != 0:
            print(rv.stdout[-2000:])
            print("STDERR:", rv.stderr[-1000:])
            _fail(4, f"{t} rc={rv.returncode}")
    _ok(4, f"regression {len(targets)} verify 全 rc=0: {results}")
    return results


# --- V5 smoke 整体 ./init.sh rc=0 ---------------------------------------------
def v5_smoke() -> dict:
    init = REPO / "init.sh"
    if not init.exists():
        _fail(5, "./init.sh 不存在")
    t0 = time.time()
    rv = subprocess.run(["bash", str(init)], cwd=str(REPO),
                        capture_output=True, text=True, timeout=900)
    dt = time.time() - t0
    info = {"rc": rv.returncode, "elapsed_s": round(dt, 2),
            "stdout_tail": rv.stdout[-400:]}
    if rv.returncode != 0:
        print(rv.stdout[-3000:])
        print("STDERR:", rv.stderr[-1500:])
        _fail(5, f"./init.sh rc={rv.returncode}")
    _ok(5, f"./init.sh rc=0 in {info['elapsed_s']}s")
    return info


def main() -> int:
    print("=== verify_infra_023 ===")
    fp = v0_fingerprint()
    n_samples = v1_bytewise_main()
    v2_list_prefix_extensibility()
    v3_priority_and_list()
    regr = v4_regression()
    smoke_info = v5_smoke()

    summary = {
        "feature": "infra-023",
        "fingerprint": {
            "rules": fp["rules"],
            "bullets": fp["bullets"],
        },
        "v1_bytewise_samples": n_samples,
        "v4_regression": regr,
        "v5_smoke": {"rc": smoke_info["rc"], "elapsed_s": smoke_info["elapsed_s"]},
        "result": "PASS",
    }
    out_dir = REPO / "evidence" / "infra-023"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "verify_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"summary written: {out}")
    print("=== ALL V0-V5 PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
