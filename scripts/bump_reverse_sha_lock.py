#!/usr/bin/env python3
"""bump_reverse_sha_lock: 通用反向 sha lock bump helper (verify-only / dry-run 友好).

infra-V6-backlog-scope-extend-bump-helpers (phase-34 #2.34):
扩展 bump helper 覆盖面到 V6 反向 sha lock 类型的常量。

V6 (infra-V6-backlog, P264) 在 ``_verify_lib.scan_reverse_sha_locks`` 中识别
``scripts/verify_*.py`` + ``_verify_lib.py`` 顶层 64-hex sha 常量, 名称匹配
``VERIFY_<NNN>`` 模式者视为反向锁; 对应的 live target file 为同 NNN 的
``verify_<sub>_<NNN>.py``。当 target file 内容改变 (新增 sentinel / 调整
docstring 锚定等), 反向锁常量需要同步刷新, 否则 V6 pre-flight 会 FAIL。

之前每个 verify-script 单独写一个 ``bump_verify_<NNN>_self_hash.py`` 助手
(如 ``bump_verify_027_self_hash.py`` / ``bump_verify_028_self_hash.py``);
本通用 helper 用 V6 的 scan + infer 机制自动识别所有反向锁, 一次性 bump 指向
任一 target 的全部常量。

设计要点
--------
- **默认 dry-run 模式不写盘**。需要落盘必须显式 ``--apply``。
- ``--target <path>`` 指定一个待刷新的 target file (相对 repo 根或绝对路径
  均可); 计算其当前 sha256, 然后扫描所有反向锁, 找出 ``target_id`` 匹配 +
  ``candidates`` 含该 target file 的反向锁, bump 它们的字面常量。
- ``--verify`` 在 bump 完成后跑 ``scripts/verify_infra_034.py`` 验证 V6
  pre-flight 通过 (sanity check)。

退出码
------
0  无需更新 / 成功 dry-run / 成功 apply / 成功 verify
2  target file 不存在
3  无反向锁指向 target (空操作不算错, 但 print 警告)
4  字面替换失败 (regex 未命中, 表示源码格式偏离单行 / 元组规范)
5  --verify 模式下 verify_infra_034.py 非 0 退出

机器可解析 RESULT sentinel (infra-V22-cascade-warn-noop-semantics)
-----------------------------------------------------------------
main() 在退出前 (rc 落定后) 输出一行以 ``RESULT:`` 开头的 sentinel, 供 cascade
父进程区分 "实际写盘 bump" vs "空操作 (无反向锁 holder / 已最新)":

- ``RESULT: APPLIED holders=<N>`` — 实际有 N (>=1) 个反向锁常量被 bump (或在
  dry-run 下报告 "would replace")
- ``RESULT: NOOP reason=<text>`` — 没干活: 反向锁不存在 (``no_holders``) 或
  全部已最新 (``all_uptodate``)

WARN 文本保持人类可读不变, RESULT 行作为机器解析锚点。
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

sys.path.insert(0, str(SCRIPTS))
from _verify_lib import (  # noqa: E402
    live_verify_sha_set,
    scan_reverse_sha_locks,
)


# 单行 const 替换 regex (与 _verify_lib._RE_REVLOCK_SINGLELINE 对应)
_RE_SL_BUMP = re.compile(
    r'^(?P<lead>[A-Z_][A-Z0-9_]*\s*=\s*["\'])(?P<hex>[0-9a-f]{64})(?P<tail>["\']\s*(?:#.*)?)$',
)
# 元组 hex 行替换 regex (与 _verify_lib._RE_REVLOCK_TUPLE_HEX 对应)
_RE_TUP_BUMP = re.compile(
    r'^(?P<lead>\s*["\'])(?P<hex>[0-9a-f]{64})(?P<tail>["\']\s*,?\s*)$',
)


def _compute_target_sha(target: Path) -> str:
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _candidates_for(target_rel: str, live: dict[str, str], const_name: str) -> bool:
    """判断该反向锁常量是否指向 target_rel。

    匹配规则:
    1. NNN-based (verify_<sub>_NNN.py): 常量名含 ``VERIFY_<NNN>`` 且 target 末尾
       匹配 ``_NNN.py``。
    2. NNN-agnostic fallback (``_verify_lib.py``): target 是
       ``scripts/_verify_lib.py`` 时, 匹配 file-level sha 常量名:
       ``EXPECTED_LIB_FILE_SHA`` / ``EXPECTED_VERIFY_LIB_FILE_SHA``。
       func-level sha (``EXPECTED_<FUNC>_FUNC_SHA``) 不匹配, 因为它锁的是函数
       源码 sha 而非文件 sha, bump 成 file sha 会立刻 FAIL。
    """
    # NNN-agnostic: _verify_lib.py file-level lock
    if target_rel.endswith("_verify_lib.py"):
        return const_name in ("EXPECTED_LIB_FILE_SHA", "EXPECTED_VERIFY_LIB_FILE_SHA")
    # NNN-based: VERIFY_<NNN> ↔ target_<NNN>.py
    m = re.search(r"VERIFY_(\d{3})", const_name)
    if not m:
        return False
    nnn = m.group(1)
    # target_rel 形如 'scripts/verify_robot_025.py'; 末尾 _NNN.py 匹配则视为同 id
    return bool(re.search(rf"_{nnn}\.py$", target_rel))


def _bump_in_file(
    file_path: Path,
    lineno: int,
    old_sha: str,
    new_sha: str,
    dry_run: bool,
) -> Tuple[bool, str]:
    """在 file_path 的 lineno (1-based) 行附近替换 old_sha → new_sha。

    支持单行字面 (``CONST = "<hex>"``) 与元组形式 (CONST 在 lineno 行, hex 在
    lineno+1 行, 但 scan_reverse_sha_locks 返回的 lineno 指向 CONST 行,
    元组 hex 行在 lineno+1)。

    Returns:
        (changed, detail)。changed=True 表示成功识别并替换 (或 dry-run 将替换);
        changed=False 表示未匹配。
    """
    src = file_path.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    if lineno < 1 or lineno > len(lines):
        return False, f"lineno {lineno} out of range"
    # 先尝试单行
    idx = lineno - 1
    m_sl = _RE_SL_BUMP.match(lines[idx].rstrip("\n"))
    if m_sl and m_sl.group("hex") == old_sha:
        new_line = m_sl.group("lead") + new_sha + m_sl.group("tail") + (
            "\n" if lines[idx].endswith("\n") else ""
        )
        if dry_run:
            return True, f"would replace at line {lineno} (singleline)"
        lines[idx] = new_line
        file_path.write_text("".join(lines), encoding="utf-8")
        return True, f"replaced at line {lineno} (singleline)"
    # 否则尝试元组: hex 行在 idx+1
    if idx + 1 < len(lines):
        m_tp = _RE_TUP_BUMP.match(lines[idx + 1].rstrip("\n"))
        if m_tp and m_tp.group("hex") == old_sha:
            new_line = m_tp.group("lead") + new_sha + m_tp.group("tail") + (
                "\n" if lines[idx + 1].endswith("\n") else ""
            )
            if dry_run:
                return True, f"would replace at line {lineno+1} (tuple)"
            lines[idx + 1] = new_line
            file_path.write_text("".join(lines), encoding="utf-8")
            return True, f"replaced at line {lineno+1} (tuple)"
    return False, f"no regex match at line {lineno} (old_sha={old_sha[:16]}...)"


def find_locks_for_target(target: Path) -> List[dict]:
    """返回所有指向 target 的反向锁条目 (scan_reverse_sha_locks 子集)。"""
    live = live_verify_sha_set(SCRIPTS)
    # target 相对仓库根的字符串 (例如 'scripts/verify_robot_025.py')
    try:
        target_rel = str(target.resolve().relative_to(REPO))
    except ValueError:
        target_rel = str(target)
    scanned = scan_reverse_sha_locks(SCRIPTS)
    out: List[dict] = []
    for item in scanned:
        if _candidates_for(target_rel, live, item["const_name"]):
            # 排除常量本身就在 target 内 (例如 verify_robot_025 里某个无关常量)
            if item["file"] == target_rel:
                continue
            out.append(item)
    return out


def run_bump(target: Path, dry_run: bool) -> Tuple[int, List[str]]:
    """主流程: 找指向 target 的反向锁 + bump 各常量到 target 新 sha。

    Returns (exit_code, report_lines)。

    (infra-V22-cascade-warn-noop-semantics) 不改本函数签名 (向后兼容已锁此签名
    的 verify_infra_046 等), 改由 ``compute_sentinel(rc, report)`` 在 main 中
    单独算出 APPLIED/NOOP 语义, 再由 main 输出 ``RESULT:`` sentinel 行。
    """
    report: List[str] = []
    if not target.is_file():
        report.append(f"FAIL: target {target} not found")
        return 2, report
    new_sha = _compute_target_sha(target)
    report.append(f"target={target.relative_to(REPO) if REPO in target.parents else target}")
    report.append(f"target_new_sha={new_sha}")
    locks = find_locks_for_target(target)
    if not locks:
        report.append("WARN: 没有反向锁指向该 target (空操作)")
        return 3, report
    report.append(f"locks_found={len(locks)}")
    any_change = False
    fail_count = 0
    applied_count = 0
    for item in locks:
        file_path = REPO / item["file"] if not Path(item["file"]).is_absolute() else Path(item["file"])
        old_sha = item["sha_hex"]
        if old_sha == new_sha:
            report.append(
                f"  - {item['file']}:{item['lineno']} {item['const_name']} = "
                f"{old_sha[:16]}... (no-op)"
            )
            continue
        changed, detail = _bump_in_file(file_path, item["lineno"], old_sha, new_sha, dry_run)
        if changed:
            any_change = True
            applied_count += 1
            prefix = "DRY-RUN" if dry_run else "APPLIED"
            report.append(
                f"  - {prefix} {item['file']}:{item['lineno']} {item['const_name']}: "
                f"{old_sha[:16]} -> {new_sha[:16]} ({detail})"
            )
        else:
            fail_count += 1
            report.append(
                f"  - FAIL {item['file']}:{item['lineno']} {item['const_name']}: {detail}"
            )
    if fail_count > 0:
        return 4, report
    if not any_change:
        report.append("OK: 所有常量已是最新, 无需 bump")
    # 附加机器可解析 marker (不破坏向后兼容: 调用方仍可 unpack 2-tuple)
    report.append(f"__APPLIED_COUNT__={applied_count}")
    return 0, report


def compute_sentinel(rc: int, report: List[str]) -> dict:
    """从 (rc, report) 算 RESULT sentinel.

    (infra-V22-cascade-warn-noop-semantics)
    """
    if rc == 2:
        return {"kind": "FAIL", "holders": 0, "reason": "target_missing"}
    if rc == 4:
        return {"kind": "FAIL", "holders": 0, "reason": "regex_miss"}
    if rc == 3:
        return {"kind": "NOOP", "holders": 0, "reason": "no_holders"}
    # rc == 0: 看 report 找 __APPLIED_COUNT__
    applied_count = 0
    for line in report:
        if line.startswith("__APPLIED_COUNT__="):
            try:
                applied_count = int(line.split("=", 1)[1])
            except ValueError:
                applied_count = 0
            break
    if applied_count > 0:
        return {"kind": "APPLIED", "holders": applied_count, "reason": "bumped"}
    return {"kind": "NOOP", "holders": 0, "reason": "all_uptodate"}


def run_verify() -> int:
    """跑 scripts/verify_infra_034.py 验 V6 pre-flight."""
    verify_034 = SCRIPTS / "verify_infra_034.py"
    if not verify_034.is_file():
        print(f"WARN: {verify_034} 不存在, 跳过 --verify", flush=True)
        return 0
    py = sys.executable
    proc = subprocess.run(
        [py, str(verify_034)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )
    print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--target", required=True, help="待刷新 target 文件路径 (相对仓库根或绝对)")
    ap.add_argument("--apply", action="store_true", help="实际写盘 (默认 dry-run)")
    ap.add_argument("--verify", action="store_true", help="bump 后跑 verify_infra_034.py")
    args = ap.parse_args(argv)
    target = Path(args.target)
    if not target.is_absolute():
        target = (REPO / target).resolve()
    dry_run = not args.apply
    rc, report = run_bump(target, dry_run=dry_run)
    sentinel = compute_sentinel(rc, report)
    for line in report:
        # 不把内部 marker 打到 stdout
        if line.startswith("__APPLIED_COUNT__="):
            continue
        print(line, flush=True)
    if rc not in (0, 3):  # 3 = 无反向锁指向, 不算 hard fail
        # 失败路径也打 RESULT 便于父进程区分
        print(
            f"RESULT: {sentinel['kind']} reason={sentinel['reason']}",
            flush=True,
        )
        return rc
    if args.verify:
        vrc = run_verify()
        if vrc != 0:
            print(f"FAIL: verify_infra_034.py rc={vrc}", flush=True)
            print(
                f"RESULT: {sentinel['kind']} reason={sentinel['reason']}",
                flush=True,
            )
            return 5
        print("OK: verify_infra_034.py PASS", flush=True)
    # 输出机器可解析 sentinel (infra-V22-cascade-warn-noop-semantics)
    if sentinel["kind"] == "APPLIED":
        print(f"RESULT: APPLIED holders={sentinel['holders']}", flush=True)
    else:
        # NOOP: no_holders / all_uptodate
        print(f"RESULT: NOOP reason={sentinel['reason']}", flush=True)
    return 0 if rc != 4 else 4


if __name__ == "__main__":
    sys.exit(main())
