# infra-028 migration note

**Source backlog**: `infra-022-backlog-rotate-docstring-mismatch`
**Phase**: 24 (P202)
**Policy**: verify-only — docstring 字面量对齐, 0 业务行为改动

## 不一致点

infra-022 N4 引入 `_emit_safe` 的"rotate+append 同一把锁内原子完成"后, `scripts/_history_writer.py` 中两处 docstring 没有同步更新, 与代码实际行为出现矛盾:

### 1. Module-level docstring 边界判定字面量错误

旧文本 (第 30 行):

```
5. 行数 >ROTATE_LINES (默认 5000) 自动滚到 `.archive/` 下时间戳命名
```

实际代码 (`_rotate_locked` line 309):

```python
if _line_count(jsonl_path) < rotate_lines:
    return None
```

即 **`>=` 触发** rotate, 不是 `>`。`>=` 与 `>` 在等号边界 (恰好 5000 行) 行为不同, 文档错。

### 2. `_rotate_if_needed` docstring 自相矛盾

旧文本 (line 326-331):

```
"""无锁版本——保留对外签名兼容（测试和老调用方继续用），自带加锁。

infra-022 N4: 该入口自身加 _FileLock，确保 replace+touch+retention 与同进程
其它 append/rotate 串行化。...
```

第一句的"**无锁版本**"与同一句末尾的"**自带加锁**"以及下一段的"**该入口自身加 _FileLock**"明显矛盾。这是 infra-022 N4 之前的老 docstring (那时确实"无锁") 在 N4 加锁后没有改完整。这种矛盾会让阅读者怀疑实现是否真的锁了, 也会让 sub-agent 在调研锁作用域时被误导。

## 修正

### Module docstring (第 30-32 行)

```
5. 行数 >= ROTATE_LINES (默认 5000) 自动滚到 `.archive/` 下时间戳命名；主 jsonl 立即
   recreate 空文件（infra-017 C2）。infra-028: 字面量与 `_rotate_locked` 内
   ``_line_count(...) < rotate_lines`` 判定对齐（即 ``>=`` 触发, 非 ``>``）。
```

### `_rotate_if_needed` docstring (line 325 起)

完整重写为:

```
"""对外公共 API: rotate 入口, **自带 _FileLock**（infra-022 N4 起）。

名义上是"无锁版本对外签名"以保留向后兼容（老调用方和 verify 测试继续按
`_rotate_if_needed(path)` 调用），但 **实际实现自 infra-022 N4 起内部已加
_FileLock**, 把 ``_rotate_locked`` 的 replace+touch+retention 三步全部包入
同一把进程级 advisory 锁, 与同进程其它 append/rotate 路径 (`_append_line` /
`_emit_safe`) 互斥串行化, 杜绝以下竞态:

- rename(主→archive) 与 append 到老 fd 之间的 lost-write
- touch(主) 与 append 到不存在主文件之间的 ENOENT
- 同秒多 worker 同时 rename 撞名 (由 ``_archive_stamp`` 单调 seq 兜底)

锁作用域 (infra-028 锁面)
-------------------------
本函数 **进入 with _FileLock(jsonl_path)** → 调用 ``_rotate_locked`` →
``_rotate_locked`` 内部完成 replace+touch+_enforce_retention 三步 →
退出 with → 释放锁。即 rotate 的 **原子边界 = _FileLock 的整段 with-block**。

阈值判定 (infra-028 字面量锁)
-----------------------------
``_rotate_locked`` 内 ``_line_count(jsonl_path) < rotate_lines: return None``
即 **行数 >= rotate_lines 才触发** rotate（不是 ``>``）。
``rotate_lines=None`` 时运行时读 module-level ``ROTATE_LINES``（默认 5000），
允许测试 monkeypatch 改 module 属性。
"""
```

## 业务行为 diff

**零业务行为改动**。

- 唯一非 docstring/comment 改动: `scripts/verify_infra_022.py` 的 `EXPECTED_FINGERPRINT` 字面量从 `0300e618...` 改为 `77e3a1d7...` 以匹配新的 `_history_writer.py` 源码 sha256。该常量仅用于 V0 fingerprint 检测漂移, 与运行时行为无关。
- `_history_writer.py` 的 import / 函数签名 / 函数体 / 类定义 / module-level 全局常量 (`ROTATE_LINES=5000`, `_DEFAULT_ARCHIVE_KEEP=20`, `_DISABLE_TRUE_VALUES`) 全部未变。
- `_rotate_locked` / `_rotate_if_needed` / `_emit_safe` / `_append_line` / `emit_verify` / `emit_smoke` / `_archive_stamp` / `_enforce_retention` / `_FileLock` 函数体 0 改动。

## verify 锁面

`scripts/verify_infra_028.py` 锁:

- **V0** sha256 fingerprint (与 `verify_infra_022.py` 同源, 两份冗余 — 任一漂移都即时可见)
- **V1** 阈值字面量: `>= rotate_lines` / `>= ROTATE_LINES` / `_line_count(jsonl_path) < rotate_lines` 三处字面量必须共存
- **V2** `_rotate_if_needed` docstring 必出短语 (`自带 _FileLock` / `锁作用域` / `rotate 的 **原子边界 = _FileLock 的整段 with-block**` / `infra-028 锁面` / `infra-028 字面量锁`) + 旧矛盾措辞 (`"""无锁版本——...自带加锁。`) 不再存在
- **V3** 邻近 verify 静态锁面 rc=0: `verify_infra_022/024/025/026/027`
- **V4** `COCO_CI=1 ./scripts/smoke.py` rc=0

## verify-only / 0 业务行为改动声明

本 feature **不动任何业务逻辑** (不动 rotate 阈值 / 不动锁实现 / 不动文件名规则 / 不动 retention 数), 仅:

1. 修正两处 docstring 文字 (`scripts/_history_writer.py`)
2. 更新 `verify_infra_022.py` 的 EXPECTED_FINGERPRINT 字面量 (匹配新的 docstring 后 sha256)
3. 新增 `scripts/verify_infra_028.py` 锁面脚本
4. 新增 `evidence/infra-028/` 目录

不引入新 env 变量、新公共 API、新模块、新 callable、新行为分支。
