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
#   - 幂等:重复执行会补齐增量列(parsed_name / dispatch_hospital / signal_flag /
#     chat_session.name / user_id VARCHAR(16) 等)、创建 risk 表,并把
#     hospital_name/is_active 更新到位 —— 也可用于修复旧脚本创建的不完整租户库。
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

# 幂等补列(MySQL 8 不支持 ADD COLUMN IF NOT EXISTS)
ensure_column() {
  local table="$1" column="$2" definition="$3" cnt
  cnt=$(mysql_val "SELECT COUNT(*) FROM information_schema.COLUMNS \
    WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='$table' AND COLUMN_NAME='$column'")
  if [[ "$cnt" == "0" ]]; then
    log "补列 $table.$column"
    mysql_q -e "ALTER TABLE \`$DB_NAME\`.\`$table\` ADD COLUMN \`$column\` $definition"
  fi
}

# 旧租户 user_id 可能是 BIGINT,批量上传按身份证后六位分发后应为 VARCHAR(16)
ensure_user_id_varchar() {
  local table="$1" typ
  typ=$(mysql_val "SELECT COLUMN_TYPE FROM information_schema.COLUMNS \
    WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='$table' AND COLUMN_NAME='user_id'")
  if [[ -n "$typ" && "$typ" != "varchar(16)" ]]; then
    log "规范化 $table.user_id: $typ -> varchar(16)"
    mysql_q -e "ALTER TABLE \`$DB_NAME\`.\`$table\` MODIFY user_id VARCHAR(16) NOT NULL"
  fi
}

usage() {
  sed -n '4,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
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
    warn "未提供医院名称(可用 --name 指定),默认以 orgId 作为 hospital_name"
    NAME="$ORG_ID"
  fi
fi

# ── 建库建表(SP 内部全用 CREATE TABLE IF NOT EXISTS,可重复)──
log "调用 create_hospital_database('$ORG_ID') 创建库 $DB_NAME 与全部业务表..."
mysql_q -e "CALL hospital_template.create_hospital_database('$ORG_ID')"

# ── 增量列对齐(CALL 内的 CREATE TABLE IF NOT EXISTS 不会改已存在的表)──
log "对齐增量列(可重复执行)..."
ensure_user_id_varchar report_task
ensure_user_id_varchar report_info
ensure_user_id_varchar chat_session
ensure_column report_info parsed_name "VARCHAR(50) NULL"
ensure_column report_indicator signal_flag "TINYINT NOT NULL DEFAULT 0"
ensure_column chat_session name "VARCHAR(50) DEFAULT NULL"
ensure_column batch_import_file dispatch_hospital "VARCHAR(24) DEFAULT NULL"
ensure_column disease_mapping source "VARCHAR(20) DEFAULT 'LOCAL'"
ensure_column disease_mapping match_level "VARCHAR(10) DEFAULT 'YELLOW'"
ensure_column disease_mapping match_deviation "VARCHAR(10) DEFAULT NULL"

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

# ── 校验:与参考租户 hospital_H001 逐表/逐列对齐 ──────────────
got=$(mysql_val "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='$DB_NAME'")
ref_tables=$(mysql_val "SELECT COUNT(*) FROM information_schema.TABLES \
  WHERE TABLE_SCHEMA='hospital_H001'" || true)
if [[ "${ref_tables:-0}" == "0" ]]; then
  warn "参考租户 hospital_H001 不存在,跳过逐表/逐列校验"
else
  missing_tables=""
  while IFS= read -r t; do
    [[ -z "$t" ]] && continue
    cnt=$(mysql_val "SELECT COUNT(*) FROM information_schema.TABLES \
      WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='$t'" || true)
    [[ "$cnt" == "0" ]] && missing_tables+="$t "
  done < <(mysql_val "SELECT TABLE_NAME FROM information_schema.TABLES \
    WHERE TABLE_SCHEMA='hospital_H001' ORDER BY TABLE_NAME" || true)

  missing_columns=""
  type_drift=""
  while IFS=$'\t' read -r t c ctype; do
    [[ -z "$t" ]] && continue
    nt=$(mysql_val "SELECT COLUMN_TYPE FROM information_schema.COLUMNS \
      WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='$t' AND COLUMN_NAME='$c'" || true)
    if [[ -z "$nt" ]]; then
      missing_columns+="$t.$c "
    elif [[ "$nt" != "$ctype" ]]; then
      type_drift+="$t.$c(H001=$ctype,new=$nt) "
    fi
  done < <(mysql_val "SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE FROM information_schema.COLUMNS \
    WHERE TABLE_SCHEMA='hospital_H001' ORDER BY TABLE_NAME,ORDINAL_POSITION" || true)

  if [[ -n "$missing_tables" || -n "$missing_columns" || -n "$type_drift" ]]; then
    [[ -n "$missing_tables" ]] && err "新库缺表: $missing_tables"
    [[ -n "$missing_columns" ]] && err "新库缺列: $missing_columns"
    [[ -n "$type_drift" ]] && err "列类型漂移: $type_drift"
    err "请把 start.sh DDL 块/01_template_db.sql 的最新结构同步进 02_hospital_created.sql 后重跑本脚本"
    exit 1
  fi
fi

echo
mysql_out "SELECT hospital_id, hospital_name, db_name, is_active \
  FROM hospital_template.hospital_tenant WHERE hospital_id='$ORG_ID'"
log "完成:库 $DB_NAME 共 $got 张表"
log "下一步:给医院建账号(register/app-login 均可);批量上传前确认文件名 <姓名>_<身份证后6位>.<ext> 的用户在该 orgId 下真实存在"
