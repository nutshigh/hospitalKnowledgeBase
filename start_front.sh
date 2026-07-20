#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# start_front.sh — 一键启动前端三门户
#   用户端 :3001 / 医生端 :3002 / 管理后台 :3003
# 用法：
#   bash start_front.sh              # 启动全部三个
#   bash start_front.sh --user       # 仅用户端
#   bash start_front.sh --doctor     # 仅医生端
#   bash start_front.sh --admin      # 仅管理后台
# ============================================================

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# 解析参数：默认全部启动
START_USER=1; START_DOCTOR=1; START_ADMIN=1
HAS_FILTER=0
for arg in "$@"; do
  case "$arg" in
    --user)   HAS_FILTER=1; START_USER=1;   START_DOCTOR=0; START_ADMIN=0 ;;
    --doctor) HAS_FILTER=1; START_USER=0;   START_DOCTOR=1; START_ADMIN=0 ;;
    --admin)  HAS_FILTER=1; START_USER=0;   START_DOCTOR=0; START_ADMIN=1 ;;
  esac
done

# 提高 inotify 限制（Vite 需要）
CURRENT_WATCHES=$(cat /proc/sys/fs/inotify/max_user_watches 2>/dev/null || echo 0)
if [[ "$CURRENT_WATCHES" -lt 524288 ]]; then
  sysctl -w fs.inotify.max_user_watches=524288 fs.inotify.max_user_instances=512 >/dev/null 2>&1 || true
  log "Raised inotify limits (max_user_watches=524288)"
fi

# 检查 node_modules
if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  log "Installing frontend dependencies..."
  cd "$FRONTEND_DIR"
  npm install --silent 2>&1 | sed 's/^/  /'
  cd "$ROOT_DIR"
fi

# cleanup
cleanup() {
  log "Stopping frontends..."
  for pidfile in /tmp/start-front-*.pid; do
    [[ -f "$pidfile" ]] && kill "$(cat $pidfile)" 2>/dev/null || true
    rm -f "$pidfile"
  done
  log "Done."
  exit 0
}
trap cleanup SIGINT SIGTERM

log "Starting frontend services..."

# 用户端 :3001
if [[ "$START_USER" == "1" ]]; then
  if curl -s -m 2 -o /dev/null http://localhost:3001 >/dev/null 2>&1; then
    log "User portal already running (3001)"
  else
    cd "$FRONTEND_DIR"
    nohup npm run dev -w @hospital/user-portal > /tmp/fe-user.log 2>&1 &
    echo $! > /tmp/start-front-user.pid
    cd "$ROOT_DIR"
    log "  User portal launching (:3001, log: /tmp/fe-user.log)"
  fi
fi

# 医生端 :3002
if [[ "$START_DOCTOR" == "1" ]]; then
  if curl -s -m 2 -o /dev/null http://localhost:3002 >/dev/null 2>&1; then
    log "Doctor portal already running (3002)"
  else
    cd "$FRONTEND_DIR"
    nohup npm run dev -w @hospital/doctor-portal > /tmp/fe-doctor.log 2>&1 &
    echo $! > /tmp/start-front-doctor.pid
    cd "$ROOT_DIR"
    log "  Doctor portal launching (:3002, log: /tmp/fe-doctor.log)"
  fi
fi

# 管理后台 :3003
if [[ "$START_ADMIN" == "1" ]]; then
  if curl -s -m 2 -o /dev/null http://localhost:3003 >/dev/null 2>&1; then
    log "Admin portal already running (3003)"
  else
    cd "$FRONTEND_DIR"
    nohup npm run dev -w @hospital/admin-portal > /tmp/fe-admin.log 2>&1 &
    echo $! > /tmp/start-front-admin.pid
    cd "$ROOT_DIR"
    log "  Admin portal launching (:3003, log: /tmp/fe-admin.log)"
  fi
fi

# 等待就绪
sleep 5
echo ""
echo "=============================================="
echo "  Frontend services started"
echo "=============================================="
[[ "$START_USER"   == "1" ]] && echo "  用户端:     http://localhost:3001"
[[ "$START_DOCTOR" == "1" ]] && echo "  医生端:     http://localhost:3002"
[[ "$START_ADMIN"  == "1" ]] && echo "  管理后台:   http://localhost:3003"
echo ""
echo "  Backend API:  http://localhost:8000"
echo "  Test users:   doctor1/123456 (医生), user1/123456 (用户)"
echo ""
echo "  Logs: /tmp/fe-user.log, /tmp/fe-doctor.log, /tmp/fe-admin.log"
echo "  Stop: Ctrl+C"
echo "=============================================="
echo ""

wait
