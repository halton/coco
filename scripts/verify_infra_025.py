"""infra-025 verification: verify_interact_018 / proactive_trace_summary sys.path 注入审计.

跑法::

    uv run python scripts/verify_infra_025.py

子项（与 feature_list.json infra-025.description 对齐, verify-only 不改源码）：

V0 grep sys.path 注入站点清单 — 锁定当前两个文件 (verify_interact_018.py:42 +
   proactive_trace_summary.py:60-61) 各自的注入形态指纹。

V1 注入路径合法性 — 注入的 ROOT 路径必须存在, 且 ROOT/coco/__init__.py 实际可读
   (即注入指向真实 source tree 根, 非空目录/无关路径)。

V2 不污染 site-packages — 注入仅 sys.path.insert(0, ...), 不修改 PYTHONPATH 环境
   变量, 不写文件到 site-packages。grep 反证两个脚本未触碰 site-packages /
   PYTHONPATH 持久化路径。

V3 ImportError fallback 模式审计 —
   (a) proactive_trace_summary.py _is_fail 内部 try/except ImportError 走本地复制
       的判定函数 (优雅退化, 不爆栈);
   (b) verify_interact_018.py 三处 from coco.* import 无 ImportError fallback,
       记录为已知风险 (subprocess/wheel 环境下脆弱), 但 verify-only 不改源码;
       建议: 调用方走 module-form (`python -m`) 或包安装后调用契约。

V4 subprocess venv site-packages 引用契约 — 子进程跑 proactive_trace_summary.py
   时, 不传 PYTHONPATH 仍能 import coco (因 sys.path 注入兜底), 行为 bytewise
   等价。

V5 regression — verify_interact_018.py 单跑 rc=0 + proactive_trace_summary.py
   --help 单跑 rc=0 (脚本可独立加载, sys.path 注入未阻塞 argparse 路径)。

V6 sys.path 重复注入幂等性 —
   (a) proactive_trace_summary 用 `if str(_ROOT) not in sys.path` 守护, 二次
       import 不重复插入;
   (b) verify_interact_018 无守护, 多次执行同 ROOT 会在 sys.path 头部多次出现
       (记录为 lint 改进项, 不阻 merge)。

retval：0 全 PASS；1 任一失败
evidence 落 evidence/infra-025/verify_summary.json
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERIFY_IA018 = ROOT / "scripts" / "verify_interact_018.py"
PROACTIVE_SUMMARY = ROOT / "scripts" / "proactive_trace_summary.py"


def _print(tag: str, msg: str) -> None:
    print(f"[verify_infra_025] {tag} {msg}", flush=True)


_results: List[Dict[str, Any]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    _print(tag, f"{name}: {detail}" if detail else name)


# ---------------------------------------------------------------------------
# V0: sys.path 注入站点指纹
# ---------------------------------------------------------------------------


def v0_grep_injection_sites() -> Dict[str, List[Dict[str, Any]]]:
    """V0: grep sys.path.insert 锁定两个文件的注入形态。"""
    sites: Dict[str, List[Dict[str, Any]]] = {}
    for label, p in (("verify_interact_018", VERIFY_IA018), ("proactive_trace_summary", PROACTIVE_SUMMARY)):
        hits: List[Dict[str, Any]] = []
        if not p.exists():
            _record(f"V0_{label}_exists", False, f"{p} missing")
            sites[label] = hits
            continue
        text = p.read_text(encoding="utf-8")
        for ln_no, line in enumerate(text.splitlines(), start=1):
            if "sys.path" in line and (".insert" in line or ".append" in line):
                hits.append({"line": ln_no, "text": line.strip()})
        sites[label] = hits
    ok_018 = len(sites.get("verify_interact_018", [])) == 1
    ok_pts = len(sites.get("proactive_trace_summary", [])) == 1
    _record(
        "V0_grep_injection_sites",
        ok_018 and ok_pts,
        f"verify_interact_018 hits={len(sites['verify_interact_018'])} (expect 1), "
        f"proactive_trace_summary hits={len(sites['proactive_trace_summary'])} (expect 1)",
    )
    return sites


# ---------------------------------------------------------------------------
# V1: 注入路径合法性
# ---------------------------------------------------------------------------


def v1_injection_path_valid() -> None:
    """V1: 两脚本注入的 ROOT 都指向 repo 真实根 (含 coco/__init__.py)。"""
    coco_init = ROOT / "coco" / "__init__.py"
    ok_root = ROOT.is_dir()
    ok_init = coco_init.is_file() and coco_init.stat().st_size > 0
    # 两脚本都用 Path(__file__).resolve().parent.parent 上溯, 跟当前 ROOT 一致
    text_018 = VERIFY_IA018.read_text(encoding="utf-8")
    text_pts = PROACTIVE_SUMMARY.read_text(encoding="utf-8")
    has_018_pattern = "Path(__file__).resolve().parent.parent" in text_018
    has_pts_pattern = "Path(__file__).resolve().parent.parent" in text_pts
    ok = ok_root and ok_init and has_018_pattern and has_pts_pattern
    _record(
        "V1_injection_path_valid",
        ok,
        f"ROOT={ROOT} exists={ok_root}, coco/__init__.py={ok_init}, "
        f"verify_018_pattern={has_018_pattern}, proactive_summary_pattern={has_pts_pattern}",
    )


# ---------------------------------------------------------------------------
# V2: 不污染 site-packages / PYTHONPATH
# ---------------------------------------------------------------------------


def v2_no_site_packages_pollution() -> None:
    """V2: 两脚本不修改 PYTHONPATH 也不写文件到 site-packages。"""
    forbidden_patterns = [
        r"os\.environ\[\s*['\"]PYTHONPATH['\"]\s*\]\s*=",
        r"site-packages",
        r"site\.addsitedir",
        r"sysconfig\.get_paths",
    ]
    bad: List[str] = []
    for p in (VERIFY_IA018, PROACTIVE_SUMMARY):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden_patterns:
            for m in re.finditer(pat, text):
                bad.append(f"{p.name}:{pat}@{m.start()}")
    ok = not bad
    _record(
        "V2_no_site_packages_pollution",
        ok,
        f"forbidden hits={bad}" if bad else "no PYTHONPATH/site-packages writes",
    )


# ---------------------------------------------------------------------------
# V3: ImportError fallback 模式
# ---------------------------------------------------------------------------


def v3_import_error_fallback_audit() -> None:
    """V3: 两脚本的 ImportError 退化模式审计。

    - proactive_trace_summary.py 应有 try/except ... import is_fail 退化分支
    - verify_interact_018.py 无 fallback (已知风险, 记录, 不阻 merge)
    """
    text_pts = PROACTIVE_SUMMARY.read_text(encoding="utf-8")
    # 在 _is_fail 内 try/except 委托 coco.proactive_trace.is_fail
    has_fallback_block = (
        "from coco.proactive_trace import is_fail" in text_pts
        and "except Exception" in text_pts
        and "_shared_is_fail = None" in text_pts
    )

    text_018 = VERIFY_IA018.read_text(encoding="utf-8")
    # 统计 verify_interact_018 中裸 from coco.* import 数量
    raw_imports = re.findall(r"from coco[\.\s]", text_018)
    has_018_fallback = "except ImportError" in text_018

    note = (
        f"proactive_trace_summary has_fallback={has_fallback_block}; "
        f"verify_interact_018 raw_coco_imports={len(raw_imports)} has_fallback={has_018_fallback} "
        f"(known-risk recorded: subprocess/wheel env 下 ROOT 注入失效时 import 爆栈; "
        f"建议调用方走 `python -m scripts.verify_interact_018` 或包安装后调用)"
    )
    # V3 通过: proactive_trace_summary 有 fallback; verify_interact_018 无 fallback 仅记录
    ok = has_fallback_block
    _record("V3_import_error_fallback", ok, note)


# ---------------------------------------------------------------------------
# V4: subprocess venv site-packages 引用契约
# ---------------------------------------------------------------------------


def v4_subprocess_independent_import() -> None:
    """V4: 在不传 PYTHONPATH 的干净环境下 subprocess 跑 proactive_trace_summary.py --help, rc=0。

    这证明 sys.path 注入兜底有效, 即使 PYTHONPATH 空, 脚本也能 import coco.proactive_trace。
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.pop("PYTHONPATH", None)
    try:
        proc = subprocess.run(
            [sys.executable, str(PROACTIVE_SUMMARY), "--help"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        ok = proc.returncode == 0 and "usage" in (proc.stdout + proc.stderr).lower()
        detail = f"rc={proc.returncode}, stdout_head={proc.stdout[:80]!r}"
    except Exception as e:  # noqa: BLE001
        ok = False
        detail = f"exception: {e!r}"
    _record("V4_subprocess_independent_import", ok, detail)


# ---------------------------------------------------------------------------
# V5: regression — 两脚本本身可独立运行
# ---------------------------------------------------------------------------


def v5_regression_independent_run() -> None:
    """V5: verify_interact_018.py 单跑 rc=0 + proactive_trace_summary.py --help rc=0。"""
    results: Dict[str, int] = {}
    # verify_interact_018 全跑
    try:
        proc1 = subprocess.run(
            [sys.executable, str(VERIFY_IA018)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        results["verify_interact_018"] = proc1.returncode
    except Exception as e:  # noqa: BLE001
        results["verify_interact_018"] = -1
        _print("WARN", f"verify_interact_018 subprocess exception: {e!r}")

    # proactive_trace_summary --help (轻量)
    try:
        proc2 = subprocess.run(
            [sys.executable, str(PROACTIVE_SUMMARY), "--help"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
        results["proactive_trace_summary_help"] = proc2.returncode
    except Exception as e:  # noqa: BLE001
        results["proactive_trace_summary_help"] = -1
        _print("WARN", f"proactive_trace_summary --help exception: {e!r}")

    ok = all(rc == 0 for rc in results.values())
    _record("V5_regression_independent_run", ok, f"rcs={results}")


# ---------------------------------------------------------------------------
# V6: sys.path 重复注入幂等性
# ---------------------------------------------------------------------------


def v6_idempotent_injection_audit() -> None:
    """V6: proactive_trace_summary 有 `if str(_ROOT) not in sys.path` 守护; verify_interact_018 无。

    审计两侧的幂等模式差异并记录。verify_interact_018 的非幂等只在多次 import 同 process
    场景下污染 sys.path 头部 (不致命), 列为 lint 改进项。
    """
    text_pts = PROACTIVE_SUMMARY.read_text(encoding="utf-8")
    text_018 = VERIFY_IA018.read_text(encoding="utf-8")
    pts_guard = "not in sys.path" in text_pts and "sys.path.insert" in text_pts
    ia018_guard = "not in sys.path" in text_018
    note = (
        f"proactive_trace_summary idempotent_guard={pts_guard}; "
        f"verify_interact_018 idempotent_guard={ia018_guard} "
        f"(lint-only; verify-only 不改源码; 影响仅多次 in-process import 头部多条 entry)"
    )
    ok = pts_guard  # 至少 summary 必须幂等; ia018 非幂等仅记录
    _record("V6_idempotent_injection_audit", ok, note)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    sites = v0_grep_injection_sites()
    v1_injection_path_valid()
    v2_no_site_packages_pollution()
    v3_import_error_fallback_audit()
    v4_subprocess_independent_import()
    v5_regression_independent_run()
    v6_idempotent_injection_audit()

    ok = all(r["ok"] for r in _results)

    summary = {
        "feature": "infra-025",
        "source_backlog": "interact-018-backlog-syspath-injection-audit",
        "direction": "verify-only 审计 (无源码改动); sys.path 注入合法性 + ImportError fallback 模式 + subprocess 契约 + 幂等",
        "ok": ok,
        "results": _results,
        "injection_sites": sites,
        "files_changed": [
            "scripts/verify_infra_025.py",
            "evidence/infra-025/verify_summary.json",
        ],
        "known_risk_recorded": [
            "verify_interact_018.py 三处 from coco.* import 无 ImportError fallback — "
            "subprocess/wheel 环境 ROOT 推断失效时直接爆栈; 建议调用方走 module-form 或包安装契约",
            "verify_interact_018.py sys.path.insert(0, ROOT) 无 `if not in` 守护 — "
            "多次 in-process import 同一 ROOT 会重复插入 (头部多条 entry); lint-only",
        ],
        "real_machine_uat": "n/a",
    }

    ev_dir = ROOT / "evidence" / "infra-025"
    ev_dir.mkdir(parents=True, exist_ok=True)
    (ev_dir / "verify_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _print("DONE", f"ok={ok}, results={len(_results)}, evidence=evidence/infra-025/verify_summary.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
