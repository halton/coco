#!/usr/bin/env python3
"""infra-018 verify: smoke exit-code 细分 + CI history-summary upload artifact.

Acceptance (feature_list.json infra-018)
----------------------------------------
V1 init.sh smoke 3 子检查全 PASS → rc=0; 故意 FAIL → rc=1; WARN-only → rc=2;
   SKIP-only → rc=3 (后两者仅在 COCO_SMOKE_FINEGRAINED_EXIT=1 时生效)。
V2 .github/workflows/verify-matrix.yml 含 history-summary upload-artifact step,
   `if: always()`, retention-days=14, path 包含 `evidence/_history/**` 与
   `evidence/**/verify_summary.json`。
V3 default-OFF env COCO_SMOKE_FINEGRAINED_EXIT=1 启用 rc=2/3 细分; 未设置时
   rc 退化为 0/1, 与改造前行为完全一致。
V4 verify_infra_018.py 覆盖 4 种 rc + CI artifact dry-run (静态 yaml 解析)。
V5 actionlint --version >= 1.7 校验 workflow yaml lint 通过 (若 actionlint 存在;
   缺失则 SKIP, 不阻断)。

运行期零影响: smoke 主路径未改, 仅在 main() 末尾按 areas 状态决定 rc。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
EVIDENCE_DIR = REPO_ROOT / "evidence" / "infra-018"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "verify-matrix.yml"


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _import_smoke():
    """动态 import scripts/smoke.py (脚本目录非 package)。每次 reload 让 monkeypatch 干净。"""
    sys.path.insert(0, str(SCRIPTS_DIR))
    import importlib
    if "smoke" in sys.modules:
        del sys.modules["smoke"]
    return importlib.import_module("smoke")


def _run_main_with_overrides(*, overrides: dict, env: dict | None = None) -> tuple[int, dict]:
    """直接调 smoke.main() 并 monkeypatch 指定子检查; 返回 (rc, areas)。

    overrides: {sub_check_name: fake_callable}
    env: 临时环境变量（self-restore）。
    """
    import sys as _sys
    saved_env: dict[str, str | None] = {}
    if env:
        for k, v in env.items():
            saved_env[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    saved_argv = _sys.argv[:]
    _sys.argv = ["smoke"]
    captured_areas: dict[str, str] = {}
    try:
        smoke = _import_smoke()
        for fn_name, fake in overrides.items():
            setattr(smoke, fn_name, fake)
        orig_emit = smoke._emit_smoke_history

        def _spy(areas: dict[str, str], dur: float, *, failed: bool) -> None:
            captured_areas.update(areas)
            # 不调用真 emit, 避免污染 history jsonl
            return None

        smoke._emit_smoke_history = _spy
        try:
            rc = smoke.main()
        except SystemExit as e:  # main() 现在返回 int, 不抛
            rc = int(e.code) if e.code is not None else 0
        return rc, dict(captured_areas)
    finally:
        _sys.argv = saved_argv
        if env:
            for k, prev in saved_env.items():
                if prev is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = prev


# ---------- 假子检查 ---------------------------------------------------------

def _fake_pass(label: str):
    def _fn() -> None:
        print(f"==> Smoke: {label}\n  ok: fake pass")
    return _fn


def _fake_warn(label: str):
    def _fn() -> None:
        print(f"==> Smoke: {label}\n  WARN: fake warn, model missing")
    return _fn


def _fake_skip(label: str):
    def _fn() -> None:
        print(f"==> Smoke: {label}\n  SKIP: fake skip")
    return _fn


def _fake_fail(label: str):
    def _fn() -> None:
        print(f"==> Smoke: {label}")
        sys.exit(f"FAIL: fake fail in {label}")
    return _fn


# 所有 smoke 子检查名 → smoke 函数名 映射
ALL_CHECKS = [
    "smoke_audio", "smoke_asr", "smoke_tts", "smoke_vision",
    "smoke_companion_vision", "smoke_face_tracker", "smoke_vad",
    "smoke_wake_word", "smoke_power_state", "smoke_config", "smoke_publish",
]


def _all_pass_overrides() -> dict:
    return {name: _fake_pass(name) for name in ALL_CHECKS}


# ---------- V1 + V3: rc=0 / 1 / 2 / 3 -----------------------------------------

def v1_v3_exit_codes() -> dict:
    results: dict[str, dict] = {}

    # --- 全 PASS, OFF 模式 → rc=0
    over = _all_pass_overrides()
    rc, areas = _run_main_with_overrides(
        overrides=over,
        env={"COCO_SMOKE_FINEGRAINED_EXIT": None},
    )
    _ok(rc == 0, f"全 PASS OFF 模式期望 rc=0, got {rc} (areas={areas})")
    results["all_pass_off"] = {"rc": rc, "states": sorted(set(areas.values()))}

    # --- 全 PASS, ON 模式 → 仍 rc=0
    rc, areas = _run_main_with_overrides(
        overrides=_all_pass_overrides(),
        env={"COCO_SMOKE_FINEGRAINED_EXIT": "1"},
    )
    _ok(rc == 0, f"全 PASS ON 模式期望 rc=0, got {rc}")
    results["all_pass_on"] = {"rc": rc, "states": sorted(set(areas.values()))}

    # --- 一个 FAIL → rc=1 (两模式相同)
    over = _all_pass_overrides()
    over["smoke_publish"] = _fake_fail("publish")
    rc, areas = _run_main_with_overrides(
        overrides=over,
        env={"COCO_SMOKE_FINEGRAINED_EXIT": None},
    )
    _ok(rc == 1, f"FAIL OFF 模式期望 rc=1, got {rc}")
    _ok(areas.get("publish") == "FAIL", f"publish 应记 FAIL, got {areas.get('publish')}")
    results["one_fail_off"] = {"rc": rc, "publish": areas.get("publish")}

    over = _all_pass_overrides()
    over["smoke_vision"] = _fake_fail("vision")
    rc, areas = _run_main_with_overrides(
        overrides=over,
        env={"COCO_SMOKE_FINEGRAINED_EXIT": "1"},
    )
    _ok(rc == 1, f"FAIL ON 模式期望 rc=1, got {rc}")
    results["one_fail_on"] = {"rc": rc, "vision": areas.get("vision")}

    # --- WARN-only, ON 模式 → rc=2
    over = _all_pass_overrides()
    over["smoke_tts"] = _fake_warn("tts")
    rc, areas = _run_main_with_overrides(
        overrides=over,
        env={"COCO_SMOKE_FINEGRAINED_EXIT": "1"},
    )
    _ok(rc == 2, f"WARN-only ON 期望 rc=2, got {rc} (areas={areas})")
    _ok(areas.get("tts") == "WARN", f"tts 应 WARN, got {areas.get('tts')}")
    results["warn_only_on"] = {"rc": rc, "tts": areas.get("tts")}

    # --- WARN-only, OFF 模式 → 仍 rc=0 (向后兼容)
    over = _all_pass_overrides()
    over["smoke_tts"] = _fake_warn("tts")
    rc, areas = _run_main_with_overrides(
        overrides=over,
        env={"COCO_SMOKE_FINEGRAINED_EXIT": None},
    )
    _ok(rc == 0, f"WARN-only OFF 期望 rc=0 (兼容), got {rc}")
    results["warn_only_off"] = {"rc": rc}

    # --- SKIP-only, ON 模式 → rc=3
    over = _all_pass_overrides()
    over["smoke_audio"] = _fake_skip("audio")
    rc, areas = _run_main_with_overrides(
        overrides=over,
        env={"COCO_SMOKE_FINEGRAINED_EXIT": "1"},
    )
    _ok(rc == 3, f"SKIP-only ON 期望 rc=3, got {rc} (areas={areas})")
    _ok(areas.get("audio") == "SKIP", f"audio 应 SKIP, got {areas.get('audio')}")
    results["skip_only_on"] = {"rc": rc, "audio": areas.get("audio")}

    # --- SKIP-only, OFF 模式 → rc=0 (兼容)
    over = _all_pass_overrides()
    over["smoke_audio"] = _fake_skip("audio")
    rc, areas = _run_main_with_overrides(
        overrides=over,
        env={"COCO_SMOKE_FINEGRAINED_EXIT": None},
    )
    _ok(rc == 0, f"SKIP-only OFF 期望 rc=0, got {rc}")
    results["skip_only_off"] = {"rc": rc}

    # --- WARN + SKIP, ON 模式 → WARN 优先 → rc=2
    over = _all_pass_overrides()
    over["smoke_audio"] = _fake_skip("audio")
    over["smoke_tts"] = _fake_warn("tts")
    rc, _ = _run_main_with_overrides(
        overrides=over,
        env={"COCO_SMOKE_FINEGRAINED_EXIT": "1"},
    )
    _ok(rc == 2, f"WARN+SKIP ON 期望 rc=2 (WARN 优先), got {rc}")
    results["warn_plus_skip_on"] = {"rc": rc}

    # --- FAIL + WARN + SKIP, ON 模式 → FAIL 优先 → rc=1
    over = _all_pass_overrides()
    over["smoke_audio"] = _fake_skip("audio")
    over["smoke_tts"] = _fake_warn("tts")
    over["smoke_vision"] = _fake_fail("vision")
    rc, _ = _run_main_with_overrides(
        overrides=over,
        env={"COCO_SMOKE_FINEGRAINED_EXIT": "1"},
    )
    _ok(rc == 1, f"FAIL+WARN+SKIP ON 期望 rc=1, got {rc}")
    results["fail_dominates_on"] = {"rc": rc}

    return results


# ---------- V2: workflow yaml 含 history-summary upload step ----------------

def v2_workflow_artifact_step() -> dict:
    _ok(WORKFLOW_PATH.exists(), f"workflow 缺失: {WORKFLOW_PATH}")
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    # 必须包含 history-summary upload step 的几个关键标识
    needles = [
        "history-summary-",                # artifact 名前缀 (带 run_id)
        "evidence/_history/**",            # path 包含 history jsonl
        "evidence/**/verify_summary.json", # path 包含 evidence verify summary
        "retention-days: 14",              # retention 锁定 14 天
        "actions/upload-artifact@v4",      # 仍用 v4 action
    ]
    missing = [n for n in needles if n not in text]
    _ok(not missing, f"workflow 缺关键字段: {missing}")

    # 必须在 smoke job 里 (不是 lint / changes); 用 anchor "Run smoke (COCO_CI=1)"
    smoke_idx = text.find("Run smoke (COCO_CI=1)")
    history_idx = text.find("history-summary-")
    _ok(smoke_idx > 0 and history_idx > smoke_idx,
        f"history-summary step 应在 smoke job 内且位于 Run smoke 之后 (smoke@{smoke_idx} history@{history_idx})")

    # if: always() 必须在 history step 附近 (取 history_idx 之前的 ~500 字符内)
    window = text[max(0, history_idx - 500):history_idx + 200]
    _ok("if: always()" in window, "history-summary step 附近应有 if: always()")

    return {
        "workflow": str(WORKFLOW_PATH.relative_to(REPO_ROOT)),
        "needles_ok": True,
        "smoke_position_ok": True,
    }


# ---------- V4: 老 caller `rc != 0 → FAIL` 仍正确 ----------------------------

def v4_legacy_caller_compat() -> dict:
    """模拟老 caller: 只看 rc==0 / rc!=0; OFF 模式下任何 PASS/WARN/SKIP 组合都该 rc=0。"""
    cases: list[tuple[str, dict, int]] = [
        ("all_pass", _all_pass_overrides(), 0),
    ]

    # WARN-only OFF → rc=0
    w = _all_pass_overrides(); w["smoke_tts"] = _fake_warn("tts")
    cases.append(("warn_off", w, 0))

    # SKIP-only OFF → rc=0
    s = _all_pass_overrides(); s["smoke_audio"] = _fake_skip("audio")
    cases.append(("skip_off", s, 0))

    # FAIL OFF → rc=1
    f = _all_pass_overrides(); f["smoke_publish"] = _fake_fail("publish")
    cases.append(("fail_off", f, 1))

    out: dict = {}
    for label, over, want_rc in cases:
        rc, _ = _run_main_with_overrides(
            overrides=over,
            env={"COCO_SMOKE_FINEGRAINED_EXIT": None},
        )
        _ok(rc == want_rc, f"[{label}] 期望 rc={want_rc}, got {rc}")
        # 老语义二元: rc==0 OK / rc!=0 FAIL
        legacy_fail = rc != 0
        out[label] = {"rc": rc, "legacy_fail": legacy_fail}
    return out


# ---------- V5: actionlint workflow yaml ------------------------------------

def v5_actionlint() -> dict:
    al = shutil.which("actionlint")
    if not al:
        return {"status": "SKIP", "reason": "actionlint not installed"}
    proc = subprocess.run(
        [al, str(WORKFLOW_PATH)],
        capture_output=True, text=True,
    )
    _ok(proc.returncode == 0,
        f"actionlint FAIL rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}")
    return {"status": "PASS", "rc": 0}


# ---------- main -------------------------------------------------------------

def _git_head_short() -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            text=True,
        ).strip()
        return out
    except Exception:
        return "unknown"


def main() -> int:
    summary: dict = {
        "feature": "infra-018",
        "git_head": _git_head_short(),
        "results": {},
    }
    try:
        summary["results"]["V1_V3_exit_codes"] = v1_v3_exit_codes()
        print("V1+V3 PASS: rc=0/1/2/3 + default-OFF 兼容")

        summary["results"]["V2_workflow_artifact"] = v2_workflow_artifact_step()
        print("V2 PASS: workflow history-summary upload step OK")

        summary["results"]["V4_legacy_compat"] = v4_legacy_caller_compat()
        print("V4 PASS: 老 caller rc!=0 语义保持")

        v5 = v5_actionlint()
        summary["results"]["V5_actionlint"] = v5
        print(f"V5 {v5['status']}: actionlint {v5.get('reason', 'OK')}")
    except AssertionError as e:
        summary["status"] = "FAIL"
        summary["error"] = str(e)
        print(f"verify_infra_018 FAIL: {e}", file=sys.stderr)
        (EVIDENCE_DIR / "verify_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 1

    summary["status"] = "PASS"
    (EVIDENCE_DIR / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"verify_infra_018 PASS → {EVIDENCE_DIR / 'verify_summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
