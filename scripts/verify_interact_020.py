"""interact-020 verification: trace status contract 白名单 token 文档化.

承接 interact-019 closeout backlog (interact-019-backlog-trace-status-contract-doc):
interact-019 把 _is_fail 对 status 的匹配从 substring 改成白名单 token 精确匹配后,
留下 doc caveat — 需要把白名单 token 集合 / case-insensitive / 不允许复合写法等
contract 显式登记到文档与代码 docstring, 让外部接入方知晓。

interact-020 文档化产物:
- research/proactive_trace_contract.md  (单源真理, contract 全文)
- coco/proactive_trace.py 内 STATUS_FAIL_TOKENS 常量 docstring 加强

interact-020 为 **纯文档** feature, 运行时行为不变, V3 验证 is_fail token
匹配语义 bytewise 等价 interact-019。

跑法::

    uv run python scripts/verify_interact_020.py

子项：

V0 fingerprint: STATUS_FAIL_TOKENS frozenset 集合等于 {fail, failed, failure,
   error, errored}, 模块 docstring / 常量周边注释含 contract 关键词
   (contract 文档化锚点)。

V1 STATUS_FAIL_TOKENS 常量本身具备 docstring, 含 5 个 token 名 + 关键术语
   (white / token / case-insensitive / 三口推荐写法等)。

V2 STATUS_FAIL_TOKENS 实际等于 frozenset({"fail","failed","failure","error",
   "errored"}), 没有意外漂移。

V3 is_fail 行为不变 (与 interact-019 一致): status 5 个 token (含 case/strip)
   命中, "no_failure" / "failsafe" 不命中; 三口 (ok=False / error / failure_reason)
   仍正确。

V4 research/proactive_trace_contract.md 存在, 含白名单 5 token 全列举 + 禁止
   复合写法示例 + 推荐 ok=False 三口写法 + STATUS_FAIL_TOKENS 锚点。

V5 regression: scripts/verify_interact_019.py rc=0 (interact-019 全量 PASS)。

retval：0 全 PASS；1 任一失败
evidence 落 evidence/interact-020/verify_summary.json
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco import proactive_trace as pt  # noqa: E402


RESULTS: dict[str, dict] = {}


def _record(name: str, ok: bool, **detail) -> None:
    RESULTS[name] = {"ok": ok, **detail}
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}: {detail}")


def v0_fingerprint() -> None:
    tokens = set(pt.STATUS_FAIL_TOKENS)
    expected = {"fail", "failed", "failure", "error", "errored"}
    module_doc = (pt.__doc__ or "").lower()
    src = Path(pt.__file__).read_text(encoding="utf-8")
    src_lower = src.lower()
    # contract 关键词锚点：白名单 / token / case-insensitive / 三口
    anchors = ["white", "token", "case-insensitive", "ok=false", "failure_reason"]
    missing_anchors = [a for a in anchors if a not in src_lower]
    _record(
        "V0_fingerprint",
        ok=(tokens == expected and not missing_anchors),
        tokens=sorted(tokens),
        module_doc_has_proactive_trace="proactive.trace" in module_doc,
        missing_anchors=missing_anchors,
    )


def v1_status_tokens_const_docstring() -> None:
    # STATUS_FAIL_TOKENS 常量的 docstring 不能直接访问 (frozenset 不支持 __doc__);
    # 用源文件断言: 常量定义后紧跟的三引号 docstring 块需含关键词。
    src = Path(pt.__file__).read_text(encoding="utf-8")
    # 定位常量定义点
    idx = src.find("STATUS_FAIL_TOKENS = frozenset(")
    assert idx >= 0, "常量未找到"
    # 取常量定义后 800 字符窗口（覆盖紧随的 docstring + 上方注释块）
    near_const = src[max(0, idx - 1500): idx + 1200].lower()
    needed_tokens = ["fail", "failed", "failure", "error", "errored"]
    missing_tokens = [t for t in needed_tokens if t not in near_const]
    needed_terms = ["white", "token", "case-insensitive", "interact-019", "interact-020"]
    missing_terms = [t for t in needed_terms if t not in near_const]
    _record(
        "V1_const_docstring_terms",
        ok=(not missing_tokens and not missing_terms),
        missing_tokens=missing_tokens,
        missing_terms=missing_terms,
    )


def v2_tokens_set_equals() -> None:
    expected = frozenset({"fail", "failed", "failure", "error", "errored"})
    _record(
        "V2_tokens_set_equals",
        ok=(pt.STATUS_FAIL_TOKENS == expected),
        actual=sorted(pt.STATUS_FAIL_TOKENS),
        expected=sorted(expected),
    )


def v3_is_fail_behavior_unchanged() -> None:
    # 白名单 5 token (含 case/strip) 命中
    hits = [
        {"status": "fail"},
        {"status": "FAILED"},
        {"status": "  Error  "},
        {"status": "errored"},
        {"status": "Failure"},
    ]
    # 不命中: 历史 substring 误判候选 + 空/缺
    misses = [
        {"status": "no_failure"},
        {"status": "failsafe"},
        {"status": "no_fail_today"},
        {"status": "success"},
        {"status": ""},
        {},
        {"status": "RPC_FAILURE"},  # 复合写法不再命中（白名单 token 精确）
    ]
    # 三口主路径仍正确
    three_mouths_hit = [
        {"ok": False},
        {"error": "boom"},
        {"failure_reason": "quiet_state"},
    ]
    three_mouths_miss = [
        {"ok": True},
        {"ok": "success"},  # 字符串 truthy 不视为 fail (V2 三口)
        {"error": ""},
        {"failure_reason": "   "},
    ]
    bad = []
    for r in hits:
        if not pt.is_fail(r):
            bad.append(("hit-miss", r))
    for r in misses:
        if pt.is_fail(r):
            bad.append(("miss-hit", r))
    for r in three_mouths_hit:
        if not pt.is_fail(r):
            bad.append(("3mouth-miss", r))
    for r in three_mouths_miss:
        if pt.is_fail(r):
            bad.append(("3mouth-hit", r))
    _record("V3_is_fail_behavior_unchanged", ok=(not bad), bad=bad)


def v4_contract_doc_exists() -> None:
    doc_path = ROOT / "research" / "proactive_trace_contract.md"
    exists = doc_path.exists()
    text = doc_path.read_text(encoding="utf-8") if exists else ""
    lower = text.lower()
    needed_tokens = ["fail", "failed", "failure", "error", "errored"]
    missing_tokens = [t for t in needed_tokens if t not in lower]
    needed_terms = [
        "status_fail_tokens",
        "case-insensitive",
        "white",  # whitelist / white name
        "ok=false",
        "failure_reason",
        "rpc_failure",  # 禁止复合写法示例
        "task_failed_retry",
    ]
    missing_terms = [t for t in needed_terms if t not in lower]
    _record(
        "V4_contract_doc_exists",
        ok=(exists and not missing_tokens and not missing_terms),
        path=str(doc_path.relative_to(ROOT)),
        size=len(text),
        missing_tokens=missing_tokens,
        missing_terms=missing_terms,
    )


def v5_regression_interact_019() -> None:
    target = ROOT / "scripts" / "verify_interact_019.py"
    proc = subprocess.run(
        [sys.executable, str(target)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=120,
    )
    _record(
        "V5_regression_interact_019",
        ok=(proc.returncode == 0),
        rc=proc.returncode,
        stderr_tail=proc.stderr[-200:] if proc.stderr else "",
    )


def main() -> int:
    v0_fingerprint()
    v1_status_tokens_const_docstring()
    v2_tokens_set_equals()
    v3_is_fail_behavior_unchanged()
    v4_contract_doc_exists()
    v5_regression_interact_019()

    all_ok = all(r["ok"] for r in RESULTS.values())
    summary = {
        "feature": "interact-020",
        "all_pass": all_ok,
        "results": RESULTS,
    }
    out_dir = ROOT / "evidence" / "interact-020"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n=== interact-020 verify summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
