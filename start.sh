#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# start.sh — 一键启动后端全套服务
#   Docker 中间件（MySQL/RabbitMQ/Milvus/Neo4j）
#   模型服务（MedGo/BGE-M3/Reranker/PaddleOCR-VL）
#   后端 API + RabbitMQ Workers
# 用法：
#   bash start.sh              # 启动全部
#   bash start.sh --no-models  # 跳过模型服务（仅中间件+后端）
#   bash start.sh --no-ocr     # 跳过 PaddleOCR
#   bash start.sh --no-medgo   # 跳过 MedGo
# ============================================================

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
INFRA_DIR="$ROOT_DIR/infra"
VENV="$BACKEND_DIR/.venv/bin"
PADDLE_VENV="$BACKEND_DIR/paddle_venv/bin"
VLLM_VENV="$BACKEND_DIR/.venv-vllm-cu12/bin"

export HF_ENDPOINT=https://hf-mirror.com
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export PATH="$VENV:$PATH"

export LOG_LEVEL=${LOG_LEVEL:-INFO}
mkdir -p /data/logs

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }

# 解析参数
SKIP_MODELS=0; SKIP_OCR=0; SKIP_MEDGO=0; SKIP_EMBED=0; SKIP_RERANKER=0
for arg in "$@"; do
  case "$arg" in
    --no-models)   SKIP_MODELS=1; SKIP_OCR=1; SKIP_MEDGO=1; SKIP_EMBED=1; SKIP_RERANKER=1 ;;
    --no-ocr)      SKIP_OCR=1 ;;
    --no-medgo)    SKIP_MEDGO=1 ;;
    --no-embed)    SKIP_EMBED=1 ;;
    --no-reranker) SKIP_RERANKER=1 ;;
  esac
done

PIDS=()

cleanup() {
  log "Stopping all services..."
  for pidfile in /tmp/start-sh-*.pid; do
    [[ -f "$pidfile" ]] && kill "$(cat $pidfile)" 2>/dev/null || true
    rm -f "$pidfile"
  done
  pkill -f "vllm serve /data/models/MedGo" 2>/dev/null || true
  pkill -f "vllm serve /data/models/bge-m3" 2>/dev/null || true
  pkill -f "paddle_ocr_service.main:app" 2>/dev/null || true
  pkill -f "reranker_service.main:app" 2>/dev/null || true
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -f "app.modules.report.worker" 2>/dev/null || true
  pkill -f "app.modules.interpretation.worker" 2>/dev/null || true
  pkill -f "app.modules.report.extract_worker" 2>/dev/null || true
  log "Done. Docker 中间件保持运行（如需停止：cd $INFRA_DIR && docker compose down）"
  exit 0
}
trap cleanup SIGINT SIGTERM

# ── 1. Docker 中间件 ────────────────────────────────────────────
log "Starting Docker middleware (MySQL/RabbitMQ/Milvus/Neo4j)..."

if [[ ! -f "$INFRA_DIR/docker-compose.yml" ]]; then
  err "缺少 $INFRA_DIR/docker-compose.yml"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  err "Docker 未运行，请先启动 Docker"
  exit 1
fi

cd "$INFRA_DIR"
if docker compose ps --format '{{.Name}}' 2>/dev/null | grep -qE "hospital-mysql|milvus-standalone"; then
  log "Docker 中间件已在运行"
else
  docker compose up -d 2>&1 | sed 's/^/  /'
  log "等待中间件就绪..."
  for i in $(seq 1 60); do
    if curl -s http://localhost:9091/healthz 2>/dev/null | grep -q "OK"; then
      log "Milvus 就绪"
      break
    fi
    sleep 2
  done
fi
cd "$ROOT_DIR"

docker exec hospital-mysql mysqladmin -uroot -proot ping >/dev/null 2>&1 && log "MySQL OK" || warn "MySQL 未就绪"
docker exec hospital-rabbitmq rabbitmq-diagnostics ping >/dev/null 2>&1 && log "RabbitMQ OK" || warn "RabbitMQ 未就绪"
docker exec hospital-redis redis-cli ping >/dev/null 2>&1 && log "Redis OK" || warn "Redis 未就绪"
curl -s http://localhost:7474 >/dev/null 2>&1 && log "Neo4j OK" || warn "Neo4j 未就绪"

# ── 2. 数据库初始化（仅首次）────────────────────────────────────
# NOTE: 新 tenant 初始化必须照此 DDL 块完整复制;尤其别忘了 batch_import /
#       batch_import_file 两表(批量上传),以及 failed_stage 列。
TABLE_COUNT=$(docker exec hospital-mysql mysql -uroot -proot -N -e \
  "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='hospital_H001';" 2>/dev/null || echo 0)
if [[ "$TABLE_COUNT" == "0" ]]; then
  log "初始化数据库..."
  docker exec -i hospital-mysql mysql -uroot -proot < "$INFRA_DIR/mysql/init/01_template_db.sql" 2>/dev/null || true
  docker exec -i hospital-mysql mysql -uroot -proot hospital_H001 <<'SQL' 2>/dev/null || true
CREATE TABLE IF NOT EXISTS hospital_user (id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, name VARCHAR(50), phone VARCHAR(20), gender VARCHAR(5), age INT, unit_name VARCHAR(100), created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS knowledge_category (id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL, parent_id BIGINT DEFAULT NULL, sort_order INT NOT NULL DEFAULT 0, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS knowledge_entry (id BIGINT AUTO_INCREMENT PRIMARY KEY, category_id BIGINT DEFAULT NULL, title VARCHAR(200) NOT NULL, content TEXT NOT NULL, source_type VARCHAR(20) NOT NULL DEFAULT 'manual', source_file VARCHAR(500) DEFAULT NULL, chunk_index INT NOT NULL DEFAULT 0, parent_entry_id BIGINT DEFAULT NULL, vector_id VARCHAR(64) DEFAULT NULL, status TINYINT NOT NULL DEFAULT 1, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_task (id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(16) NOT NULL, original_file_path VARCHAR(500) NOT NULL, original_filename VARCHAR(200) NOT NULL, file_type VARCHAR(10) NOT NULL, file_size BIGINT NOT NULL DEFAULT 0, thumbnail_path VARCHAR(500) DEFAULT NULL, status VARCHAR(20) NOT NULL DEFAULT 'queued', priority TINYINT NOT NULL DEFAULT 0, retry_count INT NOT NULL DEFAULT 0, error_message TEXT DEFAULT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, completed_at DATETIME DEFAULT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_info (id BIGINT AUTO_INCREMENT PRIMARY KEY, task_id BIGINT DEFAULT NULL, user_id VARCHAR(16) NOT NULL, name VARCHAR(50), parsed_name VARCHAR(50), gender VARCHAR(5), age INT, report_date DATE, check_type VARCHAR(20), unit_name VARCHAR(100), created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_indicator (id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL, item_name VARCHAR(100) NOT NULL, item_name_standard VARCHAR(100) DEFAULT NULL, item_code VARCHAR(50) DEFAULT NULL, result_value VARCHAR(50) DEFAULT NULL, unit VARCHAR(20) DEFAULT NULL, ref_range_low VARCHAR(50) DEFAULT NULL, ref_range_high VARCHAR(50) DEFAULT NULL, category VARCHAR(50) DEFAULT NULL, raw_text TEXT DEFAULT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_interpretation (id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL, overall_level VARCHAR(10) DEFAULT NULL, red_count INT NOT NULL DEFAULT 0, yellow_count INT NOT NULL DEFAULT 0, green_count INT NOT NULL DEFAULT 0, summary_text TEXT DEFAULT NULL, status VARCHAR(20) NOT NULL DEFAULT 'pending', retry_count INT NOT NULL DEFAULT 0, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at DATETIME DEFAULT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS indicator_judgment (id BIGINT AUTO_INCREMENT PRIMARY KEY, interpretation_id BIGINT NOT NULL, indicator_id BIGINT NOT NULL, item_name VARCHAR(100) NOT NULL, result_value VARCHAR(50) DEFAULT NULL, deviation VARCHAR(10) DEFAULT NULL, color_level VARCHAR(10) DEFAULT NULL, matched_rule_id BIGINT DEFAULT NULL, explanation TEXT DEFAULT NULL, suggestion TEXT DEFAULT NULL, knowledge_refs JSON DEFAULT NULL, certainty VARCHAR(10) DEFAULT NULL, certainty_reason TEXT DEFAULT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS triage_rule (id BIGINT AUTO_INCREMENT PRIMARY KEY, rule_name VARCHAR(100) NOT NULL, rule_type VARCHAR(20) NOT NULL, indicator_code VARCHAR(50) DEFAULT NULL, conditions JSON NOT NULL, color_level VARCHAR(10) NOT NULL, priority INT NOT NULL DEFAULT 0, is_active TINYINT NOT NULL DEFAULT 1, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_template (id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL, type VARCHAR(10) NOT NULL, content LONGBLOB DEFAULT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS statistic_cache (id BIGINT AUTO_INCREMENT PRIMARY KEY, stat_type VARCHAR(50) NOT NULL, params_hash VARCHAR(64) NOT NULL, result_json JSON DEFAULT NULL, expired_at DATETIME DEFAULT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS dispatch_config (id BIGINT AUTO_INCREMENT PRIMARY KEY, config_key VARCHAR(50) NOT NULL, config_value VARCHAR(500) NOT NULL, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS resource_metric (id BIGINT AUTO_INCREMENT PRIMARY KEY, metric_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, cpu_percent DECIMAL(5,1) DEFAULT NULL, memory_percent DECIMAL(5,1) DEFAULT NULL, gpu_percent DECIMAL(5,1) DEFAULT NULL, gpu_memory_percent DECIMAL(5,1) DEFAULT NULL, queue_depth INT DEFAULT NULL, active_workers INT DEFAULT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS chat_session (id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(16) NOT NULL, name VARCHAR(50) DEFAULT NULL, hospital_id VARCHAR(32) NOT NULL, report_id BIGINT DEFAULT NULL, title VARCHAR(200) DEFAULT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS chat_message (id BIGINT AUTO_INCREMENT PRIMARY KEY, session_id BIGINT NOT NULL, role VARCHAR(10) NOT NULL, content TEXT NOT NULL, knowledge_refs JSON DEFAULT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (session_id) REFERENCES chat_session(id)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS batch_import (id VARCHAR(36) PRIMARY KEY, hospital_id VARCHAR(32) NOT NULL, user_id VARCHAR(64) NOT NULL, filename VARCHAR(255) NOT NULL, archive_path VARCHAR(512) NOT NULL, total BIGINT NOT NULL DEFAULT 0, parsed_ok BIGINT NOT NULL DEFAULT 0, interp_ok BIGINT NOT NULL DEFAULT 0, failed BIGINT NOT NULL DEFAULT 0, status VARCHAR(24) NOT NULL DEFAULT 'uploading', error_message TEXT, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at DATETIME, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, KEY idx_batch_status (status), KEY idx_batch_hospital (hospital_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS batch_import_file (id VARCHAR(36) PRIMARY KEY, batch_id VARCHAR(36) NOT NULL, file_path VARCHAR(512) NOT NULL, file_size BIGINT NOT NULL DEFAULT 0, crc32 VARCHAR(8) NOT NULL, status VARCHAR(24) NOT NULL DEFAULT 'queued', failed_stage VARCHAR(24) DEFAULT NULL, report_task_id BIGINT, error_message TEXT, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uq_batch_file (batch_id, crc32), KEY idx_bfile_status (status), CONSTRAINT fk_bfile_batch FOREIGN KEY (batch_id) REFERENCES batch_import(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
SQL
  docker exec -i hospital-mysql mysql -uroot -proot --default-character-set=utf8mb4 hospital_template <<'SQL' 2>/dev/null || true
INSERT INTO hospital_tenant (hospital_id, hospital_name, db_name, is_active)
VALUES ('H001', '演示医院', 'hospital_H001', 1)
ON DUPLICATE KEY UPDATE hospital_name=VALUES(hospital_name);
SQL
  log "数据库初始化完成"
else
  log "数据库已初始化"
  # 确保 certainty 列存在（兼容旧库）
  docker exec hospital-mysql mysql -uroot -proot hospital_H001 -e \
    "ALTER TABLE indicator_judgment ADD COLUMN IF NOT EXISTS certainty VARCHAR(10) DEFAULT NULL;" 2>/dev/null || true
  docker exec hospital-mysql mysql -uroot -proot hospital_H001 -e \
    "ALTER TABLE indicator_judgment ADD COLUMN IF NOT EXISTS certainty_reason TEXT DEFAULT NULL;" 2>/dev/null || true
  # 批量导入失败阶段列(增量迁移,兼容旧库 Spec I3)
  docker exec hospital-mysql mysql -uroot -proot hospital_H001 -e \
    "ALTER TABLE batch_import_file ADD COLUMN IF NOT EXISTS failed_stage VARCHAR(24) DEFAULT NULL;" 2>/dev/null || true
  # 批量上传按身份证后六位分发:user_id 列改字符串(兼容旧库)
  docker exec hospital-mysql mysql -uroot -proot hospital_H001 -e \
    "ALTER TABLE report_task MODIFY user_id VARCHAR(16) NOT NULL;" 2>/dev/null || true
  docker exec hospital-mysql mysql -uroot -proot hospital_H001 -e \
    "ALTER TABLE report_info MODIFY user_id VARCHAR(16) NOT NULL;" 2>/dev/null || true
  docker exec hospital-mysql mysql -uroot -proot hospital_H001 -e \
    "ALTER TABLE chat_session MODIFY user_id VARCHAR(16) NOT NULL;" 2>/dev/null || true
  docker exec hospital-mysql mysql -uroot -proot hospital_template -e \
    "ALTER TABLE platform_user ADD COLUMN IF NOT EXISTS id_card_suffix VARCHAR(8) NULL;" 2>/dev/null || true
  # 姓名锚定:platform_user / chat_session 加 name 列(兼容旧库,增量迁移)
  docker exec hospital-mysql mysql -uroot -proot hospital_template -e \
    "ALTER TABLE platform_user ADD COLUMN IF NOT EXISTS name VARCHAR(50) NULL COMMENT '登录姓名(与报告文件名姓名段一致)';" 2>/dev/null || true
  docker exec hospital-mysql mysql -uroot -proot hospital_H001 -e \
    "ALTER TABLE chat_session ADD COLUMN IF NOT EXISTS name VARCHAR(50) NULL;" 2>/dev/null || true
  # 展示名与归属分离:report_info.parsed_name(PDF 解析真实姓名,仅展示;兼容旧库)
  docker exec hospital-mysql mysql -uroot -proot hospital_H001 -e \
    "ALTER TABLE report_info ADD COLUMN IF NOT EXISTS parsed_name VARCHAR(50) NULL;" 2>/dev/null || true
fi

# ── 3. 确保 .env ────────────────────────────────────────────────
if [[ ! -f "$BACKEND_DIR/.env" ]]; then
  cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
  sed -i 's/RABBITMQ_USER=guest/RABBITMQ_USER=root/; s/RABBITMQ_PASSWORD=guest/RABBITMQ_PASSWORD=root/' "$BACKEND_DIR/.env"
  log "从 .env.example 创建 .env"
fi

# ── 4. 模型服务 ─────────────────────────────────────────────────
if [[ "$SKIP_MODELS" == "0" ]]; then
  log "启动模型服务..."

  # MedGo (8004, GPU 0-3, TP=4, 32K)
  # 4卡并行 Qwen3-32B(FP16,权重~61G) -> 单卡权重~15G; util 0.6 预留~27.6G/卡
  # 余量留给同卡共存的 BGE-M3(GPU2)/Reranker(GPU2)/PaddleOCR(GPU3)
  # enforce-eager 关闭 CUDA 图,降低显存碎片,利于共存
  if [[ "$SKIP_MEDGO" == "0" ]]; then
    if curl -s -m 3 http://localhost:8004/health >/dev/null 2>&1; then
      log "MedGo 已运行 (8004)"
    else
      log "启动 MedGo vLLM (8004, GPU 0-3, TP=4, ctx=32K, util=0.6)..."
      nohup bash -c "export HF_ENDPOINT=https://hf-mirror.com; export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True; export PATH=$VLLM_VENV:\$PATH; CUDA_VISIBLE_DEVICES=0,1,2,3 $VLLM_VENV/vllm serve /data/models/MedGo --port 8004 --trust-remote-code --tensor-parallel-size 4 --max-model-len 32768 --gpu-memory-utilization 0.6 --disable-custom-all-reduce --enforce-eager --enable-auto-tool-choice --tool-call-parser hermes --override-generation-config '{\"temperature\": 0.2}'" > /data/logs/vllm-medgo.stdout.log 2>&1 &
      echo $! > /tmp/start-sh-medgo.pid
      log "  MedGo 启动中 (PID: $!, log: /data/logs/vllm-medgo.stdout.log)"
    fi
  else
    log "跳过 MedGo (--no-medgo)"
  fi

  # BGE-M3 Embedding (8002, GPU 2)
  if [[ "$SKIP_EMBED" == "0" ]]; then
    if curl -s -m 3 http://localhost:8002/health >/dev/null 2>&1; then
      log "BGE-M3 已运行 (8002)"
    else
      log "启动 BGE-M3 embedding vLLM (8002, GPU 2)..."
      nohup bash -c "export HF_ENDPOINT=https://hf-mirror.com; export PATH=$VLLM_VENV:\$PATH; CUDA_VISIBLE_DEVICES=2 $VLLM_VENV/vllm serve /data/models/bge-m3 --port 8002 --trust-remote-code --served-model-name BAAI/bge-m3 --task embed --max-model-len 8192 --gpu-memory-utilization 0.12" > /data/logs/vllm-embed.stdout.log 2>&1 &
      echo $! > /tmp/start-sh-embed.pid
      log "  BGE-M3 启动中 (PID: $!, log: /data/logs/vllm-embed.stdout.log)"
    fi
  else
    log "跳过 BGE-M3 (--no-embed)"
  fi

  # Reranker (8003, GPU 2)
  if [[ "$SKIP_RERANKER" == "0" ]]; then
    if curl -s -m 3 http://localhost:8003/health >/dev/null 2>&1; then
      log "Reranker 已运行 (8003)"
    else
      log "启动 Reranker (8003, GPU 2)..."
      nohup bash -c "cd $BACKEND_DIR && export HF_ENDPOINT=https://hf-mirror.com; CUDA_VISIBLE_DEVICES=2 RERANKER_MODEL=/data/models/bge-reranker-v2-m3 $VENV/python -m uvicorn reranker_service.main:app --host 127.0.0.1 --port 8003" > /data/logs/reranker.stdout.log 2>&1 &
      echo $! > /tmp/start-sh-reranker.pid
      log "  Reranker 启动中 (PID: $!, log: /data/logs/reranker.stdout.log)"
    fi
  else
    log "跳过 Reranker (--no-reranker)"
  fi

  # PaddleOCR-VL (8001, GPU 3)
  if [[ "$SKIP_OCR" == "0" ]]; then
    if curl -s -m 3 http://localhost:8001/health >/dev/null 2>&1; then
      log "PaddleOCR-VL 已运行 (8001)"
    else
      log "启动 PaddleOCR-VL (8001, GPU 3)..."
      nohup bash -c "cd $BACKEND_DIR && export HF_ENDPOINT=https://hf-mirror.com; CUDA_VISIBLE_DEVICES=3 PADDLEOCR_VL_MODEL=/data/models/PaddleOCR-VL-1.5 PP_DOCLAYOUT_MODEL=/data/models/PP-DocLayoutV2 $PADDLE_VENV/python -m uvicorn paddle_ocr_service.main:app --host 0.0.0.0 --port 8001" > /data/logs/paddle-ocr.stdout.log 2>&1 &
      echo $! > /tmp/start-sh-ocr.pid
      log "  PaddleOCR-VL 启动中 (PID: $!, log: /data/logs/paddle-ocr.stdout.log)"
    fi
  else
    log "跳过 PaddleOCR-VL (--no-ocr)"
  fi
fi

# ── 5. 等待模型服务就绪 ─────────────────────────────────────────
if [[ "$SKIP_MODELS" == "0" ]]; then
  log "等待模型服务就绪（可能需要 2-3 分钟）..."
  for svc in "8004:MedGo" "8002:BGE-M3" "8003:Reranker"; do
    port=${svc%%:*}; name=${svc##*:}
    case "$name" in
      MedGo)   [[ "$SKIP_MEDGO" == "1" ]] && continue ;;
      BGE-M3)  [[ "$SKIP_EMBED" == "1" ]] && continue ;;
      Reranker) [[ "$SKIP_RERANKER" == "1" ]] && continue ;;
    esac
    for i in $(seq 1 180); do
      if curl -s -m 3 http://localhost:$port/health >/dev/null 2>&1; then
        log "  $name 就绪 (:$port)"
        break
      fi
      [[ $i -eq 180 ]] && warn "  $name 180秒未就绪 (检查 /data/logs/vllm-*.stdout.log)"
      sleep 2
    done
  done
  if [[ "$SKIP_OCR" == "0" ]]; then
    for i in $(seq 1 60); do
      if curl -s -m 3 http://localhost:8001/health >/dev/null 2>&1; then
        log "  PaddleOCR-VL 就绪 (:8001)"
        break
      fi
      [[ $i -eq 60 ]] && warn "  PaddleOCR-VL 60秒未就绪 (检查 /data/logs/paddle-ocr.stdout.log)"
      sleep 2
    done
  fi
fi

# ── 6. 后端 API (8000) ──────────────────────────────────────────
if curl -s -m 3 http://localhost:8000/api/v1/health >/dev/null 2>&1; then
  log "后端 API 已运行 (8000)"
else
  log "启动后端 API (8000)..."
  cd "$BACKEND_DIR"
  nohup $VENV/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > /data/logs/backend.stdout.log 2>&1 &
  echo $! > /tmp/start-sh-backend.pid
  BACKEND_PID=$!
  cd "$ROOT_DIR"
  log "  后端启动中 (PID: $BACKEND_PID, log: /data/logs/backend.stdout.log)"
  for i in $(seq 1 30); do
    if curl -s -m 3 http://localhost:8000/api/v1/health >/dev/null 2>&1; then
      log "  后端就绪: http://localhost:8000"
      break
    fi
    [[ $i -eq 30 ]] && warn "  后端30秒未就绪 (检查 /data/logs/backend.stdout.log)"
    sleep 1
  done
fi

# ── 7. RabbitMQ Workers ─────────────────────────────────────────
if pgrep -f "app.modules.report.worker" >/dev/null 2>&1; then
  log "报告解析 Worker 已运行"
else
  log "启动报告解析 Worker..."
  cd "$BACKEND_DIR"
  nohup $VENV/python -c "from app.modules.report.worker import start_worker; start_worker()" > /data/logs/worker-parsing.stdout.log 2>&1 &
  echo $! > /tmp/start-sh-worker-parsing.pid
  cd "$ROOT_DIR"
  log "  报告解析 Worker 已启动 (log: /data/logs/worker-parsing.stdout.log)"
fi

if pgrep -f "app.modules.interpretation.worker" >/dev/null 2>&1; then
  log "解读 Worker 已运行"
else
  log "启动解读 Worker..."
  cd "$BACKEND_DIR"
  nohup $VENV/python -c "from app.modules.interpretation.worker import start_worker; start_worker()" > /data/logs/worker-interpretation.stdout.log 2>&1 &
  echo $! > /tmp/start-sh-worker-interpretation.pid
  cd "$ROOT_DIR"
  log "  解读 Worker 已启动 (log: /data/logs/worker-interpretation.stdout.log)"
fi

if pgrep -f "app.modules.report.extract_worker" >/dev/null 2>&1; then
  log "批量解压 Worker 已运行"
else
  log "启动批量解压 Worker..."
  cd "$BACKEND_DIR"
  nohup $VENV/python -c "from app.modules.report.extract_worker import start_worker; start_worker()" > /data/logs/worker-extract.stdout.log 2>&1 &
  echo $! > /tmp/start-sh-worker-extract.pid
  cd "$ROOT_DIR"
  log "  批量解压 Worker 已启动 (log: /data/logs/worker-extract.stdout.log)"
fi

# ── 8. 创建测试用户（如不存在）──────────────────────────────────
log "确保测试用户存在..."
curl -s -X POST http://localhost:8000/api/v1/auth/register -H "Content-Type: application/json" \
  -d '{"username":"doctor1","password":"123456","role":"doctor","hospital_id":"H001"}' >/dev/null 2>&1 || true
curl -s -X POST http://localhost:8000/api/v1/auth/register -H "Content-Type: application/json" \
  -d '{"username":"user1","password":"123456","role":"user","hospital_id":"H001"}' >/dev/null 2>&1 || true
curl -s -X POST http://localhost:8000/api/v1/auth/register -H "Content-Type: application/json" \
  -d '{"username":"admin1","password":"123456","role":"admin","hospital_id":"H001"}' >/dev/null 2>&1 || true

# ── 9. 汇总 ─────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  后端全套服务已启动"
echo "=============================================="
echo "  后端 API:       http://localhost:8000"
echo "  MedGo (LLM):    http://localhost:8004  (log: /data/logs/vllm-medgo.stdout.log)"
echo "  BGE-M3 (Embed): http://localhost:8002  (log: /data/logs/vllm-embed.stdout.log)"
echo "  Reranker:       http://localhost:8003  (log: /data/logs/reranker.stdout.log)"
echo "  PaddleOCR-VL:   http://localhost:8001  (log: /data/logs/paddle-ocr.stdout.log)"
echo "  Neo4j:          http://localhost:7474  (neo4j/medgraph123)"
echo "  MySQL:          localhost:3306  (root/root)"
echo "  RabbitMQ:       localhost:5672  (root/root)"
echo "  Redis:          localhost:6379"
echo "  Milvus:         localhost:19530"
echo "  Workers:        parsing + interpretation + extract"
echo ""
echo "  测试用户: admin1/123456 (管理员), doctor1/123456 (医生), user1/123456 (用户)"
echo ""
echo "  启动前端:  bash start_front.sh"
echo "  停止全部:  Ctrl+C  (Docker 中间件需手动 docker compose down)"
echo "=============================================="
echo ""

wait