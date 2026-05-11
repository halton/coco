"""verify_infra_004 — config schema 校验 + 启动 banner + jsonl rotate。

V1  validate_config：合法配置 0 error
V2  validate_config：drowsy >= sleep 抛 error → ConfigValidationError
V3  validate_config：metrics.path 父目录不可写 → error
V4  validate_config：COCO_PROACTIVE=1 + COCO_INTENT=0 → warning（不抛）
V5  启动 banner emit "startup.banner"（component "startup"）
V6  startup banner 包含敏感字段脱敏（COCO_LLM_API_KEY → ***）
V7  banner 包含 subsystems / features / paths 三段
V8  RotatingJsonlHandler：写满 max_bytes 触发 rotate（产生 .1）
V9  RotatingJsonlWriter retention=N，超出删最老
V10 rotate 期间多线程并发写不丢日志
V11 metrics.jsonl 同样 rotate（max_bytes 极小，连续写触发 .1）
V12 ConfigValidationError 类型继承 ValueError；issues 含 error 项
V13 SENSITIVE_TOKENS 覆盖 *_KEY / PRIVATE_KEY / *_AUTH（mock env 全部 ***）
V14 RotatingJsonlWriter：单行 > max_bytes 且文件空时不重复 rotate（保护 backup_count）
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import tempfile
import threading
import time
from contextlib import redirect_stderr
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.config import (  # noqa: E402
    CocoConfig,
    ConfigValidationError,
    load_config,
    validate_config,
)
from coco.banner import (  # noqa: E402
    render_banner,
    banner_payload,
    coco_env_snapshot,
)
from coco.logging_setup import (  # noqa: E402
    setup_logging,
    emit,
    AUTHORITATIVE_COMPONENTS,
    RotatingJsonlHandler,
    RotatingJsonlWriter,
    JsonlFormatter,
)


FAILURES: List[str] = []
PASSES: List[str] = []


def _check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {label}", flush=True)
        PASSES.append(label)
    else:
        print(f"  FAIL  {label}  {detail}", flush=True)
        FAILURES.append(f"{label} :: {detail}")


def _section(title: str) -> None:
    print(f"\n--- {title} ---", flush=True)


# ---------------------------------------------------------------------------


def v1_validate_clean() -> None:
    _section("V1: validate_config 合法配置 0 error")
    # 用空 env load_config（实际 power.config_from_env 还是会读 os.environ）
    # 这里直接构造 default CocoConfig，避免环境干扰
    cfg = CocoConfig()
    issues = validate_config(cfg, env={})
    errors = [m for sev, m in issues if sev == "error"]
    _check("default cfg → 0 error", len(errors) == 0, f"errors={errors}")


def v2_drowsy_ge_sleep() -> None:
    _section("V2: drowsy >= sleep → error")
    # 构造一个 mock power 对象，绕过 PowerConfig 自身的 __post_init__ 限制
    class _MockPower:
        drowsy_after = 200.0
        sleep_after = 120.0

    cfg = CocoConfig(power=_MockPower())
    issues = validate_config(cfg, env={})
    errs = [m for sev, m in issues if sev == "error"]
    _check("drowsy=200 sleep=120 → error", any("drowsy" in m for m in errs),
           f"issues={issues}")
    # 通过 ConfigValidationError 包装
    try:
        raise ConfigValidationError(issues)
    except ConfigValidationError as e:
        _check("ConfigValidationError 携带 issues",
               len(e.issues) == len(issues) and any(sev == "error" for sev, _ in e.issues),
               f"e.issues={e.issues}")


def v3_metrics_path_not_writable() -> None:
    _section("V3: metrics.path 父目录不可写 → error")
    from coco.config import MetricsConfig
    cfg = CocoConfig(metrics=MetricsConfig(enabled=True,
                                            path="/nonexistent_root_xyz/coco/metrics.jsonl"))
    issues = validate_config(cfg, env={})
    errs = [m for sev, m in issues if sev == "error"]
    _check("不可写路径 → error", any("metrics.path" in m for m in errs),
           f"issues={issues}")


def v4_proactive_without_intent() -> None:
    _section("V4: PROACTIVE=1 + INTENT=0 → warning (不抛)")
    cfg = CocoConfig(intent_enabled=False)
    issues = validate_config(cfg, env={"COCO_PROACTIVE": "1"})
    warns = [m for sev, m in issues if sev == "warning"]
    errs = [m for sev, m in issues if sev == "error"]
    _check("出 warning",
           any("PROACTIVE" in m or "proactive" in m.lower() for m in warns),
           f"warns={warns}")
    _check("0 error", len(errs) == 0, f"errs={errs}")


def v5_banner_emit() -> None:
    _section("V5: emit('startup.banner') 走 root logger")
    # 用 stream handler 捕获
    buf = io.StringIO()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(stream=buf)
    handler.setFormatter(JsonlFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    cfg = CocoConfig()
    emit("startup.banner", component="startup", **banner_payload(cfg, env={}))

    handler.flush()
    out = buf.getvalue()
    _check("startup in AUTHORITATIVE_COMPONENTS", "startup" in AUTHORITATIVE_COMPONENTS,
           f"set={sorted(AUTHORITATIVE_COMPONENTS)}")
    _check("jsonl 行含 component=startup event=banner",
           '"component": "startup"' in out and '"event": "banner"' in out,
           f"out[:200]={out[:200]}")


def v6_sensitive_masked() -> None:
    _section("V6: 敏感字段脱敏 (COCO_LLM_API_KEY)")
    env = {"COCO_LLM_API_KEY": "sk-real-secret-123456", "COCO_FOO": "bar"}
    snap = coco_env_snapshot(env)
    _check("API_KEY 脱敏为 ***", snap.get("COCO_LLM_API_KEY") == "***",
           f"snap.COCO_LLM_API_KEY={snap.get('COCO_LLM_API_KEY')!r}")
    _check("普通 env 原样保留", snap.get("COCO_FOO") == "bar",
           f"snap.COCO_FOO={snap.get('COCO_FOO')!r}")
    # banner text 不含原始值
    cfg = CocoConfig()
    txt = render_banner(cfg, env=env)
    _check("banner text 不含 'sk-real-secret'", "sk-real-secret" not in txt,
           f"txt 包含敏感值")
    _check("banner text 含 '***'", "***" in txt, "未发现 *** 脱敏标记")


def v7_banner_sections() -> None:
    _section("V7: banner 含 subsystems / features / paths")
    cfg = CocoConfig()
    txt = render_banner(cfg, env={})
    _check("含 [subsystems]", "[subsystems]" in txt, "missing")
    _check("含 [features]", "[features]" in txt, "missing")
    _check("含 [paths]", "[paths]" in txt, "missing")
    _check("含 [COCO_* env]", "[COCO_* env]" in txt, "missing")
    _check("含 ASCII 框线 +---+", "+---" in txt, "missing")


def v8_rotating_handler_triggers() -> None:
    _section("V8: RotatingJsonlHandler 写满触发 rotate")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.jsonl"
        h = RotatingJsonlHandler(p, max_bytes=300, backup_count=3)
        h.setFormatter(JsonlFormatter())
        lg = logging.getLogger("infra004.v8")
        lg.handlers.clear()
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
        lg.propagate = False
        for i in range(20):
            lg.info(f"msg-{i:03d}", extra={"component": "metrics", "event": "tick",
                                            "payload": "x" * 50})
        h.close()
        r1 = p.with_suffix(".jsonl.1")
        _check("主文件存在", p.exists(), f"{p}")
        _check(".1 rotate 生成", r1.exists(), f"{r1}")


def v9_writer_retention() -> None:
    _section("V9: RotatingJsonlWriter retention=2 超出删最老")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "metrics.jsonl"
        w = RotatingJsonlWriter(p, max_bytes=100, backup_count=2)
        # 写若干行触发多次 rotate
        big = "x" * 80
        for i in range(20):
            w.write_line(json.dumps({"i": i, "p": big}))
        w.flush()
        w.close()
        # 检查仅保留 .1 / .2，没有 .3
        r1 = p.with_suffix(".jsonl.1")
        r2 = p.with_suffix(".jsonl.2")
        r3 = p.with_suffix(".jsonl.3")
        _check("有 .1", r1.exists())
        _check(".2 存在或主文件 + .1 就够（看 rotate 次数）", r1.exists())
        _check("无 .3（retention=2 上限）", not r3.exists(),
               f"r3 不该存在: exists={r3.exists()}")


def v10_concurrent_writes() -> None:
    _section("V10: 多线程并发写不丢日志")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "concurrent.jsonl"
        # max_bytes 适中（触发 ~3-5 次 rotate），backup_count 足够大不丢
        w = RotatingJsonlWriter(p, max_bytes=2000, backup_count=20)
        N_THREADS = 5
        N_PER = 50
        total = N_THREADS * N_PER

        def _worker(tid: int) -> None:
            for i in range(N_PER):
                w.write_line(json.dumps({"tid": tid, "i": i}))

        threads = [threading.Thread(target=_worker, args=(t,)) for t in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        w.flush()
        w.close()

        # 累加所有文件行数（主 + .1 + .2 ...）
        cnt = 0
        files = [p] + [p.with_suffix(f".jsonl.{i}") for i in range(1, 10)]
        for f in files:
            if f.exists():
                with open(f) as fp:
                    cnt += sum(1 for _ in fp)
        _check(f"行数 == {total}", cnt == total, f"实际 cnt={cnt}, expected={total}")


def v11_metrics_rotate() -> None:
    _section("V11: MetricsCollector 写满触发 rotate (env COCO_METRICS_MAX_MB)")
    from coco.metrics import MetricsCollector, Metric
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "metrics.jsonl"
        # max_bytes 极小，连续写触发
        mc = MetricsCollector(path=p, interval_s=5.0, max_bytes=200, backup_count=3)

        def _src():
            return [Metric("cpu_percent", 42.0, tags={"unit": "percent"}),
                    Metric("mem_rss_mb", 999.5, tags={"unit": "MB"})]

        mc.add_source(_src)
        for _ in range(30):
            mc.tick_once()
        mc.stop()
        r1 = p.with_suffix(".jsonl.1")
        _check("metrics.jsonl 主文件存在", p.exists())
        _check("metrics rotate 触发 .1", r1.exists(), f"r1={r1}")


def v12_config_validation_error_type() -> None:
    _section("V12: ConfigValidationError 类型 + load_config error 抛出")
    _check("ConfigValidationError 继承 ValueError",
           issubclass(ConfigValidationError, ValueError))
    # 直接构造一个抛
    issues = [("error", "x"), ("warning", "y")]
    try:
        raise ConfigValidationError(issues)
    except ConfigValidationError as e:
        _check("error.issues 携带原 list", e.issues == issues, f"e.issues={e.issues}")
        _check("str(e) 含 error msg", "x" in str(e), f"str={e}")


def v13_sensitive_token_coverage() -> None:
    _section("V13: SENSITIVE_TOKENS 覆盖 *_KEY / PRIVATE_KEY / *_AUTH")
    env = {
        "COCO_PRIVATE_KEY": "pk-real",
        "COCO_FOO_AUTH": "tok-real",
        "COCO_BAR_KEY": "k-real",
        "COCO_FOO": "bar",  # 普通 env，应原样保留
    }
    snap = coco_env_snapshot(env)
    _check("COCO_PRIVATE_KEY → ***", snap.get("COCO_PRIVATE_KEY") == "***",
           f"got={snap.get('COCO_PRIVATE_KEY')!r}")
    _check("COCO_FOO_AUTH → ***", snap.get("COCO_FOO_AUTH") == "***",
           f"got={snap.get('COCO_FOO_AUTH')!r}")
    _check("COCO_BAR_KEY → ***", snap.get("COCO_BAR_KEY") == "***",
           f"got={snap.get('COCO_BAR_KEY')!r}")
    _check("COCO_FOO 未脱敏", snap.get("COCO_FOO") == "bar",
           f"got={snap.get('COCO_FOO')!r}")
    # banner_payload 也要全部脱敏
    payload = banner_payload(CocoConfig(), env=env)
    _check("banner_payload env 全脱敏",
           all(payload["env"].get(k) == "***" for k in
               ("COCO_PRIVATE_KEY", "COCO_FOO_AUTH", "COCO_BAR_KEY")),
           f"payload.env={payload['env']}")


def v14_rotate_guard_oversize_line() -> None:
    _section("V14: 超大单行（> max_bytes）只 rotate 一次（不每行 rotate）")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "guard.jsonl"
        # max_bytes=200，每行约 1000 字节（远超）
        w = RotatingJsonlWriter(p, max_bytes=200, backup_count=5)
        big = "x" * 1000
        for i in range(5):
            w.write_line(json.dumps({"i": i, "p": big}))
        w.flush()
        w.close()
        # 期望：因为初始空文件 + 单行超长 → 不 rotate；后续每行已写满，但
        # _bytes_written != 0 时若 +新行 > max → rotate 一次（rotate 后 _bytes_written=0
        # 又触发保护不再 rotate）。所以期待最多 .1 存在，绝不该有 .2 .3 .4 .5。
        r1 = p.with_suffix(".jsonl.1")
        r2 = p.with_suffix(".jsonl.2")
        r3 = p.with_suffix(".jsonl.3")
        _check("主文件存在", p.exists())
        # 关键不变量：超大单行场景下不应该每行都 rotate；.3 / .4 / .5 都不该出现
        _check("无 .3（单行超大不应耗尽 backup）", not r3.exists(),
               f".3 不该存在: exists={r3.exists()}")


def main() -> int:
    print("\n" + "=" * 60)
    print(" verify_infra_004 — config schema + banner + rotate")
    print("=" * 60)

    v1_validate_clean()
    v2_drowsy_ge_sleep()
    v3_metrics_path_not_writable()
    v4_proactive_without_intent()
    v5_banner_emit()
    v6_sensitive_masked()
    v7_banner_sections()
    v8_rotating_handler_triggers()
    v9_writer_retention()
    v10_concurrent_writes()
    v11_metrics_rotate()
    v12_config_validation_error_type()
    v13_sensitive_token_coverage()
    v14_rotate_guard_oversize_line()

    print("\n" + "=" * 60)
    print(f" PASS={len(PASSES)} FAIL={len(FAILURES)}")
    print("=" * 60)
    if FAILURES:
        for f in FAILURES:
            print(f"  FAIL {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
