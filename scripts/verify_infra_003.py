"""verify_infra_003 — 运行时健康指标 + SLO 告警。

V1  默认 OFF：metrics_enabled_from_env(env={}) is False；main 不构造 collector
V2  COCO_METRICS=1 构造成功 + 后台线程启动
V3  内置 source factory 工作（power/dialog/proactive/face）
V4  metrics.jsonl 写入格式正确（行式 JSON，ts/metric/value 齐全）
V5  自定义 source 注册 + 采集 → 写入 jsonl
V6  SLO 规则触发 → emit "metrics.slo_breach" 事件
V7  SLO 规则未连续违例不告警（防抖）
V8  component "metrics" 在 AUTHORITATIVE_COMPONENTS
V9  psutil 不可用降级（system_source_factory 返回 None）
V10 jsonl 单行 ≤ MAX_LINE_BYTES（极端长 tags 被截 + _truncated 标志）
V11 stop() 干净退出（线程 join + 文件 flush）
V12 env clamp（COCO_METRICS_INTERVAL=0.1 → 1.0；500 → 300）
V13 main.py 在 COCO_METRICS=1 时构造 _metrics（源码字符串校验）
V14 config.py MetricsConfig 字段从 env 读取并填入 CocoConfig
V15 SLO latched 行为：持续违例只 emit 一次；healthy 后再次累积
V16 stop bridge 不泄漏：start/stop 反复 5 次后 bridge 线程清零
V17 cfg.metrics 真驱动 collector：main.py 用 cfg.metrics.path / interval_s
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import threading
import time
from contextlib import redirect_stderr
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco import metrics as cm  # noqa: E402
from coco.metrics import (  # noqa: E402
    Metric,
    MetricsCollector,
    SLORule,
    MAX_LINE_BYTES,
    metrics_enabled_from_env,
    interval_from_env,
    path_from_env,
    default_slo_rules,
    build_default_collector,
    system_source_factory,
    power_source_factory,
    dialog_source_factory,
    proactive_source_factory,
    face_tracks_source_factory,
)
from coco.logging_setup import setup_logging, AUTHORITATIVE_COMPONENTS  # noqa: E402
from coco.config import load_config, MetricsConfig  # noqa: E402


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
# fakes
# ---------------------------------------------------------------------------


class _FakePower:
    def __init__(self, state="active"):
        class _S:
            def __init__(self, v):
                self.value = v
        self._s = _S(state)

    def current_state(self):
        return self._s


class _FakeDialog:
    def __init__(self, n=3):
        self._n = n

    def recent_turns(self):
        return [("u", "a")] * self._n


class _FakeProactiveStats:
    triggered = 5


class _FakeProactive:
    stats = _FakeProactiveStats()


class _FakeSnap:
    def __init__(self, n_tracks=2):
        self.tracks = tuple(range(n_tracks))


class _FakeFaceTracker:
    def __init__(self, n=2):
        self._n = n

    def latest(self):
        return _FakeSnap(self._n)


# ---------------------------------------------------------------------------
# V1
# ---------------------------------------------------------------------------


def v1_default_off() -> None:
    _section("V1 默认 OFF")
    _check("metrics_enabled_from_env(env={})==False", metrics_enabled_from_env(env={}) is False)
    _check("metrics_enabled_from_env(COCO_METRICS=0)==False", metrics_enabled_from_env(env={"COCO_METRICS": "0"}) is False)
    _check("metrics_enabled_from_env(COCO_METRICS=1)==True", metrics_enabled_from_env(env={"COCO_METRICS": "1"}) is True)
    # main.py 文档锁：默认 OFF 时不构造
    main_src = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    _check(
        "main.py 用 metrics_enabled() 守卫构造 _metrics",
        "metrics_enabled()" in main_src and "_metrics = _build_metrics" in main_src,
    )


# ---------------------------------------------------------------------------
# V2
# ---------------------------------------------------------------------------


def v2_collector_starts(tmp_dir: Path) -> None:
    _section("V2 COCO_METRICS=1 collector 启动")
    p = tmp_dir / "v2.jsonl"
    c = MetricsCollector(path=p, interval_s=0.1)
    c.add_source(lambda: [Metric("noop", 1)])
    c.start()
    time.sleep(0.35)
    running = c.is_running()
    c.stop(timeout=2.0)
    _check("线程启动", running)
    _check("线程退出", not c.is_running())
    _check("jsonl 文件已生成", p.exists())


# ---------------------------------------------------------------------------
# V3
# ---------------------------------------------------------------------------


def v3_builtin_sources() -> None:
    _section("V3 内置 source 注册")
    # power
    src = power_source_factory(_FakePower("active"))
    metrics_out = list(src()) if src else []
    _check("power source 返回 power_state metric", any(m.name == "power_state" for m in metrics_out))
    _check("power_state value=2 (active)", any(m.value == 2 for m in metrics_out))
    # power None
    _check("power=None → factory 返回 None", power_source_factory(None) is None)

    # dialog
    src = dialog_source_factory(_FakeDialog(4))
    metrics_out = list(src()) if src else []
    _check("dialog_turns_total=4", any(m.name == "dialog_turns_total" and m.value == 4 for m in metrics_out))

    # proactive
    src = proactive_source_factory(_FakeProactive())
    metrics_out = list(src()) if src else []
    _check("proactive_topics_total=5", any(m.name == "proactive_topics_total" and m.value == 5 for m in metrics_out))

    # face
    src = face_tracks_source_factory(_FakeFaceTracker(3))
    metrics_out = list(src()) if src else []
    _check("face_tracks_active=3", any(m.name == "face_tracks_active" and m.value == 3 for m in metrics_out))


# ---------------------------------------------------------------------------
# V4
# ---------------------------------------------------------------------------


def v4_jsonl_format(tmp_dir: Path) -> None:
    _section("V4 jsonl 格式")
    p = tmp_dir / "v4.jsonl"
    c = MetricsCollector(path=p, interval_s=10.0)
    c.add_source(lambda: [Metric("foo", 1.5, ts=1700000000.0, tags={"unit": "x"})])
    c.tick_once()
    c.stop()
    lines = [l for l in p.read_text().splitlines() if l.strip()]
    _check("写入至少 1 行", len(lines) >= 1)
    parsed = json.loads(lines[0])
    _check("行有 ts 字段", "ts" in parsed)
    _check("行有 metric 字段", parsed.get("metric") == "foo")
    _check("行有 value 字段", parsed.get("value") == 1.5)
    _check("行有 tags 字段", parsed.get("tags", {}).get("unit") == "x")


# ---------------------------------------------------------------------------
# V5
# ---------------------------------------------------------------------------


def v5_custom_source(tmp_dir: Path) -> None:
    _section("V5 自定义 source 注册 + 采集")
    p = tmp_dir / "v5.jsonl"
    c = MetricsCollector(path=p, interval_s=10.0)
    captured = []
    c.add_source(lambda: [Metric("custom_x", 42)])
    c.add_source(lambda: [Metric("custom_y", "hello", tags={"k": "v"})])
    out = c.tick_once()
    captured.extend(out)
    c.stop()
    _check("采集到 ≥2 条 metric", len(captured) >= 2)
    names = {m.name for m in captured}
    _check("custom_x in metrics", "custom_x" in names)
    _check("custom_y in metrics", "custom_y" in names)
    lines = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    _check("两条都写入 jsonl", len(lines) >= 2)


# ---------------------------------------------------------------------------
# V6
# ---------------------------------------------------------------------------


def v6_slo_breach_emits(tmp_dir: Path) -> None:
    _section("V6 SLO 触发 emit")
    p = tmp_dir / "v6.jsonl"
    rule = SLORule(metric="cpu", op=">", threshold=80.0, window_n=2, severity="warn")
    c = MetricsCollector(path=p, interval_s=10.0, slo_rules=[rule])
    c.add_source(lambda: [Metric("cpu", 95.0)])

    # 用 contextual stderr 替换 + 在替换后才 setup_logging（让 handler 绑到 buf）
    buf = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = buf
    try:
        setup_logging(jsonl=True, level="INFO")
        c.tick_once()  # 1 次违例 — 不应 emit
        c.tick_once()  # 2 次违例 — 应 emit
    finally:
        sys.stderr = real_stderr
        setup_logging(jsonl=False)
    c.stop()

    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    breach_lines = []
    for l in lines:
        try:
            r = json.loads(l)
            if r.get("event") == "slo_breach" and r.get("component") == "metrics":
                breach_lines.append(r)
        except Exception:
            pass
    _check("emit ≥1 次 metrics.slo_breach", len(breach_lines) >= 1, f"got {len(breach_lines)} lines")
    if breach_lines:
        b = breach_lines[0]
        _check("breach 包含 metric=cpu", b.get("metric") == "cpu")
        _check("breach 包含 op=>", b.get("op") == ">")
        _check("breach 包含 value=95.0", b.get("value") == 95.0)


# ---------------------------------------------------------------------------
# V7
# ---------------------------------------------------------------------------


def v7_slo_debounce(tmp_dir: Path) -> None:
    _section("V7 SLO 防抖（不连续违例不告警）")
    p = tmp_dir / "v7.jsonl"
    rule = SLORule(metric="x", op=">", threshold=10.0, window_n=3, severity="warn")
    c = MetricsCollector(path=p, interval_s=10.0, slo_rules=[rule])
    seq = [20.0, 5.0, 20.0, 5.0, 20.0, 5.0]
    idx = {"i": 0}

    def _src():
        v = seq[idx["i"] % len(seq)]
        idx["i"] += 1
        return [Metric("x", v)]

    c.add_source(_src)

    buf = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = buf
    try:
        setup_logging(jsonl=True, level="INFO")
        for _ in range(6):
            c.tick_once()
    finally:
        sys.stderr = real_stderr
        setup_logging(jsonl=False)
    c.stop()

    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    breach_lines = []
    for l in lines:
        try:
            r = json.loads(l)
            if r.get("event") == "slo_breach":
                breach_lines.append(r)
        except Exception:
            pass
    _check("不连续违例 → 0 次 emit", len(breach_lines) == 0, f"got {len(breach_lines)}")


# ---------------------------------------------------------------------------
# V8
# ---------------------------------------------------------------------------


def v8_authoritative_component() -> None:
    _section("V8 'metrics' ∈ AUTHORITATIVE_COMPONENTS")
    _check("'metrics' 在白名单", "metrics" in AUTHORITATIVE_COMPONENTS)


# ---------------------------------------------------------------------------
# V9
# ---------------------------------------------------------------------------


def v9_psutil_unavailable() -> None:
    _section("V9 psutil 不可用降级")
    # mock import 失败：把 _try_import_psutil 替换返回 None
    orig = cm._try_import_psutil
    cm._try_import_psutil = lambda: None
    try:
        src = system_source_factory()
        _check("psutil 不可用 → factory 返回 None", src is None)
    finally:
        cm._try_import_psutil = orig


# ---------------------------------------------------------------------------
# V10
# ---------------------------------------------------------------------------


def v10_truncate(tmp_dir: Path) -> None:
    _section("V10 jsonl 单行 ≤ MAX_LINE_BYTES")
    huge_tag = "x" * (MAX_LINE_BYTES + 500)
    line = cm._serialize_metric(Metric("metric", 1, tags={"big": huge_tag}))
    n = len(line.encode("utf-8"))
    _check(f"序列化后 ≤ {MAX_LINE_BYTES}", n <= MAX_LINE_BYTES, f"got {n}")
    parsed = json.loads(line)
    _check("含 _truncated 标志", parsed.get("_truncated") is True)
    _check("仍含 metric/value/ts", all(k in parsed for k in ("metric", "value", "ts")))


# ---------------------------------------------------------------------------
# V11
# ---------------------------------------------------------------------------


def v11_clean_stop(tmp_dir: Path) -> None:
    _section("V11 stop() 干净退出 + flush")
    p = tmp_dir / "v11.jsonl"
    c = MetricsCollector(path=p, interval_s=0.05)
    c.add_source(lambda: [Metric("alive", 1)])
    c.start()
    time.sleep(0.2)
    c.stop(timeout=2.0)
    _check("线程已退出", not c.is_running())
    # 文件应已 flush + close
    text = p.read_text() if p.exists() else ""
    _check("文件至少有 1 行", len([l for l in text.splitlines() if l.strip()]) >= 1)
    # 二次 stop 不抛
    try:
        c.stop()
        _check("二次 stop 幂等", True)
    except Exception as e:  # noqa: BLE001
        _check("二次 stop 幂等", False, repr(e))


# ---------------------------------------------------------------------------
# V12
# ---------------------------------------------------------------------------


def v12_env_clamp() -> None:
    _section("V12 env clamp")
    _check("interval 0.1 → 1.0", interval_from_env(env={"COCO_METRICS_INTERVAL": "0.1"}) == 1.0)
    _check("interval 500 → 300", interval_from_env(env={"COCO_METRICS_INTERVAL": "500"}) == 300.0)
    _check("interval bad → 5.0", interval_from_env(env={"COCO_METRICS_INTERVAL": "abc"}) == 5.0)
    _check("interval 默认 5.0", interval_from_env(env={}) == 5.0)
    _check("path env 覆盖", str(path_from_env(env={"COCO_METRICS_PATH": "/tmp/x.jsonl"})) == "/tmp/x.jsonl")


# ---------------------------------------------------------------------------
# V13
# ---------------------------------------------------------------------------


def v13_main_integration() -> None:
    _section("V13 main.py 集成")
    src = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    _check("import metrics module", "from coco.metrics import" in src)
    _check("metrics_enabled() 守卫", "_metrics_enabled()" in src or "metrics_enabled" in src)
    _check("_build_metrics 调用", "_build_metrics(" in src)
    _check("_metrics.start(stop_event)", "_metrics.start(stop_event)" in src)
    _check("_metrics.stop(...) 在 finally", "_metrics.stop(" in src)


# ---------------------------------------------------------------------------
# V14
# ---------------------------------------------------------------------------


def v14_config_integration() -> None:
    _section("V14 CocoConfig.metrics 字段")
    cfg = load_config(env={})
    _check("默认 enabled=False", cfg.metrics.enabled is False)
    _check("默认 interval=5.0", cfg.metrics.interval_s == 5.0)
    cfg2 = load_config(env={
        "COCO_METRICS": "1",
        "COCO_METRICS_INTERVAL": "10",
        "COCO_METRICS_PATH": "/tmp/m.jsonl",
    })
    _check("env enabled=True", cfg2.metrics.enabled is True)
    _check("env interval=10.0", cfg2.metrics.interval_s == 10.0)
    _check("env path=/tmp/m.jsonl", cfg2.metrics.path == "/tmp/m.jsonl")


# ---------------------------------------------------------------------------
# V15: SLO latched
# ---------------------------------------------------------------------------


def v15_slo_latched(tmp_dir: Path) -> None:
    _section("V15 SLO latched 行为")
    p = tmp_dir / "v15.jsonl"
    # cooldown_s=0 让 cooldown 不影响 latched 验证
    rule = SLORule(metric="cpu", op=">", threshold=80.0, window_n=2, severity="warn", cooldown_s=0.0)
    c = MetricsCollector(path=p, interval_s=10.0, slo_rules=[rule])

    # 阶段 1: 持续违例 6 次 — 应只 emit 1 次（latched）
    val = {"v": 95.0}
    c.add_source(lambda: [Metric("cpu", val["v"])])

    buf = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = buf
    try:
        setup_logging(jsonl=True, level="INFO")
        for _ in range(6):
            c.tick_once()
        # 阶段 2: 一次 healthy 解锁
        val["v"] = 50.0
        c.tick_once()
        # 阶段 3: 再次连续违例 — 应再 emit 1 次
        val["v"] = 95.0
        for _ in range(4):
            c.tick_once()
    finally:
        sys.stderr = real_stderr
        setup_logging(jsonl=False)
    c.stop()

    breaches = []
    for l in buf.getvalue().splitlines():
        try:
            r = json.loads(l)
            if r.get("event") == "slo_breach":
                breaches.append(r)
        except Exception:
            pass
    _check("持续违例 6 次 + healthy + 4 次违例 → 共 emit 2 次 (latched)", len(breaches) == 2, f"got {len(breaches)}")


# ---------------------------------------------------------------------------
# V16: stop bridge 不泄漏
# ---------------------------------------------------------------------------


def v16_bridge_no_leak(tmp_dir: Path) -> None:
    _section("V16 stop bridge 不泄漏")
    p = tmp_dir / "v16.jsonl"
    for i in range(5):
        c = MetricsCollector(path=p, interval_s=0.05)
        c.add_source(lambda: [Metric("alive", 1)])
        ext = threading.Event()
        c.start(stop_event=ext)
        time.sleep(0.1)
        c.stop(timeout=2.0)
    # 给 bridge 线程退出留点时间
    time.sleep(0.6)
    bridge_threads = [t for t in threading.enumerate() if t.name == "coco-metrics-stop-bridge"]
    _check("bridge 线程不累积（≤1）", len(bridge_threads) <= 1, f"got {len(bridge_threads)} alive")


# ---------------------------------------------------------------------------
# V17: cfg.metrics 真驱动 collector
# ---------------------------------------------------------------------------


def v17_cfg_drives_collector() -> None:
    _section("V17 cfg.metrics 驱动 collector")
    src = (ROOT / "coco" / "main.py").read_text(encoding="utf-8")
    _check("main.py 引用 cfg.metrics", "_coco_cfg, \"metrics\"" in src or "_coco_cfg.metrics" in src or "getattr(_coco_cfg" in src and "metrics" in src)
    _check("main.py 用 _mcfg.path 构造 collector", "_mcfg.path" in src or "_m_path" in src)
    _check("main.py 用 _mcfg.interval_s", "_mcfg.interval_s" in src or "_m_interval" in src)
    _check("main.py 把 path 传给 _build_metrics", "path=_m_path" in src or "path=Path(_mcfg.path)" in src)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=== verify_infra_003 ===", flush=True)
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="coco-metrics-verify-"))
    try:
        v1_default_off()
        v2_collector_starts(tmp)
        v3_builtin_sources()
        v4_jsonl_format(tmp)
        v5_custom_source(tmp)
        v6_slo_breach_emits(tmp)
        v7_slo_debounce(tmp)
        v8_authoritative_component()
        v9_psutil_unavailable()
        v10_truncate(tmp)
        v11_clean_stop(tmp)
        v12_env_clamp()
        v13_main_integration()
        v14_config_integration()
        v15_slo_latched(tmp)
        v16_bridge_no_leak(tmp)
        v17_cfg_drives_collector()
    finally:
        # cleanup tmp dir
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass

    print(f"\n--- 总结 ---", flush=True)
    print(f"PASS={len(PASSES)}  FAIL={len(FAILURES)}", flush=True)

    ev_dir = ROOT / "evidence" / "infra-003"
    ev_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "verification": "verify_infra_003",
        "pass_count": len(PASSES),
        "fail_count": len(FAILURES),
        "passes": PASSES,
        "failures": FAILURES,
    }
    (ev_dir / "verify_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if FAILURES:
        print("==> FAIL: infra-003 有 failure", flush=True)
        for f in FAILURES:
            print(f"  - {f}", flush=True)
        return 1
    print("==> PASS: infra-003 verification 全部通过", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
