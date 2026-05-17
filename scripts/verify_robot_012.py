"""robot-012 verify: SIGTERM/SIGINT signal handler → RobotSequencer shutdown (default-OFF).

V0: fingerprint — install_signal_shutdown_handler 存在 + env key + token
V1: Default-OFF bytewise 等价 — env unset → register 返回 []，signal.getsignal 不变
V2: ON 时 SIGTERM → seq.shutdown 调用 (subprocess raise SIGTERM, 进程退出 + flag 文件)
V3: Windows 缺 SIGTERM 兼容 — fake signal_module 无 SIGTERM 属性, 不抛
V4: 重入安全 — handler 第二次调用 不再调 shutdown (idempotent)
V5: regression — verify_robot_008/009/010/011 子进程 rc==0
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any, List

errors: List[str] = []
t0 = time.time()


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(': ' + detail) if detail else ''}")
    if not cond:
        errors.append(f"{label} {detail}".strip())


# 清 env
for k in (
    "COCO_ROBOT_SIGTERM_HANDLE",
    "COCO_ROBOT_SEQ_SHUTDOWN_TIMEOUT_S",
):
    os.environ.pop(k, None)


# --------- V0: fingerprint ---------
print("[V0] fingerprint")
try:
    from coco.robot.sequencer import (
        SequencerConfig,
        install_signal_shutdown_handler,
        sequencer_config_from_env,
    )
    check(
        "install_signal_shutdown_handler is callable",
        callable(install_signal_shutdown_handler),
    )
    cfg = SequencerConfig()
    check(
        "SequencerConfig.shutdown_timeout_s default 2.0",
        cfg.shutdown_timeout_s == 2.0,
        f"got={cfg.shutdown_timeout_s}",
    )
    # token in source
    import coco.robot.sequencer as _seq_mod
    src = open(_seq_mod.__file__, encoding="utf-8").read()
    check(
        "token COCO_ROBOT_SIGTERM_HANDLE present in sequencer.py",
        "COCO_ROBOT_SIGTERM_HANDLE" in src,
    )
    main_src = open("coco/main.py", encoding="utf-8").read()
    check(
        "token COCO_ROBOT_SIGTERM_HANDLE present in main.py",
        "COCO_ROBOT_SIGTERM_HANDLE" in main_src,
    )
    check(
        "main.py uses signal.signal/getsignal",
        "signal.signal" in main_src.replace(" ", "") or "_sig.signal" in main_src,
    )
except Exception as e:  # noqa: BLE001
    check("import sequencer module", False, f"{type(e).__name__}: {e}")
    print("ABORT V0 failed")
    sys.exit(1)


# --------- V1: Default-OFF bytewise 等价 ---------
print("[V1] Default-OFF bytewise 等价 (env unset)")
try:
    import signal as _sig
    from coco.robot.sequencer import (
        RobotSequencer,
        SequencerConfig,
        install_signal_shutdown_handler,
    )

    # 记录 baseline
    prev_term = _sig.getsignal(_sig.SIGTERM) if hasattr(_sig, "SIGTERM") else None
    prev_int = _sig.getsignal(_sig.SIGINT)

    seq = RobotSequencer(config=SequencerConfig(enabled=False))
    registered = install_signal_shutdown_handler(seq, env={})
    check(
        "env unset → install returns []",
        registered == [],
        f"got={registered}",
    )
    # 显式 OFF 值
    for off_val in ("", "0", "false", "no", "off"):
        r = install_signal_shutdown_handler(seq, env={"COCO_ROBOT_SIGTERM_HANDLE": off_val})
        check(f"env='{off_val}' → []", r == [], f"got={r}")
    # signal 未被改
    if hasattr(_sig, "SIGTERM"):
        check(
            "SIGTERM handler 未被改 (default-OFF)",
            _sig.getsignal(_sig.SIGTERM) == prev_term,
        )
    check(
        "SIGINT handler 未被改 (default-OFF)",
        _sig.getsignal(_sig.SIGINT) == prev_int,
    )
    seq.shutdown(wait=True, timeout=0.5)
except Exception as e:  # noqa: BLE001
    check("V1 default-off", False, f"{type(e).__name__}: {e}")


# --------- V2: ON 时 SIGTERM 触发 shutdown ---------
print("[V2] ON 时 SIGTERM → seq.shutdown 被调")
v2_script = """
import os, signal, time, sys
os.environ["COCO_ROBOT_SIGTERM_HANDLE"] = "1"
from coco.robot.sequencer import RobotSequencer, SequencerConfig, install_signal_shutdown_handler
seq = RobotSequencer(config=SequencerConfig(enabled=False))
flag_path = sys.argv[1]
# monkey: shutdown 写文件
orig_shutdown = seq.shutdown
def _wrap(*a, **kw):
    with open(flag_path, "w") as f:
        f.write("called")
    return orig_shutdown(*a, **kw)
seq.shutdown = _wrap
reg = install_signal_shutdown_handler(seq, timeout_s=1.0)
print("REG=" + ",".join(reg), flush=True)
# 自发 SIGTERM
os.kill(os.getpid(), signal.SIGTERM)
time.sleep(0.5)
print("AFTER", flush=True)
"""
try:
    import tempfile
    flag = tempfile.NamedTemporaryFile(delete=False, suffix=".flag")
    flag.close()
    os.unlink(flag.name)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    proc = subprocess.run(
        [sys.executable, "-c", v2_script, flag.name],
        capture_output=True, text=True, timeout=15, env=env,
    )
    check(
        "subprocess registered SIGTERM",
        "SIGTERM" in proc.stdout,
        f"stdout={proc.stdout[:200]}",
    )
    check(
        "shutdown 被 signal handler 调到 (flag 文件存在)",
        os.path.exists(flag.name),
        f"rc={proc.returncode} stderr={proc.stderr[:200]}",
    )
    if os.path.exists(flag.name):
        os.unlink(flag.name)
except Exception as e:  # noqa: BLE001
    check("V2 SIGTERM subprocess", False, f"{type(e).__name__}: {e}")


# --------- V3: Windows 缺 SIGTERM 兼容 ---------
print("[V3] fake signal_module 无 SIGTERM 不抛")
try:
    from coco.robot.sequencer import (
        RobotSequencer,
        SequencerConfig,
        install_signal_shutdown_handler,
    )

    class _FakeSig:
        SIG_DFL = 0
        SIG_IGN = 1
        # 故意没有 SIGTERM / SIGINT
        def getsignal(self, n): return None
        def signal(self, n, h): return None

    seq = RobotSequencer(config=SequencerConfig(enabled=False))
    fake = _FakeSig()
    reg = install_signal_shutdown_handler(
        seq,
        env={"COCO_ROBOT_SIGTERM_HANDLE": "1"},
        signal_module=fake,
        signames=("SIGTERM", "SIGINT"),
    )
    check(
        "fake module 无信号属性 → registered=[] 不抛",
        reg == [],
        f"got={reg}",
    )

    # 仅 SIGTERM 缺, SIGINT 在
    class _FakePartial:
        SIG_DFL = 0
        SIG_IGN = 1
        SIGINT = 2
        def getsignal(self, n): return None
        def signal(self, n, h): return None

    fp = _FakePartial()
    reg2 = install_signal_shutdown_handler(
        seq,
        env={"COCO_ROBOT_SIGTERM_HANDLE": "1"},
        signal_module=fp,
        signames=("SIGTERM", "SIGINT"),
    )
    check(
        "fake module 仅 SIGINT → registered=['SIGINT']",
        reg2 == ["SIGINT"],
        f"got={reg2}",
    )

    # signal.signal 抛 OSError → skip 不传染
    class _FakeRaise:
        SIG_DFL = 0
        SIG_IGN = 1
        SIGTERM = 15
        SIGINT = 2
        def getsignal(self, n): return None
        def signal(self, n, h): raise OSError("not supported")

    reg3 = install_signal_shutdown_handler(
        seq,
        env={"COCO_ROBOT_SIGTERM_HANDLE": "1"},
        signal_module=_FakeRaise(),
        signames=("SIGTERM", "SIGINT"),
    )
    check(
        "signal.signal OSError → 全 skip 返回 []",
        reg3 == [],
        f"got={reg3}",
    )
    seq.shutdown(wait=True, timeout=0.5)
except Exception as e:  # noqa: BLE001
    check("V3 windows compat", False, f"{type(e).__name__}: {e}")


# --------- V4: 重入安全 ---------
print("[V4] handler 重入 idempotent")
try:
    from coco.robot.sequencer import (
        RobotSequencer,
        SequencerConfig,
        install_signal_shutdown_handler,
    )

    call_count = {"n": 0}

    class _FakeSig:
        SIG_DFL = 0
        SIG_IGN = 1
        SIGTERM = 15
        SIGINT = 2
        _handlers: dict = {}
        def getsignal(self, n): return None
        def signal(self, n, h):
            self._handlers[n] = h
            return None

    seq = RobotSequencer(config=SequencerConfig(enabled=False))
    orig = seq.shutdown
    def _count_shutdown(*a, **kw):
        call_count["n"] += 1
        return orig(*a, **kw)
    seq.shutdown = _count_shutdown

    fake = _FakeSig()
    reg = install_signal_shutdown_handler(
        seq,
        env={"COCO_ROBOT_SIGTERM_HANDLE": "1"},
        signal_module=fake,
    )
    check("V4 registered", reg == ["SIGTERM", "SIGINT"], f"got={reg}")
    handler = fake._handlers[15]
    handler(15, None)
    handler(15, None)
    handler(2, None)
    check(
        "handler 多次调用 shutdown 只跑 1 次 (in_progress flag)",
        call_count["n"] == 1,
        f"n={call_count['n']}",
    )
except Exception as e:  # noqa: BLE001
    check("V4 reentrancy", False, f"{type(e).__name__}: {e}")


# --------- V5: regression ---------
print("[V5] regression verify_robot_008/009/010/011")
for vid in ("008", "009", "010", "011"):
    try:
        env = os.environ.copy()
        env.setdefault("PYTHONPATH", os.getcwd())
        p = subprocess.run(
            [sys.executable, f"scripts/verify_robot_{vid}.py"],
            capture_output=True, text=True, timeout=120, env=env,
        )
        check(
            f"verify_robot_{vid} rc==0",
            p.returncode == 0,
            f"rc={p.returncode} tail={p.stdout[-200:]}",
        )
    except Exception as e:  # noqa: BLE001
        check(f"verify_robot_{vid}", False, f"{type(e).__name__}: {e}")


elapsed = time.time() - t0
print(f"\nelapsed={elapsed:.2f}s errors={len(errors)}")
if errors:
    for e in errors:
        print("  - " + e)
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
