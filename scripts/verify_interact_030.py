#!/usr/bin/env python3
"""verify_interact_030: interact-016 backlog 二次消化 verify-only meta 锁面。

源 backlog: interact-016-backlog-doc-polish
前序 P125 interact-028 已完成主要工作：
  - coco/proactive_trace.py: _RESERVED_TRACE_KEYS frozenset 加 ``taskName``
  - coco/logging_setup.py: JsonlFormatter._RESERVED 加 ``taskName``
  - emit_trace 注释更新 Python 3.13 KeyError 描述（说明非 3.13 特有，
    所有受支持 CPython 3.10-3.13 一致）

interact-030 (P213) 是该 backlog 的第二次复查 + verify-only 锁面：
  - V0 file existence
  - V1 双层 _RESERVED 集合都含 taskName
  - V2 trace ⊇ logging 双向同步（trace 集合 size ≥ logging size 且
       logging∩logging-reserved ⊆ trace）
  - V3 emit_trace 注释含 "Python 3.13" / "KeyError" 关键 token
  - V4 mutant 反证（in-memory 删除任一侧 taskName 应使 V1 FAIL）
  - V5 summary

0 业务行为改动；本脚本仅 read-only 静态校验。

运行环境约定 (infra-034)
------------------------
本脚本及其子进程**必须**在已激活的 .venv 下运行 (Python 解释器入口
``.venv/bin/python``); 不要用系统 ``python3`` 直接调用本脚本, 否则
``importlib`` 加载业务模块时依赖 (numpy / soundfile / onnxruntime 等) 可能
解析到系统站点而非 venv 站点, 导致与 ``./init.sh`` smoke 路径不一致。

约定细则:
  - **Reviewer / CI / 手动复跑入口**: 一律 ``.venv/bin/python`` 启动 (或先
    ``source .venv/bin/activate`` 再 ``python scripts/verify_interact_030.py``)。
  - **子进程 invoke**: 任何 ``subprocess.run`` 第一参数固定使用 ``sys.executable``
    (即本脚本所属解释器); 不写死 ``"python"`` / ``"python3"`` 字面量。
  - **环境变量继承**: 子进程从 ``os.environ`` 拷贝 PATH / PYTHONPATH 等,
    PATH 中 venv 的 ``bin`` 目录位置不可被人为打乱。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACE_FILE = ROOT / "coco" / "proactive_trace.py"
LOGGING_FILE = ROOT / "coco" / "logging_setup.py"


def _passed(tag: str, msg: str) -> None:
    print(f"[PASS] {tag}: {msg}")


def _failed(tag: str, msg: str) -> None:
    print(f"[FAIL] {tag}: {msg}")


def v0_files() -> bool:
    ok = True
    for p in (TRACE_FILE, LOGGING_FILE):
        if not p.is_file():
            _failed("V0", f"missing {p.relative_to(ROOT)}")
            ok = False
    if ok:
        _passed("V0", "proactive_trace.py + logging_setup.py 均存在")
    return ok


def _load_trace_keys() -> frozenset[str]:
    from coco.proactive_trace import _RESERVED_TRACE_KEYS  # type: ignore

    return _RESERVED_TRACE_KEYS  # type: ignore[return-value]


def _load_logging_reserved() -> frozenset[str]:
    from coco.logging_setup import JsonlFormatter  # type: ignore

    return frozenset(JsonlFormatter._RESERVED)


def v1_both_contain_taskname() -> bool:
    trace = _load_trace_keys()
    logging_reserved = _load_logging_reserved()
    ok = True
    if "taskName" not in trace:
        _failed("V1", "_RESERVED_TRACE_KEYS missing 'taskName'")
        ok = False
    if "taskName" not in logging_reserved:
        _failed("V1", "JsonlFormatter._RESERVED missing 'taskName'")
        ok = False
    if ok:
        _passed("V1", "双层 _RESERVED 集合均含 'taskName'")
    return ok


def v2_trace_superset_logging() -> bool:
    trace = _load_trace_keys()
    logging_reserved = _load_logging_reserved()
    # logging stdlib LogRecord reserved 字段（与 trace 共享子集）：除 trace 独有的
    # schema reserved (stage/candidate_id/decision/reason/ts) 外，logging_reserved
    # 中每个键都应在 trace 中。
    missing = sorted(logging_reserved - trace)
    if missing:
        _failed("V2", f"trace 缺失 logging reserved: {missing}")
        return False
    _passed(
        "V2",
        f"trace ⊇ logging（trace={len(trace)} ⊇ logging={len(logging_reserved)}）",
    )
    return True


def v3_emit_trace_comment_tokens() -> bool:
    text = TRACE_FILE.read_text(encoding="utf-8")
    required_tokens = ("Python", "3.13", "KeyError", "3.10", "3.11", "3.12")
    missing = [t for t in required_tokens if t not in text]
    if missing:
        _failed("V3", f"proactive_trace.py 缺关键注释 token: {missing}")
        return False
    # 进一步要求注释段位于 _RESERVED_TRACE_KEYS 定义附近（前 200 行内）
    head = "\n".join(text.splitlines()[:120])
    if "KeyError" not in head or "3.13" not in head:
        _failed("V3", "KeyError / 3.13 anchor 不在文件前 120 行 (_RESERVED 注释附近)")
        return False
    _passed("V3", "emit_trace 注释含 Python 3.10/3.11/3.12/3.13 + KeyError 描述")
    return True


def v4_mutant() -> bool:
    """in-memory 反证：构造缺失 taskName 的 frozenset 应使 V1 等价检查 FAIL。"""
    trace = _load_trace_keys()
    logging_reserved = _load_logging_reserved()
    mutant_trace = frozenset(k for k in trace if k != "taskName")
    mutant_logging = frozenset(k for k in logging_reserved if k != "taskName")
    if "taskName" in mutant_trace or "taskName" in mutant_logging:
        _failed("V4", "mutant 未能移除 taskName（不可能）")
        return False
    # 检查 V2 trace ⊇ logging 对 mutant 也仍成立（同步移除）
    if mutant_logging - mutant_trace:
        _failed("V4", "mutant 同步后 trace ⊉ logging 不应发生")
        return False
    # 双向反证：单边删除 taskName 应使 trace ⊉ logging 关系出现 diff
    only_trace_mutant = frozenset(k for k in trace if k != "taskName")
    diff = logging_reserved - only_trace_mutant
    if diff != {"taskName"}:
        _failed("V4", f"单边删 trace.taskName 后 diff 应为 {{'taskName'}}，实得 {diff}")
        return False
    _passed("V4", "mutant 反证通过（单边删 taskName 触发 V2 失败）")
    return True


def v5_summary(results: dict[str, bool]) -> bool:
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print()
    print(f"[SUMMARY] interact-030 verify-only meta lock: {passed}/{total} PASS")
    for k, v in results.items():
        print(f"  - {k}: {'PASS' if v else 'FAIL'}")
    return passed == total


def main() -> int:
    sys.path.insert(0, str(ROOT))
    results: dict[str, bool] = {}
    results["V0"] = v0_files()
    if not results["V0"]:
        v5_summary(results)
        return 1
    results["V1"] = v1_both_contain_taskname()
    results["V2"] = v2_trace_superset_logging()
    results["V3"] = v3_emit_trace_comment_tokens()
    results["V4"] = v4_mutant()
    ok = v5_summary(results)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
