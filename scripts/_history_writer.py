#!/usr/bin/env python3
"""infra-016/017: append-only jsonl writer for verify/smoke history trend.

Background
----------
Phase-13 infra-016 落地 verify / smoke 历史趋势。每次 `run_verify_all.py` 与
`smoke.py` 跑完都把单行结果写到 `evidence/_history/{verify,smoke}_history.jsonl`,
供 `scripts/health_summary.py` 离线聚合（pass rate / avg duration / Top-K failing
verify / per-area smoke trend）。

Phase-14 infra-017 加固
-----------------------
- 文件锁（fcntl/msvcrt fallback）防多进程并发 append 撕裂。
- rotate 后立即 recreate 空 jsonl（empty file, no leading line），保证下次
  `_line_count` 从 0 算且 schema 不破。
- .archive/ 文件名带 PID + ns-precision 时戳，防同秒多进程同名碰撞。
- retention：保最近 N=20 个 .archive/<stem>.*.jsonl（按 mtime 删旧），可用
  ``COCO_HISTORY_ARCHIVE_KEEP`` 调。
- COCO_HISTORY_DISABLE 解析 .lower() + 白名单：``1/true/yes/on``（大小写不敏感）。

设计
----
1. 一行一个 JSON object（jsonl），按 mtime 顺序追加。
2. 字段稳定：``ts`` (UTC ISO8601 second precision)、``git_head`` (短 sha)、
   ``total/pass/fail/skip``、``duration_s`` (float, 2 decimals)；
   verify 额外 ``failed_names`` 列表。
3. 写入失败不阻塞主流程：捕获所有异常，stderr 警告一次即返回。
4. **运行期零影响**：附加写入发生在 `main()` 返回值确定之后，不修改 rc / stdout
   语义。infra-016 V 项以"前后两次 run 同输入下行号差 ==1 且字段集稳定"自证。
5. 行数 >ROTATE_LINES (默认 5000) 自动滚到 `.archive/` 下时间戳命名；主 jsonl 立即
   recreate 空文件（infra-017 C2）。

依赖
----
- 仅标准库（json / pathlib / datetime / subprocess / time / fcntl|msvcrt）。
- 不依赖 coco.* 业务模块，可在 smoke 早期被 import。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = REPO_ROOT / "evidence" / "_history"
ARCHIVE_DIR = HISTORY_DIR / ".archive"
VERIFY_JSONL = HISTORY_DIR / "verify_history.jsonl"
SMOKE_JSONL = HISTORY_DIR / "smoke_history.jsonl"
ROTATE_LINES = 5000

# infra-017 C6: 默认保留最近 N=20 个 archive，可用 env 调。
_DEFAULT_ARCHIVE_KEEP = 20

# infra-017 C8: HISTORY_DISABLE 白名单（lower-case 比对）。
_DISABLE_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _archive_keep_count() -> int:
    raw = os.environ.get("COCO_HISTORY_ARCHIVE_KEEP", "").strip()
    if not raw:
        return _DEFAULT_ARCHIVE_KEEP
    try:
        n = int(raw)
        return max(0, n)
    except ValueError:
        return _DEFAULT_ARCHIVE_KEEP


def _history_disabled() -> bool:
    """infra-017 C8: 大小写不敏感解析 HISTORY_DISABLE。"""
    raw = os.environ.get("COCO_HISTORY_DISABLE", "").strip().lower()
    return raw in _DISABLE_TRUE_VALUES


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


# ---------------------------------------------------------------------------
# infra-017 C1: 跨平台文件锁
# ---------------------------------------------------------------------------
try:
    import fcntl as _fcntl  # type: ignore[import-not-found]
    _HAS_FCNTL = True
except ImportError:  # Windows
    _fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False

try:
    import msvcrt as _msvcrt  # type: ignore[import-not-found]
    _HAS_MSVCRT = True
except ImportError:  # POSIX
    _msvcrt = None  # type: ignore[assignment]
    _HAS_MSVCRT = False


class _FileLock:
    """跨平台 advisory exclusive lock for append-only jsonl.

    POSIX: fcntl.flock(LOCK_EX)。Windows: msvcrt.locking(LK_LOCK)。
    锁文件 = jsonl_path + '.lock'（独立 fd），避免被 truncate / rename 干扰。
    任意失败（如 fs 不支持 flock）退化为 no-op；不阻塞 main flow。
    """

    def __init__(self, jsonl_path: Path) -> None:
        self.lock_path = jsonl_path.with_suffix(jsonl_path.suffix + ".lock")
        self._fd = None  # type: ignore[var-annotated]

    def __enter__(self) -> "_FileLock":
        try:
            _ensure_dir(self.lock_path.parent)
            # 用 os.open 避免 buffering 影响；'a' 不 truncate
            self._fd = open(self.lock_path, "a+")
            if _HAS_FCNTL:
                _fcntl.flock(self._fd.fileno(), _fcntl.LOCK_EX)  # type: ignore[union-attr]
            elif _HAS_MSVCRT:
                # Windows: LK_LOCK 阻塞直到拿到锁
                try:
                    _msvcrt.locking(self._fd.fileno(), _msvcrt.LK_LOCK, 1)  # type: ignore[union-attr]
                except OSError:
                    pass
        except Exception:
            # 退化：无锁继续，不阻塞主流程
            if self._fd is not None:
                try:
                    self._fd.close()
                except Exception:
                    pass
                self._fd = None
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is None:
            return
        try:
            if _HAS_FCNTL:
                try:
                    _fcntl.flock(self._fd.fileno(), _fcntl.LOCK_UN)  # type: ignore[union-attr]
                except Exception:
                    pass
            elif _HAS_MSVCRT:
                try:
                    _msvcrt.locking(self._fd.fileno(), _msvcrt.LK_UNLCK, 1)  # type: ignore[union-attr]
                except Exception:
                    pass
        finally:
            try:
                self._fd.close()
            except Exception:
                pass
            self._fd = None


# ---------------------------------------------------------------------------
# rotate + archive retention
# ---------------------------------------------------------------------------

def _archive_stamp() -> str:
    """infra-017 C3: <YYYYMMDDTHHMMSSZ>.<ns>.<pid> 同秒不碰撞。

    ns 取 time.time_ns() % 1e9（ns of current second 不够稳，用全 ns 即可）；
    pid 兜底跨进程。归档命名仅用于排序与去重，不参与 verify_summary diff。
    """
    now = _dt.datetime.now(_dt.UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    ns = time.time_ns()
    return f"{stamp}.{ns}.{os.getpid()}"


def _enforce_retention(stem: str, *, archive_dir: Path | None = None,
                       keep: int | None = None) -> list[Path]:
    """infra-017 C6: 保最近 `keep` 个 archive/<stem>.*.jsonl，按 mtime 删旧。

    返回被删的 Path 列表。任何 IO 错误吞掉返回 []。
    """
    if archive_dir is None:
        archive_dir = ARCHIVE_DIR
    if keep is None:
        keep = _archive_keep_count()
    if keep <= 0 or not archive_dir.exists():
        return []
    try:
        candidates = sorted(
            archive_dir.glob(f"{stem}.*.jsonl"),
            key=lambda p: p.stat().st_mtime,
        )
    except OSError:
        return []
    if len(candidates) <= keep:
        return []
    to_delete = candidates[: len(candidates) - keep]
    deleted: list[Path] = []
    for p in to_delete:
        try:
            p.unlink()
            deleted.append(p)
        except OSError:
            continue
    return deleted


def _rotate_if_needed(jsonl_path: Path, *, rotate_lines: int = ROTATE_LINES) -> Path | None:
    """行数 >= rotate_lines → mv 到 .archive/，立即 recreate 空 jsonl，并执行 retention。

    infra-017 C2: rotate 后立即新建空文件（0 行）。
    infra-017 C3: archive 文件名带 ns + pid。
    infra-017 C6: 删多余 archive。
    """
    if not jsonl_path.exists():
        return None
    if _line_count(jsonl_path) < rotate_lines:
        return None
    _ensure_dir(ARCHIVE_DIR)
    stamp = _archive_stamp()
    target = ARCHIVE_DIR / f"{jsonl_path.stem}.{stamp}.jsonl"
    jsonl_path.replace(target)
    # C2: 立即 recreate 空文件
    try:
        jsonl_path.touch()
    except OSError:
        pass
    # C6: retention
    _enforce_retention(jsonl_path.stem)
    return target


def _append_line(jsonl_path: Path, record: dict) -> None:
    """append-only，单行 json + \\n。infra-017 C1: 文件锁防并发撕裂。"""
    _ensure_dir(jsonl_path.parent)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with _FileLock(jsonl_path):
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
    if _history_disabled():
        return False  # escape hatch，dev 可关（infra-017 C8 大小写不敏感）
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
    if _history_disabled():
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
