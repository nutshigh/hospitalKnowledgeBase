#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

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

# ── 3. Start RabbitMQ ──────────────────────────────────────────────
log "Starting RabbitMQ..."
if ! rabbitmqctl status 2>/dev/null | grep -q "OS PID"; then
    RABBITMQ_NODE_IP_ADDRESS=127.0.0.1 ERL_EPMD_ADDRESS=127.0.0.1 \
        rabbitmq-server -detached 2>&1 | sed 's/^/  /'
    sleep 3
    rabbitmqctl start_app 2>&1 | sed 's/^/  /' || true
fi
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
popd >/dev/null

log "Checking frontend dependencies..."
pushd "$FRONTEND_DIR" >/dev/null
if [[ ! -d node_modules ]]; then
    npm install --silent 2>&1 | sed 's/^/  /'
fi
popd >/dev/null

# ── 7. Start backend ───────────────────────────────────────────────
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

# ── 8. Start frontend (optional, use --no-frontend to skip) ────────
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

# ── 9. Summary ─────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  All services started (Docker-free mode)"
echo "=============================================="
echo "  Backend API:  http://localhost:8000"
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
