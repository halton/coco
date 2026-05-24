#!/usr/bin/env python3
"""verify_infra_107: dump_v4_sha_graph family 等价不变量锁 (verify-only).

infra-P290-backlog-dump-family-equality-check (phase-54 #3.54):
``scripts/dump_v4_sha_graph.py`` 中 ``_HUB_FAMILY`` / ``_LIB_FAMILY`` /
``_DUMP_FAMILY`` 三个 frozenset 是 ``_classify_node`` 分类的成员判断
入口。#3.53 已经用 verify_infra_102 锁了 ``in _XXX_FAMILY`` keyword 与
isinstance(frozenset) 检查, 但**未锁三 family 之间的等价/不交关系**, 也
未把 contents 与 type 双信号绑在一起 (frozenset({"x"}) == {"x"} 为
True, 单看 == 不能区分 frozenset 与 set)。本 verifier 在 P290 体系外
新增一个独立锁:

- 三 family pairwise intersection == empty (V1)
- 每个 family 非空 (V2)
- 三 family 当前 elems 快照与预期相等 (type+frozenset 双信号; V3)
- ``scripts/dump_v4_sha_graph.py`` 整文件 file-sha 锁 (V4, cascade 入口)
- 本 verifier ``main`` func sha 自锁 (V4b)
- Reviewer LGTM gate (V5)

INFRA_107_SHA_LOCKS
-------------------
- ``scripts/dump_v4_sha_graph.py`` file sha: EXPECTED_DUMP_FILE_SHA
- self ``main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DUMP_FILE = SCRIPTS / "dump_v4_sha_graph.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    verify_summary_exit,
)

# infra-P290-backlog sha lock 常量
EXPECTED_DUMP_FILE_SHA = (
    "c2a63a68b7c38f80190d1ad2b6ed7cd5e61bb24c9e15ccb9be471b6f3e05bb79"
)
# 自身 main func sha (首跑用 __BUMP_ME__ 占位, 再回填)
EXPECTED_SELF_MAIN_FUNC_SHA = (
    "ef5f4042ea4fc5893a07f798e5d4dce9061dd56a1843120628eaff561022976d"
)

# 三 family 预期 elems 快照 (与 dump_v4_sha_graph.py 当前定义一致)
EXPECTED_HUB_FAMILY = frozenset({"v4_sha_json"})
EXPECTED_LIB_FAMILY = frozenset({"_verify_lib"})
EXPECTED_DUMP_FAMILY = frozenset({"dump_v4_sha_graph", "dump_reverse_sha_lock_index"})

REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-P290-backlog-dump-family-equality-check"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_107][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_dump_module():
    spec = importlib.util.spec_from_file_location(
        "dump_v4_sha_graph_for_v107", DUMP_FILE
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# V0: scaffolding
# ---------------------------------------------------------------------------
def v0_scaffolding() -> None:
    _emit(
        "V0_dump_file_exists",
        DUMP_FILE.is_file(),
        f"path={DUMP_FILE.relative_to(REPO)}",
    )


# ---------------------------------------------------------------------------
# V1: 三 family pairwise disjoint
# ---------------------------------------------------------------------------
def v1_families_disjoint(mod) -> None:
    if mod is None:
        _emit("V1_hub_lib_disjoint", False, "module load failed")
        _emit("V1_hub_dump_disjoint", False, "module load failed")
        _emit("V1_lib_dump_disjoint", False, "module load failed")
        return
    hub = getattr(mod, "_HUB_FAMILY", None)
    lib = getattr(mod, "_LIB_FAMILY", None)
    dump = getattr(mod, "_DUMP_FAMILY", None)
    if hub is None or lib is None or dump is None:
        _emit(
            "V1_hub_lib_disjoint",
            False,
            f"missing family: hub={hub is not None} lib={lib is not None} dump={dump is not None}",
        )
        return
    pairs = [
        ("V1_hub_lib_disjoint", hub, lib, "_HUB_FAMILY", "_LIB_FAMILY"),
        ("V1_hub_dump_disjoint", hub, dump, "_HUB_FAMILY", "_DUMP_FAMILY"),
        ("V1_lib_dump_disjoint", lib, dump, "_LIB_FAMILY", "_DUMP_FAMILY"),
    ]
    for tag, a, b, na, nb in pairs:
        inter = set(a) & set(b)
        _emit(
            tag,
            len(inter) == 0,
            f"{na} & {nb} = {sorted(inter)!r}",
        )


# ---------------------------------------------------------------------------
# V2: 每个 family 非空
# ---------------------------------------------------------------------------
def v2_families_nonempty(mod) -> None:
    if mod is None:
        _emit("V2_hub_nonempty", False, "module load failed")
        _emit("V2_lib_nonempty", False, "module load failed")
        _emit("V2_dump_nonempty", False, "module load failed")
        return
    for tag, name in [
        ("V2_hub_nonempty", "_HUB_FAMILY"),
        ("V2_lib_nonempty", "_LIB_FAMILY"),
        ("V2_dump_nonempty", "_DUMP_FAMILY"),
    ]:
        fam = getattr(mod, name, None)
        ok = fam is not None and len(fam) > 0
        size = len(fam) if fam is not None else -1
        _emit(tag, ok, f"{name} size={size}")


# ---------------------------------------------------------------------------
# V3: type+elems 双信号锁 (frozenset({...}) 与 set({...}) 都 == set, 必须
# 同时校验 isinstance(frozenset) 与 elems 一致, 才能 catch frozenset→set
# 的 mutation)
# ---------------------------------------------------------------------------
def v3_type_and_elems(mod) -> None:
    if mod is None:
        for tag in (
            "V3_hub_type_frozenset",
            "V3_hub_elems",
            "V3_lib_type_frozenset",
            "V3_lib_elems",
            "V3_dump_type_frozenset",
            "V3_dump_elems",
        ):
            _emit(tag, False, "module load failed")
        return
    cases = [
        ("V3_hub_type_frozenset", "V3_hub_elems", "_HUB_FAMILY", EXPECTED_HUB_FAMILY),
        ("V3_lib_type_frozenset", "V3_lib_elems", "_LIB_FAMILY", EXPECTED_LIB_FAMILY),
        ("V3_dump_type_frozenset", "V3_dump_elems", "_DUMP_FAMILY", EXPECTED_DUMP_FAMILY),
    ]
    for type_tag, elems_tag, name, expect in cases:
        fam = getattr(mod, name, None)
        is_fs = isinstance(fam, frozenset)
        _emit(
            type_tag,
            is_fs,
            f"{name} type={type(fam).__name__}",
        )
        elems_ok = is_fs and set(fam) == set(expect)
        _emit(
            elems_tag,
            elems_ok,
            f"{name} got={sorted(fam) if fam is not None else None!r} "
            f"expect={sorted(expect)!r}",
        )


# ---------------------------------------------------------------------------
# V4: dump_v4_sha_graph.py 整文件 file-sha 锁 (cascade 入口)
# ---------------------------------------------------------------------------
def v4_dump_file_sha() -> None:
    got = _file_sha(DUMP_FILE)
    if EXPECTED_DUMP_FILE_SHA == "__BUMP_ME__":
        _emit(
            "V4_dump_file_sha",
            False,
            f"placeholder; bump EXPECTED_DUMP_FILE_SHA={got}",
        )
    else:
        _emit(
            "V4_dump_file_sha",
            got == EXPECTED_DUMP_FILE_SHA,
            f"got={got[:16]} expect={EXPECTED_DUMP_FILE_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V4b: 自身 main func sha 自锁
# ---------------------------------------------------------------------------
def v4b_self_main_func_sha() -> None:
    self_path = Path(__file__)
    got = func_sha_by_name(self_path, "main")
    if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
        _emit(
            "V4b_self_main_func_sha",
            False,
            f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got}",
        )
    else:
        _emit(
            "V4b_self_main_func_sha",
            got == EXPECTED_SELF_MAIN_FUNC_SHA,
            f"got={got[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
        )


# ---------------------------------------------------------------------------
# V5: Reviewer LGTM gate
# ---------------------------------------------------------------------------
def v5_reviewer_gate() -> None:
    if not REAL_FEATURE_LIST.is_file():
        _emit(
            "V5_reviewer_lgtm_gate",
            False,
            f"feature_list.json not found at {REAL_FEATURE_LIST}",
        )
        return
    result = assert_v5_reviewer_gate_evidence_bind(
        V5_GATE_FEATURE_ID, REAL_FEATURE_LIST,
        grace_period_feature_ids=(V5_GATE_FEATURE_ID,),
    )
    _emit(
        "V5_reviewer_lgtm_gate",
        bool(result["ok"]),
        f"target={V5_GATE_FEATURE_ID} helper_ok={result['ok']} "
        f"grace_skipped={result['grace_skipped']} "
        f"verdict={result['verdict']!r} kind={result['reviewer_kind']!r} "
        f"summary_len={result['summary_len']} reason={result['reason']!r}",
    )


def main() -> None:
    v0_scaffolding()
    mod = _load_dump_module()
    v1_families_disjoint(mod)
    v2_families_nonempty(mod)
    v3_type_and_elems(mod)
    v4_dump_file_sha()
    v4b_self_main_func_sha()
    v5_reviewer_gate()
    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(f"[verify_infra_107][SUMMARY] FAIL {failed}/{total}: {names}", flush=True)
    else:
        print(f"[verify_infra_107][SUMMARY] ALL PASS ({total} checks)", flush=True)
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
