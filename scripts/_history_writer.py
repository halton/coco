#!/usr/bin/env python3
"""infra-016: append-only jsonl writer for verify/smoke history trend.

Background
----------
Phase-13 infra-016 落地 verify / smoke 历史趋势。每次 `run_verify_all.py` 与
`smoke.py` 跑完都把单行结果写到 `evidence/_history/{verify,smoke}_history.jsonl`，
供 `scripts/health_summary.py` 离线聚合（pass rate / avg duration / Top-K failing
verify / per-area smoke trend）。

设计
----
1. 一行一个 JSON object（jsonl），按 mtime 顺序追加；不读写锁，单进程串行。
2. 字段稳定：``ts`` (UTC ISO8601 second precision)、``git_head`` (短 sha)、
   ``total/pass/fail/skip``、``duration_s`` (float, 2 decimals)；
   verify 额外 ``failed_names`` 列表。
3. 写入失败不阻塞主流程：捕获所有异常，stderr 警告一次即返回。
4. **运行期零影响**：附加写入发生在 `main()` 返回值确定之后，不修改 rc / stdout
   语义。infra-016 V 项以"前后两次 run 同输入下行号差 ==1 且字段集稳定"自证。
5. 行数 >ROTATE_LINES (默认 5000) 自动滚到 `.archive/` 下时间戳命名；保留主
   jsonl 头部 0 行从空开始。

依赖
----
- 仅标准库（json / pathlib / datetime / subprocess）；不引第三方。
- 不依赖 coco.* 业务模块，可在 smoke 早期被 import。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = REPO_ROOT / "evidence" / "_history"
ARCHIVE_DIR = HISTORY_DIR / ".archive"
VERIFY_JSONL = HISTORY_DIR / "verify_history.jsonl"
SMOKE_JSONL = HISTORY_DIR / "smoke_history.jsonl"
ROTATE_LINES = 5000


def _git_head_short(repo: Path = REPO_ROOT) -> str:
    """返回当前 HEAD 短 sha；任何失败返回空字符串而不抛。"""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo), capture_output=True, text=True, timeout=3, check=True,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _iso_utc_now() -> str:
    """UTC ISO8601 to seconds（避免 micro 让 diff 干净）。"""
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat()


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    with open(path, "rb") as f:
        for _ in f:
            n += 1
    return n


def _rotate_if_needed(jsonl_path: Path, *, rotate_lines: int = ROTATE_LINES) -> Path | None:
    """如果 jsonl_path 行数 >= rotate_lines，把它 mv 到 .archive/ 下时间戳文件名。

    返回归档后的 Path（如果触发），否则 None。新的 jsonl_path 不在此函数创建，
    交给下一次 append 自然新建。
    """
    if not jsonl_path.exists():
        return None
    if _line_count(jsonl_path) < rotate_lines:
        return None
    _ensure_dir(ARCHIVE_DIR)
    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    target = ARCHIVE_DIR / f"{jsonl_path.stem}.{stamp}.jsonl"
    jsonl_path.replace(target)
    return target


def _append_line(jsonl_path: Path, record: dict) -> None:
    """append-only，单行 json + \\n。文件锁不必要（单进程串行）。"""
    _ensure_dir(jsonl_path.parent)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _emit_safe(jsonl_path: Path, record: dict) -> bool:
    """对外入口：rotate-if-needed + append。任何异常吞掉返回 False。"""
    try:
        _rotate_if_needed(jsonl_path)
        _append_line(jsonl_path, record)
        return True
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(
            f"[_history_writer] WARN: failed to append {jsonl_path.name}: "
            f"{type(e).__name__}: {e}\n"
        )
        return False


def emit_verify(
    *,
    total: int,
    pass_: int,
    fail: int,
    skip: int,
    duration_s: float,
    failed_names: list[str] | None = None,
    extra: dict | None = None,
) -> bool:
    """run_verify_all.py 跑完后调用。返回 True 表示行已落盘。"""
    if os.environ.get("COCO_HISTORY_DISABLE", "").strip() in ("1", "true", "yes"):
        return False  # escape hatch，dev 可关
    rec = {
        "ts": _iso_utc_now(),
        "kind": "verify",
        "git_head": _git_head_short(),
        "total": int(total),
        "pass": int(pass_),
        "fail": int(fail),
        "skip": int(skip),
        "duration_s": round(float(duration_s), 2),
        "failed_names": list(failed_names or []),
    }
    if extra:
        rec.update(extra)
    return _emit_safe(VERIFY_JSONL, rec)


def emit_smoke(
    *,
    total: int,
    pass_: int,
    fail: int,
    skip: int,
    duration_s: float,
    areas: dict[str, str] | None = None,
    extra: dict | None = None,
) -> bool:
    """smoke.py 跑完后调用。``areas`` 是 {area_name: "PASS"|"FAIL"|"SKIP"|"WARN"}。"""
    if os.environ.get("COCO_HISTORY_DISABLE", "").strip() in ("1", "true", "yes"):
        return False
    rec = {
        "ts": _iso_utc_now(),
        "kind": "smoke",
        "git_head": _git_head_short(),
        "total": int(total),
        "pass": int(pass_),
        "fail": int(fail),
        "skip": int(skip),
        "duration_s": round(float(duration_s), 2),
        "areas": dict(areas or {}),
    }
    if extra:
        rec.update(extra)
    return _emit_safe(SMOKE_JSONL, rec)


def load_records(jsonl_path: Path) -> list[dict]:
    """读取 jsonl 全部记录；坏行跳过。"""
    if not jsonl_path.exists():
        return []
    out: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
