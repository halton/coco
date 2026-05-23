#!/usr/bin/env python3
"""verify_infra_110: render_mermaid unknown target 复合 key 防 collision (verify-only).

## Lock: EXPECTED_CLASSIFY_NODE_FUNC_SHA
- target_function: _classify_node
- target_file: scripts/dump_v4_sha_graph.py
- lock_kind: ast_func_sha
- bump_when: _classify_node implementation changes
- bump_protocol: recompute func_sha_by_name("_classify_node", target_file) then update EXPECTED_CLASSIFY_NODE_FUNC_SHA
- rationale: lock 防止 unknown collision 处理 (V3b/V6b/V6c) 被悄改

infra-039-backlog-mermaid-unknown-target-id-collision (phase-59 #4):
``scripts/dump_v4_sha_graph.py`` 的 ``render_mermaid`` 此前对 unknown target
(``_infer_target`` 返回 ``<unknown target>``, 即 target 字段中无 ``.py`` token 的
反向锁) 用 ``unknown_<CONST>`` 作为占位 node id; 跨 source 同名 const (例如
``EXPECTED_TARGET_FILE_SHA`` 同时出现在 verify_infra_P290 与 verify_infra_P301)
会被合并到同一个 ``unknown_<CONST>`` 节点, 视觉上把多个互不相干的 unknown target
错误地呈现为一个节点接收多条入边。本 verify 锁住复合 key 改写后的行为:

- render_mermaid func sha 锁 (改写痕迹)
- behavior: build_graph 中若存在跨 source 同 const → unknown 的场景, mermaid 输出
  中每条 unknown 边的 target node id 必须互不相同 (即 outbound 到 unknown 的
  ``(src, const)`` 二元组与 ``tgt_id`` 一一对应, 不存在两个不同 source 指向同一个
  ``unknown_*`` 节点的退化)

INFRA_110_SHA_LOCKS
-------------------
- ``scripts/dump_v4_sha_graph.py`` file sha: EXPECTED_DUMP_FILE_SHA
- ``scripts/_verify_lib.py`` file sha: EXPECTED_VERIFY_LIB_FILE_SHA
- ``dump_v4_sha_graph.render_mermaid`` func sha: EXPECTED_RENDER_MERMAID_FUNC_SHA
- ``dump_v4_sha_graph._classify_node`` func sha: EXPECTED_CLASSIFY_NODE_FUNC_SHA
  (infra-110-backlog-classify-node-unknown-lock: V4_no_unknown_id_collision 隐式
  依赖 _classify_node 中 ``startswith("unknown_")`` 判定逻辑; 加独立 func sha
  锁，避免未来重构改动 _classify_node 时 V4 检查被 vacuous 绕过。verify_infra_060
  已有同名 EXPECTED_CLASSIFY_FUNC_SHA 主锁；本处独立持有第二份)
- self ``main`` func sha: EXPECTED_SELF_MAIN_FUNC_SHA

校验层级 (V0-V6):

- V0 scaffolding: dump_v4_sha_graph.py 存在 + render_mermaid 顶层符号在
- V1 docstring sentinel ``INFRA_110_SHA_LOCKS`` 自锁
- V2 双 file sha 锁 (dump + lib)
- V3 render_mermaid func sha 锁
- V3b _classify_node func sha 锁 (infra-110-backlog: 锁 unknown 分支判定逻辑)
- V4 behavior (live):
  - V4_unknown_edges_exist: build_graph 至少含 1 条 unknown target lock
  - V4_no_unknown_id_collision: 渲染出的 mermaid 输出中, 不存在「两条 unknown
    入边落到同一个 unknown_* node id」的情况; 即每条 unknown 边的 ``tgt_id``
    必须唯一, 且 ``tgt_id`` 形如 ``unknown_<src_stem>_<CONST>``
  - V4_composite_key_well_formed: 每个 unknown_* node id 必须能解析出 src_stem
    与 const 两段, 验证复合 key 设计而非单 key 退化
  - V4c_composite_key_src_stem_no_expected_substring: 每个 unknown_<src>_<const>
    的 src 段不得含 ``EXPECTED`` 或 ``expected`` 子串。理论上 verify 脚本 stem
    不会含 EXPECTED, 此检查作为**预防性硬锁** — 未来若有人引入诡异命名的
    verify 脚本（如 ``verify_EXPECTED_xxx.py``）, 非贪婪 anchor regex
    ``^unknown_(?P<src>...?)_(?P<const>EXPECTED_...)$`` 会切到 src 段内的第一个
    ``_EXPECTED_``, 把真正的 const 段错切到 src; V4 仍 PASS 但语义错位.
    本 check 单独把 src 段拎出来断言无 EXPECTED 子串, 显式 catch 这种 vacuous.
    (infra-110-backlog-composite-key-src-stem-no-expected-substring, phase-61 #4)
  - V4d_unknown_tgt_raw_no_expected_substring: anchor-independent 加固; 对所有
    unknown_* tgt_id 贪婪剥离尾部 ``_EXPECTED_<UPPER>$`` 后剩余 src_stem 做
    case-insensitive 'expected' substring 扫描. 对 anchor 不成型场景 (例如
    lowercase const 'expected_foo' / mixed-case 'Expected_FOO') 也触发 FAIL.
    与 V4c 互补 (V4c 锁 anchor 成型场景, V4d 锁 case 盲点 / anchor 不成型场景).
    (infra-V4c-case-insensitive-promote, phase-64 #3)
- V4b self main func sha
- V5 Reviewer LGTM gate (grace_period 兜底)
- V6 reverse_sha meta-lock: render_mermaid func sha (同 V3, 第二份独立持有)
- V6b reverse_sha meta-lock: _classify_node func sha (同 V3b, 第二份独立持有)
- V6c read_constant 自校 EXPECTED_CLASSIFY_NODE_FUNC_SHA 文字常量化
- V7 classify_node lock doc present (本 docstring 顶部 Lock 元信息小节自锁)
- V11 classify_node lock doc values (V7 升级: 字段值精确锁, 防字段值被悄改)

退出码 0=ALL PASS / 2=任一 FAIL.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DUMP_PY = SCRIPTS / "dump_v4_sha_graph.py"
VERIFY_LIB = SCRIPTS / "_verify_lib.py"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    assert_v5_reviewer_gate_evidence_bind,
    func_sha_by_name,
    read_constant,
    verify_summary_exit,
)

# infra-039-backlog-mermaid-unknown-target-id-collision sha lock 常量
EXPECTED_DUMP_FILE_SHA = (
    "b1fbe28b70bf3048a5b919877c07d15f966fe8da5769a03b2dfb6ffbc4bb6682"
)
EXPECTED_VERIFY_LIB_FILE_SHA = (
    "f789e0d870c9e6c3764b893a6bcbcca356bfc464520d9c49f3d4b17045ecd243"
)
EXPECTED_RENDER_MERMAID_FUNC_SHA = (
    "7c7a3ff794c99af00d24b42346f4e73ce879ab0a9cde462b3966d778be236685"
)
# infra-110-backlog-classify-node-unknown-lock: _classify_node func sha 锁
# (verify_infra_060.EXPECTED_CLASSIFY_FUNC_SHA 是主锁; 本处独立持有第二份避免
# 未来 _classify_node 改写 unknown 分支时 V4_no_unknown_id_collision 被 vacuous 绕过)
EXPECTED_CLASSIFY_NODE_FUNC_SHA = (
    "8dffcf4ebd0186243107dca1df2b78cc4b3950fe23508faa4c100c906b2738e1"
)
# 自身 main func sha (首跑用 __BUMP_ME__ 占位, 再回填)
EXPECTED_SELF_MAIN_FUNC_SHA = "4cc7d516805927ea2501f438f80c781b6e19c93e0133fa2e2d19c6dc409e0f48"

DOCSTRING_SENTINEL = "INFRA_110_SHA_LOCKS"
REAL_FEATURE_LIST = REPO / "feature_list.json"
V5_GATE_FEATURE_ID = "infra-039-backlog-mermaid-unknown-target-id-collision"

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    _results.append((tag, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[verify_infra_110] {status} {tag}: {detail}", flush=True)


def _file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _check_v4d_unknown_tgt_raw_no_expected_substring(
    unknown_edges: List[Tuple[str, str, str]],
) -> tuple[bool, str]:
    """V4d: 对所有 unknown_* tgt_id 做 anchor-independent 的 case-insensitive
    'expected' substring 扫描 (infra-V4c-case-insensitive-promote, phase-64 #3).

    背景: V4c 仅在 anchor regex ``^unknown_<src>_<EXPECTED_*>$`` 匹配成功的
    tgt 上做 src_seg 'expected' lower() 检查. 若有人引入 lowercase const 命名
    (例如 mermaid edge ``... -->|Expected_FOO| unknown_verify_xyz_Expected_FOO``)
    anchor regex 因 ``[A-Z0-9_]+`` 强制全大写而不匹配, V4c 因 ``if not m: continue``
    被 vacuously skip; 此时 V4 也会报 malformed FAIL, 但 src_stem 自身含
    'expected' 子串的语义错位**没有任何 check 显式 catch**, 仅作为 V4
    malformed 副作用露出.

    V4d 直接对 raw tgt_id 操作: 把尾部 ``_EXPECTED_<UPPER...>$`` 贪婪剥离作
    'const 段', 剩余视为 src_stem; 在剩余 src_stem 上做 case-insensitive
    'expected' substring 扫描. 对 anchor 不成型的 tgt (如 const 大小写绕过),
    也会触发 FAIL — anchor-independent 硬锁.

    与 V4c 互补: V4c 锁 anchor 成型场景的 mixed-case src; V4d 锁 anchor
    不成型场景的 mixed-case src/const (case 盲点消除).
    """
    # 贪婪剥离尾部 EXPECTED_<UPPER...>$, 剩余视为 src_stem (含前缀 'unknown_')
    strip_pat = re.compile(r"^(?P<rest>unknown_.+?)(_EXPECTED_[A-Z0-9_]+)?$")
    violations: List[str] = []
    checked = 0
    for _src_edge, _const_edge, tgt in unknown_edges:
        if not tgt.startswith("unknown_"):
            continue
        checked += 1
        m = strip_pat.match(tgt)
        # m 一定 match (至少 'unknown_' 前缀+1字符). rest 含前缀 'unknown_'
        rest = m.group("rest") if m else tgt
        # 去掉 'unknown_' 前缀, 剩余是 src_stem 候选 (可能含 mixed-case const)
        src_candidate = rest[len("unknown_") :] if rest.startswith("unknown_") else rest
        if "expected" in src_candidate.lower():
            violations.append(
                f"{tgt} (src_candidate={src_candidate!r} contains 'expected' "
                f"case-insensitively; anchor-independent check)"
            )
    if violations:
        return False, f"violations={violations[:3]}"
    return True, (
        f"all {checked} unknown_* tgt raw src segments free of 'expected' "
        f"substring (case-insensitive, anchor-independent)"
    )


def _check_v4c_composite_key_src_stem_no_expected_substring(
    unknown_edges: List[Tuple[str, str, str]],
) -> tuple[bool, str]:
    """V4c: 复合 key ``unknown_<src>_<const>`` 中 src 段不得含 ``expected`` 子串 (大小写无关).

    理论上 verify 脚本文件 stem 不会含 expected, 所以本 check 通常 vacuously PASS.
    预防性硬锁: 非贪婪 anchor regex ``^unknown_(?P<src>...?)_(?P<const>EXPECTED_...)$``
    在 src_stem 含 EXPECTED 时会切到第一个 ``_EXPECTED_`` 处, 把真 const 错切到 src,
    V4_composite_key_well_formed 仍 PASS 但语义错位; 此 check 显式 catch.

    实现: 从已 anchor-parse 出 ``unknown_<src>_<EXPECTED_*>`` 的 tgt_id 提取 src 段
    (用与 V4_composite_key_well_formed 相同的 anchor regex 重新 parse), 断言
    每个 src 段以 ``.lower()`` 比较后不含 ``expected`` 子串
    (覆盖 Expected / EXPECTeD / expecteD 等混合大小写绕过, phase-62 #3 加固).
    """
    composite_key_pattern = re.compile(
        r"^unknown_(?P<src>[a-zA-Z0-9_]+?)_(?P<const>EXPECTED_[A-Z0-9_]+)$"
    )
    violations: List[str] = []
    checked = 0
    for _src_edge, _const_edge, tgt in unknown_edges:
        m = composite_key_pattern.match(tgt)
        if not m:
            # anchor regex 不匹配的 tgt 由 V4_composite_key_well_formed 负责, 本 check 跳过
            continue
        src_seg = m.group("src")
        checked += 1
        if "expected" in src_seg.lower():
            violations.append(f"{tgt} (src_seg={src_seg!r} contains 'expected' substring case-insensitively)")
    if violations:
        return False, f"violations={violations[:3]}"
    return True, f"all {checked} composite key src segments free of 'expected' substring (case-insensitive)"


def _check_v7_classify_node_lock_doc_present() -> tuple[bool, str]:
    """V7: SELF 文件顶部 docstring (前 50 行) 必须含 classify_node lock 元信息字段.

    本检查是 sha-lock 的文档姊妹: 防止未来重构改了锁形态而 docstring 不同步,
    或反过来——把锁删了 docstring 还残留. 字段名匹配大小写敏感, 必须以 `- `
    前缀 (markdown 列表风格) 出现.
    """
    required_fields = [
        "- target_function:",
        "- target_file:",
        "- lock_kind:",
        "- bump_when:",
        "- bump_protocol:",
        "- rationale:",
    ]
    self_lines = Path(__file__).read_text(encoding="utf-8").splitlines()[:50]
    head = "\n".join(self_lines)
    missing = [f for f in required_fields if f not in head]
    if missing:
        return False, f"missing fields in top-50-line docstring: {missing}"
    return True, f"all {len(required_fields)} fields present in top-50-line docstring"


def _check_v11_classify_node_lock_doc_values() -> tuple[bool, str]:
    """V11: SELF 文件顶部 docstring (前 50 行) Lock 字段值必须精确匹配期望.

    V7 (字段名存在) 的升级版: 不仅锁字段名, 还锁字段值. 防止有人保留
    `- target_function:` 字段名但把值改成无关内容 (V7 仍 PASS 但语义被悄改).
    V11 与 V7 互补 — V7 防字段缺失, V11 防字段值篡改, 二者均需 PASS.

    前 3 字段精确等值; 后 3 字段宽松 startswith 期望前缀 (允许末尾扩写).
    """
    expected_exact = {
        "- target_function:": "_classify_node",
        "- target_file:": "scripts/dump_v4_sha_graph.py",
        "- lock_kind:": "ast_func_sha",
    }
    expected_prefix = {
        "- bump_when:": "_classify_node implementation changes",
        "- bump_protocol:": "recompute func_sha_by_name",
        "- rationale:": "lock 防止 unknown collision",
    }
    self_lines = Path(__file__).read_text(encoding="utf-8").splitlines()[:50]
    violations: List[str] = []
    checked = 0
    for field, expect_value in expected_exact.items():
        found = False
        for line in self_lines:
            if line.lstrip().startswith(field):
                value = line.split(":", 1)[1].strip()
                checked += 1
                found = True
                if value != expect_value:
                    violations.append(
                        f"{field!r} exact-match fail: got={value!r} expect={expect_value!r}"
                    )
                break
        if not found:
            violations.append(f"{field!r} not found in top-50-line docstring")
    for field, expect_prefix in expected_prefix.items():
        found = False
        for line in self_lines:
            if line.lstrip().startswith(field):
                value = line.split(":", 1)[1].strip()
                checked += 1
                found = True
                if not value.startswith(expect_prefix):
                    violations.append(
                        f"{field!r} prefix-match fail: got={value!r} expect_startswith={expect_prefix!r}"
                    )
                break
        if not found:
            violations.append(f"{field!r} not found in top-50-line docstring")
    if violations:
        return False, f"violations={violations[:3]}"
    return True, f"all {checked} lock field values match expected (3 exact + 3 prefix)"


def main() -> None:
    # V0 scaffolding
    if not DUMP_PY.is_file():
        _emit("V0_dump_exists", False, f"missing {DUMP_PY}")
        verify_summary_exit(sum(1 for _, ok, _ in _results if not ok))
        return
    _emit("V0_dump_exists", True, str(DUMP_PY.relative_to(REPO)))
    src = DUMP_PY.read_text(encoding="utf-8")
    if "def render_mermaid" not in src:
        _emit("V0_render_mermaid_symbol", False, "render_mermaid not found")
        verify_summary_exit(sum(1 for _, ok, _ in _results if not ok))
        return
    _emit("V0_render_mermaid_symbol", True, "render_mermaid present")

    # V1 docstring sentinel
    self_src = Path(__file__).read_text(encoding="utf-8")
    _emit(
        "V1_docstring_sentinel",
        DOCSTRING_SENTINEL in self_src,
        f"sentinel={DOCSTRING_SENTINEL}",
    )

    # V2 file sha (dump + lib)
    got_dump = _file_sha(DUMP_PY)
    if EXPECTED_DUMP_FILE_SHA == "__BUMP_ME__":
        _emit("V2_dump_file_sha", False, f"placeholder; bump EXPECTED_DUMP_FILE_SHA={got_dump}")
    else:
        _emit(
            "V2_dump_file_sha",
            got_dump == EXPECTED_DUMP_FILE_SHA,
            f"got={got_dump[:16]} expect={EXPECTED_DUMP_FILE_SHA[:16]}",
        )
    got_lib = _file_sha(VERIFY_LIB)
    if EXPECTED_VERIFY_LIB_FILE_SHA == "__BUMP_ME__":
        _emit("V2_verify_lib_file_sha", False, f"placeholder; bump EXPECTED_VERIFY_LIB_FILE_SHA={got_lib}")
    else:
        _emit(
            "V2_verify_lib_file_sha",
            got_lib == EXPECTED_VERIFY_LIB_FILE_SHA,
            f"got={got_lib[:16]} expect={EXPECTED_VERIFY_LIB_FILE_SHA[:16]}",
        )

    # V3 render_mermaid func sha
    try:
        got_render = func_sha_by_name(DUMP_PY, "render_mermaid")
    except Exception as e:
        _emit("V3_render_mermaid_func_sha", False, f"compute err: {e!r}")
        got_render = ""
    if got_render:
        if EXPECTED_RENDER_MERMAID_FUNC_SHA == "__BUMP_ME__":
            _emit(
                "V3_render_mermaid_func_sha",
                False,
                f"placeholder; bump EXPECTED_RENDER_MERMAID_FUNC_SHA={got_render}",
            )
        else:
            _emit(
                "V3_render_mermaid_func_sha",
                got_render == EXPECTED_RENDER_MERMAID_FUNC_SHA,
                f"got={got_render[:16]} expect={EXPECTED_RENDER_MERMAID_FUNC_SHA[:16]}",
            )

    # V3b _classify_node func sha (infra-110-backlog-classify-node-unknown-lock)
    # 锁住 _classify_node 整个函数体, 含 startswith("unknown_") 分支判定;
    # V4_no_unknown_id_collision 隐式依赖此判定逻辑正确, 加锁防止重构 vacuous.
    try:
        got_classify = func_sha_by_name(DUMP_PY, "_classify_node")
    except Exception as e:
        _emit("V3b_classify_node_func_sha", False, f"compute err: {e!r}")
        got_classify = ""
    if got_classify:
        if EXPECTED_CLASSIFY_NODE_FUNC_SHA == "__BUMP_ME__":
            _emit(
                "V3b_classify_node_func_sha",
                False,
                f"placeholder; bump EXPECTED_CLASSIFY_NODE_FUNC_SHA={got_classify}",
            )
        else:
            _emit(
                "V3b_classify_node_func_sha",
                got_classify == EXPECTED_CLASSIFY_NODE_FUNC_SHA,
                f"got={got_classify[:16]} expect={EXPECTED_CLASSIFY_NODE_FUNC_SHA[:16]}",
            )

    # V4 behavior — live build_graph + render_mermaid 观测 unknown 边
    from dump_v4_sha_graph import build_graph, render_mermaid  # noqa: E402

    g = build_graph()
    mermaid = render_mermaid(g)
    # 抓所有 edge ``src -->|const| tgt`` 中 tgt 以 ``unknown_`` 开头者
    edge_pat = re.compile(
        r"^\s*([A-Za-z0-9_]+)\s+-->\|([^|]+)\|\s+(unknown_\S+)\s*$",
        re.MULTILINE,
    )
    unknown_edges: List[Tuple[str, str, str]] = edge_pat.findall(mermaid)
    _emit(
        "V4_unknown_edges_exist",
        len(unknown_edges) >= 1,
        f"unknown_edge_count={len(unknown_edges)}",
    )

    # V4_no_unknown_id_collision: 不存在两条来自不同 (src, const) 的边落到同一 tgt_id
    # 反过来也要保证: 同一个 tgt_id 只对应一个 (src, const) 组合
    tgt_to_keys: Dict[str, set] = {}
    for src, const, tgt in unknown_edges:
        tgt_to_keys.setdefault(tgt, set()).add((src, const.strip()))
    collisions = {tgt: keys for tgt, keys in tgt_to_keys.items() if len(keys) > 1}
    _emit(
        "V4_no_unknown_id_collision",
        len(collisions) == 0,
        f"collisions={list(collisions.items())[:3]}" if collisions else f"all {len(tgt_to_keys)} unknown_* tgt_ids unique per (src,const)",
    )

    # V4_composite_key_well_formed: 每个 unknown_<src>_<CONST> 必须严格匹配
    # anchor 正则 ^unknown_<src_stem>_<EXPECTED_CONST>$ 且 (src, const) 与 edge 完全一致。
    # 严格 ordering 锁: 不允许 unknown_{const}_{src} (B 颠倒) 或 unknown_xxx_{src}_{const} (C 中插)
    # 等任何顺序变体。正则要求 src 段必须由小写字母/数字/下划线组成且整体以 EXPECTED_ 开头。
    # phase-60 #4 infra-110-backlog-composite-key-strict-ordering
    composite_key_pattern = re.compile(
        r"^unknown_(?P<src>[a-zA-Z0-9_]+?)_(?P<const>EXPECTED_[A-Z0-9_]+)$"
    )
    malformed: List[str] = []
    for src, const, tgt in unknown_edges:
        const_sanitized = re.sub(r"[^A-Za-z0-9_]", "_", const.strip())
        src_sanitized = re.sub(r"[^A-Za-z0-9_]", "_", src)
        m = composite_key_pattern.match(tgt)
        if not m:
            malformed.append(f"{tgt} (anchor regex mismatch)")
            continue
        got_src = m.group("src")
        got_const = m.group("const")
        if got_src != src_sanitized:
            malformed.append(
                f"{tgt} (src segment={got_src!r} expect={src_sanitized!r})"
            )
        elif got_const != const_sanitized:
            malformed.append(
                f"{tgt} (const segment={got_const!r} expect={const_sanitized!r})"
            )
    _emit(
        "V4_composite_key_well_formed",
        len(malformed) == 0,
        f"malformed={malformed[:3]}" if malformed else f"all {len(unknown_edges)} edges have strict-ordered composite key",
    )

    # V4c_composite_key_src_stem_no_expected_substring: 预防性硬锁
    # (infra-110-backlog-composite-key-src-stem-no-expected-substring, phase-61 #4)
    v4c_ok, v4c_detail = _check_v4c_composite_key_src_stem_no_expected_substring(unknown_edges)
    _emit("V4c_composite_key_src_stem_no_expected_substring", v4c_ok, v4c_detail)

    # V4d_unknown_tgt_raw_no_expected_substring: anchor-independent case-insensitive
    # (infra-V4c-case-insensitive-promote, phase-64 #3)
    v4d_ok, v4d_detail = _check_v4d_unknown_tgt_raw_no_expected_substring(unknown_edges)
    _emit("V4d_unknown_tgt_raw_no_expected_substring", v4d_ok, v4d_detail)

    # V4b self main func sha
    try:
        got_main = func_sha_by_name(Path(__file__), "main")
    except Exception as e:
        _emit("V4b_self_main_func_sha", False, f"compute err: {e!r}")
        got_main = ""
    if got_main:
        if EXPECTED_SELF_MAIN_FUNC_SHA == "__BUMP_ME__":
            _emit(
                "V4b_self_main_func_sha",
                False,
                f"placeholder; bump EXPECTED_SELF_MAIN_FUNC_SHA={got_main}",
            )
        else:
            _emit(
                "V4b_self_main_func_sha",
                got_main == EXPECTED_SELF_MAIN_FUNC_SHA,
                f"got={got_main[:16]} expect={EXPECTED_SELF_MAIN_FUNC_SHA[:16]}",
            )

    # V5 Reviewer LGTM gate (grace_period 兜底)
    if REAL_FEATURE_LIST.is_file():
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
    else:
        _emit("V5_reviewer_lgtm_gate", False, f"feature_list not found: {REAL_FEATURE_LIST}")

    # V6 reverse_sha meta-lock (与 V3 同 render_mermaid func sha, 独立持有)
    if got_render and EXPECTED_RENDER_MERMAID_FUNC_SHA != "__BUMP_ME__":
        _emit(
            "V6_render_mermaid_func_sha_meta",
            got_render == EXPECTED_RENDER_MERMAID_FUNC_SHA,
            f"meta got={got_render[:16]} expect={EXPECTED_RENDER_MERMAID_FUNC_SHA[:16]}",
        )

    # V6b reverse_sha meta-lock (与 V3b 同 _classify_node func sha, 独立持有)
    if got_classify and EXPECTED_CLASSIFY_NODE_FUNC_SHA != "__BUMP_ME__":
        _emit(
            "V6b_classify_node_func_sha_meta",
            got_classify == EXPECTED_CLASSIFY_NODE_FUNC_SHA,
            f"meta got={got_classify[:16]} expect={EXPECTED_CLASSIFY_NODE_FUNC_SHA[:16]}",
        )

    # V6c read_constant 自校 EXPECTED_CLASSIFY_NODE_FUNC_SHA 文字常量化
    # (确保常量是字符串字面量而非动态计算; 防止 EXPECTED_CLASSIFY_NODE_FUNC_SHA
    # 被改写为 ``func_sha_by_name(...)`` 之类的运行时表达式而 vacuous 通过 V3b)
    try:
        const_via_read = read_constant(Path(__file__), "EXPECTED_CLASSIFY_NODE_FUNC_SHA")
        _emit(
            "V6c_classify_node_func_sha_constant_literal",
            isinstance(const_via_read, str) and const_via_read == EXPECTED_CLASSIFY_NODE_FUNC_SHA,
            f"read_constant={(const_via_read[:16] if isinstance(const_via_read, str) else type(const_via_read).__name__)} "
            f"runtime={EXPECTED_CLASSIFY_NODE_FUNC_SHA[:16]}",
        )
    except Exception as e:
        _emit("V6c_classify_node_func_sha_constant_literal", False, f"read_constant err: {e!r}")

    # V7 classify_node lock doc present (docstring 元信息姊妹锁)
    v7_ok, v7_detail = _check_v7_classify_node_lock_doc_present()
    _emit("V7_classify_node_lock_doc_present", v7_ok, v7_detail)

    # V11 classify_node lock doc values (V7 升级: 字段值精确锁)
    v11_ok, v11_detail = _check_v11_classify_node_lock_doc_values()
    _emit("V11_classify_node_lock_doc_values", v11_ok, v11_detail)

    total = len(_results)
    failed = sum(1 for _, ok, _ in _results if not ok)
    if failed:
        names = [t for t, ok, _ in _results if not ok]
        print(f"[verify_infra_110][SUMMARY] FAIL {failed}/{total}: {names}", flush=True)
    else:
        print(f"[verify_infra_110][SUMMARY] ALL PASS ({total} checks)", flush=True)
    verify_summary_exit(failed)


if __name__ == "__main__":
    main()
