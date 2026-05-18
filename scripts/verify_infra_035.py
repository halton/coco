#!/usr/bin/env python3
"""infra-035 verify — v4_sha 外置 + bump 助手 meta-lock.

来源: infra-034-backlog-v4-sha-table-externalize
范围: docs-only / verify-only / 不修业务源码. infra-034 V4 sha 锁表从
verify_infra_034.py 内 hardcoded EXPECTED_SHA dict 改为读取
evidence/infra-034/v4_sha.json; 新增 scripts/bump_infra_034_v4_sha.py 维护该
JSON, 风格对齐 bump_verify_027/028_self_hash.py.

V0 evidence/infra-034/v4_sha.json 存在 + schema:
    version:int, targets:dict, 每项 value 为 64-hex sha256 串.
V1 v4_sha.json 中列出的 5 个 target verify 脚本都存在.
V2 sha256 锁 scripts/verify_infra_034.py 整体 (锁 V4 外置实现段所在文件).
V3 mutant 反证: 临时把 v4_sha.json 中第一个 target 的 sha 末位翻转 →
    subprocess 调 verify_infra_034.py rc != 0; finally 还原文件原样.
V4 sha256 锁 scripts/bump_infra_034_v4_sha.py 整体.
V5 subprocess `python scripts/bump_infra_034_v4_sha.py --dry-run` rc==0 且
    stdout 含 "已一致".

默认 OFF 严守: 本 verify 不引入新 env hook, 不依赖网络, 不修改业务源码.

运行环境约定 (infra-035)
------------------------
本脚本及其 V3/V5 子进程**必须**在已激活的 .venv 下运行 (Python 解释器入口
``.venv/bin/python``); 不要用系统 ``python3`` 直接调用本脚本, 否则
``importlib`` 加载业务模块时依赖 (numpy / soundfile / onnxruntime 等) 可能
解析到系统站点而非 venv 站点, 导致与 ``./init.sh`` smoke 路径不一致,
进而 V0-V5 退出码漂移.

约定细则:
  - **Reviewer / CI / 手动复跑入口**: 一律 ``.venv/bin/python`` 启动 (或先
    ``source .venv/bin/activate`` 再 ``python scripts/verify_infra_035.py``).
  - **子进程 invoke**: 任何 ``subprocess.run`` 第一参数固定使用 ``sys.executable``
    (即本脚本所属解释器); 不写死 ``"python"`` / ``"python3"`` 字面量.
  - **环境变量继承**: 子进程从 ``os.environ`` 拷贝 PATH / PYTHONPATH 等,
    PATH 中 venv 的 ``bin`` 目录位置不可被人为打乱 (init.sh 已在激活时前置).
  - **新会话注意事项**: 干净 shell 进来务必先 ``source .venv/bin/activate``
    或显式 ``./.venv/bin/python``.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
V4_SHA_JSON = ROOT / "evidence" / "infra-034" / "v4_sha.json"
VERIFY_034 = ROOT / "scripts" / "verify_infra_034.py"
BUMP_034 = ROOT / "scripts" / "bump_infra_034_v4_sha.py"

# V2/V4 整体 sha 锁 (本 infra-035 闭锚)
VERIFY_034_EXPECTED_SHA = (
    "643e503d19cb94df943363ee684fec388089d084d7511d77e65241921ea16119"
)
BUMP_034_EXPECTED_SHA = (
    "fe26902eb371872eb8e8227a3d0985ef71df2fb63cbbf60c88a9ea4d23b1e744"
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

_results: List[Tuple[str, bool, str]] = []


def _emit(tag: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[verify_infra_035][{mark}] {tag} {detail}", flush=True)
    _results.append((tag, ok, detail))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# V0: v4_sha.json 存在 + schema
# ---------------------------------------------------------------------------
def v0_schema() -> None:
    if not V4_SHA_JSON.is_file():
        _emit("V0_v4_sha_json_exists", False, f"missing {V4_SHA_JSON}")
        return
    _emit("V0_v4_sha_json_exists", True, str(V4_SHA_JSON.relative_to(ROOT)))
    try:
        data = json.loads(V4_SHA_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        _emit("V0_parse", False, f"json parse failed: {e}")
        return
    _emit("V0_parse", True, "json ok")

    ok_v = isinstance(data.get("version"), int)
    _emit("V0_schema_version_int", ok_v, f"version={data.get('version')!r}")

    targets = data.get("targets")
    ok_t = isinstance(targets, dict) and len(targets) > 0
    _emit("V0_schema_targets_dict", ok_t, f"len={len(targets) if isinstance(targets, dict) else 'N/A'}")
    if not ok_t:
        return
    for k, v in targets.items():
        ok = isinstance(v, str) and bool(_HEX64.match(v))
        _emit(f"V0_schema_sha_{k}", ok, f"sha={str(v)[:16]}")


# ---------------------------------------------------------------------------
# V1: target verify 文件都存在
# ---------------------------------------------------------------------------
def v1_targets_exist() -> None:
    if not V4_SHA_JSON.is_file():
        _emit("V1_targets_exist", False, "v4_sha.json missing")
        return
    targets = json.loads(V4_SHA_JSON.read_text(encoding="utf-8")).get("targets", {})
    for rel in targets:
        p = ROOT / rel
        _emit(f"V1_{rel}_exists", p.is_file(), str(p))


# ---------------------------------------------------------------------------
# V2: sha256 锁 verify_infra_034.py 整体
# ---------------------------------------------------------------------------
def v2_lock_verify_034() -> None:
    if not VERIFY_034.is_file():
        _emit("V2_lock_verify_034", False, "verify_infra_034.py missing")
        return
    got = _sha256(VERIFY_034)
    _emit(
        "V2_lock_verify_034",
        got == VERIFY_034_EXPECTED_SHA,
        f"got={got[:16]} expect={VERIFY_034_EXPECTED_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V3: mutant 反证 — 改 v4_sha.json 后 verify_infra_034 V4 FAIL
# ---------------------------------------------------------------------------
def v3_mutant() -> None:
    if not V4_SHA_JSON.is_file():
        _emit("V3_mutant", False, "v4_sha.json missing")
        return
    original = V4_SHA_JSON.read_bytes()
    try:
        data = json.loads(original.decode("utf-8"))
        targets = data.get("targets", {})
        if not targets:
            _emit("V3_mutant", False, "no targets to mutate")
            return
        first_key = sorted(targets.keys())[0]
        old_sha = targets[first_key]
        # 末位翻转 (0->1, 其它->0); 保持 64-hex
        last = old_sha[-1]
        new_last = "0" if last != "0" else "1"
        mutated_sha = old_sha[:-1] + new_last
        data["targets"][first_key] = mutated_sha
        V4_SHA_JSON.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(VERIFY_034)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        ok = proc.returncode != 0
        _emit(
            "V3_mutant_v4_fails",
            ok,
            f"verify_infra_034 rc={proc.returncode} (expect !=0 under mutant)",
        )
    finally:
        V4_SHA_JSON.write_bytes(original)
    # 还原后再校验一次原样
    restored = V4_SHA_JSON.read_bytes() == original
    _emit("V3_mutant_restore", restored, "v4_sha.json restored byte-equal")


# ---------------------------------------------------------------------------
# V4: sha256 锁 bump_infra_034_v4_sha.py 整体
# ---------------------------------------------------------------------------
def v4_lock_bump() -> None:
    if not BUMP_034.is_file():
        _emit("V4_lock_bump", False, "bump_infra_034_v4_sha.py missing")
        return
    got = _sha256(BUMP_034)
    _emit(
        "V4_lock_bump",
        got == BUMP_034_EXPECTED_SHA,
        f"got={got[:16]} expect={BUMP_034_EXPECTED_SHA[:16]}",
    )


# ---------------------------------------------------------------------------
# V5: subprocess bump --dry-run rc==0 且 stdout 含 "已一致"
# ---------------------------------------------------------------------------
def v5_bump_dryrun() -> None:
    if not BUMP_034.is_file():
        _emit("V5_bump_dryrun", False, "bump_infra_034_v4_sha.py missing")
        return
    proc = subprocess.run(
        [sys.executable, str(BUMP_034), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    rc_ok = proc.returncode == 0
    has_phrase = "已一致" in proc.stdout
    _emit(
        "V5_bump_dryrun",
        rc_ok and has_phrase,
        f"rc={proc.returncode} 已一致_in_stdout={has_phrase}",
    )


def main() -> int:
    v0_schema()
    v1_targets_exist()
    v2_lock_verify_034()
    v3_mutant()
    v4_lock_bump()
    v5_bump_dryrun()

    failed = [t for t, ok, _ in _results if not ok]
    total = len(_results)
    print(f"[verify_infra_035] summary total={total} failed={len(failed)}", flush=True)
    if failed:
        print(f"[verify_infra_035] failed tags: {failed}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
