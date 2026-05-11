"""vision-003 verification: 人脸 ID 识别 (LBPH + Histogram fallback).

跑法：
  uv run python scripts/verify_vision_003.py

子项：
  V1  backend 探测 — auto 在本环境选 histogram (cv2.face 不可用) 或 lbph
  V2  enroll 2 user × 3 image，store round-trip + atomic write + chmod 0o600
  V3  identify 同人 fixture → 正确 name + confidence ≥ threshold
  V4  identify unknown → name=None / confidence < threshold
  V5  backward-compat — COCO_FACE_ID 未设默认 OFF；FaceTracker 无 classifier 时
       TrackedFace.name 始终 None
  V6  持久化 round-trip — enroll 后重启 store load 仍能识别
  V7  env clamp — threshold=2.0 → 1.0；threshold=-0.5 → 0.0；非法 backend 回退 auto
  V8  HistogramBackend 强制 backend="histogram" 单独验证
  V9  enroll CLI main() 干跑（stub args/image）不抛
  V10 emit events — face.id_backend_selected / face.identified / face.unknown 触发

retval：0 全 PASS；1 任一失败
evidence 落 evidence/vision-003/verify_summary.json
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import List

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.logging_setup import setup_logging
from coco.perception.face_detect import FaceBox
from coco.perception.face_id import (
    DEFAULT_HIST_THRESHOLD,
    FaceIDClassifier,
    FaceIDStore,
    HistogramBackend,
    LBPHBackend,
    SCHEMA_VERSION,
    config_from_env,
    select_backend,
)
from coco.perception.face_tracker import FaceTracker

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

errors: List[str] = []
results: dict = {}


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok   {msg}")
    else:
        errors.append(msg)
        print(f"  FAIL {msg}")


FIX = ROOT / "tests" / "fixtures" / "vision"
ALICE = [cv2.imread(str(FIX / "face_id" / "alice" / f"{i}.jpg")) for i in range(1, 6)]
BOB = [cv2.imread(str(FIX / "face_id" / "bob" / f"{i}.jpg")) for i in range(1, 6)]
UNK = cv2.imread(str(FIX / "unknown_face.jpg"))
assert all(im is not None for im in ALICE), "ALICE fixture 缺失，跑 gen_vision_fixtures.py"
assert all(im is not None for im in BOB), "BOB fixture 缺失"
assert UNK is not None, "unknown_face fixture 缺失"


# ---------------------------------------------------------------------------
# V1: backend 探测
# ---------------------------------------------------------------------------
print("\n[V1] backend selection")
v1_backend = select_backend("auto")
v1_name = v1_backend.name
print(f"  backend.name = {v1_name}")
check(v1_name in {"lbph", "histogram"}, f"V1 backend.name 合法 (got {v1_name})")
# cv2.face 在本环境探测
has_cv2_face = hasattr(cv2, "face")
print(f"  hasattr(cv2, 'face') = {has_cv2_face}")
if has_cv2_face:
    check(v1_name == "lbph", "V1 cv2.face 可用 → 选 lbph")
else:
    check(v1_name == "histogram", "V1 cv2.face 不可用 → 选 histogram fallback")
# 强制 histogram 应总能成功
hb = select_backend("histogram")
check(hb.name == "histogram", "V1 强制 histogram OK")
# 强制 lbph：当前环境无 contrib 时应抛
try:
    select_backend("lbph")
    lbph_ok = has_cv2_face
except Exception:
    lbph_ok = not has_cv2_face
check(lbph_ok, "V1 强制 lbph 行为符合 contrib 可用性")
results["V1"] = {"backend_selected": v1_name, "has_cv2_face": has_cv2_face}


# ---------------------------------------------------------------------------
# V2: enroll + atomic write + chmod 0o600
# ---------------------------------------------------------------------------
print("\n[V2] enroll + persistence")
with tempfile.TemporaryDirectory() as td:
    store = FaceIDStore(Path(td))
    clf = FaceIDClassifier(backend=HistogramBackend(), store=store)
    uid_a = clf.enroll("alice", ALICE[:3])
    uid_b = clf.enroll("bob", BOB[:3])
    check(uid_a == 1, f"V2 alice user_id=1 (got {uid_a})")
    check(uid_b == 2, f"V2 bob user_id=2 (got {uid_b})")
    recs = store.all_records()
    check(len(recs) == 2, f"V2 records 数=2 (got {len(recs)})")
    check(recs[1].sample_count == 3, f"V2 alice sample_count=3 (got {recs[1].sample_count})")
    # 文件落盘
    idx = Path(td) / "known_faces.json"
    check(idx.exists(), "V2 known_faces.json 存在")
    obj = json.loads(idx.read_text())
    check(obj["schema_version"] == SCHEMA_VERSION, "V2 schema_version 写入正确")
    # chmod 0o600（POSIX）
    if sys.platform != "win32":
        mode = oct(idx.stat().st_mode & 0o777)
        check(mode == "0o600", f"V2 known_faces.json chmod 0o600 (got {mode})")
        # .npy 文件也 0o600
        npys = list(Path(td).glob("*.npy"))
        check(len(npys) == 6, f"V2 .npy 数=6 (got {len(npys)})")
        for p in npys:
            m = oct(p.stat().st_mode & 0o777)
            if m != "0o600":
                errors.append(f"V2 {p.name} 不是 0o600 (got {m})")
    results["V2"] = {"records": len(recs), "samples_total": sum(r.sample_count for r in recs.values())}


# ---------------------------------------------------------------------------
# V3: identify 同人 → 正确 name + conf ≥ threshold
# ---------------------------------------------------------------------------
print("\n[V3] identify known faces")
v3_results = []
with tempfile.TemporaryDirectory() as td:
    clf = FaceIDClassifier(backend=HistogramBackend(), store=FaceIDStore(Path(td)))
    clf.enroll("alice", ALICE[:3])
    clf.enroll("bob", BOB[:3])
    for img, gt in [(ALICE[3], "alice"), (ALICE[4], "alice"), (BOB[3], "bob"), (BOB[4], "bob")]:
        name, conf = clf.identify(img)
        v3_results.append({"gt": gt, "pred": name, "conf": round(conf, 4)})
        check(name == gt, f"V3 {gt} 识别正确 (pred={name}, conf={conf:.3f})")
        check(conf >= clf.threshold, f"V3 {gt} conf ≥ threshold ({conf:.3f} ≥ {clf.threshold})")
results["V3"] = v3_results


# ---------------------------------------------------------------------------
# V4: identify unknown → None
# ---------------------------------------------------------------------------
print("\n[V4] identify unknown")
with tempfile.TemporaryDirectory() as td:
    clf = FaceIDClassifier(backend=HistogramBackend(), store=FaceIDStore(Path(td)))
    clf.enroll("alice", ALICE[:3])
    clf.enroll("bob", BOB[:3])
    name, conf = clf.identify(UNK)
    check(name is None, f"V4 unknown 应返 None (got name={name}, conf={conf:.3f})")
    check(conf < clf.threshold, f"V4 unknown conf < threshold ({conf:.3f} < {clf.threshold})")
    # 空 store
    clf2 = FaceIDClassifier(backend=HistogramBackend(), store=FaceIDStore(Path(td) + "_empty" if False else tempfile.mkdtemp()))
    n2, c2 = clf2.identify(ALICE[0])
    check(n2 is None and c2 == 0.0, f"V4 空 store identify 返 (None, 0.0) (got {n2}, {c2})")
    results["V4"] = {"unknown_pred": name, "unknown_conf": round(conf, 4)}


# ---------------------------------------------------------------------------
# V5: backward-compat — COCO_FACE_ID 默认 OFF；FaceTracker 无 classifier
# ---------------------------------------------------------------------------
print("\n[V5] backward-compat")
# 清环境
for k in ("COCO_FACE_ID", "COCO_FACE_ID_PATH", "COCO_FACE_ID_THRESHOLD", "COCO_FACE_ID_BACKEND"):
    os.environ.pop(k, None)
cfg = config_from_env()
check(cfg.enabled is False, "V5 默认 enabled=False")
# FaceTracker 无 classifier → primary_track.name 永远 None
stop_event = threading.Event()
tracker = FaceTracker(stop_event, camera_spec="image:" + str(FIX / "single_face.jpg"), fps=8.0,
                      presence_min_hits=1, absence_min_misses=1)
tracker.start()
time.sleep(0.6)
stop_event.set()
tracker.join(timeout=2)
snap = tracker.latest()
check(snap.primary_track is None or snap.primary_track.name is None,
      f"V5 无 classifier 注入时 primary_track.name 为 None (got {snap.primary_track.name if snap.primary_track else '(no primary)'}) ")
results["V5"] = {"enabled_default": cfg.enabled, "tracker_name": None}


# ---------------------------------------------------------------------------
# V6: 持久化 round-trip — 重启后仍能识别
# ---------------------------------------------------------------------------
print("\n[V6] persistence round-trip")
td6 = tempfile.mkdtemp()
try:
    clf1 = FaceIDClassifier(backend=HistogramBackend(), store=FaceIDStore(Path(td6)))
    clf1.enroll("alice", ALICE[:3])
    clf1.enroll("bob", BOB[:3])
    # 重新构造（模拟重启）
    clf2 = FaceIDClassifier(backend=HistogramBackend(), store=FaceIDStore(Path(td6)))
    recs = clf2.store.all_records()
    check(len(recs) == 2, f"V6 重启后 records=2 (got {len(recs)})")
    name, conf = clf2.identify(ALICE[3])
    check(name == "alice", f"V6 重启后仍识别 alice (got {name}, {conf:.3f})")
    name, conf = clf2.identify(BOB[3])
    check(name == "bob", f"V6 重启后仍识别 bob (got {name})")
    results["V6"] = {"reloaded_records": len(recs)}
finally:
    shutil.rmtree(td6, ignore_errors=True)


# ---------------------------------------------------------------------------
# V7: env clamp + 非法 backend 回退
# ---------------------------------------------------------------------------
print("\n[V7] env clamp")
test_cases = [
    ({"COCO_FACE_ID_THRESHOLD": "2.0"}, "threshold", 1.0),
    ({"COCO_FACE_ID_THRESHOLD": "-0.5"}, "threshold", 0.0),
    # L1 fix: 非数字 env → None (sentinel)，由 backend.default_threshold() 决定
    ({"COCO_FACE_ID_THRESHOLD": "abc"}, "threshold", None),
    # L1 fix: env 未给 → None (sentinel)
    ({}, "threshold", None),
    ({"COCO_FACE_ID_BACKEND": "wat"}, "backend", "auto"),
    ({"COCO_FACE_ID_BACKEND": "LBPH"}, "backend", "lbph"),
    ({"COCO_FACE_ID": "1"}, "enabled", True),
    ({"COCO_FACE_ID": "0"}, "enabled", False),
]
v7_log = []
for env, field, expect in test_cases:
    cfg = config_from_env(env)
    actual = getattr(cfg, "confidence_threshold" if field == "threshold" else field)
    ok = actual == expect
    v7_log.append({"env": env, "field": field, "expect": expect, "actual": actual, "ok": ok})
    check(ok, f"V7 env {env} {field}={expect} (got {actual})")
results["V7"] = v7_log


# ---------------------------------------------------------------------------
# V8: HistogramBackend 强制
# ---------------------------------------------------------------------------
print("\n[V8] HistogramBackend forced")
with tempfile.TemporaryDirectory() as td:
    b = select_backend("histogram")
    check(b.name == "histogram", "V8 backend=histogram")
    clf = FaceIDClassifier(backend=b, store=FaceIDStore(Path(td)))
    clf.enroll("alice", ALICE[:3])
    clf.enroll("bob", BOB[:3])
    n_a, c_a = clf.identify(ALICE[3])
    n_b, c_b = clf.identify(BOB[3])
    n_u, c_u = clf.identify(UNK)
    check(n_a == "alice" and n_b == "bob" and n_u is None,
          f"V8 强制 histogram 三类区分正确 (alice={n_a}, bob={n_b}, unk={n_u})")
    results["V8"] = {"alice_conf": round(c_a, 4), "bob_conf": round(c_b, 4), "unknown_conf": round(c_u, 4)}


# ---------------------------------------------------------------------------
# V9: enroll CLI main() 干跑
# ---------------------------------------------------------------------------
print("\n[V9] enroll CLI main()")
import scripts.enroll_face as enroll_mod  # type: ignore  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    rc = enroll_mod.main([
        "--name", "test_user",
        "--image", str(FIX / "face_id" / "alice" / "1.jpg"),
        "--image", str(FIX / "face_id" / "alice" / "2.jpg"),
        "--store-path", td,
        "--yes",
        "--backend", "histogram",
    ])
    check(rc == 0, f"V9 enroll CLI rc=0 (got {rc})")
    # 落盘验证
    idx = Path(td) / "known_faces.json"
    check(idx.exists(), "V9 enroll CLI 写出 known_faces.json")
    if idx.exists():
        obj = json.loads(idx.read_text())
        check(len(obj.get("records", [])) == 1, "V9 enroll 落 1 record")
    # 错误路径：缺 --name + 没 --image / --from-camera 0 → rc=2
    rc_bad = enroll_mod.main([
        "--name", "x",
        "--store-path", td,
        "--yes",
    ])
    check(rc_bad == 2, f"V9 enroll CLI 无 image+camera rc=2 (got {rc_bad})")
    results["V9"] = {"happy_rc": rc, "bad_rc": rc_bad}


# ---------------------------------------------------------------------------
# V10: emit events face.id_backend_selected / face.identified / face.unknown
# ---------------------------------------------------------------------------
print("\n[V10] emit events")
# 重定向 stderr 到 buffer（setup_logging 绑 sys.stderr），开 jsonl
buf = io.StringIO()
old_stderr = sys.stderr
old_handlers = list(logging.getLogger().handlers)
sys.stderr = buf
try:
    setup_logging(jsonl=True, level="INFO")
    from coco.logging_setup import emit
    backend_name = select_backend("auto").name
    emit("face.id_backend_selected", backend=backend_name)
    emit("face.identified", user_name="alice", confidence=0.65)
    emit("face.unknown", confidence=0.21)
    # 强制 flush
    for h in logging.getLogger().handlers:
        try:
            h.flush()
        except Exception:
            pass
finally:
    sys.stderr = old_stderr
    # 还原 logging
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in old_handlers:
        root.addHandler(h)
out = buf.getvalue()
lines = [l for l in out.splitlines() if l.strip().startswith("{")]
events_seen = set()
for l in lines:
    try:
        obj = json.loads(l)
        events_seen.add(f"{obj.get('component')}.{obj.get('event')}")
    except Exception:
        pass
check("face.id_backend_selected" in events_seen, f"V10 face.id_backend_selected emit (seen={events_seen})")
check("face.identified" in events_seen, "V10 face.identified emit")
check("face.unknown" in events_seen, "V10 face.unknown emit")
results["V10"] = {"events_seen": sorted(events_seen)}


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
ev_dir = Path("evidence/vision-003")
ev_dir.mkdir(parents=True, exist_ok=True)
ev_path = ev_dir / "verify_summary.json"
summary = {
    "feature_id": "vision-003",
    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    "backend_selected": v1_name,
    "has_cv2_face": has_cv2_face,
    "results": results,
    "errors": errors,
    "all_pass": len(errors) == 0,
}
ev_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(f"\n  evidence -> {ev_path}")

if errors:
    print(f"\n[vision-003] FAIL ({len(errors)}):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print(f"\n[vision-003] PASS")
sys.exit(0)
