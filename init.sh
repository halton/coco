#!/usr/bin/env bash
# Coco / 可可 — 启动与基础验证入口（macOS / Linux）
# Windows 用户请用 init.ps1。
#
# 用法：
#   ./init.sh              # 同步依赖 + 跑 audio smoke
#   ./init.sh --daemon     # 额外验 mockup-sim daemon
#   COCO_CI=1 ./init.sh    # CI 模式：跳过真麦克录音等真硬件子检查（infra-006）

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "==> Repo: $PWD"
if [ "${COCO_CI:-}" = "1" ]; then
  echo "==> COCO_CI=1（CI 模式：跳过真麦克 / daemon 等真硬件子检查）"
fi

echo "==> uv sync"
uv sync

# TTS 模型按需拉取（幂等；已就绪即跳过）
echo "==> TTS 模型 (kokoro-zh) 检查"
bash scripts/fetch_tts_models.sh || {
  echo "WARN: TTS 模型下载失败；smoke 会以 WARN skip TTS 段，不阻断" >&2
}

echo "==> Smoke"
uv run python scripts/smoke.py "$@"
