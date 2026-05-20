#!/usr/bin/env python3
"""infra-032 verify: V0 truthy 反证表 meta-lock — 锁定 verify_infra_024 V0
扩充的 OFF/ON 变体集合不被静默回退。

来源 backlog: infra-024-backlog-v0-extra-truthy-edges
    "verify_infra_024.py V0 真值表 (1/true/yes ON;TRUE/0/空 OFF) 还有边界字符串没覆盖:
     '2' (非空数字字符串但非 truthy), 'yes\\n' (含尾换行 — 严格 strip 后变 'yes', 实际
     是 truthy, 反直觉), ' 1 ' (含前后空白 — 严格 strip 后 truthy), 'TRUE' (大写大小写敏感)。
     建议升 V0: 把这一类 OFF 反证全列入显式表，避免漏判某个变体被静默回退。"

Acceptance
----------
V0 sys.path / 关键文件存在 (verify_infra_024.py + scripts/smoke.py)。
V1 字面 sentinel — verify_infra_024.py 必须含 OFF/ON 反证表关键变体字面值,
    禁止被未来重构悄悄删表。
V2 关键锁参数 — OFF 表 ≥ 25 项 / ON 表 ≥ 10 项; 关键字面 "2" / "yes\\n" /
    " 1 " / "TRUE" / "on" 各出现 ≥ 1 次 (覆盖任务清单核心边界)。
V3 mutant 反证 — 临时去掉表中关键变体 (如 "2") 写入 sandbox copy 后跑改后脚本,
    断言 V0 覆盖断面 FAIL (即原本应 OFF 的 '2' 现在被未列入表, V0 不再覆盖)。
    用脚本文本断面替代 import-time mutation, sandbox 隔离, 不污染原脚本。
V4 sha256 锁 — 锁住升级后 verify_infra_024.py 的内容 hash, 任何修改 (包括无心
    格式化) 必须主动更新本 V4 锁字面值, 视为契约级改动。
V5 端到端 subprocess invoke — subprocess.run(verify_infra_024.py) rc==0,
    stdout 含 "[V0_static_gate] PASS", 闭环锁住 V0 表实际可执行 + 通过。

Sim-first: 纯 verify-only, 不动业务源码, 不改 smoke.py 行为。
Default-OFF 严守: 本验证只读 / 加锁, 0 业务行为变更。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "evidence" / "infra-032"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
TARGET = REPO_ROOT / "scripts" / "verify_infra_024.py"
SMOKE = REPO_ROOT / "scripts" / "smoke.py"

# 锁住升级后 verify_infra_024.py 的 sha256 (V4)。
# 任何修改 (含格式化) 都必须主动更新此值, 强制把"表的变化"提到 review 视野。
EXPECTED_VERIFY_024_SHA256 = "eb61b07104dc68d54a9c88e2533b6ad37c392dd42a83338e095d6bc5c9d654b1"

# 关键 sentinel 字面 (V1): 这些字符串字面 *必须* 仍在 verify_infra_024.py 内,
# 缺一个就 FAIL — 防止未来重构静默吞掉表项。
REQUIRED_OFF_LITERALS = [
    '"2"',         # infra-032 核心边界
    '"on"',        # 用户直觉 truthy, 实为 OFF
    '"TRUE"',      # 大写大小写敏感
    '"True"',
    '"YES"',
    '"y"',
    '"01"',
    '"1.0"',
    '"enable"',
]
REQUIRED_ON_LITERALS = [
    '"1"',
    '"true"',
    '"yes"',
    '" 1 "',       # 前后空白 + 1
    '"yes\\n"',    # 尾换行
    '"1\\n"',
]


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def v0_paths_exist() -> dict:
    _ok(TARGET.exists(), f"V0: target verify_infra_024.py missing at {TARGET}")
    _ok(SMOKE.exists(), f"V0: scripts/smoke.py missing at {SMOKE}")
    _ok(sys.version_info >= (3, 9), f"V0: python ≥3.9 required, got {sys.version_info}")
    return {"target": str(TARGET), "smoke": str(SMOKE), "size_bytes": TARGET.stat().st_size}


def v1_literal_sentinels() -> dict:
    text = TARGET.read_text(encoding="utf-8")
    missing_off = [lit for lit in REQUIRED_OFF_LITERALS if lit not in text]
    missing_on = [lit for lit in REQUIRED_ON_LITERALS if lit not in text]
    _ok(not missing_off,
        f"V1: required OFF literals missing from verify_infra_024.py: {missing_off}")
    _ok(not missing_on,
        f"V1: required ON literals missing from verify_infra_024.py: {missing_on}")
    return {
        "off_literals_required": REQUIRED_OFF_LITERALS,
        "on_literals_required": REQUIRED_ON_LITERALS,
        "all_present": True,
    }


def v2_lock_params() -> dict:
    """锁关键阈值: OFF 表 ≥25 项 / ON 表 ≥10 项 (粗略计数, 用正则截取列表块)。"""
    text = TARGET.read_text(encoding="utf-8")

    def _count_block(var_name: str) -> int:
        m = re.search(rf"{var_name}\s*=\s*\[(.*?)\]", text, re.DOTALL)
        if not m:
            return 0
        body = m.group(1)
        # 计字符串字面数量 (单/双引号)
        return len(re.findall(r'"[^"]*"', body))

    off_count = _count_block("off_variants")
    on_count = _count_block("on_variants")
    _ok(off_count >= 25,
        f"V2: off_variants count {off_count} < 25 floor (infra-032 表必须覆盖广)")
    _ok(on_count >= 10,
        f"V2: on_variants count {on_count} < 10 floor")
    return {"off_count": off_count, "on_count": on_count,
            "floor_off": 25, "floor_on": 10}


def v3_mutant_negation() -> dict:
    """反证: 把关键边界 "2" 从 off_variants 整块中移除 → 改后脚本应仍 PASS
    (因为 "2" 不再被表覆盖, V0 不再检查它). 但 V1 sentinel 检查 (literal '"2"')
    应当 FAIL.

    用此构造: sandbox copy verify_infra_024.py + 单点删字面 → 跑本脚本 V1 必 FAIL.
    """
    text = TARGET.read_text(encoding="utf-8")
    # 简化: 直接在 text 里把首个 '"2",' 替换为 '#removed,', 走 verify_infra_032 自身 V1
    # 在 sandbox 上跑, 期望 V1 FAIL (sentinel "2" 不存在)。
    mutated = text.replace('"2",', '#"2_removed",', 1)
    _ok(mutated != text, "V3: failed to mutate '\"2\",' sentinel — text unchanged")

    with tempfile.TemporaryDirectory(prefix="infra032-mutant-") as td:
        # 构造同结构 sandbox: scripts/verify_infra_024.py + 把本脚本指向 sandbox
        sandbox = Path(td)
        (sandbox / "scripts").mkdir()
        (sandbox / "scripts" / "verify_infra_024.py").write_text(mutated, encoding="utf-8")
        # 复制 smoke.py (V1 检查只读字面, 不需真执行)
        (sandbox / "scripts" / "smoke.py").write_text(SMOKE.read_text(encoding="utf-8"), encoding="utf-8")
        # 复制本脚本进 sandbox/scripts/
        my_text = Path(__file__).read_text(encoding="utf-8")
        (sandbox / "scripts" / "verify_infra_032.py").write_text(my_text, encoding="utf-8")
        # 只跑 V1 段: 用 subprocess 调 sandbox 副本, 期望 rc != 0 + "[V1_literal_sentinels] FAIL"
        proc = subprocess.run(
            [sys.executable, str(sandbox / "scripts" / "verify_infra_032.py")],
            capture_output=True, text=True, timeout=120,
        )
    _ok(proc.returncode != 0,
        f"V3 mutant: expected sandbox rc != 0 after removing '\"2\",', got rc={proc.returncode}\nstdout:\n{proc.stdout[-800:]}")
    _ok("FAIL" in proc.stdout and "V1" in proc.stdout,
        f"V3 mutant: expected V1 FAIL marker in stdout, got:\n{proc.stdout[-800:]}")
    return {"mutant_rc": proc.returncode,
            "stdout_tail": proc.stdout[-400:]}


def v4_sha256_lock() -> dict:
    actual = _sha256(TARGET)
    _ok(EXPECTED_VERIFY_024_SHA256 == actual,
        f"V4 sha256 lock: verify_infra_024.py hash drift.\n"
        f"  expected: {EXPECTED_VERIFY_024_SHA256}\n"
        f"  actual:   {actual}\n"
        f"如属正常升级, 把 EXPECTED_VERIFY_024_SHA256 更新为 actual 并 commit.")
    return {"sha256": actual}


def v5_e2e_subprocess() -> dict:
    proc = subprocess.run(
        [sys.executable, str(TARGET)],
        capture_output=True, text=True, timeout=600,
        cwd=str(REPO_ROOT),
    )
    _ok(proc.returncode == 0,
        f"V5 e2e: verify_infra_024.py rc != 0 (got {proc.returncode}).\nstdout tail:\n{proc.stdout[-1200:]}\nstderr tail:\n{proc.stderr[-500:]}")
    _ok("[V0_static_gate] PASS" in proc.stdout,
        f"V5 e2e: expected '[V0_static_gate] PASS' in stdout, got:\n{proc.stdout[-1200:]}")
    return {"rc": proc.returncode, "stdout_len": len(proc.stdout)}


def main() -> int:
    results: dict = {}
    failures: list[str] = []
    cases = [
        ("V0_paths_exist", v0_paths_exist),
        ("V1_literal_sentinels", v1_literal_sentinels),
        ("V2_lock_params", v2_lock_params),
        ("V3_mutant_negation", v3_mutant_negation),
        ("V4_sha256_lock", v4_sha256_lock),
        ("V5_e2e_subprocess", v5_e2e_subprocess),
    ]
    for vname, fn in cases:
        try:
            results[vname] = {"status": "PASS", "detail": fn()}
            print(f"[{vname}] PASS")
        except AssertionError as e:
            results[vname] = {"status": "FAIL", "error": str(e)}
            failures.append(f"{vname}: {e}")
            print(f"[{vname}] FAIL: {e}")

    summary = {
        "feature": "infra-032",
        "status": "PASS" if not failures else "FAIL",
        "results": results,
        "failures": failures,
    }
    out = EVIDENCE_DIR / "verify_summary.json"
    out.write_text(json.dumps(summary, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"\nwrote {out}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
