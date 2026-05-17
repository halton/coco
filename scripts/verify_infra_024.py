#!/usr/bin/env python3
"""infra-024 verify: smoke.py 真退出码 e2e (V5) — 把 infra-019 V1-V4 classifier
单元测闭环到 subprocess 真 rc。

来源 backlog: infra-019-backlog-finegrained-exit-e2e-v5
    "verify_infra_019.py 新增 V5 — subprocess.run(['python','scripts/smoke.py'],
     env={'COCO_SMOKE_FINEGRAINED_EXIT':'1', 'COCO_FAKE_MODEL_MISSING':'1' 等})
     断言 rc==2 (而非 1), 把 classifier 单元测 (V1-V4) 闭环到 smoke 真退出码。
     default-ON 路径不变, V5 仅 ON 模式独立 subprocess。"

实现说明
--------
smoke.py 没有 ``COCO_FAKE_MODEL_MISSING`` env hook (也不必加, 避免源码膨胀)。
ASR/TTS/VAD/KWS 子检查均查 ``Path.home() / ".cache/coco/..."`` 模型路径,
所以本验证通过 **临时 HOME 重定向** (HOME=$(mktemp -d)) 在子进程内强制
"模型缺失" 触发 WARN 路径, 而不污染开发者 ~/.cache/coco。

Acceptance
----------
V0 静态断言: smoke.py 仍导出 ``_finegrained_exit_enabled`` 且 1/true/yes 别名生效;
    fine-grained gate 默认 OFF (空 env / 未设值时返回 False)。
V1 ON 路径 e2e: HOME=tmp + COCO_CI=1 + COCO_SMOKE_FINEGRAINED_EXIT=1
    → 子进程 rc==2 (WARN 存在 / 无 FAIL, WARN 优先于 SKIP)。
V2 OFF 默认 e2e: HOME=tmp + COCO_CI=1 + 不设 GATE → 子进程 rc==0
    (default-OFF 等价 main; 即使存在 WARN/SKIP)。
V3 OFF 显式 e2e: HOME=tmp + COCO_CI=1 + COCO_SMOKE_FINEGRAINED_EXIT=0
    → 子进程 rc==0 (显式 0 与未设值一致)。
V4 ON 别名 e2e: HOME=tmp + COCO_CI=1 + COCO_SMOKE_FINEGRAINED_EXIT=true
    → 子进程 rc==2 (env 别名 1/true/yes 全部生效, 与 infra-018 文档对齐)。
V5 stdout 真实链路: V1 子进程 stdout 含 "WARN:" 行 + "SKIP:" 行 + "Smoke 通过"
    收尾, 反证 classifier 经子检查 _run 包装真实触达 areas dict 并被
    fine-grained gate 消费。

Sim-first: 全程子进程隔离, HOME 临时目录, 不依赖真模型 / 不动开发者环境;
真机依赖: 无 (纯 harness + classifier e2e)。

default-OFF 严守: 本 verify 不修改 smoke.py 源码, 不引入新 env hook;
未设置 COCO_SMOKE_FINEGRAINED_EXIT 时 smoke rc 与 main bytewise 等价 (V2 锁)。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "evidence" / "infra-024"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
SMOKE_PATH = REPO_ROOT / "scripts" / "smoke.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("coco_smoke", SMOKE_PATH)
    assert spec and spec.loader, "cannot load scripts/smoke.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _run_smoke(env_overrides: dict) -> tuple[int, str, str]:
    """Run scripts/smoke.py in an isolated subprocess with HOME redirected to
    a fresh tmpdir (forces model-missing WARN paths) plus the supplied env.

    Returns (rc, stdout, stderr). The tmpdir is removed on exit.
    """
    with tempfile.TemporaryDirectory(prefix="infra024-home-") as home_tmp:
        env = dict(os.environ)
        # 关键: 重定向 HOME 让 ~/.cache/coco/{asr,tts,kws}/*.onnx 全部缺失,
        # 进而触发 smoke_asr / smoke_tts / smoke_vad / smoke_wake_word 的 WARN 路径。
        env["HOME"] = home_tmp
        # macOS: 部分 lib 也看 XDG_CACHE_HOME, 一并重定向以求纯净 (无负面副作用)。
        env["XDG_CACHE_HOME"] = str(Path(home_tmp) / ".cache")
        # 跳过真麦克 (audio 子检查打 SKIP, 不依赖声卡)。
        env["COCO_CI"] = "1"
        # 清除可能干扰的旧 gate 残留, 再叠用例 env。
        env.pop("COCO_SMOKE_FINEGRAINED_EXIT", None)
        env.update(env_overrides)
        proc = subprocess.run(
            [sys.executable, str(SMOKE_PATH)],
            env=env,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        return proc.returncode, proc.stdout, proc.stderr


def v0_static_gate(smoke) -> dict:
    fn = smoke._finegrained_exit_enabled

    # default-OFF: 缺省 env / 空值 / 杂值一律 False
    orig = os.environ.pop("COCO_SMOKE_FINEGRAINED_EXIT", None)
    try:
        for raw in ("", " ", "0", "no", "false", "off", "random"):
            os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = raw
            _ok(fn() is False,
                f"V0 default-OFF: COCO_SMOKE_FINEGRAINED_EXIT={raw!r} should disable, got True")
        # ON 别名: 1 / true / yes (与 smoke.py L460 .strip() in ("1","true","yes") 一致;
        # case-sensitive — 大写 'TRUE' 不是 alias, 这点 V0 显式锁住, 与 infra-018 文档对齐)
        for raw in ("1", "true", "yes", " 1 ", "  true  ", "yes "):
            os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = raw
            _ok(fn() is True,
                f"V0 ON alias: COCO_SMOKE_FINEGRAINED_EXIT={raw!r} should enable, got False")
        # 大小写敏感反证: 'TRUE' / 'Yes' 不属于 alias 集合
        for raw in ("TRUE", "Yes", "YES", "True"):
            os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = raw
            _ok(fn() is False,
                f"V0 case-sensitive: COCO_SMOKE_FINEGRAINED_EXIT={raw!r} should NOT enable (alias is lowercase), got True")
        # 完全删除 env 也是 False
        os.environ.pop("COCO_SMOKE_FINEGRAINED_EXIT", None)
        _ok(fn() is False, "V0 missing env should be False")
    finally:
        os.environ.pop("COCO_SMOKE_FINEGRAINED_EXIT", None)
        if orig is not None:
            os.environ["COCO_SMOKE_FINEGRAINED_EXIT"] = orig
    return {"aliases_checked": ["1", "true", "yes", " 1 ", "  true  ", "yes "],
            "case_sensitive_negatives": ["TRUE", "Yes", "YES", "True"],
            "off_values_checked": ["", " ", "0", "no", "false", "off", "random", "<missing>"]}


def v1_on_path_rc2(stdout_buf: dict) -> dict:
    rc, out, err = _run_smoke({"COCO_SMOKE_FINEGRAINED_EXIT": "1"})
    stdout_buf["v1"] = out
    _ok(rc == 2,
        f"V1 ON path expected rc=2 (WARN priority), got rc={rc}.\nSTDOUT tail:\n{out[-1000:]}\nSTDERR:\n{err[-500:]}")
    return {"rc": rc, "stdout_len": len(out), "stderr_len": len(err)}


def v2_off_default_rc0() -> dict:
    rc, out, err = _run_smoke({})  # GATE 未设 = default-OFF
    _ok(rc == 0,
        f"V2 OFF default expected rc=0 (bytewise equiv main), got rc={rc}.\nSTDOUT tail:\n{out[-800:]}")
    return {"rc": rc}


def v3_off_explicit_rc0() -> dict:
    rc, out, err = _run_smoke({"COCO_SMOKE_FINEGRAINED_EXIT": "0"})
    _ok(rc == 0,
        f"V3 OFF explicit (=0) expected rc=0, got rc={rc}.\nSTDOUT tail:\n{out[-800:]}")
    return {"rc": rc}


def v4_on_alias_rc2() -> dict:
    rc, out, err = _run_smoke({"COCO_SMOKE_FINEGRAINED_EXIT": "true"})
    _ok(rc == 2,
        f"V4 ON alias (=true) expected rc=2, got rc={rc}.\nSTDOUT tail:\n{out[-800:]}")
    return {"rc": rc}


def v5_stdout_real_chain(stdout_buf: dict) -> dict:
    """反证: V1 已跑过 ON 路径, 复用其 stdout 检查 classifier 链路真实触达。"""
    out = stdout_buf.get("v1", "")
    _ok("Smoke 通过" in out,
        f"V5 expected '==> Smoke 通过' in V1 stdout (no FAIL path), tail:\n{out[-1500:]}")
    # 至少一行 WARN: 标记 (ASR/TTS/VAD/KWS 中任一)
    warn_lines = [ln for ln in out.splitlines() if ln.strip().startswith("WARN:")]
    _ok(len(warn_lines) >= 1,
        f"V5 expected ≥1 WARN: line in V1 stdout, found {len(warn_lines)}")
    # 至少一行 SKIP: 标记 (audio 因 COCO_CI=1)
    skip_lines = [ln for ln in out.splitlines() if ln.strip().startswith("SKIP:")]
    _ok(len(skip_lines) >= 1,
        f"V5 expected ≥1 SKIP: line in V1 stdout, found {len(skip_lines)}")
    return {"warn_line_count": len(warn_lines),
            "skip_line_count": len(skip_lines),
            "warn_sample": warn_lines[0] if warn_lines else None,
            "skip_sample": skip_lines[0] if skip_lines else None}


def main() -> int:
    smoke = _load_smoke()
    results: dict = {}
    failures: list[str] = []
    stdout_buf: dict = {}

    cases = [
        ("V0_static_gate", lambda: v0_static_gate(smoke)),
        ("V1_on_path_rc2", lambda: v1_on_path_rc2(stdout_buf)),
        ("V2_off_default_rc0", v2_off_default_rc0),
        ("V3_off_explicit_rc0", v3_off_explicit_rc0),
        ("V4_on_alias_rc2", v4_on_alias_rc2),
        ("V5_stdout_real_chain", lambda: v5_stdout_real_chain(stdout_buf)),
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
        "feature": "infra-024",
        "status": "PASS" if not failures else "FAIL",
        "results": results,
        "failures": failures,
    }
    out_path = EVIDENCE_DIR / "verify_summary.json"
    out_path.write_text(
        json.dumps(summary, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    # 顺手把 V1 stdout 留一份做 forensic
    if stdout_buf.get("v1"):
        (EVIDENCE_DIR / "v1_subprocess_stdout.txt").write_text(
            stdout_buf["v1"], encoding="utf-8"
        )
    print(f"\nwrote {out_path}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
