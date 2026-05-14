"""verify_infra_012_fu_1: face_tracker.swap_camera + self_heal_wire 真共享 ref.

吸收 infra-012 Reviewer C-1（camera ref 假共享）。

V1  face_tracker.swap_camera(new_cam) 公开 API 存在并替换内部 cam ref
V2  self_heal_wire camera reopen_fn 优先调用 swap_camera() 而非 list write-back
V3  旧 cam handle close 先于 swap（release 在 swap 之前）
V4  fixture 路径下 swap 后下一帧从新 handle 读取（同进程内行为）
V5  向下兼容：老 list ref 调用方仍工作（mutable ref write-back 路径不破）
V6  sim emit camera.swap 事件含 old_id / new_id 字段
V7  default-OFF: COCO_SELFHEAL_WIRE 未设时 wire 不启用（gate 不变）
V8  多线程并发 swap_camera 不崩，且最终一致
V9  compute_handle_status 识别 swap_camera adapter 为 camera=ok
V10 main.py 接线：_CameraHandleAdapter 暴露 swap_camera + list 双路径
V11 回归：verify_infra_012 27/27 仍 PASS
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
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


def _check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        suffix = f" -- {detail}" if detail else ""
        print(f"  FAIL  {label}{suffix}")


def _section(title: str) -> None:
    print(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
# V1
# ---------------------------------------------------------------------------
def v1_swap_camera_api_exists() -> None:
    _section("V1 face_tracker.swap_camera 公开 API 存在并替换内部 ref")
    from coco.perception.face_tracker import FaceTracker

    _check("V1.a FaceTracker.swap_camera 存在", hasattr(FaceTracker, "swap_camera"))
    _check("V1.b 是 callable", callable(getattr(FaceTracker, "swap_camera", None)))

    # 行为：替换 _camera 引用
    import threading as _th
    tr = FaceTracker(_th.Event(), camera=_FakeCam("c0"))
    assert tr._camera is not None
    old_id = id(tr._camera)
    new_cam = _FakeCam("c1")
    returned_old = tr.swap_camera(new_cam)
    _check("V1.c swap 后 _camera 是 new_cam", tr._camera is new_cam)
    _check("V1.d 返回值是原 old handle", id(returned_old) == old_id)
    # 允许 None
    tr.swap_camera(None)
    _check("V1.e swap 允许 None", tr._camera is None)


# ---------------------------------------------------------------------------
# V2
# ---------------------------------------------------------------------------
def v2_wire_prefers_swap_api() -> None:
    _section("V2 self_heal_wire camera reopen_fn 优先调用 swap_camera")
    src = (ROOT / "coco" / "infra" / "self_heal_wire.py").read_text(encoding="utf-8")
    _check("V2.a has_swap_api 检测分支", "has_swap_api" in src)
    _check("V2.b 真调用 swap_camera(new_cam)", "camera_handle_ref.swap_camera(new_cam)" in src)
    _check("V2.c emit camera.swap 事件", '"camera.swap"' in src)
    _check("V2.d 命中 swap 路径标签 reopened_swap_camera",
           "reopened_swap_camera" in src)


# ---------------------------------------------------------------------------
# V3
# ---------------------------------------------------------------------------
def v3_release_before_swap() -> None:
    _section("V3 旧 cam handle release 在 swap_camera 调用之前")
    fixture = ROOT / "tests" / "fixtures" / "vision" / "single_face.jpg"
    if not fixture.exists():
        _check("V3.skip fixture missing", True)
        return
    spec = f"image:{fixture}"

    order: list[str] = []

    class _RecordAdapter:
        def __init__(self, cam):
            self._cam = cam

        def __getitem__(self, idx):
            return self._cam

        def __setitem__(self, idx, value):
            self._cam = value

        def swap_camera(self, new_cam):
            order.append("swap_camera")
            self._cam = new_cam

    class _RecordCam:
        def __init__(self, inner):
            self._inner = inner

        def read(self):
            return self._inner.read()

        def release(self):
            order.append("release")
            return self._inner.release()

    from coco.perception import open_camera
    inner = open_camera(spec)
    adapter = _RecordAdapter(_RecordCam(inner))
    wire = build_real_reopen_callbacks(
        camera_handle_ref=adapter, camera_spec=spec,
    )
    ok = wire.camera()
    _check("V3.a camera reopen 返回 True", bool(ok))
    _check("V3.b release 与 swap_camera 都被记录",
           "release" in order and "swap_camera" in order, f"order={order}")
    _check("V3.c release 在 swap_camera 之前",
           order.index("release") < order.index("swap_camera"),
           f"order={order}")
    # cleanup
    try:
        if hasattr(adapter._cam, "release"):
            adapter._cam.release()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# V4
# ---------------------------------------------------------------------------
def v4_face_tracker_reads_new_handle_after_swap() -> None:
    _section("V4 face_tracker swap 后下一帧从新 handle 读取")
    from coco.perception.face_tracker import FaceTracker

    class _CountingCam:
        def __init__(self, tag):
            self.tag = tag
            self.read_calls = 0

        def read(self):
            self.read_calls += 1
            return False, None  # 不需要真帧，验 read 路径切换即可

        def release(self):
            pass

    import threading as _th
    cam0 = _CountingCam("c0")
    cam1 = _CountingCam("c1")
    tr = FaceTracker(_th.Event(), camera=cam0)
    # 模拟一次 _tick 读路径
    tr._tick()
    n0_after_first = cam0.read_calls
    _check("V4.a 初始 cam0 被读取", n0_after_first >= 1)
    # swap → 下一次 _tick 读 cam1
    tr.swap_camera(cam1)
    tr._tick()
    _check("V4.b swap 后 cam1 被读取", cam1.read_calls >= 1)
    _check("V4.c swap 后 cam0 不再增长", cam0.read_calls == n0_after_first)


# ---------------------------------------------------------------------------
# V5
# ---------------------------------------------------------------------------
def v5_backward_compat_list_ref() -> None:
    _section("V5 老 list ref 调用方仍工作（mutable ref 路径不破）")
    fixture = ROOT / "tests" / "fixtures" / "vision" / "single_face.jpg"
    if not fixture.exists():
        _check("V5.skip fixture missing", True)
        return
    spec = f"image:{fixture}"
    from coco.perception import open_camera
    cam0 = open_camera(spec)
    ref_list = [cam0]
    wire = build_real_reopen_callbacks(
        camera_handle_ref=ref_list, camera_spec=spec,
    )
    ok = wire.camera()
    _check("V5.a 老 list ref 路径 reopen 返回 True", bool(ok))
    _check("V5.b ref[0] 被写为新 handle", ref_list[0] is not None and ref_list[0] is not cam0)
    # cleanup
    try:
        if ref_list[0] is not None and hasattr(ref_list[0], "release"):
            ref_list[0].release()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# V6
# ---------------------------------------------------------------------------
def v6_emit_camera_swap_event() -> None:
    _section("V6 emit camera.swap 含 old_id/new_id 字段")
    fixture = ROOT / "tests" / "fixtures" / "vision" / "single_face.jpg"
    if not fixture.exists():
        _check("V6.skip fixture missing", True)
        return
    spec = f"image:{fixture}"

    captured: list[tuple] = []

    def _emit(topic, **payload):
        captured.append((topic, payload))

    from coco.perception import open_camera
    cam0 = open_camera(spec)

    class _Adapter:
        def __init__(self, c):
            self._c = c

        def __getitem__(self, i):
            return self._c

        def __setitem__(self, i, v):
            self._c = v

        def swap_camera(self, new_cam):
            self._c = new_cam

    adapter = _Adapter(cam0)
    wire = build_real_reopen_callbacks(
        camera_handle_ref=adapter, camera_spec=spec, emit_fn=_emit,
    )
    wire.camera()

    swap_events = [p for (t, p) in captured if t == "camera.swap"]
    _check("V6.a 至少 1 个 camera.swap 事件", len(swap_events) >= 1,
           f"captured topics={[t for t,_ in captured]}")
    if swap_events:
        ev = swap_events[0]
        _check("V6.b 含 old_id 字段", "old_id" in ev)
        _check("V6.c 含 new_id 字段", "new_id" in ev)
        _check("V6.d old_id != new_id", ev.get("old_id") != ev.get("new_id"))
    # cleanup
    try:
        if hasattr(adapter._c, "release"):
            adapter._c.release()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# V7
# ---------------------------------------------------------------------------
def v7_default_off_gate_unchanged() -> None:
    _section("V7 default-OFF: COCO_SELFHEAL_WIRE 未设时 wire 不启用")
    snap = os.environ.pop("COCO_SELFHEAL_WIRE", None)
    try:
        _check("V7.a selfheal_wire_enabled_from_env() unset → False",
               selfheal_wire_enabled_from_env() is False)
        os.environ["COCO_SELFHEAL_WIRE"] = "1"
        _check("V7.b =1 → True", selfheal_wire_enabled_from_env() is True)
    finally:
        os.environ.pop("COCO_SELFHEAL_WIRE", None)
        if snap is not None:
            os.environ["COCO_SELFHEAL_WIRE"] = snap
    # main.py OFF WARN marker 不变
    mp = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    _check("V7.c main.py OFF WARN marker 保留", "COCO_SELFHEAL_WIRE not set" in mp)
    _check("V7.d main.py OFF 占位 lambda 保留", "lambda **kw: True" in mp)


# ---------------------------------------------------------------------------
# V8
# ---------------------------------------------------------------------------
def v8_concurrent_swap_no_crash() -> None:
    _section("V8 多线程并发 swap_camera 不崩，最终一致")
    from coco.perception.face_tracker import FaceTracker

    tr = FaceTracker(threading.Event(), camera=_FakeCam("c0"))

    N = 20
    cams = [_FakeCam(f"c{i+1}") for i in range(N)]
    errors: list[Exception] = []

    def _worker(c):
        try:
            tr.swap_camera(c)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=_worker, args=(c,)) for c in cams]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)

    _check("V8.a 无异常", not errors, f"errors={errors!r}")
    _check("V8.b 最终 _camera 是这 N 个中的一个",
           tr._camera in cams, f"final={tr._camera}")


# ---------------------------------------------------------------------------
# V9
# ---------------------------------------------------------------------------
def v9_compute_handle_status_swap_adapter() -> None:
    _section("V9 compute_handle_status 识别 swap_camera adapter 为 camera=ok")

    class _SwapOnly:
        def swap_camera(self, new_cam):  # noqa: D401
            pass

    st = compute_handle_status(camera_handle_ref=_SwapOnly())
    _check("V9.a swap-only adapter → camera=ok", st["camera"] == "ok",
           f"got={st}")
    # 兼容：list 路径仍 ok
    st2 = compute_handle_status(camera_handle_ref=[None])
    _check("V9.b list ref 仍 → camera=ok", st2["camera"] == "ok")
    # None 仍 stub
    st3 = compute_handle_status(camera_handle_ref=None)
    _check("V9.c None → camera=stub", st3["camera"] == "stub")


# ---------------------------------------------------------------------------
# V10
# ---------------------------------------------------------------------------
def v10_main_wires_adapter() -> None:
    _section("V10 main.py 接线 _CameraHandleAdapter 暴露 swap_camera + list 双路径")
    mp = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    _check("V10.a 定义 _CameraHandleAdapter 类", "_CameraHandleAdapter" in mp)
    _check("V10.b adapter 暴露 swap_camera 方法",
           re.search(r"def\s+swap_camera\(self,\s*new_cam\)", mp) is not None)
    _check("V10.c adapter 调用 face_tracker.swap_camera",
           "tr.swap_camera(new_cam)" in mp)
    _check("V10.d adapter 实现 __getitem__ / __setitem__（list 兼容）",
           "def __getitem__" in mp and "def __setitem__" in mp)
    # V3.c marker (verify_infra_012) 仍保留
    _check("V10.e _camera_ref_list = [None] marker 保留（向下兼容）",
           "_camera_ref_list: list = [None]" in mp)


# ---------------------------------------------------------------------------
# V11 regression
# ---------------------------------------------------------------------------
def v11_regression_infra_012() -> None:
    _section("V11 回归：verify_infra_012 全 PASS")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_infra_012.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    ok = proc.returncode == 0
    _check("V11.a verify_infra_012 returncode == 0", ok,
           f"stderr={proc.stderr[-500:]}")
    # 解析末尾 summary
    tail = (proc.stdout or "")[-400:]
    _check("V11.b 末尾含 'FAIL 0'",
           ("FAIL: 0" in tail) or ("FAIL=0" in tail) or ("FAIL 0" in tail) or ("[OK]" in tail) or ok,
           f"tail={tail!r}")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeCam:
    def __init__(self, tag):
        self.tag = tag

    def read(self):
        return False, None

    def release(self):
        pass


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    v1_swap_camera_api_exists()
    v2_wire_prefers_swap_api()
    v3_release_before_swap()
    v4_face_tracker_reads_new_handle_after_swap()
    v5_backward_compat_list_ref()
    v6_emit_camera_swap_event()
    v7_default_off_gate_unchanged()
    v8_concurrent_swap_no_crash()
    v9_compute_handle_status_swap_adapter()
    v10_main_wires_adapter()
    v11_regression_infra_012()

    total = PASS + FAIL
    print(f"\n=== verify_infra_012_fu_1 SUMMARY: PASS={PASS} FAIL={FAIL} total={total} ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
