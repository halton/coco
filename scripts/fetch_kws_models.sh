#!/usr/bin/env bash
# 幂等下载 sherpa-onnx KWS 模型（zipformer wenetspeech 3.3M, 拼音建模）到
# ${COCO_KWS_CACHE:-$HOME/.cache/coco/kws}/
#
# 用法：
#   bash scripts/fetch_kws_models.sh
#   COCO_KWS_CACHE=/some/path bash scripts/fetch_kws_models.sh

set -euo pipefail

CACHE_DIR="${COCO_KWS_CACHE:-$HOME/.cache/coco/kws}"
KWS_NAME="sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
KWS_DIR="${CACHE_DIR}/${KWS_NAME}"
KWS_TARBALL="${CACHE_DIR}/${KWS_NAME}.tar.bz2"
KWS_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/${KWS_NAME}.tar.bz2"

file_size() {
  local f="$1"
  if [[ ! -f "$f" ]]; then echo 0; return; fi
  if stat -f%z "$f" >/dev/null 2>&1; then stat -f%z "$f"; else stat -c%s "$f"; fi
}

mkdir -p "$CACHE_DIR" "$KWS_DIR"

ENC="${KWS_DIR}/encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
DEC="${KWS_DIR}/decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
JOI="${KWS_DIR}/joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
TOK="${KWS_DIR}/tokens.txt"

need_download=1
need_extract=1

if [[ -f "$ENC" && -f "$DEC" && -f "$JOI" && -f "$TOK" ]]; then
  e_size=$(file_size "$ENC")
  if (( e_size > 1048576 )); then
    echo "[skip] KWS 已就绪：$KWS_DIR (encoder=$e_size B)"
    need_download=0
    need_extract=0
  fi
fi

if (( need_download == 1 )); then
  if [[ -f "$KWS_TARBALL" ]]; then
    t_size=$(file_size "$KWS_TARBALL")
    # 上游 ~31MB；> 25MB 视为完整
    if (( t_size > 26214400 )); then
      echo "[skip-download] tarball 已存在且 > 25MB：$KWS_TARBALL ($t_size B)"
      need_download=0
    else
      echo "[redownload] tarball 大小不足（$t_size B），重新下载"
      rm -f "$KWS_TARBALL"
    fi
  fi
fi

if (( need_download == 1 )); then
  echo "[download] KWS zipformer-wenetspeech (~31MB) → $KWS_TARBALL"
  if ! curl -L --fail --progress-bar -o "$KWS_TARBALL" "$KWS_URL"; then
    echo "ERROR: 下载失败。手动下载地址：" >&2
    echo "  $KWS_URL" >&2
    echo "  保存到：$KWS_TARBALL" >&2
    exit 2
  fi
  t_size=$(file_size "$KWS_TARBALL")
  if (( t_size <= 26214400 )); then
    echo "ERROR: 下载完成但大小 $t_size B < 25MB，可能损坏" >&2
    exit 3
  fi
fi

if (( need_extract == 1 )); then
  echo "[extract] $KWS_TARBALL → $CACHE_DIR/"
  tmp_extract="${CACHE_DIR}/.extract_tmp_$$"
  rm -rf "$tmp_extract"
  mkdir -p "$tmp_extract"
  if ! tar -xjf "$KWS_TARBALL" -C "$tmp_extract"; then
    echo "ERROR: 解压失败" >&2
    rm -rf "$tmp_extract"
    exit 4
  fi
  if [[ -d "${tmp_extract}/${KWS_NAME}" ]]; then
    cp -R "${tmp_extract}/${KWS_NAME}/." "${KWS_DIR}/"
  else
    cp -R "${tmp_extract}/." "${KWS_DIR}/"
  fi
  rm -rf "$tmp_extract"
fi

# 解压后校验
e_size=$(file_size "$ENC")
if [[ ! -f "$ENC" ]] || (( e_size <= 1048576 )); then
  echo "ERROR: 解压后 encoder 缺失或过小 ($e_size B)" >&2
  echo "  期望路径：$ENC" >&2
  echo "  手动下载：$KWS_URL" >&2
  exit 5
fi
for p in "$DEC" "$JOI" "$TOK"; do
  if [[ ! -f "$p" ]]; then
    echo "ERROR: 解压后缺失：$p" >&2
    exit 6
  fi
done
echo "[ok] KWS：encoder=$e_size B, decoder/joiner/tokens 齐"

echo ""
echo "全部就绪。缓存目录：$CACHE_DIR"
