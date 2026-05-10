# Coco / 可可 — 启动与基础验证入口（Windows）
# macOS / Linux 用户请用 init.sh。
#
# 用法：
#   .\init.ps1              # 同步依赖 + 跑 audio smoke
#   .\init.ps1 --daemon     # 额外验 mockup-sim daemon
#
# 注：本脚本镜像 init.sh 的核心步骤，仅做 dev-mode 自检。
# Windows UAT（真机：Reachy Mini Lite + USB 音频 / 摄像头）待真机环境上手后专项验证；
# 目前只保证 dev mode（uv sync + smoke）跑得通。

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

Write-Host "==> Repo: $PWD"

Write-Host "==> uv sync"
uv sync
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# TTS 模型按需拉取（幂等；已就绪即跳过）
# Windows 无 bash，跳过 fetch_tts_models.sh；smoke 会以 WARN skip TTS 段，不阻断。
# 真要拉模型请在 WSL / Git Bash 里跑 `bash scripts/fetch_tts_models.sh`。
Write-Host "==> TTS 模型 fetch 在 Windows 跳过（如需，请在 WSL/Git Bash 内跑 scripts/fetch_tts_models.sh）"

Write-Host "==> Smoke"
uv run python scripts/smoke.py @args
exit $LASTEXITCODE
