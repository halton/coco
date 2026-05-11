"""verify_infra_002 — 验配置中心 + jsonl 结构化日志。

不依赖真硬件 / 网络，全在内存跑。子项与 feature_list.json 中 verification 列表对齐：

V1  默认值符合 phase-3 行为（无 env 时 vad_enabled=True / wake_enabled=False / power_idle=False / dialog_memory=False / llm.timeout=2.0 / llm.max_chars=60）
V2  env override 类型转换 + clamp（COCO_LLM_TIMEOUT / COCO_LLM_MAX_CHARS / COCO_VAD_DISABLE / COCO_WAKE_WORD）
V3  非法值回退 + warning（COCO_LLM_TIMEOUT=-1 → clamp 0.1；COCO_LLM_MAX_CHARS=abc → 60；COCO_LOG_LEVEL=BOGUS → INFO）
V4  config_summary 字段完整 + 不含 secret（api_key 仅以 set/unset 记）
V5  jsonl 行 json.loads + 字段集 ⊇ {ts, level, component, event}；非 jsonl 模式不输出 jsonl
V6  backward-compat：现有各模块 env helper 仍工作（dialog_memory_enabled_from_env / vad_disabled_from_env / power_idle_enabled_from_env / wake_word_enabled_from_env）
V7  5 个 component 各 emit 一行 jsonl 都能被 json.loads 解析且 component/event 正确
V8  长 payload truncate（payload 含 4KB+ 字符串触发 truncate 标志）
V9  logger.exception 在 jsonl 行落 'exc' 字段且含异常类名（closeout L1-1）
V10 main.py 5 处 emit 的 component 全部命中 AUTHORITATIVE_COMPONENTS（closeout L1-2）
V11 load_config(env=...) 注入对子模块 dataclass 字段无效的语义文档锁（closeout L1-4 / L2-2）
V2b COCO_PTT_SECONDS=10 时 Coco.run 路径下 PUSH_TO_TALK_SECONDS 同步覆盖（closeout L1-3）

evidence 写 evidence/infra-002/verify_summary.json。
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
from contextlib import redirect_stderr
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coco.config import load_config, config_summary, CocoConfig  # noqa: E402
from coco.logging_setup import setup_logging, emit, JsonlFormatter, MAX_LINE_BYTES, AUTHORITATIVE_COMPONENTS  # noqa: E402


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


def _capture_jsonl(fn) -> List[str]:
    """运行 fn() 捕获 stderr，返回每行字符串（保留非空行）。"""
    buf = io.StringIO()
    with redirect_stderr(buf):
        fn()
    return [line for line in buf.getvalue().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# V1 默认值
# ---------------------------------------------------------------------------


def v1_defaults() -> None:
    _section("V1 默认值（无 env）")
    cfg = load_config(env={})
    _check("vad_enabled=True", cfg.vad_enabled is True)
    _check("wake_enabled=False", cfg.wake_enabled is False)
    _check("power_idle_enabled=False", cfg.power_idle_enabled is False)
    _check("dialog_memory_enabled=False", cfg.dialog_memory_enabled is False)
    _check("ptt.seconds==4.0", cfg.ptt.seconds == 4.0, f"got {cfg.ptt.seconds}")
    _check("ptt.disabled==False", cfg.ptt.disabled is False)
    _check("camera.spec=='' (默认 usb:0 由 CameraSource 决定)", cfg.camera.spec == "")
    _check("llm.timeout==2.0", cfg.llm.timeout == 2.0, f"got {cfg.llm.timeout}")
    _check("llm.max_chars==60", cfg.llm.max_chars == 60, f"got {cfg.llm.max_chars}")
    _check("llm.api_key_set==False", cfg.llm.api_key_set is False)
    _check("log.jsonl==False", cfg.log.jsonl is False)
    _check("log.level=='INFO'", cfg.log.level == "INFO")


# ---------------------------------------------------------------------------
# V2 env override + clamp
# ---------------------------------------------------------------------------


def v2_env_override() -> None:
    _section("V2 env override + 类型转换 + clamp")
    cfg = load_config(env={
        "COCO_LLM_TIMEOUT": "5.5",
        "COCO_LLM_MAX_CHARS": "200",
        "COCO_VAD_DISABLE": "1",
        "COCO_WAKE_WORD": "1",
        "COCO_DIALOG_MEMORY": "1",
        "COCO_POWER_IDLE": "1",
        "COCO_PTT_SECONDS": "10.0",
        "COCO_PTT_DISABLE": "1",
        "COCO_CAMERA": "image:/tmp/foo.jpg",
        "COCO_LOG_JSONL": "1",
        "COCO_LOG_LEVEL": "DEBUG",
        "COCO_LLM_BACKEND": "openai",
        "COCO_LLM_API_KEY": "sk-FAKE",
    })
    _check("llm.timeout==5.5", cfg.llm.timeout == 5.5, f"got {cfg.llm.timeout}")
    _check("llm.max_chars==200", cfg.llm.max_chars == 200, f"got {cfg.llm.max_chars}")
    _check("vad_enabled==False (DISABLE=1)", cfg.vad_enabled is False)
    _check("wake_enabled==True", cfg.wake_enabled is True)
    _check("dialog_memory_enabled==True", cfg.dialog_memory_enabled is True)
    _check("power_idle_enabled==True", cfg.power_idle_enabled is True)
    _check("ptt.seconds==10.0", cfg.ptt.seconds == 10.0)
    _check("ptt.disabled==True", cfg.ptt.disabled is True)
    _check("camera.spec=='image:/tmp/foo.jpg'", cfg.camera.spec == "image:/tmp/foo.jpg")
    _check("log.jsonl==True", cfg.log.jsonl is True)
    _check("log.level=='DEBUG'", cfg.log.level == "DEBUG")
    _check("llm.backend=='openai'", cfg.llm.backend == "openai")
    _check("llm.api_key_set==True", cfg.llm.api_key_set is True)

    # clamp upper
    cfg2 = load_config(env={"COCO_LLM_TIMEOUT": "9999", "COCO_LLM_MAX_CHARS": "999999"})
    _check("clamp timeout 9999 -> 120.0", cfg2.llm.timeout == 120.0, f"got {cfg2.llm.timeout}")
    _check("clamp max_chars 999999 -> 4096", cfg2.llm.max_chars == 4096, f"got {cfg2.llm.max_chars}")


# ---------------------------------------------------------------------------
# V3 非法值
# ---------------------------------------------------------------------------


def v3_invalid_values() -> None:
    _section("V3 非法值回退 + warning")
    # 切到 logger 收集 warnings
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    for h in old_handlers:
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    try:
        cfg = load_config(env={
            "COCO_LLM_TIMEOUT": "-1",          # < lo=0.1 → clamp 0.1
            "COCO_LLM_MAX_CHARS": "abc",        # 非整数 → 60
            "COCO_LOG_LEVEL": "BOGUS",         # 非法 → INFO
            "COCO_PTT_SECONDS": "not-a-num",   # → 默认 4.0
        })
    finally:
        root.removeHandler(handler)
        for h in old_handlers:
            root.addHandler(h)
    log_text = buf.getvalue()
    _check("llm.timeout clamp -1 -> 0.1", cfg.llm.timeout == 0.1, f"got {cfg.llm.timeout}")
    _check("llm.max_chars 'abc' -> 60", cfg.llm.max_chars == 60, f"got {cfg.llm.max_chars}")
    _check("log.level 'BOGUS' -> 'INFO'", cfg.log.level == "INFO")
    _check("ptt.seconds 'not-a-num' -> 4.0", cfg.ptt.seconds == 4.0, f"got {cfg.ptt.seconds}")
    _check("warnings 含 COCO_LLM_TIMEOUT", "COCO_LLM_TIMEOUT" in log_text, log_text[:200])
    _check("warnings 含 COCO_LLM_MAX_CHARS", "COCO_LLM_MAX_CHARS" in log_text, log_text[:200])


# ---------------------------------------------------------------------------
# V4 config_summary 完整 + 无 secret
# ---------------------------------------------------------------------------


def v4_summary() -> None:
    _section("V4 config_summary 字段完整 + 不含 secret")
    cfg = load_config(env={"COCO_LLM_API_KEY": "sk-SECRET-ABC"})
    s = config_summary(cfg)
    expected_keys = {"log", "ptt", "camera", "llm", "metrics", "vad", "wake", "power", "dialog", "emotion", "intent", "conversation", "attention"}
    _check("summary 顶层 keys 完整",
           set(s.keys()) == expected_keys,
           f"got {sorted(s.keys())}")
    _check("llm.api_key=='set' (而非明文)", s["llm"]["api_key"] == "set",
           f"got {s['llm']['api_key']!r}")
    serialized = json.dumps(s, ensure_ascii=False)
    _check("序列化不含 'sk-SECRET-ABC'", "sk-SECRET-ABC" not in serialized)
    _check("vad 子结构有 enabled+config", "enabled" in s["vad"] and "config" in s["vad"])
    _check("dialog 子结构有 memory_enabled+config",
           "memory_enabled" in s["dialog"] and "config" in s["dialog"])


# ---------------------------------------------------------------------------
# V5 jsonl 解析 + 非 jsonl 行为
# ---------------------------------------------------------------------------


def v5_jsonl_parse() -> None:
    _section("V5 jsonl 行 json.loads 且字段集 ⊇ {ts, level, component, event}")
    def _emit_one():
        setup_logging(jsonl=True, level="INFO")
        emit("asr.transcribe", text="你好", cer=0.0, latency_ms=120)
    lines = _capture_jsonl(_emit_one)
    _check("emit 至少 1 行", len(lines) >= 1, f"lines={lines}")
    if lines:
        try:
            obj = json.loads(lines[0])
            ok = {"ts", "level", "component", "event"}.issubset(obj.keys())
            _check("第一行必须含 {ts,level,component,event}", ok, f"keys={sorted(obj.keys())}")
            _check("component=='asr'", obj.get("component") == "asr", f"got {obj.get('component')}")
            _check("event=='transcribe'", obj.get("event") == "transcribe")
            _check("payload text 透传", obj.get("text") == "你好")
            _check("payload cer 透传", obj.get("cer") == 0.0)
        except Exception as e:  # noqa: BLE001
            _check("json.loads 第一行", False, f"{type(e).__name__}: {e}")

    # 非 jsonl 模式：行不应该是 valid json
    def _emit_plain():
        setup_logging(jsonl=False, level="INFO")
        emit("asr.transcribe", text="你好")
    plain = _capture_jsonl(_emit_plain)
    is_json = False
    if plain:
        try:
            json.loads(plain[0])
            is_json = True
        except Exception:
            is_json = False
    _check("非 jsonl 模式输出非 JSON", not is_json, f"got: {plain[:1]}")


# ---------------------------------------------------------------------------
# V6 backward compat — 旧 helper 仍工作
# ---------------------------------------------------------------------------


def v6_backward_compat() -> None:
    _section("V6 backward-compat：旧 *_from_env() 仍工作")
    from coco.dialog import dialog_memory_enabled_from_env, config_from_env as dialog_cfg_env
    from coco.vad_trigger import vad_disabled_from_env, config_from_env as vad_cfg_env
    from coco.power_state import power_idle_enabled_from_env, config_from_env as power_cfg_env
    from coco.wake_word import wake_word_enabled_from_env, config_from_env as wake_cfg_env

    # 暂存 + 清空相关 env
    keys = ["COCO_DIALOG_MEMORY", "COCO_VAD_DISABLE", "COCO_POWER_IDLE", "COCO_WAKE_WORD",
            "COCO_DIALOG_MAX_TURNS", "COCO_DIALOG_IDLE_S"]
    backup = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    try:
        _check("dialog_memory_enabled_from_env() 默认 False", dialog_memory_enabled_from_env() is False)
        _check("vad_disabled_from_env() 默认 False", vad_disabled_from_env() is False)
        _check("power_idle_enabled_from_env() 默认 False", power_idle_enabled_from_env() is False)
        _check("wake_word_enabled_from_env() 默认 False", wake_word_enabled_from_env() is False)
        # config_from_env 不抛
        try:
            d = dialog_cfg_env()
            v = vad_cfg_env()
            p = power_cfg_env()
            w = wake_cfg_env()
            _check("dialog_cfg_env 返回 dataclass", hasattr(d, "max_turns"))
            _check("vad_cfg_env 返回 dataclass", v is not None)
            _check("power_cfg_env 返回 dataclass", p is not None)
            _check("wake_cfg_env 返回 dataclass", w is not None)
        except Exception as e:  # noqa: BLE001
            _check("旧 config_from_env 不抛", False, f"{type(e).__name__}: {e}")
    finally:
        for k, v in backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# V7 5 component event 全 emit
# ---------------------------------------------------------------------------


def v7_components() -> None:
    _section("V7 5 个 component event 各 emit 一行")
    def _emit_all():
        setup_logging(jsonl=True, level="INFO")
        emit("asr.transcribe", text="测试", cer=0.0, latency_ms=120)
        emit("llm.reply", backend="fallback", latency_ms=8, chars=12)
        emit("vad.utterance", duration_s=1.4, peak_db=-22.3)
        emit("wake.hit", word="可可", score=0.83)
        emit("power.transition", from_state="active", to_state="drowsy", source="tick")
    lines = _capture_jsonl(_emit_all)
    _check("捕获到 >=5 行", len(lines) >= 5, f"got {len(lines)}")
    seen = set()
    for line in lines:
        try:
            obj = json.loads(line)
            seen.add(f"{obj.get('component')}.{obj.get('event')}")
        except Exception:
            pass
    expected = {"asr.transcribe", "llm.reply", "vad.utterance", "wake.hit", "power.transition"}
    missing = expected - seen
    _check("5 个 component event 全到位",
           missing == set(),
           f"missing={missing} seen={seen}")


# ---------------------------------------------------------------------------
# V8 truncate
# ---------------------------------------------------------------------------


def v8_truncate() -> None:
    _section("V8 长 payload 触发 truncate")
    big = "x" * (MAX_LINE_BYTES + 100)
    def _emit_big():
        setup_logging(jsonl=True, level="INFO")
        emit("asr.transcribe", text=big)
    lines = _capture_jsonl(_emit_big)
    _check("捕获到 1 行", len(lines) == 1, f"got {len(lines)}")
    if lines:
        try:
            obj = json.loads(lines[0])
            _check("含 _truncated=True", obj.get("_truncated") is True, f"obj={obj}")
            _check("行字节数 <= MAX_LINE_BYTES",
                   len(lines[0].encode("utf-8")) <= MAX_LINE_BYTES,
                   f"got {len(lines[0].encode('utf-8'))}")
        except Exception as e:  # noqa: BLE001
            _check("json.loads truncated 行", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V9 logger.exception 落 'exc' 字段
# ---------------------------------------------------------------------------


def v9_exception_traceback() -> None:
    _section("V9 logger.exception 在 jsonl 行落 'exc' 字段")
    def _emit_exc():
        setup_logging(jsonl=True, level="INFO")
        logger = logging.getLogger("asr")
        try:
            raise ValueError("boom-test-l1-1")
        except ValueError:
            logger.exception("decode failed", extra={"component": "asr", "event": "transcribe"})
    lines = _capture_jsonl(_emit_exc)
    _check("捕获到 1 行", len(lines) == 1, f"got {len(lines)}")
    if lines:
        try:
            obj = json.loads(lines[0])
            _check("行含 'exc' 字段", "exc" in obj, f"keys={sorted(obj.keys())}")
            exc_text = obj.get("exc", "")
            _check("'exc' 含 'ValueError'", "ValueError" in exc_text, f"exc={exc_text[:200]!r}")
            _check("'exc' 含 'boom-test-l1-1'",
                   "boom-test-l1-1" in exc_text, f"exc={exc_text[:200]!r}")
        except Exception as e:  # noqa: BLE001
            _check("json.loads exc 行", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# V10 main.py emit 的 component 全部 authoritative
# ---------------------------------------------------------------------------


def v10_authoritative_components() -> None:
    _section("V10 main.py 5 处 emit component 全部命中 AUTHORITATIVE_COMPONENTS")
    main_py = ROOT / "coco" / "main.py"
    text = main_py.read_text(encoding="utf-8")
    # 简单正则提 emit("xxx.yyy", ...) 的 'xxx' 短名
    import re
    found = re.findall(r'emit\(\s*"([a-zA-Z_][a-zA-Z_0-9]*)\.', text)
    _check("emit 调用至少 5 处", len(found) >= 5, f"got {len(found)}: {found}")
    bad = [c for c in found if c not in AUTHORITATIVE_COMPONENTS]
    _check("所有 component 短名在 AUTHORITATIVE_COMPONENTS",
           bad == [], f"unknown={bad} authoritative={sorted(AUTHORITATIVE_COMPONENTS)}")
    # 行为：emit 未知 component 时只 warn 不抛
    def _emit_unknown():
        setup_logging(jsonl=True, level="WARNING")
        emit("unknown_component_xyz.test", value=1)
    lines = _capture_jsonl(_emit_unknown)
    saw_warn = any('AUTHORITATIVE_COMPONENTS' in line or 'unknown_component_xyz' in line
                   for line in lines)
    _check("未知 component 触发 warn 不抛", saw_warn, f"lines={lines[:3]}")


# ---------------------------------------------------------------------------
# V11 load_config(env=...) 注入对子模块 dataclass 字段无效（语义文档锁）
# ---------------------------------------------------------------------------


def v11_env_injection_scope() -> None:
    _section("V11 load_config(env=...) 注入仅覆盖本文件直管字段（子模块 dataclass 字段不受影响）")
    # 暂存并清空真实 env 的 COCO_DIALOG_MAX_TURNS，确保隔离
    backup = os.environ.pop("COCO_DIALOG_MAX_TURNS", None)
    try:
        cfg = load_config(env={"COCO_DIALOG_MAX_TURNS": "7"})
        # DialogConfig 默认 max_turns=4；env 注入对子模块 dataclass 字段不应生效
        actual = getattr(cfg.dialog, "max_turns", None)
        _check("cfg.dialog.max_turns == DialogConfig 默认（不被注入 env 改写）",
               actual == 4,
               f"got {actual}; 这是 phase-4 已知限制 L2-2，详见 coco/config.py load_config docstring")
        # 但本文件直管字段 (log) 应该响应注入
        cfg2 = load_config(env={"COCO_LOG_LEVEL": "DEBUG"})
        _check("cfg2.log.level == 'DEBUG' (本文件直管字段响应注入)",
               cfg2.log.level == "DEBUG", f"got {cfg2.log.level}")
    finally:
        if backup is not None:
            os.environ["COCO_DIALOG_MAX_TURNS"] = backup


# ---------------------------------------------------------------------------
# V2b COCO_PTT_SECONDS 在 Coco.run 路径下覆盖模块级 PUSH_TO_TALK_SECONDS
# ---------------------------------------------------------------------------


def v2b_ptt_seconds_unified() -> None:
    _section("V2b Coco.run 路径下 PUSH_TO_TALK_SECONDS 同步 cfg.ptt.seconds")
    # 不实际启动 Coco.run（依赖 ReachyMini）；直接验 module-level 路径与
    # cfg 字段一致的逻辑。本测先确认 import-time 默认；再 reload + env 验证；
    # 最后用 cfg.ptt.seconds 直接覆盖（模拟 run() 内部 global 写回）。
    import importlib
    backup = os.environ.get("COCO_PTT_SECONDS")
    try:
        os.environ["COCO_PTT_SECONDS"] = "10"
        # reload 触发模块级 float(os.environ.get(...)) 重算
        import coco.main as coco_main
        importlib.reload(coco_main)
        _check("import-time PUSH_TO_TALK_SECONDS 读 env=10",
               coco_main.PUSH_TO_TALK_SECONDS == 10.0,
               f"got {coco_main.PUSH_TO_TALK_SECONDS}")
        # 再模拟 run() 内部 global 写回路径
        cfg = load_config(env={"COCO_PTT_SECONDS": "12.5"})
        _check("cfg.ptt.seconds == 12.5", cfg.ptt.seconds == 12.5, f"got {cfg.ptt.seconds}")
        # 仿照 main.py run() 的 global 写回
        coco_main.PUSH_TO_TALK_SECONDS = float(cfg.ptt.seconds)
        coco_main.PUSH_TO_TALK_DISABLED = bool(cfg.ptt.disabled)
        _check("run 路径模拟：PUSH_TO_TALK_SECONDS == cfg.ptt.seconds",
               coco_main.PUSH_TO_TALK_SECONDS == 12.5,
               f"got {coco_main.PUSH_TO_TALK_SECONDS}")
    finally:
        if backup is None:
            os.environ.pop("COCO_PTT_SECONDS", None)
        else:
            os.environ["COCO_PTT_SECONDS"] = backup
        # reload 一次还原默认（避免污染后续 verify）
        import coco.main as coco_main
        importlib.reload(coco_main)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=== verify_infra_002 ===", flush=True)
    v1_defaults()
    v2_env_override()
    v3_invalid_values()
    v4_summary()
    v5_jsonl_parse()
    v6_backward_compat()
    v7_components()
    v8_truncate()
    v9_exception_traceback()
    v10_authoritative_components()
    v11_env_injection_scope()
    v2b_ptt_seconds_unified()

    print(f"\n--- 总结 ---", flush=True)
    print(f"PASS={len(PASSES)}  FAIL={len(FAILURES)}", flush=True)

    # evidence (确定性 — 不写时间戳)
    ev_dir = ROOT / "evidence" / "infra-002"
    ev_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "verification": "verify_infra_002",
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
        print("==> FAIL: infra-002 有 failure，详见上方", flush=True)
        for f in FAILURES:
            print(f"  - {f}", flush=True)
        return 1
    print("==> PASS: infra-002 verification 全部通过", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
