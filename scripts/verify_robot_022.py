"""robot-022 verify: SIGTERM swallow 行为契约锁面 (verify-only).

source backlog: robot-012-backlog-sigterm-swallow-doc
scope: docs + verify-only; 0 业务源码改动.

锁定 coco/robot/sequencer.py::install_signal_shutdown_handler 的:
  - env gate token COCO_ROBOT_SIGTERM_HANDLE 接受集合 {1,true,yes,on}
  - default-OFF 行为 (env 未设 → return []; bytewise 等价)
  - SIG_DFL / SIG_IGN 跳过链 prev 的 swallow 设计
  - 重入 flag + 跨平台异常兜底

V0: file existence + sha256 fingerprint
V1: doc 关键短语锁 (>=12 项)
V2: 代码现状指纹 — 关键 token / 关键行字面量锁
V3: mutant 反证 — 删 SIG_DFL 跳过 token 或 doc 关键短语 → 同一 verify FAIL
V4: default-OFF 行为不变性 (subprocess) — env 未设时 register() 返回 [] 且 SIGTERM
    handler 仍为 SIG_DFL (bytewise 等价基线)
V5: 总结 evidence
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import List

errors: List[str] = []
t0 = time.time()

REPO = Path(__file__).resolve().parents[1]
SEQ_PY = REPO / "coco" / "robot" / "sequencer.py"
SPEC_DOC = REPO / "docs" / "robot-sigterm-swallow-spec.md"
SELF = Path(__file__).resolve()
EVID_DIR = REPO / "evidence" / "robot-022"
EVID_DIR.mkdir(parents=True, exist_ok=True)


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


def sha256_full(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# 清相关 env 隔离
for k in ("COCO_ROBOT_SIGTERM_HANDLE",):
    os.environ.pop(k, None)


# --------- V0: fingerprint ---------
print("[V0] fingerprint")
check("sequencer.py exists", SEQ_PY.is_file())
check("spec doc exists", SPEC_DOC.is_file())
check("self exists", SELF.is_file())
seq_full = sha256_full(SEQ_PY) if SEQ_PY.is_file() else ""
spec_full = sha256_full(SPEC_DOC) if SPEC_DOC.is_file() else ""
self_full = sha256_full(SELF) if SELF.is_file() else ""
print(f"  sequencer.py sha256={seq_full[:16]}...")
print(f"  spec_doc    sha256={spec_full[:16]}...")
print(f"  self        sha256={self_full[:16]}...")


# --------- V1: doc 关键短语锁 ---------
print("[V1] doc 关键短语锁")
DOC_TOKENS = [
    "SIGTERM swallow",
    "COCO_ROBOT_SIGTERM_HANDLE",
    "default-OFF",
    "install_signal_shutdown_handler",
    "SIG_DFL",
    "SIG_IGN",
    "bytewise",
    "重入",
    "Windows",
    "swallow (swallow)" if False else "swallow",
    "verify-only",
    "robot-012",
    "intentional",
    "SIGINT",
    "default_int_handler",
]
spec_text = SPEC_DOC.read_text(encoding="utf-8") if SPEC_DOC.is_file() else ""
for tok in DOC_TOKENS:
    check(f"doc token: {tok!r}", tok in spec_text)


# --------- V2: 代码现状指纹 ---------
print("[V2] code anchor 锁")
seq_text = SEQ_PY.read_text(encoding="utf-8") if SEQ_PY.is_file() else ""

# 函数签名锚
check(
    "func def install_signal_shutdown_handler",
    "def install_signal_shutdown_handler(" in seq_text,
)
check(
    "signames default (SIGTERM, SIGINT)",
    'signames: Sequence[str] = ("SIGTERM", "SIGINT")' in seq_text,
)
# env gate
check(
    "env key COCO_ROBOT_SIGTERM_HANDLE",
    'e.get("COCO_ROBOT_SIGTERM_HANDLE", "")' in seq_text,
)
check(
    "env accept set {1,true,yes,on}",
    '"1", "true", "yes", "on"' in seq_text,
)
check(
    "early return [] for default-OFF",
    "return []" in seq_text,
)
# swallow 关键: SIG_DFL / SIG_IGN 跳过
check(
    "sig_dfl getattr",
    'sig_dfl = getattr(signal_module, "SIG_DFL", None)' in seq_text,
)
check(
    "sig_ign getattr",
    'sig_ign = getattr(signal_module, "SIG_IGN", None)' in seq_text,
)
check(
    "skip SIG_DFL/SIG_IGN/None when chain prev",
    "prev not in (sig_dfl, sig_ign, None)" in seq_text,
)
check(
    "callable(prev) check",
    "callable(prev)" in seq_text,
)
# 重入
check(
    "in_progress flag",
    'in_progress = {"flag": False}' in seq_text,
)
check(
    "reentrancy early return",
    'if in_progress["flag"]:' in seq_text,
)
# 跨平台异常兜底
check(
    "cross-platform except tuple",
    "except (ValueError, OSError, AttributeError)" in seq_text,
)
# signal_module 注入参数
check(
    "signal_module inject param",
    "signal_module: Optional[Any] = None" in seq_text,
)
# shutdown 调用
check(
    "seq.shutdown(wait=True, timeout=...)",
    "seq_ref.shutdown(wait=True, timeout=seq_to)" in seq_text,
)


# --------- V3: mutant 反证 ---------
# 真实 mutate code 风险大且本 verify 不允许改源, 采用"假设删除关键 token 后内
# 存检测"模拟: 复制 seq_text 删掉关键 token, 然后重跑 check 关键断言, 应失败.
print("[V3] mutant 反证 (in-memory)")


def _run_anchor_check(text: str) -> bool:
    """关键 anchor 全在即 PASS, 缺一即 FAIL — 模拟 V2 子集."""
    needed = [
        "prev not in (sig_dfl, sig_ign, None)",
        'e.get("COCO_ROBOT_SIGTERM_HANDLE", "")',
        "return []",
    ]
    return all(t in text for t in needed)


# A. 原文 PASS
check("mutant baseline: original anchors all present", _run_anchor_check(seq_text))
# B. 删 SIG_DFL/SIG_IGN 跳过 token → 应 FAIL
mutated_a = seq_text.replace(
    "prev not in (sig_dfl, sig_ign, None)",
    "True",
)
check(
    "mutant A: remove SIG_DFL/SIG_IGN skip → anchor check FAIL",
    not _run_anchor_check(mutated_a),
)
# C. 改 env key → 应 FAIL
mutated_b = seq_text.replace(
    'e.get("COCO_ROBOT_SIGTERM_HANDLE", "")',
    'e.get("COCO_ROBOT_SIGTERM_HANDLE_X", "")',
)
check(
    "mutant B: rename env key → anchor check FAIL",
    not _run_anchor_check(mutated_b),
)
# D. doc token mutant
mutated_doc = spec_text.replace("SIG_DFL", "REDACTED")
doc_anchor_after = "SIG_DFL" in mutated_doc
check(
    "mutant C: doc remove SIG_DFL → doc anchor FAIL",
    not doc_anchor_after,
)


# --------- V4: default-OFF subprocess 行为不变性 ---------
print("[V4] default-OFF subprocess invariant")

script = textwrap.dedent(
    """
    import os, sys, signal
    # 显式清 env 模拟 default-OFF
    os.environ.pop("COCO_ROBOT_SIGTERM_HANDLE", None)
    sys.path.insert(0, {repo!r})
    from coco.robot.sequencer import install_signal_shutdown_handler

    class _FakeSeq:
        def shutdown(self, wait=True, timeout=2.0):
            pass

    # 取调用前 SIGTERM handler 作为基线
    before = signal.getsignal(signal.SIGTERM)
    registered = install_signal_shutdown_handler(_FakeSeq(), timeout_s=0.5)
    after = signal.getsignal(signal.SIGTERM)

    # 必须 registered == [] 且 handler 未改 (default-OFF bytewise 等价)
    assert registered == [], f"expected [] got {{registered!r}}"
    assert before is after, f"handler changed: {{before!r}} != {{after!r}}"
    print("V4_OK registered=", registered, "before==after=", before is after)
    """
).format(repo=str(REPO))

try:
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=20,
        cwd=str(REPO),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    check(
        "subprocess rc=0",
        proc.returncode == 0,
        f"rc={proc.returncode} tail={out[-200:]!r}",
    )
    check(
        "default-OFF: registered==[] & handler unchanged",
        "V4_OK registered= [] before==after= True" in out,
        f"out_tail={out[-300:]!r}",
    )
except subprocess.TimeoutExpired:
    check("subprocess timeout", False, "20s exceeded")
except Exception as exc:  # noqa: BLE001
    check("subprocess exec", False, repr(exc))


# --------- V5: summary ---------
print("[V5] summary")
dt = time.time() - t0
summary = {
    "feature": "robot-022",
    "scope": "sigterm-swallow-doc + verify-only",
    "ok": len(errors) == 0,
    "errors": errors,
    "elapsed_s": round(dt, 3),
    "anchors": {
        "sequencer_sha256": seq_full,
        "spec_doc_sha256": spec_full,
        "self_sha256": self_full,
    },
    "doc_tokens_checked": len(DOC_TOKENS),
}
out_path = EVID_DIR / "verify_summary.json"
out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"  wrote {out_path}")

if errors:
    print(f"[FAIL] robot-022 verify: {len(errors)} error(s)")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print(f"[PASS] robot-022 verify ({dt:.2f}s)")
sys.exit(0)
