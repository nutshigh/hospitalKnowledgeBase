#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
DOCKER_DIR="$BACKEND_DIR/docker"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }

cleanup() {
    log "正在关闭所有服务..."
    # kill 子进程组
    jobs -l 2>/dev/null | awk '{print $2}' | xargs -r kill 2>/dev/null || true
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

# ── 2. 启动基础设施 ──────────────────────────────────────────────
log "启动基础设施 (MySQL + RabbitMQ)..."

# 如果指定了 --no-infra，跳过 Docker 启动
if [[ "${1:-}" != "--no-infra" ]]; then
    # 检查是否需要使用代理拉取镜像
    pull_image() {
        local image=$1 proxy_image=""
        if docker image inspect "$image" &>/dev/null; then
            return 0
        fi
        log "镜像 $image 不存在，尝试拉取..."
        for proxy in dockerproxy.net docker.m.daocloud.io docker.1ms.run; do
            proxy_image="${proxy}/library/${image}"
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

    # 进入 docker 目录启动 (需要 docker-compose.yml 存在)
    pushd "$DOCKER_DIR" >/dev/null
    docker-compose up -d 2>&1 | sed 's/^/  /'
    popd >/dev/null

    log "等待 MySQL 就绪..."
    for i in $(seq 1 30); do
        if docker exec docker-mysql-1 mysqladmin ping -uroot -proot123 --silent 2>/dev/null; then
            log "MySQL 已就绪"
            break
        fi
        sleep 2
    done
else
    log "跳过基础设施启动 (--no-infra)"
fi

# ── 3. 初始化数据库 ──────────────────────────────────────────────
log "检查数据库..."

# 读取 .env 中的端口
MYSQL_PORT=$(grep -oP '^MYSQL_PORT=\K.*' "$BACKEND_DIR/.env" 2>/dev/null || echo "3306")
MYSQL_USER=$(grep -oP '^MYSQL_USER=\K.*' "$BACKEND_DIR/.env" 2>/dev/null || echo "root")

DB_EXISTS=$(docker exec docker-mysql-1 mysql -u"$MYSQL_USER" -proot123 -N -e \
    "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='hospital_H001';" 2>/dev/null || echo "0")

if [[ "$DB_EXISTS" == "0" ]]; then
    log "初始化 hospital_H001 数据库..."
    docker exec -i docker-mysql-1 mysql -u"$MYSQL_USER" -proot123 <<'EOSQL'
CREATE DATABASE IF NOT EXISTS hospital_H001
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;
EOSQL
    # 从 init SQL 提取建表语句并执行
    docker exec -i docker-mysql-1 mysql -u"$MYSQL_USER" -proot123 hospital_H001 <<'EOSQL'
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
EOSQL
    log "数据库初始化完成"
else
    log "数据库已存在，跳过初始化"
fi

# ── 4. 确保 .env 存在 ─────────────────────────────────────────────
if [[ ! -f "$BACKEND_DIR/.env" ]]; then
    cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
    log "已从 .env.example 创建 .env"
fi

# ── 5. 安装依赖 ───────────────────────────────────────────────────
log "检查 Python 依赖..."
pushd "$BACKEND_DIR" >/dev/null
uv sync --quiet 2>&1 | sed 's/^/  /'
popd >/dev/null

log "检查前端依赖..."
pushd "$FRONTEND_DIR" >/dev/null
if [[ ! -d node_modules ]]; then
    npm install --silent 2>&1 | sed 's/^/  /'
fi
popd >/dev/null

# ── 6. 启动后端 ───────────────────────────────────────────────────
log "启动后端 (port 8000)..."
pushd "$BACKEND_DIR" >/dev/null
uv run uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!
popd >/dev/null

# 等待后端就绪
for i in $(seq 1 20); do
    if curl -s http://localhost:8000/api/v1/health 2>/dev/null | grep -q ok; then
        log "后端已就绪: http://localhost:8000"
        break
    fi
    sleep 1
done

# ── 7. 启动前端 ───────────────────────────────────────────────────
log "启动前端..."
pushd "$FRONTEND_DIR" >/dev/null

npm run dev -w @hospital/user-portal -- --port 3001 &
FPID1=$!
npm run dev -w @hospital/doctor-portal -- --port 3002 &
FPID2=$!
npm run dev -w @hospital/admin-portal -- --port 3003 &
FPID3=$!
popd >/dev/null

# 等待前端就绪
sleep 3

# ── 8. 摘要 ───────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║          所有服务已启动                           ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║  后端 API:   http://localhost:8000               ║"
echo "║  用户端:     http://localhost:3001               ║"
echo "║  医生端:     http://localhost:3002               ║"
echo "║  管理后台:   http://localhost:3003               ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║  按 Ctrl+C 停止所有服务                          ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# 等待任意子进程退出
wait
