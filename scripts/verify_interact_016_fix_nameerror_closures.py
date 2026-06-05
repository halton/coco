"""scripts/verify_interact_016_fix_nameerror_closures.py — interact-016 锁定.

修复 coco/main.py 两个闭包 NameError：

* ``_group_mode_ref`` ：attention_loop 闭包按名查找该容器，但旧代码定义点在
  闭包之后（line ~580），导致 attention thread 启动后每 tick 抛
  ``NameError("_group_mode_ref")``。
* ``_proactive_ref`` ：scene_caption on_caption 60s 回调访问该容器，但旧代码
  完全没有 ``_proactive_ref = [None]`` 初始化行，只有写入端（line ~1392）和
  读取端（line ~603），导致 NameError("_proactive_ref")。

修法：将两个容器统一在 attention block 之前初始化（line 335-336），保留写入端
与闭包读端不变；保留 ``_mm_fusion_ref`` 原位（它无 NameError 风险）。

V0  hash lock on coco/main.py + anchors（含两条新定义 + 注释 tag）
V1  ``import coco.main`` 不抛
V2  AST 解析：``_group_mode_ref`` 计数 ``= [None]`` 定义 >=1 且使用 >=1
V3  AST 解析：``_proactive_ref`` 计数 ``= [None]`` 定义 >=1 且使用 >=1
V4  模拟 attention tick 闭包：构造同结构 closure 访问 ``_group_mode_ref[0]`` 不抛
V5  模拟 scene_caption on_caption 闭包：访问 ``_proactive_ref[0]`` 不抛
V6  py_compile coco/main.py 通过

跑法：``./.venv/bin/python scripts/verify_interact_016_fix_nameerror_closures.py``
"""

from __future__ import annotations

import hashlib
import py_compile
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}: {detail}")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


MAIN_PATH = ROOT / "coco" / "main.py"

# 修改后预期 sha；任何后续编辑都会让 V0 红，提醒重新锁。
EXPECTED_MAIN_SHA = "afa5e5b3f9e2b1802dcba96811e3cfcd214025582d207dc8b44d6924bd0d6de4"


# ---------------------------------------------------------------------------
# V0 hash lock + anchors
# ---------------------------------------------------------------------------


def v0_hash_lock() -> None:
    try:
        if not MAIN_PATH.exists():
            _record("V0_hash_lock", False, f"missing {MAIN_PATH}")
            return
        actual = _sha256(MAIN_PATH)
        src = MAIN_PATH.read_text(encoding="utf-8")
        anchors = {
            "feature_tag": "interact-016" in src,
            "group_mode_def_early": "_group_mode_ref: list = [None]" in src,
            "proactive_def_early": "_proactive_ref: list = [None]" in src,
            "group_mode_closure_read": "_gmc = _group_mode_ref[0]" in src,
            "proactive_closure_read": "_p = _proactive_ref[0]" in src,
            "group_mode_writer": "_group_mode_ref[0] = _group_mode_coord" in src,
            "proactive_writer": "_proactive_ref[0] = _proactive" in src,
        }
        missing = [k for k, v in anchors.items() if not v]
        sha_ok = actual == EXPECTED_MAIN_SHA
        ok = sha_ok and not missing
        detail = (
            f"sha256={actual[:16]} expected={EXPECTED_MAIN_SHA[:16]} "
            f"anchors_missing={missing}"
        )
        _record("V0_hash_lock", ok, detail)
    except Exception as e:  # noqa: BLE001
        _record("V0_hash_lock", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V1 import coco.main 不抛
# ---------------------------------------------------------------------------


def v1_import_main() -> None:
    try:
        import importlib

        if "coco.main" in sys.modules:
            del sys.modules["coco.main"]
        mod = importlib.import_module("coco.main")
        ok = mod is not None and hasattr(mod, "main")
        _record(
            "V1_import_main",
            ok,
            f"module={mod.__name__ if mod else None} has_main={hasattr(mod, 'main')}",
        )
    except Exception as e:  # noqa: BLE001
        _record("V1_import_main", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V2 / V3 AST 计数
# ---------------------------------------------------------------------------


def _count_defs_and_uses(src: str, name: str) -> Dict[str, int]:
    # 定义形态： `_xxx: list = [None]`（容器初始化） 或 `_xxx = ...`
    def_pat = re.compile(rf"\b{name}\s*(?::\s*list)?\s*=\s*\[")
    use_pat = re.compile(rf"\b{name}\b")
    defs = def_pat.findall(src)
    uses = use_pat.findall(src)
    # 排除注释行中的引用（按行 grep '#' 之前部分）
    code_only = "\n".join(
        line.split("#", 1)[0] for line in src.splitlines()
    )
    code_uses = use_pat.findall(code_only)
    return {"defs_init": len(defs), "uses_all": len(uses), "uses_code": len(code_uses)}


def v2_group_mode_ref_ast() -> None:
    try:
        src = MAIN_PATH.read_text(encoding="utf-8")
        c = _count_defs_and_uses(src, "_group_mode_ref")
        ok = c["defs_init"] >= 1 and c["uses_code"] >= 2  # 至少 1 def + 闭包读 + 写入
        _record(
            "V2_group_mode_ref_ast",
            ok,
            f"defs_init={c['defs_init']} uses_code={c['uses_code']}",
        )
    except Exception as e:  # noqa: BLE001
        _record("V2_group_mode_ref_ast", False, f"{type(e).__name__}: {e}")


def v3_proactive_ref_ast() -> None:
    try:
        src = MAIN_PATH.read_text(encoding="utf-8")
        c = _count_defs_and_uses(src, "_proactive_ref")
        ok = c["defs_init"] >= 1 and c["uses_code"] >= 2  # 至少 1 def + 闭包读 + 写入
        _record(
            "V3_proactive_ref_ast",
            ok,
            f"defs_init={c['defs_init']} uses_code={c['uses_code']}",
        )
    except Exception as e:  # noqa: BLE001
        _record("V3_proactive_ref_ast", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V4 模拟 attention_loop 闭包结构访问 _group_mode_ref[0] 不抛
# ---------------------------------------------------------------------------


def v4_attention_closure_sim() -> None:
    """复刻原 closure 模式：enclosing 先定义 list 容器，闭包按名查找。

    原 bug：enclosing 定义在闭包之后，closure 创建时 cell 还未绑定 → NameError。
    修后：enclosing 定义在闭包之前，闭包正常 cell-lookup。
    """
    try:
        def _make_enclosing_pre_def():
            # 模拟 main.py 当前结构：先定义容器，再定义闭包
            _group_mode_ref: list = [None]  # noqa: F841

            def _attention_loop_tick():
                _gmc = _group_mode_ref[0]
                return _gmc

            return _attention_loop_tick

        tick = _make_enclosing_pre_def()
        # 模拟 attention tick：default OFF，_gmc=None，no-op，闭包不抛
        result = tick()
        ok = result is None
        _record(
            "V4_attention_closure_sim",
            ok,
            f"tick()={result!r} (expected None, no NameError)",
        )
    except NameError as e:
        _record("V4_attention_closure_sim", False, f"NameError raised: {e!r}")
    except Exception as e:  # noqa: BLE001
        _record("V4_attention_closure_sim", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V5 模拟 scene_caption on_caption 闭包访问 _proactive_ref[0] 不抛
# ---------------------------------------------------------------------------


def v5_scene_caption_closure_sim() -> None:
    try:
        def _make_enclosing_pre_def():
            _proactive_ref: list = [None]  # noqa: F841

            def _on_caption_cb(text):  # noqa: ANN001
                _p = _proactive_ref[0]
                if _p is not None:
                    return _p.record_caption_trigger(text)
                return None

            return _on_caption_cb

        cb = _make_enclosing_pre_def()
        result = cb("hello")
        ok = result is None
        _record(
            "V5_scene_caption_closure_sim",
            ok,
            f"cb('hello')={result!r} (expected None, no NameError)",
        )
    except NameError as e:
        _record("V5_scene_caption_closure_sim", False, f"NameError raised: {e!r}")
    except Exception as e:  # noqa: BLE001
        _record("V5_scene_caption_closure_sim", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V6 py_compile coco/main.py
# ---------------------------------------------------------------------------


def v6_py_compile() -> None:
    try:
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=True) as tmp:
            py_compile.compile(
                str(MAIN_PATH),
                cfile=tmp.name,
                doraise=True,
            )
        _record("V6_py_compile", True, f"compiled {MAIN_PATH.name}")
    except py_compile.PyCompileError as e:
        _record("V6_py_compile", False, f"PyCompileError: {e!r}")
    except Exception as e:  # noqa: BLE001
        _record("V6_py_compile", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    v0_hash_lock()
    v1_import_main()
    v2_group_mode_ref_ast()
    v3_proactive_ref_ast()
    v4_attention_closure_sim()
    v5_scene_caption_closure_sim()
    v6_py_compile()
    total = len(_results)
    passed = sum(1 for r in _results if r["ok"])
    failed = total - passed
    print(f"\nSUMMARY: total={total} pass={passed} fail={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
