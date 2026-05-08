#!/usr/bin/env bash
# Coco / 可可 — 启动与基础验证入口（macOS / Linux）
# Windows 用户请用 init.ps1。
#
# 用法：
#   ./init.sh              # 同步依赖 + 跑 audio smoke
#   ./init.sh --daemon     # 额外验 mockup-sim daemon

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "==> Repo: $PWD"

echo "==> uv sync"
uv sync

echo "==> Smoke"
uv run python scripts/smoke.py "$@"
