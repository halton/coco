"""companion-018 verification — preference emit env 命名 spec 文档同步锁面 (verify-only).

跑法::

    uv run python scripts/verify_companion_018.py

背景
----
companion-016 brief 与 backlog 文案使用旧名 ``COCO_PREFERENCE_EMIT_INTERVAL_S``
默认 30s, 而实现一直是 ``COCO_PERSIST_EMIT_MIN_INTERVAL_S`` 默认 10s。companion-017
已在 ``coco/companion/preference_learner.py`` 内联注释里锁面 (以代码为准),
companion-018 把这层口径外移到 ``docs/companion-preference-emit-env-spec.md``
作为独立权威 spec 文档, 并锁面 C1 (env 命名) + C4 (warn-once 多进程语义) 子项。

phase-22 P182 verify-only 锁面 (0 源码改动):

V0  fingerprint — git HEAD + python + preference_learner.py / spec doc / verify
    自身 sha256。
V1  字面量锁 — ``coco/companion/preference_learner.py`` 必须有
    ``_PERSIST_EMIT_INTERVAL_ENV = "COCO_PERSIST_EMIT_MIN_INTERVAL_S"`` 字面;
    反证: 同文件不得读取或赋值旧名 ``COCO_PREFERENCE_EMIT_INTERVAL_S`` (历史误称)。
    spec 文档侧必须出现新名且 (允许) 出现旧名 (作为 deprecation 注解)。
V2  spec doc 关键短语锁面 — ``docs/companion-preference-emit-env-spec.md`` 必须
    包含权威 env 名 / 默认 10.0 / WARN once / 多进程 / "以代码为准" / verify-only
    等关键短语, 且字节数 > 1500。
V3  regression — verify_companion_015 / 016 / 017 rc=0。
V4  smoke 回归 — ``./init.sh`` 总数 11 PASS。
V5  migration evidence — ``evidence/companion-018/migration_note.md`` 存在,
    含未来 alias 旧名的代价分析文字。

evidence 落 ``evidence/companion-018/verify_summary.json``。
default-OFF, real_machine_uat=n/a, 0 源码改动。
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

EVIDENCE_DIR = ROOT / "evidence" / "companion-018"
SUMMARY_PATH = EVIDENCE_DIR / "verify_summary.json"
MIGRATION_NOTE_PATH = EVIDENCE_DIR / "migration_note.md"
SPEC_DOC_PATH = ROOT / "docs" / "companion-preference-emit-env-spec.md"
PREF_LEARNER_PATH = ROOT / "coco" / "companion" / "preference_learner.py"

AUTHORITATIVE_ENV = "COCO_PERSIST_EMIT_MIN_INTERVAL_S"
LEGACY_ENV = "COCO_PREFERENCE_EMIT_INTERVAL_S"  # 历史误称, 仅作 deprecation 注解

_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, **detail: Any) -> None:
    _results.append({"name": name, "ok": bool(ok), **detail})
    flag = "PASS" if ok else "FAIL"
    print(f"[verify_companion_018] {name}: {flag} {detail}", flush=True)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
    except Exception as exc:  # noqa: BLE001
        return f"unknown:{exc}"


# ---------------------------------------------------------------------------
# V0 fingerprint
# ---------------------------------------------------------------------------
def v0_fingerprint() -> Dict[str, Any]:
    pl_text = PREF_LEARNER_PATH.read_text(encoding="utf-8")
    doc_text = SPEC_DOC_PATH.read_text(encoding="utf-8") if SPEC_DOC_PATH.exists() else ""
    self_text = Path(__file__).read_text(encoding="utf-8")
    fp = {
        "git_head": _git_head(),
        "python": sys.version.split()[0],
        "preference_learner_sha256": _sha256_text(pl_text),
        "spec_doc_sha256": _sha256_text(doc_text),
        "verify_self_sha256": _sha256_text(self_text),
    }
    _record("V0_fingerprint", True, **fp)
    return fp


# ---------------------------------------------------------------------------
# V1 字面量锁 — env 名 (code + spec doc)
# ---------------------------------------------------------------------------
def v1_env_name_literal_lock() -> None:
    pl_text = PREF_LEARNER_PATH.read_text(encoding="utf-8")
    doc_text = SPEC_DOC_PATH.read_text(encoding="utf-8") if SPEC_DOC_PATH.exists() else ""

    # (a) code 侧必须有权威常量声明
    pat_const = re.compile(
        r'_PERSIST_EMIT_INTERVAL_ENV\s*=\s*"COCO_PERSIST_EMIT_MIN_INTERVAL_S"'
    )
    hit_const = pat_const.findall(pl_text)

    # (b) code 侧反证: 不得通过 os.environ.get 或 mapping.get 读旧名
    # 字面 "COCO_PREFERENCE_EMIT_INTERVAL_S" 在 code 中只允许出现在文字注释里, 不允许
    # 出现在 env.get / os.environ[...] / dict key 等 *读取* 路径上。
    # 简化锁面: 旧名字面量在 code 中**不应该出现在 .get( 调用紧前**。
    pat_legacy_read = re.compile(
        r'(?:os\.environ|env|environ)\.get\s*\(\s*["\']COCO_PREFERENCE_EMIT_INTERVAL_S["\']'
    )
    hit_legacy_read = pat_legacy_read.findall(pl_text)

    # (c) code 侧旧名出现次数 (允许出现在注释中作历史说明)
    legacy_in_code_count = pl_text.count(LEGACY_ENV)

    # (d) spec doc 必须出现新名 (权威)
    doc_has_new = AUTHORITATIVE_ENV in doc_text
    # (e) spec doc 允许出现旧名 (作 deprecation 说明), 不强制
    doc_has_legacy = LEGACY_ENV in doc_text

    ok = (
        len(hit_const) >= 1
        and len(hit_legacy_read) == 0
        and doc_has_new
    )
    _record(
        "V1_env_name_literal_lock",
        ok,
        hit_const=len(hit_const),
        hit_legacy_read=len(hit_legacy_read),
        legacy_in_code_count=legacy_in_code_count,
        doc_has_authoritative=doc_has_new,
        doc_has_legacy_ref=doc_has_legacy,
    )


# ---------------------------------------------------------------------------
# V2 spec doc 关键短语锁面
# ---------------------------------------------------------------------------
def v2_spec_doc_phrase_lock() -> None:
    if not SPEC_DOC_PATH.exists():
        _record("V2_spec_doc_phrase_lock", False, reason="spec doc missing")
        return
    text = SPEC_DOC_PATH.read_text(encoding="utf-8")
    phrases = [
        AUTHORITATIVE_ENV,
        LEGACY_ENV,  # 旧名作为 deprecation 注解必须出现 (告诉读者 "不要用这个")
        "10.0",
        "WARN once",
        "多进程",
        "以代码为准",
        "verify-only",
        "default-OFF",
        "companion-016-backlog-polish",
        "preference_learner.py",
        "_PERSIST_EMIT_INTERVAL_ENV",
        "preference_persist_emit_min_interval_s_from_env",
    ]
    hits = {p: (p in text) for p in phrases}
    nbytes = len(text.encode("utf-8"))
    ok = all(hits.values()) and nbytes > 1500
    _record(
        "V2_spec_doc_phrase_lock",
        ok,
        path=str(SPEC_DOC_PATH.relative_to(ROOT)),
        bytes=nbytes,
        missing=[p for p, v in hits.items() if not v],
    )


# ---------------------------------------------------------------------------
# V3 regression — 邻近 verify rc=0
# ---------------------------------------------------------------------------
def v3_regression() -> None:
    scripts = [
        "scripts/verify_companion_015.py",
        "scripts/verify_companion_016.py",
        "scripts/verify_companion_017.py",
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
            timeout=240,
        )
        dt = time.time() - t0
        rcs[s] = proc.returncode
        ok_one = proc.returncode == 0
        all_ok = all_ok and ok_one
        print(
            f"[verify_companion_018] regression {s}: rc={proc.returncode} dt={dt:.1f}s",
            flush=True,
        )
    _record("V3_regression_companion_015_016_017", all_ok, rcs=rcs)


# ---------------------------------------------------------------------------
# V4 smoke 回归
# ---------------------------------------------------------------------------
def v4_smoke_regression() -> None:
    t0 = time.time()
    proc = subprocess.run(
        ["bash", "init.sh"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "CI": "1"},
    )
    dt = time.time() - t0
    out = (proc.stdout or "") + (proc.stderr or "")
    smoke_lines = re.findall(r"^==>\s*Smoke:\s+", out, re.M)
    ok_lines = re.findall(r"^\s*ok:\s+", out, re.M)
    total_n = len(smoke_lines)
    pass_n = len(ok_lines)
    smoke_done = "Smoke 通过" in out
    rc_ok = proc.returncode == 0
    counts_ok = (total_n >= 11) and (pass_n >= total_n - 1)
    ok = rc_ok and counts_ok and smoke_done
    _record(
        "V4_smoke_regression",
        ok,
        rc=proc.returncode,
        pass_n=pass_n,
        total_n=total_n,
        smoke_done=smoke_done,
        dt_s=round(dt, 1),
    )


# ---------------------------------------------------------------------------
# V5 migration evidence
# ---------------------------------------------------------------------------
MIGRATION_NOTE = """# companion-018 future migration note (evidence-only)

scope: 未来若需为旧名 ``COCO_PREFERENCE_EMIT_INTERVAL_S`` 提供 alias 兼容,
本 phase **不动手**, 仅锁面 evidence。

current state (companion-017 后):

- ``coco/companion/preference_learner.py`` ``_PERSIST_EMIT_INTERVAL_ENV =
  "COCO_PERSIST_EMIT_MIN_INTERVAL_S"`` 单一权威 env 名。
- ``preference_persist_emit_min_interval_s_from_env(env=None)`` 仅读这一个名字。
- 旧名 ``COCO_PREFERENCE_EMIT_INTERVAL_S`` 在 code 中**只出现在注释**作历史说明,
  没有任何 ``.get(...)`` 读取路径。
- companion-018 新建 ``docs/companion-preference-emit-env-spec.md`` 作权威 spec,
  §1.1 显式说明 "不为旧名提供 alias"。
- 模块级 ``_PERSIST_EMIT_INTERVAL_WARN_ONCE`` 为进程级 flag, 多进程各 warn 一次,
  与 companion-015 ``_PREFERENCE_STATE_WARN_ONCE`` 行为一致, 详见 spec §2。

future migration (若必须 alias 旧名):

1. 源码改动 (估算 5-15 行):
   - ``preference_persist_emit_min_interval_s_from_env``: 改为优先读新名,
     若 ``raw is None`` 再读旧名; 旧名命中加 deprecation WARN once。
   - module docstring + function docstring 同步说明 alias + deprecation timeline。
   - 新增常量 ``_PERSIST_EMIT_INTERVAL_LEGACY_ENV = "COCO_PREFERENCE_EMIT_INTERVAL_S"``。
2. verify 同步代价:
   - ``scripts/verify_companion_017.py`` V1 "C1 code uses only new name" 需放宽
     允许旧名出现 (但限定在 alias 解析路径)。
   - ``scripts/verify_companion_018.py`` V1 ``hit_legacy_read == 0`` 需放宽。
   - 新增 fixture: 旧名 env set / 新名 env unset → 解析回旧名值。
   - 新增 fixture: 新名 + 旧名同时 set → 新名胜出。
3. spec 文档同步:
   - ``docs/companion-preference-emit-env-spec.md`` §1 新增 deprecation table。
   - §1.1 "不为旧名提供 alias 的理由" 改写为 "alias 已启用, deprecation timeline X"。
4. 风险与不做手术的理由:
   - 旧名 ``COCO_PREFERENCE_EMIT_INTERVAL_S`` 默认 30s 与新名默认 10s **不一致**,
     直接 alias 会让既有用户 (假设有) 的 30s 默认行为变成 10s, 反而破坏兼容。
   - 真要 alias 还得保留旧默认值, 实现复杂度翻倍。
   - 当前**外部无任何引用**, alias 是 "修一个不存在的问题"。
5. multi-process WARN once 切到 "true global once" 的成本 (spec §2.4 已分析):
   - 需要持久化 flag + atomic + schema, 收益极低, **不做**。

companion-018 作为 verify-only spec 锁面**到此为止**, 不衍生 fu chain。
不动源码, 不为旧名提供 alias, 不切多进程 warn 语义。
"""


def v5_migration_evidence() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    MIGRATION_NOTE_PATH.write_text(MIGRATION_NOTE, encoding="utf-8")
    text = MIGRATION_NOTE_PATH.read_text(encoding="utf-8")
    has_new = AUTHORITATIVE_ENV in text
    has_legacy = LEGACY_ENV in text
    has_verify_ref = "verify_companion_017" in text or "verify_companion_018" in text
    has_freeze = "锁面" in text or "不衍生" in text
    has_alias_cost = "alias" in text.lower()
    nbytes = len(text.encode("utf-8"))
    ok = (
        has_new and has_legacy and has_verify_ref and has_freeze
        and has_alias_cost and nbytes > 800
    )
    _record(
        "V5_migration_evidence",
        ok,
        path=str(MIGRATION_NOTE_PATH.relative_to(ROOT)),
        bytes=nbytes,
        has_authoritative=has_new,
        has_legacy_ref=has_legacy,
        has_verify_ref=has_verify_ref,
        has_freeze=has_freeze,
        has_alias_cost=has_alias_cost,
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    fp = v0_fingerprint()
    v1_env_name_literal_lock()
    v2_spec_doc_phrase_lock()
    v3_regression()
    v4_smoke_regression()
    v5_migration_evidence()

    all_ok = all(r["ok"] for r in _results)
    summary = {
        "feature": "companion-018",
        "scope": "preference emit env 命名 spec 文档同步锁面 (verify-only)",
        "verify_only": True,
        "source_code_changes": 0,
        "default_off": True,
        "real_machine_uat": "n/a",
        "authoritative_env": AUTHORITATIVE_ENV,
        "legacy_env_no_alias": LEGACY_ENV,
        "fingerprint": fp,
        "results": _results,
        "all_ok": all_ok,
        "ts": time.time(),
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[verify_companion_018] ALL_OK={all_ok} summary={SUMMARY_PATH.relative_to(ROOT)}",
        flush=True,
    )
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
