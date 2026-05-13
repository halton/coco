#!/usr/bin/env bash
# infra-008 pre-commit hook template (default-OFF).
#
# 启用：COCO_PRECOMMIT_HOOK=1（环境变量，或写入 shell rc）
# 安装到 .git/hooks/pre-commit：bash scripts/install_pre_commit.sh
# 临时 bypass：COCO_PRECOMMIT_SKIP=1 git commit ... 或 git commit --no-verify
#
# 行为：
#   - COCO_PRECOMMIT_HOOK 未设或为 0：直接 exit 0（不打扰未启用的开发者）
#   - COCO_PRECOMMIT_SKIP=1：直接 exit 0（临时跳过）
#   - 否则：调 scripts/precommit_impact.py --staged --run --max 10
#     失败时 print 提示并 exit 1，阻断 commit

set -u

if [[ "${COCO_PRECOMMIT_SKIP:-0}" == "1" ]]; then
    echo "[coco-precommit] COCO_PRECOMMIT_SKIP=1 → 跳过"
    exit 0
fi

if [[ "${COCO_PRECOMMIT_HOOK:-0}" != "1" ]]; then
    # default OFF
    exit 0
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [[ -z "$REPO_ROOT" ]]; then
    echo "[coco-precommit] not a git repo; skip"
    exit 0
fi

PY="${COCO_PYTHON:-python3}"
SCRIPT="$REPO_ROOT/scripts/precommit_impact.py"

if [[ ! -f "$SCRIPT" ]]; then
    echo "[coco-precommit] $SCRIPT 不存在；跳过"
    exit 0
fi

echo "[coco-precommit] 影响面分析 + verify（COCO_PRECOMMIT_HOOK=1, max=10）"
if ! "$PY" "$SCRIPT" --staged --run --max 10; then
    echo ""
    echo "[coco-precommit] 影响面 verify 失败；commit aborted。"
    echo "  · 修复后重试，或用 COCO_PRECOMMIT_SKIP=1 git commit 临时跳过"
    echo "  · 或 git commit --no-verify"
    exit 1
fi

exit 0
