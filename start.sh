#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
DOCKER_DIR="$BACKEND_DIR/docker"

# Model cache & HuggingFace mirror
export HF_ENDPOINT=https://hf-mirror.com
export HF_HOME=${HF_HOME:-/root/autodl-tmp/model/huggingface}

RED='\033[0;31m'
GREEN='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "\033[0;32m[INFO]\033[0m  $*"; }
warn() { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
err()  { echo -e "\033[0;31m[ERROR]\033[0m $*"; }

BACKEND_PID=""
OCR_PID=""
EMBED_PID=""
RERANKER_PID=""
PARSING_WORKER_PID=""
INTERP_WORKER_PID=""
FPID1=""
FPID2=""
FPID3=""

cleanup() {
    log "正在关闭所有服务..."
    for pid in "$BACKEND_PID" "$OCR_PID" "$EMBED_PID" "$RERANKER_PID" \
               "$PARSING_WORKER_PID" "$INTERP_WORKER_PID" \
               "$FPID1" "$FPID2" "$FPID3"; do
        [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    log "已关闭。"
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# ── 1. 检查前置依赖 ──────────────────────────────────────────────
log "检查前置依赖..."
for cmd in docker uv node npm; do
    if ! command -v $cmd &>/dev/null; then
        err "$cmd 未安装或不在 PATH 中"
        exit 1
    fi
done
log "依赖检查通过"

# ── 2. 启动基础设施 (Docker) ─────────────────────────────────────
log "启动基础设施 (MySQL + RabbitMQ + Milvus)..."

if [[ "${1:-}" != "--no-infra" ]]; then
    pull_image() {
        local image=$1 proxy_image=""
        if docker image inspect "$image" &>/dev/null; then
            return 0
        fi
        log "镜像 $image 不存在，尝试拉取..."
        for proxy in dockerproxy.net docker.m.daocloud.io docker.1ms.run; do
            proxy_image="${proxy}/${image}"
            if curl -sI --connect-timeout 3 "https://${proxy}/v2/" >/dev/null 2>&1; then
                log "通过代理 $proxy 拉取 $image ..."
                docker pull "$proxy_image" && docker tag "$proxy_image" "$image" && return 0
            fi
        done
        err "无法拉取 $image，请检查网络或手动拉取"
        return 1
    }

    pull_image "mysql:8.0" || exit 1
    pull_image "rabbitmq:3.12-management" || exit 1
    pull_image "quay.io/coreos/etcd:v3.5.5" || exit 1
    pull_image "minio/minio:RELEASE.2023-03-20T20-16-18Z" || exit 1
    pull_image "milvusdb/milvus:v2.6.3" || exit 1

    pushd "$DOCKER_DIR" >/dev/null
    docker-compose up -d 2>&1 | sed 's/^/  /'
    popd >/dev/null

    # 等待 MySQL 就绪
    log "等待 MySQL 就绪..."
    MYSQL_ROOT_PW=$(grep -oP '^MYSQL_PASSWORD=\K.*' "$BACKEND_DIR/.env" 2>/dev/null || echo "root")
    for i in $(seq 1 30); do
        if docker exec docker-mysql-1 mysqladmin ping -uroot -p"$MYSQL_ROOT_PW" --silent 2>/dev/null; then
            log "MySQL 已就绪"
            break
        fi
        sleep 2
    done

    # 等待 RabbitMQ 就绪
    log "等待 RabbitMQ 就绪..."
    for i in $(seq 1 30); do
        if docker exec docker-rabbitmq-1 rabbitmqctl status 2>/dev/null | grep -q "OS PID"; then
            log "RabbitMQ 已就绪"
            break
        fi
        sleep 2
    done

    # 等待 Milvus 就绪
    log "等待 Milvus 就绪..."
    for i in $(seq 1 60); do
        if curl -s http://localhost:9091/healthz 2>/dev/null | grep -q "OK"; then
            log "Milvus 已就绪 (port 19530)"
            break
        fi
        sleep 2
    done
else
    log "跳过基础设施启动 (--no-infra)"
fi

# ── 3. 初始化数据库 ──────────────────────────────────────────────
log "检查数据库..."

MYSQL_PW=$(grep -oP '^MYSQL_PASSWORD=\K.*' "$BACKEND_DIR/.env" 2>/dev/null || echo "root")
MYSQL_CONTAINER="docker-mysql-1"

# hospital_template 库由 docker init SQL 自动创建，这里检查并补全
TEMPLATE_EXISTS=$(docker exec "$MYSQL_CONTAINER" mysql -uroot -p"$MYSQL_PW" -N -e \
    "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='hospital_template';" 2>/dev/null || echo "0")

if [[ "$TEMPLATE_EXISTS" == "0" ]]; then
    log "初始化 hospital_template 库..."
    docker exec -i "$MYSQL_CONTAINER" mysql -uroot -p"$MYSQL_PW" < "$DOCKER_DIR/mysql/init/01_template_db.sql"
    docker exec -i "$MYSQL_CONTAINER" mysql -uroot -p"$MYSQL_PW" hospital_template < "$DOCKER_DIR/mysql/init/02_hospital_created.sql"
    log "hospital_template 库初始化完成"
else
    log "hospital_template 库已存在"
fi

# 检查 hospital_H001 库
DB_EXISTS=$(docker exec "$MYSQL_CONTAINER" mysql -uroot -p"$MYSQL_PW" -N -e \
    "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='hospital_H001';" 2>/dev/null || echo "0")

if [[ "$DB_EXISTS" == "0" ]]; then
    log "初始化 hospital_H001 数据库..."
    docker exec "$MYSQL_CONTAINER" mysql -uroot -p"$MYSQL_PW" -e \
        "CALL hospital_template.create_hospital_database('H001');" 2>/dev/null || true
    # 如果存储过程不存在，手动建库建表
    docker exec -i "$MYSQL_CONTAINER" mysql -uroot -p"$MYSQL_PW" <<'EOSQL'
CREATE DATABASE IF NOT EXISTS hospital_H001
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;
USE hospital_H001;
CREATE TABLE IF NOT EXISTS hospital_user (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, name VARCHAR(50),
    phone VARCHAR(20), gender VARCHAR(5), age INT, unit_name VARCHAR(100),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS knowledge_category (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL,
    parent_id BIGINT DEFAULT NULL, sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS knowledge_entry (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, category_id BIGINT DEFAULT NULL,
    title VARCHAR(200) NOT NULL, content TEXT NOT NULL,
    source_type VARCHAR(20) NOT NULL DEFAULT 'manual', source_file VARCHAR(500) DEFAULT NULL,
    chunk_index INT NOT NULL DEFAULT 0, parent_entry_id BIGINT DEFAULT NULL,
    vector_id VARCHAR(64) DEFAULT NULL, status TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_task (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL,
    original_file_path VARCHAR(500) NOT NULL, original_filename VARCHAR(200) NOT NULL,
    file_type VARCHAR(10) NOT NULL, file_size BIGINT NOT NULL DEFAULT 0,
    thumbnail_path VARCHAR(500) DEFAULT NULL, status VARCHAR(20) NOT NULL DEFAULT 'queued',
    priority TINYINT NOT NULL DEFAULT 0, retry_count INT NOT NULL DEFAULT 0,
    error_message TEXT DEFAULT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    completed_at DATETIME DEFAULT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_info (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, task_id BIGINT DEFAULT NULL, user_id BIGINT NOT NULL,
    name VARCHAR(50), gender VARCHAR(5), age INT, report_date DATE,
    check_type VARCHAR(20), unit_name VARCHAR(100),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_indicator (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL,
    item_name VARCHAR(100) NOT NULL, item_name_standard VARCHAR(100) DEFAULT NULL,
    item_code VARCHAR(50) DEFAULT NULL, result_value VARCHAR(50) DEFAULT NULL,
    unit VARCHAR(20) DEFAULT NULL, ref_range_low VARCHAR(50) DEFAULT NULL,
    ref_range_high VARCHAR(50) DEFAULT NULL, category VARCHAR(50) DEFAULT NULL,
    raw_text TEXT DEFAULT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_interpretation (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL,
    overall_level VARCHAR(10) DEFAULT NULL, red_count INT NOT NULL DEFAULT 0,
    yellow_count INT NOT NULL DEFAULT 0, green_count INT NOT NULL DEFAULT 0,
    summary_text TEXT DEFAULT NULL, status VARCHAR(20) NOT NULL DEFAULT 'pending',
    retry_count INT NOT NULL DEFAULT 0, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME DEFAULT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS indicator_judgment (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, interpretation_id BIGINT NOT NULL,
    indicator_id BIGINT NOT NULL, item_name VARCHAR(100) NOT NULL,
    result_value VARCHAR(50) DEFAULT NULL, deviation VARCHAR(10) DEFAULT NULL,
    color_level VARCHAR(10) DEFAULT NULL, matched_rule_id BIGINT DEFAULT NULL,
    explanation TEXT DEFAULT NULL, suggestion TEXT DEFAULT NULL,
    knowledge_refs JSON DEFAULT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS triage_rule (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, rule_name VARCHAR(100) NOT NULL,
    rule_type VARCHAR(20) NOT NULL, indicator_code VARCHAR(50) DEFAULT NULL,
    conditions JSON NOT NULL, color_level VARCHAR(10) NOT NULL,
    priority INT NOT NULL DEFAULT 0, is_active TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS report_template (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL,
    type VARCHAR(10) NOT NULL, content LONGBLOB DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS statistic_cache (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, stat_type VARCHAR(50) NOT NULL,
    params_hash VARCHAR(64) NOT NULL, result_json JSON DEFAULT NULL,
    expired_at DATETIME DEFAULT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS dispatch_config (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, config_key VARCHAR(50) NOT NULL,
    config_value VARCHAR(500) NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS resource_metric (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    metric_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cpu_percent DECIMAL(5,1) DEFAULT NULL, memory_percent DECIMAL(5,1) DEFAULT NULL,
    gpu_percent DECIMAL(5,1) DEFAULT NULL, gpu_memory_percent DECIMAL(5,1) DEFAULT NULL,
    queue_depth INT DEFAULT NULL, active_workers INT DEFAULT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS chat_session (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL,
    hospital_id VARCHAR(32) NOT NULL, report_id BIGINT DEFAULT NULL,
    title VARCHAR(200) DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS chat_message (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, session_id BIGINT NOT NULL,
    role VARCHAR(10) NOT NULL, content TEXT NOT NULL,
    knowledge_refs JSON DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES chat_session(id)
) ENGINE=InnoDB;
EOSQL
    log "数据库初始化完成"
else
    log "数据库已存在，跳过初始化"
fi

# ── 4. 确保 .env 存在 ─────────────────────────────────────────────
if [[ ! -f "$BACKEND_DIR/.env" ]]; then
    cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
    log "已从 .env.example 创建 .env"
    warn "请检查 .env 中的密码与 docker-compose.yml 一致"
fi

# ── 5. 安装依赖 ───────────────────────────────────────────────────
log "检查 Python 依赖..."
pushd "$BACKEND_DIR" >/dev/null
export PATH="$HOME/.local/bin:$PATH"
uv sync --quiet 2>&1 | sed 's/^/  /'
# vLLM 不在 lockfile 中，单独安装（用于 OCR/Embedding）
uv pip install vllm --python .venv/bin/python3 --quiet 2>&1 | sed 's/^/  /' || true
popd >/dev/null

log "检查前端依赖..."
pushd "$FRONTEND_DIR" >/dev/null
if [[ ! -d node_modules ]]; then
    npm install --silent 2>&1 | sed 's/^/  /'
fi
popd >/dev/null

# ── 6. Start vLLM OCR server (optional, use --no-ocr to skip) ─────
if [[ "${1:-}" != "--no-ocr" ]] && [[ "${2:-}" != "--no-ocr" ]]; then
    log "启动 vLLM OCR 服务 (port 8001)..."
    if [[ -x "$BACKEND_DIR/.venv/bin/vllm" ]] || uv run --project "$BACKEND_DIR" python3 -c "import vllm" 2>/dev/null; then
        pushd "$BACKEND_DIR" >/dev/null
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
        log "vLLM OCR 启动中 (PID: $OCR_PID, log: /tmp/vllm-ocr.log)"
    else
        warn "vLLM 未安装，跳过 OCR 服务"
    fi
else
    log "跳过 vLLM OCR (--no-ocr)"
fi

# ── 6.5 Start vLLM Embedding server (BGE-M3, port 8002) ───────────
log "启动 vLLM Embedding 服务 (port 8002)..."
if [[ -x "$BACKEND_DIR/.venv/bin/vllm" ]] || uv run --project "$BACKEND_DIR" python3 -c "import vllm" 2>/dev/null; then
    pushd "$BACKEND_DIR" >/dev/null
    nohup uv run vllm serve BAAI/bge-m3 \
        --port 8002 \
        --trust-remote-code \
        --max-model-len 8192 \
        --gpu-memory-utilization 0.3 \
        --no-enable-prefix-caching \
        > /tmp/vllm-embed.log 2>&1 &
    EMBED_PID=$!
    popd >/dev/null
    log "vLLM Embedding 启动中 (PID: $EMBED_PID, log: /tmp/vllm-embed.log)"
else
    warn "vLLM 未安装，跳过 Embedding 服务"
fi

# ── 7. Start Reranker service (port 8003) ─────────────────────────
log "启动 Reranker 服务 (port 8003)..."
RERANKER_DIR="$BACKEND_DIR/reranker_service"
if [[ -d "$RERANKER_DIR" ]]; then
    pushd "$RERANKER_DIR" >/dev/null
    PATH="$HOME/.local/bin:$PATH" nohup uv run uvicorn main:app --host 127.0.0.1 --port 8003 > /tmp/reranker.log 2>&1 &
    RERANKER_PID=$!
    popd >/dev/null
    log "Reranker 服务已启动 (PID: $RERANKER_PID, log: /tmp/reranker.log)"
else
    warn "reranker_service 目录不存在，跳过"
fi

# ── 8. 启动后端 ───────────────────────────────────────────────────
log "启动后端 (port 8000)..."
pushd "$BACKEND_DIR" >/dev/null
PATH="$HOME/.local/bin:$PATH" nohup uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/backend.log 2>&1 &
BACKEND_PID=$!
popd >/dev/null

# 等待后端就绪
for i in $(seq 1 30); do
    if curl -s http://localhost:8000/api/v1/health 2>/dev/null | grep -q ok; then
        log "后端已就绪: http://localhost:8000"
        break
    fi
    sleep 1
done

# ── 8.5 启动 RabbitMQ Workers ─────────────────────────────────────
log "启动报告解析 Worker..."
pushd "$BACKEND_DIR" >/dev/null
nohup uv run python3 -c "
from app.modules.report.worker import start_worker
start_worker()
" > /tmp/worker-parsing.log 2>&1 &
PARSING_WORKER_PID=$!
popd >/dev/null
log "解析 Worker 已启动 (PID: $PARSING_WORKER_PID, log: /tmp/worker-parsing.log)"

log "启动报告解读 Worker..."
pushd "$BACKEND_DIR" >/dev/null
nohup uv run python3 -c "
from app.modules.interpretation.worker import start_worker
start_worker()
" > /tmp/worker-interpretation.log 2>&1 &
INTERP_WORKER_PID=$!
popd >/dev/null
log "解读 Worker 已启动 (PID: $INTERP_WORKER_PID, log: /tmp/worker-interpretation.log)"

# ── 9. 启动前端 ───────────────────────────────────────────────────
if [[ "${1:-}" != "--no-frontend" ]]; then
    log "启动前端..."
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
    log "跳过前端 (--no-frontend)"
fi

# ── 10. 摘要 ──────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║          所有服务已启动 (Docker 基础设施模式)          ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  后端 API:    http://localhost:8000                  ║"
echo "║  vLLM OCR:    http://localhost:8001 (log: vllm-ocr)  ║"
echo "║  vLLM Embed:  http://localhost:8002 (log: vllm-embed)║"
echo "║  Reranker:    http://localhost:8003 (log: reranker)  ║"
echo "║  Milvus:      http://localhost:19530                  ║"
echo "║  Workers:     parsing + interpretation               ║"
echo "║  用户端:      http://localhost:3001                  ║"
echo "║  医生端:      http://localhost:3002                  ║"
echo "║  管理后台:    http://localhost:3003                  ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  Docker 容器: docker-compose -f backend/docker/      ║"
echo "║               docker-compose.yml ps                  ║"
echo "║  按 Ctrl+C 停止所有服务                               ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "  创建测试用户:"
echo "  cd backend && uv run python scripts/create_test_user.py"
echo ""

# 等待任意子进程退出
wait
