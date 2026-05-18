"""interact-032 verification: verify_interact_023 V3 函数名 rename meta lock.

承接 interact-023 Reviewer caveat (interact-023-backlog-v3-rename):
scripts/verify_interact_023.py V3 函数名 v3_type_strict_and_monotonic 与
实际断言略歧义 (仅 latency_ms 非负 + type-strict bool 排除, 不跨 call
monotonic)。本 feature rename 为 v3_type_strict_and_nonneg, docstring 明确
monotonic 仅作 emit 顺序契约 (V2 负责), V3 不承担跨 call monotonic 断言。

子项::

V0 file_existence: scripts/verify_interact_023.py + verify_interact_032.py 存在。

V1 new_name_present: verify_interact_023.py 含新名 v3_type_strict_and_nonneg
   的关键锚点 (def / _record label / 调用点 / docstring 短语) 出现次数下限锁。

V2 old_name_absent: verify_interact_023.py 不含旧名 v3_type_strict_and_monotonic
   (0 残留, 不包括描述"V3 不承担 monotonic"这类否定语义中的 monotonic 字面量)。

V3 subprocess_v023: 子进程跑 verify_interact_023.py rc=0, 且 verify_summary.json
   含 V3_type_strict_and_nonneg ok=True (锁新名 V3 子项实际跑通)。

V4 zero_business_diff: coco/ 业务源码相对 main 基线 0 改动 (interact-032 是
   verify-only feature, 仅碰 scripts/verify_interact_023.py + 新增
   scripts/verify_interact_032.py)。git diff main -- coco/ 应为空。

V5 summary: 汇总 + evidence/interact-032/verify_summary.json。

retval: 0 全 PASS; 1 任一失败
evidence: evidence/interact-032/verify_summary.json

吸收 phase-25/26 finding: 字面量集合 + 出现次数下限双层 / 不锁行号 / 不用
sha256 自洽 (V4 改 git diff 直查 0 行业务源码改动)。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent

RESULTS: Dict[str, Dict[str, Any]] = {}


def _record(name: str, ok: bool, **detail: Any) -> None:
    RESULTS[name] = {"ok": ok, **detail}
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}: {detail}")


# 新名锚点 (verify_interact_023.py 中 rename 后应出现的字面量 + 下限)
# def 1 次 + 调用 1 次 + _record label 4 次 (3 个 False + 1 个 True 分支)
# = 6 处函数名级别命中; 加上 docstring/section header/注释 "nonneg" 字样
NEW_NAME_FUNC = "v3_type_strict_and_nonneg"
NEW_NAME_LABEL = "V3_type_strict_and_nonneg"

NEW_NAME_MIN_COUNTS = {
    NEW_NAME_FUNC: 2,        # def + 调用 (至少 2 次)
    NEW_NAME_LABEL: 4,       # 4 个 _record 调用
    "nonneg": 5,             # def + label + docstring/section/comment 综合 (宽松下限)
}

# docstring 关键短语 (V3 语义说明)
DOCSTRING_ANCHORS = [
    "V3: 仅断言 cooldown_hit latency_ms 为 type-strict 数值",
    "monotonic 跨 call 不承担",
    "V3 不做跨 call monotonic 断言",
]

# 旧名 (0 残留)
OLD_NAME_FUNC = "v3_type_strict_and_monotonic"
OLD_NAME_LABEL = "V3_type_strict_and_monotonic"


def v0_file_existence() -> None:
    v023 = ROOT / "scripts" / "verify_interact_023.py"
    v032 = ROOT / "scripts" / "verify_interact_032.py"
    missing = [str(p.relative_to(ROOT)) for p in (v023, v032) if not p.exists()]
    _record(
        "V0_file_existence",
        ok=(not missing),
        missing=missing,
        v023_size=v023.stat().st_size if v023.exists() else 0,
        v032_size=v032.stat().st_size if v032.exists() else 0,
    )


def v1_new_name_present() -> None:
    """V1: verify_interact_023.py 含新名锚点 + 出现次数下限."""
    v023 = ROOT / "scripts" / "verify_interact_023.py"
    text = v023.read_text(encoding="utf-8")

    counts: Dict[str, int] = {}
    bad_counts: List[str] = []
    for anchor, lower in NEW_NAME_MIN_COUNTS.items():
        c = text.count(anchor)
        counts[anchor] = c
        if c < lower:
            bad_counts.append(f"{anchor}: {c} < {lower}")

    missing_docstring = [a for a in DOCSTRING_ANCHORS if a not in text]

    ok = (not bad_counts) and (not missing_docstring)
    _record(
        "V1_new_name_present",
        ok=ok,
        counts=counts,
        bad_counts=bad_counts,
        missing_docstring=missing_docstring,
        docstring_anchors_total=len(DOCSTRING_ANCHORS),
    )


def v2_old_name_absent() -> None:
    """V2: verify_interact_023.py 不含旧名 (0 残留)."""
    v023 = ROOT / "scripts" / "verify_interact_023.py"
    text = v023.read_text(encoding="utf-8")

    old_func_count = text.count(OLD_NAME_FUNC)
    old_label_count = text.count(OLD_NAME_LABEL)

    ok = (old_func_count == 0) and (old_label_count == 0)
    _record(
        "V2_old_name_absent",
        ok=ok,
        old_func_count=old_func_count,
        old_label_count=old_label_count,
        old_func=OLD_NAME_FUNC,
        old_label=OLD_NAME_LABEL,
    )


def v3_subprocess_v023() -> None:
    """V3: 子进程跑 verify_interact_023.py rc=0 + V3 子项 ok=True (新名)."""
    v023 = ROOT / "scripts" / "verify_interact_023.py"
    proc = subprocess.run(
        [sys.executable, str(v023)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=180,
    )
    rc = proc.returncode

    # 读 evidence/interact-023/verify_summary.json
    summary_path = ROOT / "evidence" / "interact-023" / "verify_summary.json"
    v3_ok = False
    v3_detail: Dict[str, Any] = {}
    parse_err = ""
    if summary_path.exists():
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            results = data.get("results", [])
            # verify_interact_023 的 results 形如 list[{name, ok, detail}]
            if isinstance(results, list):
                for entry in results:
                    if entry.get("name") == NEW_NAME_LABEL:
                        v3_ok = bool(entry.get("ok"))
                        v3_detail = dict(entry)
                        break
            elif isinstance(results, dict):  # 兼容 dict 形态
                v3 = results.get(NEW_NAME_LABEL, {})
                v3_ok = bool(v3.get("ok"))
                v3_detail = dict(v3)
        except Exception as e:  # noqa: BLE001
            parse_err = repr(e)

    ok = (rc == 0) and v3_ok
    _record(
        "V3_subprocess_v023",
        ok=ok,
        rc=rc,
        v3_label=NEW_NAME_LABEL,
        v3_ok=v3_ok,
        v3_detail=v3_detail,
        parse_err=parse_err,
        stderr_tail=proc.stderr[-200:] if proc.stderr else "",
    )


def v4_zero_business_diff() -> None:
    """V4: coco/ 业务源码 0 改动 (interact-032 verify-only).

    git diff main -- coco/ 应为空; 本 feature 仅碰 scripts/verify_interact_023.py
    (rename) + 新增 scripts/verify_interact_032.py + feature_list.json + evidence/.
    """
    try:
        proc = subprocess.run(
            ["git", "diff", "main", "--", "coco/"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=30,
        )
        if proc.returncode != 0:
            _record("V4_zero_business_diff", False,
                    reason="git diff failed",
                    rc=proc.returncode,
                    stderr_tail=proc.stderr[-200:])
            return
        diff_out = proc.stdout
        diff_lines = len(diff_out.splitlines())
        ok = (diff_lines == 0)
        _record(
            "V4_zero_business_diff",
            ok=ok,
            diff_lines=diff_lines,
            baseline="main",
            scope="coco/",
            diff_head=diff_out[:200] if diff_out else "",
        )
    except Exception as e:  # noqa: BLE001
        _record("V4_zero_business_diff", False, reason=f"exception: {e!r}")


def v5_summary() -> None:
    all_ok = all(r["ok"] for r in RESULTS.values())
    _record("V5_summary", ok=all_ok,
            total=len(RESULTS),
            pass_count=sum(1 for r in RESULTS.values() if r["ok"]))


def main() -> int:
    v0_file_existence()
    v1_new_name_present()
    v2_old_name_absent()
    v3_subprocess_v023()
    v4_zero_business_diff()
    v5_summary()

    all_ok = all(r["ok"] for r in RESULTS.values())
    summary = {
        "feature": "interact-032",
        "all_pass": all_ok,
        "results": RESULTS,
    }
    out_dir = ROOT / "evidence" / "interact-032"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n=== interact-032 verify summary ===")
    print(json.dumps({"feature": "interact-032", "all_pass": all_ok}, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
