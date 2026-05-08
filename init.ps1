# Coco / 可可 — 启动与基础验证入口（Windows）
# macOS / Linux 用户请用 init.sh。
#
# 用法：
#   .\init.ps1              # 同步依赖 + 跑 audio smoke
#   .\init.ps1 --daemon     # 额外验 mockup-sim daemon

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

Write-Host "==> Repo: $PWD"

Write-Host "==> uv sync"
uv sync
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> Smoke"
uv run python scripts/smoke.py @args
exit $LASTEXITCODE
