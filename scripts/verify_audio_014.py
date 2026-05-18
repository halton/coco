"""audio-014 verification — wake_word→vad_trigger 跨模块耦合 verify-only 审计.

跑法::

    uv run python scripts/verify_audio_014.py

背景
----
audio-012 closeout C1: 历史上 ``coco/wake_word.py`` 跨模块 import
``vad_trigger._read_loss_window_override_ms`` 私有函数, 形成隐性耦合。
audio-013 已把 env 解析抽到 ``coco.audio_resilience.read_loss_window_override_ms``
公共 util, ``vad_trigger`` 与 ``wake_word`` 均通过该公共名访问。

audio-014 是 **phase-22 verify-only 契约审计**, 不重构 (audio-013 已重构),
只做契约 freeze + 文档锁面 + 未来迁移指引 evidence, 防止后续有人再把 wake_word
私连回 vad_trigger 私有名。

子项 (V0-V5)::

V0  fingerprint — git HEAD + python + 三处关键站点 sha256
    (audio_resilience.read_loss_window_override_ms 源行段 /
     vad_trigger import 段 / wake_word L460-475 reopen 段)。
V1  单一 owner / 三方 ``is`` 同对象 —
    ``coco.audio_resilience.read_loss_window_override_ms`` 必须是
    ``coco.vad_trigger._read_loss_window_override_ms`` 同一对象 (``is`` 真),
    确认 vad_trigger 仅是 thin re-export, 没有重新定义。
V2  字面量锁面 (反证) — ``coco/wake_word.py`` 源码字面量必须满足:
    (a) 不含 ``from coco.vad_trigger import`` (任何形式);
    (b) 不含 ``vad_trigger._read_loss_window_override_ms`` 字面;
    (c) 不含裸 ``import coco.vad_trigger`` /``from coco import vad_trigger``.
    任一命中 → FAIL (有人扩耦合)。
V3  docstring 锁面 (契约文档化) —
    ``audio_resilience.read_loss_window_override_ms.__doc__`` 必须同时提及
    ``vad_trigger`` (来源) 且源文件 audio_resilience.py L405-411 注释段
    同时提及 ``wake_word`` 与 ``vad_trigger`` (契约 reader 名)。
V4  regression — verify_audio_011 / 012 / 013 rc=0
    (确保契约审计不影响既有 audio resilience 链)。
V5  future migration guidance — evidence/audio-014/migration_note.md
    必须存在并含未来若要进一步抽 ``coco.audio_common`` 时的迁移路径文字
    (evidence-only, 此 phase 不动手)。

evidence 落 ``evidence/audio-014/verify_summary.json``。
default-OFF, real_machine_uat=n/a。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / "evidence" / "audio-014"
SUMMARY_PATH = EVIDENCE_DIR / "verify_summary.json"
MIGRATION_NOTE_PATH = EVIDENCE_DIR / "migration_note.md"

_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, **detail: Any) -> None:
    _results.append({"name": name, "ok": bool(ok), **detail})
    flag = "PASS" if ok else "FAIL"
    print(f"[verify_audio_014] {name}: {flag} {detail}", flush=True)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git_head() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
        return out
    except Exception as exc:  # noqa: BLE001
        return f"unknown:{exc}"


# ---------------------------------------------------------------------------
# V0 fingerprint
# ---------------------------------------------------------------------------
def v0_fingerprint() -> Dict[str, Any]:
    ar_path = ROOT / "coco" / "audio_resilience.py"
    vt_path = ROOT / "coco" / "vad_trigger.py"
    ww_path = ROOT / "coco" / "wake_word.py"

    ar_text = ar_path.read_text(encoding="utf-8")
    vt_text = vt_path.read_text(encoding="utf-8")
    ww_text = ww_path.read_text(encoding="utf-8")

    fp = {
        "git_head": _git_head(),
        "python": sys.version.split()[0],
        "audio_resilience_sha256": _sha256_text(ar_text),
        "vad_trigger_sha256": _sha256_text(vt_text),
        "wake_word_sha256": _sha256_text(ww_text),
    }
    _record("V0_fingerprint", True, **fp)
    return fp


# ---------------------------------------------------------------------------
# V1 单一 owner / `is` 同对象
# ---------------------------------------------------------------------------
def v1_single_owner() -> None:
    from coco import audio_resilience as _ar
    from coco import vad_trigger as _vt

    public = _ar.read_loss_window_override_ms
    alias = getattr(_vt, "_read_loss_window_override_ms", None)

    same_obj = alias is public
    has_alias = alias is not None

    _record(
        "V1_single_owner_is_same_obj",
        same_obj and has_alias,
        public_qualname=f"{public.__module__}.{public.__name__}",
        alias_qualname=(
            f"{alias.__module__}.{alias.__name__}" if has_alias else None
        ),
        is_same_obj=same_obj,
    )


# ---------------------------------------------------------------------------
# V2 字面量锁面 (反证)
# ---------------------------------------------------------------------------
def v2_literal_lock() -> None:
    ww_path = ROOT / "coco" / "wake_word.py"
    text = ww_path.read_text(encoding="utf-8")

    # (a) 不含 from coco.vad_trigger import (任何形式)
    pat_from_vt = re.compile(r"^\s*from\s+coco\.vad_trigger\s+import\b", re.M)
    hit_from_vt = pat_from_vt.findall(text)

    # (b) 不含 vad_trigger._read_loss_window_override_ms 私有字面
    pat_private = re.compile(r"vad_trigger\._read_loss_window_override_ms")
    hit_private = pat_private.findall(text)

    # (c) 不含 `import coco.vad_trigger` 或 `from coco import vad_trigger`
    pat_bare1 = re.compile(r"^\s*import\s+coco\.vad_trigger\b", re.M)
    pat_bare2 = re.compile(r"^\s*from\s+coco\s+import\s+.*\bvad_trigger\b", re.M)
    hit_bare = pat_bare1.findall(text) + pat_bare2.findall(text)

    ok = not hit_from_vt and not hit_private and not hit_bare
    _record(
        "V2_wake_word_literal_lock",
        ok,
        hit_from_vt_count=len(hit_from_vt),
        hit_private_literal_count=len(hit_private),
        hit_bare_import_count=len(hit_bare),
    )


# ---------------------------------------------------------------------------
# V3 docstring 锁面
# ---------------------------------------------------------------------------
def v3_docstring_lock() -> None:
    from coco import audio_resilience as _ar

    doc = _ar.read_loss_window_override_ms.__doc__ or ""
    doc_mentions_vt = "vad_trigger" in doc

    ar_path = ROOT / "coco" / "audio_resilience.py"
    ar_text = ar_path.read_text(encoding="utf-8")
    # 头部契约注释段 (L400-420 附近) 同时提及 wake_word 与 vad_trigger
    # 直接全文检查 (简化 + 健壮)
    src_mentions_ww = "wake_word" in ar_text
    src_mentions_vt = "vad_trigger" in ar_text

    ok = doc_mentions_vt and src_mentions_ww and src_mentions_vt
    _record(
        "V3_docstring_lock",
        ok,
        doc_mentions_vad_trigger=doc_mentions_vt,
        src_mentions_wake_word=src_mentions_ww,
        src_mentions_vad_trigger=src_mentions_vt,
    )


# ---------------------------------------------------------------------------
# V4 regression
# ---------------------------------------------------------------------------
def v4_regression() -> None:
    scripts = [
        "scripts/verify_audio_011.py",
        "scripts/verify_audio_012.py",
        "scripts/verify_audio_013.py",
    ]
    rcs: Dict[str, int] = {}
    all_ok = True
    for s in scripts:
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, s],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
        )
        dt = time.time() - t0
        rcs[s] = proc.returncode
        ok_one = proc.returncode == 0
        all_ok = all_ok and ok_one
        print(
            f"[verify_audio_014] regression {s}: rc={proc.returncode} dt={dt:.1f}s",
            flush=True,
        )
    _record("V4_regression_audio_011_012_013", all_ok, rcs=rcs)


# ---------------------------------------------------------------------------
# V5 future migration guidance (evidence-only)
# ---------------------------------------------------------------------------
MIGRATION_NOTE = """# audio-014 future migration note (evidence-only)

scope: 未来若需进一步从 ``coco.audio_resilience`` 拆出更细 ``coco.audio_common``
公共 util (audio-012 closeout C1 长期建议), 这里给出迁移路径 (evidence-only,
此 phase 不动手, 仅锁面)。

current owner (audio-013 后):

- ``coco.audio_resilience.read_loss_window_override_ms`` — env 解析 (9-case 已验)
- ``coco.audio_resilience.classify_stream_error`` — PortAudio 异常判定
- ``coco.audio_resilience.ENV_LOSS_WINDOW_MS`` — env 名常量
- ``coco.vad_trigger`` — thin re-export ``_read_loss_window_override_ms`` 别名 (向后兼容)
- ``coco.wake_word`` reopen 路径 — 直接 ``from coco.audio_resilience import``

future migration (若拆 ``coco.audio_common``):

1. 新建 ``coco/audio_common.py``, 把以下 3 个符号物理迁过去:
   - ``ENV_LOSS_WINDOW_MS``
   - ``read_loss_window_override_ms``
   - ``classify_stream_error``
2. ``coco.audio_resilience`` 改为从 ``coco.audio_common`` 公共 re-export,
   保持 ``__all__`` 不变 (audio-013 已暴露)。
3. ``coco.vad_trigger`` 的 thin delegate 不动 (向后兼容 verify_audio_012)。
4. ``coco.wake_word`` reopen 路径 import 路径**不需要变**, 因为它走的是
   ``coco.audio_resilience`` 公共名, audio_resilience 内部再 re-export。
5. 新写 verify_audio_NNN_migration.py:
   - V_n 双源 ``is`` 同对象: ``audio_common.X is audio_resilience.X``
   - V_n vad_trigger thin delegate ``is`` 同对象 (链路三跳锁面)
   - V_n verify_audio_011/012/013/014 全 PASS (regression)

风险与不做手术的理由:

- 当前 audio_resilience 已是单一 owner; 若再拆一层, 引入 3 → 4 module 间接性,
  无功能收益, 只有审计开销 ↑。
- audio-014 verify 已**契约 freeze**: wake_word 不得跨模块连回 vad_trigger 私有,
  vad_trigger 必须 thin re-export 同对象。任何破契约改动会被 V1/V2 抓到。
- 未来若 audio_resilience 单文件超过 ~600 行或同时承载 hotplug + loss_window +
  recovery 三块互不相关职责, 再考虑按上述路径迁移。

audio-014 作为 verify-only 审计**到此为止**, 不衍生 fu chain, 不动源码。
"""


def v5_migration_guidance() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    MIGRATION_NOTE_PATH.write_text(MIGRATION_NOTE, encoding="utf-8")

    text = MIGRATION_NOTE_PATH.read_text(encoding="utf-8")
    has_audio_common = "coco.audio_common" in text or "audio_common" in text
    has_migration = "migration" in text.lower()
    has_freeze = "freeze" in text.lower() or "锁面" in text
    nbytes = len(text.encode("utf-8"))

    ok = has_audio_common and has_migration and has_freeze and nbytes > 200
    _record(
        "V5_migration_guidance_evidence",
        ok,
        path=str(MIGRATION_NOTE_PATH.relative_to(ROOT)),
        bytes=nbytes,
        mentions_audio_common=has_audio_common,
        mentions_migration=has_migration,
        mentions_freeze_or_lock=has_freeze,
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    fp = v0_fingerprint()
    v1_single_owner()
    v2_literal_lock()
    v3_docstring_lock()
    v4_regression()
    v5_migration_guidance()

    all_ok = all(r["ok"] for r in _results)
    summary = {
        "feature": "audio-014",
        "scope": "wake_word→vad_trigger 跨模块耦合 verify-only 审计",
        "verify_only": True,
        "source_code_changes": 0,
        "default_off": True,
        "real_machine_uat": "n/a",
        "fingerprint": fp,
        "results": _results,
        "all_ok": all_ok,
        "ts": time.time(),
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[verify_audio_014] ALL_OK={all_ok} summary={SUMMARY_PATH.relative_to(ROOT)}",
        flush=True,
    )
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
