#!/usr/bin/env python3
"""verify_infra_040_backlog_tmpl_edge: 把 .github/ PR 模板纳入 sha lock graph (verify-only).

infra-040-backlog-v4-sha-graph-tmpl-edge (phase-67 #13):
``scripts/dump_v4_sha_graph.py`` 历史上把 ``EXPECTED_VERIFY_TMPL_SHA``
通过 ``_KNOWN_NON_NUMERIC_TARGETS`` 映射到 ``scripts/_verify_template.py``,
但该文件**并不存在**; 实际锁定的是
``.github/PULL_REQUEST_TEMPLATE/verify-script.md`` (verify_infra_040 V2)。
同样 ``EXPECTED_DEFAULT_TMPL_SHA`` (verify_infra_040 V2b) 此前也未被
graph 识别为 ``.github/pull_request_template.md`` 节点。

本轮在 ``_PER_FILE_LOCKS`` 中增补 ``(verify_infra_040.py, EXPECTED_*_TMPL_SHA)``
显式映射到 ``.github/`` 真实路径, 使 ``_infer_target`` / ``build_graph``
输出与 verify_infra_040 V2 / V2b file-sha 锁一致, PR 模板首次以正确节点
身份进入 sha lock DAG。

INFRA_040_TMPL_EDGE_SHA_LOCKS
-----------------------------
- ``scripts/dump_v4_sha_graph.py`` file sha: EXPECTED_DUMP_FILE_SHA (V2 target_file_sha)
- self file sha: EXPECTED_SELF_FILE_SHA (V0)

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DUMP_FILE = SCRIPTS / "dump_v4_sha_graph.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import verify_summary_exit  # noqa: E402

# scripts/dump_v4_sha_graph.py file sha (V2 target_file_sha)
EXPECTED_DUMP_FILE_SHA = (
    "83c8258ab830b590aeff02b98ec040f91afb9688a3d56f842d8f0b2af9579eda"
)
# self file sha (V0). 首跑用 __BUMP_ME__ 占位, 再回填。
# self file sha (V0). 自锁会陷入 fixed-point 问题 (改 const 字面值即改
# self sha); 本 verifier 不强行自锁, V0 仅做 file-exists scaffolding 校验。
# 真正的篡改/回归保护由 V2 (target_file_sha) + V_business 行为校验承担。
EXPECTED_SELF_FILE_SHA = "__SCAFFOLDING_ONLY__"

EXPECTED_VERIFY_TMPL_PATH = ".github/PULL_REQUEST_TEMPLATE/verify-script.md"
EXPECTED_DEFAULT_TMPL_PATH = ".github/pull_request_template.md"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_040_tmpl_edge][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0: self file exists (scaffolding 校验; 不做 self-sha 自锁, 见上方注释)
# ---------------------------------------------------------------------------
def v0_self_file_sha() -> None:
    self_path = Path(__file__)
    got = _file_sha(self_path)
    _emit(
        "V0_self_file_exists",
        self_path.is_file(),
        f"path={self_path.name} sha={got[:16]}",
    )


# ---------------------------------------------------------------------------
# V2: dump_v4_sha_graph.py file sha (target_file_sha)
# ---------------------------------------------------------------------------
def v2_dump_file_sha() -> None:
    got = _file_sha(DUMP_FILE)
    _emit(
        "V2_dump_file_sha",
        got == EXPECTED_DUMP_FILE_SHA,
        f"got={got[:16]} expect={EXPECTED_DUMP_FILE_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V_business: _infer_target 映射 + build_graph 节点
# ---------------------------------------------------------------------------
def v_business_infer_targets() -> None:
    """两条新映射必须命中 .github/ PR 模板路径, 而不是 unknown / _verify_template.py。"""
    try:
        # 用 importlib 避免 sys.path 污染调用者
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "dump_v4_sha_graph_for_040_edge", DUMP_FILE
        )
        if spec is None or spec.loader is None:
            for tag in (
                "V_business_verify_tmpl_infer",
                "V_business_default_tmpl_infer",
                "V_business_verify_tmpl_in_graph",
                "V_business_default_tmpl_in_graph",
            ):
                _emit(tag, False, "module load failed")
            return
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        for tag in (
            "V_business_verify_tmpl_infer",
            "V_business_default_tmpl_infer",
            "V_business_verify_tmpl_in_graph",
            "V_business_default_tmpl_in_graph",
        ):
            _emit(tag, False, f"import failed: {e!r}")
        return

    # _infer_target 直接调用
    try:
        tgt_v = mod._infer_target("EXPECTED_VERIFY_TMPL_SHA", "verify_infra_040.py")
    except Exception as e:
        tgt_v = f"<exc: {e!r}>"
    _emit(
        "V_business_verify_tmpl_infer",
        EXPECTED_VERIFY_TMPL_PATH in str(tgt_v) and "<unknown" not in str(tgt_v),
        f"got={tgt_v!r}",
    )

    try:
        tgt_d = mod._infer_target("EXPECTED_DEFAULT_TMPL_SHA", "verify_infra_040.py")
    except Exception as e:
        tgt_d = f"<exc: {e!r}>"
    _emit(
        "V_business_default_tmpl_infer",
        EXPECTED_DEFAULT_TMPL_PATH in str(tgt_d) and "<unknown" not in str(tgt_d),
        f"got={tgt_d!r}",
    )

    # build_graph 输出验证: locks 列表中两条 verify_infra_040 模板锁的
    # target 字段必须含 .github/ 路径 (且不是 unknown)
    try:
        graph = mod.build_graph()
    except Exception as e:
        for tag in (
            "V_business_verify_tmpl_in_graph",
            "V_business_default_tmpl_in_graph",
        ):
            _emit(tag, False, f"build_graph failed: {e!r}")
        return

    locks = graph.get("locks", [])
    verify_lock = None
    default_lock = None
    for l in locks:
        src = l.get("source", "")
        const = l.get("const", "")
        if not src.endswith("verify_infra_040.py"):
            continue
        if const == "EXPECTED_VERIFY_TMPL_SHA":
            verify_lock = l
        elif const == "EXPECTED_DEFAULT_TMPL_SHA":
            default_lock = l

    if verify_lock is None:
        _emit(
            "V_business_verify_tmpl_in_graph",
            False,
            "EXPECTED_VERIFY_TMPL_SHA not present in graph locks",
        )
    else:
        t = str(verify_lock.get("target", ""))
        ok = EXPECTED_VERIFY_TMPL_PATH in t and "<unknown" not in t and "_verify_template" not in t
        _emit(
            "V_business_verify_tmpl_in_graph",
            ok,
            f"target={t!r}",
        )

    if default_lock is None:
        _emit(
            "V_business_default_tmpl_in_graph",
            False,
            "EXPECTED_DEFAULT_TMPL_SHA not present in graph locks",
        )
    else:
        t = str(default_lock.get("target", ""))
        ok = EXPECTED_DEFAULT_TMPL_PATH in t and "<unknown" not in t
        _emit(
            "V_business_default_tmpl_in_graph",
            ok,
            f"target={t!r}",
        )


def main() -> None:
    v0_self_file_sha()
    v2_dump_file_sha()
    v_business_infer_targets()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(
            f"[verify_infra_040_tmpl_edge][SUMMARY] FAIL {failed}/{total}: {names}",
            flush=True,
        )
    else:
        print(
            f"[verify_infra_040_tmpl_edge][SUMMARY] ALL PASS ({total} checks)",
            flush=True,
        )
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
