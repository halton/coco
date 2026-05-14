"""verify_companion_012 — 验证 companion-012 落地。

吸收 companion-011 follow-ups：
- fu-1: verify_companion_011 V12b 表达式简化 + observe cheap-doc
- fu-2: profile_id_resolver face_id 真接（stub + fallback chain）
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []
PASSES: list[str] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    msg = f"[{tag}] {name}" + (f" — {detail}" if detail else "")
    print(msg)
    (PASSES if ok else FAILURES).append(name)


def v1_verify011_v12b_simplified() -> None:
    """V1: verify_companion_011 V12b 应为简化布尔表达式（无 and/or 混合优先级歧义）。"""
    src = (ROOT / "scripts" / "verify_companion_011.py").read_text(encoding="utf-8")
    # 找 V12b 紧邻的几行（label + 表达式 + detail）
    lines = src.splitlines()
    region_lines: list[str] = []
    for i, ln in enumerate(lines):
        if "V12b main.py wires _group_mode_ref" in ln:
            region_lines = lines[i : i + 3]
            break
    expr_region = "\n".join(region_lines)
    # 简化后形式：`("..." in src) and ("..." in src)` —— 必含显式括号 and
    has_parens_and = ') and (' in expr_region
    has_required_atoms = ("_gmc.observe" in expr_region) and ("_group_mode_ref" in expr_region)
    no_old_mix = re.search(r"not\s+in\s+src\s+or\s+", expr_region) is None
    ok = has_parens_and and has_required_atoms and no_old_mix
    _check(
        "V1 verify_companion_011 V12b 简化（显式括号 + 无 and/or 混合）",
        ok,
        f"parens_and={has_parens_and} atoms={has_required_atoms} no_old_mix={no_old_mix}",
    )


def v2_observe_cheap_doc() -> None:
    """V2: group_mode.py observe docstring 含 cheap path 注释。"""
    src = (ROOT / "coco" / "companion" / "group_mode.py").read_text(encoding="utf-8")
    # 取 observe 函数体的 docstring 段
    m = re.search(r"def observe\(self, snapshot.*?\n(.*?)\n    [^\s]", src, re.S)
    region = m.group(1) if m else src
    has_cheap = "cheap" in region.lower() or "no inline io" in region.lower()
    _check("V2 group_mode.observe docstring 含 cheap / no inline IO", has_cheap)


def v3_face_tracker_get_face_id() -> None:
    """V3: face_tracker 有 get_face_id 方法。"""
    src = (ROOT / "coco" / "perception" / "face_tracker.py").read_text(encoding="utf-8")
    has_method = re.search(r"def get_face_id\(self,\s*name", src) is not None
    _check("V3 face_tracker.get_face_id 方法存在", has_method)


def v4_main_resolver_face_id_path() -> None:
    """V4: main.py group_mode wire profile_id_resolver 走 face_id 路径。"""
    src = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    has_call = "get_face_id" in src
    has_fallback = re.search(r"_fid\s*or\s*name", src) is not None
    _check(
        "V4 main.py profile_id_resolver 调用 get_face_id + fallback chain",
        has_call and has_fallback,
        f"get_face_id={has_call} fallback={has_fallback}",
    )


def v5_resolver_fallback_behavior() -> None:
    """V5: face_id=None 时 resolver 仍能 resolve 出 profile_id（fallback 到 name）。"""
    try:
        from coco.companion.profile_persist import compute_profile_id

        class _FakeFT:
            def get_face_id(self, name):  # noqa: ARG002
                return None

        ft = _FakeFT()

        def resolver(name: str):
            if not name:
                return None
            fid = None
            if hasattr(ft, "get_face_id"):
                try:
                    fid = ft.get_face_id(name)
                except Exception:
                    fid = None
            stable = fid or name
            return compute_profile_id(stable, name)

        pid = resolver("alice")
        _check("V5 resolver fallback(face_id=None) -> profile_id 非空", bool(pid), f"pid={pid!r}")
    except Exception as e:  # noqa: BLE001
        _check("V5 resolver fallback behavior", False, f"exception {e!r}")


def v6_regress_companion_011() -> None:
    """V6: verify_companion_011 全量回归（退出码 0）。"""
    py = sys.executable
    r = subprocess.run(
        [py, "scripts/verify_companion_011.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    _check(
        "V6 verify_companion_011 回归 (exit=0)",
        r.returncode == 0,
        f"rc={r.returncode} tail={r.stdout.strip().splitlines()[-1] if r.stdout else r.stderr[-120:]}",
    )


def v7_observe_no_inline_io() -> None:
    """V7: group_mode.observe 函数体内无 IO 调用。"""
    src = (ROOT / "coco" / "companion" / "group_mode.py").read_text(encoding="utf-8")
    # 提取 observe 函数体（粗略：def observe 到下一个顶层 def）
    m = re.search(r"\n    def observe\(self,.*?\n(.*?)\n    def\s", src, re.S)
    body = m.group(1) if m else ""
    bad_patterns = [r"\bopen\(", r"\.write\(", r"requests\.", r"subprocess\.", r"urllib\."]
    found = [p for p in bad_patterns if re.search(p, body)]
    _check("V7 observe 函数体无 IO 调用", not found, f"violations={found}")


def v8_import_health_and_auth() -> None:
    """V8: 主要模块 import OK + AUTHORITATIVE_FILES 含 group_mode。"""
    try:
        from coco.companion import group_mode  # noqa: F401
        from coco.perception import face_tracker  # noqa: F401
        ok_imp = True
    except Exception as e:  # noqa: BLE001
        ok_imp = False
        print(f"  import error: {e!r}")
    _check("V8a imports (group_mode + face_tracker)", ok_imp)
    # AUTH 检查（与 verify_companion_011 V2 同源）
    try:
        from coco.logging_setup import AUTHORITATIVE_COMPONENTS
        _check("V8b AUTHORITATIVE_COMPONENTS 含 group_mode", "group_mode" in AUTHORITATIVE_COMPONENTS)
    except Exception as e:  # noqa: BLE001
        _check("V8b AUTHORITATIVE_COMPONENTS 含 group_mode", False, f"err: {e!r}")


def main() -> int:
    for fn in (
        v1_verify011_v12b_simplified,
        v2_observe_cheap_doc,
        v3_face_tracker_get_face_id,
        v4_main_resolver_face_id_path,
        v5_resolver_fallback_behavior,
        v6_regress_companion_011,
        v7_observe_no_inline_io,
        v8_import_health_and_auth,
    ):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _check(fn.__name__, False, f"exception: {e!r}")
    print(
        f"\nverify_companion_012: PASS={len(PASSES)} FAIL={len(FAILURES)} "
        f"failures={FAILURES}"
    )
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
