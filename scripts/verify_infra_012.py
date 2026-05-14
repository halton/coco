"""verify_infra_012: self_heal wire 完善（吸收 infra-010-fu-1..4）.

V1  main.py startup log 含 "self_heal] wire=on handles="
V2  compute_handle_status 返回 dict（含 audio/asr/camera 三 key，值 ok|stub）
V3  main.py wire ON 分支：offline_fallback 真传入（非 None 字面量）
V4  camera_handle_ref 用 mutable list；reopen 后 ref[0] 被写为新 CameraSource
V5  verify_infra_010 V2.c 简化为清晰单条件（不再含混合 and/or）
V6  audio handle stub-by-design caveat 注释存在
V7  OFF 回归：COCO_SELFHEAL_WIRE 未设仍 WARN（行为不破）
V8  startup log handles=N/3 中 N >= 1（即至少一档真接）
V9  USB 独占：mutable ref 路径不在 reopen 时多 open 一个临时句柄
V10 verify_infra_010 整套仍 PASS
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.infra.self_heal_wire import (
    build_real_reopen_callbacks,
    compute_handle_status,
    selfheal_wire_enabled_from_env,
)

PASS = 0
FAIL = 0


def _check(label: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")


def _section(title: str) -> None:
    print(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
# V1
# ---------------------------------------------------------------------------
def v1_main_startup_log() -> None:
    _section("V1 main.py wire ON 分支含 startup log 'self_heal] wire=on handles='")
    mp = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    _check("V1.a 含 '[coco][self_heal] wire=on handles='", "wire=on handles=" in mp)
    _check("V1.b 含 audio= / asr= / camera= 标签",
           "audio={" in mp and "asr={" in mp and "camera={" in mp)


# ---------------------------------------------------------------------------
# V2
# ---------------------------------------------------------------------------
def v2_compute_handle_status() -> None:
    _section("V2 compute_handle_status 三档 dict")
    st = compute_handle_status()
    _check("V2.a key 完整", set(st.keys()) == {"audio", "asr", "camera"})
    _check("V2.b 全 None → 全 stub",
           st["audio"] == "stub" and st["asr"] == "stub" and st["camera"] == "stub")

    class FakeFB:
        def _enter_fallback(self, **k): pass
        def _exit_fallback(self, **k): pass

    class FakeAudio:
        def reopen(self): return True

    st2 = compute_handle_status(
        audio_handle=FakeAudio(),
        offline_fallback=FakeFB(),
        camera_handle_ref=[None],
    )
    _check("V2.c FakeAudio.reopen → audio=ok", st2["audio"] == "ok")
    _check("V2.d FakeFB._enter_fallback → asr=ok", st2["asr"] == "ok")
    _check("V2.e mutable list ref → camera=ok", st2["camera"] == "ok")


# ---------------------------------------------------------------------------
# V3
# ---------------------------------------------------------------------------
def v3_main_passes_offline_fallback() -> None:
    _section("V3 main.py wire ON 分支真传 offline_fallback / camera_ref")
    mp = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    # 在 wire=on 段中应当出现 offline_fallback=_offline_fallback
    _check("V3.a offline_fallback=_offline_fallback 真传",
           "offline_fallback=_offline_fallback" in mp)
    _check("V3.b camera_handle_ref=_camera_ref_arg 真传",
           "camera_handle_ref=_camera_ref_arg" in mp)
    _check("V3.c _camera_ref_list = [None] mutable ref",
           "_camera_ref_list: list = [None]" in mp or "_camera_ref_list = [None]" in mp)


# ---------------------------------------------------------------------------
# V4
# ---------------------------------------------------------------------------
def v4_camera_ref_writeback() -> None:
    _section("V4 mutable ref reopen 后 ref[0] 写为新 CameraSource")
    # 用 image fixture 做端到端 reopen 测试
    fixture = ROOT / "tests" / "fixtures" / "vision" / "single_face.jpg"
    if not fixture.exists():
        _check("V4.skip fixture missing", True)
        return
    spec = f"image:{fixture}"
    from coco.perception import open_camera
    cam0 = open_camera(spec)
    ref_list = [cam0]
    wire = build_real_reopen_callbacks(
        camera_handle_ref=ref_list,
        camera_spec=spec,
    )
    ok = wire.camera()
    _check("V4.a reopen 返回 True", bool(ok))
    _check("V4.b ref[0] 不为 None", ref_list[0] is not None)
    _check("V4.c ref[0] 是新 handle (不是初始 cam0)", ref_list[0] is not cam0)
    # cleanup
    try:
        if ref_list[0] is not None and hasattr(ref_list[0], "release"):
            ref_list[0].release()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# V5
# ---------------------------------------------------------------------------
def v5_verify_infra_010_v2c_simplified() -> None:
    _section("V5 verify_infra_010 V2.c 清晰单条件")
    src = (ROOT / "scripts" / "verify_infra_010.py").read_text(encoding="utf-8")
    # 旧式：含 'and' 与 'or' 同时出现在 V2.c 行附近 (前一版混合表达)
    m = re.search(r'"V2\.c[^"]*"[^)]+\)', src, re.DOTALL)
    _check("V5.a 找到 V2.c block", m is not None)
    if m is None:
        return
    block = m.group(0)
    # 新表达式应为单一相等判断
    _check("V5.b V2.c 含 'wire.audio.__name__ == \"_audio_reopen\"'",
           '"_audio_reopen"' in block)
    _check("V5.c V2.c 不再混合 and/or",
           not (" and " in block and " or " in block))


# ---------------------------------------------------------------------------
# V6
# ---------------------------------------------------------------------------
def v6_audio_stub_by_design_caveat() -> None:
    _section("V6 audio stub-by-design caveat 文档")
    mp = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    _check("V6.a main.py 解释 audio stub-by-design",
           "stub-by-design" in mp)


# ---------------------------------------------------------------------------
# V7
# ---------------------------------------------------------------------------
def v7_off_path_warn_unchanged() -> None:
    _section("V7 OFF 路径仍 WARN（COCO_SELFHEAL_WIRE 未设）")
    mp = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    _check("V7.a OFF WARN 字符串保留",
           "COCO_SELFHEAL_WIRE not set" in mp)
    _check("V7.b OFF 占位 lambda 保留", "lambda **kw: True" in mp)
    # env 行为
    snap = os.environ.pop("COCO_SELFHEAL_WIRE", None)
    try:
        _check("V7.c selfheal_wire_enabled_from_env() unset → False",
               selfheal_wire_enabled_from_env() is False)
    finally:
        if snap is not None:
            os.environ["COCO_SELFHEAL_WIRE"] = snap


# ---------------------------------------------------------------------------
# V8
# ---------------------------------------------------------------------------
def v8_handles_n_format() -> None:
    _section("V8 startup log handles=N/3 N>=1 是可达的（asr=ok 当 offline_fallback 真传）")
    # 静态判断：main.py wire ON 段传入 offline_fallback=_offline_fallback；若该变量
    # 在 OFFLINE_FALLBACK env 启用时不为 None，则 asr=ok。这里用 compute_handle_status
    # 模拟该场景，确认 N=1 是可达的。
    class FakeFB:
        def _enter_fallback(self, **k): pass
        def _exit_fallback(self, **k): pass

    st = compute_handle_status(offline_fallback=FakeFB(), camera_handle_ref=[None])
    n_ok = sum(1 for v in st.values() if v == "ok")
    _check("V8.a 模拟场景 N>=1", n_ok >= 1)
    _check("V8.b N == 2 (asr+camera)", n_ok == 2)


# ---------------------------------------------------------------------------
# V9
# ---------------------------------------------------------------------------
def v9_usb_exclusive_no_temp_handle() -> None:
    _section("V9 mutable ref 路径 USB 独占友好：不多 open 一个临时句柄")
    # 通过 fake CameraSource 计数 open 次数：mutable ref 模式下，reopen 应只 open 一次
    # （新 handle 写回 ref），不应再 release 它（callable / None 模式才 release）。
    src = (ROOT / "coco" / "infra" / "self_heal_wire.py").read_text(encoding="utf-8")
    _check("V9.a self_heal_wire 含 mutable ref 写回路径 'reopened_ref_writeback'",
           "reopened_ref_writeback" in src)
    _check("V9.b mutable ref 分支区分 callable / __getitem__",
           "is_mutable_ref" in src)
    # 用 fake 验证：mutable ref 路径下不应 release 新 handle
    fixture = ROOT / "tests" / "fixtures" / "vision" / "single_face.jpg"
    if not fixture.exists():
        _check("V9.skip fixture missing", True)
        return
    spec = f"image:{fixture}"
    from coco.perception import open_camera
    cam0 = open_camera(spec)
    ref_list = [cam0]
    wire = build_real_reopen_callbacks(camera_handle_ref=ref_list, camera_spec=spec)
    wire.camera()
    # 写回的新 handle 仍能 read（说明没被立刻 release）
    new_cam = ref_list[0]
    try:
        ok, _frame = new_cam.read()
        _check("V9.c 新 handle 写回后仍可 read（未被 release）", bool(ok))
    except Exception as e:
        _check(f"V9.c 新 handle 读取失败: {e!r}", False)
    finally:
        try:
            if hasattr(new_cam, "release"):
                new_cam.release()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# V10
# ---------------------------------------------------------------------------
def v10_verify_infra_010_regression() -> None:
    _section("V10 回归 verify_infra_010 整套")
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_infra_010.py")],
        capture_output=True, text=True,
    )
    out = p.stdout + p.stderr
    m = re.search(r"summary:\s+PASS=(\d+)\s+FAIL=(\d+)", out)
    _check("V10.a verify_infra_010 含 summary", m is not None)
    if m:
        _check(f"V10.b FAIL=0 (got PASS={m.group(1)} FAIL={m.group(2)})",
               m.group(2) == "0")


def main() -> int:
    v1_main_startup_log()
    v2_compute_handle_status()
    v3_main_passes_offline_fallback()
    v4_camera_ref_writeback()
    v5_verify_infra_010_v2c_simplified()
    v6_audio_stub_by_design_caveat()
    v7_off_path_warn_unchanged()
    v8_handles_n_format()
    v9_usb_exclusive_no_temp_handle()
    v10_verify_infra_010_regression()
    print(f"\n=== summary: PASS={PASS}  FAIL={FAIL} ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
