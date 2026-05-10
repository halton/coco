#!/usr/bin/env bash
# 幂等下载 Kokoro-multi-lang-v1.1 (int8) 中文/英文 TTS 模型到 ${COCO_TTS_CACHE:-$HOME/.cache/coco/tts}/
#
# 已下载且解压完整 → 跳过；只下载未完成 → 重下并解压。
# 失败时给出手动下载 URL 提示。
#
# 用法:
#   bash scripts/fetch_tts_models.sh
#   COCO_TTS_CACHE=/some/path bash scripts/fetch_tts_models.sh

set -euo pipefail

CACHE_DIR="${COCO_TTS_CACHE:-$HOME/.cache/coco/tts}"
KOKORO_DIR="${CACHE_DIR}/kokoro-int8-multi-lang-v1_1"

# Researcher (2026-05-10) 实测 URL：
#   https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/
KOKORO_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-int8-multi-lang-v1_1.tar.bz2"
KOKORO_TARBALL="${CACHE_DIR}/kokoro-int8-multi-lang-v1_1.tar.bz2"

file_size() {
  local f="$1"
  if [[ ! -f "$f" ]]; then
    echo 0
    return
  fi
  if stat -f%z "$f" >/dev/null 2>&1; then
    stat -f%z "$f"
  else
    stat -c%s "$f"
  fi
}

mkdir -p "$CACHE_DIR"

KOKORO_MODEL="${KOKORO_DIR}/model.int8.onnx"
KOKORO_TOKENS="${KOKORO_DIR}/tokens.txt"
KOKORO_VOICES="${KOKORO_DIR}/voices.bin"
KOKORO_DICT="${KOKORO_DIR}/dict"
KOKORO_DATA="${KOKORO_DIR}/espeak-ng-data"

need_download=1
need_extract=1

if [[ -f "$KOKORO_MODEL" && -f "$KOKORO_TOKENS" && -f "$KOKORO_VOICES" && -d "$KOKORO_DICT" && -d "$KOKORO_DATA" ]]; then
  m_size=$(file_size "$KOKORO_MODEL")
  v_size=$(file_size "$KOKORO_VOICES")
  # model.int8.onnx ~85MB；voices.bin ~50MB；> 50MB / > 30MB 视为完整
  if (( m_size > 52428800 && v_size > 31457280 )); then
    echo "[skip] Kokoro 已就绪：$KOKORO_DIR (model=${m_size}B voices=${v_size}B)"
    need_download=0
    need_extract=0
  fi
fi

if (( need_download == 1 )); then
  if [[ -f "$KOKORO_TARBALL" ]]; then
    tar_size=$(file_size "$KOKORO_TARBALL")
    # 上游实测 147,031,220 B (~140MB)；> 100MB 视为完整
    if (( tar_size > 104857600 )); then
      echo "[skip-download] tarball 已存在且 > 100MB：$KOKORO_TARBALL ($tar_size B)"
      need_download=0
    else
      echo "[redownload] tarball 大小不足（$tar_size B），重新下载"
      rm -f "$KOKORO_TARBALL"
    fi
  fi
fi

if (( need_download == 1 )); then
  echo "[download] Kokoro int8 multi-lang v1.1 (~140MB) → $KOKORO_TARBALL"
  if ! curl -L --fail --progress-bar -o "$KOKORO_TARBALL" "$KOKORO_URL"; then
    echo "ERROR: 下载失败。手动下载地址：" >&2
    echo "  $KOKORO_URL" >&2
    echo "  保存到：$KOKORO_TARBALL" >&2
    exit 2
  fi
  tar_size=$(file_size "$KOKORO_TARBALL")
  if (( tar_size <= 104857600 )); then
    echo "ERROR: 下载完成但大小 $tar_size B < 100MB，可能损坏" >&2
    exit 3
  fi
fi

if (( need_extract == 1 )); then
  echo "[extract] $KOKORO_TARBALL → $CACHE_DIR/"
  if ! tar -xjf "$KOKORO_TARBALL" -C "$CACHE_DIR"; then
    echo "ERROR: 解压失败" >&2
    exit 4
  fi
fi

# 解压后校验
m_size=$(file_size "$KOKORO_MODEL")
v_size=$(file_size "$KOKORO_VOICES")
if [[ ! -f "$KOKORO_MODEL" ]] || (( m_size <= 52428800 )); then
  echo "ERROR: 解压后 model.int8.onnx 缺失或过小 ($m_size B)" >&2
  echo "  期望路径：$KOKORO_MODEL" >&2
  echo "  手动下载：$KOKORO_URL" >&2
  exit 5
fi
if [[ ! -f "$KOKORO_VOICES" ]] || (( v_size <= 31457280 )); then
  echo "ERROR: 解压后 voices.bin 缺失或过小 ($v_size B)" >&2
  exit 6
fi
if [[ ! -d "$KOKORO_DICT" ]] || [[ ! -d "$KOKORO_DATA" ]]; then
  echo "ERROR: 解压后 dict/ 或 espeak-ng-data/ 缺失" >&2
  exit 7
fi
echo "[ok] Kokoro：model.int8.onnx (${m_size}B), voices.bin (${v_size}B), dict/, espeak-ng-data/, tokens.txt"

echo ""
echo "全部就绪。缓存目录：$CACHE_DIR"
