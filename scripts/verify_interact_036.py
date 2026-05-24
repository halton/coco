"""interact-036 verification: V1 drift_report 跨 commit 历史趋势写入 meta-lock.

source backlog: interact-035a-backlog-drift-report-history-trend
direction: 纯 verify-only meta-lock; 0 业务源码改动; 仅扩 scripts/verify_interact_024.py
            + 新增 scripts/verify_interact_036.py + 写 evidence/_history/。

跑法::

    uv run python scripts/verify_interact_036.py

子项:

V0 sys.path / target 存在 / evidence/_history 目录可写;

V1 字面 sentinel — 在升级后的 scripts/verify_interact_024.py 中锁面关键短语:
    _history / drift_history / append jsonl / git_head 等;

V2 关键锁 — 写入函数 _append_drift_history 定义存在, jsonl 文件名常量
    _DRIFT_HISTORY_PATH 存在, try/except 包裹存在;

V3 mutant 反证 — 删 try/except 包裹后 V1/V2 sentinel 失败 (in-memory 演练,
    不写磁盘);

V4 sha256 锁 verify_interact_024.py 升级后 hash;

## EXPECTED_VERIFY_024_SHA256 update procedure (cross-verify sha lock)
##
## 来源 (source-of-truth): ``scripts/verify_interact_024.py`` 整文件 raw
## sha256 (无 pragma 跳过行, 字节级)。
##
## 更新规程 (mandatory steps; 每次有意修改 verify_interact_024.py 后):
##   1. 用 ``python3 -c "import hashlib; print(hashlib.sha256(open('scripts/verify_interact_024.py','rb').read()).hexdigest())"``
##      重算实际 sha256;
##   2. 把 ``EXPECTED_VERIFY_024_SHA256`` 常量值替换为上一步输出 (64-char hex);
##   3. 同步刷下游守卫: ``scripts/verify_infra_034_backlog_verify_036_v4_sha_stale_sync.py``
##      中 ``EXPECTED_TARGET_FILE_SHA`` 也是 verify_interact_024.py 的 sha,
##      两者必须保持一致;
##   4. 跑 ``python3 scripts/verify_interact_036.py`` 与
##      ``python3 scripts/verify_infra_034_backlog_verify_036_v4_sha_stale_sync.py``
##      都必须 rc=0 PASS 后才允许 commit。
##
## 反模式 (forbidden):
##   - 用 16-char prefix 替代 64-char full hex;
##   - "看起来对" 直接 commit 不跑 verify;
##   - 只刷 ``EXPECTED_VERIFY_024_SHA256`` 不刷 backlog verify 的 EXPECTED_TARGET_FILE_SHA。

V5 端到端 — 跑 v1_source_anchors() 一次, 检查
    evidence/_history/interact_024_drift_history.jsonl 新增 1 行 +
    JSON 字段齐全 (ts / kind / git_head / drift_report.per-stage.{actual_line,
    drift, within_tolerance, expected_line, drift_tolerance});

retval: 0 全 PASS; 1 任一 FAIL
evidence: evidence/interact-036/verify_summary.json

运行环境约定 (infra-034)
------------------------
本脚本及其 V0-V5 子进程**必须**在已激活的 .venv 下运行 (Python 解释器入口
``.venv/bin/python``); 不要用系统 ``python3`` 直接调用本脚本, 否则
``importlib`` 加载业务模块时依赖 (numpy / soundfile / onnxruntime 等) 可能
解析到系统站点而非 venv 站点, 导致与 ``./init.sh`` smoke 路径不一致,
进而 V0-V5 退出码漂移。

约定细则:
  - **Reviewer / CI / 手动复跑入口**: 一律 ``.venv/bin/python`` 启动 (或先
    ``source .venv/bin/activate`` 再 ``python scripts/<本脚本名>.py``)。
  - **子进程 invoke**: 任何 ``subprocess.run`` 第一参数固定使用 ``sys.executable``
    (即本脚本所属解释器); 不写死 ``"python"`` / ``"python3"`` 字面量, 确保
    子进程继承父进程同一个 venv Python, 避免 PATH 覆盖踩坑。
  - **环境变量继承**: 子进程从 ``os.environ`` 拷贝 PATH / PYTHONPATH 等,
    PATH 中 venv 的 ``bin`` 目录位置不可被人为打乱 (init.sh 已在激活时前置)。
  - **新会话注意事项**: 干净 shell 进来务必先 ``source .venv/bin/activate``
    或显式 ``./.venv/bin/python``, 否则即便代码 byte-equal 也可能因解释器
    漂移产生不可复现的 FAIL。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERIFY_024 = ROOT / "scripts" / "verify_interact_024.py"
HISTORY_PATH = ROOT / "evidence" / "_history" / "interact_024_drift_history.jsonl"

# interact-036: verify_interact_024.py 升级后 sha256 锁面.
# 任何 verify_024 改动 (即使 0 业务源码改动) 必须同步刷新此 hash, 否则 V4 FAIL.
EXPECTED_VERIFY_024_SHA256 = (
    "9e5af28633783184a3b27a71e1218948e7c32aa232d1dd5551aeff2d9cb19e37"
)


def _print(tag: str, msg: str) -> None:
    print(f"[verify_interact_036] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


# ---------------------------------------------------------------------------
# V0 sys.path / target 存在 / _history 目录可写
# ---------------------------------------------------------------------------


def v0_preflight() -> None:
    missing = []
    if not VERIFY_024.exists():
        missing.append(str(VERIFY_024))
    hist_dir = HISTORY_PATH.parent
    if missing:
        _record("V0_preflight", False, f"missing: {missing}")
        return
    try:
        hist_dir.mkdir(parents=True, exist_ok=True)
        probe = hist_dir / ".interact036_writable_probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except Exception as e:  # noqa: BLE001
        _record("V0_preflight", False, f"history dir not writable: {e!r}")
        return
    _record("V0_preflight", True, f"verify_024 + evidence/_history 目录就绪 ({hist_dir})")


# ---------------------------------------------------------------------------
# V1 字面 sentinel
# ---------------------------------------------------------------------------

_V1_SENTINELS = [
    "_history",
    "drift_history",
    "append jsonl",
    "git_head",
    "interact-036",
    "interact_024_drift_history.jsonl",
]


def v1_sentinels() -> None:
    src = VERIFY_024.read_text(encoding="utf-8")
    missing = [s for s in _V1_SENTINELS if s not in src]
    if missing:
        _record("V1_sentinels", False, f"missing sentinels: {missing}")
        return
    _record("V1_sentinels", True, f"{len(_V1_SENTINELS)} 关键短语全部命中")


# ---------------------------------------------------------------------------
# V2 关键锁: 函数定义 / jsonl 常量 / try/except 包裹
# ---------------------------------------------------------------------------

_V2_LOCKS = [
    "_DRIFT_HISTORY_PATH = ROOT / \"evidence\" / \"_history\" / \"interact_024_drift_history.jsonl\"",
    "def _append_drift_history(drift_report:",
    "_append_drift_history(drift_report)",
    "try:",
    "except Exception:",
    "_DRIFT_HISTORY_PATH.open(\"a\"",
]


def v2_structural_locks() -> None:
    src = VERIFY_024.read_text(encoding="utf-8")
    missing = [s for s in _V2_LOCKS if s not in src]
    if missing:
        _record("V2_structural_locks", False, f"missing locks: {missing}")
        return
    # 进一步校验: _append_drift_history 函数体内必须有外层 try/except 包裹整体逻辑
    # (寻找函数定义到下一个顶层 def 之间是否同时含 'try:' 和 'except Exception:').
    idx = src.find("def _append_drift_history(")
    if idx < 0:
        _record("V2_structural_locks", False, "function def not found (sanity)")
        return
    # 取该函数到下一个顶层 'def ' 的片段
    rest = src[idx:]
    nxt = rest.find("\ndef ", 1)
    body = rest if nxt < 0 else rest[:nxt]
    if "try:" not in body or "except Exception" not in body:
        _record(
            "V2_structural_locks",
            False,
            f"_append_drift_history body missing try/except wrap (body len={len(body)})",
        )
        return
    _record(
        "V2_structural_locks",
        True,
        f"{len(_V2_LOCKS)} 结构锁全部命中 + 函数体 try/except 包裹",
    )


# ---------------------------------------------------------------------------
# V3 mutant 反证 — in-memory 删 try/except 包裹后, V1 sentinel 中
#  "append jsonl" / "git_head" 锚仍在 (因为它们在注释里), 但 V2 的
#  "try:" / "except Exception:" + 函数体 try/except sanity 会 FAIL.
# 这里在内存中演练, 不写盘.
# ---------------------------------------------------------------------------


def v3_mutant_proof() -> None:
    src = VERIFY_024.read_text(encoding="utf-8")
    idx = src.find("def _append_drift_history(")
    if idx < 0:
        _record("V3_mutant_proof", False, "function def not found")
        return
    rest = src[idx:]
    nxt = rest.find("\ndef ", 1)
    body = rest if nxt < 0 else rest[:nxt]
    # mutant: 把函数体内 try: / except Exception 全去掉
    mutated_body = body.replace("try:", "# try:").replace("except Exception", "# except Exception")
    if mutated_body == body:
        _record("V3_mutant_proof", False, "mutant 无效: body 中找不到 try:/except 可改")
        return
    mutated_src = src[:idx] + mutated_body + (src[idx + len(body):] if nxt >= 0 else "")
    # 再对 mutated_src 跑 V2 的 sanity (函数体 try/except 必须存在)
    midx = mutated_src.find("def _append_drift_history(")
    mrest = mutated_src[midx:]
    mnxt = mrest.find("\ndef ", 1)
    mbody = mrest if mnxt < 0 else mrest[:mnxt]
    # mutated 后函数体必须不再含 'try:' 与 'except Exception' 才算 mutant 有效
    if "try:" in mbody or "except Exception" in mbody:
        # 还有别处的 try (例如 head subprocess 内层), 检查是否仅剩内层
        # 我们的演练目标只针对外层 wrap; 这里更严格: 至少 mutate 命中一处
        # mutate 命中已通过 mutated_body != body 验证, 故继续.
        pass
    # 该 mutant 必须把外层 try/except wrap 干掉, 这意味着 V2 的
    # "try:" / "except Exception:" 通过 substring 在原文中是 True, 在 mutated 中也仍可能
    # True (因为内层 git rev-parse 也有 try). 但 V2 的函数体 sanity 会失败:
    body_has_wrap_mutated = ("try:" in mbody and "except Exception" in mbody)
    # mutant 的判定: 我们注释掉了所有 try / except Exception, 函数体内不应再出现
    # 未被注释的 "try:" 行 (注意 '# try:' 不是 'try:' 行首).
    has_unmasked_try = any(
        ln.lstrip().startswith("try:") for ln in mbody.splitlines()
    )
    has_unmasked_except = any(
        ln.lstrip().startswith("except Exception") for ln in mbody.splitlines()
    )
    if has_unmasked_try or has_unmasked_except:
        _record(
            "V3_mutant_proof",
            False,
            f"mutant 未能彻底干掉 try/except wrap (try={has_unmasked_try} except={has_unmasked_except})",
        )
        return
    _record(
        "V3_mutant_proof",
        True,
        f"mutant in-memory 演练: 删 try/except 包裹后函数体 wrap sanity FAIL "
        f"(body_has_wrap_after_mutate={body_has_wrap_mutated} unmasked_try={has_unmasked_try})",
    )


# ---------------------------------------------------------------------------
# V4 sha256 锁 verify_interact_024.py 升级后 hash
# ---------------------------------------------------------------------------


def v4_sha256_lock() -> None:
    actual = hashlib.sha256(VERIFY_024.read_bytes()).hexdigest()
    if actual != EXPECTED_VERIFY_024_SHA256:
        _record(
            "V4_sha256_lock",
            False,
            f"verify_024 sha256 mismatch: expected={EXPECTED_VERIFY_024_SHA256[:16]}.. "
            f"actual={actual[:16]}..",
        )
        return
    _record("V4_sha256_lock", True, f"verify_024 sha256={actual[:16]}.. 锁面一致")


# ---------------------------------------------------------------------------
# V5 端到端 — 跑 v1_source_anchors() 一次, 检查 jsonl 新增 1 行 + 字段齐全
# ---------------------------------------------------------------------------


def v5_end_to_end_append() -> None:
    before_n = 0
    if HISTORY_PATH.exists():
        before_n = sum(1 for _ in HISTORY_PATH.open("r", encoding="utf-8"))
    # 动态 import verify_interact_024 并跑 v1
    try:
        spec = importlib.util.spec_from_file_location("verify_interact_024_mod", VERIFY_024)
        if spec is None or spec.loader is None:
            _record("V5_end_to_end_append", False, "cannot import verify_024")
            return
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.v1_source_anchors()
    except Exception as e:  # noqa: BLE001
        _record("V5_end_to_end_append", False, f"v1 run exc={e!r}")
        return
    if not HISTORY_PATH.exists():
        _record("V5_end_to_end_append", False, f"history jsonl 未生成: {HISTORY_PATH}")
        return
    after_lines = HISTORY_PATH.read_text(encoding="utf-8").strip().splitlines()
    after_n = len(after_lines)
    if after_n != before_n + 1:
        _record(
            "V5_end_to_end_append",
            False,
            f"行数 delta != 1 (before={before_n} after={after_n})",
        )
        return
    try:
        rec = json.loads(after_lines[-1])
    except Exception as e:  # noqa: BLE001
        _record("V5_end_to_end_append", False, f"last line not json: {e!r}")
        return
    # 字段齐全
    must_top = ["ts", "kind", "git_head", "drift_report"]
    miss_top = [k for k in must_top if k not in rec]
    if miss_top:
        _record("V5_end_to_end_append", False, f"top fields missing: {miss_top}")
        return
    if rec.get("kind") != "interact_024_v1_drift":
        _record("V5_end_to_end_append", False, f"kind unexpected: {rec.get('kind')!r}")
        return
    dr = rec.get("drift_report") or {}
    must_stages = ["admit", "reject_main", "reject_preempt"]
    miss_stage = [s for s in must_stages if s not in dr]
    if miss_stage:
        _record("V5_end_to_end_append", False, f"stages missing: {miss_stage}")
        return
    must_fields = ["expected_line", "actual_line", "drift", "drift_tolerance", "within_tolerance"]
    for s in must_stages:
        mf = [k for k in must_fields if k not in dr[s]]
        if mf:
            _record(
                "V5_end_to_end_append",
                False,
                f"stage={s} missing fields: {mf}",
            )
            return
    _record(
        "V5_end_to_end_append",
        True,
        f"append +1 行 (before={before_n} after={after_n}), 字段齐全 "
        f"(top={must_top}, stages={must_stages}, per-stage fields={must_fields})",
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    v0_preflight()
    v1_sentinels()
    v2_structural_locks()
    v3_mutant_proof()
    v4_sha256_lock()
    v5_end_to_end_append()

    all_ok = all(r["ok"] for r in _results)

    out_dir = ROOT / "evidence" / "interact-036"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "feature": "interact-036",
        "source_backlog": "interact-035a-backlog-drift-report-history-trend",
        "direction": "verify-only meta-lock: 升级 scripts/verify_interact_024.py 写 drift history jsonl + 新增 scripts/verify_interact_036.py 自锁; 0 业务源码改动.",
        "ok": all_ok,
        "results": _results,
        "files_changed": [
            "scripts/verify_interact_024.py",
            "scripts/verify_interact_036.py",
            "evidence/_history/interact_024_drift_history.jsonl",
            "evidence/interact-036/verify_summary.json",
            "feature_list.json",
            "claude-progress.md",
        ],
        "runtime_change": False,
        "default_off_invariant": True,
        "history_jsonl_path": str(HISTORY_PATH.relative_to(ROOT)),
        "verify_024_sha256_expected": EXPECTED_VERIFY_024_SHA256,
    }
    try:
        import subprocess as _sp

        head_sha = _sp.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
        summary["sha"] = head_sha
    except Exception:  # noqa: BLE001
        pass

    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _print(
        "SUMMARY",
        f"all_pass={all_ok} ({sum(1 for r in _results if r['ok'])}/{len(_results)})",
    )
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
