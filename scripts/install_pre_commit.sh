#!/usr/bin/env bash
# infra-008: 安装 scripts/pre-commit-hook.sh 到 .git/hooks/pre-commit。
# 不自动启用：hook 内部还需 COCO_PRECOMMIT_HOOK=1 才会真正跑 verify。

set -eu

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [[ -z "$REPO_ROOT" ]]; then
    echo "[install_pre_commit] 当前目录不是 git repo；abort" >&2
    exit 1
fi

SRC="$REPO_ROOT/scripts/pre-commit-hook.sh"
DST="$REPO_ROOT/.git/hooks/pre-commit"

if [[ ! -f "$SRC" ]]; then
    echo "[install_pre_commit] 模板 $SRC 不存在" >&2
    exit 1
fi

if [[ -e "$DST" || -L "$DST" ]]; then
    BAK="$DST.bak.$(date +%Y%m%d%H%M%S)"
    echo "[install_pre_commit] 已存在 $DST，备份到 $BAK"
    mv "$DST" "$BAK"
fi

cp "$SRC" "$DST"
chmod +x "$DST"
echo "[install_pre_commit] 安装完成：$DST"
echo "  · 启用：export COCO_PRECOMMIT_HOOK=1"
echo "  · 临时跳过：COCO_PRECOMMIT_SKIP=1 git commit ... 或 git commit --no-verify"
