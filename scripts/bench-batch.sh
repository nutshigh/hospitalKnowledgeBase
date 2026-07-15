#!/bin/bash
# scripts/bench-batch.sh
# 批量上传压测脚本 (Spec §7.6)
# 生成 1000 个小 PDF -> zip -> curl 流式分片上传 -> 轮询进度
# 每 5s 采样 nvidia-smi 显存,最终打印 "<max_gpu_mem_MB> <wall_clock_seconds> <fail_count>"
#
# 用法:
#   JWT=<admin_jwt> bash scripts/bench-batch.sh
#   PDF_COUNT=1000 CHUNK_SIZE=5242880 bash scripts/bench-batch.sh
#
# 环境变量:
#   JWT              管理 JWT (必填,否则用占位符会失败)
#   API_BASE          后端 API 地址 (默认 http://localhost:8000)
#   PDF_COUNT         生成 PDF 数量 (默认 1000)
#   CHUNK_SIZE        分片大小,字节 (默认 5242880 = 5MB)
#   POLL_INTERVAL     轮询间隔,秒 (默认 10)
#   GPU_SAMPLE_INTERVAL nvidia-smi 采样间隔,秒 (默认 5)
set -eu

JWT="${JWT:-REPLACE_WITH_ADMIN_JWT}"
API_BASE="${API_BASE:-http://localhost:8000}"
PDF_COUNT="${PDF_COUNT:-1000}"
CHUNK_SIZE="${CHUNK_SIZE:-5242880}"
POLL_INTERVAL="${POLL_INTERVAL:-10}"
GPU_SAMPLE_INTERVAL="${GPU_SAMPLE_INTERVAL:-5}"

WORK_DIR="$(mktemp -d /tmp/bench-batch.XXXXXX)"
ZIP_PATH="$WORK_DIR/bench.zip"
GPU_LOG="/tmp/bench-gpu.txt"

trap 'cleanup' EXIT
cleanup() {
  [[ -f "$GPU_LOG" ]] || rm -f "$GPU_LOG"
  rm -rf "$WORK_DIR"
}

log() { echo "[BENCH] $*"; }

# ── 1. 生成 PDF ──────────────────────────────────────────────
log "生成 $PDF_COUNT 个小 PDF 到 $WORK_DIR ..."
python3 - "$WORK_DIR" "$PDF_COUNT" <<'PY'
import os, sys
work_dir, count = sys.argv[1], int(sys.argv[2])
os.makedirs(work_dir, exist_ok=True)
# 最小可用 PDF (单页空白)
pdf_tmpl = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n168\n%%EOF\n"
)
for i in range(count):
    name = f"report_{i:04d}.pdf"
    # 注入序号使 CRC32 不同
    body = pdf_tmpl.replace(b"report_XXXX", name.encode())
    with open(os.path.join(work_dir, name), "wb") as f:
        f.write(body)
print(f"generated {count} pdfs")
PY

# ── 2. 打包 zip ──────────────────────────────────────────────
log "打包 zip -> $ZIP_PATH"
( cd "$WORK_DIR" && zip -q bench.zip report_*.pdf )

ZIP_SIZE=$(stat -c%s "$ZIP_PATH")
log "zip 大小: $ZIP_SIZE bytes"

# ── 3. 创建 batch ────────────────────────────────────────────
log "创建 batch..."
CREATE_RESP=$(curl -s -X POST "$API_BASE/api/v1/reports/batches" \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d "{\"filename\":\"bench.zip\",\"total\":$PDF_COUNT}")
log "create resp: $CREATE_RESP"

BATCH_ID=$(echo "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('batch_id',''))" 2>/dev/null || echo "")
if [[ -z "$BATCH_ID" ]]; then
  log "ERROR: 未获取到 batch_id,退出"
  exit 1
fi
log "batch_id = $BATCH_ID"

# ── 4. 分片上传 ──────────────────────────────────────────────
log "分片上传 (chunk_size=$CHUNK_SIZE)..."
split -b "$CHUNK_SIZE" "$ZIP_PATH" "$WORK_DIR/chunk_"
CHUNKS=( "$WORK_DIR"/chunk_* )
TOTAL_CHUNKS=${#CHUNKS[@]}
log "总分片数: $TOTAL_CHUNKS"

for idx in "${!CHUNKS[@]}"; do
  seq_no=$((idx + 1))
  log "  上传分片 $seq_no / $TOTAL_CHUNKS (${CHUNKS[$idx]})"
  curl -s -X POST "$API_BASE/api/v1/reports/batches/$BATCH_ID/chunks" \
    -H "Authorization: Bearer $JWT" \
    -H "X-Chunk-Seq: $seq_no" \
    -H "X-Total-Chunks: $TOTAL_CHUNKS" \
    --form "file=@${CHUNKS[$idx]}" >/dev/null
done

# ── 5. 标记上传完成 ─────────────────────────────────────────
log "标记上传完成..."
curl -s -X POST "$API_BASE/api/v1/reports/batches/$BATCH_ID/complete" \
  -H "Authorization: Bearer $JWT" >/dev/null

# ── 6. 后台采样 nvidia-smi ──────────────────────────────────
log "启动 GPU 采样 (每 ${GPU_SAMPLE_INTERVAL}s) -> $GPU_LOG"
: > "$GPU_LOG"
gpu_sampler() {
  while true; do
    nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null >> "$GPU_LOG" || true
    sleep "$GPU_SAMPLE_INTERVAL"
  done
}
gpu_sampler &
SAMPLER_PID=$!

# ── 7. 轮询进度 ─────────────────────────────────────────────
START_TS=$(date +%s)
log "轮询进度 (每 ${POLL_INTERVAL}s)..."
STATUS=""
while true; do
  POLL_RESP=$(curl -s "$API_BASE/api/v1/reports/batches/$BATCH_ID" \
    -H "Authorization: Bearer $JWT")
  STATUS=$(echo "$POLL_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  PARSED=$(echo "$POLL_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('parsed_ok',0))" 2>/dev/null || echo "0")
  FAILED=$(echo "$POLL_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('failed',0))" 2>/dev/null || echo "0")
  log "  status=$STATUS parsed=$PARSED failed=$FAILED"
  case "$STATUS" in
    completed|partial_failed|cancelled) break ;;
  esac
  sleep "$POLL_INTERVAL"
done
END_TS=$(date +%s)

# 停止采样
kill "$SAMPLER_PID" 2>/dev/null || true
wait "$SAMPLER_PID" 2>/dev/null || true

# ── 8. 汇总输出 ─────────────────────────────────────────────
WALL=$((END_TS - START_TS))
MAX_GPU_MB=0
if [[ -s "$GPU_LOG" ]]; then
  MAX_GPU_MB=$(sort -n "$GPU_LOG" | tail -1)
fi
FAIL_COUNT=$(curl -s "$API_BASE/api/v1/reports/batches/$BATCH_ID" \
  -H "Authorization: Bearer $JWT" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('failed',0))" 2>/dev/null || echo "0")

log "完成: status=$STATUS wall=${WALL}s max_gpu=${MAX_GPU_MB}MB failed=$FAIL_COUNT"
echo "$MAX_GPU_MB $WALL $FAIL_COUNT"
