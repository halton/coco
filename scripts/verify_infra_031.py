"""infra-031 verification: doc §6 锁单向性提示 + verify_infra_030 V3 docstring 微对齐 meta-lock.

承接 infra-030 Reviewer findings (infra-030-backlog-doc-and-v3-polish):

  F1 — docs/syspath-injection-audit.md §6 末追加「锁单向性」段落：
       明示 V1 总数锁仅感知数量下降回归，不感知新增注入；新增 anti-pattern
       由 code review / Reviewer 兜底。

  F2 — scripts/verify_infra_030.py v3_mutant_detect docstring 与实现对齐：
       原 docstring 提到「反证：剔除任一已知核心锚点后总数 < 当前实测时锁不变」，
       但实现实际只做「新增 mutant -> 命中数 +1」基础敏感性证明；docstring 改为
       与实现一致并显式提示「不证 V1 锁拦截新增注入」。

本 feature 0 业务源码改动；仅文档化 + docstring polish + 本 meta-lock。

子项::

V0 sys.path / 文件存在: scripts/verify_infra_030.py + verify_infra_031.py +
   docs/syspath-injection-audit.md 存在。

V1 字面 sentinel: docs/syspath-injection-audit.md §6 含 F1 锁单向性
   关键字面（"锁单向性"、"infra-031"、"不感知新增注入"、"code review"）；
   scripts/verify_infra_030.py V3 docstring 含 F2 关键字面（"+1"、
   "不证 V1_grep_total 锁能拦截新增注入"）。

V2 关键锁参数: verify_infra_030.py 中 INJECTION_TOTAL_MIN == 130；
   CORE_ANCHORS 仍含 verify_interact_018.py 与 proactive_trace_summary.py
   两条 anchor；v3_mutant_detect 函数体仍写 mutant 临时文件并清理。

V3 mutant 反证: docs §6 锁单向性段落不可被静默移除（"锁单向性"短语必须存在）；
   verify_infra_030.py V3 docstring 不可回退到旧字面（"剔除任一已知核心锚点"
   旧短语应不存在，证明 docstring 已升级）。

V4 sha256 锁: 锁住升级后的两份文件指纹（verify_infra_030.py 全文件 +
   docs/syspath-injection-audit.md 全文件），未来无意修改会被立即捕获
   （吸收 robot-027 / interact-033 verify-self-hash 模式）。

V5 端到端 subprocess: 子进程跑 verify_infra_030.py rc==0 且 stdout 含
   "overall: PASS"，证 F2 docstring 修改不影响 verify 行为（V0-V5 仍全 PASS）。

retval: 0 全 PASS; 1 任一失败
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERIFY_030 = ROOT / "scripts" / "verify_infra_030.py"
VERIFY_031 = ROOT / "scripts" / "verify_infra_031.py"
DOC_AUDIT = ROOT / "docs" / "syspath-injection-audit.md"

RESULTS: Dict[str, Dict[str, Any]] = {}


def _record(name: str, ok: bool, **detail: Any) -> None:
    RESULTS[name] = {"ok": ok, **detail}
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}: {detail}")


# ---------------------------------------------------------------------------
# V0 file_existence
# ---------------------------------------------------------------------------


def v0_file_existence() -> None:
    files = {
        "scripts/verify_infra_030.py": VERIFY_030,
        "scripts/verify_infra_031.py": VERIFY_031,
        "docs/syspath-injection-audit.md": DOC_AUDIT,
    }
    missing = [k for k, p in files.items() if not p.exists()]
    sizes = {k: (p.stat().st_size if p.exists() else 0) for k, p in files.items()}
    _record(
        "V0_file_existence",
        ok=(not missing),
        missing=missing,
        sizes=sizes,
        syspath_root=str(ROOT),
    )


# ---------------------------------------------------------------------------
# V1 literal_sentinel
# ---------------------------------------------------------------------------


F1_DOC_LITERALS = [
    "锁单向性",
    "infra-031",
    "不感知新增注入",
    "code review",
]

F2_DOCSTRING_LITERALS = [
    "grep 命中数应 +1",
    "**不**证 V1_grep_total 锁能拦截",  # docstring 升级关键短语
    "锁单向性说明",
]


def v1_literal_sentinel() -> None:
    doc_text = DOC_AUDIT.read_text(encoding="utf-8")
    v030_text = VERIFY_030.read_text(encoding="utf-8")
    miss_doc = [s for s in F1_DOC_LITERALS if s not in doc_text]
    miss_v030 = [s for s in F2_DOCSTRING_LITERALS if s not in v030_text]
    ok = not miss_doc and not miss_v030
    _record(
        "V1_literal_sentinel",
        ok=ok,
        missing_in_doc=miss_doc,
        missing_in_verify_030=miss_v030,
    )


# ---------------------------------------------------------------------------
# V2 关键锁参数
# ---------------------------------------------------------------------------


def v2_lock_params() -> None:
    v030_text = VERIFY_030.read_text(encoding="utf-8")
    checks = {
        "INJECTION_TOTAL_MIN == 130": "INJECTION_TOTAL_MIN = 130" in v030_text,
        "anchor verify_interact_018.py": '"scripts/verify_interact_018.py":' in v030_text,
        "anchor proactive_trace_summary.py": '"scripts/proactive_trace_summary.py":' in v030_text,
        "v3 writes _mutant_infra_030_tmp.py": "_mutant_infra_030_tmp.py" in v030_text,
        "v3 cleanup mutant.unlink": "mutant.unlink()" in v030_text,
    }
    failed = [k for k, v in checks.items() if not v]
    _record("V2_lock_params", ok=(not failed), failed=failed, checks=checks)


# ---------------------------------------------------------------------------
# V3 mutant 反证（关键短语必须存在 / 旧短语必须消失）
# ---------------------------------------------------------------------------


def v3_mutant_anti_anchors() -> None:
    doc_text = DOC_AUDIT.read_text(encoding="utf-8")
    v030_text = VERIFY_030.read_text(encoding="utf-8")
    # F1 锁单向性段落不能被静默删除
    f1_present = "锁单向性（infra-031 F1）" in doc_text
    # F2 docstring 升级后旧短语应消失
    obsolete_phrase = "剔除任一已知核心锚点后总数"
    f2_old_gone = obsolete_phrase not in v030_text
    # F2 docstring 新短语应存在
    f2_new_present = "grep 命中数应 +1" in v030_text
    ok = f1_present and f2_old_gone and f2_new_present
    _record(
        "V3_mutant_anti_anchors",
        ok=ok,
        f1_doc_paragraph_present=f1_present,
        f2_obsolete_phrase_removed=f2_old_gone,
        f2_new_phrase_present=f2_new_present,
    )


# ---------------------------------------------------------------------------
# V4 sha256 working-tree lock
# ---------------------------------------------------------------------------


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


EXPECTED_HASHES: Dict[str, str] = {
    "scripts/verify_infra_030.py": "8dd69ca753e74de2415b7a37c70111478fc77303c747132a97f28b5e6a1ee623",
    "docs/syspath-injection-audit.md": "0fcc35541d4432db1833ddd989a520dbd399e241149fa53e5e907a86239ba517",
}


def v4_sha256_lock() -> None:
    current = {
        "scripts/verify_infra_030.py": _sha256(VERIFY_030),
        "docs/syspath-injection-audit.md": _sha256(DOC_AUDIT),
    }
    if not EXPECTED_HASHES:
        # 首跑模式：打印 sha256 供后续锁定；不视为失败（fresh-launch 模式）
        _record(
            "V4_sha256_lock",
            ok=True,
            mode="fresh-launch",
            current_sha256=current,
            note="EXPECTED_HASHES 为空时仅打印，由 bump-helper 后续锁定",
        )
        return
    drift = {k: (current[k], EXPECTED_HASHES.get(k)) for k in current if current[k] != EXPECTED_HASHES.get(k)}
    _record(
        "V4_sha256_lock",
        ok=(not drift),
        drift=drift,
        current_sha256=current,
    )


# ---------------------------------------------------------------------------
# V5 端到端 subprocess invoke verify_infra_030.py
# ---------------------------------------------------------------------------


def v5_e2e_subprocess() -> None:
    try:
        r = subprocess.run(
            [sys.executable, str(VERIFY_030)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired as exc:
        _record("V5_e2e_subprocess", ok=False, error=f"timeout: {exc}")
        return
    rc_ok = r.returncode == 0
    overall_pass = "overall: PASS" in r.stdout
    # 抽 v3 PASS 行做佐证（F2 docstring 修改不影响 V3 行为）
    v3_pass = bool(re.search(r"PASS V3_mutant_detect:", r.stdout))
    ok = rc_ok and overall_pass and v3_pass
    _record(
        "V5_e2e_subprocess",
        ok=ok,
        rc=r.returncode,
        overall_pass=overall_pass,
        v3_pass_line_present=v3_pass,
        stdout_tail=r.stdout.strip().splitlines()[-3:] if r.stdout else [],
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    v0_file_existence()
    v1_literal_sentinel()
    v2_lock_params()
    v3_mutant_anti_anchors()
    v4_sha256_lock()
    v5_e2e_subprocess()
    overall_ok = all(r["ok"] for r in RESULTS.values())
    print(f"[verify_infra_031] overall: {'PASS' if overall_ok else 'FAIL'}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
