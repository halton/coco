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
V6 v4_sha.json canonical 字节序锁 (infra-035-backlog #5.50): 文件内容 ==
    json.dumps(loaded, ensure_ascii=False, indent=2, sort_keys=True) + "\n";
    并断言 bump 助手源码包含 sort_keys=True 字面量 (防回退至 sort_keys=False).
V7 bump 助手原子写锁 (infra-035-backlog-bump-atomic-write #1.53): bump 助手
    源码必须含 `os.replace(` + `.tmp` 字面量 + `import os`, 锁住 tmp+rename
    原子写模式 (防回退至 write_text 直写半文件).
V8 verify_infra_035 自体 sha 锁 (infra-035-backlog-verify-infra-035-self-hash
    phase-59 #2): 对本脚本自身整体 sha256 锁住, 防 docstring / 逻辑悄悄漂移
    而无 verify 红线. 自指处理: 计算 sha 时严格剔除行尾带 pragma
    标记 (hash)+空格+``V8-SELF-SHA-SKIP`` 的那一行 (见 infra-P305 phase-60 #5,
    把宽松子串匹配收紧到显式 pragma 锚), 其余字节参与 hash.
    设计参考 verify_interact_036b.py V0 schema metadata 字段语义.
V9 V8 pragma cardinality 机械断言 (infra-P305-backlog-pragma-cardinality-
    mechanical-assert, phase-61 #1): 全文 rstrip 后 endswith
    ``# V8-SELF-SHA-SKIP`` 的行数必须恰好 == 1. 若 future commit / drift /
    attacker 不慎或恶意插入第二条 pragma 行 (例如把 pragma 复制到伪常量或
    docstring 上), V8 skip 集合会被悄悄扩大形成绕过 surface, 而 V8 自身仍
    PASS (因伪行被一并剔除, sha 等式仍成立). V9 在 V8 之外独立锁住 pragma
    行的 *cardinality*, 任何 ≠1 立即红线. verify-only, 不改 V8 计算逻辑.
V10 V8 pragma 必须挂在真常量行机械断言 (infra-P305-backlog-pragma-attached-
    to-real-constant-only, phase-61 #2): V9 已锁 pragma 行数 == 1, V10 进
    一步锁住"那唯一一条 pragma 行必须是 ``EXPECTED_SELF_FILE_SHA`` 真常量
    定义行"——堵住"把唯一一条 pragma 移到一个无关行(例如注释)"的盲点.
    匹配正则 ``^EXPECTED_SELF_FILE_SHA\\s*=\\s*"[0-9a-f]{64}"\\s*#\\s*V8-
    SELF-SHA-SKIP\\s*$``. verify-only, 不改 V8/V9 逻辑.

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
# infra-V6-backlog bump (P264): V6 helper 抽到 _verify_lib 后 verify_infra_034 sha 变更
# infra-036-batch-3 bump (phase-58 #2): TARGETS 15→20 + 注释续推, verify_infra_034 sha 再变更
VERIFY_034_EXPECTED_SHA = (
    "0058fbead1d15b993d1882933f9abe4a94b2990906c449a60eaab8588ee20eea"
)
BUMP_034_EXPECTED_SHA = (
    "431881e31551aadffc9c7b4ad15ae5855d2f9ccab53931a7fc1eecc598f0c4e9"
)

# V8 自体 sha 锁 (infra-035-backlog-verify-infra-035-self-hash, phase-59 #2)
# 计算时严格剔除行尾带 pragma `(hash) V8-SELF-SHA-SKIP` 标记的那一行 (见
# infra-P305-backlog-v8-self-sha-stricter-sentinel-pragma, phase-60 #5).
# 仅 EXPECTED_SELF_FILE_SHA 真常量行打 pragma; 其余字节(含 docstring / 注释 /
# 逻辑) 一律纳入 sha256 -> 任何漂移都会触发 V8 红线, 不再被宽松子串绕过.
# 元信息字段语义参考 verify_interact_036b.py V0 schema metadata.
V8_SELF_SHA_LOCK_VERSION = 4
V8_SELF_SHA_LOCK_BUMPED_AT = "2026-05-24"
EXPECTED_SELF_FILE_SHA = "a5901bb7a3ae9b8a373a79b58096f257c5d601fb7006254be9dcea1831814d73"  # V8-SELF-SHA-SKIP

SELF = Path(__file__).resolve()

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


# ---------------------------------------------------------------------------
# V6: v4_sha.json canonical 字节序锁 (infra-035-backlog #5.50)
# ---------------------------------------------------------------------------
def v6_canonical_bytes() -> None:
    """锁 v4_sha.json bytewise == json.dumps(loaded, sort_keys=True) + "\\n".

    防 v4_sha.json 键序在不同 Python 版本 / dict 实现 / 手工编辑下漂移,
    干扰 diff 与 sha 锁链根节点稳定性. 同时锁 bump 助手源码含
    ``sort_keys=True`` 字面量, 防回退至 ``sort_keys=False``.
    """
    if not V4_SHA_JSON.is_file():
        _emit("V6_canonical_bytes", False, "v4_sha.json missing")
        return
    raw = V4_SHA_JSON.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except Exception as e:
        _emit("V6_canonical_bytes", False, f"json parse failed: {e}")
        return
    canonical = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _emit(
        "V6_canonical_bytes",
        raw == canonical,
        f"raw_len={len(raw)} canonical_len={len(canonical)} match={raw == canonical}",
    )

    if not BUMP_034.is_file():
        _emit("V6_bump_sort_keys_true", False, "bump_infra_034_v4_sha.py missing")
        return
    bump_src = BUMP_034.read_text(encoding="utf-8")
    has_sort_true = "sort_keys=True" in bump_src
    has_sort_false = "sort_keys=False" in bump_src
    _emit(
        "V6_bump_sort_keys_true",
        has_sort_true and not has_sort_false,
        f"sort_keys=True_in_bump={has_sort_true} sort_keys=False_in_bump={has_sort_false}",
    )


# ---------------------------------------------------------------------------
# V7: bump 助手原子写模式锁 (infra-035-backlog-bump-atomic-write #1.53)
# ---------------------------------------------------------------------------
def v7_bump_atomic_write() -> None:
    """锁 bump 助手原子写 (tmp + os.replace) 模式.

    防回退至 ``V4_SHA_JSON.write_text(...)`` 直写, 后者在 SIGKILL / 磁盘满 /
    解释器崩溃时会留半截 JSON, 破坏 v4_sha.json 作为 sha 锁链根节点的
    可解析不变量. 锁住三个 sentinel:
      - ``import os`` (引入 os.replace 的命名空间)
      - ``os.replace(`` (原子 rename 调用)
      - ``.tmp`` 字面量 (sibling tmp 文件后缀)
    """
    if not BUMP_034.is_file():
        _emit("V7_bump_atomic_write", False, "bump_infra_034_v4_sha.py missing")
        return
    bump_src = BUMP_034.read_text(encoding="utf-8")
    has_import_os = re.search(r"^import os(\s|$)", bump_src, re.MULTILINE) is not None
    has_replace = "os.replace(" in bump_src
    has_tmp_suffix = ".tmp" in bump_src
    ok = has_import_os and has_replace and has_tmp_suffix
    _emit(
        "V7_bump_atomic_write",
        ok,
        f"import_os={has_import_os} os.replace={has_replace} .tmp={has_tmp_suffix}",
    )


# ---------------------------------------------------------------------------
# V8: verify_infra_035 自体 sha 锁 (infra-035-backlog-verify-infra-035-self-hash)
# ---------------------------------------------------------------------------
def _self_sha_skip_sentinel() -> str:
    """计算本脚本自体 sha256, 严格剔除行尾带 pragma ``V8-SELF-SHA-SKIP`` 的行.

    自指处理 (infra-P305 phase-60 #5 收紧版): 旧实现用 ``"EXPECTED_SELF_FILE_SHA = "``
    子串匹配剔除, 命中 5 行 (注释/docstring/常量/实现/函数体内同名引用), skip
    集合过宽; 新实现要求行尾必须是 pragma 锚 ``(hash) V8-SELF-SHA-SKIP``
    (rstrip 后 endswith), 这样 skip 集合精确到 1 行 = 真常量行. 任何
    其它字节 (docstring / 逻辑微调 / 注释漂移) 都进入 sha256.
    """
    raw = SELF.read_text(encoding="utf-8")
    lines = raw.splitlines(keepends=True)
    _PRAGMA = "# V8-SELF-SHA-SKIP"
    kept = [ln for ln in lines if not ln.rstrip().endswith(_PRAGMA)]
    blob = "".join(kept).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def v8_self_file_sha_lock() -> None:
    """锁本脚本自身整体 sha (跳过 sentinel 行) 防 docstring / 逻辑漂移.

    与 V2/V4 对外部脚本的 sha 锁互补: 那些是被 sha 锁住的目标, 而本脚本
    (锁的发起者) 此前未被任何 sha 锁监控, docstring 微调即可悄悄改变,
    无 verify 红线. V8 补齐这一漏洞. bump 路径: 把 EXPECTED_SELF_FILE_SHA
    设回占位 ``"__PLACEHOLDER_WILL_BE_FILLED__"`` (保留行尾 pragma 标记)
    跑一次, 把日志里 actual 回填即可 (因 pragma 行整行剔除, 占位字符串
    本身不影响 sha 计算).
    """
    actual = _self_sha_skip_sentinel()
    ok = actual == EXPECTED_SELF_FILE_SHA
    _emit(
        "V8_self_file_sha_lock",
        ok,
        (
            f"actual={actual[:16]} expect={EXPECTED_SELF_FILE_SHA[:16]} "
            f"lock_schema_version={V8_SELF_SHA_LOCK_VERSION} "
            f"bumped_at={V8_SELF_SHA_LOCK_BUMPED_AT}"
        ),
    )


# ---------------------------------------------------------------------------
# V9: V8 pragma cardinality 机械断言
#     (infra-P305-backlog-pragma-cardinality-mechanical-assert, phase-61 #1)
# ---------------------------------------------------------------------------
def v9_pragma_cardinality() -> None:
    """全文 rstrip 后 endswith ``# V8-SELF-SHA-SKIP`` 的行数恰好 == 1.

    与 V8 互补: V8 锁住 *剔除 pragma 行后* 的 sha; V9 锁住 pragma 行本身
    的 *计数*. 若有人 (无心或恶意) 在脚本内再加一条 pragma 行 (例如复制
    到伪常量、docstring 例子、注释中), V8 skip 集合会扩大为 2 行 -> V8
    sha 等式仍可能成立 (取决于 attacker 操作), 形成绕过 surface; V9 在
    cardinality 维度独立锁定, 任何 ≠1 立即红线.

    本 emit 的 "pragma 行" 自身在脚本内出现 1 次 = ``EXPECTED_SELF_FILE_SHA``
    那条真常量行; 本 docstring 内的 ``# V8-SELF-SHA-SKIP`` 字面量以双反引号
    inline-code 形式书写 (即 ``\\`\\`# V8-SELF-SHA-SKIP\\`\\``), rstrip 后
    不 endswith pragma 文本, 不计入.
    """
    _PRAGMA = "# V8-SELF-SHA-SKIP"
    raw = SELF.read_text(encoding="utf-8")
    hits = [
        ln for ln in raw.splitlines() if ln.rstrip().endswith(_PRAGMA)
    ]
    ok = len(hits) == 1
    _emit(
        "V9_pragma_cardinality",
        ok,
        f"pragma_hit_count={len(hits)} expect=1",
    )


# ---------------------------------------------------------------------------
# V10: V8 pragma 必须挂在真常量行机械断言
#     (infra-P305-backlog-pragma-attached-to-real-constant-only, phase-61 #2)
# ---------------------------------------------------------------------------
# 与 V9 互补: V9 锁 pragma 行数 == 1; V10 锁那唯一一条 pragma 行**必须**是
# `EXPECTED_SELF_FILE_SHA` 真常量定义行. 防"把唯一一条 pragma 移到无关行
# (例如随手 # foo  # V8-SELF-SHA-SKIP 注释行)"的盲点—V9 仍 ==1 但 V10 FAIL.
_PRAGMA_LINE_PATTERN = re.compile(
    r'^EXPECTED_SELF_FILE_SHA\s*=\s*"[0-9a-f]{64}"\s*#\s*V8-SELF-SHA-SKIP\s*$'
)


def v10_pragma_attached_to_real_constant_only() -> None:
    """唯一一条 endswith pragma 行必须匹配 ``EXPECTED_SELF_FILE_SHA`` 真常量
    定义正则.

    依赖 V9 已确保 cardinality == 1; V10 不重复 cardinality 判断, 只对取出的
    那一条候选行做正则匹配. 若 V9 在前面已 FAIL, V10 行为退化为"取首条候选";
    实际 ordering 下 V9 先跑, 异常态会被 V9 红线先抓住, V10 是叠加锁.
    """
    _PRAGMA = "# V8-SELF-SHA-SKIP"
    raw = SELF.read_text(encoding="utf-8")
    hits = [
        ln for ln in raw.splitlines() if ln.rstrip().endswith(_PRAGMA)
    ]
    if not hits:
        _emit(
            "V10_pragma_attached_to_real_constant_only",
            False,
            "no pragma line found (V9 should have failed first)",
        )
        return
    line = hits[0]
    ok = bool(_PRAGMA_LINE_PATTERN.match(line))
    _emit(
        "V10_pragma_attached_to_real_constant_only",
        ok,
        f"line_repr={line!r} matched={ok}",
    )


def main() -> int:
    v0_schema()
    v1_targets_exist()
    v2_lock_verify_034()
    v3_mutant()
    v4_lock_bump()
    v5_bump_dryrun()
    v6_canonical_bytes()
    v7_bump_atomic_write()
    v8_self_file_sha_lock()
    v9_pragma_cardinality()
    v10_pragma_attached_to_real_constant_only()

    failed = [t for t, ok, _ in _results if not ok]
    total = len(_results)
    print(f"[verify_infra_035] summary total={total} failed={len(failed)}", flush=True)
    if failed:
        print(f"[verify_infra_035] failed tags: {failed}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
