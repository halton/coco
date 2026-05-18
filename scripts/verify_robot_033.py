#!/usr/bin/env python3
"""verify_robot_033: 锁定 ProactiveScheduler._fallback_warned warn-once 语义。

V0  文件/行号存在性
V1  __init__ 中 `self._fallback_warned: bool = False` 静态 sha256 锁
V2  except 块 (L1332-1341) 静态 sha256 锁
V3  动态：实例化 ProactiveScheduler，触发 enqueue 异常
    - 第一次 → WARN=1
    - 第二次 → DEBUG，WARN 仍=1
    - 新实例 → 重置，WARN=1
    mutant 反证：替换 except 块赋值为 False → WARN=3，证明锁有效
V4  working-tree sha256 锁 coco/proactive.py
V5  subprocess 自调用 rc=0

robot-030 已落地 per-instance warn-once；本脚本仅 verify 不改业务源码。

运行环境约定 (infra-034)
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

import hashlib
import io
import logging
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PROACTIVE = REPO / "coco" / "proactive.py"

# 锁常量（基于 main HEAD = 56dd176，coco/proactive.py 当前内容）
INIT_LINE_NO = 390  # `self._fallback_warned: bool = False`
EXCEPT_LINE_START = 1332  # `except Exception as _e:  # noqa: BLE001`
EXCEPT_LINE_END = 1341    # `                                      type(_e).__name__, _e)`

INIT_LINE_SHA = "4852b19e23964820a99cf106f61308fc08889234bd77186576c76d8ce6620278"
EXCEPT_BLOCK_SHA = "1da1ab84249bf2c76adca3923a094d71f9b236322a53597442224ad54de41d9a"
FILE_SHA = "929694d0fd56dd81505ab0c294a85b5848a7a1a91c479cee87f8cbdec2470364"


def _read_lines() -> list[bytes]:
    with open(PROACTIVE, "rb") as f:
        return f.read().splitlines(keepends=True)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def v0_existence() -> None:
    assert PROACTIVE.exists(), f"missing: {PROACTIVE}"
    lines = _read_lines()
    assert len(lines) >= EXCEPT_LINE_END, f"file too short: {len(lines)}"
    init_line = lines[INIT_LINE_NO - 1].decode()
    assert "_fallback_warned" in init_line and "False" in init_line, \
        f"V0: L{INIT_LINE_NO} unexpected: {init_line!r}"
    except_head = lines[EXCEPT_LINE_START - 1].decode()
    assert "except Exception as _e" in except_head, \
        f"V0: L{EXCEPT_LINE_START} not except head: {except_head!r}"
    print(f"V0 OK: L{INIT_LINE_NO} init + L{EXCEPT_LINE_START}-{EXCEPT_LINE_END} except 存在")


def v1_init_line_sha() -> None:
    lines = _read_lines()
    actual = _sha(lines[INIT_LINE_NO - 1])
    assert actual == INIT_LINE_SHA, (
        f"V1 FAIL: init L{INIT_LINE_NO} sha={actual} expect={INIT_LINE_SHA}\n"
        f"  line={lines[INIT_LINE_NO-1]!r}"
    )
    print(f"V1 OK: init L{INIT_LINE_NO} sha256={actual[:16]}…")


def v2_except_block_sha() -> None:
    lines = _read_lines()
    block = b"".join(lines[EXCEPT_LINE_START - 1:EXCEPT_LINE_END])
    actual = _sha(block)
    assert actual == EXCEPT_BLOCK_SHA, (
        f"V2 FAIL: except L{EXCEPT_LINE_START}-{EXCEPT_LINE_END} sha={actual} "
        f"expect={EXCEPT_BLOCK_SHA}"
    )
    print(f"V2 OK: except L{EXCEPT_LINE_START}-{EXCEPT_LINE_END} sha256={actual[:16]}…")


# ---------------------- V3 动态行为 ----------------------

def _count_warn_records(buf_records: list[logging.LogRecord]) -> tuple[int, int]:
    """返回 (warn_count, debug_count) for messages containing '[proactive] robot_sequencer.enqueue failed'."""
    needle = "robot_sequencer.enqueue failed"
    w = sum(1 for r in buf_records
            if r.levelno == logging.WARNING and needle in r.getMessage())
    d = sum(1 for r in buf_records
            if r.levelno == logging.DEBUG and needle in r.getMessage())
    return w, d


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _trigger_enqueue_failure_path(scheduler, n: int = 1) -> None:
    """调用 _do_trigger_unlocked 走 enqueue 失败路径 n 次。"""
    # 注入一个 sequencer：enqueue 抛 RuntimeError
    class _BadSeq:
        def enqueue(self, action):  # noqa: D401
            raise RuntimeError("synthetic enqueue failure")
        def is_shutdown(self):
            return False
    scheduler._robot_sequencer = _BadSeq()
    for i in range(n):
        # _do_trigger_unlocked(t, system_prompt=None, seed="...")
        # llm_reply_fn 默认 None → 走 fail-soft，会继续 TTS+emit；
        # 这里我们只关心 enqueue 失败路径，因此即便 TTS 失败也无所谓。
        try:
            scheduler._do_trigger_unlocked(
                t=float(i),
                system_prompt=None,
                seed="hello",
            )
        except Exception:
            # 不该 propagate（fail-soft），但保险起见容忍
            pass


def _make_scheduler(cls=None):
    from coco.proactive import ProactiveScheduler
    cls = cls or ProactiveScheduler
    sched = cls(
        llm_reply_fn=lambda *a, **kw: "",
        tts_say_fn=lambda *a, **kw: None,
        emit_fn=lambda *a, **kw: None,
        on_interaction=lambda *a, **kw: None,
    )
    return sched


def v3_dynamic_warn_once() -> None:
    # capture coco.proactive logger
    log = logging.getLogger("coco.proactive")
    prev_level = log.level
    prev_propagate = log.propagate
    log.setLevel(logging.DEBUG)
    log.propagate = False
    handler = _ListHandler()
    log.addHandler(handler)
    try:
        sched1 = _make_scheduler()
        _trigger_enqueue_failure_path(sched1, n=1)
        w1, d1 = _count_warn_records(handler.records)
        assert w1 == 1, f"V3a: 第一次失败 WARN 应=1, 实际={w1} (records={[(r.levelname, r.getMessage()) for r in handler.records]})"
        assert d1 == 0, f"V3a: 第一次失败 DEBUG 应=0, 实际={d1}"

        _trigger_enqueue_failure_path(sched1, n=1)
        w2, d2 = _count_warn_records(handler.records)
        assert w2 == 1, f"V3b: 第二次后 WARN 仍应=1 (warn-once), 实际={w2}"
        assert d2 == 1, f"V3b: 第二次后 DEBUG 应=1, 实际={d2}"

        # 新实例：per-instance 重置
        handler.records.clear()
        sched2 = _make_scheduler()
        _trigger_enqueue_failure_path(sched2, n=1)
        w3, d3 = _count_warn_records(handler.records)
        assert w3 == 1, f"V3c: 新实例首次 WARN 应=1 (per-instance 重置), 实际={w3}"
        assert d3 == 0, f"V3c: 新实例首次 DEBUG 应=0, 实际={d3}"
        print(f"V3 OK: warn-once 行为 (实例1: 1+1→W1D1; 新实例: W1D0)")
    finally:
        log.removeHandler(handler)
        log.setLevel(prev_level)
        log.propagate = prev_propagate

    # ---- mutant 反证：修改 _fallback_warned 永不置位 ----
    log2 = logging.getLogger("coco.proactive")
    prev_level2 = log2.level
    prev_propagate2 = log2.propagate
    log2.setLevel(logging.DEBUG)
    log2.propagate = False
    handler2 = _ListHandler()
    log2.addHandler(handler2)
    try:
        from coco.proactive import ProactiveScheduler
        orig_init = ProactiveScheduler.__init__

        # 用一个 property 拦截 _fallback_warned setter → 永远 False
        # 这通过给实例增加一个 always-False descriptor 难直接做；
        # 改用更直接的 monkey-patch：在 __setattr__ 层拦截。
        class _NeverWarnedScheduler(ProactiveScheduler):
            def __setattr__(self, k, v):
                if k == "_fallback_warned" and v is True:
                    # 模拟 except 块从未把 flag 置位（mutant）
                    super().__setattr__(k, False)
                else:
                    super().__setattr__(k, v)

        sched_m = _make_scheduler(cls=_NeverWarnedScheduler)
        _trigger_enqueue_failure_path(sched_m, n=3)
        w_m, d_m = _count_warn_records(handler2.records)
        assert w_m == 3, (
            f"V3 mutant FAIL: 期望 mutant 下 WARN=3 (永不置位), 实际 WARN={w_m} DEBUG={d_m}\n"
            f"  → 说明 warn-once 逻辑可能不依赖 _fallback_warned，验证失效"
        )
        print(f"V3 mutant OK: 反证 WARN={w_m} (mutant 下永不置位 → 期望 3, 锁有效)")
    finally:
        log2.removeHandler(handler2)
        log2.setLevel(prev_level2)
        log2.propagate = prev_propagate2


def v4_file_sha() -> None:
    with open(PROACTIVE, "rb") as f:
        data = f.read()
    actual = _sha(data)
    assert actual == FILE_SHA, (
        f"V4 FAIL: coco/proactive.py sha={actual} expect={FILE_SHA}\n"
        f"  → main 已推进或源码已动；若是有意改动，重新生成 FILE_SHA"
    )
    print(f"V4 OK: coco/proactive.py sha256={actual[:16]}…")


def v5_subprocess_selfcall() -> None:
    # 避免递归：用环境变量短路
    if os.environ.get("_ROBOT_033_SUB") == "1":
        print("V5 sub: skipped (in subprocess)")
        return
    env = os.environ.copy()
    env["_ROBOT_033_SUB"] = "1"
    r = subprocess.run(
        [sys.executable, str(Path(__file__))],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, (
        f"V5 FAIL: subprocess rc={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
    )
    print(f"V5 OK: subprocess rc=0")


def main() -> int:
    sub_mode = os.environ.get("_ROBOT_033_SUB") == "1"
    checks = [
        ("V0", v0_existence),
        ("V1", v1_init_line_sha),
        ("V2", v2_except_block_sha),
        ("V3", v3_dynamic_warn_once),
        ("V4", v4_file_sha),
    ]
    if not sub_mode:
        checks.append(("V5", v5_subprocess_selfcall))
    failed = []
    for name, fn in checks:
        try:
            fn()
        except AssertionError as e:
            failed.append((name, str(e)))
            print(f"{name} FAIL: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, f"{type(e).__name__}: {e}"))
            print(f"{name} ERROR: {type(e).__name__}: {e}")
    if failed:
        print(f"\n[verify_robot_033] FAIL: {len(failed)} check(s) failed")
        for n, msg in failed:
            print(f"  - {n}: {msg}")
        return 1
    print("\n[verify_robot_033] PASS (V0-V{}) main-baseline=56dd176".format(
        5 if not sub_mode else 4
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
