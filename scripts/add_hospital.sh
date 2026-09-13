#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# add_hospital.sh — 按 orgId 新建医院租户(等价于后端 POST /api/v1/tenants)
#   复用 infra/mysql/init/02_hospital_created.sql 的存储过程
#   create_hospital_database:建 hospital_<orgId> 库 + 全量表(幂等),
#   再在 platform 库 hospital_tenant 登记 active=1。
#   新库一建出来即等于 hospital_H001 当前态,无需再跑 manual migrations。
# 用法:
#   ./add_hospital.sh <orgId> [医院名称]
#   ./add_hospital.sh <orgId> --name "市人民医院"
#   bash add_hospital.sh 1000002
# 环境变量(覆盖默认 docker 直连):
#   MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASS(默认走 docker exec hospital-mysql)
# 说明:
#   - 幂等:重复执行只会确保建库建表 + 把 hospital_name/is_active 更新到位。
#   - 不建任何 platform_user / admin 账号(账号走 /auth/register 或 app-login 自动注册)。
#   - 完成后无需重启 backend/worker;文件存储目录由代码自动创建。
# ============================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SP_FILE="$ROOT_DIR/infra/mysql/init/02_hospital_created.sql"
TPL_FILE="$ROOT_DIR/infra/mysql/init/01_template_db.sql"

MYSQL_CONTAINER="${MYSQL_CONTAINER:-hospital-mysql}"
MYSQL_HOST="${MYSQL_HOST:-}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_USER="${MYSQL_USER:-root}"
MYSQL_PASS="${MYSQL_PASS:-root}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }

if [[ -n "$MYSQL_HOST" ]]; then
  # 查询/执行入口(不读 stdin,避免与 while-read 争用)
  mysql_q() { mysql -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" -p"$MYSQL_PASS" \
    --default-character-set=utf8mb4 "$@"; }
  # 从 stdin 导入 SQL 文件入口
  mysql_pipe() { mysql -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" -p"$MYSQL_PASS" \
    --default-character-set=utf8mb4; }
else
  mysql_q() { docker exec "$MYSQL_CONTAINER" mysql -u"$MYSQL_USER" -p"$MYSQL_PASS" \
    --default-character-set=utf8mb4 "$@"; }
  mysql_pipe() { docker exec -i "$MYSQL_CONTAINER" mysql -u"$MYSQL_USER" -p"$MYSQL_PASS" \
    --default-character-set=utf8mb4; }
fi

# 取单值(纯数据,无表头)
mysql_val() { mysql_q -N -B -e "$1"; }
# 交互可读输出
mysql_out() { mysql_q -e "$1"; }

usage() {
  sed -n '4,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  echo
  echo "  $0 <orgId> [--name 医院名称]"
  exit "${1:-0}"
}

# ── 参数解析 ────────────────────────────────────────────────
ORG_ID=""
NAME=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)  usage 0 ;;
    -n|--name)  shift; NAME="${1:-}" ;;
    *)          if [[ -z "$ORG_ID" ]]; then ORG_ID="$1"; else err "未知参数: $1"; usage 1; fi ;;
  esac
  shift
done

if [[ -z "$ORG_ID" ]]; then
  err "缺少 orgId(必填)"
  usage 1
fi
if ! [[ "$ORG_ID" =~ ^[A-Za-z0-9]{1,32}$ ]]; then
  err "orgId 必须为 1-32 位字母/数字(hospital_<orgId> 将作为库名): $ORG_ID"
  exit 1
fi
if [[ -z "$NAME" ]]; then
  warn "未提供医院名称(可用 --name 指定),默认以 orgId 作为 hospital_name"
fi

DB_NAME="hospital_${ORG_ID}"

# ── 连接健康检查 ─────────────────────────────────────────────
if ! mysql_q -e "SELECT 1" >/dev/null 2>&1; then
  err "无法连接 MySQL(${MYSQL_HOST:-docker:$MYSQL_CONTAINER}),请确认中间件已启动: cd infra && docker compose up -d"
  exit 1
fi
log "MySQL 连接 OK"

# ── 首次引导:platform 库缺 hospital_tenant / SP 时从仓库补载 ──
tenant_cnt=$(mysql_val "SELECT COUNT(*) FROM information_schema.TABLES \
  WHERE TABLE_SCHEMA='hospital_template' AND TABLE_NAME='hospital_tenant'" || true)
if [[ "${tenant_cnt:-0}" == "0" ]]; then
  if [[ ! -f "$TPL_FILE" ]]; then err "缺 $TPL_FILE"; exit 1; fi
  warn "platform 库尚未初始化,补载 01_template_db.sql"
  mysql_pipe < "$TPL_FILE"
fi

sp_cnt=$(mysql_val "SELECT COUNT(*) FROM information_schema.ROUTINES \
  WHERE ROUTINE_SCHEMA='hospital_template' AND ROUTINE_NAME='create_hospital_database'" || true)
if [[ -f "$SP_FILE" ]]; then
  # SP 是新建租户的唯一 DDL 源,直接每次从仓库重载(DROP+CREATE 幂等),
  # 防止库里残留旧版 SP(缺表)导致新库少表。
  if [[ "${sp_cnt:-0}" == "0" ]]; then
    warn "存储过程缺失,从仓库补载 02_hospital_created.sql"
  fi
  log "同步存储过程 create_hospital_database(以仓库 $SP_FILE 为准)..."
  mysql_pipe < "$SP_FILE"
else
  if [[ "${sp_cnt:-0}" == "0" ]]; then
    err "缺少 $SP_FILE 且库内无该存储过程,无法建库"
    exit 1
  fi
  warn "未找到 $SP_FILE,使用库内既有存储过程(可能落后于 start.sh DDL,请注意)"
fi

# ── 已有租户快照(用于日志与幂等提示)──────────────────────────
existing_name=""
existing=$(mysql_val "SELECT CONCAT(hospital_name,'|',is_active) \
  FROM hospital_template.hospital_tenant WHERE hospital_id='$ORG_ID'" || true)
if [[ -n "$existing" ]]; then
  warn "hospital_id=$ORG_ID 已存在(hospital_name|is_active = $existing),将幂等刷新"
  existing_name="${existing%%|*}"
fi
# 未显式给名称:已登记时沿用原名(避免把名称覆盖成 orgId);全新则退回 orgId
if [[ -z "$NAME" ]]; then
  if [[ -n "$existing_name" ]]; then
    NAME="$existing_name"
  else
    NAME="$ORG_ID"
  fi
fi

# ── 建库建表(SP 内部全用 CREATE TABLE IF NOT EXISTS,可重复)──
log "调用 create_hospital_database('$ORG_ID') 创建库 $DB_NAME 与全部业务表..."
mysql_q -e "CALL hospital_template.create_hospital_database('$ORG_ID')"

# ── 登记 hospital_tenant(幂等 upsert)──────────────────────
NAME_ESC="${NAME//\'/\'\'}"
log "登记 hospital_tenant: hospital_id=$ORG_ID  name=$NAME  db=$DB_NAME  active=1"
mysql_q -e "INSERT INTO hospital_template.hospital_tenant \
  (hospital_id, hospital_name, db_name, is_active) \
  VALUES ('$ORG_ID', '$NAME_ESC', '$DB_NAME', 1) \
  ON DUPLICATE KEY UPDATE \
    hospital_name=VALUES(hospital_name), \
    db_name=VALUES(db_name), \
    is_active=1"

# ── 校验:与参考租户 hospital_H001 逐表对齐(剔除历史遗留 risk 表)──
# disease_hit / disease_mapping / disease_rule 仅 H001 历史遗留,不随新租户创建,不算漂移。
got=$(mysql_val "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='$DB_NAME'")
missing=""
while IFS= read -r t; do
  [[ -z "$t" ]] && continue
  case "$t" in
    disease_hit|disease_mapping|disease_rule) continue ;;
  esac
  cnt=$(mysql_val "SELECT COUNT(*) FROM information_schema.TABLES \
    WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='$t'")
  if [[ "$cnt" == "0" ]]; then
    missing+="$t "
  fi
done < <(mysql_val "SELECT TABLE_NAME FROM information_schema.TABLES \
  WHERE TABLE_SCHEMA='hospital_H001' ORDER BY TABLE_NAME" || true)
if [[ -n "$missing" ]]; then
  warn "新库缺以下应在的表: $missing"
  warn "请把 start.sh DDL 块/01_template_db.sql 的最新表同步进 02_hospital_created.sql 后重跑本脚本"
fi

echo
mysql_out "SELECT hospital_id, hospital_name, db_name, is_active \
  FROM hospital_template.hospital_tenant WHERE hospital_id='$ORG_ID'"
log "完成:库 $DB_NAME 共 $got 张表"
log "下一步:给医院建账号(register/app-login 均可);批量上传前确认文件名 <姓名>_<身份证后6位>.<ext> 的用户在该 orgId 下真实存在"
