#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

# Model cache & HuggingFace mirror
export HF_ENDPOINT=https://hf-mirror.com
export HF_HOME=/root/autodl-tmp/model/huggingface

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }

cleanup() {
    log "Stopping services..."
    kill $BACKEND_PID 2>/dev/null || true
    kill $OCR_PID 2>/dev/null || true
    kill $EMBED_PID 2>/dev/null || true
    kill $PARSING_WORKER_PID 2>/dev/null || true
    kill $INTERP_WORKER_PID 2>/dev/null || true
    kill $RERANKER_PID 2>/dev/null || true
    kill $MILVUS_PID 2>/dev/null || true
    kill $ETCD_PID 2>/dev/null || true
    kill $MINIO_PID 2>/dev/null || true
    kill $FPID1 $FPID2 $FPID3 2>/dev/null || true
    log "Done."
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── 1. Check prerequisites ─────────────────────────────────────────
log "Checking prerequisites..."
for cmd in uv node npm python3; do
    if ! command -v $cmd &>/dev/null; then
        err "$cmd not found"
        exit 1
    fi
done
log "Prerequisites OK"

# ── 2. Start MySQL ─────────────────────────────────────────────────
log "Starting MySQL..."
if ! pgrep -x mysqld > /dev/null; then
    service mysql start 2>&1 | sed 's/^/  /'
    sleep 2
fi
if mysql -uroot -proot -e "SELECT 1;" 2>/dev/null | grep -q 1; then
    log "MySQL is running"
else
    err "MySQL failed to start"
    exit 1
fi

# ── 2.5. Start Milvus (standalone, non-Docker) ─────────────────────
log "Starting Milvus (standalone)..."
MILVUS_DIR="$ROOT_DIR/milvus"
MILVUS_BIN="$MILVUS_DIR/milvus"
MILVUS_VERSION="v2.6.3"
MILVUS_DATA="$MILVUS_DIR/data"
ETCD_DATA="$MILVUS_DIR/etcd_data"
MINIO_DATA="$MILVUS_DIR/minio_data"

mkdir -p "$MILVUS_DATA" "$ETCD_DATA" "$MINIO_DATA"

# Check if Milvus is already running on port 19530
if curl -s http://localhost:9091/healthz 2>/dev/null | grep -q "OK"; then
    log "Milvus already running (port 19530)"
else
    # Download milvus binary if not present
    if [[ ! -x "$MILVUS_BIN" ]]; then
        log "Downloading Milvus $MILVUS_VERSION binary..."
        ARCH=$(uname -m)
        case "$ARCH" in
            x86_64)  MILVUS_ARCH="amd64" ;;
            aarch64) MILVUS_ARCH="arm64" ;;
            *) err "Unsupported architecture: $ARCH"; exit 1 ;;
        esac
        DOWNLOAD_URL="https://github.com/milvus-io/milvus/releases/download/${MILVUS_VERSION}/milvus-${MILVUS_ARCH}"
        if ! curl -L -o "$MILVUS_BIN" "$DOWNLOAD_URL" 2>&1 | sed 's/^/  /'; then
            err "Failed to download Milvus binary"
            warn "If GitHub is unreachable, try mirror or download manually:"
            warn "  https://github.com/milvus-io/milvus/releases"
            warn "  Place binary at: $MILVUS_BIN"
            warn "  chmod +x $MILVUS_BIN"
            exit 1
        fi
        chmod +x "$MILVUS_BIN"
        log "Milvus binary downloaded"
    fi

    # Download etcd if not present
    ETCD_BIN="$MILVUS_DIR/etcd"
    if [[ ! -x "$ETCD_BIN" ]]; then
        log "Downloading etcd..."
        ETCD_VERSION="v3.5.5"
        ETCD_URL="https://github.com/etcd-io/etcd/releases/download/${ETCD_VERSION}/etcd-${ETCD_VERSION}-linux-${MILVUS_ARCH}.tar.gz"
        if ! curl -L -o /tmp/etcd.tar.gz "$ETCD_URL" 2>&1 | sed 's/^/  /'; then
            err "Failed to download etcd"
            exit 1
        fi
        tar -xzf /tmp/etcd.tar.gz -C /tmp
        cp "/tmp/etcd-${ETCD_VERSION}-linux-${MILVUS_ARCH}/etcd" "$ETCD_BIN"
        cp "/tmp/etcd-${ETCD_VERSION}-linux-${MILVUS_ARCH}/etcdctl" "$MILVUS_DIR/etcdctl"
        chmod +x "$ETCD_BIN" "$MILVUS_DIR/etcdctl"
        rm -rf /tmp/etcd.tar.gz "/tmp/etcd-${ETCD_VERSION}-linux-${MILVUS_ARCH}"
        log "etcd downloaded"
    fi

    # Download minio if not present
    MINIO_BIN="$MILVUS_DIR/minio"
    if [[ ! -x "$MINIO_BIN" ]]; then
        log "Downloading minio..."
        MINIO_URL="https://dl.min.io/server/minio/release/linux-${MILVUS_ARCH}/minio"
        if ! curl -L -o "$MINIO_BIN" "$MINIO_URL" 2>&1 | sed 's/^/  /'; then
            err "Failed to download minio"
            exit 1
        fi
        chmod +x "$MINIO_BIN"
        log "minio downloaded"
    fi

    # Start etcd
    log "Starting etcd..."
    nohup "$ETCD_BIN" \
        --advertise-client-urls=http://127.0.0.1:2379 \
        --listen-client-urls=http://0.0.0.0:2379 \
        --data-dir "$ETCD_DATA" \
        > /tmp/etcd.log 2>&1 &
    ETCD_PID=$!
    log "etcd starting (PID: $ETCD_PID, log: /tmp/etcd.log)"

    # Start minio
    log "Starting minio..."
    nohup "$MINIO_BIN" server "$MINIO_DATA" \
        --address ":9000" \
        > /tmp/minio.log 2>&1 &
    MINIO_PID=$!
    log "minio starting (PID: $MINIO_PID, log: /tmp/minio.log)"

    # Wait for etcd and minio to be ready
    sleep 3

    # Start milvus standalone
    log "Starting Milvus standalone..."
    export ETCD_ENDPOINTS="127.0.0.1:2379"
    export MINIO_ADDRESS="127.0.0.1:9000"
    nohup "$MILVUS_BIN" run standalone \
        > /tmp/milvus.log 2>&1 &
    MILVUS_PID=$!
    log "Milvus starting (PID: $MILVUS_PID, log: /tmp/milvus.log)"

    # Wait for Milvus to be ready (up to 60s)
    log "Waiting for Milvus to be ready..."
    MILVUS_READY=false
    for i in $(seq 1 60); do
        if curl -s http://localhost:9091/healthz 2>/dev/null | grep -q "OK"; then
            log "Milvus ready: http://localhost:19530"
            MILVUS_READY=true
            break
        fi
        sleep 1
    done
    if [[ "$MILVUS_READY" != "true" ]]; then
        err "Milvus failed to start within 60s"
        warn "Check /tmp/milvus.log for details"
        warn "If binary download failed, manually download from:"
        warn "  https://github.com/milvus-io/milvus/releases"
        exit 1
    fi
fi

# ── 3. Start RabbitMQ ──────────────────────────────────────────────
log "Starting RabbitMQ..."
if ! rabbitmqctl status 2>/dev/null | grep -q "OS PID"; then
    RABBITMQ_NODE_IP_ADDRESS=127.0.0.1 ERL_EPMD_ADDRESS=127.0.0.1 \
        rabbitmq-server -detached 2>&1 | sed 's/^/  /'
    sleep 3
    rabbitmqctl start_app 2>&1 | sed 's/^/  /' || true
fi
# Ensure root/root user matches .env
rabbitmqctl add_user root root 2>/dev/null || true
rabbitmqctl set_user_tags root administrator 2>/dev/null || true
rabbitmqctl set_permissions -p / root ".*" ".*" ".*" 2>/dev/null || true
log "RabbitMQ is running"

# ── 4. Ensure databases exist ──────────────────────────────────────
log "Checking databases..."
mysql -uroot -proot -e "
  CREATE DATABASE IF NOT EXISTS hospital_template
    DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci;
  CREATE DATABASE IF NOT EXISTS hospital_H001
    DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci;" 2>/dev/null

# Check if tables exist, init if needed
TABLE_COUNT=$(mysql -uroot -proot -N -e \
    "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='hospital_template';" 2>/dev/null)
if [[ "$TABLE_COUNT" == "0" ]]; then
    log "Initializing hospital_template..."
    mysql -uroot -proot hospital_template <<'EOSQL'
CREATE TABLE IF NOT EXISTS hospital_tenant (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, hospital_id VARCHAR(32) NOT NULL UNIQUE,
    hospital_name VARCHAR(100) NOT NULL, db_name VARCHAR(64) NOT NULL,
    is_active TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS platform_user (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL, role VARCHAR(20) NOT NULL,
    hospital_id VARCHAR(32) DEFAULT NULL, is_active TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
EOSQL
fi

TABLE_COUNT=$(mysql -uroot -proot -N -e \
    "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='hospital_H001';" 2>/dev/null)
if [[ "$TABLE_COUNT" == "0" ]]; then
    log "Initializing hospital_H001..."
    mysql -uroot -proot hospital_H001 <<'EOSQL'
CREATE TABLE IF NOT EXISTS hospital_user (id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, name VARCHAR(50), phone VARCHAR(20), gender VARCHAR(5), age INT, unit_name VARCHAR(100), created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS knowledge_category (id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL, parent_id BIGINT DEFAULT NULL, sort_order INT NOT NULL DEFAULT 0, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS knowledge_entry (id BIGINT AUTO_INCREMENT PRIMARY KEY, category_id BIGINT DEFAULT NULL, title VARCHAR(200) NOT NULL, content TEXT NOT NULL, source_type VARCHAR(20) NOT NULL DEFAULT 'manual', source_file VARCHAR(500) DEFAULT NULL, chunk_index INT NOT NULL DEFAULT 0, parent_entry_id BIGINT DEFAULT NULL, vector_id VARCHAR(64) DEFAULT NULL, status TINYINT NOT NULL DEFAULT 1, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_task (id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, original_file_path VARCHAR(500) NOT NULL, original_filename VARCHAR(200) NOT NULL, file_type VARCHAR(10) NOT NULL, file_size BIGINT NOT NULL DEFAULT 0, thumbnail_path VARCHAR(500) DEFAULT NULL, status VARCHAR(20) NOT NULL DEFAULT 'queued', priority TINYINT NOT NULL DEFAULT 0, retry_count INT NOT NULL DEFAULT 0, error_message TEXT DEFAULT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, completed_at DATETIME DEFAULT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_info (id BIGINT AUTO_INCREMENT PRIMARY KEY, task_id BIGINT DEFAULT NULL, user_id BIGINT NOT NULL, name VARCHAR(50), gender VARCHAR(5), age INT, report_date DATE, check_type VARCHAR(20), unit_name VARCHAR(100), created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_indicator (id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL, item_name VARCHAR(100) NOT NULL, item_name_standard VARCHAR(100) DEFAULT NULL, item_code VARCHAR(50) DEFAULT NULL, result_value VARCHAR(50) DEFAULT NULL, unit VARCHAR(20) DEFAULT NULL, ref_range_low VARCHAR(50) DEFAULT NULL, ref_range_high VARCHAR(50) DEFAULT NULL, category VARCHAR(50) DEFAULT NULL, raw_text TEXT DEFAULT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_interpretation (id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL, overall_level VARCHAR(10) DEFAULT NULL, red_count INT NOT NULL DEFAULT 0, yellow_count INT NOT NULL DEFAULT 0, green_count INT NOT NULL DEFAULT 0, summary_text TEXT DEFAULT NULL, status VARCHAR(20) NOT NULL DEFAULT 'pending', retry_count INT NOT NULL DEFAULT 0, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at DATETIME DEFAULT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS indicator_judgment (id BIGINT AUTO_INCREMENT PRIMARY KEY, interpretation_id BIGINT NOT NULL, indicator_id BIGINT NOT NULL, item_name VARCHAR(100) NOT NULL, result_value VARCHAR(50) DEFAULT NULL, deviation VARCHAR(10) DEFAULT NULL, color_level VARCHAR(10) DEFAULT NULL, matched_rule_id BIGINT DEFAULT NULL, explanation TEXT DEFAULT NULL, suggestion TEXT DEFAULT NULL, knowledge_refs JSON DEFAULT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS triage_rule (id BIGINT AUTO_INCREMENT PRIMARY KEY, rule_name VARCHAR(100) NOT NULL, rule_type VARCHAR(20) NOT NULL, indicator_code VARCHAR(50) DEFAULT NULL, conditions JSON NOT NULL, color_level VARCHAR(10) NOT NULL, priority INT NOT NULL DEFAULT 0, is_active TINYINT NOT NULL DEFAULT 1, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_template (id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL, type VARCHAR(10) NOT NULL, content LONGBLOB DEFAULT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS statistic_cache (id BIGINT AUTO_INCREMENT PRIMARY KEY, stat_type VARCHAR(50) NOT NULL, params_hash VARCHAR(64) NOT NULL, result_json JSON DEFAULT NULL, expired_at DATETIME DEFAULT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS dispatch_config (id BIGINT AUTO_INCREMENT PRIMARY KEY, config_key VARCHAR(50) NOT NULL, config_value VARCHAR(500) NOT NULL, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS resource_metric (id BIGINT AUTO_INCREMENT PRIMARY KEY, metric_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, cpu_percent DECIMAL(5,1) DEFAULT NULL, memory_percent DECIMAL(5,1) DEFAULT NULL, gpu_percent DECIMAL(5,1) DEFAULT NULL, gpu_memory_percent DECIMAL(5,1) DEFAULT NULL, queue_depth INT DEFAULT NULL, active_workers INT DEFAULT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS chat_session (id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, hospital_id VARCHAR(32) NOT NULL, report_id BIGINT DEFAULT NULL, title VARCHAR(200) DEFAULT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS chat_message (id BIGINT AUTO_INCREMENT PRIMARY KEY, session_id BIGINT NOT NULL, role VARCHAR(10) NOT NULL, content TEXT NOT NULL, knowledge_refs JSON DEFAULT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (session_id) REFERENCES chat_session(id)) ENGINE=InnoDB;
EOSQL
fi
log "Databases ready"

# ── 5. Ensure .env exists ──────────────────────────────────────────
if [[ ! -f "$BACKEND_DIR/.env" ]]; then
    cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
    log "Created .env from .env.example"
    warn "Please update .env with correct passwords (expected: root/root)"
fi

# ── 6. Install dependencies ────────────────────────────────────────
log "Checking Python dependencies..."
pushd "$BACKEND_DIR" >/dev/null
export PATH="$HOME/.local/bin:$PATH"
uv sync --quiet 2>&1 | sed 's/^/  /'
# Ensure vllm is installed for OCR (not in lockfile, installed separately)
uv pip install vllm --python .venv/bin/python3 --quiet 2>&1 | sed 's/^/  /' || true
popd >/dev/null

log "Checking frontend dependencies..."
pushd "$FRONTEND_DIR" >/dev/null
if [[ ! -d node_modules ]]; then
    npm install --silent 2>&1 | sed 's/^/  /'
fi
popd >/dev/null

# ── 7. Start vLLM OCR server (optional, use --no-ocr to skip) ──────
if [[ "${1:-}" != "--no-ocr" ]] && [[ "${2:-}" != "--no-ocr" ]]; then
    log "Starting vLLM OCR server (port 8001)..."
    if [[ -x "$BACKEND_DIR/.venv/bin/vllm" ]] || uv run --project "$BACKEND_DIR" python3 -c "import vllm" 2>/dev/null; then
        pushd "$BACKEND_DIR" >/dev/null
        export HF_ENDPOINT=https://hf-mirror.com
        nohup uv run vllm serve deepseek-ai/DeepSeek-OCR-2 \
            --port 8001 \
            --trust-remote-code \
            --max-model-len 8192 \
            --gpu-memory-utilization 0.6 \
            --no-enable-prefix-caching \
            --mm-processor-cache-gb 0 \
            > /tmp/vllm-ocr.log 2>&1 &
        OCR_PID=$!
        popd >/dev/null
        log "vLLM OCR starting (PID: $OCR_PID, log: /tmp/vllm-ocr.log)"
    else
        warn "vLLM not installed, skipping OCR server"
    fi
else
    log "Skipping vLLM OCR (--no-ocr)"
fi

# ── 7.5 Start vLLM Embedding server (BGE-M3, port 8002) ───────────
log "Starting vLLM Embedding server (port 8002)..."
if [[ -x "$BACKEND_DIR/.venv/bin/vllm" ]] || uv run --project "$BACKEND_DIR" python3 -c "import vllm" 2>/dev/null; then
    pushd "$BACKEND_DIR" >/dev/null
    export HF_ENDPOINT=https://hf-mirror.com
    nohup uv run vllm serve BAAI/bge-m3 \
        --port 8002 \
        --trust-remote-code \
        --max-model-len 8192 \
        --gpu-memory-utilization 0.3 \
        --no-enable-prefix-caching \
        > /tmp/vllm-embed.log 2>&1 &
    EMBED_PID=$!
    popd >/dev/null
    log "vLLM Embedding starting (PID: $EMBED_PID, log: /tmp/vllm-embed.log)"
else
    warn "vLLM not installed, skipping Embedding server"
fi

# ── 7.6 Start Reranker service (port 8003) ──────────────────────
log "Starting Reranker service (port 8003)..."
RERANKER_DIR="$BACKEND_DIR/reranker_service"
if [[ -d "$RERANKER_DIR" ]]; then
    pushd "$RERANKER_DIR" >/dev/null
    export HF_ENDPOINT=https://hf-mirror.com
    PATH="$HOME/.local/bin:$PATH" nohup uv run uvicorn main:app --host 127.0.0.1 --port 8003 > /tmp/reranker.log 2>&1 &
    RERANKER_PID=$!
    popd >/dev/null
    log "Reranker service starting (PID: $RERANKER_PID, log: /tmp/reranker.log)"
else
    warn "reranker_service dir not found, skipping"
fi

# ── 8. Start backend ────────────────────────────────────────────────
log "Starting backend (port 8000)..."
pushd "$BACKEND_DIR" >/dev/null
PATH="$HOME/.local/bin:$PATH" nohup uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/backend.log 2>&1 &
BACKEND_PID=$!
popd >/dev/null

# Wait for backend to be ready
for i in $(seq 1 20); do
    if curl -s http://localhost:8000/api/v1/health 2>/dev/null | grep -q ok; then
        log "Backend ready: http://localhost:8000"
        break
    fi
    sleep 1
done

# ── 8.5 Start RabbitMQ workers ──────────────────────────────────────
log "Starting report parsing worker..."
pushd "$BACKEND_DIR" >/dev/null
nohup uv run python3 -c "
from app.modules.report.worker import start_worker
start_worker()
" > /tmp/worker-parsing.log 2>&1 &
PARSING_WORKER_PID=$!
popd >/dev/null
log "Parsing worker started (PID: $PARSING_WORKER_PID)"

log "Starting interpretation worker..."
pushd "$BACKEND_DIR" >/dev/null
nohup uv run python3 -c "
from app.modules.interpretation.worker import start_worker
start_worker()
" > /tmp/worker-interpretation.log 2>&1 &
INTERP_WORKER_PID=$!
popd >/dev/null
log "Interpretation worker started (PID: $INTERP_WORKER_PID)"

# ── 9. Start frontend (optional, use --no-frontend to skip) ─────────
if [[ "${1:-}" != "--no-frontend" ]]; then
    log "Starting frontends..."
    pushd "$FRONTEND_DIR" >/dev/null

    npm run dev -w @hospital/user-portal -- --port 3001 &
    FPID1=$!
    npm run dev -w @hospital/doctor-portal -- --port 3002 &
    FPID2=$!
    npm run dev -w @hospital/admin-portal -- --port 3003 &
    FPID3=$!
    popd >/dev/null

    sleep 3
else
    log "Skipping frontend (--no-frontend)"
fi

# ── 10. Summary ──────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  All services started (Docker-free mode)"
echo "=============================================="
echo "  Backend API:  http://localhost:8000"
echo "  vLLM OCR:    http://localhost:8001  (log: /tmp/vllm-ocr.log)"
echo "  vLLM Embed:  http://localhost:8002  (log: /tmp/vllm-embed.log)"
echo "  Reranker:    http://localhost:8003  (log: /tmp/reranker.log)"
echo "  Milvus:      http://localhost:19530  (log: /tmp/milvus.log)"
echo "  Workers:     parsing + interpretation (logs: /tmp/worker-*.log)"
if [[ "${1:-}" != "--no-frontend" ]]; then
    echo "  User Portal:  http://localhost:3001"
    echo "  Doctor Portal: http://localhost:3002"
    echo "  Admin Portal:  http://localhost:3003"
fi
echo ""
echo "  Create test user:"
echo "  curl -X POST http://localhost:8000/api/v1/auth/register \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"username\":\"doctor1\",\"password\":\"123456\",\"role\":\"doctor\",\"hospital_id\":\"H001\"}'"
echo ""
echo "  Press Ctrl+C to stop all services"
echo "=============================================="
echo ""

wait
