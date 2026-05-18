# scripts/ sys.path 注入审计（infra-030 / P222）

来源 backlog：`interact-018-backlog-syspath-injection-audit`
背景：Reviewer caveat（interact-018）— `scripts/verify_interact_018.py` 与
`scripts/proactive_trace_summary.py` 均含 `sys.path` 注入（`__file__` 父目录上溯），
在 subprocess / `-m` / 打包等场景下健壮性未审计。

本审计仅文档化，不修改任何注入；anti-pattern 发现入 backlog，本 feature 不修。

## 1. 总览（统计自 `grep -rn "sys.path.insert\|sys.path.append" scripts/`）

- 总注入行（含文档字符串/字面量出现）：**136**
- 实际可执行注入（剔除 docstring/grep 字符串文档说明）：**约 126**
- 带 `if not in sys.path` 守护：**11**
- 字面量（`%r` / `repr(...)`）— subprocess 生成代码片段：**10**
- Type A（注入 repo 根，供 `import coco.*`）：**92**
- Type B（注入 `scripts/` 目录，供同级脚本相互 import）：**24**
- Type C（字面量 — 由 verify 脚本写入到 subprocess/子进程脚本中）：**10**

## 2. 四档执行路径与实测

四档定义（来自 infra-030 acceptance_criteria）：

| 档位 | 调用形式 | 代表命令 |
|---|---|---|
| 档1 | 直接 .venv/bin/python `scripts/xxx.py` | `.venv/bin/python scripts/verify_interact_018.py` |
| 档2 | subprocess 调用（PYTHONPATH 不预设） | `subprocess.run([sys.executable, 'scripts/verify_interact_018.py'])` |
| 档3 | `python -m` 模块运行 | `cd scripts && python -m verify_interact_018` |
| 档4 | 非 repo-root cwd 绝对路径调用 | `cd /tmp && /abs/.venv/bin/python /abs/scripts/verify_interact_018.py` |

### 实测结果（2026-05-19，main HEAD=691dcc3 + feat/infra-030）

代表样本：`scripts/verify_interact_018.py`、`scripts/proactive_trace_summary.py`

| 档 | verify_interact_018 | proactive_trace_summary |
|---|---|---|
| 档1 | PASS（overall PASS） | PASS（--help 正常输出） |
| 档2 | PASS（rc=0） | PASS（rc=0） |
| 档3 | PASS（`cd scripts && python -m verify_interact_018`，overall PASS） | 不适用（cli 工具，未配 `__main__` 模块协议） |
| 档4 | PASS（`/tmp` 下绝对路径调用 overall PASS） | PASS（--help 正常输出） |

结论：**当前 `__file__.resolve().parent.parent` 模式在四档下均能正确定位 repo 根并 import `coco.*`**。
原因：`__file__` 在四档下都被解析为 `scripts/xxx.py` 的真实绝对路径，
`parent.parent` 即 repo 根，不受 cwd / `-m` 影响。

## 3. 三类注入形态

### Type A — `sys.path.insert(0, str(ROOT))`（92 处）

最常见。`ROOT = Path(__file__).resolve().parent.parent`，注入 repo 根。
目的：让脚本能 `import coco.*`、`import tests.fixtures.*`。

四档健壮性：**OK**。`__file__` 在 PyInstaller / wheel 打包场景下行为不同
（PyInstaller 把脚本冻结到 `_MEIPASS`，wheel 安装到 site-packages 后 `__file__`
指向 site-packages），但 verify 脚本不在 wheel 打包范围内（`pyproject.toml`
`[tool.setuptools.packages.find]` 仅包含 `coco*`），无打包风险。

代表锚点：
- `scripts/verify_interact_018.py:42` → `sys.path.insert(0, str(ROOT))`
- `scripts/verify_infra_025.py:54` → 同上
- `scripts/verify_robot_022.py` 等共 92 处

### Type B — `sys.path.insert(0, str(SCRIPTS_DIR))` / `REPO_ROOT / "scripts"`（24 处）

供同级 `scripts/` 内脚本互相 import（例如 `verify_infra_016.py` 多次注入
`SCRIPTS_DIR` 以引入其他 verify_*  模块进行 meta lock）。

四档健壮性：**OK**。同样依赖 `__file__`。

代表锚点：
- `scripts/verify_infra_016.py:49,61,91,114,235,255,277`
- `scripts/verify_infra_017.py:47,120,148,183,218,360`
- `scripts/run_verify_all.py:269,294`
- `scripts/precommit_impact.py:60`
- `scripts/health_summary.py:30`

### Type C — 字面量 `sys.path.insert(0, %r)` / `repr(...)`（10 处）

verify 脚本把字面 repo 根路径写入到 subprocess 即时生成的子脚本中。
例如 `scripts/verify_robot_019.py:114`、`verify_robot_020.py:123,211`、
`verify_robot_021.py:144,258`、`verify_robot_022.py:218`、`verify_vision_010.py:133`、
`verify_companion_008.py:349`、`verify_interact_016.py:237`、`verify_interact_029.py:199`。

四档健壮性：**OK 但脆**。字面量绑定写脚本时的绝对路径，若把生成的子脚本
跨机/跨容器传输则失效——但本仓库 subprocess 都是当场生成、当场调用，
不持久化跨环境，因此目前安全。

## 4. 守护与冗余

- **未守护重复注入**：`verify_interact_018.py:42`、`proactive_trace_summary.py:61` 等
  126 处中仅 11 处带 `if str(...) not in sys.path` 守护。重复 import 时
  `sys.path` 会冗余追加（性能影响可忽略）。这是 **interact-018 backlog 提到的痛点**，
  不构成 anti-pattern。
- **唯一带显式 `if not in` 守护的代表**：`scripts/proactive_trace_summary.py:59-61`：
  ```python
  _ROOT = Path(__file__).resolve().parent.parent
  if str(_ROOT) not in sys.path:
      sys.path.insert(0, str(_ROOT))
  ```
  这是推荐写法（幂等）。

## 5. anti-pattern 与新 backlog 候选

本审计发现以下可选改进，**不在 infra-030 范围内修复**，仅记录：

1. **`verify_interact_018.py:42` 缺 `if not in` 守护**（重复 import 时冗余追加 sys.path）
   — 与 verify_infra_025.py:312 已有 finding 一致。可入 backlog 统一加守护。
2. **Type C 字面量 subprocess 注入** — 在跨容器或 path 含非 ASCII 时可能失效，
   现状下安全，但 `repr(path)` 在 Windows 反斜杠转义场景下需注意。可入 backlog 评估
   改为 env 传递。

**不入 backlog 的项**：Type A / Type B 主体注入是正确实践（pyproject 不打包 scripts/，
不存在 wheel 注入位置失效问题），保留现状。

## 6. 锁定指纹（meta-lock）

`scripts/verify_infra_030.py` 锁以下两层：

1. **总数锁**：`grep -rn "sys.path.insert" scripts/` 命中数 ≥ 130（buffer 防回归性下降）。
2. **核心两锚点**：
   - `scripts/verify_interact_018.py` 内含 `sys.path.insert(0, str(ROOT))` 字面（不锁行号，行号在重构时易飘）
   - `scripts/proactive_trace_summary.py` 内含 `if str(_ROOT) not in sys.path` + `sys.path.insert(0, str(_ROOT))`

不锁 sha256：吸收 robot-025 finding F1/F2，sha256 锁会与未来正常编辑冲突，
改用「字面量集合双层 + 总数下限」更稳。

**锁单向性（infra-031 F1）**：本锁仅感知数量**下降**回归（命中数 < 130 → FAIL），
**不感知新增注入**——即新增 `sys.path.insert` anti-pattern 会让总数上升，
锁仍 PASS。新增 anti-pattern 的兜底由 code review / Reviewer fresh-context
评审承担，本 meta-lock 不替代 code review。

## 7. 实测命令重放

```bash
# 档1
.venv/bin/python scripts/verify_interact_018.py

# 档2
.venv/bin/python -c "import subprocess,sys; r=subprocess.run([sys.executable,'scripts/verify_interact_018.py']); print('rc=',r.returncode)"

# 档3
cd scripts && ../.venv/bin/python -m verify_interact_018 && cd ..

# 档4
cd /tmp && /Users/halton/work/coco/.venv/bin/python /Users/halton/work/coco/scripts/verify_interact_018.py
```

四档全 PASS（2026-05-19，feat/infra-030）。
