"""scripts/enroll_face.py — vision-003 注册新人脸 CLI.

用法
====

A. 从 image 文件注册（开发 / fixture）：

    python scripts/enroll_face.py --name alice --image a1.jpg --image a2.jpg

B. 从默认摄像头抓 N 帧注册（真机 milestone gate 用）：

    python scripts/enroll_face.py --name alice --from-camera 10

设计
====

- name 必填；至少一个 --image 或 --from-camera N。
- 自动用 FaceDetector 找最大脸 → crop → 调 FaceIDClassifier.enroll。
- 路径默认 ``~/.cache/coco/face_id/``；``COCO_FACE_ID_PATH`` 覆盖。
- 干净退出：argparse / 缺脸 / 摄像头打不开等场景给非零退出码 + 中文错误。
- 显式同意提示：CLI 启动时打印 PII 警告，要求用户确认（stdin tty）；
  ``--yes`` 跳过（脚本化 / verification）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.perception.face_detect import FaceDetector
from coco.perception.face_id import FaceIDClassifier, FaceIDStore, default_store_path


def _crop_largest_face(img: np.ndarray, detector: FaceDetector) -> Optional[np.ndarray]:
    boxes = detector.detect(img)
    if not boxes:
        return None
    best = max(boxes, key=lambda b: b.w * b.h)
    x1, y1 = max(0, int(best.x)), max(0, int(best.y))
    x2, y2 = min(img.shape[1], int(best.x + best.w)), min(img.shape[0], int(best.y + best.h))
    if x2 <= x1 or y2 <= y1:
        return None
    return img[y1:y2, x1:x2]


def _consent_prompt(yes: bool) -> bool:
    if yes:
        return True
    print(
        "[enroll_face] 注意：本工具会把人脸特征落到本地磁盘\n"
        f"            (默认 {default_store_path()})。仅本人 (chmod 0o600) 可读。\n"
        "            继续? (y/N): ",
        end="",
        flush=True,
    )
    try:
        ans = input().strip().lower()
    except EOFError:
        return False
    return ans in {"y", "yes"}


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="vision-003: 注册新人脸到 face_id store")
    p.add_argument("--name", required=True, help="user 显示名（如 alice）")
    p.add_argument("--image", action="append", default=[], help="图片路径，可重复")
    p.add_argument("--from-camera", type=int, default=0, metavar="N",
                   help="从默认摄像头抓 N 帧（>0 启用）")
    p.add_argument("--camera-index", type=int, default=0, help="USB 摄像头 index")
    p.add_argument("--store-path", default="", help="覆盖 store 目录（默认 ~/.cache/coco/face_id/）")
    p.add_argument("--threshold", type=float, default=None, help="confidence 阈值（默认按 backend）")
    p.add_argument("--backend", choices=["auto", "lbph", "histogram"], default="auto")
    p.add_argument("--yes", action="store_true", help="跳过 PII 同意提示（脚本化用）")

    args = p.parse_args(argv)
    name = args.name.strip()
    if not name:
        print("[enroll_face] FAIL: --name 不能为空", file=sys.stderr)
        return 2
    if not args.image and args.from_camera <= 0:
        print("[enroll_face] FAIL: 至少 --image 或 --from-camera N", file=sys.stderr)
        return 2
    if not _consent_prompt(args.yes):
        print("[enroll_face] 用户拒绝 PII 同意，退出", file=sys.stderr)
        return 3

    store = FaceIDStore(Path(args.store_path) if args.store_path else None)
    clf = FaceIDClassifier(store=store, threshold=args.threshold, backend_pref=args.backend)
    detector = FaceDetector()

    crops: List[np.ndarray] = []
    for img_path in args.image:
        img = cv2.imread(img_path)
        if img is None:
            print(f"[enroll_face] WARN: 读不到 {img_path}", file=sys.stderr)
            continue
        crop = _crop_largest_face(img, detector)
        if crop is None:
            # 整图直接当 face crop（fixture 是已裁好的小图）
            crop = img
        crops.append(crop)

    if args.from_camera > 0:
        cap = cv2.VideoCapture(args.camera_index)
        if not cap.isOpened():
            print(f"[enroll_face] FAIL: 摄像头 index={args.camera_index} 打不开", file=sys.stderr)
            return 4
        try:
            captured = 0
            while captured < args.from_camera:
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.05)
                    continue
                crop = _crop_largest_face(frame, detector)
                if crop is None:
                    continue
                crops.append(crop)
                captured += 1
                print(f"  ok: 抓到 {captured}/{args.from_camera}", flush=True)
                time.sleep(0.2)
        finally:
            cap.release()

    if not crops:
        print("[enroll_face] FAIL: 没有可用的人脸 crop", file=sys.stderr)
        return 5

    uid = clf.enroll(name, crops)
    print(f"[enroll_face] OK: name={name!r} user_id={uid} samples={len(crops)} "
          f"backend={clf.backend_name} store={store.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
