#!/usr/bin/env bash
# 幂等下载 SenseVoice INT8 ASR 模型 + Silero VAD 到 ${COCO_ASR_CACHE:-$HOME/.cache/coco/asr}/
#
# 已下载且解压完整 → 跳过；只下载未完成 → 重下并解压。
# 失败时给出手动下载 URL 提示。
#
# 用法:
#   bash scripts/fetch_asr_models.sh
#   COCO_ASR_CACHE=/some/path bash scripts/fetch_asr_models.sh

set -euo pipefail

CACHE_DIR="${COCO_ASR_CACHE:-$HOME/.cache/coco/asr}"
SENSE_DIR="${CACHE_DIR}/sense-voice-2024-07-17"
VAD_DIR="${CACHE_DIR}/silero_vad"

SENSE_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"
SENSE_TARBALL="${CACHE_DIR}/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"
# 解压后顶层目录名（来自上游 tarball）
SENSE_EXTRACT_NAME="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"

VAD_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
VAD_FILE="${VAD_DIR}/silero_vad.onnx"

# 文件大小（字节）跨平台读取
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

mkdir -p "$CACHE_DIR" "$SENSE_DIR" "$VAD_DIR"

# ---------- SenseVoice ----------
SENSE_MODEL="${SENSE_DIR}/model.int8.onnx"
SENSE_TOKENS="${SENSE_DIR}/tokens.txt"

need_sense_download=1
need_sense_extract=1

if [[ -f "$SENSE_MODEL" && -f "$SENSE_TOKENS" ]]; then
  m_size=$(file_size "$SENSE_MODEL")
  t_size=$(file_size "$SENSE_TOKENS")
  # model.int8.onnx > 1MB，tokens.txt > 100KB → 视为已就绪
  if (( m_size > 1048576 && t_size > 102400 )); then
    echo "[skip] SenseVoice 已就绪：$SENSE_MODEL ($m_size B), $SENSE_TOKENS ($t_size B)"
    need_sense_download=0
    need_sense_extract=0
  fi
fi

if (( need_sense_download == 1 )); then
  if [[ -f "$SENSE_TARBALL" ]]; then
    tar_size=$(file_size "$SENSE_TARBALL")
    # 上游实测 163,002,883 B (~155MB)；> 100MB 视为完整（挡住截断/HTML 错误页）
    if (( tar_size > 104857600 )); then
      echo "[skip-download] tarball 已存在且 > 100MB：$SENSE_TARBALL ($tar_size B)"
      need_sense_download=0
    else
      echo "[redownload] tarball 大小不足（$tar_size B），重新下载"
      rm -f "$SENSE_TARBALL"
    fi
  fi
fi

if (( need_sense_download == 1 )); then
  echo "[download] SenseVoice INT8 (~155MB) → $SENSE_TARBALL"
  if ! curl -L --fail --progress-bar -o "$SENSE_TARBALL" "$SENSE_URL"; then
    echo "ERROR: 下载失败。手动下载地址：" >&2
    echo "  $SENSE_URL" >&2
    echo "  保存到：$SENSE_TARBALL" >&2
    exit 2
  fi
  tar_size=$(file_size "$SENSE_TARBALL")
  if (( tar_size <= 104857600 )); then
    echo "ERROR: 下载完成但大小 $tar_size B < 100MB，可能损坏" >&2
    exit 3
  fi
fi

if (( need_sense_extract == 1 )); then
  echo "[extract] $SENSE_TARBALL → $SENSE_DIR/"
  tmp_extract="${CACHE_DIR}/.extract_tmp_$$"
  rm -rf "$tmp_extract"
  mkdir -p "$tmp_extract"
  if ! tar -xjf "$SENSE_TARBALL" -C "$tmp_extract"; then
    echo "ERROR: 解压失败" >&2
    rm -rf "$tmp_extract"
    exit 4
  fi
  # 上游 tarball 顶层是 SENSE_EXTRACT_NAME/，把内部内容平铺到 SENSE_DIR
  if [[ -d "${tmp_extract}/${SENSE_EXTRACT_NAME}" ]]; then
    cp -R "${tmp_extract}/${SENSE_EXTRACT_NAME}/." "${SENSE_DIR}/"
  else
    cp -R "${tmp_extract}/." "${SENSE_DIR}/"
  fi
  rm -rf "$tmp_extract"
fi

# 解压后校验
m_size=$(file_size "$SENSE_MODEL")
t_size=$(file_size "$SENSE_TOKENS")
if [[ ! -f "$SENSE_MODEL" ]] || (( m_size <= 1048576 )); then
  echo "ERROR: 解压后 model.int8.onnx 缺失或过小 ($m_size B)" >&2
  echo "  期望路径：$SENSE_MODEL" >&2
  echo "  手动下载：$SENSE_URL" >&2
  exit 5
fi
if [[ ! -f "$SENSE_TOKENS" ]] || (( t_size <= 102400 )); then
  echo "ERROR: 解压后 tokens.txt 缺失或过小 ($t_size B)" >&2
  exit 6
fi
echo "[ok] SenseVoice：model.int8.onnx ($m_size B), tokens.txt ($t_size B)"

# ---------- Silero VAD ----------
need_vad_download=1
if [[ -f "$VAD_FILE" ]]; then
  v_size=$(file_size "$VAD_FILE")
  # silero_vad.onnx ~2MB，> 500KB 视为合理
  if (( v_size > 524288 )); then
    echo "[skip] Silero VAD 已就绪：$VAD_FILE ($v_size B)"
    need_vad_download=0
  else
    echo "[redownload] silero_vad.onnx 大小不足（$v_size B）"
    rm -f "$VAD_FILE"
  fi
fi

if (( need_vad_download == 1 )); then
  echo "[download] Silero VAD (~2MB) → $VAD_FILE"
  if ! curl -L --fail --progress-bar -o "$VAD_FILE" "$VAD_URL"; then
    echo "ERROR: silero_vad.onnx 下载失败。手动下载地址：" >&2
    echo "  $VAD_URL" >&2
    echo "  保存到：$VAD_FILE" >&2
    exit 7
  fi
  v_size=$(file_size "$VAD_FILE")
  if (( v_size <= 524288 )); then
    echo "ERROR: silero_vad.onnx 大小不足 ($v_size B)" >&2
    exit 8
  fi
  echo "[ok] Silero VAD：$VAD_FILE ($v_size B)"
fi

echo ""
echo "全部就绪。缓存目录：$CACHE_DIR"
