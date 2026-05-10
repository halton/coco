"""spike + verify：image / video / usb 三档源各跑 5 秒，打印 shape 与帧率。

PASS 条件（sub-agent 可在主机执行）：
- image:<jpg> ≥ 4.5 秒内，得到 ≥ 130 帧（30fps 节流，理论 150 帧），shape (240,320,3)
- video:<mp4> ≥ 4.5 秒内，得到 ≥ 60 帧（15fps 节流，理论 75 帧），shape (240,320,3)
- usb:<idx> 可选：能打开则采 5s 报告 fps；打不开则 SKIP（不影响 verify 整体 PASS）
- 解析 COCO_CAMERA 错误情况能抛 ValueError
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

from coco.perception.camera_source import (  # noqa: E402
    ImageLoopSource,
    UsbCameraSource,
    VideoFileSource,
    open_camera,
    parse_camera_env,
)

FIX = REPO / "tests" / "fixtures" / "vision"
DUR = 5.0


def run_source(label: str, src, expected_shape=(240, 320, 3)):
    t0 = time.monotonic()
    n = 0
    last_shape = None
    last_dtype = None
    while time.monotonic() - t0 < DUR:
        ok, frame = src.read()
        if not ok or frame is None:
            print(f"  [{label}] read returned False/None at frame={n} dt={time.monotonic()-t0:.2f}s")
            break
        n += 1
        last_shape = frame.shape
        last_dtype = frame.dtype
    elapsed = time.monotonic() - t0
    fps = n / elapsed if elapsed > 0 else 0.0
    print(f"  [{label}] frames={n} elapsed={elapsed:.2f}s fps={fps:.2f} shape={last_shape} dtype={last_dtype}")
    src.release()
    return n, elapsed, last_shape, fps


def check_parse():
    print("== parse_camera_env ==")
    cases = [
        (None, "usb", "0"),
        ("", "usb", "0"),
        ("usb:0", "usb", "0"),
        ("usb:2", "usb", "2"),
        ("image:/tmp/x.jpg", "image", "/tmp/x.jpg"),
        ("video:/tmp/y.mp4", "video", "/tmp/y.mp4"),
    ]
    for spec, exp_kind, exp_target in cases:
        got = parse_camera_env(spec)
        assert got.kind == exp_kind and got.target == exp_target, f"{spec!r} -> {got}"
        print(f"  ok: {spec!r:32s} -> kind={got.kind} target={got.target}")
    bad_cases = ["foo", "image:", "junk:1", "usb:abc"]
    for spec in bad_cases:
        try:
            parse_camera_env(spec)
        except ValueError as e:
            print(f"  ok (raised): {spec!r} -> {e}")
        else:
            raise AssertionError(f"expected ValueError for {spec!r}")


def main() -> int:
    print(f"REPO: {REPO}")
    check_parse()

    # A 档：image
    print("== A 档：ImageLoopSource ==")
    img_path = FIX / "single_face.jpg"
    src = ImageLoopSource(img_path, fps=30.0)
    n_img, dt_img, shape_img, fps_img = run_source("image", src)
    assert shape_img == (240, 320, 3), f"image shape {shape_img}"
    assert n_img >= 130, f"image frames too few: {n_img}"

    # B/C 档：video
    print("== B/C 档：VideoFileSource ==")
    vid_path = FIX / "user_walks_away.mp4"
    src = VideoFileSource(vid_path)
    n_vid, dt_vid, shape_vid, fps_vid = run_source("video", src)
    assert shape_vid == (240, 320, 3), f"video shape {shape_vid}"
    assert n_vid >= 60, f"video frames too few: {n_vid}"

    # 工厂 + COCO_CAMERA
    print("== 工厂 open_camera via COCO_CAMERA ==")
    os.environ["COCO_CAMERA"] = f"image:{img_path}"
    with open_camera() as src:
        ok, frame = src.read()
        assert ok and frame is not None and frame.shape == (240, 320, 3)
        print(f"  ok: env image -> shape={frame.shape}")
    os.environ["COCO_CAMERA"] = f"video:{vid_path}"
    with open_camera() as src:
        ok, frame = src.read()
        assert ok and frame is not None and frame.shape == (240, 320, 3)
        print(f"  ok: env video -> shape={frame.shape}")
    os.environ.pop("COCO_CAMERA", None)

    # 真机：usb:0 best-effort
    print("== 真机 UsbCameraSource (best-effort, SKIP if 不可用) ==")
    try:
        src = UsbCameraSource(0)
    except RuntimeError as e:
        print(f"  SKIP usb:0 ({e})")
        n_usb = -1
    else:
        n_usb, dt_usb, shape_usb, fps_usb = run_source("usb", src, expected_shape=None)
        if n_usb < 5:
            print(f"  WARN usb:0 frames too few ({n_usb}) — 设备可能受限")

    print()
    print("== summary ==")
    print(f"  image  PASS  frames={n_img}  fps={fps_img:.2f}")
    print(f"  video  PASS  frames={n_vid}  fps={fps_vid:.2f}")
    if n_usb < 0:
        print(f"  usb    SKIP  (device not available — sub-agent 可接受)")
    else:
        print(f"  usb    OK    frames={n_usb}")
    print("ALL PASS (image + video required; usb optional)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
