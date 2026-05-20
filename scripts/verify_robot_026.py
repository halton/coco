"""robot-026 verify-only meta-lock: verify_robot_015 错误打印截断放宽.

source backlog: robot-015-backlog-verify-error-print-truncation

锁定 scripts/verify_robot_015.py 错误汇总打印:
  原: print(f"  - {e[:300]}")  # 长 traceback 被切
  新: print(f"  - {e[:1500]}") # 放宽到 1500 字, 便于排障

verify-only, 0 业务源码改动.

V0 file existence:          scripts/verify_robot_015.py 存在
V1 字面量替换正确:           含 "[:1500]" 出现 >=1; 不含 "[:300]" 残留 (在错误汇总打印行)
V2 import traceback 存在:    `import traceback` 顶层 import 存在
V3 mutant 反证:              把 "[:1500]" 替换回 "[:300]" 字符串后再校验 → 应 FAIL (锁定有效)
V4 default-OFF 等价 main:    业务源码 (coco/**.py) 未被 robot-026 改动 (本 feature verify-only)
V5 summary

吸收 robot-025 / infra-030 finding:
  - 字面量集合 + 出现次数下限 (不锁行号)
  - 不用 sha256 自洽
运行环境约定 (infra-038)
------------------------
本脚本及其 V0-V5 子进程**必须**在已激活的 .venv 下运行 (Python 解释器入口
``.venv/bin/python``); 不要用系统 ``python3`` 直接调用本脚本, 否则
``importlib`` 加载业务模块时依赖 (numpy / soundfile / onnxruntime 等) 可能
解析到系统站点而非 venv 站点, 导致与 ``./init.sh`` smoke 路径不一致,
进而 V0-V5 退出码漂移。

约定细则:
  - **Reviewer / CI / 手动复跑入口**: 一律 ``.venv/bin/python`` 启动 (或先
    ``source .venv/bin/activate`` 再 ``python scripts/<本脚本名>.py``)。
  - **子进程 invoke**: 任何 ``subprocess.run`` 第一参数固定使用 ``sys.executable``
    (即本脚本所属解释器); 不写死 ``"python"`` / ``"python3"`` 字面量, 确保
    子进程继承父进程同一个 venv Python, 避免 PATH 覆盖踩坑。
  - **环境变量继承**: 子进程从 ``os.environ`` 拷贝 PATH / PYTHONPATH 等,
    PATH 中 venv 的 ``bin`` 目录位置不可被人为打乱 (init.sh 已在激活时前置)。
  - **新会话注意事项**: 干净 shell 进来务必先 ``source .venv/bin/activate``
    或显式 ``./.venv/bin/python``, 否则即便代码 byte-equal 也可能因解释器
    漂移产生不可复现的 FAIL。

"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "scripts" / "verify_robot_015.py"

errors: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}" + (f" :: {detail}" if detail else ""))
    if not ok:
        errors.append(f"{label} :: {detail}")


# =======================================================================
# V0 file existence
# =======================================================================
v0_ok = TARGET.is_file()
check("V0 verify_robot_015.py 存在", v0_ok, str(TARGET))
if not v0_ok:
    print("FAIL (V0 file missing)")
    sys.exit(1)

src = TARGET.read_text(encoding="utf-8")


# =======================================================================
# V1 字面量替换正确
# =======================================================================
new_lit = "[:1500]"
old_lit = "[:300]"

new_count = src.count(new_lit)
old_count = src.count(old_lit)

check(
    f"V1a verify_robot_015 含 '{new_lit}' >=1",
    new_count >= 1,
    f"count={new_count}",
)
check(
    f"V1b verify_robot_015 不含 '{old_lit}' 残留",
    old_count == 0,
    f"count={old_count}",
)


# =======================================================================
# V2 import traceback 存在
# =======================================================================
has_import = any(
    line.strip() == "import traceback"
    for line in src.splitlines()
)
check("V2 import traceback 存在", has_import)


# =======================================================================
# V3 mutant 反证: 把 [:1500] 改回 [:300] 应 FAIL V1b
# =======================================================================
mutant_src = src.replace(new_lit, old_lit, 1)
mutant_old_count = mutant_src.count(old_lit)
mutant_new_count = mutant_src.count(new_lit)

mutant_should_fail = (mutant_old_count >= 1) and (mutant_new_count < new_count)
check(
    "V3 mutant: 把 [:1500] 换回 [:300] 后 V1b 必失效 (旧字面量 >=1)",
    mutant_should_fail,
    f"mutant_old={mutant_old_count}, mutant_new={mutant_new_count}, orig_new={new_count}",
)


# =======================================================================
# V4 default-OFF 等价 main: 业务源码 (coco/**.py) 未被 robot-026 改动
# =======================================================================
import subprocess

try:
    diff = subprocess.run(
        ["git", "diff", "--name-only", "main...HEAD"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if diff.returncode != 0:
        check("V4 git diff main...HEAD 可执行", False, diff.stderr.strip()[:300])
    else:
        changed = [
            ln.strip() for ln in diff.stdout.splitlines() if ln.strip()
        ]
        biz_changed = [
            f for f in changed
            if f.startswith("coco/") and f.endswith(".py")
        ]
        check(
            "V4 0 业务源码改动 (coco/**.py 未改)",
            len(biz_changed) == 0,
            f"biz_changed={biz_changed}",
        )
except FileNotFoundError:
    check("V4 git 可用", False, "git not found")
except subprocess.TimeoutExpired:
    check("V4 git diff timeout", False, "30s")


# =======================================================================
# V5 summary
# =======================================================================
print()
if errors:
    print(f"FAIL ({len(errors)} errors)")
    for e in errors:
        print(f"  - {e[:1500]}")
    sys.exit(1)
else:
    print("PASS (V0-V5 all checks)")
    sys.exit(0)
